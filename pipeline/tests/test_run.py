import json
import os
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest

from vo import audio, db, run, tts

VOICE = "kokoro:am_michael"


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    for i in range(1, 6):
        conn.execute("INSERT INTO lines (id, npc_id, type, quest_id, raw_text, tts_text) VALUES (?, 823, 'quest_detail',"
                     " ?, ?, ?)", (i, i, f"line {i}", f"line {i}"))
    conn.commit()
    return conn


class Fake:
    """Processor stand-in: records calls, writes a file, fails the lines/seeds it's told to."""

    def __init__(self, fail=lambda job: False, kill_on_call=None):
        self.calls, self.fail, self.kill_on_call = [], fail, kill_on_call

    def __call__(self, job: run.Job) -> run.Result:
        self.calls.append((job.line_id, job.seed))
        if self.kill_on_call == len(self.calls):
            raise KeyboardInterrupt  # the process dies mid-line
        if self.fail(job):
            return run.Result(False, "ASR WER 0.90 > 0.2", 0.9, "garbage")
        Path(job.out).parent.mkdir(parents=True, exist_ok=True)
        Path(job.out).write_bytes(b"OggS")
        return run.Result(True, None, 0.0, job.text, 1.0)


def go(conn, tmp_path, fake, **kw):
    return run.run(conn, tmp_path / "audio", voice_id=VOICE, workers=1, processor=fake, log=lambda _: None, **kw)


def statuses(conn):
    return dict(conn.execute("SELECT line_id, status FROM jobs ORDER BY line_id").fetchall())


def test_run_generates_every_line_and_records_audio(conn, tmp_path):
    summary = go(conn, tmp_path, Fake())
    assert summary["jobs"] == {"done": 5}
    rows = conn.execute("SELECT line_id, voice_id, path, status FROM audio ORDER BY line_id").fetchall()
    assert [tuple(r) for r in rows][0] == (1, VOICE, str((tmp_path / "audio" / "823" / "1.ogg").resolve()), "done")
    assert len(rows) == 5


def test_killed_run_resumes_without_redoing_finished_lines(conn, tmp_path):
    first = Fake(kill_on_call=3)
    with pytest.raises(KeyboardInterrupt):
        go(conn, tmp_path, first)
    assert statuses(conn) == {1: "done", 2: "done", 3: "pending", 4: "pending", 5: "pending"}
    second = Fake()
    go(conn, tmp_path, second)
    assert [c[0] for c in second.calls] == [3, 4, 5]
    assert set(statuses(conn).values()) == {"done"}


def test_hard_kill_leaves_running_jobs_that_the_next_run_picks_up(conn, tmp_path):
    go(conn, tmp_path, Fake())
    conn.execute("UPDATE jobs SET status = 'running' WHERE line_id = 4")  # kill -9 mid-line: no cleanup ran
    conn.commit()
    fake = Fake()
    go(conn, tmp_path, fake)
    assert [c[0] for c in fake.calls] == [4]


def test_text_change_requeues_only_that_line(conn, tmp_path):
    go(conn, tmp_path, Fake())
    conn.execute("UPDATE lines SET tts_text = 'new words' WHERE id = 2")
    conn.commit()
    assert run.sync_jobs(conn, VOICE) == 1
    assert conn.execute("SELECT status FROM audio WHERE line_id = 2").fetchone()[0] == "stale"
    fake = Fake()
    go(conn, tmp_path, fake)
    assert [c[0] for c in fake.calls] == [2]
    assert conn.execute("SELECT status FROM audio WHERE line_id = 2").fetchone()[0] == "done"


def test_voice_change_requeues_only_that_npcs_lines(conn, tmp_path):
    conn.execute("INSERT INTO lines (id, npc_id, type, quest_id, raw_text, tts_text) VALUES (6, 99, 'quest_detail',"
                 " 6, 'x', 'x')")
    conn.commit()
    go(conn, tmp_path, Fake())
    conn.execute("INSERT INTO voices (npc_id, voice_id) VALUES (99, 'kokoro:af_heart')")
    conn.commit()
    fake = Fake()
    go(conn, tmp_path, fake)
    assert fake.calls == [(6, 0)]
    assert [tuple(r) for r in conn.execute("SELECT voice_id, status FROM audio WHERE line_id = 6 ORDER BY voice_id")] == [
        ("kokoro:af_heart", "done"), (VOICE, "stale")]


