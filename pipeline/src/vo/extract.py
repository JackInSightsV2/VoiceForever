"""Full Core Content extraction: NPCs, spawns, Quest Text and Gossip from the Source Data.

Joins the VMaNGOS world DB (text, NPCs, spawns, quest links), QuestieDB's Forever tables (zones,
quest-giver fallback) and Forever's display tables (race and gender) into the pipeline DB.

`raw_text` is stored as in the Source Data, one row per player gender variant (`$G`); every other
token stays intact. `tts_text` is left to text prep.
"""
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from vo import drift, gossip, ingest, quests, text
from vo.display import Display, Displays
from vo.questie import Questie

PATCH = 10  # the final vanilla patch (1.12), which Forever is built from

# creature_template.type
BEAST, CRITTER = 1, 8
FILTERED_TYPES = {BEAST: "beast", CRITTER: "critter"}

# creature_template.npc_flags (vanilla values)
NPC_FLAGS = [  # (bit, role) in priority order
    (0x80, "innkeeper"), (0x100, "banker"), (0x1000, "auctioneer"), (0x8, "flightmaster"),
    (0x2000, "stablemaster"), (0x800, "battlemaster"), (0x20, "spirit healer"), (0x40, "spirit healer"),
    (0x10, "trainer"), (0x4, "vendor"), (0x4000, "vendor"),
]
PLACEHOLDER_QUEST = re.compile(r"<(NYI|TXT|UNUSED|DEPRECATED|PH)>|\(123\)", re.I)  # never shipped
GUARD = re.compile(r"\b(guard|guardian|sentinel|sentry|watchman|watcher|protector|footman|grunt|"
                   r"peacekeeper|bruiser|deathguard|patroller|warden|mountaineer|bluffwatcher|brave)s?\b", re.I)
NAME_WORD = re.compile(r"[\w'-]+")
RARE_WORD = 3  # a name word used in at most this many creature names is a proper noun


@dataclass
class Resolved:
    race: str | None
    gender: str | None
    model: str | None
    issues: list[tuple[str, str | None]] = field(default_factory=list)


def resolve(slots: list[tuple[int, int]], displays: Displays) -> Resolved:
    """Race and gender from an NPC's display slots [(display_id, probability)].

    The most probable display is voiced (first slot wins ties; all slots are equal when no
    probability is set). Mixed races or genders across the NPC's displays are flagged.
    """
    slots = [(d, p) for d, p in slots if d]
    if not slots:
        return Resolved(None, None, None, [("no_display", None)])
    weighted = [(d, p) for d, p in slots if p > 0] or [(d, 1) for d, _ in slots]
    best = max(weighted, key=lambda s: s[1])[0]  # max() keeps the first of equals
    chosen: Display = displays.get(best)
    issues = []
    resolved = [displays.get(d) for d, _ in weighted]
    races = sorted({d.race for d in resolved if d.race})
    genders = sorted({d.gender for d in resolved if d.gender})
    if len(races) > 1:
        issues.append(("mixed_race", ", ".join(races)))
    if len(genders) > 1:
        issues.append(("mixed_gender", ", ".join(genders)))
    if chosen.how == "missing":
        issues.append(("display_missing", str(best)))
    if chosen.race is None and chosen.how != "missing":
        issues.append(("race_unresolved", chosen.model))
    if chosen.gender is None and chosen.how != "missing":
        issues.append(("gender_unresolved", chosen.model))
    if chosen.gender_default:
        issues.append(("gender_default", chosen.model))
    return Resolved(chosen.race, chosen.gender, chosen.model, issues)


SETTLED_BY = {  # resolution issues an override of that field settles
    "race": {"race_unresolved", "mixed_race"},
    "gender": {"gender_unresolved", "gender_default", "mixed_gender"},
}


