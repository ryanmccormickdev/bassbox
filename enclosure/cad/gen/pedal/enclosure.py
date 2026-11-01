"""bassbox pedal enclosure r1: the printed parts (lid, base, spacer) in frame B. Units mm.

Read `DESIGN.md` (concept, stack, decisions) and `cad/gen/pedal/dims.py` (frame B and the
shared numbers) first. `build()` gives every part in **frame B, installed pose**; `print_pose(name,
part)` turns one into its print orientation (lid face down, base floor down, spacer upright; lower-left
corner at the origin). `cad.gen.pedal` (`__main__.py`) exports, renders, slices and checks them.

    base    floor + walls up to the split (Z 34.1): open-top port slots in the USB-end wall, USB-C /
            dual micro-HDMI / audio openings and the recessed IO panel in the IO wall, the two jack
            holes (teardrops, point up) with spot faces and anti-rotation ribs, four floor posts, the
            rabbet lip, **the four snap tabs**, vents (floor grille, +Y wall, jack-end wall), foot rings.
    lid     bezel plate with the screen window, skirt down to the split with the rabbet pocket, the
            ports' and IO panel's share above the split, **the four snap windows**, four bosses (three
            tapped, one locating pin), engraved "bassbox".
    spacer  optional Ø5 / Ø2.8 tube between the Pi and the HAT at a header-side hole (print 2).

Deviations from the first design (each explained where it is built, and in `DESIGN.md`):

1. **The snap tabs are on the base, the windows in the lid's skirt** (DESIGN: tabs on the lid, windows
   in the base). A lid tab hangs inside the wall, so in plan it lies 1.2 inside the display PCB's
   outline (PCB edge 0.2 from the wall, tab 1.4 thick) and below it (Z <= 38.4): the display could not
   be put into the lid past the +Y tabs. A base tab rises inside the wall to Z 38.4, under the PCB's
   edge, and hooks into a window in the lid's skirt; opening is the same (press the barbs in through
   the windows).
2. **One opening for both micro-HDMI plugs** (spec: one per port). Separate openings leave a 1.9 x 1.2
   web between them (2.3 mm^2 section) standing free above the openings' bottoms: the sliver rule.
3. **The audio opening is a U** (semicircle Ø8 below the axis, straight sides up to the circle's top):
   the upper half of a round hole in the base would be an overhang flatter than 50 degrees.
4. The jack teardrops' flanks and the spot faces' rims are at 50 degrees (the spec's overhang limit);
   the spot face is still a flat Ø18 seat 0.4 deep.
5. The HAT posts are Ø4.6 (a review decision; dims 5.0), the spacers 9.85 long (dims 9.9: never longer
   than the gap), the USB-C / HDMI openings' corners R0.8 (square-cornered overmold models clear).
6. The barbs' square retention faces are 0.8 flat ledges: the one overhang left (see SNAP_PROTRUDE).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from build123d import (
    Align,
    Axis,
    Box,
    Circle,
    Cone,
    Cylinder,
    Part,
    Plane,
    Polygon,
    Pos,
    Rectangle,
    RectangleRounded,
    Rot,
    Sketch,
    SlotOverall,
    Torus,
    Vector,
    chamfer,
    extrude,
    fillet,
)

from cad.gen import checks, features
from cad.gen.pedal import dims as D

FAMILY = "pedal-enclosure"
REV = 1
NAMES = ("lid", "base", "spacer")
MIN3 = (Align.MIN, Align.MIN, Align.MIN)

# --- numbers this module adds to dims.py (each: why) ---------------------------------------------
OVERHANG_DEG = 50.0  # spec: no printed slope flatter than this (from horizontal) unless it bridges
TAN_OH = math.tan(math.radians(OVERHANG_DEG))
OVER = 0.5  # cutters and joins overlap solids this far, so no faces coincide

# Openings
OPEN_R = 0.8  # corner radius of the USB-C and dual-HDMI openings: small enough that a square-cornered
#               overmold (components.plugs) clears it (0.3..0.5 margins allow at most 1.19)
SLOT_R = 0.5  # bottom-corner radius of the open-top USB/Ethernet slots (review: 1.0 left 0.15 at the port corners)
IO_PANEL_R = 2.0  # spec: the IO panel's corners
LIP_PANEL_MARGIN = 1.5  # the lip stops this far short of the IO panel: the lid's skirt between its
#                         rabbet pocket and the recess is then 1.35 wide, not a 0.05 sliver

# Floor posts
POST_WEB_W = 2.4  # web from each tall IO post to the IO wall (2 extrusion lines + a bit)
POST_WEB_TOP = 26.0  # web top: 1.1 below the lowest IO opening (audio, Z 27.1)
ROOT_FILLET = 1.0  # fillet round each post's root and along the floor-wall edges ("where cheap")
POST_HDR_D = 4.6  # review, 2026-10-19 (dims: 5.0): the HAT has a top-face SMD part 2.55 from the
#                  hole at X 81.7 (components' hat.smd_hole_a), 0.05 from a Ø5.0 post

# Jack ribs (spec: 1.4 wide, 3 deep, Z 14..SPLIT_Z); their underside slopes at OVERHANG_DEG
JACK_RIB_W, JACK_RIB_D, JACK_RIB_Z0 = 1.4, 3.0, 14.0

# Snaps (see the deviation above): tab on the base, window in the lid's skirt
SNAP_W = 6.0  # tab width along its wall: the jack-end tabs fit between the display's screw heads
#               (Y <= 5.87, >= 56.27) and its header duponts (Y 13.3..48.9) with 0.4..1.0 to spare
SNAP_GAP = 0.4  # tab's outer face to the wall's inner face: a 0.4 slot stays open in the slicer
SNAP_TOP = 38.4  # tab tip (spec: below 38.4; the display PCB's back is at 38.9)
SNAP_LAND = 0.2  # vertical land at the barb's tip
SNAP_LEAD_DEG = 30.0  # spec: lead-in at 30 degrees to the insertion direction
SNAP_RAMP_IN = 0.1  # the ramp ends this far inside the skirt's face: the lid's edge meets the ramp
SNAP_PLAY = 0.1  # seated, the barb's retention face is this far above the window's lower edge
SNAP_WIN_SIDE = 0.3  # lid window wider than the tab, per side
SNAP_WIN_TOP = 0.1  # window top above the tab's tip
SNAP_PROTRUDE = SNAP_GAP + D.SNAP_BARB  # barb from the tab face: it reaches SNAP_BARB into the skirt
# The barb's square retention face is the one overhang the spec's 50 degree rule cannot avoid
# (a horizontal face resisting the lid's pull faces down in either part's print pose): the part
# over the gap slopes at 50 degrees, the SNAP_BARB that hooks the lid is a flat 0.8 ledge, printed
# without support. Bridging it between end gussets was tried: it moves the same
# cantilever into the lid's window and shrinks the hook from 6.0 to 2.4 wide.
PETG_E = 2000.0  # N/mm^2, printed PETG (as head_h6's BLADE_E), for the snap-force estimate only
PETG_MU = 0.4  # PETG on PETG friction, for the force estimate

# Vents
VENT_Z = (12.0, 22.0)  # spec: Z 12..22 in the +Y wall (the jack-end slots use the same band)
VENT_PITCH = 4.0
VENT_SIDE_X = (10.0, 82.0)  # slot centres within X 8..84 (spec) ...
VENT_SNAP_CLEAR = 6.0  # ... none within this of a snap tab's centre (they sit under its window)
VENT_END_Y = (27.0, 31.0, 35.0)  # spec: 3 between the jacks, within Y 25..37
GRILLE_X = (18.0, 74.0)  # floor slot centres (pitch VENT_PITCH): under the Pi/HAT, clear of posts
GRILLE_Y = (12.0, 46.0)  # slot ends: >= 4 from the walls, clear of the posts and the foot rings

# Foot rings (spec: Ø12, 0.4 wide, 0.4 deep, near the corners)
FOOT_D, FOOT_RING_W, FOOT_DEPTH = 12.0, 0.4, 0.4
FOOT_INSET = 8.0  # ring centre from the outer edges

# Bosses
BOSS_WEB_LIFT = 0.3  # boss webs stop this far above the PCB's front face: the boss face is the datum
BOSS_WALL_REACH = 1.0  # a boss closer than this to a wall is merged into it (no thin groove)

# Engraving (spec: 0.4 deep; reads from above, UI top at +X so the text's up is +X)
ENGRAVE_DEPTH = 0.4
LABEL_FONT = 7.0  # "bassbox"

# Plate (Ender-3 V2 Neo bed, X 5..215 x Y 0..220)
PLATE_GAP = 10.0


# --- derived numbers --------------------------------------------------------------------------


@dataclass(frozen=True)
class Snap:
    """The snap's heights (frame B) and its strain and forces (estimates)."""

    z_tip: float
    z_ret: float  # barb's retention face
    z_root: float  # where the tab leaves its corbel (free length SNAP_LEN up to z_ret)
    win_z0: float  # lid window's lower edge (the face the barb hooks over)
    win_z1: float
    strain: float  # bending strain at the root for the full deflection (SNAP_BARB)
    force_n: float  # tip force for that deflection
    insert_n: float  # push to close, per snap
    lead_h: float  # height of the lead-in ramp


