"""Future hardware controls (docs/architecture.md section 5): USB HID footswitches / knobs / expression pedals.

Hot-plug: /dev/input is rescanned every 2 s; every event device except the touchscreen is read (never grabbed).
Mapping: /var/lib/bassbox/controls.json (falls back to config/controls.json):
  keys  KEY_* -> action        rel  REL_* -> action        abs  ABS_* or "*" -> action
Actions: toggle_slot:N, prev_preset, next_preset, bypass, tuner, board, focus_param.
focus_param adjusts the last knob touched on the edit screen (REL: +-rel_step of range per tick; ABS: absolute).
"""
import fcntl
import json
import os
import struct
import time

import core
import values as V

HERE = os.path.dirname(os.path.abspath(__file__))
EV_KEY, EV_REL, EV_ABS = 1, 2, 3
EVENT = struct.Struct("llHHi")
KEYS = {"KEY_ESC": 1, **{f"KEY_{i}": i + 1 for i in range(1, 10)}, "KEY_0": 11, "KEY_T": 20, "KEY_B": 48,
        "KEY_SPACE": 57, "KEY_UP": 103, "KEY_PAGEUP": 104, "KEY_LEFT": 105, "KEY_RIGHT": 106, "KEY_DOWN": 108,
        "KEY_PAGEDOWN": 109, "KEY_ENTER": 28, "KEY_A": 30, "KEY_N": 49, "KEY_P": 25}
RELS = {"REL_X": 0, "REL_Y": 1, "REL_HWHEEL": 6, "REL_DIAL": 7, "REL_WHEEL": 8}
ABSS = {"ABS_X": 0, "ABS_Y": 1, "ABS_Z": 2, "ABS_RX": 3, "ABS_RY": 4, "ABS_RZ": 5, "ABS_THROTTLE": 6,
        "ABS_RUDDER": 7, "ABS_WHEEL": 8, "ABS_GAS": 9, "ABS_BRAKE": 10}
focus = None      # (uid, param spec) set by the edit screen when a control is touched


def load_map():
    for p in ("/var/lib/bassbox/controls.json", os.path.join(HERE, "..", "config", "controls.json")):
        try:
            m = json.load(open(p))
            return m
        except (OSError, ValueError):
            pass
    return {"keys": {}, "rel": {}, "abs": {}, "rel_step": 0.01}


def _absinfo(fd, code):
    req = (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + code)
    try:
        _v, mn, mx, *_ = struct.unpack("6i", fcntl.ioctl(fd, req, bytes(24)))
        return mn, mx
    except OSError:
        return 0, 255


class Device:
    def __init__(self, ctl, path, name):
        self.ctl, self.path, self.name = ctl, path, name
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.buf = b""
        self.abs = {}

    def fileno(self):
        return self.fd

    def wants_write(self):
        return False

    def on_readable(self):
        try:
            data = os.read(self.fd, EVENT.size * 64)
        except BlockingIOError:
            return
        except OSError:
            return self.ctl.drop(self)
        if not data:
            return self.ctl.drop(self)
        for i in range(0, len(data) - EVENT.size + 1, EVENT.size):
            _s, _us, typ, code, val = EVENT.unpack_from(data, i)
            self.ctl.event(self, typ, code, val)


class Controls:
    SCAN_S = 2.0

    def __init__(self, model, nav, log=print):
        self.model, self.nav, self.log = model, nav, log
        self.map = load_map()
        self.rev_keys = {KEYS[k]: a for k, a in self.map.get("keys", {}).items() if k in KEYS}
        self.rev_rel = {RELS[k]: a for k, a in self.map.get("rel", {}).items() if k in RELS}
        amap = self.map.get("abs", {})
        self.abs_any = amap.get("*")
        self.rev_abs = {ABSS[k]: a for k, a in amap.items() if k in ABSS}
        self.devs = {}
        self.next_scan = 0.0
        self.actions = []           # recent actions (debug/test)
        core.app.io.append(self)

    # io-object protocol for the app loop (only tick/deadline; devices register themselves)
    def fileno(self):
        return None

    def wants_write(self):
        return False

    def next_deadline(self):
        return self.next_scan

    def tick(self, now):
        if now >= self.next_scan:
            self.next_scan = now + self.SCAN_S
            self.scan()

    def scan(self):
        try:
            names = [n for n in os.listdir("/dev/input") if n.startswith("event")]
        except OSError:
            return
        for n in names:
            path = "/dev/input/" + n
            if path in self.devs:
                continue
            try:
                name = open(f"/sys/class/input/{n}/device/name").read().strip()
            except OSError:
                name = "?"
            if "ADS7846" in name or "XPT2046" in name.upper() or getattr(self, "_skip", {}).get(path) == name:
                continue
            try:
                d = Device(self, path, name)
            except OSError:
                self._skip = getattr(self, "_skip", {})
                self._skip[path] = name
                continue
            self.devs[path] = d
            core.app.io.append(d)
            self.log("controls: +", path, name)
        for path in [p for p in self.devs if not os.path.exists(p)]:
            self.drop(self.devs[path])

    def drop(self, d):
        if d in core.app.io:
            core.app.io.remove(d)
        try:
            os.close(d.fd)
        except OSError:
            pass
        self.devs.pop(d.path, None)
        self.log("controls: -", d.path, d.name)

    # ---------------------------------------------------------------- mapping
    def event(self, dev, typ, code, val):
        if typ == EV_KEY and val == 1 and code in self.rev_keys:
            self.do(self.rev_keys[code])
        elif typ == EV_REL and code in self.rev_rel and val:
            self.do(self.rev_rel[code], rel=val)
        elif typ == EV_ABS and (code in self.rev_abs or self.abs_any):
            if code not in dev.abs:
                dev.abs[code] = _absinfo(dev.fd, code)
            mn, mx = dev.abs[code]
            self.do(self.rev_abs.get(code, self.abs_any), absolute=(val - mn) / max(1, mx - mn))

    def do(self, action, rel=None, absolute=None):
        m, nav = self.model, self.nav
        self.actions = (self.actions + [action])[-20:]
        if action.startswith("toggle_slot:"):
            i = int(action.split(":")[1]) - 1
            if 0 <= i < len(m.chain):
                s = m.chain[i]
                m.set_enabled(s["uid"], not s.get("enabled", True))
        elif action in ("prev_preset", "next_preset"):
            names = [p["name"] for p in m.presets]
            if names:
                cur = names.index(m.state.get("name")) if m.state.get("name") in names else -1
                m.load_preset(names[(cur + (1 if action == "next_preset" else -1)) % len(names)])
        elif action == "bypass":
            m.set_master(bypass=not m.bypassed())
        elif action == "tuner":
            nav.board() if core.app.screen and core.app.screen.name == "tuner" else nav.tuner()
        elif action == "board":
            nav.board()
        elif action == "focus_param" and focus is not None:
            uid, p = focus
            s = m.slot(uid)
            if s is None:
                return
            cur = s.get("mix", 1.0) if p["id"] == "__mix" else m.pval(s, p)
            if absolute is not None:
                v = V.denorm(p, absolute)
            elif V.is_discrete(p):
                v = V.clamp(p, cur + (1 if rel > 0 else -1))
            else:
                v = V.denorm(p, V.norm(p, cur) + rel * float(self.map.get("rel_step", 0.01)))
            m.set_param(uid, p["id"], v)
            scr = core.app.screen
            if scr is not None and scr.name == "edit" and getattr(scr, "uid", None) == uid:
                for c in scr.children:
                    if getattr(c, "p", None) is p:
                        c.invalidate()
                scr.lcd.show(p["name"], V.text(p, v))
