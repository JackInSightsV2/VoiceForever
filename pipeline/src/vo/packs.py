"""Voice Pack assignment: every line belongs to exactly one Voice Pack, by faction and level band.

A Voice Pack is `VoiceForever_<Faction>_<band>`: Faction is Alliance, Horde or Neutral (lines both factions
hear, e.g. Booty Bay, Gadgetzan, Ratchet), band is one of 1-10, 10-20, ..., 50-60, named by the level a player
enters it: levels 1-9 go to 1-10, 10-19 to 10-20, ..., 50 and up to 50-60; unknown or below 1 to 1-10. So a zone
opening at 10 (Westfall) or a level-10 quest is in 10-20, not 1-10.

Quest Text is assigned per quest, so a quest's detail, progress and completion always ship together:
  faction  1. RequiredRaces: only Alliance races -> Alliance, only Horde races -> Horde.
           2. The quest's NPCs (giver and ender): Alliance if one is Alliance-only and none Horde-only,
              likewise Horde; conflicting or all Neutral -> Neutral.
           3. No NPC at all (object or item quests, Narrator only): the team of the quest's zone.
           4. Neutral.
  band     QuestLevel, else MinLevel (QuestLevel -1 scales to the player), else the quest zone's minimum level,
           else the lowest band of its NPCs (below), else 1.
Gossip and quest greetings are assigned per NPC:
  faction  The NPC's faction template: Alliance if it is hostile to every Horde race and not to some Alliance
           race, likewise Horde, else Neutral. An NPC with no known template (Capture) takes the team of its
           zones when they all agree, else Neutral.
  band     The lowest minimum level of the zones it spawns in (a capital city counts as 1, as players of every
           level visit it), else its own minimum level, else 1.

Everything is computed from the pipeline DB plus the VMaNGOS world DB (quest_template, faction_template),
so the assignment is deterministic for a given pair of databases.
"""
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field

PREFIX = "VoiceForever_"
FACTIONS = ("Alliance", "Horde", "Neutral")
BANDS = ((1, "1-10"), (10, "10-20"), (20, "20-30"), (30, "30-40"), (40, "40-50"), (50, "50-60"))  # (from level, name)
QUEST_TYPES = ("quest_detail", "quest_progress", "quest_complete")

ALLIANCE_RACES, HORDE_RACES = 1 | 4 | 8 | 64, 2 | 16 | 32 | 128  # RequiredRaces bits (Human, Dwarf, ... / Orc, ...)
PLAYER_TEMPLATES = {"Alliance": (1, 3, 4, 115), "Horde": (2, 5, 6, 116)}  # each race's player faction template
BUILD = 5875  # 1.12.1: faction_template keeps one row per client build that changed it

