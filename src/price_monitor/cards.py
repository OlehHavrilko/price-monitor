"""PNG card attached to a Telegram alert: product picture, name, price was -> now."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw, ImageFont

from . import glyphs
from .notifications import drop_percent

WIDTH, HEIGHT = 900, 460
BG, INK, MUTED = "#f4f1ea", "#2b2a27", "#6b6b66"
BADGE_COLORS = {"drop": "#2e7d32", "up": "#b3261e", "stock": "#1d5fa8", "out": "#b3261e", "removed": "#6b6b66"}


def _font(size: int):
    return ImageFont.load_default(size=size)


def render_alert_card(card: dict) -> bytes:
    """card keys: name, key, source, currency, price, old_price (optional), headline, tone, stock (optional)."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(img)

    d.rounded_rectangle([40, 50, 400, 410], radius=18, fill=glyphs.color_for(card["key"]))
    glyphs.draw_glyph(d, card["name"], 70, 80, 300)

    x = 440
    tone = BADGE_COLORS.get(card.get("tone", ""), "#2f4a3c")
    headline = card["headline"]
    w = d.textlength(headline, font=_font(26)) + 36
    d.rounded_rectangle([x, 50, x + w, 94], radius=10, fill=tone)
    d.text((x + 18, 72), headline, font=_font(26), fill="white", anchor="lm")

    d.text((x, 130), card["name"], font=_font(36), fill=INK)

    currency = card.get("currency", "")
    old, new = card.get("old_price"), card["price"]
    price_y = 215
    if not new:
        d.text((x, price_y), "no price shown", font=_font(44), fill=MUTED)
    elif old and old != new:
        was = f"was {old} {currency}"
        d.text((x, 175), was, font=_font(30), fill=MUTED)
        d.line([(x + d.textlength("was ", font=_font(30)), 192), (x + d.textlength(was, font=_font(30)), 192)], fill=MUTED, width=3)
        d.text((x, price_y), f"now {new}", font=_font(64), fill=INK)
        pct = drop_percent(old, new)
        if pct:
            d.text((x, 300), f"-{pct}%", font=_font(34), fill=BADGE_COLORS["drop"])
    else:
        d.text((x, price_y), f"{new} {currency}", font=_font(64), fill=INK)

    if card.get("stock"):
        d.text((x, 340), card["stock"], font=_font(28), fill=BADGE_COLORS["out" if "Out" in card["stock"] else "stock"])
    d.text((x, 410), f'{card["source"]} · code {card["key"]}', font=_font(22), fill=MUTED)

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()
