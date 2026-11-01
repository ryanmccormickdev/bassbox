"""The bassbox pedal's bought parts as simplified solids, in frame B (installed pose). Units: mm.

Three dictionaries, each `{name: Part}`:

* `parts()`: the real objects (display, Pi 4, ULTRA++ HAT, jacks, screw heads). The printed parts
  must not intersect them, and (unless listed in `assembly.EXEMPT`) must not touch them.
* `plugs()`: the mated cable plugs, fully inserted and extending outward. The enclosure must not
  touch them either (the openings are sized for the overmolds).
* `keepouts()`: empty-space zones the enclosure has to leave free (dupont wires and their bends,
  the heatsink, jack lugs, the solder tails under the Pi).

Names read `<group>.<thing>` (`pi.usb2`, `lcd.dupont`, `jack_in.body`); the group is everything
before the last dot. Everything is a conservative envelope except where a face meets the box. See
`DESIGN.md` for the layout and `dims.py` for frame B and the shared numbers; numbers this
module had to add are defined below with their source and confidence.

The vendor HAT mesh (`scratch/pedal/3D_UltraMIC4layer_2026-03-18.obj`, RaspiAudio's model, not included, 14 MB) is data
only: `hat_mesh()` parses it as text for the renders and `derive_hat_boxes()` re-derives the boxes
below from it (a test compares them when the file exists), so nothing here needs the file to run.
"""

from __future__ import annotations

import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from build123d import (
    Align,
    Axis,
    Box,
    Compound,
    Cylinder,
    Part,
    Pos,
    RegularPolygon,
    Rot,
    extrude,
    fillet,
)

from cad.gen.pedal import dims as d

# --------------------------------------------------------------------------------------------
# Numbers this module adds to dims.py (source, confidence)
# --------------------------------------------------------------------------------------------
# Display
LCD_TAPE_T = 0.50  # back tape between the PCB front and the LCD (MSP4021 drawing, high)
LCD_BL_T = 2.50  # LCD + backlight, without tape (drawing, high)
LCD_RTP_T = d.LCD_STACK - LCD_TAPE_T - LCD_BL_T  # 1.05: touch glass on top (derived)
LCD_PIN_SQ = 0.64  # header pin cross-section (standard 0.64 mm square pin)
LCD_PIN_FRONT = 1.5  # pin tails above the PCB front (spec: 1.5)
LCD_HDR_PIN_PAST = 8.35  # pins past the PCB back (drawing: 14.00 - 5.65)
# Screws
SCREW_HEAD_D, SCREW_HEAD_H = 4.5, 2.5  # M2.5 socket head (ISO 4762: 4.5 x 2.5; high)
# Pi 4: depths read off raspberry-pi-4-mechanical-drawing.pdf (medium; +-0.2)
PI_IO_DEPTH = {"usb_c": 6.15, "hdmi0": 6.6, "hdmi1": 6.6, "audio": 12.5}  # y_pi of the inner end
PI_AUDIO_BODY_W = 7.0  # the rectangular body of the audio jack (drawing); the barrel is Ø6
PI_END_DEPTH = {"usb2": 70.4, "usb3": 70.4, "eth": 66.6}  # x_pi of the inner end (drawing)
PI_HDR_Y = (49.96, 55.04)  # 2x20 header body (5.08 across, rows at y_pi 51.23 / 53.77)
PI_SOC = (29.25, 32.5, 15.0, 2.4)  # centre x_pi, y_pi, side, height (drawing: read off, medium)
PI_HEATSINK = (
    14.0,
    6.0,
)  # side, height ON TOP of the SoC (spec; the heatsink is unmeasured: LOW)
PI_CSI = ((45.0, 49.1), (0.3, 22.7), 5.5)  # camera connector (x_pi, y_pi, height; drawing, medium)
PI_DSI = ((1.5, 5.5), (16.7, 39.1), 5.5)  # display connector (drawing, medium)
PI_SD_SOCKET = (
    0.0,
    15.0,
    21.5,
    34.5,
)  # underside microSD socket x_pi, y_pi (typical push-push: LOW)
# Jacks (photos and one caliper reading, LOW-MEDIUM)
JACK_WASHER_D, JACK_WASHER_T = d.JACK_NUT_OD, 1.0  # Ø16.4 x 1.0 (spec)
JACK_NUT_AF, JACK_NUT_T = 14.0, 3.2  # hex nut across flats x thickness (spec)
JACK_BUSHING_PAST = 3.0  # spec: bushing sticks 3 past the wall; we run it to the nut face instead
# Plugs (spec: overmold sizes; positions are the port centres)
PLUG_USB_C = {"overmold": (12.0, 6.6, 22.0), "metal": (8.3, 2.5, 6.6)}  # width, height, length
PLUG_HDMI = (10.6, 7.0, 20.0)
PLUG_AUDIO = (7.0, 20.0)  # barrel Ø, length
# USB-C and micro-HDMI overmolds are rounded in real life (the spec gives only W x H x L): their long
# edges get this radius. With 0.0 (sharp boxes) the corners poke 0.13 (USB-C) / 0.06 (HDMI) into the
# enclosure's R1.5 opening corners. The USB-A and RJ45 envelopes stay sharp.
PLUG_CORNER_R = 1.0
PLUG_USB_A = (16.0, 8.0, 25.0)  # two per stack, centred at z_pi 4 and 12
PLUG_RJ45 = (12.0, 14.0, 25.0)

