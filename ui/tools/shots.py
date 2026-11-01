#!/usr/bin/env python3
"""Drive the UI through every screen/state via the debug socket and save PNG screenshots.

    python3 ui/tools/shots.py OUTDIR [--sock /run/bassbox/ui-debug.sock] [--mock]   (--mock: allows mock-only steps)

Works against the mock or the real engine (chain setup uses the engine passthrough). Restores nothing:
run it on a scratch state.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inject import send  # noqa: E402

SOCK = os.environ.get("BASSBOX_UI_DEBUG_SOCK", "/run/bassbox/ui-debug.sock")


def ui(msg, **kw):
    if isinstance(msg, str):
        msg = {"cmd": msg}
    msg.update(kw)
    r = send(SOCK, msg)
    if not r.get("ok"):
        print("FAIL", msg, r, file=sys.stderr)
    return r


def eng(cmd, **args):
    r = ui({"cmd": "engine", "msg": dict(cmd=cmd, **args)})
    return r.get("response", {})


def set_chain(slots):
    """slots: list of (type, enabled, params)."""
    st = eng("state").get("state", {})
    for s in st.get("chain", []):
        eng("remove", uid=s["uid"])
    for typ, en, params in slots:
        r = eng("add", type=typ)
        uid = r.get("uid")
        for k, v in params.items():
            eng("set_param", uid=uid, param=k, value=v)
        if not en:
            eng("set_enabled", uid=uid, enabled=False)
    eng("set_master", bypass=False)
    ui("refresh")


def shot(outdir, name, now=False):
    import base64
    path = os.path.join(outdir, name + ".png")
    r = ui({"cmd": "screenshot", "stream": True, "now": now})
    if r.get("ok"):
        open(path, "wb").write(base64.b64decode(r["png_b64"]))
    print("shot", path)


def main():
    global SOCK
    a = sys.argv[1:]
    if "--sock" in a:
        i = a.index("--sock")
        SOCK = a[i + 1]
        del a[i:i + 2]
    mock = "--mock" in a
    a = [x for x in a if x != "--mock"]
    out = a[0] if a else "shots"
    os.makedirs(out, exist_ok=True)
    ui({"cmd": "jack", "which": "in", "plugged": 1})
    ui({"cmd": "jack", "which": "out", "plugged": 1})
    ui("goto", screen="board")
    set_chain([("distortion", True, {"drive": 62, "tone": 45}), ("delay", False, {"time": 380, "fdbk": 35}),
               ("wah", True, {"pos": 0.68})])
    ui("goto", screen="board")
    time.sleep(0.6)
    shot(out, "board_3")
    st = ui("state")
    dist = st["chain"][0]["uid"]
    ui("goto", screen="edit", uid=dist)
    ui({"cmd": "drag", "x1": 82, "y1": 200, "x2": 82, "y2": 176, "ms": 250})
    shot(out, "edit_distortion")
    ui({"cmd": "jack", "which": "in", "plugged": 0})
    ui("goto", screen="board")
    time.sleep(0.6)
    shot(out, "board_in_unplugged")
    ui({"cmd": "jack", "which": "in", "plugged": 1})
    eng("set_master", bypass=True)
    ui("refresh")
    time.sleep(0.8)
    shot(out, "board_bypass")
    eng("set_master", bypass=False)
    ui("refresh")
    set_chain([("comp", True, {}), ("octaver", True, {}), ("fuzz", True, {}), ("geq7", True, {"b120": 4, "b800": -5}),
               ("chorus", False, {}), ("tape", True, {}), ("boost", True, {"gain": 3})])
    ui("goto", screen="board")
    time.sleep(0.5)
    shot(out, "board_7")
    st = ui("state")
    geq = next(s["uid"] for s in st["chain"] if s["type"] == "geq7")
    ui("goto", screen="edit", uid=geq)
    shot(out, "edit_geq7")
    ui("goto", screen="library", index=7)
    shot(out, "library")
    ui({"cmd": "tap", "x": 80, "y": 190})   # DRIVE category tile
    shot(out, "library_drive")
    ui("goto", screen="presets")
    shot(out, "presets")
    ui("goto", screen="saveas")
    shot(out, "saveas")
    ui("goto", screen="tuner")
    time.sleep(2.5)
    shot(out, "tuner")
    ui("goto", screen="settings")
    time.sleep(1.2)
    shot(out, "settings")
    ui("goto", screen="board")
    ui("goto", screen="splash")
    shot(out, "splash", now=True)
    time.sleep(1.0)
    if "--cleanup" in sys.argv:          # loopback-safe: leave an empty chain
        set_chain([])
        ui("goto", screen="board")
    print(json.dumps(ui("state").get("screen")))


if __name__ == "__main__":
    main()
