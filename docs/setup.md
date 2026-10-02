# Device setup (run once per device)

Target: `192.168.1.35` (or `http://raspberrypi-2w.local:5000` for the app
— see "Reaching the camera" below, works regardless of IP/WiFi mode), user
`pi` (password known to the device owner — not recorded here since this
doc is public).

## Fresh install (recommended): one `.deb` package

For a brand-new Pi Zero 2 W (stock Raspberry Pi OS image), everything below
— dependencies, the app itself, the AP profile, the sudoers rule, the
captive-portal DNS override, and both systemd services — is packaged into
one `.deb`, built from `packaging/`:

```sh
# On a Debian/Raspberry Pi OS machine (needs dpkg-deb) -- the Pi itself is
# the simplest choice, since it always has it. Not runnable on Windows
# directly.
sh packaging/build-deb.sh
sudo apt install ./astro-pi-cam_1.0.0_all.deb
```

`apt install ./<file>.deb` (rather than plain `dpkg -i`) resolves and pulls
in the `Depends:` packages automatically on a fresh system that doesn't
have them yet. Safe to re-run on an already-set-up device too — every step
it performs is the same idempotent script described in the manual sections
below, just run automatically by the package's `postinst`. A capture
directory already populated with photos is never touched, even on
`apt purge`.

See `packaging/build-deb.sh`, `packaging/postinst`/`prerm`/`postrm`, and
`packaging/control.in` for exactly what it does; the sections below are the
same steps spelled out individually — useful for understanding what's
happening, or if you'd rather not build a package.

## Manual setup, step by step (what the `.deb` automates)

### 1. Install dependencies

```sh
ssh pi@192.168.1.35
cd ~/astro-pi-cam
sh deploy/setup_pi.sh
```

Installs via apt (not pip — see docs/architecture.md): `python3-picamera2`,
`python3-flask`, `python3-pil`.

### 2. Create the AP connection profile (inactive until you switch to it)

```sh
sh deploy/setup_ap_profile.sh
```

Creates an nmcli connection `AstroPiCamAP` — SSID `AstroCamera`, **open,
no password**, so a phone can join it with zero setup in the field. Does
**not** activate it, so your SSH session stays up.

### 2b. Let the app trigger WiFi mode switches from its own UI

```sh
sh deploy/setup_sudoers.sh
```

Installs a sudoers.d rule granting the `pi` user passwordless `sudo` for
exactly `deploy/ap-mode.sh` and `deploy/sta-mode.sh` — nothing else. This
is what lets the "Switch" button on the web page (see below) flip WiFi mode
without the app needing a stored root password. The rule is validated with
`visudo -c` before being installed.

### 2c. Make connecting to the AP pop up the dashboard automatically

```sh
sh deploy/setup_captive_portal.sh
```

Installs a DNS wildcard redirect for AP-mode clients (every hostname they
look up resolves to the Pi — harmless, since the AP has no upstream
internet anyway) in `/etc/NetworkManager/dnsmasq-shared.d/`, the config
directory NetworkManager's shared-mode dnsmasq already reads. Paired with
captive-portal-probe routes in `app.py`, this is what makes joining
`AstroCamera` behave like public WiFi — the phone pops its own "sign in to
this network" browser straight to the dashboard, like a hotel or café
hotspot, instead of the user needing to know to open a browser themselves.
Takes effect next time AP mode is (re)activated, not retroactively.

### 3. Install the systemd services

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

## Reaching the camera: IP changes with the mode, mDNS doesn't

In AP mode the camera is always `http://10.42.0.1:5000` (nmcli's shared-
mode gateway). In STA/home-WiFi mode its IP is whatever DHCP hands out
(`192.168.1.35` on this network, but that's not guaranteed forever). If
you switch modes and the page just stops loading, it's very likely this —
**not a failed WiFi reconnect** — you're still pointed at the old address.

The device also answers to `http://raspberrypi-2w.local:5000` via mDNS
(`avahi-daemon`), which resolves correctly in either mode. Prefer that
over the raw IP when you're not sure which mode the Pi is currently in.

## Switching WiFi mode by hand

**From the web UI**: the control page has a small toggle switch in the
nav bar (next to the Gallery link) showing the current mode ("WiFi"/"AP").
Tapping it is a full WiFi mode flip — it drops whatever connection you're
using to reach the camera right then, so it asks for confirmation first
with mode-specific wording (which includes the address to use afterward).
Switching to home WiFi automatically falls back to the AP if the home
network turns out not to be reachable (`sta-mode.sh`'s built-in fallback —
see below), so it can't strand the Pi with no network at all; switching
*to* AP mode always succeeds (it's the Pi's own radio/profile, no external
dependency).

**From SSH** (same two scripts the UI button calls):

```sh
sh deploy/ap-mode.sh    # go into the field: hosts "AstroCamera" AP, drops SSH over wlan0
sh deploy/sta-mode.sh   # back home: rejoins the home network (falls back to the AP if it can't)
```

## Redeploying code after changes

Two ways, for two different situations:

- **Fast iteration while developing** — copies `app/`/`deploy/` straight
  into the already-set-up install and restarts the service; doesn't touch
  the AP/sudoers/captive-portal setup, so it assumes that's already in
  place:
  ```sh
  ASTRO_PI_SSH_PASSWORD=<password> python deploy/deploy.py
  ```
- **Provisioning a new/reset device, or a clean reproducible upgrade** —
  rebuild and reinstall the `.deb` (bump the version in `VERSION` first for
  a real release):
  ```sh
  sh packaging/build-deb.sh
  sudo apt install ./astro-pi-cam_<version>_all.deb
  ```
