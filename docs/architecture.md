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
  mount.py            MountLink: Bluetooth (RFCOMM) link to the telescope
                       mount's adapter, iOptron commands, move watchdog
  templates/           index.html (control form + live status + mount card),
                       gallery.html (browse/download past sessions),
                       wifi.html (scan for / join WiFi networks),
                       bluetooth.html (scan for / connect the mount adapter)
  static/style.css    mobile-first styling

deploy/
  astro-pi-cam.service          systemd unit, runs app.py
  astro-pi-wifi-failover.service systemd unit, boot-time AP/STA selection
  setup_pi.sh                   one-time: apt install picamera2/flask/pillow
  setup_ap_profile.sh           one-time: create (inactive) nmcli AP connection
  setup_sudoers.sh              one-time: passwordless sudo for the two mode scripts
  setup_captive_portal.sh       one-time: DNS wildcard redirect for AP clients
  wifi-failover.sh              run by the failover service at boot
  wifi.sh                       every WiFi mode change: start the AP, join the
                                 best saved network, scan, join a new network
  ap-mode.sh / sta-mode.sh      thin wrappers: `wifi.sh ap` / `wifi.sh home`
  deploy.py                     fast dev-iteration push (SFTP app/+deploy/, restart)

packaging/
  build-deb.sh    assembles astro-pi-cam_<version>_all.deb from the repo
  control.in      package metadata + Depends (version substituted at build time)
  postinst/prerm/postrm  maintainer scripts — run the deploy/setup_*.sh scripts
                         above, so there's one definition of each setup step
                         whether it's run by hand or by the package
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
- **A real `.deb` for fresh installs, not just a pile of setup scripts**:
  `packaging/build-deb.sh` assembles the whole app, both systemd units, and
  the three `deploy/setup_*.sh` scripts into one package whose
  `Depends:` (`python3-picamera2`, `python3-flask`, `python3-pil`,
  `network-manager`, `dnsmasq-base`, `avahi-daemon`, `sudo`) pulls in
  everything apt needs to, and whose `postinst` runs those same setup
  scripts rather than reimplementing their logic — one definition of "what
  the AP profile/sudoers rule/DNS override are" regardless of whether
  they're applied by hand or by the package. Verified by actually building
  and `dpkg -i`-installing it on the real device (not just reading the
  spec): confirmed a clean install with no errors, the AP/sudoers/DNS setup
  correctly idempotent on reinstall (recreates the AP connection rather
  than erroring on an existing one), both services active and enabled
  afterward, and — importantly — a capture directory already full of
  photos surviving the reinstall untouched. `postrm` only removes the
  network/privilege state on an explicit `apt purge`, and even then never
  touches captured images.
- **nmcli hotspot, not hostapd+dnsmasq**: NetworkManager already owns wlan0
  on this image; a hand-rolled hostapd/dnsmasq setup would fight NM for the
  interface. `nmcli` has native AP support that NM manages directly.
- **WiFi mode is chosen automatically at boot**: `astro-pi-wifi-failover.
  service` runs `deploy/wifi-failover.sh` once per boot, before
  `astro-pi-cam.service` starts. It leans entirely on NetworkManager's own
  autoconnect for the "at home" case (the home network profile already has
  `autoconnect: yes` — no code needed) and only adds the missing piece: if
  wlan0 isn't connected to a real network within 25s, it brings up the open
  `AstroCamera` AP (`AstroPiCamAP` connection, no password —
  `deploy/setup_ap_profile.sh` creates it the same way) itself.
- **...with a manual override from the web UI, not automatic switching
  while running**: the "Network" card's Switch button calls
  `/network/toggle`, which shells out to the same `ap-mode.sh`/
  `sta-mode.sh` scripts used for manual SSH switching — one source of
  truth for the actual nmcli commands either way. The Flask process itself
  (`User=pi`) has no privilege to change network state; a narrowly-scoped
  sudoers.d rule (`deploy/setup_sudoers.sh`) grants passwordless `sudo` for
  exactly those two script paths and nothing else, rather than running the
  whole app as root or storing a root password in it. The toggle route
  launches the script with `subprocess.Popen` (fire-and-forget) instead of
  waiting for it to finish, since the HTTP request triggering it may be
  arriving over the very connection the script is about to tear down.
  Switching *to* STA reuses `sta-mode.sh`'s built-in AP fallback (below),
  so clicking it can never strand the Pi with no network; switching to AP
  always succeeds (it's the Pi's own radio and profile, no external
  dependency to fail).
