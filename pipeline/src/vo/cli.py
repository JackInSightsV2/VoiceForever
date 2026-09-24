"""`vo` command-line entry point. Each stage registers a subcommand here."""
import argparse
from pathlib import Path

from vo import db

REPO = Path(__file__).resolve().parents[3]
BUILD = REPO / "build"
DEFAULT_DB = BUILD / "vo.sqlite"
DATA = REPO / "data"
DEFAULT_WORLD = DATA / "vmangos" / "sqlite-dump" / "mangos.sqlite"
CORE_ADDON = REPO / "addon" / "VoiceForever"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vo")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD, help="VMaNGOS world DB (SQLite)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the pipeline database")
    sub.add_parser("fetch", help="download the Source Data (VMaNGOS, QuestieDB, display tables) into data/")
    p = sub.add_parser("extract", help="extract Source Data into npcs, spawns and lines")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--quest", type=int, action="append", help="one quest's detail text only")
    g.add_argument("--all", action="store_true", help="all Core Content: NPCs, spawns, Quest Text, Gossip")
    sub.add_parser("stats", help="summarise NPCs, lines and words in the pipeline DB")
    p = sub.add_parser("generate", help="render audio for lines without it")
    p.add_argument("--voice", default=None, help="Kokoro voice (default am_michael)")
    p = sub.add_parser("package", help="build a Voice Pack into build/packs")
    p.add_argument("--pack", default=None)
    p = sub.add_parser("install", help="symlink the Core Addon and built packs into an AddOns dir")
    p.add_argument("addons_dir", type=Path)
    args = parser.parse_args(argv)

    if args.command == "init":
        db.connect(args.db).close()
        print(f"initialised {args.db}")
    elif args.command == "fetch":
        from vo import source
        for path in source.fetch_all(DATA):
            print(path)
    elif args.command == "extract":
        from vo import quests, source
        conn, world = db.connect(args.db), source.open_world(args.world)
        if args.all:
            from vo import extract
            questie, displays = source.load_questie(DATA), source.load_displays(DATA, world)
            print(extract.extract_all(conn, world, questie, displays))
        else:
            for q in args.quest:
                print(f"quest {q}: lines {quests.extract_detail(conn, world, q)}")
    elif args.command == "stats":
        from vo import stats
        print(stats.summary(db.connect(args.db)))
    elif args.command == "generate":
        from vo import tts
        for path in tts.generate(db.connect(args.db), BUILD / "audio", args.voice or tts.DEFAULT_VOICE):
            print(path)
    elif args.command == "package":
        from vo import package
        print(package.package(db.connect(args.db), BUILD / "packs", args.pack or package.DEFAULT_PACK))
    elif args.command == "install":
        from vo import install
        for link in install.install(args.addons_dir, CORE_ADDON, BUILD / "packs"):
            print(f"{link} -> {link.resolve()}")


if __name__ == "__main__":
    main()
