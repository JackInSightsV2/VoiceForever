"""The Lexicon (#12): phonetic respellings of lore names (Kel'Thuzad -> "Kel-thoo-zahd") so the TTS says them right.

1. Extraction: capitalised tokens in the spoken form of every line (`text.prepare`, before the Lexicon) that are not
   English words (misaki's gold lexicon plus a small bundled list), not inflections of one and not compounds of
   English words ("Stormwind" reads as written). Apostrophe names (Kel'Thuzad, Ahn'Qiraj) are one token; a possessive
   or contraction ("Thrall's") is not part of the name. Ranked by how many lines say the name.
2. Drafts: the seed table (data/lexicon_seed.json, community pronunciations with their sources) else rules that fix
   what an English reader gets wrong (q -> k, initial x -> z, a final i -> ee, apostrophe parts hyphenated, ...).
3. Review: the top TOP names are accepted or corrected on the dashboard's Approval page (review_actions, applied by
   `vo prepare`, which also renders each one's sample). Once all are reviewed, `vo prepare` writes the read-only
   lexicon.json; `vo run` refuses to start without it (like approved_voices.json).
4. Every other name is drafted automatically. When ASR misses a name in MISS_LINES different lines, `vo run` moves it
   to its next alternative respelling (`alternatives()`) and requeues the lines that say it.

State lives in the `lexicon` table (one row per name; `spelling` is the one in use) and `lexicon_misses`.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Callable, Iterable

from vo import lock, text

TOP = 300
MISS_LINES = 3  # an auto name moves to its next respelling once ASR missed it in this many different lines
ENV = "VO_LEXICON"
FILENAME = "lexicon.json"
DATA = resources.files("vo") / "data"
REVIEWED = ("accepted", "corrected")
# status: pending (top name awaiting review) | accepted | corrected | auto (drafted, ASR-checked)

APOS = "['’]"
TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
CONTRACTIONS = {"s", "ll", "re", "ve", "d", "t", "m"}
ROMAN = re.compile(r"[IVXLCDM]+")

LockError = lock.LockError


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def key(name: str) -> str:
    """Lookup key: case-folded, one apostrophe form."""
    return re.sub(APOS, "'", name).lower()


# --- 1. extraction ---------------------------------------------------------------------------------------------------

@cache
def english() -> frozenset[str]:
    """Lower-cased English words: misaki's US gold lexicon (a pipeline dependency) plus data/lexicon_english.txt
    (interjections, common given names, archaic words the gold list lacks)."""
    words = {w.strip().lower() for w in (DATA / "lexicon_english.txt").read_text().splitlines()
             if w.strip() and not w.startswith("#")}
    try:
        gold = json.loads((resources.files("misaki") / "data" / "us_gold.json").read_text())
        words |= {k.lower() for k in gold}
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    return frozenset(words)


INFLECTIONS = (("s", ""), ("es", ""), ("ies", "y"), ("ed", ""), ("ed", "e"), ("ing", ""), ("ing", "e"),
               ("er", ""), ("ers", ""), ("en", ""), ("ern", ""), ("ish", ""))


def is_english(word: str) -> bool:
    """An English word or a regular inflection of one (Gnolls, Barrens, Dwarven)."""
    w = word.lower()
    words = english()
    if w in words:
        return True
    return any(w.endswith(suf) and len(w) > len(suf) + 2 and w[:-len(suf)] + rep in words for suf, rep in INFLECTIONS)


# Three-letter words common in lore compounds (Warsong, Redridge, Razorfen, Timbermaw). Other parts need 4+ letters,
# so a name isn't split into chance short words (Orgrimmar is not "org" + "rim" + "mar", Arthas not "art" + "has").
SHORT_PARTS = {"war", "red", "sky", "sea", "ash", "fel", "sun", "owl", "elf", "orc", "oak", "ice", "bog", "fen", "maw",
               "cut", "box", "den", "mar", "saw", "tar", "gut", "rot", "axe", "far", "low", "top", "end", "dew"}


def _part_ok(p: str, last: bool) -> bool:
    if len(p) < 3 or (len(p) == 3 and p not in SHORT_PARTS):
        return False
    return is_english(p) if last else p in english()


def is_compound(word: str, parts: int = 3) -> bool:
    """Made of English words (Stormwind, Winterspring, Plaguelands, Warsong): reads as written."""
    w = word.lower()
    for i in range(3, len(w) - 2):
        head, tail = w[:i], w[i:]
        if _part_ok(head, False) and (_part_ok(tail, True) or (parts > 2 and is_compound(tail, parts - 1))):
            return True
    return False


def name_of(token: str) -> str | None:
    """The lore name in a token, or None: "Thrall's" -> None (English), "Kel'Thuzad's" -> "Kel'Thuzad"."""
    parts = re.split(APOS, token)
    if len(parts) > 1 and parts[-1].lower() in CONTRACTIONS:
        if parts[-1].lower() == "t" and parts[-2].lower().endswith("n"):
            return None  # didn't, won't
        parts = parts[:-1]
    name = "'".join(parts)
    if not name[:1].isupper() or len(name) < 3 or ROMAN.fullmatch(name):
        return None
    if len(parts) == 1 and (is_english(name) or is_compound(name)):
        return None
    if len(parts) > 1 and all(is_english(p) for p in parts):
        return None  # o'clock-like
    return name


def names_in(spoken: str) -> set[str]:
    return {n for m in TOKEN.finditer(spoken) if (n := name_of(m[0]))}


@dataclass
class Name:
    name: str
    lines: int
    npc: bool = False    # part of an NPC's name
    zone: bool = False   # part of a zone or area name
    example: int | None = None  # a line id that says it


def known_names(conn: sqlite3.Connection, areas: Iterable[str] = ()) -> tuple[set[str], set[str]]:
    """Name tokens of NPCs (npcs.name) and of zones/areas (e.g. the world DB's area_template), lower-cased keys."""
    npcs = {key(m[0]) for (n,) in conn.execute("SELECT name FROM npcs WHERE name IS NOT NULL") for m in TOKEN.finditer(n)}
    zones = {key(m[0]) for a in areas for m in TOKEN.finditer(a)}
    return npcs, zones


def extract(conn: sqlite3.Connection, areas: Iterable[str] = ()) -> list[Name]:
    """Every lore name in the lines, most lines first (ties by name). Counts lines, not occurrences."""
    npcs, zones = known_names(conn, areas)
    lines: dict[str, set[int]] = {}
    spellings: dict[str, dict[str, int]] = {}
    for line_id, raw, gender in conn.execute("SELECT id, raw_text, player_gender FROM lines ORDER BY id"):
        try:
            spoken = text.prepare(raw or "", gender)
        except ValueError:
            continue
        for n in names_in(spoken):
            k = key(n)
            spellings.setdefault(k, {}).setdefault(n, 0)
            spellings[k][n] += 1
            lines.setdefault(k, set()).add(line_id)
    for k in [k for k in lines if k.endswith("s") and k[:-1] in lines]:  # "Barovs", "Gnolls": the Lexicon's
        lines[k[:-1]] |= lines.pop(k)                                   # plural handling covers them
        spellings.pop(k)
    found = []
    for k, ids in lines.items():
        name = max(spellings[k].items(), key=lambda kv: (kv[1], kv[0]))[0]  # "Rut'theran" over "Rut'Theran"
        found.append(Name(name, len(ids), k in npcs, k in zones, min(ids)))
    return sorted(found, key=lambda n: (-n.lines, n.name))


# --- 2. drafts -------------------------------------------------------------------------------------------------------

@cache
def seed_table() -> dict[str, str]:
    """{key: respelling} from data/lexicon_seed.json."""
    data = json.loads((DATA / "lexicon_seed.json").read_text())
    return {key(n): e["respelling"] for n, e in data["entries"].items()}


def _part(p: str) -> str:
    """Rule respelling of one apostrophe-separated part (letters only)."""
    w = p.lower()
    w = re.sub(r"^x", "z", w)                   # Xavius
    w = re.sub(r"^y(?=[^aeiou])", "ee", w)      # Ysida
    w = re.sub(r"^kh", "k", w)                  # Kharanos, Khaz
    w = re.sub(r"que$", "k", w)                 # Mekkatorque
    w = w.replace("qu", "kw").replace("q", "k")  # Qiraji, Quel
    w = w.replace("ph", "f").replace("ae", "ay").replace("aa", "ah").replace("ii", "ee").replace("uu", "oo")
    if w == "ai":
        w = "eye"                               # Atal'ai
    w = re.sub(r"(?<=[^aeiou])i$", "ee", w)     # Arathi, Hakkari, Magni
    return w


def rule_draft(name: str) -> str:
    """Rules only: each apostrophe part respelled, joined with hyphens (an apostrophe isn't read as a pause, and can
    read as a possessive), first letter capitalised. A one-part name the rules don't change is kept as written."""
    parts = re.split(APOS, name)
    out = [_part(p) for p in parts]
    if len(parts) == 1 and out[0] == name.lower():
        return name
    if out == [p.lower() for p in parts]:  # only the apostrophes change: keep the name's own capitals
        out = parts
    joined = "-".join(out)
    return joined[:1].upper() + joined[1:]


def draft(name: str) -> str:
    """The seed table's respelling, else the rules'."""
    return seed_table().get(key(name)) or rule_draft(name)


def _syllables(word: str) -> str:
    """A crude syllable split for an alternative respelling: V-CV and VC-CV (Ma-rau-don, Zzor-goth)."""
    w = re.sub(APOS, "", word.lower())
    w = re.sub(r"(?<=[aeiouy])(?=[^aeiouy-][aeiouy])|(?<=[aeiouy][^aeiouy-])(?=[^aeiouy-][aeiouy])", "-", w)
    return w[:1].upper() + w[1:]


def alternatives(name: str) -> list[str]:
    """Respellings to try in turn when ASR keeps missing an auto name: the draft, its hyphens dropped, a syllable
    split of the written name, then the name as written."""
    d = draft(name)
    joined = re.sub(r"[-\s]+", "", d).lower()
    out = []
    for alt in (d, joined[:1].upper() + joined[1:], _syllables(d), name):
        if alt and alt not in out:
            out.append(alt)
    return out


# --- 3. applying it --------------------------------------------------------------------------------------------------

def _pattern(name: str) -> str:
    return APOS.join(r"\s+".join(re.escape(w) for w in part.split()) for part in re.split(APOS, name))


class Lexicon:
    """Callable text -> text (a `text.Lexicon`): every name respelled, whole words only. A possessive or plural
    ("Barov's", "Barovs", "Barovs'") keeps its ending; a name inside a longer token (Kel in Kel'Thuzad, Ahn'Qiraj in Ahn'Qiraji) is left
    alone; the name must start with a capital (ALL CAPS matches too)."""

    def __init__(self, spellings: dict[str, str] | None = None):
        self.spellings: dict[str, str] = {}   # key -> spelling
        self._written: dict[str, str] = {}    # key -> name as written
        self._re: re.Pattern | None = None
        self._back: re.Pattern | None = None
        for n, s in (spellings or {}).items():
            self.set(n, s)

    def set(self, name: str, spelling: str) -> None:
        self.spellings[key(name)] = spelling
        self._written[key(name)] = name
        self._re = self._back = None

    def _compiled(self) -> re.Pattern | None:
        if self._re is None and self.spellings:
            alts = "|".join(_pattern(self._written[k]) for k in sorted(self.spellings, key=lambda k: (-len(k), k)))
            self._re = re.compile(rf"(?<![\w'’])({alts})(?=(?:s|['’]s|s['’]|['’])?(?![\w'’]))", re.I)
        return self._re

    def __call__(self, s: str) -> str:
        pattern = self._compiled()
        if pattern is None:
            return s

        def repl(m: re.Match) -> str:
            if not m[1][0].isupper():
                return m[0]
            return self.spellings[key(re.sub(r"\s+", " ", m[1]))]
        return pattern.sub(repl, s)

    def pairs(self, spoken: str) -> list[tuple[str, str]]:
        """(spelling, written name) for every respelled name in already-spoken text (for the ASR check)."""
        if self._back is None:
            if not self.spellings:
                return []
            spells = sorted({s for s in self.spellings.values() if s}, key=lambda s: (-len(s), s))
            self._back = re.compile(rf"(?<![\w'’-])({'|'.join(re.escape(s) for s in spells)})(?![\w'’-])")
            self._by_spelling: dict[str, str] = {}
            for k, s in sorted(self.spellings.items()):
                self._by_spelling.setdefault(s, self._written[k])
        out = []
        for m in self._back.finditer(spoken):
            pair = (m[1], self._by_spelling[m[1]])
            if pair not in out:
                out.append(pair)
        return out


def from_db(conn: sqlite3.Connection, locked: dict | None = None) -> Lexicon:
    """The Lexicon in use: every name's current spelling from the `lexicon` table, the lock's reviewed spellings
    overriding. A name spelled as written stays in, so ASR still checks it."""
    spellings = {r["name"]: r["spelling"] for r in conn.execute("SELECT name, spelling FROM lexicon")}
    for n, e in (locked or {}).get("entries", {}).items():
        spellings[n] = e["spelling"]
    return Lexicon({n: s for n, s in spellings.items() if s})


def lines_saying(conn: sqlite3.Connection, names: Iterable[str]) -> list[int]:
    """Lines whose raw text mentions any of the names (case-insensitive, either apostrophe)."""
    ids: set[int] = set()
    for n in names:
        like = [f"%{v}%" for v in {n, n.replace("'", "’")}]
        ids |= {r[0] for r in conn.execute(
            f"SELECT id FROM lines WHERE {' OR '.join('raw_text LIKE ?' for _ in like)}", like)}
    return sorted(ids)


def respell_lines(conn: sqlite3.Connection, lex: Lexicon, line_ids: Iterable[int]) -> list[int]:
    """Recompute tts_text for these lines with the Lexicon (hand-edited lines are kept); returns the changed ids."""
    from vo import prep
    edited, changed = prep.manual_edits(conn), []
    for line_id in line_ids:
        r = conn.execute("SELECT raw_text, player_gender, tts_text FROM lines WHERE id = ?", (line_id,)).fetchone()
        if r is None or line_id in edited:
            continue
        spoken = text.prepare(r["raw_text"], r["player_gender"], lex)
        if spoken != r["tts_text"]:
            conn.execute("UPDATE lines SET tts_text = ? WHERE id = ?", (spoken, line_id))
            changed.append(line_id)
    conn.commit()
    return changed


# --- 4. ASR: a respelling is not an error ------------------------------------------------------------------------------

def _letters(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


SOUNDS_LIKE = 0.7


def similarity(heard: str, spelling: str, written: str) -> float:
    """How close `heard` (one or a few ASR words) is to the name, spelled either way: letter-level ratio, 0..1."""
    h = _letters(heard)
    return max((difflib.SequenceMatcher(None, h, _letters(t)).ratio() for t in (spelling, written) if _letters(t)),
               default=0.0) if h else 0.0


def sounds_like(heard: str, spelling: str, written: str) -> bool:
    return similarity(heard, spelling, written) >= SOUNDS_LIKE


# --- 5. the table ----------------------------------------------------------------------------------------------------

def sync(conn: sqlite3.Connection, areas: Iterable[str] = (), top: int = TOP) -> dict[str, int]:
    """Upsert every extracted name with its rank and draft. New top-`top` names await review; the rest are auto.
    A reviewed spelling is never changed; a name that left the lines is dropped unless reviewed."""
    names = extract(conn, areas)
    now = _now()
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM lexicon")}
    by_key = {key(n): r for n, r in rows.items()}
    seen = set()
    with conn:
        for rank, n in enumerate(names, 1):
            r = by_key.get(key(n.name))
            d = draft(n.name)
            status = "pending" if rank <= top else "auto"
            if r is None:
                conn.execute("INSERT INTO lexicon (name, lines, rank, npc, zone, example_line, draft, spelling, status,"
                             " alt, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                             (n.name, n.lines, rank, n.npc, n.zone, n.example, d, d, status, now))
            else:
                seen.add(r["name"])
                if r["status"] in REVIEWED:
                    status, spelling, alt = r["status"], r["spelling"], r["alt"]
                elif status == "auto":
                    alts = alternatives(r["name"])
                    alt = min(r["alt"], len(alts) - 1)
                    spelling = alts[alt]
                else:
                    spelling, alt = d, 0
                conn.execute("UPDATE lexicon SET lines = ?, rank = ?, npc = ?, zone = ?, example_line = ?, draft = ?,"
                             " spelling = ?, status = ?, alt = ?, updated_at = ? WHERE name = ?",
                             (n.lines, rank, n.npc, n.zone, n.example, d, spelling, status, alt, now, r["name"]))
        gone = [n for n in rows if n not in seen and key(n) not in {key(x.name) for x in names}]
        for n in gone:
            if rows[n]["status"] in REVIEWED:
                conn.execute("UPDATE lexicon SET lines = 0, rank = NULL WHERE name = ?", (n,))
            else:
                conn.execute("DELETE FROM lexicon WHERE name = ?", (n,))
    return {"names": len(names), "top": min(top, len(names))}


def progress(conn: sqlite3.Connection, top: int = TOP) -> tuple[int, int]:
    """(reviewed, total) of the top names."""
    r = conn.execute(f"SELECT COUNT(*), SUM(status IN {REVIEWED}) FROM lexicon WHERE rank <= ?", (top,)).fetchone()
    return int(r[1] or 0), int(r[0])


# --- 6. review actions (consumed by `vo prepare`) --------------------------------------------------------------------

MAX_SPELLING = 120


def _row(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM lexicon WHERE name = ?", (name,)).fetchone()
    if r is None:
        raise ValueError(f"no Lexicon name {name!r}")
    return r


def _accept(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    r = _row(conn, row["target"])
    payload = json.loads(row["payload"]) if row["payload"] else {}
    spelling = str(payload.get("spelling") or r["spelling"]).strip()  # the spelling the reviewer heard
    conn.execute("UPDATE lexicon SET status = 'accepted', spelling = ?, reviewed_at = ?, updated_at = ? WHERE name = ?",
                 (spelling, _now(), _now(), r["name"]))
    return f"Lexicon {r['name']}: accepted {spelling!r}"


def _correct(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    r = _row(conn, row["target"])
    spelling = str((json.loads(row["payload"]) if row["payload"] else {}).get("spelling") or "").strip()
    if not spelling or len(spelling) > MAX_SPELLING or "\n" in spelling:
        raise ValueError("a correction needs a one-line spelling")
    conn.execute("UPDATE lexicon SET status = 'corrected', spelling = ?, reviewed_at = ?, updated_at = ? WHERE name = ?",
                 (spelling, _now(), _now(), r["name"]))
    return f"Lexicon {r['name']}: corrected to {spelling!r}"


ACTIONS: dict[str, Callable[[sqlite3.Connection, sqlite3.Row], str]] = {
    "accept-lexicon": _accept,
    "correct-lexicon": _correct,
}


# --- 7. samples for review -------------------------------------------------------------------------------------------

SAMPLE_LEN = (25, 200)


def sample_sentence(raw: str, gender: str | None, name: str) -> str:
    """The shortest reviewable sentence of a line that says the name (else the whole spoken line)."""
    spoken = text.prepare(raw, gender)
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n", spoken) if key(name) in key(s)]
    ok = [s for s in sentences if SAMPLE_LEN[0] <= len(s) <= SAMPLE_LEN[1]]
    pick = min(ok, key=lambda s: (abs(len(s) - 90), s)) if ok else (sentences[0] if sentences else spoken)
    return pick.strip()


# --- 8. the lock -----------------------------------------------------------------------------------------------------

HOW = ("Run `vo prepare`, accept or correct each of the Lexicon's top names on the dashboard's Approval page "
       "(`vo dashboard`, /approval), then run `vo prepare` again to apply them and write the lock.")


def default_path() -> Path:
    return Path(__file__).resolve().parents[3] / "build" / FILENAME


def path() -> Path:
    return Path(os.environ.get(ENV) or default_path())


def lock_data(conn: sqlite3.Connection, top: int = TOP) -> dict | None:
    """lexicon.json's content once every top name is reviewed, else None. Holds every reviewed name."""
    reviewed, total = progress(conn, top)
    if reviewed < total:
        return None
    entries = {r["name"]: {"spelling": r["spelling"], "draft": r["draft"], "status": r["status"], "lines": r["lines"]}
               for r in conn.execute(f"SELECT * FROM lexicon WHERE status IN {REVIEWED} ORDER BY name")}
    return {"locked": True, "top": top, "entries": entries}


def update_lock(conn: sqlite3.Connection, p: Path, log: Callable[[str], None] = print, top: int = TOP) -> str:
    """Write the lock once every top name is reviewed (unchanged content keeps the file); remove a stale one."""
    data = lock_data(conn, top)
    if data is None:
        if lock.remove(p):
            log(f"removed {p}: a top Lexicon name awaits review")
        return "open"
    if p.exists():
        try:
            old = json.loads(p.read_text())
            if {k: v for k, v in old.items() if k != "locked_at"} == data:
                return "unchanged"
        except json.JSONDecodeError:
            pass
    lock.write(p, {**data, "locked_at": _now()})
    log(f"wrote {p} ({len(data['entries'])} names)")
    return "written"


def load(p: Path) -> dict:
    if not p.exists():
        raise LockError(f"{p} not found: the Lexicon isn't approved. {HOW}")
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        raise LockError(f"{p} is not valid JSON ({e}). {HOW}") from e
    if not data.get("locked") or not isinstance(data.get("entries"), dict):
        raise LockError(f"{p} is not a locked lexicon.json. {HOW}")
    return data


def require(conn: sqlite3.Connection, p: Path) -> dict:
    """The lock, checked against the DB: every current top name is in it."""
    data = load(p)
    top = int(data.get("top", TOP))
    have = {key(n) for n in data["entries"]}
    missing = [r[0] for r in conn.execute("SELECT name FROM lexicon WHERE rank <= ? ORDER BY rank", (top,))
               if key(r[0]) not in have]
    if missing:
        shown = ", ".join(missing[:10]) + (f" and {len(missing) - 10} more" if len(missing) > 10 else "")
        raise LockError(f"{p} has no approved spelling for: {shown}. {HOW}")
    return data


# --- 9. vo run: ASR misses move an auto name to its next respelling --------------------------------------------------

def after_take(conn: sqlite3.Connection, lex: Lexicon, line_id: int, spoken: str, pairs: Iterable[tuple[str, str]],
               transcript: str | None, log: Callable[[str], None] = print) -> list[int]:
    """`vo run`, after one take: note the names ASR missed; if that moved any name to a new respelling, respell the
    lines that say it. Returns the lines whose tts_text changed (for the caller to requeue)."""
    from vo import asr
    pairs = list(pairs)
    if not pairs or transcript is None:
        return []
    _, _, missed = asr.collapse_names(spoken, transcript, pairs)
    moved = note_take(conn, lex, line_id, pairs, missed, log)
    return respell_lines(conn, lex, lines_saying(conn, moved)) if moved else []


def note_take(conn: sqlite3.Connection, lex: Lexicon, line_id: int, pairs: Iterable[tuple[str, str]],
              missed: Iterable[str], log: Callable[[str], None] = print) -> list[str]:
    """Record the names ASR missed in one take of a line; an auto name missed in MISS_LINES different lines moves to
    its next alternative (misses reset) and is updated in `lex`. Returns the names that moved."""
    moved = []
    missed = set(missed)
    with conn:
        for spelling, written in pairs:
            if written not in missed:
                continue
            conn.execute("INSERT OR IGNORE INTO lexicon_misses (name, line_id, at) VALUES (?, ?, ?)",
                         (written, line_id, _now()))
            r = conn.execute("SELECT * FROM lexicon WHERE name = ?", (written,)).fetchone()
            if r is None or r["status"] != "auto":
                continue
            n = conn.execute("SELECT COUNT(*) FROM lexicon_misses WHERE name = ?", (written,)).fetchone()[0]
            alts = alternatives(written)
            if n < MISS_LINES or r["alt"] + 1 >= len(alts):
                continue
            new = alts[r["alt"] + 1]
            conn.execute("UPDATE lexicon SET alt = alt + 1, spelling = ?, updated_at = ? WHERE name = ?",
                         (new, _now(), written))
            conn.execute("DELETE FROM lexicon_misses WHERE name = ?", (written,))
            lex.set(written, new)
            log(f"Lexicon {written}: ASR missed it in {n} lines, respelled {r['spelling']!r} -> {new!r}")
            moved.append(written)
    return moved
