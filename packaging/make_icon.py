"""Draw the application icon of the Windows exe with Pillow (no third-party artwork).

A simple abstract mark: a dark rounded square holding a light "page" with three text lines and
a row of three colour swatches (a design reference document). Drawn at 1024 px and downsampled
to every size of the .ico.

    python packaging/make_icon.py build/gdm-ui/icon.ico
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

MASTER_SIZE = 1024
ICO_SIZES: tuple[int, ...] = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

BACKGROUND = (30, 33, 48, 255)
PAGE = (245, 246, 250, 255)
LINE = (148, 155, 178, 255)
SWATCHES: tuple[tuple[int, int, int, int], ...] = (
    (20, 184, 166, 255),
    (245, 158, 11, 255),
    (239, 68, 94, 255),
)


def draw_master(size: int = MASTER_SIZE) -> Image.Image:
    """The icon artwork as a square RGBA image of `size` pixels."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def scale(value: float) -> int:
        return round(value * size / MASTER_SIZE)

    draw.rounded_rectangle(
        (scale(32), scale(32), scale(992), scale(992)), radius=scale(220), fill=BACKGROUND
    )
    draw.rounded_rectangle(
        (scale(232), scale(168), scale(792), scale(856)), radius=scale(64), fill=PAGE
    )
    for index, width in enumerate((416, 336, 376)):
        top = scale(268 + index * 104)
        draw.rounded_rectangle(
            (scale(312), top, scale(312 + width), top + scale(52)), radius=scale(26), fill=LINE
        )
    for index, color in enumerate(SWATCHES):
        left = scale(312 + index * 144)
        draw.ellipse((left, scale(624), left + scale(112), scale(736)), fill=color)
    return image


def make_icon(path: Path, sizes: tuple[int, ...] = ICO_SIZES) -> Path:
    """Write a multi-resolution .ico (and nothing else) to `path`; returns `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    master = draw_master()
    master.save(path, format="ICO", sizes=[(size, size) for size in sizes])
    return path


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("Cách dùng: python packaging/make_icon.py <đường dẫn .ico>", file=sys.stderr)
        return 2
    target = make_icon(Path(argv[0]))
    print(f"Đã tạo biểu tượng: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
