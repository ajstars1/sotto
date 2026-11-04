"""What differs between Linux, macOS and Windows, in one place.

Everything else in sotto asks this module to open a link, a folder, an app or a
terminal, for system numbers (RAM, disk, uptime) and whether a process is alive.
Linux keeps the Hyprland behaviour it always had; macOS and Windows get the nearest
native equivalent. Only the standard library is used (ctypes on Windows).
"""
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

OS = "macos" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"
HOME = Path.home()


def runtime_dir() -> str:
    """Where sockets and the pid file live: $XDG_RUNTIME_DIR on Linux, the temp folder elsewhere."""
    return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()


def in_hyprland() -> bool:
    return OS == "linux" and bool(os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"))


def has_unix_dgram() -> bool:
    return hasattr(socket, "AF_UNIX") and OS != "windows"


# ── starting things ───────────────────────────────────────────────────────
def spawn(cmd: list[str] | str, cwd: str | None = None) -> bool:
    """Start a program detached from the wallpaper. Never raises; False when it couldn't start."""
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": cwd}
    if OS == "windows":
        kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    if in_hyprland():                                       # Hyprland starts it, so it isn't our child
        line = cmd if isinstance(cmd, str) else shlex.join(cmd)
        if cwd:
            line = f"cd {shlex.quote(cwd)} && {line}"
        cmd, kw["cwd"] = ["hyprctl", "dispatch", "exec", line], None
    try:
        subprocess.Popen(cmd, shell=isinstance(cmd, str), **kw)
        return True
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def open_url(url: str) -> bool:
    if OS == "windows":
        try:
            os.startfile(url)                              # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    return spawn(["open" if OS == "macos" else "xdg-open", url])


def open_path(path: str) -> bool:
    return open_url(os.path.expanduser(path))


def open_terminal(path: str) -> bool:
    """A terminal in that folder: kitty when it's installed, else the system's own."""
    path = os.path.expanduser(path)
    if OS == "linux":
        return spawn(["kitty", "--directory", path])
    if OS == "macos":
        if Path("/Applications/kitty.app").exists() or shutil.which("kitty"):
            return spawn(["open", "-na", "kitty", "--args", "--directory", path])
        return spawn(["open", "-a", "Terminal", path])
    if shutil.which("wt"):
        return spawn(["wt", "-d", path])
    return spawn(["cmd", "/c", "start", "", "cmd", "/K", f'cd /d "{path}"'])


def run_open_command(template: str, path: str) -> bool:
    """A pinned project's own open command, with {path} filled in (quoted for the shell)."""
    q = f'"{path}"' if OS == "windows" else shlex.quote(path)
    return spawn(template.format(path=q))


# ── apps (macOS and Windows; Linux reads .desktop files in assistant.py) ──
def _mac_apps() -> list[dict]:
    apps, seen = [], set()
    for root in (Path("/Applications"), Path("/System/Applications"), Path("/System/Applications/Utilities"),
                 HOME / "Applications", Path("/Applications/Utilities")):
        try:
            entries = sorted(root.glob("*.app"))
        except OSError:
            continue
        for app in entries:
            if app.stem in seen:
                continue
            seen.add(app.stem)
            apps.append({"id": app.stem, "path": str(app), "name": app.stem, "words": app.stem.lower()})
    return apps


def _win_apps() -> list[dict]:
    apps, seen = [], set()
    roots = [Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
             Path(os.environ.get("APPDATA", HOME / "AppData/Roaming")) / "Microsoft/Windows/Start Menu/Programs"]
    for root in roots:
        try:
            links = sorted(root.rglob("*.lnk"))
        except OSError:
            continue
        for lnk in links:
            name = lnk.stem
            if name.lower() in seen or re.search(r"uninstall|readme|help|website|release notes", name, re.I):
                continue
            seen.add(name.lower())
            apps.append({"id": name, "path": str(lnk), "name": name, "words": f"{name} {lnk.parent.name}".lower()})
    return apps


def apps() -> list[dict]:
    return _mac_apps() if OS == "macos" else _win_apps() if OS == "windows" else []


def launch_app(app: dict) -> bool:
    if OS == "macos":
        return spawn(["open", app["path"]])
    if OS == "windows":
        return open_url(app["path"])
    return spawn(["gtk-launch", app["id"].removesuffix(".desktop")])


def default_browser_note() -> str:
    return "your browser"


# ── system numbers ────────────────────────────────────────────────────────
def _run(argv: list[str], timeout: float = 3) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def memory() -> tuple[int, int, int]:
    """(used percent, total bytes, available bytes); zeros when unknown."""
    if OS == "linux":
        try:
            mem = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    mem[k] = int(v.split()[0]) * 1024
            total, avail = mem["MemTotal"], mem["MemAvailable"]
            return 100 - round(avail * 100 / total), total, avail
        except (OSError, KeyError, ValueError, ZeroDivisionError):
            return 0, 0, 0
    if OS == "macos":
        total = int(_run(["sysctl", "-n", "hw.memsize"]) or 0)
        vm = _run(["vm_stat"])
        page = int((re.search(r"page size of (\d+)", vm) or [0, 4096])[1])
        pages = {k.strip().lower(): int(v.strip(" .")) for k, v in re.findall(r"^([^:\n]+):\s+(\d+)\.?$", vm, re.M)}
        avail = (pages.get("pages free", 0) + pages.get("pages inactive", 0) + pages.get("pages speculative", 0)) * page
        if not total:
            return 0, 0, 0
        return 100 - round(avail * 100 / total), total, avail
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(st)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):      # type: ignore[attr-defined]
        return 0, 0, 0
    return int(st.dwMemoryLoad), int(st.ullTotalPhys), int(st.ullAvailPhys)


