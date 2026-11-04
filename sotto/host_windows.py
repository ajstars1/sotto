"""sotto on Windows: the wallpaper in a WebView2 window behind the desktop icons. PREVIEW.

    python -m sotto host            start it (pythonw -m sotto host: without a console window)
    python -m sotto host --window   in a normal window instead, to try it
    python -m sotto host --stop     stop the running one
    python -m sotto host --selftest find the desktop's wallpaper layer and print what was found

How it works:
- The wall runs exactly as on Linux; its frames go to xterm.js in a pywebview (WebView2) window
  through sotto/web.py.
- The window is parented to the desktop's wallpaper layer (the WorkerW / Progman trick that live
  wallpaper apps use), so it sits under the icons and above the picture.
- Windows doesn't give a window behind the icons any mouse input, so this host reads the cursor
  position and the button state 60 times a second (no hooks, nothing injected into other apps)
  and passes them to the wall while the cursor is over the bare desktop.
- Keys: when the wall needs the keyboard (the double-click prompt, the terminal), the window
  steps in front for a moment, full screen and looking the same, and goes back after.
"""
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

from . import plat, web

user32 = ctypes.windll.user32 if plat.OS == "windows" else None          # type: ignore[attr-defined]
kernel32 = ctypes.windll.kernel32 if plat.OS == "windows" else None      # type: ignore[attr-defined]

PIDFILE = os.path.join(plat.runtime_dir(), "sotto-host.pid")
DESKTOP_CLASSES = {"SysListView32", "SHELLDLL_DefView", "WorkerW", "Progman"}
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_CHILD, WS_POPUP, WS_VISIBLE = 0x40000000, 0x80000000, 0x10000000
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000080, 0x08000000
SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_NOZORDER = 0x0010, 0x0040, 0x0004
HWND_TOPMOST, HWND_NOTOPMOST, HWND_BOTTOM = -1, -2, 1

if user32 is not None:
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.FindWindowW.restype = wintypes.HWND
    user32.FindWindowExW.restype = wintypes.HWND
    user32.FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetParent.restype = wintypes.HWND
    user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                           wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    _set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    _get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    _set_long.restype = ctypes.c_ssize_t
    _get_long.restype = ctypes.c_ssize_t
    _set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    _get_long.argtypes = [wintypes.HWND, ctypes.c_int]


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


def screen_rect() -> tuple[int, int, int, int]:
    """The primary monitor in physical pixels: (left, top, width, height)."""
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def virtual_origin() -> tuple[int, int]:
    return user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)         # SM_X/YVIRTUALSCREEN


