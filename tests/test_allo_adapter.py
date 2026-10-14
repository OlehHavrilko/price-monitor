from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest

from conftest import make_source
from price_monitor.adapters.allo import parse_page
from price_monitor.collector import collect_once
from price_monitor.models import Availability as A
from price_monitor.models import CollectionError

FIX = Path(__file__).parent / "fixtures"
URL = "https://allo.ua/ua/fitnes-braslety/"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_real_cards_from_fixture_buy_and_order_on_request():
    items, current, last, next_url = parse_page(read("allo_page1.html"), URL)
    assert (current, last, next_url) == (1, 3, "https://allo.ua/ua/fitnes-braslety/p-2/")
    buy, order = items
    assert (buy.product_key, buy.price, buy.currency, buy.availability) == ("1207856", D("4299"), "UAH", A.IN_STOCK)
    assert buy.name.startswith("Фітнес-браслет Xiaomi") and buy.url.startswith("https://allo.ua/ua/")
    assert (order.price, order.availability) == (None, A.UNKNOWN)  # "Замовити": no price, stock not stated


def test_back_in_stock_button_means_out_of_stock_and_last_page_has_no_next():
    items, current, last, next_url = parse_page(read("allo_last.html"), URL + "p-3/")
    assert (current, last, next_url) == (3, 3, None)
    notify, buy = items
    assert notify.availability is A.OUT_OF_STOCK and notify.price == D("1999")
    assert buy.product_key == "33307412-6060"  # keys stay text, hyphens included


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h: h.replace("product-card", "item-card"),
        lambda h: h.replace("product-sku__value", "sku"),
        lambda h: h.replace("Купити", "Додати"),
        lambda h: h.replace("₴", "$"),
        lambda h: h.replace("v-pb__cur", "price"),  # a priced card without a price is not "order on request"
    ],
)
def test_changed_markup_is_an_error(mutate):
    with pytest.raises(CollectionError):
        parse_page(mutate(read("allo_page1.html")), URL)


# --- whole collection over a substituted transport -------------------------------------------

def card(sku, button="Купити", price="1 000 ₴"):
    price_html = f'<div class="v-pb__cur">{price}</div>' if price else ""
    return (
        f'<div class="product-card"><span class="product-sku__value">{sku}</span>'
        f'<a class="product-card__title" href="https://allo.ua/ua/p/{sku}.html">Band {sku}</a>{price_html}'
        f"<button>{button}</button></div>"
    )


def pager(current, last):
    items = "".join(
        f'<li class="pagination__item{" current" if n == current else ""}">'
        f'<a class="pagination__links" href="https://allo.ua/ua/fitnes-braslety/{"" if n == 1 else f"p-{n}/"}">{n}</a></li>'
        for n in range(1, last + 1)
    )
    return f'<nav class="pagination"><ul class="pagination__list">{items}</ul></nav>'


def site(pages):
    def handler(request):
        key = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        number = int(key[2:]) if key.startswith("p-") else 1
        html = pages.get(number)
        return httpx.Response(200 if html else 404, text=html or "")

    return httpx.MockTransport(handler)


def run(conn, pages, **kw):
    source = make_source("https://allo.ua", id="allo", adapter="allo", **kw)
    return collect_once(conn, source, site(pages))


def test_three_page_category_is_collected_completely(conn):
    pages = {
        1: card("1") + card("2") + pager(1, 3),
        2: card("3") + card("4") + pager(2, 3),
        3: card("5") + pager(3, 3),
    }
    result = run(conn, pages)
    assert result.ok and result.product_count == 5


@pytest.mark.parametrize(
    "pages, text",
    [
        ({1: card("1") + card("2") + pager(1, 3), 2: card("3") + pager(2, 3), 3: card("5") + pager(3, 3)}, "Page 2 has 1"),
        ({1: card("1") + pager(1, 3), 2: card("3") + pager(2, 3)}, "HTTP 404"),
        ({1: card("1") + card("2") + pager(1, 2), 2: card("2") + pager(2, 2)}, "Duplicate product key"),
        ({1: card("1") + card("2") + pager(1, 3), 2: card("3") + card("4") + pager(2, 3), 3: card("5") + card("6") + card("7") + pager(3, 3)}, "more products than the first"),
    ],
)
def test_incomplete_or_inconsistent_category_is_a_failed_run(conn, pages, text):
    result = run(conn, pages)
    assert not result.ok and text in result.error


def test_category_growing_beyond_the_page_limit_is_an_error(conn):
    pages = {n: card(str(n)) + pager(n, 8) for n in range(1, 9)}
    result = run(conn, pages, max_pages=6)
    assert not result.ok and "Page limit 6" in result.error


def test_a_priceless_card_is_stored_and_then_priced(conn):
    first = run(conn, {1: card("1", "Замовити", price=None) + pager(1, 1)})
    assert first.ok
    second = run(conn, {1: card("1", "Купити", price="999 ₴") + pager(1, 1)})
    assert [e.type for e in second.events] == ["PRICE_CHANGED"]
    assert (second.events[0].old_value, second.events[0].new_value) == (None, "999")
