"""Settings -> Audio check: live input/output meters, auto-set input gain, test tone.

The engine meters the input on the raw ADC signal (before in_gain_db), so auto-gain sets
in_gain_db = -6 dBFS - measured raw peak, clamped to -12..+18 dB."""
import math
import time

import art
import core
from art import CARD, EDGE, TXT, TXT2, TXT3
from chrome import Button, FooterBar, f_label, f_num, f_title, snackbar, wrap
from core import Widget
from gfx import mix
from screens.base import BaseScreen, ctx

TARGET_DB, GAIN_MIN, GAIN_MAX, NO_SIGNAL_DB = -6.0, -12.0, 18.0, -60.0
DB_FLOOR = -60.0


def db(x):
    return 20 * math.log10(max(1e-6, x or 0))


class BigMeter(Widget):
    """Horizontal meter: RMS bar, peak line, 2 s peak hold, dBFS readout, clip lamp."""

    def __init__(self, scr, label, y, h, which):
        super().__init__(10, y, 300, h)
        self.scr, self.label, self.which = scr, label, which
        self.hold, self.hold_t = DB_FLOOR, 0.0
        self.key = None

    def update(self):
        m = self.scr.model.meters
        pk, rms = db(m.get(self.which + "_peak")), db(m.get(self.which + "_rms"))
        now = time.monotonic()
        if pk >= self.hold or now - self.hold_t > 2.0:
            self.hold, self.hold_t = pk, now
        key = (round(pk), round(rms), round(self.hold), bool(m.get("clip_" + self.which)))
        if key != self.key:
            self.key = key
            self.invalidate()

    def x_of(self, d):
        bx, bw = self.x + 14, self.w - 28
        return bx + bw * (max(DB_FLOOR, min(0.0, d)) - DB_FLOOR) / -DB_FLOOR

    def draw(self, cv):
        x, y, w, h = self.rect
        cv.rrect(x, y, w, h, 12, fill=CARD, stroke=EDGE, lw=1.5)
        pk, rms, hold, clip = self.key or (DB_FLOOR, DB_FLOOR, DB_FLOOR, False)
        cv.text(self.label, x + 14, y + 22, f_label(12, 750), TXT2, tracking=1.2)
        cv.text("—" if pk <= DB_FLOOR else f"{pk:.0f} dBFS".replace("-", "−"), x + w - 44, y + 24, f_num(18, 700),
                TXT, align="right")
        cv.circle(x + w - 22, y + 18, 8, fill=art.RED if clip else 0x2A1512, stroke=0x000000, lw=1)
        if clip:
            cv.glow(x + w - 22, y + 18, 18, art.RED, 0.6)
        by, bh = y + 34, h - 50
        bx0, bx1 = self.x_of(DB_FLOOR), self.x_of(0)
        cv.rrect(bx0, by, bx1 - bx0, bh, 4, fill=0x0A0B0E, stroke=0x3A3E46, lw=1)
        if rms > DB_FLOOR:
            xr = self.x_of(rms)
            for lo, hi, c in ((DB_FLOOR, -18, art.GREEN), (-18, -6, art.YELLOW), (-6, 0, art.RED)):
                a, b = self.x_of(lo), min(xr, self.x_of(hi))
                if b > a:
                    cv.rect(a, by + 2, b - a, bh - 4, fill=c)
        if pk > DB_FLOOR:
            cv.rect(self.x_of(pk) - 1, by - 2, 3, bh + 4, fill=0xFFFFFF)
        if hold > DB_FLOOR:
            cv.rect(self.x_of(hold) - 1.5, by - 4, 3, bh + 8, fill=art.YELLOW)
        for d in (-48, -36, -24, -18, -12, -6, 0):
            tx = self.x_of(d)
            cv.rect(tx, by + bh + 2, 1, 4, fill=TXT3)
            cv.text(str(d).replace("-", "−"), tx, by + bh + 16, f_label(12, 500), TXT3, align="center")
        tx = self.x_of(TARGET_DB) if self.which == "in" else None


