#!/bin/sh
# 50 child quits under one headless mutter session. Run from repo root.
export GSETTINGS_BACKEND=memory GTK_A11Y=none GDK_DEBUG=no-portals LANG=C LC_ALL=C \
  GSK_RENDERER=cairo BABBLE_SRCDIR=$PWD/src GSETTINGS_SCHEMA_DIR=$PWD/build/data G_DEBUG=fatal-criticals
for i in $(seq 1 50); do
  timeout 60 python3 src/test-media-quit.py --child > reports/video-playback-bench/qa/quit_child.out 2>&1
  rc=$?
  echo "quit $i exit=$rc $(grep '^QUIT' reports/video-playback-bench/qa/quit_child.out)"
done
