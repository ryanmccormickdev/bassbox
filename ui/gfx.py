"""bassbox UI rendering core: cairo vector shapes + Pillow/FreeType text + RGB565 output.

Everything draws into one cairo RGB24 ImageSurface (XRGB8888, little-endian uint32 per pixel).
Text is shaped and rasterised by Pillow (FreeType, + raqm when present for kerning and OpenType
features such as tabular figures), cached as A8 masks, and composited with cairo. Using the same
rasteriser on the Mac and the Pi keeps headless PNGs pixel-identical (modulo FreeType version).

    from gfx import Canvas, font
    cv = Canvas(320, 480)
    cv.fill(0x0B0C10)
    cv.rrect(12, 12, 100, 44, 10, fill=0x1A1D24)
    cv.text("Hello", 20, 40, font("inter", 17, 600), 0xFFFFFF)
    cv.save_png("out.png", panel=True)   # panel=True: dither + quantise to RGB565 like the LCD

Colours are 0xRRGGBB ints or (r, g, b[, a]) floats 0..1.
"""
import math
import os

import cairo
import numpy as np
from PIL import Image, ImageDraw, ImageFont, features

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")

# family -> (file prefix, {suffix: weight}) of the static instances built by tools/make_fonts.py
_FAMILIES = {
    "inter": "Inter", "mono": "JetBrainsMono", "grotesk": "SpaceGrotesk", "archivo": "Archivo", "unbounded": "Unbounded",
}
# raqm (HarfBuzz) positions glyphs unhinted while FreeType renders hinted advances, which gives
# uneven gaps ("att ack") at most weights. BASIC layout spaces consistently; values use a mono font.
_RAQM = os.environ.get("BASSBOX_RAQM") == "1" and features.check("raqm")
from collections import OrderedDict

_fonts = {}
_masks = OrderedDict()          # LRU of rendered text masks
_MASK_CACHE_MAX = 512
_MASK_BYTES_MAX = 2 << 20
_mask_bytes = [0]
_static = {}


def _instances(prefix):
    if prefix not in _static:
        d = os.path.join(FONT_DIR, "static")
        out = []
        for fn in os.listdir(d):
            if fn.startswith(prefix + "-") and fn.endswith(".ttf"):
                suf = fn[len(prefix) + 1:-4]
                w = int("".join(ch for ch in suf if ch.isdigit()))
                out.append((w, suf.replace(str(w), ""), os.path.join(d, fn)))
        _static[prefix] = out
    return _static[prefix]


def font(family, size, weight=400, width=100):
    """Nearest static instance. inter: >= 20 px uses the Display optical size. archivo: width>110 -> Expanded."""
    key = (family, size, weight, width)
    f = _fonts.get(key)
    if f is None:
        prefix = _FAMILIES[family]
        tag = ""
        if family == "inter" and size >= 20:
            tag = "d"
        if family == "archivo" and width > 110:
            tag = "x"
        if family == "archivo" and width < 90:
            tag = "c"
        cands = [c for c in _instances(prefix) if c[1] == tag] or _instances(prefix)
        _w, _t, path = min(cands, key=lambda c: abs(c[0] - weight))
        layout = ImageFont.Layout.RAQM if _RAQM else ImageFont.Layout.BASIC
        f = ImageFont.truetype(path, size, layout_engine=layout)
        f.bb_key = key
        _fonts[key] = f
    return f


def rgba(c, a=None):
    """0xRRGGBB | (r,g,b) | (r,g,b,a) -> (r,g,b,a) floats."""
    if isinstance(c, int):
        r, g, b = ((c >> 16) & 255) / 255, ((c >> 8) & 255) / 255, (c & 255) / 255
        return (r, g, b, 1.0 if a is None else a)
    if len(c) == 3:
        return (*c, 1.0 if a is None else a)
    return c if a is None else (c[0], c[1], c[2], a)


def mix(c1, c2, t):
    """Blend two 0xRRGGBB colours, t=0 -> c1, t=1 -> c2. Returns 0xRRGGBB."""
    out = 0
    for sh in (16, 8, 0):
        a, b = (c1 >> sh) & 255, (c2 >> sh) & 255
        out |= int(round(a + (b - a) * t)) << sh
    return out


def _text_mask(text, f, features_, tracking):
    key = (text, f.bb_key, tuple(features_ or ()), tracking)
    m = _masks.get(key)
    if m is not None:
        _masks.move_to_end(key)
        return m
    kw = {"features": list(features_)} if (features_ and _RAQM) else {}
    if tracking:
        # per-glyph with extra spacing (used for small-caps labels; kerning is irrelevant there)
        adv = [f.getlength(ch, **kw) + tracking for ch in text]
        width = sum(adv) - tracking
    else:
        adv = None
        width = f.getlength(text, **kw)
    asc, desc = f.getmetrics()
    pad = 2
    w = int(math.ceil(width)) + 2 * pad + 2
    h = asc + desc + 2 * pad
    im = Image.new("L", (max(1, w), max(1, h)), 0)
    d = ImageDraw.Draw(im)
    if adv is None:
        d.text((pad, pad + asc), text, font=f, fill=255, anchor="ls", **kw)
    else:
        x = pad
        for ch, a in zip(text, adv):
            d.text((x, pad + asc), ch, font=f, fill=255, anchor="ls", **kw)
            x += a
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, im.width)
    buf = np.zeros((im.height, stride), np.uint8)
    buf[:, : im.width] = np.asarray(im)
    surf = cairo.ImageSurface.create_for_data(buf, cairo.FORMAT_A8, im.width, im.height, stride)
    m = (surf, buf, pad, pad + asc, width)
    _masks[key] = m
    _mask_bytes[0] += buf.nbytes
    while len(_masks) > _MASK_CACHE_MAX or _mask_bytes[0] > _MASK_BYTES_MAX:
        _k, old = _masks.popitem(last=False)
        _mask_bytes[0] -= old[1].nbytes
    return m


