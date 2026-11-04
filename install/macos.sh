#!/bin/sh
# sotto on macOS (preview). Run from the repo:  ./install/macos.sh
#   ./install/macos.sh            set up and start the wallpaper, and start it at every login
#   ./install/macos.sh --plan     print what it would do; change nothing
#   ./install/macos.sh --uninstall  stop it and remove the login item (your config stays)
set -e
cd "$(dirname "$0")/.."
REPO=$(pwd)
LABEL=io.github.ajstars1.sotto
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
FONT="$HOME/Library/Fonts/DepartureMono-Regular.otf"
PLAN=0
case "${1:-}" in
    --plan) PLAN=1 ;;
    --uninstall)
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        rm -f "$PLIST"
        echo "sotto stopped and removed from login items. Config kept at ~/.config/sotto/."
        exit 0 ;;
esac
say() { printf '%s\n' "$*"; }
do_() { if [ "$PLAN" = 1 ]; then say "would: $*"; else "$@"; fi; }

[ "$(uname)" = Darwin ] || { say "this installer is for macOS"; exit 1; }

# 1. kitty 0.42+ (its panel draws on the desktop layer)
KITTY=""
for k in /Applications/kitty.app/Contents/MacOS/kitty "$HOME/Applications/kitty.app/Contents/MacOS/kitty" "$(command -v kitty || true)"; do
    [ -n "$k" ] && [ -x "$k" ] && { KITTY=$k; break; }
done
if [ -z "$KITTY" ]; then
    say "kitty is missing. Install it, then run this again:"
    say "    brew install --cask kitty      (or download it from https://sw.kovidgoyal.net/kitty/)"
    exit 1
fi
VER=$("$KITTY" --version | awk '{print $2}')
MAJ=$(echo "$VER" | cut -d. -f1); MIN=$(echo "$VER" | cut -d. -f2)
if [ "$MAJ" -eq 0 ] && [ "$MIN" -lt 42 ]; then
    say "kitty $VER is too old: the background panel needs 0.42 or newer (brew upgrade --cask kitty)"
    exit 1
fi
say "kitty $VER: ok"

# 2. uv and the Python env
if ! command -v uv >/dev/null 2>&1; then
    say "uv is missing. Install it (https://docs.astral.sh/uv/), then run this again:"
    say "    brew install uv"
    exit 1
fi
do_ uv sync --managed-python --python 3.12

# 3. the font (Departure Mono, MIT, bundled in sotto/fonts)
if [ -f "$FONT" ]; then say "font: already installed"; else do_ cp sotto/fonts/DepartureMono-Regular.otf "$FONT"; fi

# 4. a starter config, unless you have one
do_ .venv/bin/python -m sotto config init

# 5. start at login, and now
if [ "$PLAN" = 1 ]; then
    say "would: write $PLIST (runs $REPO/bin/sotto-wall-macos at login) and start it"
    exit 0
fi
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key><array><string>$REPO/bin/sotto-wall-macos</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>StandardErrorPath</key><string>$HOME/Library/Logs/sotto.log</string>
    <key>StandardOutPath</key><string>$HOME/Library/Logs/sotto.log</string>
</dict>
</plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
say ""
say "sotto is on your desktop. Double-click the wallpaper to ask it something."
say "Customize it:  .venv/bin/python -m sotto config check   (file: ~/.config/sotto/config.toml)"
say "Logs: ~/Library/Logs/sotto.log   Stop it: ./install/macos.sh --uninstall"
