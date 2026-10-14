"""Adapter for one category of allo.ua, a Ukrainian electronics shop: fitness bands (/ua/fitnes-braslety/).

Chosen because its robots.txt does not restrict category pages for generic crawlers and the catalog
is server-rendered HTML, so no browser automation is needed. The category is small (about 160 items
on 3 pages); the page limit in the source config is set low so that growth is reported as an error.

product_key is the shop's "код товару" printed on the card, kept as text ("33307412-6060" exists).

Stock is read from the card's button:
  "Купити"                          -> IN_STOCK
  "Повідомити про наявність товару" -> OUT_OF_STOCK (the shop offers a back-in-stock alert)
  "Замовити" (no price shown)       -> UNKNOWN, price None
Any other state is treated as a changed layout and fails the collection.

The page does not state how many products the category has, so completeness is checked by structure:
the pager's last page must be reached and every page except the last must be as full as the first.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urldefrag, urljoin

import httpx
from bs4 import BeautifulSoup

from ..models import Availability, CollectionError, ProductObservation, SourceConfig
from .paging import collect_pages

CATEGORY_PATH = "/ua/fitnes-braslety/"
BUTTON_STATE = {
    "Купити": Availability.IN_STOCK,
    "Повідомити про наявність товару": Availability.OUT_OF_STOCK,
    "Замовити": Availability.UNKNOWN,
}


def _price(text: str, page_url: str) -> Decimal:
    if "₴" not in text:
        raise CollectionError(f"Unexpected currency in price '{text}' at {page_url}")
    digits = re.sub(r"[^\d.,]", "", text).replace(",", ".")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        raise CollectionError(f"Unreadable price '{text}' at {page_url}")
    if value < 0:
        raise CollectionError(f"Negative price '{text}' at {page_url}")
    return value


def parse_page(html: str, page_url: str) -> tuple[list[ProductObservation], int, int, str | None]:
    """Return (products, current page, last page, next page URL)."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(".product-card")
    if not cards:
        raise CollectionError(f"No product cards at {page_url}: the page layout may have changed")

    items: list[ProductObservation] = []
    for card in cards:
        sku_el = card.select_one(".product-sku__value")
        link = card.select_one("a.product-card__title[href]")
        if sku_el is None or link is None or not sku_el.get_text(strip=True):
            raise CollectionError(f"Product card without code or title at {page_url}")
        buttons = {b.get_text(strip=True) for b in card.select("button")}
        states = [BUTTON_STATE[t] for t in BUTTON_STATE if t in buttons]
        if len(states) != 1:
            raise CollectionError(f"Unrecognised availability state {sorted(buttons)} at {page_url}")
        price_el = card.select_one(".v-pb__cur")
        if price_el is None:
            if states[0] is not Availability.UNKNOWN:
                raise CollectionError(f"Card {sku_el.get_text(strip=True)} has no price at {page_url}")
            price = None
        else:
            price = _price(price_el.get_text(" ", strip=True), page_url)
        items.append(
            ProductObservation(
                product_key=sku_el.get_text(strip=True),
                name=" ".join(link.get_text().split()),
                url=urldefrag(urljoin(page_url, link["href"]))[0],
                price=price,
                currency="UAH",
                availability=states[0],
            )
        )

    numbers = {
        int(a.get_text(strip=True)): a["href"]
        for a in soup.select(".pagination__item a.pagination__links[href]")
        if a.get_text(strip=True).isdigit()
    }
    current_el = soup.select_one(".pagination__item.current")
    if not numbers:
        return items, 1, 1, None  # a category that fits one page has no pager
    if current_el is None or not current_el.get_text(strip=True).isdigit():
        raise CollectionError(f"Pager without a current page at {page_url}")
    current, last = int(current_el.get_text(strip=True)), max(numbers)
    next_url = None
    if current < last:
        if current + 1 not in numbers:
            raise CollectionError(f"Pager has no link to page {current + 1} at {page_url}")
        next_url = urljoin(page_url, numbers[current + 1])
    return items, current, last, next_url


def fetch_snapshot(client: httpx.Client, source: SourceConfig) -> list[ProductObservation]:
    state: dict[str, int] = {}

    def parser(html: str, page_url: str):
        items, current, last, next_url = parse_page(html, page_url)
        first = state.setdefault("page_size", len(items))
        if state.setdefault("last_page", last) != last:
            raise CollectionError("The number of pages changed during the collection")
        if current < last and len(items) != first:
            raise CollectionError(f"Page {current} has {len(items)} products, the first page had {first}")
        if current == last and len(items) > first:
            raise CollectionError(f"The last page has more products than the first ({len(items)} > {first})")
        return None, items, next_url  # None: the shop gives no total to compare with

    return collect_pages(client, source, urljoin(source.base_url, CATEGORY_PATH), parser)
