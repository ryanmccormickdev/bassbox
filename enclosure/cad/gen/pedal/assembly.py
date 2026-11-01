"""Clash and clearance harness for the bassbox pedal enclosure, plus the renders and assembly STEP.

`check(printed)` takes the printed parts in frame B (`{name: Part}`, e.g. `enclosure.build()`) and
measures every one against every bought part, plug and keep-out from `components.py`:

* **CLASH**: intersection volume above 0.01 mm^3. Always a failure.
* **TOUCH**: a printed face touching a part or plug (gap < 0.02 mm) where that is not by design.
  A failure: the Pi's edge sits 0.2 from the wall and plugs must not touch anything.
* **CONTACT**: a touch listed in `EXEMPT` (post tops on the Pi, bosses on the display, jack front
  plates on the wall, washers on the spot faces ...), with its reason. Not a failure.
* **TIGHT** (gap < 0.1) and **near** (gap < 2): reported, not failures.

It also checks the bought parts against each other (`Report.components`): a clash there is a design
problem (jack bodies against the HAT's terminal block, display duponts, ...). Mated pairs are
listed in `MATED`; open design findings in `KNOWN_FINDINGS`.

Run `python -m cad.gen.pedal.assembly` to build the components, import
`cad.gen.pedal.enclosure.build()` if that module exists, print the table, and write renders and
`pedal-assembly.step` to `out/pedal/`.
"""

from __future__ import annotations

import argparse
import importlib
import itertools
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from build123d import Align, Box, Compound, Part, Pos, export_step

from cad.gen import cli_logging
from cad.gen.export import STEP_PCURVES, draft_dir
from cad.gen.pedal import components as comp
from cad.gen.pedal import dims as d

log = logging.getLogger(__name__)

CLASH_TOL = 0.01  # mm^3
TOUCH_TOL = 0.02  # mm: gap below this is a touch
TIGHT = 0.1  # mm: gap below this is worth a look
NEAR = 2.0  # mm: pairs farther apart than this are not reported
COMP_NEAR = 0.5  # component-vs-component pairs are reported when closer than this

# Touches that are by design: (regex on the printed part's name, regex on the component's, reason).
EXEMPT: tuple[tuple[str, str, str], ...] = (
    ("^(base|spacer)", r"^pi\.pcb$", "post tops and spacer ends on the Pi's top face"),
    ("^(base|spacer)", r"^hat\.pcb$", "HAT posts and spacers meet the HAT's board faces"),
    ("^lid", r"^lcd\.pcb$", "boss ends on the display PCB front face"),
    ("^(base|lid)", r"^jack_(in|out)\.body$", "jack front plate against the wall's inner face"),
    ("^(base|lid)", r"^jack_(in|out)\.washer$", "washer seated on the spot face"),
)

# Overlaps that are by design between bought parts: (regex, regex, reason). Plugs inside their own
# port are handled by `_mated_plug`.
MATED: tuple[tuple[str, str, str], ...] = (
    (r"^pi\.header$", r"^hat\.female$", "the Pi's pins inside the HAT's female header"),
)

# Open design findings: component clashes that are real but need a decision (not an enclosure bug).
KNOWN_FINDINGS: dict[frozenset[str], str] = {
    frozenset({"lcd.sd", "pi.bottom_tails"}): (
        "the display's microSD socket (1.9) sits over the Pi's audio/HDMI tails (1.6): 3.5 needed, "
        "3.4 between the boards; the envelopes overlap 0.1 (real tails are probably shorter)"
    ),
    frozenset({"pi.heatsink", "hat.jack35_b"}): (
        "a 6 mm heatsink on the SoC hits the HAT's middle 3.5 mm jack "
        "(only 2.78 mm of room above the SoC)"
    ),
    frozenset({"pi.heatsink", "hat.speaker_a"}): (
        "a 6 mm heatsink on the SoC hits the HAT's speaker (its body starts 4.46 mm above the SoC)"
    ),
}

