from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from collections.abc import Sequence
from decimal import Decimal

from . import comparison, notifications, outbox
from .models import Availability, ProductObservation

# Each entry upgrades the schema by one version. Never edit an applied entry; append a new one.
MIGRATIONS: list[str] = [
    """
    CREATE TABLE sources (
        id TEXT PRIMARY KEY,
        base_url TEXT NOT NULL
    );
    CREATE TABLE collection_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT NOT NULL REFERENCES sources(id),
        started_at TEXT NOT NULL,
        finished_at TEXT,
        status TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed', 'interrupted')),
        error TEXT,
        product_count INTEGER
    );
    CREATE INDEX idx_runs_source ON collection_runs(source_id, id);
    CREATE TABLE products (
        source_id TEXT NOT NULL REFERENCES sources(id),
        product_key TEXT NOT NULL,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        first_seen TEXT NOT NULL,
        PRIMARY KEY (source_id, product_key)
    );
    CREATE TABLE observations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES collection_runs(id),
        source_id TEXT NOT NULL,
        product_key TEXT NOT NULL,
        name TEXT NOT NULL,
        url TEXT NOT NULL,
        price TEXT NOT NULL,
        currency TEXT NOT NULL,
        availability TEXT NOT NULL CHECK (availability IN ('IN_STOCK', 'OUT_OF_STOCK', 'UNKNOWN')),
        observed_at TEXT NOT NULL,
        UNIQUE (run_id, product_key),
        FOREIGN KEY (source_id, product_key) REFERENCES products(source_id, product_key)
    );
    """,
    """
    CREATE TABLE product_state (
        source_id TEXT NOT NULL,
        product_key TEXT NOT NULL,
        missing_count INTEGER NOT NULL DEFAULT 0,
        removed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (source_id, product_key),
        FOREIGN KEY (source_id, product_key) REFERENCES products(source_id, product_key)
    );
    CREATE TABLE tracking_rules (
        source_id TEXT NOT NULL,
        product_key TEXT NOT NULL,
        threshold TEXT,
        drop_percent TEXT,
        PRIMARY KEY (source_id, product_key),
        FOREIGN KEY (source_id, product_key) REFERENCES products(source_id, product_key)
    );
    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id INTEGER NOT NULL REFERENCES collection_runs(id),
        source_id TEXT NOT NULL,
        product_key TEXT NOT NULL,
        type TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        created_at TEXT NOT NULL,
        UNIQUE (run_id, product_key, type)
    );
    CREATE INDEX idx_events_source_time ON events(source_id, created_at);
    """,
    """
    CREATE TABLE notification_outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        dedupe_key TEXT NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'sent', 'failed')),
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT NOT NULL,
        last_error TEXT,
        created_at TEXT NOT NULL,
        sent_at TEXT,
        UNIQUE (chat_id, dedupe_key)
    );
    CREATE INDEX idx_outbox_due ON notification_outbox(status, next_attempt_at);
    CREATE TABLE source_health (
        source_id TEXT PRIMARY KEY REFERENCES sources(id),
        alerted INTEGER NOT NULL DEFAULT 0,
        stopped INTEGER NOT NULL DEFAULT 0,
        last_error TEXT
    );
    """,
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: str | Path) -> sqlite3.Connection:
    # isolation_level=None: transactions are opened explicitly, so none stays open during network I/O.
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise RuntimeError(
            f"Database schema v{version} is newer than this program (v{len(MIGRATIONS)}). Update the program."
        )
    for target in range(version, len(MIGRATIONS)):
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in MIGRATIONS[target].split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {target + 1}")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def ensure_source(conn: sqlite3.Connection, source_id: str, base_url: str) -> None:
    conn.execute(
        "INSERT INTO sources(id, base_url) VALUES (?, ?) "
        "ON CONFLICT(id) DO UPDATE SET base_url = excluded.base_url",
        (source_id, base_url),
    )


def _break_missing_series(conn: sqlite3.Connection, source_id: str) -> None:
    """An unsuccessful attempt does not confirm absence and interrupts the series of absences."""
    conn.execute(
        "UPDATE product_state SET missing_count = 0 WHERE source_id = ? AND removed = 0", (source_id,)
    )


def mark_interrupted(conn: sqlite3.Connection, source_id: str) -> int:
    """Runs left in 'running' by a crashed process must never become a comparison baseline."""
    cur = conn.execute(
        "UPDATE collection_runs SET status = 'interrupted', finished_at = ? "
        "WHERE source_id = ? AND status = 'running'",
        (utcnow(), source_id),
    )
    if cur.rowcount:
        _break_missing_series(conn, source_id)
    return cur.rowcount


