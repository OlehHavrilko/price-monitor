from decimal import Decimal as D
from pathlib import Path

import httpx
import pytest

from conftest import make_source
from price_monitor.adapters.xcom import parse_page
from price_monitor.collector import collect_once
from price_monitor.models import Availability as A
from price_monitor.models import CollectionError

FIX = Path(__file__).parent / "fixtures"
URL = "https://xcom.ua/catalog/avto_fm_transmitter/"


def read(name):
    return (FIX / name).read_text(encoding="utf-8")


def test_real_cards_from_fixture():
    items, current, next_url = parse_page(read("xcom_page1.html"), URL)
    assert (current, next_url) == (1, URL + "?page=2")
    stocked, sold_out = items
    assert (stocked.product_key, stocked.price, stocked.currency, stocked.availability) == ("990874", D("205"), "UAH", A.IN_STOCK)
    assert stocked.url.startswith("https://xcom.ua/product/990874")
    assert (sold_out.product_key, sold_out.availability) == ("840216", A.OUT_OF_STOCK)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda h: h.replace("itemCells", "cells"),
        lambda h: h.replace('name="item"', 'name="goods"'),
        lambda h: h.replace('itemprop="price"', 'itemprop="cost"'),
        lambda h: h.replace('content="UAH"', 'content="USD"'),
        lambda h: h.replace('itemprop="availability"', 'itemprop="state"'),
        lambda h: h.replace('class="currentpage"', 'class="page"'),
    ],
)
def test_changed_markup_is_an_error(mutate):
    with pytest.raises(CollectionError):
        parse_page(mutate(read("xcom_page1.html")), URL)


def test_unfamiliar_stock_wording_is_unknown_not_a_guess():
    html = read("xcom_page1.html").replace("Есть на складе", "Ожидается поставка")
    items, _, _ = parse_page(html, URL)
    assert items[0].availability is A.UNKNOWN


# --- whole collection over a substituted transport -------------------------------------------

def card(item_id, stock="Есть на складе", price="205"):
    return (
        f'<div class="item"><div class="title"><a href="/product/{item_id}__x/">Item {item_id}</a></div>'
        f'<span itemprop="offers"><div itemprop="availability">{stock}</div><meta content="UAH" itemprop="priceCurrency"/>'
        f'<div class="price"><span itemprop="price"> {price}</span> грн</div>'
        f'<form><input name="item" type="hidden" value="{item_id}"/></form></span></div>'
    )


def page(cards, current, last):
    links = "".join(
        f'<span class="currentpage">{n}</span>' if n == current else f'<a href="{URL}?page={n}">{n}</a>'
        for n in range(1, last + 1)
    )
    return f'<div class="itemCells">{"".join(cards)}</div><div class="pages">{links}</div>'


def run(conn, pages, **kw):
    def handler(request):
        number = int(request.url.params.get("page", "1"))
        html = pages.get(number)
        return httpx.Response(200 if html else 404, text=html or "")

    source = make_source("https://xcom.ua", id="xcom", adapter="xcom", **kw)
    return collect_once(conn, source, httpx.MockTransport(handler))


def test_two_page_category_is_collected_completely(conn):
    result = run(conn, {1: page([card(1), card(2)], 1, 2), 2: page([card(3, "Нет в наличии")], 2, 2)})
    assert result.ok and result.product_count == 3


@pytest.mark.parametrize(
    "pages, text",
    [
        ({1: page([card(1), card(2)], 1, 3), 2: page([card(3)], 2, 3), 3: page([card(4)], 3, 3)}, "Page 2 has 1"),
        ({1: page([card(1)], 1, 2)}, "HTTP 404"),
        ({1: page([card(1), card(2)], 1, 2), 2: page([card(2)], 2, 2)}, "Duplicate product key"),
        ({1: page([card(1)], 1, 2), 2: page([card(2), card(3)], 2, 2)}, "more products than the first"),
    ],
)
def test_incomplete_or_inconsistent_category_is_a_failed_run(conn, pages, text):
    result = run(conn, pages)
    assert not result.ok and text in result.error


def test_stock_change_between_two_collections_is_an_event(conn):
    run(conn, {1: page([card(1)], 1, 1)})
    second = run(conn, {1: page([card(1, "Нет в наличии")], 1, 1)})
    assert [(e.type, e.old_value, e.new_value) for e in second.events] == [("AVAILABILITY_CHANGED", "IN_STOCK", "OUT_OF_STOCK")]
