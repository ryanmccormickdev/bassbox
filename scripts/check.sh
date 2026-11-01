#!/bin/bash
# bassbox: health check (runs ON THE MAC, ~65 s). One PASS / FAIL / INFO line per check;
# exit status 1 if anything failed.
#
#   scripts/check.sh            (BASSBOX_PI=<user>@<pi-host> by default)
#
# Checks: services, engine buffer config + RT placement, xruns over a 60 s window, DSP load,
# throttling, jacks, bb-selftest on the Pi, cmdline tokens, boot-to-audio time, live preset.
PI="${BASSBOX_PI:-<user>@<pi-host>}"
WINDOW="${BASSBOX_CHECK_SECONDS:-60}"
ssh -o ServerAliveInterval=5 -o ConnectTimeout=8 "$PI" "WINDOW=$WINDOW bash -s" <<'REMOTE'
B="python3 /opt/bassbox/scripts/bbctl.py"
fails=0
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; fails=$((fails + 1)); }
info() { printf 'INFO  %s\n' "$*"; }
jget() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

# services
for u in bassbox-engine bassbox-ui; do
    a=$(systemctl is-active $u 2>/dev/null); e=$(systemctl is-enabled $u 2>/dev/null)
    [ "$a" = active ] && [ "$e" = enabled ] && pass "$u: active, enabled" || fail "$u: $a, $e"
done
e=$(systemctl is-enabled pipedald 2>/dev/null); a=$(systemctl is-active pipedald 2>/dev/null)
[ "$e" != enabled ] && [ "$a" != active ] && pass "pipedald (v1 fallback): $e, $a" || fail "pipedald: $e, $a (must be disabled)"

# engine config from this boot's journal
line=$(journalctl -b -u bassbox-engine --no-pager 2>/dev/null | grep ' audio: ' | tail -1 | sed 's/.* audio: //')
cfg=$(echo "$line" | jget '"%sx%s %s, %s" % (d["period"], d["periods"], d["format"], d["rt"])')
case "$cfg" in
    64x3*"pinned to CPU3, SCHED_FIFO 80"*) pass "engine: $cfg" ;;
    "") fail "engine: no 'audio:' line in this boot's journal (journalctl -b -u bassbox-engine)" ;;
    *) fail "engine: $cfg (expected 64x3, CPU3, SCHED_FIFO 80)" ;;
esac

# bb-selftest runs in parallel with the xrun window (CPU1; the audio core is CPU3)
ST=$(mktemp)
taskset -c 1 /opt/bassbox/engine/build/bb-selftest >"$ST" 2>&1 & stpid=$!
x0=$($B status 2>/dev/null | jget 'd["xruns"]')
sleep "$WINDOW"
st=$($B status 2>/dev/null)
x1=$(echo "$st" | jget 'd["xruns"]')
dmax=$(echo "$st" | jget '"%.1f%%" % (100 * d["dsp_load_max"])')
davg=$(echo "$st" | jget '"%.1f%%" % (100 * d["dsp_load"])')
if [ -z "$x0" ] || [ -z "$x1" ]; then fail "engine socket not answering (bbctl.py status)"
elif [ "$x1" = "$x0" ]; then pass "xruns: 0 new in ${WINDOW} s (total $x1); DSP load avg $davg, max $dmax of a period"
else fail "xruns: $((x1 - x0)) new in ${WINDOW} s (total $x1); DSP load avg $davg, max $dmax"; fi
wait $stpid; strc=$?
last=$(tail -1 "$ST")
[ $strc -eq 0 ] && pass "bb-selftest: $last" || fail "bb-selftest: $last (see /opt/bassbox/engine/build/bb-selftest)"
rm -f "$ST"

# throttling (under-voltage / overheating)
t=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
[ "$t" = "0x0" ] && pass "get_throttled: 0x0" || fail "get_throttled: ${t:-unknown} (power supply or heat)"

# jacks (informational)
info "jacks: $($B jacks 2>/dev/null | jget '"input %s, output %s" % ("plugged" if d["input"] else "EMPTY (output muted)", "plugged" if d["output"] else "empty")')"

# kernel command line
miss=""
for tok in isolcpus=3 nohz_full=3 rcu_nocbs=3 irqaffinity=0-2 quiet; do grep -qw -- "$tok" /proc/cmdline || miss="$miss $tok"; done
[ -z "$miss" ] && pass "cmdline: RT and quiet tokens present (isolated CPUs: $(cat /sys/devices/system/cpu/isolated))" || fail "cmdline: missing$miss"

# boot to audio (kernel start -> engine streaming; add ~8.6 s of firmware for power-on)
s=$(journalctl -b -o short-monotonic -u bassbox-engine --no-pager 2>/dev/null | grep ' audio: ' | head -1 | sed -n 's/^\[ *\([0-9.]*\)\].*/\1/p')
if [ -n "$s" ]; then
    s=$(printf '%.1f' "$s")
    awk -v s="$s" 'BEGIN { exit !(s < 45) }' && pass "boot to audio: ${s} s after kernel start (~$(awk -v s="$s" 'BEGIN{printf "%.0f", s + 8.6}') s from power-on)" \
        || fail "boot to audio: ${s} s after kernel start (slow)"
else
    info "boot to audio: engine was restarted since boot (no first 'audio:' timestamp)"
fi

# live state
info "live preset: $($B state 2>/dev/null | jget '"%s%s, out %s dB, %d slots" % (d["state"]["name"] or "(unsaved)", " (edited)" if d["state"]["dirty"] else "", d["state"]["master"]["out_gain_db"], len(d["state"]["chain"]))')"

echo
[ $fails -eq 0 ] && echo "ALL CHECKS PASSED" || echo "$fails CHECK(S) FAILED"
exit $([ $fails -eq 0 ] && echo 0 || echo 1)
REMOTE
