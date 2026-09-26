"""Forever Content from Wowhead's Forever database (https://www.wowhead.com/forever/): quests the Source Data doesn't
have (new Forever and Skyborne quests), and the NPCs that give and end them.

Pages are cached under data/wowhead/ and never fetched twice: `quest/<id>.html`, `npc/<id>.html` (a `.missing` file
beside an id Wowhead doesn't have). A page may come from `vo wowhead fetch` (polite: robots.txt, one request every
`interval` seconds, stops at the first block) or be saved there by hand from a browser ("Save Page As", HTML only),
which is the way when Wowhead blocks automated requests. `vo wowhead ingest` reads whatever is cached:

- Targets are the quest IDs Forever's client knows (QuestV2) that the VMaNGOS Source Data lacks.
- A quest page gives the title, level, zone, quest giver and ender and the Quest Text: description and objectives
  (detail), progress and completion. Wowhead shows the player tokens as <name>, <race>, <class> and <male/female>;
  they become $N, $R, $C and $Gmale:female; again, line breaks $B, as in the Source Data.
- Each part becomes a `source='wowhead'` line (per player gender when it has $G), voiced by the giver (detail) or
  ender (progress, completion), else the Narrator. A quest part with Core Content or Capture lines is left alone:
  in-game text wins, and later Capture Drift updates a Wowhead line like a core one.
- An NPC the pipeline doesn't know becomes a `source='wowhead'` NPC: race and gender from its Wowhead page's display
  (the Forever display tables, as for Core Content), else from its in-game voice set, else unresolved; it is flagged
  for review like a Capture-only NPC either way.
"""
from __future__ import annotations

import csv
import hashlib
import html as htmllib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from vo import drift, text

BASE = "https://www.wowhead.com/forever/"
ROBOTS = "https://www.wowhead.com/robots.txt"
USER_AGENT = "VoiceForever-pipeline (+https://github.com/JackInSightsV2/VoiceForever)"
INTERVAL = 2.0  # seconds between requests
KINDS = ("quest", "npc")
QUEST_TYPES = ("quest_detail", "quest_progress", "quest_complete")


def url(kind: str, id_: int) -> str:
    return f"{BASE}{kind}={id_}"


# --- targets ---------------------------------------------------------------------------------------------------------

def targets(world: sqlite3.Connection, questv2: Path) -> list[int]:
    """Quest IDs in Forever's client (QuestV2) that the VMaNGOS Source Data doesn't have."""
    with open(questv2, encoding="utf-8", newline="") as f:
        client = {int(r["ID"]) for r in csv.DictReader(f)}
    known = {r[0] for r in world.execute("SELECT DISTINCT entry FROM quest_template")}
    return sorted(client - known)


# --- cache and polite fetching ---------------------------------------------------------------------------------------

class Blocked(RuntimeError):
    """Wowhead refused an automated request (robots.txt, HTTP 403/429/503 or a bot challenge page)."""


def page_path(root: Path, kind: str, id_: int) -> Path:
    return root / kind / f"{id_}.html"


def cached(root: Path, kind: str, id_: int) -> bool:
    p = page_path(root, kind, id_)
    return p.exists() or p.with_suffix(".missing").exists()


BLOCK_MARKERS = (b"Request blocked", b"cf-chl", b"Just a moment...", b"challenge-platform", b"Access denied")


def is_block(status: int, body: bytes) -> bool:
    return status in (401, 403, 429, 503) or (status == 200 and any(m in body[:20000] for m in BLOCK_MARKERS))


Get = Callable[[str], tuple[int, bytes]]


