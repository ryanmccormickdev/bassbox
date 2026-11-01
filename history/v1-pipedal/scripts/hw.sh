#!/bin/bash
# bassbox: hardware bring-up steps that need the HAT. Runs ON THE PI as root.
# Normally driven from the Mac by scripts/bringup.sh, which handles the human prompts.
#
#   hw.sh detect                 is the WM8960 card there? (card, EEPROM, driver messages)
#   hw.sh probe [--apply]        LOOPBACK CABLE IN: find which input carries the line jack.
#                                Plays a 1 kHz sine at -20 dBFS and records it under each candidate
#                                input profile. --apply writes the winner into config/mixer.conf.
#   hw.sh latency                LOOPBACK CABLE IN: pipedal_latency_test -> results/latency.txt
#   hw.sh record <secs> <out>    record the line input (pipedald stopped) to <out>.wav
#   hw.sh calibrate <wav>        set the input gain so the wav's peak would land at -6 dBFS;
#                                updates config/mixer.conf + config/input_calibration_dbu
#   hw.sh soak <buf> <nbuf> <secs>
#                                LOOPBACK CABLE OUT: run pipedald on the HAT at <buf>x<nbuf> for
#                                <secs>; print xruns, CPU and throttling. Restores pipedal.conf.
#   hw.sh snapshot               alsactl store -> config/mixer.state; amixer contents ->
#                                results/amixer-contents.txt (baseline for check.sh check 4)
#   hw.sh preset                 regenerate the PiPedal config/preset from the repo config
#
# Steps that play test tones stop pipedald first (with the loopback cable in, a running pedal
# would feed back) and start it again afterwards.
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

BASE="$(cd "$(dirname "$0")/.." && pwd)"
. "$BASE/config/pipedal.conf"
CONF="$BASE/config/mixer.conf"
RESULTS="$BASE/results"
DEV="plughw:$CARD"
TOOLS="python3 $BASE/scripts/audio_tools.py"
mkdir -p "$RESULTS"
export PYTHONDONTWRITEBYTECODE=1

log() { echo "hw: $*" >&2; }
die() { echo "hw: ERROR: $*" >&2; exit 1; }
present() { "$BASE/scripts/mixer.sh" present; }
need_card() { present || die "card '$CARD' not found (aplay -l). Is the HAT seated? Run: hw.sh detect"; }
cset() { amixer -q -c "$CARD" cset name="$1" "$2" >/dev/null; }
cget() { amixer -c "$CARD" cget name="$1" | sed -n 's/^  : values=//p'; }

PIPEDALD_WAS_ACTIVE=0
stop_pedal() {
    if systemctl is-active --quiet pipedald; then
        PIPEDALD_WAS_ACTIVE=1
        systemctl stop pipedald
    fi
    # nothing else may hold the card
    if fuser /dev/snd/pcmC*D*[cp] 2>/dev/null | grep -q .; then
        fuser -v /dev/snd/* >&2 || true
        die "something still holds the sound card"
    fi
}
start_pedal() {
    if [ $PIPEDALD_WAS_ACTIVE -eq 1 ]; then
        systemctl start pipedald
        systemctl restart bassbox-governor.service
    fi
}

# Set "<control> = <value>" in config/mixer.conf (exact name match; appends if missing).
conf_set() {
    local name="$1" value="$2" tmp
    tmp="$(mktemp)"
    awk -v n="$name" -v v="$value" '
        BEGIN { done = 0 }
        {
            line = $0; key = line
            sub(/[ \t]+=[ \t].*$/, "", key)
            if (line !~ /^[ \t]*#/ && key == n) { print n " = " v; done = 1 } else print line
        }
        END { if (!done) print n " = " v }' "$CONF" > "$tmp"
    cat "$tmp" > "$CONF"; rm -f "$tmp"
}
conf_get() {
    awk -v n="$1" '!/^[ \t]*#/ { k = $0; sub(/[ \t]+=[ \t].*$/, "", k); if (k == n) { sub(/^[^=]*=[ \t]*/, ""); print; exit } }' "$CONF"
}

