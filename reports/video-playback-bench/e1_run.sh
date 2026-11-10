#!/bin/sh
# E1 full run. Alternates control and deferred runs of 100 cycles each,
# then 100 deferred cycles against the live post inside org.gnome.Platform//51.
# Results: e1_results.txt (one SUMMARY or CRASH line per run).
D=$(cd "$(dirname "$0")" && pwd)
MEDIA=${MEDIA:-$D/media}
OUT=$D/e1_results.txt
LIVE="https://mastodon.me.uk/system/media_attachments/files/117/581/332/107/689/033/original/13af5d8053337ede.mp4"
export GTK_A11Y=none GDK_DEBUG=no-portals G_DEBUG=fatal-criticals
: > "$OUT"

run() {  # label, command...
  label=$1; shift
  log=$D/e1-$label.log
  t0=$(date '+%Y-%m-%d %H:%M:%S')
  timeout 900 dbus-run-session -- mutter --headless --wayland --no-x11 \
    --virtual-monitor 1280x1024 -- "$@" > "$log" 2>&1
  s=$(grep '^SUMMARY' "$log")
  last=$(grep '^cycle' "$log" | tail -1)
  cores=$(coredumpctl list --since "$t0" --no-legend 2>/dev/null | grep -c python)
  if [ -n "$s" ]; then
    echo "$label $s cores=$cores" >> "$OUT"
  else
    echo "$label CRASH-OR-HANG last=[$last] cores=$cores" >> "$OUT"
  fi
}

for n in 1 2 3 4 5; do
  run "immediate-$n" python3 "$D/e1_release.py" "file://$MEDIA/gifv.mp4" 100 immediate
  run "deferred-$n" python3 "$D/e1_release.py" "file://$MEDIA/gifv.mp4" 100 deferred
done
run "sandbox-deferred-live" sh -c "flatpak run --user --share=network --socket=wayland \
  --device=dri --env=GTK_A11Y=none --env=G_DEBUG=fatal-criticals \
  --command=python3 org.gnome.Platform//51 - '$LIVE' 100 deferred 6 < '$D/e1_release.py'"
echo DONE >> "$OUT"