_ENCLOSURE_MODULE = "cad.gen.pedal.enclosure"


@dataclass(frozen=True)
class Row:
    """One measured pair. `volume` in mm^3, `gap` in mm (0 when they touch or overlap)."""

    a: str
    b: str
    kind: str  # of b: part, plug or keepout
    volume: float
    gap: float
    status: str  # CLASH, TOUCH, FINDING, MATED, CONTACT, TIGHT, near
    note: str = ""


_ORDER = {"CLASH": 0, "TOUCH": 1, "FINDING": 2, "TIGHT": 3, "CONTACT": 4, "MATED": 5, "near": 6}
_FAIL = {"CLASH", "TOUCH"}


@dataclass
class Report:
    """Result of `check`: printed-vs-bought rows and bought-vs-bought rows."""

    printed: list[str]
    rows: list[Row] = field(default_factory=list)
    components: list[Row] = field(default_factory=list)

    @property
    def failures(self) -> list[Row]:
        return [r for r in self.rows if r.status in _FAIL]

    @property
    def ok(self) -> bool:
        """No printed part clashes with, or unexpectedly touches, anything bought."""
        return not self.failures

    @property
    def component_failures(self) -> list[Row]:
        return [r for r in self.components if r.status in _FAIL]

    @property
    def components_ok(self) -> bool:
        """No unexplained clash between the bought parts themselves (open findings excluded)."""
        return not self.component_failures

    @property
    def all_ok(self) -> bool:
        """`ok` and `components_ok`."""
        return self.ok and self.components_ok

    @property
    def findings(self) -> list[Row]:
        return [r for r in self.components if r.status == "FINDING"]

    def __str__(self) -> str:
        return self.table()

    def min_gap(self, printed: str, component_pattern: str) -> float | None:
        """Smallest reported gap between a printed part and the components matching a regex."""
        gaps = [r.gap for r in self.rows if r.a == printed and re.search(component_pattern, r.b)]
        return min(gaps) if gaps else None

    def table(self, max_near: float = 1.0) -> str:
        """The report as text; plain `near` rows beyond `max_near` mm are only counted."""
        hidden = [0]

        def fmt(rows: list[Row]) -> list[str]:
            out = []
            for r in sorted(rows, key=lambda r: (_ORDER[r.status], r.gap, r.a, r.b)):
                if r.status == "near" and r.gap > max_near:
                    hidden[0] += 1
                    continue
                vol = f"{r.volume:9.2f}" if r.volume > CLASH_TOL else "        -"
                out.append(
                    f"  {r.status:8s} {r.a:22s} {r.b:26s} {r.kind:7s} {vol} {r.gap:6.2f}  {r.note}"
                )
            return out

        head = f"  {'status':8s} {'a':22s} {'b':26s} {'kind':7s} {'vol mm^3':>9s} {'gap':>6s}  note"
        lines = [
            f"printed parts: {', '.join(self.printed) or '(none)'}",
            "printed vs bought parts:",
            head,
        ]
        lines += fmt(self.rows) or ["  (nothing within 2 mm)"]
        lines += ["bought parts against each other (clashes, and gaps under 0.5 mm):", head]
        lines += fmt(self.components) or ["  (none)"]
        if hidden[0]:
            lines.append(f"({hidden[0]} more pairs between {max_near:g} and {NEAR:g} mm not shown)")
        verdict = "OK" if self.ok else f"FAIL ({len(self.failures)} printed-vs-bought problems)"
        lines.append(
            f"printed vs bought: {verdict}; bought vs bought: "
            f"{'OK' if self.components_ok else 'FAIL'}"
            f" ({len(self.findings)} open finding(s))"
        )
        return "\n".join(lines)


