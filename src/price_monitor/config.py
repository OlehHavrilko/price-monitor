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
    return {"demo": SourceConfig(id="demo", base_url=demo_url, adapter="demo", interval=interval)}


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


def lock_dir() -> Path:
    return db_path().parent
