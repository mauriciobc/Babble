#!/bin/sh
export BABBLE_SRCDIR=$PWD/src GSETTINGS_SCHEMA_DIR=$PWD/build/data GSETTINGS_BACKEND=memory GSK_RENDERER=cairo LANG=C LC_ALL=C GTK_A11Y=none G_DEBUG=fatal-criticals
for i in 1 2 3 4 5; do
  s=$(date +%s.%N)
  timeout 300 python3 src/test-media-progressive.py > reports/video-playback-bench/qa1b/prog_$i.out 2>&1
  rc=$?
  e=$(date +%s.%N)
  echo "progressive run $i rc=$rc dur=$(echo "$e - $s" | bc)s fails=$(grep -c '^FAIL' reports/video-playback-bench/qa1b/prog_$i.out)"
done