def test_three_failures_quarantine_with_new_seed_each_and_never_block(conn, tmp_path):
    fake = Fake(fail=lambda job: job.line_id == 2)
    summary = go(conn, tmp_path, fake)
    assert [c for c in fake.calls if c[0] == 2] == [(2, 0), (2, 1), (2, 2)]
    assert statuses(conn) == {1: "done", 2: "quarantined", 3: "done", 4: "done", 5: "done"}
    row = conn.execute("SELECT attempts, tries, reason, wer FROM jobs WHERE line_id = 2").fetchone()
    assert tuple(row) == (3, 3, "ASR WER 0.90 > 0.2", 0.9)
    assert summary["this_run"] == {"done": 4, "pending": 2, "quarantined": 1}


def test_quarantined_lines_retry_next_run_with_fresh_seeds(conn, tmp_path):
    go(conn, tmp_path, Fake(fail=lambda job: job.line_id == 2))
    fake = Fake()
    go(conn, tmp_path, fake)
    assert fake.calls == [(2, 3)]
    assert statuses(conn)[2] == "done"


def test_quarantined_lines_can_be_left_alone(conn, tmp_path):
    go(conn, tmp_path, Fake(fail=lambda job: job.line_id == 2))
    fake = Fake()
    go(conn, tmp_path, fake, retry_quarantined=False)
    assert fake.calls == [] and statuses(conn)[2] == "quarantined"


def test_model_error_counts_as_a_failed_take():
    job = run.Job(1, "nope:voice", "hello", 0, "/nonexistent/1.ogg")
    result = run.process_job(job)
    assert not result.ok and result.reason.startswith("error: ValueError")


def test_until_stops_taking_new_lines_and_next_run_resumes(conn, tmp_path):
    start = datetime(2026, 9, 24, 6, 58)
    ticks = iter([start, start + timedelta(minutes=1), start + timedelta(minutes=2), start + timedelta(minutes=3)])
    fake = Fake()
    summary = go(conn, tmp_path, fake, until=run.parse_until("07:00", start), clock=lambda: next(ticks))
    assert summary["paused"] is True
    assert [c[0] for c in fake.calls] == [1, 2]
    assert summary["jobs"] == {"done": 2, "pending": 3}
    fake = Fake()
    go(conn, tmp_path, fake)
    assert [c[0] for c in fake.calls] == [3, 4, 5]


def test_parse_until_rolls_to_tomorrow():
    now = datetime(2026, 9, 24, 22, 0)
    assert run.parse_until("07:00", now) == datetime(2026, 9, 25, 7, 0)
    assert run.parse_until("23:30", now) == datetime(2026, 9, 24, 23, 30)


def test_review_actions_are_consumed_first(conn, tmp_path):
    go(conn, tmp_path, Fake())
    conn.executemany("INSERT INTO review_actions (action, target, payload) VALUES (?, ?, ?)", [
        ("retry-line", "3", None),
        ("skip-line", "4", json.dumps({"reason": "bad source text"})),
        ("approve-voice", "npc:823", None),
    ])
    conn.execute("UPDATE lines SET tts_text = 'changed' WHERE id = 4")  # skip wins over the requeue from a text change
    conn.commit()
    fake = Fake()
    go(conn, tmp_path, fake)
    assert fake.calls == [(3, 1)]
    assert statuses(conn)[4] == "skipped"
    assert conn.execute("SELECT reason FROM jobs WHERE line_id = 4").fetchone()[0] == "bad source text"
    consumed = conn.execute("SELECT action, consumed_at IS NOT NULL FROM review_actions ORDER BY id").fetchall()
    assert [tuple(r) for r in consumed] == [("retry-line", 1), ("skip-line", 1), ("approve-voice", 0)]


