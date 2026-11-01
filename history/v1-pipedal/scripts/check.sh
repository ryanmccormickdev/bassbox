#!/bin/bash
# bassbox (runs ON THE MAC): acceptance checks 1-5. Prints PASS/FAIL per check; exit 1 on any FAIL.
#
#   scripts/check.sh            check the current boot (for check 1, power-cycle the Pi first)
#   scripts/check.sh --reboot   soft-reboot the Pi first and wait for it (up to 3 min)
#
#   1. pipedald active within 90 s of boot, on the WM8960, with "Bass Drive" as the loaded preset
#   2. after >= 10 min uptime: zero xruns since audio start, pipedald CPU < 70 %, get_throttled 0x0
#   3. loopback latency measured and recorded (results/latency.txt + NOTES.md)
#   4. mixer state as configured: mixer.sh verify, and `amixer contents` identical to the
#      snapshot taken at bring-up (results/amixer-contents.txt)
#   5. http://<pi-host> loads the PiPedal UI from this Mac
set -uo pipefail
PI="${BASSBOX_PI:-<user>@<pi-host>}"
SSH=(ssh -o ServerAliveInterval=5 -o ConnectTimeout=8 -o BatchMode=yes)
cd "$(dirname "$0")/.."
FAILS=0
join() { local out="" x; for x in "$@"; do out="${out:+$out; }$x"; done; echo "$out"; }
pass() { printf 'PASS  %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }
pi() { "${SSH[@]}" "$PI" "$@"; }

if [ "${1:-}" = "--reboot" ]; then
    echo "rebooting the Pi..."
    pi 'sudo systemctl reboot' 2>/dev/null || true
    sleep 15
    up=0
    for _ in $(seq 33); do
        if pi true 2>/dev/null; then up=1; break; fi
        sleep 5
    done
    [ $up -eq 1 ] || { fail "Pi not reachable over ssh within 3 minutes of reboot"; exit 1; }
    sleep 10
fi

pi true 2>/dev/null || { fail "cannot ssh to $PI"; exit 1; }

# ---------------------------------------------------------------- 1
# first time pipedald came up this boot (later manual restarts don't count)
read -r active_s state <<<"$(pi 'sudo bash -s' <<'EOF2'
t=$(journalctl -b -u pipedald -o short-monotonic --no-pager |
    awk '/Started pipedald.service/ { sub(/^\[ */, ""); split($0, a, "]"); print a[1]; exit }')
echo "${t:-0} $(systemctl is-active pipedald)"
EOF2
)"
active_s=${active_s:-0}
device_ok=$(pi 'sudo journalctl -b -u pipedald -o cat --no-pager | grep "Audio started" | tail -1')
preset=$(pi 'sudo python3 /opt/bassbox/scripts/pipedal_preset.py show 2>/dev/null | head -1; \
    sudo python3 -c "import json; print(\"edited:\", json.load(open(\"/var/pipedal/currentPreset.json\"))[\"preset\"][\"name\"])" 2>/dev/null')
c1=()
[ "$state" = active ] || c1+=("pipedald is $state")
awk -v a="$active_s" 'BEGIN{exit !(a > 0 && a <= 90)}' || c1+=("pipedald active at ${active_s}s after boot (limit 90)")
[[ "$device_ok" == *"hw:wm8960soundcard"* ]] || c1+=("audio not on hw:wm8960soundcard (${device_ok:-no 'Audio started' line}; HAT fitted?)")
[[ "$preset" == *"/ 'Bass Drive'"* ]] || c1+=("boot preset is not Bass Drive ($preset)")
if [[ "$preset" == *"edited:"* && "$preset" != *"edited: Bass Drive"* ]]; then
    c1+=("an unsaved edit of another preset will load instead ($preset)")
