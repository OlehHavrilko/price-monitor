from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

from fastapi import HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import queries
from .auth import COOKIE, matches
from .db import read_database
from .downloads import download_report
from .views import ROOT, pagination, render


def register_routes(app, token):
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    def database():
        return read_database(app.state.db_path)

    def require_source(conn, source):
        result = queries.source_info(conn, app.state.sources, source)
        if result is None:
            raise HTTPException(404, "Джерело не знайдено.")
        return result

    def same_origin(request):
        origin = request.headers.get("origin")
        if (origin and origin != str(request.base_url).rstrip("/")) or request.headers.get("sec-fetch-site") == "cross-site":
            raise HTTPException(403, "Запит з іншого сайту заборонено.")

    @app.get("/login")
    def login(request: Request):
        return render(request, "login.html", title="Вхід")

    @app.post("/login")
    async def authenticate(request: Request):
        same_origin(request)
        if request.headers.get("content-type", "").split(";")[0] != "application/x-www-form-urlencoded":
            raise HTTPException(400, "Неправильний формат форми.")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > 8192:
                raise HTTPException(400, "Форма завелика.")
        try:
            value = parse_qs(body.decode(), max_num_fields=4).get("token", [""])[0]
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Неправильний формат форми.")
        if not matches(value, token):
            return render(request, "login.html", status_code=401, title="Вхід", error="Невірний токен.")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, httponly=True, secure=request.url.scheme == "https",
                            samesite="strict", max_age=8 * 3600, path="/")
        return response

    @app.post("/logout")
    def logout(request: Request):
        same_origin(request)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/")
    def index(request: Request):
        with database() as conn:
            sources = queries.source_rows(conn, app.state.sources)
        return render(request, "sources.html", title="Магазини", sources=sources)

    @app.get("/sources/{source}")
    def source_page(request: Request, source: str, q: str = Query("", max_length=200),
                    availability: str = "", page: int = Query(1, ge=1, le=1000000)):
        if availability and availability not in queries.AVAILABILITIES:
            raise HTTPException(400, "Невідомий стан наявності.")
        with database() as conn:
            info = require_source(conn, source)
            products, total = queries.products(conn, source, q.strip(), availability, page)
        partial = request.headers.get("hx-request") == "true"
        return render(request, "partials/products.html" if partial else "products.html", title=info["title"],
                      source=info, products=products, q=q, availability=availability,
                      pager=pagination(request, total, page, queries.PAGE_SIZE))

    @app.get("/products/{source}/{key:path}")
    def product_page(request: Request, source: str, key: str, page: int = Query(1, ge=1, le=1000000)):
        with database() as conn:
            info = require_source(conn, source)
            product = queries.product(conn, source, key)
            if product is None:
                raise HTTPException(404, "Товар не знайдено.")
            events, total = queries.events(conn, source, key=key, page=page)
        return render(request, "product.html", title=product["name"], source=info, product=product,
                      events=events, pager=pagination(request, total, page, queries.PAGE_SIZE))

    @app.get("/api/history/{source}/{key:path}")
    def price_history(source: str, key: str):
        with database() as conn:
            require_source(conn, source)
            if queries.product(conn, source, key) is None:
                raise HTTPException(404, "Товар не знайдено.")
            return {"source": source, "key": key, "observations": queries.history(conn, source, key)}

    @app.get("/events")
    def event_feed(request: Request, source: str = "", kind: str = "", page: int = Query(1, ge=1, le=1000000)):
        if kind and kind not in queries.EVENT_TYPES:
            raise HTTPException(400, "Невідомий тип події.")
        with database() as conn:
            sources = queries.source_rows(conn, app.state.sources)
            if source:
                require_source(conn, source)
            events, total = queries.events(conn, source, kind, page=page)
        partial = request.headers.get("hx-request") == "true"
        return render(request, "partials/events.html" if partial else "events.html", title="Події",
                      sources=sources, selected_source=source, kind=kind, events=events,
                      pager=pagination(request, total, page, queries.PAGE_SIZE))

    @app.get("/report")
    def report_form(request: Request):
        with database() as conn:
            sources = queries.source_rows(conn, app.state.sources)
        today = datetime.now(timezone.utc).date()
        return render(request, "report.html", title="XLSX-звіт", sources=sources,
                      since=(today - timedelta(days=7)).isoformat(), until=(today + timedelta(days=1)).isoformat())

    @app.get("/report/download")
    def report_download(source: str, since: str, until: str):
        return download_report(app, source, since, until)

    @app.get("/status")
    def status(request: Request):
        with database() as conn:
            sources = queries.source_rows(conn, app.state.sources)
            delivery = queries.delivery_status(conn)
        return render(request, "status.html", title="Стан сервісу", sources=sources, **delivery)
