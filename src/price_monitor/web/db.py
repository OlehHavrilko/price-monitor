"""Read-only connections: never call storage.connect or migrate from the web."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from ..storage import MIGRATIONS


class DatabaseUnavailable(Exception):
    pass


@contextmanager
def read_database(path: Path):
    conn = None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                               isolation_level=None, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        # One consistent snapshot per request, including counts and report sheets.
        conn.execute("BEGIN")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != len(MIGRATIONS):
            raise DatabaseUnavailable("Версія бази несумісна. Запустіть актуальний monitor для підготовки бази.")
        conn.create_function("casefold", 1, lambda value: (value or "").casefold(), deterministic=True)
        yield conn
    except sqlite3.Error as exc:
        raise DatabaseUnavailable("База даних поки недоступна. Перевірте запуск monitor та доступ до файлів WAL.") from exc
    finally:
        if conn is not None:
            conn.close()
