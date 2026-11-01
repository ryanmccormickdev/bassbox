"""Shared UI chrome: persistent header (jacks, meters, title), buttons, footer bar, snackbar, banner, dialog."""
import math
import time

import art
import core
from art import BG, DIM, EDGE, NEUTRAL, TXT, TXT2, TXT3
from core import Widget
from gfx import font, mix, text_width

HEADER_H = 72
LX, RX = 22, 298              # signal gutters (= jack centres)
NUT_Y = 62                    # top of the panel nut; the plug's cable runs up off the screen
SOCKET_BOT = NUT_Y + 38       # the signal line leaves the socket here
STUB_Y = SOCKET_BOT
FOOTER_Y = 434


def f_title(size=15):
    return font("archivo", size, 800, 125)


def f_label(size=12, weight=600):
    return font("inter", size, weight)


def f_num(size=14, weight=600):
    return font("mono", size, weight)


def ease_out_back(p, k=1.7):
    p -= 1
    return 1 + p * p * ((k + 1) * p + k)


def ease_in(p):
    return p * p


# ====================================================================== jacks
class JackWidget(Widget):
    """Animated TRS jack. Plug-in: slide (accelerating), snap with a glow ring in the downstream colour,
    cable tension settles. Unplug: pops out with overshoot, tilts, cable goes slack and grey."""
    overflow = 14
    cache = True
    PLUG_S, UNPLUG_S = 0.38, 0.42

    def __init__(self, side):
        cx = LX if side == "in" else RX
        super().__init__(cx - 22, 0, 44, SOCKET_BOT + 4)
        self.side, self.cx = side, cx
        self.plugged = True
        self.anim = None          # ("in"|"out", t0)
        self.color = NEUTRAL
        self.glow = 0.0
        self.gestures = ("tap",)
        self.on_tap_cb = None
        self.ring = JackRing(self)
        self.add(self.ring)
        self.compact = False
        self.nut_y = NUT_Y

    def set_compact(self, v):
        if v != self.compact:
            self.invalidate()
            self.compact = v
            self.nut_y = 50 if v else NUT_Y
            self.h = (self.nut_y + 12) if v else SOCKET_BOT + 4
            self.ring.y = self.nut_y - 6.5 - 20
            self.ring.ring_y = self.nut_y - 6.5
            self.recache()

    def set_plugged(self, v, animate=True):
        if v == self.plugged:
            return
        self.plugged = v
        if animate:
            self.anim = ("in" if v else "out", time.monotonic())
            core.app.animate(self)
        self.recache()
        self._notify_line()

    def _notify_line(self):
        sl = getattr(core.app.screen, "signal", None)
        if sl is not None:
            sl.rebuild() if hasattr(sl, "rebuild") else sl.invalidate()

    def settled(self):
        return self.anim is None

    def set_level(self, color, glow):
        g = round(min(1.0, glow) * 4) / 4
        if color != self.color:
            self.color, self.glow = color, g
            self.recache()
        elif g != self.glow:
            self.glow = g
            self.ring.invalidate()                 # only the small ring/glow overlay re-renders

    def tick(self, now):
        self.recache()
        if self.anim is None:
            return None
        kind, t0 = self.anim
        dur = self.PLUG_S if kind == "in" else self.UNPLUG_S
        if now - t0 >= dur:
            self.anim = None
            self._notify_line()
            return None
        return now + 1 / 30

    def pose(self, now=None):
        """-> lift, tilt, live, bow, pulse"""
        if self.anim is None:
            return art.jack_pose(None, 0, self.plugged)
        kind, t0 = self.anim
        dur = self.PLUG_S if kind == "in" else self.UNPLUG_S
        return art.jack_pose(kind, min(1.0, ((now or time.monotonic()) - t0) / dur))

    def draw(self, cv):
        lift, tilt, live, bow, pulse = self.pose()
        tilt = tilt if self.side == "in" else -tilt
        settled = self.anim is None and self.plugged
        if self.compact:
            lift *= 0.7
        art.jack(cv, self.cx, self.nut_y, lift=lift, tilt=tilt, glow=0 if settled else pulse, color=self.color,
                 live=live, bow=bow, socket=not self.compact)
        if pulse > 0:
            cv.glow(self.cx, self.nut_y + 4, 26, self.color, 0.7 * pulse)
            cv.circle(self.cx, self.nut_y + 4, 12 + 12 * (1 - pulse), stroke=self.color, lw=2, alpha=0.8 * pulse)

    def on_tap(self, g):
        if self.on_tap_cb:
            self.on_tap_cb(self.side)


