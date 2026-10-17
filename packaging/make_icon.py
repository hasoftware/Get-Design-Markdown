"""The application logo, drawn from simple shapes (no third-party artwork).

The mark: a black rounded square holding a white camera-style capture frame (four corner
brackets) around three lines of text in the app's colours (lilac, lime, magenta). It reads as
"capture the design of a web page and write it down as a document with its colours".

One geometry, two outputs that must stay identical:

- `make_icon` draws the multi-size .ico of the Windows exe with Pillow; every size is rendered
  from the shapes (supersampled), not scaled down from one bitmap.
- `svg_markup` writes the same shapes as SVG: the favicon and the mark in the app's top bar
  (`src/get_design_markdown/ui/static/favicon.svg`; a test keeps that file in sync).

    python packaging/make_icon.py build/gdm-ui/icon.ico
    python packaging/make_icon.py src/get_design_markdown/ui/static/favicon.svg
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw

#: The artwork is designed on a 64-unit square. Every straight stroke edge lies on a multiple of
#: 4 units, so the 16, 32 and 48 px icons stay sharp (one 16 px pixel = 4 units); for that the
#: text lines sit 2 units above the centre, which also reads as the optical centre.
GRID = 64
MASTER_SIZE = 256
ICO_SIZES: tuple[int, ...] = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
#: Each size is drawn on a canvas at least this large, then box-filtered down.
SUPERSAMPLE_PX = 1024

BACKGROUND_RADIUS = 14
#: Flat, like the rest of the app: depth comes from colour, never from gradients.
BACKGROUND = "#000000"

FRAME_COLOR = "#ffffff"
FRAME_STROKE = 4
#: The frame's stroke centre lines run along this square (left/top, right/bottom).
FRAME_EDGES = (10, 54)
#: How far each bracket reaches along an edge, from the frame's corner.
FRAME_ARM = 10
FRAME_CORNER_RADIUS = 4

LINE_STROKE = 4
LINE_X = 22
#: (y, length, colour) of the three text lines, top to bottom.
LINES: tuple[tuple[float, float, str], ...] = (
    (22, 20, "#c5b0f4"),
    (30, 12, "#dceeb1"),
    (38, 16, "#ff3d8b"),
)


def _brackets() -> list[tuple[float, float, int, int]]:
    """(corner x, corner y, x direction inwards, y direction inwards) of the four brackets."""
    low, high = FRAME_EDGES
    return [(low, low, 1, 1), (high, low, -1, 1), (high, high, -1, -1), (low, high, 1, -1)]


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _fmt(value: float) -> str:
    return f"{value:g}"


# ─── SVG ─────────────────────────────────────────────────────────────────────


def svg_markup() -> str:
    """The logo as a standalone SVG document (one line plus a final newline)."""
    radius = FRAME_CORNER_RADIUS
    bracket_paths = []
    for x, y, sx, sy in _brackets():
        sweep = 1 if sx * sy > 0 else 0
        bracket_paths.append(
            f"M{_fmt(x)} {_fmt(y + sy * FRAME_ARM)}V{_fmt(y + sy * radius)}"
            f"A{_fmt(radius)} {_fmt(radius)} 0 0 {sweep} {_fmt(x + sx * radius)} {_fmt(y)}"
            f"H{_fmt(x + sx * FRAME_ARM)}"
        )
    lines = "".join(
        f'<path d="M{_fmt(LINE_X)} {_fmt(y)}h{_fmt(length)}" stroke="{color}"/>'
        for y, length, color in LINES
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}">'
        f'<rect width="{GRID}" height="{GRID}" rx="{BACKGROUND_RADIUS}" fill="{BACKGROUND}"/>'
        f'<path d="{"".join(bracket_paths)}" fill="none" stroke="{FRAME_COLOR}" '
        f'stroke-width="{_fmt(FRAME_STROKE)}" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<g fill="none" stroke-width="{_fmt(LINE_STROKE)}" stroke-linecap="round">{lines}</g>'
        "</svg>\n"
    )


# ─── Bitmap ──────────────────────────────────────────────────────────────────


def _draw_at(canvas: int) -> Image.Image:
    """The artwork drawn on a `canvas`-pixel square, without antialiasing."""
    unit = canvas / GRID

    def px(value: float) -> int:
        return round(value * unit)

    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(
        (0, 0, canvas - 1, canvas - 1), radius=px(BACKGROUND_RADIUS), fill=BACKGROUND
    )

    def capsule(x0: float, y0: float, x1: float, y1: float, stroke: float, color: str) -> None:
        """A stroke with round caps along an axis-aligned segment."""
        half = stroke / 2
        draw.rounded_rectangle(
            (px(min(x0, x1) - half), px(min(y0, y1) - half),
             px(max(x0, x1) + half) - 1, px(max(y0, y1) + half) - 1),
            radius=px(half),
            fill=color,
        )  # fmt: skip

    radius = FRAME_CORNER_RADIUS
    half = FRAME_STROKE / 2
    quadrants = {(1, 1): 180, (-1, 1): 270, (-1, -1): 0, (1, -1): 90}
    for x, y, sx, sy in _brackets():
        capsule(x, y + sy * FRAME_ARM, x, y + sy * radius, FRAME_STROKE, FRAME_COLOR)
        capsule(x + sx * radius, y, x + sx * FRAME_ARM, y, FRAME_STROKE, FRAME_COLOR)
        cx, cy = x + sx * radius, y + sy * radius
        outer = radius + half
        start = quadrants[(sx, sy)]
        draw.arc(
            (px(cx - outer), px(cy - outer), px(cx + outer) - 1, px(cy + outer) - 1),
            start=start,
            end=start + 90,
            fill=FRAME_COLOR,
            width=px(FRAME_STROKE),
        )
    for y, length, color in LINES:
        capsule(LINE_X, y, LINE_X + length, y, LINE_STROKE, color)
    return image


def draw_master(size: int = MASTER_SIZE) -> Image.Image:
    """The icon artwork as a square RGBA image of `size` pixels (antialiased)."""
    factor = max(1, math.ceil(SUPERSAMPLE_PX / size))
    return _draw_at(size * factor).resize((size, size), Image.Resampling.BOX)


def make_icon(path: Path, sizes: tuple[int, ...] = ICO_SIZES) -> Path:
    """Write a multi-resolution .ico (and nothing else) to `path`; returns `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(set(sizes))
    images = [draw_master(size) for size in ordered]
    images[-1].save(
        path,
        format="ICO",
        sizes=[(size, size) for size in ordered],
        append_images=images[:-1],
    )
    return path


def write_svg(path: Path) -> Path:
    """Write the SVG logo to `path` (UTF-8, LF); returns `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(svg_markup().encode("utf-8"))
    return path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        usage = "Cách dùng: python packaging/make_icon.py <đường dẫn .ico hoặc .svg>"
        print(usage, file=sys.stderr)
        return 2
    target = Path(argv[0])
    if target.suffix.lower() == ".svg":
        write_svg(target)
    else:
        make_icon(target)
    print(f"Đã tạo biểu tượng: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
