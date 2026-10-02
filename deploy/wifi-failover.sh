#!/bin/sh
# Boot-time WiFi mode selection for Astro Pi Camera.
#
# NetworkManager already auto-connects to any known (autoconnect=yes) WiFi
# network it finds in range - e.g. the home network - with no help needed.
# This script's only job is the fallback: if nothing known is in range
# within a short window, bring up the Pi's own "AstroCamera" open AP so a
# phone can still connect to it out in the field.
#
# Runs as root via systemd (astro-pi-wifi-failover.service) at boot.
set -e

AP_NAME="AstroPiCamAP"
WAIT_SECONDS=25
INTERVAL=2

elapsed=0
while [ "$elapsed" -lt "$WAIT_SECONDS" ]; do
    state=$(nmcli -t -f DEVICE,STATE device status | awk -F: '$1=="wlan0"{print $2}')
    if [ "$state" = "connected" ]; then
        active_con=$(nmcli -t -f DEVICE,CONNECTION device status | awk -F: '$1=="wlan0"{print $2}')
        if [ "$active_con" != "$AP_NAME" ]; then
            echo "wlan0 connected to '$active_con' - staying on this network."
            exit 0
        fi
    fi
    sleep "$INTERVAL"
    elapsed=$((elapsed + INTERVAL))
done

echo "No known WiFi network found after ${WAIT_SECONDS}s - starting the AstroCamera AP."
nmcli connection up "$AP_NAME"