def snap() -> Snap:
    lead = math.tan(math.radians(SNAP_LEAD_DEG))
    lead_h = (SNAP_PROTRUDE - (SNAP_GAP - SNAP_RAMP_IN)) / lead
    z_ret = SNAP_TOP - SNAP_LAND - lead_h
    z_root = z_ret - D.SNAP_LEN
    t, ln, dfl = D.SNAP_T, D.SNAP_LEN, D.SNAP_BARB
    strain = 1.5 * t * dfl / ln**2
    inertia = SNAP_W * t**3 / 12
    force = 3 * PETG_E * inertia * dfl / ln**3
    insert = force * (PETG_MU + lead) / (1 - PETG_MU * lead)
    return Snap(SNAP_TOP, z_ret, z_root, z_ret - SNAP_PLAY, SNAP_TOP + SNAP_WIN_TOP, strain, force,
                insert, lead_h)


def window_rect() -> tuple[float, float, float, float]:
    """The screen window in B (x0, x1, y0, y1) at the plate's underside: LCD active area + margin."""
    x0, y0, _ = D.lcd_to_b((D.LCD_PCB_W - D.LCD_AA_W) / 2, D.LCD_AA_Y_T)
    m = D.WINDOW_MARGIN
    return (x0 - m, x0 + D.LCD_AA_L + m, y0 - m, y0 + D.LCD_AA_W + m)


