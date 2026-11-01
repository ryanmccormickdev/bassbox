#!/usr/bin/env python3
"""Mock bassbox engine: the docs/architecture.md section 3 protocol with fake but realistic data.

    python3 ui/mock_engine.py --socket /tmp/bassbox-mock.sock [--user-dir DIR] [--catalog PATH] [--silent]

- Catalog from engine/catalog.json, else ui/catalog_fallback.json.
- Meters follow a bass-like envelope: phrases of plucked notes (8 ms attack, ~0.7 s decay), with rests.
- Tuner: cycles E1/A1/D2/G2 strings drifting into tune.
- Presets: factory from presets/factory/*.json (plus a few built-ins), user presets in --user-dir.
Mock-only commands (for tests and ui/tools/inject.py):
    {"cmd":"mock_jack","input":false}   {"cmd":"mock_signal","on":false}   {"cmd":"mock_xrun"}
"""
import argparse
import glob
import json
import math
import os
import random
import selectors
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

BUILTIN_FACTORY = {
    "Clean DI": [],
    "Grit": [("comp", {}), ("overdrive", {"drive": 55}), ("eq3", {})],
    "Velvet Mammoth": [("distortion", {"drive": 62, "tone": 45}), ("delay", {"time": 380}), ("wah", {})],
    "Dub Echo": [("comp", {}), ("tape", {"time": 420, "fdbk": 55}), ("reverb", {})],
    "Funk Machine": [("comp", {}), ("env_filter", {"sens": 70}), ("chorus", {})],
    "Sub Synth": [("gate", {}), ("subsynth", {}), ("synth_filter", {})],
}


class Bass:
    """Fake bass player: envelope + note pitch."""

    def __init__(self, seed=7):
        self.rng = random.Random(seed)
        self.on = True
        self.t_note = 0.0
        self.amp = 0.0
        self.peak = 0.0
        self.phrase_end = time.monotonic() + 8
        self.rest_end = 0.0
        self.next_note = time.monotonic()

    def level(self, now):
        if not self.on:
            return 0.0, 0.0
        if now > self.phrase_end and now > self.rest_end:
            self.rest_end = now + self.rng.uniform(2.0, 4.0)
            self.phrase_end = self.rest_end + self.rng.uniform(6, 12)
        playing = now >= self.rest_end
        if playing and now >= self.next_note:
            self.t_note = now
            self.peak = self.rng.uniform(0.25, 0.75)
            self.next_note = now + self.rng.choice([0.25, 0.25, 0.5, 0.5, 0.75, 1.0])
        dt = now - self.t_note
        env = self.peak * (min(1.0, dt / 0.008)) * math.exp(-dt / 0.7) if self.t_note else 0.0
        env += 0.0015 * self.rng.random()  # hiss floor
        return min(1.2, env * 1.35), env * 0.55


