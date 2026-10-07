"""Local demo catalog: a test bench, not a product.

Serves a small HTML catalog on 127.0.0.1. The active scenario is read from a state file on
every request, so `price-monitor demo-scenario <name>` switches it without a restart.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import demo_theme

PAGE_SIZE = 5


@dataclass(frozen=True)
class Item:
    sku: str
    name: str
    price: str
    stock: str | None  # "in", "out" or None (page shows no stock information)


BASE_CATALOG = [
    Item("00123", "Desk lamp Nord", "24.90", "in"),
    Item("123", "Desk lamp Nord, spare bulb", "3.50", "in"),
    Item("00456", "Paper notebook A5, 5 pcs", "9.99", "in"),
    Item("00457", "Gel pen set, 12 colours", "6.40", "in"),
    Item("00789", "Office chair Basic", "89.00", "out"),
    Item("01001", "USB hub 4 ports", "14.25", "in"),
    Item("01002", "HDMI cable 2 m", "7.80", "in"),
    Item("01003", "Wireless mouse", "12.50", "in"),
    Item("01004", "Keyboard, compact", "32.00", "in"),
    Item("01005", "Monitor stand", "27.45", "in"),
    Item("01006", "Webcam 1080p", "41.00", None),
    Item("01007", "Cable organiser", "4.20", "in"),
]

SCENARIOS = {
    "initial": "Base catalog, 12 products on 3 pages.",
    "price_drop": "00123 costs 19.90 instead of 24.90; 01003 costs 12.50 -> 11.00.",
    "out_of_stock": "01001 is explicitly out of stock; 00789 is back in stock.",
    "page_lost": "Page 2 answers 404 (temporary loss).",
    "pagination_cycle": "'Next' link on page 2 leads back to page 1.",
    "product_missing": "01002 is gone from the catalog.",
    "two_products_missing": "01002 and 01007 are gone from the catalog.",
    "changed_template": "Product cards use a different class; parser must refuse the page.",
    "duplicate_key": "Two cards share the same SKU.",
    "count_mismatch": "Declared total is higher than the number of cards served.",
}


def catalog_for(scenario: str) -> list[Item]:
    items = list(BASE_CATALOG)
    by_sku = {i.sku: i for i in items}
    if scenario == "price_drop":
        by_sku["00123"] = replace(by_sku["00123"], price="19.90")
        by_sku["01003"] = replace(by_sku["01003"], price="11.00")
    elif scenario == "out_of_stock":
        by_sku["01001"] = replace(by_sku["01001"], stock="out")
        by_sku["00789"] = replace(by_sku["00789"], stock="in")
    elif scenario == "product_missing":
        del by_sku["01002"]
    elif scenario == "two_products_missing":
        del by_sku["01002"]
        del by_sku["01007"]
    elif scenario == "duplicate_key":
        return [replace(i, sku="01006") if i.sku == "01007" else i for i in items]
    return [by_sku[i.sku] for i in items if i.sku in by_sku]


def read_scenario(state_file: Path) -> str:
    try:
        name = json.loads(state_file.read_text(encoding="utf-8"))["scenario"]
    except (OSError, ValueError, KeyError):
        return "initial"
    return name if name in SCENARIOS else "initial"


def write_scenario(state_file: Path, name: str) -> None:
    if name not in SCENARIOS:
        raise ValueError(f"Unknown scenario '{name}'. Available: {', '.join(SCENARIOS)}")
    state_file.write_text(json.dumps({"scenario": name}), encoding="utf-8")


def render_card(item: Item, scenario: str) -> str:
    card_class = "item" if scenario == "changed_template" else "product"
    if item.stock == "in":
        stock_html = '<span class="stock">In stock</span>'
    elif item.stock == "out":
        stock_html = '<span class="stock out">Out of stock</span>'
    else:
        # No .stock element on purpose: the adapter must report UNKNOWN, not OUT_OF_STOCK.
        stock_html = '<span class="nostock">Availability not shown</span>'
    base = next((b.price for b in BASE_CATALOG if b.sku == item.sku), item.price)
    was = f'<s class="was">{base}</s>' if base != item.price else ""
    flag = '<span class="flag">Price changed</span>' if was else ""
    sku, name = escape(item.sku), escape(item.name)
    return (
        f'<article class="{card_class}" data-sku="{sku}">{flag}'
        f'<a href="/product/{sku}"><img src="/img/{sku}.svg" alt="{name}"></a>'
        f'<h2><a href="/product/{sku}">{name}</a></h2>'
        f'<div><span class="price" data-currency="USD">{item.price}</span>{was}</div>{stock_html}</article>'
    )


def layout(body: str, scenario: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        f"<title>Demo Shop</title><style>{demo_theme.CSS}</style></head><body>"
        "<header><h1>Demo Shop</h1><span>stationery and desk accessories</span></header><main>"
        '<p class="notice">Test bench of Price Monitor: the shop and its products are fictional. '
        f"Active scenario: <b>{escape(scenario)}</b>.</p>{body}</main>"
        "<footer>Prices and stock here are changed on purpose to demonstrate the monitor.</footer></body></html>"
    )


def render_product(item: Item, scenario: str) -> str:
    stock = {"in": "In stock", "out": "Out of stock"}.get(item.stock or "", "Availability not shown")
    sku, name = escape(item.sku), escape(item.name)
    return layout(
        f'<div class="detail"><img src="/img/{sku}.svg" alt="{name}"><div><h2>{name}</h2>'
        f'<p>Code: {sku}</p><p class="price">{item.price} USD</p><p>{stock}</p>'
        '<p><a href="/catalog?page=1">Back to catalog</a></p></div></div>',
        scenario,
    )


def render_page(page: int, scenario: str) -> str | None:
    items = catalog_for(scenario)
    pages = max(1, -(-len(items) // PAGE_SIZE))
    if page < 1 or page > pages:
        return None
    chunk = items[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]
    declared = len(items) + (3 if scenario == "count_mismatch" else 0)
    nxt = ""
    if page < pages:
        target = 1 if (scenario == "pagination_cycle" and page == 2) else page + 1
        nxt = f'<a rel="next" href="/catalog?page={target}">Next</a>'
    cards = "\n".join(render_card(i, scenario) for i in chunk)
    prev = f'<a href="/catalog?page={page - 1}">Previous</a>' if page > 1 else "<span></span>"
    return layout(
        f'<p id="total" data-count="{declared}">{declared} products, page {page} of {pages}</p>\n'
        f'<div class="grid">\n{cards}\n</div>\n<nav>{prev}{nxt}</nav>',
        scenario,
    )


def make_handler(state_file: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            scenario = read_scenario(state_file)
            url = urlparse(self.path)
            content_type = "text/html; charset=utf-8"
            if url.path == "/catalog":
                try:
                    page = int(parse_qs(url.query).get("page", ["1"])[0])
                except ValueError:
                    page = 0
                body = None if (scenario == "page_lost" and page == 2) else render_page(page, scenario)
            elif url.path.startswith("/product/"):
                sku = url.path.removeprefix("/product/")
                item = next((i for i in catalog_for(scenario) if i.sku == sku), None)
                body = render_product(item, scenario) if item else None
            elif url.path.startswith("/img/") and url.path.endswith(".svg"):
                sku = url.path.removeprefix("/img/").removesuffix(".svg")
                item = next((i for i in catalog_for(scenario) if i.sku == sku), None)
                body = demo_theme.product_svg(item.sku, item.name) if item else None
                content_type = "image/svg+xml"
            else:
                body = None
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):  # noqa: A002
            pass

    return Handler


def serve(state_file: Path, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(state_file))


def serve_in_thread(state_file: Path) -> tuple[ThreadingHTTPServer, str]:
    server = serve(state_file, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
