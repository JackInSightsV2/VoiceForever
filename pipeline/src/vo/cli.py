"""`vo` command-line entry point. Each stage registers a subcommand here."""
import argparse
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

from vo import db

REPO = Path(__file__).resolve().parents[3]
BUILD = REPO / "build"
DEFAULT_DB = BUILD / "vo.sqlite"
DEFAULT_WORLD = REPO / "data" / "vmangos" / "sqlite-dump" / "mangos.sqlite"
CORE_ADDON = REPO / "addon" / "VoiceForever"
DASHBOARD = REPO / "dashboard"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="vo")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--world", type=Path, default=DEFAULT_WORLD, help="VMaNGOS world DB (SQLite)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="create the pipeline database")
    sub.add_parser("fetch", help="download the VMaNGOS SQLite snapshot into data/vmangos")
    p = sub.add_parser("extract", help="extract quest detail text into lines")
    p.add_argument("--quest", type=int, action="append", required=True)
    sub.add_parser("prep", help="recompute every line's tts_text from its raw text")
    p = sub.add_parser("run", aliases=["generate"], help="work the generation queue unattended (resumable)")
    p.add_argument("--voice", default=None,
                   help="default voice for NPCs without one: Kokoro voice or <backend>:<voice> (default am_michael)")
    p.add_argument("--workers", type=int, default=3, help="parallel render workers (default 3)")
    p.add_argument("--until", metavar="HH:MM", help="stop taking new lines at this local time; rerun to resume")
    p.add_argument("--wer-threshold", type=float, default=0.2, help="max ASR word error rate before a retake")
    p.add_argument("--asr-model", default=None, help="Whisper model (mlx-audio)")
    p.add_argument("--ntfy", default=os.environ.get("VO_NTFY"),
                   help="ntfy topic URL to notify on finish or fatal error (default $VO_NTFY)")
    p.add_argument("--no-caffeinate", action="store_true", help="don't hold caffeinate -dis")
    p.add_argument("--no-retry-quarantined", action="store_true", help="leave quarantined lines alone this run")
    p = sub.add_parser("package", help="build a Voice Pack into build/packs")
    p.add_argument("--pack", default=None)
    p = sub.add_parser("install", help="symlink the Core Addon and built packs into an AddOns dir")
    p.add_argument("addons_dir", type=Path)
    p = sub.add_parser("dashboard", help="serve the review dashboard (Bun) on localhost")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1", help="bind address (e.g. a Tailscale IP to check from a phone)")
    p.add_argument("--audio", type=Path, default=BUILD / "audio", help="audio dir to serve for playback")
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
    elif args.command == "prep":
        from vo import prep
        print(f"tts_text updated for {prep.prepare_lines(db.connect(args.db))} lines")
    elif args.command in ("run", "generate"):
        from vo import asr, run, tts
        voice = args.voice or tts.DEFAULT_VOICE
        opts = dict(
            voice_id=voice if ":" in voice else f"kokoro:{voice}", workers=args.workers,
            until=run.parse_until(args.until, datetime.now()) if args.until else None,
            wer_threshold=args.wer_threshold, asr_model=args.asr_model or asr.DEFAULT_MODEL,
            retry_quarantined=not args.no_retry_quarantined)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # unwind: stop workers, release caffeinate
        with run.caffeinate(not args.no_caffeinate):
            summary = run.run_notified(args.ntfy, lambda: run.run(db.connect(args.db), BUILD / "audio", **opts))
        print(run.summary_text(summary))
    elif args.command == "package":
        from vo import package
        print(package.package(db.connect(args.db), BUILD / "packs", args.pack or package.DEFAULT_PACK))
    elif args.command == "install":
        from vo import install
        for link in install.install(args.addons_dir, CORE_ADDON, BUILD / "packs"):
            print(f"{link} -> {link.resolve()}")
    elif args.command == "dashboard":
        import shutil
        bun = shutil.which("bun")
        if bun is None:
            sys.exit("vo dashboard needs Bun: https://bun.sh")
        db.connect(args.db).close()  # create it and its tables (WAL) so the dashboard can open it read-only
        os.execv(bun, [bun, str(DASHBOARD / "server.ts"), "--db", str(args.db), "--audio", str(args.audio),
                       "--port", str(args.port), "--host", args.host])


if __name__ == "__main__":
    main()
