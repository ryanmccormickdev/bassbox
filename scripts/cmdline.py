#!/usr/bin/env python3
"""bassbox: idempotently adjust /boot/firmware/cmdline.txt. Runs ON THE PI as root.

    cmdline.py [--check] [--file PATH]

Ensures the real-time and quiet-boot tokens are present and the kernel console is not on the
panel (tty1). It only appends tokens or removes `console=tty1`; every other token (root=,
modules-load=, anything networking-related) is left exactly as it was, in order. A backup
`cmdline.txt.bak-bassbox-<timestamp>` is written before any change. Exit status: 0 = already
correct or updated, 1 = --check found differences.
"""
import argparse
import shutil
import sys
import time

# CPU3 is reserved for the audio thread (docs/realtime.md). nohz_full/rcu_nocbs are
# kept for the contract but are inert on the stock Pi kernel (no CONFIG_NO_HZ_FULL /
# CONFIG_RCU_NOCB_CPU); isolcpus and irqaffinity do take effect.
RT_TOKENS = ["isolcpus=3", "nohz_full=3", "rcu_nocbs=3", "irqaffinity=0-2"]
# Quiet panel: no kernel log, no boot logo, no blinking cursor on the fbcon (it stays bound to
# fb0 so the panel is initialised to black; the UI takes it over with KDSETMODE KD_GRAPHICS).
QUIET_TOKENS = ["quiet", "loglevel=3", "vt.global_cursor_default=0", "logo.nologo"]
REMOVE_TOKENS = ["console=tty1"]  # kernel console stays on serial0 only


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--file", default="/boot/firmware/cmdline.txt")
    a = ap.parse_args()

    raw = open(a.file).read()
    lines = [l for l in raw.splitlines() if l.strip()]
    if len(lines) != 1:
        sys.exit(f"{a.file}: expected exactly one non-empty line, found {len(lines)}; not touching it")
    tokens = lines[0].split()
    new = [t for t in tokens if t not in REMOVE_TOKENS]
    for t in RT_TOKENS + QUIET_TOKENS:
        key = t.split("=")[0]
        # replace a token with the same key but another value (e.g. isolcpus=2) in place
        same = [i for i, x in enumerate(new) if x.split("=")[0] == key]
        if same:
            new[same[0]] = t
            for i in reversed(same[1:]):
                del new[i]
        else:
            new.append(t)
    if new == tokens:
        print("cmdline.txt already correct")
        return 0
    removed = [t for t in tokens if t not in new]
    added = [t for t in new if t not in tokens]
    print("cmdline.txt changes:  -" + " -".join(removed) if removed else "cmdline.txt changes:", " +" + " +".join(added))
    if a.check:
        return 1
    bak = f"{a.file}.bak-bassbox-{time.strftime('%Y%m%d%H%M%S')}"
    shutil.copy2(a.file, bak)
    with open(a.file, "w") as f:
        f.write(" ".join(new) + "\n")
    print(f"backup: {bak}")
    print("new:    " + " ".join(new))
    print("reboot required")
    return 0


if __name__ == "__main__":
    sys.exit(main())
