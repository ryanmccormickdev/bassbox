"""bassbox vector art: palette, effect glyphs, TRS jack + plug, mini stompbox chip, knobs, icons."""
import math

import cairo

from gfx import mix

# ---------------------------------------------------------------- palette
CATEGORY = {
    # tuned for the panel: saturated, warm hues pushed toward red/yellow (the TN panel adds a
    # blue cast off-axis, so plain orange drifts salmon and amber drifts khaki)
    "dynamics": (0x00E0C0, "Dynamics"),
    "drive": (0xFF8A00, "Drive"),
    "amp": (0xFF3B30, "Amp"),
    "filter": (0xFF45C8, "Filter"),
    "eq": (0xFFD500, "EQ"),
    "modulation": (0xA472FF, "Mod"),
    "pitch": (0x3CF06E, "Pitch"),
    "time": (0x2F9BFF, "Time"),
    "utility": (0xC2CAD6, "Utility"),
}
CATEGORY_ORDER = ["dynamics", "drive", "amp", "filter", "eq", "modulation", "pitch", "time", "utility"]

BG = 0x000000
CARD = 0x1A1D23
CARD_OFF = 0x101216
EDGE = 0x4A505C
EDGE_OFF = 0x353942
TXT = 0xF4F6F8
TXT2 = 0xB4BBC6
TXT3 = 0x9AA2AE
DIM = 0x737A86
NEUTRAL = 0xE4E9F0
INK = 0x0A0A0C
GREEN, YELLOW, RED = 0x3CF06E, 0xFFD500, 0xFF3B30
LED_RED = 0xFF2A1A


def accent(cat):
    return CATEGORY.get(cat, CATEGORY["utility"])[0]


def ink_on(c):
    r, g, b = (c >> 16) & 255, (c >> 8) & 255, c & 255
    return INK if (0.299 * r + 0.587 * g + 0.114 * b) > 120 else 0xFFFFFF


def body_color(c, on=True):
    return c if on else mix(c, 0x2A2C32, 0.72)


# ---------------------------------------------------------------- glyphs
def _wave(fn, n=48, x0=0.0, x1=1.0):
    return [(x0 + (x1 - x0) * i / (n - 1), fn(i / (n - 1))) for i in range(n)]


_ALIAS = {"bass_amp": "amp", "graphic_eq": "geq7", "gain": "boost", "lowpass": "cut"}