class AutoGain(Widget):
    """3-2-1 countdown, then 8 s of "play hard"; tracks the raw input peak."""
    ambient = True

    def __init__(self, scr):
        super().__init__(10, 252, 300, 96)
        self.scr = scr
        self.state = "idle"   # idle | count | listen | done | nosignal | clipping
        self.t0 = 0.0
        self.max_pk = 0.0
        self.result = None
        self.btn = Button(22, 300, 150, 40, "Auto-set gain", self.start, "solid", TXT, font_=f_label(14, 750))
        self.b_undo = Button(184, 300, 114, 40, "Undo", self.undo, "outline", TXT, font_=f_label(14, 700))
        self.add(self.btn, self.b_undo)
        self.b_undo.visible = False

    def start(self):
        if self.state in ("count", "listen"):
            return self.cancel()
        self.state, self.t0, self.max_pk = "count", time.monotonic(), 0.0
        self.prev = float(self.scr.model.state["master"].get("in_gain_db", 0.0))
        self.scr.model.fast = True
        self.btn.set_label("Cancel")
        self.b_undo.visible = False
        core.app.animate(self)
        self.invalidate()

    def cancel(self):
        self.state = "idle"
        self.scr.model.fast = False
        self.btn.set_label("Auto-set gain")
        self.invalidate()

    def undo(self):
        self.scr.model.set_master(in_gain_db=self.prev)
        self.state = "idle"
        self.b_undo.visible = False
        snackbar(f"Input gain back to {self.prev:+.1f} dB")
        self.invalidate()

    def feed(self, pk):
        if self.state == "listen":
            self.max_pk = max(self.max_pk, pk or 0.0)

    def tick(self, now):
        el = now - self.t0
        if self.state == "count" and el >= 3.0:
            self.state, self.t0 = "listen", now
        elif self.state == "listen" and el >= 8.0:
            self.finish()
            return None
        if self.state not in ("count", "listen"):
            return None
        self.invalidate((self.x, self.y, self.w, 44))
        return now + 0.25

    def finish(self):
        self.scr.model.fast = False
        self.btn.set_label("Auto-set gain")
        p = db(self.max_pk)
        if p < NO_SIGNAL_DB:
            self.state = "nosignal"
        else:
            g = max(GAIN_MIN, min(GAIN_MAX, round((TARGET_DB - p) * 2) / 2))
            self.result = (p, g)
            self.state = "clipping" if p > -0.5 else "done"
            self.scr.model.set_master(in_gain_db=g)
            self.b_undo.visible = True
        self.invalidate()

    def draw(self, cv):
        x, y, w, h = self.rect
        cv.rrect(x, y, w, h, 12, fill=CARD, stroke=EDGE, lw=1.5)
        now = time.monotonic()
        g = float(self.scr.model.state["master"].get("in_gain_db", 0.0))
        if self.state == "count":
            n = 3 - int(now - self.t0)
            cv.text(f"GET READY… {max(1, n)}", x + 14, y + 28, f_title(15), art.YELLOW)
        elif self.state == "listen":
            left = max(0.0, 8.0 - (now - self.t0))
            cv.text(f"PLAY HARD  {left:.0f} s", x + 14, y + 28, f_title(15), art.GREEN)
            cv.rrect(x + 14, y + 36, (w - 28), 4, 2, fill=0x2C3038)
            cv.rrect(x + 14, y + 36, (w - 28) * (1 - left / 8), 4, 2, fill=art.GREEN)
        elif self.state == "nosignal":
            cv.text("No signal: check the cable/jack", x + 14, y + 28, f_label(14, 700), art.RED)
        elif self.state in ("done", "clipping"):
            p, gg = self.result
            cv.text(f"Peak {p:.0f} dBFS → in gain {gg:+.1f} dB".replace("-", "−"), x + 14, y + 28, f_label(14, 700), art.GREEN)
            if self.state == "clipping":
                cv.text("Input clips at the converter: turn the bass down", x + 14, y + 44, f_label(12, 600), art.YELLOW)
        else:
            cv.text(f"INPUT GAIN {g:+.1f} dB".replace("-", "−"), x + 14, y + 28, f_label(12, 750), TXT2, tracking=1)
            cv.text("Target: peaks near −6 dBFS", x + w - 14, y + 28, f_label(12, 500), TXT3, align="right")


class ToneButton(Widget):
    gestures = ("tap", "press")

    def __init__(self, scr):
        super().__init__(10, 356, 300, 74)
        self.scr = scr
        self.on = False

    def on_tap(self, g):
        want = not self.on
        self.scr.model.client.request("test_tone", on=want, hz=110, db=-18,
                                      cb=lambda r: self._resp(r, want))

    def _resp(self, r, want):
        if r.get("ok"):
            self.set(want)
        else:
            snackbar("Test tone not available yet")

    def set(self, on):
        if on != self.on:
            self.on = on
            self.invalidate()

    def draw(self, cv):
        x, y, w, h = self.rect
        cv.rrect(x, y, w, h, 12, fill=CARD, stroke=art.GREEN if self.on else EDGE, lw=2 if self.on else 1.5)
        art.toggle(cv, x + 14, y + 12, 52, 28, self.on, art.GREEN)
        cv.text("TEST TONE  110 Hz · −18 dB", x + 78, y + 31, f_label(13, 750), TXT)
        for i, ln in enumerate(wrap("Plays a low A out of the OUTPUT jack; if you hear it, the output path works",
                                    f_label(12, 500), w - 28, 2)):
            cv.text(ln, x + 14, y + 54 + i * 14, f_label(12, 500), TXT2)


class AudioCheck(BaseScreen):
    name = "audiocheck"

    def __init__(self):
        super().__init__()
        self.m_in = BigMeter(self, "INPUT · RAW ADC", 80, 84, "in")
        self.m_out = BigMeter(self, "OUTPUT", 170, 76, "out")
        self.auto = AutoGain(self)
        self.tone = ToneButton(self)
        self.footer = FooterBar([("DONE", lambda cv, x, y, c: art.check(cv, x, y, 16, c), lambda: self.nav.settings())])
        self.add(self.m_in, self.m_out, self.auto, self.tone, self.footer)

    def configure_header(self):
        ctx.header.configure(title="Audio check", kicker="SETTINGS", back=lambda: self.nav.settings())

    def on_show(self):
        self.model.fast = True
        self.model._schedule_poll(0.02)
        super().on_show()

    def on_hide(self):
        self.model.fast = False
        self.auto.cancel() if self.auto.state in ("count", "listen") else None

    def on_model(self, what):
        super().on_model(what)
        if what == "meters":
            self.m_in.update()
            self.m_out.update()
            self.auto.feed(self.model.meters.get("in_peak"))
            self.tone.set(bool(self.model.meters.get("test_tone")))
        elif what == "master":
            self.auto.invalidate()
