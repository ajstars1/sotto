#!/usr/bin/env python3
"""Displays menu: text size per screen, and with a TV plugged in: extend, mirror,
or use only one screen. Control Center > Displays.

Layout changes here last until the next config reload or restart (SUPER+SHIFT+R
brings both screens back). Text sizes are remembered per screen by scale.sh."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qs_ui import SCRIPTS, Rows, notify, run, show  # noqa: E402


def monitors() -> list[dict]:
    try:
        return json.loads(run("hyprctl", "monitors", "all", "-j").stdout)
    except json.JSONDecodeError:
        return []


def label(m: dict) -> str:
    desc = m.get("description") or m["name"]
    # "AOC 1970W 0x0000C5C8" -> "AOC 1970W"
    words = [w for w in desc.split() if not w.startswith("0x")]
    return " ".join(words[:3]) or m["name"]


def scale(direction: str, m: dict) -> None:
    run(os.path.join(SCRIPTS, "scale.sh"), direction, m["description"])


def keyword(rule: str) -> None:
    run("hyprctl", "keyword", "monitor", rule)


def extend() -> None:
    run("hyprctl", "reload")  # the config = every screen on, side by side, saved sizes
    notify("Displays", "Extended: two separate screens")


def mirror(src: dict, others: list[dict]) -> None:
    for o in others:
        keyword(f"{o['name']}, preferred, auto, 1, mirror, {src['name']}")
    notify("Displays", f"Mirroring {label(src)}")


def only(keep: dict, others: list[dict]) -> None:
    if keep.get("disabled"):
        keyword(f"{keep['name']}, preferred, auto, 1")
    for o in others:
        keyword(f"{o['name']}, disable")
    notify("Displays", f"Only {label(keep)}. SUPER+SHIFT+R brings the others back")


def main(selected: int = 1) -> None:
    mons = monitors()
    rows = Rows()
    again: set[int] = set()

    for m in mons:
        head = f"{label(m)} · {m['name']}"
        if m.get("disabled"):
            rows.header(f"{head} · off")
            continue
        mirrored = m.get("mirrorOf") not in (None, "", "none")
        rows.header(f"{head} · {m['width']}×{m['height']}" + (" · mirroring" if mirrored else ""))
        rows.add("󰍹", "Text size", f"{m['scale']:g}×", None, bold=True)
        again.add(rows.add("󰁝", "Bigger", "", lambda m=m: scale("up", m)))
        again.add(rows.add("󰁅", "Smaller", "", lambda m=m: scale("down", m)))

    if len(mons) > 1:
        rows.header("Arrange")
        rows.add("󰍺", "Extend", "two separate screens", extend)
        main_mon = next((m for m in mons if m["name"].startswith(("DP", "eDP"))), mons[0])
        rest = [m for m in mons if m is not main_mon]
        rows.add("󰍹", f"Mirror {label(main_mon)}", "same picture", lambda: mirror(main_mon, rest))
        for m in mons:
            rows.add("󰶐", f"Only {label(m)}", "", lambda m=m: only(m, [o for o in mons if o is not m]))
    else:
        rows.header("Arrange")
        rows.add("󰍺", "One screen connected", "plug in the TV for more")

    rows.back()
    picked = show(rows, "Displays", f"{len(mons)} screen" + ("s" if len(mons) != 1 else ""), selected=selected)
    if not picked:
        return
    idx, action = picked
    if action:
        action()
    if idx in again or action is None:
        main(selected=idx)


if __name__ == "__main__":
    main()