def _glyph_paths(eid):
    s = math.sin
    tau = 2 * math.pi
    eid = _ALIAS.get(eid, eid)
    P = []
    L = lambda pts, dim=False: P.append(("line", pts, dim))  # noqa: E731
    if eid == "distortion":
        L(_wave(lambda t: 0.5 - max(-0.22, min(0.22, 0.42 * s(tau * t)))))
    elif eid == "overdrive":
        L(_wave(lambda t: 0.5 - 0.34 * math.tanh(2.2 * s(tau * t)) / math.tanh(2.2)))
    elif eid == "fuzz":
        L([(0, .5), (.08, .2), (.42, .2), (.5, .5), (.58, .8), (.92, .8), (1, .5)])
        L([(.15, .2), (.2, .1), (.25, .2), (.3, .1), (.35, .2)])
    elif eid == "octafuzz":
        L(_wave(lambda t: 0.78 - 0.6 * abs(s(tau * t))))
        L([(.0, .86), (1, .86)], True)
    elif eid == "bitcrush":
        pts = []
        for i in range(9):
            x0, x1 = i / 9, (i + 1) / 9
            y = round((0.5 - 0.34 * s(tau * (x0 + 0.055))) * 6) / 6
            pts += [(x0, y), (x1, y)]
        L(pts)
    elif eid == "comp":
        L(_wave(lambda t: 0.5 - 0.16 * s(tau * 2 * t)))
        L([(.5, .02), (.5, .22)])
        L([(.38, .12), (.5, .24), (.62, .12)])
        L([(.5, .98), (.5, .78)])
        L([(.38, .88), (.5, .76), (.62, .88)])
    elif eid == "gate":
        L([(0, .8), (.2, .8), (.2, .2), (.8, .2), (.8, .8), (1, .8)])
        L([(0.3 + .4 * t, .5 - .14 * s(tau * 3 * t)) for t in [i / 30 for i in range(31)]])
    elif eid == "limiter":
        L([(0, .2), (1, .2)])
        L(_wave(lambda t: .5 - max(-.3, min(.3, .46 * s(tau * 1.5 * t)))))
    elif eid == "amp":
        P.append(("rrect", .04, .16, .92, .68, .12))
        for cx in (.28, .5, .72):
            P.append(("circle", cx, .44, .08))
        L([(.18, .68), (.82, .68)])
    elif eid == "cab":
        P.append(("rrect", .1, .06, .8, .88, .1))
        P.append(("circle", .5, .5, .27))
        P.append(("fillcircle", .5, .5, .08))
    elif eid == "nam":
        nodes = [(.12, .3), (.12, .7), (.5, .18), (.5, .5), (.5, .82), (.88, .5)]
        for a in nodes[:2]:
            for b in nodes[2:5]:
                L([a, b])
        for b in nodes[2:5]:
            L([b, nodes[5]])
        for n in nodes:
            P.append(("fillcircle", n[0], n[1], .075))
    elif eid == "wah":
        L(_wave(lambda t: .84 - .64 * math.exp(-((t - .55) / .13) ** 2)))
        L([(.2, .14), (.42, .14)])
        L([(.68, .14), (.9, .14)])
        L([(.82, .06), (.9, .14), (.82, .22)])
        L([(.28, .06), (.2, .14), (.28, .22)])
    elif eid == "env_filter":
        L(_wave(lambda t: .84 - .56 * math.exp(-((t - .4) / .12) ** 2)))
        L([(.56, .78), (.86, .22)])
        L([(.72, .22), (.86, .22), (.86, .36)])
    elif eid == "synth_filter":
        L(_wave(lambda t: .35 - .25 * math.exp(-((t - .55) / .07) ** 2) + (0 if t < .55 else (t - .55) * 1.1)))
    elif eid == "cut":
        L(_wave(lambda t: .3 + (0.5 * (1 - t / .25) if t < .25 else 0) + (0.5 * (t - .75) / .25 if t > .75 else 0)))
    elif eid == "eq3":
        for x, y in ((.2, .62), (.5, .3), (.8, .52)):
            L([(x, .08), (x, .92)])
            P.append(("fillrect", x - .12, y - .07, .24, .14))
    elif eid == "geq7":
        for i, h in enumerate((.5, .7, .45, .3, .55, .75, .6)):
            x = .08 + i * .14
            L([(x, .92), (x, .92 - h)])
    elif eid == "tilt":
        L([(.05, .78), (.95, .22)])
        P.append(("fillcircle", .5, .5, .09))
    elif eid == "chorus":
        L(_wave(lambda t: .5 - .28 * s(tau * 1.5 * t)))
        L(_wave(lambda t: .5 - .28 * s(tau * 1.5 * t + .9)), True)
    elif eid == "flanger":
        L(_wave(lambda t: .5 - .32 * s(tau * (1 + 2.2 * t) * t)))
    elif eid == "phaser":
        L(_wave(lambda t: .5 - .3 * s(tau * 1.5 * t)))
        P.append(("circle", .5, .5, .44, True))
    elif eid == "tremolo":
        L(_wave(lambda t: .5 - .4 * (0.55 + 0.45 * s(tau * t)) * s(tau * 6 * t), 90))
    elif eid == "vibrato":
        L(_wave(lambda t: .5 - .3 * s(tau * (2 + 1.2 * s(tau * t)) * t), 70))
    elif eid == "univibe":
        P.append(("circle", .5, .5, .42))
        L(_wave(lambda t: .5 - .2 * s(tau * 2 * t), 40, .14, .86))
    elif eid == "ringmod":
        P.append(("circle", .5, .5, .4))
        P.append(("circle", .5, .5, .2, True))
        L([(.5, .02), (.5, .98)])
    elif eid == "octaver":
        L(_wave(lambda t: .3 - .16 * s(tau * 2 * t)))
        L(_wave(lambda t: .72 - .2 * s(tau * t)), True)
    elif eid == "pitch":
        L([(.32, .9), (.32, .12)])
        L([(.16, .3), (.32, .12), (.48, .3)])
        L([(.68, .1), (.68, .88)])
        L([(.52, .7), (.68, .88), (.84, .7)])
    elif eid == "subsynth":
        L([(0, .3), (.25, .3), (.25, .7), (.5, .7), (.5, .3), (.75, .3), (.75, .7), (1, .7)])
        L([(.1, .9), (.9, .9)], True)
    elif eid == "delay":
        for i, h in enumerate((.8, .56, .38, .24, .14)):
            x = .1 + i * .2
            L([(x, .5 - h / 2), (x, .5 + h / 2)])
    elif eid == "tape":
        P.append(("circle", .26, .42, .2))
        P.append(("circle", .74, .42, .2))
        P.append(("fillcircle", .26, .42, .05))
        P.append(("fillcircle", .74, .42, .05))
        L([(.06, .42), (.12, .86), (.88, .86), (.94, .42)])
    elif eid in ("reverb", "shimmer"):
        for r in (.12, .26, .4):
            P.append(("arc", .14, .5, r * 2, -55, 55))
        P.append(("fillcircle", .14, .5, .07))
        if eid == "shimmer":
            L([(.86, .08), (.86, .3)])
            L([(.75, .19), (.97, .19)])
    elif eid == "boost":
        L([(.18, .1), (.18, .9), (.88, .5), (.18, .1)])
        L([(.32, .5), (.52, .5)])
        L([(.42, .4), (.42, .6)])
    elif eid == "di":
        L([(.04, .22), (.4, .22), (.6, .5), (.96, .5)])
        L([(.04, .78), (.4, .78), (.6, .5)])
    else:
        L(_wave(lambda t: .5 - .3 * s(tau * t)))
    return P


