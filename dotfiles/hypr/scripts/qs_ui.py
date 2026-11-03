"""Shared bits for the quick-settings menus: Control Center, Wi-Fi, Sound, Displays, Bluetooth.

Every menu is a rofi list in the `quick` theme, dropped under the bar on the right.
Rows are built with Rows(): section headers (dim, not clickable), and rows with an
icon, a left label and a right-aligned value, each tied to an action.
"""
import html
import os
import subprocess
import sys
from collections.abc import Callable

THEME = "quick"
WIDTH = 50  # characters per row (monospace font)
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
BACK = "--back" in sys.argv

Action = Callable[[], None] | None
OPEN = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "quick-menu-open")
ME = os.path.basename(sys.argv[0])


def _toggle() -> None:
    """Bar button / shortcut pressed while a menu is open: close it.
    Same menu again -> just close (on/off switch). Another menu -> close, then open this one."""
    if BACK:
        return
    try:
        pid, name = open(OPEN).read().split()
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return
    run("pkill", "-x", "rofi")
    if name == ME:
        sys.exit(0)
    import time
    time.sleep(0.2)



def run(*args: str, inp: str | None = None, timeout: int = 40) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, input=inp, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def spawn(*args: str) -> None:
    subprocess.Popen(args, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def notify(title: str, body: str = "") -> None:
    run("notify-send", "-t", "2500", title, body)


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def dim(text: str) -> str:
    return f"<span alpha='55%'>{esc(text)}</span>"


def fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def open_menu(name: str) -> None:
    """Replace this menu with another one (it gets a Back row to the Control Center)."""
    path = os.path.join(SCRIPTS, name)
    os.execv(sys.executable, [sys.executable, path, "--back"])


class Rows:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.actions: list[Action] = []
        self.headers: list[int] = []

    def header(self, title: str) -> None:
        self.headers.append(len(self.text))
        self.text.append(f"<span size='small' alpha='50%'>{esc(title.upper())}</span>")
        self.actions.append(None)

    def add(self, icon: str, left: str, right: str = "", action: Action = None,
            bold: bool = False, strong_right: bool = False) -> int:
        left = fit(left, WIDTH - len(right) - 2)
        pad = " " * max(1, WIDTH - len(left) - len(right))
        l = f"<b>{esc(left)}</b>" if bold else esc(left)
        r = f"<b>{esc(right)}</b>" if strong_right else dim(right)
        self.text.append(f"{icon}  {l}{pad}{r}")
        self.actions.append(action)
        return len(self.text) - 1

    def back(self) -> None:
        if BACK:
            self.add("󰁍", "Control Center", "", lambda: open_menu("control-center.py"))


def toggle_text(on: bool) -> str:
    return "● On" if on else "○ Off"


def show(rows: Rows, title: str, status: str = "", selected: int = 0) -> tuple[int, Action] | None:
    """Show the menu. Returns (row index, action) or None when closed (Esc / click outside)."""
    mesg = f"<b>{esc(title)}</b>" + (f"   {dim(status)}" if status else "")
    while selected in rows.headers and selected < len(rows.text) - 1:
        selected += 1
    while True:
        args = ["rofi", "-dmenu", "-i", "-no-custom", "-markup-rows", "-format", "i",
                "-theme", THEME, "-p", "", "-mesg", mesg, "-selected-row", str(selected),
                "-theme-str", f"listview {{ lines: {min(len(rows.text), 20)}; }}",
                "-kb-cancel", "Escape,MousePrimary"]  # left-click outside the panel closes it
        if rows.headers:
            args += ["-u", ",".join(map(str, rows.headers))]
        with open(OPEN, "w") as f:
            f.write(f"{os.getpid()} {ME}")
        r = run(*args, inp="\n".join(rows.text), timeout=600)
        try:
            if open(OPEN).read().split()[0] == str(os.getpid()):
                os.remove(OPEN)
        except (OSError, IndexError):
            pass
        if r.returncode != 0 or not r.stdout.strip():
            return None
        idx = int(r.stdout.strip())
        if idx in rows.headers:  # headers are labels, not buttons
            selected = idx + 1
            continue
        return idx, rows.actions[idx]


def ask(mesg: str, password: bool = False) -> str | None:
    args = ["rofi", "-dmenu", "-theme", THEME, "-p", "", "-mesg", mesg,
            "-theme-str", 'mainbox { children: [ "message", "inputbar" ]; } '
                          'entry { placeholder: "type, then Enter"; }']
    if password:
        args.append("-password")
    r = run(*args, inp="", timeout=600)
    text = r.stdout.rstrip("\n")
    return text if r.returncode == 0 and text else None


def confirm(question: str, yes: str) -> bool:
    rows = Rows()
    rows.add("󰜺", "Cancel")
    rows.add("󰄬", yes)
    picked = show(rows, question)
    return picked is not None and picked[0] == 1


_toggle()  # runs when a menu starts (needs run() above)
