"""Runs a real Wall against the web transport, with no browser: feed it keys and mouse
reports, read back what it draws. Used by the tests on Linux, macOS and Windows."""
import os
import queue
import tempfile
import threading
import time

_tmp = tempfile.mkdtemp(prefix="sotto-test-")
# never the real ones: a test wall must not take over the running wallpaper's sockets or the dock
os.environ["XDG_RUNTIME_DIR"] = _tmp
os.environ["XDG_CACHE_HOME"] = os.path.join(_tmp, "cache")
os.environ["SOTTO_CONFIG"] = os.path.join(_tmp, "config.toml")
os.environ.pop("KITTY_LISTEN_ON", None)
os.environ.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
os.environ["SOTTO_DEMO"] = "1"
os.environ.setdefault("SOTTO_STATE", os.path.join(os.path.dirname(__file__), "..", "demo", "state.json"))

from sotto import web  # noqa: E402


class Harness:
    def __init__(self, cols=160, rows=48):
        self.srv = web.Server(0)
        self.q: queue.Queue = queue.Queue()
        with self.srv.lock:
            self.srv.clients.append(self.q)
        self.srv.io.resized(cols, rows, 7, 14)
        self.focus = []
        self.srv.io.on_focus = self.focus.append
        self.t = threading.Thread(target=web.run_wall, args=(self.srv,), daemon=True)
        self.t.start()

    def drain(self, wait=0.4) -> str:
        """Everything drawn in the next `wait` seconds."""
        out, end = [], time.time() + wait
        while time.time() < end:
            try:
                kind, data = self.q.get(timeout=max(0.01, end - time.time()))
                if kind == "out":
                    out.append(data)
            except queue.Empty:
                pass
        return "".join(out)

    def feed(self, s: str):
        self.srv.io.feed(s.encode())

    def move(self, x: int, y: int):
        self.feed(f"\x1b[<35;{x + 1};{y + 1}M")

    def click(self, x: int, y: int):
        self.feed(f"\x1b[<0;{x + 1};{y + 1}M\x1b[<0;{x + 1};{y + 1}m")

    def stop(self):
        self.srv.io.wall.flags.add("quit")
        self.feed("\x00")
        self.t.join(3)
