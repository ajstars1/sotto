#!/usr/bin/env python3
"""Windows-style drag-to-edge snapping for Hyprland (runs as a small daemon).

Hyprland gives no event when a window is dragged by its title bar (the hyprbars
plugin swallows the mouse button), so this watches instead: a floating window
whose position changes by exactly as much as the cursor moved is being dragged.

While a window is dragged with the cursor at a screen edge, an outline shows
where it will land (drawn by the dock). When the drag pauses there, it snaps:

  left / right edge  -> that half of the screen
  top edge           -> maximized
  corners            -> that quarter
  dragging a snapped window away -> its previous size again, right away

Normal sizes are stored where window-max.sh keeps them, so maximize/restore and
snapping share one memory. Queries go over Hyprland's socket (no process
spawns): about 5 small queries a second at rest, 25 while something is dragged.
"""
import json
import os
import socket
import sys
import time

RUN = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
STATE = os.path.join(RUN, "hypr-max")             # shared with window-max.sh
PIDFILE = os.path.join(RUN, "hypr-snap.pid")
PREVIEW_SOCK = os.path.join(RUN, "sotto.sock")
LOG = os.path.expanduser("~/.cache/hypr-snap.log")

BAR = 26          # hyprbars title bar height
EDGE = 3          # how close to the screen edge the cursor must be
CORNER = 90       # size of the corner zones that give quarters
SETTLE = 0.35     # seconds a drag must pause before it counts as dropped
IDLE_POLL = 0.2
DRAG_POLL = 0.04


def hyprq(cmd: str) -> str:
    path = os.path.join(RUN, "hypr", os.environ["HYPRLAND_INSTANCE_SIGNATURE"], ".socket.sock")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(path)
        s.sendall(cmd.encode())
        out = []
        while chunk := s.recv(65536):
            out.append(chunk)
    return b"".join(out).decode()


def j(cmd: str):
    return json.loads(hyprq("j/" + cmd) or "null")


def dispatch(*args: str):
    hyprq("dispatch " + " ".join(args))


def log(msg: str):
    try:
        with open(LOG, "a") as f:
            f.write(time.strftime("%T ") + msg + "\n")
    except OSError:
        pass


def screen_size(m: dict) -> tuple[int, int]:
    """Size in layout pixels: hyprctl gives the panel's real pixels (a 4K TV at 2.5x
    is 3840x2160) but windows are placed in scaled ones (1536x864)."""
    w, h = m["width"], m["height"]
    if m.get("transform", 0) % 2:      # rotated 90/270
        w, h = h, w
    s = m.get("scale") or 1
    return round(w / s), round(h / s)


def work_area(m: dict):
    l, t, r, b = m["reserved"]
    w, h = screen_size(m)
    return m["x"] + l, m["y"] + t, w - l - r, h - t - b


def layouts(m: dict) -> dict:
    """Target rectangles (including the title bar) for each snap zone."""
    x, y, w, h = work_area(m)
    hw, hh = w // 2, h // 2
    return {
        "max": (x, y, w, h),
        "left": (x, y, hw, h),
        "right": (x + hw, y, w - hw, h),
        "top-left": (x, y, hw, hh),
        "top-right": (x + hw, y, w - hw, hh),
        "bottom-left": (x, y + hh, hw, h - hh),
        "bottom-right": (x + hw, y + hh, w - hw, h - hh),
    }


def window_rect(rect, b: int):
    """Target rectangle -> window geometry (below the title bar, inside the border)."""
    x, y, w, h = rect
    return x + b, y + BAR + b, w - 2 * b, h - BAR - 2 * b


def zone_at(cx: int, cy: int, m: dict):
    mx, my = m["x"], m["y"]
    mw, mh = screen_size(m)
    top_area = my + m["reserved"][1]
    at_left, at_right, at_top = cx <= mx + EDGE, cx >= mx + mw - 1 - EDGE, cy <= my + EDGE
    if at_top:
        if cx <= mx + CORNER:
            return "top-left"
        if cx >= mx + mw - CORNER:
            return "top-right"
        return "max"
    if at_left or at_right:
        side = "left" if at_left else "right"
        if cy <= top_area + CORNER:
            return f"top-{side}"
        if cy >= my + mh - CORNER:
            return f"bottom-{side}"
        return side
    return None


def snapped_layout(at, size, m: dict, b: int):
    for name, rect in layouts(m).items():
        x, y, w, h = window_rect(rect, b)
        if abs(at[0] - x) <= 3 and abs(at[1] - y) <= 3 and abs(size[0] - w) <= 3 and abs(size[1] - h) <= 3:
            return name
    return None


def preview(msg: str):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(msg.encode(), PREVIEW_SOCK)
    except OSError:
        pass


def find(addr: str):
    return next((c for c in j("clients") if c["address"] == addr), None)


def snap_into(win: dict, z: str, m: dict, b: int):
    """Snap a dropped window into zone z, remembering its normal size first."""
    addr = win["address"]
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, addr), "w") as f:
        f.write("%d %d %d %d\n" % (*win["at"], *win["size"]))
    x, y, w, h = window_rect(layouts(m)[z], b)
    dispatch("resizewindowpixel", f"exact {w} {h},address:{addr}")
    dispatch("movewindowpixel", f"exact {x} {y},address:{addr}")
    time.sleep(0.15)
    # apps with a minimum size (browsers won't go below ~500 px tall) stay bigger;
    # Hyprland would centre them on the zone and push the top under the bar
    got = find(addr)
    if got and (got["size"][0] > w + 2 or got["size"][1] > h + 2):
        aw, ah = got["size"]
        wx, wy, ww, wh = work_area(m)
        nx = (wx + ww - b - aw) if z.endswith("right") else x
        nx = max(wx + b, min(nx, wx + ww - b - aw))
        ny = y if y + ah <= wy + wh - b else max(wy + BAR + b, wy + wh - b - ah)
        dispatch("movewindowpixel", f"exact {nx} {ny},address:{addr}")
    dispatch("alterzorder", f"top,address:{addr}")
    log(f"snap {z} {win['class']}")


