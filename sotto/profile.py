"""Frame stats from the running wallpaper, written for people and agents to read.

  python -m sotto profile [seconds]

asks the running wall, over its command socket (the one the dock uses), to note
every frame it draws for that long (default 30 s). The wall then writes
~/.cache/sotto/profile-<stamp>.json and this command prints the path.

Per frame: when, why (state, say, clock, hover, trail, terminal...), how many
cells changed, bytes sent to kitty, and the time spent building the frame and
writing it. A summary on top: frames, idle seconds (0 bytes), p50/p95 frame ms,
total bytes, and the same split by cause.

When nothing is being recorded the wall pays one `is None` check per frame.
"""
import json
import math
import os
import re
import socket
import sys
import time

from . import collect

DEFAULT_SECONDS = 30.0
MAX_SECONDS = 600.0
MAX_FRAMES = 20_000          # 30 fps for 10 minutes; past this, frames are only counted
STAMP = re.compile(r"\d{8}-\d{6}")


def path_for(stamp: str):
    return collect.CACHE / f"profile-{stamp}.json"


def changed_cells(prev, cur, only=None) -> int:
    """Cells that differ between two grids (only counted while profiling)."""
    pc, pf, pb, cc, cf, cb = prev.ch, prev.fg, prev.bg, cur.ch, cur.fg, cur.bg
    idx = range(len(cc)) if only is None else only
    return sum(1 for i in idx if pc[i] != cc[i] or pf[i] != cf[i] or pb[i] != cb[i])


class Profile:
    """One recording, owned by the wall while it runs."""

    def __init__(self, seconds: float, stamp: str):
        self.stamp = stamp
        self.started = time.time()
        self.end = self.started + seconds
        self.seconds = seconds
        self.frames: list[tuple[float, str, int, int, float, float]] = []
        self.dropped = 0
        self.mark = time.perf_counter()        # when work on the next frame began

    def frame(self, cause: str, cells: int, nbytes: int, build_s: float, write_s: float):
        if len(self.frames) < MAX_FRAMES:
            self.frames.append((time.time(), cause, cells, nbytes, build_s, write_s))
        else:
            self.dropped += 1
        self.mark = time.perf_counter()

    def summary(self, until: float) -> dict:
        duration = max(0.0, until - self.started)
        ms = sorted((f[4] + f[5]) * 1000 for f in self.frames)
        busy = {int(f[0] - self.started) for f in self.frames if f[3]}
        whole = int(duration)
        by_cause: dict[str, dict] = {}
        for f in self.frames:
            c = by_cause.setdefault(f[1], {"frames": 0, "bytes": 0, "cells": 0})
            c["frames"] += 1
            c["bytes"] += f[3]
            c["cells"] += f[2]
        return {
            "frames": len(self.frames) + self.dropped,
            "frames_recorded": len(self.frames),
            "frames_with_output": sum(1 for f in self.frames if f[3]),
            "seconds": round(duration, 3),
            "idle_seconds": sum(1 for s in range(whole) if s not in busy),
            "whole_seconds": whole,
            "total_bytes": sum(f[3] for f in self.frames),
            "total_cells": sum(f[2] for f in self.frames),
            "frame_ms_p50": round(pct(ms, 50), 3),
            "frame_ms_p95": round(pct(ms, 95), 3),
            "frame_ms_max": round(ms[-1], 3) if ms else 0.0,
            "by_cause": by_cause,
        }

    def save(self) -> str:
        """Atomic write (temp file + rename), so a reader never sees half a file."""
        now = time.time()
        data = {
            "sotto_profile": 1,
            "started_at": round(self.started, 3),
            "requested_seconds": self.seconds,
            "pid": os.getpid(),
            "summary": self.summary(now),
            "fields": {
                "ts": "unix time the frame was written",
                "t": "seconds since the recording started",
                "cause": "what asked for the frame",
                "cells": "cells that changed on screen",
                "bytes": "bytes written to the terminal",
                "build_ms": "work before writing: input, layout, compose, since the last frame or wake-up",
                "write_ms": "diffing the grid into escape codes and writing them",
            },
            "frames": [
                {"ts": round(ts, 4), "t": round(ts - self.started, 4), "cause": cause, "cells": cells,
                 "bytes": nb, "build_ms": round(b * 1000, 3), "write_ms": round(w * 1000, 3)}
                for ts, cause, cells, nb, b, w in self.frames
            ],
        }
        out = path_for(self.stamp)
        collect.CACHE.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, out)
        return str(out)


def pct(sorted_ms: list[float], p: float) -> float:
    """Nearest-rank percentile of an already sorted list."""
    if not sorted_ms:
        return 0.0
    k = max(0, min(len(sorted_ms) - 1, math.ceil(p / 100 * len(sorted_ms)) - 1))
    return sorted_ms[k]


def parse_request(msg: list[str]) -> tuple[float, str] | None:
    """`profile <seconds> <stamp>` from the socket; anything else is ignored."""
    if len(msg) != 3 or not STAMP.fullmatch(msg[2]):
        return None
    try:
        secs = float(msg[1])
    except ValueError:
        return None
    if not 0 < secs <= MAX_SECONDS:
        return None
    return secs, msg[2]


def request(argv: list[str]) -> int:
    """The `python -m sotto profile [seconds]` command."""
    from .wall import SOCKET

    try:
        secs = float(argv[0]) if argv else DEFAULT_SECONDS
    except ValueError:
        print(f"usage: python -m sotto profile [seconds]  (more than 0, at most {int(MAX_SECONDS)})", file=sys.stderr)
        return 2
    if not 0 < secs <= MAX_SECONDS:
        print(f"seconds must be between 0 and {int(MAX_SECONDS)}", file=sys.stderr)
        return 2
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = path_for(stamp)
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(f"profile {secs:g} {stamp}".encode(), SOCKET)
    except OSError as e:
        print(f"the wallpaper isn't running (could not reach {SOCKET}: {e.strerror or e})", file=sys.stderr)
        return 1
    print(f"recording {secs:g} s of frames...", file=sys.stderr)
    deadline = time.time() + secs + 5
    while time.time() < deadline:
        if out.exists():
            print(out)
            return 0
        time.sleep(0.25)
    print("no profile came back. The running wall may predate `profile` (restart it with "
          "bin/sotto-restart), or another recording was still running.", file=sys.stderr)
    return 1
