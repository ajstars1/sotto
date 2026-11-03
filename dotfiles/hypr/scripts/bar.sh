#!/usr/bin/env bash
# Keep the top bar alive: waybar can die (e.g. when a TV/monitor is plugged or unplugged),
# and Hyprland only starts it once. Restart it whenever it exits; log why.
LOG="$HOME/.local/state/waybar.log"
mkdir -p "$(dirname "$LOG")"
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/bar-keeper.lock"; flock -n 9 || exit 0   # one keeper only
while :; do
  waybar >>"$LOG" 2>&1
  echo "$(date '+%F %T') waybar exited with code $?, restarting" >>"$LOG"
  tail -n 400 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
  sleep 2
done
