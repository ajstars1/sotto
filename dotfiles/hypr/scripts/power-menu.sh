#!/usr/bin/env bash
# SUPER+SHIFT+E, or click OFF in the bar. The menu itself is power-menu.py
# (a drop-down like the Control Center; restart and shut down ask first).
exec "$(dirname "$0")/power-menu.py" "$@"
