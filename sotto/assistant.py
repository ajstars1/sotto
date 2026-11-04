"""The wallpaper assistant: double-click the wallpaper, type, press Enter.

Small requests are understood and done locally, instantly and for free:
opening apps, websites, folders and your projects, web and YouTube searches,
system info (time, RAM, disk, temperature, IP), lock, screenshot, volume,
how-to questions about this desktop, and its keyboard shortcuts (read from
the same cheatsheet Super+F1 shows, so Claude knows them too). Anything else is a real question
and goes to Claude (Haiku, through the Claude Code login), answered in a
couple of sentences. Claude can only ask for the same safe actions; it never
runs commands.

Pure standard library, so it works from the dock (system python + GTK) and
from `python -m sotto ask "..."` alike.
"""
import configparser
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from pathlib import Path

from . import plat

HOME = Path.home()
STATE = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "sotto" / "state.json"
WORKDIR = Path(os.environ.get("XDG_CACHE_HOME", HOME / ".cache")) / "sotto" / "assistant"
CHEATSHEET = [HOME / ".config/hypr/cheatsheet.txt",                               # the live config
              Path(__file__).resolve().parent.parent / "dotfiles/hypr/cheatsheet.txt"]  # the repo's copy
SHOW_SHORTCUTS = {"type": "cmd", "argv": [str(HOME / ".config/hypr/scripts/cheatsheet.sh")]}
WALL_SOCKET = os.path.join(plat.runtime_dir(), "sotto-wall.sock")
SLASH = {   # /commands typed in the prompt
    "terminal": "a terminal inside the wallpaper: drag its label to move it, the bottom-right mark to resize",
    "help": "this list",
}
SLASH_ALIASES = {"term": "terminal", "t": "terminal", "shell": "terminal", "sh": "terminal", "?": "help"}
APP_DIRS = [
    HOME / ".local/share/applications",
    HOME / ".local/share/flatpak/exports/share/applications",
    Path("/var/lib/flatpak/exports/share/applications"),
    Path("/var/lib/snapd/desktop/applications"),
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
]

SITES = {
    "youtube": "https://www.youtube.com", "gmail": "https://mail.google.com", "mail": "https://mail.google.com",
    "google": "https://www.google.com", "github": "https://github.com", "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com", "calendar": "https://calendar.google.com",
    "maps": "https://maps.google.com", "google maps": "https://maps.google.com", "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai", "whatsapp": "https://web.whatsapp.com", "linkedin": "https://www.linkedin.com",
    "twitter": "https://x.com", "instagram": "https://www.instagram.com", "reddit": "https://www.reddit.com",
    "netflix": "https://www.netflix.com", "spotify": "https://open.spotify.com", "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com", "news": "https://news.google.com", "translate": "https://translate.google.com",
    "docs": "https://docs.google.com", "sheets": "https://sheets.google.com", "meet": "https://meet.google.com",
}
FOLDERS = {
    "home": "~", "home folder": "~", "downloads": "~/Downloads", "documents": "~/Documents",
    "pictures": "~/Pictures", "photos": "~/Pictures", "videos": "~/Videos", "music": "~/Music",
    "desktop": "~/Desktop", "developer": "~/Developer", "projects": "~/Developer",
    "screenshots": "~/Pictures/Screenshots", "wallpapers": "~/Pictures/Wallpapers",
}
ALIASES = {  # everyday words -> what to open
    "browser": "browser", "web browser": "browser", "internet": "browser",
    "terminal": "app:kitty.desktop", "console": "app:kitty.desktop", "files": "app:org.gnome.Nautilus.desktop",
    "file manager": "app:org.gnome.Nautilus.desktop", "file explorer": "app:org.gnome.Nautilus.desktop",
    "explorer": "app:org.gnome.Nautilus.desktop", "apps": "drawer", "all apps": "drawer", "app drawer": "drawer",
    "launcher": "drawer", "code": "app:code.desktop", "vs code": "app:code.desktop", "vscode": "app:code.desktop",
}

HELP = ("Try: open blender · open youtube · play lofi on youtube · search best laptops · "
        "open downloads · open <a project> · how much ram · what time is it · lock · screenshot · "
        "volume up · shortcuts · /terminal · how do I snap windows. Anything else, just ask.")

