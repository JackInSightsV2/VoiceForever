"""Source Data: the VMaNGOS world database (its published SQLite snapshot), QuestieDB's Forever
tables and Forever's display tables, all downloaded into data/."""
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


def download(url: str, dest: Path) -> Path:
    """Fetch url to dest unless it is already there."""
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        req = urllib.request.Request(url, headers={"User-Agent": "VoiceForever-pipeline"})
        with urllib.request.urlopen(req) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.rename(dest)
    return dest


def fetch_all(data: Path) -> list[Path]:
    """Download every Source Data file extraction reads; return their paths."""
    from vo import display, questie

    paths = [fetch(data / "vmangos")]
    for name in questie.FILES.values():
        paths.append(download(questie.BASE_URL + name, data / "questie" / name))
    from vo import npcgamevoice
    for table in display.TABLES + npcgamevoice.TABLES:
        url = display.DB2_URL.format(table=table, build=display.BUILD)
        paths.append(download(url, data / "db2" / display.BUILD / f"{table}.csv"))
    paths.append(download(display.LISTFILE_URL, data / "community-listfile.csv"))
    return paths


def load_questie(data: Path):
    from vo import questie
    return questie.load(data / "questie")


def load_displays(data: Path, world: sqlite3.Connection):
    """Forever display tables, plus VMaNGOS's per-display gender as a fallback for creature models."""
    from vo import display
    server_gender = {r[0]: r[1] for r in world.execute(
        "SELECT display_id, gender FROM creature_display_info_addon ORDER BY build")}
    return display.Displays.load(data / "db2" / display.BUILD, data / "community-listfile.csv", server_gender)


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
