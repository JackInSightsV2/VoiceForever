"""`vo ingest`: import Capture records from the Core Addon's SavedVariables (VoiceForeverDB.capture).

Each record lands once in `capture` (de-duplicated across uploads by its Capture key). New records then feed lines:

- a miss whose text no line has becomes a `source='capture'` line (Quest Text by quest ID, Gossip by NPC);
- a Drift record replaces the matching line's text with the captured wording (the old text goes to `line_history`),
  so `vo run` requeues it. The line keeps its Source Data wording in `source_text`, so `vo extract` doesn't revert it;
  a Drift record whose line doesn't exist yet is retried by each `vo extract --all` (`retry_drift`);
- an NPC the pipeline doesn't know becomes a `source='capture'` NPC, gendered by UnitSex and flagged for review.

The client shows text with the player's name, race and class filled in and `$G` resolved. The addon's hash masks
name, race and class, so they are recovered by finding the words whose masking reproduces that hash; the player's
gender isn't recorded, so Capture lines have player_gender NULL.
"""
import hashlib
import itertools
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from vo import drift, savedvars, text

TYPES = {"QUEST_DETAIL": "quest_detail", "QUEST_PROGRESS": "quest_progress", "QUEST_COMPLETE": "quest_complete",
         "QUEST_GREETING": "quest_greeting", "GOSSIP_SHOW": "gossip"}
QUEST_TYPES = {"quest_detail", "quest_progress", "quest_complete"}
ENGLISH = {"enUS", "enGB"}  # lines and Drift come from English clients only; other locales are stored, not voiced
NPC_GUIDS = {"Creature", "Vehicle"}
UNIT_SEX = {2: "male", 3: "female"}
MAX_TEXT = 64 * 1024
# enUS UnitRace()/UnitClass() names the addon may have masked. Only those present in a text are tried.
RACES = ["Human", "Dwarf", "Night Elf", "Gnome", "Orc", "Undead", "Tauren", "Troll", "Skyborne", "Blood Elf",
         "Draenei", "Goblin", "Worgen", "Pandaren"]
CLASSES = ["Warrior", "Paladin", "Hunter", "Rogue", "Priest", "Shaman", "Mage", "Warlock", "Druid", "Death Knight",
           "Monk", "Demon Hunter", "Evoker"]
