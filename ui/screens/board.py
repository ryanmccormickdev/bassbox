"""Board (home): the signal chain. IN (top left) -> live signal line down the left gutter -> pedal cards ->
U-turn -> up the right gutter -> OUT (top right)."""
import math
import time

import art
import core
from art import (BG, CARD, CARD_OFF, DIM, EDGE, EDGE_OFF, NEUTRAL, TXT, TXT2, TXT3, accent, mini_stomp)
from chrome import (FOOTER_Y, LX, RX, STUB_Y, Button, FooterBar, f_label, f_num, f_title, fit, shrink)
from core import Widget
from gfx import font, mix, text_width
from screens.base import BaseScreen, ctx
import values as V

CHAIN_TOP, CHAIN_BOT = 80, 394
UTURN_Y = 416
CARD_X, CARD_W = 40, 244
MAX_SLOTS = 8


def card_layout(n):
    if n <= 0:
        return [], 86, 10
    if n <= 3:
        h, g = 86, 10
    elif n == 4:
        h, g = 72, 8
    else:
        g = 3 if n >= 7 else 5
        h = min(56, int((CHAIN_BOT - CHAIN_TOP - (n - 1) * g) / n))
    return [CHAIN_TOP + i * (h + g) for i in range(n)], h, g


# ====================================================================== comets
class Comets:  # noqa: D101
    """Light travelling along a polyline. Rate/brightness from the live level; 20 fps; only the comets'
    own small boxes are invalidated."""
    FPS = 20
    TAIL = 14

    def __init__(self, owner):
        self.owner = owner
        self.items = []
        self.next_spawn = 0.0
        self.last = 0.0
        self.pts = []

    def box(self, s):
        i1 = int(s)
        i0 = max(0, i1 - self.TAIL)
        seg = self.pts[i0:i1 + 1] or [self.pts[min(i1, len(self.pts) - 1)]]
        xs = [p[0] for p in seg]
        ys = [p[1] for p in seg]
        return (min(xs) - 9, min(ys) - 9, max(xs) - min(xs) + 18, max(ys) - min(ys) + 18)

    def clear(self):
        for s in self.items:
            if 0 <= s < len(self.pts):
                self.owner.invalidate(self.box(s))
        self.items = []

    def step(self, now, level):
        dt = min(0.1, now - self.last) if self.last else 1 / self.FPS
        self.last = now
        speed = 70 + 150 * level
        keep = []
        n = len(self.pts)
        for s in self.items:
            old = self.box(min(s, n - 1)) if s < n + self.TAIL else None
            s += speed * dt
            if s < n + self.TAIL:
                keep.append(s)
                new = self.box(min(s, n - 1))
                old = core._union(old, new) if old else new
            if old:
                core.app.dirty(old)
        self.items = keep
        if now >= self.next_spawn and self.pts:
            self.items.append(0.0)
            self.next_spawn = now + (1.5 - 0.9 * level)
        return now + 1 / self.FPS

    def draw(self, cv, color_at, level):
        a = 0.55 + 0.45 * level
        n = len(self.pts)
        cx0, cy0, cx1, cy1 = cv.cr.clip_extents()
        for s in self.items:
            bx, by, bw, bh = self.box(min(s, n - 1))
            if bx > cx1 or by > cy1 or bx + bw < cx0 or by + bh < cy0:
                continue
            head = int(s)
            i0, i1 = max(0, head - self.TAIL), min(n - 1, head)
            if i1 - i0 >= 2:      # tail: one translucent stroke
                cv.line(self.pts[i0:i1 + 1:2] + [self.pts[i1]], mix(color_at(i1), 0xFFFFFF, 0.35), 3.2, alpha=0.45 * a)
            if 0 <= head < n:
                x, y = self.pts[head]
                c = color_at(head)
                cv.glow(x, y, 9, c, 0.6 * a)
                cv.circle(x, y, 3.0, fill=mix(c, 0xFFFFFF, 0.75), alpha=a)