# questions about this desktop, answered locally
HOWTO = [
    (r"\bsnap|half (the )?screen|side by side|split screen", "Drag a window by its title bar to the left or right edge for half the screen, the top edge to maximize, or a corner for a quarter. Pause there for a moment and it snaps."),
    (r"minimi[sz]e", "Click – on the title bar, click the app's icon in the top bar while you're in it, or press Super+N. Click the icon again to bring it back."),
    (r"maximi[sz]e|full ?screen|bigger", "Click + on the title bar, double-click the title bar, or press Super+M. Do it again to go back."),
    (r"\bclose\b", "Click × on the title bar, or press Super+Q."),
    (r"\bdock\b", "Move the mouse to the bottom edge and the dock slides up. Right-click an icon for its menu and Dock Settings."),
    (r"drawer|all (my )?apps|launcher|find (an )?app", "Click the dotted grid at the top left or the first dock icon, or press Super+Space, then type to search."),
    (r"switch(?!.*workspace)|alt.?tab|between (windows|apps)", "Click an app in the top bar, or press Alt+Tab."),
    (r"\block", "Press Super+L, or type lock here."),
    (r"shut ?down|turn (it |the \w+ )?off|power (off|down)|restart|reboot|log ?out|sign out",
     "Click OFF at the top right, or press Super+Shift+E, for lock, log out, restart and shut down."),
    (r"screenshot|screen ?shot", "Press Print to pick an area or Shift+Print for the whole screen; it's copied and saved in Pictures/Screenshots. Or type screenshot here."),
    (r"terminal (in|inside|on) (the )?wallpaper|embedded terminal|/terminal",
     "Type /terminal here: a shell opens inside the wallpaper. Drag its label to move it, the bottom-right mark to resize it, × to close."),
    (r"wallpaper|this (box|prompt|thing)|what (is|are) you|who are you", "I'm the assistant on your wallpaper. Double-click anywhere on the wallpaper, type, and press Enter. Esc or a click elsewhere closes me."),
]

SYSTEM_PROMPT = (
    "You are the assistant built into the wallpaper of this computer: Ubuntu with the Hyprland "
    "desktop, a dock at the bottom, an app switcher in the top bar, title bars with × + – buttons, "
    "drag-to-edge snapping and a right-click desktop menu. Reply in plain text with no markdown, at most "
    "three short sentences, friendly and direct. If the user wants something done on the computer, end with "
    "one line that is exactly one of: 'ACTION: open_url <url>', 'ACTION: open_app <app name>', "
    "'ACTION: search <query>', 'ACTION: show_shortcuts' (opens the full list of shortcuts). "
    "Never claim to have done anything else."
)
SUMMARY_KEYS = ["SUPER + ENTER", "SUPER + SPACE", "SUPER + Q", "ALT + TAB", "SUPER + D"]
STOP = set("how do does did can could i me my you your to the a an of on in for with and or is are it this that "
           "what which where when key keys shortcut shortcuts press hit use using get make open opens system "
           "computer pc laptop desktop here there check see show tell way want need please turn off all new set".split())
WEAK = {"window", "screen"}       # in too many lines to pick one on their own


def shortcuts() -> list[tuple[str, str]]:
    """(keys, what it does) from the cheatsheet, in its order (most useful first)."""
    for path in CHEATSHEET:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            continue
        found = [tuple(re.split(r"\s{2,}", ln.strip(), maxsplit=1)) for ln in lines if re.search(r"\S\s{2,}\S", ln)]
        if found:
            return found
    return []


def nice_keys(keys: str) -> str:
    """SUPER + ENTER -> Super+Enter; DOUBLE-CLICK WALLPAPER -> Double-click wallpaper."""
    if "+" not in keys:
        return keys.capitalize() if " " in keys else keys.title()
    return "+".join(k.strip().title() if k.strip().isalpha() else k.strip() for k in keys.split("+"))


def nice_text(desc: str) -> str:
    return re.sub(r"\b[A-Z]+(?: \+ [A-Z0-9`]+)+", lambda m: nice_keys(m.group(0)), desc)


