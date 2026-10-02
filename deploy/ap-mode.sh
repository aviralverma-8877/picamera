#!/bin/sh
# Switch wlan0 from home-network (dev) mode into the field AP mode.
# WARNING: this drops any SSH session connected over wlan0's current IP.
set -e

HOME_CONN="netplan-wlan0-TATA_3071"
AP_NAME="AstroPiCamAP"

sudo nmcli connection down "$HOME_CONN" 2>/dev/null || true
sudo nmcli connection up "$AP_NAME"

echo "AP mode active. Connect a phone to SSID 'AstroCamera' (open, no password) and browse to http://10.42.0.1:5000"