- **`sta-mode.sh` falls back to the AP if the home network isn't actually
  reachable**: whether triggered by hand, by reboot-time failover, or from
  the UI, "go to STA" wouldn't be safe to assume always succeeds — the Pi
  might not really be home, the router might be off, etc. So it waits up
  to 15s after bringing the home connection up, and if wlan0 still isn't
  actually connected to it, brings the AP back up instead of leaving the
  Pi with no network until a reboot.
- **Joining a WiFi network from the dashboard goes through one root
  script, `deploy/wifi.sh`**, which also now does the AP / home switching
  (`ap-mode.sh`, `sta-mode.sh` and the boot failover just call it). "Home"
  is no longer one hard-coded connection name: it's every saved WiFi
  profile except the AP, tried in NetworkManager's own preference order
  (autoconnect priority, then most recently used) — so a network joined
  from the dashboard is immediately what "switch to WiFi" and reboot use,
  and the package works on devices that never saw the original home
  network. The app may run only `wifi.sh scan` and `wifi.sh connect`
  (exact arguments in the sudoers rule); the SSID and password go to it
  over stdin, not argv, so they don't show in `ps` for the long-running
  script. A new network gets a profile under a temporary name and
  replaces any older profile for the same SSID only once it has actually
  connected, so a mistyped password never costs a working one. On failure
  the temporary profile is deleted, the script rejoins the previous
  network (or starts the AP), and writes the reason to
  `/run/astro-pi-cam/wifi-result`; the page reads it back through
  `/wifi/status` after the phone finds its way back. A `flock` keeps two
  mode changes from interleaving. The request itself is fire-and-forget,
  like the toggle, since it tears down the connection it arrived on.
- **Scan results in AP mode come from a cache**: with one radio hosting the
  AP, a scan would mean leaving the AP's channel and possibly dropping the
  phone that asked for it. So every path that starts the AP (`wifi.sh ap`,
  used by the toggle, the boot failover and failed joins) saves a scan
  first, and in AP mode the dashboard shows that list with its age, plus a
  typed-name option for anything missing. Live scans happen in STA mode.
- **The boot-time decision doesn't continuously re-evaluate** — a direct
  consequence of the single-WiFi-radio constraint above: once the radio is
  busy hosting the AP, it can't simultaneously scan for the home network in
  the background. So if the Pi boots in the field (AP active) and is later
  brought back within range of home WiFi, it won't switch back on its own;
  `deploy/sta-mode.sh` (or a reboot) reconnects it for dev/SSH. The manual
  `deploy/ap-mode.sh`/`sta-mode.sh` toggle scripts still exist for
  switching without a reboot.
- **Joining the AP behaves like public WiFi (captive-portal-style)**: two
  halves. `deploy/setup_captive_portal.sh` drops a wildcard DNS override
  (`address=/#/4.3.2.1`) into `/etc/NetworkManager/dnsmasq-shared.d/` —
  NetworkManager's own config-include directory for the internal dnsmasq
  it runs whenever a connection is in shared (AP) mode, confirmed present
  on-device pre-created by the NM package. This sends every hostname an
  AP-mode client looks up to the Pi itself, which is free of any real
  tradeoff since that AP has no upstream internet to begin with. On the
  app side, `captive_portal_probe()` in `app.py` answers each major OS's
  own connectivity-check URL (Apple's `/hotspot-detect.html`, Android's
  `/generate_204`, Windows' `/connecttest.txt`, etc. — DNS sends all of
  them here regardless of which hostname they were dialed against) with a
  302 instead of the exact "yes, you have real internet" response each OS
  expects; that mismatch is exactly what makes the OS conclude there's a
  captive portal and open a browser straight to the redirect target. A
  catch-all route (`catch_all`, registered last) extends the same
  treatment to any other unrecognized path, since not every OS/background
  request uses one of the well-known probe URLs; Werkzeug's routing
  always prefers a more specific match over it, so it never shadows a real
  route. Takes effect next time AP mode activates, not retroactively on an
  already-running hotspot.
