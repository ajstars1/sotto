"""Pixel art: a big headline font cut from DepartureMono itself, and the
mascot, a pixel puppy wearing a sensor collar.

Each art pixel becomes half a terminal cell (▀ ▄ █). A 7x14 cell is then two
7x7 squares, so the art keeps square pixels on screen.
"""
import os
from pathlib import Path

FONT_CANDIDATES = [
    Path.home() / ".local/share/fonts/DepartureMonoNerdFont-Regular.otf",
    Path.home() / ".fonts/DepartureMonoNerdFont-Regular.otf",
    Path("/usr/local/share/fonts/sotto/DepartureMonoNerdFont-Regular.otf"),   # for the login screen
    Path(__file__).resolve().parent / "fonts" / "DepartureMono-Regular.otf",  # bundled (MIT): macOS, Windows
]

SPRITE_COLORS = {"o": "line", "a": "text", "h": "accent", "d": "dim", "k": "bg", "w": "accent", "t": "alert", "c": "dim", "p": "accent"}


def font_path() -> str:
    for p in FONT_CANDIDATES:
        if p.exists():
            return str(p)
    raise FileNotFoundError("DepartureMono Nerd Font not found")


class BigFont:
    """DepartureMono's own 11px glyphs, blown up so every font pixel is a 7x7 block."""

    SAMPLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789,.?!'-:"

    def __init__(self, path: str | None = None):
        from PIL import Image, ImageDraw, ImageFont

        self._font = ImageFont.truetype(path or font_path(), 11)
        self._Image, self._Draw = Image, ImageDraw
        self._cache: dict[str, list[str]] = {}
        rows = [r for ch in self.SAMPLE for r, line in enumerate(self._raster(ch)) if "#" in line]
        top, bottom = min(rows), max(rows)
        if (bottom - top + 1) % 2:
            bottom += 1
        self.band = (top, bottom)
        self.height = (bottom - top + 1) // 2      # in terminal rows
        self.advance = 7                           # in terminal columns

    def _raster(self, ch: str) -> list[str]:
        img = self._Image.new("L", (7, 14), 0)
        d = self._Draw.Draw(img)
        d.fontmode = "1"
        d.text((0, 0), ch, font=self._font, fill=255)
        px = img.load()
        return ["".join("#" if px[x, y] > 127 else "." for x in range(7)) for y in range(14)]

    def glyph(self, ch: str) -> list[str]:
        if ch not in self._cache:
            top, bottom = self.band
            self._cache[ch] = self._raster(ch)[top:bottom + 1]
        return self._cache[ch]

    def width(self, text: str) -> int:
        return len(text) * self.advance

    def draw(self, grid, x: int, y: int, text: str, fg: str = "text") -> list[int]:
        """Draw text; returns the indices of the cells it lit (handy for effects)."""
        lit = []
        for k, ch in enumerate(text):
            g = self.glyph(ch)
            for r in range(0, len(g), 2):
                top_row, bot_row = g[r], g[r + 1] if r + 1 < len(g) else "." * 7
                for c in range(7):
                    t, b = top_row[c] == "#", bot_row[c] == "#"
                    if not (t or b):
                        continue
                    cx, cy = x + k * self.advance + c, y + r // 2
                    grid.set(cx, cy, "█" if t and b else ("▀" if t else "▄"), fg)
                    if 0 <= cx < grid.cols and 0 <= cy < grid.rows:
                        lit.append(cy * grid.cols + cx)
        return lit


