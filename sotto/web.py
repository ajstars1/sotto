"""The wallpaper in a web view: the same wall, drawn by xterm.js instead of kitty.

Used by the Windows host (a WebView2 window behind the desktop icons) and by
`python -m sotto web`, which opens it in your browser on any OS: a quick way to try
sotto, or to see a config change, without installing anything else.

The wall writes the same escape codes it writes to kitty; this module carries them to the
page over Server-Sent Events, and the page's keys and mouse reports back with POSTs.
Only this computer can reach it (127.0.0.1), and every request but the static files needs
the random token in the URL the host opened, so no web page can type into your wallpaper.
"""
import json
import os
import queue
import secrets
import socket
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as configmod
from .grid import hex_of, set_palette

HERE = Path(__file__).resolve().parent
STATIC = {
    "/static/xterm.js": (HERE / "web" / "vendor" / "xterm.js", "text/javascript"),
    "/static/xterm.css": (HERE / "web" / "vendor" / "xterm.css", "text/css"),
    "/static/font.woff2": (HERE / "fonts" / "DepartureMono-Regular.woff2", "font/woff2"),
}
PAGE = HERE / "web" / "index.html"
MAX_BODY = 64 * 1024


class WebIO:
    """What the wall reads from and writes to, when it lives in a web page."""

    kind = "web"

    def __init__(self, server: "Server"):
        self.server = server
        self._r, self._w = socket.socketpair()       # input: a socket, so select() works on Windows too
        self.cols, self.rows, self.cw, self.ch = 195, 54, 7, 14
        self.cwf, self.chf = 7.0, 14.0
        self.ready = threading.Event()               # set by the page's first /resize
        self.on_focus = None                         # the Windows host wants to know when keys are needed
        self.on_resize = None
        self.wall = None

    def fileno(self) -> int:
        return self._r.fileno()

    def read(self) -> bytes:
        return self._r.recv(65536)

    def feed(self, data: bytes):
        if data:
            self._w.sendall(data)

    def write(self, b: bytes):
        self.server.broadcast("out", b.decode("utf-8", "surrogateescape"))

    def size(self) -> tuple[int, int]:
        return self.cols, self.rows

    def cell_px(self) -> tuple[int, int]:
        return self.cw, self.ch

    def focus(self, on: bool):
        if self.on_focus:
            try:
                self.on_focus(on)
            except Exception:
                pass

    def theme(self, bg: str):
        self.server.broadcast("theme", bg)

    def resized(self, cols: int, rows: int, cw: float, ch: float):
        changed = (cols, rows) != (self.cols, self.rows)
        self.cols, self.rows, self.cw, self.ch = cols, rows, max(1, int(cw)), max(1, int(ch))
        self.cwf, self.chf = cw, ch                   # CSS pixels, unrounded (the Windows host maps the cursor)
        if self.on_resize:
            self.on_resize(cols, rows, cw, ch)
        if not self.ready.is_set():
            self.ready.set()
        elif self.wall is not None:
            self.wall.flags.add("resize")             # also when only a new page connected: repaint it all
        return changed


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int = 0):
        super().__init__(("127.0.0.1", port), Handler)
        self.token = secrets.token_urlsafe(24)
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()
        self.io = WebIO(self)
        self.font_size = 11
        self.title = "sotto"

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/?t={self.token}"

    def broadcast(self, kind: str, data: str):
        with self.lock:
            for q in self.clients:
                q.put((kind, data))


class Handler(BaseHTTPRequestHandler):
    server: Server
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):                    # quiet
        pass

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _token_ok(self, query: str = "") -> bool:
        tok = self.headers.get("X-Sotto-Token", "")
        if not tok and query:
            tok = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("t", "")
        return secrets.compare_digest(tok, self.server.token)

    def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden")
        path, _, query = self.path.partition("?")
        if path in STATIC:
            f, ctype = STATIC[path]
            return self._send(200, f.read_bytes(), ctype)
        if not self._token_ok(query):
            return self._send(403, b"forbidden")
        if path == "/":
            pal = configmod.load()["palette"]
            html = (PAGE.read_text(encoding="utf-8")
                    .replace("{{BG}}", pal["bg"]).replace("{{FG}}", pal["text"])
                    .replace("{{FONT_SIZE}}", str(self.server.font_size))
                    .replace("{{TITLE}}", self.server.title))
            return self._send(200, html.encode(), "text/html; charset=utf-8",
                              {"Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                                          "style-src 'self' 'unsafe-inline'; font-src 'self'"})
        if path == "/stream":
            return self._stream()
        return self._send(404, b"not found")

    def _stream(self):
        q: queue.Queue = queue.Queue()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        with self.server.lock:
            self.server.clients.append(q)
        try:
            while True:
                try:
                    kind, data = q.get(timeout=15)
                    msg = f"event: {kind}\ndata: {json.dumps(data)}\n\n"
                except queue.Empty:
                    msg = ": keep-alive\n\n"
                self.wfile.write(msg.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with self.server.lock:
                if q in self.server.clients:
                    self.server.clients.remove(q)

    def do_POST(self):
        if not self._host_ok() or not self._token_ok():
            return self._send(403, b"forbidden")
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n < 0 or n > MAX_BODY:
            return self._send(413, b"too large")
        body = self.rfile.read(n) if n else b""
        path = self.path.partition("?")[0]
        if path == "/input":
            self.server.io.feed(body)
            return self._send(204)
        if path == "/resize":
            try:
                d = json.loads(body)
                cols, rows = int(d["cols"]), int(d["rows"])
                cw, ch = float(d.get("cw", 7)), float(d.get("ch", 14))
            except (ValueError, KeyError, TypeError):
                return self._send(400, b"bad resize")
            if not (10 <= cols <= 1000 and 5 <= rows <= 500):
                return self._send(400, b"bad size")
            self.server.io.resized(cols, rows, cw, ch)
            return self._send(204)
        return self._send(404, b"not found")


def start(port: int = 0, font_size: int | None = None) -> Server:
    """Start the server in a background thread; returns it (its .url opens the wallpaper)."""
    srv = Server(port)
    disp = configmod.raw().get("display")
    fs = font_size or (disp.get("font_size") if isinstance(disp, dict) else None) or 11
    srv.font_size = int(fs) if isinstance(fs, (int, float)) and 6 <= fs <= 64 else 11
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def run_wall(srv: Server) -> int:
    """Run the wall against the server's page (blocks). Call once the page has connected."""
    from .wall import Wall, log_error

    set_palette(configmod.load()["palette"])
    wall = Wall(srv.io)
    srv.io.wall = wall
    srv.io.theme(hex_of("bg"))
    try:
        wall.run()
    except (SystemExit, KeyboardInterrupt):
        pass
    except Exception:
        log_error()
        raise
    finally:
        if wall.term:
            wall.close_term()
    return 0


def main(argv: list[str]) -> int:
    """`python -m sotto web [--port N] [--no-open]`: the wallpaper in your browser."""
    port = 0
    if "--port" in argv:
        i = argv.index("--port")
        port = int(argv[i + 1]) if len(argv) > i + 1 and argv[i + 1].isdigit() else 0
    srv = start(port)
    print(f"sotto is drawing at {srv.url}", flush=True)
    print("Open it in a browser (full screen with F11). Ctrl+C stops it.")
    if "--no-open" not in argv and not os.environ.get("SOTTO_NO_BROWSER"):
        webbrowser.open(srv.url)
    try:
        while not srv.io.ready.wait(0.5):
            pass
        return run_wall(srv)
    except KeyboardInterrupt:
        return 0
    finally:
        srv.shutdown()
        time.sleep(0.05)
