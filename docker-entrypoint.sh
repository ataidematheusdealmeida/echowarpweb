#!/usr/bin/env bash
set -Eeuo pipefail

: "${ECHOWARP_SERVER:=}"
: "${ECHOWARP_PORT:=4415}"
: "${ECHOWARP_PASSWORD:=}"
: "${ECHOWARP_DEVICE_NAME:=EchoWarpWeb}"
: "${PULSE_SINK_NAME:=echowarp_web}"
: "${PULSE_SINK_DESCRIPTION:=$ECHOWARP_DEVICE_NAME}"
: "${PULSE_RATE:=48000}"
: "${PULSE_CHANNELS:=2}"
: "${WEB_PORT:=3008}"

HLS_DIR=/usr/share/nginx/html/hls

mkdir -p "$HLS_DIR"
rm -f "$HLS_DIR"/*
mkdir -p "$XDG_RUNTIME_DIR"
chmod 700 "$XDG_RUNTIME_DIR"
rm -rf "$XDG_RUNTIME_DIR/pulse"

echo "Starting PulseAudio"
pulseaudio --daemonize=yes --exit-idle-time=-1 --disallow-exit --log-target=stderr

for _ in $(seq 1 30); do
  if pactl info >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

if ! pactl info >/dev/null 2>&1; then
  echo "PulseAudio did not start" >&2
  exit 1
fi

for module_id in $(pactl list short modules | awk '$2 == "module-null-sink" {print $1}'); do
  pactl unload-module "$module_id" >/dev/null 2>&1 || true
done
for module_id in $(pactl list short modules | awk '$2 == "module-suspend-on-idle" {print $1}'); do
  pactl unload-module "$module_id" >/dev/null 2>&1 || true
done

pactl load-module module-null-sink \
  "sink_name=$PULSE_SINK_NAME" \
  "sink_properties=device.description=\"$PULSE_SINK_DESCRIPTION\"" \
  "rate=$PULSE_RATE" \
  "channels=$PULSE_CHANNELS" >/dev/null
pactl set-default-sink "$PULSE_SINK_NAME"
pactl set-sink-volume "$PULSE_SINK_NAME" 100%
pactl suspend-sink "$PULSE_SINK_NAME" false >/dev/null 2>&1 || true

echo "Available EchoWarp devices:"
/opt/echowarp/EchoWarp devices || true

exec python3 /opt/echowarp-web/server.py