class Engine:
    def __init__(self, catalog_path, user_dir, factory_dir):
        self.catalog = self._load_catalog(catalog_path)
        self.by_id = {e["id"]: e for e in self.catalog}
        self.user_dir = user_dir
        os.makedirs(user_dir, exist_ok=True)
        self.factory = self._load_factory(factory_dir)
        self.uid = 1
        self.state = {"name": "Velvet Mammoth", "master": {"in_gain_db": 0.0, "out_gain_db": -12.0, "bypass": False},
                      "chain": [], "dirty": False}
        self._apply_preset(self.factory.get("Velvet Mammoth") or next(iter(self.factory.values())))
        self.state["name"] = "Velvet Mammoth"
        self.jacks = {"input": True, "output": True}
        self.bass = Bass()
        self.tuner_on = False
        self.tuner_t0 = 0.0
        self.xruns = 0
        self.clip_until = {"in": 0.0, "out": 0.0}

    # ----------------------------------------------------------- data
    def _load_catalog(self, path):
        for p in [path, os.path.join(REPO, "engine", "catalog.json"), os.path.join(HERE, "catalog_fallback.json")]:
            if p and os.path.exists(p):
                d = json.load(open(p))
                print("mock: catalog", p, file=sys.stderr)
                return d["effects"] if isinstance(d, dict) else d
        raise SystemExit("no catalog")

    def _slot(self, typ, params=None, enabled=True, mix=1.0):
        info = self.by_id[typ]
        ps = {p["id"]: p["def"] for p in info["params"]}
        for k, v in (params or {}).items():
            if k in ps:
                ps[k] = v
        s = {"uid": self.uid, "type": typ, "enabled": enabled, "mix": mix, "params": ps}
        self.uid += 1
        return s

    def _load_factory(self, d):
        out = {}
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            try:
                p = json.load(open(f))
                out[p.get("name") or os.path.basename(f)[:-5]] = p
            except Exception as e:
                print("mock: bad preset", f, e, file=sys.stderr)
        if not out:
            for name, chain in BUILTIN_FACTORY.items():
                ch = [{"type": t, "enabled": not (name == "Velvet Mammoth" and t == "delay"), "mix": 1.0, "params": pr}
                      for t, pr in chain if t in self.by_id]
                out[name] = {"name": name, "master": {"in_gain_db": 0, "out_gain_db": -12, "bypass": False}, "chain": ch}
        return out

    def _user(self):
        out = {}
        for f in sorted(glob.glob(os.path.join(self.user_dir, "*.json"))):
            try:
                p = json.load(open(f))
                out[p["name"]] = p
            except Exception:
                pass
        return out

    def _apply_preset(self, p):
        self.state["chain"] = [self._slot(s["type"], s.get("params"), s.get("enabled", True), s.get("mix", 1.0))
                               for s in p.get("chain", []) if s.get("type") in self.by_id]
        m = dict(self.state["master"])
        m.update(p.get("master", {}))
        self.state["master"] = m
        self.state["name"] = p.get("name", "Untitled")
        self.state["dirty"] = False

    def _find(self, uid):
        for i, s in enumerate(self.state["chain"]):
            if s["uid"] == uid:
                return i, s
        raise KeyError(f"no slot uid {uid}")

    # ----------------------------------------------------------- commands
    def handle(self, msg):
        cmd = msg.get("cmd")
        fn = getattr(self, "c_" + str(cmd), None)
        if fn is None:
            return {"ok": False, "error": f"unknown cmd {cmd}"}, False
        try:
            res, changed = fn(msg)
        except (KeyError, ValueError, TypeError) as e:
            return {"ok": False, "error": str(e)}, False
        if changed:
            self.state["dirty"] = True
        out = {"ok": True}
        out.update(res or {})
        return out, changed

    def c_catalog(self, m):
        return {"effects": self.catalog}, False

    def c_state(self, m):
        return {"state": self.state}, False

    def c_add(self, m):
        if len(self.state["chain"]) >= 8:
            raise ValueError("chain full (8)")
        s = self._slot(m["type"])
        idx = m.get("index")
        ch = self.state["chain"]
        ch.insert(len(ch) if idx is None else max(0, min(len(ch), int(idx))), s)
        return {"uid": s["uid"]}, True

    def c_remove(self, m):
        i, _ = self._find(m["uid"])
        del self.state["chain"][i]
        return None, True

    def c_move(self, m):
        i, s = self._find(m["uid"])
        ch = self.state["chain"]
        del ch[i]
        ch.insert(max(0, min(len(ch), int(m["index"]))), s)
        return None, True

    def c_set_param(self, m):
        _, s = self._find(m["uid"])
        info = self.by_id[s["type"]]
        p = next(p for p in info["params"] if p["id"] == m["param"])
        s["params"][p["id"]] = max(p["min"], min(p["max"], float(m["value"])))
        return None, True

    def c_set_enabled(self, m):
        _, s = self._find(m["uid"])
        s["enabled"] = bool(m["enabled"])
        return None, True

    def c_set_mix(self, m):
        _, s = self._find(m["uid"])
        s["mix"] = max(0.0, min(1.0, float(m["mix"])))
        return None, True

    def c_set_master(self, m):
        for k in ("in_gain_db", "out_gain_db", "bypass"):
            if k in m:
                self.state["master"][k] = bool(m[k]) if k == "bypass" else float(m[k])
        return None, True

    def c_jacks(self, m):
        return dict(self.jacks), False

    def c_tuner(self, m):
        self.tuner_on = bool(m.get("on"))
        self.tuner_t0 = time.monotonic()
        return None, False

    def c_presets(self, m):
        user = self._user()
        lst = [{"name": n, "factory": True} for n in self.factory if n not in user]
        lst += [{"name": n, "factory": False} for n in user]
        return {"presets": lst, "current": self.state["name"], "dirty": self.state["dirty"]}, False

    def c_load_preset(self, m):
        name = m["name"]
        p = self._user().get(name) or self.factory.get(name)
        if p is None:
            raise KeyError(f"no preset {name}")
        self._apply_preset(p)
        return None, False

    def c_save_preset(self, m):
        name = m.get("name") or self.state["name"]
        p = {"name": name, "master": self.state["master"],
             "chain": [{k: v for k, v in s.items() if k != "uid"} for s in self.state["chain"]]}
        fn = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in name).strip() or "preset"
        json.dump(p, open(os.path.join(self.user_dir, fn + ".json"), "w"), indent=1)
        self.state["name"] = name
        self.state["dirty"] = False
        return None, False

    def c_delete_preset(self, m):
        name = m["name"]
        for f in glob.glob(os.path.join(self.user_dir, "*.json")):
            try:
                if json.load(open(f)).get("name") == name:
                    os.remove(f)
                    return None, False
            except Exception:
                pass
        if name in self.factory:
            raise ValueError("factory presets can't be deleted")
        raise KeyError(f"no preset {name}")

    def c_meters(self, m):
        return self.meters(), False

    def c_test_tone(self, m):
        on = bool(m.get("on"))
        self.tone_until = time.monotonic() + 30 if on else 0
        self.tone_db = float(m.get("db", -18))
        return {"on": on, "hz": float(m.get("hz", 110)), "db": self.tone_db}, False

    def c_mock_jack(self, m):
        for k in ("input", "output"):
            if k in m:
                self.jacks[k] = bool(m[k])
        return dict(self.jacks), False

    def c_mock_signal(self, m):
        self.bass.on = bool(m.get("on", True))
        return None, False

    def c_mock_xrun(self, m):
        self.xruns += 1
        return None, False

    # ----------------------------------------------------------- meters
    def meters(self):
        now = time.monotonic()
        pk, rms = self.bass.level(now)
        g_in = 10 ** (self.state["master"]["in_gain_db"] / 20)
        if not self.jacks["input"]:
            pk = rms = 0.0
        raw_pk, raw_rms = pk, rms            # like the engine: input meters are pre-gain (raw ADC)
        out_pk, out_rms = pk * g_in, rms * g_in
        slots = {}
        for s in self.state["chain"]:
            if not s["enabled"] or self.state["master"]["bypass"]:
                slots[str(s["uid"])] = 0.0
                continue
            cat = self.by_id[s["type"]]["category"]
            if cat == "drive":
                out_rms = min(0.9, out_rms * 1.6 + 0.02 * (out_rms > 0.01))
                out_pk = min(1.05, out_pk * 1.15)
                act = min(1.0, out_rms * 2.2)
            elif cat == "modulation":
                act = 0.5 + 0.5 * math.sin(now * 2 * math.pi * 0.8)
            elif cat == "time":
                act = min(1.0, rms * 2.5 + 0.3 * math.exp(-((now - self.bass.t_note) / 1.5)) * (rms > 0.002))
            else:
                act = min(1.0, rms * 2.4)
            slots[str(s["uid"])] = round(act, 3)
        g_out = 10 ** (self.state["master"]["out_gain_db"] / 20)
        out_pk, out_rms = out_pk * g_out * 2.2, out_rms * g_out * 2.2
        if self.tuner_on:
            out_pk = out_rms = 0.0
        tone = now < getattr(self, "tone_until", 0)
        if tone:
            a = 10 ** (self.tone_db / 20) * 10 ** (self.state["master"]["out_gain_db"] / 20)
            out_pk, out_rms = max(out_pk, a), max(out_rms, a * 0.707)
        pk, rms = raw_pk, raw_rms
        if pk >= 0.999:
            self.clip_until["in"] = now + 1
        if out_pk >= 0.999:
            self.clip_until["out"] = now + 1
        m = {"in_peak": round(pk, 4), "in_rms": round(rms, 4), "out_peak": round(out_pk, 4), "out_rms": round(out_rms, 4),
             "dsp_load": round(0.08 + 0.035 * len(self.state["chain"]) + 0.01 * random.random(), 3), "xruns": self.xruns,
             "slots": slots, "clip_in": now < self.clip_until["in"], "clip_out": now < self.clip_until["out"],
             "test_tone": tone}
        if self.tuner_on:
            m["tuner"] = self.tuner(now)
        return m

    def tuner(self, now):
        t = now - self.tuner_t0
        k = int(t // 9)
        hz0 = [41.20, 55.00, 73.42, 98.00][k % 4]
        start = [34, -27, 18, -41][k % 4]
        ph = t % 9
        cents = start * math.exp(-ph / 2.0) + 1.2 * math.sin(now * 7.3)
        if self.bass.on and self.jacks["input"]:
            hz = hz0 * 2 ** (cents / 1200)
            midi = 69 + 12 * math.log2(hz / 440)
            n = int(round(midi))
            return {"hz": round(hz, 2), "note": NOTES[n % 12], "octave": n // 12 - 1,
                    "cents": round((midi - n) * 100, 1), "confidence": 0.95}
        return {"hz": 0, "note": "", "octave": 0, "cents": 0, "confidence": 0.0}


class Server:
    def __init__(self, path, engine):
        self.path, self.e = path, engine
        if os.path.exists(path):
            os.unlink(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.ls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.ls.bind(path)
        os.chmod(path, 0o666)
        self.ls.listen(8)
        self.ls.setblocking(False)
        self.sel = selectors.DefaultSelector()
        self.sel.register(self.ls, selectors.EVENT_READ, None)
        self.clients = {}   # sock -> dict(buf, sub, meters)
        self.last_jacks = dict(engine.jacks)

    def send(self, s, obj):
        try:
            s.sendall((json.dumps(obj, separators=(",", ":")) + "\n").encode())
        except OSError:
            self.drop(s)

    def drop(self, s):
        if s in self.clients:
            self.sel.unregister(s)
            del self.clients[s]
            s.close()

    def run(self):
        next_meter = time.monotonic()
        while True:
            subs_m = [s for s, c in self.clients.items() if c["meters"]]
            timeout = max(0.0, next_meter - time.monotonic()) if subs_m else 0.5
            for key, _ in self.sel.select(timeout):
                if key.data is None:
                    c, _ = self.ls.accept()
                    c.setblocking(True)
                    self.clients[c] = {"buf": b"", "sub": False, "meters": False}
                    self.sel.register(c, selectors.EVENT_READ, "c")
                    continue
                s = key.fileobj
                try:
                    data = s.recv(65536)
                except OSError:
                    data = b""
                if not data:
                    self.drop(s)
                    continue
                cl = self.clients[s]
                cl["buf"] += data
                while b"\n" in cl["buf"]:
                    line, cl["buf"] = cl["buf"].split(b"\n", 1)
                    if line.strip():
                        self.on_line(s, cl, line)
            if self.e.jacks != self.last_jacks:
                self.last_jacks = dict(self.e.jacks)
                for s, c in list(self.clients.items()):
                    if c["sub"]:
                        self.send(s, {"event": "jacks", **self.e.jacks})
            now = time.monotonic()
            if subs_m and now >= next_meter:
                next_meter = now + 1 / 30
                m = self.e.meters()
                for s in subs_m:
                    self.send(s, {"event": "meters", **m})

    def on_line(self, s, cl, line):
        try:
            msg = json.loads(line)
        except ValueError:
            return self.send(s, {"ok": False, "error": "bad json"})
        if msg.get("cmd") == "subscribe":
            cl["sub"] = True
            cl["meters"] = bool(msg.get("meters"))
            resp, changed = {"ok": True}, False
        else:
            resp, changed = self.e.handle(msg)
        if "id" in msg:
            resp["id"] = msg["id"]
        self.send(s, resp)
        if changed or msg.get("cmd") in ("load_preset", "save_preset"):
            for o, c in list(self.clients.items()):
                if o is not s and c["sub"]:
                    self.send(o, {"event": "state", "state": self.e.state})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--socket", default="/tmp/bassbox-mock.sock")
    ap.add_argument("--catalog")
    ap.add_argument("--user-dir", default="/tmp/bassbox-mock/presets")
    ap.add_argument("--factory-dir", default=os.path.join(REPO, "presets", "factory"))
    ap.add_argument("--silent", action="store_true", help="start with no input signal")
    a = ap.parse_args()
    e = Engine(a.catalog, a.user_dir, a.factory_dir)
    e.bass.on = not a.silent
    print(f"mock engine on {a.socket}: {len(e.catalog)} effects, {len(e.factory)} factory presets", file=sys.stderr)
    Server(a.socket, e).run()


if __name__ == "__main__":
    main()
