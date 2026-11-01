#!/bin/bash
# bassbox: hear what an audio file sounds like through the pedal's CURRENT sound, on the Mac.
# Runs ON THE MAC. No bass, no HAT and no PiPedal needed.
#
#   scripts/play-file.sh <audio file> [--ab] [--no-play]
#     --ab        play the dry file first, then the pedal version
#     --no-play   only render (files in renders/)
#
# 1. fetches the Pi's live state (chain + master) over the engine socket, falling back to
#    /var/lib/bassbox/live.json, or uses the last saved copy when the Pi is unreachable;
# 2. converts the file to 48 kHz mono WAV (afconvert, else sox);
# 3. renders it with the Mac-built bb-render: the same engine code as the pedal (builds it if
#    needed: cmake -S engine -B engine/build);
# 4. normalises both versions to -1 dBFS peak and plays them with afplay at half volume.
# A dry DI bass track works best. Outputs: renders/<name>-dry.wav, renders/<name>-<preset>.wav
set -euo pipefail
PI="${BASSBOX_PI:-<user>@<pi-host>}"
cd "$(dirname "$0")/.."
IN="${1:-}"; [ -n "$IN" ] && [ -f "$IN" ] || { sed -n '5,8p' "$0" >&2; exit 64; }
AB=0; PLAY=1
for a in "${@:2}"; do
    case "$a" in --ab) AB=1 ;; --no-play) PLAY=0 ;; *) echo "unknown option $a" >&2; exit 64 ;; esac
done
mkdir -p renders
name=$(basename "${IN%.*}")
STATE=renders/live-state.json

# 1. live state
if ssh -o ConnectTimeout=5 -o ServerAliveInterval=5 "$PI" \
       'python3 /opt/bassbox/scripts/bbctl.py state 2>/dev/null || sudo cat /var/lib/bassbox/live.json' > "$STATE.tmp" 2>/dev/null \
   && python3 - "$STATE.tmp" "$STATE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
s = d.get("state", d)          # bbctl response {"state": ...} or live.json {"state": ...}
if "chain" not in s: sys.exit(1)
json.dump(s, open(sys.argv[2], "w"), indent=1)
PY
then
    rm -f "$STATE.tmp"
else
    rm -f "$STATE.tmp"
    [ -f "$STATE" ] || { echo "Pi unreachable and no saved state in $STATE" >&2; exit 1; }
    echo "Pi unreachable: using the last fetched state ($STATE)"
fi
preset=$(python3 -c "import json;s=json.load(open('$STATE'));print(s.get('name') or 'live')")
chain=$(python3 -c "import json;s=json.load(open('$STATE'));print(' -> '.join(c['type']+('' if c.get('enabled',True) else ' (off)') for c in s['chain']) or '(empty chain)')")
echo "pedal sound: $preset: $chain"

# 2. 48 kHz mono WAV (float)
DRY48="renders/.${name}-48k.wav"
if command -v afconvert >/dev/null; then
    afconvert -f WAVE -d LEF32@48000 -c 1 --mix "$IN" "$DRY48"
elif command -v sox >/dev/null; then
    sox "$IN" -r 48000 -c 1 -e floating-point -b 32 "$DRY48"
else
    echo "need afconvert (macOS) or sox" >&2; exit 1
fi

# 3. bb-render (Mac build of the engine's host code)
BBR=engine/build/bb-render
if [ ! -x "$BBR" ] || [ -n "$(find engine/src engine/effects engine/include -newer "$BBR" -name '*.[ch]*' 2>/dev/null | head -1)" ]; then
    echo "building bb-render..."
    cmake -S engine -B engine/build -DCMAKE_BUILD_TYPE=Release >/dev/null
    cmake --build engine/build --target bb-render -j8 >/dev/null
fi
safe=$(echo "$preset" | tr -c 'A-Za-z0-9_-' '_' | sed 's/_*$//')
WET="renders/${name}-${safe}.wav"
DRY="renders/${name}-dry.wav"
"$BBR" --preset "$STATE" --tail 2 "$DRY48" "$WET.f32" 2>&1 | sed -n 's/^bb-render: \([0-9]* frames.*\)/render: \1/p'

# 4. normalise to -1 dBFS peak (16-bit WAV) so playback never blares
python3 - "$DRY48" "$DRY" "$WET.f32" "$WET" <<'PY'
import struct, sys, wave, array
def read_f32(path):  # bb-render / afconvert float WAV: find the data chunk
    b = open(path, "rb").read()
    p, fmt = 12, None
    while p + 8 <= len(b):
        cid, ln = b[p:p+4], struct.unpack("<I", b[p+4:p+8])[0]
        if cid == b"fmt ": fmt = struct.unpack("<HHIIHH", b[p+8:p+24])
        if cid == b"data":
            a = array.array("f"); a.frombytes(b[p+8:p+8+ln - ln % 4]); return a, fmt
        p += 8 + ln + (ln & 1)
    raise SystemExit(path + ": no data")
for src, dst in ((sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])):
    a, fmt = read_f32(src)
    pk = max((abs(v) for v in a), default=0.0)
    g = min(10.0, 0.891 / pk) if pk > 1e-6 else 1.0   # -1 dBFS peak, at most +20 dB
    out = array.array("h", (int(max(-1.0, min(1.0, v * g)) * 32767) for v in a))
    w = wave.open(dst, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
    w.writeframes(out.tobytes()); w.close()
    print("%-34s peak %+5.1f dBFS -> normalised to -1.0 dBFS" % (dst, 20 * __import__("math").log10(pk) if pk > 0 else -99))
PY
rm -f "$WET.f32" "$DRY48"

# 5. play
if [ $PLAY -eq 1 ]; then
    command -v afplay >/dev/null || { echo "afplay not found; files are in renders/"; exit 0; }
    if [ $AB -eq 1 ]; then echo "playing: dry"; afplay -v 0.5 "$DRY"; fi
    echo "playing: through '$preset'"; afplay -v 0.5 "$WET"
fi
