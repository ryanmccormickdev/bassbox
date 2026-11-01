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
# Every change it makes is described in docs/system-changes.md (with how to revert it).
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo $0" >&2; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/opt/bassbox
PROXY=0
[ "${1:-}" = "--proxy" ] && PROXY=1

step() { printf '\n== %s\n' "$*"; }
note() { echo "   $*"; }
REBOOT_NEEDED=0
TMP="$(mktemp -d)"

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
fi
# Never leave our proxy behind (apt would hang whenever the tunnel is down). Only remove the
# file if this run created it.
trap 'if [ $PROXY -eq 1 ]; then rm -f "$APT_PROXY"; fi; rm -rf "$TMP"' EXIT

need_pkgs=()
for p in sox alsa-utils python3-numpy python3-pil fonts-dejavu-core lilv-utils i2c-tools \
         cmake g++ pkg-config libasound2-dev libgpiod-dev device-tree-compiler stress-ng; do
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
# PiPedal stays installed (disabled) as the v1 fallback.
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
    note "PiPedal $(dpkg-query -W -f '${Version}' pipedal) installed (fallback, disabled below)"
else
    note "packages present; PiPedal $installed_ver installed (fallback)"
fi
if [ $PROXY -eq 1 ]; then rm -f "$APT_PROXY"; fi

# ------------------------------------------------------------------ 2. nothing else on the sound card
# PiPedal's package depends on "alsa-base | pipewire"; Trixie has no alsa-base, so apt installs
# PipeWire. Masking its user units keeps it installed (dependency satisfied) but never running.
step "PipeWire/WirePlumber user units: masked; PiPedal: disabled"
systemctl --global mask pipewire.socket pipewire.service pipewire-pulse.socket \
    pipewire-pulse.service wireplumber.service >/dev/null 2>&1 || true
pkill -x wireplumber 2>/dev/null || true
pkill -x pipewire-pulse 2>/dev/null || true
pkill -x pipewire 2>/dev/null || true
for u in pipedald.service pipedaladmind.service; do
    if systemctl list-unit-files "$u" >/dev/null 2>&1; then
        systemctl disable --now "$u" >/dev/null 2>&1 || true
    fi
done
note "pipedald: $(systemctl is-enabled pipedald 2>/dev/null; true)"

# ------------------------------------------------------------------ 3. config.txt overlays
CFG=/boot/firmware/config.txt
backup_cfg() { cp -n "$CFG" "$CFG.bassbox-orig"; cp "$CFG" "$CFG.bassbox-$(date +%Y%m%d%H%M%S)"; }
if ! grep -q '^dtoverlay=wm8960-soundcard' "$CFG"; then
    step "config.txt: adding dtoverlay=wm8960-soundcard"
    backup_cfg
    printf '\n[all]\n# bassbox: RaspiAudio ULTRA++ (WM8960). Fallback if HAT EEPROM does not auto-load it.\ndtoverlay=wm8960-soundcard\n' >> "$CFG"
    REBOOT_NEEDED=1
else
    note "config.txt already has the WM8960 overlay"
fi

# 3a. bassbox-wm8960: DAPM "Speaker Switch" so the class-D amp is never powered (ADC noise)
OVL=/boot/firmware/overlays/bassbox-wm8960.dtbo
dtc -@ -q -I dts -O dtb -o "$TMP/bassbox-wm8960.dtbo" "$DEST/config/overlays/bassbox-wm8960.dts"
if ! cmp -s "$TMP/bassbox-wm8960.dtbo" "$OVL"; then
    step "installing $OVL"
    install -m 644 "$TMP/bassbox-wm8960.dtbo" "$OVL"
    REBOOT_NEEDED=1
fi
if ! grep -q '^dtoverlay=bassbox-wm8960' "$CFG"; then
    step "config.txt: adding dtoverlay=bassbox-wm8960 (after wm8960-soundcard)"
    backup_cfg
    printf '\n[all]\n# bassbox: speaker pin switch for the WM8960 card (config/overlays/bassbox-wm8960.dts)\ndtoverlay=bassbox-wm8960\n' >> "$CFG"
    REBOOT_NEEDED=1
else
    note "config.txt already has the bassbox-wm8960 overlay"
fi

# ------------------------------------------------------------------ 3b. touchscreen (ST7796S + XPT2046 on SPI0)
# Wiring and the board's 3.3 V mod (J1 bridged, VCC on Pi 3.3 V): see docs/hardware.md.
# The init sequence carries the rotation: 180 = upright in the enclosure (portrait).
FW=/lib/firmware/st7796s.bin
python3 "$DEST/display/make_st7796s_bin.py" --rotate=180 "$TMP/st7796s.bin" >/dev/null
if ! cmp -s "$TMP/st7796s.bin" "$FW"; then
    step "display: installing $FW"
    install -m 644 "$TMP/st7796s.bin" "$FW"
    REBOOT_NEEDED=1
else
    note "display firmware up to date"
fi
if ! grep -q '^dtoverlay=mipi-dbi-spi' "$CFG"; then
    step "config.txt: adding display + touch overlays (config/display.txt)"
    backup_cfg
    { printf '\n[all]\n'; cat "$DEST/config/display.txt"; } >> "$CFG"
    REBOOT_NEEDED=1
else
    note "config.txt already has the display overlays"
fi
# Touch calibration is device state (display/touchtest.py rewrites it); seed it from the repo.
if [ ! -f /var/lib/bassbox/touch_cal.json ]; then
    install -D -m 644 "$DEST/config/touch_cal.json" /var/lib/bassbox/touch_cal.json
    note "seeded /var/lib/bassbox/touch_cal.json"
