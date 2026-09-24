"""Source Data: QuestieDB's Forever tables (zones and quest start/end points).

Each file is Lua: `QuestieDB.<x>Keys = {['name'] = 1, ...}` then `QuestieDB.<x>Data = [[return {...}]]`.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/Questie/QuestieDB/master/data/Forever/"
FILES = {"npc": "foreverNpcDB.lua", "quest": "foreverQuestDB.lua", "object": "foreverObjectDB.lua"}

_KEY = re.compile(r"^\s*\['(\w+)'\]\s*=\s*(\d+)", re.M)


@dataclass
class Npc:
    zone: int | None                                   # Questie's guess at the most common zone
    spawns: dict[int, list[tuple[float, float]]] = field(default_factory=dict)  # zone -> [(x%, y%)]


@dataclass
class Quest:
    creature_starts: list[int] = field(default_factory=list)
    object_starts: list[int] = field(default_factory=list)
    item_starts: list[int] = field(default_factory=list)
    creature_ends: list[int] = field(default_factory=list)
    object_ends: list[int] = field(default_factory=list)
    zone_or_sort: int | None = None


@dataclass
class Questie:
    npcs: dict[int, Npc]
    quests: dict[int, Quest]


def parse(src: str, var: str) -> tuple[dict[str, int], dict]:
    """Return (keys, rows) for one Questie Lua file; rows are nested Python dicts/lists."""
    from lupa import LuaRuntime

    head, _, rest = src.partition(f"QuestieDB.{var}Data = [[")
    keys_block = head.split(f"QuestieDB.{var}Keys = {{", 1)[1].split("\n}", 1)[0]
    keys = {k: int(v) for k, v in _KEY.findall(keys_block)}  # commented `--[...` lines don't match
    body = rest.rsplit("]]", 1)[0]
    table = LuaRuntime(unpack_returned_tuples=True).execute(body)
    return keys, {int(k): _py(v) for k, v in table.items()}


def _py(v):
    """Lua table -> dict (array tables keep their 1-based keys); scalars unchanged."""
    if v is None or isinstance(v, (int, float, str, bool)):
        return v
    return {k: _py(x) for k, x in v.items()}


def _nth(v, i):
    return v.get(i) if isinstance(v, dict) else None


def _field(row, keys, name):
    return _nth(row, keys[name]) if name in keys else None


def _ids(v) -> list[int]:
    return [int(x) for _, x in sorted(v.items())] if isinstance(v, dict) else []


def load(directory: Path) -> Questie:
    keys, rows = parse((directory / FILES["npc"]).read_text(encoding="utf-8"), "npc")
    npcs = {}
    for npc_id, row in rows.items():
        spawns = {}
        for zone, coords in (_field(row, keys, "spawns") or {}).items():
            pts = [p for _, p in sorted(coords.items())] if isinstance(coords, dict) else []
            spawns[int(zone)] = [(float(p[1]), float(p[2])) for p in pts if isinstance(p, dict) and 1 in p]
        zone = _field(row, keys, "zoneID")
        npcs[npc_id] = Npc(zone=int(zone) if zone else None, spawns=spawns)

    keys, rows = parse((directory / FILES["quest"]).read_text(encoding="utf-8"), "quest")
    quests = {}
    for quest_id, row in rows.items():
        started, finished = _field(row, keys, "startedBy"), _field(row, keys, "finishedBy")
        zos = _field(row, keys, "zoneOrSort")
        quests[quest_id] = Quest(
            creature_starts=_ids(_nth(started, 1)), object_starts=_ids(_nth(started, 2)),
            item_starts=_ids(_nth(started, 3)),
            creature_ends=_ids(_nth(finished, 1)), object_ends=_ids(_nth(finished, 2)),
            zone_or_sort=int(zos) if zos else None)
    return Questie(npcs=npcs, quests=quests)
