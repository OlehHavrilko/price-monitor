from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class Availability(str, Enum):
    IN_STOCK = "IN_STOCK"
    OUT_OF_STOCK = "OUT_OF_STOCK"
    # Page gives no reliable stock information. Not the same as out of stock.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SourceConfig:
    id: str
    base_url: str
    adapter: str
    max_pages: int = 50
    timeout: float = 10.0
    page_delay: float = 0.5
    retries: int = 2
    retry_backoff: float = 1.0
    interval: int = 3600  # seconds between scheduled collections
    title: str = ""  # shown to the user in Telegram
    description: str = ""


@dataclass(frozen=True)
class ProductObservation:
    # Key is a string on purpose: "00123" and "123" are different products.
    product_key: str
    name: str
    url: str
    price: Decimal
    currency: str
    availability: Availability


class CollectionError(Exception):
    """A source could not be read completely. The previous snapshot stays valid."""

    def __init__(self, reason: str, *, fatal: bool = False):
        super().__init__(reason)
        self.reason = reason
        # fatal: access was refused (401/403); the source must be stopped, not retried.
        self.fatal = fatal
