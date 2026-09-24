"""`vo` command-line entry point. Each stage registers a subcommand here."""
import argparse
from pathlib import Path

from vo import db

DEFAULT_DB = Path("build/vo.sqlite")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vo")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the pipeline database")
    args = parser.parse_args(argv)

    if args.command == "init":
        db.connect(args.db).close()
        print(f"initialised {args.db}")


if __name__ == "__main__":
    main()
