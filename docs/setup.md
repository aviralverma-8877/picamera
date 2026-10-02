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

Creates an nmcli connection `AstroPiCamAP` — SSID `AstroCamera`, **open,
no password**, so a phone can join it with zero setup in the field. Does
**not** activate it, so your SSH session stays up.

## 2b. Let the app trigger WiFi mode switches from its own UI

```sh
sh deploy/setup_sudoers.sh
```

Installs a sudoers.d rule granting the `pi` user passwordless `sudo` for
exactly `deploy/ap-mode.sh` and `deploy/sta-mode.sh` — nothing else. This
is what lets the "Switch" button on the web page (see below) flip WiFi mode
without the app needing a stored root password. The rule is validated with
`visudo -c` before being installed.

## 3. Install the systemd services

```sh
sudo cp deploy/astro-pi-cam.service deploy/astro-pi-wifi-failover.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now astro-pi-wifi-failover.service astro-pi-cam.service
```

The app runs on port 5000 regardless of whether wlan0 is in STA or AP mode,
and restarts automatically on boot/crash. `astro-pi-wifi-failover` runs once
at every boot — see below.

## Automatic WiFi mode selection at boot

`deploy/wifi-failover.sh` (run by `astro-pi-wifi-failover.service`) is what
makes "at home vs. in the field" automatic:

- NetworkManager already auto-connects to any **known** WiFi network
  (autoconnect is on for the home network profile by default) the moment
  it's in range — no extra code needed for that part.
- The script's only job is the fallback: it waits up to 25s for that to
  happen, and if wlan0 still isn't connected to a real network by then, it
  brings up the `AstroCamera` AP itself.

So: camera boots at home → joins the home WiFi automatically → `deploy.py`
and SSH work as usual. Camera boots in the field with no known network in
range → after ~25s it starts hosting `AstroCamera` (open, no password) →
connect a phone to it and browse to `http://10.42.0.1:5000`.

**Known limitation** (single WiFi radio — see architecture.md): this
decision is made once at boot. If the AP is already up and you bring the
Pi back within range of the home network, it won't switch back on its own
— run `deploy/sta-mode.sh` (or reboot) to reconnect for dev/SSH.

## Switching WiFi mode by hand

**From the web UI**: the control page has a "Network" card showing the
current mode and a "Switch" button. Clicking it is a full WiFi mode flip —
it drops whatever connection you're using to reach the camera right then,
so it asks for confirmation first with mode-specific wording. Switching to
home WiFi automatically falls back to the AP if the home network turns out
not to be reachable (`sta-mode.sh`'s built-in fallback — see below), so it
can't strand the Pi with no network at all; switching *to* AP mode always
succeeds (it's the Pi's own radio/profile, no external dependency).

**From SSH** (same two scripts the UI button calls):

```sh
sh deploy/ap-mode.sh    # go into the field: hosts "AstroCamera" AP, drops SSH over wlan0
sh deploy/sta-mode.sh   # back home: rejoins the home network (falls back to the AP if it can't)
```

## Redeploying code after changes

From the dev machine:

```sh
ASTRO_PI_SSH_PASSWORD=<password> python deploy/deploy.py
```

Copies `app/` to the Pi and restarts the service.