def glyph(cv, eid, x, y, size, color, lw=None, dim_alpha=0.45):
    """Effect glyph inside the box (x, y, size, size)."""
    cr = cv.cr
    lw = lw or max(1.6, size / 13)
    pad = size * 0.08
    sc = size - 2 * pad
    ox, oy = x + pad, y + pad
    cr.save()
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.set_line_width(lw)
    for item in _glyph_paths(eid):
        kind = item[0]
        dim = item[-1] is True
        cv.src(color, dim_alpha if dim else 1.0)
        if kind == "line":
            pts = item[1]
            cr.move_to(ox + pts[0][0] * sc, oy + pts[0][1] * sc)
            for px, py in pts[1:]:
                cr.line_to(ox + px * sc, oy + py * sc)
            cr.stroke()
        elif kind in ("circle", "fillcircle"):
            cr.new_sub_path()
            cr.arc(ox + item[1] * sc, oy + item[2] * sc, item[3] * sc, 0, 2 * math.pi)
            cr.fill() if kind == "fillcircle" else cr.stroke()
        elif kind == "arc":
            cr.new_sub_path()
            cr.arc(ox + item[1] * sc, oy + item[2] * sc, item[3] * sc, math.radians(item[4]), math.radians(item[5]))
            cr.stroke()
        elif kind == "rrect":
            cv.rrect_path(ox + item[1] * sc, oy + item[2] * sc, item[3] * sc, item[4] * sc, item[5] * sc)
            cr.stroke()
        elif kind == "fillrect":
            cv.rrect_path(ox + item[1] * sc, oy + item[2] * sc, item[3] * sc, item[4] * sc, 1.5)
            cr.fill()
    cr.restore()


# ---------------------------------------------------------------- mini stompbox chip
def led(cv, cx, cy, on, r=4.0, c=LED_RED):
    if on:
        cv.glow(cx, cy, r * 3.2, c, 0.75)
        cv.circle(cx, cy, r, fill=mix(c, 0xFFFFFF, 0.35))
    else:
        cv.circle(cx, cy, r, fill=0x050506, stroke=0x000000, lw=1)
        cv.circle(cx, cy, r - 0.6, stroke=0x6A6F78, lw=1.1)