class JackRing(Widget):
    """Live level glow on a seated plug's ring (kept out of the cached jack so it costs ~1k px per change)."""

    def __init__(self, jack):
        self.ring_y = NUT_Y - 6.5          # the plug barrel's coloured band when seated
        super().__init__(jack.cx - 20, self.ring_y - 20, 40, 40)
        self.j = jack

    def draw(self, cv):
        j = self.j
        if not (j.anim is None and j.plugged) or j.glow <= 0:
            return
        cv.glow(j.cx, self.ring_y, 20, j.color, 0.45 * j.glow)
        cv.rect(j.cx - 9, self.ring_y - 1.5, 18, 3, fill=mix(j.color, 0xFFFFFF, 0.35 * j.glow))


class MeterWidget(Widget):
    """Vertical 8-segment meter beside a jack, with a latched clip segment."""

    def __init__(self, side):
        x = 44 if side == "in" else 270
        super().__init__(x, 16, 6, 50)
        self.side = side
        self.n = 0
        self.clip = False
        self.live = True

    def set(self, level, clip, live=True):
        db = 20 * math.log10(max(1e-6, level))
        n = 0 if db < -60 else min(8, int((db + 60) / 60 * 8 + 0.5))
        if n == self.n - 1 and db > (self.n - 0.5) * 7.5 - 60 - 2.5:   # 2.5 dB hysteresis on the way down
            n = self.n
        if (n, bool(clip), live) != (self.n, self.clip, self.live):
            self.n, self.clip, self.live = n, bool(clip), live
            self.invalidate()

    def draw(self, cv):
        for i in range(8):
            y = self.y + self.h - (i + 1) * 6.25
            on = i < self.n or (i == 7 and self.clip)
            c = art.GREEN if i < 5 else (art.YELLOW if i < 7 else art.RED)
            if on:
                cv.rect(self.x, y, 6, 5, fill=c)
            else:   # faint outline: unlit segments stay visible on the panel
                cv.rect(self.x + 0.5, y + 0.5, 5, 4, stroke=mix(c, 0x000000, 0.55) if self.live else 0x3A3E46, lw=1)


class Header(Widget):
    """Persistent header: IN jack + meter (left), title area, OUT meter + jack (right)."""
    persistent = True

    def __init__(self):
        super().__init__(0, 0, 320, HEADER_H)
        self.jack_in = JackWidget("in")
        self.jack_out = JackWidget("out")
        self.m_in = MeterWidget("in")
        self.m_out = MeterWidget("out")
        self.title = TitleArea()
        self.add(self.title, self.m_in, self.m_out, self.jack_in, self.jack_out)

    def draw(self, cv):
        cv.text("IN", 44, 11, f_label(12, 700), TXT2, tracking=1)
        cv.text("OUT", 276, 11, f_label(12, 700), TXT2, align="right", tracking=1)

    def configure(self, **kw):
        self.title.configure(**kw)


