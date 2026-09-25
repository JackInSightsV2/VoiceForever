"""The Lexicon (#12): extraction and ranking, drafts, applying respellings, review actions, the lexicon.json lock,
WER that ignores respellings, and auto names respelled when ASR keeps missing them."""
import json
from pathlib import Path

import pytest

from vo import asr, cli, db, lexicon, lock, prep, prepare, run, text
from test_approval import FakeASR, FakeEngine


@pytest.fixture
def conn(tmp_path):
    conn = db.connect(tmp_path / "vo.sqlite")
    conn.executemany("INSERT INTO npcs (id, name, race, gender) VALUES (?, ?, ?, ?)",
                     [(1, "Archmage Kel'Thuzad", "Human", "male"), (2, "Magni Bronzebeard", "Dwarf", "male")])
    rows = [
        (1, 1, "Kel'Thuzad's plans for Stormwind are dire, $N."),
        (2, 1, "Ahn’Qiraj stirs. Kel'Thuzad knows. Kel'Thuzad waits."),
        (3, 2, "Go to Ahn'Qiraj, then find the Barovs in Caer Darrow. I'll wait. The Barov family's crypt..."),
        (4, 2, "Hail, $C. The Qiraji and the Barov line have much to answer for. Ahn'Qiraj! II."),
        (5, None, "Magni didn't say much. The Plaguelands and Winterspring are cold. Thrall's orcs, too."),
        (6, None, "Seek the Qiraji near Ahn'Qiraj."),
    ]
    conn.executemany("INSERT INTO lines (id, npc_id, type, raw_text, tts_text) VALUES (?, ?, 'gossip', ?, ?)",
                     [(i, n, t, t) for i, n, t in rows])
    conn.commit()
    return conn


# --- extraction and ranking --------------------------------------------------------------------------------------------

def test_extract_ranks_lore_names_by_line_count(conn):
    names = lexicon.extract(conn, areas=["Caer Darrow", "Ahn'Qiraj"])
    got = [(n.name, n.lines) for n in names]
    # Ahn'Qiraj in 4 lines (either apostrophe), Kel'Thuzad's possessive folded, Barovs folded into Barov.
    assert got == [("Ahn'Qiraj", 4), ("Barov", 2), ("Kel'Thuzad", 2), ("Qiraji", 2), ("Caer", 1), ("Darrow", 1),
                   ("Magni", 1)]
    by = {n.name: n for n in names}
    assert by["Kel'Thuzad"].npc and not by["Kel'Thuzad"].zone
    assert by["Caer"].zone and by["Ahn'Qiraj"].zone and by["Magni"].npc
    assert by["Kel'Thuzad"].example == 1


def test_english_words_compounds_contractions_and_numerals_are_not_names():
    for word in ("Thrall", "Stormwind", "Plaguelands", "Winterspring", "Warsong", "Razorfen", "Hail",
                 "The", "II", "Didn't", "I'll", "Barrens", "Dwarven", "Tis"):
        assert lexicon.names_in(word) == set(), word
    assert lexicon.names_in("Orgrimmar and Arthas") == {"Orgrimmar", "Arthas"}  # no chance short-word splits
    assert lexicon.names_in("Kel'Thuzad's and Zul'Farrak") == {"Kel'Thuzad", "Zul'Farrak"}
    assert lexicon.names_in("durotar is lowercase") == set()


def test_sync_splits_top_names_for_review_from_auto_names(conn):
    got = lexicon.sync(conn, top=2)
    assert got == {"names": 7, "top": 2}
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM lexicon")}
    assert [n for n, r in rows.items() if r["status"] == "pending"] == ["Ahn'Qiraj", "Barov"]
    assert rows["Kel'Thuzad"]["status"] == "auto" and rows["Kel'Thuzad"]["spelling"] == "Kel-thoo-zahd"
    assert rows["Ahn'Qiraj"]["rank"] == 1 and rows["Magni"]["rank"] == 7
    # A name gone from the lines is dropped unless reviewed; a reviewed spelling is never redrafted.
    conn.execute("UPDATE lexicon SET status = 'corrected', spelling = 'Bar-off' WHERE name = 'Barov'")
    conn.execute("DELETE FROM lines WHERE id IN (3, 4)")
    conn.commit()
    lexicon.sync(conn, top=2)
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM lexicon")}
    assert "Caer" not in rows
    assert (rows["Barov"]["spelling"], rows["Barov"]["rank"], rows["Barov"]["lines"]) == ("Bar-off", None, 0)
    assert rows["Kel'Thuzad"]["status"] == "pending"  # now in the top 2


