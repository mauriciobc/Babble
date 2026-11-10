#!/bin/sh
# Final acceptance run on the final tree (main session). From the repo root.
# 1) full suite  2) crash gate 5 x 50 real-viewer cycles  3) 20 quits while playing
Q=reports/video-playback-bench/qa
date '+%Y-%m-%d %H:%M:%S' > $Q/final_start.txt
XDG_RUNTIME_DIR=$(mktemp -d) dbus-run-session -- mutter --headless --wayland --no-x11 \
  --virtual-monitor 1280x1024 -- meson test -C build -j1 --print-errorlogs > $Q/final_suite.log 2>&1
echo "suite exit=$?" >> $Q/final_summary.txt
grep -E '^(Ok|Fail|Skipped|Timeout):' $Q/final_suite.log >> $Q/final_summary.txt

# crash gate: e3b_run.sh does 10 runs; run the viewer harness 5 x 50 directly
B=reports/video-playback-bench
python3 $B/server.py 18767 "$MEDIA" 50000000 & SRV=$!
sleep 1
for r in 1 2 3 4 5; do
  GTK_A11Y=none GDK_DEBUG=no-portals LANG=C LC_ALL=C BABBLE_SRCDIR=$PWD/src \
  GSETTINGS_SCHEMA_DIR=$PWD/build/data G_DEBUG=fatal-criticals \
  timeout 400 dbus-run-session -- mutter --headless --wayland --no-x11 \
    --virtual-monitor 1280x1024 -- python3 $B/e3b_viewer.py http://127.0.0.1:18767/ 50 \
    > $Q/final_e3b_run.log 2>&1
  echo "crash run $r mutter_exit=$? $(grep '^SUMMARY' $Q/final_e3b_run.log) last=[$(grep '^cycle' $Q/final_e3b_run.log | tail -1)]" >> $Q/final_summary.txt
done
kill $SRV

# quit gate, 20 runs inside one session
dbus-run-session -- mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- \
  sh -c 'export GSETTINGS_BACKEND=memory GTK_A11Y=none GDK_DEBUG=no-portals LANG=C LC_ALL=C GSK_RENDERER=cairo BABBLE_SRCDIR=$PWD/src GSETTINGS_SCHEMA_DIR=$PWD/build/data G_DEBUG=fatal-criticals
         for i in $(seq 1 20); do timeout 60 python3 src/test-media-quit.py --child > reports/video-playback-bench/qa/final_quit_child.out 2>&1; echo "quit $i exit=$? $(grep "^QUIT" reports/video-playback-bench/qa/final_quit_child.out)"; done' \
  > $Q/final_quit.log 2>/dev/null
echo "quit ok=$(grep -c 'exit=0 QUIT playing=True' $Q/final_quit.log)/20" >> $Q/final_summary.txt
echo "python cores since start: $(coredumpctl list --since "$(cat $Q/final_start.txt)" --no-legend 2>/dev/null | grep -c python)" >> $Q/final_summary.txt
echo DONE >> $Q/final_summary.txt
