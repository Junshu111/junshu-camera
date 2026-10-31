#!/bin/sh
# Called once by start-session.sh inside the existing labwc session.
set -eu
camera_app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
camera_state_dir="$HOME/.local/state/junshu-camera"
mkdir -p -- "$camera_state_dir"
exec >>"$camera_state_dir/autostart-newcam-1.0.log" 2>&1
printf '%s\n' 'New Cam 1.0.0 startup (one invocation)'
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    printf '%s\n' 'Graphical session missing; camera not started.'
    exit 2
fi
cd "$camera_app_dir"
if [ -z "${QT_QPA_PLATFORM:-}" ] && [ -n "${WAYLAND_DISPLAY:-}" ]; then
    export QT_QPA_PLATFORM=wayland
fi
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 -B "$camera_app_dir/run_camera.py" "$@"