# --- drafts --------------------------------------------------------------------------------------------------------------

def test_drafts_come_from_the_seed_table_then_rules():
    assert lexicon.draft("Kel'Thuzad") == "Kel-thoo-zahd"
    assert lexicon.draft("Ahn'Qiraj") == "Ahn-kee-rahzh"
    assert lexicon.rule_draft("Qiraji") == "Kirajee"
    assert lexicon.rule_draft("Mekkatorque") == "Mekkatork"
    assert lexicon.rule_draft("Zul'Farrak") == "Zul-Farrak"
    assert lexicon.rule_draft("Atal'ai") == "Atal-eye"
    assert lexicon.rule_draft("Xavius") == "Zavius"
    assert lexicon.rule_draft("Durotar") == "Durotar"  # nothing an English reader gets wrong
    data = json.loads((lexicon.DATA / "lexicon_seed.json").read_text())
    assert all(e["respelling"] and e["source"].startswith("http") for e in data["entries"].values())


def test_alternatives_end_with_the_name_as_written():
    alts = lexicon.alternatives("Kel'Thuzad")
    assert alts[0] == "Kel-thoo-zahd" and alts[-1] == "Kel'Thuzad" and len(alts) == len(set(alts)) >= 3


# --- applying it ---------------------------------------------------------------------------------------------------------

LEX = lexicon.Lexicon({"Kel'Thuzad": "Kel-thoo-zahd", "Kel": "Kell", "Thrall": "Thrawl", "Ahn'Qiraj": "Ahn-kee-rahj",
                       "Barov": "Bar-off", "Caer Darrow": "Care Darrow"})


@pytest.mark.parametrize("before, after", [
    ("Kel'Thuzad rises.", "Kel-thoo-zahd rises."),
    ("Kel'Thuzad's phylactery", "Kel-thoo-zahd's phylactery"),         # possessive
    ("Kel’Thuzad’s phylactery", "Kel-thoo-zahd’s phylactery"),         # curly apostrophes
    ("the Barovs and the Barovs' land", "the Bar-offs and the Bar-offs' land"),  # plural, plural possessive
    ("Thrall's orcs", "Thrawl's orcs"),
    ("Kel said: Kel!", "Kell said: Kell!"),                              # the shorter name alone
    ("Kelthuzad and Kelly", "Kelthuzad and Kelly"),                      # inside a longer word: untouched
    ("Ahn'Qiraji", "Ahn'Qiraji"),                                        # a different name that starts the same
    ("thrall of the Lich", "thrall of the Lich"),                         # lower case is a common noun
    ("KEL'THUZAD!", "Kel-thoo-zahd!"),                                   # shouted
    ("(Thrall), \"Thrall\" -Thrall-", "(Thrawl), \"Thrawl\" -Thrawl-"),  # punctuation around it
    ("Caer Darrow, Caer  Darrow", "Care Darrow, Care Darrow"),           # multiword, any spacing
    ("Ahn'Qiraj\nKel'Thuzad", "Ahn-kee-rahj\nKel-thoo-zahd"),
])
def test_replacement_edge_cases(before, after):
    assert LEX(before) == after


def test_text_prep_applies_the_lexicon_last():
    raw = "Greetings, $C. Kel'Thuzad's 10g are in Ahn'Qiraj."
    assert text.prepare(raw, None, LEX) == "Greetings, friend. Kel-thoo-zahd's ten gold are in Ahn-kee-rahj."


def test_pairs_find_respelled_names_in_spoken_text():
    assert LEX.pairs("Kel-thoo-zahd waits in Ahn-kee-rahj; Kel-thoo-zahd!") == [
        ("Kel-thoo-zahd", "Kel'Thuzad"), ("Ahn-kee-rahj", "Ahn'Qiraj")]


