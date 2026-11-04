"""The wallpaper: a quiet clock and one greeting line, then two columns that are
worth a glance: your Claude Code agents (who needs you, who is working, who is
done; click one to jump to its terminal) and your day (calendar and tasks)."""
import time
from dataclasses import dataclass

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


def greeting(name: str, hour: int) -> str:
    if 5 <= hour < 12:
        part = "morning"
    elif 12 <= hour < 17:
        part = "afternoon"
    elif 17 <= hour < 22:
        part = "evening"
    else:
        part = "night"
    return f"Good {part}, {name.title()}"


def _ago(ts: float, now: float) -> str:
    if not ts:
        return ""
    sec = max(0, int(now - ts))
    return "now" if sec < 60 else f"{sec // 60}m" if sec < 3600 else f"{sec // 3600}h" if sec < 86400 else f"{sec // 86400}d"


# markers the wallpaper font (DepartureMono) actually has
GROUPS = (("needs", "NEEDS YOU", "►", "alert"), ("work", "WORKING", "•", "accent"), ("done", "DONE", "√", "dim"))
TODAY_MARK = {"overdue": ("!", "alert"), "today": ("•", "accent"), "event": ("»", "text"), "": ("·", "dim")}


def build(state: dict, cols: int, rows: int, now: float | None = None,
          hover: tuple | None = None, say: str | None = None,
          agents: list | None = None) -> tuple[Grid, list[Region]]:
    now = now or time.time()
    lt = time.localtime(now)
    g = Grid(cols, rows)
    regions: list[Region] = []
    bf = bigfont()
    agents = agents if agents is not None else state.get("agents", [])
    today = state.get("today") or {}

    width = min(cols - 8, 168)
    gap = 6
    left_w = int(width * 0.55)
    right_w = width - left_w - gap
    x0 = (cols - width) // 2
    xr = x0 + left_w + gap

    body_h = max(len(agents) + 2 * 3 + 2, len(today.get("items", [])) + 4)
    block_h = bf.height + 4 + min(body_h, rows)
    y = max(3, (rows - block_h) // 2 - 3)

    clock = time.strftime("%H:%M", lt)
    bf.draw(g, (cols - bf.width(clock)) // 2, y, clock, "dim")
    y += bf.height + 2
    line = say or f"{time.strftime('%A %d %B', lt)}  ·  {greeting(state.get('user', ''), lt.tm_hour)}"
    g.text((cols - len(line)) // 2, y, line, "accent" if say else "dim")
    y += 3
    top = y

    # ── left: agents ──
    g.text(x0, y, "AGENTS", "dim")
    g.text(x0 + left_w - 16, y, "click to jump in", "line")
    y += 2
    if not agents:
        g.text(x0, y, "No Claude Code sessions running.", "dim", width=left_w)
        g.text(x0, y + 1, "Start one with `claude` in any terminal; it shows up here.", "dim", width=left_w)
    n = 0
    for key, title, mark, colour in GROUPS:
        group = [a for a in agents if a.get("status") == key]
        if not group:
            continue
        if y >= rows - 2:
            break
        g.text(x0, y, f"{title} ({len(group)})", colour if key != "done" else "dim")
        y += 1
        for a in group:
            if y >= rows - 2:
                break
            idx = agents.index(a)
            on = hover == ("agent", idx)
            when = _ago(a.get("since", 0), now)
            g.text(x0, y, "›" if on else mark, "accent" if on else colour)
            g.text(x0 + 2, y, a.get("name", "?"), "accent" if on else "text", width=20)
            g.text(x0 + 23, y, a.get("detail", ""), "text" if key == "needs" else "dim", width=left_w - 23 - 6)
            g.text(x0 + left_w - len(when), y, when, "dim")
            regions.append(Region(x0, y, x0 + left_w - 1, y, "agent", idx))
            y += 1
            n += 1
        y += 1

    # ── right: today ──
    y = top
    g.text(xr, y, "TODAY", "dim")
    y += 2
    items = today.get("items", [])
    if not today.get("configured"):
        for k, ln in enumerate(("Connect your day: in ~/.config/sotto/config.toml add",
                                "  [today]",
                                "  ics = [\"<your calendar's iCal link>\"]",
                                "or command = \"<script that prints JSON>\" (see README)")):
            g.text(xr, y + k, ln, "dim", width=right_w)
    elif not items:
        g.text(xr, y, "Nothing due. A clear day.", "dim", width=right_w)
    for k, it in enumerate(items):
        if y >= rows - 2:
            break
        mark, colour = TODAY_MARK.get(it.get("flag", ""), TODAY_MARK[""])
        g.text(xr, y, mark, colour)
        g.text(xr + 2, y, it.get("when", ""), "alert" if it.get("flag") == "overdue" else "dim", width=11)
        g.text(xr + 14, y, it.get("title", ""), "text", width=right_w - 14)
        y += 1
    footer = today.get("footer") or today.get("error") or ""
    if footer and y < rows - 2:
        g.text(xr, y + 1, footer, "accent" if today.get("footer") else "alert", width=right_w)
    return g, regions
