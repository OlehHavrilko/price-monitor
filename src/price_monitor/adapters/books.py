"""Adapter for books.toscrape.com, a public site made for practising scraping.

Prices and stock there are fixed random values, so it shows parsing and pagination only.
product_key is the book's directory in the URL ("a-light-in-the-attic_1000"): the site has no
separate SKU on the listing, and the trailing number is part of that directory name.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ..models import Availability, CollectionError, ProductObservation, SourceConfig
from .paging import collect_pages

CURRENCIES = {"£": "GBP"}


def _key_from_url(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    # .../catalogue/<key>/index.html
    if len(parts) < 2 or parts[-1] != "index.html":
        raise CollectionError(f"Unexpected product link: {url}")
    return parts[-2]


def parse_page(html: str, page_url: str) -> tuple[int, list[ProductObservation], str | None]:
    soup = BeautifulSoup(html, "html.parser")
    total_el = soup.select_one("form.form-horizontal strong")
    try:
        declared = int(total_el.get_text(strip=True))
    except (AttributeError, ValueError):
        raise CollectionError(f"Page template changed: no readable result count at {page_url}")

    items: list[ProductObservation] = []
    for card in soup.select("article.product_pod"):
        link = card.select_one("h3 a[href][title]")
        price_el = card.select_one("p.price_color")
        stock_el = card.select_one("p.availability")
        if link is None or price_el is None or stock_el is None:
            raise CollectionError(f"Product card is missing title, price or availability at {page_url}")
        price_text = price_el.get_text(strip=True)
        currency = CURRENCIES.get(price_text[:1])
        try:
            price = Decimal(price_text[1:])
        except InvalidOperation:
            price = None
        if currency is None or price is None or price < 0:
            raise CollectionError(f"Unreadable price '{price_text}' at {page_url}")
        url = urldefrag(urljoin(page_url, link["href"]))[0]
        stock_text = " ".join(stock_el.get_text().split()).lower()
        availability = {"in stock": Availability.IN_STOCK, "out of stock": Availability.OUT_OF_STOCK}.get(
            stock_text, Availability.UNKNOWN
        )
        items.append(ProductObservation(_key_from_url(url), link["title"], url, price, currency, availability))
    if not items and declared > 0:
        raise CollectionError(f"No product cards found at {page_url} although {declared} results are declared")

    next_el = soup.select_one("li.next a[href]")
    return declared, items, urljoin(page_url, next_el["href"]) if next_el else None


def fetch_snapshot(client: httpx.Client, source: SourceConfig) -> list[ProductObservation]:
    return collect_pages(client, source, urljoin(source.base_url, "/catalogue/page-1.html"), parse_page)