def mini_stomp(cv, x, y, w, h, eid, color, on=True):
    """Solid category-coloured stompbox with the glyph cut out, a tiny knob and an LED."""
    body = body_color(color, on)
    r = max(4, w * 0.17)
    cv.rrect(x, y, w, h, r, fill=cv.lin_grad(x, y, x, y + h, [(0, mix(body, 0xFFFFFF, 0.16)), (1, mix(body, 0x000000, 0.18))]))
    cv.rrect(x + 0.6, y + 0.6, w - 1.2, h - 1.2, r - 0.5, stroke=0xFFFFFF, lw=1, alpha=0.25)
    k = max(2.5, w * 0.085)
    cv.circle(x + w - k - 4, y + k + 4, k, fill=INK)
    cv.line([(x + w - k - 4, y + k + 4), (x + w - k - 4 + k * 0.6, y + 4 + k * 0.4)], 0xF2E9D8, 1.2)
    led(cv, x + k + 4, y + k + 4, on, r=max(2.2, w * 0.07))
    g = min(w - 10, h - 2 * k - 14)
    glyph(cv, eid, x + (w - g) / 2, y + 2 * k + 8 + (h - 2 * k - 12 - g) / 2, g, INK, lw=max(1.8, g / 10))


# ---------------------------------------------------------------- TRS jack
BRASS = 0xF5C761
NICKEL = 0xE2E7EE
SHAFT_LEN = 30


