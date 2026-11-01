"""Settings: in gain / out level, DSP load, xruns, CPU temp, version, touch recalibration, restart engine."""
import os
import subprocess
import time

import art
import core
from art import CARD, EDGE, TXT, TXT2, TXT3
from chrome import Button, Dialog, FooterBar, f_label, f_num, f_title, snackbar
from core import Screen, Widget
from gfx import font, mix
from screens.base import BaseScreen, ctx
import values as V

UI_VERSION = "1.0"


def cpu_temp():
    try:
        return int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000
    except (OSError, ValueError):
        return None


class GainRow(Widget):
    gestures = ("drag", "double", "press", "long")

    def __init__(self, scr, p, y):
        super().__init__(10, y, 300, 60)
        self.scr, self.p = scr, p
        self.active = False

    def value(self):
        return float(self.scr.model.state["master"].get(self.p["id"], self.p["def"]))

    def on_drag_start(self, g):
        self.active = True
        self.t0 = V.norm(self.p, self.value())
        self.fine = g.long
        core.app.dragging_value = True
        self.invalidate()

    def on_drag(self, g):
        v = V.denorm(self.p, self.t0 + g.dx / 240 * (0.25 if self.fine else 1))
        if v != self.value():
            self.scr.model.set_master(**{self.p["id"]: v})
            self.invalidate()

    def on_drag_end(self, g):
        self.active = False
        core.app.dragging_value = False
        self.invalidate()

    def on_double_tap(self, g):
        self.scr.model.set_master(**{self.p["id"]: V.default(self.p)})
        self.invalidate()

    def draw(self, cv):
        x, y, w, h = self.rect
        cv.rrect(x, y, w, h, 12, fill=CARD, stroke=TXT if self.active else EDGE, lw=1.5)
        v = self.value()
        cv.text(self.p["name"].upper(), x + 14, y + 22, f_label(12, 750), TXT2, tracking=1.2)
        cv.text(V.text(self.p, v), x + w - 14, y + 23, f_num(16, 700), TXT, align="right")
        tx, tw, ty = x + 14, w - 28, y + 38
        t = V.norm(self.p, v)
        z = V.norm(self.p, 0.0)
        cv.rrect(tx, ty, tw, 8, 4, fill=0x2C3038)
        a, b = sorted((z, t))
        cv.rrect(tx + tw * a, ty, max(4, tw * (b - a)), 8, 4, fill=art.NEUTRAL if self.p["id"] == "in_gain_db" else art.GREEN)
        cv.rect(tx + tw * z - 1, ty - 3, 2, 14, fill=TXT3)
        cv.circle(tx + tw * t, ty + 4, 9, fill=0xFFFFFF)


class Stats(Widget):
    def __init__(self, scr):
        super().__init__(10, 214, 300, 84)
        self.scr = scr
        self.vals = None

    def refresh(self):
        m = self.scr.model
        mt = m.meters
        vals = (round((mt.get("dsp_load") or 0) * 100), mt.get("xruns", 0),
                None if cpu_temp() is None else round(cpu_temp(), 1), m.connected)
        if vals != self.vals:
            self.vals = vals
            self.invalidate()

    def draw(self, cv):
        x, y, w, h = self.rect
        cv.rrect(x, y, w, h, 12, fill=CARD, stroke=EDGE, lw=1.5)
        if not self.vals:
            return
        dsp, xr, temp, conn = self.vals
        cols = [("DSP LOAD", f"{dsp}%", art.GREEN if dsp < 60 else (art.YELLOW if dsp < 85 else art.RED)),
                ("XRUNS", str(xr), TXT if xr == 0 else art.YELLOW),
                ("CPU", "—" if temp is None else f"{temp:.0f}°C", TXT if (temp or 0) < 70 else art.YELLOW)]
        for i, (lab, val, c) in enumerate(cols):
            cx = x + 16 + i * 98
            cv.text(lab, cx, y + 24, f_label(12, 750), TXT3, tracking=1)
            cv.text(val, cx, y + 50, f_num(20, 700), c)
        cv.circle(x + 20, y + 68, 4, fill=art.GREEN if conn else art.RED)
        cv.text("Engine connected" if conn else "Engine offline · reconnecting", x + 30, y + 73, f_label(12, 600), TXT2)


class Version(Widget):
    def __init__(self, scr):
        super().__init__(10, 410, 300, 20)
        self.scr = scr

    def draw(self, cv):
        cv.text(f"bassbox ui {UI_VERSION}   ·   {len(self.scr.model.catalog)} effects", 160, 424, f_label(12, 500), TXT3,
                align="center")


