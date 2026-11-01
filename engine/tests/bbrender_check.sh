#!/bin/bash
# Verify every effect (at defaults) and every factory preset through the real host (bb-render),
# on samples/test-bass-riff.wav. Prints a markdown table: level vs. an empty chain rendered the
# same way, output peak, CPU and NaN resets as reported by bb-render.
#   engine/tests/bbrender_check.sh [path/to/bb-render] [out.md]
set -euo pipefail
cd "$(dirname "$0")/../.."
BBR="${1:-engine/build/bb-render}"
OUT="${2:-engine/tests/out/bb-render.md}"
IN=samples/test-bass-riff.wav
TASKSET="${TASKSET-$(command -v taskset >/dev/null && echo "taskset -c 2")}"  # pin like the RT thread
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$(dirname "$OUT")"

summary() {  # args: json-or-preset-flag value -> "peak rms cpu_avg cpu_max nan"
    $TASKSET "$BBR" --tail 3 "$1" "$2" "$IN" "$TMP/o.wav" 2>&1 |
        sed -nE 's/.*out peak ([-0-9.]+) dBFS rms ([-0-9.]+).*avg ([0-9.]+)% max ([0-9.]+)%.*nan resets ([0-9]+).*/\1 \2 \3 \4 \5/p'
}
read -r _ base _ _ _ <<<"$(summary --json '{"chain":[]}')"
{
    echo "# bb-render check ($(uname -n), $(date -u +%F))"
    echo
    echo "Input: test riff + 3 s tail, block 64, host DC blocker + output limiter on. Level = RMS vs. an empty chain (${base} dBFS)."
    echo
    echo "| chain | level dB | out peak dBFS | cpu avg % | worst block % (wall clock, includes OS jitter) | NaN resets |"
    echo "|---|---|---|---|---|---|"
    ids=$(python3 -c 'import json;print(" ".join(e["id"] for e in json.load(open("engine/catalog.json"))["effects"]))')
    for id in $ids; do
        read -r pk rms ca cm nan <<<"$(summary --json "{\"chain\":[{\"type\":\"$id\"}]}")"
        printf '| %s | %+.1f | %s | %s | %s | %s |\n' "$id" "$(awk -v a="$rms" -v b="$base" 'BEGIN{print a-b}')" "$pk" "$ca" "$cm" "$nan"
    done
    for p in presets/factory/*.json; do
        read -r pk rms ca cm nan <<<"$(summary --preset "$p")"
        printf '| preset: %s | %+.1f | %s | %s | %s | %s |\n' "$(basename "$p" .json)" "$(awk -v a="$rms" -v b="$base" 'BEGIN{print a-b}')" "$pk" "$ca" "$cm" "$nan"
    done
} | tee "$OUT"