def disk() -> tuple[int, int, int]:
    """(used percent, total bytes, free bytes) of the home drive."""
    if OS == "linux":
        cap = _run(["zpool", "list", "-H", "-p", "-o", "cap,size,free", "rpool"]).split()
        if len(cap) == 3 and all(c.isdigit() for c in cap):
            return int(cap[0]), int(cap[1]), int(cap[2])
    try:
        u = shutil.disk_usage(HOME)
        return round(u.used * 100 / u.total), u.total, u.free
    except (OSError, ZeroDivisionError):
        return 0, 0, 0


def uptime_seconds() -> int:
    if OS == "linux":
        try:
            with open("/proc/uptime") as f:
                return int(float(f.read().split()[0]))
        except (OSError, ValueError):
            return 0
    if OS == "macos":
        m = re.search(r"sec = (\d+)", _run(["sysctl", "-n", "kern.boottime"]))
        return int(time.time()) - int(m[1]) if m else 0
    import ctypes

    return int(ctypes.windll.kernel32.GetTickCount64() // 1000)                # type: ignore[attr-defined]


def cpu_temp() -> int | None:
    if OS != "linux":
        return None                                     # needs admin rights or extra tools on macOS and Windows
    for h in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            if (h / "name").read_text().strip() in ("coretemp", "k10temp", "cpu_thermal"):
                return round(int((h / "temp1_input").read_text()) / 1000)
        except (OSError, ValueError):
            pass
    return None


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))           # no packet is sent; it only picks the route
            return s.getsockname()[0]
    except OSError:
        return ""


def stats() -> dict:
    """The numbers state.json keeps under "system"."""
    ram, _, _ = memory()
    used, _, _ = disk()
    up = uptime_seconds()
    pressure = 0.0
    if OS == "linux":
        try:
            with open("/proc/pressure/memory") as f:
                m = re.search(r"avg10=([\d.]+)", f.readline())
                pressure = float(m[1]) if m else 0.0
        except OSError:
            pass
    return {"ram": ram, "swap": 0, "disk": used, "temp": cpu_temp(),
            "uptime": f"{up // 3600}h{up % 3600 // 60:02d}m", "pressure": pressure}


# ── processes ─────────────────────────────────────────────────────────────
def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if OS == "windows":
        # never os.kill here: on Windows it terminates the process
        import ctypes

        k32 = ctypes.windll.kernel32                                         # type: ignore[attr-defined]
        h = k32.OpenProcess(0x1000, False, pid)                              # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.GetLastError() == 5                                # access denied: it exists
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def parent_pid(pid: int) -> int:
    if OS == "linux":
        try:
            with open(f"/proc/{pid}/stat") as f:
                return int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return 0
    if OS == "macos":
        out = _run(["ps", "-o", "ppid=", "-p", str(pid)])
        return int(out) if out.isdigit() else 0
    return 0


# ── actions the assistant can take ────────────────────────────────────────
def lock_argv() -> list[str] | None:
    if OS == "linux":
        return ["loginctl", "lock-session"]
    if OS == "macos":
        return ["pmset", "displaysleepnow"]            # locks when "require password" is on (the default)
    return ["rundll32.exe", "user32.dll,LockWorkStation"]


def screenshot_argv() -> list[str] | None:
    if OS == "linux":
        return [os.path.expanduser("~/.config/hypr/scripts/screenshot.sh"), "region"]
    if OS == "macos":
        return ["screencapture", "-i", "-c"]           # pick an area; it goes to the clipboard
    return ["explorer.exe", "ms-screenclip:"]


def volume(what: str) -> bool:
    """what: up, down, mute, unmute."""
    if OS == "linux":
        argv = {"up": ["wpctl", "set-volume", "-l", "1", "@DEFAULT_AUDIO_SINK@", "10%+"],
                "down": ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "10%-"],
                "mute": ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"],
                "unmute": ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"]}[what]
        return spawn(argv)
    if OS == "macos":
        script = {"up": "set volume output volume ((output volume of (get volume settings)) + 10)",
                  "down": "set volume output volume ((output volume of (get volume settings)) - 10)",
                  "mute": "set volume with output muted", "unmute": "set volume without output muted"}[what]
        return spawn(["osascript", "-e", script])
    import ctypes

    vk = {"up": 0xAF, "down": 0xAE, "mute": 0xAD, "unmute": 0xAD}[what]       # VK_VOLUME_UP/DOWN/MUTE
    user32 = ctypes.windll.user32                                            # type: ignore[attr-defined]
    for _ in range(5 if what in ("up", "down") else 1):                      # 5 steps of 2% = 10%
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)
    return True


def desktop_name() -> str:
    if OS == "macos":
        return "macOS"
    if OS == "windows":
        return "Windows"
    return "Ubuntu with the Hyprland desktop" if in_hyprland() else "Linux"
