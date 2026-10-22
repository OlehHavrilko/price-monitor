"""SELECT-only queries behind the dashboard pages. Every value from a request is a bound parameter."""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import comparison, outbox

PAGE_SIZE = 25
# The chart and the table show only points where price or stock changed; a long history is cut to the latest ones.
MAX_HISTORY_POINTS = 2000

# Filters on the events page. Stock events are stored as AVAILABILITY_CHANGED with old/new values;
# the direction is a view on them, the stored events stay as they are.
EVENT_KINDS: dict[str, tuple[str, tuple]] = {
    "PRICE_CHANGED": ("e.type = ?", (comparison.PRICE_CHANGED,)),
    "BELOW_THRESHOLD": ("e.type = ?", (comparison.BELOW_THRESHOLD,)),
    "PRICE_DROP_PERCENT": ("e.type = ?", (comparison.PRICE_DROP_PERCENT,)),
    "OUT_OF_STOCK": ("e.type = ? AND e.new_value = 'OUT_OF_STOCK'", (comparison.AVAILABILITY_CHANGED,)),
    "BACK_IN_STOCK": ("e.type = ? AND e.new_value = 'IN_STOCK'", (comparison.AVAILABILITY_CHANGED,)),
    "REMOVED": ("e.type = ?", (comparison.REMOVED,)),
    "RETURNED": ("e.type = ?", (comparison.RETURNED,)),
}
AVAILABILITY_FILTERS = ("IN_STOCK", "OUT_OF_STOCK", "UNKNOWN")


@dataclass(frozen=True)
class Page:
    rows: list[dict]
    total: int
    page: int
    page_size: int = PAGE_SIZE

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


def event_kind(event_type: str, new_value: str | None) -> str:
    if event_type == comparison.AVAILABILITY_CHANGED:
        if new_value == "OUT_OF_STOCK":
            return "OUT_OF_STOCK"
        if new_value == "IN_STOCK":
            return "BACK_IN_STOCK"
    return event_type