def pup(eyes: str = "open", mouth: str = "smile") -> list[str]:
    """The mascot, built from ellipses so it stays symmetric and clean.
    eyes: open | blink | happy | worried | sleep    mouth: smile | talk | flat"""
    W, H = 32, 28
    g = [["."] * W for _ in range(H)]

    def ell(cx, cy, rx, ry, ch):
        for y in range(H):
            for x in range(W):
                if ((x + 0.5 - cx) / rx) ** 2 + ((y + 0.5 - cy) / ry) ** 2 <= 1:
                    g[y][x] = ch

    ell(5.5, 12.5, 4.6, 8.6, "d")      # floppy ears, darker
    ell(26.5, 12.5, 4.6, 8.6, "d")
    ell(16.0, 13.0, 11.0, 10.5, "a")   # head
    ell(16.0, 24.0, 7.5, 3.6, "a")     # chest
    ell(16.0, 18.2, 6.6, 4.3, "h")     # muzzle

    for y in (22, 23):                 # the collar...
        for x in range(W):
            if g[y][x] in "ah":
                g[y][x] = "c"
    for y in (22, 23):                 # ...and its glowing sensor pod
        for x in (15, 16):
            g[y][x] = "p"

    out = [row[:] for row in g]        # dark outline wherever a shape meets empty space
    for y in range(H):
        for x in range(W):
            if g[y][x] == ".":
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < W and 0 <= ny < H) or g[ny][nx] == ".":
                    out[y][x] = "o"
                    break
    g = out

    def px(x, y, ch):
        g[y][x] = ch

    if eyes in ("open", "worried"):
        for ex in (11, 19):
            for y in (10, 11, 12):
                for x in (ex, ex + 1):
                    px(x, y, "k")
            px(ex, 10, "w")
        if eyes == "worried":          # brows: / \
            for x, y in ((10, 9), (11, 8), (12, 8), (13, 7)):
                px(x, y, "o")
            for x, y in ((21, 9), (20, 8), (19, 8), (18, 7)):
                px(x, y, "o")
    elif eyes == "happy":              # ^ ^
        for ex in (10, 18):
            for x, y in ((ex, 12), (ex + 1, 11), (ex + 2, 11), (ex + 3, 12)):
                px(x, y, "k")
    else:                              # blink / sleep: closed lines
        for ex in (10, 18):
            for x in range(ex, ex + 4):
                px(x, 12 if eyes == "sleep" else 11, "k")

    for x in (14, 15, 16, 17):         # nose
        px(x, 15, "k")
    px(15, 16, "k")
    px(16, 16, "k")
    px(15, 15, "w")

    if mouth == "talk":
        for x in (14, 15, 16, 17):
            px(x, 18, "k")
        for x in (14, 17):
            px(x, 19, "k")
        px(15, 19, "t")
        px(16, 19, "t")
    elif mouth == "flat":
        for x in (14, 15, 16, 17):
            px(x, 19, "k")
    else:
        for x, y in ((12, 18), (13, 19), (14, 19), (17, 19), (18, 19), (19, 18)):
            px(x, y, "k")
        px(15, 18, "k")
        px(16, 18, "k")
        px(15, 20, "t")
        px(16, 20, "t")
    return ["".join(r) for r in g]


def draw_sprite(grid, x: int, y: int, sprite: list[str]) -> list[int]:
    """Two sprite rows per terminal row; transparent pixels keep what's underneath."""
    lit = []
    for r in range(0, len(sprite), 2):
        top = sprite[r]
        bot = sprite[r + 1] if r + 1 < len(sprite) else "." * len(top)
        for c in range(len(top)):
            t, b = top[c], bot[c]
            if t == "." and b == ".":
                continue
            cx, cy = x + c, y + r // 2
            if not (0 <= cx < grid.cols and 0 <= cy < grid.rows):
                continue
            if t == "." or b == ".":
                ch = "▀" if b == "." else "▄"
                grid.set(cx, cy, ch, SPRITE_COLORS[t if b == "." else b])
            elif SPRITE_COLORS[t] == SPRITE_COLORS[b]:
                grid.set(cx, cy, "█", SPRITE_COLORS[t])
            else:
                grid.set(cx, cy, "▀", SPRITE_COLORS[t], SPRITE_COLORS[b])
            lit.append(cy * grid.cols + cx)
    return lit
