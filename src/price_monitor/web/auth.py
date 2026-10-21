"""Shared-token authentication; no credentials in URLs or database writes."""
import os
import secrets

from starlette.requests import Request

COOKIE = "price_monitor_web_token"


def required_token(token: str | None = None) -> str:
    value = os.environ.get("PRICE_MONITOR_WEB_TOKEN", "") if token is None else token
    if not value or not value.strip():
        raise ValueError("Set PRICE_MONITOR_WEB_TOKEN before starting the web dashboard")
    if len(value.encode()) > 2048 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("PRICE_MONITOR_WEB_TOKEN must be printable ASCII without spaces (max 2048 bytes)")
    return value


def matches(candidate: str, expected: str) -> bool:
    return secrets.compare_digest(candidate.encode(), expected.encode())


def authenticated(request: Request, token: str) -> bool:
    header = request.headers.get("authorization")
    if header is not None:
        scheme, _, value = header.partition(" ")
        return scheme.lower() == "bearer" and matches(value, token)
    return matches(request.cookies.get(COOKIE, ""), token)
