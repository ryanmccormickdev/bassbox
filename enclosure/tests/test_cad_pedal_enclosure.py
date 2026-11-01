"""Bassbox pedal enclosure r1 (cad/gen/pedal/enclosure.py): the printed lid, base and spacer."""

from __future__ import annotations

import importlib.util
import math

import pytest

pytest.importorskip("build123d")

from build123d import export_stl

from cad.gen import checks
from cad.gen.pedal import dims as D
from cad.gen.pedal import enclosure as E

TOL = 0.005


@pytest.fixture(scope="module")
def parts():
    return E.build()


@pytest.fixture(scope="module")
def printed(parts):
    return {n: E.print_pose(n, p) for n, p in parts.items()}


def _bb(part):
    b = part.bounding_box()
    return (b.min.X, b.min.Y, b.min.Z, b.max.X, b.max.Y, b.max.Z)


def _air(part, p, axis="Y"):
    return E.wall_thickness(part, p, axis) == 0.0


def test_outline_matches_dims(parts):
    x0, y0 = -D.WALL, -D.WALL
    x1, y1 = D.INNER_L + D.WALL, D.INNER_W + D.WALL
    assert _bb(parts["lid"]) == pytest.approx((x0, y0, D.SPLIT_Z, x1, y1, D.Z_TOP), abs=TOL)
    assert _bb(parts["base"]) == pytest.approx((x0, y0, -D.FLOOR_T, x1, y1, E.SNAP_TOP), abs=TOL)
    assert x1 - x0 == pytest.approx(D.OUTER_L) and y1 - y0 == pytest.approx(D.OUTER_W)
    assert D.Z_TOP + D.FLOOR_T == pytest.approx(D.OUTER_H)


def test_window_is_the_active_area_plus_margin(parts):
    x0, x1, y0, y1 = E.window_rect()
    ax0, ay0, _ = D.lcd_to_b((D.LCD_PCB_W - D.LCD_AA_W) / 2, D.LCD_AA_Y_T)
    m = D.WINDOW_MARGIN
    assert (x0, x1, y0, y1) == pytest.approx((ax0 - m, ax0 + D.LCD_AA_L + m, ay0 - m, ay0 + D.LCD_AA_W + m))
    # DESIGN: borders 15.2 / 16.9 along X, centred across
    assert x0 + D.WALL == pytest.approx(15.2, abs=0.01)
    assert D.INNER_L + D.WALL - x1 == pytest.approx(16.92, abs=0.01)
    assert y0 + D.WALL == pytest.approx(D.INNER_W + D.WALL - y1, abs=0.01)
    lid = parts["lid"]
    z = D.Z_BEZEL_UNDER + 0.3  # in the plate, below the window chamfer
    assert E.wall_thickness(lid, (5.0, 31.0, z), "X") == pytest.approx(x0 + D.WALL, abs=TOL)
    assert E.wall_thickness(lid, (105.0, 31.0, z), "X") == pytest.approx(D.INNER_L + D.WALL - x1, abs=TOL)
    assert E.wall_thickness(lid, (50.0, 1.0, z), "Y") == pytest.approx(y0 + D.WALL, abs=TOL)
    assert _air(lid, (50.0, 31.0, z), "Z")
    # the plate is TOP_T thick and its underside is the bezel underside
    assert E.wall_thickness(lid, (50.0, 1.0, 45.5), "Z") == pytest.approx(D.TOP_T, abs=TOL)


def test_glass_gap():
    assert E.glass_gap() > 0.2


def test_pi_posts_on_dims_positions(parts):
    base = parts["base"]
    posts = E.pi_posts()
    expected = sorted(D.pi_to_b(x, y)[:2] for x, y in D.PI_HOLES if y < 10)
    # flattened: newer pytest releases reject approx() on a list of tuples
    assert [c for p in sorted(posts) for c in p] == pytest.approx([c for p in expected for c in p])
    for x, y in posts:
        assert checks.hole_diameters_at(base, x, y) == pytest.approx([D.POST_TAP_D])
        tap = checks.holes_at(base, x, y)[0]
        assert tap.center[2] + tap.length == pytest.approx(D.PI_TOP_Z, abs=0.02)
        assert tap.length == pytest.approx(D.POST_TAP_DEPTH, abs=0.02)
        bosses = [c for c in checks.cylinders(base) if not c.is_hole
                  and abs(c.center[0] - x) < TOL and abs(c.center[1] - y) < TOL]
        assert any(c.diameter == pytest.approx(D.POST_IO_D) for c in bosses)
        assert E.wall_thickness(base, (x + 2.0, y, 20.0), "Z") == pytest.approx(D.PI_TOP_Z + D.FLOOR_T,
                                                                               abs=0.02)


