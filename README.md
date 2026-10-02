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
- **Standalone in the field** — hosts its own WiFi AP (`nmcli`); switches
  back to the home network only for development.

## Hardware

- Raspberry Pi Zero 2 W
- Raspberry Pi HQ Camera, IR-cut filter removed
- Single WiFi radio: AP-only in the field (can't do AP + client WiFi at
  the same time on this chip), client mode for dev/SSH at home.

## Project layout

```
app/      Flask web app, Picamera2 capture logic, templates/static
deploy/   systemd service, apt setup script, AP mode toggle scripts,
          and deploy.py (pushes app/ to the Pi over SFTP and restarts it)
docs/     architecture.md (design + rationale), setup.md (device setup
          steps), progress.md (dated changelog of what's been built/tested)
```

## Setup

See [`docs/setup.md`](docs/setup.md) for installing dependencies on the
device, creating the WiFi AP profile, and installing the systemd service.
See [`docs/architecture.md`](docs/architecture.md) for how it's built and
why.

## Deploying

```sh
ASTRO_PI_SSH_PASSWORD=<password> python deploy/deploy.py
```

Copies `app/` and `deploy/` to the Pi over SFTP and restarts the service.
