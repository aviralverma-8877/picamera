import datetime
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path

from flask import Flask, Response, after_this_request, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for

import config
from camera import AstroCamera
from capture_session import CaptureSession

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = Flask(__name__)
camera = AstroCamera()
current_session = None
# Remembered so the "Rotate 180°" box (and the preview's orientation) stay
# as set after starting a sequence reloads the page.
last_flip = False

# Browsers don't reliably close the old multipart/x-mixed-replace connection
# the instant an <img src> is reassigned, and there's only one physical
# camera behind one lock. Without this, a new preview request (e.g. after
# switching ISO) can sit blocked behind a stream the browser already
# abandoned. Each new /preview.mjpg request bumps this counter; a running
# stream notices it's no longer the latest and stops itself within a frame
# or two, instead of waiting for the client to actually disconnect.
_preview_gen_lock = threading.Lock()
_preview_generation = 0


def _next_preview_generation():
    global _preview_generation
    with _preview_gen_lock:
        _preview_generation += 1
        return _preview_generation


def _clamp(value, lo, hi):
    return max(lo, min(hi, value))


# New session names must match this — the same rules a filesystem directory
# name would need, narrowed to a safe/friendly subset (no dots, so ".."
# can't even come up) since the name also goes straight into URLs.
_SESSION_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,99}$")


def _list_session_names():
    return sorted(
        (d.name for d in config.CAPTURES_DIR.iterdir() if d.is_dir()),
        reverse=True,
    )


def _next_default_session_name():
    today = datetime.date.today().isoformat()
    n = 1
    while (config.CAPTURES_DIR / f"{today}-{n}").exists():
        n += 1
    return f"{today}-{n}"


@app.route("/")
def index():
    status = current_session.get_status() if current_session else None
    return render_template(
        "index.html",
        status=status,
        iso_options=config.ISO_SELECT_OPTIONS,
        min_zoom=config.MIN_ZOOM,
        max_zoom=config.MAX_ZOOM,
        existing_sessions=_list_session_names(),
        default_session_name=_next_default_session_name(),
        defaults={
            "exposure": config.DEFAULT_EXPOSURE_SECONDS,
            "iso": config.DEFAULT_ISO,
            "count": config.DEFAULT_COUNT,
            "interval": config.DEFAULT_INTERVAL_SECONDS,
            "flip": last_flip,
        },
    )


@app.route("/session/start", methods=["POST"])
def start_session():
    global current_session, last_flip
    if _session_running():
        return redirect(url_for("index"))

    try:
        exposure = float(request.form.get("exposure", config.DEFAULT_EXPOSURE_SECONDS))
        gain = config.parse_iso(request.form.get("iso", config.DEFAULT_ISO))
        count = int(request.form.get("count", config.DEFAULT_COUNT))
        interval = float(request.form.get("interval", config.DEFAULT_INTERVAL_SECONDS))
    except ValueError:
        return "Invalid input", 400
    raw = request.form.get("raw") == "on"
    flip = request.form.get("flip") == "on"

    session_choice = request.form.get("session_choice", "__new__")
    if session_choice != "__new__":
        # Appending to an existing session: the chosen directory must
        # actually exist. CaptureSession continues frame numbering after
        # whatever's already there instead of overwriting it.
        if not _is_safe_name(session_choice) or not (config.CAPTURES_DIR / session_choice).is_dir():
            return "Unknown session", 400
        session_name = session_choice
    else:
        session_name = request.form.get("session_name", "").strip() or _next_default_session_name()
        if not _SESSION_NAME_RE.match(session_name):
            return "Invalid session name — use letters, numbers, spaces, hyphens or underscores only", 400
        if (config.CAPTURES_DIR / session_name).exists():
            return "A session with that name already exists", 400
        # Create it synchronously (not in the background thread) so it's
        # already there — and shows up in the session dropdown — by the
        # time this request's redirect reloads the page.
        (config.CAPTURES_DIR / session_name).mkdir(parents=True)

    exposure = _clamp(exposure, config.MIN_EXPOSURE_SECONDS, config.MAX_EXPOSURE_SECONDS)
    count = int(_clamp(count, config.MIN_COUNT, config.MAX_COUNT))
    interval = max(exposure, interval)

    last_flip = flip
    current_session = CaptureSession(camera, session_name, exposure, gain, count, interval, raw, flip)
    current_session.start()
    return redirect(url_for("index"))


