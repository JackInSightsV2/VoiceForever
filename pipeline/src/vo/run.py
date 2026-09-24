"""`vo run`: unattended audio generation from a job queue in SQLite, checkpointed per line.

Only this (main) process writes to SQLite; workers render, post-process and QA a line and hand back a Result.
"""
import hashlib
import json
import multiprocessing as mp
import os
import shutil
import sqlite3
import subprocess
import tempfile
import urllib.request
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable

from vo import asr, audio, tts

MAX_ATTEMPTS = 3
DEFAULT_WORKERS = 3
DEFAULT_WER = 0.2
NTFY_ENV = "VO_NTFY"


def tts_hash(text: str, line_type: str | None = None) -> str:
    """What a job was queued for: its text and its type's Delivery, so a change to either requeues it."""
    return hashlib.sha256(f"{text}\0{tts.delivery(line_type).key()}".encode()).hexdigest()[:16]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Job:
    line_id: int
    voice_id: str
    text: str
    seed: int
    out: str
    wer_threshold: float = DEFAULT_WER
    asr_model: str = asr.DEFAULT_MODEL
    line_type: str | None = None  # picks the per-type Delivery (tts.DELIVERY)


@dataclass(frozen=True)
class Result:
    ok: bool
    reason: str | None = None
    wer: float | None = None
    transcript: str | None = None
    duration_s: float | None = None


# --- queue -----------------------------------------------------------------------------------------------------------

def sync_jobs(conn: sqlite3.Connection, default_voice_id: str,
              narrator_voice_id: str = tts.NARRATOR_VOICE_ID) -> int:
    """Queue every line for its voice: the NPC's, else the default; lines with no NPC get the Narrator. A changed
    tts_text or Delivery requeues the job and marks its old audio stale; jobs for deleted lines or a replaced voice are
    dropped. Returns how many were (re)queued."""
    wanted = {(r[0], r[1]): tts_hash(r[2], r[3]) for r in conn.execute(
        "SELECT l.id, CASE WHEN l.npc_id IS NULL THEN ? ELSE COALESCE(v.voice_id, ?) END, l.tts_text, l.type"
        " FROM lines l LEFT JOIN voices v ON v.npc_id = l.npc_id"
        " WHERE COALESCE(l.tts_text, '') != ''", (narrator_voice_id, default_voice_id))}
    have = {(r[0], r[1]): r[2] for r in conn.execute("SELECT line_id, voice_id, tts_hash FROM jobs")}
    new = [(*k, h) for k, h in wanted.items() if k not in have]
    changed = [(h, _now(), *k) for k, h in wanted.items() if k in have and have[k] != h]
    gone = [k for k in have if k not in wanted]
    with conn:
        conn.executemany("INSERT INTO jobs (line_id, voice_id, tts_hash) VALUES (?, ?, ?)", new)
        conn.executemany(
            "UPDATE jobs SET tts_hash = ?, status = 'pending', attempts = 0, reason = NULL, wer = NULL,"
            " transcript = NULL, updated_at = ? WHERE line_id = ? AND voice_id = ?", changed)
        conn.executemany("DELETE FROM jobs WHERE line_id = ? AND voice_id = ?", gone)
        conn.executemany("UPDATE audio SET status = 'stale' WHERE line_id = ? AND voice_id = ?",
                         [c[2:] for c in changed] + gone)
    return len(new) + len(changed)


def _payload(action: sqlite3.Row) -> dict:
    return json.loads(action["payload"]) if action["payload"] else {}


def _for_line(action: sqlite3.Row) -> tuple[str, list]:
    """WHERE clause for the action's line, narrowed to payload.voice_id when given."""
    voice = _payload(action).get("voice_id")
    return ("line_id = ? AND voice_id = ?", [int(action["target"]), voice]) if voice else (
        "line_id = ?", [int(action["target"])])


def _retry_line(conn: sqlite3.Connection, action: sqlite3.Row) -> None:
    where, params = _for_line(action)
    conn.execute(f"UPDATE jobs SET status = 'pending', attempts = 0, reason = NULL, updated_at = ? WHERE {where}",
                 [_now(), *params])


def _skip_line(conn: sqlite3.Connection, action: sqlite3.Row) -> None:
    where, params = _for_line(action)
    conn.execute(f"UPDATE jobs SET status = 'skipped', reason = ?, updated_at = ? WHERE {where}",
                 [_payload(action).get("reason", "skipped in review"), _now(), *params])


# Other dashboard actions (approve voice, fix lexicon, ...) land with their tickets; until then they stay unconsumed.
ACTIONS: dict[str, Callable[[sqlite3.Connection, sqlite3.Row], None]] = {
    "retry-line": _retry_line,
    "skip-line": _skip_line,
}


