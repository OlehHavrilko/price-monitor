"""Plays the demo scenarios in order, with pauses, so the whole story can be screen-recorded.

Everything here is the normal program path: it switches the demo catalog, runs real collections
against it and lets the service deliver the queued Telegram messages. Nothing is faked.
"""
from __future__ import annotations

import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from . import demo_site, reports, storage
from .collector import RunResult, collect_once
from .models import SourceConfig

# (title, scenario, number of collections). Two collections for "missing": REMOVED needs two full runs.
STORY = [
    ("Prices drop on two tracked products", "price_drop", 1),
    ("One tracked item sells out", "out_of_stock", 1),
    ("Stock is restored and prices return to normal", "initial", 1),
    ("A product disappears from the catalog", "product_missing", 2),
    ("The product returns", "initial", 1),
    ("The source breaks (page layout changed)", "changed_template", 1),
    ("The source recovers", "initial", 1),
]

# Rules the story needs. Set through the same storage function the /track command uses.
TRACKED = [
    ("00123", dict(drop_percent=Decimal("10")), "Desk lamp Nord: alert on a drop of 10% or more"),
    ("01003", dict(threshold=Decimal("12")), "Wireless mouse: alert when the price goes below 12"),
    ("01001", {}, "USB hub: alert on stock changes"),
    ("01002", {}, "HDMI cable: alert on removal and return"),
]


def _collect(conn: sqlite3.Connection, source: SourceConfig, recipients: Sequence[int], lock_dir: Path | None) -> RunResult:
    for _ in range(60):
        result = collect_once(conn, source, recipients=recipients, lock_dir=lock_dir)
        if result.ok or result.run_id is not None:
            return result
        time.sleep(1)  # the service is collecting right now; wait for it to finish
    return result


def play(
    conn: sqlite3.Connection,
    source: SourceConfig,
    state_file: Path,
    reports_dir: Path,
    recipients: Sequence[int] = (),
    lock_dir: Path | None = None,
    pause: float = 20.0,
    say: Callable[[str], None] = print,
) -> Path:
    def wait() -> None:
        if recipients:
            say(f"  (waiting {pause:g} s: the service delivers Telegram messages every 15 s)")
        time.sleep(pause)

    say("Demo story. Source: " + source.id + (" (Telegram messages go to the allowlist)" if recipients else " (Telegram is off)"))
    demo_site.write_scenario(state_file, "initial")
    base = _collect(conn, source, recipients, lock_dir)
    if not base.ok:
        raise SystemExit(f"Cannot start: {base.error}")
    say(f"[0] Baseline: {base.product_count} products collected, nothing to compare with yet.")
    for key, options, description in TRACKED:
        storage.set_rule(conn, source.id, key, **options)
        say(f"    tracking {key}: {description}")
    wait()

    for number, (title, scenario, runs) in enumerate(STORY, start=1):
        say(f"[{number}] {title}  (demo scenario: {scenario})")
        demo_site.write_scenario(state_file, scenario)
        for _ in range(runs):
            result = _collect(conn, source, recipients, lock_dir)
            if result.ok:
                counts = Counter(e.type for e in result.events)
                found = ", ".join(f"{t} x{n}" for t, n in sorted(counts.items())) or "no changes yet"
                say(f"    collection ok, events: {found}")
            else:
                say(f"    collection failed, previous snapshot kept: {result.error}")
        wait()

    now = datetime.now(timezone.utc)
    path = reports.write_report(conn, reports_dir, source.id, now - timedelta(hours=1), now + timedelta(minutes=1))
    say(f"Report with the current state and the history of this run: {path}")
    return path