@app.route("/session/stop", methods=["POST"])
def stop_session():
    if current_session:
        current_session.stop()
    return redirect(url_for("index"))


@app.route("/session/status")
def session_status():
    if current_session is None:
        return jsonify({"running": False})
    return jsonify(current_session.get_status())


def _session_running():
    return current_session is not None and current_session.get_status().get("running", False)


@app.route("/preview.mjpg")
def preview_stream():
    if _session_running():
        return "Camera busy with capture session", 503
    try:
        gain = config.parse_iso(request.args.get("iso", config.DEFAULT_ISO))
        zoom = float(request.args.get("zoom", 1.0))
    except ValueError:
        return "Invalid ISO or zoom", 400
    zoom = _clamp(zoom, config.MIN_ZOOM, config.MAX_ZOOM)
    flip = request.args.get("flip") == "1"

    # Bump the generation *before* trying to acquire the lock: this is the
    # only signal that tells a still-running old stream to let go (its
    # should_stop() checks this counter). Acquiring first would be a
    # deadlock in slow motion — a healthy old stream would never learn it
    # should stop, since the thing that evicts it hadn't happened yet, so
    # every new request would just time out waiting for a lock that was
    # never going to be released.
    my_generation = _next_preview_generation()

    # Acquired here (not inside the generator) so a busy camera gets a
    # clean, synchronous 503 instead of a 200 that then silently hangs —
    # stream_mjpeg's body, being a generator, wouldn't even start running
    # until Werkzeug first iterates it, by which point the response status
    # is already committed.
    if not camera.acquire(timeout=5):
        return "Camera busy, try again shortly", 503

    def should_stop():
        return _session_running() or _preview_generation != my_generation

    def generate():
        try:
            yield from camera.stream_mjpeg(should_stop, gain=gain, zoom=zoom, flip=flip)
        finally:
            camera.release()

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=FRAME")


@app.route("/gallery")
def gallery():
    sessions = []
    for d in sorted(config.CAPTURES_DIR.iterdir(), reverse=True):
        if d.is_dir():
            jpgs = sorted(f for f in d.iterdir() if f.suffix == ".jpg")
            files = [
                {"name": f.name, "has_raw": f.with_suffix(".dng").exists()}
                for f in jpgs
            ]
            sessions.append({"name": d.name, "files": files})
    return render_template("gallery.html", sessions=sessions)


def _is_safe_name(name):
    return name not in ("..", ".") and "/" not in name and "\\" not in name


@app.route("/captures/<session_name>/<filename>")
def download_capture(session_name, filename):
    if not _is_safe_name(session_name):
        return "Not found", 404
    directory = config.CAPTURES_DIR / session_name
    # as_attachment only affects top-level navigation/clicks (prompts Save
    # As); <img src> thumbnail fetches ignore Content-Disposition and still
    # render normally, so this is safe to share with the gallery thumbnails.
    return send_from_directory(directory, filename, as_attachment=True)


@app.route("/gallery/<session_name>/download")
def download_session_zip(session_name):
    if not _is_safe_name(session_name):
        return "Not found", 404
    directory = config.CAPTURES_DIR / session_name
    if not directory.is_dir():
        return "Not found", 404
    if _session_running() and current_session.get_status().get("session_dir") == session_name:
        return "Cannot download while this session is actively capturing", 409

    # Build the zip on disk (not in memory) — the Pi has ~250MB free RAM
    # but 12GB of disk, and a session with several raw DNGs could be
    # hundreds of MB.
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w", zipfile.ZIP_STORED) as zf:
        for f in sorted(directory.iterdir()):
            if f.is_file():
                zf.write(f, arcname=f.name)

    @after_this_request
    def _cleanup(response):
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return response

    return send_file(tmp.name, as_attachment=True, download_name=f"{session_name}.zip")


