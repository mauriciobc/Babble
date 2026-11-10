#!/bin/sh
# E6: attribute E5's engine memory plateau to the decoder. Host, inside headless
# mutter. Forces the H.264 decoder by rank and records which one was used.
# Result: e6_mem.txt
D=$(cd "$(dirname "$0")" && pwd)
python3 "$D/e6_server.py" 18779 "$MEDIA" & SRV=$!
sleep 1
: > "$D/e6_mem.txt"
for cfg in nvidia va software; do
  case $cfg in
    nvidia)   R="" ;;
    va)       R="nvh264dec:0,vulkanh264device1dec:0" ;;
    software) R="nvh264dec:0,vah264dec:0,vulkanh264device1dec:0,openh264dec:0" ;;
  esac
  used=$(GST_PLUGIN_FEATURE_RANK="$R" python3 "$D/e6_probe.py" decode \
    http://127.0.0.1:18779/f/ gifv.mp4 auto 2>/dev/null | grep -oE 'used=[^ ]+')
  for r in 1 2; do
    line=$(GST_PLUGIN_FEATURE_RANK="$R" timeout 240 python3 "$D/e5_idle.py" engine \
      "$MEDIA/gifv.mp4" 2>/dev/null | grep '^E5')
    echo "$cfg run=$r $used ${line:-E5 FAILED}" >> "$D/e6_mem.txt"
  done
done
kill $SRV
