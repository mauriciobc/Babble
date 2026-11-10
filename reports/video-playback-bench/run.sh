#!/bin/sh
# run.sh OUT RUNS CANDIDATES... -- executed inside headless mutter.
D=$(dirname "$0")
OUT=$1; RUNS=$2; shift 2
PY=$D/../venv/bin/python
PORT=18765
python3 "$D/server.py" $PORT "$D/media" 1048576 &
SRV=$!
sleep 1
for c in "$@"; do
  i=1
  while [ $i -le "$RUNS" ]; do
    r=$(timeout 150 "$PY" "$D/bench.py" "$c" single $PORT 2>"$D/err-$c-$i.log" | tail -1)
    code=$?
    [ -z "$r" ] && r="{\"candidate\":\"$c\",\"mode\":\"single\",\"crashed\":true}"
    echo "$r" >> "$OUT"
    i=$((i+1))
  done
  r=$(timeout 200 "$PY" "$D/bench.py" "$c" cycles $PORT 2>"$D/err-$c-cycles.log" | tail -1)
  [ -z "$r" ] && r="{\"candidate\":\"$c\",\"mode\":\"cycles\",\"crashed\":true}"
  echo "$r" >> "$OUT"
done
kill $SRV
