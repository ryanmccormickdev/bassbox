"""Tuner: bass string row, full-width cents arc with a needle, huge note letter, green lock within ±3 ct,
Hz + cents readouts. Readable from standing height. The engine mutes the output while the tuner is on."""
import math

import art
import core
from art import GREEN, TXT, TXT2, TXT3
from chrome import FooterBar, f_label, f_num, f_title
from core import Widget
from gfx import font, mix, text_width
from screens.base import BaseScreen, ctx

CX, CY, R = 160, 352, 150
A0, A1 = 200, 340            # arc span (degrees, screen coords) for -50..+50 ct
LOCK = 3.0
STRINGS = [("B", 0, 30.87), ("E", 1, 41.20), ("A", 1, 55.00), ("D", 2, 73.42), ("G", 2, 98.00)]
AMBER = 0xFFB000


def ang(c):
    c = max(-50, min(50, c))
    return math.radians(A0 + (c + 50) / 100 * (A1 - A0))


def flat_sign(cv, x, y, h, c, lw=3):
    cv.line([(x, y - h), (x, y)], c, lw)
    cr = cv.cr
    cr.save()
    cr.move_to(x, y)
    cr.curve_to(x + h * 0.55, y - h * 0.2, x + h * 0.5, y - h * 0.62, x, y - h * 0.42)
    cv.src(c)
    cr.set_line_width(lw)
    cr.stroke()
    cr.restore()


def sharp_sign(cv, x, y, h, c, lw=3):
    w = h * 0.62
    for dx in (w * 0.3, w * 0.7):
        cv.line([(x + dx + h * 0.05, y - h), (x + dx - h * 0.05, y)], c, lw)
    for dy in (h * 0.33, h * 0.67):
        cv.line([(x, y - dy + h * 0.06), (x + w, y - dy - h * 0.06)], c, lw)


class Gauge(Widget):
    """Cents arc, ticks, ♭/♯ glyphs (cached; re-rendered only when the lock state flips)."""
    cache = True

    def __init__(self, scr):
        super().__init__(0, 186, 320, 160)
        self.scr = scr

    def draw(self, cv):
        locked = self.scr.locked
        if locked:
            cv.arc(CX, CY, R, A0, A1, GREEN, 26, cap=0, alpha=0.15)
        cv.arc(CX, CY, R, A0, A1, 0x30343C, 12, cap=0)
        a, b = math.degrees(ang(-LOCK)), math.degrees(ang(LOCK))
        cv.arc(CX, CY, R, a, b, GREEN, 16 if locked else 12, cap=0)
        for c in range(-50, 51, 10):
            t = ang(c)
            big = c % 50 == 0
            r0, r1 = R + 10, R + (24 if big else 17)
            cv.line([(CX + r0 * math.cos(t), CY + r0 * math.sin(t)), (CX + r1 * math.cos(t), CY + r1 * math.sin(t))],
                    GREEN if c == 0 else TXT2, 3 if big else 2)
        flat_sign(cv, 20, 334, 26, TXT2)
        sharp_sign(cv, 288, 334, 26, TXT2)


class Needle(Widget):
    def __init__(self, scr):
        super().__init__(0, 180, 320, 130)
        self.scr = scr
        self.cents = None

    @staticmethod
    def box(c):
        t = ang(c)
        x0, y0 = CX + 120 * math.cos(t), CY + 120 * math.sin(t)
        x1, y1 = CX + 168 * math.cos(t), CY + 168 * math.sin(t)
        return (min(x0, x1) - 10, min(y0, y1) - 10, abs(x1 - x0) + 20, abs(y1 - y0) + 20)

    def set(self, c):
        c = None if c is None else round(c * 2) / 2
        if c != self.cents:
            if self.cents is not None:
                core.app.dirty(self.box(self.cents))
            self.cents = c
            if c is not None:
                core.app.dirty(self.box(c))

    def draw(self, cv):
        if self.cents is None:
            return
        t = ang(self.cents)
        col = GREEN if abs(self.cents) <= LOCK else 0xFFFFFF
        p0 = (CX + 122 * math.cos(t), CY + 122 * math.sin(t))
        p1 = (CX + 164 * math.cos(t), CY + 164 * math.sin(t))
        cv.line([p0, p1], 0x000000, 10)
        cv.line([p0, p1], col, 6)
        cv.circle(*p1, 5, fill=col)


class StringRow(Widget):
    def __init__(self, scr):
        super().__init__(10, 84, 300, 36)
        self.scr = scr
        self.near = None

    def set(self, idx):
        if idx != self.near:
            self.near = idx
            self.invalidate()

    def draw(self, cv):
        w = 54
        for i, (n, o, hz) in enumerate(STRINGS):
            x = self.x + 3 + i * (w + 6)
            sel = i == self.near
            col = GREEN if sel and self.scr.locked else (0xFFFFFF if sel else 0x2C3038)
            cv.rrect(x, self.y, w, 34, 10, fill=col if sel else None, stroke=col if not sel else None, lw=2)
            tc = 0x0A0A0C if sel else TXT2
            cv.text(n, x + w / 2 - 4, self.y + 25, f_title(16), tc, align="center")
            cv.text(str(o), x + w / 2 + 12, self.y + 27, f_num(12, 700), tc, align="center")