A, H = "Alliance", "Horde"
# AreaTable zone id -> (minimum level, team). Minimum levels are the zones' recommended ranges; cities are 1.
# Team is only for faction territory (starting zones, their neighbours, capitals), as area_template.team has it.
ZONES = {
    # capitals and the tram
    1519: (1, A), 1537: (1, A), 1657: (1, A), 2257: (1, A),  # Stormwind, Ironforge, Darnassus, Deeprun Tram
    1637: (1, H), 1638: (1, H), 1497: (1, H),  # Orgrimmar, Thunder Bluff, Undercity
    # 1-10
    12: (1, A), 1: (1, A), 141: (1, A),  # Elwynn Forest, Dun Morogh, Teldrassil
    14: (1, H), 215: (1, H), 85: (1, H),  # Durotar, Mulgore, Tirisfal Glades
    493: (10, None),  # Moonglade: druids from level 10
    # 10-20 and up (outdoor)
    40: (10, A), 38: (10, A), 148: (10, A),  # Westfall, Loch Modan, Darkshore
    17: (10, H), 130: (10, H),  # The Barrens, Silverpine Forest
    44: (15, None), 406: (15, None), 331: (18, None), 10: (18, None),  # Redridge, Stonetalon, Ashenvale, Duskwood
    267: (20, None), 11: (20, None), 400: (25, None),  # Hillsbrad Foothills, Wetlands, Thousand Needles
    36: (30, None), 45: (30, None), 405: (30, None), 33: (30, None),  # Alterac Mts, Arathi, Desolace, Stranglethorn
    15: (35, None), 3: (35, None), 8: (35, None),  # Dustwallow Marsh, Badlands, Swamp of Sorrows
    357: (40, None), 47: (40, None), 440: (40, None), 51: (43, None),  # Feralas, Hinterlands, Tanaris, Searing Gorge
    16: (45, None), 4: (45, None), 490: (48, None), 361: (48, None),  # Azshara, Blasted Lands, Un'Goro, Felwood
    46: (50, None), 25: (50, None), 28: (51, None), 139: (53, None),  # Burning Steppes, Blackrock Mt, W & E Plaguelands
    618: (53, None), 41: (55, None), 1377: (55, None),  # Winterspring, Deadwind Pass, Silithus
    # battlegrounds
    3277: (10, None), 3358: (20, None), 2597: (51, None),  # Warsong Gulch, Arathi Basin, Alterac Valley
    # dungeons and raids
    2437: (13, H), 1581: (17, None), 718: (17, None), 209: (22, None), 719: (24, None), 717: (24, A),
    721: (29, None), 491: (29, None), 796: (34, None), 722: (37, None), 1337: (41, None), 1176: (44, None),
    2100: (46, None), 1477: (50, None), 1417: (50, None), 1584: (52, None), 1583: (55, None), 2557: (55, None),
    2057: (58, None), 2017: (58, None), 2717: (60, None), 2159: (60, None), 1977: (60, None), 3429: (60, None),
    3428: (60, None), 2677: (60, None), 3456: (60, None),
}


def band(level: int | None) -> str:
    """Level band name: 1-9 -> 1-10, 10-19 -> 10-20, ..., 50+ -> 50-60 (unknown -> 1-10)."""
    level = level or 1
    return [name for start, name in BANDS if level >= start or start == 1][-1]


def pack_name(faction: str, band_name: str) -> str:
    return f"{PREFIX}{faction}_{band_name}"


def all_packs() -> list[str]:
    return [pack_name(f, b) for f in FACTIONS for _, b in BANDS]


def race_team(races: int | None) -> str | None:
    races = (races or 0) & (ALLIANCE_RACES | HORDE_RACES)
    if races and not races & HORDE_RACES:
        return A
    if races and not races & ALLIANCE_RACES:
        return H
    return None


@dataclass(frozen=True)
class FactionTemplate:
    faction: int
    our_mask: int
    friendly_mask: int
    hostile_mask: int
    enemies: tuple[int, ...] = ()
    friends: tuple[int, ...] = ()


def hostile(npc: FactionTemplate, player: FactionTemplate) -> bool:
    """Either side hostile to the other, as the server's FactionTemplate reaction rules have it."""
    for a, b in ((npc, player), (player, npc)):
        if b.faction in a.enemies:
            return True
        if b.faction in a.friends:
            continue
        if a.hostile_mask & b.our_mask:
            return True
    return False


def template_team(npc: FactionTemplate, players: dict[str, list[FactionTemplate]]) -> str:
    """The one team whose players can talk to the NPC, else Neutral (both can, or neither)."""
    open_to = [team for team, rows in players.items() if any(not hostile(npc, p) for p in rows)]
    return open_to[0] if len(open_to) == 1 else "Neutral"


@dataclass(frozen=True)
class Quest:
    level: int = 0      # QuestLevel; -1 scales to the player
    min_level: int = 0  # MinLevel
    races: int = 0      # RequiredRaces
    zone: int = 0       # ZoneOrSort; negative is a quest sort (class, profession), not a zone


@dataclass
class Source:
    """What assignment needs from the world DB: quests and each faction template's team."""
    quests: dict[int, Quest] = field(default_factory=dict)
    teams: dict[int, str] = field(default_factory=dict)


