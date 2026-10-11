from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections import Counter
from datetime import datetime, timezone

from . import config, demo_site, reports, storage
from .collector import collect_once


def cmd_collect(args: argparse.Namespace) -> int:
    sources = config.sources()
    if args.source not in sources:
        print(f"Unknown source '{args.source}'. Available: {', '.join(sources)}", file=sys.stderr)
        return 2
    db = config.db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = storage.connect(db)
    recipients = config.allowed_ids() if config.telegram_enabled() else ()
    result = collect_once(conn, sources[args.source], recipients=recipients, lock_dir=config.lock_dir())
    if not result.ok:
        print(f"Collection failed: {result.error}", file=sys.stderr)
        return 1
    print(f"Collected {result.product_count} products from '{args.source}' (run {result.run_id}).")
    if result.events:
        counts = Counter(e.type for e in result.events)
        print("Events: " + ", ".join(f"{t} x{n}" for t, n in sorted(counts.items())))
    else:
        print("No changes since the previous successful run.")
    return 0


def _utc_date(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


def cmd_report(args: argparse.Namespace) -> int:
    sources = config.sources()
    if args.source not in sources:
        print(f"Unknown source '{args.source}'. Available: {', '.join(sources)}", file=sys.stderr)
        return 2
    db = config.db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = storage.connect(db)
    try:
        path = reports.write_report(conn, config.reports_dir(), args.source, _utc_date(args.since), _utc_date(args.until))
    except (reports.ReportError, ValueError) as exc:
        print(f"Report not created: {exc}", file=sys.stderr)
        return 1
    print(f"Report written: {path}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .scheduler import run_service

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(run_service())


def cmd_demo_play(args: argparse.Namespace) -> int:
    from .demo_play import play

    sources = config.sources()
    if args.source not in sources or sources[args.source].adapter != "demo":
        print("demo-play works only with the demo source.", file=sys.stderr)
        return 2
    db = config.db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    state = config.demo_state_file()
    state.parent.mkdir(parents=True, exist_ok=True)
    recipients = config.allowed_ids() if config.telegram_enabled() else ()
    play(
        storage.connect(db), sources[args.source], state, config.reports_dir(),
        recipients=recipients, lock_dir=config.lock_dir(), pause=args.pause,
    )
    return 0


def cmd_resume_source(args: argparse.Namespace) -> int:
    if args.source not in config.sources():
        print(f"Unknown source '{args.source}'.", file=sys.stderr)
        return 2
    conn = storage.connect(config.db_path())
    storage.resume_source(conn, args.source)
    print(f"Source '{args.source}' can be collected again.")
    return 0


def cmd_demo_serve(args: argparse.Namespace) -> int:
    state = config.demo_state_file()
    state.parent.mkdir(parents=True, exist_ok=True)
    server = demo_site.serve(state, host=args.host, port=args.port)
    print(f"Demo catalog (test bench) on http://{args.host}:{args.port}, state file {state}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_demo_scenario(args: argparse.Namespace) -> int:
    if args.name is None:
        for name, text in demo_site.SCENARIOS.items():
            print(f"{name:22} {text}")
        return 0
    state = config.demo_state_file()
    state.parent.mkdir(parents=True, exist_ok=True)
    try:
        demo_site.write_scenario(state, args.name)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    print(f"Demo scenario: {args.name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="price-monitor")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="run one collection for a source")
    p.add_argument("source", nargs="?", default="demo")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("run", help="run the service: schedule, notification delivery and the Telegram bot")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("demo-play", help="play the demo story with pauses (for screen recording)")
    p.add_argument("--source", default="demo")
    p.add_argument("--pause", type=float, default=20.0, help="seconds between steps (default 20)")
    p.set_defaults(func=cmd_demo_play)

    p = sub.add_parser("resume-source", help="allow collection of a source stopped after HTTP 401/403")
    p.add_argument("source")
    p.set_defaults(func=cmd_resume_source)

    p = sub.add_parser("report", help="write an XLSX report; the period is in UTC, end excluded")
    p.add_argument("--source", default="demo")
    p.add_argument("--since", required=True, help="start, ISO date or datetime, e.g. 2026-10-01")
    p.add_argument("--until", required=True, help="end, ISO date or datetime, e.g. 2026-11-01")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("demo-serve", help="run the local demo catalog")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.set_defaults(func=cmd_demo_serve)

    p = sub.add_parser("demo-scenario", help="switch demo catalog scenario (no name: list them)")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_demo_scenario)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
