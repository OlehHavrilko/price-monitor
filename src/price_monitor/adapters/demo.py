from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..http import get_page
from ..models import Availability, CollectionError, ProductObservation, SourceConfig

STOCK_TEXT = {"in stock": Availability.IN_STOCK, "out of stock": Availability.OUT_OF_STOCK}


def _same_origin(base: str, url: str) -> bool:
    a, b = urlparse(base), urlparse(url)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


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
    url: str | None = urljoin(source.base_url, "/catalog?page=1")
    seen_pages: set[str] = set()
    products: dict[str, ProductObservation] = {}
    declared_total: int | None = None

    while url:
        if url in seen_pages:
            raise CollectionError(f"Pagination loop: {url} was already visited")
        if len(seen_pages) >= source.max_pages:
            raise CollectionError(f"Page limit {source.max_pages} reached before the catalog ended")
        if not _same_origin(source.base_url, url):
            raise CollectionError(f"Next page {url} is outside the allowed source")
        if seen_pages:
            time.sleep(source.page_delay)
        seen_pages.add(url)

        declared, items, url = parse_page(get_page(client, source, url), url)
        if declared_total is not None and declared != declared_total:
            raise CollectionError("Declared product count changed between pages")
        declared_total = declared
        for item in items:
            if item.product_key in products:
                raise CollectionError(f"Duplicate product key '{item.product_key}' in one snapshot")
            products[item.product_key] = item

    if declared_total != len(products):
        raise CollectionError(f"Collected {len(products)} products, source declares {declared_total}")
    return list(products.values())
