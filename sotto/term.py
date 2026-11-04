"""A shell inside the wallpaper: type /terminal in the wallpaper prompt.

The wallpaper is itself a terminal (kitty), so this runs your shell in a pty,
lets pyte keep its screen, and the wall writes that screen straight onto the
wallpaper, in its own font and colours, between four corner marks. Drag the
label to move it and the bottom-right mark to resize it; × closes it (or type
`exit`).
"""
import fcntl
import os
import pty
import shutil
import signal
import struct
import subprocess
import termios

import pyte

# what wallpaper colours fade to right around the shell's text (fading twice changes nothing)
GHOST = {"text": "line", "dim": "line", "accent": "line", "alert": "line"}
# ANSI colours for a light background (white stays readable, black is the light one)
ANSI = {
    "black": "#e2d4bd", "red": "#a9532f", "green": "#6f7d3c", "brown": "#a7783f",
    "blue": "#4f6d82", "magenta": "#8c5b68", "cyan": "#4f7f72", "white": "#8f7759",
    "brightblack": "#a08a6c", "brightred": "#c0623c", "brightgreen": "#82913f", "brightbrown": "#b98a4c",
    "brightblue": "#62829a", "brightmagenta": "#a1707c", "brightcyan": "#629486", "brightwhite": "#3d2c1f",
}
DECCKM = 1 << 5            # pyte keeps private modes shifted by 5: application cursor keys
PASTE = 2004 << 5          # bracketed paste
# don't let programs in the shell find (and drive) the wallpaper's own kitty
HIDE_ENV = ("KITTY_WINDOW_ID", "KITTY_LISTEN_ON", "KITTY_PID", "KITTY_PUBLIC_KEY", "TERMINFO", "TERM_PROGRAM")


def _controlling_tty():
    """In the child, before the shell starts: new session, stdin (the pty) becomes its terminal."""
    os.setsid()
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


def color(c: str, default: str) -> str:
    if c == "default":
        return default
    if c in ANSI:
        return ANSI[c]
    return "#" + c if len(c) == 6 else default


