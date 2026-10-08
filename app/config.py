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

# nmcli connection name for the field AP, and the scripts that switch
# wlan0 into/out of it — see deploy/setup_ap_profile.sh and
# deploy/setup_sudoers.sh (grants the app passwordless sudo for exactly
# these two scripts, nothing else).
AP_CONNECTION_NAME = "AstroPiCamAP"
# The hotspot's own address. Deliberately NOT a private range (10.x,
# 172.16-31.x, 192.168.x): recent Android skips its captive-portal check
# entirely when the check hostname resolves to a private address, so a
# private hotspot address means no sign-in prompt. Set on the nmcli
# profile by deploy/setup_ap_profile.sh; the hotspot has no internet, so
# this address never reaches the real host that owns it.
AP_GATEWAY_IP = "4.3.2.1"
HTTP_PORT = 80
DEPLOY_DIR = APP_DIR.parent / "deploy"
AP_MODE_SCRIPT = DEPLOY_DIR / "ap-mode.sh"
STA_MODE_SCRIPT = DEPLOY_DIR / "sta-mode.sh"
# Scans for / joins WiFi networks from the dashboard (also run, via sudo,
# under the same sudoers rule). It records the outcome of the latest join
# attempt here, readable by the app's unprivileged user.
WIFI_SCRIPT = DEPLOY_DIR / "wifi.sh"
WIFI_RESULT_FILE = Path("/run/astro-pi-cam/wifi-result")

# Telescope mount over Bluetooth (see mount.py). rfkill lifts the radio's
# boot-time block — the one step that needs root, via the sudoers rule.
RFKILL = "/usr/sbin/rfkill"
# The last mount adapter connected to, so it can be reconnected in one tap
# without scanning (it stays reachable by address).
MOUNT_DEVICE_FILE = APP_DIR / "mount-device.json"
# How the preview's direction buttons map onto mount moves, for however
# the camera happens to sit on the mount (see mount.load_orientation).
MOUNT_ORIENTATION_FILE = APP_DIR / "mount-orientation.json"