SYSTEM_PROMPT_OTHER = (
    "You are the assistant built into the live wallpaper of this computer ({os}). Reply in plain text with no "
    "markdown, at most three short sentences, friendly and direct. If the user wants something done on the computer, "
    "end with one line that is exactly one of: 'ACTION: open_url <url>', 'ACTION: open_app <app name>', "
    "'ACTION: search <query>'. Never claim to have done anything else."
)


def system_prompt() -> str:
    if plat.OS != "linux":
        return SYSTEM_PROMPT_OTHER.format(os=plat.desktop_name())
    lines = [f"{nice_keys(k)}: {nice_text(d)}" for k, d in shortcuts()]
    if not lines:
        return SYSTEM_PROMPT
    return (SYSTEM_PROMPT + " Questions about how to use this computer, its keys, shortcuts or 'commands' are about "
            "this desktop: answer from this list first (Super is the Windows key), and mention that Super+F1 "
            "shows all of it.\n" + "\n".join(lines))


def _stem(w: str) -> str:
    w = {"minimise": "minimize", "maximise": "maximize", "screenshots": "screenshot"}.get(w, w)
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def shortcut_for(low: str):
    """The one shortcut a question is about ("how do I open a terminal"), if it clearly names one.
    A tie between two lines means it's not clear: Claude, who has the whole list, answers instead."""
    words = {_stem(w) for w in re.findall(r"[a-z]+", low) if w not in STOP and len(w) > 2}
    best, best_score, tied = None, 0, False
    for keys, desc in shortcuts():
        main = re.sub(r"\(.*?\)", "", desc).lower()        # notes in brackets don't count
        dwords = {_stem(w) for w in re.findall(r"[a-z]+", main)}
        strong = len((words - WEAK) & dwords)
        score = strong * 2 + len(words & WEAK & dwords)
        if strong and score > best_score:
            best, best_score, tied = (keys, desc), score, False
        elif strong and score == best_score:
            tied = True
    if not best or tied:
        return None
    keys, desc = best
    gesture = re.search(r"CLICK|DRAG|MOUSE", keys) and "+" not in keys
    return reply(f"{nice_keys(keys)}: {nice_text(desc)}." if gesture else f"Press {nice_keys(keys)}: {nice_text(desc)}.")


def shortcut_list():
    table = dict(shortcuts())
    short = {k: re.split(r" \(| ·|:", d)[0] for k, d in table.items()}    # "terminal (kitty) · ..." -> "terminal"
    picks = [f"{nice_keys(k)} {short[k]}" for k in SUMMARY_KEYS if k in short]
    if not picks:
        return reply("Press Super+F1 for the list of shortcuts.", SHOW_SHORTCUTS)
    return reply("The main ones (Super is the Windows key): " + " · ".join(picks) +
                 ". I've opened the full list; Super+F1 shows it any time.", SHOW_SHORTCUTS)


LIST_ASK = re.compile(r"\b(short ?cuts?|hot ?keys?|key ?binds?|key ?bindings?|keyboard|super( key| button)?|"
                      r"windows key|keys|comm?ands?|cmds?)\b")


def reply(text, action=None):
    return {"reply": text, "action": action}


# ── apps ──────────────────────────────────────────────────────────────────
def desktop_apps():
    if plat.OS != "linux":
        return plat.apps()
    seen, apps = set(), []
    for d in APP_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.desktop")):
            if f.name in seen:
                continue
            seen.add(f.name)
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true" or e.get("Type") != "Application":
                continue
            apps.append({"id": f.name, "path": str(f), "name": e.get("Name", f.stem),
                         "words": " ".join([e.get("GenericName", ""), e.get("Keywords", ""), f.stem]).lower()})
    return apps


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def find_app(target: str, min_score: float = 1):
    """Best installed app for a name: whole-word matches beat prefixes beat
    substrings; browser web-app shortcuts (brave-…-Default) lose ties."""
    t = norm(target)
    words_t = target.lower().split()
    if not t:
        return None
    best, best_score = None, 0.0
    for a in desktop_apps():
        n = norm(a["name"])
        words = [norm(w) for w in re.split(r"[\s\-_.]+", a["name"]) if w]
        if n == t:
            score = 5
        elif t in words or (len(words_t) > 1 and all(norm(w) in words for w in words_t)):
            score = 4
        elif n.startswith(t):
            score = 3
        elif any(w.startswith(t) for w in words):
            score = 2.5
        elif t in n:
            score = 2
        elif t in norm(a["words"]):
            score = 1
        else:
            continue
        if re.search(r"-Default\.desktop$|^(brave|chrome|msedge)-[a-z]{20,}", a["id"]):
            score -= 0.5                  # a web-app shortcut, not the real app
        if score > best_score or (score == best_score and best and len(a["name"]) < len(best["name"])):
            best, best_score = a, score
    return best if best and best_score >= min_score else None


