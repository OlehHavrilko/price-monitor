from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from conftest import make_source
from price_monitor.adapters.books import parse_page
from price_monitor.collector import collect_once
from price_monitor.models import Availability, CollectionError

FIX = Path(__file__).parent / "fixtures"
BASE = "https://books.toscrape.com/catalogue/"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_first_page_fixture():
    declared, items, next_url = parse_page(read("books_page1.html"), BASE + "page-1.html")
    assert declared == 1000 and len(items) == 3
    first = items[0]
    assert first.product_key == "a-light-in-the-attic_1000"
    assert first.name == "A Light in the Attic"  # full title from the attribute, not the shortened link text
    assert (first.price, first.currency, first.availability) == (Decimal("51.77"), "GBP", Availability.IN_STOCK)
    assert first.url == BASE + "a-light-in-the-attic_1000/index.html"
    assert next_url == BASE + "page-2.html"


def test_last_page_has_no_next_link():
    declared, items, next_url = parse_page(read("books_last.html"), BASE + "page-50.html")
    assert len(items) == 2 and next_url is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h: h.replace("product_pod", "book_card"),
        lambda h: h.replace("price_color", "cost"),
        lambda h: h.replace("£51.77", "51.77 EUR"),
        lambda h: h.replace("form-horizontal", "other"),
    ],
)
def test_changed_template_is_an_error(mutate):
    with pytest.raises(CollectionError):
        parse_page(mutate(read("books_page1.html")), BASE + "page-1.html")


def test_two_page_site_over_a_substituted_transport(conn):
    page1, last = read("books_page1.html"), read("books_last.html")
    # Rebuild a consistent two-page site: 3 + 2 products, declared total 5.
    page1 = page1.replace("<strong>1000</strong>", "<strong>5</strong>")
    last = last.replace("<strong>1000</strong>", "<strong>5</strong>").replace("page-49.html", "page-1.html")
    pages = {"/catalogue/page-1.html": page1, "/catalogue/page-2.html": last}

    def handler(request):
        text = pages.get(request.url.path)
        return httpx.Response(200 if text else 404, text=text or "")

    source = make_source("https://books.toscrape.com", id="books", adapter="books")
    result = collect_once(conn, source, httpx.MockTransport(handler))
    assert result.ok and result.product_count == 5
