"""~/.config/sotto/config.toml: everything you can change about the wallpaper.

Every key is optional; a missing or broken file gives the default wallpaper. `python -m sotto
config check` lists what's wrong (unknown keys, bad colours, a widget without a source), and
the running wallpaper re-reads the file as soon as you save it.

    name = "Sam"
    theme = "night"                       # fresco · night · paper · sage · dusk
    [colors]  accent = "#d0a35c"          # override any colour of the theme
    [clock]   format = "12h"              # "24h", "12h", or a strftime pattern; show = false hides it
    [greeting] text = "Hi {name}"         # {part} {name} {date}; date = "%a %d %b"
    [layout]  left = ["agents"]  right = ["today", "weather"]
    [effects] trail = true  dissolve = true  trail_chars = "·•∙"
    [[widget]] id = "weather"  title = "WEATHER"  command = "curl -s 'wttr.in?format=3'"  every = 900

See docs/CUSTOMIZE.md for every key with examples.
"""
import os
import re
import tomllib
from pathlib import Path

HOME = Path.home()
PATH = Path(os.environ.get("SOTTO_CONFIG") or Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "sotto" / "config.toml")

COLOR_KEYS = ("bg", "faint", "surface", "line", "dim", "text", "accent", "alert")
THEMES: dict[str, dict[str, str]] = {
    # light, from an old ceiling fresco: the original
    "fresco": {"bg": "#ece2d0", "faint": "#e2d6c1", "surface": "#e2d4bd", "line": "#cdb99b",
               "dim": "#8f7759", "text": "#3d2c1f", "accent": "#a7783f", "alert": "#a9532f"},
    # dark: lamp light on walnut
    "night": {"bg": "#1c1a17", "faint": "#26231f", "surface": "#2c2924", "line": "#3d3830",
              "dim": "#8f8570", "text": "#e8dcc6", "accent": "#d0a35c", "alert": "#d9774f"},
    # light and cool: ink on paper
    "paper": {"bg": "#f4f4f0", "faint": "#eaeae4", "surface": "#e4e4dd", "line": "#d0d0c8",
              "dim": "#6e7178", "text": "#1f2328", "accent": "#2f6fca", "alert": "#c7362c"},
    # light green-grey, like a herb garden
    "sage": {"bg": "#e3e8dc", "faint": "#d8dfd0", "surface": "#d2dac8", "line": "#b9c4b0",
             "dim": "#6b7c68", "text": "#2c3a2e", "accent": "#5f7f4f", "alert": "#b05a3c"},
    # dark violet, the hour after sunset
    "dusk": {"bg": "#2b2238", "faint": "#342a44", "surface": "#3a2f4b", "line": "#4a3d5c",
             "dim": "#a08fb0", "text": "#efe3f5", "accent": "#e0a96d", "alert": "#ef7a85"},
}
BLOCKS = ("agents", "today")                  # built-in blocks; widget ids are blocks too
DEFAULT_LAYOUT = {"left": ["agents"], "right": ["today"]}
TOP_KEYS = {"name", "hide", "pin", "today", "theme", "colors", "clock", "greeting", "layout", "effects", "widget", "display"}
SECTION_KEYS = {
    "clock": {"format", "show"},
    "greeting": {"text", "date", "show"},
    "layout": {"left", "right"},
    "effects": {"trail", "dissolve", "trail_chars", "fps"},
    "today": {"ics", "command"},
    "display": {"font_size"},
}
WIDGET_KEYS = {"id", "title", "command", "text", "file", "every", "lines", "color", "timeout"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def raw() -> dict:
    """The file as TOML, {} when it's missing or broken."""
    try:
        with open(PATH, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def mtime() -> float:
    try:
        return PATH.stat().st_mtime
    except OSError:
        return 0.0


def _bool(v, default: bool) -> bool:
    return v if isinstance(v, bool) else default


def _list(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v] if isinstance(v, list) else []


def palette(cfg: dict) -> dict[str, str]:
    theme = cfg.get("theme", "fresco")
    base = dict(THEMES.get(theme if isinstance(theme, str) else "fresco", THEMES["fresco"]))
    colors = cfg.get("colors")
    if isinstance(colors, dict):
        for k, v in colors.items():
            if k in COLOR_KEYS and isinstance(v, str) and HEX.match(v):
                base[k] = v.lower()
    return base


def widgets(cfg: dict) -> list[dict]:
    out = []
    for i, w in enumerate(cfg.get("widget") or []):
        if not isinstance(w, dict):
            continue
        wid = str(w.get("id") or f"widget{i + 1}")
        out.append({
            "id": wid,
            "title": str(w.get("title", wid.upper())),
            "command": w.get("command") if isinstance(w.get("command"), str) else None,
            "text": _list(w.get("text")),
            "file": w.get("file") if isinstance(w.get("file"), str) else None,
            "every": max(5, int(w.get("every", 300))) if isinstance(w.get("every", 300), (int, float)) else 300,
            "lines": max(1, min(30, int(w.get("lines", 8)))) if isinstance(w.get("lines", 8), int) else 8,
            "color": w.get("color") if isinstance(w.get("color"), str) else "text",
            "timeout": max(1, min(60, int(w.get("timeout", 10)))) if isinstance(w.get("timeout", 10), (int, float)) else 10,
        })
    return out


def load(cfg: dict | None = None) -> dict:
    """The settings the wallpaper uses, with every default filled in."""
    cfg = raw() if cfg is None else cfg
    sec = lambda k: cfg.get(k) if isinstance(cfg.get(k), dict) else {}     # noqa: E731
    clock, greet, layout, fx = sec("clock"), sec("greeting"), sec("layout"), sec("effects")
    fmt = clock.get("format", "24h")
    fmt = {"24h": "%H:%M", "12h": "%I:%M"}.get(fmt, fmt) if isinstance(fmt, str) else "%H:%M"
    ws = widgets(cfg)
    known = set(BLOCKS) | {w["id"] for w in ws}
    lay = {}
    for side in ("left", "right"):
        blocks = _list(layout.get(side)) if side in layout else list(DEFAULT_LAYOUT[side])
        lay[side] = [b for b in blocks if b in known]
    if "right" not in layout:                  # widgets nobody placed go under Today
        placed = set(lay["left"]) | set(lay["right"])
        lay["right"] += [w["id"] for w in ws if w["id"] not in placed]
    chars = fx.get("trail_chars", "·•∙")
    disp = sec("display")
    fs = disp.get("font_size")
    return {
        "display": {"font_size": fs if isinstance(fs, (int, float)) and 6 <= fs <= 64 else None},
        "palette": palette(cfg),
        "clock": {"format": fmt, "show": _bool(clock.get("show"), True)},
        "greeting": {"text": str(greet.get("text", "Good {part}, {name}")),
                     "date": str(greet.get("date", "%A %d %B")), "show": _bool(greet.get("show"), True)},
        "layout": lay,
        "effects": {"trail": _bool(fx.get("trail"), True), "dissolve": _bool(fx.get("dissolve"), True),
                    "trail_chars": chars if isinstance(chars, str) and chars.strip() else "·•∙",
                    "fps": max(5, min(60, int(fx.get("fps", 30)))) if isinstance(fx.get("fps", 30), int) else 30},
        "widgets": ws,
    }


def check(cfg: dict | None = None) -> list[str]:
    """Problems in the config file, as sentences. Empty when it's fine."""
    if cfg is None:
        if not PATH.exists():
            return []
        try:
            with open(PATH, "rb") as f:
                cfg = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            return [f"{PATH} is not valid TOML: {e}"]
        except OSError as e:
            return [f"can't read {PATH}: {e.strerror}"]
    probs = []
    for k in cfg:
        if k not in TOP_KEYS:
            probs.append(f"unknown key '{k}' (known: {', '.join(sorted(TOP_KEYS))})")
    for sec, keys in SECTION_KEYS.items():
        v = cfg.get(sec)
        if v is None:
            continue
        if not isinstance(v, dict):
            probs.append(f"[{sec}] should be a table")
            continue
        for k in v:
            if k not in keys:
                probs.append(f"unknown key '{k}' in [{sec}] (known: {', '.join(sorted(keys))})")
    theme = cfg.get("theme")
    if theme is not None and theme not in THEMES:
        probs.append(f"theme '{theme}' doesn't exist (pick one of: {', '.join(THEMES)})")
    colors = cfg.get("colors")
    if isinstance(colors, dict):
        for k, v in colors.items():
            if k not in COLOR_KEYS:
                probs.append(f"unknown colour '{k}' in [colors] (known: {', '.join(COLOR_KEYS)})")
            elif not (isinstance(v, str) and HEX.match(v)):
                probs.append(f"colour {k} = {v!r} should look like \"#a7783f\"")
    ids = set()
    for i, w in enumerate(cfg.get("widget") or []):
        name = f"widget #{i + 1}" + (f" ('{w.get('id')}')" if isinstance(w, dict) and w.get("id") else "")
        if not isinstance(w, dict):
            probs.append(f"{name} should be a table ([[widget]])")
            continue
        for k in w:
            if k not in WIDGET_KEYS:
                probs.append(f"unknown key '{k}' in {name} (known: {', '.join(sorted(WIDGET_KEYS))})")
        srcs = [k for k in ("command", "text", "file") if w.get(k)]
        if len(srcs) != 1:
            probs.append(f"{name} needs exactly one of command, text or file (has {', '.join(srcs) or 'none'})")
        wid = str(w.get("id") or f"widget{i + 1}")
        if wid in ids or wid in BLOCKS:
            probs.append(f"{name}: id '{wid}' is used twice (or is a built-in block)")
        ids.add(wid)
        c = w.get("color")
        if c is not None and not (c in COLOR_KEYS or (isinstance(c, str) and HEX.match(c))):
            probs.append(f"{name}: color should be a colour name ({', '.join(COLOR_KEYS)}) or \"#rrggbb\"")
    layout = cfg.get("layout")
    if isinstance(layout, dict):
        known = set(BLOCKS) | ids
        for side in ("left", "right"):
            for b in _list(layout.get(side)):
                if b not in known:
                    probs.append(f"[layout] {side} has '{b}', which isn't a block (known: {', '.join(sorted(known))})")
    disp = cfg.get("display")
    if isinstance(disp, dict) and "font_size" in disp and not (
            isinstance(disp["font_size"], (int, float)) and 6 <= disp["font_size"] <= 64):
        probs.append("[display] font_size should be a number from 6 to 64")
    clock = cfg.get("clock")
    if isinstance(clock, dict) and isinstance(clock.get("format"), str) and clock["format"] not in ("24h", "12h") \
            and "%" not in clock["format"]:
        probs.append("[clock] format should be \"24h\", \"12h\" or a strftime pattern like \"%H:%M\"")
    return probs


TEMPLATE = '''# sotto: your wallpaper. Save this file and the running wallpaper updates by itself.
# Check it:   python -m sotto config check
# See it:     python -m sotto preview out.png      (renders one frame to a PNG)
# Every key:  docs/CUSTOMIZE.md

name = "{name}"              # used in the greeting
theme = "fresco"             # fresco · night · paper · sage · dusk   (python -m sotto themes)

# [colors]                   # override any colour of the theme
# accent = "#a7783f"         # keys: bg faint surface line dim text accent alert

[clock]
format = "24h"               # "24h", "12h", or a strftime pattern like "%H:%M:%S"
show = true

[greeting]
text = "Good {{part}}, {{name}}"   # {{part}} = morning/afternoon/evening/night, {{name}}, {{date}}
date = "%A %d %B"            # strftime; shown before the greeting

[layout]                     # what each column shows, top to bottom: agents, today, or a widget id
left = ["agents"]
right = ["today", "focus"]

# [display]
# font_size = 11             # the wallpaper's text size (macOS: points; Windows and browser: CSS pixels).
#                            # DepartureMono is pixel-sharp at 11, 22, 33... Linux keeps kitty's 8.25 pt.

[effects]
trail = true                 # dots follow the mouse
dissolve = true              # text and the clock come loose under the mouse
trail_chars = "·•∙"

# Your own blocks. Each one has exactly one source: text, file or command.
[[widget]]
id = "focus"
title = "FOCUS"
text = ["Edit this list in ~/.config/sotto/config.toml"]

# [[widget]]
# id = "weather"
# title = "WEATHER"
# command = "curl -s 'https://wttr.in/?format=3'"   # any program; each line it prints is a row
# every = 900                                       # seconds between runs

# [[widget]]
# id = "notes"
# title = "NOTES"
# file = "~/notes/today.md"                         # its first lines
# lines = 6

# [today]                    # the Today block: your calendar and tasks
# ics = ["https://calendar.google.com/calendar/ical/.../basic.ics"]
# command = "my-tasks --json"
'''


def init(name: str, force: bool = False) -> tuple[Path, bool]:
    """Write a commented starter config. Returns (path, written)."""
    if PATH.exists() and not force:
        return PATH, False
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(TEMPLATE.format(name=name.replace('"', "")), encoding="utf-8")
    return PATH, True
