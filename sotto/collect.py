"""Collect what the wallpaper shows and write it to state.json.

Every source is local: Claude Code session folders (where you have actually
been working), git (branch, uncommitted files, last commit) and /proc + /sys
(RAM, disk, temperature). No network, no API keys.

state.json is rewritten only when its content changes, so the wallpaper
animates only when there is something new to show.
"""
import getpass
import json
import os
import random
import re
import subprocess
import time
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config as configmod
from . import plat
from . import today as todaymod

HOME = Path.home()
CACHE = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "sotto"
STATE = Path(os.environ["SOTTO_STATE"]) if os.environ.get("SOTTO_STATE") else CACHE / "state.json"
INBOX = CACHE / "say.json"
CONFIG = configmod.PATH
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
SCAN_ROOTS = [HOME / "Developer"]
MAX_PROJECTS = 6

SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build", "target", ".next", "__pycache__", ".cache"}
SOURCE_EXT = {".py", ".ts", ".tsx", ".js", ".jsx", ".c", ".h", ".cpp", ".rs", ".go", ".ino", ".glsl", ".frag", ".sh"}
# never read anything that might hold a secret; tokens shown on screen come only from source code
SECRETISH = re.compile(r"(^|/)\.env|secret|credential|token|password|\.pem$|\.key$|id_rsa", re.I)
IDENT = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{3,22}\b")
COMMON = set(
    "self None True False return import from class def async await const function export default "
    "string number boolean null undefined this that with while for else elif then true false print "
    "type interface public private static void char unsigned struct include define pragma value values "
    "data args kwargs result error index length items item name path file line lines text json".split()
)


def config():
    return configmod.raw()


def git(path, *args, timeout=3):
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def is_repo(p: Path) -> bool:
    return (p / ".git").exists()


def child_repos(p: Path):
    try:
        return [c for c in p.iterdir() if c.is_dir() and is_repo(c)]
    except OSError:
        return []


def commit_ts(p: Path) -> int:
    out = git(p, "log", "-1", "--format=%ct")
    return int(out) if out.isdigit() else 0


def my_authors() -> list[str]:
    who = [git(HOME, "config", "--global", key) for key in ("user.email", "user.name")]
    return [w for w in who if w]


def my_commit_ts(p: Path, authors: list[str]) -> int:
    """Last commit *you* wrote. Cloned open-source repos get pulled with fresh
    upstream commits; those shouldn't count as you working on them."""
    if not authors:
        return commit_ts(p)
    out = git(p, "log", "-1", "--format=%ct", *[f"--author={a}" for a in authors])
    return int(out) if out.isdigit() else 0


REPO_CACHE = CACHE / "repo-cache.json"