class TitleArea(Widget):
    """Kicker + title in the header centre, optional back chevron and ‹ › steppers."""
    cache = True
    contain = True

    def __init__(self):
        super().__init__(56, 0, 208, HEADER_H)
        self.kicker = ""
        self.title = ""
        self.title_color = TXT
        self.kicker_color = TXT3
        self.dot = None
        self.back = None
        self.on_title = None
        self.steppers = None      # (on_prev, on_next, prev_ok, next_ok)
        self.children_built = False
        self.b_back = IconButton(56, 6, 44, 60, "back", lambda: self.back and self.back())
        self.b_prev = IconButton(100, 0, 44, 42, "prev", lambda: self.steppers and self.steppers[0]())
        self.b_next = IconButton(192, 0, 44, 42, "next", lambda: self.steppers and self.steppers[1]())
        self.b_title = Tappable(104, 26, 112, 44, lambda: self.on_title and self.on_title())
        self.add(self.b_title, self.b_back, self.b_prev, self.b_next)
        self.configure()

    def configure(self, title="", kicker="", back=None, on_title=None, steppers=None, title_color=TXT,
                  kicker_color=TXT3, dot=None):
        look = (title, kicker, back is not None, on_title is not None, steppers and steppers[2:], title_color,
                kicker_color, dot)
        same = look == getattr(self, "_look", None)
        self._look = look
        self.title, self.kicker, self.back, self.on_title = title, kicker, back, on_title
        self.steppers, self.title_color, self.kicker_color, self.dot = steppers, title_color, kicker_color, dot
        self.b_back.visible = back is not None
        self.b_prev.visible = self.b_next.visible = steppers is not None
        if steppers:
            self.b_prev.enabled, self.b_next.enabled = steppers[2], steppers[3]
            tw = text_width(kicker, f_label(12, 650), tracking=0.6)
            self.b_prev.x = round(160 - tw / 2 - 16 - 22)
            self.b_next.x = round(160 + tw / 2 + 16 - 22)
        self.b_title.visible = on_title is not None
        if not same:
            self.recache()

    def draw(self, cv):
        cx = 160 if (self.steppers or not self.back) else 166
        maxw = 150 if self.back else 186
        if self.kicker:
            cv.text(self.kicker, cx, 27, f_label(12, 650), self.kicker_color, align="center", tracking=1.2 if not self.steppers else 0.6)
        if self.title:
            size = 15
            t = self.title.upper()
            while size > 11 and text_width(t, f_title(size)) > maxw:
                size -= 1
            w = cv.text(t, cx, 55, f_title(size), self.title_color, align="center")
            if self.dot is not None:
                cv.circle(cx + w / 2 + 7, 49, 3.5, fill=self.dot)


class Tappable(Widget):
    gestures = ("tap",)

    def __init__(self, x, y, w, h, cb):
        super().__init__(x, y, w, h)
        self.cb = cb

    def on_tap(self, g):
        self.cb()


class IconButton(Widget):
    gestures = ("tap", "press")

    def __init__(self, x, y, w, h, icon, cb, color=TXT):
        super().__init__(x, y, w, h)
        self.icon, self.cb, self.color = icon, cb, color
        self.pressed = False

    def on_press(self, g):
        self.pressed = True
        self.invalidate()

    def on_release(self, g):
        self.pressed = False
        self.invalidate()

    def on_tap(self, g):
        if self.enabled:
            self.cb()

    def draw(self, cv):
        cx, cy = self.x + self.w / 2, self.y + self.h / 2
        c = self.color if self.enabled else 0x3A3E46
        if self.pressed and self.enabled:
            cv.circle(cx, cy, 19, fill=0xFFFFFF, alpha=0.12)
        if self.icon == "back":
            art.chevron(cv, cx - 2, cy + 6, 16, c, "left", 2.8)
        elif self.icon in ("prev", "next"):
            d = -1 if self.icon == "prev" else 1
            tx = cx
            cr = cv.cr
            cr.move_to(tx + d * 7, cy + 1)
            cr.line_to(tx - d * 5, cy - 7)
            cr.line_to(tx - d * 5, cy + 9)
            cr.close_path()
            cv.src(c)
            cr.fill()
        elif self.icon == "up":
            art.chevron(cv, cx, cy, 14, c, "up", 2.6)
        elif self.icon == "down":
            art.chevron(cv, cx, cy, 14, c, "down", 2.6)


