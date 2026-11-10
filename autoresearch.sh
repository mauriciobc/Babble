#!/usr/bin/env bash
# Timeline performance/memory benchmark: see bench/timeline_bench.py.
# Runs the end-to-end workload BENCH_RUNS times (default 5; single runs
# spread about ±14%) in fresh processes under a private Xvfb with the
# cairo renderer and in-memory GSettings, and reports the median of each
# figure: process CPU (cpu_ms), main-thread dispatch CPU and stalls
# (main_ms, stall_max_ms, stall_p99_ms, stalls_over_16ms), painted frames
# and frames showing an empty run shell, phases, and memory.
# BENCH_JSON=<path> also writes {"median": {...}, "runs": [{...}, ...]} there
# for bench/compare.py; the printed output is the same with or without it.
set -euo pipefail
cd "$(dirname "$0")"

RUNS=${BENCH_RUNS:-5}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cp data/io.github.mauriciobc.Babble.gschema.xml "$work/"
glib-compile-schemas "$work"

for i in $(seq "$RUNS"); do
    env -u WAYLAND_DISPLAY -u DISPLAY \
        GDK_BACKEND=x11 GSK_RENDERER=cairo GSETTINGS_BACKEND=memory \
        GSETTINGS_SCHEMA_DIR="$work" NO_AT_BRIDGE=1 GTK_A11Y=none \
        PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 \
        xvfb-run -a -s "-screen 0 1280x1024x24" \
        python3 bench/timeline_bench.py >"$work/run$i.json" 2>"$work/run$i.err" || {
            cat "$work/run$i.err" >&2; exit 1; }
    grep -q '^{' "$work/run$i.json" || { cat "$work/run$i.err" >&2; exit 1; }
done

python3 - "$work" "$RUNS" "${BENCH_JSON:-}" <<'EOF'
import json, statistics, sys
work, runs, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
results = [json.loads(open('%s/run%d.json' % (work, i)).read().strip().splitlines()[-1])
           for i in range(1, runs + 1)]
print('runs cpu_ms: %s' % [r['cpu_ms'] for r in results], file=sys.stderr)
for key in results[0]:
    print('METRIC %s=%s' % (key, statistics.median(r[key] for r in results)))
if out:
    with open(out, 'w') as f:
        json.dump({'median': {k: statistics.median(r[k] for r in results)
                              for k in results[0]},
                   'runs': results}, f, indent=1)
EOF
