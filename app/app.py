import datetime
import logging
import os
import re
import shutil
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
        },
    )


@app.route("/session/start", methods=["POST"])
def start_session():
    global current_session
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

    current_session = CaptureSession(camera, session_name, exposure, gain, count, interval, raw)
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

    my_generation = _next_preview_generation()

    def should_stop():
        return _session_running() or _preview_generation != my_generation

    return Response(
        camera.stream_mjpeg(should_stop, gain=gain, zoom=zoom),
        mimetype="multipart/x-mixed-replace; boundary=FRAME",
    )


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


if __name__ == "__main__":
    # threaded=True so the long-lived MJPEG preview connection doesn't
    # block status polling / session start / gallery requests.
    app.run(host="0.0.0.0", port=5000, threaded=True)