# ---------------------------------------------------------------- detect
detect() {
    echo "--- cards"; aplay -l 2>/dev/null | grep '^card' || true
    echo "--- capture"; arecord -l 2>/dev/null | grep '^card' || echo "(none)"
    echo "--- HAT EEPROM"
    if [ -d /proc/device-tree/hat ]; then
        for f in vendor product product_ver; do
            printf '%s: %s\n' "$f" "$(tr -d '\0' < /proc/device-tree/hat/$f 2>/dev/null)"
        done
    else
        echo "(no EEPROM read; the dtoverlay=wm8960-soundcard line in config.txt covers this)"
    fi
    echo "--- driver"; dmesg | grep -iE 'wm8960|simple-audio' | tail -5 || true
    if present; then echo "RESULT: HAT detected as '$CARD'"; else echo "RESULT: HAT NOT detected"; return 1; fi
}

# ---------------------------------------------------------------- probe
# Candidate input profiles (overrides on top of mixer.conf). The line jack is documented as
# LINPUT3; the others are there in case this board revision is wired differently.
PROFILES=(pga-linput3 boost-linput3 pga-linput2 pga-linput1)
profile_lines() {
    local common='Left Boost Mixer LINPUT1 Switch=off
Left Boost Mixer LINPUT2 Switch=off
Left Boost Mixer LINPUT3 Switch=off
Left Input Boost Mixer LINPUT2 Volume=0
Left Input Boost Mixer LINPUT3 Volume=0
Left Input Mixer Boost Switch=on
Capture Switch=on,off'
    echo "$common"
    case "$1" in
        pga-linput3)   echo 'Left Boost Mixer LINPUT3 Switch=on' ;;
        pga-linput2)   echo 'Left Boost Mixer LINPUT2 Switch=on' ;;
        pga-linput1)   echo 'Left Boost Mixer LINPUT1 Switch=on' ;;
        # direct LINPUT3 -> boost at 0 dB; PGA input switch on only so DAPM powers the stage,
        # PGA itself muted
        boost-linput3) printf '%s\n' 'Left Boost Mixer LINPUT3 Switch=on' \
                                     'Left Input Boost Mixer LINPUT3 Volume=5' 'Capture Switch=off,off' ;;
    esac
}
apply_profile() {
    "$BASE/scripts/mixer.sh" apply >/dev/null
    local name value
    while IFS='=' read -r name value; do cset "$name" "$value"; done < <(profile_lines "$1")
}

probe() {
    need_card
    stop_pedal
    local sig="$RESULTS/.probe-sine.wav" rec="$RESULTS/.probe-rec.wav" out="$RESULTS/probe.txt"
    $TOOLS sine "$sig" 1000 -20 3
    : > "$out"
    echo "# hw.sh probe $(date -Is): 1 kHz sine at -20 dBFS out both channels -> loopback -> line in" >> "$out"
    echo "# profile          L_level  L_thdn   R_level   (dBFS RMS / dB)" >> "$out"
    local best="" best_level=-999 p l lt r
    for p in "${PROFILES[@]}"; do
        apply_profile "$p"
        sleep 0.3
        arecord -q -D "$DEV" -f S32_LE -r 48000 -c 2 -d 4 "$rec" &
        sleep 0.3
        aplay -q -D "$DEV" "$sig"
        wait
        read -r l lt r < <($TOOLS tone "$rec" 1000 | awk -F'[= ]' '/^ch1/{l=$3; t=$5} /^ch2/{r=$3} END{print l, t, r}')
        printf '%-16s %8s %8s %9s\n' "$p" "$l" "$lt" "$r" | tee -a "$out"
        # winner: loudest clean left channel (THD+N below -30 dB)
        if awk -v l="$l" -v t="$lt" -v b="$best_level" 'BEGIN{exit !(t < -30 && l > b)}'; then
            best="$p"; best_level="$l"
        fi
    done
    rm -f "$sig" "$rec"
    "$BASE/scripts/mixer.sh" apply >/dev/null
    if [ -z "$best" ] || awk -v l="$best_level" 'BEGIN{exit !(l < -50)}'; then
        echo "RESULT: no input profile picked up the tone. Is the loopback cable from the headphone jack into LINE IN (middle jack)?" | tee -a "$out"
        start_pedal
        return 1
    fi
    echo "RESULT: best=$best (left $best_level dBFS)" | tee -a "$out"
    if [ "${1:-}" = "--apply" ]; then
        local name value
        while IFS='=' read -r name value; do conf_set "$name" "$value"; done < <(profile_lines "$best")
        "$BASE/scripts/mixer.sh" apply >/dev/null
        echo "applied $best to config/mixer.conf" | tee -a "$out"
    fi
    start_pedal
}

