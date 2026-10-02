import datetime
import logging
import re
import threading
import time

import config

log = logging.getLogger(__name__)

_FRAME_NUM_RE = re.compile(r"^frame_(\d+)\.jpg$")


class CaptureSession(threading.Thread):
    """Runs N manually-exposed shots at a fixed interval in the background.

    `session_name` is a user-chosen (or default-generated) directory name
    under captures/. If it already has frames in it (the user picked an
    existing session to add more shots to), numbering continues after the
    highest existing frame number instead of restarting at 0001 — restarting
    would silently overwrite that session's earlier frames.
    """

    def __init__(self, camera, session_name, exposure_seconds, gain, count, interval_seconds, raw=False):
        super().__init__(daemon=True)
        self.camera = camera
        self.session_name = session_name
        self.exposure_seconds = exposure_seconds
        self.gain = gain
        self.count = count
        self.interval_seconds = interval_seconds
        self.raw = raw

        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.session_dir = config.CAPTURES_DIR / session_name
        self._status = {
            "running": False,
            "completed": 0,
            "total": count,
            "last_file": None,
            "error": None,
            "session_dir": session_name,
            "started_at": None,
            "finished_at": None,
        }

    def _next_frame_start(self):
        existing_nums = [
            int(m.group(1))
            for f in self.session_dir.glob("frame_*.jpg")
            if (m := _FRAME_NUM_RE.match(f.name))
        ]
        return max(existing_nums, default=0) + 1

    def stop(self):
        self._stop_event.set()

    def get_status(self):
        with self._lock:
            return dict(self._status)

    def _set_status(self, **fields):
        with self._lock:
            self._status.update(fields)

    def run(self):
        self._set_status(running=True, started_at=datetime.datetime.now().isoformat(timespec="seconds"))
        self.session_dir.mkdir(parents=True, exist_ok=True)
        frame_start = self._next_frame_start()

        try:
            for i in range(1, self.count + 1):
                if self._stop_event.is_set():
                    break
                shot_start = time.monotonic()
                stamp = f"{frame_start + i - 1:04d}"
                jpeg_path = self.session_dir / f"frame_{stamp}.jpg"
                raw_path = self.session_dir / f"frame_{stamp}.dng" if self.raw else None

                self.camera.capture_still(jpeg_path, self.exposure_seconds, self.gain, raw_path)

                self._set_status(completed=i, last_file=jpeg_path.name)

                elapsed = time.monotonic() - shot_start
                remaining_wait = self.interval_seconds - elapsed
                if remaining_wait > 0 and not self._stop_event.is_set():
                    self._stop_event.wait(remaining_wait)
        except Exception as exc:
            log.exception("Capture session failed")
            self._set_status(error=str(exc))
        finally:
            self._set_status(running=False, finished_at=datetime.datetime.now().isoformat(timespec="seconds"))
