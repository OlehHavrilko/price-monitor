"""Look of the demo catalog: page styles and generated product pictures (SVG).

Purely presentational. The adapter reads only the markup contract in demo_site.render_card, so
nothing here may change class names or the structure of the cards.
"""
from __future__ import annotations

from html import escape

CSS = """
*{box-sizing:border-box}
body{margin:0;font:16px/1.45 system-ui,Segoe UI,Arial,sans-serif;background:#f4f1ea;color:#2b2a27}
header{background:#2f4a3c;color:#fff;padding:18px 24px;display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
header h1{margin:0;font-size:24px}
header span{opacity:.8;font-size:14px}
main{max-width:1000px;margin:0 auto;padding:20px 16px 40px}
.notice{background:#fff7d6;border:1px solid #e6d58a;border-radius:8px;padding:10px 14px;margin:0 0 18px;font-size:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(176px,1fr));gap:16px}
.product,.item{background:#fff;border-radius:10px;padding:12px;box-shadow:0 1px 3px rgba(0,0,0,.12);display:flex;flex-direction:column;gap:6px;position:relative}
.product h2,.item h2{font-size:15px;margin:4px 0 0;min-height:42px}
.product a,.item a{color:inherit;text-decoration:none}
.product img,.item img{width:100%;border-radius:8px;display:block}
.price{font-size:20px;font-weight:700}
.was{color:#8a8a85;font-size:14px;margin-left:6px}
.stock{font-size:13px;color:#2e7d32}
.stock.out{color:#b3261e}
.nostock{font-size:13px;color:#6b6b66}
.flag{position:absolute;top:18px;left:18px;background:#d9480f;color:#fff;border-radius:6px;padding:2px 8px;font-size:12px}
nav{display:flex;justify-content:space-between;margin-top:22px}
nav a{background:#2f4a3c;color:#fff;padding:8px 16px;border-radius:8px;text-decoration:none}
footer{text-align:center;color:#6b6b66;font-size:13px;padding:0 0 24px}
.detail{display:flex;gap:24px;flex-wrap:wrap;background:#fff;border-radius:10px;padding:20px}
.detail img{width:280px;max-width:100%;border-radius:10px}
"""

PALETTE = ["#4f7c6a", "#6c7fb0", "#b0746c", "#a38b4b", "#7a6aa8", "#4d8fa3", "#8a8f4a", "#a35f8a"]

# Simple line pictograms on a 120x120 canvas, drawn in white.
GLYPHS = {
    "lamp": '<path d="M40 60 L52 30 H68 L80 60 Z"/><path d="M60 60 V92"/><path d="M42 94 H78"/>',
    "notebook": '<rect x="34" y="26" width="52" height="68" rx="4"/><path d="M46 44 H74 M46 56 H74 M46 68 H66"/>',
    "pen": '<path d="M38 88 L82 36 L90 44 L46 96 Z"/><path d="M38 88 L34 100 L46 96"/>',
    "chair": '<rect x="42" y="24" width="36" height="34" rx="4"/><rect x="38" y="62" width="44" height="10" rx="3"/><path d="M60 72 V92 M44 96 H76"/>',
    "hub": '<rect x="28" y="48" width="64" height="26" rx="5"/><path d="M42 58 H48 M56 58 H62 M70 58 H76"/>',
    "cable": '<path d="M30 80 C30 40 60 40 60 60 C60 80 90 80 90 40"/><rect x="24" y="78" width="10" height="10"/><rect x="86" y="32" width="10" height="10"/>',
    "mouse": '<rect x="42" y="30" width="36" height="60" rx="18"/><path d="M60 30 V54 M42 54 H78"/>',
    "keyboard": '<rect x="24" y="44" width="72" height="36" rx="5"/><path d="M34 54 H38 M46 54 H50 M58 54 H62 M70 54 H74 M82 54 H86 M36 66 H84"/>',
    "stand": '<rect x="30" y="30" width="60" height="38" rx="3"/><path d="M60 68 V88 M44 92 H76"/>',
    "webcam": '<circle cx="60" cy="46" r="20"/><circle cx="60" cy="46" r="8"/><path d="M60 66 V86 M44 92 H76"/>',
}

KEYWORDS = [
    ("lamp", "lamp"), ("notebook", "notebook"), ("pen", "pen"), ("chair", "chair"), ("hub", "hub"),
    ("cable organiser", "cable"), ("cable", "cable"), ("mouse", "mouse"), ("keyboard", "keyboard"),
    ("stand", "stand"), ("webcam", "webcam"),
]


def glyph_for(name: str) -> str:
    lowered = name.lower()
    for word, glyph in KEYWORDS:
        if word in lowered:
            return GLYPHS[glyph]
    return '<rect x="34" y="38" width="52" height="44" rx="4"/>'


def product_svg(sku: str, name: str) -> str:
    color = PALETTE[sum(map(ord, sku)) % len(PALETTE)]
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" role="img" '
        f'aria-label="{escape(name)}"><rect width="120" height="120" fill="{color}"/>'
        '<g fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">'
        f"{glyph_for(name)}</g></svg>"
    )
