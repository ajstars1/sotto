"""The live wallpaper. Runs inside `kitty +kitten panel --edge=background`.

Always on screen: a quiet clock, one greeting line and your recent projects
on light fresco parchment. Move the mouse over empty space and a faint trail
of dots follows it and fades, and whatever it touches (the clock, the date,
the terminal's text) dissolves and re-forms. Click a project to open it; right-click
anywhere for the desktop menu. `python -m sotto say "..."` shows a message
in place of the greeting for a few seconds.

/terminal in the wallpaper prompt opens a shell inside the wallpaper itself
(sotto/term.py). While you use it the wallpaper holds the keyboard, through
kitty's remote control; a click anywhere else gives it back.
"""
import json
import os
import random
import re
import select
import signal
import socket
import struct
import sys
import threading
import time
import traceback

from . import agents as agentboard
from . import collect
from . import config as configmod
from . import plat
from . import profile as prof
from . import widgets as widgetmod
from .grid import PALETTE, Grid, ansi_diff, set_palette
from .scene import build

if plat.OS != "windows":
    import fcntl
    import termios
    import tty

FPS = 30
REFRESH_EVERY = 300            # re-read projects/system this often (seconds)
AGENTS_EVERY = 3               # re-check Claude Code sessions this often (cheap: unchanged files are not re-read)
SAY_SECONDS = 8                # a `say` message stays this long
RUNTIME = plat.runtime_dir()
PIDFILE = os.path.join(RUNTIME, "sotto-wall.pid")
SOCKET = os.path.join(RUNTIME, "sotto-wall.sock")        # commands for the wall (from the dock)
DOCK_SOCKET = os.path.join(RUNTIME, "sotto.sock")        # the GTK dock, on Linux
LOG = collect.CACHE / "wall.log"

MOUSE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
HALF_MOUSE = re.compile(r"\x1b\[<[\d;]*$")            # a mouse report cut in two by a read
DOTS = "·•∙"
MATRIX = "01"
# a trail dot ages through these colours, then disappears
TRAIL_COLORS = ["accent", "dim", "line", "faint"]
SHADES = "░▒▓"                 # a touched clock pixel dissolves to these, then re-forms
SCATTER = "·•∙01"              # a touched letter flickers through these, then settles


def clear_code() -> str:
    r, g, b = PALETTE["bg"]
    return f"\x1b[48;2;{r};{g};{b}m\x1b[2J"


class TtyIO:
    """The wall in a terminal: kitty's background panel on Linux and macOS."""

    kind = "tty"

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.out = sys.stdout.buffer
        self._old = None

    def fileno(self) -> int:
        return self.fd

    def read(self) -> bytes:
        return os.read(self.fd, 4096)

    def write(self, b: bytes):
        self.out.write(b)
        self.out.flush()

    def size(self) -> tuple[int, int]:
        s = os.get_terminal_size()
        return s.columns, s.lines

    def cell_px(self) -> tuple[int, int]:
        """Pixel size of one character cell, from the terminal (falls back to 7x14)."""
        try:
            rows, cols, xp, yp = struct.unpack("HHHH", fcntl.ioctl(self.fd, termios.TIOCGWINSZ, b"\0" * 8))
            if xp and yp and cols and rows:
                return xp // cols, yp // rows
        except OSError:
            pass
        return 7, 14

    def focus(self, on: bool):
        """Let the wallpaper's kitty take the keyboard (or give it back), through kitty's remote control."""
        to = os.environ.get("KITTY_LISTEN_ON")
        if not to:
            return
        plat.spawn(["kitten", "@", "--to", to, "resize-os-window", "--self", "--action=os-panel",
                    "--incremental", f"focus-policy={'exclusive' if on else 'not-allowed'}"])

    def setup(self, wall):
        self._old = termios.tcgetattr(self.fd)
        signal.signal(signal.SIGWINCH, lambda *_: wall.flags.add("resize"))
        tty.setcbreak(self.fd)

    def restore(self):
        if self._old is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self._old)


