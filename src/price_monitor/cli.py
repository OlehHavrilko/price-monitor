from __future__ import annotations

import argparse
import sys

from . import config, demo_site, storage
from .collector import collect_once


def cmd_collect(args: argparse.Namespace) -> int:
    sources = config.sources()
    if args.source not in sources:
        print(f"Unknown source '{args.source}'. Available: {', '.join(sources)}", file=sys.stderr)
        return 2
    db = config.db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = storage.connect(db)
    result = collect_once(conn, sources[args.source])
    if not result.ok:
        print(f"Collection failed: {result.error}", file=sys.stderr)
        return 1
    print(f"Collected {result.product_count} products from '{args.source}' (run {result.run_id}).")
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
