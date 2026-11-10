#!/bin/sh
export XDG_RUNTIME_DIR=$(mktemp -d)
dbus-run-session -- mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- reports/video-playback-bench/qa/quit_loop.sh
