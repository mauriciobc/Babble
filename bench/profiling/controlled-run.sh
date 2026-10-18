#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# One controlled recording of Babble.
#
#   bench/profiling/controlled-run.sh RUN_NUMBER [--manual] [-- UIDRIVER_ARGS...]
#
# By default the UI is driven by bench/profiling/uidriver.py, so every run
# performs exactly the same steps (see its docstring); --manual leaves the
# window to you. Arguments after -- go to uidriver.py (e.g. --flicks 20).
#
# sysprof records GTK's and GLib's marks and counters only (--no-perf); perf,
# running inside it, records the CPU stacks with Python function names. sysprof
# 51.0's own sampler can miss whole libraries' mappings for a spawned process
# (fixed after 51.0 in 611b21a), so its stacks are not used.
#
# Controls: no bench or other Babble running, the user-wide GTK stylesheet
# (~/.config/gtk-4.0/gtk.css) hidden, translucent sidebar off, performance
# power profile. Everything else in ~/.config is kept — in particular dconf,
# which holds the stored accounts, so no sign-in is needed. Settings are
# restored on exit. perf needs kernel.perf_event_paranoid <= 1 for call stacks.
set -u
run=${1:?usage: bench/profiling/controlled-run.sh RUN_NUMBER [--manual] [-- UIDRIVER_ARGS...]}
shift
manual=0
if [ "${1:-}" = "--manual" ]; then manual=1; shift; fi
if [ "${1:-}" = "--" ]; then shift; fi
cd "$(dirname "$0")/../.." || exit 1
schema="$PWD/build/data"
out="$HOME/babble-profiles/run$run"
mkdir -p "$(dirname "$out")"

if pgrep -f 'bench/v2|timeline_bench|/babble( |$)|build/src/babble|app/bin/babble' >/dev/null; then
    echo "Quit Babble (and stop any bench run) first; it is single-instance:" >&2
    pgrep -af 'bench/v2|timeline_bench|/babble( |$)|build/src/babble|app/bin/babble' >&2
    exit 1
fi
if [ "$(cat /proc/sys/kernel/perf_event_paranoid)" -gt 1 ]; then
    echo "perf cannot record call stacks at perf_event_paranoid > 1. Run:" >&2
    echo "  sudo sysctl kernel.perf_event_paranoid=1" >&2
    exit 1
fi

# A config directory that is the real one minus GTK's user stylesheet. An
# empty XDG_CONFIG_HOME would also hide dconf ($XDG_CONFIG_HOME/dconf/user),
# i.e. every stored account and setting: each run would start signed out.
real_config="${XDG_CONFIG_HOME:-$HOME/.config}"
config="${XDG_RUNTIME_DIR:-/tmp}/babble-profile-config"
rm -rf "$config"
mkdir -p "$config/gtk-4.0"
for entry in "$real_config"/* "$real_config"/.[!.]*; do
    [ -e "$entry" ] || continue
    [ "$(basename "$entry")" = "gtk-4.0" ] && continue
    ln -s "$entry" "$config/"
done
for entry in "$real_config"/gtk-4.0/*; do
    [ -e "$entry" ] || continue
    case "$(basename "$entry")" in gtk.css*) continue ;; esac
    ln -s "$entry" "$config/gtk-4.0/"
done

prev_profile=$(powerprofilesctl get)
prev_sidebar=$(GSETTINGS_SCHEMA_DIR="$schema" gsettings get io.github.mauriciobc.Babble translucent-sidebar)
restore() {
    powerprofilesctl set "$prev_profile"
    GSETTINGS_SCHEMA_DIR="$schema" gsettings set io.github.mauriciobc.Babble translucent-sidebar "$prev_sidebar"
    rm -rf "$config"
    echo "Restored power profile ($prev_profile) and translucent-sidebar ($prev_sidebar)."
}
trap restore EXIT
# A handler (not an ignore) so Ctrl+C reaches the recorders and the script
# still runs restore afterwards.
trap 'true' INT

powerprofilesctl set performance
GSETTINGS_SCHEMA_DIR="$schema" gsettings set io.github.mauriciobc.Babble translucent-sidebar false

if [ "$manual" = 1 ]; then
    app=(python3 -X perf build/src/babble)
    echo "Run $run (manual): do the steps, then quit Babble."
else
    app=(python3 -X perf bench/profiling/uidriver.py --log "$out.jsonl" "$@")
    echo "Run $run (scripted): do not touch the Babble window until it quits."
fi
echo "-> $out.syscap (marks), $out.perf.data (stacks)"
sysprof-cli --force --no-perf --no-sysprofd --no-debuginfod "$out.syscap" -- \
    perf record -e cycles:u -F 999 -g -k CLOCK_MONOTONIC -o "$out.perf.data" -- \
    env XDG_CONFIG_HOME="$config" GSETTINGS_SCHEMA_DIR="$schema" "${app[@]}"
echo "Saved $out.syscap ($(du -h "$out.syscap" | cut -f1)) and $out.perf.data ($(du -h "$out.perf.data" | cut -f1))"
if [ "$manual" = 0 ]; then
    # sysprof-cli exits 0 whatever its child did; the driver's log says.
    if grep -q '"event": "scenario-complete"' "$out.jsonl"; then
        echo "Scenario completed: $out.jsonl"
    else
        echo "Scenario did NOT complete; see $out.jsonl" >&2
        exit 3
    fi
fi
