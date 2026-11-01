#!/bin/bash
# bassbox: WM8960 mixer control. Runs ON THE PI.
#
#   mixer.sh apply    wait for the HAT, apply config/mixer.conf, alsactl store (boot unit uses this)
#   mixer.sh verify   compare every mixer.conf control with the live card; exit 1 on mismatch
#   mixer.sh dump     amixer contents for the HAT
#   mixer.sh present  exit 0 if the HAT's card exists
#
# The HAT being absent is not an error for `apply` (logs and exits 0) so boot never hangs on it.
set -u
CARD="${BASSBOX_CARD:-wm8960soundcard}"
BASE="$(cd "$(dirname "$0")/.." && pwd)"
CONF="${BASSBOX_MIXER_CONF:-$BASE/config/mixer.conf}"
WAIT_SECS="${BASSBOX_CARD_WAIT:-20}"

log() { echo "bassbox-mixer: $*"; }

present() { [ -e "/proc/asound/$CARD" ]; }

wait_card() {
    local i
    for ((i = 0; i < WAIT_SECS * 2; i++)); do
        present && return 0
        sleep 0.5
    done
    return 1
}

# Emit "name<TAB>value" for each non-comment line of mixer.conf.
conf_lines() {
    sed -e 's/[[:space:]]*#.*$//' -e '/^[[:space:]]*$/d' "$CONF" |
        sed -E 's/^[[:space:]]*(.+[^[:space:]])[[:space:]]+=[[:space:]]+(.+[^[:space:]])[[:space:]]*$/\1\t\2/'
}

apply() {
    if ! wait_card; then
        log "card '$CARD' not present after ${WAIT_SECS}s (HAT not fitted?). Nothing to do."
        return 0
    fi
    local name value fail=0
    while IFS=$'\t' read -r name value; do
        if ! amixer -q -c "$CARD" cset name="$name" "$value" >/dev/null 2>&1; then
            log "WARN: failed to set '$name' = '$value'"
            fail=1
        fi
    done < <(conf_lines)
    alsactl store "$CARD" 2>/dev/null || log "WARN: alsactl store failed"
    [ $fail -eq 0 ] && log "applied $CONF to $CARD" || log "applied with warnings"
    return 0
}

# Normalise a value list so "on" matches "on,on" and enum names/indices compare as given.
norm() { echo "$1" | tr -d ' '; }

verify() {
    present || { log "card '$CARD' not present"; return 2; }
    local name value live want bad=0
    while IFS=$'\t' read -r name value; do
        live=$(amixer -c "$CARD" cget name="$name" 2>/dev/null | sed -n 's/^  : values=//p')
        if [ -z "$live" ]; then
            echo "MISSING  $name"; bad=1; continue
        fi
        want=$(norm "$value")
        live=$(norm "$live")
        # expand single value to match a 2-channel control
        if [[ "$live" == *,* && "$want" != *,* ]]; then want="$want,$want"; fi
        if [ "$want" != "$live" ]; then
            echo "MISMATCH $name: want=$want live=$live"; bad=1
        fi
    done < <(conf_lines)
    return $bad
}

case "${1:-}" in
    apply)   apply ;;
    verify)  verify ;;
    dump)    present && amixer -c "$CARD" contents ;;
    present) present ;;
    *) echo "usage: $0 {apply|verify|dump|present}" >&2; exit 64 ;;
esac
