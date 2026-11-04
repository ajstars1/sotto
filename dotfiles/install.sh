#!/usr/bin/env bash
# sotto dotfiles installer.
# Symlinks everything into ~/.config. Anything it would replace is moved to
# ~/.dotfiles-backup/<timestamp>/ first, so every change can be undone.
# Safe to run more than once.
#
#   ./dotfiles/install.sh           install
#   ./dotfiles/install.sh --plan    print every action it would take; changes nothing
#   ./dotfiles/install.sh doctor    check what's installed and linked; changes nothing
#                                   (exit 0 only when every required check passes)
set -euo pipefail

DOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$DOT/.." && pwd -P)"
BAK="$HOME/.dotfiles-backup/$(date +%Y%m%d-%H%M%S)"

# source inside dotfiles : target relative to $HOME (one list for install, --plan and doctor)
LINKS=(
    "hypr:.config/hypr"
    "waybar:.config/waybar"
    "rofi:.config/rofi"
    "mako:.config/mako"
    "starship/starship.toml:.config/starship.toml"
    "fastfetch:.config/fastfetch"
    "btop/themes/fresco.theme:.config/btop/themes/fresco.theme"
    "kitty/fresco.conf:.config/kitty/fresco.conf"
    "ghostty/themes/fresco:.config/ghostty/themes/fresco"
)

MODE=install
case "${1:-}" in
    ""|install) ;;
    --plan|plan) MODE=plan ;;
    doctor|--doctor) MODE=doctor ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "usage: $0 [--plan | doctor]" >&2; exit 2 ;;
esac

# ── doctor: read only ────────────────────────────────────────────────────
if [ "$MODE" = doctor ]; then
    FAIL=0
    ok()       { printf '  OK        %s\n' "$*"; }
    bad()      { printf '  %-9s %s\n' "$1" "$2"; FAIL=1; }
    optional() { printf '  %-9s %s\n' "$1" "$2"; }

    echo "== packages (dotfiles/packages.txt) =="
    while read -r pkg; do
        pkg="${pkg%%#*}"; pkg="${pkg//[[:space:]]/}"
        [ -n "$pkg" ] || continue
        if [ "$(dpkg-query -W -f='${db:Status-Status}' "$pkg" 2>/dev/null)" = installed ]; then
            ok "$pkg"
        else
            bad MISSING "$pkg"
        fi
    done < "$DOT/packages.txt"

    echo "== links in ~ =="
    for entry in "${LINKS[@]}"; do
        src="$DOT/${entry%%:*}" dst="$HOME/${entry#*:}"
        if [ -L "$dst" ] && [ "$(readlink -f "$dst")" = "$(readlink -f "$src")" ]; then
            ok "~/${entry#*:} -> $(readlink -f "$dst")"
        elif [ -L "$dst" ]; then
            bad WRONG "~/${entry#*:} points to $(readlink "$dst"), not into this repo"
        elif [ -e "$dst" ]; then
            bad NOT-LINK "~/${entry#*:} is a real file or folder, not a link into this repo"
        else
            bad MISSING "~/${entry#*:}"
        fi
    done

    echo "== the wallpaper's Python (uv env) =="
    PY="$REPO/.venv/bin/python"
    if [ -x "$PY" ] && v="$("$PY" -B -c 'import sys, pyte, PIL; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null)"; then
        ok ".venv: Python $v with pyte and Pillow"
    else
        bad MISSING ".venv with pyte and Pillow (run: uv sync --managed-python --python 3.12)"
    fi

    echo "== kitty and Hyprland =="
    if command -v kitty >/dev/null 2>&1; then ok "$(kitty --version 2>/dev/null | head -1)"; else bad MISSING kitty; fi
    if command -v Hyprland >/dev/null 2>&1; then
        ok "$(Hyprland --version 2>/dev/null | head -1 | cut -c1-60)"
    else
        bad MISSING Hyprland
    fi

    echo "== \$sotto in hyprland.conf =="
    CONF="$HOME/.config/hypr/hyprland.conf"
    [ -f "$CONF" ] || CONF="$DOT/hypr/hyprland.conf"
    raw="$(sed -n 's/^[[:space:]]*\$sotto[[:space:]]*=[[:space:]]*\([^#[:space:]]*\).*/\1/p' "$CONF" | head -1)"
    want="${raw/#\~/$HOME}"; want="${want//\$HOME/$HOME}"
    if [ -z "$raw" ]; then
        bad MISSING "\$sotto = ... in $CONF"
    elif [ -d "$want" ] && [ "$(cd "$want" && pwd -P)" = "$REPO" ]; then
        ok "\$sotto = $raw (this repo)"
    else
        bad WRONG "\$sotto = $raw, but this repo is $REPO"
    fi

    echo "== optional =="
    if command -v claude >/dev/null 2>&1; then
        optional OK "Claude Code: $(readlink -f "$(command -v claude)") (AI answers in the prompt)"
    else
        optional ABSENT "Claude Code (the prompt still works, without AI answers)"
    fi
    if [ -x /usr/local/lib/sotto-greeter/run-greeter ]; then
        if [ "$(readlink -f /etc/systemd/system/display-manager.service 2>/dev/null)" = /usr/lib/systemd/system/greetd.service ]; then
            optional OK "login screen: installed and enabled (greetd)"
        else
            optional OK "login screen: installed, not enabled (bin/sotto-greeter enable)"
        fi
    else
        optional ABSENT "login screen (bin/sotto-greeter install)"
    fi
    if [ -f /usr/share/plymouth/themes/sotto/sotto.plymouth ]; then
        if [ "$(readlink -f /usr/share/plymouth/themes/default.plymouth 2>/dev/null)" = /usr/share/plymouth/themes/sotto/sotto.plymouth ]; then
            optional OK "boot screen: installed and active (Plymouth)"
        else
            optional OK "boot screen: installed, not the default (bin/sotto-bootsplash)"
        fi
    else
        optional ABSENT "boot screen (bin/sotto-bootsplash)"
    fi

    echo
    if [ "$FAIL" = 0 ]; then echo "All required checks pass."; else echo "Some required checks failed (see above)."; fi
    exit "$FAIL"
