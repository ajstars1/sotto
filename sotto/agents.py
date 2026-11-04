"""Your Claude Code sessions, for the wallpaper's agent board.

Claude Code keeps one small file per running session in ~/.claude/sessions/<pid>.json
(name, folder, busy or idle). The end of that session's transcript
(~/.claude/projects/<folder>/<session id>.jsonl) says what it last did and whether it is
waiting for you. Everything is local and read only, on Linux and macOS alike.

Each session gets a status:
  needs  it asked you something, or wants a permission or a choice
  work   it is working right now
  done   it finished its turn and is not asking anything
"""
import glob
import json
import os
import re
import subprocess
import time
from pathlib import Path

HOME = Path.home()
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR", HOME / ".claude"))
SESSIONS = CLAUDE / "sessions"
PROJECTS = CLAUDE / "projects"
TAIL_BYTES = 256 * 1024          # enough for the last few turns of any transcript
ASKING_TOOLS = {"AskUserQuestion", "ExitPlanMode"}
MAX_DETAIL = 90

# session id -> (transcript path, mtime, parsed result), so unchanged files are not re-read
_cache: dict[str, tuple[str, float, dict]] = {}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _transcript(session_id: str) -> str | None:
    hit = _cache.get(session_id)
    if hit and os.path.exists(hit[0]):
        return hit[0]
    found = glob.glob(str(PROJECTS / "*" / f"{glob.escape(session_id)}.jsonl"))
    return found[0] if found else None


