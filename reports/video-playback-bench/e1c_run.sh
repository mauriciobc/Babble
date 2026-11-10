#!/bin/sh
# E1c volume: 25x12 deferred local, then 100 deferred live in the GNOME 51 sandbox.
D=$(cd "$(dirname "$0")" && pwd); OUT=$D/e1c_results.txt
LIVE="https://mastodon.me.uk/system/media_attachments/files/117/581/332/107/689/033/original/13af5d8053337ede.mp4"
export GTK_A11Y=none GDK_DEBUG=no-portals
: > "$OUT"
for n in $(seq 1 25); do
  t0=$(date '+%Y-%m-%d %H:%M:%S')
  s=$(timeout 150 dbus-run-session -- mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- \
      python3 "$D/e1c_reproducer.py" "file://$MEDIA/gifv.mp4" 12 deferred 2>&1 | grep '^SUMMARY')
  echo "local-$n ${s:-CRASH-OR-HANG} cores=$(coredumpctl list --since "$t0" --no-legend 2>/dev/null | grep -c python)" >> "$OUT"
done
t0=$(date '+%Y-%m-%d %H:%M:%S')
s=$(timeout 900 dbus-run-session -- mutter --headless --wayland --no-x11 --virtual-monitor 1280x1024 -- \
    sh -c "flatpak run --user --share=network --socket=wayland --device=dri --env=GTK_A11Y=none \
    --command=python3 org.gnome.Platform//51 - '$LIVE' 100 deferred < '$D/e1c_bundle.py'" 2>&1 | grep '^SUMMARY')
echo "sandbox-live ${s:-CRASH-OR-HANG} cores=$(coredumpctl list --since "$t0" --no-legend 2>/dev/null | grep -c python)" >> "$OUT"
echo DONE >> "$OUT"