class Prompt:
    """The double-click prompt drawn on the wallpaper itself, where there is no GTK dock
    (macOS, Windows, or Linux without sotto's dock). Type, Enter, and the answer types itself out."""

    WIDTH = 64

    def __init__(self, x: int, y: int, cols: int, rows: int):
        self.x = max(1, min(x, cols - self.WIDTH - 2))
        self.y = max(1, min(y, rows - 8))
        self.text = ""
        self.answer = ""
        self.answer_t0 = 0.0
        self.busy = False
        self.focused = True
        self.action = None

    def lines(self) -> list[str]:
        words, out, cur = self.answer.split(), [], ""
        for w in words:
            if len(cur) + len(w) + 1 > self.WIDTH:
                out.append(cur)
                cur = w
            else:
                cur = f"{cur} {w}".strip()
        if cur:
            out.append(cur)
        return out[:6]

    def revealing(self, now: float) -> bool:
        return bool(self.answer) and (now - self.answer_t0) * 90 < len(self.answer)

    def cells(self, cols: int, rows: int) -> set[int]:
        out = set()
        for yy in range(self.y, min(rows, self.y + 8)):
            for xx in range(self.x - 1, min(cols, self.x + self.WIDTH + 2)):
                if xx >= 0:
                    out.add(yy * cols + xx)
        return out

    def hit(self, x: int, y: int) -> bool:
        return self.y <= y < self.y + 2 + len(self.lines()) and self.x - 1 <= x <= self.x + self.WIDTH

    def draw(self, g: Grid, now: float):
        for yy in range(self.y, self.y + 2 + len(self.lines())):
            for xx in range(self.x - 1, self.x + self.WIDTH + 1):
                g.set(xx, yy, " ", "text", "bg")
        shown = self.text[-(self.WIDTH - 3):]
        g.text(self.x, self.y, "›", "accent" if self.focused else "dim")
        g.text(self.x + 2, self.y, shown or ("ask anything, or /terminal" if self.focused else ""), "text" if shown else "line")
        if self.focused and not self.busy:
            g.set(self.x + 2 + len(shown), self.y, "▏", "accent")
        if self.busy:
            g.text(self.x + 2, self.y + 1, "·" * (1 + int(now * 3) % 3), "dim")
            return
        budget = int((now - self.answer_t0) * 90)
        for k, ln in enumerate(self.lines()):
            if budget <= 0:
                break
            g.text(self.x + 2, self.y + 1 + k, ln[:budget], "dim")
            budget -= len(ln) + 1


class Fade:
    """Home screen in/out: each changed cell flickers as a dot, then settles."""

    def __init__(self, src: Grid, dst: Grid, rng: random.Random, speed: float = 1.0):
        self.dst = dst
        self.rng = rng
        self.plan: dict[int, tuple[float, float]] = {}
        cols = dst.cols
        for i in range(len(dst.ch)):
            if src.ch[i] == dst.ch[i] and src.fg[i] == dst.fg[i] and src.bg[i] == dst.bg[i]:
                continue
            t0 = ((i % cols) / cols * 0.2 + rng.random() * 0.25) * speed
            self.plan[i] = (t0, t0 + (0.10 + rng.random() * 0.20) * speed)
        self.end = max((p[1] for p in self.plan.values()), default=0.0)

    def apply(self, t: float, out: Grid, src: Grid):
        rnd = self.rng.random
        for i, (t0, t1) in self.plan.items():
            if t < t0:
                out.ch[i], out.fg[i], out.bg[i] = src.ch[i], src.fg[i], src.bg[i]
            elif t < t1:
                out.ch[i], out.fg[i], out.bg[i] = DOTS[int(rnd() * 3)], "line" if rnd() < 0.6 else "dim", "bg"
            else:
                out.ch[i], out.fg[i], out.bg[i] = self.dst.ch[i], self.dst.fg[i], self.dst.bg[i]


