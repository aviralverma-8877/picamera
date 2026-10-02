# Device setup (run once per device)

Target: `192.168.1.35`, user `pi` (password known to the device owner —
not recorded here since this doc is public).

## 1. Install dependencies

```sh
ssh pi@192.168.1.35
cd ~/astro-pi-cam
sh deploy/setup_pi.sh
```

Installs via apt (not pip — see docs/architecture.md): `python3-picamera2`,
`python3-flask`, `python3-pil`.

## 2. Create the AP connection profile (inactive until you switch to it)

```sh
sh deploy/setup_ap_profile.sh
```

Creates an nmcli connection `AstroPiCamAP` (SSID `AstroPiCam`, WPA2 password
`astrophoto` — edit the script before running if you want a different
SSID/password). Does **not** activate it, so your SSH session stays up.

## 3. Install the systemd service

```sh
sudo cp deploy/astro-pi-cam.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now astro-pi-cam.service
```

The app then runs on port 5000 regardless of whether wlan0 is in STA or AP
mode, and restarts automatically on boot/crash.

## Switching between dev (home WiFi) and field (AP) mode

```sh
sh deploy/ap-mode.sh    # go into the field: hosts "AstroPiCam" AP, drops SSH over wlan0
sh deploy/sta-mode.sh   # back home: rejoins the home network, SSH works again
```

While in AP mode, connect a phone to SSID `AstroPiCam` and browse to
`http://10.42.0.1:5000` (nmcli's default shared-mode gateway IP).

## Redeploying code after changes

From the dev machine:

```sh
ASTRO_PI_SSH_PASSWORD=<password> python deploy/deploy.py
```

Copies `app/` to the Pi and restarts the service.