# ====================================================================== buttons
class Button(Widget):
    """Rounded button. style: 'ghost' | 'solid' | 'outline' | 'footer'."""
    gestures = ("tap", "press")

    def __init__(self, x, y, w, h, label, cb, style="outline", color=TXT, icon=None, lit=False, lit_color=None,
                 font_=None):
        super().__init__(x, y, w, h)
        self.label, self.cb, self.style, self.color, self.icon = label, cb, style, color, icon
        self.lit, self.lit_color = lit, lit_color or art.RED
        self.pressed = False
        self.font = font_

    def set_lit(self, v, label=None):
        if v != self.lit or (label and label != self.label):
            self.lit = v
            if label:
                self.label = label
            self.invalidate()

    def set_label(self, label):
        if label != self.label:
            self.label = label
            self.invalidate()

    def on_press(self, g):
        self.pressed = True
        self.invalidate()

    def on_release(self, g):
        self.pressed = False
        self.invalidate()

    def on_tap(self, g):
        if self.enabled:
            self.cb()

    def draw(self, cv):
        x, y, w, h = self.rect
        en = self.enabled
        c = self.color if en else 0x4A4F59
        if self.style == "footer":
            if self.lit:
                cv.rrect(x + 4, y + 4, w - 8, h - 8, 10, fill=self.lit_color)
                c = art.ink_on(self.lit_color)
            elif self.pressed:
                cv.rrect(x + 4, y + 4, w - 8, h - 8, 10, fill=0xFFFFFF, alpha=0.12)
            cx = x + w / 2
            if self.icon:
                self.icon(cv, cx, y + 17, c)
                cv.text(self.label, cx, y + 39, self.font or f_label(12, 650), c, align="center", tracking=0.8)
            else:
                cv.text(self.label, cx, y + h / 2 + 5, self.font or f_label(13, 700), c, align="center", tracking=0.8)
            return
        r = min(12, h / 2)
        if self.style == "solid":
            cv.rrect(x, y, w, h, r, fill=mix(c, 0x000000, 0.25) if self.pressed else c)
            tc = art.ink_on(c)
        else:
            fill = 0x262A31 if self.pressed else (0x16181D if self.style == "outline" else None)
            cv.rrect(x, y, w, h, r, fill=fill, stroke=EDGE if en else 0x2A2D33, lw=1.5)
            tc = c
        if self.icon:
            tw = text_width(self.label, self.font or f_label(14, 650))
            ix = x + w / 2 - tw / 2 - 12
            self.icon(cv, ix, y + h / 2, tc)
            cv.text(self.label, ix + 14, y + h / 2 + 5, self.font or f_label(14, 650), tc)
        else:
            cv.text(self.label, x + w / 2, y + h / 2 + 5, self.font or f_label(14, 650), tc, align="center")


class FooterBar(Widget):
    contain = True

    def __init__(self, items):
        """items: list of (label, icon_fn or None, cb, color)."""
        super().__init__(0, FOOTER_Y, 320, 480 - FOOTER_Y)
        n = len(items)
        w = 320 / n
        self.buttons = []
        for i, it in enumerate(items):
            label, icon, cb = it[:3]
            color = it[3] if len(it) > 3 else TXT
            b = Button(round(i * w), FOOTER_Y, round(w), 480 - FOOTER_Y, label, cb, "footer", color, icon)
            self.buttons.append(b)
            self.add(b)

    def draw(self, cv):
        cv.rect(0, self.y, 320, self.h, fill=0x0C0D10)
        cv.rect(0, self.y, 320, 1, fill=0x30343C)


# ====================================================================== overlays
class Snackbar(Widget):
    """Bottom message with an optional action (Undo). Auto-hides."""
    gestures = ("tap",)

    def __init__(self, text, action=None, cb=None, secs=4.0):
        super().__init__(10, FOOTER_Y - 52, 300, 44)
        self.text, self.action, self.cb = text, action, cb
        self.timer = core.app.after(secs, self.close)

    def close(self):
        core.app.cancel(self.timer)
        core.app.remove_overlay(self)

    def on_tap(self, g):
        if self.action and g.x > self.x + self.w - 100 and self.cb:
            self.cb()
        self.close()

    def draw(self, cv):
        cv.rrect(self.x, self.y, self.w, self.h, 12, fill=0xE8ECF1)
        cv.text(self.text, self.x + 16, self.y + 28, f_label(14, 600), 0x0A0A0C)
        if self.action:
            cv.text(self.action, self.x + self.w - 18, self.y + 28, f_title(13), 0xD15A00, align="right")


