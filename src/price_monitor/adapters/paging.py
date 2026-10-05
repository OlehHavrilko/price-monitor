from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

from ..http import get_page
from ..models import CollectionError, ProductObservation, SourceConfig

# parse_page(html, page_url) -> (declared total, products on the page, next page URL or None)
PageParser = Callable[[str, str], tuple[int, list[ProductObservation], "str | None"]]


def _same_origin(base: str, url: str) -> bool:
    a, b = urlparse(base), urlparse(url)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def collect_pages(
    client: httpx.Client, source: SourceConfig, start_url: str, parse_page: PageParser
) -> list[ProductObservation]:
    """Walk a paginated catalog and return the complete snapshot, or raise CollectionError.

    A page limit, a pagination loop, a duplicate key or a total that differs from the declared
    count is an error: a truncated or inconsistent catalog is never returned as a success.
    """
    url: str | None = start_url
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
