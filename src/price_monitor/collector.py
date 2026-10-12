from __future__ import annotations

import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import storage
from .adapters import fetch_snapshot
from .http import make_client
from .locking import FileLock
from .models import CollectionError, SourceConfig

# One run per source at a time: a thread lock inside the process and, when lock_dir is given,
# a file lock against a second process (for example a manual `collect` while the service runs).
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
    conn: sqlite3.Connection,
    source: SourceConfig,
    transport: httpx.BaseTransport | None = None,
    recipients: Sequence[int] = (),
    lock_dir: Path | None = None,
) -> RunResult:
    busy = RunResult(None, False, error=f"A collection of '{source.id}' is already running")
    lock = _lock_for(source.id)
    if not lock.acquire(blocking=False):
        return busy
    file_lock = FileLock(lock_dir / f"collect-{source.id}.lock") if lock_dir else None
    try:
        if file_lock and not file_lock.try_acquire():
            return busy
        storage.ensure_source(conn, source.id, source.base_url)
        if storage.is_stopped(conn, source.id):
            return RunResult(None, False, error=f"Source '{source.id}' is stopped after refused access", fatal=True)
        storage.mark_interrupted(conn, source.id)
        run_id = storage.start_run(conn, source.id)
        try:
            with make_client(source, transport) as client:
                items = fetch_snapshot(client, source)  # no DB transaction is open here
        except CollectionError as exc:
            storage.record_failure(conn, run_id, source.id, exc.reason, exc.fatal, recipients, source.title)
            return RunResult(run_id, False, error=exc.reason, fatal=exc.fatal)
        except Exception as exc:  # unexpected bug: record it, never report an empty success
            reason = f"Unexpected error: {type(exc).__name__}: {exc}"
            storage.record_failure(conn, run_id, source.id, reason, False, recipients, source.title)
            return RunResult(run_id, False, error=reason)
        events = storage.commit_snapshot(conn, run_id, source.id, items, recipients, source.title)
        return RunResult(run_id, True, product_count=len(items), events=tuple(events))
    finally:
        if file_lock:
            file_lock.release()
        lock.release()