def find_wallpaper_layer() -> tuple[int, str, int]:
    """(parent window, how it was found, the icons' SHELLDLL_DefView). Asks Explorer to split the
    wallpaper from the icons first (message 0x052C), as every live-wallpaper app does."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return 0, "no Progman window (is Explorer running?)", 0
    res = ctypes.c_size_t()
    user32.SendMessageTimeoutW(progman, 0x052C, 0xD, 0x1, 0, 1000, ctypes.byref(res))
    user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0, 1000, ctypes.byref(res))
    found = {"worker": 0, "defview": 0}

    def each(hwnd, _):
        shell = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
        if shell:
            found["defview"] = shell
            w = user32.FindWindowExW(None, hwnd, "WorkerW", None)            # the WorkerW after it
            if w:
                found["worker"] = w
        return True

    user32.EnumWindows(WNDENUMPROC(each), 0)
    if found["worker"]:
        return found["worker"], "WorkerW behind the icons", found["defview"]
    defview = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None) or found["defview"]
    child = user32.FindWindowExW(progman, None, "WorkerW", None)            # Windows 11 24H2 and later
    if child:
        return progman, "Progman (24H2 layout), below the icons", defview
    return progman, "Progman, below the icons", defview


def attach(hwnd: int) -> str:
    parent, how, defview = find_wallpaper_layer()
    if not parent:
        return how
    style = _get_long(hwnd, GWL_STYLE)
    _set_long(hwnd, GWL_STYLE, (style | WS_CHILD | WS_VISIBLE) & ~WS_POPUP)
    user32.SetParent(hwnd, parent)
    left, top, w, h = screen_rect()
    vx, vy = virtual_origin()
    after = defview if (parent == user32.FindWindowW("Progman", None) and defview) else HWND_BOTTOM
    flags = SWP_NOACTIVATE | SWP_SHOWWINDOW
    user32.SetWindowPos(hwnd, after, left - vx, top - vy, w, h, flags)
    return how


def detach_to_front(hwnd: int):
    """Step in front (same place, same look) so the window can have the keyboard."""
    style = _get_long(hwnd, GWL_STYLE)
    user32.SetParent(hwnd, None)
    _set_long(hwnd, GWL_STYLE, (style | WS_POPUP | WS_VISIBLE) & ~WS_CHILD)
    left, top, w, h = screen_rect()
    user32.SetWindowPos(hwnd, HWND_TOPMOST, left, top, w, h, SWP_SHOWWINDOW)
    user32.keybd_event(0x12, 0, 0, 0)                 # an Alt tap lets a background app take the foreground
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)


class Host:
    def __init__(self, windowed: bool):
        self.windowed = windowed
        self.srv = web.start()
        self.srv.title = "sotto-wallpaper-" + self.srv.token[:8]
        self.hwnd = 0
        self.front = False                            # stepped in front for the keyboard
        self.window = None
        self.how = ""
        self.srv.io.on_focus = self.on_focus

    # ── keyboard ──
    def on_focus(self, on: bool):
        if self.windowed or not self.hwnd or on == self.front:
            return
        self.front = on
        if on:
            detach_to_front(self.hwnd)
            if self.window is not None:
                try:
                    self.window.evaluate_js("document.querySelector('.xterm-helper-textarea').focus()")
                except Exception:
                    pass
        else:
            user32.SetWindowPos(self.hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, 0x0001 | 0x0002 | SWP_NOACTIVATE)
            self.how = attach(self.hwnd)

    # ── mouse, read from the system while the window is behind the icons ──
    def mouse_loop(self):
        pt = wintypes.POINT()
        last_cell, buttons = None, {0x01: False, 0x02: False}
        while True:
            time.sleep(1 / 60)
            if self.front or not self.hwnd or self.windowed:
                last_cell = None
                continue
            if not user32.GetCursorPos(ctypes.byref(pt)):
                continue
            under = user32.WindowFromPoint(pt)
            top = user32.GetAncestor(under, 2) if under else 0          # GA_ROOT
            over_desktop = under == self.hwnd or (under and class_name(under) in DESKTOP_CLASSES) or \
                (top and class_name(top) in DESKTOP_CLASSES)
            if not over_desktop:
                last_cell = None
                for vk in buttons:
                    buttons[vk] = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                continue
            io = self.srv.io
            left, top_px, w, h = screen_rect()
            cols, rows = io.cols, io.rows
            dpi = user32.GetDpiForWindow(self.hwnd) / 96 if hasattr(user32, "GetDpiForWindow") else 1
            cx = max(0, min(cols - 1, int((pt.x - left) / max(0.5, io.cwf * dpi))))
            cy = max(0, min(rows - 1, int((pt.y - top_px) / max(0.5, io.chf * dpi))))
            out = ""
            if (cx, cy) != last_cell:
                last_cell = (cx, cy)
                out += f"\x1b[<35;{cx + 1};{cy + 1}M"
            for vk, b in ((0x01, 0), (0x02, 2)):
                down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                if down != buttons[vk]:
                    buttons[vk] = down
                    out += f"\x1b[<{b};{cx + 1};{cy + 1}{'M' if down else 'm'}"
            if out:
                io.feed(out.encode())

    def watch_explorer(self):
        """Explorer restarts tear the wallpaper layer down; put the window back."""
        while True:
            time.sleep(5)
            if self.windowed or self.front or not self.hwnd:
                continue
            parent = user32.GetParent(self.hwnd)
            if not parent or not user32.IsWindow(parent):
                self.how = attach(self.hwnd)

    def on_shown(self):
        for _ in range(50):
            self.hwnd = user32.FindWindowW(None, self.srv.title)
            if self.hwnd:
                break
            time.sleep(0.1)
        if not self.hwnd:
            print("could not find the wallpaper window", file=sys.stderr)
            return
        if not self.windowed:
            ex = _get_long(self.hwnd, GWL_EXSTYLE)
            _set_long(self.hwnd, GWL_EXSTYLE, ex | WS_EX_TOOLWINDOW)            # not in Alt+Tab or the taskbar
            self.how = attach(self.hwnd)
            print(f"sotto: on the desktop ({self.how})", flush=True)
        threading.Thread(target=self.mouse_loop, daemon=True).start()
        threading.Thread(target=self.watch_explorer, daemon=True).start()

    def run(self) -> int:
        try:
            import webview                                                   # pywebview
        except ImportError:
            print("The Windows wallpaper needs pywebview: uv sync --extra windows  (or pip install pywebview)",
                  file=sys.stderr)
            return 1
        from .config import load

        bg = load()["palette"]["bg"]
        left, top, w, h = screen_rect()
        scale = user32.GetDpiForSystem() / 96 if hasattr(user32, "GetDpiForSystem") else 1
        kw = dict(frameless=True, easy_drag=False, background_color=bg, x=left, y=top,
                  width=int(w / scale), height=int(h / scale)) if not self.windowed else \
            dict(width=1280, height=760, background_color=bg)
        self.window = webview.create_window(self.srv.title, self.srv.url, **kw)
        wall = threading.Thread(target=self._wall, daemon=True)
        wall.start()
        self.window.events.shown += lambda: threading.Thread(target=self.on_shown, daemon=True).start()
        webview.start(gui="edgechromium", private_mode=True)
        return 0

    def _wall(self):
        while not self.srv.io.ready.wait(0.5):
            pass
        try:
            web.run_wall(self.srv)
        finally:
            if self.window is not None:
                try:
                    self.window.destroy()
                except Exception:
                    pass


def stop() -> int:
    try:
        pid = int(open(PIDFILE).read().strip())
    except (OSError, ValueError):
        print("sotto host isn't running")
        return 1
    if not plat.alive(pid):
        print("sotto host isn't running")
        return 1
    os.kill(pid, 15)                                  # on Windows this ends the process
    print(f"stopped sotto host (pid {pid})")
    return 0


def main(argv: list[str]) -> int:
    if plat.OS != "windows":
        print("sotto host runs on Windows only", file=sys.stderr)
        return 2
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)                       # type: ignore[attr-defined]
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()
    if "--stop" in argv:
        return stop()
    if "--selftest" in argv:
        parent, how, defview = find_wallpaper_layer()
        print(f"wallpaper layer: {how} (window {parent:#x}, icons {defview:#x}); screen {screen_rect()}")
        return 0 if parent else 1
    if os.path.exists(PIDFILE):
        try:
            if plat.alive(int(open(PIDFILE).read().strip())):
                print("sotto host is already running (python -m sotto host --stop to stop it)")
                return 1
        except (OSError, ValueError):
            pass
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    try:
        return Host(windowed="--window" in argv).run()
    finally:
        try:
            os.unlink(PIDFILE)
        except OSError:
            pass