def jack(cv, cx, nut_y=62, lift=0.0, tilt=0.0, glow=0.0, color=0xFFFFFF, live=True, bow=0.0, s=1.0, cable_top=0.0,
         socket=True):
    """Panel jack from the side. The socket (cut-away, inside the pedal) hangs below a hex nut at nut_y; the
    1/4" TRS plug enters from above and its cable runs up off the top edge (to the bass / the amp).
    lift: px the plug is raised out of the nut (0 = seated, ~32 = out). tilt: degrees. bow: cable slack 0..1.
    Returns the socket bottom (x, y), where the signal leaves into the pedal."""
    cr = cv.cr
    sw = 20 * s
    sock_top, sock_bot = nut_y + 9 * s, nut_y + 38 * s
    seated = lift < 0.5
    if socket:   # socket (cut-away): dark cavity with a light outline and the two contact springs
        cv.rrect(cx - sw / 2, sock_top, sw, sock_bot - sock_top, 3 * s, fill=0x06070A, stroke=0x6A707A, lw=1.2 * s)
        lit = mix(0x80868F, color, min(1.0, glow)) if (seated and live) else 0x5A5F68
        cv.line([(cx - sw / 2 + 2 * s, sock_bot - 4 * s), (cx - 3.5 * s, sock_bot - 9 * s)], lit, 1.6 * s)
        cv.line([(cx + sw / 2 - 2 * s, sock_top + 13 * s), (cx + 3.5 * s, sock_top + 15 * s)], lit, 1.6 * s)
    else:        # compact (sub-screens): nothing below the nut
        cr.save()
        cr.rectangle(cx - 60, -1000, 120, nut_y + 9 * s + 1000)
        cr.clip()
    # plug (rotates about the nut mouth)
    g = 0 if live else 1
    tipc, nic = mix(BRASS, 0x7A7E86, g * 0.75), mix(NICKEL, 0x7A7E86, g * 0.65)
    cr.save()
    cr.translate(cx, nut_y)
    cr.rotate(math.radians(tilt))
    cr.translate(-cx, -nut_y)
    sh = 8 * s
    tip_bot = nut_y + SHAFT_LEN * s - lift - 0.0
    segs = [(7 * s, tipc), (2.2 * s, 0x0A0A0C), (5 * s, nic), (2.2 * s, 0x0A0A0C), (SHAFT_LEN * s - 16.4 * s, nic)]
    y = tip_bot
    for i, (ln, c) in enumerate(segs):
        top = y - ln
        if i == 0:      # rounded tip
            cr.new_sub_path()
            cr.move_to(cx - sh / 2, top)
            cr.line_to(cx - sh / 2, y - sh / 2)
            cr.arc_negative(cx, y - sh / 2, sh / 2, math.pi, 0)
            cr.line_to(cx + sh / 2, top)
            cr.close_path()
            cv.src(c)
            cr.fill()
        else:
            cv.rect(cx - sh / 2, top, sh, ln, fill=c)
        y = top
    cv.rect(cx - sh / 2 + 1.3 * s, tip_bot - SHAFT_LEN * s + 1, 1.4 * s, SHAFT_LEN * s - 4 * s, fill=0xFFFFFF,
            alpha=0.5 if live else 0.15)
    bb = tip_bot - SHAFT_LEN * s            # barrel bottom
    bw, bh = 18 * s, 22 * s
    bar = 0x4E545E if live else 0x2E3035
    cv.rrect(cx - bw / 2, bb - bh, bw, bh, 3 * s, fill=cv.lin_grad(cx - bw / 2, 0, cx + bw / 2, 0, [
        (0, mix(bar, 0xFFFFFF, 0.28)), (0.45, bar), (1, mix(bar, 0x000000, 0.35))]))
    for k in range(4):                      # grip knurl
        cv.rect(cx - bw / 2 + 2 * s, bb - bh + 3 * s + k * 2.6 * s, bw - 4 * s, 1 * s, fill=0x000000, alpha=0.35)
    cv.rect(cx - bw / 2, bb - 8 * s, bw, 3 * s, fill=color if live else 0x4A4E57)          # coloured band
    rt = bb - bh
    cr.move_to(cx - 6 * s, rt)
    cr.line_to(cx + 6 * s, rt)
    cr.line_to(cx + 3.5 * s, rt - 8 * s)
    cr.line_to(cx - 3.5 * s, rt - 8 * s)
    cr.close_path()
    cv.src(mix(bar, 0x000000, 0.25))
    cr.fill()
    relief_top = cr.user_to_device(cx, rt - 8 * s)
    cr.restore()
    if not socket:
        cr.restore()
    # cable: from the strain relief up off the top edge; slack = sideways bow
    ex, ey = cr.device_to_user(*relief_top)     # back to user space (cached layers draw translated)
    if ey > cable_top:
        dirn = -1 if cx < 160 else 1        # slack bows outward, away from the labels
        b = 18 * s * bow * dirn
        cr.save()
        cr.move_to(ex, ey)
        cr.curve_to(ex + b * 0.3, ey - (ey - cable_top) * 0.4, cx + b, cable_top + (ey - cable_top) * 0.35, cx + b * 0.4, cable_top)
        cr.set_line_cap(1)
        cr.set_line_width(7 * s)
        cv.src(0x000000)
        cr.stroke_preserve()
        cr.set_line_width(5.5 * s)
        cv.src(0x2A2D33 if live else 0x24262A)
        cr.stroke_preserve()
        cr.set_line_width(1.2 * s)
        cv.src(0x7A808A if live else 0x45484F)
        cr.stroke()
        cr.restore()
    # hex nut + washer on the panel (drawn over the shaft: the plug is "in" the jack)
    nw, nh = 30 * s, 8 * s
    cv.rect(cx - 12 * s, nut_y + nh, 24 * s, 2 * s, fill=mix(NICKEL, 0x000000, 0.45))
    cv.rrect(cx - nw / 2, nut_y, nw, nh, 1.5 * s, fill=cv.lin_grad(0, nut_y, 0, nut_y + nh, [
        (0, NICKEL), (1, mix(NICKEL, 0x000000, 0.45))]))
    for fx in (-nw / 6, nw / 6):
        cv.rect(cx + fx - 0.5 * s, nut_y + 1, 1 * s, nh - 2, fill=0x000000, alpha=0.3)
    return cx, sock_bot


