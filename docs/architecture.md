# Architecture

## Goal

Pi Zero 2 W + HQ Camera (IR-cut filter removed) as a standalone astrophotography
camera: hosts its own WiFi AP, a phone connects directly to it, configures
exposure/gain/count/interval through a web page, and triggers a sequence of
timed exposures. No internet, no app install required on the phone.

## Hardware (confirmed via SSH, see memory `pi-zero-device-state`)

- Raspberry Pi Zero 2 W Rev 1.0, quad-core aarch64, ~415MB RAM.
- Camera: imx477 (HQ Camera), works via `rpicam-apps` / `libcamera` / `picamera2`.
- Single WiFi radio (`brcmfmac`) — cannot reliably do STA+AP at the same time.
  AP-only in the field; switch back to STA (home network) for dev/SSH.

## Components

```
app/
  app.py              Flask web server (routes, HTML rendering)
  camera.py           AstroCamera: thin wrapper over Picamera2 for manual
                       exposure stills + a quick auto-exposure preview shot
  capture_session.py  CaptureSession: background thread running N exposures
                       at a fixed interval, exposes polls-able status
  config.py           Defaults/limits, captures directory
  templates/           index.html (control form + live status),
                       gallery.html (browse/download past sessions)
  static/style.css    mobile-first styling

deploy/
  astro-pi-cam.service     systemd unit, runs app.py on boot
  setup_pi.sh              one-time: apt install picamera2/flask/pillow
  setup_ap_profile.sh      one-time: create (inactive) nmcli AP connection
  ap-mode.sh / sta-mode.sh  toggle wlan0 between field AP and home STA
```

## Why these choices

- **Flask, not a JS framework**: 415MB RAM is tight; a Python-rendered page
  with a little polling JS is far cheaper than a SPA + API.
- **picamera2 over legacy `picamera`**: legacy MMAL stack is gone on trixie;
  `picamera2`/libcamera is the only supported path and is apt-installable.
- **apt packages, not pip/venv**: `picamera2` is tightly coupled to the
  system libcamera build apt ships; pip wheels would fight that. Flask/Pillow
  are available via apt too, so the whole app runs on system Python with no
  venv to manage on a constrained device.
- **nmcli hotspot, not hostapd+dnsmasq**: NetworkManager already owns wlan0
  on this image; a hand-rolled hostapd/dnsmasq setup would fight NM for the
  interface. `nmcli` has native AP support that NM manages directly.
- **Manual AP/STA toggle scripts, not automatic switching from the app**:
  flipping wlan0 out of STA mode drops the SSH session used for dev/testing.
  The app never touches network mode itself; switching to AP mode is a
  deliberate step taken when the device is physically deployed.
- **Small live MJPEG preview, not a click-to-refresh shot**: `/preview.mjpg`
  streams continuous low-res (480x360) auto-exposure JPEG frames via
  Picamera2's `JpegEncoder`, shown small (240px wide) on the control page so
  focusing is actually usable from a phone. To keep this safe on a single
  camera pipeline and ~250MB free RAM: the stream opens the camera only
  while a client is connected (closed again when the browser navigates away
  or the connection drops), and the generator polls whether a capture
  session has started on every frame — if so it stops and releases the
  camera itself rather than holding the lock and blocking the session
  (`camera.py`'s `stream_mjpeg`). The index page reconnects the stream
  automatically once a running sequence finishes. Flask runs with
  `threaded=True` so this long-lived connection doesn't block status
  polling or other requests.
- **Raw DNG capture is optional per-session**: stacking software (Siril,
  DeepSkyStacker) wants raw frames, but DNGs are large (~18MB on imx477) and
  slower to write; JPEG-only is the default, raw is an opt-in checkbox.
- **Preview zoom crops the sensor (`ScalerCrop`), not the output image**:
  for picking out a Bahtinov mask's diffraction pattern on a tiny star, a
  CSS/canvas zoom on the already-downscaled 480x360 preview would just
  blur up pixels that are already there. `ScalerCrop` instead crops the
  full 4056x3040 sensor active area around its center *before* the ISP
  downscales to the preview size, so a higher zoom genuinely puts more
  real sensor pixels on the star. Capped at 8x (`config.MAX_ZOOM`) —
  roughly where the crop region becomes smaller than the 480px output, past
  which there's no more real detail to gain. Preview-only: `capture_still`
  never applies a crop, so actual captures are always full-frame regardless
  of what the preview was zoomed to when the sequence was started.
- **Center crosshair is a plain absolutely-positioned overlay div**, toggled
  client-side — it's drawn on top of the `<img>` in CSS, not baked into the
  video frames, so it stays perfectly centered regardless of zoom level and
  costs nothing on the Pi side.

## Capture flow

1. User opens `http://<ap-ip>:5000/` on their phone, picks a session (a
   growing dropdown of existing session folders, plus "New session"),
   names it if creating new (defaults to `<today>-<n>`, the nth new session
   created that day), and sets exposure (seconds), ISO, shot count, interval
   (seconds), and whether to also save raw DNG.
2. `POST /session/start` resolves the session: picking an existing one
   just reuses that folder; "New session" validates the name (same
   directory-name-safe character set as downloads/deletes, plus no two
   sessions may share a name) and creates `app/captures/<name>/`
   synchronously, before starting the background thread — so the dropdown
   on the very next page load already includes it. Inputs are
   validated/clamped and a `CaptureSession` thread is started; the HTTP
   request returns immediately.
3. The thread figures out where to continue: it scans the session folder
   for existing `frame_NNNN.jpg` files and starts numbering after the
   highest one found (0001 if empty) — appending more shots to an existing
   session must never overwrite its earlier frames. It then loops:
   configure manual exposure/gain controls on the camera, sleep roughly one
   exposure period (sensor needs that long to produce a correctly-exposed
   frame after a control change), capture, write `frame_NNNN.jpg` (+ `.dng`
   if enabled), update shared status, then wait out the remainder of the
   interval before the next shot.
4. The index page polls `GET /session/status` (JSON) to show progress
   without reloading.
5. `/gallery` lists past sessions and their frames for download over the AP.

## Known constraints / future work

- Exposure is clamped to 200s; the imx477 can do longer with the right sensor
  mode but 200s already covers most deep-sky single-frame use and keeps the
  UI simple.
- No dithering/guiding — this is a fixed-tripod lucky-imaging-style sequencer,
  not a tracked-mount controller.
- The ST7789 display HAT noticed during hardware inspection (SPI/I2C
  currently disabled) is not part of this implementation — plan.txt doesn't
  call for an on-device display, so it's left for a later iteration if
  actually wanted.
