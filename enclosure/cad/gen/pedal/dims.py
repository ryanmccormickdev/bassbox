"""The bassbox pedal enclosure's dimension contract: frame B and every number the parts share.

Read `DESIGN.md` first. Both `components.py` (the bought parts, as solids) and
`enclosure.py` (the printed parts) import from here, so a number lives in one place. Units: mm.

Frame B (the box frame)
-----------------------
* Origin: the inner corner where the USB-end wall's inner face, the IO-side wall's inner face
  and the floor's top (inner) surface meet.
* +X: along the box, from the USB/Ethernet end (X = 0) toward the jack end (X = INNER_L).
* +Y: across, from the IO side (the Pi's USB-C / HDMI / audio edge, Y = 0) toward the HAT-header
  side (Y = INNER_W). Seen from above with the screen upright (UI top at +X... see DESIGN.md),
  the IO side is the player's right.
* +Z: up. Z = 0 is the floor's inner surface; the screen faces +Z.
* Walls are WALL thick and sit outside the cavity: outer X -WALL..INNER_L+WALL, etc.

Sources (confidence)
--------------------
* Display: lcdwiki MSP4021 drawing "MSP4021_Size.pdf" (high) and caliper measurements 61.5 x 108.0,
  glass strip 6.9 (match).
* Pi 4: official raspberry-pi-4-mechanical-drawing.pdf (high); port overhangs read off the drawing
  (medium).
* ULTRA++ HAT: RaspiAudio's own model 3D_UltraMIC4layer_2026-03-18.obj (high): 65 x 30 board on the
  header side only; female header 6.9 + 0.51 below the board, 2.5 + 6 male pass-through above.
* 1/4-inch jacks: photos, 16.4 mm across the front nut (calipers) (low-medium for the body).
* Stack: caliper reading, 45.9 glass-top to wire-top (medium: wires are soft).
"""

from __future__ import annotations

# --------------------------------------------------------------------------------------------
# Shell
# --------------------------------------------------------------------------------------------
WALL = 2.0  # side and end walls
FLOOR_T = 2.0  # floor thickness (Z -FLOOR_T..0)
TOP_T = 1.6  # bezel plate thickness over the glass
CORNER_R = 3.0  # outer vertical-edge radius
TOP_CHAMFER = 1.0  # 45 deg on the top outer edge (prints on the bed: lid is printed face down)
BOTTOM_CHAMFER = 0.6  # 45 deg on the bottom outer edge (elephant foot)

FIT = 0.2  # default clearance between a bought part and a printed face (per side)

# Display offset from the USB-end wall (E in DESIGN.md): makes room for the jack bay between the
# HAT's terminal block and the display's header duponts, and centres the window.
DISPLAY_X0 = 4.2  # display PCB's SD-pin ("top") edge at X = DISPLAY_X0
DISPLAY_Y0 = FIT  # display PCB's left edge (front view) at Y = DISPLAY_Y0

# Display (lcdwiki MSP4021). Front-view coords: x_f from the left edge, y_t from the top
# (SD-pin) edge, z_f from the PCB front face toward the glass.
LCD_PCB_W = 61.74  # measured: 61.5
LCD_PCB_L = 108.04  # measured: 108.0
LCD_PCB_T = 1.6
LCD_STACK = 4.05  # PCB front to touch-glass top: 0.50 back tape + 3.55 +-0.1 RTP+LCD (measured: 3.7)
LCD_BL_W, LCD_BL_L, LCD_BL_Y_T = 60.88, 94.57, 6.75  # LCD/backlight outline, top offset
RTP_OD_W, RTP_OD_L, RTP_OD_Y_T = 60.48, 93.87, 6.95  # touch glass outline (measured: 6.9 strip)
LCD_AA_W, LCD_AA_L, LCD_AA_Y_T = 55.68, 83.52, 9.40  # active area (pixels)
RTP_AA_W, RTP_AA_L, RTP_AA_Y_T = 56.28, 84.32, 9.15  # touch active area
LCD_HOLE_D = 3.2  # 4 corner holes, pads 4.7
LCD_HOLE_PITCH_X, LCD_HOLE_PITCH_Y = 54.90, 102.04  # across, along (3.42 / 3.00 from the edges)
LCD_HOLE_X_F, LCD_HOLE_Y_T = 3.42, 3.00
LCD_HDR_N = 14  # 14-pin male header on the back, pins pointing away from the glass
LCD_HDR_PIN1_X_F = 14.36  # pin 1 (VCC) .. pin 14 (T_IRQ) at 2.54 pitch
LCD_HDR_Y_B = 2.00  # pin row from the bottom (jack-end) edge
LCD_HDR_PLASTIC = 2.5  # header plastic on the PCB back
LCD_SMD_MAX = 3.0  # back-side SMD envelope (drawing: 3.00 max)
LCD_SD_X_F = (4.23, 23.24)  # back-side microSD socket, front-view x range
LCD_SD_Y_T = (27.7, 57.1)  # ... and y range from the top edge
LCD_SD_H = 1.9
LCD_SMD_X_F = (13.4, 48.3)  # back-side IC cluster (U1, U2, Q1, R/C), front-view x
LCD_SMD_Y_B = (14.9, 23.7)  # ... from the bottom (header) edge

