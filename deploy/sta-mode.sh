#!/bin/sh
# Switch wlan0 back to the home network for development/SSH access. If the
# home network can't actually be reached (wrong assumption about being
# home, router off, etc.), falls back to the AstroCamera AP instead of
# leaving the Pi with no network at all until a reboot.
set -e

HOME_CONN="netplan-wlan0-TATA_3071"
AP_NAME="AstroPiCamAP"
WAIT_SECONDS=15
INTERVAL=2

sudo nmcli connection down "$AP_NAME" 2>/dev/null || true
sudo nmcli connection up "$HOME_CONN" 2>/dev/null || true

elapsed=0
while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    state=$(nmcli -t -f DEVICE,STATE device status | awk -F: '$1=="wlan0"{print $2}')
    active_con=$(nmcli -t -f DEVICE,CONNECTION device status | awk -F: '$1=="wlan0"{print $2}')
    if [ "$state" = "connected" ] && [ "$active_con" = "$HOME_CONN" ]; then
        echo "STA mode active, rejoined home network."
        exit 0
    fi
    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
done

echo "Could not reach '$HOME_CONN' — falling back to the AstroCamera AP."
sudo nmcli connection up "$AP_NAME"
