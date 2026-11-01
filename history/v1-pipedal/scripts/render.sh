#!/bin/bash
# bassbox: render an audio file through the LIVE PiPedal preset. Runs ON THE PI as root.
#
#   render.sh <in.wav> <out.wav> [--peak <dBFS> | --raw]
#
# How: PiPedal is pointed at an ALSA loopback card (snd-aloop) for the duration of the render.
# The input is played into the loopback, PiPedal processes it in real time with exactly the
# preset that is currently loaded (including unsaved UI tweaks), and its output is recorded.
# Afterwards the normal audio config is restored and PiPedal restarted. The pedal is silent
# while a render runs (takes as long as the file, plus ~20 s of PiPedal restarts).
#
# Input conditioning mirrors the hardware: downmixed to mono, placed on the LEFT channel
# (right silent), 48 kHz. By default the peak is normalised to -6 dBFS, which is where
# scripts/calibrate.sh puts a hard-played bass. --peak changes that, --raw keeps file levels.
set -euo pipefail

IN="${1:?usage: render.sh <in.wav> <out.wav> [--peak dBFS|--raw]}"
OUT="${2:?usage: render.sh <in.wav> <out.wav> [--peak dBFS|--raw]}"
shift 2
PEAK=-6
while [ $# -gt 0 ]; do
    case "$1" in
        --peak) PEAK="$2"; shift 2 ;;
        --raw)  PEAK=""; shift ;;
        *) echo "unknown option $1" >&2; exit 64 ;;
    esac
done
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

BASE="$(cd "$(dirname "$0")/.." && pwd)"
CFG=/var/pipedal/AudioConfig.json
WORK="$(mktemp -d /tmp/bassbox-render.XXXXXX)"
LOOP=Loopback
SAVED_CFG="$WORK/AudioConfig.json.saved"

log() { echo "render: $*" >&2; }

RESTORED=0
restore() {
    [ $RESTORED -eq 1 ] && return
    RESTORED=1
    set +e
    log "restoring normal audio config"
    systemctl stop pipedald
    if [ -f "$SAVED_CFG" ]; then
        cp "$SAVED_CFG" "$CFG"; chown pipedal_d:pipedal_d "$CFG"
    fi
    systemctl start pipedald
    systemctl restart bassbox-governor.service 2>/dev/null
    modprobe -r snd-aloop 2>/dev/null
    set -e
}
cleanup() { restore; rm -rf "$WORK"; }

# 1. condition the input: mono -> left channel, right silent, 48 kHz / 32-bit, 0.5 s lead-in, 2 s tail
NORM=()
[ -n "$PEAK" ] && NORM=(gain -n "$PEAK")
sox -q "$IN" -r 48000 -b 32 -e signed-integer -c 1 "$WORK/mono.wav" "${NORM[@]}"
sox -q "$WORK/mono.wav" "$WORK/in.wav" remix 1 0 pad 0.5 2
DUR=$(soxi -D "$WORK/in.wav")
log "input $(basename "$IN"): ${DUR}s incl. padding, peak $(sox "$WORK/mono.wav" -n stat 2>&1 | awk '/Maximum amplitude/{a=$3} /Minimum amplitude/{b=-$3} END{m=(a>b?a:b); printf "%.1f dBFS", 20*log(m)/log(10)}')"

# 2. loopback card: one substream per device so the pairing is fixed:
#    PiPedal hw:Loopback,0,0 (in+out)  <->  us hw:Loopback,1,0
if [ ! -e /proc/asound/$LOOP ]; then
    modprobe snd-aloop id=$LOOP pcm_substreams=1
    for _ in $(seq 20); do [ -e /proc/asound/$LOOP ] && break; sleep 0.25; done
fi
[ -e /proc/asound/$LOOP ] || { log "snd-aloop did not load"; exit 1; }

trap cleanup EXIT
cp "$CFG" "$SAVED_CFG" 2>/dev/null || true
systemctl stop pipedald
python3 "$BASE/scripts/pipedal_preset.py" audio "$LOOP" 256 3 >/dev/null
SINCE="$(date '+%Y-%m-%d %H:%M:%S')"
systemctl start pipedald

# 3. wait for PiPedal to open the loopback
for _ in $(seq 60); do
    if journalctl -u pipedald --since "$SINCE" --no-pager -o cat | grep -q "Audio started. ALSA, hw:$LOOP"; then
        break
    fi
    sleep 0.5
done
journalctl -u pipedald --since "$SINCE" --no-pager -o cat | grep -q "Audio started. ALSA, hw:$LOOP" ||
    { log "PiPedal did not start on the loopback:"; journalctl -u pipedald --since "$SINCE" --no-pager -o cat | tail -5 >&2; exit 1; }
sleep 2   # let the preset finish loading the NAM model (start-up xruns happen here; ignored)
PLAY_SINCE="$(date '+%Y-%m-%d %H:%M:%S')"

# 4. play in, record out (record starts first; both sides use plughw so formats adapt)
log "rendering (real time)..."
REC_SECS=$(python3 -c "import math; print(math.ceil($DUR + 1))")
arecord -q -D plughw:$LOOP,1,0 -f S32_LE -r 48000 -c 2 -d "$REC_SECS" "$WORK/rec.wav" &
REC=$!
sleep 0.2
aplay -q -D plughw:$LOOP,1,0 "$WORK/in.wav"
# once aplay closes, PiPedal's input underruns continuously (nothing feeds it): not counted
PLAY_UNTIL="$(date -d '-1 sec' '+%Y-%m-%d %H:%M:%S')"
wait $REC
XRUNS=$(journalctl -u pipedald --since "$PLAY_SINCE" --until "$PLAY_UNTIL" --no-pager -o cat |
    grep -ciE "Recovering from ALSA|xrun" || true)
restore   # pedal back on its real audio device before the (slow) post-processing
if [ "$XRUNS" -gt 0 ]; then
    log "WARNING: $XRUNS xruns while rendering (output may have clicks)"
else
    log "no xruns while rendering"
fi

# 5. line the output up with the dry input (removes lead-in + loopback latency) and write both
sox -q "$WORK/mono.wav" -b 24 "${OUT%.wav}.dry.wav"
python3 "$BASE/scripts/audio_tools.py" align "$WORK/rec.wav" "${OUT%.wav}.dry.wav" "$OUT" >&2
log "wrote $OUT (and ${OUT%.wav}.dry.wav)"
sox "$OUT" -n stat 2>&1 | awk '/RMS     amplitude/{r=$3} /Maximum amplitude/{a=$3} END{printf "render: output peak %.1f dBFS, RMS %.1f dBFS\n", 20*log(a)/log(10), 20*log(r)/log(10)}' >&2