def test_hat_posts(parts):
    base = parts["base"]
    for x, y in E.hat_posts():
        assert checks.hole_diameters_at(base, x, y) == pytest.approx([D.POST_TAP_D])
        assert E.wall_thickness(base, (x + 2.0, y, 10.0), "Z") == pytest.approx(D.Z_HAT_TOP + D.FLOOR_T,
                                                                               abs=0.02)
        bosses = [c for c in checks.cylinders(base) if not c.is_hole
                  and abs(c.center[0] - x) < TOL and abs(c.center[1] - y) < TOL]
        assert any(c.diameter == pytest.approx(E.POST_HDR_D) for c in bosses)


def test_display_bosses_and_pin(parts):
    lid = parts["lid"]
    holes = E.display_holes()
    assert sum(pin for *_xy, pin in holes) == 1
    for x, y, pin in holes:
        if pin:
            pins = [c for c in checks.cylinders(lid) if not c.is_hole and c.diameter == pytest.approx(D.PIN_D)
                    and abs(c.center[0] - x) < TOL and abs(c.center[1] - y) < TOL]
            assert pins and pins[0].center[2] == pytest.approx(D.Z_LCD_FRONT - D.PIN_L, abs=0.01)
            assert checks.hole_diameters_at(lid, x, y) == []
        else:
            tap = checks.holes_at(lid, x, y)
            assert [round(c.diameter, 4) for c in tap] == [D.BOSS_TAP_D]
            assert tap[0].center[2] == pytest.approx(D.Z_LCD_FRONT, abs=0.02)
            assert D.Z_TOP - (tap[0].center[2] + tap[0].length) == pytest.approx(1.0, abs=0.02)
        # the boss face is the datum: it reaches the display's front face
        assert E.wall_thickness(lid, (x + 2.0, y, 42.0), "Z") == pytest.approx(D.Z_TOP - D.Z_LCD_FRONT, abs=0.02)


def test_bosses_clear_the_glass_and_fpc():
    r = D.BOSS_D / 2
    gx0 = D.DISPLAY_X0 + D.RTP_OD_Y_T
    gx1 = gx0 + D.RTP_OD_L
    for x, y, _pin in E.display_holes():
        assert x + r < gx0 or x - r > gx1
        assert y + r < 9.0 or y - r > 53.0  # the LCD's FPC at the jack end


def test_jack_holes_and_spot_faces(parts):
    base = parts["base"]
    for y in (0.0, D.INNER_W):
        hole = checks.holes_through(base, (D.JACK_X, y, D.JACK_Z), (0, 1, 0))
        assert any(c.diameter == pytest.approx(D.JACK_HOLE_D) for c in hole)
    assert E.wall_thickness(base, (D.JACK_X + 7.5, -1.0, D.JACK_Z), "Y") == pytest.approx(
        D.WALL - D.JACK_SPOTFACE_DEPTH, abs=TOL)
    # teardrop: open above the circle, closed above the apex
    apex = D.JACK_Z + D.JACK_HOLE_D / 2 / math.sin(math.radians(90 - E.OVERHANG_DEG))
    assert _air(base, (D.JACK_X, -1.0, apex - 0.2), "X")
    assert not _air(base, (D.JACK_X, -1.0, apex + 0.2), "X")
    assert apex < D.SPLIT_Z


def test_openings_cross_the_split(parts):
    lid, base = parts["lid"], parts["base"]
    for name, (x0, x1, z0, z1) in E.io_openings().items():
        xc = (x0 + x1) / 2
        assert z0 < D.SPLIT_Z < z1, name
        assert _air(base, (xc, -0.6, D.SPLIT_Z - 0.3), "X") and _air(lid, (xc, -0.6, D.SPLIT_Z + 0.3), "X")
        assert not _air(lid, (xc, -0.6, z1 + 0.3), "X")
        assert not _air(base, (xc, -0.6, z0 - 0.3), "X")
    # the recessed panel leaves 1.2 of wall in both parts
    assert E.wall_thickness(base, (50.0, -0.6, 27.0), "Y") == pytest.approx(D.WALL - D.IO_PANEL_RECESS, abs=TOL)
    assert E.wall_thickness(lid, (50.0, -0.6, 37.5), "Y") == pytest.approx(D.WALL - D.IO_PANEL_RECESS, abs=TOL)
    # USB-end slots: open to the top of the base, a notch in the lid over each port's top face
    for name, (y0, y1, zb) in E.end_port_slots().items():
        yc = (y0 + y1) / 2
        assert _air(base, (-1.0, yc, zb + 0.3), "Y"), name
        assert not _air(base, (-1.0, yc, zb - 0.3), "Y"), name
        assert _air(lid, (-1.0, yc, D.SPLIT_Z + D.IO_MARGIN - 0.1), "Y"), name
        assert not _air(lid, (-1.0, yc, D.SPLIT_Z + D.IO_MARGIN + 0.1), "Y"), name
    # the pillars between the slots keep the full wall
    assert E.wall_thickness(base, (-1.0, 18.2, 25.0), "X") == pytest.approx(D.WALL, abs=TOL)