def consume_actions(conn: sqlite3.Connection, log: Callable[[str], None] = print) -> int:
    """Apply unconsumed review_actions in order; returns how many were consumed."""
    n = 0
    for action in conn.execute("SELECT * FROM review_actions WHERE consumed_at IS NULL ORDER BY id").fetchall():
        handler = ACTIONS.get(action["action"])
        if handler is None:
            log(f"review action {action['id']}: {action['action']!r} not supported yet, left queued")
            continue
        with conn:
            handler(conn, action)
            conn.execute("UPDATE review_actions SET consumed_at = ? WHERE id = ?", (_now(), action["id"]))
        n += 1
    return n


def claim(conn: sqlite3.Connection, audio_dir: Path, **job_opts) -> Job | None:
    """Mark the next pending job running and return it; each take gets a new seed (its lifetime try number)."""
    row = conn.execute(
        "SELECT j.line_id, j.voice_id, j.tries, l.npc_id, l.type, l.tts_text FROM jobs j JOIN lines l ON l.id = j.line_id"
        " WHERE j.status = 'pending' ORDER BY j.attempts, j.line_id LIMIT 1").fetchone()
    if row is None:
        return None
    with conn:
        conn.execute("UPDATE jobs SET status = 'running', tries = tries + 1, updated_at = ?"
                     " WHERE line_id = ? AND voice_id = ?", (_now(), row["line_id"], row["voice_id"]))
    out = audio_dir / str(row["npc_id"] or "narrator") / f"{row['line_id']}.ogg"
    return Job(row["line_id"], row["voice_id"], row["tts_text"], row["tries"], str(out.resolve()),
               line_type=row["type"], **job_opts)


