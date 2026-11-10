#!/bin/sh
# run_live.sh OUT RUNS CANDIDATES... -- single runs against BENCH_URL, no cycles.
D=$(dirname "$0")
OUT=$1; RUNS=$2; shift 2
PY=$D/../venv/bin/python
for c in "$@"; do
  i=1
  while [ $i -le "$RUNS" ]; do
    r=$(timeout 150 "$PY" "$D/bench.py" "$c" single 0 2>"$D/err-live-$c-$i.log" | tail -1)
    [ -z "$r" ] && r="{\"candidate\":\"$c\",\"mode\":\"live\",\"crashed\":true}"
    echo "$r" >> "$OUT"
    sleep 2
    i=$((i+1))
  done
done
