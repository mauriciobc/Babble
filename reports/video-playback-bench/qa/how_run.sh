#!/bin/sh
cd /home/mauriciobc/Documentos/Code/Babble
export BABBLE_SRCDIR=$PWD/src GSETTINGS_SCHEMA_DIR=$PWD/build/data GSETTINGS_BACKEND=memory GSK_RENDERER=cairo LANG=C LC_ALL=C GTK_A11Y=none G_DEBUG=fatal-criticals
for t in test-media-engine test-mediaviewer-teardown; do
  echo "=== $t"; timeout 400 python3 reports/video-playback-bench/qa/how_wrap.py src/$t.py; echo "rc=$?"
done
GSK_RENDERER=gl timeout 300 python3 src/test-media-stream.py; echo "gl test-media-stream rc=$?"
