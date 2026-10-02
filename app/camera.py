import io
import logging
import threading
import time

from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder
from picamera2.outputs import FileOutput

import config

log = logging.getLogger(__name__)

PREVIEW_STREAM_SIZE = (480, 360)


class _StreamingOutput(io.BufferedIOBase):
    """Holds the latest JPEG frame written by Picamera2's JpegEncoder."""

    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class AstroCamera:
    """Thin wrapper over Picamera2 for manual-exposure still capture and a
    small live MJPEG preview used for focusing.

    Opens the camera lazily and closes it between uses so the capture
    session and the live preview never fight over it. A lock serializes
    access since there is only one physical camera.
    """

    def __init__(self):
        self._picam2 = None
        self._lock = threading.Lock()

    def _open(self):
        if self._picam2 is None:
            self._picam2 = Picamera2()

    def _close(self):
        if self._picam2 is not None:
            self._picam2.close()
            self._picam2 = None

    def _meter_gain(self, target_exposure_us):
        """Estimate a sensible AnalogueGain for a long manual exposure.

        Meters quickly at a fast frame rate and extrapolates via the usual
        reciprocity relationship (gain x exposure keeps total light roughly
        constant), rather than running libcamera's AE at the full target
        exposure itself. The latter would mean every AE convergence
        iteration takes as long as the shot — for a 60s exposure, a
        multi-frame convergence turns one shot into several minutes.
        Metering with sub-second frames keeps Auto ISO overhead to about a
        second regardless of how long the actual exposure is.
        """
        meter_config = self._picam2.create_video_configuration(
            main={"size": PREVIEW_STREAM_SIZE}
        )
        self._picam2.configure(meter_config)
        self._picam2.set_controls({"AeEnable": True, "AwbEnable": True})
        self._picam2.start()
        try:
            metadata = None
            for _ in range(8):  # let AE converge over a handful of fast frames
                metadata = self._picam2.capture_metadata()
        finally:
            self._picam2.stop()

        metered_exposure_us = metadata.get("ExposureTime") or 1
        metered_gain = metadata.get("AnalogueGain") or 1.0
        equivalent_gain = metered_gain * metered_exposure_us / target_exposure_us
        return max(config.MIN_GAIN, min(config.MAX_GAIN, equivalent_gain))

    def capture_still(self, filepath, exposure_seconds, gain=None, raw_path=None):
        """Capture one frame at a fixed (manually chosen) exposure time.

        `gain` is the AnalogueGain to lock in, or None for Auto ISO: a
        sensible gain is metered first (see `_meter_gain`) and then locked
        in just like a manual ISO, so the long exposure itself is always a
        single deterministic frame — never an AE convergence loop running
        at the full exposure duration.

        The sensor needs roughly one full exposure period after a control
        change before it produces a correctly-exposed frame, so we sleep
        for the exposure duration before reading it out.
        """
        with self._lock:
            self._open()
            exposure_us = int(exposure_seconds * 1_000_000)
            try:
                if gain is None:
                    gain = self._meter_gain(exposure_us)

                still_config = self._picam2.create_still_configuration(
                    raw={} if raw_path else None
                )
                self._picam2.configure(still_config)
                self._picam2.set_controls(
                    {
                        "AeEnable": False,
                        "AwbEnable": False,
                        "ExposureTime": exposure_us,
                        "AnalogueGain": gain,
                        "FrameDurationLimits": (exposure_us, exposure_us),
                    }
                )
                self._picam2.start()
                time.sleep(exposure_seconds + 0.5)
                if raw_path:
                    request = self._picam2.capture_request()
                    try:
                        request.save("main", str(filepath))
                        request.save_dng(str(raw_path))
                    finally:
                        request.release()
                else:
                    self._picam2.capture_file(str(filepath))
            finally:
                self._picam2.stop()
                self._close()

    def _zoom_crop_rect(self, zoom):
        """A centered ScalerCrop rectangle for `zoom`x digital zoom.

        ScalerCrop crops the sensor's full-resolution active area *before*
        downscaling to the preview's output size, so this is a genuine
        optical-style crop — more real pixels on a tiny star — not a CSS
        stretch of the already-downscaled 480x360 preview frame, which
        would just blur up pixels that are already there.
        """
        x, y, w, h = self._picam2.camera_properties["PixelArrayActiveAreas"][0]
        crop_w = max(1, int(w / zoom))
        crop_h = max(1, int(h / zoom))
        crop_x = x + (w - crop_w) // 2
        crop_y = y + (h - crop_h) // 2
        return (crop_x, crop_y, crop_w, crop_h)

    def stream_mjpeg(self, should_stop, gain=None, zoom=1.0):
        """Yield MJPEG multipart frames for a live focusing preview.

        `should_stop()` is polled between frames; once it returns True the
        stream stops itself and releases the camera, instead of holding the
        lock. The caller uses this both to yield to a capture session and to
        evict a stream that's been superseded by a newer preview request
        (e.g. the client switched ISO) — browsers don't reliably close the
        old multipart connection the instant <img src> changes, so we can't
        just wait for the client to disconnect.

        `gain` locks the preview to that ISO's AnalogueGain while exposure
        time is left auto (so brightness/frame rate stay reasonable) — the
        mirror image of the fixed-exposure/auto-gain trick in
        capture_still. None means fully automatic (Auto ISO).

        `zoom` (>= 1.0) crops the sensor around its center via ScalerCrop —
        see `_zoom_crop_rect`. Preview-only: capture_still always uses the
        full frame regardless of what the preview was zoomed to.
        """
        with self._lock:
            self._open()
            output = _StreamingOutput()
            try:
                video_config = self._picam2.create_video_configuration(
                    main={"size": PREVIEW_STREAM_SIZE}
                )
                self._picam2.configure(video_config)
                controls = {"AeEnable": True, "AwbEnable": True}
                if gain is not None:
                    controls["AnalogueGain"] = gain
                if zoom and zoom > 1.0:
                    controls["ScalerCrop"] = self._zoom_crop_rect(zoom)
                self._picam2.set_controls(controls)
                self._picam2.start_recording(JpegEncoder(), FileOutput(output))
                while not should_stop():
                    with output.condition:
                        output.condition.wait(timeout=2)
                        frame = output.frame
                    if frame is None:
                        continue
                    yield (
                        b"--FRAME\r\n"
                        b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                    )
            finally:
                self._picam2.stop_recording()
                self._close()
