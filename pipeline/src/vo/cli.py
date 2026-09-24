"""`vo` command-line entry point. Each stage registers a subcommand here."""
import argparse
from pathlib import Path

from vo import db

REPO = Path(__file__).resolve().parents[3]
BUILD = REPO / "build"
DEFAULT_DB = BUILD / "vo.sqlite"
DEFAULT_WORLD = REPO / "data" / "vmangos" / "sqlite-dump" / "mangos.sqlite"
CORE_ADDON = REPO / "addon" / "VoiceForever"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vo")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD, help="VMaNGOS world DB (SQLite)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the pipeline database")
    sub.add_parser("fetch", help="download the VMaNGOS SQLite snapshot into data/vmangos")
    p = sub.add_parser("extract", help="extract quest detail text into lines")
    p.add_argument("--quest", type=int, action="append", required=True)
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
        print(source.fetch(args.world.parents[1]))
    elif args.command == "extract":
        from vo import quests, source
        conn, world = db.connect(args.db), source.open_world(args.world)
        for q in args.quest:
            print(f"quest {q}: lines {quests.extract_detail(conn, world, q)}")
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