def default_browser():
    if plat.OS != "linux":
        return None
    try:
        out = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def projects():
    try:
        return json.loads(STATE.read_text()).get("projects", [])
    except (OSError, json.JSONDecodeError):
        return []


# ── system info ───────────────────────────────────────────────────────────
def meminfo():
    pct, total, avail = plat.memory()
    if not total:
        return "I can't read the memory use here."
    used = (total - avail) / 2**30
    return f"RAM: {used:.1f} of {total / 2**30:.1f} GB in use ({pct}%)."


def diskinfo():
    if plat.OS == "linux":
        try:
            out = subprocess.run(["zpool", "list", "-H", "-o", "size,alloc,free,cap", "rpool"], capture_output=True, text=True).stdout.split()
        except (OSError, subprocess.SubprocessError):
            out = []
        if len(out) == 4:
            return f"Disk: {out[2]} free of {out[0]} ({out[3]} used)."
    pct, total, free = plat.disk()
    return f"Disk: {free / 1e9:.0f} GB free of {total / 1e9:.0f} GB ({pct}% used)." if total else "I can't read the disk."


def tempinfo():
    t = plat.cpu_temp()
    return f"CPU is at {t}°C." if t is not None else "I can't read the CPU temperature here."


def ipinfo():
    ip = plat.local_ip()
    return f"Your local IP is {ip}." if ip else "You don't seem to be connected."


def uptimeinfo():
    s = plat.uptime_seconds()
    return f"Up for {s // 3600} h {s % 3600 // 60} min."


# ── understanding ─────────────────────────────────────────────────────────
def url_action(url):
    return {"type": "url", "url": url}


def open_target(target: str):
    """'open X': site, link, folder, project or app."""
    t = target.strip().strip(".").lower()
    t = re.sub(r"\s+(website|site|app|application|folder)$", "", t)
    if t in ALIASES and plat.OS != "linux":
        kind, _, val = ALIASES[t].partition(":")
        if kind == "browser":
            return reply("Opening your browser.", url_action("https://www.google.com"))
        if val == "kitty.desktop":
            return reply("Opening a terminal.", {"type": "project", "path": str(HOME)})
        if "Nautilus" in val:
            return reply("Opening your files.", {"type": "path", "path": str(HOME)})
        if kind == "drawer":
            return reply("Your apps are in the " + ("Launchpad." if plat.OS == "macos" else "Start menu."))
        app = find_app("visual studio code") if val == "code.desktop" else None
        if app:
            return reply(f"Opening {app['name']}.", {"type": "app", "id": app["id"], "path": app["path"]})
    if t in ALIASES and plat.OS == "linux":
        kind, _, val = ALIASES[t].partition(":")
        if kind == "drawer":
            return reply("Here are all your apps.", {"type": "drawer"})
        if kind == "browser":
            b = default_browser()
            return reply("Opening your browser.", {"type": "app", "id": b} if b else url_action("https://www.google.com"))
        return reply(f"Opening {t}.", {"type": "app", "id": val})
    strong = find_app(t, min_score=4)         # an installed app by that name wins over its website
    if strong:
        return reply(f"Opening {strong['name']}.", {"type": "app", "id": strong["id"], "path": strong["path"]})
    if t in SITES:
        return reply(f"Opening {t.title()}.", url_action(SITES[t]))
    if re.fullmatch(r"(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?", t):
        return reply(f"Opening {t}.", url_action(t if t.startswith("http") else "https://" + t))
    if t in FOLDERS:
        return reply(f"Opening {t}.", {"type": "path", "path": os.path.expanduser(FOLDERS[t])})
    for p in projects():
        if norm(p["name"]).startswith(norm(t)) and len(norm(t)) >= 3:
            return reply(f"Opening {p['name'].title()}.", {"type": "project", "path": p["path"]})
    app = find_app(t)
    if app:
        return reply(f"Opening {app['name']}.", {"type": "app", "id": app["id"], "path": app["path"]})
    return None


