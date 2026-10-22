"""Read-only web dashboard over the monitor's database.

Pages: sources, products of the last snapshot, the price history of one product, events, an XLSX
report and the service status. Nothing here writes to the database: tracking rules are still
changed in Telegram, collections still run in the monitor.
"""
from __future__ import annotations

import asyncio
import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException

from .. import reports
from ..models import SourceConfig
from . import auth, queries
from .db import DatabaseUnavailable, open_readonly, schema_version

HERE = Path(__file__).parent
DEFAULT_REPORT_DAYS = 7
MAX_LOGIN_BODY = 4096
PUBLIC_PATHS = {"/login", "/healthz"}

AVAILABILITY_LABELS = {
    "IN_STOCK": "В наявності",
    "OUT_OF_STOCK": "Немає в наявності",
    "UNKNOWN": "Невідомо",
    "REMOVED": "Зник з каталогу",
}
EVENT_LABELS = {
    "PRICE_CHANGED": "Зміна ціни",
    "BELOW_THRESHOLD": "Ціна нижче порогу",
    "PRICE_DROP_PERCENT": "Ціна впала на відсоток",
    "OUT_OF_STOCK": "Закінчився",
    "BACK_IN_STOCK": "Знову в наявності",
    "AVAILABILITY_CHANGED": "Зміна наявності",
    "REMOVED": "Зник з каталогу",
    "RETURNED": "Повернувся в каталог",
}
RUN_LABELS = {"success": "успішно", "failed": "помилка", "running": "триває", "interrupted": "перервано"}

SECURITY_HEADERS = {
    # Scripts and styles come only from /static; the chart data is a JSON block, not inline code.
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def _utc(value: str | None) -> str:
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return value
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _event_value(value: str | None) -> str:
    if value is None:
        return "—"
    return AVAILABILITY_LABELS.get(value, value)


def _shop_href(url: str | None) -> str | None:
    """Product URLs come from shop pages: link only plain http(s), never javascript: or data:."""
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return url if parts.scheme in ("http", "https") and parts.netloc else None


def _link(path: str, **params) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "", False)}
    return f"{path}?{urlencode(clean)}" if clean else path


def _page(value: str | None) -> int:
    try:
        return min(max(int(value or 1), 1), 100_000)
    except ValueError:
        return 1