def http_get(u: str, timeout: float = 30.0) -> tuple[int, bytes]:
    req = urllib.request.Request(u, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or b""


def robots(root: Path, get: Get | None = None) -> urllib.robotparser.RobotFileParser:
    """Wowhead's robots.txt, cached at root/robots.txt."""
    p = root / "robots.txt"
    if not p.exists():
        status, body = (get or http_get)(ROBOTS)
        if status != 200:
            raise Blocked(f"robots.txt: HTTP {status}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(body)
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(p.read_text(encoding="utf-8", errors="replace").splitlines())
    return rp


def fetch(root: Path, kind: str, ids: list[int], *, get: Get | None = None, sleep: Callable[[float], None] = time.sleep,
          interval: float = INTERVAL, limit: int | None = None, log: Callable[[str], None] = print) -> Counter:
    """Fetch the pages not cached yet, one every `interval` seconds. Raises Blocked at the first refusal (nothing
    more is requested); pages fetched before it stay cached."""
    get = get or http_get
    rp = robots(root, get)
    counts = Counter()
    todo = [i for i in ids if not cached(root, kind, i)]
    counts["cached"] = len(ids) - len(todo)
    for n, id_ in enumerate(todo[:limit] if limit is not None else todo):
        u = url(kind, id_)
        if not rp.can_fetch(USER_AGENT, u):
            raise Blocked(f"robots.txt disallows {u} for {USER_AGENT}")
        if n:
            sleep(interval)
        status, body = get(u)
        if is_block(status, body):
            raise Blocked(f"{u}: HTTP {status}{' (bot challenge page)' if status == 200 else ''}")
        p = page_path(root, kind, id_)
        p.parent.mkdir(parents=True, exist_ok=True)
        if status == 404:
            p.with_suffix(".missing").write_text(str(status))
            counts["missing"] += 1
            continue
        if status != 200:
            counts["failed"] += 1
            log(f"{u}: HTTP {status}, skipped")
            continue
        tmp = p.with_suffix(".part")
        tmp.write_bytes(body)
        tmp.replace(p)
        counts["fetched"] += 1
    return counts


# --- parsing ---------------------------------------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>", re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.S | re.I)
_TOKENS = {"name": "$N", "race": "$R", "class": "$C"}
_TOKEN = re.compile(r"<\s*(name|race|class)\s*>", re.I)
_GENDER_TOKEN = re.compile(r"<\s*([^<>/]{1,40}?)\s*/\s*([^<>/]{1,40}?)\s*>")
_ID_LINK = re.compile(r"\[url=\\?/(?:forever\\?/)?(npc|object|item)=(\d+)[^\]]*\](.*?)\[\\?/url\]")


def _clean(fragment: str) -> str:
    """An HTML fragment as plain text, line breaks kept as newlines."""
    s = _COMMENT.sub("", fragment)
    s = _BR.sub("\n", s)
    s = re.sub(r"</(p|div|li)>", "\n", s, flags=re.I)
    s = _TAG.sub("", s)
    return htmllib.unescape(s).replace("\xa0", " ")


def raw_text(fragment: str) -> str:
    """Wowhead's rendering of quest text back to a raw line: <name>/<race>/<class> -> $N/$R/$C,
    <male/female> -> $Gmale:female;, line breaks -> $B."""
    s = _clean(fragment)
    s = _TOKEN.sub(lambda m: _TOKENS[m[1].lower()], s)
    s = _GENDER_TOKEN.sub(lambda m: f"$G{m[1]}:{m[2]};", s)
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in s.replace("\r\n", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "$B".join(lines)


def _section(page: str, heading: str) -> str | None:
    """The HTML after an <h2> whose text is `heading`, up to the next <h2>, script or table."""
    for m in _H2.finditer(page):
        if _clean(m[1]).strip().lower() == heading.lower():
            rest = page[m.end():]
            end = re.search(r"<h2|<script|<table|<div class=\"pad|<div id=\"[^\"]*-(progress|completion)\"", rest, re.I)
            return rest[:end.start()] if end else rest
    return None


def _div(page: str, suffix: str) -> str | None:
    """The content of Wowhead's hidden disclosure div `<div id="…-progress">` / `…-completion`."""
    m = re.search(r"<div[^>]*\bid=\"[^\"]*-" + suffix + r"\"[^>]*>", page, re.I)
    if not m:
        return None
    depth, pos = 1, m.end()
    for t in re.finditer(r"<(/?)div\b[^>]*>", page[pos:], re.I):
        depth += -1 if t[1] else 1
        if depth == 0:
            return page[pos:pos + t.start()]
    return page[pos:]


def _markup(page: str) -> str:
    """The Quick Facts infobox markup (WH.markup.printHtml("[ul][li]Level: …", "infobox-contents-…")), unescaped."""
    m = re.search(r"printHtml\(\s*\"((?:[^\"\\]|\\.)*)\"\s*,\s*\"infobox-contents", page)
    if not m:
        return ""
    try:
        return json.loads(f'"{m[1]}"')
    except ValueError:
        return m[1].replace("\\/", "/")


def _gatherer(page: str, type_id: int, id_: int) -> dict:
    """The entity's record in WH.Gatherer.addData(<type>, <env>, {...}) (1 NPC, 5 quest), or g_quests/g_npcs."""
    for m in re.finditer(r"WH\.Gatherer\.addData\(\s*" + str(type_id) + r"\s*,\s*\d+\s*,\s*(\{.*?\})\s*\);", page, re.S):
        try:
            data = json.loads(m[1])
        except ValueError:
            continue
        if str(id_) in data:
            return data[str(id_)]
    return {}


@dataclass
class Ref:
    kind: str  # npc | object | item
    id: int
    name: str


@dataclass
class Quest:
    id: int
    title: str | None = None
    level: int | None = None
    min_level: int | None = None
    zone: int | None = None
    side: str | None = None
    start: Ref | None = None
    end: Ref | None = None
    objectives: str = ""
    description: str = ""
    progress: str = ""
    completion: str = ""
    issues: list[str] = field(default_factory=list)

    @property
    def detail(self) -> str:
        """What QUEST_DETAIL shows: the description, then the objectives (as vo.quests.detail_text)."""
        return "$B$B".join(p for p in (self.description, self.objectives) if p)

    def parts(self) -> dict[str, str]:
        return {"quest_detail": self.detail, "quest_progress": self.progress, "quest_complete": self.completion}


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _title(page: str) -> str | None:
    m = _H1.search(page)
    if m:
        return _clean(m[1]).strip() or None
    m = re.search(r"<meta property=\"og:title\" content=\"([^\"]*)\"", page) or re.search(r"<title>([^<]*?)(?: - Quest| - NPC| - World of Warcraft)", page)
    return htmllib.unescape(m[1]).strip() if m else None


def parse_quest(page: str, id_: int) -> Quest:
    q = Quest(id_, title=_title(page))
    rec = _gatherer(page, 5, id_)
    if rec:
        q.title = q.title or rec.get("name_enus") or rec.get("name")
        q.level, q.min_level = _int(rec.get("level")), _int(rec.get("reqlevel"))
        q.zone = _int(rec.get("category")) if _int(rec.get("category")) and _int(rec.get("category")) > 0 else None
        q.side = {1: "Alliance", 2: "Horde", 3: "Both"}.get(_int(rec.get("side")))
    facts = _markup(page)
    if m := re.search(r"Level:\s*(\d+)", facts):
        q.level = q.level or int(m[1])
    if m := re.search(r"Requires level\s*(\d+)", facts, re.I):
        q.min_level = q.min_level or int(m[1])
    if m := re.search(r"Side:\s*(?:\[[^\]]*\])*\s*(Alliance|Horde|Both)", facts):
        q.side = q.side or m[1]
    for label, attr in (("Start", "start"), ("End", "end")):
        if m := re.search(label + r":\s*" + _ID_LINK.pattern, facts):
            setattr(q, attr, Ref(m[1], int(m[2]), _clean(m[3]).strip()))
    if q.zone is None and (m := re.search(r"breadcrumb:\s*\[([\d,\s-]+)\]", page)):
        crumbs = [int(x) for x in m[1].split(",") if x.strip()]
        if len(crumbs) >= 4 and crumbs[-1] > 0:
            q.zone = crumbs[-1]
    # Objectives: the text right after the <h1>, up to the first section.
    if m := _H1.search(page):
        rest = page[m.end():]
        end = re.search(r"<h2|<table|<script|<div class=\"pad", rest, re.I)
        q.objectives = raw_text(rest[:end.start()] if end else "")
    q.description = raw_text(_section(page, "Description") or "")
    q.progress = raw_text(_div(page, "progress") or "")
    q.completion = raw_text(_div(page, "completion") or "")
    if not q.title:
        q.issues.append("no_title")
    if not (q.description or q.objectives):
        q.issues.append("no_detail")
    if q.start is None:
        q.issues.append("no_start")
    return q


@dataclass
class Npc:
    id: int
    name: str | None = None
    subname: str | None = None
    display_id: int | None = None
    level_min: int | None = None
    level_max: int | None = None
    zones: list[int] = field(default_factory=list)
    sounds: list[dict] = field(default_factory=list)  # [{"id": sound kit, "name": ..., "files": [FileDataID]}]


def _top_keys(obj: str) -> list[int]:
    """Top-level integer keys of a JS object literal."""
    out, depth, i = [], 0, 0
    while i < len(obj):
        c = obj[i]
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        elif depth == 1 and (m := re.match(r"['\"]?(\d+)['\"]?\s*:", obj[i:])) and re.match(r"[{,\s]", obj[i - 1]):
            out.append(int(m[1]))
            i += m.end()
            continue
        i += 1
    return out


def _balanced(s: str, start: int) -> str:
    """The bracketed literal starting at s[start] ('{' or '[')."""
    depth, quote, i = 0, None, start
    while i < len(s):
        c = s[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
        i += 1
    return s[start:]


def _sounds(page: str) -> list[dict]:
    """The NPC's "Sounds" tab (new Listview({template: 'sound', ...})): sound kits and their files."""
    m = re.search(r"new Listview\(\{[^{}]*?template:\s*['\"]sound['\"]", page)
    if not m:
        return []
    d = re.compile(r"\bdata:\s*\[").search(page, m.end())
    if not d:
        return []
    literal = _balanced(page, d.end() - 1)
    try:
        items = json.loads(literal)
    except ValueError:
        items = [{"id": int(k), "files": []} for k in re.findall(r"\{\s*\"?id\"?\s*:\s*(\d+)", literal)]
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or _int(it.get("id")) is None:
            continue
        files = [f["id"] for f in it.get("files") or [] if isinstance(f, dict) and _int(f.get("id")) is not None]
        out.append({"id": int(it["id"]), "name": it.get("name"), "files": [int(f) for f in files]})
    return out


def parse_npc(page: str, id_: int) -> Npc:
    n = Npc(id_, name=_title(page))
    rec = _gatherer(page, 1, id_)
    if rec:
        n.name = n.name or rec.get("name_enus") or rec.get("name")
        n.subname = rec.get("tag") or None
        n.level_min, n.level_max = _int(rec.get("minlevel")), _int(rec.get("maxlevel"))
    if n.subname is None and (m := re.search(r"<h1[^>]*>.*?</h1>\s*<div[^>]*>\s*&lt;([^<&]+)&gt;", page, re.S)):
        n.subname = htmllib.unescape(m[1]).strip()
    if m := re.search(r"displayId['\"]?\s*:\s*(\d+)", page):
        n.display_id = int(m[1])
    if (m := re.search(r"g_mapperData\s*=\s*\{", page)):
        n.zones = _top_keys(_balanced(page, m.end() - 1))
    facts = _markup(page)
    if n.level_min is None and (m := re.search(r"Level:\s*(\d+)(?:\s*-\s*(\d+))?", facts)):
        n.level_min, n.level_max = int(m[1]), int(m[2] or m[1])
    n.sounds = _sounds(page)
    return n


# --- ingest ----------------------------------------------------------------------------------------------------------

@dataclass
class Voices:
    """Race and gender for a Wowhead-only NPC: from its display (vo.display) or its voice kit (vo.npcgamevoice)."""
    displays: object | None = None       # vo.display.Displays
    tables: object | None = None         # vo.npcgamevoice.Tables


def _race_of_archetype(aid: str) -> tuple[str | None, str | None]:
    from vo import archetypes
    prefix, _, g = aid.rpartition("_")
    race = next((label for label, p in archetypes.RACES.items() if p == prefix), None)
    return race, {"m": "male", "f": "female"}.get(g)


def _page_hash(page: str) -> str:
    return hashlib.sha256(page.encode("utf-8", "replace")).hexdigest()[:16]


def _read(root: Path, kind: str) -> dict[int, str]:
    d = root / kind
    return {int(p.stem): p.read_text(encoding="utf-8", errors="replace")
            for p in sorted(d.glob("*.html")) if p.stem.isdigit()} if d.exists() else {}


def _ensure_npc(conn: sqlite3.Connection, ref: Ref, npc: Npc | None, zone: int | None, voices: Voices,
                counts: Counter) -> int | None:
    if ref is None or ref.kind != "npc":
        return None
    if conn.execute("SELECT 1 FROM npcs WHERE id = ?", (ref.id,)).fetchone():
        return ref.id
    race = gender = model = None
    issues = [("wowhead_only", f"from its Wowhead page{'' if npc else ' (not saved: name from the quest page)'}")]
    how = None
    if npc and npc.display_id and voices.displays is not None:
        from vo.extract import resolve
        res = resolve([(npc.display_id, 1)], voices.displays)
        race, gender, model = res.race, res.gender, res.model
        issues += res.issues
        how = "display"
    if race is None and voices.tables is not None and npc:
        kit = voices.tables.kit(npc.display_id) if npc.display_id else None
        kit = kit[1] if kit else voices.tables.sound_kit([f for s in npc.sounds for f in s["files"]])
        if kit is not None:
            race, g = _race_of_archetype(kit.archetype)
            gender = gender or g
            if race:
                issues.append(("race_from_voice_kit", kit.folder))
                issues = [i for i in issues if i[0] not in ("race_unresolved", "gender_unresolved")]
                how = "voice kit"
    if race is None and how != "display":
        issues.append(("race_unresolved", None))
    if gender is None and how != "display":
        issues.append(("gender_unresolved", None))
    name = (npc.name if npc else None) or ref.name
    conn.execute("INSERT INTO npcs (id, name, subname, race, gender, model, level_min, level_max, source)"
                 " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'wowhead')",
                 (ref.id, name, npc.subname if npc else None, race, gender, model,
                  npc.level_min if npc else None, npc.level_max if npc else None))
    zones = (npc.zones if npc else []) or ([zone] if zone else [])
    conn.executemany("INSERT INTO spawns (npc_id, map, zone, x, y, z) VALUES (?, NULL, ?, NULL, NULL, NULL)",
                     [(ref.id, z) for z in zones])
    issues.append(("no_spawn", None))
    if not zones:
        issues.append(("no_zone", None))
    conn.executemany("INSERT INTO npc_issues VALUES (?, ?, ?)", [(ref.id, i, d) for i, d in dict.fromkeys(issues)])
    counts["new_npcs"] += 1
    return ref.id


def _store_npc(conn: sqlite3.Connection, n: Npc, page: str, now: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO wowhead_npcs (id, name, subname, display_id, level_min, level_max, zones, sounds,"
        " page_hash, parsed, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (n.id, n.name, n.subname, n.display_id, n.level_min, n.level_max, json.dumps(n.zones), json.dumps(n.sounds),
         _page_hash(page), json.dumps(asdict(n)), now))


def _lines(conn: sqlite3.Connection, q: Quest, giver: int | None, ender: int | None, counts: Counter) -> None:
    for kind, raw in q.parts().items():
        if not raw:
            continue
        npc = giver if kind == "quest_detail" else ender
        others = {r[0] for r in conn.execute("SELECT DISTINCT source FROM lines WHERE type = ? AND quest_id = ?"
                                             " AND source != 'wowhead'", (kind, q.id))}
        if others:  # Core Content or Capture: in-game text wins
            counts["known_parts"] += 1
            continue
        existing = {r["player_gender"]: r for r in conn.execute(
            "SELECT * FROM lines WHERE type = ? AND quest_id = ? AND source = 'wowhead'", (kind, q.id))}
        wanted = text.genders(raw)
        for gender in wanted:
            hash_ = drift.text_hash(raw, gender)
            row = existing.pop(gender, None)
            if row is None:
                conn.execute("INSERT INTO lines (npc_id, type, quest_id, player_gender, raw_text, tts_text, text_hash,"
                             " source) VALUES (?, ?, ?, ?, ?, ?, ?, 'wowhead')",
                             (npc, kind, q.id, gender, raw, text.prepare(raw, gender), hash_))
                counts["new_lines"] += 1
            elif row["raw_text"] != raw or row["npc_id"] != npc:
                conn.execute("INSERT INTO line_history (line_id, raw_text, tts_text, text_hash, reason)"
                             " VALUES (?, ?, ?, ?, 'wowhead')",
                             (row["id"], row["raw_text"], row["tts_text"], row["text_hash"]))
                conn.execute("UPDATE lines SET npc_id = ?, raw_text = ?, tts_text = ?, text_hash = ? WHERE id = ?",
                             (npc, raw, text.prepare(raw, gender), hash_, row["id"]))
                counts["updated_lines"] += 1
            else:
                counts["unchanged_lines"] += 1
        for row in existing.values():  # a gender variant the text no longer has
            conn.execute("DELETE FROM lines WHERE id = ? AND id NOT IN (SELECT line_id FROM audio)", (row["id"],))


def ingest(conn: sqlite3.Connection, root: Path, *, core_quests: set[int] | None = None,
           voices: Voices | None = None) -> Counter:
    """Parse every cached page and store it (idempotent). `core_quests`: quest IDs the Source Data has; their
    pages are parsed but add no lines. Returns counts."""
    voices = voices or Voices()
    now = datetime.now().isoformat(timespec="seconds")
    counts = Counter()
    npc_pages = _read(root, "npc")
    npcs = {}
    with conn:
        for id_, page in npc_pages.items():
            npcs[id_] = parse_npc(page, id_)
            _store_npc(conn, npcs[id_], page, now)
            counts["npc_pages"] += 1
        for id_, page in _read(root, "quest").items():
            counts["quest_pages"] += 1
            q = parse_quest(page, id_)
            for issue in q.issues:
                counts[issue] += 1
            if not any(q.parts().values()):
                counts["unparsed"] += 1
                continue
            counts["parsed"] += 1
            conn.execute(
                "INSERT OR REPLACE INTO wowhead_quests (id, title, level, min_level, zone, side, giver, ender, page_hash,"
                " parsed, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (q.id, q.title, q.level, q.min_level, q.zone, q.side,
                 q.start.id if q.start and q.start.kind == "npc" else None,
                 q.end.id if q.end and q.end.kind == "npc" else None, _page_hash(page), json.dumps(asdict(q)), now))
            if core_quests is not None and q.id in core_quests:
                counts["core_quests"] += 1
                continue
            giver = _ensure_npc(conn, q.start, npcs.get(q.start.id) if q.start else None, q.zone, voices, counts)
            ender = _ensure_npc(conn, q.end, npcs.get(q.end.id) if q.end else None, q.zone, voices, counts)
            _lines(conn, q, giver, ender, counts)
    return counts


def needed_npcs(conn: sqlite3.Connection, root: Path) -> list[int]:
    """NPCs only Wowhead knows (quest givers and enders of ingested Forever quests) whose page isn't cached: their
    page gives race, gender (its display) and zones."""
    ids = [r[0] for r in conn.execute("SELECT id FROM npcs WHERE source = 'wowhead' ORDER BY id")]
    return [i for i in ids if not cached(root, "npc", i)]


def summary_text(c: Counter) -> str:
    out = (f"wowhead: {c['quest_pages']} quest pages, {c['parsed']} parsed ({c['core_quests']} Core Content quests"
           f" skipped); {c['new_lines']} new lines, {c['updated_lines']} updated, {c['unchanged_lines']} unchanged;"
           f" {c['new_npcs']} new NPCs; {c['npc_pages']} NPC pages")
    extra = [f"{c[k]} {k.replace('_', ' ')}" for k in ("unparsed", "no_title", "no_detail", "no_start", "known_parts")
             if c[k]]
    return out + (f" ({', '.join(extra)})" if extra else "")
