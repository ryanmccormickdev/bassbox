"""Splash: bassbox wordmark + a small animated cable (a plug sliding into a jack, light running along the
cable) while the engine connects. Leaves as soon as the engine state arrives (min 0.6 s), or after 3 s
with the 'reconnecting' banner."""
import math
import time

import art
import core
from art import NEUTRAL, TXT2, TXT3, accent
from chrome import f_label, f_title
from core import Screen, Widget
from gfx import font, mix, text_width


class Cable(Widget):
    ambient = True
    def __init__(self):
        super().__init__(40, 262, 240, 60)
        self.t0 = time.monotonic()
        self.cols = [accent(c) for c in ("drive", "time", "filter", "modulation", "dynamics")]

    def tick(self, now):
        self.invalidate((self.x - 12, self.y + 14, self.w + 24, 34))
        return now + 1 / 20

    def draw(self, cv):
        x0, x1, y = self.x, self.x + self.w, self.y + 30
        cv.line([(x0, y), (x1, y)], 0x2A2E35, 4)
        t = (time.monotonic() - self.t0) * 0.9
        for k in range(3):
            p = (t + k / 3) % 1.0
            cx = x0 + p * self.w
            c = self.cols[int(t + k / 3) % len(self.cols)]
            seg = [(max(x0, cx - 26), y), (cx, y)]
            cv.line(seg, c, 4)
            cv.glow(cx, y, 12, c, 0.6)
            cv.circle(cx, y, 3, fill=mix(c, 0xFFFFFF, 0.7))


class Splash(Screen):
    name = "splash"

    def __init__(self):
        super().__init__()
        self.cable = Cable()
        self.add(self.cable)
        self.status = "connecting to engine…"

    def on_show(self):
        core.app.animate(self.cable)

    def set_status(self, s):
        if s != self.status:
            self.status = s
            core.app.dirty((0, 340, 320, 30))

    def draw(self, cv):
        f = font("archivo", 40, 900, 125)
        w = text_width("bassbox", f)
        cv.text("bassbox", 160, 236, f, 0xFFFFFF, align="center")
        cv.rect(160 - w / 2, 248, 26, 5, fill=accent("drive"))
        cv.rect(160 - w / 2 + 30, 248, 26, 5, fill=accent("time"))
        cv.rect(160 - w / 2 + 60, 248, 26, 5, fill=accent("filter"))
        cv.text(self.status, 160, 356, f_label(13, 500), TXT2, align="center")
        art.jack(cv, 22, 62, color=NEUTRAL)
        art.jack(cv, 298, 62, color=NEUTRAL)
