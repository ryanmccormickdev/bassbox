#!/bin/bash
# bassbox: xrun soak + loopback latency per ALSA buffer setting. Runs ON THE PI as root.
#
#   sudo scripts/soak.sh SECONDS [CONFIGS...]        e.g.  soak.sh 600 64x2
#   default configs: 32x2 32x3 64x2 64x3 128x2 128x3
#
# For each config: stops bassbox-engine.service, runs the engine binary with that period on the
# real socket (the UI stays connected) with a throw-away state dir (live.json untouched), loads
# a heavy chain (factory "Doom" + "Dub Echo" stacked) at out_gain -60 dB (no feedback through a
# loopback cable), keeps CPU0-2 busy (stress-ng, nice 19; the UI app keeps running too), then
# reads xruns / DSP load and runs `bassbox-engine --latency-test`. Restores the service at the
# end. Results: /var/lib/bassbox/soak-<time>.txt
set -u
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
SECS="${1:-600}"; shift || true
CONFIGS=("$@"); [ ${#CONFIGS[@]} -gt 0 ] || CONFIGS=(32x2 32x3 64x2 64x3 128x2 128x3)
BASE=/opt/bassbox
ENG=/usr/local/bin/bassbox-engine
CTL="python3 $BASE/scripts/bbctl.py"
OUT=/var/lib/bassbox/soak-$(date +%Y%m%d-%H%M%S).txt
STATE=$(mktemp -d)
echo '{"version":1,"state":{"master":{"in_gain_db":0,"out_gain_db":-60,"bypass":false},"chain":[]},"current":"","baseline":null}' > "$STATE/live.json"

chain=$(python3 - "$BASE" <<'PY'
import json, sys
b = sys.argv[1] + "/presets/factory/"
c = []
for f in ("doom.json", "dub_echo.json"):
    try: c += json.load(open(b + f))["chain"]
    except Exception: pass
print(json.dumps({"cmd": "load_state", "state": {"name": "soak",
      "master": {"in_gain_db": 0, "out_gain_db": -60, "bypass": False}, "chain": c[:16]}}))
PY
)

stress_pid=""
if command -v stress-ng >/dev/null; then
    nice -n 19 stress-ng --cpu 3 --taskset 0-2 --vm 1 --vm-bytes 128M --timeout $(( (SECS + 40) * ${#CONFIGS[@]} ))s \
        >/dev/null 2>&1 & stress_pid=$!
fi
# --runtime mask: nothing (other tools, timers) may restart the service mid-soak;
# cleared on reboot and undone at the end.
systemctl stop bassbox-engine
systemctl mask --runtime bassbox-engine >/dev/null 2>&1
trap 'systemctl unmask --runtime bassbox-engine >/dev/null 2>&1; systemctl start bassbox-engine' EXIT
echo "# soak $(date -Is): ${SECS}s per config, chain doom+dub_echo, stress-ng cpu x3 + vm on CPU0-2 (nice 19), UI: $(pgrep -f ui/main.py >/dev/null && echo running || echo not running)" | tee "$OUT"
echo "# config  xruns  dsp_avg  dsp_max(peak 1s)  latency(frames/ms)  loop_gain_dB" | tee -a "$OUT"
for cfg in "${CONFIGS[@]}"; do
    p=${cfg%x*}; n=${cfg#*x}
    $ENG --config $BASE/config/engine.conf --period "$p" --periods "$n" --state-dir "$STATE" >"$STATE/log-$cfg" 2>&1 &
    pid=$!
    for _ in $(seq 50); do [ -S /run/bassbox/engine.sock ] && $CTL ping >/dev/null 2>&1 && break; sleep 0.1; done
    $CTL "$chain" >/dev/null || echo "   (chain load failed)"
    peak=0; avg=0; t=0; reasserts=0
    want=$(echo "$chain" | grep -o '"type"' | wc -l)
    while [ $t -lt "$SECS" ] && kill -0 $pid 2>/dev/null; do
        sleep 5; t=$((t + 5))
        # other clients (UI test tools) may edit the live state; keep the soak load in place
        if [ $((t % 30)) -eq 0 ] && [ "$($CTL state 2>/dev/null | grep -c '"type"')" != "$want" ]; then
            $CTL "$chain" >/dev/null 2>&1; reasserts=$((reasserts + 1))
        fi
        read -r x a m < <($CTL status 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["xruns"], d["dsp_load"], d["dsp_load_max"])' 2>/dev/null)
        peak=$(python3 -c "print(max($peak, ${m:-0}))"); avg=${a:-0}
    done
    xr=$($CTL status 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin); print("%d(c%d/p%d)" % (d["xruns"], d.get("xruns_capture",0), d.get("xruns_playback",0)))' 2>/dev/null || echo "died")
    [ $reasserts -gt 0 ] && echo "   note: soak chain re-loaded $reasserts x (another client changed the state)"
    grep -q watchdog "$STATE/log-$cfg" && xr="$xr(stalled)"
    kill $pid 2>/dev/null; wait $pid 2>/dev/null
    lat=$($ENG --latency-test 3 --period "$p" --periods "$n" 2>/dev/null)
    l=$(echo "$lat" | python3 -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print("%s/%s %s" % (d.get("latency_frames","-"), d.get("latency_ms","-"), d.get("gain_db","no-signal")))')
    printf '%-8s %5s  %7.4f  %7.4f  %s\n' "$cfg" "$xr" "$avg" "$peak" "$l" | tee -a "$OUT"
done
[ -n "$stress_pid" ] && kill "$stress_pid" 2>/dev/null
rm -rf "$STATE"
echo "# results in $OUT"
