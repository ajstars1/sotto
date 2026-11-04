#!/usr/bin/env python3
"""Top-bar badge: a quiet ochre "● claude" while an agent is driving the desktop.

The desk tools in agent-os write ~/.cache/aos-desk/acting.json as
  {"tool": "<name>", "until": <unix seconds>}
before they move the mouse or type. The badge shows while now < until and hides
when the file is missing, malformed or expired.

Waybar runs this once and reads one JSON line per change. It sleeps on inotify,
wakes for writes to that folder and for the moment the badge expires; while the
folder doesn't exist yet it looks once a second. Only two fields are read, from
at most 4 KB of a regular file; the tool name is reduced to [A-Za-z0-9_.:-]."""
import ctypes
import json
import math
import os
import re
import select
import stat
import struct
import time

DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "aos-desk")
FILE = os.path.join(DIR, "acting.json")
MAX_BYTES = 4096
LABEL = "● claude"

IN_CLOSE_WRITE, IN_MOVED_FROM, IN_MOVED_TO = 0x08, 0x40, 0x80
IN_CREATE, IN_DELETE, IN_DELETE_SELF, IN_MOVE_SELF, IN_IGNORED = 0x100, 0x200, 0x400, 0x800, 0x8000
WATCH = IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF
EVENT = struct.Struct("iIII")


def read_acting(path: str = FILE) -> tuple[str, float] | None:
    """(tool, until) from the file, or None if it's missing or not exactly what we expect."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        raw = os.read(fd, MAX_BYTES + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    tool, until = data.get("tool"), data.get("until")
    if not isinstance(tool, str) or isinstance(until, bool) or not isinstance(until, (int, float)):
        return None
    if not math.isfinite(until):
        return None
    return re.sub(r"[^A-Za-z0-9_.:-]", "", tool)[:32] or "agent", float(until)


def render(acting: tuple[str, float] | None, now: float) -> dict:
    if not acting or now >= acting[1]:
        return {"text": "", "class": "idle"}
    return {"text": LABEL, "class": "acting", "tooltip": f"An agent is using the desktop ({acting[0]})"}


class Watch:
    """inotify on the folder, through libc (no extra packages)."""

    def __init__(self):
        self.libc = None
        self.fd = -1
        self.wd = -1
        try:
            self.libc = ctypes.CDLL(None, use_errno=True)
            self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
        except (OSError, AttributeError):
            self.fd = -1

    @property
    def active(self) -> bool:
        return self.wd >= 0

    def arm(self):
        if self.fd >= 0 and self.wd < 0 and os.path.isdir(DIR):
            self.wd = self.libc.inotify_add_watch(self.fd, DIR.encode(), WATCH)

    def wait(self, timeout: float | None):
        fds = [self.fd] if self.active else []
        r, _, _ = select.select(fds, [], [], timeout)
        if r:
            self.drain()

    def drain(self):
        try:
            buf = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        i = 0
        while i + EVENT.size <= len(buf):
            _, mask, _, n = EVENT.unpack_from(buf, i)
            if mask & (IN_IGNORED | IN_DELETE_SELF | IN_MOVE_SELF):
                self.wd = -1                          # the folder went away: look again once a second
            i += EVENT.size + n


def main() -> int:
    watch = Watch()
    last = None
    while True:
        watch.arm()
        acting = read_acting()
        now = time.time()
        out = json.dumps(render(acting, now), ensure_ascii=False)
        if out != last:
            print(out, flush=True)
            last = out
        if not watch.active:
            timeout: float | None = 1.0
        elif acting and now < acting[1]:
            timeout = min(acting[1] - now, 60.0) + 0.01
        else:
            timeout = None                            # nothing to expire: sleep until the folder changes
        watch.wait(timeout)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BrokenPipeError, KeyboardInterrupt):
        pass