# ---------------------------------------------------------------- latency
latency() {
    need_card
    stop_pedal
    local out="$RESULTS/latency.txt"
    {
        echo "# pipedal_latency_test $(date -Is), PiPedal $(dpkg-query -W -f '${Version}' pipedal)"
        echo "# loopback cable: headphone out -> line in; mixer per config/mixer.conf; 48 kHz"
        pipedal_latency_test -r 48000 -i 0 -o 0 "hw:$CARD" 2>&1
    } | tee "$out"
    start_pedal
}

# ---------------------------------------------------------------- record / calibrate
record() {
    need_card
    stop_pedal
    local secs="$1" out="$2"
    log "recording ${secs}s from line in..."
    arecord -q -D "$DEV" -f S32_LE -r 48000 -c 2 -d "$secs" "$out"
    $TOOLS rms "$out" | head -1
    start_pedal
}

# Gain stages, dB:  PGA "Capture Volume" step s: -17.25 + 0.75*s (0..63)
#                   PGA boost "Left Input Boost Mixer LINPUT1 Volume": 0/13/20/29 dB
#                   direct boost "Left Input Boost Mixer LINPUT3 Volume" v: -15 + 3*v (1..7)
#                   ADC digital "ADC PCM Capture Volume" d: -97.5 + 0.5*d (0 dB = 195)
current_gain_db() {
    local pga boost adc lin3 cap
    adc=$(conf_get 'ADC PCM Capture Volume' | cut -d, -f1)
    cap=$(conf_get 'Capture Switch' | cut -d, -f1)
    if [ "$cap" = "on" ]; then
        pga=$(conf_get 'Capture Volume' | cut -d, -f1)
        boost=$(conf_get 'Left Input Boost Mixer LINPUT1 Volume')
        awk -v s="$pga" -v b="$boost" -v d="$adc" 'BEGIN{split("0 13 20 29", B, " "); printf "%.2f\n", -17.25 + 0.75*s + B[b+1] + (-97.5 + 0.5*d)}'
    else
        lin3=$(conf_get 'Left Input Boost Mixer LINPUT3 Volume')
        awk -v v="$lin3" -v d="$adc" 'BEGIN{printf "%.2f\n", -15 + 3*v + (-97.5 + 0.5*d)}'
    fi
}

calibrate() {
    local wav="$1" target=-6 peak cur want
    peak=$($TOOLS rms "$wav" | awk '/^ch1/{print $3}')
    awk -v p="$peak" 'BEGIN{exit !(p < -60)}' && die "recording peak is $peak dBFS: no signal. Bass plugged into LINE IN, volume up?"
    awk -v p="$peak" 'BEGIN{exit !(p > -0.3)}' && log "recording clipped (peak $peak dBFS): result is a lower bound; re-run to refine"
    cur=$(current_gain_db)
    want=$(awk -v c="$cur" -v p="$peak" -v t="$target" 'BEGIN{printf "%.2f\n", c + (t - p)}')
    log "peak $peak dBFS at input gain $cur dB -> want $want dB"
    local cap; cap=$(conf_get 'Capture Switch' | cut -d, -f1)
    if [ "$cap" = "on" ]; then
        # PGA path: smallest PGA boost that keeps the PGA step in range; ADC digital stays 0 dB
        read -r step boost < <(awk -v w="$want" 'BEGIN{
            split("0 13 20 29", B, " ")
            for (i = 1; i <= 4; i++) { s = int((w - B[i] + 17.25) / 0.75 + 0.5); if (s <= 63) break }
            if (s < 0) s = 0; if (s > 63) s = 63
            print s, i - 1 }')
        conf_set 'Capture Volume' "$step,0"
        conf_set 'Left Input Boost Mixer LINPUT1 Volume' "$boost"
        conf_set 'ADC PCM Capture Volume' "195,0"
    else
        # direct path: 3 dB analog steps, then ADC digital for the remainder
        read -r v d < <(awk -v w="$want" 'BEGIN{
            v = int((w + 15) / 3); if (v < 1) v = 1; if (v > 7) v = 7
            d = int(195 + (w - (-15 + 3*v)) / 0.5 + 0.5); if (d < 1) d = 1; if (d > 255) d = 255
            print v, d }')
        conf_set 'Left Input Boost Mixer LINPUT3 Volume' "$v"
        conf_set 'ADC PCM Capture Volume' "$d,0"
    fi
    local new; new=$(current_gain_db)
    # NAM input calibration: dBu at 0 dBFS. WM8960 line full scale ~1 Vrms (+2.2 dBu) at 0 dB gain.
    awk -v g="$new" 'BEGIN{printf "%.1f\n", 2.2 - g}' > "$BASE/config/input_calibration_dbu"
    "$BASE/scripts/mixer.sh" apply >/dev/null
    echo "RESULT: input gain $cur dB -> $new dB (expected peak ~$(awk -v p="$peak" -v a="$new" -v b="$cur" 'BEGIN{printf "%.1f", p + a - b}') dBFS); NAM calibration $(cat "$BASE/config/input_calibration_dbu") dBu"
}

