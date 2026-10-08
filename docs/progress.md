# Progress

## 2026-10-02 — Initial implementation, deployed and tested live

Built and deployed the full MVP described in `docs/architecture.md`:

- Flask web app (`app/app.py`) with manual-exposure capture sessions
  (`app/capture_session.py`), a Picamera2 wrapper (`app/camera.py`), a
  mobile-friendly control page, status polling, an on-demand focus preview,
  and a gallery for browsing/downloading past sessions.
- systemd service (`deploy/astro-pi-cam.service`) — installed, enabled, and
  confirmed to survive the service restart cycle.
- nmcli AP connection profile `AstroPiCamAP` (SSID `AstroPiCam`, WPA2
  password `astrophoto`) created on the device, inactive by default so dev
  SSH access over the home network wasn't disrupted.
- `deploy/deploy.py` (paramiko-based) pushes `app/` + `deploy/` to the Pi,
  installs the systemd unit, and restarts the service — used for this
  deploy and for future iterations.

### Live verification on 192.168.1.35

- `GET /` → 200.
- `GET /preview.jpg` → real 800x600 JPEG from the imx477 (confirmed via
  JPEG EXIF: manufacturer Raspberry Pi, model imx477, software Picamera2).
- `POST /session/start` (exposure=1s, gain=2, count=3, interval=2s) → ran a
  real background capture sequence; `GET /session/status` correctly
  reported progress (0→2→3 of 3) and completion with no errors.
- Captured frames landed at full sensor resolution (4056x3040 JPEG) under
  `app/captures/<timestamp>/frame_NNNN.jpg`.
- `GET /gallery` listed the session and its 3 frames; individual frame
  downloads via `GET /captures/<session>/<file>` returned the correct JPEG
  bytes.
- Path-traversal attempt on the download route (`..%2f..%2fetc/passwd`)
  correctly returned 404.
- `systemctl is-active` / `is-enabled` both confirm the service is running
  and will start on boot.

### Known rough edge hit during deploy

Right after `apt-get install python3-picamera2 ...` finished, the very next
service start crashed with `ImportError: liblapack.so.3` — numpy (a
picamera2 dependency) briefly couldn't resolve a shared library right after
install. A restart ~30s later succeeded and it's been stable since; systemd's
`Restart=on-failure` already covers this automatically on boot.

## 2026-10-02 — Live MJPEG focusing preview

Replaced the click-to-refresh `/preview.jpg` single shot with a small
(480x360, shown at 240px) continuous live MJPEG stream at `/preview.mjpg`,
using Picamera2's `JpegEncoder` + a `FileOutput`-backed buffer
(`camera.py: stream_mjpeg`). Enabled Flask `threaded=True` so the long-lived
stream connection doesn't block status polling/session start.

To avoid the live view deadlocking against a capture session on the single
physical camera: the stream generator polls whether a session is running on
every frame and self-terminates (releasing the camera) the moment one
starts, rather than holding the lock. The index page's status poll detects
when a running session finishes and reconnects the stream automatically;
the `<img>` also retries on error (covers the window where a session is
already running when the page loads).

### Verified live on 192.168.1.35

- `GET /preview.mjpg` while idle → 200, continuous multipart stream; a 5s
  capture yielded 119 frames (~24fps) at ~146KB/s — confirmed by counting
  `--FRAME` boundaries and JPEG SOI markers, and by decoding/viewing an
  extracted frame (480x360, real camera image).
- `GET /preview.mjpg` while a session is running → 503, as designed.
- After the session finished, the stream became available again (200)
  without a service restart.
- An abrupt client disconnect mid-stream (curl `--max-time`) didn't leave
  the camera locked — a subsequent request succeeded immediately,
  confirming the generator's `finally` (stop_recording/close/lock release)
  runs on disconnect.

## 2026-10-02 — ISO dropdown instead of raw gain

Replaced the free-form "Gain" number input with an "ISO" dropdown, matching
how a conventional camera exposes sensitivity. Queried the imx477's actual
`AnalogueGain` control range live via picamera2 (`camera_controls`):
**1.0-16.0**, confirmed by briefly stopping the service to get an exclusive
camera handle. Using the standard Pi HQ Camera convention (gain 1.0 = ISO
100, linear), that's an ISO range of **100-1600**; the dropdown offers the
standard full-stop values within it: 100, 200, 400, 800, 1600
(`config.ISO_OPTIONS`). `config.iso_to_gain()` converts at the form boundary
in `app.py`; `camera.py`/`capture_session.py` still work in terms of
`AnalogueGain` internally, unchanged.

### Verified live on 192.168.1.35

- `GET /` renders a `<select name="iso">` with exactly the 5 options, 100
  selected by default.
- `POST /session/start` with `iso=500` (not one of the options) → 400,
  rejected.
- `POST /session/start` with `iso=400` → real capture completed
  successfully (`completed: 1`, no error).

## 2026-10-02 — Auto ISO + live preview follows the selected ISO

Two related changes:

1. Added an "Auto" option to the ISO dropdown (now first and the default).
   Auto ISO uses a documented-but-obscure picamera2/libcamera capability:
   leaving `AeEnable: True` while fixing only *one* of `ExposureTime` /
   `AnalogueGain` lets the AE algorithm auto-adjust the other. For captures
   this means the user's chosen exposure time (shutter) stays exactly fixed
   — essential for a timed sequence — while gain floats to match the scene,
   exactly like Auto-ISO on a conventional camera. `config.parse_iso()`
   converts the form/query value to a `gain` (`None` = auto) used
   throughout; `camera.py`'s `capture_still`/`stream_mjpeg` both branch on
   `gain is None` vs a fixed value.
2. The live preview now locks to whichever ISO is selected, instead of
   always being fully automatic: changing the dropdown reconnects
   `/preview.mjpg?iso=...`, which fixes `AnalogueGain` (auto exposure time)
   for a manual ISO, or leaves both free for Auto — the mirror image of the
   capture-side trick.

### Verified live on 192.168.1.35

- Dropdown now renders "Auto" first, selected by default.
- Confirmed the gain-pinning mechanism actually works, not just "doesn't
  crash": queried `capture_metadata()` directly (service stopped briefly for
  exclusive access) in a dim room where auto-gain naturally settled at 16.0
  — forcing `AnalogueGain: 1.0` with `AeEnable` still `True` genuinely
  overrode that to 1.0 in the reported metadata, proving the fix-one/
  auto-the-other trick is real, not a no-op.
- `GET /preview.mjpg?iso=auto` and `?iso=1600` both stream valid MJPEG
  frames (verified frame boundaries/JPEG markers, decoded and viewed a
  frame from each).
- `GET /preview.mjpg?iso=9999` → 400 (invalid ISO rejected).
- A real 2-shot capture sequence with `iso=auto` completed successfully
  (`completed: 2`, no error).

## 2026-10-02 — Fixed stale preview connections; added gallery delete

**Bug fix**: changing the ISO dropdown wasn't actually changing the live
preview. Root cause: browsers don't reliably close the old
`multipart/x-mixed-replace` connection the instant `<img src>` is
reassigned, and with a single physical camera behind one lock, the new
`/preview.mjpg` request could sit blocked behind a stream the browser had
already visually abandoned. Fixed with a generation counter
(`app.py: _next_preview_generation`) — each new preview request bumps it,
and a running stream's `should_stop()` check (polled every frame) now also
fires when it's no longer the latest request, not just when a capture
session starts. Verified by deliberately holding an old connection open
(not letting the client close it) and confirming a new request with a
different ISO still got served promptly — server logs showed the old
stream's camera stopped/closed immediately followed by the new one opening.

**Gallery delete**: added delete for a single image, a whole session, or
everything (`/captures/<session>/<file>/delete`,
`/gallery/<session>/delete`, `/gallery/delete-all`), all POST with a JS
`confirm()` before submitting. Deleting a single frame also removes its
matching raw `.dng` if present, and removes the session folder if that was
its last file. All three routes reuse the same path-safety check as
downloads, and are blocked with 409 while a capture session is running (a
session actively writing into its folder shouldn't have it deleted out from
under it).

### Verified live on 192.168.1.35

- Single-file delete: removed exactly the targeted frame, left the other
  frame in place.
- Session delete: removed the session's folder and all its contents.
- Delete-all: emptied `app/captures/` entirely (confirmed via SSH listing,
  not just the gallery page no longer showing anything).
- Path traversal attempts on both the session-delete and file-delete routes
  → 404.
- Attempting delete-all while a session was actively running → 409,
  correctly refused.

## 2026-10-02 — Exposure field validation bug + Auto ISO performance bug

**Validation bug**: the Exposure input had `min="0.0001"` with `step="0.01"`,
which don't share a grid — the browser's native step validation rejected
the plain default value `5.0` ("nearest valid values are 4.9901 and
5.0001"). Fixed by switching to `step="any"` (range checking via min/max
still applies; the step grid was never meaningful here).