def test_rabbet(parts):
    lid, base = parts["lid"], parts["base"]
    z = D.SPLIT_Z + 0.7
    assert E.wall_thickness(base, (50.0, D.INNER_W + 0.5, z), "Y") == pytest.approx(D.RABBET_T, abs=TOL)
    assert _air(lid, (50.0, D.INNER_W + 0.5, z), "Y")
    assert E.wall_thickness(lid, (50.0, D.INNER_W + 1.6, z), "Y") == pytest.approx(
        D.WALL - D.RABBET_T - D.RABBET_GAP, abs=TOL)
    # no lip over the slots or the IO panel
    assert _air(base, (-0.5, 10.0, z), "Y")
    assert _air(base, (50.0, -0.5, z), "X")
    lap = lid & base
    assert (lap.volume if lap else 0.0) < 0.01


def test_snaps(parts):
    lid, base = parts["lid"], parts["base"]
    s = E.snap()
    assert s.strain < 0.02
    assert s.z_tip <= 38.4 < D.Z_LCD_BACK
    assert s.z_ret - s.z_root == pytest.approx(D.SNAP_LEN)
    for wall, c in E.snap_sites():
        if wall == "hdr":
            tab = (c, D.INNER_W - E.SNAP_GAP - D.SNAP_T / 2, 30.0)
            window = (c, D.INNER_W + 1.0, (s.win_z0 + s.win_z1) / 2)
            barb = (c, D.INNER_W + D.SNAP_BARB / 2, s.z_ret + 0.1)
            axis = "Y"
        else:
            tab = (D.INNER_L - E.SNAP_GAP - D.SNAP_T / 2, c, 30.0)
            window = (D.INNER_L + 1.0, c, (s.win_z0 + s.win_z1) / 2)
            barb = (D.INNER_L + D.SNAP_BARB / 2, c, s.z_ret + 0.1)
            axis = "X"
        assert E.wall_thickness(base, tab, axis) == pytest.approx(D.SNAP_T, abs=TOL)
        assert _air(lid, window, axis)
        assert not _air(base, barb, axis)  # the barb reaches into the window
    assert lid.distance_to(E.snap_tabs()) > 0.05


def test_walls(parts):
    for name, where, t in E.wall_report(parts):
        if where in E.RABBET_SAMPLES:
            assert t >= 0.85 - TOL, where
        else:
            assert t >= E.WALL_MIN - TOL, (name, where, t)


def test_single_watertight_bodies(printed, tmp_path):
    for name, part in printed.items():
        assert part.is_valid and len(part.solids()) == 1, name
        stl = tmp_path / f"{name}.stl"
        export_stl(part, stl, tolerance=0.01, angular_tolerance=0.1)
        rep = checks.mesh_report(stl)
        assert rep.watertight and rep.bodies == 1, name


def test_print_pose(parts, printed):
    for name, part in printed.items():
        b = part.bounding_box()
        assert (b.min.X, b.min.Y, b.min.Z) == pytest.approx((0, 0, 0), abs=1e-6), name
        assert checks.bbox_size(part) == pytest.approx(checks.bbox_size(parts[name]), abs=1e-6)
    # lid face down: the top surface on the bed, the pin pointing up
    x, y, _ = next(h for h in E.display_holes() if h[2])
    px, py, pz = E.to_print("lid", (x, y, D.Z_LCD_FRONT - D.PIN_L))
    assert pz == pytest.approx(D.Z_TOP - D.Z_LCD_FRONT + D.PIN_L)
    assert not _air(printed["lid"], (px, py, pz - 0.1), "Z")
    assert _air(printed["lid"], (px, py, pz + 0.1), "Z")
    assert printed["lid"].bounding_box().max.Z == pytest.approx(D.Z_TOP - D.SPLIT_Z)


def test_overhangs_only_designed_ones(printed):
    for name in ("lid", "base"):
        kinds = {o.kind for o in E.overhangs(name, printed[name])}
        assert "other" not in kinds, name
    barbs = [o for o in E.overhangs("base", printed["base"]) if o.kind == "barb ledge"]
    assert len(barbs) == 4


def test_no_slivers(printed):
    for name in ("lid", "base", "spacer"):
        sl = E.slivers(name, printed[name], step=1.0)
        assert sl["knife_edges"] == [], name
        assert sl["small_pieces"] == [], name


def test_spacer(parts):
    sp = parts["spacer"]
    assert checks.bbox_size(sp) == pytest.approx((E.SPACER_OD, E.SPACER_OD, E.SPACER_L), abs=TOL)
    assert sp.bounding_box().max.Z == pytest.approx(D.PI_TOP_Z)
    x, y = min(E.hat_posts())
    assert checks.hole_diameters_at(sp, x, y) == pytest.approx([E.SPACER_ID])
    assert E.SPACER_L <= D.PI_TOP_Z - (D.Z_HAT_TOP + D.HAT_T)


@pytest.mark.skipif(importlib.util.find_spec("cad.gen.pedal.assembly") is None,
                    reason="the clash harness is not in this tree")
def test_no_clash_with_bought_parts(parts):
    from cad.gen.pedal import assembly

    report = assembly.check(parts, include_components=False)
    assert report.ok, [f"{r.a} / {r.b}: {r.status}" for r in report.failures]