@app.route("/captures/<session_name>/<filename>/delete", methods=["POST"])
def delete_capture(session_name, filename):
    if not _is_safe_name(session_name) or not _is_safe_name(filename):
        return "Not found", 404
    if _session_running():
        return "Cannot delete while a capture session is running", 409

    directory = config.CAPTURES_DIR / session_name
    if not directory.is_dir():
        return "Not found", 404

    # Remove the jpg and any matching raw (same frame number, different ext).
    for match in directory.glob(f"{Path(filename).stem}.*"):
        match.unlink(missing_ok=True)

    if not any(directory.iterdir()):
        directory.rmdir()
    return redirect(url_for("gallery"))


@app.route("/gallery/<session_name>/delete", methods=["POST"])
def delete_session(session_name):
    if not _is_safe_name(session_name):
        return "Not found", 404
    if _session_running():
        return "Cannot delete while a capture session is running", 409

    directory = config.CAPTURES_DIR / session_name
    if directory.is_dir():
        shutil.rmtree(directory)
    return redirect(url_for("gallery"))


@app.route("/gallery/delete-all", methods=["POST"])
def delete_all():
    if _session_running():
        return "Cannot delete while a capture session is running", 409

    for d in config.CAPTURES_DIR.iterdir():
        if d.is_dir():
            shutil.rmtree(d)
    return redirect(url_for("gallery"))


def _wifi_mode():
    """Returns (mode, connection_name) for wlan0: mode is 'ap', 'sta', or
    'disconnected' ('unknown' if nmcli couldn't be queried)."""
    try:
        out = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,CONNECTION", "device", "status"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except Exception:
        log.exception("Failed to query nmcli device status")
        return "unknown", None

    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[0] == "wlan0":
            conn = parts[1]
            if conn == config.AP_CONNECTION_NAME:
                return "ap", conn
            if conn and conn != "--":
                return "sta", conn
            return "disconnected", None
    return "unknown", None


@app.route("/network/status")
def network_status():
    mode, conn = _wifi_mode()
    return jsonify({"mode": mode, "connection": conn})


@app.route("/network/toggle", methods=["POST"])
def network_toggle():
    mode, _ = _wifi_mode()
    # Whatever's viewing this page — quite possibly a live preview stream
    # — is about to have its connection orphaned by the network change.
    # Evicting it now (rather than waiting for it to notice on its own)
    # keeps it from getting stuck writing to a socket that's about to
    # become unreachable, which can block the camera lock for a long time
    # (the OS can take a long time to notice a half-open TCP connection is
    # dead). preview_stream()'s own 5s acquire timeout is the backstop for
    # whatever this doesn't catch.
    _next_preview_generation()

    # Going to STA has its own fallback built in (sta-mode.sh reverts to
    # the AP if the home network turns out not to be reachable), so this
    # is safe to trigger even speculatively.
    script = config.STA_MODE_SCRIPT if mode == "ap" else config.AP_MODE_SCRIPT
    try:
        # Fire-and-forget: this request may be arriving over the very
        # connection the script is about to tear down, so we don't wait
        # for it to finish — just launch it and return immediately, giving
        # the client the best chance of seeing a response before the
        # network actually changes.
        subprocess.Popen(["sudo", "-n", str(script)])
    except Exception as exc:
        log.exception("Failed to launch network mode switch")
        return f"Failed to switch network mode: {exc}", 500
    return jsonify({"switching_to": "sta" if mode == "ap" else "ap"})