def handle(text: str):
    """Returns {'reply', 'action'} for things done locally, or None for a real question."""
    raw = text.strip()
    low = raw.lower().strip().rstrip("?!. ")
    if not low:
        return None
    is_question = raw.endswith("?") or re.match(r"^(how|what|where|why|when|which|who|can|is|are|do|does)\b", low)

    if raw.startswith("/"):
        word = raw[1:].split()[0].lower() if raw[1:].split() else "help"
        cmd = SLASH_ALIASES.get(word, word)
        if cmd == "terminal":
            return reply("Opening a terminal in the wallpaper.", {"type": "wallterm"})
        listing = " · ".join(f"/{k}: {v}" for k, v in SLASH.items())
        return reply(listing if cmd == "help" else f"There's no /{word}. Try: {listing}")
    if low in ("help", "?", "commands", "what can you do", "what can i do", "what can i ask"):
        return reply(HELP)
    if is_question and plat.OS == "linux":
        for pattern, answer in HOWTO:
            if re.search(pattern, low):
                return reply(answer)
    if plat.OS == "linux" and (is_question or LIST_ASK.search(low)):
        r = shortcut_for(low) if is_question or re.search(r"\b(key|shortcut)", low) else None
        if r:
            return r
        if LIST_ASK.search(low):
            return shortcut_list()

    m = (re.match(r"^(?:play|search|find|watch|look up)\s+(.+?)\s+(?:on|in)\s+youtube$", low)
         or re.match(r"^(?:search\s+)?youtube\s+(?:for\s+)?(.+)$", low))
    if m:
        q = m.group(1)
        return reply(f"Searching YouTube for {q}.", url_action("https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(q)))
    m = re.match(r"^(?:search|google|look up|search for|search the web for)\s+(?:for\s+)?(.+)$", low)
    if m:
        q = m.group(1)
        return reply(f"Searching for {q}.", url_action("https://www.google.com/search?q=" + urllib.parse.quote_plus(q)))
    m = re.match(r"^(?:open|launch|start|run|show(?: me)?|go to)\s+(?:up\s+)?(?:the\s+|my\s+)?(.+)$", low)
    if m:
        r = open_target(m.group(1))
        if r:
            return r
    if low in SITES:
        return reply(f"Opening {low.title()}.", url_action(SITES[low]))

    if re.search(r"\b(time)\b", low) and not re.search(r"(time zone|timer|times)", low):
        return reply(time.strftime("It's %H:%M."))
    if re.search(r"\b(date|day is it|today)\b", low):
        return reply(time.strftime("Today is %A, %d %B %Y."))
    if re.search(r"\b(ram|memory)\b", low):
        return reply(meminfo())
    if re.search(r"\b(disk|storage|space left|free space)\b", low):
        return reply(diskinfo())
    if re.search(r"\b(temp|temperature|hot)\b", low):
        return reply(tempinfo())
    if re.search(r"\b(ip|ip address)\b", low):
        return reply(ipinfo())
    if re.search(r"\buptime\b|how long .*(on|running)", low):
        return reply(uptimeinfo())

    if re.fullmatch(r"(lock|lock (the )?screen|lock (my )?(pc|computer))", low):
        return reply("Locking.", {"type": "cmd", "argv": plat.lock_argv()})
    if re.fullmatch(r"(take a )?screenshot|screen ?shot", low):
        return reply("Pick an area.", {"type": "cmd", "argv": plat.screenshot_argv()})
    m = re.fullmatch(r"(?:turn )?(?:the )?volume (up|down)|(mute|unmute)", low)
    if m:
        what = m.group(1) or m.group(2)
        if plat.OS != "linux":
            return reply({"up": "Volume up.", "down": "Volume down.", "mute": "Muted.", "unmute": "Unmuted."}[what],
                         {"type": "volume", "what": what})
        argv = {"up": ["wpctl", "set-volume", "-l", "1", "@DEFAULT_AUDIO_SINK@", "10%+"],
                "down": ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "10%-"],
                "mute": ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1"],
                "unmute": ["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0"]}[what]
        return reply({"up": "Volume up.", "down": "Volume down.", "mute": "Muted.", "unmute": "Unmuted."}[what], {"type": "cmd", "argv": argv})

    if is_question:
        return None                       # a real question: ask Claude
    # an unknown short command ("blender", "spotify") is most likely an app or site
    if len(low.split()) <= 3:
        r = open_target(low)
        if r:
            return r
    return None


def ask(question: str, timeout: int = 45):
    """Ask Claude (Haiku). Returns {'reply', 'action'}; never raises."""
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(claude):
        return reply("I can only do quick local things right now (Claude isn't installed).")
    WORKDIR.mkdir(parents=True, exist_ok=True)
    cmd = [claude, "-p", "--model", "haiku", "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
           "--no-session-persistence", "--setting-sources", "", "--system-prompt", system_prompt(), question]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=WORKDIR)
    except subprocess.TimeoutExpired:
        return reply("That took too long. Try again in a moment?")
    except OSError as e:
        return reply(f"I couldn't reach Claude ({e.strerror}).")
    text = (out.stdout or "").strip()
    if out.returncode != 0 or not text:
        err = (out.stderr or "").strip().splitlines()
        return reply("I couldn't get an answer" + (f": {err[-1][:120]}" if err else "."))
    lines = text.splitlines()
    action = None
    if lines and lines[-1].strip().upper().startswith("ACTION:"):
        verb, _, arg = lines.pop().split(":", 1)[1].strip().partition(" ")
        arg = arg.strip()
        if verb == "open_url" and re.match(r"^https?://", arg):
            action = url_action(arg)
        elif verb == "search" and arg:
            action = url_action("https://www.google.com/search?q=" + urllib.parse.quote_plus(arg))
        elif verb == "show_shortcuts":
            action = SHOW_SHORTCUTS
        elif verb == "open_app" and arg:
            app = find_app(arg)
            action = {"type": "app", "id": app["id"], "path": app["path"]} if app else None
    return reply(" ".join(l.strip() for l in lines if l.strip()) or "Done.", action)


