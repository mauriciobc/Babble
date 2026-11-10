#!/bin/sh
# Quit-while-playing check (50 naive + 50 mitigated, interleaved) and E3
# (42 x 12 Gtk.MediaFile cycles on a local file). Run inside headless mutter.
# Results: quit_results.txt, e3_results.txt. Exit 139 = SIGSEGV.
D=$(cd "$(dirname "$0")" && pwd)
: > "$D/quit_results.txt"; : > "$D/e3_results.txt"
for i in $(seq 1 50); do
  for m in naive mitigated; do
    timeout 30 python3 "$D/quit_check.py" $m "file://$MEDIA/video.mp4" > "$D/quit-run.log" 2>/dev/null
    rc=$?
    line=$(grep '^QUIT' "$D/quit-run.log")
    echo "$i $m exit=$rc ${line:-NO-QUIT-LINE}" >> "$D/quit_results.txt"
  done
done
echo DONE >> "$D/quit_results.txt"
for r in $(seq 1 42); do
  timeout 120 python3 "$D/e3_mediafile.py" "$MEDIA/gifv.mp4" 12 > "$D/e3-run.log" 2>/dev/null
  rc=$?
  last=$(grep '^cycle' "$D/e3-run.log" | tail -1)
  s=$(grep '^SUMMARY' "$D/e3-run.log")
  echo "run $r exit=$rc last=[$last] ${s}" >> "$D/e3_results.txt"
done
echo DONE >> "$D/e3_results.txt"
