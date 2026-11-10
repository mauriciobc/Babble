#!/bin/sh
# E6 playback probes in org.gnome.Platform//51, run inside headless mutter.
# GstPlay + gtk4paintablesink: CDN (files.mastodon.social), 302 redirect, no-Range.
D=$(cd "$(dirname "$0")" && pwd)
CDN=https://files.mastodon.social/media_attachments/files/117/587/728/606/060/718/original/cd45d56eb63e2f35.mp4
python3 "$D/e6_server.py" 18777 "$MEDIA" & SRV=$!
sleep 1
: > "$D/e6_play.txt"
probe() {  # label uri seek
  for r in 1 2; do
    line=$(flatpak run --user --share=network --socket=wayland --device=dri \
      --env=GTK_A11Y=none --command=python3 org.gnome.Platform//51 - play "$2" "$3" \
      < "$D/e6_probe.py" 2>>"$D/e6_play.err" | grep '^PLAY')
    echo "$1 run=$r ${line:-PLAY FAILED}" >> "$D/e6_play.txt"
  done
}
probe cdn "$CDN" 20
probe redirect http://127.0.0.1:18777/r/h264_1080p60.mp4 5
probe norange http://127.0.0.1:18777/norange/h264_1080p60.mp4 5
probe plain http://127.0.0.1:18777/f/h264_1080p60.mp4 5
kill $SRV
