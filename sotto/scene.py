"""The wallpaper: a quiet clock and one greeting line, then two columns of blocks.

The built-in blocks are your Claude Code agents (who needs you, who is working, who is done;
click one to jump to its terminal) and your day (calendar and tasks). Your own blocks come from
[[widget]] in config.toml, and [layout] says which block goes in which column.
"""
import time
from dataclasses import dataclass

from . import config as configmod
from .art import BigFont
from .grid import Grid

LIST_W = 84          # width of the centred column, in cells


@dataclass
class Region:
    x0: int
    y0: int
    x1: int
    y1: int
    kind: str
    index: int

    def hit(self, x: int, y: int) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


_bigfont = None


def bigfont() -> BigFont:
    global _bigfont
    if _bigfont is None:
        _bigfont = BigFont()
    return _bigfont


def day_part(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 22:
        return "evening"
    return "night"


def greeting(name: str, hour: int, template: str = "Good {part}, {name}", date: str = "") -> str:
    try:
        return template.format(part=day_part(hour), name=name.title(), date=date)
    except (KeyError, IndexError, ValueError):
        return template


def _ago(ts: float, now: float) -> str:
    if not ts:
        return ""
    sec = max(0, int(now - ts))
    return "now" if sec < 60 else f"{sec // 60}m" if sec < 3600 else f"{sec // 3600}h" if sec < 86400 else f"{sec // 86400}d"


# markers the wallpaper font (DepartureMono) actually has
GROUPS = (("needs", "NEEDS YOU", "►", "alert"), ("work", "WORKING", "•", "accent"), ("done", "DONE", "√", "dim"))
TODAY_MARK = {"overdue": ("!", "alert"), "today": ("•", "accent"), "event": ("»", "text"), "": ("·", "dim")}


def _colour(c: str, default: str) -> str:
    return c if c in configmod.COLOR_KEYS or (len(c) == 7 and c.startswith("#")) else default


class _Ctx:
    def __init__(self, g, regions, rows, now, hover, agents, today, widget_rows, widgets):
        self.g, self.regions, self.rows, self.now, self.hover = g, regions, rows, now, hover
        self.agents, self.today, self.widget_rows = agents, today, widget_rows
        self.widgets = {w["id"]: w for w in widgets}


def _agents_block(c: _Ctx, x: int, y: int, w: int, draw: bool = True) -> int:
    g, rows = c.g, c.rows
    if draw:
        g.text(x, y, "AGENTS", "dim")
        g.text(x + w - 16, y, "click to jump in", "line")
    y += 2
    if not c.agents:
        if draw:
            g.text(x, y, "No Claude Code sessions running.", "dim", width=w)
            g.text(x, y + 1, "Start one with `claude` in any terminal; it shows up here.", "dim", width=w)
        return y + 3
    for key, title, mark, colour in GROUPS:
        group = [a for a in c.agents if a.get("status") == key]
        if not group:
            continue
        if y >= rows - 2:
            break
        if draw:
            g.text(x, y, f"{title} ({len(group)})", colour if key != "done" else "dim")
        y += 1
        for a in group:
            if y >= rows - 2:
                break
            if draw:
                idx = c.agents.index(a)
                on = c.hover == ("agent", idx)
                when = _ago(a.get("since", 0), c.now)
                g.text(x, y, "›" if on else mark, "accent" if on else colour)
                g.text(x + 2, y, a.get("name", "?"), "accent" if on else "text", width=20)
                g.text(x + 23, y, a.get("detail", ""), "text" if key == "needs" else "dim", width=w - 23 - 6)
                g.text(x + w - len(when), y, when, "dim")
                c.regions.append(Region(x, y, x + w - 1, y, "agent", idx))
            y += 1
        y += 1
    return y


def _today_block(c: _Ctx, x: int, y: int, w: int, draw: bool = True) -> int:
    g, rows, today = c.g, c.rows, c.today
    if draw:
        g.text(x, y, "TODAY", "dim")
    y += 2
    items = today.get("items", [])
    if not today.get("configured"):
        hint = ("Connect your day: in ~/.config/sotto/config.toml add",
                "  [today]",
                "  ics = [\"<your calendar's iCal link>\"]",
                "or command = \"<script that prints JSON>\" (see README)")
        if draw:
            for k, ln in enumerate(hint):
                g.text(x, y + k, ln, "dim", width=w)
        return y + len(hint) + 1
    if not items:
        if draw:
            g.text(x, y, "Nothing due. A clear day.", "dim", width=w)
        y += 1
    for it in items:
        if y >= rows - 2:
            break
        if draw:
            mark, colour = TODAY_MARK.get(it.get("flag", ""), TODAY_MARK[""])
            g.text(x, y, mark, colour)
            g.text(x + 2, y, it.get("when", ""), "alert" if it.get("flag") == "overdue" else "dim", width=11)
            g.text(x + 14, y, it.get("title", ""), "text", width=w - 14)
        y += 1
    footer = today.get("footer") or today.get("error") or ""
    if footer and y < rows - 2:
        if draw:
            g.text(x, y + 1, footer, "accent" if today.get("footer") else "alert", width=w)
        y += 2
    return y + 1


def _widget_block(c: _Ctx, wid: str, x: int, y: int, w: int, draw: bool = True) -> int:
    spec = c.widgets[wid]
    rows = c.widget_rows.get(wid)
    if draw and spec.get("title"):
        c.g.text(x, y, spec["title"], "dim", width=w)
    y += 2 if spec.get("title") else 0
    if rows is None:
        rows = [("…", "line")]
    for text, colour in rows:
        if y >= c.rows - 2:
            break
        if draw:
            c.g.text(x, y, text, _colour(colour, _colour(spec.get("color", "text"), "text")), width=w)
        y += 1
    return y + 1


def _column(c: _Ctx, blocks: list[str], x: int, y: int, w: int, draw: bool = True) -> int:
    for b in blocks:
        if y >= c.rows - 2:
            break
        if b == "agents":
            y = _agents_block(c, x, y, w, draw)
        elif b == "today":
            y = _today_block(c, x, y, w, draw)
        elif b in c.widgets:
            y = _widget_block(c, b, x, y, w, draw)
    return y


def build(state: dict, cols: int, rows: int, now: float | None = None,
          hover: tuple | None = None, say: str | None = None,
          agents: list | None = None, settings: dict | None = None,
          widget_rows: dict | None = None) -> tuple[Grid, list[Region]]:
    now = now or time.time()
    lt = time.localtime(now)
    s = settings or configmod.load()
    g = Grid(cols, rows)
    regions: list[Region] = []
    bf = bigfont()
    agents = agents if agents is not None else state.get("agents", [])
    c = _Ctx(g, regions, rows, now, hover, agents, state.get("today") or {}, widget_rows or {}, s["widgets"])
    left, right = s["layout"]["left"], s["layout"]["right"]

    width = min(cols - 8, 168)
    gap = 6
    if left and right:
        left_w = int(width * 0.55)
        right_w = width - left_w - gap
    else:                                         # one column: centred, narrower
        left_w = right_w = min(width, 96)
    x0 = (cols - (width if left and right else left_w)) // 2
    xr = x0 + left_w + gap if left and right else x0

    body_h = max(_column(c, left, 0, 0, left_w, draw=False), _column(c, right, 0, 0, right_w, draw=False)) if (left or right) else 0
    show_clock, show_greet = s["clock"]["show"], s["greeting"]["show"] or bool(say)
    head_h = (bf.height + 2 if show_clock else 0) + (3 if show_greet else 0)
    block_h = head_h + min(body_h, rows)
    y = max(3, (rows - block_h) // 2 - 3)

    if show_clock:
        try:
            clock = time.strftime(s["clock"]["format"], lt)
        except ValueError:
            clock = time.strftime("%H:%M", lt)
        clock = clock.lstrip("0") if s["clock"]["format"] == "%I:%M" else clock
        bf.draw(g, (cols - bf.width(clock)) // 2, y, clock, "dim")
        y += bf.height + 2
    if show_greet:
        try:
            date = time.strftime(s["greeting"]["date"], lt) if s["greeting"]["date"] else ""
        except ValueError:
            date = ""
        hello = greeting(state.get("user", ""), lt.tm_hour, s["greeting"]["text"], date)
        line = say or (f"{date}  ·  {hello}" if date and "{date}" not in s["greeting"]["text"] else hello)
        g.text((cols - len(line)) // 2, y, line, "accent" if say else "dim")
        y += 3
    top = y
    if left and right:
        _column(c, left, x0, top, left_w)
        _column(c, right, xr, top, right_w)
    elif left or right:
        _column(c, left or right, x0, top, left_w)
    return g, regions