def _power_action(systemctl_verb):
    # Refused mid-capture rather than silently cutting a sequence short —
    # a reboot/poweroff partway through a long exposure loses that frame
    # and every one after it. The UI says so; this is the actual guard.
    if _session_running():
        return "A capture sequence is running — stop it first", 409
    try:
        # Fire-and-forget for the same reason as the network toggle: the
        # machine is about to go away, so there's nothing to wait for.
        subprocess.Popen(["sudo", "-n", "/usr/bin/systemctl", systemctl_verb])
    except Exception as exc:
        log.exception("Failed to launch systemctl %s", systemctl_verb)
        return f"Failed to {systemctl_verb}: {exc}", 500
    return jsonify({"ok": True, "action": systemctl_verb})


@app.route("/system/reboot", methods=["POST"])
def system_reboot():
    return _power_action("reboot")


@app.route("/system/shutdown", methods=["POST"])
def system_shutdown():
    return _power_action("poweroff")


# Captive-portal detection, paired with the DNS wildcard redirect in
# deploy/setup_captive_portal.sh (every hostname a client looks up while on
# the AstroCamera AP resolves to this Pi — harmless, since that AP has no
# upstream internet anyway). iOS, Android, and Windows each dial a specific
# URL right after joining a network to decide whether it's a plain
# connection or a captive portal; each one expects an exact "yes, you have
# real internet" response (Apple wants a literal "Success" page, Android
# wants a bare 204, Windows wants the text "Microsoft Connect Test").
# Answering with a redirect instead — to any of them, regardless of what
# hostname they actually dialed, since DNS sends them all here — is what
# makes the OS treat the network as a captive portal and pop up a browser
# pointed straight at the redirect target, landing the user on the
# dashboard without them needing to know to open one themselves.
@app.route("/hotspot-detect.html")  # Apple
@app.route("/library/test/success.html")  # Apple (older)
@app.route("/generate_204")  # Android
@app.route("/gen_204")  # Android (older)
@app.route("/connecttest.txt")  # Windows
@app.route("/ncsi.txt")  # Windows
@app.route("/success.txt")  # Firefox
def captive_portal_probe():
    return _redirect_to_dashboard()


# Catch-all: anything else an OS or app probes for while DNS is pointed
# here (not every connectivity check uses one of the well-known paths
# above) also lands on the dashboard instead of a bare 404. Flask/Werkzeug
# prefers more specific routes over this for any URL that matches one
# (e.g. /gallery, /captures/<session>/<file>), so it only ever catches
# paths nothing else claimed.
@app.route("/<path:_unused>")
def catch_all(_unused):
    return _redirect_to_dashboard()


def _redirect_to_dashboard():
    # A request addressed to some other site's hostname (e.g.
    # connectivitycheck.gstatic.com) only reaches us because the AP's DNS
    # hijack sent it here. A relative redirect would keep the phone's
    # captive-portal window on that borrowed hostname, so send it to the
    # AP's real address instead. Requests to our own names (IP, .local)
    # stay relative, which also keeps this harmless on the home network.
    host = request.host.split(":")[0]
    is_ours = host.endswith(".local") or host.replace(".", "").isdigit()
    if not is_ours:
        return redirect(f"http://{config.AP_GATEWAY_IP}/", code=302)
    return redirect(url_for("index"), code=302)


if __name__ == "__main__":
    # Port 80, not a high port: phones' captive-portal checks are plain
    # http:// on port 80, and if nothing answers there the OS concludes
    # "no internet" rather than "captive portal" and never shows the
    # sign-in prompt. The service runs as `pi`; binding a port below 1024
    # comes from AmbientCapabilities=CAP_NET_BIND_SERVICE in the systemd
    # unit, not from running as root.
    #
    # threaded=True so the long-lived MJPEG preview connection doesn't
    # block status polling / session start / gallery requests.
    app.run(host="0.0.0.0", port=config.HTTP_PORT, threaded=True)
