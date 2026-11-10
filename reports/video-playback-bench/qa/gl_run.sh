#!/bin/sh
cd /home/mauriciobc/Documentos/Code/Babble
export BABBLE_SRCDIR=$PWD/src GSETTINGS_SCHEMA_DIR=$PWD/build/data GSETTINGS_BACKEND=memory GSK_RENDERER=gl LANG=C LC_ALL=C GTK_A11Y=none G_DEBUG=fatal-criticals
for t in test-media-stream test-mediaviewer-teardown; do
  echo "=== $t (GSK_RENDERER=gl)"; timeout 300 python3 src/$t.py; echo "rc=$?"
done
