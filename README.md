# Astro Pi Camera

A standalone astrophotography camera built on a Raspberry Pi Zero 2 W and a
Pi HQ Camera (IR-cut filter removed). It hosts its own WiFi access point so
a phone can connect directly — no internet, no app install — and run timed
exposure sequences through a small web UI.

## Features

- **Manual exposure control** — exposure time (up to 200s), ISO (100-1600,
  or Auto), shot count, and interval between shots.
- **Named, appendable sessions** — pick an existing session to add more
  shots to it (numbering continues, nothing is overwritten) or start a new
  one with a custom or auto-generated name.
- **Live focusing preview** — a small MJPEG stream with a digital zoom
  (crops the sensor itself for real extra detail, not a CSS stretch) and a
  center crosshair, aimed at picking out a Bahtinov mask's diffraction
  pattern on a small star.
- **Raw DNG capture** (optional, per session) alongside JPEG, for stacking
  in Siril / DeepSkyStacker / etc.
- **Gallery** — browse captures, download individual frames or a whole
  session as a zip, delete a frame/session/everything.
- **Automatic WiFi mode at boot** — joins a known network (e.g. home WiFi)
  if one's in range, otherwise hosts its own open `AstroCamera` AP so a
  phone can connect in the field with zero setup. A toggle switch in the
  nav bar also lets you flip modes by hand at any time.
- **Connects like public WiFi** — joining the `AstroCamera` AP pops up a
  "sign in to this network" prompt straight to the dashboard, the same way
  a hotel or café hotspot does, instead of needing to know to open a
  browser and type an address.

## Hardware

- Raspberry Pi Zero 2 W
- Raspberry Pi HQ Camera, IR-cut filter removed
- Single WiFi radio: AP-only in the field (can't do AP + client WiFi at
  the same time on this chip), client mode for dev/SSH at home.

## Project layout

```
app/        Flask web app, Picamera2 capture logic, templates/static
deploy/     systemd services, apt/AP/sudoers/captive-portal setup scripts,
            and deploy.py (pushes app/ to the Pi over SFTP and restarts it)
packaging/  builds astro-pi-cam_<version>_all.deb — everything above,
            bundled into one installable package for a fresh device
docs/       architecture.md (design + rationale), setup.md (device setup
            steps), progress.md (dated changelog of what's been built/tested)
```

## Setup

**Fresh Pi Zero 2 W**: install the `.deb` from the
[latest release](https://github.com/aviralverma-8877/picamera/releases/latest):

```sh
wget https://github.com/aviralverma-8877/picamera/releases/download/v1.0.2/astro-pi-cam_1.0.2_all.deb
sudo apt install ./astro-pi-cam_1.0.2_all.deb
```

Handles dependencies, the app, the AP profile, the sudoers rule, the
captive-portal DNS override, and both systemd services in one step. It
also adds this project's signed apt repository, so after that one manual
install, new versions arrive with the normal:

```sh
sudo apt update && sudo apt upgrade
```

To build the package yourself from source instead, see
[`docs/setup.md`](docs/setup.md#fresh-install-recommended-one-deb-package).

## Releasing a new version

Bump `VERSION`, commit, then push a matching tag:

```sh
git tag v1.0.3 && git push origin v1.0.3
```

The `Release` GitHub Actions workflow builds the `.deb`, creates the
GitHub release, and publishes it to the apt repository.

For the manual, step-by-step version of the same setup (useful for
understanding what's happening, or tweaking a single piece), or for how
WiFi mode selection and the captive portal work, see
[`docs/setup.md`](docs/setup.md). See [`docs/architecture.md`](docs/architecture.md)
for how it's all built and why.

## Deploying code changes to an already-set-up device

```sh
ASTRO_PI_SSH_PASSWORD=<password> python deploy/deploy.py
```

Copies `app/` and `deploy/` to the Pi over SFTP and restarts the service —
the fast path while iterating. For provisioning a new device, use the
`.deb` above instead.
