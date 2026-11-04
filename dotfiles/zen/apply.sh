#!/bin/sh
# Copies the sotto theme into Zen's profile. Copies, not links: the Zen Flatpak
# can't see ~/dotfiles. Run it again after changing the files here, with Zen closed.
#   apply.sh --plan   print what it would copy and back up; changes nothing
set -e
PLAN=0
[ "${1:-}" = "--plan" ] && PLAN=1
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
for root in "$HOME/.var/app/app.zen_browser.zen/.zen" "$HOME/.zen"; do
    [ -f "$root/profiles.ini" ] || continue
    # the profile Zen actually opens: the install's default, else the one marked Default=1
    prof="$(sed -n 's/^Default=\(.*\)$/\1/p' "$root/installs.ini" 2>/dev/null | head -1)"
    [ -n "$prof" ] || prof="$(awk -F= '/^Path=/{p=$2} /^Default=1/{print p; exit}' "$root/profiles.ini")"
    dir="$root/$prof"
    [ -d "$dir" ] || continue
    found=1
    if [ "$PLAN" = 1 ]; then
        for f in chrome/userChrome.css chrome/userContent.css user.js; do
            dst="$dir/$f"
            if [ -f "$dst" ] && cmp -s "$HERE/$(basename "$f")" "$dst"; then
                echo "  Zen: $dst is already the sotto version"
                continue
            fi
            [ -f "$dst" ] && echo "  would back up $dst to $dst.before-sotto"
            echo "  would copy $(basename "$f") to $dst"
        done
        continue
    fi
    mkdir -p "$dir/chrome"
    for f in chrome/userChrome.css chrome/userContent.css user.js; do
        dst="$dir/$f"
        if [ -f "$dst" ] && ! cmp -s "$HERE/$(basename "$f")" "$dst"; then
            cp "$dst" "$dst.before-sotto"            # keep what was there
        fi
        cp "$HERE/$(basename "$f")" "$dst"
    done
    echo "  Zen themed: $dir (restart Zen to see it)"
done
[ -n "${found:-}" ] || echo "  Zen isn't installed (no profile found): nothing to do"
