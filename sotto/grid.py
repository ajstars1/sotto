"""A character grid shared by both outputs: ANSI escape codes for the live
wallpaper in kitty, and a PNG for previews. A colour is a palette name or,
for the terminal card's programs, a "#rrggbb" string."""

PALETTE = {
    # fresco light palette, from the old ceiling-painting wallpaper
    "bg": (0xEC, 0xE2, 0xD0),        # parchment: the wallpaper itself
    "faint": (0xE2, 0xD6, 0xC1),
    "surface": (0xE2, 0xD4, 0xBD),
    "line": (0xCD, 0xB9, 0x9B),
    "dim": (0x8F, 0x77, 0x59),
    "text": (0x3D, 0x2C, 0x1F),      # deep umber text
    "accent": (0xA7, 0x78, 0x3F),    # ochre accent
    "alert": (0xA9, 0x53, 0x2F),     # terracotta
}


def rgb(color: str) -> tuple[int, int, int]:
    if color in PALETTE:
        return PALETTE[color]
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


class _SGR(dict):
    """Escape code per colour, built the first time a colour is used."""

    def __init__(self, code: int):
        super().__init__()
        self.code = code

    def __missing__(self, color: str) -> str:
        r, g, b = rgb(color)
        self[color] = f"\x1b[{self.code};2;{r};{g};{b}m"
        return self[color]


SGR_FG, SGR_BG = _SGR(38), _SGR(48)


def set_palette(colors: dict[str, str]) -> bool:
    """Switch the theme ({"bg": "#ece2d0", ...}). Returns True when anything changed."""
    new = {k: (int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16)) for k, v in colors.items() if k in PALETTE}
    if all(PALETTE[k] == v for k, v in new.items()):
        return False
    PALETTE.update(new)
    SGR_FG.clear()
    SGR_BG.clear()
    return True


def hex_of(name: str) -> str:
    r, g, b = rgb(name)
    return f"#{r:02x}{g:02x}{b:02x}"

BOX = {"tl": "┌", "tr": "┐", "bl": "└", "br": "┘", "h": "─", "v": "│"}


class Grid:
    __slots__ = ("cols", "rows", "ch", "fg", "bg")

    def __init__(self, cols: int, rows: int, bg: str = "bg"):
        self.cols, self.rows = cols, rows
        n = cols * rows
        self.ch = [" "] * n
        self.fg = ["text"] * n
        self.bg = [bg] * n

    def copy(self) -> "Grid":
        g = Grid.__new__(Grid)
        g.cols, g.rows = self.cols, self.rows
        g.ch, g.fg, g.bg = self.ch[:], self.fg[:], self.bg[:]
        return g

    def set(self, x: int, y: int, ch: str, fg: str | None = None, bg: str | None = None):
        if 0 <= x < self.cols and 0 <= y < self.rows:
            i = y * self.cols + x
            self.ch[i] = ch
            if fg:
                self.fg[i] = fg
            if bg:
                self.bg[i] = bg

    def text(self, x: int, y: int, s: str, fg: str = "text", bg: str | None = None, width: int | None = None) -> int:
        if width is not None and len(s) > width:
            s = s[: max(0, width - 1)] + "…"
        for k, c in enumerate(s):
            self.set(x + k, y, c, fg, bg)
        return len(s)

    def fill(self, x: int, y: int, w: int, h: int, ch: str = " ", fg: str | None = None, bg: str | None = None):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.set(xx, yy, ch, fg, bg)

    def box(self, x: int, y: int, w: int, h: int, fg: str = "line", bg: str | None = None):
        self.fill(x + 1, y + 1, w - 2, h - 2, " ", fg, bg)
        for xx in range(x + 1, x + w - 1):
            self.set(xx, y, BOX["h"], fg, bg)
            self.set(xx, y + h - 1, BOX["h"], fg, bg)
        for yy in range(y + 1, y + h - 1):
            self.set(x, yy, BOX["v"], fg, bg)
            self.set(x + w - 1, yy, BOX["v"], fg, bg)
        self.set(x, y, BOX["tl"], fg, bg)
        self.set(x + w - 1, y, BOX["tr"], fg, bg)
        self.set(x, y + h - 1, BOX["bl"], fg, bg)
        self.set(x + w - 1, y + h - 1, BOX["br"], fg, bg)


def ansi_diff(prev: Grid | None, cur: Grid, only=None) -> str:
    """Escape codes that turn `prev` into `cur` on screen, touching only cells
    that changed. `only` limits the check to a set of cell indices."""
    out = []
    last_fg = last_bg = None
    cursor = -1
    cols = cur.cols
    indices = range(len(cur.ch)) if only is None else sorted(only)
    for i in indices:
        c, f, b = cur.ch[i], cur.fg[i], cur.bg[i]
        if prev is not None and prev.ch[i] == c and prev.fg[i] == f and prev.bg[i] == b:
            continue
        if c == "":                      # right half of a wide character: drawn with its left half
            cursor = -1
            continue
        if cursor != i:
            y, x = divmod(i, cols)
            out.append(f"\x1b[{y + 1};{x + 1}H")
        if f != last_fg:
            out.append(SGR_FG[f])
            last_fg = f
        if b != last_bg:
            out.append(SGR_BG[b])
            last_bg = b
        out.append(c)
        cursor = i + 1 if (i + 1) % cols else -1
    if not out:
        return ""
    # synchronized update: kitty shows the frame all at once, no tearing
    return "\x1b[?2026h" + "".join(out) + "\x1b[?2026l"


def to_png(grid: Grid, path: str, font_path: str, cw: int = 7, chh: int = 14):
    """Render the grid like kitty would, for previews. Box and block characters
    are drawn as exact pixels, the way kitty draws them itself."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (grid.cols * cw, grid.rows * chh), PALETTE["bg"])
    d = ImageDraw.Draw(img)
    d.fontmode = "1"
    font = ImageFont.truetype(font_path, 11)
    hx, hy = cw // 2, chh // 2
    for i, c in enumerate(grid.ch):
        y, x = divmod(i, grid.cols)
        px, py = x * cw, y * chh
        fg, bg = rgb(grid.fg[i]), rgb(grid.bg[i])
        if grid.bg[i] != "bg":
            d.rectangle([px, py, px + cw - 1, py + chh - 1], fill=bg)
        if c in (" ", ""):
            continue
        if c == "█":
            d.rectangle([px, py, px + cw - 1, py + chh - 1], fill=fg)
        elif c == "▀":
            d.rectangle([px, py, px + cw - 1, py + hy - 1], fill=fg)
        elif c == "▄":
            d.rectangle([px, py + hy, px + cw - 1, py + chh - 1], fill=fg)
        elif c in "─┌┐└┘│":
            if c in "─┌└":
                d.line([px + (hx if c != "─" else 0), py + hy, px + cw - 1, py + hy], fill=fg)
            if c in "─┐┘":
                d.line([px, py + hy, px + (hx if c != "─" else cw - 1), py + hy], fill=fg)
            if c in "│┌┐":
                d.line([px + hx, py + (hy if c != "│" else 0), px + hx, py + chh - 1], fill=fg)
            if c in "│└┘":
                d.line([px + hx, py, px + hx, py + (hy if c != "│" else chh - 1)], fill=fg)
        else:
            d.text((px, py), c, font=font, fill=fg)
    img.save(path)