def _sample(segs, step=1.0):
    pts = []
    for sg in segs:
        if sg[0] == "L":
            (x0, y0), (x1, y1) = sg[1], sg[2]
            n = max(2, int(math.hypot(x1 - x0, y1 - y0) / step))
            pts += [(x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1)) for i in range(n)]
        elif sg[0] == "A":
            _, cx, cy, r, a0, a1 = sg
            n = max(2, int(abs(math.radians(a1 - a0)) * r / step))
            pts += [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / (n - 1))),
                     cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / (n - 1)))) for i in range(n)]
        else:  # cubic bezier
            _, p0, c1, c2, p1 = sg
            n = 120
            for i in range(n):
                t = i / (n - 1)
                mt = 1 - t
                pts.append((mt ** 3 * p0[0] + 3 * mt * mt * t * c1[0] + 3 * mt * t * t * c2[0] + t ** 3 * p1[0],
                            mt ** 3 * p0[1] + 3 * mt * mt * t * c1[1] + 3 * mt * t * t * c2[1] + t ** 3 * p1[1]))
    return pts


# ====================================================================== signal line
class CometLayer(Widget):
    """Draws a Comets object (kept separate from the cached cable so only comet boxes re-render)."""
    ambient = True

    def __init__(self, owner, rect):
        super().__init__(*rect)
        self.owner = owner

    def tick(self, now):
        return self.owner.tick(now)

    def draw(self, cv):
        o = self.owner
        if o.comets.items:
            o.comets.draw(cv, o.color_at, min(1.0, (o.board.model.meters.get("in_rms", 0) or 0) * 6))


class SignalLine(Widget):
    """The main cable: IN -> nodes -> U-turn -> OUT. Segment colour = last active pedal upstream."""
    cache = True

    def __init__(self, board):
        super().__init__(0, 60, 320, UTURN_Y + 12 - 60)
        self.board = board
        self.layer = CometLayer(self, self.rect)
        self.comets = Comets(self.layer)
        self.pts, self.cols, self.nodes = [], [], []
        self.ticking = False

    def rebuild(self):
        b, m = self.board, self.board.model
        h = ctx.header
        y0 = y1 = STUB_Y          # the line starts/ends at the socket bottoms
        cards = [c for c in b.cards]
        nodes = [(c.home_y + c.h / 2, c) for c in cards]
        segs = [("L", (LX, y0), (LX, UTURN_Y - 14)), ("A", LX + 14, UTURN_Y - 14, 14, 180, 90),
                ("L", (LX + 14, UTURN_Y), (RX - 14, UTURN_Y)), ("A", RX - 14, UTURN_Y - 14, 14, 90, 0),
                ("L", (RX, UTURN_Y - 14), (RX, y1))]
        self.pts = _sample(segs)
        # colour per point: neutral until the first active node, then that pedal's accent, ...
        cols = []
        cur = NEUTRAL
        ni = 0
        for x, y in self.pts:
            while ni < len(nodes) and x == LX and y >= nodes[ni][0]:
                s = m.slot(nodes[ni][1].uid)
                if s and s.get("enabled", True):
                    cur = accent(m.info(s)["category"])
                ni += 1
            if x != LX and ni < len(nodes):
                ni = len(nodes)
            cols.append(cur)
        self.cols = cols
        self.nodes = nodes
        self.comets.pts = self.pts
        self._cache = None             # colours/nodes live only in the gutters + U-turn: flush just those
        for r in ((2, 60, 40, UTURN_Y - 66), (286, 60, 26, UTURN_Y - 66), (2, UTURN_Y - 22, 316, 32)):
            core.app.dirty(r)

    def color_at(self, i):
        return self.cols[min(i, len(self.cols) - 1)] if self.cols else NEUTRAL

    def running(self):
        m = self.board.model
        return (core.app.screen is self.board and m.signal_present() and m.input_live() and not m.bypassed()
                and not core.app.dragging_value and self.board.reorder is None and m.connected)

    def kick(self):
        if self.running() and not self.ticking:
            self.ticking = True
            core.app.animate(self.layer)

    def tick(self, now):
        if not self.running():
            self.comets.clear()
            self.ticking = False
            return None
        lvl = min(1.0, (self.board.model.meters.get("in_rms", 0) or 0) * 6)
        return self.comets.step(now, lvl)

    def draw(self, cv):
        m = self.board.model
        dim = m.bypassed() or not m.input_live() or not m.connected
        pts, cols = self.pts, self.cols
        if not pts:
            return
        # draw runs of equal colour
        start = 0
        for i in range(1, len(pts) + 1):
            if i == len(pts) or cols[i] != cols[start]:
                seg = pts[max(0, start - 1):i]
                c = 0x464B55 if dim else cols[start]
                if len(seg) > 1:
                    if not dim:
                        cv.line(seg, c, 11, alpha=0.16)
                    cv.line(seg, c, 5 if not dim else 4)
                start = i
        # nodes + stubs
        for ny, card in self.nodes:
            s = m.slot(card.uid)
            if s is None or card.lifted:
                continue
            on = s.get("enabled", True)
            ac = accent(m.info(s)["category"])
            if on and not dim:
                cv.line([(LX, ny), (CARD_X, ny)], ac, 4)
                cv.circle(LX, ny, 10, fill=BG)
                cv.glow(LX, ny, 17, ac, 0.5)
                cv.circle(LX, ny, 8, fill=ac)
                cv.circle(LX, ny, 3.2, fill=0xFFFFFF)
            else:
                cv.line([(LX + 10, ny), (CARD_X, ny)], 0x5A606A, 2, dash=[3, 4])
                cv.circle(LX, ny, 8, fill=BG, stroke=0x8A909A if on else 0x6A707A, lw=2.4)


