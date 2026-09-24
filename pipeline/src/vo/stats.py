"""`vo stats`: a summary of what extraction put in the pipeline DB."""
import re
import sqlite3

WORD = re.compile(r"[\w'’-]+")


def words(text: str) -> int:
    return len(WORD.findall(re.sub(r"\$[Bb]", " ", text or "")))


def summary(conn: sqlite3.Connection) -> str:
    q = lambda sql: conn.execute(sql).fetchall()
    npcs = q("SELECT COUNT(*) FROM npcs")[0][0]
    unresolved = q("SELECT COUNT(*) FROM npcs WHERE race IS NULL OR gender IS NULL")[0][0]
    out = [f"NPCs: {npcs}  (named {q('SELECT COUNT(*) FROM npcs WHERE is_named')[0][0]})",
           f"unresolved race/gender: {unresolved} ({100 * unresolved / npcs if npcs else 0:.2f}%)"]
    out.append("roles: " + ", ".join(f"{r} {n}" for r, n in q(
        "SELECT role, COUNT(*) FROM npcs GROUP BY role ORDER BY 2 DESC")))
    out.append("genders: " + ", ".join(f"{g} {n}" for g, n in q(
        "SELECT IFNULL(gender, 'NULL'), COUNT(*) FROM npcs GROUP BY 1 ORDER BY 2 DESC")))
    out.append("top races: " + ", ".join(f"{r} {n}" for r, n in q(
        "SELECT IFNULL(race, 'NULL'), COUNT(*) FROM npcs GROUP BY 1 ORDER BY 2 DESC LIMIT 15")))
    out.append("issues: " + (", ".join(f"{i} {n}" for i, n in q(
        "SELECT issue, COUNT(DISTINCT npc_id) FROM npc_issues GROUP BY issue ORDER BY 2 DESC")) or "none"))
    spawns = q("SELECT COUNT(*), SUM(zone IS NULL) FROM spawns")[0]
    out.append(f"spawns: {spawns[0]}  (no zone {spawns[1] or 0})")

    out.append("lines:")
    total_lines = total_words = 0
    for kind, rows in _by_type(conn).items():
        n, w = len(rows), sum(words(t) for t in rows)
        total_lines += n
        total_words += w
        out.append(f"  {kind:<15} {n:>7} lines {w:>9} words")
    out.append(f"  {'total':<15} {total_lines:>7} lines {total_words:>9} words")
    narrator = q("SELECT COUNT(*) FROM lines WHERE npc_id IS NULL")[0][0]
    out.append(f"  Narrator lines: {narrator}")
    return "\n".join(out)


def _by_type(conn):
    out = {}
    for kind, raw in conn.execute("SELECT type, raw_text FROM lines ORDER BY type"):
        out.setdefault(kind, []).append(raw)
    return out
