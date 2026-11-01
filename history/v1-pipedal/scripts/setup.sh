#!/bin/bash
# bassbox: build the pedal from a freshly flashed Raspberry Pi OS Lite (Trixie) image.
# Runs ON THE PI as root. Idempotent: safe to re-run; only changes what differs.
#
#   sudo /opt/bassbox/scripts/setup.sh [--proxy]
#
#   --proxy   use the Mac's reverse SOCKS tunnel (ssh -R 1080) for apt/curl while running, and
#             remove the apt proxy again on exit, so the finished pedal never depends on it.
#
# From the Mac, `scripts/push.sh --setup` syncs the repo to /opt/bassbox and runs this.
# Never touches the Pi's networking configuration.
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/bassbox
PROXY=0
[ "${1:-}" = "--proxy" ] && PROXY=1

step() { printf '\n== %s\n' "$*"; }
note() { echo "   $*"; }

# shellcheck source=../config/pipedal.conf
. "$SRC/config/pipedal.conf"

# ------------------------------------------------------------------ 0. repo in /opt/bassbox
if [ "$SRC" != "$DEST" ]; then
    step "installing repo to $DEST"
    mkdir -p "$DEST"
    cp -a "$SRC/." "$DEST/"
    exec "$DEST/scripts/setup.sh" "$@"
