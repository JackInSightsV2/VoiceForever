import json
import threading
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import numpy as np
import pytest

from vo import audio, db, run

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


def test_parallel_workers_share_one_writer(conn, tmp_path):
    summary = run.run(conn, tmp_path / "audio", voice_id=VOICE, workers=3, processor=_pool_processor,
                      log=lambda _: None)
    assert summary["jobs"] == {"done": 5}
    assert all((tmp_path / "audio" / "823" / f"{i}.ogg").exists() for i in range(1, 6))


def _pool_processor(job):  # module level so spawn workers can import it
    return Fake()(job)


# --- process_job with fake TTS and ASR, real post-processing ---------------------------------------------------------

class ToneTTS:
    def render(self, text, voice, seed):
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
