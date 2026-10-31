#!/bin/sh
# New Cam 1.0 manual launch in the existing Pi desktop session.
set -eu
camera_app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$camera_app_dir"
if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    printf '%s\n' '请在树莓派本地桌面启动，不要从普通SSH运行。' >&2
    exit 2
fi
if [ -z "${QT_QPA_PLATFORM:-}" ] && [ -n "${WAYLAND_DISPLAY:-}" ]; then
    export QT_QPA_PLATFORM=wayland
fi
export PYTHONDONTWRITEBYTECODE=1
exec /usr/bin/python3 -B "$camera_app_dir/run_camera.py" "$@"
