"""Source Data: the VMaNGOS world database, read from its published SQLite snapshot."""
import json
import sqlite3
import urllib.request
import zipfile
from pathlib import Path

RELEASE_API = "https://api.github.com/repos/vmangos/core/releases/tags/db_latest"


def fetch(dest: Path) -> Path:
    """Download and unpack the latest VMaNGOS SQLite snapshot; return the world DB path."""
    with urllib.request.urlopen(RELEASE_API) as r:
        release = json.load(r)
    asset = next(a for a in release["assets"] if a["name"].startswith("db-sqlite-"))
    dest.mkdir(parents=True, exist_ok=True)
    archive = dest / asset["name"]
    if not archive.exists():
        urllib.request.urlretrieve(asset["browser_download_url"], archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    return dest / "sqlite-dump" / "mangos.sqlite"


def open_world(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def quest(world: sqlite3.Connection, quest_id: int) -> sqlite3.Row | None:
    """The quest as of the latest patch (VMaNGOS keeps one row per patch that changed it)."""
    return world.execute(
        "SELECT entry, Title, Details, Objectives, RequestItemsText, OfferRewardText"
        " FROM quest_template WHERE entry = ? ORDER BY patch DESC LIMIT 1",
        (quest_id,),
    ).fetchone()


def quest_givers(world: sqlite3.Connection, quest_id: int) -> list[int]:
    return [r[0] for r in world.execute(
        "SELECT id FROM creature_questrelation WHERE quest = ? ORDER BY id", (quest_id,))]
