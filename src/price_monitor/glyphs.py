"""Product pictograms drawn from simple primitives on a 120x120 canvas.

One description feeds both the SVG pictures of the demo shop and the PNG cards of Telegram alerts,
so the two always show the same picture.
"""
from __future__ import annotations

from html import escape

PALETTE = ["#4f7c6a", "#6c7fb0", "#b0746c", "#a38b4b", "#7a6aa8", "#4d8fa3", "#8a8f4a", "#a35f8a"]

# ("rect", x, y, w, h, corner radius) | ("line", [points]) | ("poly", [points], closed) | ("circle", cx, cy, r)
Primitive = tuple

GLYPHS: dict[str, list[Primitive]] = {
    "lamp": [("poly", [(40, 60), (52, 30), (68, 30), (80, 60)]), ("line", [(60, 60), (60, 92)]), ("line", [(42, 94), (78, 94)])],
    "notebook": [("rect", 34, 26, 52, 68, 4), ("line", [(46, 44), (74, 44)]), ("line", [(46, 56), (74, 56)]), ("line", [(46, 68), (66, 68)])],
    "pen": [("poly", [(38, 88), (82, 36), (90, 44), (46, 96)]), ("line", [(38, 88), (34, 100), (46, 96)])],
    "chair": [("rect", 42, 24, 36, 34, 4), ("rect", 38, 62, 44, 10, 3), ("line", [(60, 72), (60, 92)]), ("line", [(44, 96), (76, 96)])],
    "hub": [("rect", 28, 48, 64, 26, 5), ("line", [(42, 58), (48, 58)]), ("line", [(56, 58), (62, 58)]), ("line", [(70, 58), (76, 58)])],
    "cable": [
        ("line", [(30, 80), (30, 62), (38, 50), (50, 48), (58, 54), (60, 60), (62, 68), (70, 76), (82, 74), (90, 60), (90, 40)]),
        ("rect", 24, 78, 10, 10, 0), ("rect", 86, 32, 10, 10, 0),
    ],
    "mouse": [("rect", 42, 30, 36, 60, 18), ("line", [(60, 30), (60, 54)]), ("line", [(42, 54), (78, 54)])],
    "keyboard": [
        ("rect", 24, 44, 72, 36, 5),
        *[("line", [(x, 54), (x + 4, 54)]) for x in (34, 46, 58, 70, 82)],
        ("line", [(36, 66), (84, 66)]),
    ],
    "stand": [("rect", 30, 30, 60, 38, 3), ("line", [(60, 68), (60, 88)]), ("line", [(44, 92), (76, 92)])],
    "webcam": [("circle", 60, 46, 20), ("circle", 60, 46, 8), ("line", [(60, 66), (60, 86)]), ("line", [(44, 92), (76, 92)])],
    "generic": [("rect", 34, 38, 52, 44, 4)],
}

# First match wins, so "cable organiser" is checked before the plain "cable".
KEYWORDS = [
    ("lamp", "lamp"), ("notebook", "notebook"), ("pen", "pen"), ("chair", "chair"), ("hub", "hub"),
    ("cable organiser", "cable"), ("cable", "cable"), ("mouse", "mouse"), ("keyboard", "keyboard"),
    ("stand", "stand"), ("webcam", "webcam"),
]


def glyph_for(name: str) -> list[Primitive]:
    lowered = name.lower()
    for word, glyph in KEYWORDS:
        if word in lowered:
            return GLYPHS[glyph]
    return GLYPHS["generic"]


def color_for(key: str) -> str:
    return PALETTE[sum(map(ord, key)) % len(PALETTE)]


def _pts(points) -> str:
    return " ".join(f"{x},{y}" for x, y in points)


def _svg_shape(p: Primitive) -> str:
    kind = p[0]
    if kind == "rect":
        _, x, y, w, h, r = p
        return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>'
    if kind == "line":
        return f'<polyline points="{_pts(p[1])}"/>'
    if kind == "poly":
        return f'<polygon points="{_pts(p[1])}"/>'
    _, cx, cy, r = p
    return f'<circle cx="{cx}" cy="{cy}" r="{r}"/>'


def product_svg(key: str, name: str) -> str:
    shapes = "".join(_svg_shape(p) for p in glyph_for(name))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120" role="img" '
        f'aria-label="{escape(name)}"><rect width="120" height="120" fill="{color_for(key)}"/>'
        f'<g fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">{shapes}</g></svg>'
    )


def draw_glyph(draw, name: str, left: int, top: int, size: int) -> None:
    """Draw the pictogram with Pillow into the square (left, top, size x size)."""
    k = size / 120
    width = max(2, round(3 * k))

    def at(x, y):
        return (left + x * k, top + y * k)

    for p in glyph_for(name):
        if p[0] == "rect":
            _, x, y, w, h, r = p
            draw.rounded_rectangle([at(x, y), at(x + w, y + h)], radius=r * k, outline="white", width=width)
        elif p[0] == "line":
            draw.line([at(x, y) for x, y in p[1]], fill="white", width=width, joint="curve")
        elif p[0] == "poly":
            pts = [at(x, y) for x, y in p[1]]
            draw.line(pts + [pts[0]], fill="white", width=width, joint="curve")
        else:
            _, cx, cy, r = p
            draw.ellipse([at(cx - r, cy - r), at(cx + r, cy + r)], outline="white", width=width)