def act(conn, action, target, payload=None):
    conn.execute("INSERT INTO review_actions (action, target, payload) VALUES (?, ?, ?)",
                 (action, target, payload and json.dumps(payload)))
    conn.commit()


def live_run(conn):
    return f"run:{conn.execute('SELECT MAX(id) FROM runs').fetchone()[0]}"


def test_edit_tts_text_updates_the_line_and_requeues_it(conn, tmp_path):
    go(conn, tmp_path, Fake(fail=lambda job: job.line_id == 2), retry_quarantined=False)
    act(conn, "edit-tts-text", "2", {"tts_text": "  Kel Thoo zad awaits.  "})
    fake = Fake()
    go(conn, tmp_path, fake, retry_quarantined=False)
    assert fake.calls == [(2, 3)]
    assert conn.execute("SELECT tts_text FROM lines WHERE id = 2").fetchone()[0] == "Kel Thoo zad awaits."
    assert statuses(conn)[2] == "done"
    assert run.sync_jobs(conn, VOICE) == 0  # the job's hash already matches the new text


def test_edit_tts_text_marks_done_audio_stale(conn, tmp_path):
    go(conn, tmp_path, Fake())
    act(conn, "edit-tts-text", "3", {"tts_text": "new words"})
    run.consume_actions(conn, log=lambda _: None)
    assert conn.execute("SELECT status FROM audio WHERE line_id = 3").fetchone()[0] == "stale"
    assert statuses(conn)[3] == "pending"


def test_invalid_action_is_dropped_not_fatal(conn, tmp_path):
    act(conn, "edit-tts-text", "2", {"tts_text": "   "})
    act(conn, "retry-line", "not-a-line")
    logs = []
    assert run.consume_actions(conn, log=logs.append) == 2
    assert all("invalid, dropped" in l for l in logs)
    assert conn.execute("SELECT tts_text FROM lines WHERE id = 2").fetchone()[0] == "line 2"


def test_pause_stops_taking_lines_until_resumed(conn, tmp_path):
    events = []

    class Pausing(Fake):
        def __call__(self, job):
            if job.line_id == 2:
                act(conn, "pause-run", live_run(conn))
            events.append(("line", job.line_id))
            return super().__call__(job)

    def sleep(s):
        events.append(("sleep", conn.execute("SELECT status FROM runs").fetchone()[0]))
        act(conn, "resume-run", live_run(conn))

    summary = go(conn, tmp_path, Pausing(), sleep=sleep)
    assert events == [("line", 1), ("line", 2), ("sleep", "paused"), ("line", 3), ("line", 4), ("line", 5)]
    assert summary["jobs"] == {"done": 5}


def test_set_until_from_dashboard_stops_the_run(conn, tmp_path):
    now = datetime(2026, 9, 24, 6, 58)

    class Setting(Fake):
        def __call__(self, job):
            if job.line_id == 2:
                act(conn, "set-until", live_run(conn), {"until": "06:30"})  # already past today: tomorrow 06:30
                act(conn, "set-until", live_run(conn), {"until": "06:59"})
            return super().__call__(job)

    clock = iter([now, now, now + timedelta(minutes=1)])
    summary = go(conn, tmp_path, Setting(), clock=lambda: next(clock))
    assert summary["paused"] is True and summary["jobs"] == {"done": 2, "pending": 3}
    row = conn.execute("SELECT status, until FROM runs").fetchone()
    assert tuple(row) == ("until", "2026-09-24T06:59:00")


def test_set_until_can_clear_the_cli_until(conn, tmp_path):
    now = datetime(2026, 9, 24, 6, 58)
    act(conn, "set-until", "run:1", {"until": None})
    summary = go(conn, tmp_path, Fake(), until=now, clock=lambda: now)
    assert summary["paused"] is False and summary["jobs"] == {"done": 5}


def test_run_actions_for_another_run_are_dropped(conn, tmp_path):
    act(conn, "pause-run", "run:99")
    summary = go(conn, tmp_path, Fake(), sleep=lambda s: pytest.fail("paused by a stale action"))
    assert summary["jobs"] == {"done": 5}
    assert conn.execute("SELECT consumed_at IS NOT NULL FROM review_actions").fetchone()[0] == 1


