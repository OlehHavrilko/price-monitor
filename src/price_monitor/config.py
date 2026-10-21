from __future__ import annotations

import os
from pathlib import Path

from .models import SourceConfig


def db_path() -> Path:
    return Path(os.environ.get("PRICE_MONITOR_DB", "data/price_monitor.db"))


def demo_state_file() -> Path:
    return Path(os.environ.get("PRICE_MONITOR_DEMO_STATE", "data/demo-state.json"))


def sources() -> dict[str, SourceConfig]:
    """Fixed source list. Users cannot add URLs at runtime; the demo catalog is the default source."""
    demo_url = os.environ.get("PRICE_MONITOR_DEMO_URL", "http://127.0.0.1:8080")
    interval = int(os.environ.get("PRICE_MONITOR_INTERVAL_SECONDS", "3600"))
    result = {"demo": SourceConfig(id="demo", base_url=demo_url, adapter="demo", interval=interval,
        title="Demo Shop",
        description="вигаданий магазин-стенд: 12 канцелярських і офісних товарів, ціни та наявність змінюються за сценарієм",
    )}
    # The external practice site is off by default and has to be switched on explicitly.
    if os.environ.get("PRICE_MONITOR_ENABLE_BOOKS") == "1":
        result["books"] = SourceConfig(
            id="books", base_url="https://books.toscrape.com", adapter="books", max_pages=60, interval=max(interval, 3600),
            title="Books to Scrape",
            description="публічний навчальний сайт для парсингу, 1000 книжок (ціни на ньому не змінюються)",
        )
    if os.environ.get("PRICE_MONITOR_ENABLE_ALLO") == "1":
        # A real shop: gentle settings. 3 pages of ~2 MB each, one collection every 6 hours.
        result["allo"] = SourceConfig(
            id="allo", base_url="https://allo.ua", adapter="allo", max_pages=6, page_delay=3.0,
            interval=max(interval, 6 * 3600), title="allo.ua — фітнес-браслети",
            description="реальний інтернет-магазин електроніки, категорія «Фітнес-браслети» (близько 160 товарів)",
        )
    if os.environ.get("PRICE_MONITOR_ENABLE_XCOM") == "1":
        # A real shop: gentle settings. 2 light pages, 10 s apart (its robots.txt asks for Crawl-delay 10).
        result["xcom"] = SourceConfig(
            id="xcom", base_url="https://xcom.ua", adapter="xcom", max_pages=5, page_delay=10.0,
            interval=max(interval, 6 * 3600), title="xcom.ua — FM-трансмітери",
            description="реальний інтернет-магазин, категорія «FM-трансмітери» (близько 50 товарів)",
        )
    return result


def reports_dir() -> Path:
    return Path(os.environ.get("PRICE_MONITOR_REPORTS", "data/reports"))


def telegram_token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or None


def allowed_ids() -> tuple[int, ...]:
    raw = os.environ.get("TELEGRAM_ALLOWED_IDS", "")
    try:
        return tuple(int(part) for part in raw.split(",") if part.strip())
    except ValueError:
        raise ValueError("TELEGRAM_ALLOWED_IDS must be a comma-separated list of numeric Telegram user IDs")


def telegram_enabled() -> bool:
    """Telegram runs only when both a token and at least one allowed ID are set."""
    return telegram_token() is not None and bool(allowed_ids())


def web_token() -> str | None:
    return os.environ.get("PRICE_MONITOR_WEB_TOKEN") or None


def web_secure_cookie() -> bool:
    """Set to 1 when the dashboard is served over HTTPS (behind a TLS proxy)."""
    return os.environ.get("PRICE_MONITOR_WEB_SECURE_COOKIE") == "1"


def lock_dir() -> Path:
    return db_path().parent
