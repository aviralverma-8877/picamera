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

### Not yet done

- Whether the captive-portal popup actually fires on real phones (iOS,
  Android, and/or Windows) — needs the on-site test described above.
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