fi
if [ ${#c1[@]} -eq 0 ]; then pass "1 boot: pipedald active at ${active_s}s, audio on WM8960, preset Bass Drive"
else fail "1 boot: $(join "${c1[@]}")"; fi

# ---------------------------------------------------------------- 2
read -r uptime_s cpu thr xruns <<<"$(pi 'sudo bash -s' <<'EOF'
up=$(cut -d. -f1 /proc/uptime)
pid=$(systemctl show -p MainPID --value pipedald)
cpu=$(top -b -d 5 -n 2 -p "$pid" | awk -v p="$pid" '$1 == p {c = $9} END {print c + 0}')
thr=$(vcgencmd get_throttled | cut -d= -f2)
# xruns are only meaningful while running on the HAT: count from the latest audio start, and
# only if that start was on the WM8960 (renders temporarily switch to a loopback card)
started=$(journalctl -b -u pipedald -o short-unix --no-pager |
    awk '/Audio started. ALSA, / {s = (index($0, "hw:wm8960soundcard") ? $1 : -1)} END {print s + 0}')
x=$(journalctl -b -u pipedald -o short-unix --no-pager |
    awk -v s="$started" 's > 0 && $1 > s + 5 && tolower($0) ~ /underrun|overrun|xrun|recovering from alsa/' | wc -l)
[ "$started" = "-1" ] || [ "$started" = "0" ] && x=-1
echo "$up $cpu $thr $x"
EOF
)"
c2=()
[ "${uptime_s:-0}" -ge 600 ] || c2+=("uptime only ${uptime_s}s; re-run after 10 minutes")
if [ "${xruns:-1}" -lt 0 ]; then c2+=("audio is not running on the WM8960 (HAT fitted?)")
elif [ "$xruns" -gt 0 ]; then c2+=("$xruns xrun messages since audio start"); fi
awk -v c="${cpu:-100}" 'BEGIN{exit !(c < 70)}' || c2+=("pipedald CPU ${cpu}%")
[ "$thr" = "0x0" ] || c2+=("get_throttled=$thr")
if [ ${#c2[@]} -eq 0 ]; then pass "2 stability: up ${uptime_s}s, 0 xruns, CPU ${cpu}%, throttled $thr"
else fail "2 stability: $(join "${c2[@]}")"; fi

# ---------------------------------------------------------------- 3
if [ -s results/latency.txt ] && grep -qiE '[0-9]+(\.[0-9]+)? *ms' results/latency.txt &&
    ! grep -q 'To be measured' NOTES.md; then
    pass "3 latency: recorded in results/latency.txt and NOTES.md"
else
    fail "3 latency: not measured yet (scripts/bringup.sh, loopback cable in)"
fi

# ---------------------------------------------------------------- 4
c4=()
verify=$(pi 'sudo /opt/bassbox/scripts/mixer.sh verify' 2>&1); rc=$?
[ $rc -eq 0 ] || c4+=("mixer.sh verify: $(echo "$verify" | head -3 | tr '\n' ' ')")
if [ -s results/amixer-contents.txt ]; then
    d=$(diff <(pi 'amixer -c wm8960soundcard contents' 2>/dev/null) results/amixer-contents.txt)
    [ -z "$d" ] || c4+=("amixer contents differ from bring-up snapshot ($(echo "$d" | grep -c '^[<>]') lines)")
else
    c4+=("no bring-up snapshot (results/amixer-contents.txt); run scripts/bringup.sh")
fi
if [ ${#c4[@]} -eq 0 ]; then pass "4 mixer: matches config/mixer.conf and the bring-up snapshot"
else fail "4 mixer: $(join "${c4[@]}")"; fi

# ---------------------------------------------------------------- 5
page="$(mktemp)"
code=$(curl -s -m 10 -o "$page" -w '%{http_code}' http://<pi-host>/)
if [ "$code" = 200 ] && grep -qi pipedal "$page"; then
    pass "5 web UI: http://<pi-host> returns the PiPedal UI"
else
    fail "5 web UI: http://<pi-host> returned HTTP $code"
fi
rm -f "$page"

echo
[ $FAILS -eq 0 ] && echo "ALL PASS" || echo "$FAILS check(s) failed"
[ $FAILS -eq 0 ]
