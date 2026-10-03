#!/bin/sh
# Builds astro-pi-cam_<version>_all.deb from the current source tree.
#
# Needs dpkg-deb, so run it on a Debian/Raspberry Pi OS machine — the Pi
# itself is the simplest choice (it always has dpkg-deb), or any other
# Debian-based dev machine. Not runnable on Windows directly.
set -e

cd "$(dirname "$0")/.."  # repo root

VERSION=$(cat VERSION)
PKG=astro-pi-cam
STAGE=$(mktemp -d)
OUT="${PKG}_${VERSION}_all.deb"

trap 'rm -rf "$STAGE"' EXIT

# --- App + deploy scripts, under the pi user's home dir ---
INSTALL_DIR="$STAGE/home/pi/astro-pi-cam"
mkdir -p "$INSTALL_DIR"
cp -r app "$INSTALL_DIR/app"
cp -r deploy "$INSTALL_DIR/deploy"
rm -rf "$INSTALL_DIR/app/captures"
find "$INSTALL_DIR" -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# --- systemd units, shipped at their final path so dpkg manages them
#     natively (tracked, removed cleanly on uninstall) instead of
#     postinst copying them in by hand ---
mkdir -p "$STAGE/etc/systemd/system"
cp deploy/astro-pi-cam.service "$STAGE/etc/systemd/system/"
cp deploy/astro-pi-wifi-failover.service "$STAGE/etc/systemd/system/"

# --- apt source for this project's repo + its signing key, so the
#     installed system gets future versions via `apt upgrade` ---
mkdir -p "$STAGE/etc/apt/sources.list.d" "$STAGE/usr/share/keyrings"
cp packaging/astro-pi-cam.sources "$STAGE/etc/apt/sources.list.d/"
cp packaging/astro-pi-cam-archive-keyring.gpg "$STAGE/usr/share/keyrings/"
chmod 644 "$STAGE/etc/apt/sources.list.d/astro-pi-cam.sources" \
          "$STAGE/usr/share/keyrings/astro-pi-cam-archive-keyring.gpg"

# --- package metadata + maintainer scripts ---
mkdir -p "$STAGE/DEBIAN"
sed "s/@VERSION@/$VERSION/" packaging/control.in > "$STAGE/DEBIAN/control"
cp packaging/postinst "$STAGE/DEBIAN/postinst"
cp packaging/prerm "$STAGE/DEBIAN/prerm"
cp packaging/postrm "$STAGE/DEBIAN/postrm"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm"
chmod +x "$INSTALL_DIR"/deploy/*.sh

dpkg-deb --build --root-owner-group "$STAGE" "$OUT"
echo "Built $OUT"
