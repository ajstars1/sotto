---
name: sotto-customize
description: Customize the sotto live wallpaper (theme, colours, clock, greeting, layout, effects, and your own widgets that show text, a file or a command's output). Use when the user asks to change, theme, restyle or add something to their sotto wallpaper, e.g. "make my wallpaper dark", "add the weather to my wallpaper", "show my git status on the desktop", "use a 12-hour clock".
---

# Customize sotto

sotto is a live wallpaper drawn as a character grid. Everything a user can change lives in one
TOML file, and the running wallpaper re-reads it within a second of a save, so you never restart
anything. Your job: turn what the user asks for into a valid config, check it, show them a
picture, and explain what you changed.

## 1. Find the setup

Run these from the sotto repo (the folder with `pyproject.toml`); use `.venv/bin/python`
(`.venv\Scripts\python` on Windows) or `uv run python` as the interpreter.

```sh
python -m sotto config path      # where the config lives (usually ~/.config/sotto/config.toml)
python -m sotto config show      # every setting in use, defaults filled in
python -m sotto themes           # the built-in themes and their colours
```

No config yet? `python -m sotto config init` writes a commented starter file. Never overwrite an
existing config with `--force` unless the user asks; edit it instead, keeping their other keys.

## 2. What can change

Read `docs/CUSTOMIZE.md` in the repo for the full reference. In short:

| Want | Key |
|---|---|
| a different look | `theme = "fresco" \| "night" \| "paper" \| "sage" \| "dusk"` |
| one colour | `[colors]` with `bg faint surface line dim text accent alert` = `"#rrggbb"` |
| clock | `[clock] format = "24h" \| "12h" \| "%H:%M:%S"`, `show = false` hides it |
| greeting | `[greeting] text = "Hi {name}"` (`{part}` `{name}` `{date}`), `date = "%a %d %b"`, `show` |
| what goes where | `[layout] left = [...]`, `right = [...]`: `"agents"`, `"today"`, or widget ids |
| effects | `[effects] trail`, `dissolve` (true/false), `trail_chars = "·•∙"`, `fps` |
| text size | `[display] font_size = 11` (macOS, Windows, browser; sharp at 11, 22, 33) |
| calendar and tasks | `[today] ics = ["<iCal link>"]`, `command = "<prints JSON>"` |
| your own block | `[[widget]]` with `id`, `title`, and exactly one of `text`, `file`, `command` |

A widget command can be any program in any language. Each line it prints is a row; for colours it
prints JSON: `{"lines": ["plain", {"text": "late", "color": "alert"}]}`. Options: `every`
(seconds, min 5, default 300), `timeout` (default 10), `lines` (max rows, default 8), `color`.

## 3. Rules for good widgets

- **Fast and quiet.** A command runs in the background every `every` seconds with a timeout. Pick
  the slowest `every` that is still useful (weather: 900+, git status: 30 to 60, a clock: don't).
- **Short lines.** A column is roughly 50 to 90 characters wide; longer lines end in "…".
- **Nothing secret.** The wallpaper is on screen for anyone in the room. Don't put tokens, private
  messages or anything the user wouldn't show a visitor into a widget.
- **Check the command first.** Run it in a shell and look at the output before adding it, so the
  user doesn't get an error row. Use full paths if it depends on PATH tweaks from a shell profile.
- **Portable when it matters.** Widget commands run through the system shell (sh on Linux and
  macOS, cmd on Windows). Write for the user's OS.
- **New widgets need a place.** If `[layout]` sets `right`, add the widget's id there (or to
  `left`); widgets not in any column are not shown. Without `right` in `[layout]`, widgets go
  under Today by themselves.

## 4. Change, check, look

1. Edit the config file with your file tools (keep comments and the user's existing keys).
2. `python -m sotto config check`: must print "looks good" (exit 0). Fix every line it lists.
3. `python -m sotto preview /tmp/sotto-preview.png` renders one frame with the new config (it runs
   widgets once). Open the PNG and look at it: is the text readable on the background, does the
   widget fit, did the layout change the way the user wanted?
4. Tell the user what changed and that the running wallpaper has already picked it up.

To try without touching their real wallpaper: `SOTTO_CONFIG=/tmp/try.toml python -m sotto preview
/tmp/try.png`, or `python -m sotto web` to see it live in a browser.

## 5. Recipes

```toml
# weather (wttr.in, no key needed)
[[widget]]
id = "weather"
title = "WEATHER"
command = "curl -s 'https://wttr.in/?format=%l:+%c+%t'"
every = 1800

# the repo you're in, at a glance
[[widget]]
id = "repo"
title = "SOTTO REPO"
command = "git -C ~/Developer/sotto status --short --branch"
every = 30
lines = 6

# a focus list you edit by hand
[[widget]]
id = "focus"
title = "THIS WEEK"
text = ["Ship the beta", "Call the bank", "Gym x3"]

# your notes file, first lines
[[widget]]
id = "notes"
title = "NOTES"
file = "~/notes/today.md"
lines = 6

[layout]
left = ["agents", "repo"]
right = ["today", "weather", "focus"]
```

Custom colours: keep enough contrast between `text` and `bg` (dark on light or light on dark);
`dim` is for labels, `line` for the faintest marks, `accent` for highlights, `alert` for problems.
