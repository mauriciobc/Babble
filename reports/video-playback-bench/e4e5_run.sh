#!/bin/sh
# E4 (forwarding cost) and E5 (idle memory). Run inside headless mutter.
# Results: e4_results.txt, e5_results.txt.
D=$(cd "$(dirname "$0")" && pwd)
: > "$D/e4_results.txt"; : > "$D/e5_results.txt"
for clip in v720p30.mp4 v1080p60.mp4; do
  for r in 1 2 3 4 5; do
    for m in wrapper direct; do
      out=$(timeout 60 python3 "$D/e4_forward.py" $m "$MEDIA/$clip" 10 2>/dev/null | grep '^E4')
      echo "run $r ${out:-E4 FAILED mode=$m file=$clip exit=$?}" >> "$D/e4_results.txt"
    done
  done
done
echo DONE >> "$D/e4_results.txt"
for r in 1 2 3; do
  for m in engine mediafile; do
    out=$(timeout 240 python3 "$D/e5_idle.py" $m "$MEDIA/gifv.mp4" 2>/dev/null | grep '^E5')
    echo "run $r ${out:-E5 FAILED mode=$m exit=$?}" >> "$D/e5_results.txt"
  done
done
echo DONE >> "$D/e5_results.txt"
