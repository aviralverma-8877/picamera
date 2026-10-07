#!/bin/sh
# All WiFi mode changes for Astro Pi Camera live here, so the boot-time
# failover, the manual ap-mode.sh/sta-mode.sh toggles, and the dashboard's
# "WiFi networks" card all share one definition of each step.
#
#   wifi.sh ap        save a scan of nearby networks, then start the AstroCamera AP
#   wifi.sh home      join the best saved network in range, else fall back to the AP
#   wifi.sh scan      print nearby + saved networks (for the dashboard)
#   wifi.sh connect   join a network given on stdin, saving it for next time:
#                       line 1  SSID
#                       line 2  password (empty for an open or already-saved network)
#                       line 3  key management: none | wpa-psk | sae
#                       line 4  1 if the SSID was typed by hand (hidden network), else 0
#                     If it can't join, it goes back to the previous network,
#                     or the AP, and leaves the reason in $RESULT_FILE.
#
# The password arrives on stdin rather than as an argument so it doesn't
# show up in `ps` for the sudo/wifi.sh process. (It's briefly an argument
# to the nmcli call that stores it, which runs as root.)
#
# Needs root; re-runs itself through sudo when started by hand as pi.
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
set -e

AP_NAME="AstroPiCamAP"
STATE_DIR=/run/astro-pi-cam
SCAN_CACHE="$STATE_DIR/scan-cache"
RESULT_FILE="$STATE_DIR/wifi-result"
LOCK_FILE="$STATE_DIR/wifi.lock"
NEW_CONN="astro-pi-cam-new-wifi"
CONNECT_WAIT=30

mkdir -p "$STATE_DIR"
chmod 755 "$STATE_DIR"

ap_uuid() {
    nmcli -g connection.uuid connection show "$AP_NAME" 2>/dev/null || true
}

active_uuid() {
    nmcli -g GENERAL.CON-UUID device show wlan0 2>/dev/null || true
}

# Saved WiFi profiles other than the AP, in the order NetworkManager itself
# would prefer them: highest autoconnect priority, then most recently used.
saved_wifi_uuids() {
    ap=$(ap_uuid)
    nmcli -t -f UUID,TYPE,AUTOCONNECT-PRIORITY,TIMESTAMP connection show \
        | awk -F: '$2=="802-11-wireless"' \
        | sort -t: -k3,3nr -k4,4nr \
        | cut -d: -f1 \
        | grep -vx "${ap:-none}" || true
}

profile_ssid() {
    nmcli -g 802-11-wireless.ssid connection show uuid "$1" 2>/dev/null || true
}

# nmcli -g may escape ':' and '\' in values; compare against both spellings.
profile_has_ssid() {
    s=$(profile_ssid "$1")
    escaped=$(printf '%s' "$2" | sed 's/\\/\\\\/g; s/:/\\:/g')
    [ "$s" = "$2" ] || [ "$s" = "$escaped" ]
}

saved_uuid_for_ssid() {
    for u in $(saved_wifi_uuids); do
        if profile_has_ssid "$u" "$1"; then
            echo "$u"
            return 0
        fi
    done
}

# Creates $NEW_CONN for the network being joined; fails (without exiting
# the script) if nmcli rejects any of it, e.g. a password of invalid length.
create_profile() {
    nmcli connection delete "$NEW_CONN" >/dev/null 2>&1 || true
    nmcli connection add type wifi ifname wlan0 con-name "$NEW_CONN" \
        autoconnect yes ssid "$1" >/dev/null || return 1
    if [ "$4" = "1" ]; then
        nmcli connection modify "$NEW_CONN" 802-11-wireless.hidden yes || return 1
    fi
    case "$3" in
        wpa-psk|sae)
            nmcli connection modify "$NEW_CONN" \
                wifi-sec.key-mgmt "$3" wifi-sec.psk "$2" || return 1
            ;;
    esac
}

rescan_list() {
    nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list --rescan "$1" 2>/dev/null
}

# Scanning only works while wlan0 isn't hosting the AP (single radio), so
# every path that's about to start the AP saves a scan first; the dashboard
# shows that list while in AP mode.
cache_scan() {
    if rescan_list yes > "$SCAN_CACHE.tmp" || rescan_list no > "$SCAN_CACHE.tmp"; then
        mv "$SCAN_CACHE.tmp" "$SCAN_CACHE"
        chmod 644 "$SCAN_CACHE"
    else
        rm -f "$SCAN_CACHE.tmp"
    fi
}

start_ap() {
    if [ "$(active_uuid)" != "$(ap_uuid)" ]; then
        cache_scan
    fi
    nmcli connection up "$AP_NAME"
    echo "AP mode active. Connect a phone to SSID 'AstroCamera' (open, no password) and browse to http://4.3.2.1"
}

