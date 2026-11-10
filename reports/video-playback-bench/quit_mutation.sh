#!/bin/sh
# Mutation check for the quit test: run the media-quit child with
# mediaengine.shutdown_all() disabled and expect at least one crash.
# Not a suite test: it produces deliberate core dumps.
#   quit_mutation.sh [RUNS]   (run under headless mutter, 1280x1024, from anywhere)
RUNS=${1:-10}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
export BABBLE_SRCDIR="$ROOT/src" GSETTINGS_BACKEND=memory \
  GSETTINGS_SCHEMA_DIR="$ROOT/build/data" GSK_RENDERER=cairo \
  LANG=C LC_ALL=C GTK_A11Y=none NO_AT_BRIDGE=1 GDK_DEBUG=no-portals
TMP=$(mktemp -d)
cat > "$TMP/mutant.py" <<'PY'
import os, sys
sys.path.insert(0, os.environ['BABBLE_SRCDIR'] + '/..')
sys.path.insert(0, os.environ['BABBLE_SRCDIR'])
from src import mediaengine
mediaengine.shutdown_all = lambda *a, **k: None   # the mutation
sys.argv = [os.environ['BABBLE_SRCDIR'] + '/test-media-quit.py', '--child']
exec(compile(open(sys.argv[0]).read(), sys.argv[0], 'exec'),
     {'__name__': '__main__', '__file__': sys.argv[0]})
PY
crashes=0
for i in $(seq "$RUNS"); do
  python3 "$TMP/mutant.py" >"$TMP/out.$i" 2>&1
  code=$?
  echo "run $i: exit $code"
  [ "$code" -ne 0 ] && crashes=$((crashes + 1))
done
echo "crashes=$crashes/$RUNS (expect >= 1 with shutdown_all disabled)"
rm -rf "$TMP"
[ "$crashes" -ge 1 ]
