from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import ProductObservation

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


def mark_interrupted(conn: sqlite3.Connection, source_id: str) -> int:
    """Runs left in 'running' by a crashed process must never become a comparison baseline."""
    cur = conn.execute(
        "UPDATE collection_runs SET status = 'interrupted', finished_at = ? "
        "WHERE source_id = ? AND status = 'running'",
        (utcnow(), source_id),
    )
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


def commit_snapshot(
    conn: sqlite3.Connection, run_id: int, source_id: str, items: list[ProductObservation]
) -> None:
    """Save a fully validated snapshot and close the run in one transaction."""
    now = utcnow()
    conn.execute("BEGIN IMMEDIATE")
    try:
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
        conn.execute(
            "UPDATE collection_runs SET status = 'success', finished_at = ?, product_count = ? WHERE id = ?",
            (now, len(items), run_id),
        )
    except Exception:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def last_success_run(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM collection_runs WHERE source_id = ? AND status = 'success' ORDER BY id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
