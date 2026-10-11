import asyncio
import io
import json
import threading
from decimal import Decimal as D

import pytest
from PIL import Image

from conftest import make_source
from price_monitor import cards, demo_site, storage, telegram
from price_monitor.collector import collect_once
from price_monitor.glyphs import GLYPHS, glyph_for, product_svg


def test_alert_card_is_a_valid_png_for_every_kind_of_alert():
    base = dict(name="Desk lamp Nord", key="00123", source="demo", currency="USD", price="19.90")
    for extra in (
        dict(old_price="24.90", headline="Price drop", tone="drop", stock="In stock"),
        dict(headline="Out of stock", tone="out", stock="Out of stock"),
        dict(headline="Removed from catalog", tone="removed", stock=None),
    ):
        img = Image.open(io.BytesIO(cards.render_alert_card({**base, **extra})))
        assert img.format == "PNG" and img.size == (cards.WIDTH, cards.HEIGHT)


def test_drop_percent_is_exact_and_ignores_rises_and_zero():
    assert cards.drop_percent("24.90", "19.90") == D("20.1")
    assert cards.drop_percent("10", "12") is None
    assert cards.drop_percent("0", "5") is None
    assert cards.drop_percent(None, "5") is None


def test_every_glyph_has_both_svg_and_picture_output():
    assert set(GLYPHS) >= {"lamp", "chair", "generic"}
    assert glyph_for("Desk lamp Nord, spare bulb") is GLYPHS["lamp"]
    assert glyph_for("Cable organiser") is GLYPHS["cable"] and glyph_for("HDMI cable 2 m") is GLYPHS["cable"]
    assert glyph_for("Something else") is GLYPHS["generic"]
    assert product_svg("1", "Webcam").startswith("<svg")


def test_alert_in_the_queue_carries_the_card_data(demo, conn):
    url, state = demo
    collect_once(conn, make_source(url), recipients=(7,))
    storage.set_rule(conn, "demo", "00123", drop_percent=D("10"))
    demo_site.write_scenario(state, "price_drop")
    collect_once(conn, make_source(url), recipients=(7,))
    card = json.loads(conn.execute("SELECT card FROM notification_outbox").fetchone()[0])
    assert (card["name"], card["old_price"], card["price"], card["headline"]) == ("Desk lamp Nord", "24.90", "19.90", "Price drop")


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_photo(self, chat_id, photo, caption=None):
        self.calls.append(("photo", chat_id, caption, len(photo.data)))

    async def send_message(self, chat_id, text, **kwargs):
        self.calls.append(("text", chat_id, text))


@pytest.fixture
def loop():
    lp = asyncio.new_event_loop()
    thread = threading.Thread(target=lp.run_forever, daemon=True)
    thread.start()
    yield lp
    lp.call_soon_threadsafe(lp.stop)
    thread.join()


def test_sender_attaches_the_picture_and_falls_back_to_text(loop, monkeypatch):
    bot = FakeBot()
    send = telegram.make_sender(bot, loop)
    card = dict(name="Desk lamp Nord", key="00123", source="demo", currency="USD", price="19.90", headline="Price drop", tone="drop")
    send(5, "caption", card)
    send(5, "plain", None)
    assert bot.calls[0][:3] == ("photo", 5, "caption") and bot.calls[0][3] > 1000
    assert bot.calls[1] == ("text", 5, "plain")

    monkeypatch.setattr(cards, "render_alert_card", lambda c: (_ for _ in ()).throw(RuntimeError("no font")))
    send(5, "still delivered", card)
    assert bot.calls[2] == ("text", 5, "still delivered")