def test_lexicon_change_requeues_only_the_lines_that_say_the_name(conn):
    lexicon.sync(conn)
    prep.prepare_lines(conn, lexicon.from_db(conn))
    run.sync_jobs(conn, "kokoro:am_michael")
    conn.execute("UPDATE jobs SET status = 'done'")
    conn.execute("UPDATE lexicon SET spelling = 'Kee-rah-jee' WHERE name = 'Qiraji'")
    conn.commit()
    assert prep.prepare_lines(conn, lexicon.from_db(conn)) == 2
    assert run.sync_jobs(conn, "kokoro:am_michael") == 2
    pending = [r[0] for r in conn.execute("SELECT line_id FROM jobs WHERE status = 'pending' ORDER BY line_id")]
    assert pending == [4, 6]
    assert "Kee-rah-jee" in conn.execute("SELECT tts_text FROM lines WHERE id = 6").fetchone()[0]


# --- WER: a respelling is not an error -------------------------------------------------------------------------------------

K = [("Kel-thoo-zahd", "Kel'Thuzad")]


@pytest.mark.parametrize("heard", ["Kel'Thuzad waits in the tower.", "Kelthuzad waits in the tower.",
                                   "kel thoo zahd waits in the tower", "Kel-Thuzad waits in the tower."])
def test_wer_ignores_how_asr_writes_a_respelled_name(heard):
    spoken = "Kel-thoo-zahd waits in the tower."
    assert asr.wer(spoken, "Kel'Thuzad waits in the tower.") > 0  # without the names: inflated
    assert asr.wer(spoken, heard, K) == 0


def test_wer_with_names_still_counts_real_errors_and_reports_misses():
    spoken = "Tell the Kel-thoo-zahd cult we come."
    assert asr.wer(spoken, "Tell the Kelthuzad cult we come.", K) == 0
    assert asr.wer(spoken, "Tell the Kelthuzad cult we go.", K) == pytest.approx(1 / 6)
    ref, hyp, missed = asr.collapse_names(spoken, "Tell the bell to sad cult we come.", K)
    assert missed == ["Kel'Thuzad"] and len(ref) == 6
    assert asr.collapse_names(spoken, "tell the kel too zod cult we come", K)[2] == []  # close enough


def test_process_job_uses_the_names_for_wer(tmp_path):
    class Speaker:
        def render(self, text, voice, seed, delivery=None):
            import numpy as np
            return np.zeros(8000, dtype=np.float32) + 0.1, 16000

    class Heard:
        def transcribe(self, wav):
            return "Kel'Thuzad waits."

    job = run.Job(1, "kokoro:x", "Kel-thoo-zahd waits.", 1, str(tmp_path / "1.ogg"), names=tuple(K))
    r = run.process_job(job, Speaker(), Heard())
    assert r.wer == 0 and r.ok


# --- auto names: ASR misses move them to the next respelling --------------------------------------------------------------

def test_auto_name_is_respelled_after_misses_in_three_lines(conn):
    lexicon.sync(conn, top=0)  # everything auto
    lex = lexicon.from_db(conn)
    pairs = [("Kirajee", "Qiraji")]
    logged = []
    assert lexicon.note_take(conn, lex, 4, pairs, ["Qiraji"], logged.append) == []
    assert lexicon.note_take(conn, lex, 4, pairs, ["Qiraji"], logged.append) == []  # same line: counts once
    assert lexicon.note_take(conn, lex, 6, pairs, [], logged.append) == []  # heard: no miss
    assert lexicon.note_take(conn, lex, 6, pairs, ["Qiraji"], logged.append) == []
    assert lexicon.note_take(conn, lex, 5, pairs, ["Qiraji"], logged.append) == ["Qiraji"]
    r = conn.execute("SELECT * FROM lexicon WHERE name = 'Qiraji'").fetchone()
    assert (r["alt"], r["spelling"]) == (1, lexicon.alternatives("Qiraji")[1])
    assert lex.spellings["qiraji"] == r["spelling"] and "respelled" in logged[0]
    assert conn.execute("SELECT COUNT(*) FROM lexicon_misses").fetchone()[0] == 0  # misses reset


def test_reviewed_names_are_never_respelled_automatically(conn):
    lexicon.sync(conn)
    conn.execute("UPDATE lexicon SET status = 'accepted' WHERE name = 'Qiraji'")
    lex = lexicon.from_db(conn)
    for line in (1, 2, 3, 4):
        assert lexicon.note_take(conn, lex, line, [("Kirajee", "Qiraji")], ["Qiraji"]) == []