class Wall:
    def __init__(self, io=None):
        self.io = io or TtyIO()
        self.flags: set[str] = set()
        self.rng = random.Random()
        self.config_mtime = configmod.mtime()
        self.settings = configmod.load()
        set_palette(self.settings["palette"])
        self.widgets = widgetmod.Runner(lambda: self.flags.add("widgets"))
        self.cols, self.rows = self.size()
        self.base = Grid(self.cols, self.rows)     # what sits under the trail
        self.shown = self.base.copy()              # what is on screen
        self.visible = False
        self.fade: Fade | None = None
        self.fade_src: Grid | None = None
        self.fade_t0 = 0.0
        self.trail: dict[int, tuple[str, float, float]] = {}   # cell -> (char, born, life)
        self.scatter: dict[int, tuple[float, float, int]] = {}  # touched cell -> (born, life, seed)
        self.state = self.load_state()
        self.state_mtime = self.mtime(collect.STATE)
        self.inbox_mtime = self.mtime(collect.INBOX)
        self.regions = []
        self.hover: tuple | None = None                # (kind, index) of the row under the mouse
        self.agents: list[dict] = []
        self.agents_sig: tuple = ()
        self.next_agents = 0.0
        self.say_text: str | None = None
        self.say_until = 0.0
        self.hide_at = 0.0
        self.minute = -1
        self.collecting = False
        self.last_cell = (-1, -1)
        self.last_click = None
        self.next_refresh = 0.0
        self.term = None                           # the shell card, when open
        self.term_focused = False
        self.term_dirty = False
        self.term_drawn = 0.0
        self.prompt_drawn = 0.0
        self.pending_answer = None
        self.next_config = 0.0
        self.drag = None                           # ("move" | "size", dx, dy) while dragging the card
        self.outer_modes = (False, False)          # cursor-key and paste modes mirrored to kitty
        self.sock = None
        self.prof: prof.Profile | None = None      # a frame recording, while `sotto profile` runs
        self.fade_cause = "show"
        self.prompt: Prompt | None = None          # the in-wallpaper prompt, where there is no dock
        self.prompt_dirty = False

    # ── plumbing ──────────────────────────────────────────────────────────
    def size(self):
        return self.io.size()

    @staticmethod
    def mtime(path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    def load_state(self) -> dict:
        try:
            return json.loads(collect.STATE.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _out(self, s: str) -> int:
        if not s:
            return 0
        b = s.encode()
        self.io.write(b)
        return len(b)

    def write(self, s: str):
        """Escape codes outside a frame (modes, clearing the screen)."""
        n = self._out(s)
        if n and self.prof is not None:
            self.prof.frame("control", 0, n, 0.0, 0.0)

    def refresh_async(self):
        if self.collecting or os.environ.get("SOTTO_DEMO"):
            return                            # demo mode: show the given state.json as is
        self.collecting = True

        def work():
            try:
                collect.write(collect.collect())
            except Exception:
                log_error()
            finally:
                self.collecting = False

        threading.Thread(target=work, daemon=True).start()

    # ── frames ────────────────────────────────────────────────────────────
    def home(self) -> Grid:
        say = self.say_text if time.time() < self.say_until else None
        live = None if os.environ.get("SOTTO_DEMO") else self.agents
        g, self.regions = build(self.state, self.cols, self.rows, time.time(), self.hover, say, live,
                                settings=self.settings, widget_rows=self.widgets.snapshot())
        return g

    def reload_config(self):
        """config.toml was saved: new theme, layout, widgets and effects, without a restart."""
        self.settings = configmod.load()
        self.widgets.configure(self.settings["widgets"])
        if set_palette(self.settings["palette"]):
            if hasattr(self.io, "theme"):
                self.io.theme(self.settings["palette"]["bg"])
            self.shown = Grid(self.cols, self.rows)      # every colour changed: repaint it all
            self.fade = None
            self.write(clear_code())
            self.set_base(self.home(), animate=False, cause="config")
        elif self.visible:
            self.set_base(self.home(), animate=True, speed=0.6, cause="config")

    def compose(self) -> Grid:
        """The layers, back to front: the home screen, the mouse trail, the terminal's
        text (the wallpaper fades just around it), and what the mouse disturbed."""
        g = self.base.copy()
        now = time.time()
        if self.term:
            self.term.recede(g)
        for i, (ch, born, life) in self.trail.items():
            k = min(len(TRAIL_COLORS) - 1, int((now - born) / life * len(TRAIL_COLORS)))
            if g.ch[i] == " ":
                g.ch[i], g.fg[i] = ch, TRAIL_COLORS[k]
        if self.term:
            self.term.draw(g, self.term_focused)
        self.apply_scatter(g, now)
        if self.prompt:
            self.prompt.draw(g, now)
        return g

    def apply_scatter(self, g: Grid, now: float):
        t = self.term if self.term_focused else None
        for i, (born, life, seed) in self.scatter.items():
            ch = g.ch[i]
            if ch in (" ", ""):
                continue
            if t and t.has_ink(*reversed(divmod(i, self.cols))):
                continue                             # never scramble what you're typing
            p = (now - born) / life                  # 0 just touched .. 1 whole again
            if ch in "█▀▄":
                g.ch[i] = SHADES[min(2, int(p * 3))]     # light shade first, then denser, then solid
            elif p < 0.65:
                g.ch[i] = SCATTER[(seed + int(p * 9)) % len(SCATTER)]
                g.fg[i] = "line" if seed % 3 else "accent"

    def disturb(self, x: int, y: int, rx: int = 3, ry: int = 1):
        """The mouse passing over the wallpaper's text and pixels shakes them loose."""
        if not self.settings["effects"]["dissolve"]:
            return
        now = time.time()
        t = self.term
        for cy in range(y - ry, y + ry + 1):
            for cx in range(x - rx, x + rx + 1):
                if not (0 <= cx < self.cols and 0 <= cy < self.rows):
                    continue
                i = cy * self.cols + cx
                if self.shown.ch[i] in (" ", "") or i in self.trail:
                    continue
                if any(r.hit(cx, cy) for r in self.regions):
                    continue                         # project rows stay readable: they light up instead
                if t and self.term_focused and (t.has_ink(cx, cy) or t.part(cx, cy) not in (None, "body")):
                    continue                         # what you're typing stays still
                if t and not self.term_focused and t.part(cx, cy) not in (None, "body"):
                    continue                         # the label and the corner marks too
                old = self.scatter.get(i)
                if old and now - old[0] < old[1] * 0.5:
                    continue
                self.scatter[i] = (now, self.rng.uniform(0.45, 1.1), self.rng.randrange(1000))

    def present(self, frame: Grid, only=None, cause: str = "frame"):
        p = self.prof
        if p is None:
            self._out(ansi_diff(self.shown, frame, only))
        else:
            t1 = time.perf_counter()
            n = self._out(ansi_diff(self.shown, frame, only))
            t2 = time.perf_counter()
            p.frame(cause, prof.changed_cells(self.shown, frame, only), n, t1 - p.mark, t2 - t1)
        self.shown = frame

    def set_base(self, new: Grid, animate: bool, speed: float = 1.0, cause: str = "frame"):
        if animate:
            self.fade_src = self.shown.copy()
            self.fade = Fade(self.fade_src, new, self.rng, speed)
            self.fade_t0 = time.time()
            self.fade_cause = cause
        self.base = new
        if not animate:
            self.present(self.compose(), cause=cause)

    # ── show / hide ───────────────────────────────────────────────────────
    def show(self, text: str | None = None):
        now = time.time()
        if text:
            self.say_text, self.say_until = text, now + SAY_SECONDS
        if not self.visible:
            self.visible = True
            self.trail.clear()
            self.scatter.clear()
            self.refresh_async()
        self.set_base(self.home(), animate=True, cause="say" if text else "show")

    def hide(self):
        """The wallpaper never hides; kept so old callers stay harmless."""

    # ── input ─────────────────────────────────────────────────────────────
    def drop_dots(self, x: int, y: int):
        """Leave a few dots where the cursor passed; some drift down like rain."""
        if (x, y) == self.last_cell or not self.settings["effects"]["trail"]:
            return
        self.last_cell = (x, y)
        now = time.time()
        dots = self.settings["effects"]["trail_chars"]
        for _ in range(2):
            dx, dy = self.rng.randint(-1, 1), self.rng.randint(0, 1)
            cx, cy = x + dx, y + dy
            if 0 <= cx < self.cols and 0 <= cy < self.rows:
                ch = dots[self.rng.randrange(len(dots))]
                if dots == DOTS and self.rng.random() < 0.12:
                    ch = MATRIX[self.rng.randrange(2)]
                self.trail[cy * self.cols + cx] = (ch, now, self.rng.uniform(0.5, 1.1))

    def on_mouse(self, b: int, x: int, y: int, press: bool):
        t = self.term
        part = t.part(x, y) if t else None
        if self.drag:
            if b & 32:                               # still holding the button: follow the mouse
                self.drag_to(x, y)
            elif not press:
                self.end_drag()
            return
        if b & 64:                                   # wheel: scroll the shell's history
            if part == "body" and press:
                t.scroll(up=not b & 1)
                self.term_dirty = True
            return
        if b & 32:                                   # motion
            if t and t.hover_close != (part == "close"):
                t.hover_close = part == "close"
                self.term_dirty = True
            if part:                                 # the terminal shares the wallpaper's space:
                self.drop_dots(x, y)                 # the trail runs through it, and it gets
                self.disturb(x, y)                   # shaken like everything else
                return
            hit = next((r for r in self.regions if r.hit(x, y)), None)
            if not hit:
                self.drop_dots(x, y)
                self.disturb(x, y)
            new = (hit.kind, hit.index) if hit else None
            if new != self.hover:
                self.hover = new
                self.set_base(self.home(), animate=False, cause="hover")
            return
        if not press:
            return
        if self.prompt and (b & 3) == 0:
            if self.prompt.hit(x, y):
                if not self.prompt.focused or self.prompt.answer:   # click the answer: ask something new
                    self.prompt.text, self.prompt.answer, self.prompt.action = "", "", None
                    self.focus_prompt(True)
                return
            self.close_prompt()
        if part:
            if (b & 3) == 0:
                if part == "close":
                    self.close_term()
                    return
                self.focus_term(True)
                if part == "title":
                    self.drag = ("move", x - t.x, y - t.y)
                elif part == "corner":
                    self.drag = ("size", t.x + t.width - 1 - x, t.y + t.height - 1 - y)
            return
        if self.term_focused:
            self.focus_term(False)                   # a click on the wallpaper gives the keyboard back
        if (b & 3) == 2:                             # right click: desktop menu (sotto's Hyprland desktop)
            menu = os.path.expanduser("~/.config/hypr/scripts/desktop-menu.sh")
            if plat.OS == "linux" and os.path.exists(menu):
                plat.spawn([menu])
        elif (b & 3) == 0:
            hit = next((r for r in self.regions if r.hit(x, y)), None)
            if hit:
                self.open_row(hit)
                return
            now = time.time()
            last = self.last_click
            if last and now - last[0] < 0.45 and abs(x - last[1]) <= 2 and abs(y - last[2]) <= 1:
                self.last_click = None           # double-click on empty wallpaper: open the prompt here
                if self.dock_present():
                    cw, ch = self.cell_px()
                    self.tell_dock(f"prompt {x * cw} {y * ch}")
                else:
                    self.open_prompt(x, y)
            else:
                self.last_click = (now, x, y)
                self.tell_dock("prompt-close")   # a single click elsewhere closes an open prompt

    def cell_px(self) -> tuple[int, int]:
        return self.io.cell_px()

    # ── the in-wallpaper prompt (no dock) ─────────────────────────────────
    def dock_present(self) -> bool:
        return plat.has_unix_dgram() and os.path.exists(DOCK_SOCKET) and not os.environ.get("SOTTO_INLINE_PROMPT")

    def open_prompt(self, x: int, y: int):
        old = self.prompt.cells(self.cols, self.rows) if self.prompt else set()
        if self.term_focused:
            self.focus_term(False)
        self.prompt = Prompt(x, y, self.cols, self.rows)
        self.io.focus(True)
        self.present(self.compose(), only=old | self.prompt.cells(self.cols, self.rows), cause="prompt")

    def focus_prompt(self, on: bool):
        if self.prompt:
            self.prompt.focused = on
            self.io.focus(on)
            self.prompt_dirty = True

    def close_prompt(self):
        p = self.prompt
        if not p:
            return
        cells = p.cells(self.cols, self.rows)
        self.prompt = None
        if not self.term_focused:
            self.io.focus(False)
        self.present(self.compose(), only=cells, cause="prompt")

    def prompt_keys(self, s: str):
        p = self.prompt
        if p.busy:
            return
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == "\x1b":
                m = re.match(r"\x1b(\[[0-9;?]*[ -/]*[@-~]|O.)", s[i:])
                if m:                                   # arrows and other keys: ignored
                    i += len(m[0])
                    continue
                self.close_prompt()                     # Esc on its own
                return
            if ch in "\r\n":
                self.submit_prompt()
                return
            if ch in "\x7f\x08":
                p.text = p.text[:-1]
            elif ch == "\x15":                          # Ctrl+U
                p.text = ""
            elif ch >= " ":
                p.text += ch
            i += 1
        p.answer = ""
        self.prompt_dirty = True

    def submit_prompt(self):
        p = self.prompt
        text = p.text.strip()
        if not text:
            return
        from . import assistant

        r = assistant.handle(text)
        if r is not None:
            self.show_answer(r)
            return
        p.busy = True
        self.prompt_dirty = True

        def work():
            try:
                res = assistant.ask(text)
            except Exception:
                log_error()
                res = {"reply": "Something went wrong asking Claude.", "action": None}
            self.pending_answer = (p, res)
            self.flags.add("answer")

        threading.Thread(target=work, daemon=True).start()

    def show_answer(self, r: dict, p: "Prompt | None" = None):
        p = p or self.prompt
        if p is not self.prompt or p is None:
            return
        from . import assistant

        p.busy = False
        p.answer, p.answer_t0 = r.get("reply") or "", time.time()
        action = r.get("action")
        self.focus_prompt(False)                        # after Enter the keyboard goes straight back
        if action and action.get("type") == "wallterm":
            x, y = p.x, p.y
            self.close_prompt()
            cw, chh = self.cell_px()
            self.open_term(x * cw, y * chh)
            return
        if action:
            try:
                assistant.run_action(action)
            except Exception:
                log_error()
        self.prompt_dirty = True

    # ── the shell card ────────────────────────────────────────────────────
    def open_term(self, px: int | None = None, py: int | None = None):
        if self.term:
            self.focus_term(True)
            return
        try:
            from .term import Term
        except ImportError:                          # pyte isn't installed, or no pty (Windows)
            self.show("The terminal inside the wallpaper isn't on Windows yet" if plat.OS == "windows"
                      else "The terminal needs pyte: run uv sync in the sotto folder")
            return
        t = Term(0, 0)
        if px is None or py is None:
            x, y = (self.cols - t.width) // 2, (self.rows - t.height) // 2
        else:
            cw, chh = self.cell_px()
            x, y = px // cw, py // chh
        t.move_to(x, y, self.cols, self.rows)
        self.term = t
        self.focus_term(True)
        self.present(self.compose(), only=t.cells(self.cols, self.rows), cause="terminal")

    def close_term(self):
        t = self.term
        if not t:
            return
        cells = t.cells(self.cols, self.rows)
        self.focus_term(False)
        self.term, self.drag = None, None
        t.close()
        self.present(self.compose(), only=cells, cause="terminal")

    def focus_term(self, on: bool):
        on = bool(on and self.term)
        if on == self.term_focused:
            return
        self.term_focused = on
        if on and self.prompt:
            self.prompt.focused = False
        self.io.focus(on)
        self.tell_dock(self.term_rect() if on else "term-off")
        self.sync_modes()
        self.term_dirty = True

    def term_rect(self) -> str:
        """The card in screen pixels, for the dock (it catches clicks everywhere else)."""
        cw, chh = self.cell_px()
        t = self.term
        return f"term-on {t.x * cw} {t.y * chh} {t.width * cw} {t.height * chh}"

    def sync_modes(self):
        """Mirror the shell's cursor-key and bracketed-paste modes to kitty, so kitty
        sends exactly the keys the program inside expects."""
        from .term import DECCKM, PASTE
        mode = self.term.screen.mode if self.term and self.term_focused else set()
        want = (DECCKM in mode, PASTE in mode)
        if want != self.outer_modes:
            self.write(("\x1b[?1h" if want[0] else "\x1b[?1l") + ("\x1b[?2004h" if want[1] else "\x1b[?2004l"))
            self.outer_modes = want

    def drag_to(self, x: int, y: int):
        t = self.term
        if not t:
            self.drag = None
            return
        kind, dx, dy = self.drag
        before = t.cells(self.cols, self.rows)
        if kind == "move":
            t.move_to(x - dx, y - dy, self.cols, self.rows)
        else:
            t.resize(x + dx - t.x - 1, y + dy - t.y - 1, self.cols, self.rows)
        self.push_through(t)
        self.present(self.compose(), only=before | t.cells(self.cols, self.rows) | set(self.scatter), cause="terminal-drag")

    def push_through(self, t):
        """Dragging the terminal shakes the wallpaper loose along its edges."""
        now = time.time()
        for cy in (t.y, t.y + t.height - 1):
            for cx in range(t.x, t.x + t.width):
                self._loosen(cx, cy, now)
        for cx in (t.x, t.x + t.width - 1):
            for cy in range(t.y, t.y + t.height):
                self._loosen(cx, cy, now)

    def _loosen(self, cx: int, cy: int, now: float):
        if 0 <= cx < self.cols and 0 <= cy < self.rows:
            i = cy * self.cols + cx
            if self.base.ch[i] not in (" ", "") and i not in self.scatter:
                self.scatter[i] = (now, self.rng.uniform(0.35, 0.8), self.rng.randrange(1000))

    def end_drag(self):
        self.drag = None
        if self.term_focused:
            self.tell_dock(self.term_rect())

    def on_command(self, msg: list[str]):
        if msg and msg[0] == "term":
            if len(msg) == 3 and msg[1].isdigit() and msg[2].isdigit():
                self.open_term(int(msg[1]), int(msg[2]))
            else:
                self.open_term()
        elif msg and msg[0] == "term-release":
            self.focus_term(False)
        elif msg and msg[0] == "profile":
            req = prof.parse_request(msg)
            if req and self.prof is None:
                self.prof = prof.Profile(*req)

    def tell_dock(self, msg: str):
        if not plat.has_unix_dgram():
            return
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
                s.sendto(msg.encode(), DOCK_SOCKET)
        except OSError:
            pass

    def on_keys(self, s: str):
        if not s:
            return
        if self.term_focused and self.term:
            self.term.send(s.encode("utf-8", "surrogateescape"))    # the exact bytes kitty sent
            return
        if self.prompt and self.prompt.focused:
            self.prompt_keys(s)
            return
        for ch in s:
            self.on_key(ch)

    def on_key(self, ch: str):
        if self.visible and ch in "123456789" and int(ch) <= len(self.agents):
            self.jump_agent(int(ch) - 1)

    def open_row(self, hit):
        if hit.kind == "agent":
            self.jump_agent(hit.index)
        elif hit.kind == "project":
            self.open_project(hit.index)

    def jump_agent(self, n: int):
        if n >= len(self.agents):
            return
        try:
            note = agentboard.jump(self.agents[n])
        except Exception:
            log_error()
            note = "could not reach that session's window"
        if note:
            self.say_text, self.say_until = note, time.time() + 6
            self.set_base(self.home(), animate=False, cause="say")

    def poll_agents(self, now: float):
        """Re-read the Claude Code sessions; re-assemble the board only when something changed."""
        self.next_agents = now + AGENTS_EVERY
        try:
            fresh = agentboard.sessions(now)
        except Exception:
            log_error()
            return
        sig = tuple((a["pid"], a["status"], a["detail"]) for a in fresh)
        if sig == self.agents_sig:
            self.agents = fresh                      # same board; times refresh with the clock
            return
        first = not self.agents_sig and not self.agents
        self.agents, self.agents_sig = fresh, sig
        if self.visible and not self.fade:
            self.set_base(self.home(), animate=not first, speed=0.8, cause="agents")


    def open_project(self, n: int):
        projects = self.state.get("projects", [])
        if n >= len(projects):
            return
        p = projects[n]
        if not p.get("open"):
            plat.open_terminal(p["path"])
            return
        for c in p["open"]:
            try:
                plat.run_open_command(c, p["path"])
            except (KeyError, IndexError, ValueError):
                log_error()

    def read_input(self, buf: str) -> str:
        pos = 0
        for m in MOUSE.finditer(buf):
            self.on_keys(buf[pos:m.start()])
            self.on_mouse(int(m[1]), int(m[2]) - 1, int(m[3]) - 1, m[4] == "M")
            pos = m.end()
        rest = buf[pos:]
        half = HALF_MOUSE.search(rest)
        if half:                                     # keep half a mouse report for the next read
            self.on_keys(rest[:half.start()])
            return rest[half.start():]
        self.on_keys(rest)                           # keys (arrows, Esc...) go through right away
        return ""

    # ── main loop ─────────────────────────────────────────────────────────
    def tick(self):
        now = time.time()
        if "quit" in self.flags:
            raise SystemExit
        if self.prof is not None and now >= self.prof.end:
            p, self.prof = self.prof, None
            try:
                p.save()
            except OSError:
                log_error()
        if "resize" in self.flags:
            self.flags.discard("resize")
            self.cols, self.rows = self.size()
            self.fade, self.trail, self.scatter = None, {}, {}
            self.base = self.home() if self.visible else Grid(self.cols, self.rows)
            self.shown = Grid(self.cols, self.rows)
            if self.prompt:
                self.prompt = None
                self.io.focus(False)
            self.write(clear_code())
            self.present(self.compose(), cause="resize")
        if "toggle" in self.flags:
            self.flags.discard("toggle")
            self.refresh_async()
        if now >= self.next_config:
            self.next_config = now + 1
            m = configmod.mtime()
            if m != self.config_mtime:
                self.config_mtime = m
                self.reload_config()
        self.widgets.poll(now)
        if "widgets" in self.flags:
            self.flags.discard("widgets")
            if self.visible and not self.fade:
                self.set_base(self.home(), animate=True, speed=0.6, cause="widgets")
            else:
                self.flags.add("widgets-later")
        if "answer" in self.flags:
            self.flags.discard("answer")
            p, res = self.pending_answer
            self.show_answer(res, p)
        if self.prompt and (self.prompt_dirty or self.prompt.busy or self.prompt.revealing(now)) \
                and now - self.prompt_drawn >= 1 / FPS:
            self.prompt_dirty, self.prompt_drawn = False, now
            self.present(self.compose(), only=self.prompt.cells(self.cols, self.rows), cause="prompt")

        m = self.mtime(collect.STATE)
        if m != self.state_mtime:
            self.state_mtime = m
            self.state = self.load_state()
            if self.visible and not self.fade:
                self.set_base(self.home(), animate=True, speed=0.6, cause="state")
        m = self.mtime(collect.INBOX)
        if m != self.inbox_mtime:
            self.inbox_mtime = m
            try:
                self.show(json.loads(collect.INBOX.read_text()).get("text"))
            except (OSError, json.JSONDecodeError):
                pass

        if self.term:
            if not self.term.alive:
                self.close_term()                    # the shell exited
            elif self.term_dirty and now - self.term_drawn >= 1 / FPS:
                self.term_dirty, self.term_drawn = False, now
                self.sync_modes()
                self.present(self.compose(), only=self.term.cells(self.cols, self.rows) | set(self.trail), cause="terminal")

        if self.fade:
            t = now - self.fade_t0
            frame = self.shown.copy()
            self.fade.apply(t, frame, self.fade_src)
            only = set(self.fade.plan.keys())
            if self.term:                                    # the terminal stays on top
                self.term.recede(frame)
                self.term.draw(frame, self.term_focused)
                only |= self.term.cells(self.cols, self.rows)
            self.present(frame, only=only, cause=self.fade_cause)
            if t >= self.fade.end:
                self.fade = None
                if "widgets-later" in self.flags:
                    self.flags.discard("widgets-later")
                    self.flags.add("widgets")
            return

        if self.trail or self.scatter:
            dead = [i for i, (_, born, life) in self.trail.items() if now - born >= life]
            for i in dead:
                del self.trail[i]
            settled = [i for i, (born, life, _) in self.scatter.items() if now - born >= life]
            for i in settled:
                del self.scatter[i]
            self.present(self.compose(), only=set(self.trail) | set(dead) | set(self.scatter) | set(settled), cause="trail")

        if now >= self.next_refresh:
            self.next_refresh = now + REFRESH_EVERY
            self.refresh_async()
        if now >= self.next_agents and not os.environ.get("SOTTO_DEMO"):
            self.poll_agents(now)
        if self.visible:
            minute = time.localtime(now).tm_min
            if minute != self.minute or (self.say_text and now >= self.say_until):
                cause = "clock" if minute != self.minute else "say-end"
                self.minute = minute
                if self.say_text and now >= self.say_until:
                    self.say_text = None
                self.set_base(self.home(), animate=False, cause=cause)

    def timeout(self) -> float:
        busy = self.prompt and (self.prompt.busy or self.prompt_dirty or self.prompt.revealing(time.time()))
        return 1 / FPS if (self.fade or self.trail or self.scatter or self.term_dirty or self.drag or busy) else 1.0

    def run(self, fd=None):
        global FPS
        FPS = self.settings["effects"]["fps"]
        io = self.io
        # any-motion mouse tracking stays on: motion only arrives when the pointer is over the bare desktop
        self.write("\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?1003h\x1b[?1006h" + clear_code())
        self.widgets.configure(self.settings["widgets"])
        self.show()
        self.listen()
        pending = ""
        while True:
            fds = [io] + ([self.sock] if self.sock else []) + ([self.term.master] if self.term else [])
            r, _, _ = select.select(fds, [], [], self.timeout())
            if self.prof is not None:
                self.prof.mark = time.perf_counter()     # a frame's build time starts at the wake-up
            if io in r:
                try:
                    data = io.read().decode(errors="surrogateescape")
                except OSError:
                    data = ""
                pending = self.read_input(pending + data)
            if self.sock and self.sock in r:
                try:
                    self.on_command(self.sock.recv(256).decode(errors="ignore").split())
                except OSError:
                    pass
            if self.term and self.term.master in r:
                for _ in range(8):                   # a burst of output, but keep drawing
                    if not self.term.read():
                        break
                self.term_dirty = True
            self.tick()

    def listen(self):
        if not plat.has_unix_dgram():
            return                                   # Windows: commands come through the host instead
        try:
            os.unlink(SOCKET)
        except OSError:
            pass
        try:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.sock.bind(SOCKET)
            self.sock.setblocking(False)
        except OSError:
            self.sock = None
            log_error()


def log_error():
    try:
        collect.CACHE.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(time.strftime("%F %T ") + traceback.format_exc() + "\n")
    except OSError:
        pass


def run(io=None) -> int:
    wall = Wall(io)
    try:
        with open(PIDFILE, "w") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass
    if threading.current_thread() is threading.main_thread():
        for name, flag in (("SIGUSR1", "toggle"), ("SIGTERM", "quit"), ("SIGHUP", "quit")):
            if hasattr(signal, name):
                signal.signal(getattr(signal, name), lambda *_, f=flag: wall.flags.add(f))
    try:
        if hasattr(wall.io, "setup"):
            wall.io.setup(wall)
        wall.run()
    except (SystemExit, KeyboardInterrupt):
        pass
    except Exception:
        log_error()
        raise
    finally:
        if wall.term:
            wall.close_term()
        try:
            os.unlink(SOCKET)
        except OSError:
            pass
        wall.write("\x1b[?1003l\x1b[?1006l\x1b[?1l\x1b[?2004l\x1b[?7h\x1b[?25h\x1b[0m\x1b[?1049l")
        if hasattr(wall.io, "restore"):
            wall.io.restore()
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass
    return 0