fi

# ── install, or --plan: the same steps, printed instead of done ──────────
PLAN=0
[ "$MODE" = plan ] && PLAN=1
[ "$PLAN" = 1 ] && echo "PLAN ONLY: nothing below is done. Backups would go to $BAK"

backup() {   # $1 = path relative to $HOME; copies it before an in-place edit
    if [ "$PLAN" = 1 ]; then echo "  would back up ~/$1 to $BAK/$1"; return; fi
    mkdir -p "$BAK/$(dirname "$1")"
    cp -a "$HOME/$1" "$BAK/$1"
    echo "  backed up ~/$1"
}

link() {     # $1 = source inside dotfiles, $2 = target relative to $HOME
    local src="$DOT/$1" dst="$HOME/$2"
    if [ -L "$dst" ] && [ "$(readlink -f "$dst")" = "$(readlink -f "$src")" ]; then
        [ "$PLAN" = 1 ] && echo "  already linked ~/$2"
        return 0
    fi
    if [ "$PLAN" = 1 ]; then
        if [ -e "$dst" ] || [ -L "$dst" ]; then echo "  would move the old ~/$2 to $BAK/$2"; fi
        echo "  would link ~/$2 -> $src"
        return
    fi
    if [ -e "$dst" ] || [ -L "$dst" ]; then
        mkdir -p "$BAK/$(dirname "$2")"
        mv "$dst" "$BAK/$2"
        echo "  moved the old ~/$2 into the backup"
    fi
    mkdir -p "$(dirname "$dst")"
    ln -s "$src" "$dst"
    echo "  linked ~/$2"
}

echo "== linking configs =="
for entry in "${LINKS[@]}"; do
    link "${entry%%:*}" "${entry#*:}"
done
if [ "$PLAN" = 1 ]; then
    n="$(find "$DOT"/hypr/scripts -maxdepth 1 \( -name '*.sh' -o -name '*.py' \) ! -perm -u+x | wc -l)"
    echo "  would chmod +x hypr/scripts/*.sh and *.py ($n not executable yet)"
else
    chmod +x "$DOT"/hypr/scripts/*.sh "$DOT"/hypr/scripts/*.py
fi

# Terminal colour themes are left as they are: kitty keeps its Gruvbox theme.conf.

echo "== btop theme =="
BCONF="$HOME/.config/btop/btop.conf"
if [ -f "$BCONF" ]; then
    if ! grep -q '^color_theme = "fresco"' "$BCONF"; then
        backup .config/btop/btop.conf
        if [ "$PLAN" = 1 ]; then echo "  would set color_theme = \"fresco\" in ~/.config/btop/btop.conf"
        else sed -i 's|^color_theme = .*|color_theme = "fresco"|' "$BCONF"; fi
    elif [ "$PLAN" = 1 ]; then
        echo "  already set"
    fi
elif [ "$PLAN" = 1 ]; then
    echo "  would write ~/.config/btop/btop.conf with color_theme = \"fresco\""
else
    echo 'color_theme = "fresco"' > "$BCONF"
fi

echo "== zsh: prompt and tools (each line only runs if that tool is installed) =="
if ! grep -qs '>>> sotto >>>' "$HOME/.zshrc"; then
    [ -f "$HOME/.zshrc" ] && backup .zshrc
    if [ "$PLAN" = 1 ]; then
        echo "  would add the zoxide line to ~/.zshrc"
    else
        cat >> "$HOME/.zshrc" <<'EOF'

# >>> sotto >>>
command -v zoxide >/dev/null 2>&1 && eval "$(zoxide init zsh)"
# <<< sotto <<<
EOF
        echo "  added zoxide to ~/.zshrc"
    fi
elif [ "$PLAN" = 1 ]; then
    echo "  already in ~/.zshrc"
fi

echo "== Zen browser theme (if Zen is installed) =="
if [ "$PLAN" = 1 ]; then "$DOT/zen/apply.sh" --plan; else "$DOT/zen/apply.sh"; fi

echo "== cursor theme (GTK apps read it from here, in both Hyprland and GNOME) =="
if [ "$PLAN" = 1 ]; then
    echo "  would set org.gnome.desktop.interface cursor-theme to 'Bibata-Modern-Amber'" \
         "(now: $(gsettings get org.gnome.desktop.interface cursor-theme 2>/dev/null || echo unknown))"
else
    gsettings set org.gnome.desktop.interface cursor-theme 'Bibata-Modern-Amber' 2>/dev/null || true
fi

if command -v Hyprland >/dev/null 2>&1; then
    echo "== checking the Hyprland config =="
    if [ "$PLAN" = 1 ]; then
        echo "  would run: Hyprland --verify-config -c ~/.config/hypr/hyprland.conf"
    else
        Hyprland --verify-config -c "$HOME/.config/hypr/hyprland.conf" || true
    fi
fi

echo
if [ "$PLAN" = 1 ]; then
    echo "Plan only: nothing was changed. Run without --plan to do it."
    exit 0
fi
[ -d "$BAK" ] && echo "Backups of everything replaced: $BAK"
echo "Done. Log out, click the gear icon on the login screen, and pick Hyprland."
echo "To go back, log out and pick Ubuntu."
