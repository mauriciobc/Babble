#!/bin/sh
# usage: sandbox_run.sh N  (run inside headless mutter)
cd /home/mauriciobc/Documentos/Code/Babble
flatpak run --user --share=network --socket=wayland --device=dri --socket=pulseaudio \
  --command=python3 org.gnome.Platform//51 - < reports/video-playback-bench/qa/sandbox_play.py \
  > reports/video-playback-bench/qa/sandbox_run$1.out 2> reports/video-playback-bench/qa/sandbox_run$1.err
echo "run $1 rc=$?"