class BypassCable(Widget):
    """Global bypass: a direct cable from IN to OUT straight across the bottom of the header."""
    cache = True

    def __init__(self, board):
        super().__init__(0, 62, 320, 22)
        self.board = board
        self.layer = CometLayer(self, self.rect)
        self.comets = Comets(self.layer)
        self.comets.pts = _sample([("C", (LX + 15, 67), (LX + 80, 78), (RX - 80, 78), (RX - 15, 67))])
        self.ticking = False
        self.visible = False

    def running(self):
        m = self.board.model
        return (self.visible and core.app.screen is self.board and m.signal_present() and m.input_live()
                and not core.app.dragging_value and m.connected)

    def kick(self):
        if self.running() and not self.ticking:
            self.ticking = True
            core.app.animate(self.layer)

    def tick(self, now):
        if not self.running():
            self.comets.clear()
            self.ticking = False
            return None
        lvl = min(1.0, (self.board.model.meters.get("in_rms", 0) or 0) * 6)
        return self.comets.step(now, lvl)

    def color_at(self, i):
        return NEUTRAL

    def draw(self, cv):
        pts = self.comets.pts
        cv.line(pts, 0x000000, 8)
        cv.line(pts, NEUTRAL, 11, alpha=0.18)
        cv.line(pts, NEUTRAL, 4.5)


class Dimmer(Widget):
    """Dims the chain when the input is unplugged, the engine is offline or the board is bypassed."""
    cache = True

    def __init__(self, board):
        super().__init__(0, 74, 320, FOOTER_Y - 74)
        self.board = board
        self.visible = False
        self.mode = None

    def set_mode(self, mode):
        if mode == self.mode:
            return
        if self.visible:
            self.invalidate()
        self._cache = None
        self.mode = mode
        self.visible = mode is not None
        if self.visible:
            self.invalidate()

    def draw(self, cv):
        cv.rect(self.x, self.y, self.w, self.h, fill=0x000000, alpha=0.62)
        if self.mode == "unplugged":
            x, y, w, h = 40, 196, 240, 92
            cv.rrect(x, y, w, h, 16, fill=0x16181D, stroke=0x5A606A, lw=1.5)
            art.jack(cv, x + 32, y + 52, lift=26, tilt=6, live=False, bow=1, s=0.75, cable_top=y + 6)
            cv.text("PLUG IN", x + 66, y + 40, f_title(16), TXT)
            cv.text("YOUR BASS", x + 66, y + 60, f_title(16), TXT)
            cv.text("No input · output muted", x + w / 2, y + 82, f_label(12, 500), TXT2, align="center")
        elif self.mode == "bypass":
            cv.rrect(86, 230, 148, 34, 17, fill=0x16181D, stroke=0x5A606A, lw=1.5)
            cv.text("BYPASSED", 160, 253, f_title(14), TXT, align="center")
        elif self.mode == "offline":
            pass


