"""Your own blocks on the wallpaper ([[widget]] in config.toml).

A widget has one source:
  text    = ["a line", "another"]             fixed lines
  file    = "~/notes/today.md"                 the file's first lines (re-read when it changes)
  command = "curl -s 'wttr.in?format=3'"       a program, run every `every` seconds

A command prints plain lines, or JSON for colours and marks:
  {"lines": ["plain", {"text": "in ochre", "color": "accent"}, {"text": "late!", "color": "alert"}]}
Colours are the theme's names (text dim accent alert line) or "#rrggbb".
Commands run in the background with a timeout, so a slow one never stalls the wallpaper.
"""
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

CTRL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f\x7f]")   # ANSI codes and control chars
MAX_LINE = 200


def _clean(s: str) -> str:
    return CTRL.sub("", s).replace("\t", "  ").rstrip()[:MAX_LINE]


def parse(out: str, limit: int) -> list[tuple[str, str]]:
    """A command's output as (text, colour) rows."""
    s = out.strip()
    if s.startswith("{"):
        try:
            data = json.loads(s)
            rows = []
            for ln in data.get("lines", []):
                if isinstance(ln, dict):
                    rows.append((_clean(str(ln.get("text", ""))), str(ln.get("color", ""))))
                else:
                    rows.append((_clean(str(ln)), ""))
            return rows[:limit]
        except (ValueError, AttributeError):
            pass
    return [(_clean(ln), "") for ln in out.splitlines() if ln.strip()][:limit]


def run_once(w: dict) -> list[tuple[str, str]]:
    """The widget's rows now. Never raises; a failure becomes one alert row."""
    try:
        if w.get("text"):
            return [(_clean(t), "") for t in w["text"]][: w["lines"]]
        if w.get("file"):
            p = Path(os.path.expanduser(os.path.expandvars(w["file"])))
            with open(p, encoding="utf-8", errors="replace") as f:
                lines = [ln for ln in (f.readline() for _ in range(200)) if ln]
            return [(_clean(ln), "") for ln in lines if ln.strip()][: w["lines"]]
        if w.get("command"):
            r = subprocess.run(os.path.expandvars(os.path.expanduser(w["command"])), shell=True, capture_output=True,
                               text=True, timeout=w.get("timeout", 10), stdin=subprocess.DEVNULL,
                               encoding="utf-8", errors="replace")
            rows = parse(r.stdout or "", w["lines"])
            if r.returncode != 0 and not rows:
                err = (r.stderr or "").strip().splitlines()
                return [(f"exit {r.returncode}" + (f": {_clean(err[-1])[:80]}" if err else ""), "alert")]
            return rows
    except subprocess.TimeoutExpired:
        return [(f"took longer than {w.get('timeout', 10)} s", "alert")]
    except OSError as e:
        return [(f"{type(e).__name__}: {e.strerror or e}", "alert")]
    return []


class Runner:
    """Keeps every widget's rows fresh in the background; `rows` is what the wallpaper draws."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.rows: dict[str, list[tuple[str, str]]] = {}
        self.widgets: list[dict] = []
        self._due: dict[str, float] = {}
        self._mtimes: dict[str, float] = {}
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def configure(self, widgets: list[dict]):
        with self._lock:
            self.widgets = widgets
            ids = {w["id"] for w in widgets}
            self.rows = {k: v for k, v in self.rows.items() if k in ids}
            self._due, self._mtimes = {}, {}
        self.poll(time.time())

    def _file_changed(self, w: dict) -> bool:
        try:
            m = Path(os.path.expanduser(os.path.expandvars(w["file"]))).stat().st_mtime
        except OSError:
            m = -1.0
        changed = self._mtimes.get(w["id"]) != m
        self._mtimes[w["id"]] = m
        return changed

    def poll(self, now: float):
        """Cheap; called from the wallpaper's loop. Starts whatever is due."""
        for w in list(self.widgets):
            wid = w["id"]
            if wid in self._busy:
                continue
            if w.get("command"):
                if now < self._due.get(wid, 0):
                    continue
                self._due[wid] = now + w["every"]
                self._busy.add(wid)
                threading.Thread(target=self._work, args=(w,), daemon=True).start()
            elif w.get("file"):
                if now < self._due.get(wid, 0):
                    continue
                self._due[wid] = now + 2                      # a file is checked every 2 s, re-read on change
                if self._file_changed(w):
                    self._store(wid, run_once(w))
            elif wid not in self.rows:
                self._store(wid, run_once(w))

    def _work(self, w: dict):
        try:
            self._store(w["id"], run_once(w))
        finally:
            self._busy.discard(w["id"])

    def _store(self, wid: str, rows: list[tuple[str, str]]):
        with self._lock:
            if not any(w["id"] == wid for w in self.widgets) or self.rows.get(wid) == rows:
                return
            self.rows[wid] = rows
        self.on_change()

    def snapshot(self) -> dict[str, list[tuple[str, str]]]:
        with self._lock:
            return dict(self.rows)


def run_all(widgets: list[dict]) -> dict[str, list[tuple[str, str]]]:
    """Every widget once, for previews."""
    return {w["id"]: run_once(w) for w in widgets}