def text_width(text, f, features_=None, tracking=0):
    return _text_mask(text, f, features_, tracking)[4]


class Canvas:
    def __init__(self, w=320, h=480, surface=None):
        self.w, self.h = w, h
        self.surf = surface or cairo.ImageSurface(cairo.FORMAT_RGB24, w, h)
        self.cr = cairo.Context(self.surf)

    # ------------------------------------------------------------ basics
    def src(self, c, a=None):
        self.cr.set_source_rgba(*rgba(c, a))

    def fill(self, c):
        self.cr.save()
        self.src(c)
        self.cr.paint()
        self.cr.restore()

    def rect(self, x, y, w, h, fill=None, stroke=None, lw=1.0, alpha=None):
        cr = self.cr
        cr.rectangle(x, y, w, h)
        self._paint(fill, stroke, lw, alpha)

    def _paint(self, fill, stroke, lw, alpha=None):
        cr = self.cr
        if fill is not None:
            if isinstance(fill, cairo.Pattern):
                cr.set_source(fill)
            else:
                self.src(fill, alpha)
            if stroke is not None:
                cr.fill_preserve()
            else:
                cr.fill()
        if stroke is not None:
            self.src(stroke, alpha if fill is None else None)
            cr.set_line_width(lw)
            cr.stroke()
        if fill is None and stroke is None:
            cr.new_path()

    def rrect_path(self, x, y, w, h, r):
        cr = self.cr
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
        cr.close_path()

    def rrect(self, x, y, w, h, r, fill=None, stroke=None, lw=1.0, alpha=None):
        self.rrect_path(x, y, w, h, r)
        self._paint(fill, stroke, lw, alpha)

    def circle(self, cx, cy, r, fill=None, stroke=None, lw=1.0, alpha=None):
        self.cr.new_sub_path()
        self.cr.arc(cx, cy, r, 0, 2 * math.pi)
        self._paint(fill, stroke, lw, alpha)

    def line(self, pts, c, lw=2.0, cap=cairo.LINE_CAP_ROUND, join=cairo.LINE_JOIN_ROUND, alpha=None, dash=None):
        cr = self.cr
        cr.save()
        cr.move_to(*pts[0])
        for p in pts[1:]:
            cr.line_to(*p)
        cr.set_line_cap(cap)
        cr.set_line_join(join)
        cr.set_line_width(lw)
        if dash:
            cr.set_dash(dash)
        self.src(c, alpha)
        cr.stroke()
        cr.restore()

    def arc(self, cx, cy, r, a0, a1, c, lw=4.0, cap=cairo.LINE_CAP_ROUND, alpha=None):
        """Angles in degrees, 0 = 3 o'clock, clockwise (screen coords)."""
        cr = self.cr
        cr.save()
        cr.new_sub_path()
        cr.arc(cx, cy, r, math.radians(a0), math.radians(a1))
        cr.set_line_cap(cap)
        cr.set_line_width(lw)
        self.src(c, alpha)
        cr.stroke()
        cr.restore()

    def lin_grad(self, x0, y0, x1, y1, stops):
        g = cairo.LinearGradient(x0, y0, x1, y1)
        for off, c, *a in stops:
            g.add_color_stop_rgba(off, *rgba(c, a[0] if a else None))
        return g

    def rad_grad(self, cx, cy, r0, r1, stops):
        g = cairo.RadialGradient(cx, cy, r0, cx, cy, r1)
        for off, c, *a in stops:
            g.add_color_stop_rgba(off, *rgba(c, a[0] if a else None))
        return g

    def glow(self, cx, cy, r, c, a=0.5):
        """Soft radial glow (dithered on output, so no banding)."""
        cr = self.cr
        cr.save()
        cr.set_source(self.rad_grad(cx, cy, 0, r, [(0, c, a), (0.4, c, a * 0.45), (1, c, 0)]))
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.fill()
        cr.restore()

    # ------------------------------------------------------------ text
    def text(self, s, x, y, f, c, align="left", features_=None, tracking=0, alpha=None):
        """Draw text with its baseline at y. align: left | center | right. Returns width."""
        surf, _buf, ox, oy, width = _text_mask(s, f, features_, tracking)
        if align == "center":
            x -= width / 2
        elif align == "right":
            x -= width
        x, y = round(x), round(y)
        self.src(c, alpha)
        self.cr.mask_surface(surf, x - ox, y - oy)
        return width

    def text_fit(self, s, x, y, maxw, family, size, weight, c, minsize=12, width=100, **kw):
        """Shrink font size until s fits maxw."""
        while size > minsize and text_width(s, font(family, size, weight, width)) > maxw:
            size -= 1
        return self.text(s, x, y, font(family, size, weight, width), c, **kw)

    # ------------------------------------------------------------ output
    def rgb888(self):
        self.surf.flush()
        a = np.ndarray((self.h, self.surf.get_stride() // 4), np.uint32, self.surf.get_data())[:, : self.w]
        return a

    def save_png(self, path, panel=True):
        img = to_panel_rgb(self.rgb888()) if panel else xrgb_to_rgb(self.rgb888())
        Image.fromarray(img, "RGB").save(path)


# ------------------------------------------------------------------ RGB565
# 4x4 Bayer ordered dither, position-stable (so re-rendering identical content gives identical
# pixels and dirty-rect diffs stay small). Thresholds scaled to one LSB of 5-bit / 6-bit channels.
_BAYER = np.array([[0, 8, 2, 10], [12, 4, 14, 6], [3, 11, 1, 9], [15, 7, 13, 5]], np.float32)
_B5 = ((_BAYER + 0.5) / 16 * 8 - 4).astype(np.int16)    # +-4  (one 5-bit LSB = 8)
_B6 = ((_BAYER + 0.5) / 16 * 4 - 2).astype(np.int16)    # +-2  (one 6-bit LSB = 4)


def _tile(b, h, w, y0=0, x0=0):
    reps = ((h + y0) // 4 + 2, (w + x0) // 4 + 2)
    return np.tile(b, reps)[y0 % 4 : y0 % 4 + h, x0 % 4 : x0 % 4 + w]


_FULL = (488, 328)
_B5F = np.tile(_B5, (_FULL[0] // 4, _FULL[1] // 4))
_B6F = np.tile(_B6, (_FULL[0] // 4, _FULL[1] // 4))


def xrgb_to_rgb(a):
    return np.stack([(a >> 16) & 255, (a >> 8) & 255, a & 255], -1).astype(np.uint8)


# LUT path: phase (0..15) x channel value (0..255) -> dithered, shifted 565 bits.
_PHASE = ((np.arange(_FULL[0])[:, None] % 4) * 4 + (np.arange(_FULL[1])[None, :] % 4)).astype(np.uint16) * 256
_v = np.arange(256, dtype=np.int16)
_LUT_R = np.concatenate([(np.clip(_v + _B5.ravel()[k], 0, 255) >> 3).astype(np.uint16) << 11 for k in range(16)])
_LUT_G = np.concatenate([(np.clip(_v + _B6.ravel()[k], 0, 255) >> 2).astype(np.uint16) << 5 for k in range(16)])
_LUT_B = np.concatenate([(np.clip(_v + _B5.ravel()[k], 0, 255) >> 3).astype(np.uint16) for k in range(16)])


def to_rgb565(a, x0=0, y0=0):
    """XRGB8888 uint32 (h, w) -> dithered RGB565 uint16 (h, w). x0/y0: absolute origin for the dither phase."""
    h, w = a.shape
    if y0 + h <= _FULL[0] and x0 + w <= _FULL[1]:
        ph = _PHASE[y0:y0 + h, x0:x0 + w]
        b8 = a.view(np.uint8).reshape(h, w, 4)       # little-endian XRGB: [B, G, R, X]
        out = _LUT_R[ph + b8[..., 2]]
        out |= _LUT_G[ph + b8[..., 1]]
        out |= _LUT_B[ph + b8[..., 0]]
        return out
    return _to_rgb565_slow(a, x0, y0)


def _to_rgb565_slow(a, x0=0, y0=0):
    h, w = a.shape
    if y0 + h <= _FULL[0] and x0 + w <= _FULL[1]:
        b5, b6 = _B5F[y0:y0 + h, x0:x0 + w], _B6F[y0:y0 + h, x0:x0 + w]
    else:
        b5, b6 = _tile(_B5, h, w, y0, x0), _tile(_B6, h, w, y0, x0)
    r = ((a >> 16) & 255).astype(np.int16) + b5
    g = ((a >> 8) & 255).astype(np.int16) + b6
    b = (a & 255).astype(np.int16) + b5
    np.clip(r, 0, 255, out=r)
    np.clip(g, 0, 255, out=g)
    np.clip(b, 0, 255, out=b)
    return ((r.astype(np.uint16) >> 3) << 11) | ((g.astype(np.uint16) >> 2) << 5) | (b.astype(np.uint16) >> 3)


def rgb565_to_rgb(p):
    r = ((p >> 11) & 31).astype(np.uint16)
    g = ((p >> 5) & 63).astype(np.uint16)
    b = (p & 31).astype(np.uint16)
    return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)], -1).astype(np.uint8)


def to_panel_rgb(a):
    """What the LCD will show: dithered RGB565 expanded back to RGB888 (for PNGs/screenshots)."""
    return rgb565_to_rgb(to_rgb565(a))
