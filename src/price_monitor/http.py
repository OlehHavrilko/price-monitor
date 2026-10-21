from __future__ import annotations

import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from .models import CollectionError, SourceConfig


def make_client(source: SourceConfig, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    # Redirects are not followed: a redirect could lead outside the allowed source.
    return httpx.Client(
        timeout=source.timeout,
        follow_redirects=False,
        transport=transport,
        headers={"User-Agent": "price-monitor-portfolio/0.1 (+https://github.com/OlehHavrilko/price-monitor)"},
    )


# Longest Retry-After a collection waits for. A shop asking for more is not hammered: the run fails
# and the next scheduled collection tries again. Waiting longer would hold the source lock for that time.
MAX_RETRY_AFTER_SECONDS = 120.0


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Parse Retry-After: delay in seconds or an HTTP date. None if absent or unreadable."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0.0, (moment - (now or datetime.now(timezone.utc))).total_seconds())


def get_page(client: httpx.Client, source: SourceConfig, url: str) -> str:
    """GET one page. Retries timeouts, connection errors, 5xx and 429; stops on 401/403 and other 4xx.

    A Retry-After on 429 or 503 replaces the backoff delay before the next attempt.
    """
    attempts = source.retries + 1
    last = "no attempt made"
    wait: float | None = None
    for attempt in range(attempts):
        if attempt:
            time.sleep(wait if wait is not None else source.retry_backoff * 2 ** (attempt - 1))
        wait = None
        try:
            resp = client.get(url)
        except httpx.TimeoutException:
            last = f"timeout fetching {url}"
            continue
        except httpx.TransportError as exc:
            last = f"network error fetching {url}: {type(exc).__name__}"
            continue
        code = resp.status_code
        if code == 200:
            return resp.text
        if code in (401, 403):
            raise CollectionError(f"Access refused (HTTP {code}) at {url}; source stopped", fatal=True)
        if 300 <= code < 400:
            raise CollectionError(f"Redirect (HTTP {code}) at {url} is not followed")
        if 400 <= code < 500 and code != 429:
            raise CollectionError(f"HTTP {code} at {url}")
        last = f"HTTP {code} at {url}"
        if code in (429, 503):
            wait = retry_after_seconds(resp.headers.get("retry-after"))
            if wait is not None and wait > MAX_RETRY_AFTER_SECONDS:
                raise CollectionError(
                    f"HTTP {code} at {url}: the shop asks to wait {wait:.0f} s (Retry-After); "
                    "the next scheduled collection will try again"
                )
    raise CollectionError(f"{last} (after {attempts} attempts)")
