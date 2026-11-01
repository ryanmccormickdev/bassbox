"""Export, render, slice and check the bassbox pedal enclosure (DRAFT r1). Units mm.

    python -m cad.gen.pedal [--no-slice] [--no-render] [--no-assembly]

Writes to `out/pedal/` (the folder `cad.gen.pedal.assembly` also writes its renders to;
only the files named below are this module's): per part `<part>.stl` + `<part>.3mf` in its print
pose, `pedal-plate.3mf` (lid + base + 2 spacers on the Ender-3 V2 Neo's 220 x 220 bed),
`enclosure-installed.step` (the three parts in frame B, named), PNG renders (`<part>-print.png`,
`<part>-installed.png`, `enclosure-details.png`), the plate's G-code and `enclosure-report.json` /
`README-enclosure.md`. It slices the plate with `cad.gen.ender` (V2 Neo, PETG) and prints time and
grams, runs the checks (watertight single bodies, walls, knife edges, small section pieces,
overhangs in the print pose) and, if `cad.gen.pedal.assembly` exists, its clash check.
"""

from __future__ import annotations

import argparse
import copy
import importlib
import importlib.util
import json
import logging
import time
from collections.abc import Sequence
from pathlib import Path

from build123d import Compound, Mesher, Part, export_step, export_stl

from cad.gen import checks, cli_logging, ender, export
from cad.gen.pedal import dims as D
from cad.gen.pedal import enclosure as E

log = logging.getLogger(__name__)

OUT = export.DRAFTS_DIR / "pedal"
PLATE = "pedal-plate"
MATERIAL = "PETG"
PRINTER = "V2NEO"
COLORS = {"lid": "#8fb3cc", "base": "#a9b8c4", "spacer": "#e0b04a"}
OWN_GLOBS = ("lid*", "base*", "spacer*", f"{PLATE}*", "enclosure-*", "README-enclosure.md")


# --- files -------------------------------------------------------------------------------------------


def write_stl(part: Part, path: Path) -> Path:
    export_stl(part, path, tolerance=export.STL_TOL, angular_tolerance=export.STL_ANGULAR_TOL)
    return path


def write_3mf(parts: Sequence[tuple[str, Part]], path: Path) -> Path:
    mesher = Mesher()
    for name, part in parts:
        mesher.add_shape(part, linear_deflection=export.STL_TOL,
                         angular_deflection=export.STL_ANGULAR_TOL, part_number=name)
    mesher.write(str(path))
    return path


def plate_items(printed: dict[str, Part]) -> list[export.PlateItem]:
    """Lid and base side by side in Y, two spacers to their right, the group centred on the bed."""
    prn = ender.printer_of(PRINTER)
    size = {n: checks.bbox_size(p) for n, p in printed.items()}
    lx, ly, _ = size["lid"]
    bx, by, _ = size["base"]
    sx, sy, _ = size["spacer"]
    w = max(lx, bx) + E.PLATE_GAP + sx
    h = ly + E.PLATE_GAP + by
    cx, cy = prn.centre
    x0, y0 = cx - w / 2, cy - h / 2
    xs = x0 + max(lx, bx) + E.PLATE_GAP
    return [
        export.PlateItem("lid", printed["lid"], x0, y0),
        export.PlateItem("base", printed["base"], x0, y0 + ly + E.PLATE_GAP),
        export.PlateItem("spacer-1", printed["spacer"], xs, cy - sy - E.PLATE_GAP / 2),
        export.PlateItem("spacer-2", printed["spacer"], xs, cy + E.PLATE_GAP / 2),
    ]


def write_plate_3mf(items: Sequence[export.PlateItem], path: Path) -> Path:
    """`export.export_plate_3mf`, except each object is meshed where it was built and placed by its
    3MF build-item transform: meshing the lid after moving it to some bed positions gave a mesh
    lib3mf rejects (build123d rounds the moved vertices to 1e-6 before merging them)."""
    mesher = Mesher()
    for it in items:
        mesher.add_shape(it.part, linear_deflection=export.STL_TOL,
                         angular_deflection=export.STL_ANGULAR_TOL, part_number=it.name)
        build = mesher.model.GetBuildItems()
        last = None
        while build.MoveNext():
            last = build.GetCurrent()
        last.SetObjectTransform(mesher.wrapper.GetTranslationTransform(it.x, it.y, 0.0))
    mesher.write(str(path))
    return path


