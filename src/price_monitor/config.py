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
    return {"demo": SourceConfig(id="demo", base_url=demo_url, adapter="demo")}


def reports_dir() -> Path:
    return Path(os.environ.get("PRICE_MONITOR_REPORTS", "data/reports"))