# --------------------------------------------------------------------------------------------
# Measuring
# --------------------------------------------------------------------------------------------
def _bbox_gap(a, b) -> float:
    """Distance between two bounding boxes (0 when they overlap)."""
    dx = max(a.min.X - b.max.X, b.min.X - a.max.X, 0.0)
    dy = max(a.min.Y - b.max.Y, b.min.Y - a.max.Y, 0.0)
    dz = max(a.min.Z - b.max.Z, b.min.Z - a.max.Z, 0.0)
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def measure(
    a: Part, b: Part, bba, bbb, *, near: float, want_gap: bool = True
) -> tuple[float, float | None]:
    """(intersection volume, gap) of two solids; the gap is None when they are farther than `near`
    apart (or `want_gap` is off and they do not overlap)."""
    if _bbox_gap(bba, bbb) > near:
        return 0.0, None
    common = a & b
    vol = common.volume if common is not None else 0.0
    if vol > CLASH_TOL:
        return vol, 0.0
    if not want_gap:
        return vol, None
    gap = a.distance_to(b)
    return vol, (gap if gap <= near else None)


def _exempt_reason(printed: str, component: str) -> str | None:
    for pa, pb, why in EXEMPT:
        if re.search(pa, printed) and re.search(pb, component):
            return why
    return None


def _mated_plug(a: str, b: str) -> bool:
    """True for a plug and the Pi port it is inserted in (`plug.usb2a.*` with `pi.usb2`)."""
    for x, y in ((a, b), (b, a)):
        m = re.match(r"^plug\.([a-z_]+\d?)[ab]?\.", x)
        if m and y == f"pi.{m.group(1)}":
            return True
    return False


def _mated_reason(a: str, b: str) -> str | None:
    if _mated_plug(a, b):
        return "plug inserted in its port"
    for pa, pb, why in MATED:
        if (re.search(pa, a) and re.search(pb, b)) or (re.search(pa, b) and re.search(pb, a)):
            return why
    return None


def load_enclosure() -> dict[str, Part] | None:
    """`cad.gen.pedal.enclosure.build()`, or None when that module does not exist yet."""
    try:
        mod = importlib.import_module(_ENCLOSURE_MODULE)
    except ModuleNotFoundError as exc:
        if exc.name == _ENCLOSURE_MODULE:
            return None
        raise
    return mod.build()


def check(
    printed: dict[str, Part],
    *,
    parts: dict[str, Part] | None = None,
    plugs: dict[str, Part] | None = None,
    keepouts: dict[str, Part] | None = None,
    include_components: bool = True,
) -> Report:
    """Measure `printed` (frame B) against the bought parts, plugs and keep-outs, and the bought
    parts against each other. Pass prebuilt component dicts to save time on repeated calls."""
    parts = comp.parts() if parts is None else parts
    plugs = comp.plugs() if plugs is None else plugs
    keepouts = comp.keepouts() if keepouts is None else keepouts
    kinds = {
        **dict.fromkeys(parts, "part"),
        **dict.fromkeys(plugs, "plug"),
        **dict.fromkeys(keepouts, "keepout"),
    }
    solids = {**parts, **plugs, **keepouts}
    boxes = {n: s.bounding_box() for n, s in solids.items()}
    report = Report(printed=list(printed))

    for pname, p in printed.items():
        pbb = p.bounding_box()
        for cname, c in solids.items():
            kind = kinds[cname]
            vol, gap = measure(p, c, pbb, boxes[cname], near=NEAR, want_gap=kind != "keepout")
            if vol > CLASH_TOL:
                report.rows.append(Row(pname, cname, kind, vol, 0.0, "CLASH"))
            elif gap is not None:
                why = _exempt_reason(pname, cname)
                if kind == "keepout":
                    status, note = "near", ""
                elif gap < TOUCH_TOL and why:
                    status, note = "CONTACT", why
                elif gap < TOUCH_TOL:
                    status, note = "TOUCH", "touching, not listed in EXEMPT"
                elif gap < TIGHT:
                    status, note = "TIGHT", ""
                else:
                    status, note = "near", ""
                report.rows.append(Row(pname, cname, kind, 0.0, gap, status, note))

    if include_components:
        for (an, a), (bn, b) in itertools.combinations(solids.items(), 2):
            if comp.group_of(an) == comp.group_of(bn):
                continue
            if kinds[an] == "keepout" and kinds[bn] == "keepout":
                continue
            ka = kinds[bn] if kinds[an] == "keepout" else kinds[an]
            vol, gap = measure(
                a,
                b,
                boxes[an],
                boxes[bn],
                near=COMP_NEAR,
                want_gap=kinds[an] != "keepout" and kinds[bn] != "keepout",
            )
            if vol > CLASH_TOL:
                mated = _mated_reason(an, bn)
                finding = KNOWN_FINDINGS.get(frozenset({an, bn}))
                if mated:
                    row = Row(an, bn, ka, vol, 0.0, "MATED", mated)
                elif finding:
                    row = Row(an, bn, ka, vol, 0.0, "FINDING", finding)
                else:
                    row = Row(an, bn, ka, vol, 0.0, "CLASH")
                report.components.append(row)
            elif (
                gap is not None
                and TOUCH_TOL <= gap < COMP_NEAR
                and kinds[an] != "keepout"
                and kinds[bn] != "keepout"
            ):
                status = "TIGHT" if gap < TIGHT else "near"
                report.components.append(Row(an, bn, ka, 0.0, gap, status))
    return report


