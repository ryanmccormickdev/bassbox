"""Geometry checks for tests and build reports. Units are mm.

Hole checks read the exact B-rep (cylindrical faces), not the mesh, so a 2.95 vs 3.05
hole is distinguishable to well under 0.005 mm. Mesh checks confirm the exported STL
is printable: watertight, one body per piece.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from build123d import Box, Face, GeomType, Part, Pos, Vector
from build123d.topology.kernel import list_shapes
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp_Face
from OCP.collections import (
    IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as _EdgeFaces,
)
from OCP.gp import gp_Pnt, gp_Vec
from OCP.ShapeAnalysis import ShapeAnalysis_Surface
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS


@dataclass(frozen=True)
class Cylinder:
    """A cylindrical feature: axis point `center` (x, y, z), unit `axis`, diameter, extent.

    `center` is where the axis crosses the plane through the face's lowest point along
    the axis; `length` runs from there along `axis`. `is_hole` is True when the
    material is outside the cylinder (a bore), False for a boss or pin.
    """

    center: tuple[float, float, float]
    axis: tuple[float, float, float]
    diameter: float
    length: float
    is_hole: bool


def _cylinder_of(face: Face) -> Cylinder:
    surf = BRepAdaptor_Surface(face.wrapped)
    cyl = surf.Cylinder()
    loc, d = cyl.Axis().Location(), cyl.Axis().Direction()
    p0 = Vector(loc.X(), loc.Y(), loc.Z())
    ax = Vector(d.X(), d.Y(), d.Z()).normalized()
    if ax.Z < -1e-9 or (abs(ax.Z) <= 1e-9 and (ax.X < 0 or (ax.X == 0 and ax.Y < 0))):
        ax = -ax
    # Extent along the axis from the face's vertices and a few edge samples.
    pts = [v.center() for v in face.vertices()] + [
        e.position_at(t) for e in face.edges() for t in (0.0, 0.25, 0.5, 0.75, 1.0)
    ]
    ts = [(p - p0).dot(ax) for p in pts]
    tmin, tmax = min(ts), max(ts)
    c = p0 + ax * tmin
    # Hole if the outward face normal points toward the axis.
    probe = face.center()
    n = face.normal_at(probe)
    radial = probe - (p0 + ax * (probe - p0).dot(ax))
    return Cylinder(
        center=(c.X, c.Y, c.Z),
        axis=(ax.X, ax.Y, ax.Z),
        diameter=2 * cyl.Radius(),
        length=tmax - tmin,
        is_hole=n.dot(radial) < 0,
    )


def cylinder_faces(part: Part) -> list[tuple[Face, Cylinder]]:
    """Every cylindrical face with its axis, unmerged (for face-to-face distance checks)."""
    return [(f, _cylinder_of(f)) for f in part.faces() if f.geom_type == GeomType.CYLINDER]


def cylinders(part: Part, *, merge_tol: float = 1e-4) -> list[Cylinder]:
    """Every cylindrical feature in `part`, with split faces of one feature merged.

    Faces merge only if they also overlap along the axis, so a row of separate
    coaxial holes (FC5's pinch holes) stays a row of holes.
    """
    raw = [_cylinder_of(f) for f in part.faces() if f.geom_type == GeomType.CYLINDER]
    merged: list[Cylinder] = []
    for c in raw:
        for i, m in enumerate(merged):
            same_axis = all(abs(a - b) < merge_tol for a, b in zip(c.axis, m.axis))
            off = Vector(*c.center) - Vector(*m.center)
            ax = Vector(*m.axis)
            coaxial = (off - ax * off.dot(ax)).length < merge_tol
            overlap = -c.length - merge_tol <= off.dot(ax) <= m.length + merge_tol
            if same_axis and coaxial and overlap and abs(c.diameter - m.diameter) < merge_tol \
                    and c.is_hole == m.is_hole:
                lo = min(0.0, off.dot(ax))
                hi = max(m.length, off.dot(ax) + c.length)
                start = Vector(*m.center) + ax * lo
                merged[i] = Cylinder(
                    (start.X, start.Y, start.Z), m.axis, m.diameter, hi - lo, m.is_hole
                )
                break
        else:
            merged.append(c)
    return merged


def holes_at(
    part: Part,
    x: float,
    y: float,
    *,
    axis: tuple[float, float, float] = (0, 0, 1),
    tol: float = 0.005,
) -> list[Cylinder]:
    """Holes whose axis is parallel to `axis` and passes within `tol` of (x, y).

    For a vertical axis (x, y) is the bed position; for other axes it is measured in
    the plane perpendicular to `axis` through the origin, so use the hole's own axis
    point coordinates.
    """
    a = Vector(*axis).normalized()
    out = []
    for c in cylinders(part):
        if not c.is_hole or abs(abs(Vector(*c.axis).dot(a)) - 1) > 1e-6:
            continue
        p = Vector(*c.center)
        p_perp = p - a * p.dot(a)
        q = Vector(x, y, 0)
        q_perp = q - a * q.dot(a)
        if (p_perp - q_perp).length <= tol:
            out.append(c)
    return out


def holes_through(
    part: Part,
    point: tuple[float, float, float],
    axis: tuple[float, float, float],
    *,
    tol: float = 0.005,
) -> list[Cylinder]:
    """Holes whose axis is parallel to `axis` and passes within `tol` of the 3-D `point`.

    Use this for horizontal holes, where `holes_at` can't express the height.
    """
    a = Vector(*axis).normalized()
    q = Vector(*point)
    out = []
    for c in cylinders(part):
        if not c.is_hole or abs(abs(Vector(*c.axis).dot(a)) - 1) > 1e-6:
            continue
        off = Vector(*c.center) - q
        if (off - a * off.dot(a)).length <= tol:
            out.append(c)
    return out


def hole_diameters_at(part: Part, x: float, y: float, tol: float = 0.005) -> list[float]:
    """Sorted diameters (mm) of vertical holes centered at (x, y). Pockets show up too."""
    return sorted(round(c.diameter, 6) for c in holes_at(part, x, y, tol=tol))


@dataclass(frozen=True)
class MeshReport:
    path: str
    watertight: bool
    bodies: int
    extents: tuple[float, float, float]
    triangles: int


def mesh_report(stl: Path) -> MeshReport:
    """Load an exported STL and report printability basics."""
    import trimesh

    m = trimesh.load_mesh(stl, process=True)
    bodies = m.split(only_watertight=False)
    ext = tuple(float(e) for e in m.extents)
    return MeshReport(str(stl), bool(m.is_watertight), len(bodies), ext, len(m.faces))


def bbox_size(part: Part) -> tuple[float, float, float]:
    """Exact bounding-box size (mm) of the B-rep."""
    s = part.bounding_box(optimal=True).size
    return (s.X, s.Y, s.Z)


def min_wall_between(a: Cylinder, b: Cylinder) -> float:
    """Material left between two parallel holes (mm): center distance minus radii."""
    pa, pb, ax = Vector(*a.center), Vector(*b.center), Vector(*a.axis)
    off = pb - pa
    dist = (off - ax * off.dot(ax)).length
    return dist - a.diameter / 2 - b.diameter / 2


@dataclass(frozen=True)
class Clash:
    """Overlap between a part and a named keep-out envelope."""

    keepout: str
    volume: float  # mm^3
    bbox: tuple[float, float, float, float, float, float]  # xmin, ymin, zmin, xmax, ymax, zmax

    def __str__(self) -> str:
        b = ", ".join(f"{v:.1f}" for v in self.bbox)
        return f"{self.keepout}: {self.volume:.2f} mm^3 inside, overlap bbox ({b})"


def clashes(part: Part, keepouts: dict[str, Part], *, tol: float = 0.01) -> list[Clash]:
    """Every keep-out the part intersects by more than `tol` mm^3 (both in one frame).

    Use with `cad.gen.skeleton` envelopes: a part that passes has no solid inside the
    holder, the lens, the ring, the carriage, the rods, or any other neighbour's space.
    """
    out = []
    for name, ko in keepouts.items():
        common = part & ko
        vol = common.volume if common is not None else 0.0
        if vol > tol:
            bb = common.bounding_box()
            out.append(Clash(name, vol, (bb.min.X, bb.min.Y, bb.min.Z, bb.max.X, bb.max.Y, bb.max.Z)))
    return out


def gap_between(a: Part, b: Part) -> float:
    """Minimum distance (mm) between two solids; 0 if they touch or overlap."""
    return a.distance_to(b)


# -- slivers: what a cut can leave behind --------------------------------------------------------------


@dataclass(frozen=True)
class KnifeEdge:
    """A convex edge whose faces meet at less than the limit: the edge of a wedge a cut left."""

    angle: float  # degrees of material between the two faces
    point: tuple[float, float, float]  # where on the edge


def _in_face_direction(face, edge, tangent: gp_Vec, point: gp_Pnt) -> tuple[gp_Vec, gp_Vec]:
    """(outward normal, unit direction into the face, square to the edge) at `point` on `edge`."""
    ori = None
    ex = TopExp_Explorer(face, TopAbs_EDGE)
    while ex.More():
        if ex.Current().IsSame(edge):
            ori = ex.Current().Orientation()
            break
        ex.Next()
    t = gp_Vec(tangent.XYZ()) if ori == TopAbs_FORWARD else gp_Vec(tangent.XYZ()).Reversed()
    u, v = ShapeAnalysis_Surface(BRep_Tool.Surface_s(face)).ValueOfUV(point, 1e-6).Coord()
    q, n = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal(u, v, q, n)  # outward: the face's orientation is applied
    n.Normalize()
    return n, n.Crossed(t).Normalized()  # the face lies left of its oriented edge, seen from outside


def knife_edges(part: Part, max_angle: float = 50.0) -> list[KnifeEdge]:
    """Every convex edge where less than `max_angle` degrees of material lies between its two faces,
    sampled at a quarter, half and three quarters along the edge. Square edges are 90, a round
    hole's edges 90 or more; a wedge a cut left (a channel tangent-ish to a face) is sharper."""
    m = _EdgeFaces()
    TopExp.MapShapesAndAncestors_s(part.wrapped, TopAbs_EDGE, TopAbs_FACE, m)
    out = []
    for i in range(1, m.Extent() + 1):
        edge = TopoDS.Edge(m.FindKey(i))
        faces = [TopoDS.Face(f) for f in list_shapes(m.FindFromIndex(i))]
        if BRep_Tool.Degenerated_s(edge) or len(faces) != 2 or faces[0].IsSame(faces[1]):
            continue  # degenerate, free or a seam
        c = BRepAdaptor_Curve(edge)
        for s in (0.25, 0.5, 0.75):
            p, t = gp_Pnt(), gp_Vec()
            c.D1(c.FirstParameter() + s * (c.LastParameter() - c.FirstParameter()), p, t)
            if t.Magnitude() < 1e-9:
                continue
            t.Normalize()
            (_n0, d0), (n1, d1) = (_in_face_direction(f, edge, t, p) for f in faces)
            angle = math.degrees(math.acos(max(-1.0, min(1.0, d0.Dot(d1)))))
            if d0.Dot(n1) < -1e-6 and angle < max_angle:  # convex (face 0 runs under face 1's plane)
                out.append(KnifeEdge(angle, (p.X(), p.Y(), p.Z())))
                break
    return out


def small_section_pieces(part: Part, z0: float, z1: float, *, step: float = 0.5,
                         min_area: float = 3.0) -> list[tuple[float, float, tuple[float, float]]]:
    """(z, area mm^2, (x, y)) of every piece smaller than `min_area` in the part's sections
    z = z0, z0 + step, ... below z1 (each a 0.02 slab): a free-standing post a cut left shows here."""
    bb = part.bounding_box()
    sx, sy = bb.size.X + 2.0, bb.size.Y + 2.0
    cx, cy = (bb.min.X + bb.max.X) / 2, (bb.min.Y + bb.max.Y) / 2
    out, z = [], z0
    while z < z1:
        slab = part & (Pos(cx, cy, z + 0.01) * Box(sx, sy, 0.02))
        for piece in slab.solids() if slab else []:
            area = piece.volume / 0.02
            if area < min_area:
                out.append((z, area, (piece.center().X, piece.center().Y)))
        z += step
    return out
