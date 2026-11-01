#!/usr/bin/env python3
"""Create a virtual USB 'footswitch + knob' with /dev/uinput, press KEY_B (bypass) twice and turn REL_DIAL,
then report what the UI did. Run as root on the Pi:  sudo python3 /opt/bassbox/ui/tools/uinput_test.py"""
import fcntl
import json
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from inject import send  # noqa: E402

SOCK = "/run/bassbox/ui-debug.sock"
UI_SET_EVBIT, UI_SET_KEYBIT, UI_SET_RELBIT = 0x40045564, 0x40045565, 0x40045566
UI_DEV_CREATE, UI_DEV_DESTROY = 0x5501, 0x5502
EV = struct.Struct("llHHi")


def emit(fd, typ, code, val):
    os.write(fd, EV.pack(0, 0, typ, code, val))
    os.write(fd, EV.pack(0, 0, 0, 0, 0))   # SYN_REPORT


fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
for ev in (1, 2):
    fcntl.ioctl(fd, UI_SET_EVBIT, ev)
for k in (48, 20, 2, 3, 105, 106):
    fcntl.ioctl(fd, UI_SET_KEYBIT, k)
fcntl.ioctl(fd, UI_SET_RELBIT, 7)
dev = struct.pack("80sHHHHi" + "i" * 256, b"bassbox-test-footswitch", 3, 0x1234, 0x5678, 1, 0, *([0] * 256))
os.write(fd, dev)
fcntl.ioctl(fd, UI_DEV_CREATE)
time.sleep(3.0)                     # the UI rescans every 2 s
st0 = send(SOCK, {"cmd": "state"})
emit(fd, 1, 48, 1); emit(fd, 1, 48, 0)
time.sleep(0.4)
st1 = send(SOCK, {"cmd": "state"})
emit(fd, 1, 48, 1); emit(fd, 1, 48, 0)
time.sleep(0.4)
st2 = send(SOCK, {"cmd": "state"})
res = {"devices": st1["controls"]["devices"], "bypass": [st0["bypass"], st1["bypass"], st2["bypass"]]}
if st2["chain"]:                     # focus a knob by touching it on the edit screen, then turn the dial
    uid = st2["chain"][0]["uid"]
    send(SOCK, {"cmd": "goto", "screen": "edit", "uid": uid})
    send(SOCK, {"cmd": "tap", "x": 82, "y": 200})
    before = send(SOCK, {"cmd": "state"})["chain"][0]["params"]
    for _ in range(10):
        emit(fd, 2, 7, 1)
        time.sleep(0.02)
    time.sleep(0.4)
    after = send(SOCK, {"cmd": "state"})["chain"][0]["params"]
    res["dial"] = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    send(SOCK, {"cmd": "goto", "screen": "board"})
res["actions"] = send(SOCK, {"cmd": "state"})["controls"]["actions"]
fcntl.ioctl(fd, UI_DEV_DESTROY)
os.close(fd)
print(json.dumps(res))
