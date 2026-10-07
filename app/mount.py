"""Bluetooth link to the telescope mount, via the ESP32 SmartEQ-RJ9 adapter.

The adapter (github.com/aviralverma-8877/esp32-ioptron_smart_eq_controller)
is a Classic Bluetooth serial port (SPP) that copies every byte to and from
the mount's RS-232 port unchanged, so this speaks the iOptron command set
directly over an RFCOMM socket. Checked on the test Pi against an iOptron
SmartEQ Pro (:MountInfo# 0011, :V# V1.00).

No root needed for the connection itself: RFCOMM sockets are open to any
user, and BlueZ pairs the adapter on the first connect (Just Works, no
PIN). Root is only needed to lift the radio's rfkill block, through the
sudoers rule in deploy/setup_sudoers.sh.
"""
import json
import logging
import re
import select
import socket
import subprocess
import threading
import time

import config

log = logging.getLogger(__name__)

_ADDRESS_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")

# The adapter's SPP server is on RFCOMM channel 1 (the first one Bluedroid
# hands out; it has no other services).
RFCOMM_CHANNEL = 1
CONNECT_TIMEOUT_S = 15
REPLY_TIMEOUT_S = 2.0
# After an unexpected drop: retry every few seconds, for long enough to
# ride out the adapter being power-cycled.
RECONNECT_INTERVAL_S = 5
RECONNECT_FOR_S = 120

# A move runs only while the page keeps confirming the button is still held
# (every 250ms). If those stop — the phone dropped off WiFi, the browser
# was closed mid-press — the watchdog stops that axis, so the mount can't
# be left slewing into its own tripod with nobody watching.
MOVE_HOLD_S = 1.0

# direction -> (axis, command). The mount moves at the current slew rate
# until told to stop.
_MOVES = {
    "n": ("dec", ":mn#"),
    "s": ("dec", ":ms#"),
    "e": ("ra", ":me#"),
    "w": ("ra", ":mw#"),
}
_STOP_AXIS = {"ra": ":qR#", "dec": ":qD#"}

# :SR1# .. :SR9#, and the 4th digit of :GAS#.
SLEW_RATES = ["1x", "2x", "8x", "16x", "64x", "128x", "256x", "512x", "Max"]
# 2nd digit of :GAS#.
_STATES = [
    "Stopped", "Tracking", "Slewing", "Guiding", "Meridian flip",
    "Tracking (PEC)", "Parked", "At zero position",
]
_TRACK_RATES = ["Sidereal", "Lunar", "Solar", "King", "Custom"]


class MountError(Exception):
    pass


def _run(args, timeout=10):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def bluetooth_powered():
    try:
        out = _run(["bluetoothctl", "show"], timeout=5).stdout
    except Exception:
        return False
    return "Powered: yes" in out


def ensure_bluetooth_on():
    """Powers the Bluetooth controller on, lifting the rfkill block first if
    needed (the stock image boots with Bluetooth blocked)."""
    if bluetooth_powered():
        return
    try:
        _run(["sudo", "-n", config.RFKILL, "unblock", "bluetooth"], timeout=10)
        time.sleep(1)  # the controller takes a moment to come back after unblocking
        _run(["bluetoothctl", "power", "on"], timeout=10)
    except Exception as exc:
        raise MountError(f"Couldn't turn Bluetooth on: {exc}")
    if not bluetooth_powered():
        raise MountError("Couldn't turn Bluetooth on")


def load_last_device():
    try:
        data = json.loads(config.MOUNT_DEVICE_FILE.read_text())
        if _ADDRESS_RE.match(data.get("address", "")):
            return {"address": data["address"], "name": str(data.get("name", ""))}
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _save_last_device(address, name):
    try:
        config.MOUNT_DEVICE_FILE.write_text(json.dumps({"address": address, "name": name}))
    except OSError:
        log.exception("Couldn't remember the mount's Bluetooth address")


# Held for scans and connects alike: BlueZ drops a link made mid-scan (see
# MountLink._open), and pairing needs the radio to itself.
_scan_lock = threading.Lock()


def _bonded(address):
    try:
        out = _run(["bluetoothctl", "info", address], timeout=5).stdout
    except Exception:
        return False
    return "Bonded: yes" in out