def end_port_slots() -> dict[str, tuple[float, float, float]]:
    """USB-end wall slots: name -> (y0, y1, z_bottom) in B; each runs up to SPLIT_Z + IO_MARGIN."""
    out = {}
    for name, (yc, w, h, _over) in D.PI_END_PORTS.items():
        _, y, z_top = D.pi_to_b(D.PI_L, yc)
        half = w / 2 + D.IO_MARGIN
        out[name] = (y - half, y + half, z_top - h - D.IO_MARGIN)
    return out


def io_centres() -> dict[str, tuple[float, float]]:
    """IO-edge port centres (X, Z) in B."""
    out = {}
    for name, (xc, _w, h, _over) in D.PI_IO_PORTS.items():
        x, _, z_top = D.pi_to_b(xc, 0.0)
        zc = z_top - 3.0 if name == "audio" else z_top - h / 2  # audio: barrel axis 3.0 below
        out[name] = (x, zc)
    return out


def io_openings() -> dict[str, tuple[float, float, float, float]]:
    """IO wall openings' extents (x0, x1, z0, z1) in B: USB-C, the two HDMI as one, audio."""
    c = io_centres()
    (uw, uh), (hw, hh), r = D.OPEN_USB_C, D.OPEN_HDMI, D.OPEN_AUDIO_D / 2
    ux, uz = c["usb_c"]
    h0, h1 = sorted((c["hdmi0"], c["hdmi1"]))
    ax, az = c["audio"]
    return {
        "usb_c": (ux - uw / 2, ux + uw / 2, uz - uh / 2, uz + uh / 2),
        "hdmi": (h0[0] - hw / 2, h1[0] + hw / 2, h0[1] - hh / 2, h0[1] + hh / 2),
        "audio": (ax - r, ax + r, az - r, az + r),
    }


def io_panel() -> tuple[float, float, float, float]:
    """The recessed IO panel (x0, x1, z0, z1): the openings' bounding box plus the margin."""
    ex = io_openings().values()
    m = D.IO_PANEL_MARGIN
    return (min(e[0] for e in ex) - m, max(e[1] for e in ex) + m,
            min(e[2] for e in ex) - m, max(e[3] for e in ex) + m)


def pi_posts() -> list[tuple[float, float]]:
    """The two tall IO-side posts (X, Y) at the Pi's holes."""
    return [D.pi_to_b(x, y)[:2] for x, y in D.PI_HOLES if y < D.PI_W / 2]


def hat_posts() -> list[tuple[float, float]]:
    """The two short header-side posts (X, Y) at the HAT's holes."""
    return [D.hat_to_b(x, y)[:2] for x, y in D.HAT_HOLES]


def display_holes() -> list[tuple[float, float, bool]]:
    """(X, Y, is_pin) of the display's four corner holes; the pin is the x_f 3.42 / y_t 3.00 one."""
    out = []
    for xf in (D.LCD_HOLE_X_F, D.LCD_HOLE_X_F + D.LCD_HOLE_PITCH_X):
        for yt in (D.LCD_HOLE_Y_T, D.LCD_HOLE_Y_T + D.LCD_HOLE_PITCH_Y):
            x, y, _ = D.lcd_to_b(xf, yt)
            out.append((x, y, xf == D.LCD_HOLE_X_F and yt == D.LCD_HOLE_Y_T))
    return out


def snap_sites() -> list[tuple[str, float]]:
    """(wall, centre along the wall): two on the +Y wall at X, two on the jack-end wall at Y."""
    return [("hdr", x) for x in D.SNAPS_SIDE_X] + [("jack", y) for y in D.SNAPS_END_Y]


def side_vents() -> list[float]:
    """+Y wall vent slot centres (X)."""
    n = round((VENT_SIDE_X[1] - VENT_SIDE_X[0]) / VENT_PITCH)
    xs = [VENT_SIDE_X[0] + i * VENT_PITCH for i in range(n + 1)]
    return [x for x in xs if all(abs(x - c) >= VENT_SNAP_CLEAR for c in D.SNAPS_SIDE_X)]


def foot_centres() -> list[tuple[float, float]]:
    x0, x1 = -D.WALL + FOOT_INSET, D.INNER_L + D.WALL - FOOT_INSET
    y0, y1 = -D.WALL + FOOT_INSET, D.INNER_W + D.WALL - FOOT_INSET
    return [(x0, y0), (x1, y0), (x0, y1), (x1, y1)]


# --- small solids ----------------------------------------------------------------------------------


def _box(x0: float, x1: float, y0: float, y1: float, z0: float, z1: float) -> Part:
    return Pos(x0, y0, z0) * Box(x1 - x0, y1 - y0, z1 - z0, align=MIN3)


