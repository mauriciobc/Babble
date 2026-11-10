#!/bin/sh
cd /home/mauriciobc/Documentos/Code/Babble
date '+%F %T' > reports/video-playback-bench/qa1b/suite_start.txt
XDG_RUNTIME_DIR=$(mktemp -d) dbus-run-session -- mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- meson test -C build -j1 --print-errorlogs > reports/video-playback-bench/qa1b/suite.log 2>&1
echo "rc=$?" >> reports/video-playback-bench/qa1b/suite.log
