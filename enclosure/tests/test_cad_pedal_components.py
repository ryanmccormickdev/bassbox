"""The bassbox pedal's bought-part models (components.py) and the clash harness (assembly.py).

Numbers come from `cad.gen.pedal.dims` (the contract) and the vendor drawings; the harness is
exercised on a stand-in enclosure built here from the components themselves.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("build123d")

from build123d import import_step

from cad.gen import checks
from cad.gen.pedal import assembly as asm
from cad.gen.pedal import components as comp
from cad.gen.pedal import dims as d


@pytest.fixture(scope="module")
def parts():
    return comp.parts()


@pytest.fixture(scope="module")
def plugs():
    return comp.plugs()


@pytest.fixture(scope="module")
def keepouts():
    return comp.keepouts()


def bb(p):
    b = p.bounding_box()
    return (b.min.X, b.max.X, b.min.Y, b.max.Y, b.min.Z, b.max.Z)


def approx(got, want, tol=0.01):
    assert got == pytest.approx(want, abs=tol)


# ---------------------------------------------------------------------------------------------
# The models
# ---------------------------------------------------------------------------------------------
def test_inventory(parts, plugs, keepouts):
    assert not set(parts) & set(plugs) | set(parts) & set(keepouts) | set(plugs) & set(keepouts)
    for name, s in {**parts, **plugs, **keepouts}.items():
        assert s.is_valid, name
        assert s.volume > 1.0, name
        assert re.fullmatch(r"[a-z0-9_]+(\.[a-z0-9_]+)+", name), name
    for must in (
        "lcd.pcb",
        "lcd.dupont",
        "pi.pcb",
        "pi.usb2",
        "pi.usb_c",
        "hat.pcb",
        "hat.dupont",
        "jack_in.body",
        "jack_out.body",
        "screw.pi_io1",
        "screw.lcd3",
    ):
        assert must in parts
    for must in (
        "lcd.dupont_wires",
        "hat.dupont_wires",
        "wires.jack_gap",
        "pi.heatsink",
        "pi.bottom_tails",
    ):
        assert must in keepouts
    assert {k.rsplit(".", 1)[0] for k in plugs} >= {
        "plug.usb_c",
        "plug.hdmi0",
        "plug.hdmi1",
        "plug.audio",
        "plug.usb2a",
        "plug.usb2b",
        "plug.usb3a",
        "plug.usb3b",
        "plug.eth",
    }


def test_display_pcb_and_stack(parts):
    pcb = parts["lcd.pcb"]
    x0, x1, y0, y1, z0, z1 = bb(pcb)
    approx(x1 - x0, d.LCD_PCB_L)
    approx(y1 - y0, d.LCD_PCB_W)
    approx(z1 - z0, d.LCD_PCB_T)
    approx(x0, d.DISPLAY_X0)
    approx(y0, d.DISPLAY_Y0)
    approx(z0, d.Z_LCD_BACK)
    approx(z1, d.Z_LCD_FRONT)
    for xf, yt in comp.lcd_hole_xy():
        x, y, _ = d.lcd_to_b(xf, yt)
        (hole,) = checks.holes_at(pcb, x, y)
        approx(hole.diameter, d.LCD_HOLE_D, 0.005)
    # the two corners on the pin side: 54.90 x 102.04 pitch, 3.42 / 3.00 from the edges
    pts = [d.lcd_to_b(*h) for h in comp.lcd_hole_xy()]
    approx(max(p[0] for p in pts) - min(p[0] for p in pts), d.LCD_HOLE_PITCH_Y)
    approx(max(p[1] for p in pts) - min(p[1] for p in pts), d.LCD_HOLE_PITCH_X)
    # glass top at Z_GLASS_TOP; panel (tape + LCD) 3.0 above the PCB front; glass 1.05 on top
    approx(bb(parts["lcd.glass"])[5], d.Z_GLASS_TOP)
    approx(bb(parts["lcd.panel"])[4], d.Z_LCD_FRONT)
    approx(bb(parts["lcd.panel"])[5], d.Z_LCD_FRONT + 3.0)
    gx0, gx1, gy0, gy1, _, _ = bb(parts["lcd.glass"])
    approx(gx1 - gx0, d.RTP_OD_L)
    approx(gy1 - gy0, d.RTP_OD_W)
    approx(gx0, d.DISPLAY_X0 + d.RTP_OD_Y_T)


def test_display_header_and_back(parts):
    dup = bb(parts["lcd.dupont"])
    # pin row 2.00 from the jack-end edge: X = DISPLAY_X0 + 108.04 - 2.0; housings 2.6 sq, 14 long
    x_row = d.DISPLAY_X0 + d.LCD_PCB_L - d.LCD_HDR_Y_B
    approx((dup[0], dup[1]), (x_row - 1.3, x_row + 1.3))
    approx(dup[5] - dup[4], d.DUPONT_HOUSING)
    approx(dup[5], d.Z_LCD_BACK - d.LCD_HDR_PLASTIC)  # standing on the 2.5 plastic
    approx(dup[4], 22.4)  # DESIGN.md: 36.4 .. 22.4
    approx(dup[2], d.DISPLAY_Y0 + d.LCD_HDR_PIN1_X_F - 1.3)
    approx(dup[3], d.DISPLAY_Y0 + d.LCD_HDR_PIN1_X_F + 13 * d.DUPONT_PITCH + 1.3)
    # the DESIGN.md wire route: X 108.97..111.51 for the header plastic, Y 13.3..48.9
    hdr = bb(parts["lcd.header"])
    approx((hdr[0], hdr[1]), (108.97, 111.51), 0.02)
    approx((dup[2], dup[3]), (13.26, 48.88), 0.05)
    # back-side blocks: SMD 3.0 tall, microSD socket 1.9, both under the PCB back
    approx(bb(parts["lcd.smd"])[4], d.Z_LCD_BACK - d.LCD_SMD_MAX)
    approx(bb(parts["lcd.sd"])[4], d.Z_LCD_BACK - d.LCD_SD_H)
    # tails on the front, 1.5 high
    tails = bb(parts["lcd.tails"])
    approx(tails[4], d.Z_LCD_FRONT)
    approx(tails[5], d.Z_LCD_FRONT + 1.5)


def test_display_window_numbers():
    ax0, ax1, ay0, ay1 = comp.lcd_active_rect()
    wx0, wx1, wy0, wy1 = comp.screen_window_rect()
    gx0, gx1, gy0, gy1 = comp.glass_rect()
    m = d.WINDOW_MARGIN
    approx((ax1 - ax0, ay1 - ay0), (d.LCD_AA_L, d.LCD_AA_W))
    # the window is the active area plus the margin on every side ...
    approx((ax0 - wx0, wx1 - ax1, ay0 - wy0, wy1 - ay1), (m, m, m, m))
    # ... sits inside the touch glass, so the bezel never lets the glass edge show ...
    assert gx0 < wx0 and wx1 < gx1 and gy0 < wy0 and wy1 < gy1
    # ... and covers the touch active area across the width; along the length the touch AA runs 0.15
    # past the window at the jack end (it is offset 0.15 from the LCD's): immaterial, but noted
    tx0, tx1, ty0, ty1 = comp.lcd_rect(
        (d.LCD_PCB_W / 2 - d.RTP_AA_W / 2, d.LCD_PCB_W / 2 + d.RTP_AA_W / 2),
        (d.RTP_AA_Y_T, d.RTP_AA_Y_T + d.RTP_AA_L),
    )
    assert wy0 <= ty0 and ty1 <= wy1
    assert wx0 <= tx0 and tx1 - wx1 == pytest.approx(0.15, abs=0.01)
    # borders to the outer wall faces: DESIGN.md says 15.2 (USB end) and 16.9 (jack end)
    approx(wx0 - (-d.WALL), 15.2, 0.05)
    approx((d.INNER_L + d.WALL) - wx1, 16.9, 0.05)
    # the screw holes are outside the glass in the border (the bosses stay off the glass)
    hx, hy, _ = d.lcd_to_b(*comp.lcd_hole_xy()[0])
    assert hy < gy0 or hx < gx0


def test_pi_pcb_and_holes(parts):
    pcb = parts["pi.pcb"]
    x0, x1, y0, y1, z0, z1 = bb(pcb)
    approx((x1 - x0, y1 - y0, z1 - z0), (d.PI_L, d.PI_W, d.PI_T))
    approx((x0, y0, z0), (d.PI_EDGE_GAP, d.PI_EDGE_GAP, d.PI_TOP_Z))  # 0.2 from both walls
    for xp, yp in d.PI_HOLES:
        x, y, _ = d.pi_to_b(xp, yp)
        (hole,) = checks.holes_at(pcb, x, y)
        approx(hole.diameter, d.PI_HOLE_D, 0.005)
    # R3 corners: the corner point is not on the board
    assert pcb.distance_to(comp.box(x0 - 1, x0 + 0.05, y0 - 1, y0 + 0.05, z0 - 1, z1 + 1)) > 0.5


def test_pi_ports_in_frame_b(parts):
    # USB-C: centre X 74.0, front face 1.2 past the PCB edge, i.e. Y = -1.0
    usbc = bb(parts["pi.usb_c"])
    approx((usbc[0] + usbc[1]) / 2, 74.0)
    approx(usbc[2], -1.0)
    approx(usbc[5], d.PI_TOP_Z)  # ports hang from the Pi's top face
    approx(usbc[5] - usbc[4], 3.2)
    for name, h in (("hdmi0", 3.0), ("hdmi1", 3.0)):
        b = bb(parts[f"pi.{name}"])
        xc = d.pi_to_b(d.PI_IO_PORTS[name][0], 0)[0]
        approx((b[0] + b[1]) / 2, xc)
        approx(b[2], -1.0)
        approx(b[5] - b[4], h)
    audio = bb(parts["pi.audio"])
    approx(audio[2], d.PI_EDGE_GAP - 2.5)  # barrel tip
    approx(audio[5] - audio[4], 6.0)
    # USB / Ethernet: 2.8 past the PCB edge, so 0.6 proud of the 2.0 wall's outside
    for name, h in (("usb2", 16.0), ("usb3", 16.0), ("eth", 13.5)):
        b = bb(parts[f"pi.{name}"])
        approx(b[0], d.PI_EDGE_GAP - 2.8)
        assert b[0] < -d.WALL
        approx(b[5] - b[4], h)
    approx(sum(bb(parts["pi.usb2"])[2:4]) / 2, d.PI_EDGE_GAP + 9.0)
    approx(sum(bb(parts["pi.usb3"])[2:4]) / 2, d.PI_EDGE_GAP + 27.0)
    approx(sum(bb(parts["pi.eth"])[2:4]) / 2, d.PI_EDGE_GAP + 45.75)
    # header and connectors
    hdr = bb(parts["pi.header"])
    approx(hdr[5] - hdr[4], 8.5)
    approx(
        (hdr[0], hdr[1]),
        (d.PI_EDGE_GAP + d.PI_L - d.PI_HDR_X[1], d.PI_EDGE_GAP + d.PI_L - d.PI_HDR_X[0]),
    )
    for name in ("pi.csi", "pi.dsi"):
        b = bb(parts[name])
        approx(b[5] - b[4], 5.5)
    soc = bb(parts["pi.soc"])
    approx(
        ((soc[0] + soc[1]) / 2, (soc[2] + soc[3]) / 2),
        (d.pi_to_b(29.25, 32.5)[0], d.pi_to_b(29.25, 32.5)[1]),
        0.5,
    )
    approx(soc[5] - soc[4], 2.4)
    card = bb(parts["pi.sd_card"])
    approx(card[1], d.PI_EDGE_GAP + d.PI_L + 2.5)  # 2.5 past x_pi = 0, which is the high-X end
    approx(card[4], d.PI_TOP_Z + d.PI_T)  # under the board = above it in B


def test_hat_geometry(parts):
    pcb = parts["hat.pcb"]
    x0, x1, y0, y1, z0, z1 = bb(pcb)
    approx((x1 - x0, y1 - y0), (d.HAT_L, d.HAT_W), 0.1)
    approx(z1 - z0, 1.7, 0.03)
    approx(z0, d.Z_HAT_TOP, 0.01)  # component face at 22.49
    approx(z1, d.PI_TOP_Z - d.HAT_BOTTOM_ZPI, 0.03)  # underside 24.19
    # the HAT's holes are coaxial with the Pi's header-side holes
    for (xh, yh), (xp, yp) in zip(d.HAT_HOLES, d.PI_HOLES[2:]):
        hx, hy, _ = d.hat_to_b(xh, yh)
        px, py, _ = d.pi_to_b(xp, yp)
        approx((hx, hy), (px, py), 0.01)
        (hole,) = checks.holes_at(pcb, hx, hy)
        approx(hole.diameter, comp.HAT_HOLE_D, 0.005)
    # 40 housings, 2.6 square, standing on the plastic: 5.48 .. 19.48 (DESIGN.md)
    dup = bb(parts["hat.dupont"])
    approx((dup[4], dup[5]), (5.48, 19.48), 0.01)
    approx(dup[3] - dup[2], 2.54 + 2.6, 0.01)  # two rows 2.54 apart
    approx(dup[1] - dup[0], 19 * 2.54 + 2.6, 0.01)
    # its pin rows line up with the Pi's header rows (51.23 / 53.77 + 0.2 in Y)
    rows = (d.pi_to_b(0, 51.23)[1], d.pi_to_b(0, 53.77)[1])
    approx((dup[2] + 1.3, dup[3] - 1.3), rows, 0.03)
    # terminal block edge at X 85.7 (DESIGN.md), the jack bay starts at 86.2
    term = bb(parts["hat.terminal"])
    approx(term[1], 85.7, 0.05)
    assert term[1] < d.JACK_X - d.JACK_BODY_W / 2


def test_hat_boxes_match_the_vendor_mesh():
    if not comp.HAT_OBJ_PATH.exists():
        pytest.skip("vendor HAT mesh (scratch/pedal/*.obj) not present")
    derived = comp.derive_hat_boxes()
    for key, got in derived.items():
        for axis, (g, w) in enumerate(zip(got, comp.HAT_BOXES[key])):
            assert g == pytest.approx(w, abs=0.02), (key, axis)
    mesh = comp.hat_mesh()
    lo, hi = mesh.bounds
    # in B the mesh spans the board's far end (x_hat 64.91) to the terminal block's end (-0.48)
    approx(lo[0], d.hat_to_b(comp.HAT_BOXES["pcb"][0][1], 0, 0)[0], 0.02)
    approx(hi[0], d.hat_to_b(comp.HAT_BOXES["terminal"][0][0], 0, 0)[0], 0.02)


def test_jacks(parts, keepouts):
    for side, wall_y, sign in (("in", 0.0, -1.0), ("out", d.INNER_W, 1.0)):
        body = bb(parts[f"jack_{side}.body"])
        approx((body[0], body[1]), (d.JACK_X - d.JACK_BODY_W / 2, d.JACK_X + d.JACK_BODY_W / 2))
        approx((body[4], body[5]), (d.JACK_Z - d.JACK_BODY_H / 2, d.JACK_Z + d.JACK_BODY_H / 2))
        # front plate on the wall's inner face, body running into the cavity
        if side == "in":
            approx((body[2], body[3]), (0.0, d.JACK_BODY_L))
        else:
            approx((body[2], body[3]), (d.INNER_W - d.JACK_BODY_L, d.INNER_W))
        bush = parts[f"jack_{side}.bushing"]
        bx = bb(bush)
        approx(bx[1] - bx[0], d.JACK_BUSHING_D, 0.01)
        approx(bx[5] - bx[4], d.JACK_BUSHING_D, 0.01)
        approx((bx[0] + bx[1]) / 2, d.JACK_X)
        approx((bx[4] + bx[5]) / 2, d.JACK_Z)
        assert d.JACK_BUSHING_D < d.JACK_HOLE_D  # clears the printed hole
        wy = bb(parts[f"jack_{side}.washer"])
        approx(wy[1] - wy[0], d.JACK_NUT_OD, 0.01)
        approx(wy[3] - wy[2], 1.0, 0.01)
        # the washer's seat is the spot face: 0.4 into the 2.0 wall
        seat = wall_y + sign * (d.WALL - d.JACK_SPOTFACE_DEPTH)
        approx(wy[2] if side == "out" else wy[3], seat, 0.01)
        nut = bb(parts[f"jack_{side}.nut"])
        approx(nut[3] - nut[2], 3.2, 0.01)
        approx(nut[5] - nut[4], 14.0, 0.01)  # across flats, flats up and down
        # the keep-out below the body: lugs face the floor
        lugs = bb(keepouts[f"jack_{side}.lugs"])
        approx(lugs[5], body[4])
        approx(lugs[5] - lugs[4], d.JACK_LUG_DROP)
    # IN against the IO wall, OUT against the +Y wall, mirror images about the cavity's middle
    a, b = bb(parts["jack_in.body"]), bb(parts["jack_out.body"])
    approx(a[2] + b[3], d.INNER_W)
    gap = bb(keepouts["wires.jack_gap"])
    approx((gap[2], gap[3]), (a[3], b[2]))
    approx((gap[4], gap[5]), (8.0, 34.0))
    # the jacks sit in the bay between the HAT's terminal block and the display duponts
    assert bb(parts["hat.terminal"])[1] < a[0] and a[1] < bb(parts["lcd.dupont"])[0]


def test_screw_heads(parts):
    io = [parts["screw.pi_io1"], parts["screw.pi_io2"]]
    got = sorted(
        (round((bb(s)[0] + bb(s)[1]) / 2, 2), round((bb(s)[2] + bb(s)[3]) / 2, 2)) for s in io
    )
    want = sorted(
        (round(d.pi_to_b(x, y)[0], 2), round(d.pi_to_b(x, y)[1], 2))
        for x, y in d.PI_HOLES
        if y < 10
    )
    assert got == want
    for s in io:
        approx(
            (bb(s)[4], bb(s)[5]), (35.5, 38.0)
        )  # on the Pi's underside, 0.9 below the display back
        approx(bb(s)[1] - bb(s)[0], 4.5)
    lcd = [parts[f"screw.lcd{i}"] for i in (1, 2, 3)]
    centres = {
        (round((bb(s)[0] + bb(s)[1]) / 2, 2), round((bb(s)[2] + bb(s)[3]) / 2, 2)) for s in lcd
    }
    pin_corner = d.lcd_to_b(d.LCD_HOLE_X_F, d.LCD_HOLE_Y_T)
    assert (
        round(pin_corner[0], 2),
        round(pin_corner[1], 2),
    ) not in centres  # that corner has the pin
    assert len(centres) == 3
    for s in lcd:
        approx((bb(s)[4], bb(s)[5]), (36.4, 38.9))


def test_plugs_sit_in_their_ports(parts, plugs):
    # each overmold starts at its port's front face and extends outward
    usbc = bb(plugs["plug.usb_c.overmold"])
    approx(usbc[3], bb(parts["pi.usb_c"])[2])
    approx(usbc[3] - usbc[2], 22.0)
    approx(usbc[1] - usbc[0], 12.0)
    approx(usbc[5] - usbc[4], 6.6)
    # metal tongue inside the receptacle
    metal, port = bb(plugs["plug.usb_c.metal"]), bb(parts["pi.usb_c"])
    assert (
        metal[0] >= port[0] and metal[1] <= port[1] and metal[4] >= port[4] and metal[5] <= port[5]
    )
    assert metal[2] == pytest.approx(port[2], abs=0.01)
    approx(metal[1] - metal[0], 8.3)
    for name in ("hdmi0", "hdmi1"):
        p, port = bb(plugs[f"plug.{name}.overmold"]), bb(parts[f"pi.{name}"])
        approx(p[3], port[2])
        approx((p[1] - p[0], p[5] - p[4], p[3] - p[2]), (10.6, 7.0, 20.0))
    p = bb(plugs["plug.audio.overmold"])
    approx(p[3], bb(parts["pi.audio"])[2])
    approx((p[1] - p[0], p[3] - p[2]), (7.0, 20.0))
    for name in ("usb2", "usb3"):
        for tag in "ab":
            p = bb(plugs[f"plug.{name}{tag}.overmold"])
            approx(p[1], bb(parts[f"pi.{name}"])[0])
            approx((p[1] - p[0], p[3] - p[2], p[5] - p[4]), (25.0, 16.0, 8.0))
    p = bb(plugs["plug.eth.overmold"])
    approx(p[1], bb(parts["pi.eth"])[0])
    approx((p[1] - p[0], p[3] - p[2], p[5] - p[4]), (25.0, 12.0, 14.0))


def test_keepouts(parts, keepouts):
    lw = bb(keepouts["lcd.dupont_wires"])
    dup = bb(parts["lcd.dupont"])
    approx((lw[0], lw[1], lw[2], lw[3]), (dup[0], dup[1], dup[2], dup[3]))
    approx((lw[4], lw[5]), (0.0, dup[4]))
    hw = bb(keepouts["hat.dupont_wires"])
    hd = bb(parts["hat.dupont"])
    approx((hw[0], hw[1], hw[2], hw[3]), (hd[0], hd[1], hd[2], hd[3]))
    approx((hw[4], hw[5]), (0.0, hd[4]))
    assert hw[5] - hw[4] == pytest.approx(5.48, abs=0.01)  # 5.5 mm of room to turn
    hs = bb(keepouts["pi.heatsink"])
    approx((hs[1] - hs[0], hs[3] - hs[2], hs[5] - hs[4]), (14.0, 14.0, 6.0))
    approx(hs[5], d.PI_TOP_Z - 2.4)  # its base sits on the SoC's top, 2.4 below the Pi's top face
    tails = bb(keepouts["pi.bottom_tails"])
    approx((tails[4], tails[5]), (d.PI_TOP_Z + d.PI_T, d.PI_TOP_Z + d.PI_T + d.PI_BOTTOM_TAILS))


# ---------------------------------------------------------------------------------------------
# Clashes between the bought parts
# ---------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def bare_report(parts, plugs, keepouts):
    return asm.check({}, parts=parts, plugs=plugs, keepouts=keepouts)


def test_no_unexplained_component_clashes(bare_report):
    assert bare_report.ok and bare_report.components_ok, bare_report.table()
    clashing = {
        frozenset({r.a, r.b}) for r in bare_report.components if r.status in ("FINDING", "MATED")
    }
    # open findings are exactly the documented ones, nothing new
    assert {k for k in clashing if k in asm.KNOWN_FINDINGS} == {
        frozenset({r.a, r.b}) for r in bare_report.findings
    }
    mated = {frozenset({r.a, r.b}) for r in bare_report.components if r.status == "MATED"}
    assert frozenset({"pi.header", "hat.female"}) in mated
    assert frozenset({"plug.usb_c.metal", "pi.usb_c"}) in mated


def test_jacks_clear_terminal_block_duponts_and_each_other(parts):
    for side in ("in", "out"):
        j = parts[f"jack_{side}.body"]
        assert j.distance_to(parts["hat.terminal"]) > 1.0
        assert j.distance_to(parts["lcd.dupont"]) > 1.0
        for n in ("lcd.smd", "lcd.sd", "pi.sd_card", "hat.terminal_tails"):
            assert j.distance_to(parts[n]) > 0.5, n
    assert parts["jack_in.body"].distance_to(parts["jack_out.body"]) > 13.0


def test_heatsink_finding_is_real_and_height_dependent(parts, keepouts):
    # the HAT's middle 3.5 mm jack underside is only 2.78 above the SoC (4.46 for the speaker): a
    # heatsink up to 2.7 tall fits, the 6 mm one does not (frame B: the Pi hangs above the HAT)
    soc_face = bb(parts["pi.soc"])[4]  # the SoC's exposed face (lowest Z)
    approx(soc_face - bb(parts["hat.jack35_b"])[5], 2.78, 0.02)
    approx(soc_face - bb(parts["hat.speaker_a"])[5], 4.46, 0.02)
    assert (keepouts["pi.heatsink"] & parts["hat.jack35_b"]).volume > 100
    assert (keepouts["pi.heatsink"] & parts["hat.speaker_a"]).volume > 50


# ---------------------------------------------------------------------------------------------
# The harness, on a stand-in enclosure built from the components
# ---------------------------------------------------------------------------------------------
_OPENING = (
    "pi.usb",
    "pi.hdmi",
    "pi.audio",
    "pi.eth",
    "plug.",
    "jack_in.bushing",
    "jack_out.bushing",
    "jack_in.nut",
    "jack_out.nut",
    "jack_in.washer",
    "jack_out.washer",
)


def standin(parts, plugs, margin=0.4, *, extra_cut=None):
    """A box shell split at SPLIT_Z with openings grown by `margin` around every port, plug and jack
    fitting, posts under the Pi and HAT holes and bosses at the display holes."""
    shell = comp.box(
        -d.WALL, d.INNER_L + d.WALL, -d.WALL, d.INNER_W + d.WALL, -d.FLOOR_T, d.Z_TOP
    ) - comp.box(0, d.INNER_L, 0, d.INNER_W, 0, d.Z_BEZEL_UNDER)
    for name, s in {**parts, **plugs}.items():
        if name.startswith(_OPENING):
            b = s.bounding_box()
            shell = shell - comp.box(
                b.min.X - margin,
                b.max.X + margin,
                b.min.Y - margin,
                b.max.Y + margin,
                b.min.Z - margin,
                b.max.Z + margin,
            )
    for cut in extra_cut or []:
        shell = shell - cut
    base = shell & comp.box(-10, 130, -10, 80, -5, d.SPLIT_Z)
    lid = shell & comp.box(-10, 130, -10, 80, d.SPLIT_Z, 60)
    for xp, yp in d.PI_HOLES[:2]:
        x, y, _ = d.pi_to_b(xp, yp)
        base = base + comp.cyl_z(x, y, 0, d.PI_TOP_Z, d.POST_IO_D)
    for xh, yh in d.HAT_HOLES:
        x, y, _ = d.hat_to_b(xh, yh)
        base = base + comp.cyl_z(x, y, 0, d.Z_HAT_TOP, d.POST_HDR_D)
    for xf, yt in comp.lcd_hole_xy():
        x, y, _ = d.lcd_to_b(xf, yt)
        lid = lid + comp.cyl_z(x, y, d.Z_LCD_FRONT, d.Z_BEZEL_UNDER + 0.1, d.BOSS_D)
    return {"base": base, "lid": lid}


@pytest.fixture(scope="module")
def good(parts, plugs, keepouts):
    enc = standin(parts, plugs)
    return enc, asm.check(enc, parts=parts, plugs=plugs, keepouts=keepouts)


def row(report, a, b):
    return next((r for r in report.rows if r.a == a and r.b == b), None)


def test_harness_passes_a_clean_standin_and_lists_contacts(good):
    _, rep = good
    assert rep.ok, rep.table()
    contacts = {(r.a, r.b) for r in rep.rows if r.status == "CONTACT"}
    for pair in (
        ("base", "pi.pcb"),
        ("base", "hat.pcb"),
        ("lid", "lcd.pcb"),
        ("base", "jack_in.body"),
        ("base", "jack_out.body"),
        ("lid", "jack_in.body"),
    ):
        assert pair in contacts, pair
    # the Pi's PCB sits 0.2 from the walls (the lid's skirt is the nearest printed face)
    r = row(rep, "lid", "pi.pcb")
    assert r is not None and r.gap == pytest.approx(0.2, abs=0.01)
    # glass gap at nominal: bezel underside 0.35 over the glass
    r = row(rep, "lid", "lcd.glass")
    assert r is not None and r.gap == pytest.approx(d.Z_BEZEL_UNDER - d.Z_GLASS_TOP, abs=0.01)
    # ports sit in 0.4 openings
    assert rep.min_gap("base", r"^pi\.usb_c$") == pytest.approx(0.4, abs=0.01)
    # near-misses are reported: the post is 0.05 from an SMD part on the HAT's component face
    r = row(rep, "base", "hat.smd_hole_a")
    assert r is not None and r.status == "TIGHT" and r.gap == pytest.approx(0.05, abs=0.02)
    # the table prints
    text = rep.table()
    assert "CONTACT" in text and "printed vs bought: OK" in text


def test_harness_flags_a_clash_and_a_touch(parts, plugs, keepouts):
    # openings with no margin: plug faces touch the printed walls
    rep = asm.check(
        standin(parts, plugs, margin=0.0),
        parts=parts,
        plugs=plugs,
        keepouts=keepouts,
        include_components=False,
    )
    assert not rep.ok
    assert {r.status for r in rep.failures} == {"TOUCH"}
    assert any(r.b.startswith("plug.") for r in rep.failures)
    # openings 0.3 too small: the walls cut into the plugs
    rep = asm.check(
        standin(parts, plugs, margin=-0.3),
        parts=parts,
        plugs=plugs,
        keepouts=keepouts,
        include_components=False,
    )
    clash = {r.b for r in rep.failures if r.status == "CLASH"}
    assert "plug.usb_c.overmold" in clash and "pi.usb_c" in clash


def test_harness_flags_a_keepout_and_a_stray_block(parts, plugs, keepouts):
    # a rib dropped into the gap between the jacks, and a block through the Pi
    rib = comp.box(95, 99, 30, 32, 10, 30)
    block = comp.box(40, 44, 20, 24, 30, 36)
    rep = asm.check(
        {"rib": rib, "block": block},
        parts=parts,
        plugs=plugs,
        keepouts=keepouts,
        include_components=False,
    )
    got = {(r.a, r.b, r.status) for r in rep.rows}
    assert ("rib", "wires.jack_gap", "CLASH") in got
    assert ("block", "pi.pcb", "CLASH") in got
    assert not rep.ok


def test_harness_on_nothing_and_on_far_parts(parts, plugs, keepouts):
    rep = asm.check(
        {"far": comp.box(200, 210, 0, 5, 0, 5)},
        parts=parts,
        plugs=plugs,
        keepouts=keepouts,
        include_components=False,
    )
    assert rep.ok and not rep.rows
    assert "(nothing within 2 mm)" in rep.table()


def test_cli_runs_on_the_components_alone(capsys):
    assert asm.main(["--no-render", "--no-step", "--no-enclosure"]) == 0
    out = capsys.readouterr().out
    assert "printed vs bought: OK" in out and "open finding(s)" in out
    assert str(asm.check({}, include_components=False)).startswith("printed parts: (none)")


def test_enclosure_module_if_present(parts, plugs, keepouts):
    printed = asm.load_enclosure()
    if printed is None:
        pytest.skip("cad.gen.pedal.enclosure does not exist yet")
    rep = asm.check(printed, parts=parts, plugs=plugs, keepouts=keepouts)
    assert rep.ok, rep.table()


# ---------------------------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------------------------
def _leaf_labels(shape):
    kids = list(shape.children)
    return [shape.label] if not kids else [lab for k in kids for lab in _leaf_labels(k)]


def test_step_export_has_named_bodies(tmp_path, good, parts, plugs):
    enc, _ = good
    step, ko = asm.export_assembly_step(enc, tmp_path)
    assert step.stat().st_size > 10_000 and ko.stat().st_size > 1_000
    labels = _leaf_labels(import_step(step))
    # the writer turns the dots into underscores; every body is there once and named
    assert len(labels) == len(enc) + len(parts) + len(plugs)
    assert {"base", "lid", "pi_usb_c", "hat_pcb", "jack_in_body", "plug_usb_c_overmold"} <= set(
        labels
    )
    assert len(_leaf_labels(import_step(ko))) == len(comp.keepouts())


def test_hat_mesh_export(tmp_path):
    if not comp.HAT_OBJ_PATH.exists():
        pytest.skip("vendor HAT mesh (scratch/pedal/*.obj) not present")
    out = comp.export_hat_mesh(tmp_path / "hat.stl")
    assert out is not None and out.stat().st_size > 1_000_000
    import trimesh

    lo, hi = trimesh.load_mesh(out).bounds
    # the board and its tallest parts (terminal block up, female header down) in frame B
    approx(hi[2], d.Z_HAT_TOP + 9.11, 0.05)  # female header bottom, 9.11 below the component face
    approx(lo[2], d.Z_HAT_TOP - 8.96, 0.05)  # pass-through pin tips, 8.96 above it
    approx(hi[0], d.hat_to_b(-0.48, 0, 0)[0], 0.05)  # terminal block end
