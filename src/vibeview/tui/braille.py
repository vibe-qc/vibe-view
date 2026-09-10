"""Pixel buffer to terminal cells.

Two downsampling modes, both emitting 24-bit colour:

``braille``
    The Unicode braille block (U+2800) packs a 2x4 dot matrix into one
    character, so an 80x24 terminal becomes a 160x96 pixel canvas — eight
    times the geometric detail of a block-character grid. The cost is one
    colour per cell: the eight dots share the average colour of whatever
    they cover. Best for structure and isosurface geometry, where shape
    carries the information.

``half``
    The upper-half block (U+2580) splits a cell into two independently
    coloured pixels (foreground = top, background = bottom). Quarter the
    vertical resolution of braille but two true colours per cell, which
    reads better for plots and colour-coded maps.

Neither mode needs anything beyond a terminal that speaks truecolour SGR.
"""

from __future__ import annotations

import numpy as np

# Braille dot bit per (row, col) within the 2x4 cell. The historic dot
# numbering (1,2,3,7 down the left column; 4,5,6,8 down the right) is why
# the fourth row's bits are 0x40/0x80 rather than continuing the sequence.
_BRAILLE_BITS = np.array(
    [
        [0x01, 0x08],
        [0x02, 0x10],
        [0x04, 0x20],
        [0x40, 0x80],
    ],
    dtype=np.uint16,
)

_BRAILLE_BASE = 0x2800
_UPPER_HALF = 0x2580
_SPACE = 0x20

MODES = ("braille", "half")


def pixel_size(cols: int, rows: int, mode: str = "braille") -> tuple[int, int]:
    """Pixel-buffer dimensions backing a ``cols`` x ``rows`` character grid."""
    if mode == "half":
        return (max(1, cols), max(1, rows * 2))
    return (max(1, cols * 2), max(1, rows * 4))