def test_vo_run_respells_a_name_asr_keeps_missing_and_requeues_its_lines(conn, tmp_path):
    for i in range(10, 14):
        conn.execute("INSERT INTO lines (id, npc_id, type, raw_text) VALUES (?, 1, 'gossip', ?)",
                     (i, f"Bring {i} Zzorgoth fangs."))
    conn.commit()
    lexicon.sync(conn, top=0)
    lex = lexicon.from_db(conn)
    prep.prepare_lines(conn, lex)
    first = conn.execute("SELECT spelling FROM lexicon WHERE name = 'Zzorgoth'").fetchone()[0]

    def asr_never_hears_it(job):
        Path(job.out).parent.mkdir(parents=True, exist_ok=True)
        Path(job.out).write_bytes(b"OggS")
        heard = job.text.replace(first, "the dog") if first in job.text else job.text
        return run.Result(True, None, 0.0, heard, 1.0)

    run.run(conn, tmp_path / "audio", voice_id="kokoro:am_michael", workers=1, processor=asr_never_hears_it,
            log=lambda _: None, lexicon=lex)
    second = conn.execute("SELECT spelling, alt FROM lexicon WHERE name = 'Zzorgoth'").fetchone()
    assert second["alt"] == 1 and second["spelling"] != first
    # Its lines now say the new spelling, and every one of them was regenerated with it.
    texts = [r[0] for r in conn.execute("SELECT tts_text FROM lines WHERE id >= 10")]
    assert all(second["spelling"] in t for t in texts)
    assert {r[0] for r in conn.execute("SELECT status FROM jobs WHERE line_id >= 10")} == {"done"}


# --- review on the Approval page, the lock, vo run's gate ------------------------------------------------------------------

def _act(conn, action, target, payload=None):
    conn.execute("INSERT INTO review_actions (action, target, payload) VALUES (?, ?, ?)",
                 (action, target, json.dumps(payload) if payload else None))
    conn.commit()


def _prepare(conn, tmp_path, **kw):
    narrated = []

    def narrator(t):
        narrated.append(t)
        return FakeEngine._tone(3)

    s = prepare.prepare(conn, tmp_path / "candidates", engine=kw.pop("engine", FakeEngine()), asr_backend=FakeASR(),
                        narrator=narrator, candidates=1, samples=1, lock_path=tmp_path / "approved_voices.json",
                        lexicon_path=tmp_path / "lexicon.json", log=lambda m: None, **kw)
    return s, narrated


def test_prepare_renders_a_sample_per_top_name_with_its_spelling(conn, tmp_path):
    s, narrated = _prepare(conn, tmp_path)
    assert (s.names, s.names_top, s.names_reviewed, s.lexicon_lock) == (7, 7, 0, "open")
    r = conn.execute("SELECT * FROM lexicon WHERE name = 'Kel''Thuzad'").fetchone()
    assert "Kel-thoo-zahd" in r["sample_spoken"] and "Kel'Thuzad" in r["sample_text"]
    assert r["sample_spelling"] == "Kel-thoo-zahd" and Path(r["sample_path"]).exists()
    assert r["sample_voice"] == "Narrator, kokoro:bm_lewis"  # no approved Archetype yet
    assert r["sample_spoken"] in narrated
    # Nothing new: nothing rendered. A corrected spelling is rendered again.
    s, narrated = _prepare(conn, tmp_path)
    assert s.name_samples == 0
    _act(conn, "correct-lexicon", "Kel'Thuzad", {"spelling": "Kel-thoo-zad"})
    s, narrated = _prepare(conn, tmp_path)
    assert s.name_samples == 1 and "Kel-thoo-zad" in narrated[-1]


def test_sample_uses_the_approved_archetype_anchor_when_there_is_one(conn, tmp_path):
    _prepare(conn, tmp_path)
    cand = conn.execute("SELECT id FROM candidates WHERE archetype = 'human_m'").fetchone()[0]
    _act(conn, "approve-candidate", cand)
    _act(conn, "correct-lexicon", "Kel'Thuzad", {"spelling": "Kel-thoo-zadd"})  # re-render
    eng = FakeEngine()
    _prepare(conn, tmp_path, engine=eng)
    r = conn.execute("SELECT * FROM lexicon WHERE name = 'Kel''Thuzad'").fetchone()
    assert r["sample_voice"] == f"VoxCPM2, human_m anchor {cand}"
    assert any("Kel-thoo-zadd" in c[0] for c in eng.continue_calls)