# ULTRA++ HAT, vendor model coordinates: x along the board from the terminal-block end, y from the
# edge away from the header, z up from the component face (PCB z -1.72..0). Derived from the mesh's
# per-material connected components (high; `derive_hat_boxes()` re-derives them).
HAT_BOXES: dict[str, tuple[tuple[float, float], tuple[float, float], tuple[float, float]]] = {
    "pcb": ((-0.07, 64.91), (-0.09, 29.88), (-1.72, 0.0)),  # mtl35/36/37
    "terminal": ((-0.48, 6.42), (6.08, 21.23), (0.0, 8.79)),  # mtl2 housings (mtl1 poles inside)
    "terminal_tails": (
        (0.82, 4.82),
        (6.93, 20.37),
        (-3.40, -1.72),
    ),  # mtl1 pin tails under the board
    "speaker_a": ((6.66, 27.66), (-1.17, 21.83), (-4.75, 4.75)),  # mtl3
    "speaker_b": ((37.77, 58.77), (-1.16, 21.84), (-4.75, 4.75)),
    "jack35_a": ((7.41, 15.71), (-3.07, 10.93), (-6.43, -1.12)),  # mtl25 + mtl27, bottom side
    "jack35_b": ((28.85, 37.15), (-3.07, 10.93), (-6.43, -1.12)),
    "jack35_c": ((49.70, 58.00), (-3.19, 10.93), (-6.43, -1.12)),
    "button": ((29.79, 35.79), (6.78, 12.78), (-0.05, 4.85)),  # mtl11
    "header_plastic": ((6.96, 57.56), (23.92, 28.92), (0.51, 3.01)),  # mtl16
    "pins": ((7.81, 56.71), (24.83, 28.01), (3.01, 8.96)),  # mtl17 pass-through pins
    "female": ((6.66, 57.86), (24.02, 29.10), (-9.11, -2.21)),  # mtl18, underneath
    # bottom-side parts: SMD between y 1.4 and 22.6 (mtl20/21/22 ...), and the female header's pin
    # tails under the header (mtl19). Two boxes, not one: a single union box would cover the
    # spacer at the HAT's second hole, where nothing is.
    "bottom_smd": ((5.91, 60.98), (1.40, 22.57), (-3.02, -1.72)),
    "bottom_pins": ((7.83, 56.69), (23.03, 30.06), (-2.21, -1.72)),
    # top-face SMD next to the two mounting holes (the posts' neighbours): mtl4
    "smd_hole_a": ((1.56, 4.83), (21.40, 23.87), (0.39, 1.11)),
    "smd_hole_b": ((60.81, 64.08), (20.80, 23.27), (0.39, 1.11)),
}
HAT_HOLE_D = 2.75  # spec
HAT_PIN_PITCH = 2.54
HAT_PIN_COLS_X0 = 8.13  # x of pin column 0's centre (mtl17: 7.81..8.45)
HAT_PIN_ROWS_Y = (25.15, 27.69)  # y of the two rows' centres
HAT_DUPONT_BASE_Z = 3.01  # housings stand on the pass-through plastic

_SCRATCH = Path(__file__).resolve().parents[3] / "scratch" / "pedal"
HAT_OBJ_PATH = _SCRATCH / d.HAT_OBJ


# --------------------------------------------------------------------------------------------
# Solid helpers
# --------------------------------------------------------------------------------------------
_MIN = (Align.MIN, Align.MIN, Align.MIN)


