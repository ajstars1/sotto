#!/usr/bin/env python3
"""Bluetooth menu: on/off, paired devices (connect / disconnect / forget), pair a new
one. This PC has no Bluetooth chip today; plug in a USB Bluetooth dongle and the
menu starts working. Control Center > Bluetooth."""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qs_ui import Rows, confirm, notify, run, show  # noqa: E402

DEV = re.compile(r"^Device ([0-9A-F:]{17}) (.+)$")


def bt(*args: str, timeout: int = 15):
    return run("bluetoothctl", *args, timeout=timeout)


def has_adapter() -> bool:
    return bool(glob.glob("/sys/class/bluetooth/hci*"))


def service_up() -> bool:
    return run("systemctl", "is-active", "bluetooth").stdout.strip() == "active"


def powered() -> bool:
    return "Powered: yes" in bt("show").stdout


def listed(*filt: str) -> list[tuple[str, str]]:
    out = []
    for line in bt("devices", *filt).stdout.splitlines():
        m = DEV.match(line.strip())
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def connected(mac: str) -> bool:
    return "Connected: yes" in bt("info", mac).stdout


def pair_new() -> None:
    notify("Bluetooth", "Looking for devices for 10 seconds… put yours in pairing mode")
    bt("--timeout", "10", "scan", "on", timeout=15)
    paired = {m for m, _ in listed("Paired")}
    found = [(m, n) for m, n in listed() if m not in paired and n.replace("-", ":") != m]
    rows = Rows()
    if not found:
        rows.add("󰂲", "Nothing found", "try again")
    for mac, name in found:
        def go(mac: str = mac, name: str = name) -> None:
            notify("Bluetooth", f"Pairing {name}…")
            ok = bt("pair", mac, timeout=30).returncode == 0
            bt("trust", mac)
            ok = bt("connect", mac, timeout=20).returncode == 0 or ok
            notify("Bluetooth", f"{name} {'connected' if ok else 'did not pair'}")
        rows.add("󰂱", name, mac[-5:], go)
    rows.add("󰑐", "Search again", "", pair_new)
    rows.add("󰁍", "Bluetooth", "", main)
    picked = show(rows, "Pair a device", f"{len(found)} found")
    if picked and picked[1]:
        picked[1]()


def device_menu(mac: str, name: str) -> None:
    rows = Rows()
    if connected(mac):
        rows.add("󰂲", "Disconnect", "", lambda: bt("disconnect", mac))
    else:
        rows.add("󰂱", "Connect", "", lambda: notify("Bluetooth", f"{name} " + (
            "connected" if bt("connect", mac, timeout=20).returncode == 0 else "did not connect")))
    rows.add("󰆴", "Forget this device", "", lambda: confirm(f"Forget {name}?", "Forget") and bt("remove", mac))
    rows.add("󰁍", "Bluetooth", "", main)
    picked = show(rows, name)
    if picked and picked[1]:
        picked[1]()


def main() -> None:
    rows = Rows()
    if not has_adapter():
        rows.add("󰂲", "No Bluetooth in this PC", "")
        rows.add("󰗮", "Plug in a USB Bluetooth dongle", "")
        rows.add("", "and it will show up here", "")
        rows.back()
        show(rows, "Bluetooth")
        return
    if not service_up():
        rows.add("󰂲", "Bluetooth service is stopped", "")
        rows.add("", "Run: sudo systemctl enable --now bluetooth", "")
        rows.back()
        show(rows, "Bluetooth")
        return

    on = powered()
    rows.add("󰂯" if on else "󰂲", "Bluetooth", "● On" if on else "○ Off",
             lambda: (bt("power", "off" if on else "on"), main()), bold=True, strong_right=True)
    if on:
        devices = listed("Paired")
        if devices:
            rows.header("My devices")
        for mac, name in devices:
            c = connected(mac)
            rows.add("󰂱" if c else "󰂯", name, "connected ›" if c else "›",
                     lambda mac=mac, name=name: device_menu(mac, name), bold=c, strong_right=c)
        rows.header("More")
        rows.add("󰐕", "Pair a new device…", "", pair_new)
    rows.back()
    picked = show(rows, "Bluetooth")
    if picked and picked[1]:
        picked[1]()


if __name__ == "__main__":
    main()
