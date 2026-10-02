from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
CAPTURES_DIR = APP_DIR / "captures"
CAPTURES_DIR.mkdir(exist_ok=True)

DEFAULT_EXPOSURE_SECONDS = 5.0
DEFAULT_COUNT = 10
DEFAULT_INTERVAL_SECONDS = 10.0
DEFAULT_RAW = False

MIN_EXPOSURE_SECONDS = 0.0001
MAX_EXPOSURE_SECONDS = 200.0
MIN_COUNT = 1
MAX_COUNT = 1000

# The imx477 (HQ Camera) sensor's AnalogueGain control ranges 1.0-16.0
# (confirmed via picamera2 camera_controls on-device). Pi HQ Camera tools
# conventionally treat gain 1.0 as ISO 100, scaling linearly, so the usable
# ISO range is 100-1600. These are the standard full-stop ISO values a
# camera's ISO dial would offer within that range, plus "auto" (the camera
# picks gain automatically while exposure time/shutter stays fixed, same as
# Auto-ISO on a conventional camera).
ISO_AUTO = "auto"
ISO_MANUAL_OPTIONS = [100, 200, 400, 800, 1600]
ISO_SELECT_OPTIONS = [ISO_AUTO] + ISO_MANUAL_OPTIONS
DEFAULT_ISO = ISO_AUTO

ISO_TO_GAIN_BASE = 100.0
MIN_GAIN = min(ISO_MANUAL_OPTIONS) / ISO_TO_GAIN_BASE
MAX_GAIN = max(ISO_MANUAL_OPTIONS) / ISO_TO_GAIN_BASE


def iso_to_gain(iso):
    return iso / ISO_TO_GAIN_BASE


def parse_iso(value):
    """Returns the AnalogueGain for a form/query 'iso' value, or None for
    auto-gain. Raises ValueError if it isn't "auto" or a supported ISO."""
    if value == ISO_AUTO:
        return None
    iso = int(value)
    if iso not in ISO_MANUAL_OPTIONS:
        raise ValueError(f"unsupported iso: {iso}")
    return iso_to_gain(iso)


# Preview-only digital zoom (ScalerCrop), for picking out a Bahtinov mask's
# diffraction pattern on a tiny star. Capped at ~8x: the preview output is
# 480px wide against a 4056px-wide sensor (4056/480 ≈ 8.45), so beyond that
# the crop region is already smaller than the output and the ISP would be
# upscaling/interpolating rather than delivering more real detail.
MIN_ZOOM = 1.0
MAX_ZOOM = 8.0