- **The app listens on port 80, because the captive portal needs it**:
  OS connectivity checks are plain `http://` on port 80. The app originally
  ran on 5000, so on a real Android phone the probe reached the Pi (DNS
  did its job) and was refused — and "connection refused" reads as "no
  internet", not "captive portal", so no sign-in prompt ever appeared.
  Every earlier test called the probe paths on `:5000`, which is why this
  slipped through. The service still runs as `pi`, not root:
  `AmbientCapabilities=CAP_NET_BIND_SERVICE` in the unit grants just the
  right to bind ports below 1024. Probes arriving under a hijacked foreign
  hostname (e.g. `connectivitycheck.gstatic.com`) get an absolute redirect
  to `http://4.3.2.1/`, so the phone's sign-in window lands on the
  camera's real address rather than on the borrowed hostname. Port 443 is
  deliberately left closed: Android's parallel HTTPS probe then fails fast
  and it trusts the HTTP probe's "portal" verdict.
- **The hotspot uses `4.3.2.1/24`, not NetworkManager's default
  `10.42.0.1`, because Android won't show a captive portal on a private
  address.** Found on a real Galaxy S25 Ultra with dnsmasq query logging:
  the phone resolved `connectivitycheck.gstatic.com` (correctly answered
  `10.42.0.1`) over and over, yet never sent the HTTP check. Recent
  Android's network stack skips the HTTP probe when the check hostname
  resolves to a private address (10/8, 172.16/12, 192.168/16) and calls
  the network "no internet" — a guard against exactly this kind of DNS
  redirect. With the hotspot on a public-range address, the very next
  test showed `GET /generate_204` → 302 and the sign-in prompt appeared.
  `4.3.2.1` is the address commonly used for this; the hotspot has no
  internet, so it never reaches the real host, and phones route it over
  WiFi because it's the WiFi network's own subnet. Set via
  `ipv4.addresses` on the nmcli profile (`deploy/setup_ap_profile.sh`) and
  `AP_GATEWAY_IP` in `app/config.py`.
- **`local=/#/` alongside the wildcard `address=`**: without it, every
  non-A query (AAAA, HTTPS records, ...) gets forwarded upstream, and with
  no upstream servers dnsmasq answers REFUSED — a server error — instead
  of the clean empty answer a resolver expects. That turned out not to be
  what blocked the Galaxy (it only asked for A records), but it's the
  correct answer to give and costs nothing.
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
- **The camera lock has a bounded wait, not an unconditional `with
  self._lock:`** — found via a real bug: a WiFi mode switch orphans
  whatever preview connection was open on the browser that triggered it
  (its socket goes unreachable), and that generator's write to the now-dead
  socket can block for a long time before the OS notices — tens of seconds
  in the one case this was caught live, no hard upper bound in general.
  With a blocking lock, that single orphaned connection stalled every
  other camera use (new previews, new captures) until the OS gave up.
  `capture_still` now does `self._lock.acquire(timeout=30)` (generous,
  since a legitimate exposure can itself hold the lock for up to 200s —
  this is a backstop against something *else* being stuck, not a
  complaint about long shots) and raises `CameraBusyError` on timeout,
  which `CaptureSession`'s existing exception handler already surfaces as
  a normal status error. `stream_mjpeg` can't acquire its own lock early
  enough to matter — it's a generator, and generator bodies don't execute
  until first iterated, by which point Werkzeug has already committed to a
  200 response — so locking is split out into `AstroCamera.acquire()`/
  `release()`, called eagerly in the Flask route *before* constructing the
  streaming `Response`; a 5s timeout there means a busy camera gets a
  clean synchronous 503 instead of a silently hanging connection. The
  `/network/toggle` route also now proactively bumps the preview
  generation counter right before launching the switch script, so a
  still-responsive stream self-evicts immediately rather than only
  noticing after the fact.
- **Orientation: a 180° flip only, done by the camera**: the "Rotate 180°"
  box passes `Transform(hflip=1, vflip=1)` when configuring the camera,
  for both the preview stream and still captures. Checked on-device that
  this is the only rotation the HQ Camera supports — libcamera exposes no
  rotation control, and a requested 90°/270° transpose is silently replaced
  with a 180° flip. Because the flip happens on the sensor it costs
  nothing (capture time unchanged) and raw DNGs are turned too (their
  CFAPattern changes from BGGR to RGGB). An arbitrary-angle slider was
  prototyped first and dropped at the user's request: it needed software
  rotation of each 12MP JPEG, which took ~10s per frame on the Zero 2 W
  with bicubic resampling (~5s bilinear) and couldn't apply to raw DNGs.
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

