# Customize sotto

Everything you can change lives in one file: `~/.config/sotto/config.toml`
(`python -m sotto config path` prints it). Every key is optional.

- **Changes show up as soon as you save.** The running wallpaper re-reads the file within a second, so you never restart it.
- **A broken file never breaks the wallpaper.** It falls back to the defaults.

```sh
python -m sotto config init      # a commented starter file (won't touch an existing one)
python -m sotto config check     # what's wrong: unknown keys, bad colours, a widget without a source
python -m sotto preview out.png  # one frame with your config, as a PNG
python -m sotto web              # the live wallpaper in a browser tab, to try things
python -m sotto themes           # the built-in themes
```

Prefer to ask in plain words? See [Let Claude do it](#let-claude-do-it) at the end.

## Look

```toml
name = "Sam"          # used in the greeting
theme = "night"       # fresco (default) · night · paper · sage · dusk

[colors]              # override any colour of the theme
accent = "#d0a35c"
```

The colours and what they're for:

| Name | Used for |
|---|---|
| `bg` | the wallpaper |
| `text` | what you read |
| `dim` | labels and quiet text |
| `line` | the faintest marks |
| `accent` | highlights |
| `alert` | problems |
| `faint`, `surface` | the dot trail as it fades, the terminal |

Keep `text` well apart from `bg`.

```toml
[display]
font_size = 11        # macOS (points), Windows and browser (CSS px). Sharp at 11, 22, 33.
```

On Linux the size comes from `bin/sotto-wall` (8.25 pt = 11 px).

## Clock and greeting

```toml
[clock]
format = "12h"        # "24h" (default), "12h", or any strftime pattern: "%H:%M:%S"
show = true

[greeting]
text = "Good {part}, {name}"   # {part}: morning/afternoon/evening/night · {name} · {date}
date = "%A %d %B"              # shown before the greeting; "" for none
show = true
```

## Layout

The wallpaper has two columns under the clock. Each column is a list of blocks, drawn top to bottom:

```toml
[layout]
left = ["agents"]
right = ["today", "weather", "focus"]
```

- **The blocks:** `agents` (your Claude Code sessions), `today` (calendar and tasks), and the `id` of any widget.
- **One column:** leave the other list empty and the one column is centred: `left = ["today", "focus"]` with `right = []`.
- **Unplaced widgets:** if you don't set `right`, widgets you haven't placed go under Today.

## Your own widgets

A widget is a titled block with exactly one source.

```toml
[[widget]]
id = "focus"                       # a name for [layout]
title = "THIS WEEK"                # "" for no title
text = ["Ship the beta", "Gym x3"] # fixed lines

[[widget]]
id = "notes"
title = "NOTES"
file = "~/notes/today.md"          # the file's first lines; re-read when you save it
lines = 6                          # at most this many rows (default 8)

[[widget]]
id = "weather"
title = "WEATHER"
command = "curl -s 'https://wttr.in/?format=%l:+%c+%t'"
every = 1800                       # seconds between runs (min 5, default 300)
timeout = 10                       # seconds before it's stopped (default 10)
color = "accent"                   # the rows' colour: a name above or "#rrggbb"
```

**Commands.** A command is anything your shell can run, in any language:
- **Output.** Each line it prints becomes a row.
- **Where it runs.** In the background, through `sh` on Linux and macOS and `cmd` on Windows. A slow one never stalls the wallpaper.
- **Failures.** A command that fails shows its exit code and last error line in the alert colour.

**Colours from a command.** Print JSON instead of plain lines:

```json
{"lines": ["plain row", {"text": "a highlight", "color": "accent"}, {"text": "3 builds failed", "color": "alert"}]}
```

**More ideas:**

```toml
[[widget]]
id = "repo"
title = "SOTTO"
command = "git -C ~/Developer/sotto status --short --branch"
every = 30

[[widget]]
id = "uptime"
title = "MACHINE"
command = "uptime"                 # macOS and Linux
every = 60

[[widget]]
id = "next"
title = "NEXT TRAIN"
command = "python3 ~/bin/next_train.py"   # your own script: print one or two lines
every = 120
```

The wallpaper is on screen for anyone in the room, so don't show secrets in a widget.

## Effects

```toml
[effects]
trail = true            # dots follow the mouse
dissolve = true         # text and the clock come loose under the mouse and settle again
trail_chars = "·•∙"     # what the trail is made of: try "*+." or "░▒"
fps = 30                # animation frame rate (5 to 60); it only draws when something moves
```

## Your day

```toml
[today]
ics = ["https://calendar.google.com/calendar/ical/.../basic.ics"]   # events until the end of tomorrow
command = "my-tasks --json"
```

The command prints JSON like this:

```json
{"items": [{"when": "19:30", "title": "Call Sam", "flag": "overdue"}], "footer": "..."}
```

`flag` is one of `overdue`, `today` or `event`.

## Scripts can talk to it

```sh
python -m sotto say "build finished · 42 tests passed"   # takes the greeting's place for a few seconds
```

## Let Claude do it

sotto comes with a [Claude Code](https://claude.com/claude-code) skill that knows all of the above. It:
- edits your config;
- checks it;
- renders a preview and looks at it before telling you it's done.

```sh
python -m sotto skill install    # copies skills/sotto-customize into ~/.claude/skills/
```

Then ask Claude Code things like "make my wallpaper dark with a green accent", "add the weather in Lisbon under Today", or "show my open pull requests on the wallpaper".
