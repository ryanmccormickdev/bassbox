"""Edit: the pedal's full face (the board card's mini stompbox, zoomed in).

Hero knobs on top, then up to 4 small knobs per row (2 rows max). Enum params are selector knobs (tap =
next, drag = step), bools are toggles. The LCD strip shows the touched value large. The footswitch toggles
the pedal; the bottom strip is Mix. Vertical relative drag anywhere in a control's cell: 200 px = full
range; long-press first for fine (x1/4); double-tap resets to default.
"""
import math
import time

import art
import core
from art import INK, accent, body_color, ink_on
from chrome import FOOTER_Y, FooterBar, f_label, f_num, f_title, fit, shrink, snackbar
from core import Widget
from gfx import font, mix, text_width
from screens.base import BaseScreen, ctx
import values as V

FACE = (10, 78, 300, 350)          # x, y, w, h  (bottom at 428)
FULL_RANGE_PX = 200
FINE = 0.25
ENUM_STEP_PX = 32


class Face(Widget):
    cache = True
    gestures = ("drag",)

    def __init__(self, screen):
        super().__init__(*FACE)
        self.s = screen

    def on_drag_end(self, g):
        if g.dx > 70 and abs(g.dy) < 60:      # swipe right on the face background = back
            self.s.nav.board()

    def draw(self, cv):
        x, y, w, h = self.rect
        c = body_color(self.s.color, self.s.on)
        cv.rrect(x + 2, y + 4, w, h, 18, fill=0x000000, alpha=0.7)
        cv.rrect(x, y, w, h, 18, fill=cv.lin_grad(x, y, x, y + h, [(0, mix(c, 0xFFFFFF, 0.14)), (1, mix(c, 0x000000, 0.2))]))
        cv.rrect(x + 0.75, y + 0.75, w - 1.5, h - 1.5, 17.5, stroke=0xFFFFFF, lw=1.2, alpha=0.25)
        # screws
        for sx, sy in ((x + 12, y + 12), (x + w - 12, y + 12), (x + 12, y + h - 12), (x + w - 12, y + h - 12)):
            cv.circle(sx, sy, 3.2, fill=mix(c, 0x000000, 0.35))
            cv.line([(sx - 2, sy - 2), (sx + 2, sy + 2)], mix(c, 0xFFFFFF, 0.3), 1)


class LCD(Widget):
    def __init__(self, screen):
        super().__init__(28, 90, 184, 34)
        self.s = screen
        self.label, self.value, self.fine = "", "", False

    def show(self, label, value, fine=False):
        if (label, value, fine) != (self.label, self.value, self.fine):
            only_value = (label, fine) == (self.label, self.fine) and self.label
            self.label, self.value, self.fine = label, value, fine
            if only_value:
                core.app.dirty((self.x + self.w - 96, self.y + 2, 94, self.h - 4))
            else:
                self.invalidate()

    def draw(self, cv):
        x, y, w, h = self.rect
        cv.rrect(x - 2, y - 2, w + 4, h + 4, 8, fill=0x000000, alpha=0.55)
        cv.rrect(x, y, w, h, 6, fill=0x07080A)
        ac = self.s.color
        if not self.label:
            cv.text(self.s.info["name"].upper(), x + 10, y + 22, f_num(13, 700), mix(ac, 0xFFFFFF, 0.2))
            cv.text("ON" if self.s.on else "OFF", x + w - 10, y + 22, f_num(13, 700), 0x8A909A, align="right")
            return
        cv.text(fit(self.label.upper(), f_num(12, 700), 80), x + 10, y + 22, f_num(12, 700), 0x9AA2AE)
        if self.fine:
            cv.rrect(x + 84, y + 9, 34, 16, 4, fill=ac)
            cv.text("FINE", x + 101, y + 21, f_label(12, 800), ink_on(ac), align="center")
        cv.text(self.value, x + w - 10, y + 25, f_num(20, 800), mix(ac, 0xFFFFFF, 0.25), align="right")


class Footswitch(Widget):
    gestures = ("tap", "press")
    pad = 4

    def __init__(self, screen):
        super().__init__(222, 84, 84, 48)
        self.s = screen
        self.pressed = False

    def on_press(self, g):
        self.pressed = True
        self.invalidate()

    def on_release(self, g):
        self.pressed = False
        self.invalidate()

    def on_tap(self, g):
        self.s.toggle()

    def draw(self, cv):
        art.led(cv, self.x + 14, self.y + 24, self.s.on, r=4.5)
        art.footswitch(cv, self.x + 52, self.y + 24, 17, self.pressed)


