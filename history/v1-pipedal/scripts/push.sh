#!/bin/bash
# bassbox (runs ON THE MAC): sync this repo to /opt/bassbox on the Pi.
#   scripts/push.sh            sync only
#   scripts/push.sh --setup    sync, then run setup.sh on the Pi (through the Mac's internet)
set -euo pipefail
PI="${BASSBOX_PI:-<user>@<pi-host>}"
SSH=(ssh -o ServerAliveInterval=5 -o ConnectTimeout=8)
cd "$(dirname "$0")/.."

# The Pi has no RTC and no internet for NTP: set its clock from the Mac if it is >60 s off.
skew=$(( $(date +%s) - $("${SSH[@]}" "$PI" 'date +%s') ))
if [ ${skew#-} -gt 60 ]; then
    "${SSH[@]}" "$PI" "sudo date -u -s @$(date +%s) >/dev/null && (sudo fake-hwclock save 2>/dev/null || true)"
    echo "set Pi clock (was ${skew}s off)"
fi

"${SSH[@]}" "$PI" 'sudo mkdir -p /opt/bassbox && sudo chown "$USER": /opt/bassbox'
rsync -a --delete --exclude .git --exclude 'renders/' --exclude 'debs/' --exclude '.DS_Store' \
    --exclude '__pycache__' -e "ssh -o ServerAliveInterval=5" ./ "$PI":/opt/bassbox/
echo "synced to $PI:/opt/bassbox"

if [ "${1:-}" = "--setup" ]; then
    # reverse SOCKS tunnel: the Pi reaches the internet through the Mac while setup runs
    if ! "${SSH[@]}" "$PI" 'ss -ltn | grep -q 127.0.0.1:1080'; then
        ssh -fN -o ExitOnForwardFailure=yes -o ServerAliveInterval=5 -R 1080 "$PI"
        echo "started reverse SOCKS tunnel"
    fi
    "${SSH[@]}" "$PI" 'sudo /opt/bassbox/scripts/setup.sh --proxy'
fi