def apply_overrides(npc: dict, overrides: list[tuple[str, str]]) -> dict:
    """`manual_overrides` beats every other source, and clears the issues it settles."""
    for fld, value in overrides:
        if fld in ("level_min", "level_max", "faction", "is_named"):
            npc[fld] = int(value)
        elif fld in npc and fld != "issues":
            npc[fld] = value
    fields = {f for f, _ in overrides}
    settled = set().union(*(SETTLED_BY[f] for f in fields & SETTLED_BY.keys()))
    if SETTLED_BY.keys() <= fields:
        settled |= {"display_missing", "no_display"}
    npc["issues"] = [i for i in npc["issues"] if i[0] not in settled]
    return npc


def role(npc_flags: int, name: str, subname: str | None, is_named: bool, has_quests: bool, rank: int) -> str:
    """One role per NPC: service flags first, then guard (by name), story (a named elite quest NPC,
    or a boss), quest, else gossip."""
    for bit, r in NPC_FLAGS:
        if npc_flags & bit:
            return r
    if GUARD.search(f"{name} {subname or ''}"):
        return "guard"
    if is_named and (has_quests and rank > 0 or rank == 3):
        return "story"
    if has_quests:
        return "quest"
    return "gossip"


def name_words(names) -> Counter:
    """How many distinct creature names use each word (case-folded)."""
    return Counter(w for n in set(names) for w in {w.lower() for w in NAME_WORD.findall(n)})


def is_named(name: str, spawn_count: int, name_count: int, rank: int, word_counts: Counter) -> bool:
    """Heuristic: a named character rather than a generic one.

    Bosses (rank 3) are always named. Otherwise the NPC spawns at most once, no other in-scope NPC
    shares its name, and the name holds a proper noun: a word few creature names use. So "Guard
    Thomas" and "Marshal McBride" are named; "Stormwind City Guard" and "Ironforge Mountaineer"
    (common words, many spawns) are not.
    """
    if rank == 3:
        return True
    rare = any(word_counts[w.lower()] <= RARE_WORD for w in NAME_WORD.findall(name))
    return spawn_count <= 1 and name_count == 1 and rare


# ---------------------------------------------------------------- Source Data reads

def latest(world: sqlite3.Connection, table: str, columns: str, where: str = "1") -> list[sqlite3.Row]:
    """VMaNGOS keeps one template row per patch that changed it: take the highest patch."""
    return world.execute(
        f"SELECT {columns} FROM {table} t JOIN (SELECT entry AS e, MAX(patch) AS p FROM {table}"
        f" WHERE patch <= {PATCH} GROUP BY entry) m ON t.entry = m.e AND t.patch = m.p WHERE {where}").fetchall()


def relations(world: sqlite3.Connection, table: str) -> dict[int, list[int]]:
    """quest -> [creature or object ids] live at the final patch."""
    out = defaultdict(list)
    for r in world.execute(f"SELECT id, quest FROM {table} WHERE ? BETWEEN patch_min AND patch_max"
                           " ORDER BY id", (PATCH,)):
        out[r[1]].append(r[0])
    return out


def gossip_texts(world: sqlite3.Connection) -> dict[int, list[tuple[int, str, str]]]:
    """gossip menu -> [(broadcast_text id, male_text, female_text)], following sub-menus."""
    menu_texts = defaultdict(list)
    for r in world.execute("SELECT entry, text_id FROM gossip_menu ORDER BY entry, text_id"):
        menu_texts[r[0]].append(r[1])
    submenus = defaultdict(list)
    for r in world.execute("SELECT menu_id, action_menu_id FROM gossip_menu_option"
                           " WHERE action_menu_id > 0 ORDER BY menu_id, id"):
        submenus[r[0]].append(r[1])
    npc_text = {}
    cols = ", ".join(f"BroadcastTextID{i}, Probability{i}" for i in range(8))
    for r in world.execute(f"SELECT ID, {cols} FROM npc_text"):
        npc_text[r[0]] = [r[1 + 2 * i] for i in range(8) if r[1 + 2 * i]]
    broadcast = {r[0]: (r[1] or "", r[2] or "") for r in
                 world.execute("SELECT entry, male_text, female_text FROM broadcast_text")}

    out = {}
    for root in menu_texts.keys() | submenus.keys():
        seen, stack, texts = set(), [root], []
        while stack:
            m = stack.pop(0)
            if m in seen:
                continue
            seen.add(m)
            for tid in menu_texts.get(m, []):
                for bid in npc_text.get(tid, []):
                    if bid in broadcast and any(broadcast[bid]):
                        texts.append((bid, *broadcast[bid]))
            stack.extend(submenus.get(m, []))
        out[root] = list(dict.fromkeys(texts))
    return out