def record(conn: sqlite3.Connection, job: Job, result: Result, max_attempts: int = MAX_ATTEMPTS) -> str:
    """Checkpoint one finished take; returns the job's new status (done, pending for a retry, or quarantined)."""
    key = (job.line_id, job.voice_id)
    with conn:
        if result.ok:
            conn.execute("UPDATE jobs SET status = 'done', attempts = 0, reason = NULL, wer = ?, transcript = ?,"
                         " updated_at = ? WHERE line_id = ? AND voice_id = ?",
                         (result.wer, result.transcript, _now(), *key))
            conn.execute("INSERT OR REPLACE INTO audio (line_id, voice_id, path, duration_s, status)"
                         " VALUES (?, ?, ?, ?, 'done')", (*key, job.out, result.duration_s))
            return "done"
        return conn.execute(
            "UPDATE jobs SET attempts = attempts + 1,"
            " status = CASE WHEN attempts + 1 >= ? THEN 'quarantined' ELSE 'pending' END,"
            " reason = ?, wer = ?, transcript = ?, updated_at = ? WHERE line_id = ? AND voice_id = ? RETURNING status",
            (max_attempts, result.reason, result.wer, result.transcript, _now(), *key)).fetchone()[0]


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return dict(conn.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status").fetchall())


# --- worker ----------------------------------------------------------------------------------------------------------

def process_job(job: Job, tts_backend: tts.TTSBackend | None = None, asr_backend: asr.ASRBackend | None = None) -> Result:
    """Render, post-process, ASR-check and encode one line. Runs in a worker; never touches SQLite; never raises."""
    try:
        name, voice = tts.split_voice_id(job.voice_id)
        samples, rate = (tts_backend or tts.backend(name)).render(
            job.text, voice, job.seed, tts.delivery(job.line_type))
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "line.wav"
            duration = audio.postprocess(samples, rate, wav)
            transcript = (asr_backend or asr.whisper(job.asr_model)).transcribe(wav).strip()
            score = asr.wer(job.text, transcript)
            if score > job.wer_threshold:
                return Result(False, f"ASR WER {score:.2f} > {job.wer_threshold}", score, transcript, duration)
            out = Path(job.out)
            part = out.with_name(out.name + ".part")
            audio.encode_ogg(wav, part)
            os.replace(part, out)
        return Result(True, None, score, transcript, duration)
    except Exception as e:  # model or tool error: counts as a failed take
        return Result(False, f"error: {type(e).__name__}: {e}")


class InlineExecutor:
    """Runs jobs in-process (workers=1). A BaseException such as KeyboardInterrupt propagates like a kill."""

    def submit(self, fn, *args) -> Future:
        fut = Future()
        try:
            fut.set_result(fn(*args))
        except Exception as e:
            fut.set_exception(e)
        return fut

    def shutdown(self, wait: bool = True, cancel_futures: bool = False) -> None:
        pass


def _executor(workers: int):
    if workers <= 1:
        return InlineExecutor()
    return ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn"))  # no fork: MLX/Metal state


# --- run -------------------------------------------------------------------------------------------------------------

def parse_until(hhmm: str, now: datetime) -> datetime:
    """The next local time HH:MM after `now` (today, else tomorrow)."""
    t = time.fromisoformat(hhmm)
    at = datetime.combine(now.date(), t)
    return at if at > now else at + timedelta(days=1)


def run(conn: sqlite3.Connection, audio_dir: Path, *, voice_id: str = tts.DEFAULT_VOICE_ID,
        narrator_voice_id: str = tts.NARRATOR_VOICE_ID,
        workers: int = DEFAULT_WORKERS, until: datetime | None = None, wer_threshold: float = DEFAULT_WER,
        asr_model: str = asr.DEFAULT_MODEL, max_attempts: int = MAX_ATTEMPTS, retry_quarantined: bool = True,
        processor: Callable[[Job], Result] = process_job, clock: Callable[[], datetime] = datetime.now,
        log: Callable[[str], None] = print) -> dict:
    """Work the queue until it is empty or `until` passes; returns a summary. Safe to kill and rerun at any point."""
    with conn:  # a killed run leaves jobs 'running'
        conn.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")
    queued = sync_jobs(conn, voice_id, narrator_voice_id)
    if retry_quarantined:
        with conn:
            conn.execute("UPDATE jobs SET status = 'pending', attempts = 0 WHERE status = 'quarantined'")
    consume_actions(conn, log)
    audio_dir.mkdir(parents=True, exist_ok=True)
    this_run: Counter = Counter()
    paused = False
    executor = _executor(workers)
    in_flight: dict[Future, Job] = {}
    try:
        while True:
            while len(in_flight) < max(1, workers) and not paused:
                if until is not None and clock() >= until:
                    paused = True
                    log(f"--until {until:%H:%M} reached: finishing in-flight lines, taking no new ones")
                    break
                job = claim(conn, audio_dir, wer_threshold=wer_threshold, asr_model=asr_model)
                if job is None:
                    break
                in_flight[executor.submit(processor, job)] = job
            if not in_flight:
                break
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            broken = False
            for fut in done:
                job = in_flight.pop(fut)
                try:
                    result = fut.result()
                except BrokenProcessPool as e:
                    result, broken = Result(False, f"error: worker crashed: {e}"), True
                except Exception as e:
                    result = Result(False, f"error: {type(e).__name__}: {e}")
                status = record(conn, job, result, max_attempts)
                this_run[status] += 1
                left = conn.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('pending', 'running')").fetchone()[0]
                detail = f"wer={result.wer:.2f}" if result.wer is not None else ""
                log(f"line {job.line_id} [{job.voice_id}] {status} {detail} {result.reason or ''}".rstrip()
                    + f" ({left} left)")
            if broken:  # every other future of a broken pool fails too; charge them a take and start a new pool
                for fut, job in in_flight.items():
                    this_run[record(conn, job, Result(False, "error: worker crashed"), max_attempts)] += 1
                in_flight.clear()
                executor.shutdown(wait=False, cancel_futures=True)
                executor = _executor(workers)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        with conn:
            conn.execute("UPDATE jobs SET status = 'pending' WHERE status = 'running'")
    return {"queued": queued, "this_run": dict(this_run), "paused": paused, "jobs": counts(conn)}


def summary_text(summary: dict) -> str:
    jobs, ran = summary["jobs"], summary["this_run"]
    head = "paused at --until" if summary["paused"] else "finished"
    return (f"{head}: {ran.get('done', 0)} lines done this run, {ran.get('quarantined', 0)} quarantined. "
            f"Totals: {jobs.get('done', 0)} done, {jobs.get('pending', 0)} pending, "
            f"{jobs.get('quarantined', 0)} quarantined, {jobs.get('skipped', 0)} skipped.")


# --- process-level plumbing ------------------------------------------------------------------------------------------

@contextmanager
def caffeinate(enabled: bool = True):
    """Keep the Mac awake (`caffeinate -dis`) while the block runs; -w ties it to this process even if killed."""
    if not enabled or not shutil.which("caffeinate"):
        yield None
        return
    proc = subprocess.Popen(["caffeinate", "-dis", "-w", str(os.getpid())])
    try:
        yield proc
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def notify(url: str | None, title: str, message: str, log: Callable[[str], None] = print) -> bool:
    """POST to an ntfy topic URL. No URL, no network. Never raises."""
    if not url:
        return False
    try:
        req = urllib.request.Request(url, data=message.encode(), method="POST", headers={"Title": title})
        with urllib.request.urlopen(req, timeout=10):
            return True
    except Exception as e:
        log(f"ntfy failed: {e}")
        return False


def run_notified(ntfy_url: str | None, fn: Callable[[], dict], log: Callable[[str], None] = print) -> dict:
    """Call `fn` (a configured run); notify on finish or fatal error."""
    try:
        summary = fn()
    except Exception as e:
        notify(ntfy_url, "vo run failed", f"{type(e).__name__}: {e}", log)
        raise
    notify(ntfy_url, "vo run finished", summary_text(summary), log)
    return summary