def _ensure_bonded(address):
    """Bonds with the adapter once, so BlueZ keeps it as a permanent device.

    Unbonded, it's a *temporary* device, which BlueZ deletes 30s after a
    scan last saw it — taking a live connection down with it, so every
    scan (the Bluetooth page runs one when opened) cost a disconnect ~30s
    later. Connecting the RFCOMM socket alone pairs without bonding; a
    real bond needs the adapter in pairable mode and an explicit pair.
    Pairable is switched off again afterwards so nothing else nearby can
    pair with the Pi. Caller holds _scan_lock. Failure isn't fatal: the
    connection still works, just without that protection."""
    if _bonded(address):
        return
    try:
        known = _run(["bluetoothctl", "info", address], timeout=5).returncode == 0
        if not known:  # pairing needs BlueZ to have seen it recently
            _run(["bluetoothctl", "--timeout", "8", "scan", "bredr"], timeout=20)
        _run(["bluetoothctl", "pairable", "on"], timeout=5)
        out = _run(["bluetoothctl", "--agent", "NoInputNoOutput", "--timeout", "20",
                    "pair", address], timeout=30).stdout
    except Exception as exc:
        log.warning("Couldn't bond with %s: %s", address, exc)
        return
    finally:
        try:
            _run(["bluetoothctl", "pairable", "off"], timeout=5)
        except Exception:
            pass
    if _bonded(address):
        log.info("Bonded with %s", address)
    else:
        log.warning("Couldn't bond with %s: %s", address, out.strip().splitlines()[-1:] or "no output")


def scan_devices(duration=8):
    """Nearby Classic Bluetooth devices that have a name, as
    [{"address", "name"}]. BLE is left out (`scan bredr`): the adapter is
    Classic-only, and BLE beacons would bury it under unnamed entries."""
    ensure_bluetooth_on()
    with _scan_lock:
        try:
            _run(["bluetoothctl", "--timeout", str(duration), "scan", "bredr"], timeout=duration + 10)
            out = _run(["bluetoothctl", "devices"], timeout=5).stdout
        except Exception as exc:
            raise MountError(f"Bluetooth scan failed: {exc}")
    devices = []
    for line in out.splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3 or parts[0] != "Device" or not _ADDRESS_RE.match(parts[1]):
            continue
        address, name = parts[1], parts[2].strip()
        # Unnamed devices show their address with dashes as the name.
        if not name or name == address.replace(":", "-"):
            continue
        devices.append({"address": address, "name": name})
    return devices


def _decode_gas(r):
    """:GAS# -> 'G S T M X H' digits: GPS, state, tracking rate, slew rate,
    time source, hemisphere."""
    if len(r) < 6 or not r[:6].isdigit():
        return {}
    state, track, rate = int(r[1]), int(r[2]), int(r[3])
    return {
        "state": _STATES[state] if state < len(_STATES) else "Unknown",
        "moving": state in (2, 4),
        "tracking": _TRACK_RATES[track] if track < len(_TRACK_RATES) else "Unknown",
        "rate": rate if 1 <= rate <= len(SLEW_RATES) else None,
    }


def _decode_gec(r):
    """:GEC# -> sDDDDDDDD RRRRRRRR, both in 0.01 arc-seconds."""
    if len(r) < 17 or r[0] not in "+-" or not r[1:17].isdigit():
        return {}
    dec = int(r[1:9]) / 360000 * (-1 if r[0] == "-" else 1)
    ra_s = int(r[9:17]) / 100 / 15  # arc-seconds -> seconds of time
    h, rem = divmod(round(ra_s), 3600)
    m, s = divmod(rem, 60)
    d_s = round(abs(dec) * 3600)
    dd, rem = divmod(d_s, 3600)
    dm, ds = divmod(rem, 60)
    return {
        "ra": f"{h % 24:02d}h {m:02d}m {s:02d}s",
        "dec": f"{'-' if dec < 0 else '+'}{dd:02d}° {dm:02d}' {ds:02d}\"",
    }