go_home() {
    nmcli connection down "$AP_NAME" >/dev/null 2>&1 || true
    rescan_list yes >/dev/null || sleep 3
    for u in $(saved_wifi_uuids); do
        if nmcli --wait 20 connection up uuid "$u" >/dev/null 2>&1; then
            echo "Joined saved network '$(profile_ssid "$u")'."
            return 0
        fi
    done
    echo "No saved network could be reached — falling back to the AstroCamera AP."
    start_ap
    return 1
}

write_result() {
    # state / ssid / message / unix time, one per line (the app rejects
    # SSIDs containing newlines, so this can't be confused).
    printf '%s\n%s\n%s\n%s\n' "$1" "$2" "$3" "$(date +%s)" > "$RESULT_FILE.tmp"
    chmod 644 "$RESULT_FILE.tmp"
    mv "$RESULT_FILE.tmp" "$RESULT_FILE"
}

do_connect() {
    IFS= read -r ssid || true
    IFS= read -r psk || true
    IFS= read -r keymgmt || true
    IFS= read -r hidden || true
    [ -n "$ssid" ] || { echo "No SSID given" >&2; exit 2; }

    write_result connecting "$ssid" ""
    prev=$(active_uuid)
    existing=$(saved_uuid_for_ssid "$ssid")
    created=""

    if [ -n "$existing" ] && [ -z "$psk" ]; then
        target=$existing
    else
        # A fresh profile under a temporary name: an existing profile for
        # this SSID (old password) is only replaced once the new one has
        # actually connected, so a typo doesn't lose a working password.
        if ! err=$(create_profile "$ssid" "$psk" "$keymgmt" "$hidden" 2>&1); then
            nmcli connection delete "$NEW_CONN" >/dev/null 2>&1 || true
            write_result failed "$ssid" "$(printf '%s' "$err" | tr '\n' ' ')"
            echo "Could not save '$ssid': $err" >&2
            return 1
        fi
        target=$(nmcli -g connection.uuid connection show "$NEW_CONN")
        created=1
    fi

    # Leave the AP first so the radio is free to scan for the network.
    nmcli connection down "$AP_NAME" >/dev/null 2>&1 || true
    rescan_list yes >/dev/null || sleep 3

    if err=$(nmcli --wait "$CONNECT_WAIT" connection up uuid "$target" 2>&1); then
        if [ -n "$created" ]; then
            for u in $(saved_wifi_uuids); do
                if [ "$u" != "$target" ] && profile_has_ssid "$u" "$ssid"; then
                    nmcli connection delete uuid "$u" >/dev/null 2>&1 || true
                fi
            done
            nmcli connection modify uuid "$target" connection.id "$ssid" || true
        fi
        write_result connected "$ssid" ""
        echo "Joined '$ssid'."
        return 0
    fi

    echo "Could not join '$ssid': $err" >&2
    if [ -n "$created" ]; then
        nmcli connection delete uuid "$target" >/dev/null 2>&1 || true
    fi
    write_result failed "$ssid" "$(printf '%s' "$err" | tr '\n' ' ')"
    # Back to where we were: the previous network if there was one (and it
    # wasn't the AP), otherwise — or if that's gone too — the AP.
    if [ -n "$prev" ] && [ "$prev" != "$(ap_uuid)" ] && [ "$prev" != "$target" ] \
        && nmcli --wait 20 connection up uuid "$prev" >/dev/null 2>&1; then
        return 1
    fi
    start_ap
    return 1
}

do_scan() {
    # Live scan unless the radio is busy hosting the AP or another mode
    # change is in progress — then the list saved before the AP started.
    if [ "$(active_uuid)" != "$(ap_uuid)" ] && flock -n 9 && rescan_list yes > "$SCAN_CACHE.tmp"; then
        mv "$SCAN_CACHE.tmp" "$SCAN_CACHE"
        chmod 644 "$SCAN_CACHE"
        echo "SOURCE live"
    elif [ -f "$SCAN_CACHE" ]; then
        # Its age in seconds, worked out here: the phone's clock and the
        # Pi's (no clock battery) can disagree a lot.
        echo "SOURCE cached $(( $(date +%s) - $(stat -c %Y "$SCAN_CACHE") ))"
    else
        echo "SOURCE none"
    fi
    if [ -f "$SCAN_CACHE" ]; then
        sed 's/^/NETWORK /' "$SCAN_CACHE"
    fi
    for u in $(saved_wifi_uuids); do
        echo "SAVED $(profile_ssid "$u")"
    done
}

exec 9>"$LOCK_FILE"
case "$1" in
    scan)
        do_scan
        ;;
    ap)
        flock 9
        start_ap
        ;;
    home)
        flock 9
        go_home
        ;;
    connect)
        flock 9
        do_connect
        ;;
    *)
        echo "usage: $0 ap|home|scan|connect" >&2
        exit 2
        ;;
esac
