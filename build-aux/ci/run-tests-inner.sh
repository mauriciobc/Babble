#!/usr/bin/env bash
# Invoked inside babble-ci:local; do not run on the host.
set -euo pipefail

cd /src

export GDK_DEBUG=no-portals
export NO_AT_BRIDGE=1
export GTK_A11Y=none

if [ ! -d build/meson-private ]; then
    meson setup build
else
    meson setup --reconfigure build
fi
meson compile -C build

if (($# > 0)); then
    meson_args=(meson test -C build -j1 --no-rebuild --print-errorlogs "$@")
else
    meson_args=(meson test -C build -j1 --no-rebuild --print-errorlogs)
fi

# The log goes to /tmp, not the repo: the container runs as root, so a
# log written next to the sources comes back root-owned and the next tee
# fails on it.
LOG=/tmp/babble-ci.log
xvfb-run -a dbus-run-session -- "${meson_args[@]}" 2>&1 | tee "$LOG"

skipped=$(sed -nE 's/^Skipped:[[:space:]]+([0-9]+).*/\1/p' "$LOG" | tail -1)
skipped=${skipped:-0}
unexpected=$(grep -E 'SKIP' "$LOG" \
    | grep -v 'streaming helpers' | grep -v 'no network' || true)
if [ "$skipped" -ne 0 ] && [ -n "$unexpected" ]; then
    echo 'unexpected skips:' >&2
    echo "$unexpected" >&2
    exit 1
fi
