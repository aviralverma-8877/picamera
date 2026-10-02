#!/bin/sh
# Makes connecting to the AstroCamera AP behave like public WiFi: phones
# automatically pop up a "sign in to this network" prompt straight to the
# camera's dashboard, instead of the user having to know to open a browser
# and type an address.
#
# How it works: when wlan0 is in AP (shared) mode, NetworkManager runs its
# own internal dnsmasq for DHCP/DNS to connected clients, and reads extra
# config from /etc/NetworkManager/dnsmasq-shared.d/*.conf if present. This
# drops in a wildcard DNS override so every hostname a connected device
# looks up resolves to the Pi itself — harmless here since the AP has no
# upstream internet to begin with. app.py then recognizes each OS's
# connectivity-check probe (Apple/Android/Windows/Firefox all have one) and
# redirects it to the dashboard, which is what makes the OS treat this as
# a captive portal and open a browser straight to it.
set -e

CONF_DIR=/etc/NetworkManager/dnsmasq-shared.d
CONF_FILE="$CONF_DIR/captive-portal.conf"

sudo mkdir -p "$CONF_DIR"
printf 'address=/#/10.42.0.1\n' | sudo tee "$CONF_FILE" > /dev/null

echo "Installed $CONF_FILE — takes effect next time AP mode is (re)activated."