def _parse_day(value: str | None, field: str, errors: list[str]) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        errors.append(f"«{field}»: дата має бути у форматі РРРР-ММ-ДД.")
        return None


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def create_app(
    db_path: Path,
    token: str,
    sources: dict[str, SourceConfig] | None = None,
    secure_cookie: bool = False,
) -> FastAPI:
    auth.check_token_strength(token)
    titles = {sid: cfg.title or sid for sid, cfg in (sources or {}).items()}
    # A source that is switched off in the configuration (its old rows stay in the database) is not shown.
    shown = set(titles) if sources else None

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, title="Price Monitor")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    env = templates.env
    env.filters.update(utc=_utc, event_value=_event_value, shop_href=_shop_href)
    env.globals.update(
        link=_link,
        title_of=lambda sid: titles.get(sid, sid),
        availability_labels=AVAILABILITY_LABELS,
        event_labels=EVENT_LABELS,
        run_labels=RUN_LABELS,
    )

    def render(request: Request, name: str, context: dict, status_code: int = 200) -> HTMLResponse:
        return templates.TemplateResponse(request, name, context, status_code=status_code)

    def connection() -> Iterator[sqlite3.Connection]:
        conn = open_readonly(db_path)
        try:
            yield conn
        finally:
            conn.close()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path not in PUBLIC_PATHS and not path.startswith("/static/") and not auth.is_authorized(request, token):
            if request.headers.get("hx-request"):
                response = Response(status_code=401, headers={"HX-Redirect": "/login"})
            elif "text/html" in request.headers.get("accept", "") and "authorization" not in request.headers:
                target = path + (f"?{request.url.query}" if request.url.query else "")
                response = RedirectResponse(_link("/login", next=target), status_code=303)
            else:
                response = PlainTextResponse("Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"})
        else:
            response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(DatabaseUnavailable)
    async def unavailable(request: Request, exc: DatabaseUnavailable):
        return render(request, "message.html", {"heading": "Даних поки немає", "text": str(exc)}, 503)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code == 404:
            text = exc.detail if exc.detail and exc.detail != "Not Found" else "Такої сторінки немає."
            return render(request, "message.html", {"heading": "Не знайдено", "text": text}, 404)
        return PlainTextResponse(str(exc.detail), status_code=exc.status_code, headers=exc.headers)

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/login")
    def login_form(request: Request, next: str = "/"):
        return render(request, "login.html", {"next": auth.safe_next(next), "error": None})

    @app.post("/login")
    async def login(request: Request):
        body = await request.body()
        if len(body) > MAX_LOGIN_BODY:
            return PlainTextResponse("Request too large", status_code=413)
        form = parse_qs(body.decode("utf-8", "replace"))
        candidate = (form.get("token") or [""])[0]
        target = auth.safe_next((form.get("next") or ["/"])[0])
        if not auth.token_matches(candidate, token):
            await asyncio.sleep(0.5)  # slows down guessing; the token itself is long
            return render(request, "login.html", {"next": target, "error": "Невірний токен."}, 401)
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(
            auth.COOKIE_NAME, auth.session_value(token), max_age=auth.COOKIE_MAX_AGE,
            httponly=True, samesite="strict", secure=secure_cookie, path="/",
        )
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(auth.COOKIE_NAME, path="/")
        return response

    @app.get("/")
    def sources_page(request: Request, conn: sqlite3.Connection = Depends(connection)):
        return render(request, "sources.html", {"active": "sources", "sources": queries.sources_overview(conn, shown)})

    def _pick_source(conn: sqlite3.Connection, requested: str | None) -> tuple[str | None, list[str]]:
        known = queries.source_ids(conn, shown)
        if requested:
            if requested not in known:
                raise HTTPException(404, f"Джерела «{requested}» немає в базі.")
            return requested, known
        preferred = [sid for sid in titles if sid in known]
        return (preferred or known or [None])[0], known

    @app.get("/products")
    def products_page(
        request: Request,
        source: str | None = None,
        q: str = "",
        availability: str = "",
        tracked: str = "",
        page: str | None = None,
        conn: sqlite3.Connection = Depends(connection),
    ):
        source_id, known = _pick_source(conn, source)
        q = q.strip()[:200]
        result = queries.products(conn, source_id, q, availability, tracked == "1", _page(page)) if source_id else None
        return render(request, "products.html", {
            "active": "products",
            "source": source_id,
            "sources": known,
            "q": q,
            "availability": availability if availability in queries.AVAILABILITY_FILTERS else "",
            "tracked": tracked == "1",
            "result": result,
            "snapshot_at": queries.snapshot_time(conn, source_id) if source_id else None,
        })

    @app.get("/product")
    def product_page(request: Request, source: str, key: str, conn: sqlite3.Connection = Depends(connection)):
        detail = queries.product_detail(conn, source, key)
        if detail is None:
            raise HTTPException(404, f"Товару «{key}» у джерелі «{source}» немає в базі.")
        chart = [
            {"t": p["observed_at"], "price": p["price"], "availability": p["availability"]} for p in detail["points"]
        ]
        return render(request, "product.html", {
            "active": "products", "source": source, "d": detail, "chart": chart,
        })

    @app.get("/events")
    def events_page(
        request: Request,
        source: str = "",
        kind: str = "",
        q: str = "",
        since: str = "",
        until: str = "",
        page: str | None = None,
        conn: sqlite3.Connection = Depends(connection),
    ):
        errors: list[str] = []
        known = queries.source_ids(conn, shown)
        if source and source not in known:
            raise HTTPException(404, f"Джерела «{source}» немає в базі.")
        start = _parse_day(since, "з", errors)
        end = _parse_day(until, "по", errors)
        q = q.strip()[:200]
        result = queries.events(
            conn, source, kind, q,
            _day_start(start).isoformat() if start else None,
            # "по" is inclusive on the page: events of that whole day are shown.
            _day_start(end + timedelta(days=1)).isoformat() if end else None,
            _page(page),
        )
        return render(request, "events.html", {
            "active": "events", "sources": known, "source": source,
            "kind": kind if kind in queries.EVENT_KINDS else "", "kinds": list(queries.EVENT_KINDS),
            "q": q, "since": since if start else "", "until": until if end else "",
            "errors": errors, "result": result,
        })

    def _report_period(since: str, until: str, errors: list[str]) -> tuple[date, date]:
        today = datetime.now(timezone.utc).date()
        start = _parse_day(since, "з", errors) if since else today - timedelta(days=DEFAULT_REPORT_DAYS - 1)
        end = _parse_day(until, "по", errors) if until else today
        if start and end and start > end:
            errors.append("Початок періоду має бути не пізніше за його кінець.")
        return start or today, end or today

    @app.get("/report")
    def report_page(
        request: Request, source: str | None = None, since: str = "", until: str = "",
        conn: sqlite3.Connection = Depends(connection),
    ):
        source_id, known = _pick_source(conn, source)
        errors: list[str] = []
        start, end = _report_period(since, until, errors)
        return render(request, "report.html", {
            "active": "report", "sources": known, "source": source_id,
            "since": start.isoformat(), "until": end.isoformat(), "errors": errors,
            "max_rows": reports.MAX_REPORT_ROWS,
        })

    @app.get("/report.xlsx")
    def report_file(
        request: Request, source: str, since: str = "", until: str = "",
        conn: sqlite3.Connection = Depends(connection),
    ):
        source_id, known = _pick_source(conn, source)
        errors: list[str] = []
        start, end = _report_period(since, until, errors)
        if not errors:
            out_dir = Path(tempfile.mkdtemp(prefix="price-monitor-report-"))
            try:
                # The report's end is exclusive; the page asks for the last day included.
                path = reports.write_report(conn, out_dir, source_id, _day_start(start), _day_start(end + timedelta(days=1)))
            except reports.ReportError as exc:
                shutil.rmtree(out_dir, ignore_errors=True)
                errors.append(f"Звіт не створено: {exc}")
            else:
                return FileResponse(
                    path, filename=path.name,
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    background=BackgroundTask(shutil.rmtree, out_dir, ignore_errors=True),
                )
        return render(request, "report.html", {
            "active": "report", "sources": known, "source": source_id,
            "since": start.isoformat(), "until": end.isoformat(), "errors": errors,
            "max_rows": reports.MAX_REPORT_ROWS,
        }, 400)

    @app.get("/status")
    def status_page(request: Request, conn: sqlite3.Connection = Depends(connection)):
        return render(request, "status.html", {
            "active": "status", "s": queries.status(conn), "schema": schema_version(conn),
            "db_path": str(db_path),
        })

    return app