def run_action(action, launch_app=None):
    """Carry out an action. launch_app(id) lets the dock launch through GTK."""
    if not action:
        return
    kind = action["type"]

    def hypr_exec(cmdline: str):
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE"):
            subprocess.Popen(["hyprctl", "dispatch", "exec", cmdline], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(cmdline, shell=True, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if plat.OS != "linux":
        if kind in ("url", "path"):
            plat.open_url(action.get("url") or action["path"])
        elif kind == "project":
            plat.open_terminal(action["path"])
        elif kind == "app":
            plat.launch_app(action)
        elif kind == "volume":
            plat.volume(action["what"])
        elif kind == "wallterm":
            x, y = action.get("x"), action.get("y")
            tell_wall("term" + (f" {x} {y}" if x is not None and y is not None else ""))
        elif kind == "cmd" and action.get("argv"):
            plat.spawn(action["argv"])
        return
    if kind == "url":
        hypr_exec("xdg-open " + _q(action["url"]))
    elif kind == "path":
        hypr_exec("xdg-open " + _q(action["path"]))
    elif kind == "project":
        hypr_exec("kitty --directory " + _q(action["path"]))
    elif kind == "drawer":
        hypr_exec(os.path.expanduser("~/.config/hypr/scripts/apps.sh"))
    elif kind == "app":
        if launch_app and launch_app(action["id"]):
            return
        hypr_exec("gtk-launch " + _q(action["id"].removesuffix(".desktop")))
    elif kind == "wallterm":
        x, y = action.get("x"), action.get("y")
        tell_wall("term" + (f" {x} {y}" if x is not None and y is not None else ""))
    elif kind == "cmd":
        subprocess.Popen(action["argv"], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def tell_wall(msg: str):
    """Send the wallpaper a command (it listens on its own socket)."""
    import socket
    if not plat.has_unix_dgram():
        return
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.sendto(msg.encode(), WALL_SOCKET)
    except OSError:
        pass


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"