def spoken(male: str, female: str, gender: str | None) -> str:
    """broadcast_text's NPC-side gendered variant: pick by the NPC's gender, else whichever exists."""
    first, second = (female, male) if gender == "female" else (male, female)
    return (first or second).strip()


# ---------------------------------------------------------------- zones

def fit_zones(questie: Questie, spawns: dict[int, list[tuple]]) -> dict[int, tuple]:
    """Per Questie zone, a least-squares affine map from world (x, y) to map percent.

    Fitted from NPCs with exactly one spawn in VMaNGOS and one point in Questie.
    """
    import numpy as np

    pairs = defaultdict(list)
    for npc_id, rows in spawns.items():
        q = questie.npcs.get(npc_id)
        if q is None or len(rows) != 1 or len(q.spawns) != 1:
            continue
        (zone, pts), = q.spawns.items()
        if len(pts) == 1:
            pairs[zone].append((rows[0][0], rows[0][1], rows[0][2], *pts[0]))
    fits = {}
    for zone, rows in pairs.items():
        if len(rows) < 4:
            continue
        mp = Counter(r[0] for r in rows).most_common(1)[0][0]
        rows = [r for r in rows if r[0] == mp]
        a = np.array([[r[1], r[2], 1.0] for r in rows])
        b = np.array([[r[3], r[4]] for r in rows])
        coef = None
        for _ in range(3):  # drop outliers (mis-paired rows) and refit
            if len(a) < 4:
                break
            coef, *_ = np.linalg.lstsq(a, b, rcond=None)
            err = np.linalg.norm(a @ coef - b, axis=1)
            keep = err < max(2.0, 3 * np.median(err))
            if keep.all():
                break
            a, b = a[keep], b[keep]
        if coef is not None:
            fits[zone] = (mp, coef)
    return fits


def instance_zones(world: sqlite3.Connection) -> dict[int, int]:
    """map -> its zone, for instance maps with a single top-level area (Questie has no dungeon spawns)."""
    rows = world.execute("SELECT map_id, entry FROM area_template WHERE zone_id = 0 AND name NOT LIKE '*%'"
                         " AND map_id NOT IN (0, 1)").fetchall()
    per_map = defaultdict(list)
    for map_id, entry in rows:
        per_map[map_id].append(entry)
    return {m: e[0] for m, e in per_map.items() if len(e) == 1}


