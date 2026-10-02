#!/bin/sh
# Lets the always-on Flask app (running as user 'pi') trigger a WiFi mode
# switch from the web UI without a stored root password. Grants NOPASSWD
# sudo for exactly these two scripts — nothing else — via a sudoers.d
# drop-in, validated with visudo before being trusted.
set -e

RULE_FILE=/etc/sudoers.d/astro-pi-network
SCRIPT_DIR=/home/pi/astro-pi-cam/deploy
TMP_FILE=$(mktemp)

echo "pi ALL=(root) NOPASSWD: $SCRIPT_DIR/ap-mode.sh, $SCRIPT_DIR/sta-mode.sh" > "$TMP_FILE"

sudo visudo -c -f "$TMP_FILE"
sudo install -m 440 -o root -g root "$TMP_FILE" "$RULE_FILE"
rm -f "$TMP_FILE"

echo "Installed $RULE_FILE — pi may run ap-mode.sh/sta-mode.sh as root, nothing else."