def source_ids(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT id FROM sources ORDER BY id")]


def _last_success_id(conn: sqlite3.Connection, source_id: str) -> int | None:
    row = conn.execute(
        "SELECT MAX(id) FROM collection_runs WHERE source_id = ? AND status = 'success'", (source_id,)
    ).fetchone()
    return row[0]


def sources_overview(conn: sqlite3.Connection) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(timespec="seconds")
    result = []
    for source in conn.execute("SELECT * FROM sources ORDER BY id").fetchall():
        sid = source["id"]
        last_run = conn.execute(
            "SELECT * FROM collection_runs WHERE source_id = ? ORDER BY id DESC LIMIT 1", (sid,)
        ).fetchone()
        last_success = conn.execute(
            "SELECT * FROM collection_runs WHERE source_id = ? AND status = 'success' ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()
        stock = {}
        if last_success:
            stock = {
                r["availability"]: r["n"]
                for r in conn.execute(
                    "SELECT availability, COUNT(*) AS n FROM observations WHERE run_id = ? GROUP BY availability",
                    (last_success["id"],),
                )
            }
        health = conn.execute("SELECT * FROM source_health WHERE source_id = ?", (sid,)).fetchone()
        result.append({
            "id": sid,
            "base_url": source["base_url"],
            "last_run": dict(last_run) if last_run else None,
            "last_success": dict(last_success) if last_success else None,
            "stock": stock,
            "stopped": bool(health and health["stopped"]),
            "alerted": bool(health and health["alerted"]),
            "health_error": health["last_error"] if health else None,
            "tracked": conn.execute("SELECT COUNT(*) FROM tracking_rules WHERE source_id = ?", (sid,)).fetchone()[0],
            "events_day": conn.execute(
                "SELECT COUNT(*) FROM events WHERE source_id = ? AND created_at >= ?", (sid, since)
            ).fetchone()[0],
        })
    return result


def products(
    conn: sqlite3.Connection,
    source_id: str,
    query: str = "",
    availability: str = "",
    tracked_only: bool = False,
    page: int = 1,
) -> Page:
    """Products of the last successful snapshot of a source."""
    run_id = _last_success_id(conn, source_id)
    if run_id is None:
        return Page([], 0, 1)
    where = ["o.run_id = ?"]
    params: list = [run_id]
    if query:
        where.append("(instr(casefold(o.name), ?) > 0 OR instr(casefold(o.product_key), ?) > 0)")
        params += [query.casefold()] * 2
    if availability in AVAILABILITY_FILTERS:
        where.append("o.availability = ?")
        params.append(availability)
    if tracked_only:
        where.append("r.product_key IS NOT NULL")
    base = (
        "FROM observations o LEFT JOIN tracking_rules r "
        "ON r.source_id = o.source_id AND r.product_key = o.product_key WHERE " + " AND ".join(where)
    )
    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    page = min(page, max(1, -(-total // PAGE_SIZE)))
    rows = conn.execute(
        f"SELECT o.*, r.product_key IS NOT NULL AS tracked, r.threshold, r.drop_percent {base} "
        "ORDER BY o.product_key LIMIT ? OFFSET ?",
        [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ).fetchall()
    return Page([dict(r) for r in rows], total, page)


def snapshot_time(conn: sqlite3.Connection, source_id: str) -> str | None:
    row = conn.execute(
        "SELECT finished_at FROM collection_runs WHERE source_id = ? AND status = 'success' ORDER BY id DESC LIMIT 1",
        (source_id,),
    ).fetchone()
    return row[0] if row else None


def product_detail(conn: sqlite3.Connection, source_id: str, product_key: str) -> dict | None:
    product = conn.execute(
        "SELECT * FROM products WHERE source_id = ? AND product_key = ?", (source_id, product_key)
    ).fetchone()
    if product is None:
        return None
    # Keep the first observation and every point where price or stock changed, plus the latest one.
    points: list[dict] = []
    total = 0
    last = None
    for r in conn.execute(
        "SELECT price, currency, availability, observed_at, run_id FROM observations "
        "WHERE source_id = ? AND product_key = ? ORDER BY id",
        (source_id, product_key),
    ):
        total += 1
        last = dict(r)
        if not points or (points[-1]["price"], points[-1]["availability"]) != (r["price"], r["availability"]):
            points.append(last | {"latest": False})
    if last is not None and points[-1]["run_id"] != last["run_id"]:
        points.append(last | {"latest": True})
    truncated = len(points) > MAX_HISTORY_POINTS
    points = points[-MAX_HISTORY_POINTS:]

    last_success = _last_success_id(conn, source_id)
    in_snapshot = last_success is not None and conn.execute(
        "SELECT 1 FROM observations WHERE run_id = ? AND product_key = ?", (last_success, product_key)
    ).fetchone() is not None
    state = conn.execute(
        "SELECT * FROM product_state WHERE source_id = ? AND product_key = ?", (source_id, product_key)
    ).fetchone()
    rule = conn.execute(
        "SELECT * FROM tracking_rules WHERE source_id = ? AND product_key = ?", (source_id, product_key)
    ).fetchone()
    events = conn.execute(
        "SELECT * FROM events WHERE source_id = ? AND product_key = ? ORDER BY id DESC LIMIT 200",
        (source_id, product_key),
    ).fetchall()
    return {
        "product": dict(product),
        "points": points,
        "observations": total,
        "truncated": truncated,
        "latest": last,
        "in_snapshot": in_snapshot,
        "removed": bool(state and state["removed"]),
        "rule": dict(rule) if rule else None,
        "events": [dict(e) | {"kind": event_kind(e["type"], e["new_value"])} for e in events],
    }


def events(
    conn: sqlite3.Connection,
    source_id: str = "",
    kind: str = "",
    query: str = "",
    since: str | None = None,
    until: str | None = None,
    page: int = 1,
) -> Page:
    where = ["1 = 1"]
    params: list = []
    if source_id:
        where.append("e.source_id = ?")
        params.append(source_id)
    if kind in EVENT_KINDS:
        clause, values = EVENT_KINDS[kind]
        where.append(clause)
        params += values
    if query:
        where.append("(instr(casefold(p.name), ?) > 0 OR instr(casefold(e.product_key), ?) > 0)")
        params += [query.casefold()] * 2
    if since:
        where.append("e.created_at >= ?")
        params.append(since)
    if until:
        where.append("e.created_at < ?")
        params.append(until)
    base = (
        "FROM events e LEFT JOIN products p ON p.source_id = e.source_id AND p.product_key = e.product_key "
        "WHERE " + " AND ".join(where)
    )
    total = conn.execute(f"SELECT COUNT(*) {base}", params).fetchone()[0]
    page = min(page, max(1, -(-total // PAGE_SIZE)))
    rows = conn.execute(
        f"SELECT e.*, p.name {base} ORDER BY e.id DESC LIMIT ? OFFSET ?",
        [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ).fetchall()
    return Page([dict(r) | {"kind": event_kind(r["type"], r["new_value"])} for r in rows], total, page)


_BOT_TOKEN = re.compile(r"bot\d+:[\w-]+")


def _public_failure(conn: sqlite3.Connection) -> str | None:
    """The delivery error without the chat id and without any bot token a library put into the message."""
    row = conn.execute(
        "SELECT last_error FROM notification_outbox WHERE status = 'failed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return _BOT_TOKEN.sub("bot<hidden>", row["last_error"] or "") if row else None


def status(conn: sqlite3.Connection) -> dict:
    return {
        "outbox": outbox.counts(conn),
        "last_failure": _public_failure(conn),
        "runs": [dict(r) for r in conn.execute("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 20")],
        "health": [dict(r) for r in conn.execute("SELECT * FROM source_health ORDER BY source_id")],
        "totals": {
            "products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
            "observations": conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
            "runs": conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0],
        },
    }
