#!/usr/bin/env python3
"""Sound menu: volume, mute, which speakers / microphone to use (TV speakers show up
here when the TV is plugged in over HDMI). Click VOL in the bar, or Sound in the
Control Center. The menu stays open while you step the volume."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qs_ui import Rows, notify, run, show, spawn  # noqa: E402

SINK = "@DEFAULT_AUDIO_SINK@"
SOURCE = "@DEFAULT_AUDIO_SOURCE@"
LINE = re.compile(r"^[\s│├└─]*(\*)?\s*(\d+)\.\s+(.+?)\s*(\[vol:.*\])?\s*$")


def volume(target: str) -> tuple[int, bool]:
    out = run("wpctl", "get-volume", target).stdout  # "Volume: 0.45 [MUTED]"
    m = re.search(r"([\d.]+)", out)
    return (round(float(m.group(1)) * 100) if m else 0), "MUTED" in out


def devices() -> dict[str, list[dict]]:
    """{'Sinks': [...], 'Sources': [...]} from `wpctl status` (Audio section only)."""
    out: dict[str, list[dict]] = {"Sinks": [], "Sources": []}
    section, in_audio = None, False
    for raw in run("wpctl", "status").stdout.splitlines():
        if raw.startswith("Audio"):
            in_audio = True
            continue
        if raw and not raw[0].isspace() and raw[0] not in "│├└":
            in_audio = False
        if not in_audio:
            continue
        head = re.search(r"(Devices|Sinks|Sources|Filters|Streams):", raw)
        if head:
            section = head.group(1)
            continue
        if section in out:
            m = LINE.match(raw)
            if m:
                out[section].append({"id": m.group(2), "name": m.group(3), "default": bool(m.group(1))})
    return out


def bar(pct: int, muted: bool) -> str:
    filled = round(min(pct, 100) / 10)
    return ("▮" * filled + "▯" * (10 - filled)) + (" muted" if muted else f" {pct}%")


def set_vol(change: str) -> None:
    run("wpctl", "set-volume", "-l", "1", SINK, change)
    run("wpctl", "set-mute", SINK, "0")


def main(selected: int = 1) -> None:
    pct, muted = volume(SINK)
    mic_pct, mic_muted = volume(SOURCE)
    devs = devices()
    rows = Rows()
    again = {}

    rows.header("Volume")
    again[rows.add("󰕾" if not muted else "󰝟", bar(pct, muted), "", None, bold=True)] = None
    again[rows.add("󰝝", "Louder", "+5%", lambda: set_vol("5%+"))] = True
    again[rows.add("󰝞", "Quieter", "−5%", lambda: set_vol("5%-"))] = True
    again[rows.add("󰝟" if not muted else "󰕾", "Unmute" if muted else "Mute", "",
                   lambda: run("wpctl", "set-mute", SINK, "toggle"))] = True

    rows.header("Play sound on")
    for d in devs["Sinks"]:
        again[rows.add("󰓃", d["name"], "● in use" if d["default"] else "",
                       lambda d=d: (run("wpctl", "set-default", d["id"]), notify("Sound", f"Playing on {d['name']}")),
                       bold=d["default"], strong_right=d["default"])] = True
    if not devs["Sinks"]:
        rows.add("󰓄", "No speakers found", "")

    rows.header("Microphone")
    for d in devs["Sources"]:
        again[rows.add("󰍬", d["name"], "● in use" if d["default"] else "",
                       lambda d=d: run("wpctl", "set-default", d["id"]),
                       bold=d["default"], strong_right=d["default"])] = True
    if devs["Sources"]:
        again[rows.add("󰍭" if not mic_muted else "󰍬", "Unmute microphone" if mic_muted else "Mute microphone",
                       "muted" if mic_muted else f"{mic_pct}%",
                       lambda: run("wpctl", "set-mute", SOURCE, "toggle"))] = True

    rows.header("More")
    rows.add("󰒓", "Sound settings…", "", lambda: spawn("pavucontrol"))
    rows.back()

    picked = show(rows, "Sound", "", selected=selected)
    if not picked:
        return
    idx, action = picked
    if action:
        action()
    if again.get(idx) or action is None:
        main(selected=idx)  # stay open, like a slider


if __name__ == "__main__":
    main()
