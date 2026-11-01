#!/usr/bin/env python3
"""Drive the bassbox UI remotely through its debug socket (same gesture pipeline as the touch panel).

    inject.py tap X Y                  inject.py doubletap X Y
    inject.py drag X1 Y1 X2 Y2 [MS]    inject.py longpress X Y [MS] [X2 Y2 [DRAG_MS]]
    inject.py jack in|out 0|1          inject.py screenshot OUT.png
    inject.py state | stats [N] | wait | refresh
    inject.py goto board|library|presets|saveas|tuner|settings|calibrate|edit [UID]
    inject.py engine '{"cmd":"mock_signal","on":false}'      (pass a raw engine command through)

Socket: --sock PATH, $BASSBOX_UI_DEBUG_SOCK, or /run/bassbox/ui-debug.sock. On the Pi run it with ssh:
    ssh <user>@<pi-host> python3 /opt/bassbox/ui/tools/inject.py tap 160 120
Coordinates are screen pixels (320x480 portrait). Every command returns once its result is on screen.
"""
import json
import os
import shutil
import socket
import sys
import tempfile


def send(sock_path, msg, timeout=30):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(sock_path)
    s.sendall((json.dumps(msg) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        d = s.recv(65536)
        if not d:
            break
        buf += d
    s.close()
    return json.loads(buf.split(b"\n", 1)[0] or b"{}")


def main(argv):
    sock = os.environ.get("BASSBOX_UI_DEBUG_SOCK", "/run/bassbox/ui-debug.sock")
    if argv[:1] == ["--sock"]:
        sock, argv = argv[1], argv[2:]
    if not argv:
        print(__doc__)
        return 2
    cmd, a = argv[0], argv[1:]
    f = [float(x) for x in a if _num(x)]
    out = None
    if cmd == "tap":
        msg = {"cmd": "tap", "x": f[0], "y": f[1]}
    elif cmd == "doubletap":
        msg = {"cmd": "doubletap", "x": f[0], "y": f[1]}
    elif cmd == "drag":
        msg = {"cmd": "drag", "x1": f[0], "y1": f[1], "x2": f[2], "y2": f[3], "ms": f[4] if len(f) > 4 else 300}
    elif cmd == "longpress":
        msg = {"cmd": "longpress", "x": f[0], "y": f[1], "ms": f[2] if len(f) > 2 else 800}
        if len(f) >= 5:
            msg.update(x2=f[3], y2=f[4], drag_ms=f[5] if len(f) > 5 else 400)
    elif cmd == "jack":
        msg = {"cmd": "jack", "which": a[0], "plugged": int(a[1])}
    elif cmd == "screenshot":
        out = os.path.abspath(a[0] if a else "bassbox-ui.png")
        msg = {"cmd": "screenshot", "stream": True}
    elif cmd in ("state", "wait", "refresh"):
        msg = {"cmd": {"wait": "wait_idle"}.get(cmd, cmd)}
    elif cmd == "stats":
        msg = {"cmd": "stats", "n": int(f[0]) if f else 60}
    elif cmd == "goto":
        msg = {"cmd": "goto", "screen": a[0]}
        if len(a) > 1:
            msg["uid"] = int(a[1])
    elif cmd == "engine":
        msg = {"cmd": "engine", "msg": json.loads(a[0])}
    else:
        print("unknown command", cmd, file=sys.stderr)
        return 2
    r = send(sock, msg)
    if out and r.get("ok"):           # PNG bytes come over the socket; written as the calling user
        import base64
        with open(out, "wb") as fh:
            fh.write(base64.b64decode(r.pop("png_b64")))
        r["path"] = out
    print(json.dumps(r, indent=None if cmd not in ("state", "stats") else 1))
    return 0 if r.get("ok") else 1


def _num(x):
    try:
        float(x)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