def start_run(conn: sqlite3.Connection, source_id: str) -> int:
    cur = conn.execute(
        "INSERT INTO collection_runs(source_id, started_at, status) VALUES (?, ?, 'running')",
        (source_id, utcnow()),
    )
    return cur.lastrowid


def fail_run(conn: sqlite3.Connection, run_id: int, error: str) -> None:
    conn.execute(
        "UPDATE collection_runs SET status = 'failed', finished_at = ?, error = ? WHERE id = ?",
        (utcnow(), error, run_id),
    )
    source_id = conn.execute("SELECT source_id FROM collection_runs WHERE id = ?", (run_id,)).fetchone()[0]
    _break_missing_series(conn, source_id)


def _row_to_observation(r: sqlite3.Row) -> ProductObservation:
    return ProductObservation(
        product_key=r["product_key"],
        name=r["name"],
        url=r["url"],
        price=Decimal(r["price"]),
        currency=r["currency"],
        availability=Availability(r["availability"]),
    )


def _load_comparison_inputs(conn: sqlite3.Connection, run_id: int, source_id: str):
    previous_run = conn.execute(
        "SELECT id FROM collection_runs WHERE source_id = ? AND status = 'success' AND id < ? "
        "ORDER BY id DESC LIMIT 1",
        (source_id, run_id),
    ).fetchone()
    previous = {}
    if previous_run:
        rows = conn.execute("SELECT * FROM observations WHERE run_id = ?", (previous_run["id"],))
        previous = {r["product_key"]: _row_to_observation(r) for r in rows}
    known = {r[0] for r in conn.execute("SELECT product_key FROM products WHERE source_id = ?", (source_id,))}
    states = {
        r["product_key"]: comparison.ProductState(r["missing_count"], bool(r["removed"]))
        for r in conn.execute("SELECT * FROM product_state WHERE source_id = ?", (source_id,))
    }
    rules = {
        r["product_key"]: comparison.Rule(
            Decimal(r["threshold"]) if r["threshold"] else None,
            Decimal(r["drop_percent"]) if r["drop_percent"] else None,
        )
        for r in conn.execute("SELECT * FROM tracking_rules WHERE source_id = ?", (source_id,))
    }
    return previous_run is not None, previous, known, states, rules


def _enqueue_product_alerts(
    conn: sqlite3.Connection,
    run_id: int,
    source_id: str,
    recipients: Sequence[int],
    events: list[comparison.Event],
    tracked: set[str],
) -> None:
    by_product: dict[str, list[comparison.Event]] = {}
    for ev in events:
        if ev.product_key in tracked and ev.type in notifications.NOTIFIABLE:
            by_product.setdefault(ev.product_key, []).append(ev)
    for key, evs in by_product.items():
        row = conn.execute(
            "SELECT p.name, p.url, (SELECT currency FROM observations o WHERE o.source_id = p.source_id "
            "AND o.product_key = p.product_key ORDER BY o.id DESC LIMIT 1) AS currency "
            "FROM products p WHERE p.source_id = ? AND p.product_key = ?",
            (source_id, key),
        ).fetchone()
        text = notifications.product_message(source_id, key, row["name"], row["url"], row["currency"] or "", evs)
        outbox.enqueue(conn, recipients, f"{run_id}:{key}", text)


def _update_source_health_on_success(
    conn: sqlite3.Connection, source_id: str, run_id: int, recipients: Sequence[int]
) -> None:
    row = conn.execute("SELECT alerted FROM source_health WHERE source_id = ?", (source_id,)).fetchone()
    if row and row["alerted"]:
        outbox.enqueue(conn, recipients, f"{run_id}:recovered", notifications.source_recovered_message(source_id))
    conn.execute(
        "INSERT INTO source_health(source_id, alerted, stopped, last_error) VALUES (?, 0, 0, NULL) "
        "ON CONFLICT(source_id) DO UPDATE SET alerted = 0, last_error = NULL",
        (source_id,),
    )