def box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Part:
    """Axis-aligned box from its extreme corners (frame B)."""
    return Pos(x0, y0, z0) * Box(x1 - x0, y1 - y0, z1 - z0, align=_MIN)


def _mapped_box(to_b, a: tuple[float, float, float], b: tuple[float, float, float]) -> Part:
    """Box between two opposite corners given in another frame; `to_b` is that frame's helper."""
    pa, pb = to_b(*a), to_b(*b)
    return box(
        min(pa[0], pb[0]),
        max(pa[0], pb[0]),
        min(pa[1], pb[1]),
        max(pa[1], pb[1]),
        min(pa[2], pb[2]),
        max(pa[2], pb[2]),
    )


def pi_box(xs: tuple[float, float], ys: tuple[float, float], zs: tuple[float, float]) -> Part:
    return _mapped_box(d.pi_to_b, (xs[0], ys[0], zs[0]), (xs[1], ys[1], zs[1]))


def hat_box(xs: tuple[float, float], ys: tuple[float, float], zs: tuple[float, float]) -> Part:
    return _mapped_box(d.hat_to_b, (xs[0], ys[0], zs[0]), (xs[1], ys[1], zs[1]))


def lcd_box(xs: tuple[float, float], ys: tuple[float, float], zs: tuple[float, float]) -> Part:
    """Box from display front-view ranges (x_f, y_t, z_f)."""
    return _mapped_box(d.lcd_to_b, (xs[0], ys[0], zs[0]), (xs[1], ys[1], zs[1]))


def cyl_z(x: float, y: float, z0: float, z1: float, dia: float) -> Part:
    """Vertical cylinder of diameter `dia` from z0 up to z1."""
    return Pos(x, y, z0) * Cylinder(dia / 2, z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN))


def cyl_y(x: float, z: float, y0: float, y1: float, dia: float) -> Part:
    """Cylinder along Y of diameter `dia` from y0 to y1, axis through (x, z)."""
    return Pos(x, (y0 + y1) / 2, z) * (Rot(X=90) * Cylinder(dia / 2, y1 - y0))


def _union(*solids: Part) -> Part:
    out = solids[0]
    for s in solids[1:]:
        out = out + s
    return out if isinstance(out, Part) else Part(out)


def _label(p: Part, name: str) -> Part:
    p.label = name
    return p


def group_of(name: str) -> str:
    """`pi.usb2` -> `pi`; `plug.usb_c.metal` -> `plug.usb_c`."""
    return name.rsplit(".", 1)[0] if "." in name else name


# --------------------------------------------------------------------------------------------
# Display (lcdwiki MSP4021)
# --------------------------------------------------------------------------------------------
_LCD_W, _LCD_L = d.LCD_PCB_W, d.LCD_PCB_L
_LCD_W_MID = _LCD_W / 2


def lcd_pin_xy(i: int) -> tuple[float, float]:
    """Header pin i (0 = pin 1 .. 13) as display front-view (x_f, y_t)."""
    return (d.LCD_HDR_PIN1_X_F + i * d.DUPONT_PITCH, _LCD_L - d.LCD_HDR_Y_B)


def lcd_hole_xy() -> list[tuple[float, float]]:
    """The four mounting holes (x_f, y_t); the first is the locating-pin corner."""
    xs = (d.LCD_HOLE_X_F, d.LCD_HOLE_X_F + d.LCD_HOLE_PITCH_X)
    ys = (d.LCD_HOLE_Y_T, d.LCD_HOLE_Y_T + d.LCD_HOLE_PITCH_Y)
    return [(xs[0], ys[0]), (xs[1], ys[0]), (xs[0], ys[1]), (xs[1], ys[1])]


def lcd_rect(xs: tuple[float, float], ys: tuple[float, float]) -> tuple[float, float, float, float]:
    """A display front-view rectangle (x_f range, y_t range) as frame-B (X0, X1, Y0, Y1)."""
    a, b = d.lcd_to_b(xs[0], ys[0]), d.lcd_to_b(xs[1], ys[1])
    return (min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]))


def _centered(width: float) -> tuple[float, float]:
    return (_LCD_W_MID - width / 2, _LCD_W_MID + width / 2)


def lcd_active_rect() -> tuple[float, float, float, float]:
    """The LCD's active (pixel) area in frame B: X0, X1, Y0, Y1."""
    return lcd_rect(_centered(d.LCD_AA_W), (d.LCD_AA_Y_T, d.LCD_AA_Y_T + d.LCD_AA_L))


