#!/bin/sh
# E6 decode matrix inside org.gnome.Platform//51 (--device=dri), 3 runs each.
# Result: e6_decode.txt. MEDIA = folder with h264_1080p60.mp4 and h265_1080p60.mp4.
D=$(cd "$(dirname "$0")" && pwd)
python3 "$D/e6_server.py" 18776 "$MEDIA" & SRV=$!
sleep 1
: > "$D/e6_decode.txt"
for clip in h264_1080p60.mp4 h265_1080p60.mp4; do
  case $clip in
    h264*) decs="auto avdec_h264 vah264dec nvh264dec" ;;
    *)     decs="auto avdec_h265 vah265dec nvh265dec" ;;
  esac
  for d in $decs; do
    for r in 1 2 3; do
      line=$(flatpak run --user --share=network --device=dri --command=python3 \
        org.gnome.Platform//51 - decode http://127.0.0.1:18776/f/ "$clip" "$d" \
        < "$D/e6_probe.py" 2>>"$D/e6_decode.err" | grep '^DECODE')
      echo "run=$r ${line:-DECODE file=$clip dec=$d FAILED}" >> "$D/e6_decode.txt"
    done
  done
done
kill $SRV