class Zones:
    """Zone for each spawn. Positions stay VMaNGOS world coordinates; zones come from Questie.

    In order: the NPC's only Questie zone; for an NPC in several, the one whose Questie points lie
    nearest the spawn's fitted map position; for an NPC Questie lacks, the zone whose Questie points
    (any NPC's) lie nearest; Questie's guess; the instance map's zone; for a spawnless (summoned) NPC,
    Questie's zones, else the zone of the quests it gives or ends.
    """

    def __init__(self, questie: Questie, spawns: dict[int, list[tuple]], instances: dict[int, int],
                 quest_zones: dict[int, Counter]):
        import numpy as np

        self.questie, self.instances, self.quest_zones = questie, instances, quest_zones
        self.fits = fit_zones(questie, spawns)
        points = defaultdict(list)
        for npc in questie.npcs.values():
            for zone, pts in npc.spawns.items():
                points[zone] += pts
        self.points = {z: np.array(p) for z, p in points.items() if z in self.fits and p}

    def _nearest(self, map_id, x, y, zones) -> int | None:
        import numpy as np

        best, best_d = None, None
        for zone in zones:
            if zone not in self.points or self.fits[zone][0] != map_id:
                continue
            px, py = np.array((x, y, 1.0)) @ self.fits[zone][1]
            if not (-5 <= px <= 105 and -5 <= py <= 105):
                continue
            d = float(np.min(np.hypot(self.points[zone][:, 0] - px, self.points[zone][:, 1] - py)))
            if best_d is None or d < best_d:
                best, best_d = zone, d
        return best

    def spawn(self, npc_id: int, map_id: int, x: float, y: float) -> int | None:
        q = self.questie.npcs.get(npc_id)
        if q is not None and len(q.spawns) == 1:
            return next(iter(q.spawns))
        zones = list(q.spawns) if q is not None and q.spawns else list(self.points)
        return (self._nearest(map_id, x, y, zones) or (q.zone if q is not None else None)
                or self.instances.get(map_id))

    def unspawned(self, npc_id: int) -> list[int]:
        q = self.questie.npcs.get(npc_id)
        if q is not None and (q.spawns or q.zone):
            return list(q.spawns) or [q.zone]
        top = self.quest_zones.get(npc_id)
        return [top.most_common(1)[0][0]] if top else []


# ---------------------------------------------------------------- extraction