def test_run_actions_stay_queued_outside_a_run(conn):
    act(conn, "pause-run", "run:1")
    assert run.consume_actions(conn, log=lambda _: None) == 0


def test_runs_table_records_history(conn, tmp_path):
    go(conn, tmp_path, Fake())
    act(conn, "retry-line", "1")
    with pytest.raises(KeyboardInterrupt):
        go(conn, tmp_path, Fake(kill_on_call=1), until=datetime(2030, 1, 1, 7, 0))
    rows = conn.execute("SELECT pid, workers, status, until, summary IS NOT NULL, ended_at IS NOT NULL,"
                        " last_heartbeat IS NOT NULL FROM runs ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(os.getpid(), 1, "finished", None, 1, 1, 1),
                                        (os.getpid(), 1, "killed", "2030-01-01T07:00:00", 0, 1, 1)]
    assert json.loads(conn.execute("SELECT summary FROM runs WHERE id = 1").fetchone()[0])["jobs"] == {"done": 5}


def test_parallel_workers_share_one_writer(conn, tmp_path):
    summary = run.run(conn, tmp_path / "audio", voice_id=VOICE, workers=3, processor=_pool_processor,
                      log=lambda _: None)
    assert summary["jobs"] == {"done": 5}
    assert all((tmp_path / "audio" / "823" / f"{i}.ogg").exists() for i in range(1, 6))


def _pool_processor(job):  # module level so spawn workers can import it
    return Fake()(job)


# --- process_job with fake TTS and ASR, real post-processing ---------------------------------------------------------

