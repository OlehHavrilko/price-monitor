"""Look of the demo catalog: page styles and generated product pictures (SVG).

Purely presentational. The adapter reads only the markup contract in demo_site.render_card, so
nothing here may change class names or the structure of the cards.
"""
from __future__ import annotations

from . import glyphs

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

def product_svg(sku: str, name: str) -> str:
    return glyphs.product_svg(sku, name)
