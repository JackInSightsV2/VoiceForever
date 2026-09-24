"""Gossip template harness: render every Gossip line as the client would, match it in the Core Addon's Lua.

The renderer here stands in for the WoW client and is written independently of vo.gossip: it fills $G for the
player gender, $B with a line break, $N/$R/$C with a sample player (lowercase $r/$c lowercased) and world-state
counters with a number, and leaves colour codes and spacing for the addon's own normalisation. Every rendering
must play its own line (or one with an identical pattern) through VF.MatchGossip. Renderings that more than
one of the NPC's patterns match are counted as ambiguous; the most specific pattern wins, and a rendering whose
own line loses that way (e.g. "Hail, $c." shown to a druid, next to a literal "Hail, druid.") is "shadowed".

Run against a pipeline DB: uv run python tests/gossip_harness.py build/vo.sqlite
"""
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from vo import gossip, package

TESTS = Path(__file__).resolve().parent
SAMPLES = [  # (name, race, class): names are letters only in WoW, but may be non-ASCII; races may have spaces
    ("Arthas", "Human", "Paladin"),
    ("Jaína", "Night Elf", "Druid"),
    ("Zug", "Undead", "Warlock"),
]
_GENDER = re.compile(r"\$[Gg]\s*([^:;]*):([^;]*);")


def render(raw: str, gender: str, name: str, race: str, cls: str) -> str:
    """The text the client shows a player of `gender` ('m'/'f')."""
    out = _GENDER.sub(lambda m: m[1] if gender == "m" else m[2], raw)
    out = re.sub(r"\$[Bb]", "\n", out)
    out = re.sub(r"\$\d+[A-Za-z]?", "1234", out)
    values = {"N": name, "n": name, "R": race, "r": race.lower(), "C": cls, "c": cls.lower()}
    return re.sub(r"\$([NnRrCc])", lambda m: values[m[1]], out)


def gossip_lines(conn: sqlite3.Connection) -> list[tuple]:
    """(line id, npc id, player gender, raw text, match pattern) of every Gossip line with an NPC."""
    return conn.execute(f"SELECT id, npc_id, player_gender, raw_text, match_pattern FROM lines"
                        f" WHERE type IN {gossip.TYPES} AND npc_id IS NOT NULL ORDER BY id").fetchall()


def _lua_str(s: str) -> str:
    """Byte-exact Lua string literal (UTF-8 and control bytes as \\ddd escapes)."""
    return '"' + "".join(f"\\{b:03d}" if b < 32 or b > 126 or b in (34, 92) else chr(b)
                         for b in s.encode("utf-8")) + '"'


def script(lines: list[tuple]) -> str:
    """A Lua script (after wowstub.lua) that registers the lines as a pack, matches every rendering and emits
    [cases, failures, ambiguous renderings, ambiguous lines, shadowed]. A pick whose pattern is identical to
    the line's own counts as the line's own."""
    pattern = {i: p or gossip.match_pattern(raw, g) for i, _, g, raw, p in lines}
    index = gossip.build_index([(npc, g, pattern[i], str(i)) for i, npc, g, _, _ in lines])
    cases = []
    for i, npc, g, raw, _ in lines:
        for gender in [g] if g else ["m", "f"]:
            for sample in SAMPLES:
                cases.append(f"{{{npc},{_lua_str(gender)},{_lua_str(render(raw, gender, *sample))},\"{i}\"}}")
    return "\n".join([
        "load_addon()",
        package.lua_index("VoiceForever_Harness", {}, index),
        f"local PATTERN = {{}}",
        *(f"PATTERN[\"{i}\"] = {_lua_str(p)}" for i, p in pattern.items()),
        "local CASES = {", ",\n".join(cases), "}",
        """
local failures, ambiguous, ambiguousLines, shadowed = {}, 0, {}, {}
for _, c in ipairs(CASES) do
  local npc, gender, text, id = c[1], c[2], c[3], c[4]
  local best = VoiceForever.MatchGossip(npc, text, gender)
  if not best or PATTERN[best.file] ~= PATTERN[id] then
    if best then shadowed[#shadowed + 1] = { id, best.file } else failures[#failures + 1] = { id, text } end
  end
  local n, norm, seen = 0, VoiceForever.Normalise(text), {}
  for _, e in ipairs(VoiceForever.gossip[npc]) do
    if (not e.gender or e.gender == gender) and not seen[e.pattern] and norm:find(e.pattern) then
      seen[e.pattern] = true
      n = n + 1
    end
  end
  if n > 1 then ambiguous = ambiguous + 1; ambiguousLines[id] = true end
end
local lines = 0
for _ in pairs(ambiguousLines) do lines = lines + 1 end
emit(#CASES) emit(failures) emit(ambiguous) emit(lines) emit(shadowed)
"""])


def run(lines: list[tuple], lua) -> dict:
    """lua: the conftest fixture's runner (script -> emitted values)."""
    cases, failures, ambiguous, ambiguous_lines, shadowed = lua(script(lines))
    return {"lines": len(lines), "npcs": len({l[1] for l in lines}), "renderings": cases,
            "matched": cases - len(failures) - len(shadowed), "failures": failures, "shadowed": shadowed,
            "ambiguous_renderings": ambiguous, "ambiguous_lines": ambiguous_lines}


def main(db_path: str) -> None:
    from conftest import addon_files, lua_literal

    def lua(body: str) -> list:
        prelude = (f"ADDON_FILES = {lua_literal([str(f) for f in addon_files()])}\n"
                   f"dofile({lua_literal(str(TESTS / 'wowstub.lua'))})\n")
        out = subprocess.run(["lua", "-"], input=prelude + body, capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        return [json.loads(l) for l in out.stdout.splitlines()]

    stats = run(gossip_lines(sqlite3.connect(db_path)), lua)
    stats["failures"], stats["shadowed"] = stats["failures"][:10], stats["shadowed"][:10]
    print(json.dumps(stats, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1])
