#!/usr/bin/env python3
"""Generate the vibe-view app icon (icon.png + icon.icns).

A simple molecule glyph — three bonded atoms in the vibe-view palette
(#7c8aff accent on the #1a1a2e viewer background) — drawn with Pillow.
Run from this directory:

    python3 make-icon.py

Outputs:
    icon.png   (512x512, window/tray/dock icon in dev mode)
    icon.icns  (macOS bundle icon, applied by install-electron.py)
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).parent

BG = (26, 26, 46, 255)  # #1a1a2e — viewer background
ACCENT = (124, 138, 255, 255)  # #7c8aff — vibe-view accent
ATOM_LIGHT = (224, 224, 224, 255)  # #e0e0e0 — text color
BOND = (90, 100, 180, 255)


def draw_icon(size: int) -> Image.Image:
    """Molecule glyph on a rounded-square tile at the given pixel size."""
    s = size / 512.0  # design coordinates are on a 512 grid
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Rounded-square tile
    margin = 32 * s
    d.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=96 * s,
        fill=BG,
    )

    # Bent triatomic (water-like) molecule: one accent-colored central
    # atom, two light outer atoms, thick bonds.
    center = (256 * s, 216 * s)
    left = (150 * s, 340 * s)
    right = (362 * s, 340 * s)

    for other in (left, right):
        d.line([center, other], fill=BOND, width=int(28 * s))

    def atom(pos, r, color):
        x, y = pos
        d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    atom(center, 78 * s, ACCENT)
    atom(left, 52 * s, ATOM_LIGHT)
    atom(right, 52 * s, ATOM_LIGHT)

    return img


def main() -> None:
    png = draw_icon(512)
    png.save(HERE / "icon.png")
    print(f"Wrote {HERE / 'icon.png'}")

    # Pillow writes .icns directly from a large master image.
    icns = draw_icon(1024)
    icns.save(HERE / "icon.icns", format="ICNS")
    print(f"Wrote {HERE / 'icon.icns'}")


if __name__ == "__main__":
    main()