def snackbar(text, action=None, cb=None, secs=4.0):
    for o in list(core.app.overlays):
        if isinstance(o, Snackbar):
            o.close()
    s = Snackbar(text, action, cb, secs)
    core.app.add_overlay(s)
    return s


class Banner(Widget):
    """'Engine offline · reconnecting…' strip under the header."""

    def __init__(self, text):
        super().__init__(0, HEADER_H + 2, 320, 30)
        self.text = text

    def draw(self, cv):
        cv.rect(self.x, self.y, self.w, self.h, fill=0x3A2A00)
        cv.rect(self.x, self.y + self.h - 2, self.w, 2, fill=art.YELLOW)
        cv.circle(18, self.y + 15, 4, fill=art.YELLOW)
        cv.text(self.text, 30, self.y + 20, f_label(13, 700), art.YELLOW, tracking=0.6)


class Dialog(Widget):
    """Modal confirm card."""
    modal = True

    def __init__(self, title, body, ok_label, on_ok, danger=False):
        super().__init__(0, 0, 320, 480)
        self.title, self.body = title, body
        x, y, w = 24, 150, 272
        self.card = (x, y, w, 170)
        col = art.RED if danger else art.TXT
        self.add(Button(x + 16, y + 110, 112, 44, "Cancel", self.close, "outline"),
                 Button(x + w - 128, y + 110, 112, 44, ok_label, lambda: (self.close(), on_ok()), "solid", col))

    def close(self):
        core.app.remove_overlay(self)

    def draw(self, cv):
        cv.rect(0, 0, 320, 480, fill=0x000000, alpha=0.7)
        x, y, w, h = self.card
        cv.rrect(x, y, w, h, 16, fill=0x1C1F25, stroke=EDGE, lw=1.5)
        cv.text(self.title.upper(), x + 18, y + 36, f_title(14), TXT)
        for i, ln in enumerate(wrap(self.body, f_label(14, 450), w - 36, 3)):
            cv.text(ln, x + 18, y + 62 + i * 19, f_label(14, 450), TXT2)


def wrap(s, f, maxw, maxlines=2):
    words, lines, cur = s.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if text_width(t, f) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        while lines[-1] and text_width(lines[-1] + "…", f) > maxw:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip(" ,.;") + "…"
    return lines


def fit(s, f, maxw):
    if text_width(s, f) <= maxw:
        return s
    while s and text_width(s + "…", f) > maxw:
        s = s[:-1]
    return s + "…"


def shrink(s, family, sizes, weight, width, maxw):
    """Largest size in `sizes` that fits maxw (then ellipsis at the smallest). -> (font, text)"""
    for sz in sizes:
        f = font(family, sz, weight, width)
        if text_width(s, f) <= maxw:
            return f, s
    return f, fit(s, f, maxw)


def wrap_long(s, f, maxw, maxlines=2):
    """Like wrap(), but also breaks after - . / and hard-breaks tokens wider than maxw (model names)."""
    import re
    toks = [t for t in re.split(r"(?<=[\s\-./])", s) if t]
    out = []
    for t in toks:
        while text_width(t.strip(), f) > maxw and len(t) > 1:
            k = len(t)
            while k > 1 and text_width(t[:k], f) > maxw:
                k -= 1
            out.append(t[:k])
            t = t[k:]
        out.append(t)
    lines, cur = [], ""
    for t in out:
        if text_width((cur + t).strip(), f) <= maxw or not cur:
            cur += t
        else:
            lines.append(cur.strip())
            cur = t
    if cur.strip():
        lines.append(cur.strip())
    if len(lines) > maxlines:
        lines = lines[:maxlines]
        lines[-1] = fit(lines[-1] + "…", f, maxw) if text_width(lines[-1] + "…", f) > maxw else lines[-1] + "…"
    return lines