def screen_window_rect() -> tuple[float, float, float, float]:
    """The bezel window in frame B: the active area plus WINDOW_MARGIN on every side."""
    x0, x1, y0, y1 = lcd_active_rect()
    m = d.WINDOW_MARGIN
    return (x0 - m, x1 + m, y0 - m, y1 + m)


def glass_rect() -> tuple[float, float, float, float]:
    """The touch glass outline in frame B: X0, X1, Y0, Y1."""
    return lcd_rect(_centered(d.RTP_OD_W), (d.RTP_OD_Y_T, d.RTP_OD_Y_T + d.RTP_OD_L))


def _lcd_parts() -> dict[str, Part]:
    out: dict[str, Part] = {}
    pcb = lcd_box((0, _LCD_W), (0, _LCD_L), (-d.LCD_PCB_T, 0.0))
    for xf, yt in lcd_hole_xy():
        x, y, _ = d.lcd_to_b(xf, yt)
        pcb = pcb - cyl_z(x, y, d.Z_LCD_BACK - 1, d.Z_LCD_FRONT + 1, d.LCD_HOLE_D)
    out["lcd.pcb"] = pcb
    out["lcd.panel"] = lcd_box(
        _centered(d.LCD_BL_W),
        (d.LCD_BL_Y_T, d.LCD_BL_Y_T + d.LCD_BL_L),
        (0.0, LCD_TAPE_T + LCD_BL_T),
    )
    out["lcd.glass"] = lcd_box(
        _centered(d.RTP_OD_W),
        (d.RTP_OD_Y_T, d.RTP_OD_Y_T + d.RTP_OD_L),
        (LCD_TAPE_T + LCD_BL_T, d.LCD_STACK),
    )
    # 14-pin header on the back: plastic, then a dupont housing on every pin (2.6 square, 14 long;
    # neighbours overlap by 0.06 at the 2.54 pitch, so the row is one block).
    _, yt = lcd_pin_xy(0)
    xa, xb = d.LCD_HDR_PIN1_X_F, lcd_pin_xy(d.LCD_HDR_N - 1)[0]
    half = d.DUPONT_PITCH / 2
    out["lcd.header"] = lcd_box(
        (xa - half, xb + half),
        (yt - half, yt + half),
        (-d.LCD_PCB_T - d.LCD_HDR_PLASTIC, -d.LCD_PCB_T),
    )
    hw = 2.6 / 2
    z_top = -d.LCD_PCB_T - d.LCD_HDR_PLASTIC
    out["lcd.dupont"] = lcd_box(
        (xa - hw, xb + hw), (yt - hw, yt + hw), (z_top - d.DUPONT_HOUSING, z_top)
    )
    # pin tails on the front side
    p = LCD_PIN_SQ / 2
    out["lcd.tails"] = lcd_box((xa - p, xb + p), (yt - p, yt + p), (0.0, LCD_PIN_FRONT))
    # back-side SMD cluster and microSD socket
    y_b0, y_b1 = d.LCD_SMD_Y_B
    out["lcd.smd"] = lcd_box(
        d.LCD_SMD_X_F, (_LCD_L - y_b1, _LCD_L - y_b0), (-d.LCD_PCB_T - d.LCD_SMD_MAX, -d.LCD_PCB_T)
    )
    out["lcd.sd"] = lcd_box(d.LCD_SD_X_F, d.LCD_SD_Y_T, (-d.LCD_PCB_T - d.LCD_SD_H, -d.LCD_PCB_T))
    return out


# --------------------------------------------------------------------------------------------
# Raspberry Pi 4
# --------------------------------------------------------------------------------------------
def _pi_port_boxes() -> dict[str, Part]:
    out: dict[str, Part] = {}
    for name, (xc, w, h, over) in d.PI_IO_PORTS.items():
        if name == "audio":
            hw = PI_AUDIO_BODY_W / 2
            body = pi_box((xc - hw, xc + hw), (0.0, PI_IO_DEPTH[name]), (0.0, h))
            xb, _, zb = d.pi_to_b(xc, 0.0, h / 2)
            y0, y1 = d.PI_EDGE_GAP - over, d.PI_EDGE_GAP
            # barrel Ø6 on the axis 3.0 above the board face
            out["pi.audio"] = body + cyl_y(xb, zb, y0, y1 + 0.01, w)
        else:
            out[f"pi.{name}"] = pi_box(
                (xc - w / 2, xc + w / 2), (-over, PI_IO_DEPTH[name]), (0.0, h)
            )
    for name, (yc, w, h, over) in d.PI_END_PORTS.items():
        out[f"pi.{name}"] = pi_box(
            (PI_END_DEPTH[name], d.PI_L + over), (yc - w / 2, yc + w / 2), (0.0, h)
        )
    return out


