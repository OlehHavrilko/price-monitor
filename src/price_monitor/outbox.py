"""Persistent notification queue.

Rows are written in the same transaction as the events that caused them. Delivery happens later and
is at-least-once: if a send times out, Telegram may still have delivered it, and the retry then
produces a duplicate. Exactly-once delivery is not promised.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

MAX_ATTEMPTS = 6
BASE_DELAY_SECONDS = 30
MAX_DELAY_SECONDS = 3600


class PermanentDeliveryError(Exception):
    """The address will never accept the message (blocked bot, unknown chat). Do not retry."""


class TemporaryDeliveryError(Exception):
    """Network error, timeout or server error. The message may or may not have arrived."""


# send(chat_id, text, card): card is the data for an alert picture, or None for a plain text message.
Sender = Callable[[int, str, "dict | None"], None]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def enqueue(
    conn: sqlite3.Connection, recipients: Sequence[int], dedupe_key: str, text: str, card: dict | None = None
) -> None:
    """Add one message per recipient. A repeated dedupe_key for the same chat is ignored."""
    now = _iso(_now())
    for chat_id in recipients:
        conn.execute(
            "INSERT OR IGNORE INTO notification_outbox(chat_id, dedupe_key, text, card, status, attempts, "
            "next_attempt_at, created_at) VALUES (?, ?, ?, ?, 'pending', 0, ?, ?)",
            (chat_id, dedupe_key, text, json.dumps(card) if card else None, now, now),
        )


@dataclass(frozen=True)
class DeliveryReport:
    sent: int = 0
    retry_later: int = 0
    failed: int = 0


def deliver_pending(
    conn: sqlite3.Connection, send: Sender, now: datetime | None = None, limit: int = 50
) -> DeliveryReport:
    now = now or _now()
    rows = conn.execute(
        "SELECT * FROM notification_outbox WHERE status = 'pending' AND next_attempt_at <= ? ORDER BY id LIMIT ?",
        (_iso(now), limit),
    ).fetchall()
    sent = retry = failed = 0
    for row in rows:
        try:
            send(row["chat_id"], row["text"], json.loads(row["card"]) if row["card"] else None)
        except PermanentDeliveryError as exc:
            _mark_failed(conn, row["id"], row["attempts"] + 1, str(exc))
            failed += 1
        except TemporaryDeliveryError as exc:
            attempts = row["attempts"] + 1
            if attempts >= MAX_ATTEMPTS:
                _mark_failed(conn, row["id"], attempts, f"gave up after {attempts} attempts: {exc}")
                failed += 1
            else:
                delay = min(BASE_DELAY_SECONDS * 2 ** (attempts - 1), MAX_DELAY_SECONDS)
                conn.execute(
                    "UPDATE notification_outbox SET attempts = ?, last_error = ?, next_attempt_at = ? WHERE id = ?",
                    (attempts, str(exc), _iso(now + timedelta(seconds=delay)), row["id"]),
                )
                retry += 1
        else:
            conn.execute(
                "UPDATE notification_outbox SET status = 'sent', attempts = ?, sent_at = ?, last_error = NULL WHERE id = ?",
                (row["attempts"] + 1, _iso(now), row["id"]),
            )
            sent += 1
    return DeliveryReport(sent, retry, failed)


def _mark_failed(conn: sqlite3.Connection, row_id: int, attempts: int, error: str) -> None:
    conn.execute(
        "UPDATE notification_outbox SET status = 'failed', attempts = ?, last_error = ? WHERE id = ?",
        (attempts, error, row_id),
    )


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    result = {"pending": 0, "sent": 0, "failed": 0}
    for r in conn.execute("SELECT status, COUNT(*) AS n FROM notification_outbox GROUP BY status"):
        result[r["status"]] = r["n"]
    return result


def last_failure(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT chat_id, last_error FROM notification_outbox WHERE status = 'failed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return f"chat {row['chat_id']}: {row['last_error']}" if row else None
