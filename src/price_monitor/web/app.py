from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from .routes import register_routes
from .views import render

from .. import config
from .auth import authenticated, required_token
from .db import DatabaseUnavailable


def create_app(*, db_path: Path | None = None, token: str | None = None, sources=None):
    secret = required_token(token)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.db_path = db_path if db_path is not None else config.db_path()
    app.state.sources = sources if sources is not None else config.sources()

    @app.middleware("http")
    async def protect(request: Request, call_next):
        public = request.url.path == "/login" or request.url.path.startswith("/static/")
        if not public and not authenticated(request, secret):
            response = HTMLResponse('<html lang="uk"><p>Доступ заборонено. <a href="/login">Увійти</a></p></html>',
                                    status_code=401, headers={"WWW-Authenticate": "Bearer", "HX-Redirect": "/login"})
        else:
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(DatabaseUnavailable)
    async def database_error(request, exc):
        return render(request, "error.html", status_code=503, title="База недоступна", message=str(exc))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        message = exc.detail if exc.status_code != 404 else "Сторінку, джерело або товар не знайдено."
        return render(request, "error.html", status_code=exc.status_code, title=f"Помилка {exc.status_code}", message=message)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return render(request, "error.html", status_code=422, title="Некоректний запит",
                      message="Перевірте параметри: номер сторінки має бути додатним, пошук — до 200 символів.")

    register_routes(app, secret)
    return app