DUPONT_HOUSING = 14.0  # female dupont housing length
DUPONT_PITCH = 2.54
WIRE_BEND = 4.5  # room a dupont wire needs to turn 90 deg after the housing

# Pi 4 (official drawing). Pi coords: x_pi from the SD-card end, y_pi from the IO edge,
# z_pi up from the TOP face (components on the top face at z_pi > 0).
PI_L, PI_W, PI_T = 85.0, 56.0, 1.4
PI_CORNER_R = 3.0
PI_HOLE_D = 2.7
PI_HOLES = ((3.5, 3.5), (61.5, 3.5), (3.5, 52.5), (61.5, 52.5))  # (x_pi, y_pi)
PI_HOLE_PAD_D = 6.0
# IO edge (y_pi = 0): name -> (x_pi centre, width, height z, overhang beyond the edge)
PI_IO_PORTS = {
    "usb_c": (11.2, 8.94, 3.2, 1.2),
    "hdmi0": (26.0, 7.5, 3.0, 1.2),
    "hdmi1": (39.5, 7.5, 3.0, 1.2),
    "audio": (53.5, 6.0, 6.0, 2.5),  # barrel Ø6, axis about 3.0 above the face
}
# USB end (x_pi = 85): name -> (y_pi centre, width, height z, overhang beyond the edge)
PI_END_PORTS = {
    "usb2": (9.0, 15.0, 16.0, 2.8),
    "usb3": (27.0, 15.0, 16.0, 2.8),
    "eth": (45.75, 16.0, 13.5, 2.8),
}
PI_HDR_X = (7.1, 57.9)  # 2x20 header body along x_pi, rows at y_pi 51.23 / 53.77, Z=8.5
PI_SD_CARD = (22.5, 33.5, 2.5)  # microSD (bottom side): y_pi range, protrusion past x_pi = 0
PI_BOTTOM_TAILS = 1.6  # through-hole tails and the SD socket below the PCB

# Placement of the Pi: upside down (top face toward the floor), USB end against X = 0.
PI_EDGE_GAP = FIT  # PCB edge to the wall's inner face (USB end and IO side)
PI_TOP_Z = 34.1  # Z of the Pi's top face = the lid/base split plane
SPLIT_Z = PI_TOP_Z


def pi_to_b(x_pi: float, y_pi: float, z_pi: float = 0.0) -> tuple[float, float, float]:
    """Pi coords -> frame B (rotation of 180 deg about Y, then the offset)."""
    return (PI_EDGE_GAP + PI_L - x_pi, PI_EDGE_GAP + y_pi, PI_TOP_Z - z_pi)


# ULTRA++ HAT (vendor OBJ coords: x along the board from the SD/terminal-block end, y from the
# edge away from the header, z up from the component (top) face; PCB z -1.7..0).
HAT_L, HAT_W, HAT_T = 65.0, 30.0, 1.7
HAT_Y_TO_PI = 26.08  # y_pi = y_hat + 26.08 (header centre y_hat 26.42 <-> y_pi 52.5)
HAT_BOTTOM_ZPI = 9.91  # HAT underside above the Pi top face (2.5 Pi plastic + 6.9 + 0.51)
HAT_TOP_ZPI = HAT_BOTTOM_ZPI + HAT_T  # 11.61
HAT_HOLES = ((3.5, 26.42), (61.5, 26.42))  # (x_hat, y_hat): coaxial with the Pi's header-side
HAT_OBJ = "3D_UltraMIC4layer_2026-03-18.obj"  # see components.hat_mesh(); kept out of git


def hat_to_b(x: float, y: float, z: float = 0.0) -> tuple[float, float, float]:
    """HAT model coords -> frame B (the HAT rides on the Pi, so the same 180 deg about Y)."""
    return pi_to_b(x, y + HAT_Y_TO_PI, z + HAT_TOP_ZPI)