def load_repo_cache() -> dict:
    try:
        return json.loads(REPO_CACHE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_repo_cache(cache: dict):
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = REPO_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    os.replace(tmp, REPO_CACHE)


def cached_commit_ts(p: Path, authors: list[str], cache: dict) -> int:
    """my_commit_ts, recomputed only when the repo's HEAD log has moved."""
    try:
        key = (p / ".git" / "logs" / "HEAD").stat().st_mtime
    except OSError:
        key = 0
    hit = cache.get(str(p))
    if hit and hit["key"] == key:
        return hit["ts"]
    ts = my_commit_ts(p, authors)
    cache[str(p)] = {"key": key, "ts": ts}
    return ts


def dirty_ts(p: Path) -> int:
    """Newest modification among your uncommitted edits to tracked files."""
    newest = 0
    for line in git(p, "status", "--porcelain", "--untracked-files=no").splitlines()[:50]:
        try:
            newest = max(newest, int((p / line[3:].strip()).stat().st_mtime))
        except OSError:
            pass
    return newest


def is_workspace_root(p: Path) -> bool:
    return len(child_repos(p)) >= 3


def resolve_project(p: Path):
    """A git repo is a project; a folder with 1-2 repos inside maps to the most
    recently committed one (e.g. MyApp -> MyApp/app); a folder that
    holds many repos is a workspace root, not a project."""
    kids = [k for k in child_repos(p) if not is_workspace_root(k)]
    if is_repo(p):
        return None if is_workspace_root(p) else p
    if 1 <= len(kids) <= 2 and len(child_repos(p)) == len(kids):
        return max(kids, key=commit_ts)
    return None


def decode_claude_dir(name: str):
    """Claude Code names a session folder after the project path with every
    non-alphanumeric character turned into '-', which is ambiguous. Walk the
    real filesystem to find the path that encodes to this name."""
    def walk(d: Path, rest: str, depth=0):
        if not rest:
            return d
        if depth > 12:
            return None
        try:
            entries = [e for e in os.scandir(d) if e.is_dir(follow_symlinks=False)]
        except OSError:
            return None
        for e in entries:
            enc = "-" + re.sub(r"[^A-Za-z0-9]", "-", e.name)
            if rest == enc or rest.startswith(enc + "-"):
                found = walk(Path(e.path), rest[len(enc):], depth + 1)
                if found:
                    return found
        return None

    return walk(Path("/"), name)


def claude_activity():
    """{project path: last Claude Code session time}"""
    seen = {}
    if not CLAUDE_PROJECTS.is_dir():
        return seen
    for d in CLAUDE_PROJECTS.iterdir():
        stamps = [f.stat().st_mtime for f in d.glob("*.jsonl")]
        if not stamps:
            continue
        path = decode_claude_dir(d.name)
        proj = resolve_project(path) if path else None
        if proj:
            seen[proj] = max(seen.get(proj, 0), int(max(stamps)))
    return seen


def scan_repos():
    found = []
    for root in SCAN_ROOTS:
        base = len(root.parts)
        for dirpath, dirnames, _ in os.walk(root):
            p = Path(dirpath)
            if is_repo(p):
                found.append(p)
            if len(p.parts) - base >= 3:
                dirnames[:] = []
            else:
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
    return found


def ago(ts: int, now: int) -> str:
    if not ts:
        return ""
    s = max(0, now - ts)
    for unit, n in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if s >= n:
            return f"{s // n}{unit} ago"
    return "just now"


def code_tokens(projects, limit=240):
    """Identifiers from recently changed source files: the wallpaper rains your own code."""
    tokens = []
    for p in projects[:3]:
        files = git(p, "log", "-n", "6", "--name-only", "--format=").splitlines()
        files += [ln[3:] for ln in git(p, "status", "--porcelain").splitlines()]
        for rel in dict.fromkeys(files):
            f = p / rel.strip()
            if f.suffix not in SOURCE_EXT or SECRETISH.search(str(f)) or not f.is_file():
                continue
            try:
                text = f.read_text(errors="ignore")[:40_000]
            except OSError:
                continue
            for t in IDENT.findall(text):
                if t.lower() not in COMMON:
                    tokens.append(t)
    uniq = list(dict.fromkeys(tokens))
    random.Random(len(uniq)).shuffle(uniq)
    return uniq[:limit]


def system():
    return plat.stats()


def headline(name: str, hour: int):
    if 5 <= hour < 12:
        return ["GOOD MORNING,", f"{name}."]
    if 12 <= hour < 17:
        return ["GOOD AFTERNOON,", f"{name}."]
    if 17 <= hour < 22:
        return ["GOOD EVENING,", f"{name}."]
    return ["STILL UP,", f"{name}?"]


def collect():
    cfg = config()
    now = int(time.time())
    hour = time.localtime(now).tm_hour
    name = str(cfg.get("name") or os.environ.get("SOTTO_NAME") or getpass.getuser()).upper()
    hidden = {Path(os.path.expanduser(h)).resolve() for h in cfg.get("hide", [])}
    pins = {Path(os.path.expanduser(p["path"])).resolve(): p for p in cfg.get("pin", [])}

    claude = claude_activity()
    authors = my_authors()
    repos = []
    for repo in set(scan_repos()) | set(claude) | set(pins):
        repo = repo.resolve()
        if repo not in hidden and repo.is_dir() and not is_workspace_root(repo):
            repos.append(repo)

    cache = load_repo_cache()
    with ThreadPoolExecutor(max_workers=8) as pool:
        mine = dict(zip(repos, pool.map(lambda r: cached_commit_ts(r, authors, cache), repos)))
        dirty_times = dict(zip(repos, pool.map(dirty_ts, repos)))
    save_repo_cache(cache)

    activity = {}
    for repo in repos:
        ts = max(claude.get(repo, 0), mine[repo], dirty_times[repo])
        if ts or repo in pins:
            activity[repo] = ts

    ranked = sorted(activity, key=lambda p: (p in pins, activity[p]), reverse=True)[:MAX_PROJECTS]
    projects = []
    for p in ranked:
        pin = pins.get(p, {})
        log = git(p, "log", "-1", "--format=%s%x00%ct")
        subject, _, ts = log.partition("\x00")
        dirty = len([ln for ln in git(p, "status", "--porcelain").splitlines() if ln.strip()])
        projects.append({
            "name": pin.get("label", p.name).upper(),
            "path": str(p),
            "display_path": str(p).replace(str(HOME), "~", 1),
            "branch": git(p, "rev-parse", "--abbrev-ref", "HEAD") or "-",
            "dirty": dirty,
            "commit": subject,
            "commit_ago": ago(int(ts) if ts.isdigit() else 0, now),
            "claude_ago": ago(claude.get(p, 0), now),
            "open": pin.get("open", []),
        })

    sysinfo = system()
    mood = "happy"
    if sysinfo["ram"] >= 85 or sysinfo["pressure"] >= 10:
        mood = "worried"
    elif hour < 5:
        mood = "sleepy"

    messages = []
    if projects:
        top = projects[0]
        when = top["claude_ago"] or top["commit_ago"]
        messages.append(f"LAST SESSION: {top['name']}, {when}." if when else f"LAST PROJECT: {top['name']}.")
        dirty = next((p for p in projects if p["dirty"]), None)
        if dirty:
            n = dirty["dirty"]
            messages.append(f"{dirty['name']} HAS {n} UNCOMMITTED FILE{'S' if n != 1 else ''}.")
    if sysinfo["ram"] >= 85:
        messages.append(f"RAM AT {sysinfo['ram']}%. CLOSE A FEW TABS?")
    elif sysinfo["ram"] >= 70:
        messages.append(f"RAM AT {sysinfo['ram']}%. I'M KEEPING AN EYE ON IT.")
    else:
        messages.append("ALL SYSTEMS NOMINAL.")
    if hour < 5:
        messages.append("IT'S LATE. COMMIT YOUR WORK, THEN SLEEP?")
    messages.append("CLICK A PROJECT TO JUMP BACK IN.")

    return {
        "version": 1,
        "user": name,
        "headline": headline(name, hour),
        "mood": mood,
        "system": sysinfo,
        "projects": projects,
        "messages": messages,
        "tokens": code_tokens([Path(p["path"]) for p in projects]),
        "today": todaymod.collect(cfg),
    }


def write(state: dict) -> bool:
    """Atomic write; returns False when nothing changed (so nothing animates)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    try:
        if json.loads(STATE.read_text()) == state:
            return False
    except (OSError, json.JSONDecodeError):
        pass
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    os.replace(tmp, STATE)
    return True


def say(text: str, mood: str | None = None):
    """Let anything make the mascot talk: `python -m sotto say "Build finished"`."""
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = INBOX.with_suffix(".tmp")
    tmp.write_text(json.dumps({"text": text, "mood": mood, "at": time.time()}))
    os.replace(tmp, INBOX)
