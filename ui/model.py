"""Client-side model of the engine: catalog, state, presets, jacks, meters, connection.

All engine traffic goes through here. Edits are applied optimistically to the local state and sent to
the engine; structural edits re-fetch `state` afterwards so the UI converges on the engine's truth.
Screens get `on_model(what)` callbacks.
"""
import json
import os
import time

import core
from engine_client import EngineClient

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_SLOTS = 8
QUIET = 0.004          # rms below this counts as "no signal"


class Model:
    def __init__(self, sock_path, log=print):
        self.log = log
        self.catalog = []
        self.by_id = {}
        self.state = {"name": "", "master": {"in_gain_db": 0.0, "out_gain_db": 0.0, "bypass": False}, "chain": [], "dirty": False}
        self.presets = []
        self.current = ""
        self.jacks = {"input": True, "output": True}
        self.jack_override = {}
        self.meters = {"in_peak": 0, "in_rms": 0, "out_peak": 0, "out_rms": 0, "dsp_load": 0, "xruns": 0, "slots": {},
                       "clip_in": False, "clip_out": False}
        self.tuner = None
        self.tuner_on = False
        self.connected = False
        self.ever_connected = False
        self.state_loaded = False
        self.last_signal = 0.0
        self.engine_version = ""
        self.deviations = set()
        self.listeners = []
        self.client = EngineClient(sock_path, on_event=self._event, on_connect=self._connected,
                                   on_disconnect=self._disconnected, log=log)
        core.app.io.append(self.client)
        self._poll_timer = None
        self._load_catalog_file()

    # ------------------------------------------------------------- plumbing
    def notify(self, what):
        app = core.app
        for l in list(self.listeners):
            l(what)
        if app.screen is not None:
            app.screen.on_model(what)
        for o in list(app.overlays):
            if hasattr(o, "on_model"):
                o.on_model(what)

    def _load_catalog_file(self):
        """Until the engine answers, use engine/catalog.json (same content) so screens can lay out."""
        for p in (os.path.join(HERE, "..", "engine", "catalog.json"), os.path.join(HERE, "catalog_fallback.json")):
            if os.path.exists(p):
                try:
                    d = json.load(open(p))
                    self._set_catalog(d["effects"] if isinstance(d, dict) else d)
                    return
                except Exception as e:
                    self.log("catalog file:", e)

    def _set_catalog(self, effects):
        for e in effects:
            for p in e.get("params", []):
                p["_eid"] = e.get("id")
        self.catalog = [e for e in effects if e.get("id")]
        self.by_id = {e["id"]: e for e in self.catalog}

    def _connected(self):
        self.connected = True
        self.ever_connected = True
        c = self.client
        c.request("subscribe")
        c.request("catalog", cb=self._on_catalog)
        c.request("state", cb=self._on_state)
        c.request("presets", cb=self._on_presets)
        c.request("jacks", cb=self._on_jacks)
        if self.tuner_on:
            c.request("tuner", on=True)
        self.notify("connection")
        self._schedule_poll(0.05)

    def _disconnected(self):
        self.connected = False
        self.notify("connection")

    def _event(self, ev):
        name = ev.get("event")
        if name == "jacks":
            self._on_jacks(ev)
        elif name == "state":
            self._on_state(ev)
        elif name == "meters":
            self._on_meters(ev)
        elif name == "presets":
            self._on_presets(ev)

    # ------------------------------------------------------------- responses
    def _on_catalog(self, r):
        if r.get("ok") and r.get("effects"):
            self._set_catalog(r["effects"])
            self.notify("catalog")

    def _on_state(self, r):
        st = r.get("state")
        if not st:
            return
        st.setdefault("master", {})
        for k, v in (("in_gain_db", 0.0), ("out_gain_db", 0.0), ("bypass", False)):
            st["master"].setdefault(k, v)
        for s in st.get("chain", []):
            s.setdefault("enabled", True)
            s.setdefault("mix", 1.0)
            s.setdefault("params", {})
        st.setdefault("dirty", False)
        self.state = st
        self.state_loaded = True
        self.notify("chain")
        self.notify("master")

    def _on_presets(self, r):
        if r.get("ok", True) and "presets" in r:
            self.presets = r["presets"]
            self.current = r.get("current", self.current)
            if "dirty" in r:
                self.state["dirty"] = r["dirty"]
            self.notify("presets")

    def _on_jacks(self, r):
        changed = False
        for k in ("input", "output"):
            if k in r:
                v = bool(r[k])
                self.jack_override.pop(k, None)
                if v != self.jacks.get(k):
                    self.jacks[k] = v
                    changed = True
        if changed:
            self.notify("jacks")

    def _on_meters(self, r):
        if not r.get("ok", True):
            return
        m = self.meters
        for k in ("in_peak", "in_rms", "out_peak", "out_rms", "dsp_load", "xruns", "clip_in", "clip_out", "test_tone"):
            if k in r:
                m[k] = r[k]
        m["slots"] = {int(k): v for k, v in (r.get("slots") or {}).items()}
        if max(m["in_rms"] or 0, m["out_rms"] or 0) > QUIET:
            self.last_signal = time.monotonic()
        self.tuner = r.get("tuner")
        self.notify("meters")
        if self.tuner_on:
            self.notify("tuner")

    # ------------------------------------------------------------- meter polling (adaptive)
    def signal_present(self):
        return time.monotonic() - self.last_signal < 1.5

    def _schedule_poll(self, delay=None):
        core.app.cancel(self._poll_timer)
        if delay is None:
            fast = self.tuner_on or self.signal_present() or getattr(self, "fast", False)
            delay = 1 / 20 if fast else 1 / 3
        self._poll_timer = core.app.after(delay, self._poll)

    def _poll(self):
        self._poll_timer = None
        if not self.connected:
            return
        self.client.request("meters", cb=lambda r: (self._on_meters(r), self._schedule_poll()))

    # ------------------------------------------------------------- queries
    @property
    def chain(self):
        return self.state.get("chain", [])

    def slot(self, uid):
        for s in self.chain:
            if s["uid"] == uid:
                return s
        return None

    def index(self, uid):
        for i, s in enumerate(self.chain):
            if s["uid"] == uid:
                return i
        return -1

    def info(self, slot_or_type):
        t = slot_or_type["type"] if isinstance(slot_or_type, dict) else slot_or_type
        return self.by_id.get(t) or {"id": t, "name": t, "category": "utility", "description": "", "params": []}

    def pval(self, slot, p):
        return slot.get("params", {}).get(p["id"], p.get("def", p["min"]))

    def bypassed(self):
        return bool(self.state["master"].get("bypass"))

    def input_live(self):
        return bool(self.jacks.get("input", True))

    PRESET_DIRS = ["/var/lib/bassbox/presets", os.path.join(HERE, "..", "presets", "factory"),
                   os.environ.get("BASSBOX_MOCK_PRESETS", "/tmp/bassbox-mock/presets")]

    def preset_chain(self, name):
        """Effect types of a preset's chain, read from the preset files (cached by mtime)."""
        import glob
        cache = getattr(self, "_pc", None)
        if cache is None or time.monotonic() - cache[0] > 2.0:
            m = {}
            for d in self.PRESET_DIRS:
                for f in glob.glob(os.path.join(d, "**", "*.json"), recursive=True):
                    try:
                        j = json.load(open(f))
                        m.setdefault(j.get("name") or os.path.basename(f)[:-5],
                                     [(s.get("type"), s.get("enabled", True)) for s in j.get("chain", [])])
                    except Exception:
                        pass
            self._pc = cache = (time.monotonic(), m)
        return cache[1].get(name)

    def current_is_factory(self):
        for p in self.presets:
            if p["name"] == self.state.get("name"):
                return bool(p.get("factory"))
        return False

    # ------------------------------------------------------------- edits
    def _dirty(self):
        if not self.state.get("dirty"):
            self.state["dirty"] = True
            self.notify("presets")

    def refresh_state(self, cb=None):
        self.client.request("state", cb=lambda r: (self._on_state(r), cb and cb(r)))

    def refresh_presets(self):
        self.client.request("presets", cb=self._on_presets)

    def add(self, typ, index=None, cb=None, params=None, enabled=True, mix=1.0):
        if len(self.chain) >= MAX_SLOTS:
            return False
        args = {"type": typ}
        if index is not None:
            args["index"] = index

        def done(r):
            uid = r.get("uid") if r.get("ok") else None
            if uid is not None:
                for k, v in (params or {}).items():
                    self.client.request("set_param", uid=uid, param=k, value=v)
                if not enabled:
                    self.client.request("set_enabled", uid=uid, enabled=False)
                if mix != 1.0:
                    self.client.request("set_mix", uid=uid, mix=mix)
            self.refresh_state(lambda _r: cb and cb(uid))
        self.client.request("add", cb=done, **args)
        self._dirty()
        return True

    def remove(self, uid):
        i = self.index(uid)
        if i < 0:
            return
        del self.chain[i]
        self.notify("chain")
        self._dirty()
        self.client.request("remove", uid=uid, cb=lambda r: self.refresh_state())

    def move(self, uid, index):
        i = self.index(uid)
        if i < 0:
            return
        s = self.chain.pop(i)
        index = max(0, min(len(self.chain), index))
        self.chain.insert(index, s)
        self.notify("chain")
        self._dirty()
        self.client.request("move", uid=uid, index=index, cb=lambda r: self.refresh_state())

    def set_param(self, uid, pid, value):
        s = self.slot(uid)
        if s is None:
            return
        if pid == "__mix":
            s["mix"] = value
        else:
            s["params"][pid] = value
        self._dirty()
        self.client.set_param(uid, pid, value)

    def set_enabled(self, uid, on):
        s = self.slot(uid)
        if s is None:
            return
        s["enabled"] = bool(on)
        self.notify("chain")
        self._dirty()
        self.client.request("set_enabled", uid=uid, enabled=bool(on))

    def set_master(self, **kw):
        self.state["master"].update(kw)
        self.notify("master")
        self.client.request("set_master", **kw)

    def load_preset(self, name, cb=None):
        self.client.request("load_preset", name=name, cb=lambda r: (self.refresh_state(), self.refresh_presets(), cb and cb(r)))

    def save_preset(self, name=None, cb=None):
        args = {"name": name} if name else {}
        self.client.request("save_preset", cb=lambda r: (self.refresh_state(), self.refresh_presets(), cb and cb(r)), **args)

    def delete_preset(self, name, cb=None):
        self.client.request("delete_preset", name=name, cb=lambda r: (self.refresh_presets(), cb and cb(r)))

    def set_tuner(self, on):
        self.tuner_on = bool(on)
        self.client.request("tuner", on=bool(on))
        if not on:
            self.tuner = None
        self._schedule_poll(0.02)

    def simulate_jack(self, which, plugged):
        """Debug/injection: pretend the engine reported a jack change (and tell a mock engine)."""
        k = "input" if which in ("in", "input") else "output"
        self.jack_override[k] = bool(plugged)
        self.client.request("mock_jack", **{k: bool(plugged)})
        if self.jacks.get(k) != bool(plugged):
            self.jacks[k] = bool(plugged)
            self.notify("jacks")
