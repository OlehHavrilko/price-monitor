from decimal import Decimal
from pathlib import Path

import pytest

from price_monitor.adapters.demo import parse_page
from price_monitor.models import Availability, CollectionError

FIXTURE = Path(__file__).parent / "fixtures" / "demo_page.html"


def test_parse_fixture_keeps_keys_as_text_and_separates_unknown_stock():
    declared, items, next_url = parse_page(FIXTURE.read_text(encoding="utf-8"), "http://x.test/catalog?page=1")
    assert declared == 3
    assert [i.product_key for i in items] == ["00123", "00789", "01006"]
    assert items[0].price == Decimal("24.90")
    assert items[0].url == "http://x.test/product/00123"  # fragment dropped
    assert [i.availability for i in items] == [Availability.IN_STOCK, Availability.OUT_OF_STOCK, Availability.UNKNOWN]
    assert next_url == "http://x.test/catalog?page=2"


CARD = '<article class="product" data-sku="1"><h2><a href="/p/1">A</a></h2>{}</article>'


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>maintenance</body></html>",
        '<p id="total" data-count="2"></p>',
        '<p id="total" data-count="1"></p>' + CARD.format('<span class="price">free</span>'),
        '<p id="total" data-count="1"></p>' + CARD.format(""),
    ],
)
def test_unexpected_markup_is_an_error_not_an_empty_catalog(html):
    with pytest.raises(CollectionError):
        parse_page(html, "http://x.test/catalog?page=1")


def test_declared_empty_catalog_is_a_valid_empty_page():
    declared, items, next_url = parse_page('<p id="total" data-count="0"></p>', "http://x.test/catalog?page=1")
    assert (declared, items, next_url) == (0, [], None)
