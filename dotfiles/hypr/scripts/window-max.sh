#!/bin/sh
# Maximize / restore the way Windows and macOS do it: the window grows to fill
# the screen below the top bar but stays a normal window, so clicking another
# window still brings that one to the front. Run again to restore its old size.
# usage: window-max.sh [address]      (default: the focused window)
addr="$1"
[ -z "$addr" ] && addr=$(hyprctl activewindow -j | jq -r .address)
win=$(hyprctl clients -j | jq -c --arg a "$addr" '.[] | select(.address == $a)')
[ -n "$win" ] || exit 0
state="${XDG_RUNTIME_DIR:-/tmp}/hypr-max"; mkdir -p "$state"
BAR=26                                           # hyprbars title bar height
B=$(hyprctl getoption general:border_size -j | jq .int)

# leave Hyprland's own fullscreen/maximize state if the window is in it
if [ "$(echo "$win" | jq .fullscreen)" != 0 ]; then
    hyprctl dispatch focuswindow "address:$addr" >/dev/null
    hyprctl dispatch fullscreenstate 0 0 >/dev/null
    rm -f "$state/$addr"                         # it was maximized the old way: maximize it the new way
    sleep 0.1
    win=$(hyprctl clients -j | jq -c --arg a "$addr" '.[] | select(.address == $a)')
    echo "$win" | jq -r '"\(.at[0]) \(.at[1]) \(.size[0]) \(.size[1])"' > "$state/$addr.pre"
fi
[ "$(echo "$win" | jq .floating)" = true ] || hyprctl dispatch setfloating "address:$addr" >/dev/null

mon=$(hyprctl monitors -j | jq -c --argjson id "$(echo "$win" | jq .monitor)" '.[] | select(.id == $id)')
# width/height are the panel's real pixels; windows live in scaled ones (4K TV at 2.5x = 1536x864)
eval "$(echo "$mon" | jq -r '(if (.transform % 2) == 1 then [.height, .width] else [.width, .height] end) as [$w, $h]
  | "mx=\(.x) my=\(.y) mw=\($w / .scale | round) mh=\($h / .scale | round) rl=\(.reserved[0]) rt=\(.reserved[1]) rr=\(.reserved[2]) rb=\(.reserved[3])"')"
X=$((mx + rl + B)); Y=$((my + rt + BAR + B)); W=$((mw - rl - rr - 2 * B)); H=$((mh - rt - rb - BAR - 2 * B))
eval "$(echo "$win" | jq -r '"wx=\(.at[0]) wy=\(.at[1]) ww=\(.size[0]) wh=\(.size[1])"')"

near() { d=$(($1 - $2)); [ ${d#-} -le 3 ]; }
if [ -f "$state/$addr.pre" ]; then
    mv "$state/$addr.pre" "$state/$addr"         # remember its normal size, then maximize below
    wx=-99999
fi
if near "$wx" "$X" && near "$wy" "$Y" && near "$ww" "$W" && near "$wh" "$H"; then
    # already maximized: go back to the saved size, or a comfortable centred one
    if [ -f "$state/$addr" ]; then read -r ox oy ow oh < "$state/$addr"
    else ow=$((W * 7 / 10)); oh=$((H * 7 / 10)); ox=$((X + (W - ow) / 2)); oy=$((Y + (H - oh) / 2)); fi
    hyprctl dispatch resizewindowpixel "exact $ow $oh,address:$addr" >/dev/null
    hyprctl dispatch movewindowpixel "exact $ox $oy,address:$addr" >/dev/null
    rm -f "$state/$addr"
else
    [ "$wx" = -99999 ] || echo "$wx $wy $ww $wh" > "$state/$addr"
    hyprctl dispatch resizewindowpixel "exact $W $H,address:$addr" >/dev/null
    hyprctl dispatch movewindowpixel "exact $X $Y,address:$addr" >/dev/null
fi
hyprctl dispatch focuswindow "address:$addr" >/dev/null
hyprctl dispatch alterzorder "top,address:$addr" >/dev/null