def _pi_parts() -> dict[str, Part]:
    out: dict[str, Part] = {}
    pcb = box(
        d.PI_EDGE_GAP,
        d.PI_EDGE_GAP + d.PI_L,
        d.PI_EDGE_GAP,
        d.PI_EDGE_GAP + d.PI_W,
        d.PI_TOP_Z,
        d.PI_TOP_Z + d.PI_T,
    )
    pcb = fillet(pcb.edges().filter_by(Axis.Z), d.PI_CORNER_R)
    for xp, yp in d.PI_HOLES:
        x, y, _ = d.pi_to_b(xp, yp)
        pcb = pcb - cyl_z(x, y, d.PI_TOP_Z - 1, d.PI_TOP_Z + d.PI_T + 1, d.PI_HOLE_D)
    out["pi.pcb"] = pcb
    out.update(_pi_port_boxes())
    out["pi.header"] = pi_box(d.PI_HDR_X, PI_HDR_Y, (0.0, 8.5))
    sx, sy, side, sh = PI_SOC
    out["pi.soc"] = pi_box(
        (sx - side / 2, sx + side / 2), (sy - side / 2, sy + side / 2), (0.0, sh)
    )
    out["pi.csi"] = pi_box(PI_CSI[0], PI_CSI[1], (0.0, PI_CSI[2]))
    out["pi.dsi"] = pi_box(PI_DSI[0], PI_DSI[1], (0.0, PI_DSI[2]))
    # microSD card, bottom side, protruding past x_pi = 0 (the socket itself is in pi.bottom_tails)
    y0, y1, prot = d.PI_SD_CARD
    out["pi.sd_card"] = pi_box((-prot, 0.0), (y0, y1), (-d.PI_T - d.PI_BOTTOM_TAILS, -d.PI_T))
    return out


def _pi_bottom_tails() -> Part:
    """Solder tails and the microSD socket under the Pi: a 1.6 slab under each through-hole part."""
    z = (-d.PI_T - d.PI_BOTTOM_TAILS, -d.PI_T)
    slabs = [pi_box(d.PI_HDR_X, PI_HDR_Y, z)]
    for name, (xc, w, _h, _over) in d.PI_IO_PORTS.items():  # only under the board, not the overhang
        hw = (PI_AUDIO_BODY_W if name == "audio" else w) / 2
        slabs.append(pi_box((xc - hw, xc + hw), (0.0, PI_IO_DEPTH[name]), z))
    for name, (yc, w, _h, _over) in d.PI_END_PORTS.items():
        slabs.append(pi_box((PI_END_DEPTH[name], d.PI_L), (yc - w / 2, yc + w / 2), z))
    sx0, sx1, sy0, sy1 = PI_SD_SOCKET
    slabs.append(pi_box((sx0, sx1), (sy0, sy1), z))
    return Part(Compound(slabs))


def _pi_heatsink() -> Part:
    sx, sy, _side, sh = PI_SOC
    hs, hh = PI_HEATSINK
    return pi_box((sx - hs / 2, sx + hs / 2), (sy - hs / 2, sy + hs / 2), (sh, sh + hh))


# --------------------------------------------------------------------------------------------
# ULTRA++ HAT
# --------------------------------------------------------------------------------------------
def _hat_dupont_block() -> Part:
    """All 40 housings (2.6 square, 14 long) over the pass-through pins, fused: one block."""
    x0 = HAT_PIN_COLS_X0 - 1.3
    x1 = HAT_PIN_COLS_X0 + 19 * HAT_PIN_PITCH + 1.3
    y0, y1 = HAT_PIN_ROWS_Y[0] - 1.3, HAT_PIN_ROWS_Y[1] + 1.3
    return hat_box((x0, x1), (y0, y1), (HAT_DUPONT_BASE_Z, HAT_DUPONT_BASE_Z + d.DUPONT_HOUSING))


