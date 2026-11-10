#!/bin/sh
cd /home/mauriciobc/Documentos/Code/Babble
XDG_RUNTIME_DIR=$(mktemp -d) dbus-run-session -- mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- sh reports/video-playback-bench/qa1b/gl_loop.sh > reports/video-playback-bench/qa1b/gl_run.log 2>&1