def cell_size(width: int, height: int, mode: str = "braille") -> tuple[int, int]:
    """Inverse of :func:`pixel_size`."""
    if mode == "half":
        return (max(1, width), max(1, height // 2))
    return (max(1, width // 2), max(1, height // 4))


def to_cells(
    pixels: np.ndarray,
    covered: np.ndarray,
    mode: str = "braille",
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Downsample a pixel buffer to character cells.

    ``pixels`` is (H,W,3) uint8 and ``covered`` is (H,W) bool marking which
    pixels geometry actually wrote (as opposed to background). Returns
    ``(codepoints, fg, bg, filled)``:

    * ``codepoints`` (rows, cols) int — the character to print.
    * ``fg`` / ``bg`` (rows, cols, 3) uint8 — cell colours.
    * ``filled`` (rows, cols) bool — False where the cell is pure
      background, so a caller can skip emitting colour for empty space.
    """
    if mode == "half":
        return _to_half_cells(pixels)
    return _to_braille_cells(pixels, covered)


def _to_braille_cells(pixels, covered):
    height, width = covered.shape
    rows, cols = height // 4, width // 2
    if rows == 0 or cols == 0:
        empty_i = np.full((max(rows, 1), max(cols, 1)), _SPACE, dtype=np.int32)
        empty_c = np.zeros((*empty_i.shape, 3), dtype=np.uint8)
        return empty_i, empty_c, empty_c.copy(), np.zeros(empty_i.shape, dtype=bool)

    # (rows, 4, cols, 2) — the cell grid with its sub-cell axes intact.
    blocks = covered[: rows * 4, : cols * 2].reshape(rows, 4, cols, 2)
    rgb = pixels[: rows * 4, : cols * 2].reshape(rows, 4, cols, 2, 3).astype(np.uint32)

    bits = (blocks * _BRAILLE_BITS[None, :, None, :]).sum(axis=(1, 3))
    count = blocks.sum(axis=(1, 3))
    filled = count > 0

    total = (rgb * blocks[..., None]).sum(axis=(1, 3))
    mean = total / np.maximum(count, 1)[..., None]
    fg = np.zeros((rows, cols, 3), dtype=np.uint8)
    fg[filled] = np.clip(mean[filled], 0, 255).astype(np.uint8)

    codepoints = np.where(filled, _BRAILLE_BASE + bits, _SPACE).astype(np.int32)
    bg = np.zeros((rows, cols, 3), dtype=np.uint8)
    return codepoints, fg, bg, filled


def _to_half_cells(pixels):
    height, width = pixels.shape[:2]
    rows, cols = height // 2, width
    if rows == 0 or cols == 0:
        empty_i = np.full((max(rows, 1), max(cols, 1)), _SPACE, dtype=np.int32)
        empty_c = np.zeros((*empty_i.shape, 3), dtype=np.uint8)
        return empty_i, empty_c, empty_c.copy(), np.zeros(empty_i.shape, dtype=bool)

    block = pixels[: rows * 2, :cols].reshape(rows, 2, cols, 3)
    fg = block[:, 0].astype(np.uint8)
    bg = block[:, 1].astype(np.uint8)
    codepoints = np.full((rows, cols), _UPPER_HALF, dtype=np.int32)
    filled = np.ones((rows, cols), dtype=bool)
    return codepoints, fg, bg, filled


def to_ansi(
    pixels: np.ndarray,
    covered: np.ndarray,
    mode: str = "braille",
    background: tuple[int, int, int] | None = None,
) -> str:
    """Render a pixel buffer as an ANSI truecolour string, one line per row.

    Used by the non-interactive ``vibe-view show`` path; the Textual widget
    builds Rich segments from :func:`to_cells` instead. Adjacent cells
    sharing a style collapse into one escape sequence, which keeps a full
    frame to a few kilobytes rather than one sequence per character.
    """
    codepoints, fg, bg, filled = to_cells(pixels, covered, mode)
    rows, cols = codepoints.shape
    use_bg = mode == "half" or background is not None
    bg_default = background or (0, 0, 0)

    lines: list[str] = []
    for r in range(rows):
        parts: list[str] = []
        prev: tuple | None = None
        for c in range(cols):
            if not filled[r, c] and mode != "half":
                style: tuple = ("plain",)
                char = " "
            else:
                fr, fg_, fb = (int(v) for v in fg[r, c])
                if use_bg:
                    br, bgc, bb = (
                        (int(v) for v in bg[r, c]) if mode == "half" else bg_default
                    )
                    style = ("both", fr, fg_, fb, br, bgc, bb)
                else:
                    style = ("fg", fr, fg_, fb)
                char = chr(int(codepoints[r, c]))
            if style != prev:
                if style[0] == "plain":
                    parts.append("\x1b[0m")
                elif style[0] == "fg":
                    parts.append(f"\x1b[38;2;{style[1]};{style[2]};{style[3]}m")
                else:
                    parts.append(
                        f"\x1b[38;2;{style[1]};{style[2]};{style[3]}"
                        f";48;2;{style[4]};{style[5]};{style[6]}m"
                    )
                prev = style
            parts.append(char)
        parts.append("\x1b[0m")
        lines.append("".join(parts))
    return "\n".join(lines)


class CellGrid:
    """A character grid that mixes rasterized pixels with plain text.

    A braille cell carries one colour and no room for a caption, so anything
    with axis ticks, atom indices or a legend needs the two to coexist: the
    graph goes through :meth:`blit`, the labels through :meth:`text`, and
    whichever wrote a cell last owns it. This is also what the Textual
    widget renders from, so the interactive viewport and the one-shot dump
    are the same composition.
    """

    def __init__(self, cols: int, rows: int, background: tuple[int, int, int] = (0, 0, 0)) -> None:
        self.cols = max(1, int(cols))
        self.rows = max(1, int(rows))
        self.background = tuple(background)
        self.chars = np.full((self.rows, self.cols), " ", dtype="<U1")
        self.fg = np.zeros((self.rows, self.cols, 3), dtype=np.uint8)
        self.bg = np.tile(np.array(background, dtype=np.uint8), (self.rows, self.cols, 1))
        self.filled = np.zeros((self.rows, self.cols), dtype=bool)

    def blit(
        self,
        pixels: np.ndarray,
        covered: np.ndarray,
        row: int = 0,
        col: int = 0,
        mode: str = "braille",
    ) -> None:
        """Downsample a pixel buffer and paste it at ``(row, col)``."""
        codepoints, fg, bg, filled = to_cells(pixels, covered, mode)
        h, w = codepoints.shape
        r0, c0 = max(0, row), max(0, col)
        r1, c1 = min(self.rows, r0 + h), min(self.cols, c0 + w)
        if r1 <= r0 or c1 <= c0:
            return
        sub = (slice(0, r1 - r0), slice(0, c1 - c0))
        target = (slice(r0, r1), slice(c0, c1))
        keep = filled[sub] if mode != "half" else np.ones_like(filled[sub])
        chars = np.vectorize(chr)(codepoints[sub]) if codepoints[sub].size else codepoints[sub]

        dest_chars = self.chars[target]
        dest_chars[keep] = chars[keep]
        self.chars[target] = dest_chars

        dest_fg = self.fg[target]
        dest_fg[keep] = fg[sub][keep]
        self.fg[target] = dest_fg

        if mode == "half":
            dest_bg = self.bg[target]
            dest_bg[keep] = bg[sub][keep]
            self.bg[target] = dest_bg

        dest_filled = self.filled[target]
        dest_filled[keep] = True
        self.filled[target] = dest_filled

    def text(
        self,
        row: int,
        col: int,
        value: str,
        color: tuple[int, int, int] = (210, 214, 224),
        background: tuple[int, int, int] | None = None,
    ) -> None:
        """Write a string, clipped to the grid. Later writes win."""
        if row < 0 or row >= self.rows or not value:
            return
        for offset, char in enumerate(value):
            c = col + offset
            if c < 0:
                continue
            if c >= self.cols:
                break
            self.chars[row, c] = char
            self.fg[row, c] = color
            self.filled[row, c] = True
            if background is not None:
                self.bg[row, c] = background

    def text_right(self, row: int, col_end: int, value: str, color=(210, 214, 224)) -> None:
        """Right-align ``value`` so its last character lands on ``col_end``."""
        self.text(row, col_end - len(value) + 1, value, color)

    def row_runs(
        self, row: int
    ) -> list[tuple[str, tuple[int, int, int] | None, tuple[int, int, int]]]:
        """One row as ``(text, fg, bg)`` runs of constant style.

        Run-length compression matters here: a Textual repaint emits one
        styled segment per run, and a 200-column uncompressed row would be
        200 segments times 50 rows every frame.
        """
        runs: list[tuple[str, tuple[int, int, int] | None, tuple[int, int, int]]] = []
        chars: list[str] = []
        prev: tuple | None = None
        for c in range(self.cols):
            if not self.filled[row, c]:
                style = (None, self.background)
                char = " "
            else:
                style = (
                    (int(self.fg[row, c, 0]), int(self.fg[row, c, 1]), int(self.fg[row, c, 2])),
                    (int(self.bg[row, c, 0]), int(self.bg[row, c, 1]), int(self.bg[row, c, 2])),
                )
                char = str(self.chars[row, c])
            if style != prev:
                if chars and prev is not None:
                    runs.append(("".join(chars), prev[0], prev[1]))
                chars = [char]
                prev = style
            else:
                chars.append(char)
        if chars and prev is not None:
            runs.append(("".join(chars), prev[0], prev[1]))
        return runs

    def to_ansi(self) -> str:
        """Render as ANSI truecolour lines."""
        lines: list[str] = []
        for r in range(self.rows):
            parts: list[str] = []
            prev: tuple | None = None
            for c in range(self.cols):
                if not self.filled[r, c]:
                    style: tuple = ()
                    char = " "
                else:
                    style = (
                        int(self.fg[r, c, 0]),
                        int(self.fg[r, c, 1]),
                        int(self.fg[r, c, 2]),
                        int(self.bg[r, c, 0]),
                        int(self.bg[r, c, 1]),
                        int(self.bg[r, c, 2]),
                    )
                    char = str(self.chars[r, c])
                if style != prev:
                    if not style:
                        parts.append("\x1b[0m")
                    else:
                        parts.append(
                            f"\x1b[38;2;{style[0]};{style[1]};{style[2]}"
                            f";48;2;{style[3]};{style[4]};{style[5]}m"
                        )
                    prev = style
                parts.append(char)
            parts.append("\x1b[0m")
            lines.append("".join(parts))
        return "\n".join(lines)

    def to_plain(self) -> str:
        """Colourless render — for tests and pipes."""
        return "\n".join(
            "".join(str(self.chars[r, c]) for c in range(self.cols)).rstrip()
            for r in range(self.rows)
        )


def to_plain(pixels: np.ndarray, covered: np.ndarray, mode: str = "braille") -> str:
    """Colourless render — for pipes, CI logs, and doctest-style assertions."""
    codepoints, _fg, _bg, filled = to_cells(pixels, covered, mode)
    rows, cols = codepoints.shape
    lines = []
    for r in range(rows):
        lines.append(
            "".join(
                chr(int(codepoints[r, c])) if (filled[r, c] or mode == "half") else " "
                for c in range(cols)
            ).rstrip()
        )
    return "\n".join(lines)
