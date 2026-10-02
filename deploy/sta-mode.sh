#!/bin/sh
# Switch wlan0 back to the home network for development/SSH access.
set -e

HOME_CONN="netplan-wlan0-TATA_3071"
AP_NAME="AstroPiCamAP"

sudo nmcli connection down "$AP_NAME" 2>/dev/null || true
sudo nmcli connection up "$HOME_CONN"

echo "STA mode active, rejoined home network."