# ====================================================================== cards
class PedalCard(Widget):
    gestures = ("tap", "long", "drag", "press")
    overflow = 4
    cache = True

    def __init__(self, board, uid, y, h):
        super().__init__(CARD_X, y, CARD_W, h)
        self.board, self.uid = board, uid
        self.home_y = y
        self.pressed = False
        self.lifted = False
        self.grab = 0

    @property
    def compact(self):
        return self.h < 64

    def snapshot(self):
        sl = self.board.model.slot(self.uid) or {}
        return (sl.get("enabled"), sl.get("mix"), tuple(sorted((sl.get("params") or {}).items())))

    def refresh(self):
        snap = self.snapshot()
        if snap != getattr(self, "_snap", None):
            self._snap = snap
            self.recache()

    def on_press(self, g):
        self.pressed = True
        self.recache()

    def on_release(self, g):
        if self.pressed:
            self.pressed = False
            self.recache()

    def _power_zone(self, g):
        return g.x >= self.x + self.w - 58

    def on_tap(self, g):
        m = self.board.model
        s = m.slot(self.uid)
        if s is None:
            return
        if self._power_zone(g):
            m.set_enabled(self.uid, not s.get("enabled", True))
        else:
            self.board.nav.edit(self.uid)

    def on_long_press(self, g):
        self.board.start_reorder(self, g)

    def on_drag_start(self, g):
        if g.long:
            self.board.reorder_drag(g)

    def on_drag(self, g):
        if g.long:
            self.board.reorder_drag(g)

    def on_drag_end(self, g):
        if g.long:
            self.board.end_reorder()

    def on_long_release(self, g):
        self.board.end_reorder()

    def draw(self, cv):
        m = self.board.model
        s = m.slot(self.uid)
        if s is None:
            return
        info = m.info(s)
        on = s.get("enabled", True)
        ac = accent(info["category"])
        x, y, w, h = self.rect
        if self.lifted:
            cv.rrect(x - 2, y + 4, w + 4, h + 4, 14, fill=0x000000, alpha=0.7)
        fill = CARD if on else CARD_OFF
        if self.pressed and not self.lifted:
            fill = mix(fill, 0xFFFFFF, 0.08)
        cv.rrect(x, y, w, h, 12, fill=fill, stroke=(0xFFFFFF if self.lifted else (EDGE if on else EDGE_OFF)),
                 lw=2 if self.lifted else 1.5)
        if on:
            cv.rrect(x, y + 6, 4, h - 12, 2, fill=ac)
        name_c = TXT if on else DIM
        if not self.compact:
            ch_h = min(50, h - 20)
            mini_stomp(cv, x + 12, y + (h - ch_h) / 2, round(ch_h * 0.78), ch_h, info["id"], ac, on)
            tx = x + 60
            ny = y + (26 if h >= 80 else 24)
            nf, name = shrink(info["name"].upper(), "archivo", (13, 12), 800, 125, 128 if on else 90)
            if name.endswith("…"):
                nf, name = shrink(info["name"].upper(), "archivo", (15, 14, 13), 800, 75, 128 if on else 90)
            nw = cv.text(name, tx, ny, nf, name_c)
            if not on:
                cv.rrect(tx + nw + 6, ny - 13, 34, 17, 5, fill=0x2E3239)
                cv.text("OFF", tx + nw + 23, ny, f_label(12, 800), TXT2, align="center")
            for k, p in enumerate(info["params"][:2]):
                hx = tx + k * 72
                v = m.pval(s, p)
                cv.text(fit(p["name"].upper(), f_label(12, 650), 68), hx, y + (46 if h >= 80 else 43), f_label(12, 650),
                        TXT3 if on else 0x6A707A, tracking=0.4)
                cv.text(V.text(p, v), hx, y + (64 if h >= 80 else 60), f_num(14, 600), TXT if on else DIM)
                if h >= 80:
                    cv.rrect(hx, y + 70, 62, 3, 1.5, fill=0x30343C)
                    cv.rrect(hx, y + 70, max(3, 62 * V.norm(p, v)), 3, 1.5, fill=ac if on else 0x4A4F59)
            px, py, pr = x + w - 28, y + h / 2, 17
        else:
            ch_h = h - 10
            mini_stomp(cv, x + 10, y + 5, round(ch_h * 0.8), ch_h, info["id"], ac, on)
            tx = x + 14 + round(ch_h * 0.8) + 8
            cy = y + h / 2 + 5
            vs = ""
            if info["params"]:
                p = info["params"][0]
                vs = V.text(p, m.pval(s, p)) if on else "OFF"
                cv.text(vs, x + w - 54, cy, f_num(13, 600), TXT2 if on else DIM, align="right")
            room = (x + w - 54 - text_width(vs, f_num(13, 600)) - 10) - tx
            nf, nm = shrink(info["name"].upper(), "archivo", (14, 13, 12), 800, 75, room)
            cv.text(nm, tx, cy, nf, name_c)
            px, py, pr = x + w - 26, y + h / 2, min(14, h / 2 - 4)
        if on:
            cv.circle(px, py, pr, fill=ac, alpha=0.2)
            cv.circle(px, py, pr, stroke=ac, lw=2.2)
            art.power_icon(cv, px, py, pr * 0.42, ac, 2.2)
        else:
            cv.circle(px, py, pr, stroke=0x5A606A, lw=2)
            art.power_icon(cv, px, py, pr * 0.42, 0x7A808A, 2.2)


