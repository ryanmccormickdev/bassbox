#!/usr/bin/env python3
"""Interaction timings on the running UI (via the debug socket):
  - tap -> pixels: inject a tap on the first card's power button, read input_to_glass of the resulting frame
  - knob drag: drag Drive on the edit screen, per-frame render/convert/flush/total
  - idle: CPU and flushes with the engine signal off
    python3 ui/tools/timing.py [--sock PATH]
"""
import json
import os
import statistics as st
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inject import send  # noqa: E402
import perf  # noqa: E402

SOCK = os.environ.get("BASSBOX_UI_DEBUG_SOCK", "/run/bassbox/ui-debug.sock")


def ui(msg):
    return send(SOCK, msg)


def frames_since(t):
    return [f for f in ui({"cmd": "stats", "n": 400, "since": t})["frames"]]


def pct(v, q):
    v = sorted(v)
    return round(v[min(len(v) - 1, int(len(v) * q))], 2) if v else None


def main():
    out = {}
    ui({"cmd": "goto", "screen": "board"})
    st0 = ui({"cmd": "state"})
    if not st0["chain"]:
        print("need a chain"); return
    # --- tap -> pixels on the power button of card 1 (toggle twice per sample)
    lat = []
    for i in range(10):
        t = ui({"cmd": "stats", "n": 1})["now"]
        ui({"cmd": "tap", "x": 256, "y": 120})
        fr = [f for f in frames_since(t) if f.get("input") == "up" and f.get("input_to_glass_ms") is not None]
        if fr:
            lat.append(fr[0]["input_to_glass_ms"])
        time.sleep(0.15)
    out["tap_to_pixels_ms"] = {"median": pct(lat, 0.5), "p90": pct(lat, 0.9), "n": len(lat),
                               "note": "from touch-up dispatch to DIRTYFB return (frame on glass)"}
    # --- knob drag on the edit view of the first slot
    uid = st0["chain"][0]["uid"]
    ui({"cmd": "goto", "screen": "edit", "uid": uid})
    time.sleep(0.3)
    t = ui({"cmd": "stats", "n": 1})["now"]
    ui({"cmd": "drag", "x1": 82, "y1": 260, "x2": 82, "y2": 120, "ms": 1200})
    fr = [f for f in frames_since(t) if f.get("input") in ("move", "down")]
    out["knob_drag"] = {k: {"median": pct([f[k] for f in fr], 0.5), "p90": pct([f[k] for f in fr], 0.9),
                            "max": pct([f[k] for f in fr], 1.0)}
                        for k in ("render_ms", "convert_ms", "flush_ms", "total_ms", "area")}
    out["knob_drag"]["frames"] = len(fr)
    out["knob_drag"]["input_to_glass_ms"] = {"median": pct([f["input_to_glass_ms"] for f in fr if f["input_to_glass_ms"]], 0.5),
                                             "p90": pct([f["input_to_glass_ms"] for f in fr if f["input_to_glass_ms"]], 0.9)}
    # --- screen change (full frame)
    t = ui({"cmd": "stats", "n": 1})["now"]
    ui({"cmd": "goto", "screen": "board"})
    fr = frames_since(t)
    full = [f for f in fr if f["area"] == 320 * 480]
    out["screen_change_full_frame_ms"] = full[0]["total_ms"] if full else None
    # --- with signal (comets) and idle
    ui({"cmd": "engine", "msg": {"cmd": "mock_signal", "on": True}})
    time.sleep(2)
    out["board_with_signal"] = perf.measure(10, SOCK)
    ui({"cmd": "engine", "msg": {"cmd": "mock_signal", "on": False}})
    time.sleep(3)
    out["board_idle_no_signal"] = perf.measure(20, SOCK)
    ui({"cmd": "engine", "msg": {"cmd": "mock_signal", "on": True}})
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    if "--sock" in sys.argv:
        SOCK = sys.argv[sys.argv.index("--sock") + 1]
    main()
