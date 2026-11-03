#!/usr/bin/env python3
"""Power menu: click OFF in the bar, SUPER+SHIFT+E, or Control Center > Power.

Drops down under the bar like the other quick menus: click outside, click OFF again
or press Esc to close. Restart and shut down ask first (Cancel is pre-selected)."""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qs_ui import Rows, confirm, run, show, spawn  # noqa: E402


def logout() -> None:
    if shutil.which("hyprshutdown"):
        spawn("hyprshutdown")
    else:
        run("hyprctl", "dispatch", "exit")


def main() -> None:
    rows = Rows()
    rows.add("󰌾", "Lock", "SUPER+L", lambda: spawn("loginctl", "lock-session"))
    rows.add("󰍃", "Log out", "", logout)
    rows.add("󰤄", "Sleep", "", lambda: spawn("systemctl", "suspend"))
    rows.add("󰜉", "Restart…", "", lambda: confirm("Restart the computer?", "Restart now") and spawn("systemctl", "reboot"))
    rows.add("󰐥", "Shut down…", "", lambda: confirm("Shut down the computer?", "Shut down now")
             and spawn("systemctl", "poweroff"))
    rows.back()
    picked = show(rows, "Power", "", selected=0)
    if picked and picked[1]:
        picked[1]()


if __name__ == "__main__":
    main()