def record_failure(
    conn: sqlite3.Connection,
    run_id: int,
    source_id: str,
    error: str,
    fatal: bool = False,
    recipients: Sequence[int] = (),
) -> None:
    """Close a run as failed. The operator is told once per outage, not on every failed cycle."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        fail_run(conn, run_id, error)
        row = conn.execute("SELECT alerted FROM source_health WHERE source_id = ?", (source_id,)).fetchone()
        if not (row and row["alerted"]):
            outbox.enqueue(
                conn, recipients, f"{run_id}:failed", notifications.source_failed_message(source_id, error, fatal)
            )
        conn.execute(
            "INSERT INTO source_health(source_id, alerted, stopped, last_error) VALUES (?, 1, ?, ?) "
            "ON CONFLICT(source_id) DO UPDATE SET alerted = 1, last_error = excluded.last_error, "
            "stopped = MAX(stopped, excluded.stopped)",
            (source_id, int(fatal), error),
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def is_stopped(conn: sqlite3.Connection, source_id: str) -> bool:
    row = conn.execute("SELECT stopped FROM source_health WHERE source_id = ?", (source_id,)).fetchone()
    return bool(row and row["stopped"])


def resume_source(conn: sqlite3.Connection, source_id: str) -> None:
    conn.execute("UPDATE source_health SET stopped = 0 WHERE source_id = ?", (source_id,))


def commit_snapshot(
    conn: sqlite3.Connection,
    run_id: int,
    source_id: str,
    items: list[ProductObservation],
    recipients: Sequence[int] = (),
) -> list[comparison.Event]:
    """Save a validated snapshot, its events, queued notifications and the run result in one transaction."""
    now = utcnow()
    current = {i.product_key: i for i in items}
    conn.execute("BEGIN IMMEDIATE")
    try:
        has_baseline, previous, known, states, rules = _load_comparison_inputs(conn, run_id, source_id)
        # The first successful run is only a baseline: there is nothing to compare it with.
        events, new_states = [], {}
        if has_baseline:
            events, new_states = comparison.compare(previous, current, known, states, rules)
        for item in items:
            conn.execute(
                "INSERT INTO products(source_id, product_key, name, url, first_seen) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(source_id, product_key) DO UPDATE SET name = excluded.name, url = excluded.url",
                (source_id, item.product_key, item.name, item.url, now),
            )
            conn.execute(
                "INSERT INTO observations(run_id, source_id, product_key, name, url, price, currency, "
                "availability, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    source_id,
                    item.product_key,
                    item.name,
                    item.url,
                    str(item.price),
                    item.currency,
                    item.availability.value,
                    now,
                ),
            )
        for key, state in new_states.items():
            conn.execute(
                "INSERT INTO product_state(source_id, product_key, missing_count, removed) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(source_id, product_key) DO UPDATE SET "
                "missing_count = excluded.missing_count, removed = excluded.removed",
                (source_id, key, state.missing_count, int(state.removed)),
            )
        for ev in events:
            conn.execute(
                "INSERT INTO events(run_id, source_id, product_key, type, old_value, new_value, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, source_id, ev.product_key, ev.type, ev.old_value, ev.new_value, now),
            )
        _enqueue_product_alerts(conn, run_id, source_id, recipients, events, set(rules))
        _update_source_health_on_success(conn, source_id, run_id, recipients)
        conn.execute(
            "UPDATE collection_runs SET status = 'success', finished_at = ?, product_count = ? WHERE id = ?",
            (now, len(items), run_id),
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return events


def set_rule(
    conn: sqlite3.Connection,
    source_id: str,
    product_key: str,
    threshold: Decimal | None = None,
    drop_percent: Decimal | None = None,
) -> None:
    """Create or replace the rule of a known product. Repeating the same call changes nothing."""
    if threshold is not None and threshold < 0:
        raise ValueError("Threshold must not be negative")
    if drop_percent is not None and drop_percent <= 0:
        raise ValueError("Drop percent must be positive")
    if not conn.execute(
        "SELECT 1 FROM products WHERE source_id = ? AND product_key = ?", (source_id, product_key)
    ).fetchone():
        raise ValueError(f"Unknown product '{product_key}' in source '{source_id}'")
    conn.execute(
        "INSERT INTO tracking_rules(source_id, product_key, threshold, drop_percent) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(source_id, product_key) DO UPDATE SET "
        "threshold = excluded.threshold, drop_percent = excluded.drop_percent",
        (
            source_id,
            product_key,
            None if threshold is None else str(threshold),
            None if drop_percent is None else str(drop_percent),
        ),
    )


def remove_rule(conn: sqlite3.Connection, source_id: str, product_key: str) -> None:
    conn.execute("DELETE FROM tracking_rules WHERE source_id = ? AND product_key = ?", (source_id, product_key))


def last_success_run(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM collection_runs WHERE source_id = ? AND status = 'success' ORDER BY id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