# Display placement: face up, SD-pin edge at the USB end (rotation of +90 deg about Z).
Z_BEZEL_UNDER = 44.9  # underside of the bezel plate ("U" in DESIGN.md)
Z_TOP = Z_BEZEL_UNDER + TOP_T  # 46.5, top surface
BOSS_LEN = 4.40  # bezel underside to the display PCB's front face (glass gap 0.35 at nominal)
Z_LCD_FRONT = Z_BEZEL_UNDER - BOSS_LEN  # 40.5
Z_LCD_BACK = Z_LCD_FRONT - LCD_PCB_T  # 38.9
Z_GLASS_TOP = Z_LCD_FRONT + LCD_STACK  # 44.55


def lcd_to_b(x_f: float, y_t: float, z_f: float = 0.0) -> tuple[float, float, float]:
    """Display front-view coords -> frame B."""
    return (DISPLAY_X0 + y_t, DISPLAY_Y0 + x_f, Z_LCD_FRONT + z_f)


# Cavity
INNER_L = DISPLAY_X0 + LCD_PCB_L + FIT  # 112.44
INNER_W = LCD_PCB_W + 2 * FIT  # 62.14
OUTER_L = INNER_L + 2 * WALL  # 116.44
OUTER_W = INNER_W + 2 * WALL  # 66.14
OUTER_H = FLOOR_T + Z_TOP  # 48.5

# Screen window: the LCD active area plus a margin, 45 deg chamfer on the top edge.
WINDOW_MARGIN = 0.4
WINDOW_CHAMFER = 0.8
WINDOW_R = 1.0

# Bezel bosses at the display's holes. The IO-side USB-end corner sits over the Pi's USB2 port,
# so it gets a locating pin and no screw (a screw head there would land on the USB tails).
BOSS_D = 6.0
BOSS_TAP_D = 2.1  # M2.5 self-tapping (from a fit coupon)
BOSS_TAP_DEPTH = 5.0  # leaves 1.0 of skin under the top surface
PIN_D, PIN_L = 2.8, 1.3
DISPLAY_SCREW = "M2.5 x 6 socket head, from the display's back into the boss"

# Floor posts (base). IO-side holes are not under the HAT: posts go up to the Pi's top face.
POST_IO_D = 5.5
POST_HDR_D = 5.0
POST_TAP_D = 2.1
POST_TAP_DEPTH = 8.0
Z_HAT_TOP = PI_TOP_Z - HAT_TOP_ZPI  # 22.49: the HAT's component face (toward the floor)
PI_SCREW = "M2.5 x 8 socket head, from the Pi's underside into the IO-side posts"
HAT_SPACER = 9.9  # optional printed spacers for the header-side holes (M2.5 x 20 screws)

# 1/4-inch stereo jacks: square plastic body, 3/8-32 metal bushing, plastic nut + washer.
JACK_BODY_W = 20.7  # along X (photo estimate 19.4..20.7)
JACK_BODY_H = 20.7  # along Z (unknown: assumed square)
JACK_BODY_L = 24.0  # from the front plate inward, lug tabs included
JACK_LUG_DROP = 6.0  # lugs and solder below the body (lugs face the floor)
JACK_BUSHING_D = 9.53
JACK_HOLE_D = 10.2  # printed hole for the bushing (teardrop point up)
JACK_NUT_OD = 16.4  # calipers across the front nut/washer
JACK_SPOTFACE_D, JACK_SPOTFACE_DEPTH = 18.0, 0.4  # outside: flat seat, wall 1.6 there
JACK_X = 97.3  # jack axis X (bay 86.2..108.4 between the HAT terminal block and the duponts)
JACK_Z = 24.4  # jack axis Z (body top 34.75 clears the display SMD at 35.9)
JACK_RIB_GAP = JACK_BODY_W + 0.5  # anti-rotation ribs either side of the body's front plate

# IO openings (shared cutters cut both the lid and the base).
IO_MARGIN = 0.4  # around a port body
OPEN_USB_C = (12.6, 7.4)  # (width along X, height along Z) for plug overmolds
OPEN_HDMI = (11.6, 7.6)
OPEN_AUDIO_D = 8.0
IO_PANEL_RECESS = 0.8  # recessed IO panel on the outside of the IO wall (wall 1.2 there)
IO_PANEL_MARGIN = 2.0

# Lid/base joint: rabbet; the visible seam is at SPLIT_Z.
RABBET_H = 1.5
RABBET_T = 1.0  # base keeps the inner 1.0 of its wall up to SPLIT_Z + RABBET_H
RABBET_GAP = 0.15

# Snaps (lid tabs hooking into windows through the base walls).
SNAPS_SIDE_X = (21.5, 70.0)  # on the +Y wall
SNAPS_END_Y = (9.8, 52.3)  # on the jack-end wall
SNAP_T, SNAP_LEN, SNAP_BARB = 1.4, 12.0, 0.8

# Vents
VENT_SLOT_W = 1.6
