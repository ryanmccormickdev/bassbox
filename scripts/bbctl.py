#!/usr/bin/env python3
"""bbctl: talk to bassbox-engine over its unix socket (JSON lines, docs/architecture.md section 3).

    bbctl.py state
    bbctl.py add type=delay index=0
    bbctl.py set_param uid=3 param=time value=350
    bbctl.py set_master out_gain_db=-12
    bbctl.py '{"cmd": "meters"}'
    bbctl.py --watch [--meters]           subscribe and print events
    bbctl.py --socket /tmp/x.sock status

key=value values are parsed as JSON when possible (numbers, true/false, objects), else strings.
"""
import argparse
import json
import socket
import sys
import time


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--socket", default="/run/bassbox/engine.sock")
    ap.add_argument("--watch", action="store_true", help="subscribe and print events")
    ap.add_argument("--meters", action="store_true", help="with --watch: include 30 Hz meters")
    ap.add_argument("--seconds", type=float, default=0, help="with --watch: stop after N seconds")
    ap.add_argument("cmd", nargs="?")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(a.socket)
    f = s.makefile("rwb", buffering=0)

    if a.watch:
        f.write((json.dumps({"cmd": "subscribe", "meters": a.meters}) + "\n").encode())
        t0 = time.time()
        for line in f:
            print(line.decode().rstrip(), flush=True)
            if a.seconds and time.time() - t0 > a.seconds:
                break
        return 0

    if not a.cmd:
        ap.print_help()
        return 64
    if a.cmd.startswith("{"):
        req = json.loads(a.cmd)
    else:
        req = {"cmd": a.cmd}
        for kv in a.args:
            k, _, v = kv.partition("=")
            try:
                req[k] = json.loads(v)
            except ValueError:
                req[k] = v
    req.setdefault("id", 1)
    f.write((json.dumps(req) + "\n").encode())
    resp = json.loads(f.readline())
    print(json.dumps(resp, indent=1))
    return 0 if resp.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
