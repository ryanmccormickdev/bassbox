"""Small feature helpers (algebra mode), written so diameters are diameters.

build123d's `Hole`, `Cylinder`, and `Circle` take a **radius**; these take a
**diameter**, matching every table in the docs. Units are mm; +Z is up as printed;
`z_top` is the face a cut starts from.
"""

from __future__ import annotations

from collections.abc import Sequence

from build123d import (
    Align,
    Axis,
    Box,
    Cylinder,
    Face,
    Part,
    Plane,
    Pos,
    Rot,
    Sketch,
    Sphere,
    Text,
    Vector,
    Wire,
    chamfer,
    extrude,
)
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.BRepTools import BRepTools_WireExplorer
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.TopAbs import TopAbs_REVERSED

MIN3 = (Align.MIN, Align.MIN, Align.MIN)


def block(x: float, y: float, z: float) -> Part:
    """Box with its lower-left-bottom corner at the origin."""
    return Box(x, y, z, align=MIN3)


def bore(d: float, depth: float, x: float, y: float, z_top: float) -> Part:
    """Vertical cylinder of diameter `d` cut down from `z_top` (a true cylinder, no chamfer).

    Subtract it: `part -= bore(...)`. A through hole uses `depth` > the part height;
    the tool overshoots 0.01 mm above `z_top` so faces never coincide.
    """
    over = 0.01
    tool = Cylinder(d / 2, depth + over, align=(Align.CENTER, Align.CENTER, Align.MAX))
    return Pos(x, y, z_top + over) * tool


def cross_bore(
    d: float, length: float, start: tuple[float, float, float], axis: str = "X"
) -> Part:
    """Horizontal cylinder of diameter `d` starting at `start`, running +`length` along X or Y."""
    tool = Cylinder(d / 2, length, align=(Align.CENTER, Align.CENTER, Align.MIN))
    rot = Rot(0, 90, 0) if axis == "X" else Rot(-90, 0, 0)
    return Pos(*start) * rot * tool


def _polyline(wire: Wire, deflection: float) -> Wire:
    """`wire` as straight segments, each within `deflection` mm of the original curve."""
    pts: list = []
    ex = BRepTools_WireExplorer(wire.wrapped)
    while ex.More():
        e = ex.Current()
        q = GCPnts_QuasiUniformDeflection(BRepAdaptor_Curve(e), deflection)
        ep = [q.Value(i) for i in range(1, q.NbPoints() + 1)]
        if e.Orientation() == TopAbs_REVERSED:
            ep.reverse()
        pts += ep[1:] if pts else ep
        ex.Next()
    return Wire.make_polygon([Vector(p.X(), p.Y(), p.Z()) for p in pts[:-1]], close=True)


def engrave(
    text: str,
    x: float,
    y: float,
    z_top: float,
    *,
    height: float,
    depth: float,
    rotation: float = 0.0,
    font: str = "Arial",
    deflection: float = 0.01,
) -> Part:
    """Text solid, centered at (x, y), cut `depth` down from `z_top`. Subtract it.

    `height` is the font size in mm (cap height is ~70% of it). Glyph outlines become
    polylines within `deflection` mm of the font's curves: far below what a nozzle
    resolves, and it keeps a labelled plate's STL about 10× smaller than meshing the
    curved glyph walls. Raises if the font machinery fails; callers fall back to `dimples`.
    """
    glyphs = Text(text, font_size=height, font=font, align=(Align.CENTER, Align.CENTER))
    flat = Sketch([
        Face(_polyline(f.outer_wire(), deflection),
             [_polyline(w, deflection) for w in f.inner_wires()])
        for f in glyphs.faces()
    ])
    plane = Plane.XY.offset(z_top)
    sk = plane * Pos(x, y) * Rot(0, 0, rotation) * flat
    solid = extrude(sk, amount=-depth)
    if solid.volume <= 0:
        raise RuntimeError(f"engraving {text!r} produced no solid")
    return solid


def dimples(
    n: int, x: float, y: float, z_top: float, *, d: float = 0.6, pitch: float = 1.2
) -> Part:
    """`n` hemispherical dimples of diameter `d` in a row centered at (x, y): the text fallback."""
    x0 = x - (n - 1) * pitch / 2
    tools = [Pos(x0 + i * pitch, y, z_top) * Sphere(d / 2) for i in range(n)]
    out = tools[0]
    for t in tools[1:]:
        out = out + t
    return out


def chamfer_outline(part: Part, *, top: float = 0.0, bottom: float = 0.0) -> Part:
    """Chamfer only the outer boundary edges of the top and/or bottom faces.

    Hole edges are left sharp, so holes stay true cylinders. `bottom` is the usual
    elephant's-foot relief (0.3–0.5 mm).
    """
    if top:
        part = chamfer(part.faces().sort_by(Axis.Z)[-1].outer_wire().edges(), top)
    if bottom:
        part = chamfer(part.faces().sort_by(Axis.Z)[0].outer_wire().edges(), bottom)
    return part


def union(parts: Sequence[Part]) -> Part:
    """Fuse a list of solids into one."""
    out = parts[0]
    for p in parts[1:]:
        out = out + p
    return out
