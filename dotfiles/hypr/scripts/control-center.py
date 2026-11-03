#!/usr/bin/env python3
"""Control Center, like the one on a Mac: click 󰔡 in the bar or press SUPER+A.

Wi-Fi, Bluetooth, Sound and Displays open their own menus (each with a Back row);
Night light and Do not disturb switch right here and the panel stays open.
"""
import glob
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qs_ui import SCRIPTS, Rows, open_menu, run, show, spawn, toggle_text  # noqa: E402

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "crt-mode")


def wifi_status() -> str:
    out = run("nmcli", "-t", "-f", "TYPE,STATE,CONNECTION", "device").stdout.splitlines()
    for line in out:
        typ, state, conn = (line.split(":", 2) + ["", ""])[:3]
        if typ == "wifi":
            if state == "connected":
                return conn.replace("\\:", ":")
            return "Off" if "unavailable" in state else "Not connected"
    return "No adapter"


def cable() -> bool:
    return any(l.startswith("ethernet:connected") for l in
               run("nmcli", "-t", "-f", "TYPE,STATE", "device").stdout.splitlines())


def bt_status() -> str:
    if not glob.glob("/sys/class/bluetooth/hci*"):
        return "No adapter"
    return "On" if "Powered: yes" in run("bluetoothctl", "show", timeout=5).stdout else "Off"


def sound_status() -> str:
    out = run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@").stdout
    m = re.search(r"([\d.]+)", out)
    if "MUTED" in out:
        return "Muted"
    return f"{round(float(m.group(1)) * 100)}%" if m else "?"


def display_status() -> str:
    try:
        mons = json.loads(run("hyprctl", "monitors", "-j").stdout)
    except json.JSONDecodeError:
        return ""
    if len(mons) == 1:
        return f"{mons[0]['scale']:g}× text"
    return f"{len(mons)} screens"


def night_on() -> bool:
    try:
        return open(STATE).read().strip() == "night"
    except OSError:
        return False


def dnd_on() -> bool:
    return "do-not-disturb" in run("makoctl", "mode").stdout.split()


def recording() -> bool:
    return "recording" in run(os.path.join(SCRIPTS, "record.sh"), "status").stdout


def main(selected: int = 1) -> None:
    rows = Rows()
    stay: set[int] = set()

    rows.header("Connections")
    wifi = wifi_status()
    rows.add("󰖩", "Wi-Fi", f"{wifi} ›", lambda: open_menu("wifi-menu.py"), bold=True)
    if cable():
        rows.add("󰈀", "Ethernet", "connected", lambda: open_menu("wifi-menu.py"))
    rows.add("󰂯", "Bluetooth", f"{bt_status()} ›", lambda: open_menu("bt-menu.py"), bold=True)

    rows.header("Sound & screen")
    rows.add("󰕾", "Sound", f"{sound_status()} ›", lambda: open_menu("sound-menu.py"), bold=True)
    rows.add("󰍹", "Displays", f"{display_status()} ›", lambda: open_menu("display-menu.py"), bold=True)
    n = night_on()
    stay.add(rows.add("󰖔", "Night light", toggle_text(n),
                      lambda: run(os.path.join(SCRIPTS, "crt-mode.sh"), "night"), strong_right=n))
    d = dnd_on()
    stay.add(rows.add("󰂛", "Do not disturb", toggle_text(d),
                      lambda: run("makoctl", "mode", "-t", "do-not-disturb"), strong_right=d))

    rows.header("Capture")
    rows.add("󰹑", "Screenshot", "an area", lambda: (time.sleep(0.3), spawn(os.path.join(SCRIPTS, "screenshot.sh"), "region")))
    if recording():
        rows.add("󰻃", "Stop recording", "● REC", lambda: run(os.path.join(SCRIPTS, "record.sh"), "toggle"),
                 strong_right=True)
    else:
        rows.add("󰑋", "Record the screen", "an area",
                 lambda: (time.sleep(0.3), spawn(os.path.join(SCRIPTS, "record.sh"), "region")))

    rows.header("System")
    rows.add("󰌾", "Lock screen", "SUPER+L", lambda: spawn("loginctl", "lock-session"))
    rows.add("󰐥", "Power…", "restart, shut down", lambda: open_menu("power-menu.py"))
    rows.add("󰌌", "Keyboard shortcuts", "SUPER+F1", lambda: spawn(os.path.join(SCRIPTS, "cheatsheet.sh")))

    picked = show(rows, "Control Center", time.strftime("%a %d %b · %H:%M"), selected=selected)
    if not picked:
        return
    idx, action = picked
    if action:
        action()
    if idx in stay:
        main(selected=idx)


if __name__ == "__main__":
    main()
