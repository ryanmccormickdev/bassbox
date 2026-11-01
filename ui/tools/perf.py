#!/usr/bin/env python3
"""Measure the running UI: CPU% (from /proc), flushes and frame-time percentiles over a window.

    perf.py [SECONDS] [--sock PATH]     (run on the Pi next to the UI)
"""
import json
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inject import send  # noqa: E402

SOCK = os.environ.get("BASSBOX_UI_DEBUG_SOCK", "/run/bassbox/ui-debug.sock")


def ui_pid():
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                cmd = open(f"/proc/{p}/cmdline", "rb").read().split(b"\0")
            except OSError:
                continue
            if any(c.endswith(b"ui/main.py") for c in cmd) and b"sudo" not in cmd[0]:
                return int(p)


def ticks(pid):
    f = open(f"/proc/{pid}/stat").read().rsplit(")", 1)[1].split()
    return int(f[11]) + int(f[12])


def summarize(frames):
    out = {}
    for k in ("render_ms", "convert_ms", "flush_ms", "total_ms", "area", "rects"):
        v = sorted(f[k] for f in frames)
        if v:
            out[k] = {"med": round(st.median(v), 2), "p90": round(v[int(len(v) * 0.9)], 2), "max": round(v[-1], 2)}
    return out


def measure(secs, sock=SOCK):
    pid = ui_pid()
    hz = os.sysconf("SC_CLK_TCK")
    s0 = send(sock, {"cmd": "stats", "n": 1})
    t0, k0 = time.monotonic(), ticks(pid)
    time.sleep(secs)
    k1, t1 = ticks(pid), time.monotonic()
    s1 = send(sock, {"cmd": "stats", "n": 400, "since": s0["now"]})
    fr = s1["frames"]
    return {"cpu_pct": round((k1 - k0) / hz / (t1 - t0) * 100, 2), "flushes": s1["flushes"] - s0["flushes"],
            "fps": round(len(fr) / (t1 - t0), 1), "frames": summarize(fr), "pid": pid}


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--sock" in a:
        i = a.index("--sock")
        SOCK = a[i + 1]
        del a[i:i + 2]
    print(json.dumps(measure(float(a[0]) if a else 10), indent=1))
