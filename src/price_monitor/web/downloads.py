"""Reuse the XLSX generator; the database stays read-only, files are temporary."""
from datetime import date, datetime, time, timezone
import re
from tempfile import TemporaryDirectory
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import reports
from . import queries
from .db import read_database


def download_report(app, source: str, since: str, until: str):
    try:
        start = datetime.combine(date.fromisoformat(since), time.min, timezone.utc)
        end = datetime.combine(date.fromisoformat(until), time.min, timezone.utc)
    except ValueError:
        raise HTTPException(400, "Дати мають бути у форматі РРРР-ММ-ДД.")
    if start >= end:
        raise HTTPException(400, "Початок періоду має бути раніше за кінець.")
    # The core generator puts source_id in a file name. Only fixed source IDs
    # are accepted, never paths supplied through a URL or malformed database.
    if re.fullmatch(r"[A-Za-z0-9_-]{1,100}", source) is None:
        raise HTTPException(400, "Некоректний ідентифікатор джерела.")
    directory = TemporaryDirectory(prefix="price-monitor-report-")
    try:
        with read_database(app.state.db_path) as conn:
            if queries.source_info(conn, app.state.sources, source) is None:
                raise HTTPException(404, "Джерело не знайдено.")
            path = reports.write_report(conn, Path(directory.name), source, start, end)
        return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            filename=path.name, background=BackgroundTask(directory.cleanup))
    except reports.ReportError:
        directory.cleanup()
        raise HTTPException(400, "Звіт перевищує ліміт 50 000 рядків. Спробуйте коротший період; Current теж входить до ліміту.")
    except Exception:
        directory.cleanup()
        raise
