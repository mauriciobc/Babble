#!/bin/sh
# usage: sandbox_run.sh a|b  (inside headless mutter)
cd /home/mauriciobc/Documentos/Code/Babble
Q=reports/video-playback-bench/qa1b
rm -f $Q/sandbox_$1.gst
flatpak run --user --share=network --socket=wayland --device=dri --socket=pulseaudio \
  --env=MODE=$1 --env=GST_DEBUG=souphttpsrc:5 --filesystem=/home/mauriciobc/Documentos/Code/Babble/reports/video-playback-bench/qa1b --env=GST_DEBUG_FILE=/home/mauriciobc/Documentos/Code/Babble/reports/video-playback-bench/qa1b/sandbox_$1.gst --env=GST_DEBUG_NO_COLOR=1 \
  --command=python3 org.gnome.Platform//51 - < $Q/sandbox_play.py \
  > $Q/sandbox_$1.out 2> $Q/sandbox_$1.err
echo "run $1 rc=$?"