def load_source(world: sqlite3.Connection) -> Source:
    from vo.extract import latest

    quests = {r[0]: Quest(r[1], r[2], r[3], r[4]) for r in latest(
        world, "quest_template", "t.entry, t.QuestLevel, t.MinLevel, t.RequiredRaces, t.ZoneOrSort")}
    templates = {}
    for r in world.execute(
            "SELECT t.id, faction_id, our_mask, friendly_mask, hostile_mask, enemy_faction1, enemy_faction2,"
            " enemy_faction3, enemy_faction4, friend_faction1, friend_faction2, friend_faction3, friend_faction4"
            " FROM faction_template t JOIN (SELECT id, MAX(build) AS b FROM faction_template WHERE build <= ?"
            " GROUP BY id) m ON t.id = m.id AND t.build = m.b", (BUILD,)):
        templates[r[0]] = FactionTemplate(r[1], r[2], r[3], r[4], tuple(x for x in r[5:9] if x),
                                          tuple(x for x in r[9:13] if x))
    players = {team: [templates[i] for i in ids if i in templates] for team, ids in PLAYER_TEMPLATES.items()}
    return Source(quests, {i: template_team(t, players) for i, t in templates.items()})


@dataclass(frozen=True)
class Npc:
    template_team: str | None  # from the faction template; None if unknown
    level_min: int | None
    zones: tuple[int, ...]


def zone_team(zones) -> str | None:
    teams = {ZONES[z][1] for z in zones if z in ZONES}
    return teams.pop() if len(teams) == 1 and None not in teams else None


def npc_faction(npc: Npc) -> str:
    if npc.template_team:
        return npc.template_team
    return zone_team(npc.zones) or "Neutral"


def npc_level(npc: Npc) -> int:
    levels = [ZONES[z][0] for z in npc.zones if z in ZONES]
    return min(levels) if levels else (npc.level_min or 1)


def npc_pack(npc: Npc) -> str:
    return pack_name(npc_faction(npc), band(npc_level(npc)))


def quest_pack(quest: Quest | None, npcs: list[Npc]) -> str:
    quest = quest or Quest()
    faction = race_team(quest.races)
    if faction is None and npcs:
        teams = {npc_faction(n) for n in npcs} - {"Neutral"}
        faction = teams.pop() if len(teams) == 1 else "Neutral"
    if faction is None:
        faction = zone_team([quest.zone]) or "Neutral"
    if quest.level > 0:
        level = quest.level
    elif quest.min_level > 0:
        level = quest.min_level
    elif quest.zone in ZONES:
        level = ZONES[quest.zone][0]
    elif npcs:
        level = min(npc_level(n) for n in npcs)
    else:
        level = 1
    return pack_name(faction, band(level))


def _npcs(conn: sqlite3.Connection, source: Source) -> dict[int, Npc]:
    zones = defaultdict(set)
    for npc_id, zone in conn.execute("SELECT npc_id, zone FROM spawns WHERE zone IS NOT NULL"):
        zones[npc_id].add(zone)
    return {r[0]: Npc(source.teams.get(r[1]), r[2], tuple(sorted(zones[r[0]])))
            for r in conn.execute("SELECT id, faction, level_min FROM npcs")}


def assign(conn: sqlite3.Connection, source: Source | None = None) -> dict[int, str]:
    """line id -> Voice Pack name, for every line in the pipeline DB."""
    source = source or Source()
    npcs = _npcs(conn, source)
    unknown = Npc(None, None, ())
    lines = conn.execute("SELECT id, npc_id, quest_id, type FROM lines ORDER BY id").fetchall()
    quest_npcs = defaultdict(set)
    for _, npc_id, quest_id, type_ in lines:
        if type_ in QUEST_TYPES and npc_id is not None:
            quest_npcs[quest_id].add(npc_id)
    by_quest = {q: quest_pack(source.quests.get(q), [npcs.get(n, unknown) for n in sorted(ids)])
                for q, ids in quest_npcs.items()}
    out = {}
    for line_id, npc_id, quest_id, type_ in lines:
        if type_ in QUEST_TYPES:
            if quest_id not in by_quest:
                by_quest[quest_id] = quest_pack(source.quests.get(quest_id), [])
            out[line_id] = by_quest[quest_id]
        else:
            out[line_id] = npc_pack(npcs.get(npc_id, unknown))
    return out