def keep_reachable(win: dict, m: dict, b: int):
    """After a drop, never leave the title bar under the top bar or off-screen."""
    wx, wy, ww, wh = work_area(m)
    x, y = win["at"]
    w = win["size"][0]
    ny = max(y, wy + BAR + b)
    nx = min(max(x, m["x"] + 80 - w), m["x"] + screen_size(m)[0] - 80)
    if (nx, ny) != (x, y):
        dispatch("movewindowpixel", f"exact {nx} {ny},address:{win['address']}")
        log(f"nudge {win['class']} back on screen")


def restore_size(win: dict, drag: dict):
    """A snapped window is being dragged away: give its normal size back now,
    resizing only (moving it would fight the drag in progress)."""
    saved = os.path.join(STATE, win["address"])
    try:
        with open(saved) as f:
            ow, oh = (int(v) for v in f.read().split()[2:4])
        os.unlink(saved)
    except (OSError, ValueError):
        ow, oh = drag["size"][0] * 6 // 10, drag["size"][1] * 6 // 10
    dispatch("resizewindowpixel", f"exact {ow} {oh},address:{win['address']}")
    log(f"unsnap {win['class']}")


def daemon():
    with open(PIDFILE, "w") as f:
        f.write(str(os.getpid()))
    border = j("getoption general:border_size")["int"]
    prev: dict[str, tuple] = {}               # address -> (at, size) at the last poll
    prev_cursor = None
    drag = None                               # the window being dragged, if any
    ignore_until = 0.0
    shown = None
    while True:
        try:
            clients = [c for c in j("clients") if c.get("floating") and c.get("mapped", True)
                       and not c["workspace"]["name"].startswith("special")]
            c = j("cursorpos")
            cursor = (c["x"], c["y"])
        except (OSError, KeyError, ValueError, TypeError):
            time.sleep(1)
            continue
        now = time.time()
        moving = None
        if prev_cursor and now >= ignore_until:
            cdx, cdy = cursor[0] - prev_cursor[0], cursor[1] - prev_cursor[1]
            for win in clients:
                before = prev.get(win["address"])
                if not before or tuple(win["size"]) != before[1]:
                    continue                  # new window, or a resize: not a drag
                dx, dy = win["at"][0] - before[0][0], win["at"][1] - before[0][1]
                if abs(dx) + abs(dy) > 2 and abs(dx - cdx) <= 4 and abs(dy - cdy) <= 4:
                    moving = win              # it moved exactly with the cursor
                    break
        if moving and (drag is None or drag["addr"] != moving["address"]):
            at0, size0 = prev[moving["address"]]
            m = next(mm for mm in j("monitors") if mm["id"] == moving["monitor"])
            layout = snapped_layout(at0, size0, m, border)
            if layout is None and os.path.exists(os.path.join(STATE, moving["address"])):
                # already being dragged when we noticed: recognise a snapped window by its size
                for name, rect in layouts(m).items():
                    _, _, w, h = window_rect(rect, border)
                    if abs(size0[0] - w) <= 3 and abs(size0[1] - h) <= 3:
                        layout = name
                        break
            drag = {"addr": moving["address"], "at": at0, "size": size0, "layout": layout}
        if moving:
            drag["last_move"] = now
            drag["cursor"] = cursor           # where the window was dropped, even if the mouse moves on
            dist = abs(moving["at"][0] - drag["at"][0]) + abs(moving["at"][1] - drag["at"][1])
            if drag["layout"] is not None and dist > 20:
                restore_size(moving, drag)    # Windows-style: un-snap as soon as it's pulled away
                drag["layout"] = None
        if drag:
            win = next((w for w in clients if w["address"] == drag["addr"]), None)
            if win is None:
                drag = None
            else:
                m = next(mm for mm in j("monitors") if mm["id"] == win["monitor"])
                z = zone_at(cursor[0], cursor[1], m)
                target = None
                if z:
                    x, y, w, h = layouts(m)[z]
                    target = f"show {x - m['x']} {y - m['y']} {w} {h}"
                if target != shown:
                    preview(target or "hide")
                    shown = target
                if now - drag["last_move"] >= SETTLE:
                    preview("hide")
                    shown = None
                    dz = zone_at(*drag["cursor"], m)
                    if dz and drag["layout"] is None:
                        snap_into(win, dz, m, border)
                    else:
                        keep_reachable(win, m, border)
                    ignore_until = time.time() + 0.4  # our own move is not a drag
                    drag = None
        prev = {w["address"]: (tuple(w["at"]), tuple(w["size"])) for w in clients}
        prev_cursor = cursor
        time.sleep(DRAG_POLL if drag else IDLE_POLL)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "daemon":
        try:
            daemon()
        except Exception as e:                    # never leave a stuck outline behind
            preview("hide")
            log(f"error: {e!r}")
            raise
    else:
        print(__doc__)
