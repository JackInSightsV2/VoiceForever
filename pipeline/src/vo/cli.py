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
    bo = sub.add_parser("bakeoff", help="render the model bake-off and its listening page (needs --group bakeoff)")
    bo.add_argument("--out", type=Path, default=Path("build/bakeoff"))
    bo.add_argument("--models", default="all", help="comma-separated model ids, or 'all'")
    bo.add_argument("--limit", type=int, help="only the first N lines (smoke test)")
    bo.add_argument("--force", action="store_true", help="re-render clips and reference voices that already exist")
    bo.add_argument("--no-asr", action="store_true", help="skip the ASR word-error check")
    bo.add_argument("--page-only", action="store_true", help="rebuild index.html from results.json")
    args = parser.parse_args(argv)

    if args.command == "init":
        db.connect(args.db).close()
        print(f"initialised {args.db}")
    elif args.command == "bakeoff":
        from vo.bakeoff import catalog, run

        models = list(catalog.MODEL_IDS) if args.models == "all" else args.models.split(",")
        unknown = set(models) - set(catalog.MODEL_IDS)
        if unknown:
            parser.error(f"unknown model(s) {sorted(unknown)}; choose from {list(catalog.MODEL_IDS)}")
        run.bakeoff(args.out, models, limit=args.limit, force=args.force,
                    asr=not args.no_asr, page_only=args.page_only)


if __name__ == "__main__":
    main()