_HASH = re.compile(r"[0-9a-f]{8}")
_WORD = re.compile(rb"[A-Za-z0-9\x80-\xff]+")
_LOWER = bytes.maketrans(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ", b"abcdefghijklmnopqrstuvwxyz")


# --- Re-tokenising displayed text (a byte-exact port of Drift.lua's VF.Mask) --------------------------------------

def _is_word_byte(b: int | None) -> bool:
    return b is not None and (b >= 128 or 48 <= b <= 57 or 65 <= b <= 90 or 97 <= b <= 122)


def mask_word(s: bytes, word: bytes, placeholder: bytes) -> bytes:
    """Whole-word, ASCII-case-insensitive replacement of `word`, as Drift.lua's maskWord."""
    word = drift.normalise(word.decode("utf-8", "replace")).encode()
    if not word:
        return s
    lower, target, out, pos = s.translate(_LOWER), word.translate(_LOWER), [], 0
    while (start := lower.find(target, pos)) >= 0:
        end = start + len(target)
        if not _is_word_byte(s[start - 1] if start > 0 else None) and not _is_word_byte(s[end] if end < len(s) else None):
            out += [s[pos:start], placeholder]
            pos = end
        else:
            out.append(s[pos:start + 1])
            pos = start + 1
    out.append(s[pos:])
    return b"".join(out)


def mask(s: bytes, name: bytes | None, race: bytes | None, cls: bytes | None) -> bytes:
    for word, placeholder in ((name, b"$N"), (race, b"$R"), (cls, b"$C")):
        if word:
            s = mask_word(s, word, placeholder)
    return s


def player_words(displayed: str, hash_: str) -> tuple[bytes | None, bytes | None, bytes | None] | None:
    """The (name, race, class) whose masking reproduces the addon's hash, fewest first; None if nothing does."""
    norm = drift.normalise(displayed).encode()
    names = [None] + sorted({w for w in _WORD.findall(norm) if w[0] >= 128 or 65 <= w[0] <= 90})
    races = [None] + [r.encode() for r in RACES if mask_word(norm, r.encode(), b"\0") != norm]
    classes = [None] + [c.encode() for c in CLASSES if mask_word(norm, c.encode(), b"\0") != norm]
    combos = sorted(itertools.product(names, races, classes), key=lambda c: sum(w is not None for w in c))
    for combo in combos:
        if drift.fnv1a32(mask(norm, *combo)) == hash_:
            return combo
    return None


def retokenise(displayed: str, hash_: str) -> tuple[str, bool]:
    """Displayed text as a raw line: player words back to $N/$R/$C, line breaks as $B, colour codes kept out.
    Returns (raw, True) when the result hashes to the addon's hash, else (the untokenised text, False)."""
    words = player_words(displayed, hash_)
    body = displayed.replace("\r\n", "\n")
    body = re.sub(r"\|c[0-9a-fA-F]{8}", "", body).replace("|r", "")
    if words:
        body = mask(body.encode(), *words).decode("utf-8", "replace")
    raw = re.sub(r"[ \t]*\n[ \t]*", "$B", body.strip())
    ok = words is not None and drift.text_hash(raw) == hash_
    return raw, ok


# --- Records ----------------------------------------------------------------------------------------------------

def _int(v) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or abs(v) > 2 ** 53:
        return None
    return int(v) if float(v).is_integer() else None


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e9 else None


def _str(v, limit: int = 256) -> str | None:
    return v[:limit] if isinstance(v, str) else None


def record(r) -> dict | None:
    """A validated Capture record, or None if it isn't one."""
    if not isinstance(r, dict):
        return None
    kind, event, raw, hash_ = r.get("kind"), r.get("event"), r.get("text"), r.get("hash")
    if kind not in ("miss", "drift") or event not in TYPES or not isinstance(raw, str) or len(raw) > MAX_TEXT:
        return None
    if not isinstance(hash_, str) or not _HASH.fullmatch(hash_):
        return None
    expected = r.get("expected")
    if kind == "drift" and not (isinstance(expected, str) and _HASH.fullmatch(expected)):
        return None
    quest_id, npc_id, seen = _int(r.get("questId")), _int(r.get("npcId")), _int(r.get("seenAt"))
    return dict(
        kind=kind, event=event, type=TYPES[event], text=raw, hash=hash_,
        expected=expected if kind == "drift" else None,
        quest_id=quest_id if quest_id and quest_id > 0 else None,
        npc_id=npc_id if npc_id and npc_id > 0 else None,
        guid_type=_str(r.get("guidType"), 32), npc_name=_str(r.get("npcName")), unit_sex=_int(r.get("unitSex")),
        creature_type=_str(r.get("creatureType"), 64), zone=_int(r.get("zone")),
        x=_num(r.get("x")), y=_num(r.get("y")), locale=_str(r.get("locale"), 8),
        seen_at=datetime.fromtimestamp(seen, timezone.utc).isoformat() if seen and 0 < seen < 2 ** 34 else None)


def record_key(r: dict) -> str:
    """Capture.lua's de-duplication key (npcId:event:questId:hash), plus the kind."""
    return ":".join(str(v if v is not None else "") for v in (r["kind"], r["npc_id"], r["event"], r["quest_id"], r["hash"]))


def read_records(data: bytes) -> list:
    """VoiceForeverDB.capture from a SavedVariables file's bytes, as parsed."""
    db = savedvars.loads(data).get("VoiceForeverDB")
    capture = db.get("capture") if isinstance(db, dict) else None
    if capture is None:
        raise savedvars.Malformed("no VoiceForeverDB.capture table")
    if isinstance(capture, dict):  # sparse: keep the numbered records in order
        capture = [capture[k] for k in sorted(k for k in capture if type(k) is int)]
    if not isinstance(capture, list):
        raise savedvars.Malformed("VoiceForeverDB.capture isn't a table")
    return capture


# --- Applying records -------------------------------------------------------------------------------------------

def _ensure_npc(conn: sqlite3.Connection, r: dict, counts: Counter) -> None:
    if r["npc_id"] is None or r["guid_type"] not in NPC_GUIDS:
        return
    if conn.execute("SELECT 1 FROM npcs WHERE id = ?", (r["npc_id"],)).fetchone():
        return
    gender = UNIT_SEX.get(r["unit_sex"])
    conn.execute("INSERT INTO npcs (id, name, gender, source) VALUES (?, ?, ?, 'capture')",
                 (r["npc_id"], r["npc_name"], gender))
    issues = [("capture_only", f"UnitSex {r['unit_sex']}, creature type {r['creature_type']}"),
              ("race_unresolved", r["creature_type"])]
    if gender is None:
        issues.append(("gender_unresolved", f"UnitSex {r['unit_sex']}"))
    conn.executemany("INSERT INTO npc_issues VALUES (?, ?, ?)", [(r["npc_id"], i, d) for i, d in issues])
    counts["new_npcs"] += 1


def _replace_text(conn: sqlite3.Connection, line: sqlite3.Row, r: dict, capture_id: int, reason: str) -> None:
    raw, ok = retokenise(r["text"], r["hash"])
    conn.execute("INSERT INTO line_history (line_id, raw_text, tts_text, text_hash, reason, capture_id)"
                 " VALUES (?, ?, ?, ?, ?, ?)", (line["id"], line["raw_text"], line["tts_text"], line["text_hash"],
                                                reason, capture_id))
    conn.execute("UPDATE lines SET raw_text = ?, tts_text = ?, text_hash = ?,"
                 " source_text = CASE WHEN source = 'core' THEN COALESCE(source_text, raw_text) END WHERE id = ?",
                 (raw, text.prepare(raw, line["player_gender"]), r["hash"], line["id"]))
    if not ok:
        conn.execute("INSERT INTO line_issues VALUES (?, 'untokenised', ?)", (line["id"], _UNTOKENISED))


_UNTOKENISED = "Capture text: the player's name, race or class couldn't be re-tokenised and may be spoken"


def _drift(conn: sqlite3.Connection, r: dict, capture_id: int, counts: Counter) -> None:
    lines = conn.execute("SELECT * FROM lines WHERE type = ? AND quest_id IS ? AND text_hash = ?",
                         (r["type"], r["quest_id"], r["expected"])).fetchall()
    if not lines:
        already = conn.execute("SELECT 1 FROM lines WHERE type = ? AND quest_id IS ? AND text_hash = ?",
                               (r["type"], r["quest_id"], r["hash"])).fetchone()
        counts["known" if already else "unmatched"] += 1
        return
    for line in lines:
        _replace_text(conn, line, r, capture_id, "drift")
        counts["drift_updates"] += 1


def retry_drift(conn: sqlite3.Connection) -> int:
    """Apply the stored English Drift records that haven't updated a line yet (e.g. ingested before `vo extract`
    created their core line). Returns how many lines they updated."""
    counts = Counter()
    rows = conn.execute(
        f"SELECT * FROM capture c WHERE kind = 'drift' AND locale IN {tuple(sorted(ENGLISH))}"
        " AND NOT EXISTS (SELECT 1 FROM line_history h WHERE h.capture_id = c.id) ORDER BY id").fetchall()
    for c in rows:
        if c["event"] in TYPES:
            r = dict(type=TYPES[c["event"]], quest_id=c["quest_id"], expected=c["expected_hash"], hash=c["text_hash"],
                     text=c["text"])
            _drift(conn, r, c["id"], counts)
    return counts["drift_updates"]


def _miss(conn: sqlite3.Connection, r: dict, capture_id: int, counts: Counter) -> None:
    if r["type"] in QUEST_TYPES:
        if r["quest_id"] is None:
            counts["skipped"] += 1
            return
        lines = conn.execute("SELECT * FROM lines WHERE type = ? AND quest_id = ?", (r["type"], r["quest_id"])).fetchall()
    else:
        if r["npc_id"] is None:  # Gossip belongs to an NPC
            counts["skipped"] += 1
            return
        lines = conn.execute("SELECT * FROM lines WHERE type = ? AND npc_id = ?", (r["type"], r["npc_id"])).fetchall()
    if any(line["text_hash"] == r["hash"] for line in lines):
        counts["known"] += 1  # Core Content not voiced yet, or already captured
        return
    core = [line for line in lines if line["source"] in ("core", "wowhead")]
    if r["type"] in QUEST_TYPES and core:
        # Quest Text the Source Data (or Wowhead) has, worded differently: Drift, if we can tell which variant it is.
        if len(core) == 1 and core[0]["player_gender"] is None:
            _replace_text(conn, core[0], r, capture_id, "drift (miss)")
            counts["drift_updates"] += 1
        else:
            counts["unmatched"] += 1
        return
    raw, ok = retokenise(r["text"], r["hash"])
    if not raw or text.GENDER.search(raw):
        counts["skipped"] += 1
        return
    (line_id,) = conn.execute(
        "INSERT INTO lines (npc_id, type, quest_id, player_gender, raw_text, tts_text, text_hash, source)"
        " VALUES (?, ?, ?, NULL, ?, ?, ?, 'capture') RETURNING id",
        (r["npc_id"], r["type"], r["quest_id"] if r["type"] in QUEST_TYPES else None, raw,
         text.prepare(raw, None), r["hash"])).fetchone()
    if not ok:
        conn.execute("INSERT INTO line_issues VALUES (?, 'untokenised', ?)", (line_id, _UNTOKENISED))
    counts["new_lines"] += 1


CAPTURE_COLS = ("upload_id", "record_key", "kind", "npc_id", "guid_type", "npc_name", "unit_sex", "creature_type",
                "zone", "x", "y", "event", "quest_id", "text", "text_hash", "expected_hash", "locale", "seen_at",
                "ingested_at")


def ingest_file(conn: sqlite3.Connection, path: Path) -> tuple[str, Counter]:
    """Import one SavedVariables file in one transaction; return its upload id (the file's hash) and counts.
    Raises savedvars.Malformed (and writes nothing) for a file that isn't a Capture SavedVariables file."""
    size = path.stat().st_size
    if size > savedvars.MAX_BYTES:
        raise savedvars.Malformed(f"{size} bytes is over the {savedvars.MAX_BYTES}-byte limit")
    data = path.read_bytes()
    upload_id = hashlib.sha256(data).hexdigest()[:16]
    records = read_records(data)
    counts = Counter(read=len(records))
    ingested_at = datetime.now().isoformat(timespec="seconds")
    with conn:
        for raw in records:
            r = record(raw)
            if r is None:
                counts["invalid"] += 1
                continue
            values = dict(r, upload_id=upload_id, record_key=record_key(r), text_hash=r["hash"],
                          expected_hash=r["expected"], ingested_at=ingested_at)
            row = conn.execute(
                f"INSERT INTO capture ({', '.join(CAPTURE_COLS)}) VALUES ({', '.join('?' * len(CAPTURE_COLS))})"
                " ON CONFLICT (record_key) DO NOTHING RETURNING id", [values[c] for c in CAPTURE_COLS]).fetchone()
            if row is None:
                counts["duplicate"] += 1
                continue
            counts["new"] += 1
            _ensure_npc(conn, r, counts)
            if r["locale"] not in ENGLISH:
                counts["other_locale"] += 1
            elif r["kind"] == "drift":
                _drift(conn, r, row[0], counts)
            else:
                _miss(conn, r, row[0], counts)
    return upload_id, counts


def summary_text(path: Path, upload_id: str, c: Counter) -> str:
    out = (f"{path}: upload {upload_id}: {c['read']} records read, {c['new']} new, {c['duplicate']} duplicate"
           f"{f', {c['invalid']} invalid' if c['invalid'] else ''}; {c['new_lines']} new lines,"
           f" {c['drift_updates']} drift updates, {c['new_npcs']} new NPCs")
    extra = [f"{c[k]} {k.replace('_', ' ')}" for k in ("known", "unmatched", "other_locale", "skipped") if c[k]]
    return out + (f" ({', '.join(extra)})" if extra else "")