def jack_pose(kind, p, plugged=True):
    """Animation pose -> (lift, tilt, live, bow, pulse). kind: None (settled), 'in' (plugging), 'out'."""
    OUT = 32.0
    if kind is None:
        return (0.0, 0.0, True, 0.0, 0.0) if plugged else (OUT, 7.0, False, 1.0, 0.0)
    if kind == "in":
        if p < 0.62:
            q = p / 0.62
            return OUT * (1 - q * q), 7 * (1 - q) ** 2, True, 1 - q * 0.5, 0.0
        q = (p - 0.62) / 0.38
        bounce = 2.5 * math.sin(q * math.pi) * (1 - q)                    # snap: tiny rebound
        bow = abs((1 - p) ** 1.5 * math.cos(p * math.pi * 3)) * 0.8        # cable tension settles
        return bounce, 0.0, True, bow, (1 - q) ** 1.5

    def back(t, k):
        t -= 1
        return 1 + t * t * ((k + 1) * t + k)
    return OUT * back(p, 2.0), 7 * back(p, 1.4), False, max(0.0, back(p, 1.2)), 0.0


# ---------------------------------------------------------------- B-style hardware controls
def knob(cv, cx, cy, r, t, ink=INK, scale=True, cap=0x2A2C31, pointer=0xF2E9D8, positions=None):
    """Chunky knurled knob. t: 0..1. positions: n discrete detents (selector) -> dot per detent."""
    a0, a1 = 135, 405
    if scale:
        n = positions or 11
        for i in range(n):
            a = math.radians(a0 + (a1 - a0) * i / max(1, n - 1))
            big = positions or i % 5 == 0
            cv.circle(cx + (r + 7) * math.cos(a), cy + (r + 7) * math.sin(a), 2.2 if big else 1.6, fill=ink, alpha=0.8)
    cv.circle(cx + 1.5, cy + 3, r, fill=0x000000, alpha=0.45)
    cv.circle(cx, cy, r, fill=0x121316)
    n = max(18, int(r * 1.3))
    cr = cv.cr
    cr.save()
    cr.set_line_width(1.1)
    cv.src(0x44474F)
    for i in range(n):
        a = 2 * math.pi * i / n
        cr.move_to(cx + (r - 0.5) * math.cos(a), cy + (r - 0.5) * math.sin(a))
        cr.line_to(cx + (r * 0.84) * math.cos(a), cy + (r * 0.84) * math.sin(a))
    cr.stroke()
    cr.restore()
    rc = r * 0.74
    cv.circle(cx, cy, rc, fill=cv.rad_grad(cx - rc * 0.3, cy - rc * 0.4, 0, rc * 1.6,
                                             [(0, mix(cap, 0xFFFFFF, 0.2)), (1, mix(cap, 0x000000, 0.4))]))
    cv.arc(cx, cy, rc - 1, 200, 290, 0xFFFFFF, 1.2, alpha=0.3)
    a = math.radians(a0 + (a1 - a0) * t)
    cv.line([(cx + rc * 0.2 * math.cos(a), cy + rc * 0.2 * math.sin(a)), (cx + (r - 2) * math.cos(a), cy + (r - 2) * math.sin(a))],
            pointer, max(2.2, r / 9))


def footswitch(cv, cx, cy, r, pressed=False):
    cv.circle(cx, cy + 2, r + 3, fill=0x000000, alpha=0.5)
    cv.circle(cx, cy, r + 3, fill=0x2E3035)
    hi = 0xF4F6F9 if not pressed else 0xC9CDD4
    cv.circle(cx, cy, r, fill=cv.rad_grad(cx - r * 0.3, cy - r * 0.4, 0, r * 1.5, [(0, hi), (0.6, 0xB4B9C2), (1, 0x60656E)]))
    cv.circle(cx, cy, r * 0.62, stroke=0x80858E, lw=1.2)


def toggle(cv, x, y, w, h, on, color, ink=INK):
    """Pill toggle switch (bool params)."""
    cv.rrect(x, y, w, h, h / 2, fill=0x0B0C0E, stroke=0x000000, lw=1.5)
    if on:
        cv.rrect(x + 2, y + 2, w - 4, h - 4, h / 2 - 2, fill=color, alpha=0.9)
    kx = x + w - h / 2 if on else x + h / 2
    cv.circle(kx, y + h / 2, h / 2 - 3, fill=0xF4F6F9 if on else 0x8A8F98)


