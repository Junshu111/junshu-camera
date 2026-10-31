#!/bin/sh
# One labwc-session invocation. The desktop is revealed after camera exit/failure.
set -u
camera_app_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
camera_state_dir="$HOME/.local/state/junshu-camera"
mkdir -p -- "$camera_state_dir" || exit 3
session_log="$camera_state_dir/session-1.0.0.log"
exec >>"$session_log" 2>&1
printf '%s\n' 'New Cam 1.0.0 session: camera once, then desktop'

if [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
    printf '%s\n' 'Graphical session missing; cannot launch camera or desktop.'
    exit 2
fi

if /bin/sh "$camera_app_dir/start-autostart.sh"; then
    camera_status=0
else
    camera_status=$?
    printf 'Camera exited with status %s; revealing desktop.\n' "$camera_status"
fi

# labwc remains the compositor. These are the two desktop clients deferred
# from the user-level autostart script; no camera restart occurs here.
if command -v lwrespawn >/dev/null 2>&1; then
    lwrespawn /usr/bin/pcmanfm-pi >>"$session_log" 2>&1 &
    lwrespawn /usr/bin/wf-panel-pi >>"$session_log" 2>&1 &
else
    printf '%s\n' 'lwrespawn unavailable; trying desktop clients directly.'
    /usr/bin/pcmanfm-pi >>"$session_log" 2>&1 &
    /usr/bin/wf-panel-pi >>"$session_log" 2>&1 &
fi
# Give the launched clients a scheduling turn before the one-shot shell exits.
# labwc keeps them alive after this script returns.
sleep 0.1
exit "$camera_status"
