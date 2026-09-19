"""Draw the app icon: an open spellbook with a gold sparkle (design B).

Writes packaging/spellbook.ico for the exe and the favicon files served by the
web page. Small sizes use a simplified drawing (no page lines, larger shapes)
because a straight downscale of the detailed one blurs at 16-32 px.

    .venv/Scripts/python.exe packaging/make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
ICO = ROOT / "packaging" / "spellbook.ico"
STATIC = ROOT / "spellbook" / "web" / "static"

NAVY, PAPER, PAPER_SHADE, INK_LINE, GOLD = "#13243c", "#f7f4ec", "#eee9dd", "#b9b1a4", "#f0bf4c"
SUPERSAMPLE = 1024

# Geometry in a 64-unit square. Each page is (top-left, top control, top-right,
# bottom-right, bottom control, bottom-left); edges are quadratic curves.
DETAILED = {
    "background": (4, 4, 60, 60, 11),
    "left_page": ((10, 25), (20, 21), (30.5, 25), (30.5, 51), (20, 47), (10, 51)),
    "right_page": ((33.5, 25), (44, 21), (54, 25), (54, 51), (44, 47), (33.5, 51)),
    "lines": [((14, 31), (20, 29), (27, 31)), ((14, 36), (20, 34), (27, 36)), ((14, 41), (20, 39), (27, 41)),
              ((37, 31), (44, 29), (50, 31)), ((37, 36), (44, 34), (50, 36))],
    "line_width": 1.6,
    "star": ((32, 17), 10, 3.1),  # centre, outer radius, inner radius
}
SIMPLE = {
    "background": (2, 2, 62, 62, 12),
    "left_page": ((8, 29), (19, 24.5), (30.5, 29), (30.5, 56), (19, 51.5), (8, 56)),
    "right_page": ((33.5, 29), (45, 24.5), (56, 29), (56, 56), (45, 51.5), (33.5, 56)),
    "lines": [],
    "line_width": 0,
    "star": ((32, 15.5), 12, 3),
}
SIMPLE_SIZES = (16, 24, 32)
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def quad(p0, p1, p2, steps=32):
    return [
        ((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
         (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1])
        for t in (i / steps for i in range(steps + 1))
    ]


def page_outline(page):
    top_left, top_ctrl, top_right, bottom_right, bottom_ctrl, bottom_left = page
    return quad(top_left, top_ctrl, top_right) + quad(bottom_right, bottom_ctrl, bottom_left)


def star_points(centre, outer, inner):
    (cx, cy), points = centre, []
    for i in range(8):
        radius = outer if i % 2 == 0 else inner
        dx, dy = [(0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1)][i]
        scale = radius if i % 2 == 0 else radius / 2 ** 0.5
        points.append((cx + dx * scale, cy + dy * scale))
    return points


def render(design, size: int) -> Image.Image:
    scale = SUPERSAMPLE / 64
    image = Image.new("RGBA", (SUPERSAMPLE, SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    s = lambda points: [(x * scale, y * scale) for x, y in points]
    x0, y0, x1, y1, radius = design["background"]
    draw.rounded_rectangle((x0 * scale, y0 * scale, x1 * scale, y1 * scale), radius=radius * scale, fill=NAVY)
    draw.polygon(s(page_outline(design["left_page"])), fill=PAPER)
    draw.polygon(s(page_outline(design["right_page"])), fill=PAPER_SHADE)
    for line in design["lines"]:
        draw.line(s(quad(*line)), fill=INK_LINE, width=round(design["line_width"] * scale), joint="curve")
    draw.polygon(s(star_points(*design["star"])), fill=GOLD)
    return image.resize((size, size), Image.Resampling.LANCZOS)


def icon_images(sizes):
    return [render(SIMPLE if size in SIMPLE_SIZES else DETAILED, size) for size in sizes]


def svg(design) -> str:
    def path(points):
        return "M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in points) + " Z"

    x0, y0, x1, y1, radius = design["background"]
    lines = "".join(
        f'<path d="{"M" + " L".join(f"{x:.2f} {y:.2f}" for x, y in quad(*line, steps=12))}" fill="none" '
        f'stroke="{INK_LINE}" stroke-width="{design["line_width"]}" stroke-linecap="round"/>'
        for line in design["lines"]
    )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{radius}" fill="{NAVY}"/>'
        f'<path d="{path(page_outline(design["left_page"]))}" fill="{PAPER}"/>'
        f'<path d="{path(page_outline(design["right_page"]))}" fill="{PAPER_SHADE}"/>'
        f"{lines}"
        f'<path d="{path(star_points(*design["star"]))}" fill="{GOLD}"/>'
        "</svg>\n"
    )


def save_ico(path: Path, sizes) -> None:
    images = icon_images(sizes)
    largest = images[-1]
    largest.save(path, format="ICO", sizes=[(size, size) for size in sizes], append_images=images[:-1])


def main() -> None:
    save_ico(ICO, ICO_SIZES)
    save_ico(STATIC / "favicon.ico", (16, 32, 48))
    (STATIC / "favicon.svg").write_text(svg(SIMPLE), encoding="utf-8")
    for path in (ICO, STATIC / "favicon.ico", STATIC / "favicon.svg"):
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
