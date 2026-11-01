"""Output folder, mesh tolerances and the print-plate item shared by the pedal generator. Units are mm.

`python -m cad.gen.pedal` writes everything under `out/pedal/` (not tracked): STL and 3MF per
part, one 3MF print plate, a STEP of the installed parts, PNG renders and the check report.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from build123d import Location, Mesher, Part

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]  # the enclosure/ folder
DRAFTS_DIR = ROOT / "out"

# Mesh tolerances (mm, rad). Fine enough that a 1.1 mm bore prints round.
STL_TOL = 0.005
STL_ANGULAR_TOL = 0.05
# STEP files skip pcurves (2-D copies of every edge on every face): Fusion and FreeCAD
# rebuild them on import, and the file shrinks by more than half.
STEP_PCURVES = False


def draft_dir(family: str) -> Path:
    """Output folder for `family` (created on demand)."""
    d = DRAFTS_DIR / family
    d.mkdir(parents=True, exist_ok=True)
    return d


@dataclass(frozen=True)
class PlateItem:
    """A piece placed on a shared print bed. `x`, `y` in mm, bed frame."""

    name: str
    part: Part
    x: float
    y: float


def export_plate_3mf(items: Sequence[PlateItem], path: Path) -> Path:
    """Write every item as its own object in one 3MF, laid out on the bed."""
    mesher = Mesher()
    for it in items:
        placed = it.part.moved(Location((it.x, it.y, 0)))
        mesher.add_shape(
            placed, linear_deflection=STL_TOL, angular_deflection=STL_ANGULAR_TOL,
            part_number=it.name,
        )
    mesher.write(str(path))
    log.info("wrote %s (%d objects)", path.name, len(items))
    return path
