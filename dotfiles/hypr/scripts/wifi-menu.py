#!/usr/bin/env python3
"""Wi-Fi menu: click the network label in the bar, SUPER+W, or Wi-Fi in the Control Center.

Wi-Fi on/off, your networks in range, other networks, hidden networks, saved
networks, and per network: connect, disconnect, copy password, QR code, forget.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qs_ui import Rows, ask, confirm, esc, notify, run, show, spawn  # noqa: E402

BARS = ["󰤯", "󰤟", "󰤢", "󰤥", "󰤨"]  # weak .. strong
OFF = "󰤭"
LOCK = "󰌾"


def nm(*args: str, timeout: int = 40):
    return run("nmcli", *args, timeout=timeout)


def fields(line: str) -> list[str]:
    """nmcli -t output: ':' splits fields, '\\:' is a literal colon."""
    out, cur, escaped = [], "", False
    for ch in line:
        if escaped:
            cur, escaped = cur + ch, False
        elif ch == "\\":
            escaped = True
        elif ch == ":":
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


# ── state ───────────────────────────────────────────────────────────────────

def wifi_device() -> str | None:
    for line in nm("-t", "-f", "DEVICE,TYPE", "device").stdout.splitlines():
        dev, typ = fields(line)[:2]
        if typ == "wifi":
            return dev
    return None


def radio_on() -> bool:
    return nm("radio", "wifi").stdout.strip() == "enabled"


def saved_wifi() -> dict[str, str]:
    """connection name -> uuid, for saved Wi-Fi profiles."""
    out: dict[str, str] = {}
    for line in nm("-t", "-f", "NAME,UUID,TYPE", "connection", "show").stdout.splitlines():
        name, uuid, typ = fields(line)[:3]
        if typ == "802-11-wireless":
            out.setdefault(name, uuid)
    return out


def networks(rescan: bool) -> list[dict]:
    r = nm("-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list",
           "--rescan", "yes" if rescan else "no")
    best: dict[str, dict] = {}
    for line in r.stdout.splitlines():
        inuse, ssid, signal, sec = (fields(line) + ["", "", "", ""])[:4]
        if not ssid:
            continue
        n = {"ssid": ssid, "signal": int(signal or 0), "secure": sec not in ("", "--"),
             "enterprise": "802.1X" in sec, "active": inuse == "*"}
        old = best.get(ssid)
        if not old or n["active"] or (n["signal"] > old["signal"] and not old["active"]):
            best[ssid] = n
    return sorted(best.values(), key=lambda n: (not n["active"], -n["signal"]))


def wired_connected() -> bool:
    for line in nm("-t", "-f", "TYPE,STATE", "device").stdout.splitlines():
        typ, state = fields(line)[:2]
        if typ == "ethernet" and state == "connected":
            return True
    return False


def ip_of(dev: str) -> str:
    out = nm("-g", "IP4.ADDRESS", "device", "show", dev).stdout.strip().split("|")[0]
    return out.split("/")[0].strip()


def active_connection(dev: str) -> str:
    return nm("-g", "GENERAL.CONNECTION", "device", "show", dev).stdout.strip()


def password_of(name: str) -> str:
    return nm("-s", "-g", "802-11-wireless-security.psk", "connection", "show", "id", name).stdout.strip()


# ── actions ─────────────────────────────────────────────────────────────────

def connect_saved(name: str, dev: str) -> None:
    notify("Wi-Fi", f"Connecting to {name}…")
    if nm("--wait", "25", "connection", "up", "id", name, "ifname", dev).returncode == 0:
        notify("Wi-Fi", f"Connected to {name}")
        return
    pw = ask(f"<b>{esc(name)}</b>\nCould not connect. Has the password changed? Type it again:", password=True)
    if not pw:
        return
    nm("connection", "modify", "id", name, "wifi-sec.psk", pw)
    ok = nm("--wait", "25", "connection", "up", "id", name, "ifname", dev).returncode == 0
    notify("Wi-Fi", f"Connected to {name}" if ok else f"Still could not connect to {name}")


def connect_new(n: dict, dev: str) -> None:
    ssid = n["ssid"]
    if n["enterprise"]:
        notify("Wi-Fi", f"{ssid} needs a username: opening network settings")
        spawn("nm-connection-editor")
        return
    args = ["--wait", "25", "device", "wifi", "connect", ssid, "ifname", dev]
    if n["secure"]:
        pw = ask(f"Password for <b>{esc(ssid)}</b>", password=True)
        if not pw:
            return
        args += ["password", pw]
    before = set(saved_wifi().values())
    notify("Wi-Fi", f"Connecting to {ssid}…")
    if nm(*args).returncode == 0:
        notify("Wi-Fi", f"Connected to {ssid}")
        return
    # a failed try leaves a half-made profile; remove it so duplicates don't pile up
    for uuid in set(saved_wifi().values()) - before:
        nm("connection", "delete", "uuid", uuid)
    notify("Wi-Fi", f"Could not connect to {ssid}. Wrong password?")


def connect_hidden(dev: str) -> None:
    ssid = ask("Name of the hidden network")
    if not ssid:
        return
    pw = ask(f"Password for <b>{esc(ssid)}</b>  (Enter with nothing if it has none)", password=True)
    args = ["--wait", "25", "device", "wifi", "connect", ssid, "hidden", "yes", "ifname", dev]
    if pw:
        args += ["password", pw]
    ok = nm(*args).returncode == 0
    notify("Wi-Fi", f"Connected to {ssid}" if ok else f"Could not connect to {ssid}")


def copy_password(name: str) -> None:
    pw = password_of(name)
    if pw:
        run("wl-copy", inp=pw)
        notify("Wi-Fi", f"Password for {name} copied")
    else:
        notify("Wi-Fi", "No password saved for this network")


def show_qr(name: str) -> None:
    pw = password_of(name)
    q = lambda s: "".join("\\" + c if c in '\\;,:"' else c for c in s)  # noqa: E731
    data = f"WIFI:T:{'WPA' if pw else 'nopass'};S:{q(name)};P:{q(pw)};;"
    png = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "wifi-qr.png")
    if run("qrencode", "-s", "10", "-m", "2", "-o", png, data).returncode == 0:
        spawn("xdg-open", png)


def forget(name: str) -> None:
    if not confirm(f"Forget {name}?", "Forget"):
        return
    for line in nm("-t", "-f", "NAME,UUID", "connection", "show").stdout.splitlines():
        n, uuid = fields(line)[:2]
        if n == name:
            nm("connection", "delete", "uuid", uuid)
    notify("Wi-Fi", f"Forgot {name}")


def network_menu(name: str, ssid: str, dev: str) -> None:
    rows = Rows()
    rows.add("󰖪", "Disconnect", "", lambda: (nm("device", "disconnect", dev), notify("Wi-Fi", f"Disconnected from {ssid}")))
    rows.add("󰆏", "Copy password", "", lambda: copy_password(name))
    rows.add("󰐲", "Show QR code", "phone scans to join", lambda: show_qr(name))
    rows.add("󰆴", "Forget this network", "", lambda: forget(name))
    rows.add("󰁍", "Wi-Fi", "", main)
    picked = show(rows, ssid, f"connected · {ip_of(dev)}")
    if picked and picked[1]:
        picked[1]()


def saved_menu() -> None:
    names = sorted(saved_wifi(), key=str.lower)
    rows = Rows()
    rows.header("Pick one to forget it")
    for n in names:
        rows.add("󰆴", n, "", lambda n=n: forget(n))
    rows.add("󰁍", "Wi-Fi", "", main)
    picked = show(rows, "Saved networks", f"{len(names)} saved", selected=1)
    if picked and picked[1]:
        picked[1]()


# ── main ────────────────────────────────────────────────────────────────────

def main(rescan: bool = False) -> None:
    dev = wifi_device()
    cable = wired_connected()
    rows = Rows()

    if not dev:
        rows.add(OFF, "No Wi-Fi adapter", "USB adapter unplugged?")
        rows.add("󰒓", "Network settings…", "", lambda: spawn("nm-connection-editor"))
        rows.back()
        picked = show(rows, "Wi-Fi", "cable connected" if cable else "")
        if picked and picked[1]:
            picked[1]()
        return

    def turn(on: bool) -> None:
        nm("radio", "wifi", "on" if on else "off")
        if on:
            time.sleep(3)
            main(rescan=True)

    if not radio_on():
        rows.add(OFF, "Wi-Fi", "○ Off", lambda: turn(True), strong_right=True)
        rows.add("󰒓", "Network settings…", "", lambda: spawn("nm-connection-editor"))
        rows.back()
        picked = show(rows, "Wi-Fi", "cable connected" if cable else "")
        if picked and picked[1]:
            picked[1]()
        return

    nets = networks(rescan) or networks(True)
    if not rescan:
        spawn("nmcli", "device", "wifi", "rescan")  # refresh in the background for next time
    saved = saved_wifi()
    current = next((n for n in nets if n["active"]), None)

    rows.add(BARS[4], "Wi-Fi", "● On", lambda: turn(False), bold=True, strong_right=True)

    def net_row(n: dict) -> None:
        icon = BARS[min(4, n["signal"] // 20)]
        if n["active"]:
            name = active_connection(dev) or n["ssid"]
            rows.add(icon, n["ssid"], "connected ›", lambda: network_menu(name, n["ssid"], dev), bold=True,
                     strong_right=True)
        elif n["ssid"] in saved:
            rows.add(icon, n["ssid"], LOCK if n["secure"] else "", lambda: connect_saved(n["ssid"], dev))
        else:
            rows.add(icon, n["ssid"], LOCK if n["secure"] else "open", lambda: connect_new(n, dev))

    known = [n for n in nets if n["active"] or n["ssid"] in saved]
    other = [n for n in nets if n not in known]
    if known:
        rows.header("My networks")
        for n in known:
            net_row(n)
    if other:
        rows.header("Other networks")
        for n in other:
            net_row(n)

    rows.header("More")
    rows.add("󰑐", "Scan again", "", lambda: main(rescan=True))
    rows.add("󰈈", "Join a hidden network…", "", lambda: connect_hidden(dev))
    rows.add("󰆓", "Saved networks…", f"{len(saved)}", saved_menu)
    rows.add("󰒓", "Network settings…", "", lambda: spawn("nm-connection-editor"))
    rows.back()

    status = []
    if current:
        status.append(ip_of(dev))
    else:
        status.append("not connected")
    if cable:
        status.append("cable connected")
    picked = show(rows, "Wi-Fi", " · ".join(status), selected=1 if nets else 0)
    if picked and picked[1]:
        picked[1]()


if __name__ == "__main__":
    main(rescan="--rescan" in sys.argv)