class Control(Widget):
    """A param control in a cell: knob / selector / toggle. Handles drag, double-tap, tap."""
    gestures = ("tap", "double", "long", "drag", "press")

    def __init__(self, screen, p, x, y, w, h, r, kind, dots=True):
        super().__init__(x, y, w, h)
        self.s, self.p, self.r, self.kind, self.dots = screen, p, r, kind, dots
        self.cx = x + w / 2
        self.ring = r + (9 if dots else 2)
        block = 13 + 3 + 2 * self.ring + 3 + 13
        top = y + (h - block) / 2
        self.lab_y = top + 11
        self.cy = top + 16 + self.ring
        self.val_y = self.cy + self.ring + 14
        self.active = False
        self.segs = []
        self.picker = V.needs_picker(p)
        if self.picker:
            self.btn = (x + 6, self.lab_y + 8, w - 12, min(78, y + h - self.lab_y - 14))
        elif V.is_enum(p):
            opts = p.get("options") or [str(i) for i in range(int(p["max"] - p["min"]) + 1)]
            n = max(1, len(opts))
            top_, bot_ = self.lab_y + 8, y + h - 6
            wide = (w - 10) / n >= 42 and all(text_width(o.upper(), f_label(12, 800)) < (w - 10) / n - 6 for o in opts)
            if wide:
                sh = min(40, bot_ - top_)
                sy = top_ + (bot_ - top_ - sh) / 2
                sw = (w - 10) / n
                self.segs = [(x + 5 + i * sw, sy, sw, sh) for i in range(n)]
            else:
                sh = min(28, (bot_ - top_) / n)
                sy = top_ + (bot_ - top_ - sh * n) / 2
                self.segs = [(x + 6, sy + i * sh, w - 12, sh) for i in range(n)]
            self.opts = opts
        self.pedal = (p.get("_eid"), p.get("id")) == ("wah", "pos")
        if self.pedal:
            self.track = (x + w / 2 - 34, self.lab_y + 10, 68, (y + h - 22) - (self.lab_y + 10))
        self.t0 = 0.0
        self.fine = False

    # value plumbing
    def value(self):
        return self.s.get(self.p)

    def set(self, v):
        v = V.snap(self.p, v)
        if v != self.value():
            self.s.set(self.p, v)
            self.inval_value()
        self.s.lcd.show(self.p["name"], V.text(self.p, v), self.fine)

    def inval_value(self, ring=False):
        if self.segs or self.pedal:
            return self.invalidate()
        R = (self.ring + 6) if ring else (self.r + 5)     # per-move: knob body only (pointer), not the scale
        core.app.dirty((self.cx - R, self.cy - R, 2 * R, 2 * R))
        core.app.dirty(self.value_box())

    def value_box(self):
        return (self.x + 2, self.val_y - 13, self.w - 4, 17)

    # gestures
    def on_press(self, g):
        self.s.lcd.show(self.p["name"], V.text(self.p, self.value()))
        import controls
        controls.focus = (self.s.uid, self.p)

    def on_tap(self, g):
        if self.picker:
            core.app.add_overlay(Picker(self))
            return
        if V.is_bool(self.p):
            self.set(0.0 if self.value() >= 0.5 else 1.0)
        elif V.is_enum(self.p):
            for i, (sx, sy, sw, sh) in enumerate(self.segs):
                if sx <= g.x < sx + sw and sy - 4 <= g.y < sy + sh + 4:
                    self.set(self.p["min"] + i)
                    self.invalidate()
                    return
            n = int(self.p["max"] - self.p["min"]) + 1
            self.set(self.p["min"] + (int(self.value() - self.p["min"]) + 1) % n)
            self.invalidate()

    def on_double_tap(self, g):
        self.set(V.default(self.p))

    def on_long_press(self, g):
        self.fine = True
        self.s.lcd.show(self.p["name"], V.text(self.p, self.value()), True)

    def on_long_release(self, g):
        self.fine = False
        self.s.lcd.show(self.p["name"], V.text(self.p, self.value()), False)

    def on_drag_start(self, g):
        if self.picker:
            return
        self.active = True
        self.fine = g.long
        core.app.dragging_value = True
        self.t0 = V.norm(self.p, self.value())
        self.v0 = self.value()
        self.inval_value(ring=True)

    def on_drag(self, g):
        if self.picker:
            return
        if V.is_discrete(self.p):
            horiz = len(self.segs) > 1 and self.segs[0][1] == self.segs[-1][1]
            if horiz:
                steps = int(g.dx / ENUM_STEP_PX)            # segmented row: drag sideways
            elif self.segs:
                steps = int(g.dy / ENUM_STEP_PX)            # stacked list: drag down = next option
            else:
                steps = int(-g.dy / ENUM_STEP_PX)
            self.set(V.clamp(self.p, self.v0 + steps))
            self.invalidate()
        elif self.pedal:
            self.set(V.denorm(self.p, self.t0 - g.dy / self.track[3]))
        else:
            k = FINE if self.fine else 1.0
            self.set(V.denorm(self.p, self.t0 - g.dy / FULL_RANGE_PX * k))

    def on_drag_end(self, g):
        self.active = False
        self.fine = False
        core.app.dragging_value = False
        self.inval_value(ring=True)
        self.s.lcd.show(self.p["name"], V.text(self.p, self.value()), False)

    def draw(self, cv):
        ink = self.s.ink
        p, v = self.p, self.value()
        cx, cy, r = self.cx, self.cy, self.r
        lab_f = f_title(13) if self.kind == "hero" else font("archivo", 13, 800, 75)
        cv.text(fit(p["name"].upper(), lab_f, self.w - 4), cx, self.lab_y, lab_f, ink, align="center")
        if self.picker:
            bx, by, bw, bh = self.btn
            main, var = V.clean_option(V.text(p, v))
            cv.rrect(bx, by, bw, bh, 10, fill=0x0A0A0C, stroke=0x000000, lw=1.5)
            lc = mix(self.s.color, 0xFFFFFF, 0.55)
            from chrome import wrap_long
            f = f_label(13, 700) if bw > 100 else f_label(12, 700)
            lines = wrap_long(main, f, bw - 28, 2 if var else 3)
            ty = by + bh / 2 - (len(lines) - 1) * 8 + (0 if not var else -7) + 5
            for i, ln in enumerate(lines):
                cv.text(ln, bx + 10, ty + i * 16, f, lc)
            if var:
                cv.text(fit(var.upper(), f_label(12, 700), bw - 30), bx + 10, ty + len(lines) * 16, f_label(12, 700),
                        mix(self.s.color, 0x000000, 0.0))
            art.chevron(cv, bx + bw - 12, by + bh / 2, 10, lc, "right", 2.2)
            return
        if self.segs:
            cur = int(round(v - p["min"]))
            for i, (sx, sy, sw, sh) in enumerate(self.segs):
                sel = i == cur
                cv.rrect(sx + 1, sy + 1, sw - 2, sh - 2, 6, fill=0x0A0A0C if sel else 0x000000,
                         alpha=1.0 if sel else 0.18, stroke=None)
                lf = shrink(self.opts[i].upper(), "archivo", (13, 12), 800, 75, sw - 4)[0]
                if text_width(self.opts[i].upper(), lf) > sw - 4:
                    lf = font("archivo", 12, 700, 75)
                cv.text(fit(self.opts[i].upper(), lf, sw - 2), sx + sw / 2, sy + sh / 2 + 5, lf,
                        mix(self.s.color, 0xFFFFFF, 0.45) if sel else ink, align="center")
            return
        if self.pedal:
            tx, ty, tw, th = self.track
            t = V.norm(p, v)
            cv.rrect(tx, ty, tw, th, 14, fill=0x000000, alpha=0.35)
            cv.text("TOE", tx + tw + 6, ty + 12, f_label(12, 800), ink)
            cv.text("HEEL", tx + tw + 6, ty + th - 2, f_label(12, 800), ink)
            py = ty + 6 + (th - 50) * (1 - t)
            cv.rrect(tx + 4, py + 3, tw - 8, 40, 9, fill=0x000000, alpha=0.5)
            cv.rrect(tx + 4, py, tw - 8, 40, 9, fill=cv.lin_grad(0, py, 0, py + 40, [(0, 0x3A3D44), (1, 0x15161A)]))
            for k in range(5):
                cv.rect(tx + 12, py + 7 + k * 6.5, tw - 24, 2.5, fill=0x5A5F68)
            cv.text(V.text(p, v), cx, self.y + self.h - 6, f_num(14, 700), ink, align="center")
            return
        if V.is_bool(p):
            art.toggle(cv, cx - 22, cy - 13, 44, 26, v >= 0.5, INK if ink == INK else 0xFFFFFF)
        else:
            if V.is_enum(p):
                n = int(p["max"] - p["min"]) + 1
                t = (v - p["min"]) / max(1, n - 1)
                art.knob(cv, cx, cy, r, t, ink=ink, positions=n, pointer=0xFFE7A8, scale=self.dots)
            else:
                art.knob(cv, cx, cy, r, V.norm(p, v), ink=ink, scale=self.dots)
            if self.active:      # static "touched" ring (drawn at drag start/end only)
                cv.circle(cx, cy, self.ring + 2, stroke=ink, lw=2.5, alpha=0.8)
        cv.text(V.text(p, v), cx, self.val_y, f_num(13 if self.kind != "hero" else 14, 700), ink, align="center")


