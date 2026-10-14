from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell import Cell

MAX_REPORT_ROWS = 50_000

CURRENT_HEADERS = ["Source", "Key", "Name", "URL", "Price", "Currency", "Status", "Observed at (UTC)"]
HISTORY_HEADERS = CURRENT_HEADERS + ["Event", "Old value", "New value"]


class ReportError(Exception):
    pass


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=None)


def _put(ws, row: int, col: int, value) -> Cell:
    cell = ws.cell(row=row, column=col)
    if isinstance(value, str):
        cell.value = value
        # Names and keys come from external pages. Force text so "=...", "+...", "-...", "@..." never run as formulas.
        cell.data_type = "s"
    else:
        cell.value = value
    return cell


def _write_row(ws, row: int, values: list) -> None:
    for col, value in enumerate(values, start=1):
        cell = _put(ws, row, col, value)
        if isinstance(value, Decimal):
            cell.number_format = "0.00"
        elif isinstance(value, datetime):
            cell.number_format = "yyyy-mm-dd hh:mm:ss"


def _current_rows(conn: sqlite3.Connection, source_id: str) -> list[list]:
    rows = conn.execute(
        """
        SELECT o.*, COALESCE(s.removed, 0) AS removed
        FROM observations o
        JOIN (SELECT product_key, MAX(id) AS id FROM observations WHERE source_id = ? GROUP BY product_key) last
          ON last.id = o.id
        LEFT JOIN product_state s ON s.source_id = o.source_id AND s.product_key = o.product_key
        ORDER BY o.product_key
        """,
        (source_id,),
    ).fetchall()
    return [
        [
            r["source_id"], r["product_key"], r["name"], r["url"], Decimal(r["price"]) if r["price"] is not None else None, r["currency"],
            "REMOVED" if r["removed"] else r["availability"], _parse_time(r["observed_at"]),
        ]
        for r in rows
    ]


def _history_rows(conn: sqlite3.Connection, source_id: str, since: str, until: str) -> list[list]:
    rows = conn.execute(
        """
        SELECT e.*, p.name, p.url, o.price, o.currency, o.availability
        FROM events e
        JOIN products p ON p.source_id = e.source_id AND p.product_key = e.product_key
        LEFT JOIN observations o ON o.run_id = e.run_id AND o.product_key = e.product_key
        WHERE e.source_id = ? AND e.created_at >= ? AND e.created_at < ?
        ORDER BY e.id
        """,
        (source_id, since, until),
    ).fetchall()
    return [
        [
            r["source_id"], r["product_key"], r["name"], r["url"],
            Decimal(r["price"]) if r["price"] is not None else None,
            r["currency"], "REMOVED" if r["type"] == "REMOVED" else r["availability"],
            _parse_time(r["created_at"]), r["type"], r["old_value"], r["new_value"],
        ]
        for r in rows
    ]


def write_report(
    conn: sqlite3.Connection, out_dir: Path, source_id: str, since: datetime, until: datetime
) -> Path:
    """Write an XLSX report into `out_dir` under a generated file name. Callers never choose the path."""
    if since.tzinfo is None or until.tzinfo is None:
        raise ReportError("Period boundaries must include a timezone")
    if since >= until:
        raise ReportError("Period start must be earlier than its end")
    current = _current_rows(conn, source_id)
    history = _history_rows(conn, source_id, since.astimezone(timezone.utc).isoformat(timespec="seconds"),
                            until.astimezone(timezone.utc).isoformat(timespec="seconds"))
    if len(current) + len(history) > MAX_REPORT_ROWS:
        raise ReportError(
            f"Report has {len(current) + len(history)} rows, the limit is {MAX_REPORT_ROWS}. Choose a shorter period."
        )

    wb = Workbook()
    ws_current = wb.active
    ws_current.title = "Current"
    ws_history = wb.create_sheet("History")
    for ws, headers, rows in ((ws_current, CURRENT_HEADERS, current), (ws_history, HISTORY_HEADERS, history)):
        _write_row(ws, 1, headers)
        for i, row in enumerate(rows, start=2):
            _write_row(ws, i, row)
        ws.freeze_panes = "A2"

    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"report-{source_id}-{stamp}.xlsx"
    wb.save(path)
    load_workbook(path).close()  # reopen to be sure the file is readable
    return path
