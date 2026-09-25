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
DATA = REPO / "data"
DEFAULT_WORLD = DATA / "vmangos" / "sqlite-dump" / "mangos.sqlite"
CORE_ADDON = REPO / "addon" / "VoiceForever"
DASHBOARD = REPO / "dashboard"


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
    sub.add_parser("prep", help="recompute every line's tts_text from its raw text (Lexicon applied)")
    p = sub.add_parser("lexicon", help="list the Lexicon's lore names by line count, with drafts (extracts afresh)")
    p.add_argument("--top", type=int, default=30, help="how many to list (default 30)")
    p = sub.add_parser("prepare", help="Approval Gate: render anchor Candidates per Archetype, apply approvals,"
                                       " write approved_voices.json once all are approved")
    p.add_argument("--archetype", action="append", help="only render this Archetype (repeatable), e.g. orc_f")
    p.add_argument("--candidates", type=int, default=8, help="Candidates per Archetype (default 8)")
    p.add_argument("--samples", type=int, default=3, help="sample lines per Candidate, as continuation (default 3)")
    p.add_argument("--asr-model", default=None, help="Whisper model (mlx-audio)")
    p.add_argument("--list", action="store_true", help="list the Archetypes and their race labels, render nothing")
    p.add_argument("--import-bakeoff", action="append", metavar="ARCHETYPE",
                   help="seed this Archetype's Candidates from the bake-off anchors (vo.bakeoff_seeds), with their"
                        " anchor chain, and render their sample lines; implies --archetype (repeatable), e.g. orc_m")
    p.add_argument("--design", action="store_true",
                   help="render fresh voice-design Candidates even for Archetypes seeded from the bake-off (orc_m)")
    p = sub.add_parser("run", aliases=["generate"], help="work the generation queue unattended (resumable)")
    p.add_argument("--voice", default=None,
                   help="fallback voice for NPCs whose Archetype has no approved anchor: Kokoro voice or"
                        " <backend>:<voice> (default am_michael)")
    p.add_argument("--narrator-voice", default=None,
                   help="voice for lines with no NPC (object and item quests): Kokoro voice or <backend>:<voice>"
                        " (default: the lock's Narrator, kokoro:bm_lewis)")
    p.add_argument("--workers", type=int, default=3, help="parallel render workers (default 3)")
    p.add_argument("--until", metavar="HH:MM", help="stop taking new lines at this local time; rerun to resume")
    p.add_argument("--wer-threshold", type=float, default=0.2, help="max ASR word error rate before a retake")
    p.add_argument("--asr-model", default=None, help="Whisper model (mlx-audio)")
    p.add_argument("--ntfy", default=os.environ.get("VO_NTFY"),
                   help="ntfy topic URL to notify on finish or fatal error (default $VO_NTFY)")
    p.add_argument("--no-caffeinate", action="store_true", help="don't hold caffeinate -dis")
    p.add_argument("--no-retry-quarantined", action="store_true", help="leave quarantined lines alone this run")
    p = sub.add_parser("ingest", help="import Capture records from Core Addon SavedVariables files")
    p.add_argument("files", type=Path, nargs="+", metavar="SAVEDVARIABLES")
    p = sub.add_parser("package", help="build the Voice Packs into build/packs and report lines per pack")
    p.add_argument("--pack", default=None, help="build only this pack, e.g. VoiceForever_Alliance_1-10")
    p = sub.add_parser("install", help="symlink the Core Addon and built packs into an AddOns dir")
    p.add_argument("addons_dir", type=Path)
    p = sub.add_parser("dashboard", help="serve the review dashboard (Bun) on localhost")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--host", default="127.0.0.1", help="bind address (e.g. a Tailscale IP to check from a phone)")
    p.add_argument("--audio", type=Path, default=BUILD / "audio", help="audio dir to serve for playback")
    p.add_argument("--candidates", type=Path, default=BUILD / "candidates", help="Approval Gate audio (vo prepare)")
    bo = sub.add_parser("bakeoff", help="render the model bake-off and its listening page (needs --group bakeoff)")
    bo.add_argument("--round", type=int, choices=(1, 2, 3, 4, 5), default=1,
                    help="1: model comparison; 2: fantasy race voices (designers, cloners, DSP, VC); "
                         "3: consistent VoxCPM2 NPC voices; 4: human-picked anchors (step 1), then "
                         "continuation from the picks (step 2, with --anchors); 5: orc male growl (descriptions, DSP, VC)")
    bo.add_argument("--out", type=Path, help="default build/bakeoff, build/bakeoff2, build/bakeoff3, build/bakeoff4 or build/bakeoff5 by round")
    bo.add_argument("--r2-out", type=Path, default=Path("build/bakeoff2"), help="rounds 3-5: round-2 output (baseline)")
    bo.add_argument("--r4-out", type=Path, default=Path("build/bakeoff4"), help="round 5: round-4 output (anchor s6)")
    bo.add_argument("--anchors", type=Path,
                    help="round 4 step 2: the anchor picks (the step-1 page's Markdown export, or JSON {voice: id}); "
                         "round 5: '- pick: <candidate id>' lines (the page's export) for section 2")
    bo.add_argument("--best", help="round 3: variant for the dwarf and distinct-NPC tests (default: picked by rule)")
    bo.add_argument("--models", default="all", help="comma-separated model (round 1) or approach (round 2) ids, or 'all'")
    bo.add_argument("--r1-out", type=Path, default=Path("build/bakeoff"), help="round-1 output, for the benchmark clips")
    bo.add_argument("--no-variation", action="store_true", help="round 2: skip the per-NPC variation test")
    bo.add_argument("--limit", type=int, help="only the first N lines (smoke test)")
    bo.add_argument("--force", action="store_true", help="re-render clips and reference voices that already exist")
    bo.add_argument("--no-asr", action="store_true", help="skip the ASR word-error check")
    bo.add_argument("--page-only", action="store_true", help="rebuild index.html from results.json")
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
    elif args.command == "prep":
        from vo import lexicon, prep
        conn = db.connect(args.db)
        locked = lexicon.load(lexicon.path()) if lexicon.path().exists() else None
        print(f"tts_text updated for {prep.prepare_lines(conn, lexicon.from_db(conn, locked))} lines")
    elif args.command == "lexicon":
        from vo import lexicon
        conn = db.connect(args.db)
        names = lexicon.extract(conn, _areas(args.world))
        print(f"{len(names)} names; the top {lexicon.TOP} go to the Approval page")
        print(f"{'#':>4} {'lines':>6}  {'name':<22} {'draft':<26} flags")
        for i, n in enumerate(names[:args.top], 1):
            flags = " ".join(f for f, on in (("npc", n.npc), ("zone", n.zone)) if on)
            print(f"{i:>4} {n.lines:>6}  {n.name:<22} {lexicon.draft(n.name):<26} {flags}")
    elif args.command == "prepare":
        from vo import archetypes, asr, lexicon, lock, prepare
        conn = db.connect(args.db)
        if args.list:
            for a in archetypes.derive(conn):
                races = ", ".join(f"{r} ({n})" for r, n in a.races.items())
                print(f"{a.id:<22} {a.label:<28} {a.npcs:>4} NPCs {a.lines:>5} lines  {races}")
            return
        try:
            summary = prepare.prepare(conn, BUILD / "candidates", only=args.archetype, candidates=args.candidates,
                                      samples=args.samples, asr_model=args.asr_model or asr.DEFAULT_MODEL,
                                      design=args.design, import_bakeoff=args.import_bakeoff,
                                      lock_path=lock.path(), lexicon_path=lexicon.path(), areas=_areas(args.world))
        except (archetypes.UnmappedRace, ValueError) as e:
            sys.exit(f"vo prepare: {e}")
        print(prepare.summary_text(summary))
    elif args.command in ("run", "generate"):
        from vo import archetypes, asr, lexicon, lock, prep, run, tts
        conn = db.connect(args.db)
        try:  # the Approval Gate: both locks
            approved = lock.require(conn, lock.path())
            names = lexicon.require(conn, lexicon.path())
        except (lock.LockError, archetypes.UnmappedRace) as e:
            sys.exit(f"vo run refuses to start: {e}")
        os.environ[lock.ENV] = str(lock.path())  # spawned workers load the same lock
        lex = lexicon.from_db(conn, names)
        respelled = prep.prepare_lines(conn, lex)  # a Lexicon change requeues its lines (tts_hash)
        if respelled:
            print(f"Lexicon: tts_text updated for {respelled} lines")
        voice = args.voice or tts.DEFAULT_VOICE
        narrator = args.narrator_voice or approved.get("narrator", {}).get("voice_id") or tts.NARRATOR_VOICE_ID
        opts = dict(
            voice_id=voice if ":" in voice else f"kokoro:{voice}", npc_voices=lock.npc_voice_ids(conn, approved),
            narrator_voice_id=narrator if ":" in narrator else f"kokoro:{narrator}", workers=args.workers,
            until=run.parse_until(args.until, datetime.now()) if args.until else None,
            wer_threshold=args.wer_threshold, asr_model=args.asr_model or asr.DEFAULT_MODEL,
            retry_quarantined=not args.no_retry_quarantined, lexicon=lex)
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # unwind: stop workers, release caffeinate
        with run.caffeinate(not args.no_caffeinate):
            summary = run.run_notified(args.ntfy, lambda: run.run(conn, BUILD / "audio", **opts))
        print(run.summary_text(summary))
    elif args.command == "ingest":
        from vo import ingest, savedvars
        conn, rejected = db.connect(args.db), 0
        for path in args.files:
            try:
                upload_id, counts = ingest.ingest_file(conn, path)
            except (OSError, savedvars.Malformed) as e:
                print(f"{path}: rejected: {e}", file=sys.stderr)
                rejected += 1
                continue
            print(ingest.summary_text(path, upload_id, counts))
        if rejected:
            sys.exit(1)
    elif args.command == "package":
        from vo import package, packs, source
        if args.pack and args.pack not in packs.all_packs():
            sys.exit(f"unknown pack {args.pack}; one of: {', '.join(packs.all_packs())}")
        conn = db.connect(args.db)
        if args.world.exists():
            src = packs.load_source(source.open_world(args.world))
        else:
            print(f"warning: no world DB at {args.world}; packs assigned from NPC zones and levels only",
                  file=sys.stderr)
            src = None
        built = package.package(conn, BUILD / "packs", src, args.pack)
        print(f"{'pack':<30} {'lines':>7} {'voiced':>7} {'MB':>8}")
        for pack, n in package.report(conn, src).items():
            if not args.pack or pack == args.pack:
                print(f"{pack:<30} {n['lines']:>7} {n['voiced']:>7} {n['mb']:>8.1f}  {built.get(pack, '')}")
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
                       "--candidates", str(args.candidates),
                       "--port", str(args.port), "--host", args.host])
    elif args.command == "bakeoff" and args.round == 5:
        from vo.bakeoff import run5

        run5.bakeoff5(args.out or Path("build/bakeoff5"), r2_out=args.r2_out, r4_out=args.r4_out, picks=args.anchors,
                      force=args.force, page_only=args.page_only)
    elif args.command == "bakeoff" and args.round == 4:
        from vo.bakeoff import run4

        run4.bakeoff4(args.out or Path("build/bakeoff4"), r2_out=args.r2_out, picks=args.anchors, force=args.force,
                      page_only=args.page_only)
    elif args.command == "bakeoff" and args.round == 3:
        from vo.bakeoff import round3, run3

        if args.best and args.best not in round3.VARIANT_IDS:
            parser.error(f"unknown variant {args.best!r}; choose from {list(round3.VARIANT_IDS)}")
        run3.bakeoff3(args.out or Path("build/bakeoff3"), r2_out=args.r2_out, best=args.best, force=args.force,
                      page_only=args.page_only)
    elif args.command == "bakeoff" and args.round == 2:
        from vo.bakeoff import round2, run2

        ids = list(round2.APPROACH_IDS) if args.models == "all" else args.models.split(",")
        unknown = set(ids) - set(round2.APPROACH_IDS)
        if unknown:
            parser.error(f"unknown approach(es) {sorted(unknown)}; choose from {list(round2.APPROACH_IDS)}")
        run2.bakeoff2(args.out or Path("build/bakeoff2"), ids, force=args.force, page_only=args.page_only,
                      variation_test=not args.no_variation, r1_out=args.r1_out)
    elif args.command == "bakeoff":
        from vo.bakeoff import catalog, run

        models = list(catalog.MODEL_IDS) if args.models == "all" else args.models.split(",")
        unknown = set(models) - set(catalog.MODEL_IDS)
        if unknown:
            parser.error(f"unknown model(s) {sorted(unknown)}; choose from {list(catalog.MODEL_IDS)}")
        run.bakeoff(args.out or Path("build/bakeoff"), models, limit=args.limit, force=args.force,
                    asr=not args.no_asr, page_only=args.page_only)


def _areas(world: Path) -> list[str]:
    """Zone and area names from the world DB (area_template), if it is there."""
    if not world.exists():
        return []
    from vo import source
    try:
        return [r[0] for r in source.open_world(world).execute("SELECT name FROM area_template WHERE name IS NOT NULL")]
    except Exception:
        return []


if __name__ == "__main__":
    main()