**Performance bug, found while re-testing after the fix above**: an Auto
ISO capture at a 5s exposure took ~37 seconds end to end (logs showed
"Camera started" to "Camera stopped" spanning 36s for what should've been
a ~5.5s operation). Cause: `capture_still`'s auto-gain path pinned
`FrameDurationLimits` to the full exposure time while leaving `AeEnable`
True, so libcamera's AE algorithm had to converge across *multiple real
exposures* at that duration — for a 60s shot this would mean many minutes,
not seconds. Fixed by adding `AstroCamera._meter_gain()`: for Auto ISO,
meter at a fast frame rate first (8 quick frames, sub-second total) and
extrapolate the equivalent gain for the target exposure via the standard
reciprocity relationship (gain x exposure ≈ constant), clamped to the
sensor's 1.0-16.0 range, then take the actual shot as a single
deterministic manual-gain frame — same as the manual-ISO path. Verified:
the same 5s Auto ISO capture now completes in ~14s (dominated by the real
5s exposure + camera open/close overhead), not 37s.

**Not a bug (verified)**: that same 5s Auto ISO test shot came out
blown-out/overexposed. Captured a manual ISO 100 frame at the identical 5s
exposure as a control — it was equally overexposed. This confirms the
scene (a normally-lit indoor room) is simply too bright for a 5-second
exposure at the sensor's minimum gain (1.0); no ISO setting can go lower,
auto or manual, so both paths correctly produce the same (overexposed)
result. Long fixed exposures like this are intended for actual low-light/
night-sky scenes, not confirmed here since all testing so far has been
indoors — see "No real-sky test yet" below.

## 2026-10-02 — Preview placeholder instead of broken-image icon

While a capture session runs, `/preview.mjpg` returns 503 and the browser
showed its default broken-image icon — looked like something was wrong
rather than "capturing in progress." Replaced it with a proper placeholder
(camera-off SVG icon + text) that shows "Capturing — preview paused" when a
sequence is running, or "Preview unavailable" otherwise, swapping back to
the live `<img>` once a frame actually loads (`index.html`'s
`showPreviewImage`/`showPreviewPlaceholder`). Verified the server-side 503
behavior is unchanged (this was a template/CSS-only change) and the page
renders the new markup correctly; full visual confirmation still needs a
real browser since this environment can't screenshot client-side JS state.

## 2026-10-02 — Named sessions, selectable/appendable from a dropdown

Added a "Session" dropdown ("New session" + every existing session,
growing as sessions are created) and a "Session name" text field to the
capture form. Picking an existing session appends to it; picking "New
session" lets the user name it (defaulting to `<today>-<n>`, the nth new
session created that day) or accept the default.

- `app.py`: new-session names are validated against
  `^[A-Za-z0-9][A-Za-z0-9 _-]{0,99}$` (directory-safe, no dots so ".." can't
  come up, URL-safe since the name is used directly in routes) and must not
  already exist (`A session with that name already exists`, 400). The
  directory is created synchronously in the request handler — not lazily in
  the background thread — specifically so it's already present, and shows
  up in the dropdown, by the time the post-start redirect reloads the page.
- `capture_session.py`: `CaptureSession` now takes the resolved
  `session_name` instead of generating its own timestamp, and — this is the
  part that actually mattered — scans the target folder for existing
  `frame_NNNN.jpg` files and continues numbering after the highest one
  found, rather than always restarting at `frame_0001.jpg`. Appending to an
  existing session would otherwise silently overwrite its earlier frames.
- `index.html`: selecting an existing session in the dropdown locks the
  name field to that session's name (can't imply a rename); selecting "New
  session" restores the editable default. A live client-side check warns
  if a typed new name collides with an existing session, ahead of the
  authoritative server-side 400.

### Verified live on 192.168.1.35

- Fresh device: default name was `2026-10-02-1`; after creating it, the
  dropdown listed it and the next default correctly became `2026-10-02-2`.
- Duplicate name (`2026-10-02-1` again) → 400, "A session with that name
  already exists".
- Invalid name (`../evil`) → 400, rejected by the pattern.
- **Append correctness** (the critical case): session `2026-10-02-1` had
  `frame_0001.jpg`/`frame_0002.jpg`. Selected it from the dropdown and ran
  2 more shots — confirmed via `ls -la` that the original two files were
  byte-for-byte unchanged (identical size and mtime) while
  `frame_0003.jpg`/`frame_0004.jpg` were added; gallery showed all 4 under
  the one session.
- Custom name with spaces (`Moon Test 1`) accepted and used as the
  directory name.

## 2026-10-02 — Fixed preview image and placeholder showing at once

Both the live preview `<img>` and the "Preview unavailable" placeholder
were rendering simultaneously, stacked. Root cause: `.preview-stream
{display:block}` and `.preview-placeholder {display:flex}` are author
stylesheet rules, and author CSS overrides the browser's default
`[hidden]{display:none}` even at equal selector specificity — so toggling
`el.hidden` via JS was setting the attribute but having no visual effect on
either element. Fixed with an explicit `[hidden] { display: none
!important; }` rule in `style.css`, which now wins regardless of what
other display-setting classes an element carries. This is a general fix
(applies to any future use of `.hidden` in this app), not specific to the
preview elements. Deployed; confirmed the CSS rule is actually served, but
full visual confirmation needs a real browser since this environment can't
screenshot client-side rendering.

## 2026-10-02 — Blank preview fix + download options

**Blank preview bug**: after the `[hidden]` CSS fix, the preview went
blank instead of showing either the image or the placeholder. Root cause:
the code only revealed the `<img>` on its `load` event — but Chrome
(unlike Firefox) never fires `load` for a continuous
`multipart/x-mixed-replace` stream; it only fires once the whole HTTP
response finishes, which never happens for an open-ended MJPEG feed. So
the image stayed permanently hidden in Chrome once `[hidden]` actually
started working. Fixed by showing the `<img>` optimistically the moment a
connection is attempted (`reconnectPreview()`), falling back to the
placeholder only on an actual `error` event, instead of waiting for `load`
to reveal it.

**Download options added**:
- Individual images: `/captures/<session>/<file>` now serves with
  `Content-Disposition: attachment`, so clicking a thumbnail link prompts
  a save instead of just navigating to view it. Thumbnails still render
  fine — `<img src>` fetches ignore `Content-Disposition` entirely, it only
  affects top-level navigation/clicks.
- Whole-session batch download: `/gallery/<session>/download` zips the
  session's files (`zipfile.ZIP_STORED` — no compression, since JPEG/DNG
  are already compressed and the Pi's CPU is the scarcer resource here) to
  a temp file on disk rather than in memory (RAM is tight; disk has 12GB
  free), streams it back via `send_file`, and cleans up the temp file via
  `after_this_request`. Blocked with 409 if that specific session is
  actively being captured into (reading mid-write risks a partial frame).

### Verified live on 192.168.1.35

- Image download: `HEAD` showed `Content-Disposition: attachment;
  filename=frame_0001.jpg`; thumbnail `<img>` fetch of the same URL still
  returned valid, renderable JPEG bytes.
- Session zip: downloaded, opened with Python's `zipfile` — contained all
  4 expected frames, `testzip()` reported no corruption.
- Confirmed via SSH that the temp `.zip` was deleted after the response
  completed (no leftover files in `/tmp`).
- Starting a capture on `2026-10-02-1` and immediately requesting its zip
  → 409, correctly refused.
- The preview JS fix is deployed and confirmed present in the served page;
  full visual confirmation (Chrome specifically) still needs a real
  browser since this environment can't render client-side JS.

## 2026-10-02 — RAW badge on thumbnails; first real raw-capture test

Added a small "RAW" badge (bottom-left corner of the thumbnail) for frames
that also have a `.dng` saved alongside the `.jpg`. `gallery()` in app.py
now checks `f.with_suffix(".dng").exists()` per frame and passes
`{name, has_raw}` to the template instead of a bare filename list.

This was also the first time the raw-capture path got exercised end to end
(`docs/progress.md` had flagged it as untested). Ran a real 2-shot session
with `raw=on`:

- Both `frame_0001.dng`/`frame_0002.dng` were written successfully
  (~18.5MB each — consistent with imx477's 12MP raw Bayer data), alongside
  normal-sized jpgs.
- Gallery correctly showed 2 RAW badges for that session.
- A separate non-raw session showed 0 badges, confirming the badge is
  genuinely conditional per-frame, not just always-on.

## 2026-10-02 — Preview zoom (for Bahtinov focusing) + center crosshair

For focusing with a Bahtinov mask on a star too small to see the
diffraction pattern clearly, added:

- **Zoom slider** (1x-8x) on the preview. Implemented via picamera2's
  `ScalerCrop` control (`camera.py`: `_zoom_crop_rect`), which crops the
  sensor's full 4056x3040 active area around its center *before* the ISP
  downscales to the 480x360 preview — genuine extra detail on the star, not
  a CSS stretch of pixels already thrown away by downscaling. Capped at 8x,
  roughly where the crop region becomes smaller than the preview's output
  width (4056/480 ≈ 8.45) and there's no more real resolution to gain.
  Preview-only: `capture_still` never applies a crop, so actual captures
  stay full-frame regardless of what the preview was zoomed to. The slider
  reconnects the stream on release (`change`), not every tick (`input`,
  used only to update the numeric label live) — each reconnect is a full
  camera reconfigure, too slow to do continuously while dragging.
- **Center crosshair toggle**: a plain absolutely-positioned "+" overlay
  div on top of the preview `<img>`, shown/hidden client-side via a
  checkbox. Pure CSS overlay, not baked into the video frames, so it's free
  on the Pi and stays centered regardless of zoom level.

### Verified live on 192.168.1.35

- Compared an extracted frame at zoom=1 vs zoom=4 on the same static test
  scene: zoom=4 showed a visibly magnified, tightly-cropped center region
  (text in frame was noticeably larger/more detailed), confirming
  `ScalerCrop` is doing a real sensor-level crop, not just placebo.
- `zoom=100` and `zoom=0.1` both returned 200 (silently clamped into
  range); `zoom=abc` correctly returned 400.
- Confirmed the crosshair div and zoom slider (with server-rendered
  min="1.0"/max="8.0") are present in the served HTML.

## 2026-10-02 — Automatic WiFi mode at boot + manual toggle in the UI

Implements the "at home use home WiFi, in the field host the camera's own
AP" requirement:

- **AP reconfigured**: `AstroPiCamAP` is now SSID `AstroCamera`, fully
  open (no password) — recreated from scratch (delete + re-add) rather
  than modified in place, since nmcli has no clean one-liner to strip a
  wireless-security setting back off a connection that already has one.
  Verified live: `nmcli connection show AstroPiCamAP` has zero
  `wireless-security.*` lines.
- **Boot-time failover** (`deploy/wifi-failover.sh` +
  `astro-pi-wifi-failover.service`, oneshot, before `astro-pi-cam.service`):
  leans on NetworkManager's own autoconnect for known networks (no code
  needed — the home profile already has `autoconnect: yes`) and only adds
  the fallback: if wlan0 isn't connected to anything real within 25s, bring
  up the AP. Verified live: ran the script directly while already on the
  home network — it correctly detected that and exited without touching
  anything ("wlan0 connected to 'netplan-wlan0-TATA_3071' - staying on
  this network").
- **`sta-mode.sh` now falls back to the AP** if the home network can't
  actually be reached (waits up to 15s, checks, falls back) — the
  "Pi came back to AP" behavior explicitly requested, so neither the
  boot-time path nor a manual/UI-triggered switch to STA can strand the
  device with no network until a reboot.
- **Manual toggle in the web UI**: a "Network" card showing live status
  (polled via new `/network/status`) and a "Switch" button
  (`/network/toggle`) with mode-specific confirmation wording, since
  flipping modes drops whatever connection is viewing the page right then.
  The always-on Flask process (runs as `pi`, no root) can't change network
  state itself; `deploy/setup_sudoers.sh` installs a sudoers.d rule
  (validated with `visudo -c`) granting passwordless `sudo` for exactly
  `ap-mode.sh`/`sta-mode.sh` — nothing else — so no root password needs to
  live in the app. The toggle route launches the script with
  `subprocess.Popen` (not a blocking call) since the request triggering it
  may be arriving over the very connection about to be torn down.

### Verified live on 192.168.1.35

- `GET /network/status` → correctly reported
  `{"mode":"sta","connection":"netplan-wlan0-TATA_3071"}`.
- `sudo -l` as the `pi` user confirmed passwordless access to exactly
  `/home/pi/astro-pi-cam/deploy/ap-mode.sh` and `.../sta-mode.sh` — the
  same absolute paths `config.AP_MODE_SCRIPT`/`STA_MODE_SCRIPT` resolve to.
- **Deliberately not tested**: actually POSTing `/network/toggle`, or
  running `ap-mode.sh`/`sta-mode.sh` for real. Doing so from this session
  would drop the very SSH connection (over the home network) being used to
  verify it, with no physical access to recover if anything went wrong.
  This needs a hands-on test — click "Switch" from a phone connected to
  the camera, confirm it lands on the `AstroCamera` AP, then switch back.

## 2026-10-02 — Network control moved into the nav bar as a toggle switch

Replaced the standalone "Network" card (status line + "Switch" button)
with a compact iOS-style toggle switch in the header nav, next to the
Gallery link — same `/network/status`/`/network/toggle` endpoints, just a
smaller, always-visible control instead of a dedicated section. Checked =
AP mode, unchecked = WiFi/STA; the label next to it shows "AP", "WiFi", or
"—"/"?" for disconnected/unknown. Same confirmation-before-switching
behavior as before, now triggered on the checkbox's `change` event with
`fetch()` instead of a form submit — reverts the checkbox back if the user
cancels the confirm dialog. A `networkToggleBusy` flag pauses the 5s status
poll for 8s after a switch so it doesn't fight with the user's own action
(or, if the switch drops this client's connection, just starts failing
silently, which is the expected outcome).

### Verified live on 192.168.1.35

- Old `<h2>Network</h2>` card and its elements confirmed gone from the
  served HTML; new toggle markup confirmed present.
- `/network/status` still responds correctly (unchanged backend route).
- As before, did not trigger an actual `/network/toggle` POST from this
  session — same reasoning (would drop the SSH connection used to verify
  it). Full interactive test (tap the switch, confirm, watch it land on
  the AP) still needs a hands-on check.

## 2026-10-02 — First real-world AP↔STA test; it worked, IP confusion diagnosed

User did the first hands-on test of the nav-bar toggle: switched to AP
successfully, then reported switching back to WiFi "didn't connect."

Investigated via logs rather than guessing:
`journalctl -t sudo` showed the full chain — `sta-mode.sh` triggered at
21:41:29, `nmcli connection down AstroPiCamAP` and
`nmcli connection up netplan-wlan0-TATA_3071` both completed cleanly, and
the connection's own timestamp confirmed it was up at 21:41:32 — about 3
seconds end to end, no errors. `/network/status` confirmed it was still
correctly on `sta` mode/the home network at the time of investigation.

**Actual cause**: not a reconnect failure — an address change. AP mode is
always `10.42.0.1`; STA mode's IP is whatever DHCP assigns
(`192.168.1.35` here). The WiFi itself reconnected in ~3s; the browser was
just still pointed at the now-dead `10.42.0.1`.

**Fix**: confirmed the Pi already runs `avahi-daemon` and answers to
`http://raspberrypi-2w.local:5000` via mDNS — tested live, works. Updated
the "switch to WiFi" confirmation dialog to mention this address
explicitly, and added a "Reaching the camera" section to docs/setup.md
explaining the IP-changes-with-mode behavior and recommending the mDNS
hostname over raw IPs when unsure which mode is active. Also fixed a
stale doc reference to the old "Network card" (now a nav-bar toggle, per
the previous change).

## 2026-10-02 — Captive-portal-style onboarding for the AP

Joining `AstroCamera` should feel like joining public WiFi — phone pops up
a "sign in to this network" prompt straight to the dashboard, no need to
know to open a browser and type an address.

Two halves, verified for real NetworkManager/dnsmasq support before
writing any code (checked `/etc/NetworkManager/dnsmasq-shared.d/` already
exists on-device, pre-created by the NM package — confirms it's an
actively supported hook, not a guess):

- **`deploy/setup_captive_portal.sh`**: drops
  `address=/#/10.42.0.1` into that directory — wildcard DNS override so
  every hostname an AP-mode client looks up resolves to the Pi. Validated
  the exact config line with `dnsmasq --test` before installing
  ("syntax check OK").
- **`app.py`**: `captive_portal_probe()` answers each OS's own
  connectivity-check URL (Apple `/hotspot-detect.html`, Android
  `/generate_204`, Windows `/connecttest.txt`, Firefox `/success.txt`,
  plus older variants) with a 302 to `/` instead of the exact "you have
  real internet" response each one expects — that mismatch is what makes
  the OS treat it as a captive portal and open a browser to the redirect
  target. A catch-all route extends this to any other unrecognized path.

### Verified live on 192.168.1.35

- `dnsmasq --test --conf-file=...` with the exact redirect line → "syntax
  check OK".
- All 7 named probe paths → HTTP 302, `Location: /`.
- Catch-all on an arbitrary nonsense path → HTTP 302, `Location: /`.
- **Regression check on the catch-all** (the risky part — a bare
  `/<path:_unused>` route could in principle shadow real routes): `/`,
  `/gallery`, `/session/status`, `/network/status`, `/static/style.css`,
  a real `/captures/<session>/<file>.jpg`, and `/gallery/<session>/download`
  all still returned 200 — confirmed Werkzeug's more-specific-route-wins
  behavior holds here, not just assumed from how routing is documented to
  work.
- DNS config file installed and confirmed present with the right content.

**Deliberately not tested**: whether a phone's OS actually pops the
captive-portal browser automatically on joining — that's a physical,
on-device OS behavior (iOS/Android/Windows network-join heuristics), not
something an HTTP client or this remote session can trigger or observe.
The config only takes effect the next time AP mode activates, not
retroactively on an already-running hotspot either. Needs an on-site test:
join `AstroCamera` fresh (forget/rejoin if the phone cached it as "no
internet" before this change) and see whether the sign-in prompt appears
on its own.

## 2026-10-03 — Diagnosed and fixed: AP-toggle could permanently stall the camera

User reported two things after the captive-portal change: the sign-in
prompt never appeared, and the preview was blank on AP mode. Investigated
both via logs rather than guessing, and the preview one turned out to
still be broken *right now*, in STA mode, well after the AP test ended.

**Captive portal**: `journalctl` showed zero requests to any of the known
probe paths during the test — not even failed ones. The phone's own
request log started directly at `GET /`. This means the phone's OS skipped
its connectivity check entirely, which happens for networks it's already
cached a "trust"/"no internet" verdict for — this phone had joined this
SSID for plain connectivity testing before the captive-portal config
existed. Not a server bug; needs "Forget This Network" + rejoin to force a
fresh check.

**Preview blank — a real, serious bug**: the Flask access log showed the
AP-connected phone's very first `GET /preview.mjpg` sitting silently
unanswered for ~25 seconds after the page loaded, and tracing back
further: the *dev browser that triggered the `/network/toggle`* had its
own `/preview.mjpg` connection open at that exact moment. The instant AP
mode activated, that connection's socket became unreachable, but
`stream_mjpeg`'s write to it doesn't find out right away — a write to a
half-open TCP connection can block for a long time before the OS gives up
retransmitting. That write was happening *inside* `AstroCamera`'s lock
(`with self._lock:`), so it blocked every other camera use — including the
phone's own preview — until the OS eventually noticed.

Confirmed this was **still live** when investigating (not just a historical
log artifact): `curl --max-time 8 .../preview.mjpg` over the home network,
well after the AP test had ended, returned nothing at all — 8s, zero
bytes, no response. Restarting `astro-pi-cam.service` cleared it
immediately (confirmed preview resumed at ~24fps right after), which
unblocked the user, followed by the actual fix:

- `capture_still`: `with self._lock:` → `self._lock.acquire(timeout=30)`,
  raising a new `CameraBusyError` on timeout (30s is generous — a real
  200s exposure legitimately holds the lock that long; this is a backstop
  against something *else* being stuck, not a complaint about long shots).
  `CaptureSession`'s existing generic exception handler already surfaces
  this as a normal status error, no changes needed there.
- `stream_mjpeg`: can't acquire its own lock early enough to matter — it's
  a generator, and its body doesn't run until first iterated, by which
  point Werkzeug has already committed to a 200 response. Split locking
  out into `AstroCamera.acquire()`/`release()`, called eagerly in
  `preview_stream()` *before* constructing the streaming `Response`, with
  a 5s timeout — a busy camera now gets a clean synchronous 503 instead of
  a silently hanging connection.
- `/network/toggle` now proactively bumps the preview-generation counter
  right before launching the switch script, so a still-responsive stream
  (hasn't started blocking yet) self-evicts immediately rather than
  relying on the write ever unblocking.

### Verified live on 192.168.1.35

- Reproduced the stuck state directly: `preview.mjpg` hung 8s with zero
  response, confirming the bug was real and still active, not just
  historical.
- Service restart cleared it; confirmed with a fresh request (52-71 frames
  over 3s, normal ~24fps).
- Post-fix regression: preview streams normally, a real capture session
  (ISO 100, 1s exposure) completes with no error.

**Not yet verified**: the actual fix under the original failure
condition — deliberately leaving a preview open on one client while
triggering `/network/toggle` from it, confirming a second client gets
either a fast connection or a clean 503 instead of a long hang. Needs
another on-site AP test to confirm end to end.

## 2026-10-03 — `.deb` packaging for fresh-device installs

Added `packaging/` to replace the multi-step manual setup sequence with
one installable package for a brand-new Pi Zero 2 W:

- `packaging/build-deb.sh`: assembles `app/`+`deploy/` under
  `home/pi/astro-pi-cam/`, the two systemd units at their final
  `etc/systemd/system/` path (so dpkg tracks/removes them natively instead
  of `postinst` copying them in by hand), and `DEBIAN/control` (version
  substituted from the new `VERSION` file at repo root) into
  `astro-pi-cam_<version>_all.deb` via `dpkg-deb --build`.
- `packaging/control.in`: declares `Depends: python3-picamera2,
  python3-flask, python3-pil, network-manager, dnsmasq-base,
  avahi-daemon, sudo` — `apt install ./<file>.deb` resolves these
  automatically on a system that doesn't have them yet.
- `packaging/postinst`/`prerm`/`postrm`: **call the existing
  `deploy/setup_ap_profile.sh`/`setup_sudoers.sh`/`setup_captive_portal.sh`
  scripts** rather than reimplementing their logic — one definition of
  each setup step, whether run by hand (docs/setup.md) or by the package.
  `postrm` only touches network/privilege state on an explicit `purge`,
  and never deletes captured photos even then.

### Verified live on 192.168.1.35 (built and actually installed, not just read)

- Pushed the source tree to a scratch location on the Pi, ran
  `build-deb.sh` there (needs `dpkg-deb`, not available on the Windows
  dev machine) — built cleanly.
- `dpkg-deb --info`/`--contents` confirmed correct metadata and file
  layout before installing anything.
- `dpkg -i` over the **already-configured** live system (the closest
  safe proxy for a fresh install, since wiping the device to test a
  truly blank one isn't reasonable): clean install, zero errors. Exercised
  every idempotent setup path for real — AP profile recreated (not
  errored) since one already existed, sudoers rule re-validated and
  reinstalled, captive-portal config reinstalled.
- `dpkg -l` showed `ii` (correctly installed), both services `active` and
  `enabled`.
- **Confirmed existing captures survive a reinstall untouched** — three
  prior test sessions' photos were still present and correctly owned
  afterward.
- Full functional pass post-install: `/`, `/gallery`, `/network/status`
  all correct, real MJPEG preview streaming normally.

### A real regression, caught and fixed before it shipped quietly

The first post-install functional check found `/preview.mjpg` returning
**zero bytes** — not a test artifact, confirmed by checking the Pi's own
loopback directly. Traced to an ordering bug in the camera-lock fix from
earlier today: `preview_stream()` was calling `camera.acquire(timeout=5)`
*before* `_next_preview_generation()`, but the generation bump is the only
thing that tells an existing stream to let go of the lock. With acquire
going first, a new request would just wait out its own timeout for a lock
that was never going to be released — and critically, the generation bump
that *would* have released it never happened either, since it was
sequenced after the now-failed acquire. The net effect: the very first
preview connection after a restart would permanently wedge the camera,
with every subsequent request 503'ing forever instead of recovering.

Fixed by reordering: bump the generation first (cheap, unconditional),
*then* attempt to acquire. Verified directly against the failure mode,
not just by re-reading the diff: opened a long-lived preview connection,
started a second one 1s later, and confirmed the second got a clean 200
(not a 503 loop) while the first was cut off mid-stream — the exact
eviction behavior that was broken. Rebuilt the `.deb` with the corrected
source and reinstalled it; confirmed the installed `app.py` has the fixed
ordering and a fresh restart serves preview normally.

## 2026-10-03 — Reboot / shut down from the web UI

Power icon in the nav bar opens a small `<details>` menu (no JS needed to
open/close) with Reboot and Shut down. `POST /system/reboot` and
`/system/shutdown` (`app.py: _power_action`) launch
`sudo -n /usr/bin/systemctl reboot|poweroff` fire-and-forget, and refuse
with 409 while a capture is running (a reboot mid-sequence would silently
lose the rest of it). `deploy/setup_sudoers.sh` now grants NOPASSWD for
exactly those two `systemctl` invocations in addition to the mode scripts
— sudoers matches arguments, so `systemctl` in general stays
password-protected. Since the package's `postinst` runs this same script,
the `.deb` picks it up with no packaging changes.

Confirmations spell out consequences: reboot = ~1 min offline and WiFi
mode re-chosen at boot (may come back as the hotspot); shutdown = no way
back from the page (Zero 2 W has no power button, must replug) and wait for
the green LED to stop flashing before unplugging. After confirming, a
full-page overlay says the same; for reboot the page waits 20s (so the
still-dying old process isn't mistaken for "back"), then polls and reloads.

### Verified live on 192.168.1.35

- `sudo -n -l` as `pi`: the NOPASSWD set is exactly `ap-mode.sh`,
  `sta-mode.sh`, `systemctl reboot`, `systemctl poweroff`. (A first attempt
  at a negative control — `sudo -n -l /usr/bin/systemctl stop ssh` —
  reported "allowed", but that was a flawed test: `pi` already has stock
  password-protected full sudo, and `-l <cmd>` checks policy, not whether
  a password is needed. Checked the NOPASSWD listing directly instead.)
- Started a 6s capture, POSTed `/system/reboot` mid-exposure → 409 "A
  capture sequence is running"; the capture then completed normally
  (`completed: 1`, no error). Only tested the guard via the reboot route —
  both routes share `_power_action`, and if the guard had been broken a
  reboot recovers itself while a shutdown would need a physical replug.
- Power menu, overlay, and both route URLs present in the served page.

**Not tested**: an actual reboot or shutdown, or the reload-after-reboot
polling — both would take the device offline from this session.

## 2026-10-03 — Nav-bar WiFi toggle sat higher than the other nav items

Cause: the toggle is a `<label>`, so it picked up the global
`label { margin-bottom: 0.75rem }` meant for form fields; `nav`'s
`align-items: center` centers the margin box, so that margin pushed it up
~6px relative to Gallery and the power icon. Fixed with
`margin-bottom: 0` on `.network-toggle`. Confirmed the deployed stylesheet
has it; visual check is on the user's side (no browser here).

## 2026-10-03 — Bigger, centered preview

Preview was a fixed 240px, left-aligned. Now it fills the card width up to
480px — the stream's native width (`PREVIEW_STREAM_SIZE`), so it's never
upscaled — keeps a 4:3 `aspect-ratio`, and is centered (`margin: 0 auto`).
The placeholder matches the same box so nothing jumps when it swaps in.
`.preview-wrap` stays exactly the image's size, so the crosshair overlay
still lands on the true center.

The same screenshot showed the preview as a solid black box, so checked
the stream rather than assuming: pulled 3s from `/preview.mjpg` on the
Pi's loopback — 62 frames, and an extracted frame showed a real scene.
Stream was healthy; the black box was the client side (likely a stale
connection from before a service restart).

## 2026-10-03 — First GitHub release: v1.0.0

https://github.com/aviralverma-8877/picamera/releases/tag/v1.0.0 — tag
on `14bc5f3`, with `astro-pi-cam_1.0.0_all.deb` attached.

- Built the package on the Pi from the committed source, then
  `dpkg -i`'d that exact file: clean install, both services active,
  preview streaming, dashboard 200.
- Installed `gh` (winget, v2.102.0) and logged in via the browser
  device-code flow. First `gh release create` was rejected (HTTP 422):
  `--target` needs the full commit SHA, not a short one. Retried with the
  full SHA after confirming it matched `origin/main`.
- Downloaded the published asset back from GitHub and compared SHA-256
  with the tested build: identical
  (`a0703fecab6f9075f057b4f51bafcdee3f8822f99e9867bffcce5cd99edf9e2a`).
- `*.deb` added to `.gitignore`; release packages live on GitHub releases,
  not in the repo. Local and Pi-side build copies deleted.
- README now installs from the release download instead of a local build.

For future releases: bump `VERSION`, rebuild with
`packaging/build-deb.sh`, test-install, then
`gh release create v<version> <file>.deb --target <full sha>`.

## 2026-10-03 — v1.0.1: captive portal fixed for Android (app moves to port 80)

User reported the captive portal still didn't work on the hotspot from an
Android phone. Root cause, found on the device rather than guessed:
`ss -ltnp` showed **nothing listening on port 80** — only the app on 5000.
Android's connectivity check is plain `http://` on port 80, so the probe
reached the Pi (DNS worked) and was refused, which Android reads as "no
internet", not "captive portal". Every earlier captive-portal test called
the probe paths on `:5000`, which is how it slipped through.

- The app now listens on port 80 (user OK'd dropping 5000). Runs as `pi`
  still: `AmbientCapabilities=CAP_NET_BIND_SERVICE` in the unit, not root.
  Addresses are now plain `http://10.42.0.1` / `http://raspberrypi-2w.local`;
  updated in the UI dialogs, `ap-mode.sh`, and docs.
- Probes arriving under a hijacked hostname now redirect to the absolute
  `http://10.42.0.1/` instead of a relative `/`, so the phone's sign-in
  window lands on the real address.
- **Upgrade bug fixed in `postinst`**: `systemctl enable --now` doesn't
  restart an already-running service, so upgrading 1.0.0 → 1.0.1 would have
  installed the new files but kept the old code (on port 5000) running
  until a reboot. Now `restart`s the app; the WiFi failover oneshot is only
  `start`ed (a no-op on upgrade, so it doesn't re-decide network mode).

### Verified live on 192.168.1.35

- Port 80 owned by the app process running as `pi`; old port 5000 closed;
  dashboard 200; preview streaming on 80.
- Probe sent with Android's real Host header
  (`connectivitycheck.gstatic.com`) to `/generate_204` and `/gen_204` →
  302 `Location: http://10.42.0.1/`. Same path under our own name → 302 `/`.
- Confirmed NetworkManager passes `--conf-dir=/etc/NetworkManager/
  dnsmasq-shared.d` to the hotspot's dnsmasq (string in its binary), then
  started a throwaway dnsmasq on a spare port with that same conf-dir:
  `connectivitycheck.gstatic.com`, `www.google.com`, `captive.apple.com`,
  `anything.example` all → `10.42.0.1`.

**Not verified**: Android actually popping the sign-in prompt — needs a
phone on the hotspot. If it doesn't, check the phone's Private DNS setting
(a fixed provider like `dns.google` bypasses the hotspot's DNS) and
forget/rejoin `AstroCamera` so it re-checks.

## 2026-10-03 — v1.0.2: signed apt repository, `apt upgrade` for updates

- New GPG signing key (RSA 4096, sign-only, fingerprint
  `67089F94EA0D468F827F8E8E29F2793AD8DB1024`). Private half: the
  `APT_SIGNING_KEY` repo secret (piped straight in, never written to a
  file) and the maintainer's local GPG keyring. Public half:
  `packaging/astro-pi-cam-archive-keyring.gpg`, marked `binary` in
  `.gitattributes` so line-ending normalization can't corrupt it (checked:
  identical hash in the working tree and in the commit).
- The `.deb` now installs `/etc/apt/sources.list.d/astro-pi-cam.sources`
  (flat repository, `Suites: ./`) and the key under `/usr/share/keyrings/`.
- `.github/workflows/release.yml`, on `v*` tags: checks the tag matches
  `VERSION`, builds the `.deb`, creates the GitHub release, then
  regenerates `Packages`/`Release` with `apt-ftparchive`, signs
  `InRelease`/`Release.gpg`, and pushes to `gh-pages`. GitHub Pages
  enabled on that branch (one-time API call).

### Verified end to end

- Tagged `v1.0.2`: the workflow passed in 8s, the release was created, and
  `gh-pages` held the signed repository. Pages served `InRelease`; its
  checksums cover only `Packages`/`Packages.gz`, so no stale `Release` file
  got hashed into it.
- **The real upgrade path on the Pi** (had 1.0.1, no repository): added
  the same source and key 1.0.2 ships, under test names. `apt-get update`
  fetched and verified the repository, `apt-cache policy` showed candidate
  1.0.2 from it, and `apt-get install --only-upgrade astro-pi-cam`
  upgraded 1.0.1 → 1.0.2 from GitHub Pages. The app restarted onto the new
  version (PID changed), both services stayed active, and the dashboard and
  preview worked. The package then owns its own source and key (`dpkg -S`).
  With the test files removed, `apt update` is clean using only the
  package's own source.
- **Signature enforcement**: pointed a temporary source at the repository
  with the wrong key → apt refused ("Missing key 67089F94…") and kept its
  old index. Restored afterwards.
- GitHub release asset and apt repository serve the identical file
  (SHA-256 `110ea1c3…`), the one the Pi installed.

Used `--only-upgrade astro-pi-cam` rather than a full `apt upgrade` so
the test didn't upgrade unrelated system packages on the device.

`actions/checkout` bumped v4 → v5 afterwards (v4 runs on deprecated
Node 20); first exercised on the next release.

## 2026-10-03 — Captive portal finally working on Android (hotspot moved to 4.3.2.1)

Even after the port-80 fix, the user's Galaxy S25 Ultra showed no sign-in
prompt. Diagnosed in three rounds, each from evidence, not guesses:

1. **App log:** after the phone joined, no `/generate_204` ever arrived,
   so the check wasn't being mishandled — it wasn't being sent.
2. **On-Pi hotspot diagnostic** (a detached script: switch to AP, capture
   NM's dnsmasq command line from `/proc`, query the hotspot's DNS
   locally, switch back): confirmed NM loads
   `--conf-dir=/etc/NetworkManager/dnsmasq-shared.d` and IPv4 lookups got
   `10.42.0.1`, but **IPv6 (AAAA) lookups got REFUSED** (no upstream
   servers). Reproduced in a throwaway dnsmasq with NM's exact flags,
   tested candidate configs: `filter-AAAA` didn't help; `local=/#/` gave a
   clean NOERROR/no-data. Applied it, confirmed on the real hotspot.
   **Still no prompt.**
3. **Temporary dnsmasq `log-queries`** during the next phone test: the
   phone asked only for A records (so REFUSED-on-AAAA wasn't the blocker),
   repeatedly resolved `connectivitycheck.gstatic.com` → `10.42.0.1`, and
   still never sent the HTTP check. That matches Android's network stack
   skipping the HTTP probe when the check hostname resolves to a private
   address. Moved the hotspot to `4.3.2.1/24` (`ipv4.addresses` on the
   nmcli profile, `AP_GATEWAY_IP`, DNS answer, redirect target, UI text,
   docs); verified on the real hotspot (DHCP range `4.3.2.10–254`, DNS →
   `4.3.2.1`, probe → 302 `http://4.3.2.1/`).

**Confirmed by the user on the phone: the sign-in prompt appears.** The
app log for that test shows `GET /generate_204` from `4.3.2.124` → 302,
then `GET /` a second later — the first time the check ever arrived.
Temporary query logging removed afterwards.

Hotspot dashboard address is now `http://4.3.2.1` (was `10.42.0.1`).

### Re-published as v1.0.2 (per user: no new release)

Release workflow now replaces the asset on an existing release
(`gh release upload --clobber`) instead of failing on `gh release create`,
so moving the `v1.0.2` tag to the fixed commit rebuilds and re-publishes
v1.0.2 and the apt repository in place. Side effect: devices that already
had the earlier 1.0.2 won't get this via `apt upgrade` (same version); they
need `apt install --reinstall astro-pi-cam`. Only the test Pi was affected.

## 2026-10-03 — "Rotate 180°" option (camera-native flip)

User asked for a smooth 0–360° rotation slider for preview and capture.
Before building it they asked to check whether the HQ Camera can rotate
itself. Tested on-device: libcamera offers no rotation control; only
flips are supported (h, v, and both = 180°), and a requested transpose
(needed for 90°/270°) is silently changed to a 180° flip.

The slider was prototyped with software rotation, but profiling on the Pi
showed it was too slow for sequences: decode 0.5s, rotate 10.0s (bicubic)
/ 4.8s (bilinear) / 0.6s (nearest, visibly jagged), encode 0.5s per 12MP
frame — it would have stretched the shot interval. The user chose to
drop it and keep only the camera's own 180° flip; the slider changes were
reverted (they had never been committed).

Implemented: a "Rotate 180°" checkbox in Capture sequence.
`camera.py` passes `Transform(hflip=1, vflip=1)` when configuring both the
preview stream and still captures; `CaptureSession` and the preview route
carry the setting; the checkbox is remembered across the post-start page
reload and restarts the preview when toggled.

### Verified live on 192.168.1.35

- Preview with and without the flip: extracted frames are 180° apart;
  60 frames per 3s either way.
- Real captures with raw DNG, flipped and not: JPEGs 180° apart, both
  finished in the same time (4.6s vs 4.5s — no cost), DNGs written, and
  the DNG CFAPattern changes from BGGR to RGGB, confirming the raw data is
  flipped by the sensor too.
- Checkbox stays ticked after starting a sequence.
- Test sessions deleted afterwards.

## 2026-10-03 — v1.0.3 released (Rotate 180°); first real apt-to-apt upgrade

Bumped `VERSION`, pushed tag `v1.0.3`; the release workflow built the
package, created the release, and published it to the apt repository in
one run. Release asset and apt repository serve the same file
(SHA-256 `e2bd58a2…`). Release notes rewritten by hand afterwards.

**Upgraded the test Pi from 1.0.2 to 1.0.3 purely through apt**
(`apt-get update` + `apt-get install --only-upgrade astro-pi-cam`): candidate
1.0.3 came from the GitHub Pages repository, the app restarted onto it,
both services active, the Rotate 180° box and flipped preview work. This
is the first upgrade between two published versions via the repository —
exactly the path installed cameras will use.

**Unexplained reboot during the release**: the first upgrade attempt lost
its SSH session because the Pi rebooted (uptime 0 min, boot 07:56); the
upgrade hadn't started and `dpkg --audit` was clean. The cause isn't
recoverable — journald on this image is volatile, so the previous boot's
logs are gone. Possible causes: the UI power menu, a power blip, or an
undervoltage reset (common on a Zero 2 W with a weak supply). Worth
enabling persistent journald (`/var/log/journal`) if this recurs. On the
upside, the Pi rejoined the home network by itself after booting — the
boot-time WiFi failover's "known network in range" branch, on a real boot.

### Not yet done

- An actual reboot via the UI (checks the reload polling and that the
  device comes back), and an actual shutdown (needs someone at the device
  to replug it).
- Whether the fix above actually resolves blank-preview-on-AP under the
  original conditions (preview left open across a mode switch) — needs a
  repeat on-site test.
- Whether the captive-portal popup fires after forgetting and rejoining
  the network fresh (see above) — needs the on-site test described above.
- AP↔STA toggle confirmed working on real hardware by the device owner
  (see above) — AP mode, and switching back, both verified via logs.
  Still untested: a capture sequence run *while* actually connected via
  the AP (phone on `AstroCamera`, not just toggling modes from an
  already-authenticated session), and the boot-time failover branch where
  no known network is in range at all (only the "already connected, do
  nothing" branch has been exercised).
- No real-sky test yet — all captures above were indoor test shots to
  verify the pipeline, not actual astrophotography.
- ST7789 display HAT is still out of scope (see architecture.md).

## 2026-10-07 — Scan for and join WiFi networks from the dashboard

New **WiFi** card on the control page: **Scan** lists nearby networks
(strongest first, one entry per SSID, the current one and saved ones
marked; enterprise/WEP shown but disabled), tap one to enter its password
and **Connect**, or **Other network…** to type a name (hidden networks,
or ones the scan missed). Covers both asks: changing the camera's network
(new router/SSID), and getting it off the hotspot when it's stuck in AP
mode.

- New `deploy/wifi.sh` (root) holds every WiFi mode change: `ap`, `home`,
  `scan`, `connect`. `ap-mode.sh`, `sta-mode.sh` and the boot failover now
  call it. This also removes the hard-coded home connection name
  (`netplan-wlan0-TATA_3071`) from the shipped scripts: "home" is now any
  saved WiFi profile, in NetworkManager's preference order.
- App routes: `GET /wifi/scan`, `POST /wifi/connect` (JSON, validated:
  SSID ≤ 32 bytes, password 8–63 chars or 64 hex, no newlines),
  `GET /wifi/status` (outcome of the last attempt, with its age computed
  on the Pi since its clock and the phone's may disagree).
- Sudoers rule gains exactly `wifi.sh scan` and `wifi.sh connect`.
- A failed join deletes its new profile, rejoins the previous network (or
  starts the AP) and leaves the reason for the page to show.
- The "switch to WiFi" confirmation now uses the device's real hostname
  instead of a hard-coded `raspberrypi-2w.local`.

**Moved to its own page** (at the user's request, like the gallery): a
WiFi icon in the dashboard's nav bar opens `/wifi` (`wifi.html`), which
scans as soon as it opens.

### Tested

Off-device first (Flask test client with nmcli stubbed, `node --check` on
both pages' scripts, `sh -n`/`dash -n` on the shell scripts), then **on
192.168.1.35**, deployed with `deploy.py` + `setup_sudoers.sh` re-run. The
switching steps ran as a script on the Pi itself (they cut off SSH),
logging to a file:

- Live scan through the app in STA mode: 1.6s, 8 networks, hidden ones
  filtered, the home network marked in use and saved. `nmcli -g` does
  *not* escape the SSID; the code handles either form.
- A — wrong password for the home network, from STA: failed with "Secrets
  were required", back on `TATA_3071`, original profile (same UUID)
  intact, no leftover temporary profile.
- B — toggle to AP: AP up in 2s; `/wifi/scan` returned the cached list
  (age 2s, 8 networks, nothing marked in use).
- C — from AP, join the saved home network with no password: connected
  in 8s.
- D — to AP, then wrong password from AP: failed in 18s, back on the AP,
  reason readable at `/wifi/status`.
- E — from AP, `sta-mode.sh`: rejoined home in 2s (found through the
  saved-profile search, no hard-coded name).
- F — typed (hidden) SSID that doesn't exist: failed in 32s with "could
  not be found", back on home.
- G — home network with its real password (read from NetworkManager on
  the Pi, never printed): connected in 8s, the old profile replaced by
  the new one (new UUID, still named `TATA_3071`, one profile only).
- `/wifi` page served, nav icon present; test files removed afterwards.

Found by the test and fixed: NetworkManager's "could not be found"
wording wasn't mapped to the friendly "network not found" message.

Also noticed: on this device the home profile is now called `TATA_3071`,
not `netplan-wlan0-TATA_3071` — so the old hard-coded `sta-mode.sh` was
already broken here; the saved-profile search fixes it. `/tmp` is tmpfs
(RAM) on this image, which matters for the session-zip download.

### Not yet done

- The page hasn't been looked at in a real browser (Chrome automation was
  unavailable): layout of the network list, the inline password form and
  the overlay on a phone.
- The full phone flow: phone on `AstroCamera`, join a network, phone
  follows it, and the failure reason shown after rejoining the hotspot.
- Boot-time failover through `wifi.sh ap` with no known network in range.

## 2026-10-07 — v1.0.4 released (WiFi page); test Pi upgraded via apt

Bumped `VERSION`, pushed tag `v1.0.4`; the release workflow built and
published the package in one run (9s), release notes written by hand
afterwards. The apt repository lists 1.0.2–1.0.4.

Upgraded the test Pi from the deploy.py copy to the 1.0.4 package with
`apt-get install --only-upgrade astro-pi-cam` (repository index refreshed
first): installed cleanly; both services active; `postinst` re-ran
`setup_sudoers.sh`, so the rule includes `wifi.sh scan`/`connect` without
a manual step; `deploy/wifi.sh` is now owned by the package; the Pi
stayed on `TATA_3071` throughout. After the upgrade `/wifi` serves and
`/wifi/scan` does a live scan (8 networks, `TATA_3071` in use).

## 2026-10-07 — Telescope mount control over Bluetooth

Asked for: a Bluetooth section like the WiFi one — scan for a device and
connect to it — after which mount control buttons, slew speed and a
"reset to zero" button are enabled on the dashboard. The mount is an
iOptron SmartEQ Pro behind the ESP32 `SmartEQ-RJ9` adapter (the
`esp32-ioptron_smart_eq_controller` project), a transparent Bluetooth
SPP ⇄ RS-232 bridge.

- `app/mount.py` (new): `MountLink`, an RFCOMM socket to the adapter
  (channel 1) shared by every page, with the iOptron moves, stops, slew
  rate, go-to-zero and `:GAS#`/`:GEC#` status; a watchdog stops any
  move whose button stopped being held (1s lease, renewed every 250ms by
  the page). Scanning is `bluetoothctl scan bredr`.
- `/bluetooth` page (new, nav-bar icon): last-used adapter listed
  instantly, ~8s scan for the rest, tap to connect, Disconnect.
- Dashboard "Mount" card, under the preview: status line (state,
  tracking, RA/Dec), slew speed 1x–Max, N/S/E/W hold-to-move pad with
  Stop in the middle, Go to zero position (with a confirm). Disabled
  until a mount is connected.
- `deploy/setup_sudoers.sh`: adds exactly `rfkill unblock bluetooth` —
  the test Pi boots with Bluetooth rfkill-blocked, and that's the only
  step needing root. `packaging/control.in` now depends on `bluez` and
  `rfkill`.
- "Reset to zero" is implemented as **go to the zero position** (`:MH#`),
  not "set the current position as zero" (`:SZP#`), which would silently
  corrupt the mount's alignment if pressed by mistake.

### Verified live on 192.168.1.35 (real mount, moved with the owner's OK)

- Before writing code, by hand over RFCOMM as the unprivileged `pi`
  user: connected on channel 1 in ~1.4s, BlueZ paired by itself;
  `:MountInfo#` → `0011`, `:V#` → `V1.00#`; `:SRn#`, `:qR#`, `:qD#`,
  `:q#` each reply `1`. A connect also works when BlueZ has forgotten the
  device (it isn't bonded), which is what makes one-tap reconnect work.
- Deployed with `deploy.py`, re-ran `setup_sudoers.sh`. With the sudo
  cache cleared (`sudo -k` — this image sets `timestamp_type=global`, so
  an earlier interactive sudo would otherwise mask the rule): `rfkill
  unblock bluetooth` allowed, `rfkill block bluetooth` refused.
- Through the app's HTTP API, starting from Bluetooth blocked and
  powered off: scan unblocked it, powered it on and listed
  `SmartEQ-RJ9` (~9s); connect 1.7s; status decoded (Stopped, Sidereal,
  64x, RA/Dec); rate changes show up in `:GAS#`; bad rate/direction → 400.
- Motion, at 8x, 1.5s holds with 250ms renewals: N raised Dec
  (+66°56′24″ → +67°00′01″), S brought it back; E and W each moved RA
  ~10s beyond the normal drift, in opposite directions; each stop
  replied OK and the state went Slewing → Stopped.
- Watchdog: a single move with no renewal and no stop — Slewing at
  +0.4s, Stopped by +1.6s.
- Go to zero: Slewing for ~15s, ending at Dec +90°00′00″, state "At zero
  position". Slew rate set back to 64x afterwards; the mount was left at
  its zero position and the app left connected to it.
- Both pages' inline scripts pass `node --check`; `MountLink` was also
  exercised locally against a fake mount on a socketpair (single move
  command per hold, stop ~1s after the last renewal, a stray `1` after a
  move not taken as the next reply, link loss detected).

### Not yet done

- The pages haven't been looked at in a real phone browser (no browser
  on the dev machine): the D-pad layout, and press-and-hold behaviour on
  touch (`touch-action: none`, pointer capture).
- E/W sense: `:me#` lowered the reported RA on this mount (scope turned
  toward the western sky). Kept as the mount's own naming; confirm in the
  field whether the labels feel right.
- No automatic reconnect after an app restart or reboot.

## 2026-10-07 — Fixed: mount Bluetooth dropping ~30s after connecting

Reported: "Bluetooth keeps disconnecting after some time". The app log
had `Mount link lost: [Errno 103] Software caused connection abort` 29s
after the connect — the Pi's own stack closing the link, not the
adapter.

Reproduced on the test Pi through the app's API:

- Connect *while a scan is running* (what the Bluetooth page does when
  you tap the adapter within ~8s of opening it): dropped after 29s, every
  time.
- Connect right after a scan, or 15s after one: still up after 92s / 61s.
- `bluetoothctl trust` after connecting: still dropped (28s).
- Explicit `bluetoothctl --agent NoInputNoOutput pair`: "Pairing
  successful", then `Paired: no` the moment the link closed — the
  adapter's pairing doesn't bond, so bonding can't be the fix.

Cause: BlueZ deletes a scan-discovered device 30s after it was last seen
(`TemporaryTimeout`, default 30), and a connection made mid-scan never
makes it permanent; deleting the device takes the link with it.

Fix (`app/mount.py`): connecting now takes the scan lock, so it waits for
a running scan to finish. Plus, for drops with other causes, the watchdog
detects a closed socket within 0.1s even while idle, and the app
reconnects in the background every 5s for up to 2 minutes (not after the
user's own Disconnect); the Mount card and Bluetooth page show
"reconnecting…".

### Verified live on 192.168.1.35

- The failing scenario again (connect requested 3s into a scan): the
  app log shows the connect completing 0.7s after the scan ended; still
  connected after 93s.
- Forced drop (`bluetoothctl disconnect` on the Pi): status showed
  "reconnecting" at once and was connected again ~8s later.
- Locally against a fake mount: idle drop detected, reconnect succeeds,
  no reconnect after a user disconnect, gives up after the time limit.

## 2026-10-07 — The real fix for the mount disconnects: bond the adapter

Reported after the fix above: "still disconnecting, but re-connecting".
The log showed a drop at 16:30:35, 23s after a scan the Bluetooth page
started when opened while already connected. So it wasn't only connects
made mid-scan: *any* scan made BlueZ drop the link ~25-30s later.

- Scan while connected (no other change): dropped 24s after the scan.
- `bluetoothctl trust` while connected, then scan: dropped 25s after.
- Watched with `bluetoothctl info` during the drop: the device object is
  deleted at that moment — BlueZ's temporary-device cleanup
  (`TemporaryTimeout`, 30s; setting it to 0 would mean "never keep").
- The earlier "it doesn't bond" conclusion was wrong: the Pi's adapter
  was `Pairable: no`, and a non-pairable BlueZ pairs without bonding.
  With `bluetoothctl pairable on`, the same `pair` gave `Bonded: yes`, and
  a scan while connected no longer dropped anything (50s watched).

Fix: `mount._ensure_bonded`, run on connect when the adapter isn't bonded
yet (pairable on → scan if BlueZ doesn't know the device → pair with a
NoInputNoOutput agent → pairable off), plus a single remove-and-re-pair
retry if a connect with a stored bond fails (adapter reflashed). Connects
still wait for a running scan, and auto-reconnect stays for other drops.

### Verified live on 192.168.1.35

- From scratch (`bluetoothctl remove`, device unknown to BlueZ), connect
  through the app: 29s (scan + pair + connect); afterwards `Bonded: yes`,
  `Connected: yes`, `Pairable: no`.
- Scan while connected: no drop in 50s.
- Not exercised: the stale-bond re-pair path (needs the adapter
  reflashed).

## 2026-10-07 — v1.0.5 released (mount control over Bluetooth); test Pi upgraded via apt

Committed the Bluetooth mount control work, bumped `VERSION`, pushed tag
`v1.0.5`; the release workflow built and published the package in one
run (11s). The apt index listed 1.0.5 within a minute.

The hand-written release notes couldn't be applied from the dev machine
(`gh release edit` → HTTP 403: its token can push tags but not edit
releases), so the release still has the auto-generated notes; replace
them from the GitHub web UI.

Upgraded the test Pi from the deploy.py copy to the package with
`apt-get install --only-upgrade astro-pi-cam`: installed cleanly; both
services active; `postinst` re-ran `setup_sudoers.sh`, so the rule now
includes `rfkill unblock bluetooth`; the Pi stayed on `TATA_3071`.
Afterwards the Bluetooth page's list showed `SmartEQ-RJ9` as last used,
and connecting through the packaged app took 2s, reusing the bond (mount
at its zero position, RA/Dec read back).

## 2026-10-07 — Mount controls on the live preview

Reported: on a phone you have to scroll between the preview and the
Mount card to see the frame while moving the mount.

While a mount is connected, the dashboard now overlays the move controls
on the preview: N/S/E/W buttons (48px, translucent) at the edge
midpoints, clear of the center and crosshair; the slew speed selector
top-left; Stop bottom-right. Mount errors also show right under the
preview. The Mount card is unchanged (and keeps Go to zero and the
readout); both sets of buttons use the same press-and-hold code, and the
two speed selectors stay in sync.

### Checked on 192.168.1.35

- Copied `index.html`/`style.css` and restarted the app (Flask caches
  templates): the served page has the overlay, all 8 direction buttons
  and both speed selectors; its script passes `node --check`. Reconnected
  the mount afterwards.
- Not yet seen on a real phone (no browser on the dev machine): the
  overlay's look over the stream, and that holding an edge button doesn't
  scroll the page.

## 2026-10-07 — Mount card removed; everything on the preview

Per the user: drop the separate Mount section and show its details in
the Focus section, with the zero button at the preview's bottom-left.

- The Mount card (with its own D-pad and speed selector) is gone. Under
  the preview: the connection line with a Bluetooth link, the state and
  RA/Dec readout, a one-line hint, and mount errors.
- Go to zero moved onto the preview as a "Zero" pill, bottom-left
  (opposite Stop), still confirmed before it slews. Only one copy now.
- The Bluetooth page's link after connecting now reads "Back to
  preview".

Checked on 192.168.1.35 after a restart: the served dashboard has no
Mount card, one overlay with the four directions, speed, Zero and Stop;
both pages' scripts pass `node --check`; mount reconnected afterwards.
Still not seen on a real phone.

## 2026-10-07 — Sticky nav bar; one Bluetooth link

- The header (title + nav) now stays pinned to the top while scrolling,
  on every page (`position: sticky`, z-index below the full-screen
  overlays and above the preview's controls).
- Removed the second Bluetooth link under the preview; the nav-bar icon
  is the one way in, and the "no mount connected" line points to it.

Deployed to 192.168.1.35 and restarted; served page checked (one
Bluetooth link, script passes `node --check`); mount reconnected.

### Re-published as v1.0.5 (per user: update the latest release, no new one)

Moved the `v1.0.5` tag from `a5b897a` to `c739c7b` (preview overlay,
Zero button, sticky nav bar) and force-pushed it; the release workflow
swapped the new `.deb` into the existing v1.0.5 release and re-published
the apt repository. As with the v1.0.2 re-publish, devices that already
had the earlier 1.0.5 won't get this through `apt upgrade` (same
version): they need `sudo apt install --reinstall astro-pi-cam`. Done on
the test Pi: it pulled the new build (its sha256 matched the release
asset), both services active, the installed CSS has the sticky header;
mount reconnected afterwards. The release notes are still the
auto-generated ones (see above).

## 2026-10-07 — Track button on the preview

Asked for: once a star is framed by hand, a button (top-right of the
preview) that makes the mount follow the earth's rotation to keep it in
frame.

- `MountLink.set_tracking()` → `:ST1#` / `:ST0#`; `POST /mount/track`;
  status gains `tracking_on` (`:GAS#` state Tracking, Tracking+PEC or
  Guiding).
- Preview overlay, top-right: "Track" pill; tap to start, it turns green
  and reads "Tracking"; tap again to stop. Reflects the mount's own state
  on each status poll, except mid-move.

### Verified on the real mount (192.168.1.35)

- Stopped: RA drifted 4s over 6s (the sky turning past a still mount).
- `:ST1#` → `1`, state Tracking, and RA held at 07h 40m 19s over 6s —
  the pointing follows the sky.
- 1s nudge E at 8x while tracking, then the release's `:qR#`: still
  Tracking (RA moved 5s from the nudge, then held).
- Stop (`:q#`) while tracking: still Tracking — Stop halts moves and
  slews, not tracking; the UI hint says so.
- `:ST0#` → `1`, state back to Stopped. Left the mount stopped, slew rate
  as it was (8x).
- Served page has the button inside the overlay, script passes
  `node --check`. Not yet tried on a phone.

## 2026-10-08 — Dashboard keeps the phone's screen on

Asked for: the phone shouldn't auto-lock while the dashboard is open, so
long sequences and tracking stay visible.

- Screen Wake Lock API when the browser offers it — which it doesn't on
  plain http pages like this one (secure contexts only).
- Fallback: `app/static/keep-awake.mp4` (generated with ffmpeg: 2s,
  32x32 black H.264 baseline + silent AAC, 2.5KB, `+faststart`) played on
  a loop by an off-DOM `<video playsinline>`, as NoSleep.js does. Every
  tap retries until it's playing (needs a user gesture), and so does
  returning to the page. `.gitattributes` marks `*.mp4` binary.

Checked on 192.168.1.35 after a restart: the video serves as
`video/mp4`, and range requests (iOS needs them) get `206` with
`Accept-Ranges: bytes`; the dashboard script passes `node --check`;
mount reconnected afterwards.

**Not verified**: that phones actually stay awake — that needs a real
phone left on the dashboard past its auto-lock time (iPhone Safari and
Android Chrome both). Also whether the silent audio track pauses music
playing on the phone.

## 2026-10-08 — Release notes from the repo; v1.0.5 re-published again

Editing release notes from the dev machine kept failing with HTTP 403:
its `gh` login is a fine-grained token with admin on the repo but
without release write access (git pushes go over SSH). Instead of
needing a new token, release notes now live in the repo as
`docs/release-notes/<tag>.md`, and the release workflow applies them with
its own token — on a new release (`--notes-file`, falling back to
generated notes when there's no file) and on a re-publish (`gh release
edit --notes-file`). `docs/release-notes/v1.0.5.md` covers everything
now in 1.0.5: Bluetooth mount control, the preview controls (including
Track and Zero), screen-stays-on, and the sticky nav bar.

Committed the Track button and keep-awake work, moved `v1.0.5` to the
new commit and re-published it.

Result: the workflow run succeeded; the v1.0.5 release page now shows
`docs/release-notes/v1.0.5.md` (identical apart from a trailing newline)
and the rebuilt `.deb`. The test Pi pulled it with `apt install
--reinstall astro-pi-cam` (sha256 matched the release asset): both
services active, `keep-awake.mp4` and the Track button installed; mount
reconnected afterwards.

## 2026-10-08 — Re-mappable direction buttons

Reported: the N/S/E/W buttons don't move the preview the way they point,
because of how the camera is mounted.

- The edge buttons are now image directions (`data-pos` up/down/left/
  right) and the page maps them to mount directions with three switches
  under the preview ("Button directions"): swap up/down with left/right,
  reverse up/down, reverse left/right. Labels show the mount direction
  each button currently sends.
- Saved on the Pi (`app/mount-orientation.json`, gitignored) via
  `POST /mount/orientation`, rendered into the dashboard; also records the
  "Rotate 180°" state so toggling it later keeps the buttons right.

### Checked

- Mapping functions, taken from the template and run in Node: all 8
  switch combinations give 8 distinct layouts, each using N/S/E/W once
  with opposites on opposite edges; with Rotate 180° toggled each layout
  inverts exactly (up↔down, left↔right); saved and viewed both rotated →
  no correction.
- On 192.168.1.35: default in the page, save (unknown keys dropped),
  reload shows it, file on the Pi matches; reset back to default
  afterwards. Page script passes `node --check`; mount reconnected.
- Not done: calibrating against the actual image on a phone (needs
  someone watching the preview while holding the buttons).
