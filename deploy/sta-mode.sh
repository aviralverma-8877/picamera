#!/bin/sh
# Switch wlan0 back to a normal WiFi network for development/SSH access:
# tries each saved network (the ones joined before, including any added from
# the dashboard) in NetworkManager's own preference order. If none can be
# reached (not actually home, router off, etc.), falls back to the
# AstroCamera AP instead of leaving the Pi with no network until a reboot.
exec "$(dirname "$0")/wifi.sh" home