# ---------------------------------------------------------------- icons
def power_icon(cv, cx, cy, r, color, lw=2.2, alpha=None):
    cr = cv.cr
    cr.save()
    cv.src(color, alpha)
    cr.set_line_width(lw)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.new_sub_path()
    cr.arc(cx, cy, r, math.radians(-60), math.radians(240))
    cr.stroke()
    cr.move_to(cx, cy - r - 1)
    cr.line_to(cx, cy - r * 0.15)
    cr.stroke()
    cr.restore()


def chevron(cv, cx, cy, size, color, direction="left", lw=2.4):
    if direction in ("left", "right"):
        d = -1 if direction == "left" else 1
        cv.line([(cx - d * size * 0.3, cy - size * 0.5), (cx + d * size * 0.3, cy), (cx - d * size * 0.3, cy + size * 0.5)], color, lw)
    else:
        d = -1 if direction == "up" else 1
        cv.line([(cx - size * 0.5, cy - d * size * 0.3), (cx, cy + d * size * 0.3), (cx + size * 0.5, cy - d * size * 0.3)], color, lw)


def gear(cv, cx, cy, r, color):
    cr = cv.cr
    cr.save()
    cv.src(color)
    for i in range(8):
        cr.save()
        cr.translate(cx, cy)
        cr.rotate(i * math.pi / 4)
        cr.rectangle(-r * 0.2, -r * 1.12, r * 0.4, r * 0.4)
        cr.fill()
        cr.restore()
    cr.set_line_width(r * 0.38)
    cr.arc(cx, cy, r * 0.72, 0, 2 * math.pi)
    cr.stroke()
    cr.restore()


def tuning_fork(cv, cx, cy, h, color, lw=2.2):
    w = h * 0.34
    cv.line([(cx - w / 2, cy - h / 2), (cx - w / 2, cy), (cx, cy + h * 0.12), (cx + w / 2, cy), (cx + w / 2, cy - h / 2)], color, lw)
    cv.line([(cx, cy + h * 0.12), (cx, cy + h / 2)], color, lw)


def list_icon(cv, cx, cy, s, color, lw=2.0):
    for i, yy in enumerate((-0.32, 0, 0.32)):
        cv.line([(cx - s * 0.45, cy + yy * s), (cx + s * 0.45, cy + yy * s)], color, lw)


def plus(cv, cx, cy, s, color, lw=2.4):
    cv.line([(cx - s / 2, cy), (cx + s / 2, cy)], color, lw)
    cv.line([(cx, cy - s / 2), (cx, cy + s / 2)], color, lw)


def cross(cv, cx, cy, s, color, lw=2.4):
    cv.line([(cx - s / 2, cy - s / 2), (cx + s / 2, cy + s / 2)], color, lw)
    cv.line([(cx + s / 2, cy - s / 2), (cx - s / 2, cy + s / 2)], color, lw)


def check(cv, cx, cy, s, color, lw=2.6):
    cv.line([(cx - s / 2, cy), (cx - s / 8, cy + s * 0.38), (cx + s / 2, cy - s * 0.38)], color, lw)


def reset_icon(cv, cx, cy, r, color, lw=2.2):
    cv.arc(cx, cy, r, -200, 90, color, lw)
    cv.line([(cx - r - 3, cy - 4), (cx - r, cy + 1), (cx - r + 4, cy - 3)], color, lw)


def shuffle_icon(cv, cx, cy, s, color, lw=2.2):
    h = s / 2
    cv.line([(cx - h, cy - h * 0.6), (cx - h * 0.3, cy - h * 0.6), (cx + h * 0.3, cy + h * 0.6), (cx + h, cy + h * 0.6)], color, lw)
    cv.line([(cx - h, cy + h * 0.6), (cx - h * 0.3, cy + h * 0.6), (cx + h * 0.3, cy - h * 0.6), (cx + h, cy - h * 0.6)], color, lw)
    cv.line([(cx + h * 0.65, cy - h), (cx + h, cy - h * 0.6), (cx + h * 0.65, cy - h * 0.2)], color, lw)
    cv.line([(cx + h * 0.65, cy + h * 0.2), (cx + h, cy + h * 0.6), (cx + h * 0.65, cy + h)], color, lw)
