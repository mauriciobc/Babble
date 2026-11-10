#!/bin/sh
cd /home/mauriciobc/Documentos/Code/Babble
XDG_RUNTIME_DIR=$(mktemp -d) dbus-run-session -- mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- sh -c 'sh reports/video-playback-bench/qa1b/sandbox_run.sh a; sh reports/video-playback-bench/qa1b/sandbox_run.sh b' > reports/video-playback-bench/qa1b/sbx_all.log 2>&1
