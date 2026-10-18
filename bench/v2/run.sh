#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# v2 timeline benchmark runner. See bench/v2/README.md.
#
# Runs one scenario BENCH_RUNS times (default 5) in fresh processes under a
# private Xvfb with in-memory GSettings, and reports the median of every
# figure as METRIC key=value lines -- the same shape autoresearch.sh emits,
# so v1 and v2 output can be diffed directly.
#
#   ./run.sh                        # scroll scenario, 400 posts
#   BENCH_SCENARIO=concurrent ./run.sh
#   BENCH_PROFILE=1 ./run.sh        # one run under sysprof, writes .syscap
#   BENCH_GATE=1 ./run.sh           # compare against baseline.json, fail on drift
#
# Environment (all optional):
#   BENCH_RUNS=5            repetitions; single runs spread about +-14%
#   BENCH_SCENARIO=scroll   firstload | scroll | concurrent | soak
#   BENCH_TOTAL=400         posts in the fixture timeline
#   BENCH_SCENARIO_*  see timeline_bench2.py's module docstring
#   BENCH_AVATAR_PX=400     fixture avatar edge, px (default 400, real)
#   BENCH_MEDIA_PX=1600x1200 fixture media size (default 1600x1200, real)
#   BENCH_LATENCY_MS=0      delay every fixture response
#   BENCH_RENDERER=cairo    GSK renderer; see the note on renderers below
#   BENCH_PROFILE=<path>    write a sysprof capture there
#   BENCH_SYSPROF=auto|cli|fd  how to capture (default auto: cli if installed,
#                           else fd, which records GTK frame marks only)
#   BENCH_GATE=1            fail if a metric leaves baseline.json's tolerance
#   BENCH_RECORD=1          re-record baseline.json from this run
set -euo pipefail
cd "$(dirname "$0")"

V2=$PWD
ROOT=$(cd "$V2/../.." && pwd)
cd "$ROOT"

RUNS=${BENCH_RUNS:-5}
SCENARIO=${BENCH_SCENARIO:-scroll}
RENDERER=${BENCH_RENDERER:-cairo}
PROFILE=${BENCH_PROFILE:-}
GATE=${BENCH_GATE:-0}

if [ -n "$PROFILE" ] && [ "$RUNS" -ne 1 ]; then
    BENCH_RUNS=1
    RUNS=1
    echo "# profiling runs once: a capture is per-process and not comparable" >&2
fi

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

cp data/io.github.mauriciobc.Babble.gschema.xml "$work/"
glib-compile-schemas "$work"

# The fixture's image sizes decide which decode path the run exercises, so
# they are passed through explicitly rather than left to defaults that could
# drift between the two harnesses. Unset means the realistic default.

# --- profiling -------------------------------------------------------
#
# There are two different things here and they are not interchangeable.
#
# 1. **sysprof-cli** gives a full capture: CPU stack samples *and* GTK's
#    frame-clock marks. Call graphs, flame graphs, allocation sites -- the
#    "which code is hot" questions.
# 2. **SYSPROF_TRACE_FD alone** gives GTK's own frame-clock marks and
#    nothing else. No CPU samples, therefore no call graphs.
#
# An earlier version of this file defaulted to (2) and the README pitched
# sysprof as the tool for attribution. That overclaimed: without sysprof
# running, there is no sampler, so there are no stacks to attribute. So
# the default is now sysprof-cli when it exists, and the fallback says
# plainly what it captured and what it did not.
#
# GSK_RENDERER=sysprof is NOT how to capture anything: the renderers GTK
# 4.24 accepts are help, broadway, cairo, opengl/gl and vulkan (same
# page, GSK_RENDERER section).
#
# BENCH_SYSPROF=auto (default) | cli | fd
PROFILE_MODE=auto
if [ -n "$PROFILE" ]; then
    mkdir -p "$(dirname "$PROFILE")"
    case "${BENCH_SYSPROF:-auto}" in
        cli|auto)
            if command -v sysprof-cli >/dev/null 2>&1; then
                PROFILE_MODE=cli
            elif [ "${BENCH_SYSPROF:-auto}" = "cli" ]; then
                echo "BENCH_SYSPROF=cli but sysprof-cli is not installed." >&2
                echo "Install it (dnf install sysprof / apt install sysprof)," >&2
                echo "or use BENCH_SYSPROF=fd for GTK frame marks only." >&2
                exit 77   # Meson's "skipped", the convention the suite uses
            fi
            ;;
        fd) PROFILE_MODE=fd ;;
        *) echo "BENCH_SYSPROF must be auto, cli or fd" >&2; exit 2 ;;
    esac
    if [ "$PROFILE_MODE" = "fd" ]; then
        # fd 9 is not close-on-exec, so the Python process inherits it.
        # Verified separately: the child does see an open fd 9.
        exec 9>"$PROFILE"
        export SYSPROF_TRACE_FD=9
    fi