class Term:
    MIN_COLS, MIN_ROWS = 30, 6

    def __init__(self, x: int, y: int, cols: int = 80, rows: int = 20, cwd: str | None = None):
        self.x, self.y = x, y              # top-left cell of the card (its title bar)
        self.cols, self.rows = cols, rows
        self.screen = pyte.HistoryScreen(cols, rows, history=3000, ratio=0.5)
        self.screen.write_process_input = self.send_text     # answers to "where is the cursor?" etc.
        self.stream = pyte.ByteStream(self.screen)
        self.master, slave = pty.openpty()
        self._winsize()
        env = {k: v for k, v in os.environ.items() if k not in HIDE_ENV}
        env.update(TERM="xterm-256color", COLORTERM="truecolor")
        self.shell = os.environ.get("SHELL") or "/bin/bash"
        # a new session with the pty as its controlling terminal, so Ctrl+C and jobs work:
        # `setsid -c` on Linux; macOS has no setsid command, so the child does it itself
        if shutil.which("setsid"):
            argv, pre = ["setsid", "-c", self.shell], None
        else:
            argv, pre = [self.shell], _controlling_tty
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, preexec_fn=pre,
                                     cwd=cwd or os.path.expanduser("~"), env=env, close_fds=True)
        os.close(slave)
        os.set_blocking(self.master, False)
        self.alive = True
        self.hover_close = False

    # ── geometry ────────────────────────────────────────────────────────
    @property
    def width(self) -> int:                # card size in cells: a cell of padding each side,
        return self.cols + 2               # the title bar on top and a row at the bottom

    @property
    def height(self) -> int:
        return self.rows + 2

    def part(self, x: int, y: int) -> str | None:
        if not (self.x <= x < self.x + self.width and self.y <= y < self.y + self.height):
            return None
        if y == self.y:
            return "close" if self.x + self.width - 4 <= x <= self.x + self.width - 2 else "title"
        if y == self.y + self.height - 1 and x >= self.x + self.width - 2:
            return "corner"
        return "body"

    def cells(self, cols: int, rows: int) -> set[int]:
        return {yy * cols + xx
                for yy in range(max(0, self.y), min(rows, self.y + self.height))
                for xx in range(max(0, self.x), min(cols, self.x + self.width))}

    def move_to(self, x: int, y: int, cols: int, rows: int):
        self.x = max(0, min(cols - self.width, x))
        self.y = max(0, min(rows - self.height, y))

    def resize(self, cols: int, rows: int, max_cols: int, max_rows: int):
        cols = max(self.MIN_COLS, min(cols, max_cols - self.x - 2))
        rows = max(self.MIN_ROWS, min(rows, max_rows - self.y - 2))
        if (cols, rows) != (self.cols, self.rows):
            self.cols, self.rows = cols, rows
            self.screen.resize(rows, cols)
            self._winsize()

    def _winsize(self):
        fcntl.ioctl(self.master, termios.TIOCSWINSZ, struct.pack("HHHH", self.rows, self.cols, 0, 0))

    # ── the shell ───────────────────────────────────────────────────────
    def read(self) -> bool:
        """Feed whatever the shell printed into the screen. False when nothing came."""
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return False
        except OSError:                    # EIO: the shell has gone
            data = b""
        if not data:
            self.alive = False
            return True
        self.stream.feed(data)
        return True

    def send(self, data: bytes):
        if self.screen.history.position < self.screen.history.size:
            self.screen.next_page()        # typing jumps back to the bottom, like any terminal
        while data and self.alive:
            try:
                n = os.write(self.master, data)
                data = data[n:]
            except BlockingIOError:
                continue
            except OSError:
                self.alive = False

    def send_text(self, text: str):
        self.send(text.encode())

    def scroll(self, up: bool):
        (self.screen.prev_page if up else self.screen.next_page)()

    def close(self):
        if self.alive:
            try:
                os.killpg(self.proc.pid, signal.SIGHUP)   # the whole session, like closing a window
            except OSError:
                pass
        self.alive = False
        try:
            os.close(self.master)
        except OSError:
            pass
        try:
            self.proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass

    def title(self) -> str:
        t = (self.screen.title or "").strip()
        return t or os.path.basename(self.shell)

    # ── drawing: ink on the wallpaper, not a window ──────────────────────
    # No box: four corner marks and a label. The shell's text is written straight
    # onto the wallpaper. Right around each letter the wallpaper fades to a pale
    # print so the text reads; everywhere else (the clock behind, your projects,
    # the mouse trail) stays as it is. Asleep (not focused) the text fades to grey.
    def recede(self, g):
        """Wallpaper words the terminal overlaps fade as whole phrases (never cut in
        half at its edge), so they can't run into the shell's text. The clock's
        pixels stay solid: they only fade right around the text (see draw)."""
        X, w, cols = self.x, self.width, g.cols
        for y in range(max(0, self.y), min(g.rows, self.y + self.height)):
            row, x = y * cols, 0
            while x < cols:
                if g.ch[row + x] in (" ", "") or g.ch[row + x] in "█▀▄":
                    x += 1
                    continue
                start = end = x
                gap = 0
                while x < cols and g.ch[row + x] not in "█▀▄":
                    if g.ch[row + x] in (" ", ""):
                        gap += 1
                        if gap >= 2:           # two spaces end a phrase
                            break
                    else:
                        gap, end = 0, x
                    x += 1
                if end >= X and start < X + w:
                    for i in range(row + start, row + end + 1):
                        g.fg[i] = GHOST.get(g.fg[i], g.fg[i])
                x = max(x, end + 1)

    def has_ink(self, x: int, y: int) -> bool:
        """Is there shell text at this wallpaper cell?"""
        c, r = x - self.x - 1, y - self.y - 1
        if not (0 <= c < self.cols and 0 <= r < self.rows):
            return False
        return self.screen.buffer[r][c].data not in (" ", "")

    def draw(self, g, focused: bool):
        X, Y, w, h = self.x, self.y, self.width, self.height
        cols, rows = g.cols, g.rows
        ink: dict[int, tuple[str, str, str]] = {}          # cell -> (char, fg, bg)

        def put(x, y, ch, fg, bg="bg"):
            if 0 <= x < cols and 0 <= y < rows:
                ink[y * cols + x] = (ch, fg, bg)

        mark = "accent" if focused else "line"
        for cx, cy, ch in ((X, Y, "┌"), (X + w - 1, Y, "┐"), (X, Y + h - 1, "└"), (X + w - 1, Y + h - 1, "┘")):
            put(cx, cy, ch, mark)                            # the bottom-right one is the resize grip
        title = self.title()[: max(0, w - 7)]
        for k, ch in enumerate(title):
            put(X + 2 + k, Y, ch, "text" if focused else "dim")
        put(X + w - 3, Y, "×", "alert" if self.hover_close else ("dim" if focused else "line"))

        scr = self.screen
        cur = scr.cursor
        show_cursor = focused and not cur.hidden and scr.history.position >= scr.history.size
        default_fg = "text" if focused else "dim"
        for r in range(self.rows):
            line = scr.buffer[r]
            for c in range(self.cols):
                cell = line[c]
                ch = cell.data                 # "" is the right half of a wide character
                is_cursor = show_cursor and r == cur.y and c == cur.x
                if ch == " " and cell.bg == "default" and not cell.reverse and not is_cursor:
                    continue                   # empty: the wallpaper shows through
                if ch and (ord(ch[0]) < 32 or ord(ch[0]) == 127):
                    ch = " "                   # never pass control characters to the outer terminal
                fg, bg = color(cell.fg, default_fg), color(cell.bg, "bg")
                if cell.reverse:
                    fg, bg = bg, fg
                if is_cursor:
                    fg, bg = "bg", "text"
                put(X + 1 + c, Y + 1 + r, ch, fg, bg)

        for i in ink:                          # a pale halo around the text...
            y, x = divmod(i, cols)
            for j in (i - cols - 1, i - cols, i - cols + 1, i - 1, i + 1, i + cols - 1, i + cols, i + cols + 1):
                if 0 <= j < len(g.ch) and j not in ink and abs(j % cols - x) <= 1:
                    g.fg[j] = GHOST.get(g.fg[j], g.fg[j])
        for i, (ch, fg, bg) in ink.items():    # ...then the text itself
            g.ch[i], g.fg[i], g.bg[i] = ch, fg, bg