class ToneTTS:
    def __init__(self):
        self.deliveries = []

    def render(self, text, voice, seed, delivery=tts.DEFAULT_DELIVERY):
        self.deliveries.append(delivery)
        rate = 24000
        t = np.arange(rate) / rate
        tone = 0.2 * np.sin(2 * np.pi * 220 * t)
        return np.concatenate([np.zeros(rate // 2), tone, np.zeros(rate // 2)]).astype(np.float32), rate


class EchoASR:
    def __init__(self, text):
        self.text = text

    def transcribe(self, wav):
        assert Path(wav).exists()
        return self.text


def test_process_job_encodes_when_asr_matches(tmp_path):
    out = tmp_path / "823" / "1.ogg"
    out.parent.mkdir()
    job = run.Job(1, VOICE, "Hello, there!", 0, str(out))
    result = run.process_job(job, ToneTTS(), EchoASR("hello there"))
    assert result.ok and result.wer == 0.0 and result.duration_s == pytest.approx(1.3, abs=0.02)
    assert out.read_bytes()[:4] == b"OggS"
    lufs, tp = audio.measure(out)
    assert lufs == pytest.approx(-16, abs=1) and tp <= audio.TRUE_PEAK


def test_process_job_rejects_high_wer_without_writing(tmp_path):
    out = tmp_path / "1.ogg"
    result = run.process_job(run.Job(1, VOICE, "kill ten wolves", 0, str(out)), ToneTTS(), EchoASR("kill wolves now"))
    assert not result.ok and result.wer == pytest.approx(2 / 3) and not out.exists()



# --- Narrator and per-type delivery ----------------------------------------------------------------------------------

def test_lines_without_an_npc_get_the_narrator_voice(conn, tmp_path):
    conn.execute("INSERT INTO lines (id, npc_id, type, quest_id, raw_text, tts_text) VALUES (6, NULL, 'quest_detail',"
                 " 6, 'Wanted: Hogger.', 'Wanted: Hogger.')")
    conn.commit()
    go(conn, tmp_path, Fake())
    voices = dict(conn.execute("SELECT line_id, voice_id FROM audio").fetchall())
    assert voices[6] == tts.NARRATOR_VOICE_ID != VOICE
    assert voices[1] == VOICE
    path = conn.execute("SELECT path FROM audio WHERE line_id = 6").fetchone()[0]
    assert path == str((tmp_path / "audio" / "narrator" / "6.ogg").resolve())


def test_narrator_voice_is_configurable_and_never_an_npc_voice(conn, tmp_path):
    conn.execute("INSERT INTO lines (id, npc_id, type, quest_id, raw_text, tts_text) VALUES (6, NULL, 'quest_complete',"
                 " 6, 'x', 'x')")
    conn.execute("INSERT INTO voices (npc_id, voice_id) VALUES (823, 'kokoro:af_heart')")
    conn.commit()
    go(conn, tmp_path, Fake(), narrator_voice_id="kokoro:bf_emma")
    assert dict(conn.execute("SELECT line_id, voice_id FROM jobs").fetchall()) == {
        1: "kokoro:af_heart", 2: "kokoro:af_heart", 3: "kokoro:af_heart", 4: "kokoro:af_heart", 5: "kokoro:af_heart",
        6: "kokoro:bf_emma"}


def test_delivery_per_type():
    assert tts.delivery("quest_complete").speed > tts.delivery("quest_detail").speed == 1.0
    assert tts.delivery("quest_complete").exaggeration > tts.delivery("quest_progress").exaggeration
    assert tts.delivery("quest_progress").speed == 1.0
    assert tts.delivery(None) == tts.delivery("gossip") == tts.DEFAULT_DELIVERY


def test_claimed_job_carries_line_type_and_backend_gets_its_delivery(conn, tmp_path):
    conn.execute("UPDATE lines SET type = 'quest_complete' WHERE id = 1")
    conn.commit()
    run.sync_jobs(conn, VOICE)
    job = run.claim(conn, tmp_path)
    assert (job.line_id, job.line_type) == (1, "quest_complete")
    backend = ToneTTS()
    run.process_job(job, backend, EchoASR(job.text))
    assert backend.deliveries == [tts.delivery("quest_complete")]


def test_delivery_change_requeues_only_that_type(conn, tmp_path, monkeypatch):
    conn.execute("UPDATE lines SET type = 'quest_complete' WHERE id = 3")
    conn.commit()
    go(conn, tmp_path, Fake())
    monkeypatch.setitem(tts.DELIVERY, "quest_complete", tts.Delivery(speed=1.2))
    fake = Fake()
    go(conn, tmp_path, fake)
    assert fake.calls == [(3, 1)]


def test_kokoro_applies_speed_and_accent(monkeypatch):
    calls = []

    class Model:
        sample_rate = 24000

        def generate(self, text, **kw):
            calls.append(kw)
            yield type("R", (), {"audio": np.zeros(10, dtype=np.float32)})()

    k = tts.Kokoro()
    monkeypatch.setattr(tts.Kokoro, "_model", lambda self: Model())
    k.render("hi", "am_michael", 0, tts.delivery("quest_complete"))
    k.render("hi", "bm_george", 0)
    assert calls == [{"voice": "am_michael", "speed": tts.delivery("quest_complete").speed, "lang_code": "a"},
                     {"voice": "bm_george", "speed": 1.0, "lang_code": "b"}]


# --- ntfy ------------------------------------------------------------------------------------------------------------

@pytest.fixture
def ntfy():
    got = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((self.headers["Title"], self.rfile.read(int(self.headers["Content-Length"])).decode()))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/vo-test", got
    server.shutdown()


def test_ntfy_fires_on_finish(conn, tmp_path, ntfy):
    url, got = ntfy
    run.run_notified(url, lambda: go(conn, tmp_path, Fake()), log=lambda _: None)
    assert got == [("vo run finished", "finished: 5 lines done this run, 0 quarantined. "
                                        "Totals: 5 done, 0 pending, 0 quarantined, 0 skipped.")]


def test_ntfy_fires_on_fatal_error(ntfy):
    url, got = ntfy

    def boom():
        raise RuntimeError("disk full")

    with pytest.raises(RuntimeError):
        run.run_notified(url, boom, log=lambda _: None)
    assert got == [("vo run failed", "RuntimeError: disk full")]


def test_no_ntfy_url_means_no_network(monkeypatch):
    monkeypatch.setattr(run.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network used"))
    assert run.notify(None, "t", "m") is False