# --------------------------------------------------------------------------------------------
# Renders (pyvista, off-screen)
# --------------------------------------------------------------------------------------------
_COLORS: tuple[tuple[str, str], ...] = (
    (r"^lid", "#8fb3cc"),
    (r"^base", "#a9b8c4"),
    (r"^spacer", "#e0b04a"),
    (r"^lcd\.pcb", "#27406b"),
    (r"^lcd\.panel", "#14171f"),
    (r"^lcd\.glass", "#7aa7c9"),
    (r"^lcd\.dupont", "#202020"),
    (r"^lcd\.", "#b89a3c"),
    (r"^pi\.pcb", "#2f8f5b"),
    (r"^pi\.sd_card", "#303a48"),
    (r"^pi\.", "#c3c8cf"),
    (r"^hat\.mesh\.pcb", "#24262c"),
    (r"^hat\.mesh", "#b5a27c"),
    (r"^hat\.pcb", "#24262c"),
    (r"^hat\.dupont", "#202020"),
    (r"^hat\.", "#9d7e52"),
    (r"^jack_", "#3b3f46"),
    (r"^screw", "#555a62"),
    (r"^plug", "#e8782a"),
)
_HAT_PCB_MATERIALS = (35, 36, 37)


def _color(name: str) -> str:
    for pat, col in _COLORS:
        if re.search(pat, name):
            return col
    return "#999999"


def _first(table: dict | None, name: str, default=None):
    """The value of the first regex key in `table` that matches `name`."""
    return next((v for k, v in (table or {}).items() if re.search(k, name)), default)


def _poly(shape, tol: float = 0.05, ang: float = 0.35):
    import numpy as np
    import pyvista as pv

    verts, tris = shape.tessellate(tol, ang)
    if not tris:
        return pv.PolyData()
    pts = np.array([[v.X, v.Y, v.Z] for v in verts])
    tri = np.array(tris)
    return pv.PolyData(pts, np.hstack([np.full((len(tri), 1), 3), tri]).ravel())


def _merge(polys):
    import numpy as np
    import pyvista as pv

    pts, faces, n = [], [], 0
    for p in polys:
        f = p.faces.reshape(-1, 4)
        pts.append(np.asarray(p.points))
        faces.append(np.hstack([f[:, :1], f[:, 1:] + n]))
        n += len(p.points)
    return pv.PolyData(np.vstack(pts), np.vstack(faces).ravel())


@dataclass
class Item:
    name: str
    mesh: object  # pyvista.PolyData
    color: str
    opacity: float = 1.0
    caps: object = None  # pyvista.PolyData of the faces lying on a section plane
    edges: bool = True  # outline sharp edges (slow on the 200k-face vendor mesh: off there)