# ---------------------------------------------------------------- soak
restore_buffer() {
    # back to the configured buffer (bringup.sh updates pipedal.conf when it picks one)
    . "$BASE/config/pipedal.conf"
    systemctl stop pipedald
    python3 "$BASE/scripts/pipedal_preset.py" audio "$CARD" "$BUFFER_SIZE" "$NUM_BUFFERS" >/dev/null
    systemctl start pipedald
    systemctl restart bassbox-governor.service
}

soak() {
    need_card
    local buf="$1" nbuf="$2" secs="$3"
    systemctl stop pipedald
    python3 "$BASE/scripts/pipedal_preset.py" audio "$CARD" "$buf" "$nbuf" >/dev/null
    local t0; t0=$(date +%s)
    systemctl start pipedald
    systemctl restart bassbox-governor.service
    local started=""
    for _ in $(seq 60); do
        started=$(journalctl -u pipedald --since "@$t0" -o short-unix --no-pager |
            awk -v d="hw:$CARD" 'index($0, "Audio started. ALSA, " d) {print $1; exit}')
        [ -n "$started" ] && break
        sleep 1
    done
    if [ -z "$started" ]; then
        journalctl -u pipedald --since "@$t0" -o cat --no-pager | tail -5 >&2
        echo "RESULT: buf=${buf}x${nbuf} FAIL (pipedald did not start audio on hw:$CARD)"
        restore_buffer
        return 1
    fi
    sleep "$secs"
    local pid cpu xruns thr
    pid=$(systemctl show -p MainPID --value pipedald)
    cpu=$(top -b -d 5 -n 2 -p "$pid" | awk -v p="$pid" '$1 == p {c = $9} END {print c + 0}')
    # xruns after a 5 s start-up grace period
    xruns=$(journalctl -u pipedald --since "@$t0" -o short-unix --no-pager |
        awk -v s="$started" '$1 > s + 5 && tolower($0) ~ /underrun|overrun|xrun|recovering from alsa/' | wc -l)
    thr=$(vcgencmd get_throttled | cut -d= -f2)
    local verdict=PASS
    { [ "$xruns" -gt 0 ] || [ "$thr" != "0x0" ]; } && verdict=FAIL
    echo "RESULT: buf=${buf}x${nbuf} secs=$secs xruns=$xruns cpu=${cpu}% throttled=$thr $verdict"
    restore_buffer
    [ $verdict = PASS ]
}

# ---------------------------------------------------------------- snapshot / preset
snapshot() {
    need_card
    alsactl store "$CARD"
    alsactl -f "$BASE/config/mixer.state" store "$CARD"
    amixer -c "$CARD" contents > "$RESULTS/amixer-contents.txt"
    echo "RESULT: saved config/mixer.state and results/amixer-contents.txt"
}

preset() {
    systemctl stop pipedald
    python3 "$BASE/scripts/pipedal_preset.py" audio "$CARD" "$BUFFER_SIZE" "$NUM_BUFFERS"
    python3 "$BASE/scripts/pipedal_preset.py" install
    systemctl start pipedald
    systemctl restart bassbox-governor.service
}

cmd="${1:-}"; shift || true
case "$cmd" in
    detect) detect ;;
    probe) probe "$@" ;;
    latency) latency ;;
    record) [ $# -eq 2 ] || die "usage: hw.sh record <secs> <out.wav>"; record "$@" ;;
    calibrate) [ $# -eq 1 ] || die "usage: hw.sh calibrate <wav>"; calibrate "$1" ;;
    soak) [ $# -eq 3 ] || die "usage: hw.sh soak <buf> <nbuf> <secs>"; soak "$@" ;;
    snapshot) snapshot ;;
    preset) preset ;;
    *) sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 64 ;;
esac
