#!/bin/bash
# bassbox (runs ON THE MAC): one-time hardware bring-up once the HAT is fitted. Interactive:
# it tells you when to plug or unplug cables and when to play.
#
#   scripts/bringup.sh            all steps (~25 min, mostly the 10-minute soak)
#   scripts/bringup.sh <step>     one step: detect probe latency gain soak save checks
#
# Needs: the 3.5 mm male-to-male loopback cable, your bass and a mono instrument cable
# (3.5 mm plug or 1/4" -> 3.5 mm adapter) into LINE IN (middle jack), headphones.
# Results land in results/, config/ and NOTES.md in this repo.
set -euo pipefail
PI="${BASSBOX_PI:-<user>@<pi-host>}"
SSH=(ssh -o ServerAliveInterval=5 -o ConnectTimeout=8)
cd "$(dirname "$0")/.."
mkdir -p results
pi() { "${SSH[@]}" "$PI" "$@"; }
hw() { pi "sudo /opt/bassbox/scripts/hw.sh $*"; }
say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
ask() { local a; read -r -p "$* [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }
wait_enter() { read -r -p "$* (press Enter) " _; }

# Pi -> Mac: everything the steps write
pull() {
    rsync -a -e "ssh -o ServerAliveInterval=5" "$PI":/opt/bassbox/config/ config/
    mkdir -p results
    rsync -a -e "ssh -o ServerAliveInterval=5" "$PI":/opt/bassbox/results/ results/ 2>/dev/null || true
}

step_detect() {
    say "1/7 detect the HAT"
    scripts/push.sh >/dev/null
    if ! hw detect; then
        cat <<'EOF'
The WM8960 card is missing. Try, in order:
  - power off, re-seat the HAT on all 40 pins (no offset), power on, re-run
  - `ssh <user>@<pi-host> 'dmesg | grep -i wm8960'`: "probe ... failed with error -5" means
    the codec did not answer on I2C (seating/power), anything else: note it in NOTES.md
EOF
        exit 1
    fi
    pi 'sudo /opt/bassbox/scripts/mixer.sh apply'
    if ! pi 'sudo /opt/bassbox/scripts/mixer.sh verify'; then
        echo "Some mixer controls are missing or rejected (see above). The control names in"
        echo "config/mixer.conf come from the kernel driver; compare with:"
        echo "  ssh $PI 'amixer -c wm8960soundcard contents' | grep name="
        exit 1
    fi
    pi 'amixer -c wm8960soundcard contents' > results/amixer-contents.initial.txt
    echo "mixer profile applied and verified"
}

step_probe() {
    say "2/7 find the line-input wiring (loopback cable)"
    echo "Connect the loopback cable: HEADPHONE jack -> LINE IN (middle jack). Nothing else plugged in."
    echo "PiPedal is stopped while test tones play (it would feed back through the cable)."
    ask "Loopback cable connected?" || exit 1
    hw probe --apply
    pull
}

step_latency() {
    say "3/7 round-trip latency (loopback cable still in, ~3 min)"
    hw latency
    pull
    python3 - <<'EOF'
import re, pathlib
notes = pathlib.Path("NOTES.md")
s = notes.read_text()
raw = pathlib.Path("results/latency.txt").read_text().strip()
block = ("## Latency table\nMeasured by `scripts/bringup.sh` (`hw.sh latency`): pipedal_latency_test, "
         "48 kHz, loopback cable headphone out -> line in.\n\n```\n" + raw + "\n```\n")
s = re.sub(r"## Latency table\n.*?(?=\n## |\Z)", block, s, flags=re.S)
notes.write_text(s)
EOF
    echo "latency recorded in results/latency.txt and NOTES.md"
}

step_gain() {
    say "4/7 input gain (your bass)"
    echo "REMOVE the loopback cable now. Plug the bass into LINE IN (middle jack),"
    echo "headphones into the headphone jack. Turn the bass's volume knob(s) fully up."
    ask "Loopback removed and bass plugged in?" || exit 1
    local i
    for i in 1 2; do
        wait_enter "Get ready to play HARD for 10 seconds (dig in, slap/pop if you do that)."
        echo "recording... play now!"
        hw record 10 /opt/bassbox/results/gain-take$i.wav
        hw calibrate /opt/bassbox/results/gain-take$i.wav
        if [ $i -eq 1 ] && ! ask "Do a second take to confirm the new gain?"; then break; fi
    done
    hw preset >/dev/null   # NAM input calibration follows the new gain
    pull
}

step_soak() {
    say "5/7 buffer size: smallest with zero xruns (loopback cable OUT)"
    echo "Leave the bass plugged in (you can play along or not); ~2 min per size, then a 10 min run."
    local chosen="" size
    for size in 32 64 128 256; do
        if hw soak "$size" 3 120 | tee -a results/soak.txt | grep -q 'PASS$'; then
            chosen=$size; break
        fi
    done
    [ -n "$chosen" ] || { echo "no buffer size ran clean for 2 minutes; see results/soak.txt"; exit 1; }
    echo "confirming ${chosen}x3 for 10 minutes..."
    while ! hw soak "$chosen" 3 600 | tee -a results/soak.txt | grep -q 'PASS$'; do
        [ "$chosen" -ge 256 ] && { echo "256x3 failed the 10-minute run; see results/soak.txt"; exit 1; }
        chosen=$((chosen * 2))
        echo "stepping up to ${chosen}x3 and confirming for 10 minutes..."
    done
    sed -i '' -E "s/^BUFFER_SIZE=.*/BUFFER_SIZE=$chosen/; s/^# Provisional until .*/# Chosen by bringup.sh: smallest size with 0 xruns over 10 minutes (results\/soak.txt)./" config/pipedal.conf
    scripts/push.sh >/dev/null
    hw preset >/dev/null
    echo "buffer set to ${chosen}x3 in config/pipedal.conf"
}

step_save() {
    say "6/7 save the mixer state, reboot, confirm it restores"
    hw snapshot
    pull
    echo "rebooting (back in ~40 s)..."
    pi 'sudo systemctl reboot' 2>/dev/null || true
    sleep 15
    for _ in $(seq 33); do pi true 2>/dev/null && break; sleep 5; done
    pi true || { echo "Pi not back after 3 minutes"; exit 1; }
    sleep 10
    if diff <(pi 'amixer -c wm8960soundcard contents') results/amixer-contents.txt; then
        echo "mixer state identical after reboot"
    else
        echo "mixer state DIFFERS after reboot (diff above)"; exit 1
    fi
}

step_checks() {
    say "7/7 listening checks (one at a time)"
    echo "# human checks $(date -Is)" > results/human-checks.txt
    echo "Headphones on, bass in LINE IN, loopback cable out."
    ask "1) Play: is the bass DISTORTED (Bass Drive)?" || echo "   -> noted: NOT distorted" | tee -a results/human-checks.txt
    echo "   In the web UI (http://<pi-host>) open the preset list and choose 'Bypass (clean)'."
    ask "2) Is the bass now CLEAN?" || echo "   -> noted: bypass NOT clean" | tee -a results/human-checks.txt
    echo "   Choose 'Bass Drive' again. Now stopping PiPedal..."
    pi 'sudo systemctl stop pipedald'
    ask "3) Play: is it SILENT now (no dry bass leaking through)?" || echo "   -> noted: analog leak" | tee -a results/human-checks.txt
    pi 'sudo systemctl start pipedald && sudo systemctl restart bassbox-governor'
    ask "4) Were the onboard speakers silent the whole time?" || echo "   -> noted: speakers NOT silent" | tee -a results/human-checks.txt
    if grep -q noted results/human-checks.txt; then echo "RESULT: failures above"; else echo "RESULT: all four passed"; fi |
        tee -a results/human-checks.txt
    echo
    echo "Run scripts/check.sh after the Pi has been up for 10 minutes (check 2 needs that)."
}

case "${1:-all}" in
    detect) step_detect ;;
    probe) step_probe ;;
    latency) step_latency ;;
    gain) step_gain ;;
    soak) step_soak ;;
    save) step_save ;;
    checks) step_checks ;;
    all) step_detect; step_probe; step_latency; step_gain; step_soak; step_save; step_checks ;;
    *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 64 ;;
esac