def _items(
    solids: dict[str, Part],
    *,
    opacity: dict | None = None,
    offset: dict | None = None,
    color: str | None = None,
) -> list[Item]:
    out = []
    for name, s in solids.items():
        poly = _poly(s)
        off = _first(offset, name)
        if off:
            poly = poly.translate(off, inplace=False)
        out.append(Item(name, poly, color or _color(name), _first(opacity, name, 1.0)))
    return out


def _hat_mesh_items(offset: dict | None = None) -> list[Item]:
    """The vendor mesh (mapped to B) as two items, board and parts; empty when the OBJ is absent."""
    import numpy as np
    import pyvista as pv

    mesh = comp.hat_mesh()
    if mesh is None:
        return []
    pcb = np.isin(mesh.face_materials, _HAT_PCB_MATERIALS)
    out = []
    for name, sel in (("hat.mesh.pcb", pcb), ("hat.mesh.parts", ~pcb)):
        sub = mesh.submesh([np.flatnonzero(sel)], append=True)
        poly = pv.PolyData(
            np.asarray(sub.vertices),
            np.hstack([np.full((len(sub.faces), 1), 3), sub.faces]).ravel(),
        )
        off = _first(offset, name)
        if off:
            poly = poly.translate(off, inplace=False)
        out.append(Item(name, poly, _color(name), edges=False))
    return out


def _bought_items(
    parts_: dict[str, Part], *, offset: dict | None = None, opacity: dict | None = None
) -> list[Item]:
    """The bought parts for a view; the vendor mesh replaces the HAT's box models when present."""
    mesh = _hat_mesh_items(offset)
    covered = {
        f"hat.{k}" for k in comp.HAT_BOXES
    }  # the vendor mesh has these, not the dupont housings
    shown = {n: s for n, s in parts_.items() if not (mesh and n in covered)}
    return _items(shown, offset=offset, opacity=opacity) + mesh


def _darker(hexcol: str, f: float = 0.55) -> str:
    r, g, b = (int(hexcol[i : i + 2], 16) for i in (1, 3, 5))
    return f"#{int(r * f):02x}{int(g * f):02x}{int(b * f):02x}"


def _draw(pl, items: list[Item]) -> None:
    for it in items:
        if it.mesh.n_points:
            pl.add_mesh(
                it.mesh, color=it.color, opacity=it.opacity, smooth_shading=False, specular=0.15
            )
            if it.opacity > 0.5 and it.edges:
                feat = it.mesh.extract_feature_edges(
                    feature_angle=35, boundary_edges=False, non_manifold_edges=False
                )
                if feat.n_points:
                    pl.add_mesh(feat, color="#1b2228", line_width=1)
        if it.caps is not None and it.caps.n_points:
            pl.add_mesh(it.caps, color=_darker(it.color), smooth_shading=False)


def _snapshot(
    png: Path, panels: list[tuple[str, list[Item], tuple, dict]], size=(1500, 760)
) -> Path:
    """One PNG with one panel per entry: (title, items, direction the camera sits in, options).
    Options: `up` (view-up vector) and `parallel` (orthographic projection)."""
    import pyvista as pv

    pv.OFF_SCREEN = True
    pl = pv.Plotter(off_screen=True, shape=(1, len(panels)), window_size=size, border=False)
    pl.set_background("white")
    for col, (title, items, direction, opts) in enumerate(panels):
        pl.subplot(0, col)
        if any(i.opacity < 0.5 for i in items):
            pl.enable_depth_peeling(number_of_peels=8)
        _draw(pl, items)
        pl.view_vector(direction, viewup=opts.get("up", (0, 0, 1)))
        if opts.get("parallel"):
            pl.enable_parallel_projection()
        pl.reset_camera()
        if opts.get("zoom"):
            pl.camera.zoom(opts["zoom"])
        pl.add_text(title, font_size=10, color="black")
    pl.screenshot(str(png))
    pl.close()
    log.debug("wrote %s", png)
    return png