def _zcyl(d: float, z0: float, z1: float, x: float, y: float) -> Part:
    return Pos(x, y, z0) * Cylinder(d / 2, z1 - z0, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _xz_prism(sk: Sketch, y0: float, y1: float) -> Part:
    """`sk` drawn in (X, Z), extruded across Y from y0 to y1."""
    return Plane(origin=(0, y1, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0)) * extrude(sk, y1 - y0)


def _yz_prism(sk: Sketch, x0: float, x1: float) -> Part:
    """`sk` drawn in (Y, Z), extruded along X from x0 to x1."""
    return Plane(origin=(x0, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)) * extrude(sk, x1 - x0)


def _wall_prism(wall: str, pts: Iterable[tuple[float, float]], s0: float, w: float) -> Part:
    """A profile in a wall's (n, Z) plane, n measured from the wall's inner face into the cavity
    (negative into the wall), extruded `w` along the wall from `s0` (X or Y, increasing).
    Walls: "io" (Y = 0), "hdr" (+Y), "usb" (X = 0), "jack" (+X)."""
    planes = {
        "io": Plane(origin=(s0, 0, 0), x_dir=(0, 1, 0), z_dir=(1, 0, 0)),
        "hdr": Plane(origin=(s0 + w, D.INNER_W, 0), x_dir=(0, -1, 0), z_dir=(-1, 0, 0)),
        "usb": Plane(origin=(0, s0 + w, 0), x_dir=(1, 0, 0), z_dir=(0, -1, 0)),
        "jack": Plane(origin=(D.INNER_L, s0, 0), x_dir=(-1, 0, 0), z_dir=(0, 1, 0)),
    }
    return planes[wall] * extrude(Polygon(*pts, align=None), w)


def _wall_cut(wall: str, sk: Sketch, *, n0: float = -D.WALL - 1.0, n1: float = 0.01) -> Part:
    """A cutter through a wall: `sk` drawn in (along-wall, Z), from n0 to n1 (n as `_wall_prism`)."""
    if wall == "io":
        return _xz_prism(sk, n0, n1)
    if wall == "hdr":
        return _xz_prism(sk, D.INNER_W - n1, D.INNER_W - n0)
    if wall == "usb":
        return _yz_prism(sk, n0, n1)
    return _yz_prism(sk, D.INNER_L - n1, D.INNER_L - n0)


def _outline(inset: float = 0.0) -> Sketch:
    """The box's plan outline (R3 corners), inset by `inset` (the corner radius shrinks with it)."""
    return Pos(D.INNER_L / 2, D.INNER_W / 2) * RectangleRounded(
        D.OUTER_L - 2 * inset, D.OUTER_W - 2 * inset, D.CORNER_R - inset)


def _slab(sk: Sketch, z0: float, z1: float) -> Part:
    return Pos(0, 0, z0) * extrude(sk, z1 - z0)


def _cavity(z0: float, z1: float) -> Part:
    return _box(0, D.INNER_L, 0, D.INNER_W, z0, z1)


def _root_fillet(x: float, y: float, d: float, r: float, z: float = 0.0) -> Part:
    """A concave fillet ring of radius `r` round a vertical post of diameter `d` standing on Z = z."""
    big = d / 2 + r
    ring = Pos(x, y, z) * Cylinder(big, r, align=(Align.CENTER, Align.CENTER, Align.MIN))
    return ring - Pos(x, y, z + r) * Torus(big, r)


def _stadium(cx: float, cz: float, w: float, h: float, r: float) -> Sketch:
    return Pos(cx, cz) * RectangleRounded(w, h, r)


def _slot_up(c: float, w: float, z0: float, z1: float, r: float) -> Sketch:
    """An open-top slot sketch (along-wall c +- w/2, Z z0..z1) with its bottom corners rounded."""
    return (Pos(c, z0 + 2 * r) * RectangleRounded(w, 4 * r, r)
            + Pos(c, (z0 + r + z1) / 2) * Rectangle(w, z1 - z0 - r))


def _teardrop(cx: float, cz: float, d: float) -> Sketch:
    """A hole for a horizontal axis: circle plus a point above, flanks at OVERHANG_DEG."""
    r = d / 2
    th = math.radians(90.0 - OVERHANG_DEG)  # tangent points this far above horizontal
    apex = r / math.sin(th)
    kite = Polygon((r * math.cos(th), r * math.sin(th)), (0, apex), (-r * math.cos(th), r * math.sin(th)),
                   (0, 0), align=None)
    return Pos(cx, cz) * (Circle(r) + kite)


# --- cutters shared by the lid and the base ------------------------------------------------------


def shared_cutters() -> dict[str, Part]:
    """Every port / jack / panel cutter, built once from dims (frame B). Each part takes the share
    it crosses: the IO openings and the panel cross the split, the USB-end slots reach IO_MARGIN
    above it (a notch in the lid's skirt over each port's top face)."""
    cut: dict[str, Part] = {}
    top = D.SPLIT_Z + D.IO_MARGIN
    for name, (y0, y1, zb) in end_port_slots().items():
        cut[f"port.{name}"] = _wall_cut("usb", _slot_up((y0 + y1) / 2, y1 - y0, zb, top, SLOT_R))
    op = io_openings()
    x0, x1, z0, z1 = op["usb_c"]
    cut["port.usb_c"] = _wall_cut("io", _stadium((x0 + x1) / 2, (z0 + z1) / 2, x1 - x0, z1 - z0, OPEN_R))
    x0, x1, z0, z1 = op["hdmi"]
    cut["port.hdmi"] = _wall_cut("io", _stadium((x0 + x1) / 2, (z0 + z1) / 2, x1 - x0, z1 - z0, OPEN_R))
    ax, az = io_centres()["audio"]
    r = D.OPEN_AUDIO_D / 2
    cut["port.audio"] = _wall_cut("io", Pos(ax, az) * (Circle(r) + Pos(0, r / 2) * Rectangle(2 * r, r)))
    px0, px1, pz0, pz1 = io_panel()
    cut["io_panel"] = _wall_cut(
        "io", _stadium((px0 + px1) / 2, (pz0 + pz1) / 2, px1 - px0, pz1 - pz0, IO_PANEL_R),
        n1=-(D.WALL - D.IO_PANEL_RECESS))
    for wall, tag in (("io", "in"), ("hdr", "out")):
        cut[f"jack_{tag}.hole"] = _wall_cut(wall, _teardrop(D.JACK_X, D.JACK_Z, D.JACK_HOLE_D))
        cut[f"jack_{tag}.spot_face"] = _spot_face(wall)
    return cut


def _spot_face(wall: str) -> Part:
    """The jack's spot face: a flat Ø18 seat JACK_SPOTFACE_DEPTH into the wall's outer face. Its rim
    flares at OVERHANG_DEG (Ø18 at the seat, Ø18.95 at the face), so its top is a 50 degree slope
    and not a 0.4 flat ledge (PrusaSlicer's auto support wanted pillars under a plain counterbore)."""
    seat = D.WALL - D.JACK_SPOTFACE_DEPTH
    out = D.JACK_SPOTFACE_DEPTH + 1.0
    r0 = D.JACK_SPOTFACE_D / 2
    if wall == "io":
        plane = Plane(origin=(D.JACK_X, -seat, D.JACK_Z), x_dir=(1, 0, 0), z_dir=(0, -1, 0))
    else:
        plane = Plane(origin=(D.JACK_X, D.INNER_W + seat, D.JACK_Z), x_dir=(1, 0, 0), z_dir=(0, 1, 0))
    return plane * Cone(r0, r0 + out * TAN_OH, out, align=(Align.CENTER, Align.CENTER, Align.MIN))


def _apply(part: Part, cutters: Iterable[Part]) -> Part:
    for c in cutters:
        part = part - c
    return part


# --- the rabbet ----------------------------------------------------------------------------------


def _lip_breaks(gap: float) -> list[Part]:
    """Where the base's lip is not (over the USB-end slots and the IO panel); `gap` > 0 shrinks
    each break by that much at both ends (the lid's pocket is the lip plus RABBET_GAP)."""
    slots = end_port_slots().values()
    ys0 = min(s[0] for s in slots) - D.RABBET_GAP + gap
    ys1 = max(s[1] for s in slots) + D.RABBET_GAP - gap
    px0, px1, _, _ = io_panel()
    xs0, xs1 = px0 - LIP_PANEL_MARGIN + gap, px1 + LIP_PANEL_MARGIN - gap
    big = 20.0
    return [_box(-big, OVER, ys0, ys1, -big, 100), _box(xs0, xs1, -big, OVER, -big, 100)]


def lip() -> Part:
    """The base's half of the rabbet: the inner RABBET_T of the wall from the split up RABBET_H,
    where the wall is continuous."""
    z0, z1 = D.SPLIT_Z - 0.01, D.SPLIT_Z + D.RABBET_H
    band = _slab(_outline(D.WALL - D.RABBET_T), z0, z1) - _cavity(z0 - 1, z1 + 1)
    return _apply(band, _lip_breaks(0.0))


def pocket() -> Part:
    """The lid's half: its skirt's inner RABBET_T + RABBET_GAP removed from the split up
    RABBET_H + RABBET_GAP, wherever the base has its lip (and RABBET_GAP past the lip's ends)."""
    z0, z1 = D.SPLIT_Z - 1.0, D.SPLIT_Z + D.RABBET_H + D.RABBET_GAP
    band = _slab(_outline(D.WALL - D.RABBET_T - D.RABBET_GAP), z0, z1)
    return _apply(band, _lip_breaks(D.RABBET_GAP))


# --- snaps -----------------------------------------------------------------------------------------


def snap_tab_profile(s: Snap | None = None) -> list[tuple[float, float]]:
    """The base tab's (n, Z) profile: corbel (50 degree underside) out of the wall, the tab
    SNAP_GAP off the wall, the barb at its top (square retention face reaching SNAP_BARB past the
    wall plane, 50 degrees under the part over the gap, land, 30 degree lead-in), the flat tip."""
    s = s or snap()
    g, t, b, k = SNAP_GAP, D.SNAP_T, SNAP_PROTRUDE, TAN_OH
    return [
        (-OVER, s.z_root - (g + t + OVER) * k),
        (g + t, s.z_root),
        (g + t, s.z_tip),
        (g - SNAP_RAMP_IN, s.z_tip),
        (g - b, s.z_ret + SNAP_LAND),
        (g - b, s.z_ret),
        (0.0, s.z_ret),
        (g, s.z_ret - g * k),
        (g, s.z_root),
        (-OVER, s.z_root),
    ]


def snap_tabs(s: Snap | None = None) -> Part:
    s = s or snap()
    prof = snap_tab_profile(s)
    return features.union([_wall_prism(w, prof, c - SNAP_W / 2, SNAP_W) for w, c in snap_sites()])


def snap_windows(s: Snap | None = None) -> list[Part]:
    """The lid skirt's windows, one per tab (through the skirt)."""
    s = s or snap()
    w = SNAP_W + 2 * SNAP_WIN_SIDE
    out = []
    for wall, c in snap_sites():
        sk = Pos(c, (s.win_z0 + s.win_z1) / 2) * Rectangle(w, s.win_z1 - s.win_z0)
        out.append(_wall_cut(wall, sk, n1=OVER))
    return out


# --- the base --------------------------------------------------------------------------------------


def _jack_ribs() -> Part:
    """Two ribs either side of each jack's front plate, JACK_RIB_GAP apart, underside at 50 deg."""
    k, dd = TAN_OH, JACK_RIB_D
    prof = [(-OVER, JACK_RIB_Z0 - (dd + OVER) * k), (dd, JACK_RIB_Z0), (dd, D.SPLIT_Z),
            (-OVER, D.SPLIT_Z)]
    ribs = []
    for wall in ("io", "hdr"):
        for x0 in (D.JACK_X - D.JACK_RIB_GAP / 2 - JACK_RIB_W, D.JACK_X + D.JACK_RIB_GAP / 2):
            ribs.append(_wall_prism(wall, prof, x0, JACK_RIB_W))
    return features.union(ribs)


def _posts() -> tuple[Part, list[Part]]:
    """Floor posts (with root fillets and the IO posts' webs) and their tap holes."""
    solids, taps = [], []
    for x, y in pi_posts():
        solids += [_zcyl(D.POST_IO_D, -OVER, D.PI_TOP_Z, x, y),
                   _box(x - POST_WEB_W / 2, x + POST_WEB_W / 2, -OVER, y, -OVER, POST_WEB_TOP),
                   _root_fillet(x, y, D.POST_IO_D, ROOT_FILLET)]
        taps.append(features.bore(D.POST_TAP_D, D.POST_TAP_DEPTH, x, y, D.PI_TOP_Z))
    for x, y in hat_posts():
        solids += [_zcyl(POST_HDR_D, -OVER, D.Z_HAT_TOP, x, y),
                   _root_fillet(x, y, POST_HDR_D, ROOT_FILLET)]
        taps.append(features.bore(D.POST_TAP_D, D.POST_TAP_DEPTH, x, y, D.Z_HAT_TOP))
    return features.union(solids), taps


def _vents() -> list[Part]:
    z0, z1 = VENT_Z
    out = [_wall_cut("hdr", Pos(x, (z0 + z1) / 2) * Rectangle(D.VENT_SLOT_W, z1 - z0))
           for x in side_vents()]
    out += [_wall_cut("jack", Pos(y, (z0 + z1) / 2) * Rectangle(D.VENT_SLOT_W, z1 - z0))
            for y in VENT_END_Y]
    n = round((GRILLE_X[1] - GRILLE_X[0]) / VENT_PITCH)
    ln = GRILLE_Y[1] - GRILLE_Y[0]
    for i in range(n + 1):
        x = GRILLE_X[0] + i * VENT_PITCH
        sk = Pos(x, (GRILLE_Y[0] + GRILLE_Y[1]) / 2) * Rot(0, 0, 90) * SlotOverall(ln, D.VENT_SLOT_W)
        out.append(_slab(sk, -D.FLOOR_T - 1, OVER))
    return out


def _foot_rings() -> list[Part]:
    z = -D.FLOOR_T
    out = []
    for x, y in foot_centres():
        ring = Pos(x, y) * (Circle(FOOT_D / 2) - Circle(FOOT_D / 2 - FOOT_RING_W))
        out.append(_slab(ring, z - 1, z + FOOT_DEPTH))
    return out


def base() -> Part:
    """The tub in B: floor Z -2..0, walls to SPLIT_Z, everything listed in the module docstring."""
    shell = _slab(_outline(), -D.FLOOR_T, D.SPLIT_Z)
    shell = features.chamfer_outline(shell, bottom=D.BOTTOM_CHAMFER)
    cav = _cavity(0, D.SPLIT_Z + 1)
    cav = fillet(cav.edges().filter_by(Axis.Z, reverse=True).group_by(Axis.Z)[0], ROOT_FILLET)
    part = shell - cav
    posts, taps = _posts()
    part = part + lip() + posts + _jack_ribs() + snap_tabs()
    part = _apply(part, shared_cutters().values())
    part = _apply(part, taps)
    part = _apply(part, _vents())
    return _apply(part, _foot_rings())


# --- the lid ---------------------------------------------------------------------------------------


def _boss_blocks(x: float, y: float) -> list[Part]:
    """A boss's web to the wall(s) it nearly touches (the boss's face stays the datum)."""
    r, z0, z1 = D.BOSS_D / 2, D.Z_LCD_FRONT + BOSS_WEB_LIFT, D.Z_BEZEL_UNDER + OVER
    wy0, wy1 = (-OVER, y) if y < D.INNER_W / 2 else (y, D.INNER_W + OVER)
    near_x = min(x - r, D.INNER_L - x - r) < BOSS_WALL_REACH
    out = [_box(x - r, x + r, wy0, wy1, z0, z1)]  # every boss is 0.62 from a Y wall
    if near_x:
        wx0, wx1 = (-OVER, x) if x < D.INNER_L / 2 else (x, D.INNER_L + OVER)
        out.append(_box(wx0, wx1, y - r, y + r, z0, z1))
        out.append(_box(wx0, wx1, wy0, wy1, z0, z1))
    return out


def _bosses() -> tuple[Part, list[Part]]:
    """Bezel bosses Ø6 from the plate down to the display's front face, their webs, the pin; and
    the three tap holes."""
    solids, taps = [], []
    for x, y, is_pin in display_holes():
        solids.append(_zcyl(D.BOSS_D, D.Z_LCD_FRONT, D.Z_BEZEL_UNDER + OVER, x, y))
        solids += _boss_blocks(x, y)
        if is_pin:
            solids.append(_zcyl(D.PIN_D, D.Z_LCD_FRONT - D.PIN_L, D.Z_LCD_FRONT + 0.01, x, y))
        else:
            taps.append(_zcyl(D.BOSS_TAP_D, D.Z_LCD_FRONT - 0.01, D.Z_LCD_FRONT + D.BOSS_TAP_DEPTH, x, y))
    return features.union(solids), taps


def _window(part: Part) -> Part:
    x0, x1, y0, y1 = window_rect()
    sk = Pos((x0 + x1) / 2, (y0 + y1) / 2) * RectangleRounded(x1 - x0, y1 - y0, D.WINDOW_R)
    part = part - _slab(sk, D.Z_BEZEL_UNDER - 1, D.Z_TOP + 1)
    top = part.faces().sort_by(Axis.Z)[-1]
    return chamfer([e for w in top.inner_wires() for e in w.edges()], D.WINDOW_CHAMFER)


def labels() -> list[Part]:
    """The engraved text on the top (reads from above; up = +X, the UI's top)."""
    _, x1, _, _ = window_rect()
    bx = ((x1 + D.WINDOW_CHAMFER) + (D.INNER_L + D.WALL - D.TOP_CHAMFER)) / 2
    yc = D.INNER_W / 2
    kw = {"depth": ENGRAVE_DEPTH, "rotation": -90.0}
    return [features.engrave("bassbox", bx, yc, D.Z_TOP, height=LABEL_FONT, **kw)]


def lid(engrave: bool = True) -> Part:
    """The bezel in B: plate Z 44.9..46.5 and skirt down to SPLIT_Z."""
    part = _slab(_outline(), D.SPLIT_Z, D.Z_TOP)
    part = features.chamfer_outline(part, top=D.TOP_CHAMFER)
    part = part - _cavity(D.SPLIT_Z - 1, D.Z_BEZEL_UNDER)
    part = _window(part)
    bosses, taps = _bosses()
    part = part + bosses
    part = part - pocket()
    part = _apply(part, shared_cutters().values())
    part = _apply(part, snap_windows())
    part = _apply(part, taps)
    if engrave:
        part = _apply(part, labels())
    return part


# --- the spacer ------------------------------------------------------------------------------------


SPACER_OD, SPACER_ID = 5.0, D.PIN_D  # spec: Ø5 / Ø2.8 (M2.5 clearance)
SPACER_L = D.HAT_SPACER - 0.05  # 0.05 short of the gap: a spacer must never lift the HAT off the
#                                 header (components' HAT board is 1.72, dims' 1.70)


def spacer() -> Part:
    """One optional spacer in B, between the HAT's underside and the Pi's top face at the -X
    header-side hole (the other one is the same part at the other hole)."""
    x, y = min(hat_posts())
    z1 = D.PI_TOP_Z
    tube = _zcyl(SPACER_OD, z1 - SPACER_L, z1, x, y)
    return tube - _zcyl(SPACER_ID, z1 - SPACER_L - 1, z1 + 1, x, y)


def build() -> dict[str, Part]:
    """{lid, base, spacer} in frame B, installed pose."""
    return {"lid": lid(), "base": base(), "spacer": spacer()}


def print_pose(name: str, part: Part) -> Part:
    """`part` as printed: the lid face down (turned about X), the base floor down, the spacer
    upright; lower-left-bottom corner at the origin."""
    if name == "lid":
        part = Rot(180, 0, 0) * part
    bb = part.bounding_box()
    return Pos(-bb.min.X, -bb.min.Y, -bb.min.Z) * part


def to_print(name: str, p: tuple[float, float, float]) -> tuple[float, float, float]:
    """A frame-B point of part `name` in its print pose (for locating features in checks)."""
    lo = {"lid": (-D.WALL, -(D.INNER_W + D.WALL), -D.Z_TOP),
          "base": (-D.WALL, -D.WALL, -D.FLOOR_T)}.get(name)
    if lo is None:
        raise ValueError(name)
    x, y, z = p
    if name == "lid":
        y, z = -y, -z
    return (x - lo[0], y - lo[1], z - lo[2])


# --- checks ----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Overhang:
    """A face that leans out flatter than OVERHANG_DEG in the print pose."""

    kind: str
    bbox: tuple[float, float, float, float, float, float]
    worst: float  # degrees from vertical (90 = a flat roof)


def _overhang_faces(part: Part, limit: float = OVERHANG_DEG) -> list[tuple[object, tuple, float]]:
    thr = math.cos(math.radians(limit)) + 2e-3
    zmin = part.bounding_box().min.Z
    out = []
    for f in part.faces():
        bb = f.bounding_box()
        if bb.max.Z < zmin + 1e-6:
            continue  # on the bed
        worst = 0.0
        for u in (0.05, 0.5, 0.95):
            for v in (0.05, 0.5, 0.95):
                worst = max(worst, -f.normal_at(f.position_at(u, v)).Z)
        if worst > thr:
            box = (bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z)
            out.append((f, box, math.degrees(math.asin(min(1.0, worst)))))
    return out


def overhangs(name: str, printed: Part) -> list[Overhang]:
    """Every overhang of part `name` (in its print pose), sorted by what makes it. Expected kinds:
    `bed chamfer` (45 degree chamfers rising from the bed: dims' TOP/BOTTOM/WINDOW chamfers), `bridge`
    (flat roofs over vent slots, snap windows, foot rings, engraved letters), `barb ledge` (the four
    snaps' square retention faces: the spec asks for them square). `other` must stay empty."""
    s = snap()
    zmin = printed.bounding_box().min.Z
    out = []
    for _f, bb, worst in _overhang_faces(printed):
        z0, z1 = bb[2], bb[5]
        flat = z1 - z0 < 1e-3
        kind = "other"
        if z0 - zmin < 1e-3 and z1 - zmin <= max(D.TOP_CHAMFER, D.BOTTOM_CHAMFER) + 1e-3 and worst < 46:
            kind = "bed chamfer"
        elif flat and z0 - zmin <= ENGRAVE_DEPTH + 1e-3 + (FOOT_DEPTH if name == "base" else 0):
            kind = "bridge"  # engraving (lid) and foot rings (base): narrow roofs one layer up
        elif flat and name == "base" and abs(z0 - to_print("base", (0, 0, VENT_Z[1]))[2]) < 1e-3:
            kind = "bridge"  # vent slot roofs, VENT_SLOT_W wide
        elif flat and name == "lid" and abs(z0 - to_print("lid", (0, 0, s.win_z0))[2]) < 1e-3:
            kind = "bridge"  # snap window roofs, SNAP_W + 2 x SNAP_WIN_SIDE wide
        elif flat and name == "base" and abs(z0 - to_print("base", (0, 0, s.z_ret))[2]) < 1e-3:
            kind = "barb ledge"  # the snaps' square retention faces (SNAP_BARB, see above)
        out.append(Overhang(kind, bb, worst))
    return out


def wall_thickness(part: Part, point: tuple[float, float, float], axis: str) -> float:
    """Material thickness (mm) through `point` along "X", "Y" or "Z" (0 if the point is in air)."""
    i = "XYZ".index(axis)
    size = [0.04, 0.04, 0.04]
    size[i] = 200.0
    probe = Pos(*point) * Box(*size)
    hit = part & probe
    p = point[i]
    for s in hit.solids() if hit else []:
        bb = s.bounding_box()
        lo, hi = (bb.min.X, bb.min.Y, bb.min.Z)[i], (bb.max.X, bb.max.Y, bb.max.Z)[i]
        if lo - 1e-6 <= p <= hi + 1e-6:
            return hi - lo
    return 0.0


# (part, label, point in B, axis): sections through the walls; WALL_MIN applies to all but `rabbet`
WALL_MIN = 1.2
WALL_SAMPLES = (
    ("base", "IO wall", (10.0, -1.0, 10.0), "Y"),
    ("base", "IO panel", (50.0, -0.6, 26.0), "Y"),
    ("base", "IO panel, USB-C..HDMI web", (66.35, -0.6, 32.0), "Y"),
    ("base", "IO panel web, along the wall", (66.35, -0.6, 32.0), "X"),
    ("base", "jack spot face", (104.8, -1.0, 24.4), "Y"),
    ("base", "+Y wall", (50.0, 63.0, 10.0), "Y"),
    ("base", "+Y wall at a vent", (side_vents()[0] + 1.6, 63.0, 17.0), "Y"),
    ("base", "jack-end wall", (113.4, 20.0, 10.0), "X"),
    ("base", "USB-end pillar USB2|USB3", (-1.0, 18.2, 25.0), "X"),
    ("base", "USB-end pillar, across", (-1.0, 18.2, 25.0), "Y"),
    ("base", "floor", (60.0, 50.0, -1.0), "Z"),
    ("base", "snap tab", (D.SNAPS_SIDE_X[0], D.INNER_W - SNAP_GAP - 0.7, 30.0), "Y"),
    ("base", "rabbet lip", (50.0, D.INNER_W + 0.5, D.SPLIT_Z + 0.7), "Y"),
    ("lid", "bezel plate", (50.0, 1.0, 45.7), "Z"),
    ("lid", "skirt", (50.0, 63.1, 41.0), "Y"),
    ("lid", "skirt, IO panel", (50.0, -0.6, 37.0), "Y"),
    ("lid", "skirt at the USB-end ports", (-1.0, 10.0, 36.0), "X"),
    ("lid", "snap window bar", (D.SNAPS_SIDE_X[0], 63.6, 35.0), "Z"),
    ("lid", "rabbet skirt", (10.0, -1.6, 34.8), "Y"),
)
RABBET_SAMPLES = ("rabbet lip", "rabbet skirt")  # dims: RABBET_T 1.0, skirt 2.0 - 1.15 = 0.85


def wall_report(parts: dict[str, Part]) -> list[tuple[str, str, float]]:
    """(part, label, thickness) for every WALL_SAMPLES entry."""
    return [(n, lab, wall_thickness(parts[n], p, ax)) for n, lab, p, ax in WALL_SAMPLES]


def slivers(name: str, printed: Part, *, step: float = 0.5) -> dict[str, list]:
    """The sliver rule on a part in its print pose: knife edges and small section pieces,
    with the engraving's (lid, within ENGRAVE_DEPTH of the bed) filtered out and returned apart."""
    bb = printed.bounding_box()
    knives = checks.knife_edges(printed)
    pieces = checks.small_section_pieces(printed, bb.min.Z + 0.1, bb.max.Z, step=step)
    eng = ENGRAVE_DEPTH + 1e-3 if name == "lid" else -1.0
    return {
        "knife_edges": [k for k in knives if k.point[2] - bb.min.Z > eng],
        "small_pieces": [p for p in pieces if p[0] - bb.min.Z > eng],
        "engraving_hits": [k for k in knives if k.point[2] - bb.min.Z <= eng]
        + [p for p in pieces if p[0] - bb.min.Z <= eng],
    }


def glass_gap() -> float:
    """Bezel underside to the touch glass's top (nominal)."""
    return D.Z_BEZEL_UNDER - D.Z_GLASS_TOP


def centre_of(part: Part) -> Vector:
    return part.bounding_box().center()
