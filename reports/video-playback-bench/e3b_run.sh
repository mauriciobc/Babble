#!/bin/sh
# E3b: 10 x 50 real-viewer cycles (alternating video / GIFV), loopback HTTP.
# Run from the repository root. Results: e3b_results.txt. Exit 139 = SIGSEGV.
B=reports/video-playback-bench
OUT=$B/e3b_results.txt
: > "$OUT"
python3 "$B/server.py" 18767 "$MEDIA" 50000000 & SRV=$!
sleep 1
export GTK_A11Y=none GDK_DEBUG=no-portals LANG=C LC_ALL=C \
  BABBLE_SRCDIR=$PWD/src GSETTINGS_SCHEMA_DIR=$PWD/build/data G_DEBUG=fatal-criticals
for r in $(seq 1 10); do
  t0=$(date '+%Y-%m-%d %H:%M:%S')
  timeout 400 dbus-run-session -- mutter --headless --wayland --no-x11 \
    --virtual-monitor 1280x1024 -- python3 "$B/e3b_viewer.py" http://127.0.0.1:18767/ 50 \
    > "$B/e3b-run.log" 2>&1
  rc=$?
  last=$(grep '^cycle' "$B/e3b-run.log" | tail -1)
  s=$(grep '^SUMMARY' "$B/e3b-run.log")
  crit=$(grep -c 'CRITICAL' "$B/e3b-run.log")
  cores=$(coredumpctl list --since "$t0" --no-legend 2>/dev/null | grep -c python)
  echo "run $r mutter_exit=$rc last=[$last] ${s:-NO-SUMMARY} criticals=$crit cores=$cores" >> "$OUT"
done
kill $SRV
echo DONE >> "$OUT"
