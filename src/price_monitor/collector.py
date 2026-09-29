from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass

import httpx

from . import storage
from .adapters import fetch_snapshot
from .http import make_client
from .models import CollectionError, SourceConfig

# One run per source at a time inside this process. A file lock against a second process comes with the scheduler.
_source_locks: dict[str, threading.Lock] = {}
_registry_lock = threading.Lock()


def _lock_for(source_id: str) -> threading.Lock:
    with _registry_lock:
        return _source_locks.setdefault(source_id, threading.Lock())


@dataclass(frozen=True)
class RunResult:
    run_id: int | None
    ok: bool
    product_count: int = 0
    error: str | None = None
    fatal: bool = False
    events: tuple = ()


def collect_once(
    conn: sqlite3.Connection, source: SourceConfig, transport: httpx.BaseTransport | None = None
) -> RunResult:
    lock = _lock_for(source.id)
    if not lock.acquire(blocking=False):
        return RunResult(None, False, error=f"A collection of '{source.id}' is already running")
    try:
        storage.ensure_source(conn, source.id, source.base_url)
        storage.mark_interrupted(conn, source.id)
        run_id = storage.start_run(conn, source.id)
        try:
            with make_client(source, transport) as client:
                items = fetch_snapshot(client, source)  # no DB transaction is open here
        except CollectionError as exc:
            storage.fail_run(conn, run_id, exc.reason)
            return RunResult(run_id, False, error=exc.reason, fatal=exc.fatal)
        except Exception as exc:  # unexpected bug: record it, never report an empty success
            reason = f"Unexpected error: {type(exc).__name__}: {exc}"
            storage.fail_run(conn, run_id, reason)
            return RunResult(run_id, False, error=reason)
        events = storage.commit_snapshot(conn, run_id, source.id, items)
        return RunResult(run_id, True, product_count=len(items), events=tuple(events))
    finally:
        lock.release()