class MountLink:
    """One RFCOMM connection to the mount adapter, shared by every page.

    A lock serializes commands (the mount answers one at a time, in order),
    and a watchdog thread stops any move whose button stopped being held.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._sock = None
        self.address = None
        self.name = None
        self.model = None
        self._moving = {}     # axis -> direction currently commanded
        self._deadline = {}   # axis -> monotonic time the hold expires
        # (address, name) the user connected to and hasn't disconnected
        # from: a link lost any other way is reconnected in the background.
        self._wanted = None
        self.reconnecting = False

    # ---- connection ---------------------------------------------------

    @property
    def connected(self):
        return self._sock is not None

    def connect(self, address, name=""):
        address = address.upper()
        if not _ADDRESS_RE.match(address):
            raise ValueError("Invalid Bluetooth address")
        self.disconnect()
        self._open(address, name or address)
        self._wanted = (address, self.name)

    def _open(self, address, name):
        # Bonding (see _ensure_bonded) is what keeps BlueZ from deleting the
        # device — and the live link with it — ~30s after any scan. The
        # scan lock also keeps connects out of a running scan, where an
        # unbonded link (if bonding failed) would be dropped the same way.
        with _scan_lock:
            ensure_bluetooth_on()
            was_bonded = _bonded(address)
            _ensure_bonded(address)
            try:
                sock = self._rfcomm_connect(address)
            except OSError as exc:
                if not was_bonded:
                    raise MountError(f"Couldn't connect to {name}: {exc.strerror or exc}")
                # Reflashing the adapter wipes its side of the bond, and the
                # Pi's stale key then gets refused: pair afresh, once.
                log.info("Connect with stored bond failed (%s), pairing again", exc)
                _run(["bluetoothctl", "remove", address], timeout=10)
                _ensure_bonded(address)
                try:
                    sock = self._rfcomm_connect(address)
                except OSError as exc:
                    raise MountError(f"Couldn't connect to {name}: {exc.strerror or exc}")
        with self._lock:
            self._sock = sock
            self.address = address
            self.name = name
            self.model = None
            self._moving.clear()
            self._deadline.clear()
        _save_last_device(address, name)
        threading.Thread(target=self._watchdog, args=(sock,), daemon=True).start()
        log.info("Connected to mount adapter %s (%s)", name, address)
        # Not an error if this fails: the adapter is reachable, but the
        # mount behind it may be off or unplugged. status() says so.
        try:
            self.model = self._io(":MountInfo#", 4) or None
        except MountError:
            pass

    @staticmethod
    def _rfcomm_connect(address):
        sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        sock.settimeout(CONNECT_TIMEOUT_S)
        try:
            sock.connect((address, RFCOMM_CHANNEL))
        except OSError:
            sock.close()
            raise
        return sock

    def _lost(self, reason):
        """The link dropped without the user disconnecting (adapter power
        blip, out of range, ...): close it and keep trying to get it back
        in the background. Called with the lock held."""
        log.warning("Mount link lost: %s", reason)
        self._close()
        if self._wanted and not self.reconnecting:
            self.reconnecting = True
            threading.Thread(target=self._reconnect, args=(self._wanted,), daemon=True).start()

    def _reconnect(self, wanted):
        deadline = time.monotonic() + RECONNECT_FOR_S
        try:
            while time.monotonic() < deadline:
                time.sleep(RECONNECT_INTERVAL_S)
                if self._wanted != wanted or self.connected:
                    return  # the user disconnected, or connected by hand
                try:
                    self._open(*wanted)
                    log.info("Reconnected to mount adapter")
                    return
                except MountError as exc:
                    log.info("Reconnect failed: %s", exc)
            log.warning("Gave up reconnecting to the mount adapter")
            self._wanted = None
        finally:
            self.reconnecting = False

    def disconnect(self):
        self._wanted = None
        with self._lock:
            if self._sock is None:
                return
            if self._moving:
                try:
                    self._io(":q#", "1")
                except MountError:
                    pass
            self._close()

    def _close(self):
        try:
            self._sock.close()
        except OSError:
            pass
        self._sock = None
        self._moving.clear()
        self._deadline.clear()
        log.info("Disconnected from mount adapter")

    # ---- raw command I/O -------------------------------------------------

    def _io(self, cmd, reply):
        """Sends `cmd` and reads its reply: None (no reply expected), "1"
        (a single character), "#" (up to and including '#'), or an int
        (exactly that many characters, for :MountInfo#). Returns the reply
        without its '#'."""
        with self._lock:
            if self._sock is None:
                raise MountError("Not connected to a mount")
            sock = self._sock
            try:
                # Anything still buffered is a late or unasked-for reply
                # (e.g. the '1' some firmware sends after a move command);
                # it must not be read as the answer to this one.
                while select.select([sock], [], [], 0)[0]:
                    if not sock.recv(256):
                        raise ConnectionResetError("adapter closed the connection")
                sock.sendall(cmd.encode("ascii"))
                if reply is None:
                    return ""
                buf = b""
                deadline = time.monotonic() + REPLY_TIMEOUT_S
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise MountError(f"No reply from the mount to {cmd} — is it powered on and cabled to the adapter?")
                    if not select.select([sock], [], [], remaining)[0]:
                        continue
                    chunk = sock.recv(64)
                    if not chunk:
                        raise ConnectionResetError("adapter closed the connection")
                    buf += chunk
                    if reply == "1" and buf:
                        return buf[:1].decode("ascii", "replace")
                    if reply == "#" and b"#" in buf:
                        return buf.split(b"#", 1)[0].decode("ascii", "replace")
                    if isinstance(reply, int) and len(buf) >= reply:
                        return buf[:reply].decode("ascii", "replace")
            except OSError as exc:
                self._lost(exc)
                raise MountError("Lost the Bluetooth connection to the mount")

    def _expect_ok(self, cmd):
        if self._io(cmd, "1") != "1":
            raise MountError(f"The mount refused {cmd}")

    # ---- mount commands -------------------------------------------------

    def status(self):
        info = {
            "connected": self.connected,
            "address": self.address if self.connected else None,
            "name": self.name if self.connected or self.reconnecting else None,
            "reconnecting": self.reconnecting,
            "rates": SLEW_RATES,
        }
        if not self.connected:
            return info
        try:
            info.update(_decode_gas(self._io(":GAS#", "#")))
            info.update(_decode_gec(self._io(":GEC#", "#")))
            info["mount_ok"] = True
        except MountError as exc:
            info["connected"] = self.connected
            info["mount_ok"] = False
            info["error"] = str(exc)
        info["model"] = self.model
        return info

    def move(self, direction):
        """Starts moving in `direction` (n/s/e/w), or, if already moving that
        way, just extends the hold. Called repeatedly while the button is
        held; the watchdog stops the axis once the calls stop."""
        if direction not in _MOVES:
            raise ValueError("Unknown direction")
        axis, cmd = _MOVES[direction]
        with self._lock:
            if self._moving.get(axis) != direction:
                self._io(cmd, None)
                self._moving[axis] = direction
            self._deadline[axis] = time.monotonic() + MOVE_HOLD_S

    def stop(self, axis=None):
        """Stops one axis ("ra"/"dec"), or everything — including a
        go-to-zero slew — when `axis` is None."""
        with self._lock:
            if axis is None:
                self._moving.clear()
                self._deadline.clear()
                self._expect_ok(":q#")
            elif axis in _STOP_AXIS:
                self._moving.pop(axis, None)
                self._deadline.pop(axis, None)
                self._expect_ok(_STOP_AXIS[axis])
            else:
                raise ValueError("Unknown axis")

    def set_rate(self, rate):
        if not 1 <= rate <= len(SLEW_RATES):
            raise ValueError("Unknown slew rate")
        self._expect_ok(f":SR{rate}#")

    def goto_zero(self):
        """Slews to the mount's zero (home) position: counterweight down,
        pointing at the celestial pole."""
        self._expect_ok(":MH#")

    def _watchdog(self, sock):
        while True:
            time.sleep(0.1)
            with self._lock:
                if self._sock is not sock:
                    return  # disconnected, or replaced by a newer connection
                now = time.monotonic()
                for axis, deadline in list(self._deadline.items()):
                    if now >= deadline:
                        log.info("Move on %s no longer held, stopping", axis)
                        try:
                            self.stop(axis)
                        except MountError:
                            log.exception("Watchdog couldn't stop %s", axis)
                            self._deadline.pop(axis, None)
                            self._moving.pop(axis, None)
                if self._sock is not sock:
                    return
                # Notice a dropped link while idle too, not only on the next
                # command, so reconnecting starts straight away. Readable
                # with nothing to peek means the other end closed; a stray
                # byte is left for _io to discard.
                try:
                    if select.select([sock], [], [], 0)[0] and not sock.recv(1, socket.MSG_PEEK):
                        self._lost("adapter closed the connection")
                        return
                except OSError as exc:
                    self._lost(exc)
                    return