def _tail_entries(path: str) -> list[dict]:
    """The transcript's last entries that carry a message, oldest first."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            raw = f.read()
    except OSError:
        return []
    lines = raw.split(b"\n")
    if size > TAIL_BYTES:
        lines = lines[1:]                           # the first line is probably cut in half
    out = []
    for ln in lines:
        if b'"message"' not in ln:
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if d.get("type") in ("user", "assistant") and isinstance(d.get("message"), dict) and not d.get("isSidechain"):
            out.append(d)
    return out


def _clean(text: str) -> str:
    text = re.sub(r"`{1,3}|\*\*|__|^#+\s*", "", text, flags=re.M)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _first_sentence(text: str) -> str:
    text = _clean(text)
    m = re.match(r"(.{20,}?[.!?:])(\s|$)", text)
    return (m.group(1) if m else text)[:MAX_DETAIL]


ASKS = re.compile(r"\?$|\b(before you approve|your (ok|okay|go-ahead|yes|approval|call)|approve|let me know|"
                  r"want me to|should i|shall i|waiting for (you|your)|say (yes|the word)|reply (yes|with))\b", re.I)


def _question(text: str) -> str | None:
    """The sentence where a finished reply asks you for something, if its last lines do."""
    parts = [p for p in re.split(r"(?<=[.!?])\s+", _clean(text)[-700:]) if p]
    tail = parts[-4:]
    for p in reversed(tail):
        if p.endswith("?"):
            return p[:MAX_DETAIL]                    # a real question: show it
    if any(ASKS.search(p) for p in tail):
        return _first_sentence(text)                 # waiting for an OK: show what it did
    return None


def _tool_label(block: dict) -> str:
    name = block.get("name", "a tool")
    args = block.get("input") or {}
    hint = args.get("description") or args.get("command") or args.get("file_path") or args.get("url") or args.get("prompt") or ""
    if isinstance(hint, str) and hint:
        hint = _clean(hint.replace(str(HOME), "~"))
        return f"{name.replace('mcp__', '').split('__')[-1]}: {hint}"[:MAX_DETAIL]
    return f"running {name.replace('mcp__', '').split('__')[-1]}"[:MAX_DETAIL]


def _read(path: str) -> dict:
    """What the transcript says: the last thing done, and whether it is asking you something."""
    entries = _tail_entries(path)
    last_text, asking, pending_tool, ended = "", None, None, False
    for d in entries:
        msg = d["message"]
        content = msg.get("content")
        if d["type"] == "user":
            if isinstance(content, list) and any(c.get("type") == "tool_result" for c in content if isinstance(c, dict)):
                pending_tool = None
            elif content:                            # a new prompt from you
                asking, ended, pending_tool = None, False, None
            continue
        blocks = content if isinstance(content, list) else []
        for b in blocks:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                last_text = b["text"]
            elif b.get("type") == "tool_use":
                pending_tool = b
        ended = msg.get("stop_reason") == "end_turn"
    if pending_tool and pending_tool.get("name") in ASKING_TOOLS:
        q = (pending_tool.get("input") or {}).get("questions") or [{}]
        asking = _clean(str(q[0].get("question", "wants you to choose")))[:MAX_DETAIL]
    elif ended and last_text:
        asking = _question(last_text)
    return {
        "asking": asking,
        "tool": _tool_label(pending_tool) if pending_tool else "",
        "said": _first_sentence(last_text) if last_text else "",
        "ended": ended,
    }


def sessions(now: float | None = None) -> list[dict]:
    """Running Claude Code sessions, most urgent first: needs you, then working, then done."""
    now = now or time.time()
    out = []
    for f in glob.glob(str(SESSIONS / "*.json")):
        try:
            info = json.loads(Path(f).read_text())
            pid = int(info["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not _alive(pid):
            continue
        sid = str(info.get("sessionId", ""))
        path = _transcript(sid) if sid else None
        read = {"asking": None, "tool": "", "said": "", "ended": False}
        if path:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0.0
            hit = _cache.get(sid)
            if hit and hit[0] == path and hit[1] == mtime:
                read = hit[2]
            else:
                read = _read(path)
                _cache[sid] = (path, mtime, read)
        state = str(info.get("status", "idle"))
        if state == "busy":
            status, detail = "work", read["tool"] or read["said"] or "working"
        elif state != "idle":                         # waiting on a permission or a prompt of its own
            status, detail = "needs", read["tool"] or f"waiting ({state})"
        elif read["asking"]:
            status, detail = "needs", read["asking"]
        elif read["said"]:
            status, detail = "done", read["said"]
        else:
            status, detail = "done", "ready for a task"
        since = float(info.get("statusUpdatedAt") or info.get("updatedAt") or info.get("startedAt") or 0) / 1000
        out.append({
            "pid": pid,
            "name": str(info.get("name") or Path(info.get("cwd", "?")).name)[:28],
            "cwd": str(info.get("cwd", "")),
            "status": status,
            "detail": detail,
            "since": since,
            "tmux": str(info.get("tmux", "")),
        })
    rank = {"needs": 0, "work": 1, "done": 2}
    out.sort(key=lambda s: (rank[s["status"]], -s["since"]))
    return out


def ago(ts: float, now: float | None = None) -> str:
    if not ts:
        return ""
    s = max(0, int((now or time.time()) - ts))
    if s < 60:
        return "now"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h"
    return f"{s // 86400}d"


# ── jump to a session's terminal ─────────────────────────────────────────
def _parent(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def jump(session: dict) -> str:
    """Bring the session's terminal window to the front. Returns what happened, for the greeting line."""
    if session.get("tmux"):
        name = session["tmux"].split(":", 1)[0]
        return f"{session['name']} runs in tmux: tmux attach -t {name}"
    if not os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return f"{session['name']} is in {session.get('cwd', '')}"
    try:
        wins = json.loads(subprocess.run(["hyprctl", "clients", "-j"], capture_output=True, text=True, timeout=3).stdout or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return "could not ask Hyprland for windows"
    by_pid = {w.get("pid"): w for w in wins}
    pid = session["pid"]
    for _ in range(12):                              # claude -> shell -> kitty
        if pid in by_pid:
            w = by_pid[pid]
            addr = w["address"]
            if w["workspace"]["name"] == "special:minimized":
                try:
                    ws = json.loads(subprocess.run(["hyprctl", "activeworkspace", "-j"], capture_output=True,
                                                   text=True, timeout=3).stdout or '{"id": 1}')["id"]
                except (OSError, subprocess.SubprocessError, ValueError, KeyError):
                    ws = 1
                subprocess.run(["hyprctl", "dispatch", "movetoworkspacesilent", f"{ws},address:{addr}"], capture_output=True, timeout=3)
            subprocess.run(["hyprctl", "dispatch", "focuswindow", f"address:{addr}"], capture_output=True, timeout=3)
            subprocess.run(["hyprctl", "dispatch", "alterzorder", f"top,address:{addr}"], capture_output=True, timeout=3)
            return ""
        pid = _parent(pid)
        if pid <= 1:
            break
    return f"{session['name']} has no window here (a background or phone session)"