def test_review_actions_accept_and_correct_are_consumed_by_prepare(conn, tmp_path):
    _prepare(conn, tmp_path)
    _act(conn, "accept-lexicon", "Barov")
    _act(conn, "correct-lexicon", "Qiraji", {"spelling": "  Kee-rah-jee "})
    _act(conn, "correct-lexicon", "Magni", {"spelling": ""})     # invalid: dropped
    _act(conn, "accept-lexicon", "Nobody")                          # invalid: dropped
    _act(conn, "retry-line", "1")                                   # vo run's: left queued
    s, _ = _prepare(conn, tmp_path)
    assert s.actions == 4 and s.names_reviewed == 2
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM lexicon")}
    assert (rows["Barov"]["status"], rows["Barov"]["spelling"]) == ("accepted", "Barov")  # the draft, as heard
    assert (rows["Qiraji"]["status"], rows["Qiraji"]["spelling"]) == ("corrected", "Kee-rah-jee")
    assert rows["Magni"]["status"] == "pending"
    assert conn.execute("SELECT action FROM review_actions WHERE consumed_at IS NULL").fetchall()[0][0] == "retry-line"
    # vo run leaves them for vo prepare.
    _act(conn, "accept-lexicon", "Magni")
    assert run.consume_actions(conn, lambda m: None) == 1  # only the retry-line
    assert conn.execute("SELECT COUNT(*) FROM review_actions WHERE action = 'accept-lexicon' AND consumed_at IS NULL"
                        ).fetchone()[0] == 1


def test_lexicon_lock_written_once_every_top_name_is_reviewed(conn, tmp_path):
    path = tmp_path / "lexicon.json"
    _prepare(conn, tmp_path)
    with pytest.raises(lexicon.LockError, match="not found"):
        lexicon.require(conn, path)
    names = [r[0] for r in conn.execute("SELECT name FROM lexicon ORDER BY rank")]
    for n in names[:-1]:
        _act(conn, "accept-lexicon", n)
    s, _ = _prepare(conn, tmp_path)
    assert s.lexicon_lock == "open" and not path.exists()
    _act(conn, "correct-lexicon", names[-1], {"spelling": "Mag-nee"})
    s, _ = _prepare(conn, tmp_path)
    assert s.lexicon_lock == "written" and not path.stat().st_mode & 0o222  # read-only
    data = lexicon.require(conn, path)
    assert data["entries"]["Magni"] == {"spelling": "Mag-nee", "draft": "Magnee", "status": "corrected", "lines": 1}
    assert set(data["entries"]) == set(names)
    s, _ = _prepare(conn, tmp_path)
    assert s.lexicon_lock == "unchanged"
    # The lock's spellings are the ones used.
    assert lexicon.from_db(conn, data)("Magni") == "Mag-nee"
    # A new top name re-opens it.
    conn.execute("INSERT INTO lines (id, npc_id, type, raw_text) VALUES (99, 1, 'gossip', 'Beware Zzorgoth.')")
    conn.commit()
    s, _ = _prepare(conn, tmp_path)
    assert s.lexicon_lock == "open" and not path.exists()
    assert "Approval Gate open" in prepare.summary_text(s)


def test_lock_must_cover_every_current_top_name(conn, tmp_path):
    lexicon.sync(conn)
    path = tmp_path / "lexicon.json"
    lock.write(path, {"locked": True, "top": 300, "entries": {"Barov": {"spelling": "Barov"}}})
    with pytest.raises(lexicon.LockError, match="Ahn'Qiraj"):
        lexicon.require(conn, path)
    lock.write(path, {"locked": False, "entries": {}})
    with pytest.raises(lexicon.LockError, match="not a locked"):
        lexicon.load(path)


def test_vo_run_refuses_to_start_without_the_lexicon_lock(conn, tmp_path, monkeypatch):
    voices = tmp_path / "approved_voices.json"
    anchor = tmp_path / "a.wav"
    anchor.write_bytes(b"RIFF")
    entry = {"candidate": "human_m/g0s0", "anchor": str(anchor)}
    lock.write(voices, {"locked": True, "archetypes": {"human_m": entry, "dwarf_m": entry}})
    monkeypatch.setenv(lock.ENV, str(voices))
    lexicon.sync(conn)
    monkeypatch.setenv(lexicon.ENV, str(tmp_path / "lexicon.json"))
    with pytest.raises(SystemExit) as e:
        cli.main(["--db", str(tmp_path / "vo.sqlite"), "run", "--no-caffeinate"])
    assert "vo run refuses to start" in str(e.value) and "Lexicon" in str(e.value)
