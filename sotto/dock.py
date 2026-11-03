#!/usr/bin/env python3
"""AJSTARS dock: an Apple-style dock for Hyprland, in GTK4 + gtk4-layer-shell.

- hides itself and slides up when the mouse touches the bottom edge
- one icon per app: pinned apps get a dot when running, unpinned running
  apps appear after a divider (no duplicate icons)
- click: open the app, or bring its window forward (cycles if several;
  restores minimized ones)
- right-click: its windows, New Window, Maximize, Keep in Dock / Remove,
  Quit, Dock Settings…
- Dock Settings: pick, order and remove apps, icon size, auto-hide
- Apps: a floating list of every app, with when you last used it
- while the terminal inside the wallpaper has the keyboard, a click anywhere
  else gives it back (an invisible layer catches that click)

Settings live in ~/.config/sotto/dock.json. Runs with the system python3
(python3-gi); bin/sotto-dock preloads libgtk4-layer-shell as GTK requires.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import gi

import assistant  # the wallpaper assistant's brain (same folder)

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

# sotto-dock preloads gtk4-layer-shell for this process only; apps opened from
# the dock must not inherit it (it breaks GTK3 apps such as waybar).
os.environ.pop("LD_PRELOAD", None)

try:
    gi.require_foreign("cairo")       # needs python3-gi-cairo; without it, clicks after an answer close the prompt
    import cairo  # noqa: E402
except ImportError:
    cairo = None

try:
    gi.require_version("GioUnix", "2.0")
    from gi.repository import GioUnix  # noqa: E402
    DesktopAppInfo = GioUnix.DesktopAppInfo
except (ValueError, ImportError):
    DesktopAppInfo = Gio.DesktopAppInfo

try:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LS  # noqa: E402
    LS.get_major_version()          # the typelib can exist without the library behind it
except (ValueError, ImportError, GLib.Error):
    LS = None

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "sotto" / "dock.json"
USAGE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "sotto" / "app-usage.json"
GNOME_STATE = Path.home() / ".local/share/gnome-shell/application_state"   # GNOME's own last-used record
LAUNCHPAD = "launchpad"
DEFAULTS = {
    "pinned": [LAUNCHPAD, "org.gnome.Nautilus.desktop", "google-chrome.desktop", "kitty.desktop",
               "cursor.desktop", "code.desktop", "blender_blender.desktop", "discord.desktop"],
    "autohide": True,
    "icon_size": 44,
    "show_unpinned": True,
}
MARGIN = 8            # gap between dock and screen edge when shown
DRAWER_W = 420        # width of the Apps list
RECENT = 5            # apps shown under "Recent"
SLIDE_MS = 180

CSS = """
window.dock-window, window.snap-preview-window { background: transparent; }
window.prompt-window { background: transparent; }
.prompt { background: rgba(236, 226, 208, 0.97); border-radius: 10px; padding: 8px 12px; }
.prompt-chevron { color: #a7783f; font-family: "DepartureMono Nerd Font"; font-size: 11px; }
entry.prompt-entry { background: transparent; border: none; box-shadow: none; outline: none; padding: 0; min-height: 0;
    color: #3d2c1f; caret-color: #a7783f; font-family: "DepartureMono Nerd Font"; font-size: 11px; }
entry.prompt-entry > text > placeholder { color: #b5a185; }
.prompt-reply { color: #6b5540; font-family: "DepartureMono Nerd Font"; font-size: 11px; }
.prompt-reply.thinking { color: #a7783f; }
.snap-preview {
    background: rgba(167, 120, 63, 0.16);
    border: 2px solid rgba(167, 120, 63, 0.75);
    border-radius: 14px;
}
.dock {
    background: rgba(243, 235, 222, 0.97);
    border: 1px solid #cdb99b;
    border-radius: 20px;
    padding: 6px 10px 4px 10px;
}
.dock-item { background: transparent; border: none; box-shadow: none; padding: 2px 4px; min-width: 0; }
.dock-item image { transition: transform 140ms ease-out; }
.dock-item:hover image { transform: scale(1.16) translateY(-3px); }
.dock-item:hover { background: transparent; }
.apps-icon { color: #3d2c1f; }
.dot { min-width: 5px; min-height: 5px; border-radius: 3px; background: #2b2019; margin-top: 3px; }
.dot.off { background: transparent; }
.divider { background: #cdb99b; min-width: 1px; margin: 8px 6px 10px 6px; }
popover > contents { background: #f3ebde; border: 1px solid #cdb99b; border-radius: 12px; padding: 6px; }
popover label, popover button { color: #3d2c1f; }
popover button { background: transparent; border: none; box-shadow: none; padding: 6px 10px; border-radius: 8px; }
popover button:hover { background: #e2d4bd; }
popover .title { font-weight: bold; padding: 4px 10px 6px 10px; }
popover separator { background: #cdb99b; margin: 4px 6px; }
window.drawer-window { background: rgba(61, 44, 31, 0.08); }
window.catcher-window { background: transparent; }
.drawer {
    background: #f3ebde;
    border: 1px solid #cdb99b;
    border-radius: 16px;
    padding: 12px 8px 8px 8px;
    box-shadow: 0 18px 48px rgba(61, 44, 31, 0.20);
}
.drawer-search { margin: 0 8px 6px 8px; padding: 0 2px 10px 2px; border-bottom: 1px solid #e2d4bd; }
.drawer-search image { color: #8f7759; }
entry.drawer-entry { background: transparent; border: none; box-shadow: none; outline: none; padding: 0; min-height: 0;
    color: #3d2c1f; caret-color: #a7783f; font-family: "Ubuntu Sans", sans-serif; font-size: 15px; }
entry.drawer-entry > text > placeholder { color: #b5a185; }
.drawer list { background: transparent; }
.drawer row { background: transparent; border-radius: 10px; padding: 6px 10px; outline: none; }
.drawer row:hover { background: rgba(226, 212, 189, 0.55); }
.drawer row:selected { background: #e2d4bd; }
.drawer row.heading-row, .drawer row.heading-row:hover { background: transparent; padding: 10px 10px 3px 10px; }
.drawer-heading { color: #a08a6c; font-family: "DepartureMono Nerd Font"; font-size: 11px; letter-spacing: 1px; }
.drawer-name { color: #3d2c1f; font-family: "Ubuntu Sans", sans-serif; font-size: 13px; }
.drawer-meta { color: #8f7759; font-family: "DepartureMono Nerd Font"; font-size: 11px; }
.drawer-meta.open { color: #a7783f; }
.drawer-empty { color: #8f7759; font-family: "DepartureMono Nerd Font"; font-size: 11px; padding: 28px 0; }
.drawer scrollbar slider { background: #cdb99b; min-width: 4px; }
window.dock-settings, window.dock-settings > * { background: #ece2d0; color: #3d2c1f; }
.dock-settings label { color: #3d2c1f; }
.dock-settings .heading { font-weight: bold; margin-top: 8px; }
.dock-settings list, .dock-settings row { background: #f3ebde; color: #3d2c1f; }
.dock-settings button { color: #3d2c1f; }
"""


# ── config ───────────────────────────────────────────────────────────────
def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, json.JSONDecodeError):
        cfg = {}
    return {**DEFAULTS, **cfg}


def save_config(cfg: dict):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    os.replace(tmp, CONFIG)


def load_usage() -> dict:
    """When each app was last used: {desktop id: unix time}. Apps it hasn't seen yet
    get their time from GNOME's record, so the list is useful from the first day."""
    try:
        data = json.loads(USAGE.read_text())
        usage = {k: float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        usage = {}
    try:
        import xml.etree.ElementTree as ET
        for el in ET.parse(GNOME_STATE).iter("application"):
            app_id, seen = el.get("id"), el.get("last-seen")
            if app_id and seen and app_id not in usage:
                usage[app_id] = float(seen)
    except (OSError, ValueError, ET.ParseError):
        pass
    return usage


def save_usage(usage: dict):
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USAGE.with_suffix(".tmp")
    tmp.write_text(json.dumps(usage, indent=1))
    os.replace(tmp, USAGE)


def ago(ts: float) -> str:
    s = max(0.0, time.time() - ts)
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    d = int(s // 86400)
    return "yesterday" if d == 1 else f"{d}d ago"


# ── hyprland ─────────────────────────────────────────────────────────────
def hypr(*args) -> str:
    try:
        return subprocess.run(["hyprctl", *args], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def clients() -> list[dict]:
    try:
        return [c for c in json.loads(hypr("clients", "-j") or "[]") if c.get("mapped", True) and c.get("class")]
    except json.JSONDecodeError:
        return []


def focus_window(c: dict):
    addr = c["address"]
    if c["workspace"]["name"] == "special:minimized":
        ws = json.loads(hypr("activeworkspace", "-j") or '{"id": 1}')["id"]
        hypr("dispatch", "movetoworkspacesilent", f"{ws},address:{addr}")
    hypr("dispatch", "focuswindow", f"address:{addr}")
    hypr("dispatch", "alterzorder", f"top,address:{addr}")


# ── apps ─────────────────────────────────────────────────────────────────
class Apps:
    """Maps Hyprland window classes to installed .desktop apps. Re-reads the app
    list when apps are installed or removed, so new apps show up right away."""

    def __init__(self):
        self.all = []
        self.cache: dict = {}
        self.loaded_at = 0.0
        self.reload()
        self.monitor = Gio.AppInfoMonitor.get()
        self.monitor.connect("changed", lambda *_: self.reload())

    def reload(self):
        self.all = [a for a in Gio.AppInfo.get_all() if isinstance(a, DesktopAppInfo)]
        self.cache.clear()
        self.loaded_at = GLib.get_monotonic_time() / 1e6

    def get(self, app_id: str):
        try:
            return DesktopAppInfo.new(app_id)
        except TypeError:
            return None

    def match(self, cls: str):
        c = cls.lower()
        for a in self.all:
            wm = (a.get_startup_wm_class() or "").lower()
            aid = a.get_id().lower().removesuffix(".desktop")
            exe = os.path.basename(a.get_executable() or "").lower()
            if c in (wm, aid, exe) or aid.endswith("." + c) or aid.split("_")[0] == c:
                return a
        return None

    def for_class(self, cls: str):
        if cls in self.cache:
            return self.cache[cls]
        found = self.match(cls)
        if found is None and GLib.get_monotonic_time() / 1e6 - self.loaded_at > 5:
            self.reload()                      # maybe it was installed after the dock started
            found = self.match(cls)
        if found is not None:
            self.cache[cls] = found
        return found


# ── dock ─────────────────────────────────────────────────────────────────
class Dock(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="dev.ajstars.sotto", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.cfg = load_config()
        self.apps = Apps()
        self.win = None
        self.items: dict[str, tuple[Gtk.Widget, Gtk.Widget]] = {}   # app id -> (button, dot)
        self.running: dict[str, list[dict]] = {}
        self.margin = MARGIN
        self.target = MARGIN
        self.anim = 0
        self.hide_timer = 0
        self.popover_open = False
        self.settings_win = None
        self.refresh_pending = False
        self.usage = load_usage()
        self.usage_timer = 0
        self.drawer = None

    # startup / command line
    def do_command_line(self, cmd):
        args = cmd.get_arguments()[1:]
        if self.win is None:
            self.build()
        if "--settings" in args:
            self.open_settings()
        return 0

    def build(self):
        prov = Gtk.CssProvider()
        prov.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.win = Gtk.Window(application=self)
        self.win.add_css_class("dock-window")
        # full-width transparent strip with the pill centred in it; when hidden, its
        # bottom 2px stay on screen and catch the mouse anywhere along the edge
        strip = Gtk.Box(hexpand=True)
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, halign=Gtk.Align.CENTER, hexpand=True)
        self.box.add_css_class("dock")
        strip.append(self.box)
        self.win.set_child(strip)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.on_enter())
        motion.connect("motion", lambda *_: self.on_enter())
        motion.connect("leave", lambda *_: self.schedule_hide())
        self.win.add_controller(motion)

        if LS:
            LS.init_for_window(self.win)
            LS.set_namespace(self.win, "sotto-dock")
            LS.set_layer(self.win, LS.Layer.TOP)
            for e in (LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(self.win, e, True)
            # never take the keyboard: on Hyprland a panel holding keyboard focus keeps it
            # even when you click back into a window (typing and title-bar buttons stop working)
            LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)
        self.populate()
        self.apply_autohide()
        self.win.present()
        GLib.timeout_add(200, self.update_dock_input)
        self.make_preview()
        self.make_prompt()
        self.make_drawer()
        self.make_term_catcher()
        self.refresh_running()
        threading.Thread(target=self.listen, daemon=True).start()
        if self.cfg["autohide"]:
            self.schedule_hide(1500)

    # snap preview: an outline showing where a dragged window will land (driven by snap.py)
    def make_preview(self):
        if not LS:
            return
        w = Gtk.Window(application=self)
        w.add_css_class("snap-preview-window")
        w.set_resizable(False)
        self.preview_box = Gtk.Box()
        self.preview_box.add_css_class("snap-preview")
        w.set_child(self.preview_box)
        LS.init_for_window(w)
        LS.set_namespace(w, "sotto-snap-preview")
        LS.set_layer(w, LS.Layer.OVERLAY)
        LS.set_anchor(w, LS.Edge.TOP, True)
        LS.set_anchor(w, LS.Edge.LEFT, True)
        LS.set_exclusive_zone(w, -1)
        LS.set_keyboard_mode(w, LS.KeyboardMode.NONE)
        self.preview = w
        threading.Thread(target=self.preview_server, daemon=True).start()

    def preview_server(self):
        path = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sotto.sock")
        try:
            os.unlink(path)
        except OSError:
            pass
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.bind(path)
        while True:
            msg = s.recv(256).decode(errors="ignore").split()
            GLib.idle_add(self.on_preview, msg)

    def on_preview(self, msg):
        if msg and msg[0] == "apps":
            if len(msg) > 1:                        # for testing and demos: open with a search typed in
                self.open_drawer()
                self.drawer_entry.set_text(" ".join(msg[1:]))
                self.drawer_entry.set_position(-1)
            else:
                self.toggle_drawer()
            return False
        if msg and msg[0] == "prompt" and len(msg) == 3:
            self.open_prompt(int(msg[1]), int(msg[2]))
            return False
        if msg and msg[0] == "term-on" and len(msg) == 5:
            self.show_term_catcher(*(int(v) for v in msg[1:]))
            return False
        if msg and msg[0] == "term-off":
            if self.term_catcher:
                self.term_catcher.set_visible(False)
            return False
        if msg and msg[0] == "prompt-close":
            self.close_prompt()
            return False
        if msg and msg[0] == "prompt-demo" and len(msg) >= 4:   # for demo recordings: type it out, then Enter
            self.open_prompt(int(msg[1]), int(msg[2]))
            text, typed = " ".join(msg[3:]), [0]

            def key():
                typed[0] += 1
                self.prompt_entry.set_text(text[:typed[0]])
                self.prompt_entry.set_position(-1)
                if typed[0] >= len(text):
                    GLib.timeout_add(350, lambda: self.on_prompt_submit(self.prompt_entry) or False)
                    return False
                return True

            GLib.timeout_add(70, key)
            return False
        if msg and msg[0] == "prompt-ask":          # for testing: submit text as if typed
            self.open_prompt(400, 300)
            self.prompt_entry.set_text(" ".join(msg[1:]))
            self.on_prompt_submit(self.prompt_entry)
            return False
        inset = 6
        if len(msg) == 5 and msg[0] == "show":
            x, y, w, h = (int(v) for v in msg[1:])
            LS.set_margin(self.preview, LS.Edge.LEFT, x + inset)
            LS.set_margin(self.preview, LS.Edge.TOP, y + inset)
            self.preview_box.set_size_request(max(1, w - 2 * inset), max(1, h - 2 * inset))
            self.preview.set_visible(True)
            surf = self.preview.get_surface()
            if surf is not None and cairo is not None and not getattr(self, "preview_passthrough", False):
                try:
                    surf.set_input_region(cairo.Region())   # never catches the mouse
                    self.preview_passthrough = True
                except Exception:
                    pass
        else:
            self.preview.set_visible(False)
        return False

    # ── wallpaper prompt: double-click the wallpaper, type, Enter ──────────
    # The prompt lives in a transparent full-screen layer (kept below the top bar).
    # While you type it holds the keyboard, and Hyprland then won't move focus on a
    # click elsewhere, so the transparent area catches that click and closes the
    # prompt, like any popup. After Enter it only listens on the box itself.
    def make_prompt(self):
        self.prompt = None
        if not LS:
            return
        w = Gtk.Window(application=self)
        w.add_css_class("prompt-window")
        self.prompt_fixed = Gtk.Fixed()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        col.add_css_class("prompt")
        row = Gtk.Box(spacing=8)
        chevron = Gtk.Label(label="›")
        chevron.add_css_class("prompt-chevron")
        self.prompt_entry = Gtk.Entry(has_frame=False, hexpand=True, width_chars=46,
                                      placeholder_text="ask, or tell me to open something")
        self.prompt_entry.add_css_class("prompt-entry")
        self.prompt_entry.connect("activate", self.on_prompt_submit)
        row.append(chevron)
        row.append(self.prompt_entry)
        self.prompt_reply = Gtk.Label(xalign=0, wrap=True, max_width_chars=58, visible=False)
        self.prompt_reply.add_css_class("prompt-reply")
        col.append(row)
        col.append(self.prompt_reply)
        self.prompt_box = col
        self.prompt_fixed.put(col, 0, 0)
        w.set_child(self.prompt_fixed)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_prompt_key)
        w.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_prompt_click)
        w.add_controller(click)
        LS.init_for_window(w)
        LS.set_namespace(w, "sotto-prompt")
        LS.set_layer(w, LS.Layer.TOP)
        for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
            LS.set_anchor(w, e, True)
        LS.set_exclusive_zone(w, 0)          # stays below the top bar, so the bar keeps working
        LS.set_keyboard_mode(w, LS.KeyboardMode.NONE)
        self.prompt = w
        self.prompt_timer = 0
        self.prompt_anim = 0
        self.prompt_done = False
        self.prompt_history: list[str] = []
        self.history_at = 0

    def inside_prompt_box(self, x: float, y: float) -> bool:
        widget = self.prompt.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None:
            if widget is self.prompt_box:
                return True
            widget = widget.get_parent()
        return False

    def on_prompt_click(self, _gesture, _n, x, y):
        if not self.inside_prompt_box(x, y):
            self.close_prompt()              # a click anywhere else closes it
        elif self.prompt_done:
            self.reask()                     # click the answer to ask something new

    def set_prompt_input(self, box_only: bool):
        """Where the prompt layer accepts clicks: everywhere (while typing) or just the box."""
        surf = self.prompt.get_surface()
        if surf is None or cairo is None:
            return
        try:
            if box_only:
                ok, b = self.prompt_box.compute_bounds(self.prompt)
                x, y, w, h = (int(b.get_x()), int(b.get_y()), int(b.get_width()), int(b.get_height())) if ok else (0, 0, 1, 1)
                region = cairo.Region(cairo.RectangleInt(x, y, max(1, w), max(1, h) + 220))
            else:
                region = cairo.Region(cairo.RectangleInt(0, 0, 10000, 10000))
            surf.set_input_region(region)
        except Exception:               # never let this break the prompt
            pass

    def open_prompt(self, x: int, y: int):
        if not self.prompt:
            return
        self.prompt_origin = (x, y)          # where the wallpaper was double-clicked (screen pixels)
        geo = Gdk.Display.get_default().get_monitors().get_item(0).get_geometry()
        try:
            top = json.loads(hypr("monitors", "-j") or "[]")[0]["reserved"][1]
        except (IndexError, KeyError, json.JSONDecodeError):
            top = 26
        x = max(8, min(x - 16, geo.width - 480))
        y = max(4, min(y - 10 - top, geo.height - top - 190))
        self.prompt_fixed.move(self.prompt_box, x, y)
        self.reask()

    def reask(self):
        """Ready for a (new) question: empty line, keyboard held while typing."""
        if self.term_catcher and self.term_catcher.get_visible():
            self.release_term()
        self.stop_anim()
        self.prompt_done = False
        self.prompt_entry.set_editable(True)
        self.prompt_entry.set_text("")
        self.prompt_reply.set_visible(False)
        self.history_at = len(self.prompt_history)
        LS.set_keyboard_mode(self.prompt, LS.KeyboardMode.EXCLUSIVE)
        self.prompt.set_visible(True)
        self.prompt.present()
        self.set_prompt_input(box_only=False)
        self.prompt_entry.grab_focus()
        self.arm_prompt_close(60)

    def close_prompt(self):
        if not self.prompt:
            return
        self.stop_anim()
        if self.prompt_timer:
            GLib.source_remove(self.prompt_timer)
            self.prompt_timer = 0
        LS.set_keyboard_mode(self.prompt, LS.KeyboardMode.NONE)
        self.prompt.set_visible(False)

    def arm_prompt_close(self, seconds: int):
        if self.prompt_timer:
            GLib.source_remove(self.prompt_timer)

        def done():
            self.prompt_timer = 0
            self.close_prompt()
            return False

        self.prompt_timer = GLib.timeout_add_seconds(seconds, done)

    def on_focus_event(self):
        """Something else got focus (Alt+Tab, a top-bar click): close a prompt still being typed."""
        if self.prompt and self.prompt.get_visible() and not self.prompt_done:
            self.close_prompt()
        if self.drawer and self.drawer.get_visible() and GLib.get_monotonic_time() - self.drawer_opened > 400_000:
            self.close_drawer()
        if (self.term_catcher and self.term_catcher.get_visible()
                and GLib.get_monotonic_time() - self.term_on_at > 600_000):
            self.release_term()                     # Alt+Tab or a top-bar click: the window gets the keys
        return False

    def on_prompt_key(self, _ctrl, keyval, _code, _state):
        name = Gdk.keyval_name(keyval)
        if name == "Escape":
            self.close_prompt()
            return True
        if name in ("Up", "Down") and self.prompt_history and not self.prompt_done:
            self.history_at = max(0, min(len(self.prompt_history), self.history_at + (-1 if name == "Up" else 1)))
            text = self.prompt_history[self.history_at] if self.history_at < len(self.prompt_history) else ""
            self.prompt_entry.set_text(text)
            self.prompt_entry.set_position(-1)
            return True
        return False

    def on_prompt_submit(self, entry):
        text = entry.get_text().strip()
        if not text:
            self.close_prompt()
            return
        self.prompt_history.append(text)
        self.prompt_done = True
        entry.set_editable(False)
        LS.set_keyboard_mode(self.prompt, LS.KeyboardMode.NONE)   # hand the keyboard straight back
        self.set_prompt_input(box_only=True)                       # and stop catching clicks elsewhere
        local = assistant.handle(text)
        if local and local["action"] and local["action"]["type"] == "wallterm":
            self.close_prompt()                                    # the terminal opens right here
            x, y = getattr(self, "prompt_origin", (None, None))
            assistant.run_action(dict(local["action"], x=x, y=y))
            return
        if local:
            self.show_reply(local["reply"])
            assistant.run_action(local["action"], self.launch_app_id)
            self.arm_prompt_close(6 if local["action"] else 20)
            return
        self.show_thinking()

        def work():
            r = assistant.ask(text)
            GLib.idle_add(self.got_answer, r)

        threading.Thread(target=work, daemon=True).start()

    def got_answer(self, r):
        self.show_reply(r["reply"])
        assistant.run_action(r["action"], self.launch_app_id)
        self.arm_prompt_close(40)
        return False

    def stop_anim(self):
        if self.prompt_anim:
            GLib.source_remove(self.prompt_anim)
            self.prompt_anim = 0
        self.prompt_reply.remove_css_class("thinking")

    def show_thinking(self):
        self.stop_anim()
        self.prompt_reply.add_css_class("thinking")
        self.prompt_reply.set_visible(True)
        step = [0]

        def tick():
            step[0] = (step[0] + 1) % 4
            self.prompt_reply.set_text(" ".join("·" * max(1, step[0])))
            return True

        tick()
        self.prompt_anim = GLib.timeout_add(280, tick)

    def show_reply(self, text: str):
        """Types the answer out, a few characters at a time."""
        self.stop_anim()
        self.prompt_reply.set_visible(True)
        shown = [0]

        def tick():
            shown[0] = min(len(text), shown[0] + 3)
            self.prompt_reply.set_text(text[:shown[0]])
            if shown[0] >= len(text):
                self.prompt_anim = 0
                return False
            return True

        self.prompt_reply.set_text("")
        self.prompt_anim = GLib.timeout_add(14, tick)

    # ── the terminal inside the wallpaper ─────────────────────────────────
    # The wallpaper takes the keyboard while you use its terminal. Clicking the
    # wallpaper gives it back (the wallpaper sees that click itself), but Hyprland
    # won't move the keyboard when you click back into the window you came from.
    # So this invisible layer lies over your windows only: a click on one gives
    # the keyboard back and hands it to that window. The wallpaper stays uncovered,
    # so its mouse trail and effects keep working everywhere.
    def make_term_catcher(self):
        self.term_catcher = None
        if not LS or cairo is None:
            return
        w = Gtk.Window(application=self)
        w.add_css_class("catcher-window")
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.release_term(focus_pointer=True))
        w.add_controller(click)
        LS.init_for_window(w)
        LS.set_namespace(w, "sotto-term-catcher")
        LS.set_layer(w, LS.Layer.TOP)
        for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
            LS.set_anchor(w, e, True)
        LS.set_exclusive_zone(w, 0)              # below the top bar, which keeps working
        LS.set_keyboard_mode(w, LS.KeyboardMode.NONE)
        self.term_catcher = w
        self.term_on_at = 0

    def show_term_catcher(self, x: int, y: int, w: int, h: int):
        if not self.term_catcher:
            return
        try:
            top = json.loads(hypr("monitors", "-j") or "[]")[0]["reserved"][1]
        except (IndexError, KeyError, json.JSONDecodeError):
            top = 26
        self.term_catcher.set_visible(True)
        self.term_catcher.present()
        surf = self.term_catcher.get_surface()
        if surf is None:
            return
        self.term_on_at = GLib.get_monotonic_time()
        try:
            region = cairo.Region()
            ws = json.loads(hypr("activeworkspace", "-j") or "{}").get("id")
            for c in clients():                  # each window, with room for its title bar and border
                if c["workspace"]["id"] == ws:
                    (cx, cy), (cw, chh) = c["at"], c["size"]
                    region.union(cairo.RectangleInt(cx - 4, cy - top - 30, cw + 8, chh + 34))
            surf.set_input_region(region)        # no windows: nothing to catch
        except Exception:
            pass

    def release_term(self, focus_pointer: bool = False):
        if self.term_catcher:
            self.term_catcher.set_visible(False)
        assistant.tell_wall("term-release")
        if focus_pointer:      # the window you clicked gets the keyboard, once the wallpaper lets go
            GLib.timeout_add(150, self.focus_window_under_pointer)

    def focus_window_under_pointer(self):
        try:
            x, y = (int(v) for v in hypr("cursorpos").split(","))
            ws = json.loads(hypr("activeworkspace", "-j") or "{}").get("id")
        except (ValueError, json.JSONDecodeError):
            return False
        under = [c for c in clients() if c["workspace"]["id"] == ws
                 and c["at"][0] <= x < c["at"][0] + c["size"][0] and c["at"][1] <= y < c["at"][1] + c["size"][1]]
        if under:
            focus_window(min(under, key=lambda c: c.get("focusHistoryID", 99)))   # the one on top
        return False

    # ── Apps: a floating list of every app ────────────────────────────────
    # Same trick as the prompt: a transparent full-screen layer (below the top bar)
    # holds the keyboard while open; a click outside the list or a focus change closes it.
    def make_drawer(self):
        if not LS:
            return
        w = Gtk.Window(application=self)
        w.add_css_class("drawer-window")
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        panel.add_css_class("drawer")
        panel.set_size_request(DRAWER_W, -1)
        search = Gtk.Box(spacing=10)
        search.add_css_class("drawer-search")
        search.append(Gtk.Image.new_from_icon_name("system-search-symbolic"))
        self.drawer_entry = Gtk.Entry(has_frame=False, hexpand=True, placeholder_text="Search apps")
        self.drawer_entry.add_css_class("drawer-entry")
        self.drawer_entry.connect("changed", lambda *_: self.fill_drawer())
        search.append(self.drawer_entry)
        self.drawer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, activate_on_single_click=True)
        self.drawer_list.connect("row-activated", lambda _l, row: self.drawer_open(getattr(row, "app_id", None)))
        self.drawer_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.drawer_scroll.set_min_content_width(DRAWER_W - 16)
        self.drawer_scroll.set_child(self.drawer_list)
        panel.append(search)
        panel.append(self.drawer_scroll)
        self.drawer_panel = panel
        w.set_child(panel)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_drawer_key)
        w.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("pressed", self.on_drawer_click)
        w.add_controller(click)
        LS.init_for_window(w)
        LS.set_namespace(w, "sotto-apps")
        LS.set_layer(w, LS.Layer.TOP)
        for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
            LS.set_anchor(w, e, True)
        LS.set_exclusive_zone(w, 0)
        LS.set_keyboard_mode(w, LS.KeyboardMode.NONE)
        self.drawer = w
        self.drawer_rows: dict[str, Gtk.ListBoxRow] = {}     # "all:<id>" / "recent:<id>" -> row
        self.drawer_opened = 0

    def toggle_drawer(self):
        if self.drawer is None:
            subprocess.Popen(["rofi", "-show", "drun", "-theme", "drawer"], start_new_session=True)
        elif self.drawer.get_visible():
            self.close_drawer()
        else:
            self.open_drawer()

    def open_drawer(self):
        self.close_prompt()
        if self.term_catcher and self.term_catcher.get_visible():
            self.release_term()
        try:
            mon_h = self.screen_box()[1]
        except (IndexError, KeyError, json.JSONDecodeError):
            mon_h = 768
        h = max(240, min(500, mon_h - 26 - 150))
        self.drawer_scroll.set_min_content_height(h)
        self.drawer_scroll.set_max_content_height(h)
        if self.drawer_entry.get_text():
            self.drawer_entry.set_text("")          # refills the list
        else:
            self.fill_drawer()
        self.drawer_scroll.get_vadjustment().set_value(0)
        self.drawer_opened = GLib.get_monotonic_time()
        LS.set_keyboard_mode(self.drawer, LS.KeyboardMode.EXCLUSIVE)
        self.drawer.set_visible(True)
        self.drawer.present()
        self.drawer_entry.grab_focus()

    def close_drawer(self):
        if self.drawer is None:
            return
        LS.set_keyboard_mode(self.drawer, LS.KeyboardMode.NONE)
        self.drawer.set_visible(False)

    def on_drawer_click(self, _gesture, _n, x, y):
        widget = self.drawer.pick(x, y, Gtk.PickFlags.DEFAULT)
        while widget is not None:
            if widget is self.drawer_panel:
                return
            widget = widget.get_parent()
        self.close_drawer()

    def on_drawer_key(self, _ctrl, keyval, _code, _state):
        name = Gdk.keyval_name(keyval)
        if name == "Escape":
            self.close_drawer()
            return True
        if name in ("Down", "Up", "Page_Down", "Page_Up"):
            self.drawer_move({"Down": 1, "Up": -1, "Page_Down": 8, "Page_Up": -8}[name])
            return True
        if name in ("Return", "KP_Enter"):
            row = self.drawer_list.get_selected_row()
            self.drawer_open(getattr(row, "app_id", None) if row else None)
            return True
        return False

    def drawer_move(self, step: int):
        rows, row = [], self.drawer_list.get_first_child()
        while row is not None:
            if isinstance(row, Gtk.ListBoxRow) and row.get_selectable():
                rows.append(row)
            row = row.get_next_sibling()
        if not rows:
            return
        cur = self.drawer_list.get_selected_row()
        i = rows.index(cur) if cur in rows else -1
        target = rows[max(0, min(len(rows) - 1, i + step))]
        self.drawer_list.select_row(target)
        adj = self.drawer_scroll.get_vadjustment()
        if target is rows[0]:
            adj.set_value(0)                         # show the heading above the first row too
            return
        ok, b = target.compute_bounds(self.drawer_list)
        if ok:
            top, bottom = b.get_y(), b.get_y() + b.get_height()
            if top < adj.get_value():
                adj.set_value(top)
            elif bottom > adj.get_value() + adj.get_page_size():
                adj.set_value(bottom - adj.get_page_size())

    def drawer_open(self, app_id):
        if not app_id:
            return
        self.close_drawer()
        if self.running.get(app_id):
            self.on_click(app_id)                    # already open: bring it forward, like the dock
            self.note_used(app_id)
        else:
            self.launch(app_id)

    def app_meta(self, app_id: str, app) -> tuple[str, bool]:
        """The grey line under an app's name: open now / when it was last used, and what it is."""
        parts, is_open = [], False
        wins = self.running.get(app_id, [])
        if wins:
            parts.append("open now" + (f" · {len(wins)} windows" if len(wins) > 1 else ""))
            is_open = True
        elif app_id in self.usage:
            parts.append("used " + ago(self.usage[app_id]))
        kind = (app.get_generic_name() or "").strip()
        if not kind or kind.lower() in app.get_display_name().lower():     # "Brave Web Browser · Web Browser"
            kind = (app.get_description() or "").split(". ")[0].strip().rstrip(".")
            if len(kind) > 44:
                kind = kind[:43].rstrip() + "…"
        if not kind:
            cats = [c for c in (app.get_categories() or "").split(";") if c and c not in ("GTK", "GNOME", "Qt", "KDE", "Application")]
            kind = cats[0] if cats else ""
        if kind:
            parts.append(kind)
        return " · ".join(parts) or " ", is_open

    def drawer_row(self, key: str, app) -> Gtk.ListBoxRow:
        row = self.drawer_rows.get(key)
        if row is None:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(spacing=12)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER)
            name = Gtk.Label(label=app.get_display_name(), xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=34)
            name.add_css_class("drawer-name")
            meta = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=44)
            meta.add_css_class("drawer-meta")
            text.append(name)
            text.append(meta)
            icon = app.get_icon()
            img = Gtk.Image.new_from_gicon(icon) if icon else Gtk.Image.new_from_icon_name("application-x-executable")
            img.set_pixel_size(30)
            box.append(text)
            box.append(img)
            row.set_child(box)
            row.app_id, row.meta = app.get_id(), meta
            rc = Gtk.GestureClick(button=3)
            rc.connect("pressed", lambda *_, r=row: self.on_drawer_menu(r))
            row.add_controller(rc)
            self.drawer_rows[key] = row
        label, is_open = self.app_meta(row.app_id, app)
        row.meta.set_text(label)
        (row.meta.add_css_class if is_open else row.meta.remove_css_class)("open")
        return row

    def drawer_heading(self, text: str) -> Gtk.ListBoxRow:
        key = "heading:" + text
        row = self.drawer_rows.get(key)
        if row is None:
            label = Gtk.Label(label=text.upper(), xalign=0)
            label.add_css_class("drawer-heading")
            row = Gtk.ListBoxRow(selectable=False, activatable=False, child=label)
            row.add_css_class("heading-row")
            self.drawer_rows[key] = row
        return row

    def fill_drawer(self):
        apps = [a for a in self.apps.all if a.should_show()]
        live = {a.get_id() for a in apps}
        for key in [k for k in self.drawer_rows if k.split(":", 1)[1] not in live and not k.startswith("heading:")]:
            del self.drawer_rows[key]                 # uninstalled since last time
        q = self.drawer_entry.get_text().strip().lower()
        self.drawer_list.remove_all()
        used = lambda a: self.usage.get(a.get_id(), 0.0)  # noqa: E731
        if not q:
            recent = sorted((a for a in apps if used(a)), key=used, reverse=True)[:RECENT]
            if recent:
                self.drawer_list.append(self.drawer_heading("Recent"))
                for a in recent:
                    self.drawer_list.append(self.drawer_row("recent:" + a.get_id(), a))
                self.drawer_list.append(self.drawer_heading("All apps"))
            for a in sorted(apps, key=lambda a: a.get_display_name().lower()):
                self.drawer_list.append(self.drawer_row("all:" + a.get_id(), a))
            return
        scored = []
        for a in apps:
            name = a.get_display_name().lower()
            extra = " ".join([a.get_generic_name() or "", " ".join(a.get_keywords() or []), a.get_id()]).lower()
            if name.startswith(q):
                score = 3
            elif any(w.startswith(q) for w in name.replace("-", " ").split()):
                score = 2
            elif any(w.startswith(q) for w in extra.replace(";", " ").split()):
                score = 1.6                          # "br" finds browsers before LibreOffice
            elif q in name:
                score = 1.5
            elif q in extra:
                score = 1
            else:
                continue
            scored.append((-score, -used(a), name, a))
        if not scored:
            empty = Gtk.ListBoxRow(selectable=False, activatable=False, child=Gtk.Label(label="no apps match"))
            empty.get_child().add_css_class("drawer-empty")
            self.drawer_list.append(empty)
            return
        for *_, a in sorted(scored, key=lambda t: t[:3]):
            self.drawer_list.append(self.drawer_row("all:" + a.get_id(), a))
        first = self.drawer_list.get_row_at_index(0)
        self.drawer_list.select_row(first)            # Enter opens the best match
        self.drawer_scroll.get_vadjustment().set_value(0)

    def on_drawer_menu(self, row: Gtk.ListBoxRow):
        app_id = row.app_id
        pop = Gtk.Popover(has_arrow=False, position=Gtk.PositionType.BOTTOM)
        pop.set_parent(row)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        pop.set_child(col)

        def item(label, fn):
            b = Gtk.Button(label=label, halign=Gtk.Align.FILL)
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda *_: (pop.popdown(), fn()))
            col.append(b)

        item("Open", lambda: self.drawer_open(app_id))
        if self.running.get(app_id):
            item("New Window", lambda: (self.close_drawer(), self.launch(app_id)))
        pinned = app_id in self.cfg["pinned"]
        item("Remove from Dock" if pinned else "Keep in Dock", lambda: self.set_pinned(app_id, not pinned))
        pop.connect("closed", lambda *_: GLib.idle_add(pop.unparent))
        pop.popup()

    # last used: the focused window's app, and anything opened from the dock or the list
    def note_used_class(self, cls: str):
        if cls.startswith("sotto-"):
            return False
        app = self.apps.for_class(cls)
        if app is not None:
            self.note_used(app.get_id())
        return False

    def note_used(self, app_id: str):
        self.usage[app_id] = time.time()
        if not self.usage_timer:
            def flush():
                self.usage_timer = 0
                try:
                    save_usage(self.usage)
                except OSError:
                    pass
                return False
            self.usage_timer = GLib.timeout_add_seconds(20, flush)

    def launch_app_id(self, app_id: str) -> bool:
        app = self.apps.get(app_id)
        if not app:
            return False
        app.launch([], Gdk.Display.get_default().get_app_launch_context())
        self.note_used(app_id)
        return True

    # items
    def populate(self):
        child = self.box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.box.remove(child)
            child = nxt
        self.items.clear()
        GLib.timeout_add(60, self.update_dock_input)   # the pill's width changes with its icons
        for app_id in self.cfg["pinned"]:
            self.add_item(app_id, pinned=True)
        if self.cfg["show_unpinned"]:
            extra = [a for a in self.running if a not in self.cfg["pinned"]]
            if extra:
                div = Gtk.Box()
                div.add_css_class("divider")
                self.box.append(div)
                for app_id in extra:
                    self.add_item(app_id, pinned=False)
        self.update_dots()

    def add_item(self, app_id: str, pinned: bool):
        size = self.cfg["icon_size"]
        if app_id == LAUNCHPAD:
            img = Gtk.Image.new_from_icon_name("view-app-grid-symbolic")
            img.add_css_class("apps-icon")
            name = "Apps"
        elif app_id.startswith("class:"):
            cls = app_id[6:]
            theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
            icon = next((n for n in (cls, cls.lower(), cls.split(".")[-1].lower()) if theme.has_icon(n)), "application-x-executable")
            img = Gtk.Image.new_from_icon_name(icon)
            name = cls.split(".")[-1].replace("-", " ").title()
        else:
            app = self.apps.get(app_id)
            if app is None:
                return
            img = Gtk.Image.new_from_gicon(app.get_icon()) if app.get_icon() else Gtk.Image.new_from_icon_name("application-x-executable")
            name = app.get_display_name()
        img.set_pixel_size(size)
        dot = Gtk.Box(halign=Gtk.Align.CENTER)
        dot.add_css_class("dot")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(img)
        col.append(dot)
        btn = Gtk.Button(child=col, tooltip_text=name)
        btn.add_css_class("dock-item")
        btn.connect("clicked", lambda *_: self.on_click(app_id))
        rc = Gtk.GestureClick(button=3)
        rc.connect("pressed", lambda *_: self.on_menu(app_id, btn, pinned))
        btn.add_controller(rc)
        self.box.append(btn)
        self.items[app_id] = (btn, dot)

    def update_dots(self):
        for app_id, (_, dot) in self.items.items():
            if self.running.get(app_id):
                dot.remove_css_class("off")
            else:
                dot.add_css_class("off")

    # running windows
    def refresh_running(self):
        self.refresh_pending = False
        running: dict[str, list[dict]] = {}
        for c in clients():
            if c["class"] in ("sotto-wall",):
                continue
            app = self.apps.for_class(c["class"])
            key = app.get_id() if app else "class:" + c["class"]   # no .desktop file: still show it
            running.setdefault(key, []).append(c)
        changed_set = set(running) != set(self.running)
        self.running = running
        if changed_set and self.cfg["show_unpinned"]:
            self.populate()
        else:
            self.update_dots()
        return False

    def listen(self):
        """Hyprland's event socket: refresh the dots when windows open, close or move."""
        try:
            path = os.path.join(os.environ["XDG_RUNTIME_DIR"], "hypr", os.environ["HYPRLAND_INSTANCE_SIGNATURE"], ".socket2.sock")
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(path)
        except (KeyError, OSError):
            GLib.timeout_add_seconds(2, lambda: (self.refresh_running(), True)[1])
            return
        buf = b""
        while True:
            data = s.recv(4096)
            if not data:
                return
            buf += data
            *lines, buf = buf.split(b"\n")
            if any(ln.startswith(b"activewindowv2") for ln in lines):
                GLib.idle_add(self.on_focus_event)
            for ln in lines:
                if ln.startswith(b"activewindow>>"):          # class,title of the focused window
                    cls = ln[14:].split(b",", 1)[0].decode(errors="ignore")
                    if cls:
                        GLib.idle_add(self.note_used_class, cls)
            if any(ln.startswith((b"openwindow", b"closewindow", b"movewindow", b"windowtitle")) for ln in lines):
                if not self.refresh_pending:
                    self.refresh_pending = True
                    GLib.timeout_add(120, self.refresh_running)

    # clicks
    def launch(self, app_id: str):
        if app_id == LAUNCHPAD:
            self.toggle_drawer()
            return
        app = self.apps.get(app_id)
        if app:
            app.launch([], Gdk.Display.get_default().get_app_launch_context())
            self.note_used(app_id)

    def on_click(self, app_id: str):
        wins = sorted(self.running.get(app_id, []), key=lambda c: c.get("focusHistoryID", 0))
        if not wins:
            self.launch(app_id)
            return
        active = json.loads(hypr("activewindow", "-j") or "{}").get("address")
        if len(wins) > 1 and any(w["address"] == active for w in wins):
            focus_window(wins[-1])          # cycle to the least recently used one
        else:
            focus_window(wins[0])

    def on_menu(self, app_id: str, btn: Gtk.Widget, pinned: bool):
        pop = Gtk.Popover(has_arrow=True, position=Gtk.PositionType.TOP)
        pop.set_parent(btn)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        pop.set_child(col)

        def item(label, fn):
            b = Gtk.Button(label=label, halign=Gtk.Align.FILL)
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda *_: (pop.popdown(), fn()))
            col.append(b)

        launchable = app_id != LAUNCHPAD and not app_id.startswith("class:")
        if app_id == LAUNCHPAD:
            name = "Apps"
        elif launchable and self.apps.get(app_id):
            name = self.apps.get(app_id).get_display_name()
        else:
            name = app_id.removeprefix("class:")
        title = Gtk.Label(label=name, xalign=0)
        title.add_css_class("title")
        col.append(title)
        wins = self.running.get(app_id, [])
        for w in wins[:8]:
            t = (w.get("title") or name)[:42]
            mark = "  (minimized)" if w["workspace"]["name"] == "special:minimized" else ""
            item(t + mark, lambda w=w: focus_window(w))
        if wins:
            col.append(Gtk.Separator())
        if launchable:
            item("New Window", lambda: self.launch(app_id))
        if wins:
            item("Maximize / Restore", lambda: subprocess.Popen([os.path.expanduser("~/.config/hypr/scripts/window-max.sh"), wins[0]["address"]]))
        if launchable:
            if pinned:
                item("Remove from Dock", lambda: self.set_pinned(app_id, False))
            else:
                item("Keep in Dock", lambda: self.set_pinned(app_id, True))
        if wins:
            item("Quit", lambda: [hypr("dispatch", "closewindow", f"address:{w['address']}") for w in wins])
        col.append(Gtk.Separator())
        item("Dock Settings…", self.open_settings)

        self.popover_open = True

        def closed(*_):
            self.popover_open = False
            GLib.idle_add(lambda: pop.unparent())
            self.schedule_hide()

        pop.connect("closed", closed)
        pop.popup()

    def set_pinned(self, app_id: str, keep: bool):
        pinned = [a for a in self.cfg["pinned"] if a != app_id]
        if keep:
            pinned.append(app_id)
        self.cfg["pinned"] = pinned
        save_config(self.cfg)
        self.populate()
        if self.settings_win:
            self.fill_settings()

    # auto-hide
    def apply_autohide(self):
        if not LS:
            return
        if self.cfg["autohide"]:
            LS.set_exclusive_zone(self.win, 0)
        else:
            LS.auto_exclusive_zone_enable(self.win)
            self.slide_to(MARGIN)

    def hidden_margin(self) -> int:
        h = self.win.get_height() or (self.cfg["icon_size"] + 30)
        return -(h - 2)             # leave a 2px edge on screen to catch the mouse

    def update_dock_input(self) -> bool:
        """Where the dock catches the mouse. The window is a full-width strip, but only
        the pill should take clicks: everything beside it (and the pill while it slides
        away) clicks through to the windows behind. Once fully hidden, the whole 2px
        bottom edge catches the mouse again, so touching the bottom anywhere shows it."""
        surf = self.win.get_surface() if self.win else None
        if surf is None or cairo is None:
            return False
        try:
            if self.cfg["autohide"] and self.target < 0 and self.margin <= self.target:
                region = cairo.Region(cairo.RectangleInt(0, 0, 100000, 100000))
            else:
                ok, b = self.box.compute_bounds(self.win)
                if not ok:
                    return False
                pad = 4
                region = cairo.Region(cairo.RectangleInt(int(b.get_x()) - pad, int(b.get_y()) - pad,
                                                         int(b.get_width()) + 2 * pad, int(b.get_height()) + 2 * pad))
            surf.set_input_region(region)
            self.win.queue_draw()       # GTK only sends the new region with the next frame
        except Exception:               # never let this break the dock
            pass
        return False

    def screen_box(self) -> tuple[int, int]:
        """(top, height) of the screen the dock is on, in the same scaled pixels as
        `hyprctl cursorpos` (a 4K TV at 2.5x is 864 high, not 2160)."""
        try:
            surf = self.win.get_surface() if self.win else None
            mon = Gdk.Display.get_default().get_monitor_at_surface(surf) if surf else None
            if mon:
                g = mon.get_geometry()
                return g.y, g.height
        except Exception:
            pass
        m = json.loads(hypr("monitors", "-j") or "[]")[0]
        return m["y"], round(m["height"] / (m.get("scale") or 1))

    def pointer_on_dock(self) -> bool:
        """Hyprland sends 'enter' when the dock appears even if the mouse is far away,
        so check where the cursor really is."""
        try:
            x, y = (int(v) for v in hypr("cursorpos").split(","))
            top, mon_h = self.screen_box()
        except (ValueError, IndexError, KeyError, json.JSONDecodeError):
            return True
        visible_top = top + mon_h - (self.win.get_height() + self.margin)
        return y >= visible_top - 1

    def on_enter(self):
        if not self.cfg["autohide"]:
            return
        if self.pointer_on_dock():
            self.show_dock()
        else:
            self.schedule_hide()

    def show_dock(self):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = 0
        self.slide_to(MARGIN)

    def schedule_hide(self, delay: int = 700):
        if not self.cfg["autohide"] or not LS:
            return
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)

        def go():
            self.hide_timer = 0
            if self.popover_open or (self.margin >= 0 and self.pointer_on_dock()):
                self.schedule_hide()        # still in use: check again shortly
            else:
                self.slide_to(self.hidden_margin())
            return False

        self.hide_timer = GLib.timeout_add(delay, go)

    def slide_to(self, target: int):
        if not LS:
            return
        self.target = target
        self.update_dock_input()
        if self.anim:
            return
        start, t0 = self.margin, GLib.get_monotonic_time()

        def step():
            t = min(1.0, (GLib.get_monotonic_time() - t0) / 1000 / SLIDE_MS)
            ease = 1 - (1 - t) ** 3
            self.margin = round(start + (self.target - start) * ease)
            LS.set_margin(self.win, LS.Edge.BOTTOM, self.margin)
            if t >= 1.0 or self.margin == self.target:
                self.anim = 0
                if self.margin != self.target:
                    self.slide_to(self.target)
                self.update_dock_input()
                return False
            return True

        self.anim = GLib.timeout_add(16, step)

    # settings window
    def open_settings(self):
        if self.settings_win:
            self.settings_win.present()
            return
        w = Gtk.Window(application=self, title="Dock Settings", default_width=420, default_height=560)
        w.add_css_class("dock-settings")
        w.connect("close-request", lambda *_: setattr(self, "settings_win", None) or False)
        self.settings_win = w
        self.settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                                    margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(self.settings_box)
        w.set_child(scroller)
        self.fill_settings()
        w.present()

    def fill_settings(self):
        b = self.settings_box
        child = b.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            b.remove(child)
            child = nxt

        def switch_row(label, key):
            row = Gtk.Box(spacing=12)
            row.append(Gtk.Label(label=label, xalign=0, hexpand=True))
            sw = Gtk.Switch(active=self.cfg[key], valign=Gtk.Align.CENTER)

            def changed(s, _):
                self.cfg[key] = s.get_active()
                save_config(self.cfg)
                if key == "autohide":
                    self.apply_autohide()
                    self.show_dock()
                else:
                    self.populate()

            sw.connect("notify::active", changed)
            row.append(sw)
            b.append(row)

        switch_row("Hide the dock automatically", "autohide")
        switch_row("Show open apps that aren't in the dock", "show_unpinned")

        row = Gtk.Box(spacing=12)
        row.append(Gtk.Label(label="Icon size", xalign=0))
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 32, 64, 2)
        scale.set_value(self.cfg["icon_size"])
        scale.set_hexpand(True)

        def resized(s):
            self.cfg["icon_size"] = int(s.get_value())
            save_config(self.cfg)
            self.populate()

        scale.connect("value-changed", resized)
        row.append(scale)
        b.append(row)

        head = Gtk.Label(label="Apps in the dock (left to right)", xalign=0)
        head.add_css_class("heading")
        b.append(head)
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        for i, app_id in enumerate(self.cfg["pinned"]):
            r = Gtk.Box(spacing=8, margin_top=4, margin_bottom=4, margin_start=6, margin_end=6)
            if app_id == LAUNCHPAD:
                img, name = Gtk.Image.new_from_icon_name("view-app-grid-symbolic"), "Apps (all apps grid)"
            else:
                app = self.apps.get(app_id)
                if app is None:
                    continue
                img, name = Gtk.Image.new_from_gicon(app.get_icon()), app.get_display_name()
            img.set_pixel_size(24)
            r.append(img)
            r.append(Gtk.Label(label=name, xalign=0, hexpand=True))
            for label, delta in (("↑", -1), ("↓", 1)):
                mv = Gtk.Button(label=label, has_frame=False)
                mv.connect("clicked", lambda *_, i=i, d=delta: self.move_pinned(i, d))
                r.append(mv)
            rm = Gtk.Button(label="✕", has_frame=False, tooltip_text="Remove from Dock")
            rm.connect("clicked", lambda *_, a=app_id: self.set_pinned(a, False))
            r.append(rm)
            lst.append(r)
        b.append(lst)

        add = Gtk.Button(label="Add an app…")
        add.connect("clicked", lambda *_: self.open_app_picker())
        b.append(add)

    def move_pinned(self, i: int, delta: int):
        p = self.cfg["pinned"]
        j = i + delta
        if 0 <= j < len(p):
            p[i], p[j] = p[j], p[i]
            save_config(self.cfg)
            self.populate()
            self.fill_settings()

    def open_app_picker(self):
        w = Gtk.Window(application=self, title="Add to Dock", default_width=380, default_height=520)
        w.add_css_class("dock-settings")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
        search = Gtk.SearchEntry(placeholder_text="Search apps")
        col.append(search)
        lst = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        apps = sorted((a for a in self.apps.all if a.should_show() and a.get_id() not in self.cfg["pinned"]),
                      key=lambda a: a.get_display_name().lower())
        rows = []
        for a in apps:
            r = Gtk.Box(spacing=8, margin_top=3, margin_bottom=3, margin_start=6, margin_end=6)
            img = Gtk.Image.new_from_gicon(a.get_icon()) if a.get_icon() else Gtk.Image()
            img.set_pixel_size(24)
            r.append(img)
            r.append(Gtk.Label(label=a.get_display_name(), xalign=0, hexpand=True))
            btn = Gtk.Button(label="Add", has_frame=False)
            btn.connect("clicked", lambda *_, a=a: (self.set_pinned(a.get_id(), True), w.close()))
            r.append(btn)
            lst.append(r)
            rows.append((a.get_display_name().lower(), r.get_parent()))

        def filt(entry):
            q = entry.get_text().lower()
            for n, row in rows:
                row.set_visible(q in n)

        search.connect("search-changed", filt)
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.set_child(lst)
        col.append(sc)
        w.set_child(col)
        w.present()


def main() -> int:
    if LS is None and "--window" not in sys.argv:
        print("sotto-dock: gtk4-layer-shell is missing (sudo apt install libgtk4-layer-shell0 gir1.2-gtk4layershell-1.0)", file=sys.stderr)
        return 1
    return Dock().run([a for a in sys.argv if a != "--window"])


if __name__ == "__main__":
    sys.exit(main())