class Picker(Widget):
    """Modal paged list for long / many-option enums (NAM models). Current option highlighted."""
    modal = True
    gestures = ("tap", "drag")
    ROW, TOP, PER = 58, 76, 6

    def __init__(self, ctl):
        super().__init__(0, 0, 320, 480)
        self.ctl = ctl
        self.opts = ctl.p.get("options") or []
        cur = int(round(ctl.value() - ctl.p["min"]))
        self.page = max(0, cur // self.PER)
        self.pages = max(1, (len(self.opts) + self.PER - 1) // self.PER)

    def close(self):
        core.app.remove_overlay(self)

    def on_drag(self, g):
        pass

    def on_drag_end(self, g):
        if abs(g.dy) > 40:
            self._page(1 if g.dy < 0 else -1)

    def _page(self, d):
        p = max(0, min(self.pages - 1, self.page + d))
        if p != self.page:
            self.page = p
            self.invalidate()

    def on_tap(self, g):
        if g.y < 66 and g.x > 240:
            return self.close()
        if g.y >= 434:
            if g.x < 107:
                return self._page(-1)
            if g.x >= 213:
                return self._page(+1)
            return self.close()
        i = int((g.y - self.TOP) // self.ROW)
        k = self.page * self.PER + i
        if 0 <= i < self.PER and k < len(self.opts) and g.y >= self.TOP:
            self.ctl.set(self.ctl.p["min"] + k)
            self.ctl.invalidate()
            self.close()

    def draw(self, cv):
        cv.rect(0, 0, 320, 480, fill=0x000000)
        p = self.ctl.p
        ac = self.ctl.s.color
        cv.text(("CHOOSE " + p["name"]).upper(), 18, 34, f_title(15), art.TXT)
        cv.text(self.ctl.s.info["name"].upper() + f" · {len(self.opts)} OPTIONS", 18, 54, f_label(12, 700), ac, tracking=0.8)
        cv.circle(286, 34, 18, stroke=0x5A606A, lw=1.5)
        art.cross(cv, 286, 34, 12, art.TXT, 2.4)
        cur = int(round(self.ctl.value() - p["min"]))
        for i in range(self.PER):
            k = self.page * self.PER + i
            if k >= len(self.opts):
                break
            y = self.TOP + i * self.ROW
            sel = k == cur
            cv.rrect(10, y, 300, self.ROW - 6, 10, fill=0x262B33 if sel else art.CARD, stroke=ac if sel else art.EDGE,
                     lw=2 if sel else 1.5)
            if sel:
                cv.rrect(10, y + 6, 5, self.ROW - 18, 2.5, fill=ac)
            main, var = V.clean_option(self.opts[k])
            from chrome import wrap_long
            lines = wrap_long(main, f_label(14, 650), 272, 1 if var else 2)
            y0 = y + (21 if (var or len(lines) > 1) else 31)
            for j, ln in enumerate(lines):
                cv.text(ln, 24, y0 + j * 17, f_label(14, 650), art.TXT)
            if var:
                cv.text(fit(var, f_label(12, 600), 272), 24, y0 + 18, f_label(12, 600), art.TXT2)
        cv.rect(0, 434, 320, 46, fill=0x0C0D10)
        cv.rect(0, 434, 320, 1, fill=0x30343C)
        en_p, en_n = self.page > 0, self.page < self.pages - 1
        art.chevron(cv, 53, 451, 14, art.TXT if en_p else 0x4A4F59, "up", 2.6)
        cv.text("PREV", 53, 473, f_label(12, 650), art.TXT if en_p else 0x4A4F59, align="center")
        cv.text(f"{self.page + 1} / {self.pages}", 160, 455, f_label(13, 700), art.TXT, align="center")
        cv.text("CANCEL", 160, 473, f_label(12, 650), art.TXT2, align="center")
        art.chevron(cv, 267, 451, 14, art.TXT if en_n else 0x4A4F59, "down", 2.6)
        cv.text("NEXT", 267, 473, f_label(12, 650), art.TXT if en_n else 0x4A4F59, align="center")


class Transport(Widget):
    """Looper: big REC / PLAY / DUB / STOP buttons bound to the 'state' enum (Stop, Record, Play, Overdub)."""
    gestures = ("tap", "press")
    BTN = [("REC", 1, 0xFF3B30), ("PLAY", 2, 0x3CF06E), ("DUB", 3, 0xFFB000), ("STOP", 0, 0xF4F6F8)]
    CLEAR = 4

    def __init__(self, screen, p, x, y, w, h):
        super().__init__(x, y, w, h)
        self.s, self.p = screen, p
        self.has_clear = len(p.get("options") or []) > self.CLEAR
        ch = 40 if self.has_clear else 0
        bw, bh = (w - 30) / 2, (h - 30 - (ch + 8 if ch else 0)) / 2
        self.rects = [(x + 10 + (i % 2) * (bw + 10), y + 10 + (i // 2) * (bh + 10), bw, bh) for i in range(4)]
        self.btns = list(self.BTN)
        if self.has_clear:
            self.btns.append(("CLEAR", self.CLEAR, 0xB4BBC6))
            self.rects.append((x + 10, y + h - 10 - ch, w - 20, ch))

    def on_tap(self, g):
        for (lab, val, col), (bx, by, bw, bh) in zip(self.btns, self.rects):
            if bx <= g.x < bx + bw and by <= g.y < by + bh:
                if val == self.CLEAR:
                    # the effect can't write its param back: send Clear, then put the UI (and engine) on Stop
                    m, uid = self.s.model, self.s.uid
                    m.client.request("set_param", uid=uid, param=self.p["id"], value=float(self.CLEAR),
                                     cb=lambda r: m.set_param(uid, self.p["id"], 0.0))
                    sl = m.slot(uid)
                    if sl:
                        sl["params"][self.p["id"]] = 0.0
                    self.s.lcd.show("Looper", "CLEARED")
                else:
                    self.s.set(self.p, float(val))
                    self.s.lcd.show("Looper", lab)
                self.invalidate()

    def draw(self, cv):
        cur = int(round(self.s.get(self.p)))
        for (lab, val, col), (bx, by, bw, bh) in zip(self.btns, self.rects):
            on = cur == val and val != self.CLEAR
            cv.rrect(bx + 2, by + 4, bw, bh, 14, fill=0x000000, alpha=0.5)
            cv.rrect(bx, by, bw, bh, 14, fill=col if on else 0x15161A, stroke=0x000000, lw=2)
            if on:
                cv.glow(bx + bw / 2, by + bh / 2, bw * 0.6, col, 0.25)
            ic = 0x0A0A0C if on else col
            mx, my = bx + bw / 2, by + bh / 2 - 8
            if lab == "REC":
                cv.circle(mx, my, 11, fill=ic)
            elif lab == "PLAY":
                cr = cv.cr
                cr.move_to(mx - 8, my - 11); cr.line_to(mx + 12, my); cr.line_to(mx - 8, my + 11); cr.close_path()
                cv.src(ic); cr.fill()
            elif lab == "DUB":
                cv.circle(mx - 7, my, 8, fill=ic)
                cv.circle(mx + 7, my, 8, stroke=ic, lw=3)
            elif lab == "CLEAR":
                art.cross(cv, bx + bw / 2 - 64, by + bh / 2, 12, ic, 3)
                cv.text("CLEAR LOOP", bx + bw / 2 + 12, by + bh / 2 + 6, f_title(14), ic, align="center")
                continue
            else:
                cv.rect(mx - 9, my - 9, 18, 18, fill=ic)
            cv.text(lab, mx, by + bh - 12, f_title(14), ic, align="center")


class MixStrip(Widget):
    gestures = ("double", "drag", "press", "long")

    def __init__(self, screen):
        super().__init__(FACE[0] + 8, 380, FACE[2] - 16, 44)
        self.s = screen
        self.active = False

    @property
    def p(self):
        return self.s.mix_param

    def on_press(self, g):
        self.s.lcd.show("Mix", V.text(self.p, self.s.get(self.p)))
        import controls
        controls.focus = (self.s.uid, self.p)

    def on_double_tap(self, g):
        self._set(V.default(self.p))

    def on_drag_start(self, g):
        self.active = True
        core.app.dragging_value = True
        self.t0 = V.norm(self.p, self.s.get(self.p))
        self.fine = g.long
        self.invalidate()

    def on_drag(self, g):
        span = self.w - 40
        self._set(V.denorm(self.p, self.t0 + g.dx / span * (FINE if self.fine else 1.0)))

    def on_drag_end(self, g):
        self.active = False
        core.app.dragging_value = False
        self.invalidate()

    def _set(self, v):
        if v != self.s.get(self.p):
            self.s.set(self.p, v)
            self.invalidate()
        self.s.lcd.show("Mix", V.text(self.p, v), getattr(self, "fine", False))

    def draw(self, cv):
        ink = self.s.ink
        x, y, w, h = self.rect
        v = self.s.get(self.p)
        t = V.norm(self.p, v)
        cv.text("MIX", x + 12, y + 16, f_title(12), ink)
        cv.text("DRY", x + 60, y + 16, f_label(12, 700), ink, alpha=0.6)
        cv.text("WET", x + w - 70, y + 16, f_label(12, 700), ink, align="right", alpha=0.6)
        cv.text(V.text(self.p, v), x + w - 12, y + 16, f_num(13, 700), ink, align="right")
        tx, tw, ty = x + 20, w - 40, y + 26
        cv.rrect(tx - 2, ty - 2, tw + 4, 14, 7, fill=0x000000, alpha=0.5)
        cv.rrect(tx, ty, tw, 10, 5, fill=0x07080A)
        cv.rrect(tx, ty, max(10, tw * t), 10, 5, fill=mix(self.s.color, 0xFFFFFF, 0.35))
        hx = tx + tw * t
        cv.rrect(hx - 9, ty - 7, 18, 24, 4, fill=cv.lin_grad(0, ty - 7, 0, ty + 17, [(0, 0xF4F6F9), (1, 0x8C9199)]),
                 stroke=0x000000, lw=1.2)
        cv.line([(hx, ty - 3), (hx, ty + 13)], 0x3A3D44, 1.4)
        if self.active:
            cv.rrect(x, y, w, h, 10, stroke=ink, lw=1.5, alpha=0.7)


class Edit(BaseScreen):
    name = "edit"

    def __init__(self):
        super().__init__()
        self.uid = None
        self.footer = FooterBar([
            ("REMOVE", lambda cv, x, y, c: art.cross(cv, x, y, 13, c), self.remove, art.RED),
            ("RESET", lambda cv, x, y, c: art.reset_icon(cv, x, y, 7, c), self.reset),
            ("DONE", lambda cv, x, y, c: art.check(cv, x, y, 16, c), lambda: self.nav.board()),
        ])

    # ---- model access
    @property
    def slot(self):
        return self.model.slot(self.uid)

    @property
    def on(self):
        s = self.slot
        return bool(s and s.get("enabled", True))

    def get(self, p):
        s = self.slot
        if s is None:
            return p.get("def", 0)
        if p["id"] == "__mix":
            return s.get("mix", 1.0)
        return self.model.pval(s, p)

    def set(self, p, v):
        self.model.set_param(self.uid, p["id"], v)

    def toggle(self):
        self.model.set_enabled(self.uid, not self.on)

    # ---- build
    def open(self, uid):
        self.uid = uid
        self.build()

    def build(self):
        self.clear()
        s = self.slot
        if s is None:
            return
        self.info = self.model.info(s)
        self.color = accent(self.info["category"])
        self.ink = ink_on(self.color)
        params = list(self.info["params"])
        own_mix = next((p for p in params if p["id"] == "mix"), None)
        if own_mix:
            params.remove(own_mix)
        self.mix_param = own_mix or V.SLOT_MIX
        transport = next((p for p in params if p["id"] == "state" and V.is_enum(p)), None) \
            if self.info["id"] == "looper" else None
        if transport:
            params.remove(transport)
        hero, rest = params[:2], params[2:8]
        if transport:
            hero, rest = [], params[:4]
        rows = [rest[i:i + 4] for i in range(0, len(rest), 4)]
        self.face = Face(self)
        self.lcd = LCD(self)
        self.add(self.face, self.lcd, Footswitch(self))
        top, bot = 132, 378
        fx, fw = FACE[0] + 6, FACE[2] - 12
        if transport:
            th = 176 if len(transport.get("options") or []) > Transport.CLEAR else 156
            self.add(Transport(self, transport, fx, top, fw, th))
            for k, p in enumerate(rest):
                w = fw / max(3, len(rest))
                self.add(Control(self, p, fx + (fw - w * len(rest)) / 2 + k * w, top + th, w, bot - top - th,
                                 18 if th > 160 else 24, "small", dots=th <= 160))
            self.add(MixStrip(self), self.footer)
            if self.attached():
                self.on_show()
            return
        if len(rows) == 0:
            hero_h, r_hero = bot - top, 48
        elif len(rows) == 1:
            hero_h, r_hero = 134, 40
        else:
            hero_h, r_hero = 108, 28
        fx, fw = FACE[0] + 6, FACE[2] - 12
        for k, p in enumerate(hero):
            w = fw / len(hero)
            self.add(Control(self, p, fx + k * w, top, w, hero_h, r_hero, "hero"))
        row_h = (bot - top - hero_h) / max(1, len(rows))
        for j, row in enumerate(rows):
            w = fw / len(row) if len(row) >= 3 else fw / 3
            x0 = fx + (fw - w * len(row)) / 2
            for k, p in enumerate(row):
                two = len(rows) == 2
                self.add(Control(self, p, x0 + k * w, top + hero_h + j * row_h, w, row_h, 16 if two else 22, "small",
                                 dots=not two))
        self.add(MixStrip(self), self.footer)
        if self.attached():
            self.on_show()

    def configure_header(self):
        m = self.model
        i, n = m.index(self.uid), len(m.chain)
        ctx.header.configure(title=self.info["name"], kicker=f"MOVE {i + 1}/{n}", back=None,
                             steppers=(lambda: self.step(-1), lambda: self.step(+1), i > 0, i < n - 1),
                             title_color=art.TXT, kicker_color=self.color)
        h = ctx.header
        h.jack_in.on_tap_cb = h.jack_out.on_tap_cb = lambda side: self.nav.settings()

    def on_show(self):
        super().on_show()

    def step(self, d):
        m = self.model
        i = m.index(self.uid)
        j = i + d
        if 0 <= j < len(m.chain):
            m.move(self.uid, j)
            self.configure_header()

    def on_model(self, what):
        super().on_model(what)
        if what == "chain":
            if self.slot is None:
                self.nav.board()
                return
            self.configure_header()
            if not core.app.dragging_value:
                for c in self.children:
                    if c is not ctx.header:
                        c.recache() if c.cache else c.invalidate()

    # ---- actions
    def remove(self):
        m = self.model
        s = self.slot
        if s is None:
            return
        snap = {"type": s["type"], "index": m.index(self.uid), "params": dict(s.get("params", {})),
                "enabled": s.get("enabled", True), "mix": s.get("mix", 1.0)}
        name = self.info["name"]
        m.remove(self.uid)
        self.nav.board()
        snackbar(f"Removed {name}", "UNDO", lambda: m.add(snap["type"], snap["index"], params=snap["params"],
                                                         enabled=snap["enabled"], mix=snap["mix"]))

    def reset(self):
        s = self.slot
        if s is None:
            return
        before = dict(s.get("params", {}))
        mix_before = s.get("mix", 1.0)
        for p in self.info["params"]:
            self.model.set_param(self.uid, p["id"], V.default(p))
        if self.mix_param is V.SLOT_MIX:
            self.model.set_param(self.uid, "__mix", 1.0)
        self.model.notify("chain")
        uid = self.uid

        def undo():
            for k, v in before.items():
                self.model.set_param(uid, k, v)
            self.model.set_param(uid, "__mix", mix_before)
            self.model.notify("chain")
        snackbar("Reset to defaults", "UNDO", undo)
