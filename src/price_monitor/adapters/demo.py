from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urldefrag, urljoin

import httpx
from bs4 import BeautifulSoup

from ..models import Availability, CollectionError, ProductObservation, SourceConfig
from .paging import collect_pages

STOCK_TEXT = {"in stock": Availability.IN_STOCK, "out of stock": Availability.OUT_OF_STOCK}


def parse_page(html: str, page_url: str) -> tuple[int, list[ProductObservation], str | None]:
    """Return (declared total, products on page, next page URL). Raise on any unexpected structure."""
    soup = BeautifulSoup(html, "html.parser")
    total_el = soup.select_one("#total[data-count]")
    if total_el is None:
        raise CollectionError(f"Page template changed: no declared product count at {page_url}")
    try:
        declared = int(total_el["data-count"])
    except ValueError:
        raise CollectionError(f"Declared product count is not a number at {page_url}")

    items: list[ProductObservation] = []
    for card in soup.select("article.product"):
        sku = card.get("data-sku")
        name_el = card.select_one("h2 a")
        price_el = card.select_one(".price")
        if not sku or name_el is None or price_el is None:
            raise CollectionError(f"Product card is missing sku, name or price at {page_url}")
        try:
            price = Decimal(price_el.get_text(strip=True))
        except InvalidOperation:
            raise CollectionError(f"Unreadable price for product {sku} at {page_url}")
        if price < 0:
            raise CollectionError(f"Negative price for product {sku} at {page_url}")
        stock_el = card.select_one(".stock")
        stock_text = stock_el.get_text(strip=True).lower() if stock_el else ""
        items.append(
            ProductObservation(
                product_key=sku,
                name=name_el.get_text(strip=True),
                url=urldefrag(urljoin(page_url, name_el["href"]))[0],
                price=price,
                currency=price_el.get("data-currency", ""),
                availability=STOCK_TEXT.get(stock_text, Availability.UNKNOWN),
            )
        )
    if not items and declared > 0:
        raise CollectionError(f"No product cards found at {page_url} although {declared} are declared")

    next_el = soup.select_one('a[rel="next"][href]')
    next_url = urljoin(page_url, next_el["href"]) if next_el else None
    return declared, items, next_url


def fetch_snapshot(client: httpx.Client, source: SourceConfig) -> list[ProductObservation]:
    return collect_pages(client, source, urljoin(source.base_url, "/catalog?page=1"), parse_page)