fi

# ------------------------------------------------------------------ 3c. kernel command line + quiet panel
# Append-only token edits (CPU3 isolation for the audio thread, quiet boot, kernel console on
# serial only). scripts/cmdline.py keeps every other token untouched and backs the file up.
if ! python3 "$DEST/scripts/cmdline.py" --check >/dev/null; then
    step "cmdline.txt"
    cp -n /boot/firmware/cmdline.txt /boot/firmware/cmdline.txt.bak-rt
    python3 "$DEST/scripts/cmdline.py"
    REBOOT_NEEDED=1
else
    note "cmdline.txt already has the RT/quiet tokens"
fi
# No login prompt on the panel (serial and ssh logins are unaffected).
if [ "$(systemctl is-enabled getty@tty1.service 2>/dev/null)" != "masked" ]; then
    systemctl mask getty@tty1.service >/dev/null
    note "getty@tty1 masked"
fi

# ------------------------------------------------------------------ 4. systemd units
step "systemd units"
UNITS=(bassbox-governor.service bassbox-mixer.service bassbox-engine.service)
[ -f "$DEST/config/systemd/bassbox-ui.service" ] && UNITS+=(bassbox-ui.service)  # the UI's unit
for u in "${UNITS[@]}"; do
    install -m 644 "$DEST/config/systemd/$u" /etc/systemd/system/
done
install -d /etc/systemd/system/pipedald.service.d
install -m 644 "$DEST/config/systemd/pipedald.service.d/bassbox.conf" /etc/systemd/system/pipedald.service.d/
systemctl daemon-reload
systemctl enable "${UNITS[@]}" >/dev/null 2>&1
note "enabled: ${UNITS[*]}"
systemctl start bassbox-governor.service
note "governor: $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_governor)"

# ------------------------------------------------------------------ 5. mixer
step "WM8960 mixer"
if "$DEST/scripts/mixer.sh" present; then
    BASSBOX_CARD_WAIT=1 "$DEST/scripts/mixer.sh" apply
    "$DEST/scripts/mixer.sh" verify >/dev/null && note "mixer verified" || note "WARNING: mixer verify failed (run scripts/mixer.sh verify)"
else
    note "HAT not detected: mixer profile will be applied at boot once it is fitted"
fi

# ------------------------------------------------------------------ 6. engine: build + install
step "bassbox-engine: build (engine/build) + install (/usr/local/bin)"
BUILD="$DEST/engine/build"
cmake -S "$DEST/engine" -B "$BUILD" -DCMAKE_BUILD_TYPE=Release >/dev/null
# CPU0-1 only: core 2 may be benchmarking effects, core 3 is the audio core.
if ! taskset -c 0,1 cmake --build "$BUILD" -j2 >"$TMP/build.log" 2>&1; then
    grep -E "error" "$TMP/build.log" | head -20; tail -5 "$TMP/build.log"
    echo "engine build FAILED: nothing installed (the running engine is untouched)" >&2
    exit 1
fi
grep -E "warning: " "$TMP/build.log" | sort -u | head -5 || true
"$BUILD/bb-selftest" >"$TMP/selftest.log" 2>&1 && note "bb-selftest: $(tail -1 "$TMP/selftest.log")" \
    || { tail -20 "$TMP/selftest.log"; echo "bb-selftest FAILED" >&2; exit 1; }
cmake --install "$BUILD" >/dev/null
# restart the engine only when its binary, config or unit changed (signature kept in the state dir)
sig=$(cat /usr/local/bin/bassbox-engine "$DEST/config/engine.conf" "$DEST/config/jacks.conf" \
      /etc/systemd/system/bassbox-engine.service | sha256sum | cut -c1-16)
note "installed bassbox-engine, bb-render, bb-catalog to /usr/local/bin"

# ------------------------------------------------------------------ 7. state + start
install -d -m 755 /var/lib/bassbox /var/lib/bassbox/presets
if [ ! -f /var/lib/bassbox/live.json ]; then
    # First boot: start safe (empty chain, output -12 dB) instead of the first factory preset,
    # in case a loopback cable or a loud amp is connected. Presets are one tap away.
    cat > /var/lib/bassbox/live.json <<'JSON'
{"version": 1, "state": {"master": {"in_gain_db": 0, "out_gain_db": -12, "bypass": false}, "chain": []}, "current": "", "baseline": null}
JSON
    note "seeded /var/lib/bassbox/live.json (empty chain, out -12 dB)"
fi
if [ "$(cat /var/lib/bassbox/.engine-sig 2>/dev/null)" != "$sig" ] || ! systemctl is-active --quiet bassbox-engine; then
    systemctl restart bassbox-engine.service
    echo "$sig" > /var/lib/bassbox/.engine-sig
    note "bassbox-engine restarted (binary/config/unit changed)"
fi
sleep 2
note "bassbox-engine: $(systemctl is-active bassbox-engine)"
if [ -f /etc/systemd/system/bassbox-ui.service ] && ! systemctl is-active --quiet bassbox-ui; then
    systemctl start bassbox-ui.service || true
fi
[ -f /etc/systemd/system/bassbox-ui.service ] && note "bassbox-ui: $(systemctl is-active bassbox-ui)"

# ------------------------------------------------------------------ done
step "done"
if [ "$REBOOT_NEEDED" = 1 ]; then
    echo "   config.txt, cmdline.txt, overlays or display firmware changed: reboot to apply (sudo systemctl reboot)"
fi
"$DEST/scripts/mixer.sh" present || echo "   NOTE: HAT not detected. Fit it and power-cycle."