def _hat_parts() -> dict[str, Part]:
    out: dict[str, Part] = {}
    for name, (xs, ys, zs) in HAT_BOXES.items():
        out[f"hat.{name}"] = hat_box(xs, ys, zs)
    # the board is the contract's 1.70 thick (dims.HAT_T; the mesh says 1.72): the underside is then
    # exactly 24.19, where the spacers land. Parts on the underside hang from that face.
    pcb = hat_box(HAT_BOXES["pcb"][0], HAT_BOXES["pcb"][1], (-d.HAT_T, 0.0))
    for name in ("bottom_smd", "bottom_pins"):
        xs, ys, zs = HAT_BOXES[name]
        out[f"hat.{name}"] = hat_box(xs, ys, (zs[0], -d.HAT_T))
    for xh, yh in d.HAT_HOLES:
        x, y, _ = d.hat_to_b(xh, yh)
        pcb = pcb - cyl_z(x, y, d.Z_HAT_TOP - 2, d.PI_TOP_Z - d.HAT_BOTTOM_ZPI + 2, HAT_HOLE_D)
    out["hat.pcb"] = pcb
    out["hat.dupont"] = _hat_dupont_block()
    return out


def _hat_dupont_wires() -> Part:
    """Under the HAT housings' ends, down to the floor: the wires turn here (5.5 mm of room)."""
    blk = _hat_dupont_block().bounding_box()
    return box(blk.min.X, blk.max.X, blk.min.Y, blk.max.Y, 0.0, blk.min.Z)


# --------------------------------------------------------------------------------------------
# Jacks, screws
# --------------------------------------------------------------------------------------------
def _jack_y(side: str, t0: float, t1: float) -> tuple[float, float]:
    """Y range of a slab from t0 to t1 measured from the wall's inner face along the jack axis
    (positive = into the cavity): the IN jack sits on Y = 0, the OUT jack on Y = INNER_W."""
    if side == "in":
        return (min(t0, t1), max(t0, t1))
    return (d.INNER_W - max(t0, t1), d.INNER_W - min(t0, t1))


def _jack(side: str) -> tuple[dict[str, Part], Part]:
    """(solids, lug keep-out) of one jack; `side` is 'in' (IO wall, exits -Y) or 'out' (+Y wall)."""
    n = f"jack_{side}"
    x, z = d.JACK_X, d.JACK_Z
    hw, hh = d.JACK_BODY_W / 2, d.JACK_BODY_H / 2
    y0, y1 = _jack_y(side, 0.0, d.JACK_BODY_L)
    body = box(x - hw, x + hw, y0, y1, z - hh, z + hh)
    face = d.WALL - d.JACK_SPOTFACE_DEPTH  # the spot face's floor is 1.6 outside the inner face
    y_wash = _jack_y(side, -face, -face - JACK_WASHER_T)
    y_nut = _jack_y(side, -face - JACK_WASHER_T, -face - JACK_WASHER_T - JACK_NUT_T)
    y_bush = _jack_y(side, 0.0, -(face + JACK_WASHER_T + JACK_NUT_T))
    nut = extrude(
        RegularPolygon(radius=JACK_NUT_AF / math.sqrt(3), side_count=6), amount=JACK_NUT_T
    )
    nut = Pos(x, (y_nut[0] + y_nut[1]) / 2, z) * (Rot(X=90) * Pos(0, 0, -JACK_NUT_T / 2) * nut)
    solids = {
        f"{n}.body": body,
        f"{n}.bushing": cyl_y(x, z, y_bush[0], y_bush[1], d.JACK_BUSHING_D),
        f"{n}.washer": cyl_y(x, z, y_wash[0], y_wash[1], JACK_WASHER_D),
        f"{n}.nut": nut,
    }
    lug_y = _jack_y(side, 0.0, d.JACK_BODY_L)
    lugs = box(x - hw, x + hw, lug_y[0], lug_y[1], z - hh - d.JACK_LUG_DROP, z - hh)
    return solids, lugs


def screw_head(x: float, y: float, z_under: float, up: bool) -> Part:
    """An M2.5 socket head standing on a PCB face at `z_under`; `up` = toward +Z."""
    z0, z1 = (z_under, z_under + SCREW_HEAD_H) if up else (z_under - SCREW_HEAD_H, z_under)
    return cyl_z(x, y, z0, z1, SCREW_HEAD_D)