fi

for i in $(seq "$RUNS"); do
    env -u WAYLAND_DISPLAY -u DISPLAY \
        GDK_BACKEND=x11 GSK_RENDERER="$RENDERER" GSETTINGS_BACKEND=memory \
        GSETTINGS_SCHEMA_DIR="$work" NO_AT_BRIDGE=1 GTK_A11Y=none \
        PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 \
        BENCH_SCENARIO="$SCENARIO" \
        $([ "$PROFILE_MODE" = "cli" ] && echo sysprof-cli capture -f -o "$PROFILE" --) \
        xvfb-run -a -s "-screen 0 1280x1024x24" \
        python3 bench/v2/timeline_bench2.py \
        >"$work/run$i.json" 2>"$work/run$i.err" || {
            cat "$work/run$i.err" >&2; exit 1; }
    grep -q '^{' "$work/run$i.json" || { cat "$work/run$i.err" >&2; exit 1; }
done

PROFILED=0
if [ -n "$PROFILE" ]; then
    [ "$PROFILE_MODE" = "fd" ] && exec 9>&-
    PROFILED=1
    PROFILE_MODE_SAVED=$PROFILE_MODE
fi

python3 - "$work" "$RUNS" "$SCENARIO" <<'EOF'
import json, statistics, sys
work, runs, scenario = sys.argv[1], int(sys.argv[2]), sys.argv[3]

def load(i):
    with open('%s/run%d.json' % (work, i)) as handle:
        return json.loads(handle.read().strip().splitlines()[-1])

results = [load(i) for i in range(1, runs + 1)]
print('runs cpu_ms: %s' % [r['cpu_ms'] for r in results], file=sys.stderr)

# A scenario that does not exercise a phase reports null; medians must
# ignore those rather than crash or silently treat them as zero.
numeric = []
for r in results:
    for key, value in r.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric.append(key)
for key in dict.fromkeys(numeric):
    values = [r[key] for r in results
              if isinstance(r.get(key), (int, float))
              and not isinstance(r.get(key), bool)]
    if values:
        print('METRIC %s=%s' % (key, statistics.median(values)))
for key in ('scenario', 'posts', 'posts_loaded'):
    if key in results[0]:
        print('METRIC %s=%s' % (key, results[0][key]))
# Conditions are printed so the numbers can be read alongside the regime
# that produced them; gate.py compares them rather than trusting them.
if results[0].get('conditions'):
    print('METRIC conditions=%s'
          % json.dumps(results[0]['conditions'], sort_keys=True))
with open('%s/merged.json' % work, 'w') as handle:
    json.dump({r['scenario']: r for r in results}, handle)
EOF

# The gate compares against a committed baseline. Kept out of run.sh's
# happy path so the benchmark itself stays a measurement tool; gate.py
# owns the policy. BENCH_RECORD=1 re-records the baseline instead, which
# is how a deliberate change gets accepted.
if [ "${BENCH_RECORD:-0}" = "1" ]; then
    python3 bench/v2/gate.py --record "$work" "$SCENARIO"
elif [ "$GATE" = "1" ]; then
    python3 bench/v2/gate.py "$work" "$SCENARIO"
fi

# Reported after the metrics: a profiling run is still a real run, and
# whoever asked for the capture wants the numbers too.
if [ "$PROFILED" = "1" ]; then
    if [ -s "$PROFILE" ]; then
        size=$(wc -c <"$PROFILE")
        echo "# sysprof capture: $PROFILE ($size bytes, mode=$PROFILE_MODE_SAVED)" >&2
        if [ "$PROFILE_MODE_SAVED" = "fd" ]; then
            cat >&2 <<'EOF'
# This capture came from SYSPROF_TRACE_FD alone, so it holds GTK's
# frame-clock marks ONLY -- no CPU samples, hence no call graphs or flame
# graphs. For "which code is hot" you need the sampler:
#   dnf install sysprof   # or: apt install sysprof
#   BENCH_PROFILE=/tmp/capture.syscap ./bench/v2/run.sh   # then auto-selects cli
EOF
        fi
        echo "# inspect with: sysprof $PROFILE" >&2
    else
        cat >&2 <<EOF
The capture at $PROFILE is empty (mode=$PROFILE_MODE_SAVED).

The run itself succeeded -- its metrics are above; only the capture is
missing. GTK writes frame-clock marks only where it was built with
profiling support, and the Cairo renderer reports little. If mode was cli,
check that sysprof-cli could sample this process.

GSK_RENDERER=sysprof does not help: it is not a renderer in GTK 4.24.
EOF
    fi
fi