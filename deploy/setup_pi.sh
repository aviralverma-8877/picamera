#!/bin/sh
# One-time dependency install. Run on the Pi via SSH.
set -e

sudo apt-get update
sudo apt-get install -y python3-picamera2 python3-flask python3-pil

echo "Dependencies installed."