def _screws() -> dict[str, Part]:
    out: dict[str, Part] = {}
    # Pi: the two IO-side holes (y_pi 3.5), on the Pi's underside (faces up in B)
    for i, (xp, yp) in enumerate([h for h in d.PI_HOLES if h[1] < d.PI_W / 2], start=1):
        x, y, _ = d.pi_to_b(xp, yp)
        out[f"screw.pi_io{i}"] = screw_head(x, y, d.PI_TOP_Z + d.PI_T, up=True)
    # display: the three screwed holes, heads under the back face
    for i, (xf, yt) in enumerate(lcd_hole_xy()[1:], start=1):
        x, y, _ = d.lcd_to_b(xf, yt)
        out[f"screw.lcd{i}"] = screw_head(x, y, d.Z_LCD_BACK, up=False)
    return out


# --------------------------------------------------------------------------------------------
# Plugs
# --------------------------------------------------------------------------------------------
def _round_long_edges(p: Part) -> Part:
    """Round the four edges running along Y (the IO-edge plugs' axis) by PLUG_CORNER_R."""
    if PLUG_CORNER_R <= 0:
        return p
    return fillet(p.edges().filter_by(Axis.Y), PLUG_CORNER_R)


def _plugs() -> dict[str, Part]:
    out: dict[str, Part] = {}
    # IO edge (y_pi = 0): plugs extend toward -y_pi from the port face at y_pi = -overhang
    xc, _w, h, over = d.PI_IO_PORTS["usb_c"]
    ow, oh, ol = PLUG_USB_C["overmold"]
    mw, mh, ml = PLUG_USB_C["metal"]
    out["plug.usb_c.overmold"] = _round_long_edges(
        pi_box((xc - ow / 2, xc + ow / 2), (-over - ol, -over), (h / 2 - oh / 2, h / 2 + oh / 2))
    )
    out["plug.usb_c.metal"] = pi_box(
        (xc - mw / 2, xc + mw / 2), (-over, -over + ml), (h / 2 - mh / 2, h / 2 + mh / 2)
    )
    for name in ("hdmi0", "hdmi1"):
        xc, _w, h, over = d.PI_IO_PORTS[name]
        pw, ph, pl = PLUG_HDMI
        out[f"plug.{name}.overmold"] = _round_long_edges(
            pi_box(
                (xc - pw / 2, xc + pw / 2), (-over - pl, -over), (h / 2 - ph / 2, h / 2 + ph / 2)
            )
        )
    xc, _w, h, over = d.PI_IO_PORTS["audio"]
    pdia, pl = PLUG_AUDIO
    xb, _, zb = d.pi_to_b(xc, 0.0, h / 2)
    out["plug.audio.overmold"] = cyl_y(
        xb, zb, d.PI_EDGE_GAP - over - pl, d.PI_EDGE_GAP - over, pdia
    )
    # USB end (x_pi = 85): plugs extend toward +x_pi from the port face at x_pi = 85 + overhang
    for name in ("usb2", "usb3"):
        yc, _w, _h, over = d.PI_END_PORTS[name]
        pw, ph, pl = PLUG_USB_A
        for tag, zc in (("a", 4.0), ("b", 12.0)):
            out[f"plug.{name}{tag}.overmold"] = pi_box(
                (d.PI_L + over, d.PI_L + over + pl),
                (yc - pw / 2, yc + pw / 2),
                (zc - ph / 2, zc + ph / 2),
            )
    yc, _w, h, over = d.PI_END_PORTS["eth"]
    pw, ph, pl = PLUG_RJ45
    out["plug.eth.overmold"] = pi_box(
        (d.PI_L + over, d.PI_L + over + pl),
        (yc - pw / 2, yc + pw / 2),
        (h / 2 - ph / 2, h / 2 + ph / 2),
    )
    return out


# --------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------
def parts() -> dict[str, Part]:
    """The bought parts in frame B (installed pose), `{name: solid}`."""
    out: dict[str, Part] = {}
    out.update(_lcd_parts())
    out.update(_pi_parts())
    out.update(_hat_parts())
    for side in ("in", "out"):
        out.update(_jack(side)[0])
    out.update(_screws())
    for name, p in out.items():
        _label(p, name)
    return out


def plugs() -> dict[str, Part]:
    """Mated cable plugs, fully inserted in their ports and extending outward."""
    out = _plugs()
    for name, p in out.items():
        _label(p, name)
    return out