def extract_all(conn: sqlite3.Connection, world: sqlite3.Connection, questie: Questie,
                displays: Displays) -> dict:
    templates = {r["entry"]: dict(r) for r in latest(
        world, "creature_template", "t.entry, t.name, t.subname, t.level_min, t.level_max, t.faction,"
        " t.npc_flags, t.gossip_menu_id, t.type, t.rank, t.display_id1, t.display_id2, t.display_id3,"
        " t.display_id4, t.display_probability1, t.display_probability2, t.display_probability3,"
        " t.display_probability4")}
    quest_rows = {r["entry"]: r for r in latest(
        world, "quest_template", "t.entry, t.Title, t.ZoneOrSort, t.Details, t.Objectives, t.RequestItemsText,"
        " t.OfferRewardText")}
    starts, ends = relations(world, "creature_questrelation"), relations(world, "creature_involvedrelation")
    go_starts, go_ends = relations(world, "gameobject_questrelation"), relations(world, "gameobject_involvedrelation")
    item_starts = {r["start_quest"] for r in latest(world, "item_template", "t.start_quest", "t.start_quest > 0")}
    for qid, q in questie.quests.items():  # Questie fills quests VMaNGOS has no creature link for
        if qid in quest_rows and not starts.get(qid) and q.creature_starts:
            starts[qid] = sorted(q.creature_starts)
        if qid in quest_rows and not ends.get(qid) and q.creature_ends:
            ends[qid] = sorted(q.creature_ends)

    def is_live(qid: int) -> bool:
        """A quest a player can get: something starts it, and it isn't a placeholder."""
        qq = questie.quests.get(qid)
        started = (starts.get(qid) or go_starts.get(qid) or qid in item_starts
                   or (qq is not None and (qq.object_starts or qq.item_starts)))
        return bool(started) and not PLACEHOLDER_QUEST.search(quest_rows[qid]["Title"] or "")

    live = {qid for qid in quest_rows if is_live(qid)}

    greetings = {r[0]: r[1] for r in world.execute(
        "SELECT entry, content_default FROM quest_greeting WHERE type = 0 AND content_default != ''")}
    menus = gossip_texts(world)

    spawns = defaultdict(list)
    for r in world.execute("SELECT id, id2, id3, id4, id5, map, position_x, position_y, position_z FROM creature"
                           " WHERE ? BETWEEN patch_min AND patch_max ORDER BY guid", (PATCH,)):
        for npc_id in {r[0], r[1], r[2], r[3], r[4]} - {0}:
            spawns[npc_id].append((r[5], r[6], r[7], r[8]))

    overrides = defaultdict(list)
    for r in conn.execute("SELECT npc_id, field, value FROM manual_overrides ORDER BY rowid"):
        overrides[r[0]].append((r[1], r[2]))

    # Candidates: every creature with Quest Text or Gossip.
    quest_npcs = {n for qid in live for n in starts.get(qid, []) + ends.get(qid, [])}
    candidates = (quest_npcs | {e for e, t in templates.items() if menus.get(t["gossip_menu_id"])}
                  | set(greetings)) & templates.keys()
    dropped = Counter()
    keep = set()
    for e in candidates:
        include = dict(overrides.get(e, [])).get("include")
        t = templates[e]
        if include == "0":
            dropped["override"] += 1
        elif t["type"] in FILTERED_TYPES and include != "1":
            dropped[FILTERED_TYPES[t["type"]]] += 1
        else:
            keep.add(e)

    name_counts = Counter(templates[e]["name"] for e in keep)
    word_counts = name_words(t["name"] for t in templates.values())
    npcs = {}
    for e in sorted(keep):
        t = templates[e]
        slots = [(t[f"display_id{i}"], t[f"display_probability{i}"]) for i in range(1, 5)]
        res = resolve(slots, displays)
        named = is_named(t["name"], len(spawns.get(e, [])), name_counts[t["name"]], t["rank"], word_counts)
        npc = dict(id=e, name=t["name"], subname=t["subname"] or None, race=res.race, gender=res.gender,
                   model=res.model, faction=t["faction"], level_min=t["level_min"], level_max=t["level_max"],
                   is_named=int(named), issues=list(res.issues),
                   role=role(t["npc_flags"], t["name"], t["subname"], named, e in quest_npcs, t["rank"]))
        npcs[e] = apply_overrides(npc, overrides.get(e, []))

    def voice_for(ids: list[int]) -> int | None:
        """The NPC voicing a quest part: an in-scope creature (spawned first), else the Narrator."""
        ids = [i for i in ids if i in npcs]
        return min(ids, key=lambda i: (not spawns.get(i), i)) if ids else None

    wanted = {}  # (type, quest_id, npc_id, player_gender, raw_text) -> None, in insertion order
    def add(kind, quest_id, npc_id, raw):
        raw = (raw or "").strip()
        if not raw:
            return
        for gender in text.genders(raw):  # raw keeps its $G; each gender gets its own row
            wanted[(kind, quest_id, npc_id, gender, raw)] = None

    for qid in sorted(live):
        q = quest_rows[qid]
        giver, ender = voice_for(starts.get(qid, [])), voice_for(ends.get(qid, []))
        add("quest_detail", qid, giver, quests.detail_text(q))
        add("quest_progress", qid, ender, q["RequestItemsText"])
        add("quest_complete", qid, ender, q["OfferRewardText"])
    for e, npc in npcs.items():
        if e in greetings:
            add("quest_greeting", None, e, greetings[e])
        for _, male, female in menus.get(templates[e]["gossip_menu_id"], []):
            add("gossip", None, e, spoken(male, female, npc["gender"]))

    # NPCs whose only dialogue went elsewhere (e.g. a second quest giver) have no lines: drop them.
    voiced = {k[2] for k in wanted if k[2] is not None}
    for e in list(npcs):
        if e not in voiced:
            del npcs[e]
            dropped["no_lines"] += 1

    quest_zones = defaultdict(Counter)  # npc -> zones (ZoneOrSort > 0) of the quests it gives or ends
    for qid in live:
        zone = quest_rows[qid]["ZoneOrSort"]
        for n in starts.get(qid, []) + ends.get(qid, []):
            if zone and zone > 0:
                quest_zones[n][zone] += 1
    _write(conn, npcs, spawns, wanted, Zones(questie, spawns, instance_zones(world), quest_zones))
    with conn:  # Drift captured before its core line existed applies now
        retried = ingest.retry_drift(conn)
    gossip.update_patterns(conn)
    return {"npcs": len(npcs), "lines": len(wanted), "quests": len(live), "dropped": dict(dropped),
            "drift_retried": retried}


