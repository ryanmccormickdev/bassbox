"""Base screen: owns the shared header and wires model notifications to it."""
import types

import core
from art import NEUTRAL, accent
from core import Screen

# filled in by main: model, header, nav, touch_cal_inverse, apply_calibration
ctx = types.SimpleNamespace(model=None, header=None, nav=None, touch_cal_inverse=None,
                            apply_calibration=lambda raws, targets: (False, []))


class BaseScreen(Screen):
    def __init__(self):
        super().__init__()
        self.content = []

    @property
    def model(self):
        return ctx.model

    @property
    def nav(self):
        return ctx.nav

    def on_show(self):
        h = ctx.header
        if h.parent is not None and h in h.parent.children:
            h.parent.children.remove(h)
        self.add(h)                    # header on top of content (plugs can hang over the gutter)
        h.jack_in.set_compact(self.name != "board")    # full socket only on the board (it feeds the gutter line)
        h.jack_out.set_compact(self.name != "board")
        self.configure_header()
        self.sync_header()

    def configure_header(self):
        ctx.header.configure()

    # ---- header live state (jacks, meters) shared by every screen
    def out_color(self):
        m = self.model
        if m.bypassed():
            return NEUTRAL
        c = NEUTRAL
        for s in m.chain:
            if s.get("enabled", True):
                c = accent(m.info(s)["category"])
        return c

    def sync_header(self):
        m, h = self.model, ctx.header
        h.jack_in.set_plugged(m.jacks.get("input", True))
        h.jack_out.set_plugged(m.jacks.get("output", True))
        mt = m.meters
        live_in = m.jacks.get("input", True)
        h.m_in.set(mt.get("in_peak", 0) if live_in else 0, mt.get("clip_in"), live_in)
        h.m_out.set(mt.get("out_peak", 0), mt.get("clip_out"), m.jacks.get("output", True))
        h.jack_in.set_level(NEUTRAL, min(1.0, (mt.get("in_rms", 0) or 0) * 5) if live_in else 0)
        h.jack_out.set_level(self.out_color(), min(1.0, (mt.get("out_rms", 0) or 0) * 5))

    def on_model(self, what):
        if what in ("meters", "jacks", "chain", "master"):
            self.sync_header()
        if what == "connection":
            ctx.nav.update_banner()
