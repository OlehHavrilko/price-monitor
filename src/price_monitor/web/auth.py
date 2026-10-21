"""Access to the dashboard: one shared token.

A browser signs in once with a form and gets an HttpOnly cookie; scripts send
`Authorization: Bearer <token>`. The token never travels in a URL, so it does not end up in
browser history, proxy logs or the Referer header.
"""
from __future__ import annotations

import hashlib
import hmac

from starlette.requests import Request

COOKIE_NAME = "pm_session"
COOKIE_MAX_AGE = 7 * 24 * 3600
MIN_TOKEN_LENGTH = 16


class TokenError(ValueError):
    pass


def check_token_strength(token: str | None) -> str:
    if not token:
        raise TokenError("PRICE_MONITOR_WEB_TOKEN is not set. The dashboard does not start without a token.")
    if len(token) < MIN_TOKEN_LENGTH:
        raise TokenError(f"PRICE_MONITOR_WEB_TOKEN must be at least {MIN_TOKEN_LENGTH} characters long.")
    return token


def session_value(token: str) -> str:
    """The cookie holds a value derived from the token, not the token itself."""
    return hmac.new(token.encode(), b"price-monitor web session v1", hashlib.sha256).hexdigest()


def token_matches(candidate: str, token: str) -> bool:
    return hmac.compare_digest(candidate.encode(), token.encode())


def is_authorized(request: Request, token: str) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return token_matches(value.strip(), token)
    cookie = request.cookies.get(COOKIE_NAME)
    return cookie is not None and hmac.compare_digest(cookie.encode(), session_value(token).encode())


def safe_next(value: str | None) -> str:
    """Only a local path may follow the sign-in, never another site."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value