def _cut(shape: Part, axis: str, value: float, keep_low: bool) -> Part | None:
    """The part of `shape` on one side of the plane `axis = value` (exact: caps are real faces)."""
    big = 400.0
    size = {
        "x": (big, 2 * big, 2 * big),
        "y": (2 * big, big, 2 * big),
        "z": (2 * big, 2 * big, big),
    }[axis]
    shift = -big / 2 if keep_low else big / 2
    pos = {"x": (value + shift, 0, 0), "y": (0, value + shift, 0), "z": (0, 0, value + shift)}[axis]
    out = shape & (Pos(*pos) * Box(*size, align=(Align.CENTER,) * 3))
    return out if out is not None and out.volume > 1e-6 else None


def _cut_item(name: str, shape: Part, axis: str, value: float, keep_low: bool) -> Item | None:
    cut = _cut(shape, axis, value, keep_low)
    if cut is None:
        return None
    i = "xyz".index(axis)
    body, caps = [], []
    for f in cut.faces():
        bb = f.bounding_box()
        lo, hi = (bb.min.X, bb.min.Y, bb.min.Z)[i], (bb.max.X, bb.max.Y, bb.max.Z)[i]
        on_plane = abs(lo - value) < 1e-5 and abs(hi - value) < 1e-5
        poly = _poly(f)
        if poly.n_points:
            (caps if on_plane else body).append(poly)
    mesh = _merge(body) if body else _poly(Compound([]))
    return Item(name, mesh, _color(name), 1.0, _merge(caps) if caps else None)


def render_sections(solids: dict[str, Part], out: Path) -> list[Path]:
    """Sections at Z = SPLIT_Z (looking down), X = JACK_X (from +X) and Y = 3.7 (from -Y)."""
    cuts = [
        (
            "z",
            d.SPLIT_Z,
            True,
            f"section Z = {d.SPLIT_Z:g} (split plane), seen from above",
            (0, 0, 1),
            (0, 1, 0),
        ),
        (
            "x",
            d.JACK_X,
            True,
            f"section X = {d.JACK_X:g} (jack axis), seen from +X",
            (1, 0, 0),
            (0, 0, 1),
        ),
        (
            "y",
            3.7,
            False,
            "section Y = 3.7 (Pi IO-side screws), seen from -Y",
            (0, -1, 0),
            (0, 0, 1),
        ),
    ]
    paths = []
    for axis, value, keep_low, title, direction, up in cuts:
        items = [
            it
            for n, s in solids.items()
            if (it := _cut_item(n, s, axis, value, keep_low)) is not None
        ]
        paths.append(
            _snapshot(
                out / f"section-{axis}.png",
                [(title, items, direction, {"up": up, "parallel": True, "zoom": 1.5})],
                size=(1500, 900),
            )
        )
    return paths


