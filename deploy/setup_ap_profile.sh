#!/bin/sh
# Create (or recreate) the AP connection profile used when the camera is
# deployed in the field: open network, no password, so a phone can join it
# with no setup. Safe to run while connected over SSH on the home network —
# it does not touch wlan0's current connection, only this inactive profile.
#
# Deletes and recreates the profile if it already exists, rather than
# modifying it in place — that's the reliable way to guarantee no leftover
# security settings (nmcli has no clean one-liner to strip a wireless
# security setting back off a connection that already has one).
set -e

AP_NAME="AstroPiCamAP"
SSID="AstroCamera"

if nmcli -t -f NAME connection show | grep -qx "$AP_NAME"; then
    echo "AP profile '$AP_NAME' already exists — recreating it."
    sudo nmcli connection delete "$AP_NAME"
fi

sudo nmcli connection add type wifi ifname wlan0 con-name "$AP_NAME" autoconnect no ssid "$SSID"
sudo nmcli connection modify "$AP_NAME" 802-11-wireless.mode ap 802-11-wireless.band bg
# Public-range address instead of NetworkManager's default 10.42.0.1: recent
# Android skips its captive-portal check when the check hostname resolves to
# a private address, so the sign-in prompt only works with a public-range
# one (see AP_GATEWAY_IP in app/config.py). The hotspot has no internet, so
# this never reaches the real 4.3.2.1.
sudo nmcli connection modify "$AP_NAME" ipv4.method shared ipv4.addresses 4.3.2.1/24

echo "AP profile '$AP_NAME' ready (SSID: $SSID, open network, no password, address 4.3.2.1)."
echo "deploy/wifi-failover.sh brings it up automatically at boot if no known"
echo "network is in range; deploy/ap-mode.sh activates it immediately by hand."
