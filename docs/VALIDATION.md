# Validation

What has been checked, how, and what still needs a person at the screen.
Last updated **4 Nov 2026**. Rule for this file: a live item is marked verified only
by someone who saw it happen on the real desktop. Everything else stays "not yet verified".

## Automated checks

Commands anyone (or an agent) can run from the repo root. None of them change the
desktop: they don't restart the wall, the dock or the bar, and the ones that write
files write into a scratch folder or `~/.cache/sotto/`.

| Check | Command | Result on 4 Nov 2026 |
|---|---|---|
| Every Python file compiles | `uv run python -m py_compile sotto/*.py dotfiles/hypr/scripts/*.py` | pass |
| Every shell script parses | `for f in dotfiles/install.sh dotfiles/hypr/scripts/*.sh bin/*; do head -1 "$f" \| grep -q python \|\| bash -n "$f"; done` | pass |
| Installed packages, links, uv env, versions, `$sotto` | `./dotfiles/install.sh doctor` (exit 0 = all required checks pass) | exit 0: 27/27 packages, 9/9 links into the repo, `.venv` Python 3.12.12 with pyte and Pillow, kitty 0.45.0, Hyprland 0.53.3, `$sotto` = this repo; optional: Claude Code present, login screen installed and enabled, boot screen installed and active |
| Doctor catches problems | `HOME=<empty dir with a wrong link and a real folder> ./dotfiles/install.sh doctor` | exit 1, with MISSING / WRONG / NOT-LINK lines and `$sotto` WRONG |
| Installer plan changes nothing | `./dotfiles/install.sh --plan`, comparing `ls -la ~/.config` (and kitty, btop, ghostty subfolders), `~/.dotfiles-backup`, md5 of `~/.zshrc` and `btop.conf`, the GTK cursor setting, Zen theme files, script modes and `git status`/`git diff` before and after | exit 0; everything "already linked/set"; Zen files "already the sotto version"; no differences (the only changed line was `~/.config/aos-agent-chrome`, a Chrome profile another program was using) |
| Plan on a fresh home | `HOME=<empty dir> ./dotfiles/install.sh --plan` | lists every link, the moves into the backup folder, btop, zsh, Zen and cursor steps; the folder was unchanged afterwards |
| Waybar config is valid JSON | `python3 -c "import json; json.load(open('dotfiles/waybar/config.jsonc'))"` | pass |
| Running Hyprland config has no errors | `hyprctl configerrors` | no errors reported |
| A frame renders | `XDG_CACHE_HOME=<scratch> uv run python -m sotto preview out.png` | 1365×756 PNG written |
| Boot screen renders | `uv run python -m sotto bootsplash <dir> --preview splash.png` | theme files and preview written |
| The assistant's local intents | `uv run python -m sotto ask "open youtube"`, `... ask "how much ram"` (no `--do`) | "Opening Youtube." with a url action; RAM answered from the system |
| Profiler, end to end | a second wall in a pseudo-terminal with its own `XDG_RUNTIME_DIR`/`XDG_CACHE_HOME`, `SOTTO_DEMO=1 SOTTO_STATE=demo/state.json`; then `python -m sotto profile 8` with a mouse sweep and a `say` | JSON written and its path printed; 99 frames: 79 `trail`, 20 `say`; p50 0.45 ms, p95 0.67 ms; 3 idle seconds of 8 |
| Profiler, idle wall | same, 65 s with no input | 1 frame in 65 s (`clock`, the minute changing, 176 bytes); 64 idle seconds |
| Profiler costs nothing when off | `timeit` of `Wall.present()` with no recording against the old code path | 49.9–50.4 µs vs 49.8 µs per frame: within noise |
| Profiler against a wall that predates it | `uv run python -m sotto profile 1` with the old wall still running | exits 1 after 6 s with "restart it with bin/sotto-restart" |
| Agent badge logic | `dotfiles/hypr/scripts/agent-badge.py` with `XDG_CACHE_HOME=<scratch>`, writing `aos-desk/acting.json` | shows `● claude` for a valid future `until`, hides on expiry, deletion, bad JSON, `until` true or NaN, a list, a file over 4 KB, a FIFO, a symlink; tool name reduced to `[A-Za-z0-9_.:-]`; picks the folder up once a second until it exists, then by inotify |
| Agent badge idle cost | 10 s with the badge script waiting | 0 CPU ticks, no wake-ups |

Not run on 4 Nov 2026: `Hyprland --verify-config` (what `install.sh` runs at the end), the real
`install.sh` (only `--plan`), `bin/sotto-greeter preview`, and `python -m sotto profile` against the
real wallpaper (it needs a wall restart first, see below).

## macOS, Windows and the config (0.2 preview)

