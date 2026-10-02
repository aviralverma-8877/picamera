#!/bin/sh
# One-time setup: create (but do not activate) the AP connection profile
# used when the camera is deployed in the field. Safe to run while
# connected over SSH on the home network — it does not touch wlan0's
# current connection.
set -e

AP_NAME="AstroPiCamAP"
SSID="AstroPiCam"
PASSWORD="astrophoto"

if nmcli -t -f NAME connection show | grep -qx "$AP_NAME"; then
    echo "AP profile '$AP_NAME' already exists, skipping."
    exit 0
fi

sudo nmcli connection add type wifi ifname wlan0 con-name "$AP_NAME" autoconnect no ssid "$SSID"
sudo nmcli connection modify "$AP_NAME" 802-11-wireless.mode ap 802-11-wireless.band bg
sudo nmcli connection modify "$AP_NAME" ipv4.method shared
sudo nmcli connection modify "$AP_NAME" wifi-sec.key-mgmt wpa-psk
sudo nmcli connection modify "$AP_NAME" wifi-sec.psk "$PASSWORD"

echo "AP profile '$AP_NAME' created (SSID: $SSID, password: $PASSWORD)."
echo "Activate it with deploy/ap-mode.sh when deploying in the field."