def _write(conn, npcs, spawns, wanted, zones: Zones):
    core = [r[0] for r in conn.execute("SELECT id FROM npcs WHERE source = 'core'")]
    conn.executemany("DELETE FROM spawns WHERE npc_id = ?", [(i,) for i in core])
    conn.execute("DELETE FROM npcs WHERE source = 'core'")

    cols = ("id", "name", "subname", "race", "gender", "model", "faction", "role", "level_min", "level_max", "is_named")
    conn.executemany(f"INSERT OR REPLACE INTO npcs ({', '.join(cols)}, source) VALUES ({', '.join('?' * len(cols))}, 'core')",
                     [tuple(n[c] for c in cols) for n in npcs.values()])
    # Capture-only and Wowhead-only NPCs (vo ingest, vo wowhead ingest) keep their flags; a Capture NPC the Source Data now has is re-flagged as core.
    conn.execute("DELETE FROM npc_issues WHERE npc_id NOT IN (SELECT id FROM npcs WHERE source IN ('capture', 'wowhead'))")
    rows, issues = [], []
    for e, n in npcs.items():
        issues += [(e, i, d) for i, d in n["issues"]]
        own = [(e, m, zones.spawn(e, m, x, y), x, y, z) for m, x, y, z in spawns.get(e, [])]
        if not own:  # summoned or scripted: a zone without a position
            issues.append((e, "no_spawn", None))
            own = [(e, None, zone, None, None, None) for zone in zones.unspawned(e)]
        if not any(r[2] for r in own):
            issues.append((e, "no_zone", None))
        rows += own
    conn.executemany("INSERT INTO spawns (npc_id, map, zone, x, y, z) VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.executemany("INSERT INTO npc_issues VALUES (?, ?, ?)", list(dict.fromkeys(issues)))

    _write_lines(conn, wanted)


def _write_lines(conn, wanted):
    """Reconcile core lines with the Source Data, keyed on the wording they were extracted from (`source_text`).

    A drifted line (Capture replaced its raw_text) keeps its captured wording while the Source Data is unchanged. When
    the Source Data catches up (its new wording hashes to the captured text), the drifted line adopts it and is no
    longer drifted, rather than a duplicate line being added."""
    existing, drifted = {}, defaultdict(list)
    for r in conn.execute("SELECT id, type, quest_id, npc_id, player_gender, raw_text, source_text, text_hash"
                          " FROM lines WHERE source = 'core'"):
        source_text = r["source_text"] if r["source_text"] is not None else r["raw_text"]
        existing[(r["type"], r["quest_id"], r["npc_id"], r["player_gender"], source_text)] = r["id"]
        if source_text != r["raw_text"]:
            drifted[(r["type"], r["quest_id"], r["npc_id"], r["player_gender"])].append((r["id"], r["text_hash"]))
    new, adopted = [], set()
    for k in wanted:
        if k in existing:
            continue
        hash_ = drift.text_hash(k[4], k[3])
        caught_up = next((i for i, h in drifted.get(k[:4], []) if h == hash_ and i not in adopted), None)
        if caught_up is None:
            new.append((*k, text.prepare(k[4], k[3]), hash_, k[4]))
            continue
        conn.execute("UPDATE lines SET raw_text = ?, tts_text = ?, text_hash = ?, source_text = ? WHERE id = ?",
                     (k[4], text.prepare(k[4], k[3]), hash_, k[4], caught_up))
        conn.execute("DELETE FROM line_issues WHERE line_id = ? AND issue = 'untokenised'", (caught_up,))
        adopted.add(caught_up)
    conn.executemany(
        "INSERT INTO lines (type, quest_id, npc_id, player_gender, raw_text, tts_text, text_hash, source_text)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", new)
    stale = [(i,) for k, i in existing.items() if k not in wanted and i not in adopted]  # gone from the Source Data
    conn.executemany("DELETE FROM lines WHERE id = ? AND id NOT IN (SELECT line_id FROM audio)", stale)
    conn.commit()
