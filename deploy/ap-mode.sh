#!/bin/sh
# Switch wlan0 into the field AP mode (saving a scan of nearby networks
# first, for the dashboard's WiFi list — it can't scan while hosting the AP).
# WARNING: this drops any SSH session connected over wlan0's current IP.
exec "$(dirname "$0")/wifi.sh" ap