- **Mount control over Bluetooth, through the ESP32 `SmartEQ-RJ9`
  adapter, using a plain RFCOMM socket**: the adapter
  (esp32-ioptron_smart_eq_controller) is a Classic Bluetooth serial port
  that copies bytes to and from the mount's RS-232 port unchanged, so
  `mount.py` speaks the iOptron command set directly. Python's own
  `socket.AF_BLUETOOTH`/`BTPROTO_RFCOMM` does the connection — no new
  package, no `rfcomm` device node, and no root: RFCOMM sockets are open
  to any user, and BlueZ pairs the adapter on the first connect by itself
  (Just Works, no PIN). Its SPP service is on channel 1, used directly
  rather than looked up via SDP (`sdptool` is gone from current BlueZ).
  Scanning is `bluetoothctl scan bredr` — Classic only, since the adapter
  is Classic-only and a dual scan buries it under unnamed BLE beacons.
  The one root step is lifting the radio's rfkill block (the stock image
  boots with Bluetooth blocked), added to the sudoers rule as exactly
  `rfkill unblock bluetooth`.
- **The connection lives in the app, not the browser**: one `MountLink`
  shared by every page, so the Bluetooth page connects it and the
  dashboard uses it.
- **The move controls sit on the preview itself**: on a phone the
  preview fills the screen width, so a separate controls card below it
  meant scrolling back and forth while framing. While a mount is
  connected, N/S/E/W buttons are overlaid at the preview's edge
  midpoints (clear of the center and the crosshair), with the slew speed
  top-left, Zero (go to zero position, confirmed first) bottom-left and
  Stop bottom-right. There's no separate Mount card: the connection
  line, state and RA/Dec readout, and any mount error sit right under the
  preview in the Focus section. A lock serializes commands, since the
  mount answers one at a time; before each command any stray buffered
  bytes are discarded, so a late reply can never be read as the answer to
  the next command.
- **The adapter is bonded on first connect, so BlueZ keeps it**: BlueZ
  registers anything a scan finds as a *temporary* device and deletes it
  30s after a scan last saw it (`TemporaryTimeout`) — and deleting it
  tears down a live connection too. Connecting the RFCOMM socket pairs
  but doesn't bond (the Pi's adapter boots non-pairable, and then BlueZ
  pairs without storing keys), so the adapter stayed temporary and every
  scan — the Bluetooth page runs one when opened — cost a disconnect
  ~25-30s later. Trusting the device doesn't prevent it; a bond does. So
  `_ensure_bonded` (once per adapter, skipped when already bonded):
  pairable on, a scan if BlueZ doesn't currently know the device, `pair`
  with a NoInputNoOutput agent (Just Works), pairable off again so
  nothing else nearby can pair. The bond is stored by BlueZ and survives
  reboots. Reflashing the adapter wipes its side of the bond, so a
  connect that fails with a stored bond removes it, re-pairs and retries
  once. Connects also wait for any running scan (same lock), so even an
  unbonded link (if bonding failed) isn't made mid-scan.
- **Reconnect after unexpected drops**: adapter power blip, out of range.
  The watchdog notices a closed socket within 0.1s even while idle, and
  a background thread reconnects every 5s for up to 2 minutes, unless the
  user pressed Disconnect.
- **Moves only last while the button is held, enforced on the Pi**: the
  page sends `/mount/move` every 250ms while a direction is held and
  `/mount/stop` on release, but a release event can be lost (phone
  locks, WiFi drops, the network is switched). So the Pi treats each move
  as a lease: a watchdog thread stops that axis (`:qR#`/`:qD#`) once 1s
  passes without a renewal. Renewals don't resend the move command, and
  the page sends moves and the final stop strictly in order, so a
  late-arriving move can't restart the mount after its stop.
- **Command set, as checked against a SmartEQ Pro (`:V#` → `V1.00`)**:
  `:mn#`/`:ms#`/`:me#`/`:mw#` move at the current slew rate (no reply on
  this mount); `:qD#`/`:qR#` stop one axis and `:q#` stops everything,
  including a go-to-zero slew (each replies `1`); `:SR1#`–`:SR9#` set the
  slew rate 1x/2x/8x/16x/64x/128x/256x/512x/Max (reply `1`; the rate is
  also digit 4 of `:GAS#`); `:MH#` slews to the zero position (reply
  `1`). Status is `:GAS#` (state, tracking rate, slew rate) and `:GEC#`
  (Dec and RA in 0.01 arc-seconds). E/W follow the mount's own naming
  (`:me#` for E, as iOptron's software does); on the test mount `:me#`
  lowered the reported RA, i.e. turned the scope toward the western sky,
  so if E/W feel reversed in the field, that's the mount's convention.

## Capture flow

1. User opens `http://<ap-ip>/` on their phone, picks a session (a
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