| Check | Command | Result on 4 Nov 2026 |
|---|---|---|
| Test suite: config, widgets, layout, PNG preview, system numbers, assistant, a shell in a pty, a real wall driven over the web transport (first frame, mouse trail, double-click prompt answering, live theme switch), the server's token and Host checks | `uv run python -W ignore -m unittest discover -s tests -v` | Linux: 17/17 pass. macOS and Windows: see the GitHub Actions run for the release tag |
| The wall on a real tty, new code | a wall in a pseudo-terminal with its own `XDG_RUNTIME_DIR`/`XDG_CACHE_HOME`/`SOTTO_CONFIG`, demo state: mouse motion, double-click, `how much ram` + Enter, then the config's theme changed to dusk | first frame 34 KB; trail dots drawn; answer contains "RAM"; dusk background drawn within 2.5 s; clean exit, empty wall.log |
| `sotto web` in Chrome | Claude in Chrome on the page, demo state, a test config with two widgets | page draws; xterm.js sends SGR mouse reports and trail dots appear in the DOM; the double-click prompt answered "It's 19:40"; saving `theme = "night"` and a 12h clock repainted the open page |
| Real config still valid | `python -m sotto config check` on this machine's config | looks good; layout and colours unchanged |
| Wheel contents | `uv build --wheel` | the package plus fonts, web assets and the skill |

Not verified on a real Mac or a real Windows desktop yet: placement behind the icons,
cursor polling, the keyboard hand-over, LaunchAgent/Startup login items, and Retina/HiDPI sizing.

## Live checklist

Only a person at the screen can confirm these. Mark one verified with the date and who saw it.

| What | How to check | Status |
|---|---|---|
| Wallpaper draws at login | log in to Hyprland: clock, greeting, six projects on parchment | not yet verified |
| Only changed cells redraw | `python -m sotto collect` after a commit, or edit `state.json`: only the changed text flickers to dots and settles | not yet verified |
| Mouse trail and dissolve | move over empty space and over the clock: dots fade; clock pixels shade and re-form | not yet verified |
| `say` | `python -m sotto say "hello"`: replaces the greeting for about 8 s | not yet verified |
| Click a project | opens it (terminal at that folder, or its `open` commands) | not yet verified |
| Right-click the wallpaper | the desktop menu opens | not yet verified |
| Double-click prompt | double-click empty wallpaper: the text line appears at the click; Esc or a click elsewhere closes it | not yet verified |
| Local intents | `open youtube`, `how much ram`, `lock`, `shortcuts` act at once | not yet verified |
| Claude answers | a real question ("what is a fresco?") answers in 2–3 sentences | not yet verified |
| `/terminal` | the shell appears on the wallpaper; type, drag the label, resize from the corner, scroll, × closes, keyboard goes back on a click elsewhere | not yet verified |
| Profiler on the real wall | after a wall restart: `python -m sotto profile 30` prints a path; the JSON shows idle seconds while you don't touch the desktop | not yet verified |
| Agent badge | after a bar reload: write `{"tool": "click", "until": <now + 10>}` to `~/.cache/aos-desk/acting.json`; ochre `● claude` appears at the left of the right-hand status, hides 10 s later | **verified 4 Nov 2026** (8 s `until`, screenshots of the bar before and after) |
| Top bar | Apps button, one icon per window (click to switch, again to minimize, middle-click closes), status, show desktop | not yet verified |
| Dock | hidden until the mouse touches the bottom edge; pinned apps, running dots, right-click menu, Dock Settings | not yet verified |
| Apps list | `Super+Space`: recent apps first, search ranks names then descriptions ("br" finds browsers) | not yet verified |
| Snapping | drag a window to an edge and pause: outline preview, half/quarter/maximize; dragging away restores the old size | not yet verified |
| Minimize, maximize, title bars | × + – buttons and double-click on the title bar act on the window under the cursor | not yet verified |
| Login screen | from boot: the wallpaper's clock and a password line; Tab picks the session; logging in dissolves into the desktop | not yet verified |
| Boot splash | restart: parchment, "sotto" in the pixel font, greeting, dot trail | not yet verified |
| Lock screen | `Super+L`, or `lock` in the prompt | not yet verified |
| Zen theme | Zen in the parchment palette after a restart of Zen | not yet verified |
| Control Center and quick menus | top-bar button: Wi-Fi, Sound, Displays, Bluetooth menus open on one click | not yet verified |

## Agent board and Today (added 4 Nov 2026)

| Check | Status |
|---|---|
| `sotto.agents.sessions()` reads the 6 live sessions (24 ms first read, 0.5 ms cached) and sorts needs / working / done | verified 4 Nov |
| Click-to-jump lookup: each session's pid chain reaches its kitty window; tmux session reported as tmux; phone session as "no window" | verified 4 Nov (dry run, no focus change) |
| aos `hooks/today-json.ts` prints the day's JSON (9 overdue human tasks) | verified 4 Nov |
| New wall runs 12 s in a hidden pty without errors | verified 4 Nov |
| Live wallpaper shows the two columns, and the working row updates as a session works | **verified 4 Nov** (screenshot with windows hidden) |
| Clicking a row on the live wallpaper brings that terminal to the front | not yet verified live |
| iCal source with a real calendar link | not yet verified (no link configured) |