def keepouts() -> dict[str, Part]:
    """Empty-space zones the printed parts must leave free (frame B)."""
    out: dict[str, Part] = {}
    # Display wires: from the housings' ends down to the floor under the header footprint.
    hb = _lcd_parts()["lcd.dupont"].bounding_box()
    out["lcd.dupont_wires"] = box(hb.min.X, hb.max.X, hb.min.Y, hb.max.Y, 0.0, hb.min.Z)
    out["hat.dupont_wires"] = _hat_dupont_wires()
    # ... and between the two jack bodies, down along the jack-end wall to the floor.
    gap_y = (d.JACK_BODY_L, d.INNER_W - d.JACK_BODY_L)
    bay = d.JACK_BODY_W / 2 + 0.75  # the jack bay, X 86.2..108.4 (DESIGN.md)
    out["wires.jack_gap"] = box(d.JACK_X - bay, d.JACK_X + bay, gap_y[0], gap_y[1], 8.0, 34.0)
    for side in ("in", "out"):
        out[f"jack_{side}.lugs"] = _jack(side)[1]
    out["pi.heatsink"] = _pi_heatsink()
    out["pi.bottom_tails"] = _pi_bottom_tails()
    for name, p in out.items():
        _label(p, name)
    return out


# --------------------------------------------------------------------------------------------
# The vendor mesh (render data; also the source of HAT_BOXES)
# --------------------------------------------------------------------------------------------
def read_obj(path: Path = HAT_OBJ_PATH):
    """Parse a Wavefront OBJ as text: (vertices (n, 3), {material: faces (m, 3) vertex indices}).

    Only `v`, `usemtl` and `f` lines are read; the file is data, nothing in it is ever executed.
    """
    import numpy as np

    verts: list[tuple[float, float, float]] = []
    faces: dict[str, list[tuple[int, int, int]]] = defaultdict(list)
    cur = "none"
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("v "):
                _, x, y, z = line.split()[:4]
                verts.append((float(x), float(y), float(z)))
            elif line.startswith("usemtl"):
                cur = line.split()[1]
            elif line.startswith("f "):
                idx = [int(t.split("/")[0]) - 1 for t in line.split()[1:]]
                for k in range(1, len(idx) - 1):  # fan-triangulate quads and up
                    faces[cur].append((idx[0], idx[k], idx[k + 1]))
    return np.array(verts), {m: np.array(f) for m, f in faces.items()}


def derive_hat_boxes(path: Path = HAT_OBJ_PATH) -> dict[str, tuple[tuple[float, float], ...]]:
    """Re-derive the checkable entries of `HAT_BOXES` from the mesh (bounding boxes per material
    group); the test compares them with the constants to 0.02."""
    import numpy as np

    v, f = read_obj(path)

    def bbox(mats: list[str]):
        ids = np.unique(np.concatenate([f[m].ravel() for m in mats]))
        p = v[ids]
        return tuple(
            (round(float(lo), 2), round(float(hi), 2)) for lo, hi in zip(p.min(0), p.max(0))
        )

    return {
        "pcb": bbox(["mtl35", "mtl36", "mtl37"]),
        "terminal": bbox(["mtl2"]),
        "header_plastic": bbox(["mtl16"]),
        "female": bbox(["mtl18"]),
        "button": bbox(["mtl11"]),
    }


@lru_cache(maxsize=2)
def hat_mesh(path: Path = HAT_OBJ_PATH):
    """The vendor mesh mapped to frame B as a `trimesh.Trimesh` with `face_materials` (an array of
    material numbers per face), or None when the file is absent (it is gitignored). Cached: treat it
    as read-only."""
    import numpy as np
    import trimesh

    if not path.exists():
        return None
    v, faces = read_obj(path)
    o = np.array(d.hat_to_b(0, 0, 0))
    m = np.array([np.array(d.hat_to_b(*e)) - o for e in ((1, 0, 0), (0, 1, 0), (0, 0, 1))]).T
    vb = v @ m.T + o
    tri, mats = [], []
    for name, f in faces.items():
        tri.append(f)
        mats.append(np.full(len(f), int(name[3:]) if name[3:].isdigit() else 0))
    mesh = trimesh.Trimesh(vertices=vb, faces=np.concatenate(tri), process=False)
    mesh.face_materials = np.concatenate(mats)
    return mesh


def export_hat_mesh(out: Path | None = None) -> Path | None:
    """Write the mapped vendor mesh as STL (default `scratch/pedal/hat_in_b.stl`, gitignored)."""
    mesh = hat_mesh()
    if mesh is None:
        return None
    out = out or _SCRATCH / "hat_in_b.stl"
    out.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(out)
    return out