fi
chmod +x "$DEST"/scripts/*.sh "$DEST"/scripts/*.py

# ------------------------------------------------------------------ 1. packages
APT_PROXY=/etc/apt/apt.conf.d/99proxy
if [ $PROXY -eq 1 ]; then
    echo 'Acquire::http::Proxy "socks5h://127.0.0.1:1080"; Acquire::https::Proxy "socks5h://127.0.0.1:1080";' > "$APT_PROXY"
    export ALL_PROXY=socks5h://127.0.0.1:1080
    trap 'rm -f "$APT_PROXY"; echo "   removed $APT_PROXY"' EXIT
fi

need_pkgs=()
for p in sox alsa-utils python3-numpy lilv-utils i2c-tools; do
    dpkg -s "$p" >/dev/null 2>&1 || need_pkgs+=("$p")
done
installed_ver=$(dpkg-query -W -f '${Version}' pipedal 2>/dev/null || true)
if [ ${#need_pkgs[@]} -gt 0 ] || [ "$installed_ver" != "$PIPEDAL_VERSION" ]; then
    step "packages"
    # The Pi has no RTC; a stale clock makes apt reject repo signatures ("not live until").
    # scripts/push.sh sets the clock from the Mac before running this.
    apt-get update -q
fi
if [ ${#need_pkgs[@]} -gt 0 ]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${need_pkgs[@]}"
fi
if [ "$installed_ver" != "$PIPEDAL_VERSION" ]; then
    deb="$DEST/debs/pipedal_${PIPEDAL_VERSION}_arm64.deb"
    if [ ! -f "$deb" ]; then
        mkdir -p "$DEST/debs"
        curl -fsSL -o "$deb.part" \
            "https://github.com/rerdavies/pipedal/releases/download/v${PIPEDAL_VERSION}/pipedal_${PIPEDAL_VERSION}_arm64.deb"
        mv "$deb.part" "$deb"
    fi
    echo "$PIPEDAL_SHA256  $deb" | sha256sum -c --quiet
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$deb"
    note "PiPedal $(dpkg-query -W -f '${Version}' pipedal) installed"
else
    note "PiPedal $installed_ver already installed"
fi

# ------------------------------------------------------------------ 2. nothing else on the sound card
# PiPedal's package depends on "alsa-base | pipewire"; Trixie has no alsa-base, so apt installs
# PipeWire. Masking its user units keeps it installed (dependency satisfied) but never running.
step "PipeWire/WirePlumber user units: masked"
systemctl --global mask pipewire.socket pipewire.service pipewire-pulse.socket \
    pipewire-pulse.service wireplumber.service >/dev/null 2>&1 || true
pkill -x wireplumber 2>/dev/null || true
pkill -x pipewire-pulse 2>/dev/null || true
pkill -x pipewire 2>/dev/null || true

# ------------------------------------------------------------------ 3. WM8960 overlay (fallback for HAT EEPROM)
CFG=/boot/firmware/config.txt
if ! grep -q '^dtoverlay=wm8960-soundcard' "$CFG"; then
    step "config.txt: adding dtoverlay=wm8960-soundcard"
    cp -n "$CFG" "$CFG.bassbox-orig"
    cp "$CFG" "$CFG.bassbox-$(date +%Y%m%d%H%M%S)"
    printf '\n# bassbox: RaspiAudio ULTRA++ (WM8960). Fallback if HAT EEPROM does not auto-load it.\ndtoverlay=wm8960-soundcard\n' >> "$CFG"
    REBOOT_NEEDED=1
else
    note "config.txt already has the WM8960 overlay"
fi

# ------------------------------------------------------------------ 4. systemd units
step "systemd units"
install -m 644 "$DEST/config/systemd/bassbox-governor.service" /etc/systemd/system/
install -m 644 "$DEST/config/systemd/bassbox-mixer.service" /etc/systemd/system/
install -d /etc/systemd/system/pipedald.service.d
install -m 644 "$DEST/config/systemd/pipedald.service.d/bassbox.conf" /etc/systemd/system/pipedald.service.d/
systemctl daemon-reload
systemctl enable bassbox-governor.service bassbox-mixer.service pipedald.service >/dev/null 2>&1
systemctl start bassbox-governor.service
note "governor: $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor)"

# ------------------------------------------------------------------ 5. mixer
step "WM8960 mixer"
if [ -f "$DEST/config/mixer.state" ]; then
    install -m 644 "$DEST/config/mixer.state" /var/lib/alsa/asound.state
    note "installed config/mixer.state as /var/lib/alsa/asound.state"
fi
if "$DEST/scripts/mixer.sh" present; then
    [ -f "$DEST/config/mixer.state" ] && alsactl -f /var/lib/alsa/asound.state restore wm8960soundcard 2>/dev/null || true
    BASSBOX_CARD_WAIT=1 "$DEST/scripts/mixer.sh" apply
    "$DEST/scripts/mixer.sh" verify >/dev/null && note "mixer verified" || note "WARNING: mixer verify failed (run scripts/mixer.sh verify)"
else
    note "HAT not detected: mixer profile will be applied at boot once it is fitted"
fi

# ------------------------------------------------------------------ 6. PiPedal config
step "PiPedal: audio device, routing, 'Bass Drive' boot preset"
systemctl stop pipedald 2>/dev/null || true
# PiPedal creates /var/pipedal (presets, factory banks) on first start; make sure that happened.
if [ ! -f /var/pipedal/presets/index.banks ]; then
    systemctl start pipedald
    for _ in $(seq 60); do [ -f /var/pipedal/presets/index.banks ] && break; sleep 1; done
    systemctl stop pipedald
fi
python3 "$DEST/scripts/pipedal_preset.py" audio "$CARD" "$BUFFER_SIZE" "$NUM_BUFFERS"
python3 "$DEST/scripts/pipedal_preset.py" install
systemctl start pipedald
# pipedald's admin daemon restores the governor it saw at start when it stops; re-assert ours.
systemctl restart bassbox-governor.service
note "pipedald $(systemctl is-active pipedald), governor $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor)"

# ------------------------------------------------------------------ done
step "done"
if [ "${REBOOT_NEEDED:-0}" = 1 ]; then
    echo "   config.txt changed: reboot to load the WM8960 overlay (sudo systemctl reboot)"
fi
"$DEST/scripts/mixer.sh" present || echo "   NOTE: HAT not detected. Fit it, power-cycle, then run scripts/bringup.sh from the Mac."
