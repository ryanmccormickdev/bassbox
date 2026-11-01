#!/bin/bash
# bassbox (runs ON THE MAC): run an audio file of bass through the pedal and listen to it.
#
#   scripts/play-file.sh <file> [options]
#
#   <file>        any format macOS can read (wav, aiff, mp3, m4a, flac, ...). A dry bass DI
#                 recording works best; mixes with drums will sound odd through a bass amp.
#   --ab          play the dry input first, then the processed result
#   --hat         also play the processed result out of the pedal's headphone jack
#                 (needs the HAT; the pedal is silent for live bass while it plays)
#   --no-play     just render; don't play anything
#   --peak <dB>   normalise the input peak to <dB> dBFS before processing (default -6, which
#                 is where a hard-played bass lands after scripts/bringup.sh calibration)
#   --raw         keep the file's own levels
#
# The file is processed by the REAL PiPedal engine with whatever preset is currently loaded
# (including unsaved tweaks made in the web UI), via an ALSA loopback card on the Pi, so it
# works with or without the HAT fitted. Results land in renders/<name>.bassdrive.wav (and
# renders/<name>.bassdrive.dry.wav, the conditioned input). Rendering is real-time, plus ~20 s.
set -euo pipefail
PI="${BASSBOX_PI:-<user>@<pi-host>}"
SSH=(ssh -o ServerAliveInterval=5 -o ConnectTimeout=8)
cd "$(dirname "$0")/.."

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 64; }
[ $# -ge 1 ] || usage
IN="$1"; shift
[ -f "$IN" ] || { echo "no such file: $IN" >&2; exit 1; }
AB=0; HAT=0; PLAY=1; LEVEL=(--peak -6)
while [ $# -gt 0 ]; do
    case "$1" in
        --ab) AB=1; shift ;;
        --hat) HAT=1; shift ;;
        --no-play) PLAY=0; shift ;;
        --peak) LEVEL=(--peak "$2"); shift 2 ;;
        --raw) LEVEL=(--raw); shift ;;
        -h|--help) usage ;;
        *) echo "unknown option: $1" >&2; usage ;;
    esac
done

name="$(basename "${IN%.*}" | tr -c 'A-Za-z0-9._-\n' '_')"
mkdir -p renders
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# macOS decodes anything it can play; send the Pi a plain 48 kHz WAV
afconvert -f WAVE -d LEI24@48000 "$IN" "$tmp/$name.wav"
remote_in="/tmp/bassbox-in-$name.wav"
remote_out="/tmp/bassbox-out-$name.bassdrive.wav"
scp -q "$tmp/$name.wav" "$PI:$remote_in"

echo "rendering $(basename "$IN") through the pedal (real time)..."
"${SSH[@]}" "$PI" "sudo /opt/bassbox/scripts/render.sh '$remote_in' '$remote_out' ${LEVEL[*]}"
scp -q "$PI:$remote_out" "renders/$name.bassdrive.wav"
scp -q "$PI:${remote_out%.wav}.dry.wav" "renders/$name.bassdrive.dry.wav"
"${SSH[@]}" "$PI" "sudo rm -f '$remote_in' '$remote_out' '${remote_out%.wav}.dry.wav'"
echo "saved renders/$name.bassdrive.wav"

if [ $PLAY -eq 1 ]; then
    if [ $AB -eq 1 ]; then
        echo "playing: DRY"; afplay "renders/$name.bassdrive.dry.wav"
    fi
    echo "playing: BASS DRIVE"; afplay "renders/$name.bassdrive.wav"
fi

if [ $HAT -eq 1 ]; then
    if ! "${SSH[@]}" "$PI" '/opt/bassbox/scripts/mixer.sh present'; then
        echo "--hat: the HAT is not fitted; skipping playback on the pedal" >&2
        exit 0
    fi
    scp -q "renders/$name.bassdrive.wav" "$PI:$remote_out"
    echo "playing on the pedal's headphone jack (live bass muted meanwhile)..."
    "${SSH[@]}" "$PI" "sudo systemctl stop pipedald; aplay -q -D plughw:wm8960soundcard '$remote_out'; \
        sudo systemctl start pipedald; sudo systemctl restart bassbox-governor; rm -f '$remote_out'"
fi
