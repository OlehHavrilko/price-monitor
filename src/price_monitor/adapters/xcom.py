"""Adapter for one category of xcom.ua, a Ukrainian online shop: car FM transmitters (/catalog/avto_fm_transmitter/, about 50 items on 2 pages).

Why this shop: its pages are server-rendered HTML with schema.org markup (itemprop name, price,
priceCurrency, availability), and it answers requests from a datacenter address, which the larger
shops tried first (allo.ua, brain.com.ua, rozetka, foxtrot) refused with HTTP 403. Its robots.txt asks
generic crawlers for "Crawl-delay: 10", so the source config uses a 10 second pause between pages.

product_key is the numeric item id of the "add to cart" form (the same number as in the product URL),
kept as text.

Availability is the shop's own text next to the price:
  "Есть на складе" -> IN_STOCK, "Нет в наличии" -> OUT_OF_STOCK, anything else -> UNKNOWN.

The page gives no product total, so completeness is checked by structure: the pager must lead to its
last page and every page except the last must be as full as the first.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urldefrag, urljoin

import httpx
from bs4 import BeautifulSoup

from ..models import Availability, CollectionError, ProductObservation, SourceConfig
from .paging import collect_pages

CATEGORY_PATH = "/catalog/avto_fm_transmitter/"
STOCK_TEXT = {"есть на складе": Availability.IN_STOCK, "нет в наличии": Availability.OUT_OF_STOCK}


def _price(text: str, page_url: str) -> Decimal | None:
    digits = re.sub(r"[^\d.,]", "", text).replace(",", ".")
    if not digits:
        return None
    try:
        value = Decimal(digits)
    except InvalidOperation:
        raise CollectionError(f"Unreadable price '{text}' at {page_url}")
    if value < 0:
        raise CollectionError(f"Negative price '{text}' at {page_url}")
    return value


def parse_page(html: str, page_url: str) -> tuple[list[ProductObservation], int, str | None]:
    """Return (products, current page, next page URL)."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".itemCells .item")
    if not cards:
        raise CollectionError(f"No product cards at {page_url}: the page layout may have changed")

    items: list[ProductObservation] = []
    for card in cards:
        id_el = card.select_one("input[name=item][value]")
        link = card.select_one('.title a[href^="/product/"]')
        price_el = card.select_one('[itemprop="price"]')
        currency_el = card.select_one('meta[itemprop="priceCurrency"]')
        stock_el = card.select_one('[itemprop="availability"]')
        if None in (id_el, link, price_el, currency_el, stock_el) or not id_el["value"].strip():
            raise CollectionError(f"Product card is missing id, title, price or availability at {page_url}")
        if currency_el.get("content") != "UAH":
            raise CollectionError(f"Unexpected currency '{currency_el.get('content')}' at {page_url}")
        items.append(
            ProductObservation(
                product_key=id_el["value"].strip(),
                name=" ".join(link.get_text().split()),
                url=urldefrag(urljoin(page_url, link["href"]))[0],
                price=_price(price_el.get_text(), page_url),
                currency="UAH",
                availability=STOCK_TEXT.get(" ".join(stock_el.get_text().split()).lower(), Availability.UNKNOWN),
            )
        )

    current_el = soup.select_one(".pages .currentpage")
    if current_el is None:
        if soup.select_one(".pages") is not None:
            raise CollectionError(f"Pager without a current page at {page_url}")
        return items, 1, None  # a category that fits one page has no pager
    current = int(current_el.get_text(strip=True))
    links = {
        int(a.get_text(strip=True)): a["href"]
        for a in soup.select(".pages a[href]")
        if a.get_text(strip=True).isdigit()
    }
    next_url = urljoin(page_url, links[current + 1]) if current + 1 in links else None
    return items, current, next_url


def fetch_snapshot(client: httpx.Client, source: SourceConfig) -> list[ProductObservation]:
    state: dict[str, int] = {}

    def parser(html: str, page_url: str):
        items, current, next_url = parse_page(html, page_url)
        first = state.setdefault("page_size", len(items))
        if next_url is not None and len(items) != first:
            raise CollectionError(f"Page {current} has {len(items)} products, the first page had {first}")
        if next_url is None and len(items) > first:
            raise CollectionError(f"The last page has more products than the first ({len(items)} > {first})")
        return None, items, next_url  # None: the shop gives no total to compare with

    return collect_pages(client, source, urljoin(source.base_url, CATEGORY_PATH), parser)
