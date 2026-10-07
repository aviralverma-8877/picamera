#!/bin/sh
# Lets the always-on Flask app (running as user 'pi') switch WiFi mode,
# scan for and join WiFi networks, and reboot/shut down the Pi from the web
# UI without a stored root password. Grants NOPASSWD sudo for exactly these
# commands — the two mode scripts, `wifi.sh scan` / `wifi.sh connect`, and
# `systemctl reboot` / `systemctl poweroff`, each with those exact arguments
# (sudoers matches arguments too, so this doesn't open up systemctl or
# wifi.sh's other subcommands in general) — via a sudoers.d drop-in,
# validated with visudo before being trusted.
set -e

RULE_FILE=/etc/sudoers.d/astro-pi-network
SCRIPT_DIR=/home/pi/astro-pi-cam/deploy
TMP_FILE=$(mktemp)

echo "pi ALL=(root) NOPASSWD: $SCRIPT_DIR/ap-mode.sh, $SCRIPT_DIR/sta-mode.sh, $SCRIPT_DIR/wifi.sh scan, $SCRIPT_DIR/wifi.sh connect, /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff" > "$TMP_FILE"

sudo visudo -c -f "$TMP_FILE"
sudo install -m 440 -o root -g root "$TMP_FILE" "$RULE_FILE"
rm -f "$TMP_FILE"

echo "Installed $RULE_FILE — pi may run ap-mode.sh/sta-mode.sh, wifi.sh scan/connect and systemctl reboot/poweroff as root, nothing else."
