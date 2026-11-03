#!/usr/bin/env bash
# Make everything bigger or smaller on the screen the mouse is on, and remember it
# for that screen (TV, monitor...) next time it is plugged in.
#   scale.sh up | down | reset  [screen description]   (default: the focused screen)
set -euo pipefail

STEPS=(1 1.25 1.5 2 2.5 3)
SAVED="$HOME/.config/hypr/monitors.conf"
touch "$SAVED"

if [ -n "${2:-}" ]; then
  mon=$(hyprctl monitors -j | jq -c --arg d "$2" '.[] | select(.description == $d)')
else
  mon=$(hyprctl monitors -j | jq -c '.[] | select(.focused)')
fi
[ -n "$mon" ] || exit 1
desc=$(jq -r '.description' <<<"$mon")
cur=$(jq -r '.scale' <<<"$mon")

# index of the step closest to the current scale
idx=0; best=99
for i in "${!STEPS[@]}"; do
  d=$(awk -v a="${STEPS[$i]}" -v b="$cur" 'BEGIN{d=a-b; print (d<0?-d:d)}')
  if awk -v d="$d" -v b="$best" 'BEGIN{exit !(d<b)}'; then best=$d; idx=$i; fi
done

case "${1:-up}" in
  up)    dir=1 ;;
  down)  dir=-1 ;;
  reset) dir=0; idx=0 ;;
esac

# try the next step; if this screen's resolution can't take it, Hyprland keeps the
# old scale, so keep stepping until one sticks
new=$cur
while :; do
  if (( dir != 0 )); then
    idx=$((idx + dir))
    (( idx < 0 || idx >= ${#STEPS[@]} )) && break
  fi
  hyprctl keyword monitor "desc:$desc, preferred, auto, ${STEPS[$idx]}" >/dev/null
  got=$(hyprctl monitors -j | jq -r --arg d "$desc" '.[] | select(.description == $d) | .scale')
  if awk -v a="$got" -v b="$cur" 'BEGIN{d=a-b; exit !(d>0.01||d<-0.01)}' || (( dir == 0 )); then
    new=${STEPS[$idx]}; break
  fi
done

# remember it for this screen
grep -vF "desc:$desc," "$SAVED" > "$SAVED.tmp" || true
echo "monitor = desc:$desc, preferred, auto, $new" >> "$SAVED.tmp"
mv "$SAVED.tmp" "$SAVED"

notify-send -t 1500 "Screen size" "$desc: ${new}x" 2>/dev/null || true
