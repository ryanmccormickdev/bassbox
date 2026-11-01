"""Headless PrusaSlicer for sanity checks: print time, grams, and what the slicer did.

This is not a calibrated printer profile, so treat times and grams as estimates. Its real job is answering questions like "does a 0.85 mm
blade slice as exactly two perimeters with no gap fill?" before anything is printed.

Defaults: PETG, 0.4 mm
nozzle, 0.2 mm layers, 4 walls, 40% gyroid, elephant-foot compensation on, no XY or
hole compensation. Units: mm, s, g.

Install PrusaSlicer (macOS: `brew install --cask prusaslicer`) or set PRUSASLICER to the binary.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

APP_BIN = "/Applications/PrusaSlicer.app/Contents/MacOS/PrusaSlicer"

DEFAULTS: dict[str, str] = {
    "gcode-flavor": "marlin2",
    "nozzle-diameter": "0.4",
    "filament-diameter": "1.75",
    "filament-density": "1.27",  # PETG, g/cm³
    "temperature": "240",
    "bed-temperature": "80",
    "layer-height": "0.2",
    "first-layer-height": "0.2",
    "perimeters": "4",
    "top-solid-layers": "5",
    "bottom-solid-layers": "5",
    "fill-density": "40%",
    "fill-pattern": "gyroid",
    "elefant-foot-compensation": "0.2",
    "xy-size-compensation": "0",
    "bed-shape": "0x0,250x0,250x210,0x210",
}


def find_prusaslicer() -> str | None:
    """Path to the PrusaSlicer binary, or None if it isn't installed."""
    for cand in (os.environ.get("PRUSASLICER"), APP_BIN, shutil.which("prusa-slicer")):
        if cand and Path(cand).exists():
            return cand
    return None


def version() -> str | None:
    """PrusaSlicer's version banner (e.g. "PrusaSlicer-2.9.6"), or None if not installed."""
    exe = find_prusaslicer()
    if exe is None:
        return None
    out = subprocess.run([exe, "--help"], capture_output=True, text=True, check=False).stdout
    return out.split(" ", 1)[0] if out else None


@dataclass(frozen=True)
class SliceStats:
    gcode: Path
    time_s: float
    grams: float
    layers: int
    types: Mapping[str, int]  # count of extrusion moves per ;TYPE: (e.g. "Gap fill")


def _seconds(text: str) -> float:
    total = 0.0
    for n, unit in re.findall(r"(\d+)([dhms])", text):
        total += int(n) * {"d": 86400, "h": 3600, "m": 60, "s": 1}[unit]
    return total


def parse_gcode(gcode: Path) -> SliceStats:
    """Read the stats PrusaSlicer writes into the G-code footer, plus move counts by type."""
    time_s = grams = 0.0
    layers = 0
    types: Counter[str] = Counter()
    current = ""
    with gcode.open() as f:
        for line in f:
            if line.startswith(";TYPE:"):
                current = line[6:].strip()
            elif line.startswith(";LAYER_CHANGE"):
                layers += 1
            elif line.startswith("G1") and " E" in line and current:
                types[current] += 1
            elif line.startswith("; estimated printing time (normal mode)"):
                time_s = _seconds(line.split("=", 1)[1])
            elif line.startswith("; total filament used [g]"):
                grams = float(line.split("=", 1)[1])
    return SliceStats(gcode, time_s, grams, layers, dict(types))


@dataclass(frozen=True)
class Segment:
    """One extrusion move (mm, the slicer's bed frame) and its `;TYPE:`."""

    x0: float
    y0: float
    x1: float
    y1: float
    type: str


_WORD = re.compile(r"([XYZEF])(-?\d*\.?\d+)")


def layer_segments(gcode: Path, z: float) -> tuple[float, list[Segment]]:
    """Extrusion moves on the layer whose `;Z:` is nearest `z`. Returns (that Z, moves).

    Handles absolute (M82) and relative (M83) extrusion. Travel moves are skipped.
    """
    layers: dict[float, list[Segment]] = {}
    cur_z: float | None = None
    kind = ""
    x = y = e = 0.0
    relative = False
    with gcode.open() as f:
        for line in f:
            if line.startswith(";Z:"):
                cur_z = float(line[3:])
                layers.setdefault(cur_z, [])
            elif line.startswith(";TYPE:"):
                kind = line[6:].strip()
            elif line.startswith(("M82", "M83")):
                relative = line.startswith("M83")
            elif line.startswith("G92"):
                w = dict(_WORD.findall(line.split(";", 1)[0]))
                e = float(w.get("E", e))
            elif line.startswith(("G0 ", "G1 ")):
                w = {k: float(v) for k, v in _WORD.findall(line.split(";", 1)[0])}
                nx, ny = w.get("X", x), w.get("Y", y)
                extrudes = False
                if "E" in w:
                    extrudes = w["E"] > 0 if relative else w["E"] > e
                    e = e if relative else w["E"]
                if extrudes and (nx, ny) != (x, y) and cur_z is not None:
                    layers[cur_z].append(Segment(x, y, nx, ny, kind))
                x, y = nx, ny
    if not layers:
        raise ValueError(f"{gcode}: no ;Z: layer markers (not PrusaSlicer G-code?)")
    nearest = min(layers, key=lambda k: abs(k - z))
    return nearest, layers[nearest]


def slice_files(
    inputs: Sequence[Path], gcode: Path, overrides: Mapping[str, str] | None = None
) -> SliceStats:
    """Slice STL/3MF `inputs` (arranged on one bed) into `gcode` and return its stats."""
    exe = find_prusaslicer()
    if exe is None:
        raise FileNotFoundError("PrusaSlicer not found; brew install --cask prusaslicer")
    opts = {**DEFAULTS, **(overrides or {})}
    cmd = [exe, "--export-gcode", "-o", str(gcode)]
    if len(inputs) > 1:
        cmd.append("--merge")  # one bed, auto-arranged; otherwise one G-code per input
    for k, v in opts.items():
        cmd += [f"--{k}", v]
    cmd += [str(p) for p in inputs]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return parse_gcode(gcode)