class Settings(BaseScreen):
    name = "settings"

    def __init__(self):
        super().__init__()
        self.r_in = GainRow(self, V.GAIN_IN, 82)
        self.r_out = GainRow(self, V.GAIN_OUT, 148)
        self.stats = Stats(self)
        self.b_cal = Button(10, 308, 145, 46, "Recalibrate", lambda: self.nav.calibrate(), "outline", TXT,
                            font_=f_label(14, 700))
        self.b_restart = Button(165, 308, 145, 46, "Restart engine", self.ask_restart, "outline", art.YELLOW,
                                font_=f_label(14, 700))
        self.footer = FooterBar([("DONE", lambda cv, x, y, c: art.check(cv, x, y, 16, c), lambda: self.nav.board())])
        self.b_audio = Button(10, 362, 300, 44, "Audio check · meters, auto gain, test tone", lambda: self.nav.audio_check(),
                              "outline", art.GREEN, font_=f_label(14, 700))
        self.add(self.r_in, self.r_out, self.stats, self.b_cal, self.b_restart, self.b_audio, Version(self), self.footer)
        self.timer = None

    def configure_header(self):
        ctx.header.configure(title="Settings", kicker="BASSBOX", back=lambda: self.nav.board())

    def on_show(self):
        super().on_show()
        self._tick()

    def on_hide(self):
        core.app.cancel(self.timer)

    def _tick(self):
        self.stats.refresh()
        self.timer = core.app.after(1.0, self._tick)

    def on_model(self, what):
        super().on_model(what)
        if what == "master":
            self.r_in.invalidate()
            self.r_out.invalidate()

    def ask_restart(self):
        core.app.add_overlay(Dialog("Restart engine?", "Audio stops for a few seconds while the engine restarts.",
                                    "Restart", self.restart, danger=True))

    def restart(self):
        try:
            subprocess.Popen(["systemctl", "restart", "bassbox-engine"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            snackbar("Restarting engine…")
        except OSError as e:
            snackbar("Restart failed")
            core.app.log("restart:", e)


class Note(Widget):
    def __init__(self):
        super().__init__(10, 362, 300, 40)

    def draw(self, cv):
        cv.text("Drag a gain sideways · double-tap for 0 dB", 160, 378, f_label(12, 500), TXT3, align="center")
        cv.text("Long-press then drag for fine steps", 160, 394, f_label(12, 500), TXT3, align="center")


# ====================================================================== calibration
class Calibrate(Screen):
    """Full-screen 5-point affine calibration (same targets and fit as display/touchtest.py)."""
    name = "calibrate"
    M = 30

    def __init__(self, on_done):
        super().__init__()
        self.on_done = on_done
        self.targets = [(self.M, self.M), (320 - self.M, self.M), (320 - self.M, 480 - self.M), (self.M, 480 - self.M), (160, 240)]
        self.i = 0
        self.raws = []
        self.result = None
        self.press = None

    def on_show(self):
        self.i, self.raws, self.result = 0, [], None
        core.app.raw_listener = self.on_raw

    def on_hide(self):
        core.app.raw_listener = None

    def on_raw(self, kind, raw, xy):
        if kind != "up":
            return
        if self.result is not None:
            self.finish()
            return
        if raw is None:      # injected touch: invert the current calibration to get a raw reading
            raw = ctx.touch_cal_inverse(*xy) if ctx.touch_cal_inverse else xy
        self.raws.append(tuple(raw))
        self.i += 1
        if self.i >= len(self.targets):
            self.result = ctx.apply_calibration(self.raws, self.targets)
        core.app.invalidate_all()

    def finish(self):
        core.app.raw_listener = None
        self.on_done()

    def draw(self, cv):
        cv.fill(0x000000)
        if self.result is not None:
            ok, resid = self.result
            cv.text("CALIBRATED" if ok else "CALIBRATION FAILED", 160, 210, f_title(18), art.GREEN if ok else art.RED, align="center")
            if resid:
                cv.text("error per point: " + ", ".join(f"{r:.0f}" for r in resid) + " px", 160, 244, f_label(13, 500), TXT2, align="center")
            cv.text("Tap anywhere to finish", 160, 290, f_label(14, 600), TXT, align="center")
            return
        tx, ty = self.targets[self.i]
        cv.text(f"CALIBRATE {self.i + 1}/{len(self.targets)}", 160, 200 if self.i == 4 else 240, f_title(16), TXT, align="center")
        cv.text("Tap the centre of the crosshair", 160, 226 if self.i == 4 else 266, f_label(13, 500), TXT2, align="center")
        cv.line([(tx - 22, ty), (tx + 22, ty)], art.RED, 2)
        cv.line([(tx, ty - 22), (tx, ty + 22)], art.RED, 2)
        cv.circle(tx, ty, 9, stroke=0xFFFFFF, lw=2)