def render_all(printed: dict[str, Part], out: Path) -> list[Path]:
    """Assembled iso, exploded, translucent, keep-outs and sections into `out`."""
    parts_, plugs_, keeps = comp.parts(), comp.plugs(), comp.keepouts()
    iso_a, iso_b, top = (-1.0, -1.6, 1.0), (1.0, 1.5, 0.9), (0.0, 0.0, 1.0)
    paths: list[Path] = []
    bought = _bought_items(parts_) + _items(plugs_)
    if printed:
        ghost = {"^(lid|base|spacer)": 0.16}
        paths.append(
            _snapshot(
                out / "assembly-iso.png",
                [
                    (
                        "assembled, from the USB end and IO side",
                        _items(printed) + bought,
                        iso_a,
                        {},
                    ),
                    (
                        "assembled, from the jack end and header side",
                        _items(printed) + bought,
                        iso_b,
                        {},
                    ),
                ],
            )
        )
        shift = {
            "^lid": (0, 0, 70),
            r"^(lcd|screw\.lcd)": (0, 0, 46),
            r"^(pi|hat|screw\.pi|spacer)": (0, 0, 14),
            "^jack_in": (0, -26, 0),
            "^jack_out": (0, 26, 0),
        }
        paths.append(
            _snapshot(
                out / "assembly-exploded.png",
                [
                    (
                        "exploded: lid, display, Pi with HAT, jacks",
                        _items(printed, offset=shift) + _bought_items(parts_, offset=shift),
                        iso_a,
                        {},
                    ),
                ],
                size=(900, 1000),
            )
        )
        paths.append(
            _snapshot(
                out / "assembly-translucent.png",
                [
                    (
                        "lid and base translucent",
                        _items(printed, opacity=ghost) + bought,
                        iso_a,
                        {},
                    ),
                    (
                        "top view",
                        _items(printed, opacity=ghost) + bought,
                        top,
                        {"up": (0, 1, 0), "parallel": True},
                    ),
                ],
            )
        )
    else:
        paths.append(
            _snapshot(
                out / "components-iso.png",
                [
                    (
                        "bought parts and plugs (no enclosure module yet), USB end and IO side",
                        bought,
                        iso_a,
                        {},
                    ),
                    ("jack end and header side", bought, iso_b, {}),
                ],
            )
        )
    paths += render_sections({**printed, **parts_, **plugs_}, out)
    inside = {
        n: s for n, s in parts_.items() if n not in ("lcd.glass", "lcd.panel")
    }  # they would hide the rest
    ko = (
        _items(keeps, opacity={"": 0.5}, color="#e0245e")
        + _bought_items(inside, opacity={r"^lcd\.": 0.25})
        + _items(plugs_, opacity={"": 0.5})
    )
    paths.append(
        _snapshot(
            out / "components-keepouts.png",
            [
                ("keep-outs (red) with the bought parts and plugs", ko, iso_a, {}),
                ("top view", ko, top, {"up": (0, 1, 0), "parallel": True}),
            ],
        )
    )
    return paths


# --------------------------------------------------------------------------------------------
# STEP
# --------------------------------------------------------------------------------------------
def _named(solids: dict[str, Part]) -> list[Part]:
    out = []
    for name, s in solids.items():
        c = s.moved(Pos(0, 0, 0))
        c.label = name
        out.append(c)
    return out


def export_assembly_step(printed: dict[str, Part], out: Path) -> list[Path]:
    """`pedal-assembly.step` (printed, bought, plugs: named) and `pedal-keepouts.step`."""
    groups = [Compound(label="printed", children=_named(printed))] if printed else []
    groups += [
        Compound(label="bought parts", children=_named(comp.parts())),
        Compound(label="plugs", children=_named(comp.plugs())),
    ]
    step = out / "pedal-assembly.step"
    export_step(
        Compound(label="bassbox pedal assembly", children=groups), step, write_pcurves=STEP_PCURVES
    )
    ko = out / "pedal-keepouts.step"
    export_step(
        Compound(label="keep-outs", children=_named(comp.keepouts())),
        ko,
        write_pcurves=STEP_PCURVES,
    )
    return [step, ko]


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Clash check, renders and STEP for the pedal enclosure"
    )
    ap.add_argument("--no-render", action="store_true", help="skip the PNG renders")
    ap.add_argument("--no-step", action="store_true", help="skip the assembly STEP")
    ap.add_argument("--no-enclosure", action="store_true", help="check the components alone")
    args = ap.parse_args(argv)
    cli_logging()
    t0 = time.time()
    printed = {} if args.no_enclosure else load_enclosure()
    if printed is None:
        print("cad.gen.pedal.enclosure does not exist yet: checking the bought parts alone")
        printed = {}
    report = check(printed)
    print(report.table())
    out = draft_dir("pedal")
    hat_stl = comp.export_hat_mesh()
    if hat_stl:
        print("wrote", hat_stl)
    if not args.no_step:
        for p in export_assembly_step(printed, out):
            print("wrote", p)
    if not args.no_render:
        for p in render_all(printed, out):
            print("wrote", p)
    print(f"done in {time.time() - t0:.0f} s")
    return 0 if report.all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