def write_installed_step(parts: dict[str, Part], path: Path) -> Path:
    children = []
    for name, part in parts.items():
        p = copy.copy(part)
        p.label = name
        children.append(p)
    export_step(Compound(label="pedal-enclosure", children=children), path,
                write_pcurves=export.STEP_PCURVES)
    return path


# --- renders -----------------------------------------------------------------------------------------

ISO_IO = ((-1.0, -1.5, 1.1), (0, 0, 1))  # from the USB-end / IO-side corner, above
ISO_HDR = ((1.0, 1.5, 1.1), (0, 0, 1))  # from the jack-end / +Y corner, above
TOP = ((0, 0, 1), (1, 0, 0))  # looking down, +X (the UI's top) up the page
BELOW = ((0.4, -0.6, -1.0), (0, 0, 1))
FRONT_IO = ((0, -1, 0), (0, 0, 1))
FRONT_USB = ((-1, 0, 0), (0, 0, 1))
FRONT_JACK = ((1, 0, 0), (0, 0, 1))
FRONT_HDR = ((0, 1, 0), (0, 0, 1))


def render(panels: Sequence[tuple[str, tuple, Sequence[tuple[Path, str]]]], png: Path, *,
           shape: tuple[int, int] | None = None, size: tuple[int, int] | None = None) -> Path:
    """A grid of panels, each (title, (view_vector, view_up), [(stl, colour), ...])."""
    import pyvista as pv

    pv.OFF_SCREEN = True
    shape = shape or (1, len(panels))
    size = size or (700 * shape[1], 620 * shape[0])
    cache: dict[Path, object] = {}
    pl = pv.Plotter(off_screen=True, shape=shape, window_size=size, border=False)
    for i, (title, (vec, up), meshes) in enumerate(panels):
        pl.subplot(i // shape[1], i % shape[1])
        for path, colour in meshes:
            m = cache.setdefault(path, pv.read(str(path)))
            pl.add_mesh(m, color=colour, smooth_shading=False, specular=0.2)
            pl.add_mesh(m.extract_feature_edges(feature_angle=30, boundary_edges=False,
                                                non_manifold_edges=False),
                        color="#26323a", line_width=1)
        pl.view_vector(vec, viewup=up)
        if sum(abs(v) > 0 for v in vec) == 1:
            pl.enable_parallel_projection()
        pl.reset_camera()
        pl.add_text(title, font_size=10)
    pl.screenshot(str(png))
    pl.close()
    return png


# Sections (frame B): title, the box kept (x0, x1, y0, y1, z0, z1; a cut face lies on the first
# bound of the axis looked along), view. The cut faces are real faces (B-rep booleans).
SECTIONS = (
    ("+Y snap, cut at X 21.5 (from -X): tab, barb in the lid window",
     (D.SNAPS_SIDE_X[0], D.SNAPS_SIDE_X[0] + 8, 48, 66, 16, 47), FRONT_USB),
    ("jack-end snap, cut at Y 9.8 (from -Y)",
     (98, 116, D.SNAPS_END_Y[0], D.SNAPS_END_Y[0] + 8, 16, 47), FRONT_IO),
    ("jack IN, cut at X 97.3 (from -X): teardrop, spot face",
     (D.JACK_X, D.JACK_X + 14, -3, 12, 4, 40), FRONT_USB),
    ("USB-C opening, cut at X 74 (from -X): panel recess, split",
     (74.0, 84.0, -3, 10, 20, 47), FRONT_USB),
    ("USB2 slot, cut at Y 9.2 (from -Y): open top, lid notch",
     (-3, 12, 9.2, 20, 12, 47), FRONT_IO),
    ("pin boss, cut at Y 3.62 (from -Y)",
     (-3, 18, 3.62, 12, 33, 47), FRONT_IO),
    ("jack-end boss, cut at Y 3.62 (from -Y)",
     (98, 116, 3.62, 12, 33, 47), FRONT_IO),
    ("HAT post (X 81.7), cut at Y 52.7 (from -Y)",
     (68, 116, 52.7, 66, -3, 40), FRONT_IO),
)
BOUGHT_IN_SECTIONS = {"lcd.pcb": "#27406b", "lcd.glass": "#7aa7c9", "pi.pcb": "#2f8f5b",
                      "hat.pcb": "#24262c", "jack_in.body": "#3b3f46", "jack_in.bushing": "#8a8f96",
                      "jack_in.washer": "#8a8f96", "jack_in.nut": "#6a6f76", "pi.usb_c": "#c3c8cf",
                      "plug.usb_c.overmold": "#e8782a", "pi.usb2": "#c3c8cf",
                      "plug.usb2a.overmold": "#e8782a", "plug.usb2b.overmold": "#e8782a",
                      "screw.lcd2": "#555a62", "screw.lcd3": "#555a62", "screw.lcd1": "#555a62",
                      "hat.smd_hole_a": "#9d7e52", "jack_out.body": "#3b3f46"}


def _bought() -> dict[str, Part]:
    if importlib.util.find_spec("cad.gen.pedal.components") is None:
        return {}
    comp = importlib.import_module("cad.gen.pedal.components")
    return {**comp.parts(), **comp.plugs()}


def section_stls(installed: dict[str, Part], work: Path) -> list[list[tuple[Path, str]]]:
    """Each SECTIONS box cut out of the lid, the base and the bought parts, as STLs."""
    work.mkdir(exist_ok=True)
    bought = {n: p for n, p in _bought().items() if n in BOUGHT_IN_SECTIONS}
    out = []
    for i, (_title, (x0, x1, y0, y1, z0, z1), _view) in enumerate(SECTIONS):
        keep = E._box(x0, x1, y0, y1, z0, z1)
        meshes = []
        for name, part in [*((n, installed[n]) for n in ("lid", "base")), *bought.items()]:
            cut = part & keep
            if cut is None or not cut.solids() or cut.volume < 1e-3:
                continue
            path = write_stl(cut, work / f"s{i}-{name}.stl")
            meshes.append((path, COLORS.get(name) or BOUGHT_IN_SECTIONS[name]))
        out.append(meshes)
    return out


def render_all(installed: dict[str, Part], installed_stl: dict[str, Path],
               print_stl: dict[str, Path], out: Path) -> list[Path]:
    pngs = []
    lid_p = [(print_stl["lid"], COLORS["lid"])]
    base_p = [(print_stl["base"], COLORS["base"])]
    pngs.append(render([
        ("lid, print pose (face down): iso", ISO_IO, lid_p),
        ("from above (bosses, pin)", TOP, lid_p),
        ("from below: the bed face (reads as installed)", ((0, 0, -1), (1, 0, 0)), lid_p)],
        out / "lid-print.png"))
    pngs.append(render([
        ("base, print pose (floor down): iso", ISO_IO, base_p),
        ("iso, jack end / +Y", ISO_HDR, base_p),
        ("from below (grille, foot rings)", ((0, 0, -1), (1, 0, 0)), base_p)],
        out / "base-print.png"))
    pngs.append(render([("spacer, print pose", ISO_IO, [(print_stl["spacer"], COLORS["spacer"])])],
                       out / "spacer-print.png", size=(600, 500)))
    li = [(installed_stl["lid"], COLORS["lid"])]
    ba = [(installed_stl["base"], COLORS["base"])]
    pngs.append(render([
        ("lid installed: top (UI top = +X, up the page)", TOP, li),
        ("IO side (player's right)", FRONT_IO, li),
        ("inside, from below", BELOW, li),
        ("jack end", FRONT_JACK, li)], out / "lid-installed.png", shape=(2, 2)))
    pngs.append(render([
        ("base installed: iso (USB end, IO side)", ISO_IO, ba),
        ("iso (jack end, +Y side)", ISO_HDR, ba),
        ("IO side", FRONT_IO, ba), ("USB end", FRONT_USB, ba), ("+Y side", FRONT_HDR, ba),
        ("top", TOP, ba)], out / "base-installed.png", shape=(2, 3)))
    both = li + ba
    pngs.append(render([("lid on base", ISO_IO, both), ("lid on base", ISO_HDR, both)],
                       out / "enclosure-installed.png"))
    secs = section_stls(installed, out / "_render")
    pngs.append(render([(t, v, m) for (t, _b, v), m in zip(SECTIONS, secs, strict=True)],
                       out / "enclosure-details.png", shape=(2, 4)))
    return pngs


# --- checks ------------------------------------------------------------------------------------------


def run_checks(installed: dict[str, Part], printed: dict[str, Part],
               stls: dict[str, Path]) -> dict:
    rep: dict = {"parts": {}}
    for name, part in printed.items():
        mesh = checks.mesh_report(stls[name])
        ib = installed[name].bounding_box()
        oh = E.overhangs(name, part) if name != "spacer" else []
        kinds: dict[str, int] = {}
        for o in oh:
            kinds[o.kind] = kinds.get(o.kind, 0) + 1
        sl = E.slivers(name, part)
        rep["parts"][name] = {
            "solids": len(part.solids()),
            "valid": part.is_valid,
            "watertight": mesh.watertight,
            "bodies": mesh.bodies,
            "triangles": mesh.triangles,
            "volume_mm3": round(part.volume, 1),
            "installed_bbox": [round(v, 3) for v in (ib.min.X, ib.min.Y, ib.min.Z,
                                                     ib.max.X, ib.max.Y, ib.max.Z)],
            "print_size": [round(v, 3) for v in checks.bbox_size(part)],
            "overhangs": kinds,
            "overhang_other": [[round(v, 2) for v in o.bbox] for o in oh if o.kind == "other"],
            "knife_edges": [[round(k.angle, 1), *(round(v, 2) for v in k.point)]
                            for k in sl["knife_edges"]],
            "small_pieces": [[round(z, 2), round(a, 2), round(x, 2), round(y, 2)]
                             for z, a, (x, y) in sl["small_pieces"]],
            "engraving_hits": len(sl["engraving_hits"]),
        }
    rep["walls"] = [{"part": n, "where": w, "mm": round(t, 3)} for n, w, t in E.wall_report(installed)]
    rep["walls_min"] = min(r["mm"] for r in rep["walls"] if r["where"] not in E.RABBET_SAMPLES)
    s = E.snap()
    rep["snap"] = {"z_root": round(s.z_root, 3), "z_retention": round(s.z_ret, 3),
                   "z_tip": s.z_tip, "window_z": [round(s.win_z0, 3), round(s.win_z1, 3)],
                   "strain_pct": round(100 * s.strain, 2), "tip_force_n": round(s.force_n, 2),
                   "close_force_n": round(s.insert_n, 2)}
    both = installed["lid"] & installed["base"]
    rep["lid_base_overlap_mm3"] = round(both.volume if both else 0.0, 4)
    rep["snap_clearance"] = round(installed["lid"].distance_to(E.snap_tabs()), 3)
    rep["glass_gap"] = round(E.glass_gap(), 3)
    rep["window"] = [round(v, 3) for v in E.window_rect()]
    return rep


def check_failures(rep: dict) -> list[str]:
    bad = []
    for name, r in rep["parts"].items():
        if not (r["valid"] and r["solids"] == 1 and r["watertight"] and r["bodies"] == 1):
            bad.append(f"{name}: valid {r['valid']}, solids {r['solids']}, watertight "
                       f"{r['watertight']}, bodies {r['bodies']}")
        for key in ("overhang_other", "knife_edges", "small_pieces"):
            if r[key]:
                bad.append(f"{name}: {key} {r[key][:6]}")
    if rep["walls_min"] < E.WALL_MIN - 1e-6:
        bad.append(f"thinnest wall {rep['walls_min']} < {E.WALL_MIN}")
    if rep["snap"]["strain_pct"] > 2.0:
        bad.append(f"snap strain {rep['snap']['strain_pct']} % > 2 %")
    if rep["lid_base_overlap_mm3"] > 0.01:
        bad.append(f"lid and base overlap {rep['lid_base_overlap_mm3']} mm^3")
    if rep["snap_clearance"] < 0.05:
        bad.append(f"snap tabs {rep['snap_clearance']} from the lid")
    if rep["glass_gap"] <= 0.2:
        bad.append(f"glass gap {rep['glass_gap']} <= 0.2")
    return bad


def slice_all(out: Path, plate: Path, stls: dict[str, Path]) -> dict:
    """The plate on the V2 Neo in PETG, then each part alone with automatic supports forced on
    (a probe: 0 support extrusions means the part needs none)."""
    res = ender.slice_ender(plate, out, material=MATERIAL, printer=PRINTER, centre=False)
    rep = {"plate": {"time": res.hours_minutes, "minutes": round(res.minutes), "grams": round(res.grams, 1),
                     "metres": round(res.metres, 2), "fits": res.fits, "warnings": list(res.warnings),
                     "gcode": str(res.gcode)}, "support_probe": {}}
    probe_dir = out / "_probe"
    probe_dir.mkdir(exist_ok=True)
    for name in ("lid", "base"):
        pr = ender.slice_ender(stls[name], probe_dir, material=MATERIAL, printer=PRINTER,
                               overrides={"support_material": "1", "support_material_auto": "1"})
        n = sum(v for k, v in pr.types.items() if k.lower().startswith("support"))
        rep["support_probe"][name] = {"support_extrusions": n, "grams_alone": round(pr.grams, 1)}
    return rep


def run_assembly(installed: dict[str, Part]) -> dict | None:
    if importlib.util.find_spec("cad.gen.pedal.assembly") is None:
        print("cad.gen.pedal.assembly not found: no clash check")
        return None
    asm = importlib.import_module("cad.gen.pedal.assembly")
    report = asm.check(installed)
    print(report.table())
    return {"ok": report.ok, "failures": [f"{r.a} / {r.b}: {r.status} {r.volume:.2f} mm^3"
                                          for r in report.failures],
            "tight": [f"{r.a} / {r.b}: {r.gap:.2f}" for r in report.rows if r.status == "TIGHT"]}


# --- README --------------------------------------------------------------------------------------------

README = """# bassbox pedal enclosure, DRAFT r{rev} (lid, base, spacers)

Built by `python -m cad.gen.pedal` from `cad/gen/pedal/enclosure.py` and
`dims.py`; the design is described in `DESIGN.md`.

## Print

`{plate}.3mf`: lid (face down), base (floor down) and two spacers on the Ender-3 V2 Neo (220 x 220),
PETG, 0.20 mm, 4 walls, 5 top / bottom layers, 40 % gyroid, **no supports**. Slice: **{time}, {grams} g**
({metres} m of filament). The spacers are optional (only with M2.5 x 20 screws at the header side).

## Assemble

1. Jacks into the base from inside: bushing out through the side holes, body square between the
   ribs, lugs down; washer + nut on the spot face outside. IN on the IO side (player's right).
2. Pi + HAT (wired) lowered into the base, HAT down: the ports drop into their slots; 2 x M2.5 x 8
   through the Pi's IO-side holes into the tall posts.
3. Display face down into the lid, the pin in its USB-end / IO-side corner hole, 3 x M2.5 x 6 from
   the back into the bosses.
4. Lid on: press until the four snaps click (the **tabs stand on the base** and hook into the
   windows in the lid's skirt). To open, push the four barbs in through the windows (two on the
   +Y side, two on the jack end) and lift.
5. Sticky feet on the four rings under the corners.

## Numbers

| | |
|---|---|
| outer box | {ox:.2f} x {oy:.2f} x {oz:.1f} (Z -2.0 .. 46.5), split at Z {split} |
| screen window | X {wx0:.2f}..{wx1:.2f}, Y {wy0:.2f}..{wy1:.2f} (LCD active area + {wm}), 0.8 x 45 chamfer |
| glass gap | {gg} (bezel underside to the touch glass) |
| snaps | tab {st} x {sw}, free length {sl}, barb {sb} into the skirt, strain {strain} %, about {close} N each to close |
| thinnest wall | {wmin} (IO panel; the rabbet's halves are 1.0 / 0.85 by dims) |

Checks: {checks}

Overhangs in the print poses: 45 degree chamfers rising from the bed, flat roofs that bridge
(vent slots, snap windows, foot rings, letters), and the four snap barbs' square retention faces,
0.8 flat ledges at the top of the base's tabs. Those print without support (supports stay off);
PrusaSlicer's automatic support would stand thin pillars under them ({probe}). Check the barbs
after printing: a drooped ledge still hooks, a stringy one wants a file stroke.
"""


def write_readme(out: Path, rep: dict) -> Path:
    s = rep["snap"]
    pl = rep.get("slice", {}).get("plate", {})
    x0, x1, y0, y1 = rep["window"]
    text = README.format(
        rev=E.REV, plate=PLATE, time=pl.get("time", "not sliced"), grams=pl.get("grams", "-"),
        metres=pl.get("metres", "-"), ox=D.OUTER_L, oy=D.OUTER_W, oz=D.OUTER_H, split=D.SPLIT_Z,
        wx0=x0, wx1=x1, wy0=y0, wy1=y1, wm=D.WINDOW_MARGIN, gg=rep["glass_gap"],
        st=D.SNAP_T, sw=E.SNAP_W, sl=D.SNAP_LEN, sb=D.SNAP_BARB, strain=s["strain_pct"],
        close=round(s["close_force_n"]), wmin=rep["walls_min"],
        checks="; ".join(rep["failures"]) if rep["failures"] else
        "all clean (watertight single bodies, walls, knife edges, small pieces, overhangs)",
        probe=", ".join(f"{k}: {v['support_extrusions']} support moves" for k, v in
                        rep.get("slice", {}).get("support_probe", {}).items()) or "not probed")
    path = out / "README-enclosure.md"
    path.write_text(text, encoding="utf-8")
    return path


# --- main --------------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cad.gen.pedal", description=__doc__.splitlines()[0])
    ap.add_argument("--no-slice", action="store_true", help="skip PrusaSlicer")
    ap.add_argument("--no-render", action="store_true", help="skip the PNG renders")
    ap.add_argument("--no-assembly", action="store_true", help="skip the clash check")
    a = ap.parse_args(argv)
    cli_logging()
    t0 = time.time()
    out = export.draft_dir("pedal")
    for pattern in OWN_GLOBS:  # only this module's files: the assembly harness writes here too
        for old in out.glob(pattern):
            if old.is_file():
                old.unlink()
    installed = E.build()
    printed = {n: E.print_pose(n, p) for n, p in installed.items()}
    stls: dict[str, Path] = {}
    inst_stls: dict[str, Path] = {}
    for name, part in printed.items():
        stls[name] = write_stl(part, out / f"{name}.stl")
        write_3mf([(name, part)], out / f"{name}.3mf")
        inst_stls[name] = write_stl(installed[name], out / f"enclosure-{name}-installed.stl")
    items = plate_items(printed)
    plate = write_plate_3mf(items, out / f"{PLATE}.3mf")
    write_installed_step(installed, out / "enclosure-installed.step")
    print(f"built and exported in {time.time() - t0:.0f} s")

    rep = run_checks(installed, printed, stls)
    if not a.no_render:
        for p in render_all(installed, inst_stls, stls, out):
            print("wrote", p)
    if not a.no_slice:
        rep["slice"] = slice_all(out, plate, stls)
        pl = rep["slice"]["plate"]
        print(f"plate on the {PRINTER} in {MATERIAL}: {pl['time']}, {pl['grams']} g, "
              f"{pl['metres']} m, fits {pl['fits']}")
        for w in pl["warnings"]:
            print("  !", w)
        print("support probe (auto supports forced on):", rep["slice"]["support_probe"])
    if not a.no_assembly:
        rep["assembly"] = run_assembly(installed)
    rep["failures"] = check_failures(rep)
    if rep.get("assembly") and not rep["assembly"]["ok"]:
        rep["failures"].append("assembly: " + "; ".join(rep["assembly"]["failures"][:8]))
    (out / "enclosure-report.json").write_text(json.dumps(rep, indent=2) + "\n", encoding="utf-8")
    write_readme(out, rep)
    for name, r in rep["parts"].items():
        print(f"{name}: print {r['print_size']}, overhangs {r['overhangs']}, knife edges "
              f"{len(r['knife_edges'])}, small pieces {len(r['small_pieces'])}, engraving hits "
              f"{r['engraving_hits']}, watertight {r['watertight']}, bodies {r['bodies']}")
    print("walls:", ", ".join(f"{w['part']} {w['where']} {w['mm']}" for w in rep["walls"]))
    print("snap:", rep["snap"], "| tabs to lid", rep["snap_clearance"], "| lid/base overlap",
          rep["lid_base_overlap_mm3"])
    print("FAILURES:" if rep["failures"] else "checks: all clean", *rep["failures"], sep="\n  ")
    print(f"done in {time.time() - t0:.0f} s")
    return 1 if rep["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