class NoteView(Widget):
    def __init__(self, scr):
        super().__init__(30, 228, 260, 158)
        self.scr = scr
        self.key = None

    def set(self, key):
        if key != self.key:
            self.key = key
            self.invalidate()

    def draw(self, cv):
        t = self.scr.reading
        if not t:
            cv.text("—", 160, 330, font("archivo", 120, 900, 125), 0x3A3E46, align="center")
            cv.text("PLAY A STRING", 160, 372, f_title(15), TXT3, align="center")
            return
        locked = self.scr.locked
        col = GREEN if locked else TXT
        letter, acc = t["note"][0], t["note"][1:]
        f = font("archivo", 124, 900, 125)
        w = text_width(letter, f)
        x = 160 - w / 2 - (14 if acc else 6)
        cv.text(letter, x, 330, f, col)
        if acc:
            sharp_sign(cv, x + w + 4, 278, 36, col, 5)
        cv.text(str(t.get("octave", "")), x + w + 6, 330, f_num(34, 800), TXT2)
        c = t.get("cents", 0)
        if locked:
            cv.rrect(98, 344, 124, 36, 18, fill=GREEN)
            cv.text("IN TUNE", 160, 368, f_title(15), 0x0A0A0C, align="center")
        else:
            up = c < 0
            cv.text("TUNE UP" if up else "TUNE DOWN", 150, 370, f_title(15), AMBER, align="center")
            ax = 150 + text_width("TUNE DOWN", f_title(15)) / 2 + 16
            cr = cv.cr
            d = -1 if up else 1
            cr.move_to(ax - 8, 364 - d * 5)
            cr.line_to(ax + 8, 364 - d * 5)
            cr.line_to(ax, 364 + d * 6)
            cr.close_path()
            cv.src(AMBER)
            cr.fill()


class Readout(Widget):
    def __init__(self, scr):
        super().__init__(0, 392, 320, 36)
        self.scr = scr
        self.key = None

    def set(self, key):
        if key != self.key:
            self.key = key
            self.invalidate()

    def draw(self, cv):
        t = self.scr.reading
        if not t:
            cv.text("Chromatic · A = 440 Hz", 160, 416, f_label(13, 500), TXT3, align="center")
            return
        c = t.get("cents", 0)
        cv.text(f"{c:+.0f}".replace("-", "−") + " ct", 92, 418, f_num(22, 700), GREEN if self.scr.locked else TXT,
                align="center")
        cv.text(f"{t.get('hz', 0):.2f} Hz", 226, 418, f_num(22, 700), TXT, align="center")


class Tuner(BaseScreen):
    name = "tuner"

    def __init__(self):
        super().__init__()
        self.reading = None
        self.locked = False
        self.strings = StringRow(self)
        self.gauge = Gauge(self)
        self.needle = Needle(self)
        self.note = NoteView(self)
        self.readout = Readout(self)
        self.footer = FooterBar([("DONE", lambda cv, x, y, c: art.check(cv, x, y, 16, c), lambda: self.nav.board())])
        self.add(self.strings, self.gauge, self.needle, self.note, self.readout, self.footer)

    def configure_header(self):
        ctx.header.configure(title="Tuner", kicker="OUTPUT MUTED", kicker_color=AMBER, back=lambda: self.nav.board())

    def on_show(self):
        self.model.set_tuner(True)
        self.reading = None
        super().on_show()

    def on_hide(self):
        self.model.set_tuner(False)

    def on_model(self, what):
        super().on_model(what)
        if what != "tuner":
            return
        t = self.model.tuner
        ok = bool(t and t.get("note") and (t.get("confidence", 1) or 0) >= 0.5 and (t.get("hz") or 0) > 0)
        self.reading = t if ok else None
        locked = ok and abs(t.get("cents", 99)) <= LOCK
        if locked != self.locked:
            self.locked = locked
            self.gauge.recache()
            self.strings.invalidate()
        self.needle.set(t.get("cents") if ok else None)
        near = None
        if ok:
            near = min(range(len(STRINGS)), key=lambda i: abs(math.log2(t["hz"] / STRINGS[i][2])))
            if abs(1200 * math.log2(t["hz"] / STRINGS[near][2])) > 300:
                near = None
        self.strings.set(near)
        self.note.set((t["note"], t.get("octave"), locked, (t.get("cents", 0) < 0)) if ok else None)
        self.readout.set((round(t.get("cents", 0)), round(t.get("hz", 0), 2), locked) if ok else None)