class AddPill(Widget):
    gestures = ("tap", "press")
    pad = 6

    def __init__(self, board):
        super().__init__(92, UTURN_Y - 16, 136, 32)
        self.board = board
        self.pressed = False

    def on_press(self, g):
        self.pressed = True
        self.invalidate()

    def on_release(self, g):
        self.pressed = False
        self.invalidate()

    def on_tap(self, g):
        if len(self.board.model.chain) < MAX_SLOTS:
            self.board.nav.library(len(self.board.model.chain))

    def draw(self, cv):
        full = len(self.board.model.chain) >= MAX_SLOTS
        x, y, w, h = self.rect
        cv.rrect(x, y, w, h, h / 2, fill=0x2A2E35 if self.pressed else 0x111317,
                 stroke=0x50555F if full else 0xB4BBC6, lw=1.8)
        if full:
            cv.text("CHAIN FULL 8/8", x + w / 2, y + 21, f_label(12, 700), 0x6A707A, align="center", tracking=0.6)
        else:
            art.plus(cv, x + 22, y + h / 2, 12, TXT, 2.4)
            cv.text("ADD PEDAL", x + 36, y + 21, f_label(13, 750), TXT, tracking=0.6)


# ====================================================================== board screen
class Board(BaseScreen):
    name = "board"

    def __init__(self):
        super().__init__()
        self.cards = []
        self.reorder = None
        self.signal = SignalLine(self)
        self.cards_box = Widget(0, CHAIN_TOP - 10, 320, CHAIN_BOT - CHAIN_TOP + 20)
        self.cards_box.contain = True
        self.dimmer = Dimmer(self)
        self.bypass_cable = BypassCable(self)
        self.add_pill = AddPill(self)
        self.footer = FooterBar([
            ("BYPASS", lambda cv, x, y, c: art.power_icon(cv, x, y, 7, c), self.toggle_bypass),
            ("TUNER", lambda cv, x, y, c: art.tuning_fork(cv, x, y, 18, c), lambda: self.nav.tuner()),
            ("PRESETS", lambda cv, x, y, c: art.list_icon(cv, x, y, 18, c), lambda: self.nav.presets()),
            ("SETTINGS", lambda cv, x, y, c: art.gear(cv, x, y, 7, c), lambda: self.nav.settings()),
        ])
        self.b_bypass = self.footer.buttons[0]
        self.add(self.signal, self.signal.layer, self.cards_box, self.dimmer, self.bypass_cable, self.bypass_cable.layer,
                 self.add_pill, self.footer)
        self.sig = None

    # ---- header
    def configure_header(self):
        m = self.model
        name = m.state.get("name") or ("CONNECTING" if not m.ever_connected else "UNTITLED")
        kind = ("NOT SAVED" if not m.state.get("name") else
                "FACTORY PRESET" if m.current_is_factory() else ("USER PRESET" if m.presets else "PRESET"))
        ctx.header.configure(title=name, kicker=kind, on_title=lambda: self.nav.presets(),
                             dot=0xFF8A00 if m.state.get("dirty") else None)
        h = ctx.header
        h.jack_in.on_tap_cb = h.jack_out.on_tap_cb = lambda side: self.nav.settings()

    def on_show(self):
        super().on_show()
        self.rebuild()

    # ---- structure
    def chain_sig(self):
        m = self.model
        return tuple(s["uid"] for s in m.chain)

    def rebuild(self):
        m = self.model
        uids = [s["uid"] for s in m.chain]
        ys, h, g = card_layout(len(uids))
        self.cards_box.clear()
        self.cards = []
        for uid, y in zip(uids, ys):
            c = PedalCard(self, uid, y, h)
            c._snap = c.snapshot()
            self.cards.append(c)
            self.cards_box.add(c)
        self.sig = self.chain_sig()
        self.signal.rebuild()
        self.update_states()
        core.app.dirty((0, 72, 320, FOOTER_Y - 72))

    def update_states(self):
        m = self.model
        byp = m.bypassed()
        self.b_bypass.set_lit(byp, "BYPASSED" if byp else "BYPASS")
        mode = None
        if m.ever_connected and not m.connected:
            mode = "offline"
        elif not m.input_live():
            mode = "unplugged"
        elif byp:
            mode = "bypass"
        self.dimmer.set_mode(mode)
        self.bypass_cable.set_visible(byp)
        self.bypass_cable.layer.set_visible(byp)
        if (mode, byp) != getattr(self, "_mode", None):
            self._mode = (mode, byp)
            self.signal.recache()
        self.signal.kick()
        self.bypass_cable.kick()

    def toggle_bypass(self):
        self.model.set_master(bypass=not self.model.bypassed())

    def on_model(self, what):
        super().on_model(what)
        if what in ("chain", "catalog"):
            if self.reorder is None:
                if self.chain_sig() != self.sig or what == "catalog":
                    self.rebuild()
                else:
                    for c in self.cards:
                        c.refresh()
                    self.signal.rebuild()
            self.configure_header()
        elif what in ("presets",):
            self.configure_header()
        elif what in ("master", "jacks", "connection"):
            if what == "jacks":
                self.signal.rebuild()
            self.update_states()
            self.configure_header()
        elif what == "meters":
            self.signal.kick()
            self.bypass_cable.kick()

    def tick(self, now):
        return None

    # ---- reorder (long-press a card, drag, release)
    def start_reorder(self, card, g):
        self.reorder = card
        card.lifted = True
        card.grab = g.y - card.y
        self.signal.comets.clear()
        card.recache()
        self.signal.recache()

    def reorder_drag(self, g):
        card = self.reorder
        if card is None:
            return
        old = card.bounds
        ny = max(CHAIN_TOP - 6, min(CHAIN_BOT - card.h + 6, g.y - card.grab))
        card.y = ny
        core.app.dirty(old)
        card.invalidate()
        # target index from the card centre
        n = len(self.cards)
        ys, h, gap = card_layout(n)
        centre = ny + card.h / 2
        idx = min(n - 1, max(0, int((centre - CHAIN_TOP) / (h + gap))))
        order = [c for c in self.cards if c is not card]
        order.insert(idx, card)
        for c, y in zip(order, ys):
            c.home_y = y
            if c is not card and c.y != y:
                c.invalidate()
                c.y = y
                c.invalidate()
        self._order = order

    def end_reorder(self):
        card = self.reorder
        if card is None:
            return
        self.reorder = None
        card.lifted = False
        order = getattr(self, "_order", self.cards)
        new_idx = order.index(card) if card in order else self.model.index(card.uid)
        self._order = None
        if new_idx != self.model.index(card.uid):
            self.model.move(card.uid, new_idx)
        self.rebuild()
