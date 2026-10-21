"""Read-only access to the monitor's database.

The dashboard never calls `storage.connect()`: that switches the journal mode and runs migrations.
Here the file is opened with `mode=ro`, so a missing database is not created and nothing is written,
even if the volume is mounted writable.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .. import storage


class DatabaseUnavailable(Exception):
    """The database cannot be shown yet: no file, or a schema this program does not read."""


def _casefold(value: str | None) -> str | None:
    return value.casefold() if value is not None else None


def open_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise DatabaseUnavailable(
            "Бази даних ще немає. Вона з'явиться після першого збору: запустіть монітор "
            "(`price-monitor run` або `price-monitor collect`)."
        )
    # One connection per request; FastAPI may finish the request on another thread of its pool.
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5, check_same_thread=False)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        # SQLite's LIKE/lower() fold ASCII only; product names are often Ukrainian.
        conn.create_function("casefold", 1, _casefold, deterministic=True)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        conn.close()
        raise DatabaseUnavailable(f"Базу даних не вдалося прочитати: {exc}") from exc
    expected = len(storage.MIGRATIONS)
    if version != expected:
        conn.close()
        if version < expected:
            raise DatabaseUnavailable(
                f"Схема бази v{version} старіша за v{expected}. Монітор оновить її під час наступного запуску."
            )
        raise DatabaseUnavailable(f"Схема бази v{version} новіша за v{expected}. Оновіть програму.")
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]
