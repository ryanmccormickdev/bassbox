"""Slice for a Creality Ender-3 V2 Neo (0.4 mm, PETG or PLA): time, grams and fit.

`cad/gen/slicer.py` slices with generic speeds on a 250 x 210 bed, so its times are wrong for
the Ender. This module slices with **PrusaSlicer's own Creality presets** instead: printer
`Creality Ender-3 V2 Neo (0.4 mm nozzle)` (Bowden, CR Touch: its start G-code runs G28 then G29;
Marlin limits of 500 mm/s^2), print `0.20 mm NORMAL (0.4 mm nozzle) @CREALITY`, filament `Generic
PETG @CREALITY` or `Generic PLA @CREALITY`. The part settings go on top (4 walls, 5 top and
bottom layers, 40 % gyroid, elephant-foot 0.2, no supports), then the caller's `overrides`. The
Ender-3 Pro (`printer="PRO"`) is the other option.

Why flattened `.ini` files and not `--printer-profile / --print-profile / --material-profile`:
PrusaSlicer 2.9.6 checks those names, applies the print and filament presets, and silently
ignores the printer preset (the bed, G-code flavour, retraction, machine limits and start G-code
stay at the generic defaults). So `cad/slicer/<printer>-<material>-0.2.ini` holds the three presets
resolved through the vendor bundle's `inherits` chains, plus the overrides, and every slice is
`PrusaSlicer --load <that file>`. `--regen-profiles` rewrites the files from the installed
bundle, `--check-profiles` says whether they have drifted. Units: mm, s, g, m.

    python -m cad.gen.ender out/pedal/lid.stl
    python -m cad.gen.ender plate.3mf other.stl --material PLA --set perimeters=3
    python -m cad.gen.ender part.stl --printer pro

Behaviours of PrusaSlicer's command line this module works around (checked on 2.9.6):

* Objects outside the bed are **dropped silently** (only "All objects are outside of the print
  volume." when none are left), so a plate laid out for another printer would give a plausible
  time for half its parts. The G-code's own object labels are compared with the input's object
  count; if some are missing the slice is repeated on a virtual 1 m bed as an estimate only, the
  result says `fits=False`, and the G-code is named `*-NOFIT.gcode`.
* `--merge` arrangement crashes, and 3MF plates keep their positions. Plates are sliced as laid
  out and centred on the bed; re-pack a plate in its generator if it does not fit.
* Unknown keys in a `--load` file are ignored without a message, so `overrides` go on the
  command line, where PrusaSlicer rejects an unknown option.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cad.gen import cli_logging, slicer

log = logging.getLogger(__name__)

PROFILE_DIR = Path(__file__).resolve().parents[1] / "slicer"
OUT_DIR = Path(__file__).resolve().parents[2] / "out" / "ender"

PRINT = "0.20 mm NORMAL (0.4 mm nozzle) @CREALITY"
BUNDLE_NAME = "Creality.ini"

HOTEND_MAX_C = 240.0  # stock PTFE-lined hotend: PETG at most about 240 C
VIRTUAL_BED = 1000.0  # the bed for estimate-only slices of plates that do not fit
SLICE_TIMEOUT_S = 900.0
# print fit: flag parts within 10 mm of the build volume in X, Y or Z (no
# room for the skirt and an edge margin): over 210 x 210 x 240 on a 220 x 220 x 250 machine.
PLAN_MARGIN = 10.0


def bed_shape(origin: tuple[float, float], size: tuple[float, float]) -> str:
    """PrusaSlicer's `bed_shape` value for a rectangle."""
    (x0, y0), (dx, dy) = origin, size
    x1, y1 = x0 + dx, y0 + dy
    return f"{x0:g}x{y0:g},{x1:g}x{y0:g},{x1:g}x{y1:g},{x0:g}x{y1:g}"


@dataclass(frozen=True)
class Override:
    """One value this repo sets on top of the Creality presets, and why."""

    key: str
    value: str
    why: str


@dataclass(frozen=True)
class Printer:
    """A printer preset from the Creality bundle and the bed it is sliced for."""

    key: str  # CLI choice and the profile files' name
    preset: str  # the bundle's printer preset
    label: str
    origin: tuple[float, float]  # front-left corner of the bed shape in G-code coordinates
    size: tuple[float, float, float]  # X, Y, Z extent of the bed shape and of the build volume
    bed_override: Override | None = None  # a bed_shape this repo sets instead of the preset's

    @property
    def centre(self) -> tuple[float, float]:
        return (self.origin[0] + self.size[0] / 2, self.origin[1] + self.size[1] / 2)

    @property
    def limit(self) -> tuple[float, float, float]:
        """Planning limit: parts bigger than this leave no room for the skirt (see PLAN_MARGIN)."""
        return (self.size[0] - PLAN_MARGIN, self.size[1] - PLAN_MARGIN, self.size[2] - PLAN_MARGIN)


PRINTERS: dict[str, Printer] = {
    # Ender-3 V2 Neo: CR Touch, Bowden, 220 x 220 x 250. The preset's bed is X 5..215,
    # Y 0..220 (the start G-code primes along X 2): kept as it is.
    "V2NEO": Printer("v2neo", "Creality Ender-3 V2 Neo (0.4 mm nozzle)", "Ender-3 V2 Neo",
                     (5.0, 0.0), (210.0, 220.0, 250.0)),
    # The earlier assumption: the preset's 3x3..228x228 (225 mm) is more than the datasheet's
    # 220 x 220, so the profile trims it to 220 x 220 from the same origin.
    "PRO": Printer("pro", "Creality Ender-3 Pro (0.4 mm nozzle)", "Ender-3 Pro",
                   (3.0, 3.0), (220.0, 220.0, 250.0),
                   Override("bed_shape", bed_shape((3.0, 3.0), (220.0, 220.0)),
                            "the datasheet's 220 x 220 mm; the preset's 225 mm is more than the "
                            "plate is rated for")),
}
DEFAULT_PRINTER = "V2NEO"


def printer_of(name: str) -> Printer:
    """The `Printer` for "V2NEO" or "PRO" (any case; the CLI's v2neo / pro)."""
    try:
        return PRINTERS[name.upper()]
    except KeyError:
        raise ValueError(f"unknown printer {name!r}; choose from {sorted(PRINTERS)}") from None


# The part settings (every part README: 4 walls, 5 top and bottom, 40 % gyroid, supports
# off, elephant-foot compensation 0.2, no XY compensation). The same values as `slicer.DEFAULTS`.
PART_SETTINGS = (
    Override("perimeters", "4", "4 walls (the Creality preset has 3)"),
    Override("top_solid_layers", "5", "5 top layers (the preset has 5 as well)"),
    Override("bottom_solid_layers", "5", "5 bottom layers (the preset has 4)"),
    Override("fill_density", "40%", "40 % infill (the preset has 15 %)"),
    Override("fill_pattern", "gyroid", "gyroid infill (the preset has grid)"),
    Override("elefant_foot_compensation", "0.2", "elephant-foot compensation 0.2 (the preset 0.1)"),
    Override("support_material", "0", "supports off (the preset has them off as well)"),
    Override("xy_size_compensation", "0", "no XY compensation (the preset has none)"),
)


@dataclass(frozen=True)
class Material:
    """A filament preset from the Creality bundle and what this repo changes on it."""

    filament: str
    overrides: tuple[Override, ...] = ()


MATERIALS: dict[str, Material] = {
    "PETG": Material("Generic PETG @CREALITY", (
        Override("temperature", "235",
                 "layers 2+ at 235 C: the stock PTFE-lined hotend is good for about 240 C, so "
                 "leave 5 C of margin for the thermistor and overshoot (the preset has 240)"),
        Override("bed_temperature", "75",
                 "75 C: warp control for large flat parts, without the squish of 80 C (the "
                 "preset has 70)"),
        Override("first_layer_bed_temperature", "75", "as bed_temperature"),
    )),
    "PLA": Material("Generic PLA @CREALITY"),
}

# Vendor-bundle keys that are not print settings (GUI bookkeeping), and keys the bundle still sets
# but PrusaSlicer 2.9.6 no longer has (its own preset loader drops them as well). Legacy
# spellings of enum values that 2.9.6 converts when it loads the bundle.
NOT_SETTINGS = frozenset({
    "inherits", "renamed_from", "alias", "compatible_printers", "compatible_printers_condition",
    "compatible_prints", "compatible_prints_condition", "default_filament_profile",
    "default_print_profile",
})
OBSOLETE = frozenset({
    "clip_multipart_objects", "external_fill_pattern", "infill_only_where_needed", "top_fan_speed",
    "wipe_tower_rotation_angle", "wipe_tower_x", "wipe_tower_y",
})
LEGACY_VALUES = {
    "ensure_vertical_shell_thickness": {"0": "disabled", "1": "enabled"},
    "gcode_label_objects": {"0": "disabled", "1": "octoprint"},
}
ID_KEYS = ("printer_settings_id", "print_settings_id", "filament_settings_id")


# ---------------------------------------------------------------------------------------
# The profiles: PrusaSlicer's Creality bundle, flattened.


def find_bundle() -> Path | None:
    """PrusaSlicer's `Creality.ini`: `$PRUSASLICER_PROFILES`, else next to the installed binary."""
    cands: list[Path] = []
    if env := os.environ.get("PRUSASLICER_PROFILES"):
        cands.append(Path(env) / BUNDLE_NAME)
    if exe := slicer.find_prusaslicer():
        top = Path(exe).resolve().parents[1]
        cands += [top / "Resources" / "profiles" / BUNDLE_NAME,  # macOS .app
                  top / "share" / "PrusaSlicer" / "profiles" / BUNDLE_NAME,  # Linux
                  top / "resources" / "profiles" / BUNDLE_NAME]  # Windows, AppImage
    return next((c for c in cands if c.is_file()), None)


def parse_bundle(text: str) -> dict[str, dict[str, str]]:
    """A vendor bundle as {"print:0.20 mm NORMAL ...": {key: value}}; comments are dropped."""
    sections: dict[str, dict[str, str]] = {}
    cur: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            cur = sections.setdefault(line[1:-1].strip(), {})
        elif cur is not None and "=" in line:
            key, value = line.split("=", 1)
            cur[key.strip()] = value.strip()
    return sections


def resolve(sections: Mapping[str, Mapping[str, str]], kind: str, name: str,
            _trail: tuple[str, ...] = ()) -> dict[str, str]:
    """One preset with its `inherits` chain applied.

    The parents come first, in the order listed (a later parent overrides an earlier one, which is
    how `*speed*` makes the SPEED presets faster than `*common*`), the preset's own keys last. The
    result matched PrusaSlicer's own resolution for every print and filament preset checked.
    """
    key = f"{kind}:{name}"
    if key in _trail:
        raise ValueError(f"inheritance cycle at {key!r}")
    if key not in sections:
        raise KeyError(f"[{key}] is not in the Creality bundle")
    section = sections[key]
    out: dict[str, str] = {}
    for parent in (p.strip() for p in section.get("inherits", "").split(";") if p.strip()):
        out.update(resolve(sections, kind, parent, (*_trail, key)))
    out.update({k: v for k, v in section.items() if k != "inherits"})
    return out


@dataclass(frozen=True)
class Profile:
    """A flattened config and what was changed on it: (key, vendor value or None, ours, why)."""

    config: dict[str, str]
    changes: tuple[tuple[str, str | None, str, str], ...]
    dropped: tuple[str, ...]
    bundle_version: str


def build_profile(material: str, bundle_text: str, *, printer: str = DEFAULT_PRINTER) -> Profile:
    """The `printer` + 0.20 mm NORMAL + `material` presets from `bundle_text`, flattened.

    Order of layers: vendor presets (printer, print, filament), the printer's bed override (if it
    has one), the repo's part settings, then the material's overrides.
    """
    mat = MATERIALS[material.upper()]
    prn = printer_of(printer)
    sections = parse_bundle(bundle_text)
    vendor_version = sections.get("vendor", {}).get("config_version", "unknown")
    names = {"printer": prn.preset, "print": PRINT, "filament": mat.filament}
    cfg: dict[str, str] = {}
    for kind, name in names.items():
        cfg.update(resolve(sections, kind, name))
    dropped = sorted(k for k in cfg if k in OBSOLETE)
    for k in [k for k in cfg if k in NOT_SETTINGS or k in OBSOLETE]:
        del cfg[k]
    for key, legacy in LEGACY_VALUES.items():
        if key in cfg:
            cfg[key] = legacy.get(cfg[key], cfg[key])
    for key, name in zip(ID_KEYS, names.values(), strict=True):
        cfg[key] = f'"{name}"' if key == "filament_settings_id" else name
    changes: list[tuple[str, str | None, str, str]] = []
    for o in (*((prn.bed_override,) if prn.bed_override else ()), *PART_SETTINGS, *mat.overrides):
        changes.append((o.key, cfg.get(o.key), o.value, o.why))
        cfg[o.key] = o.value
    return Profile(cfg, tuple(changes), tuple(dropped), vendor_version)


def profile_name(material: str, printer: str = DEFAULT_PRINTER) -> str:
    return f"ender3{printer_of(printer).key}-{material.lower()}-0.2.ini"


def render_profile(material: str, bundle_text: str, *, printer: str = DEFAULT_PRINTER,
                   slicer_version: str | None = None) -> str:
    """The text of `cad/slicer/ender3<printer>-<material>-0.2.ini`: a header, then sorted keys."""
    mat = MATERIALS[material.upper()]
    prn = printer_of(printer)
    prof = build_profile(material, bundle_text, printer=printer)
    ver = f" ({slicer_version})" if slicer_version else ""
    head = [
        (f"# PrusaSlicer config: Creality {prn.label}, 0.4 mm nozzle, {material.upper()}, "
         "0.20 mm layers."),
        ("# Written by `python -m cad.gen.ender --regen-profiles`; do not edit by hand "
         "(edit cad/gen/ender.py)."),
        f"# From PrusaSlicer's Creality bundle {prof.bundle_version}{ver}, `inherits` resolved:",
        f"#   printer   {prn.preset}",
        f"#   print     {PRINT}",
        f"#   filament  {mat.filament}",
        "# Changed on top of those presets (key: preset value -> value used; why):",
    ]
    for key, vendor, ours, why in prof.changes:
        was = "(unset)" if vendor is None else vendor
        note = "same as the preset" if vendor == ours else f"{was} -> {ours}"
        head.append(f"#   {key}: {note}; {why}")
    if prof.dropped:
        head.append("# Keys the bundle sets that PrusaSlicer 2.9 no longer has, left out: "
                    + ", ".join(prof.dropped))
    head += [
        ("# Use: PrusaSlicer --load this.ini model.stl --export-gcode   (or the GUI: File > "
         "Import > Import Config)"),
        "",
    ]
    body = [f"{k} = {v}" for k, v in sorted(prof.config.items())]
    return "\n".join([*head, *body, ""])


def profile_path(material: str, printer: str = DEFAULT_PRINTER) -> Path:
    """The committed profile for `material` ("PETG" or "PLA") on `printer` ("V2NEO" or "PRO")."""
    if material.upper() not in MATERIALS:
        raise ValueError(f"unknown material {material!r}; choose from {sorted(MATERIALS)}")
    path = PROFILE_DIR / profile_name(material, printer)
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing: run `python -m cad.gen.ender "
                                "--regen-profiles`")
    return path


def read_config(path: Path) -> dict[str, str]:
    """The `key = value` lines of a PrusaSlicer config file (comments dropped)."""
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith(("#", ";")) and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def start_gcode_commands(config: Mapping[str, str]) -> list[str]:
    """The commands of a profile's start G-code, one per entry, comments dropped.

    The ini keeps the multi-line G-code on one line with `\\n` escapes; `{...}` placeholders
    stay as they are.
    """
    lines = (c.split(";", 1)[0].strip() for c in config.get("start_gcode", "").split("\\n"))
    return [c for c in lines if c]


def regen_profiles(out_dir: Path = PROFILE_DIR, *, check: bool = False) -> list[str]:
    """Write (or with `check`, only compare) every profile from the installed bundle.

    Returns the names of the files that differ (or were rewritten).
    """
    bundle = find_bundle()
    if bundle is None:
        raise FileNotFoundError("PrusaSlicer's Creality.ini not found; install PrusaSlicer or set "
                                "PRUSASLICER_PROFILES to the folder that has it")
    text = bundle.read_text(encoding="utf-8")
    changed: list[str] = []
    for printer in PRINTERS:
        for material in MATERIALS:
            path = out_dir / profile_name(material, printer)
            new = render_profile(material, text, printer=printer, slicer_version=slicer.version())
            old = path.read_text(encoding="utf-8") if path.is_file() else None
            if old != new:
                changed.append(path.name)
                if not check:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    path.write_text(new, encoding="utf-8")
    return changed


# ---------------------------------------------------------------------------------------
# Reading PrusaSlicer's G-code.


@dataclass
class _Box:
    x0: float = float("inf")
    y0: float = float("inf")
    x1: float = float("-inf")
    y1: float = float("-inf")

    def add(self, x: float, y: float) -> None:
        self.x0, self.x1 = min(self.x0, x), max(self.x1, x)
        self.y0, self.y1 = min(self.y0, y), max(self.y1, y)

    @property
    def empty(self) -> bool:
        return self.x0 > self.x1


@dataclass(frozen=True)
class GcodeScan:
    """What one pass over a PrusaSlicer G-code file finds besides `slicer.parse_gcode`'s numbers."""

    metres: float  # filament length, from `; filament used [mm]`
    cm3: float  # filament volume, from `; filament used [cm3]`
    objects: frozenset[tuple[str, str, str]]  # (name, id, copy) from `; printing object` labels
    parts: tuple[float, float, float, float]  # x0, y0, x1, y1 of the extrusions, skirt excluded
    skirt: tuple[float, float, float, float]  # the same with the skirt and brim
    z_max: float  # top of the last layer

    @property
    def size(self) -> tuple[float, float, float]:
        """X, Y, Z extent of the printed parts (no skirt): the centre lines, so about one
        extrusion width (0.4 mm) less than the model's outline."""
        x0, y0, x1, y1 = self.parts
        if x0 > x1:
            return (0.0, 0.0, self.z_max)
        return (x1 - x0, y1 - y0, self.z_max)


_OBJECT_LABEL = re.compile(r"^; printing object (.*) id:(\d+) copy (\d+)\s*$")
_WORD = re.compile(r"([XYE])(-?\d*\.?\d+)")
_SKIRT_TYPES = ("Skirt/Brim", "Skirt", "Brim")


def scan_gcode(gcode: Path) -> GcodeScan:
    """Extents of the extrusions, object labels and the footer's filament length and volume.

    Only moves after the first `;LAYER_CHANGE` count, so the start G-code's prime lines (X 2, along
    the left edge) are left out. Handles absolute (`M82`) and relative (`M83`) extrusion.
    """
    parts, everything = _Box(), _Box()
    objects: set[tuple[str, str, str]] = set()
    metres = cm3 = z_max = 0.0
    x = y = e = 0.0
    relative = True
    started = False
    kind = ""
    with gcode.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith(("G1 ", "G0 ")):
                words = {k: float(v) for k, v in _WORD.findall(line.split(";", 1)[0])}
                nx, ny = words.get("X", x), words.get("Y", y)
                extrudes = False
                if "E" in words:
                    extrudes = words["E"] > 0 if relative else words["E"] > e
                    if not relative:
                        e = words["E"]
                if started and extrudes and (nx, ny) != (x, y):
                    for box in (everything, parts) if kind not in _SKIRT_TYPES else (everything,):
                        box.add(x, y)
                        box.add(nx, ny)
                x, y = nx, ny
            elif line.startswith(";TYPE:"):
                kind = line[6:].strip()
            elif line.startswith(";LAYER_CHANGE"):
                started = True
            elif line.startswith(";Z:"):
                z_max = max(z_max, float(line[3:]))
            elif line.startswith("; printing object"):
                if m := _OBJECT_LABEL.match(line):
                    objects.add((m.group(1), m.group(2), m.group(3)))
            elif line.startswith(("M82", "M83")):
                relative = line.startswith("M83")
            elif line.startswith("G92"):
                e = float(dict(_WORD.findall(line.split(";", 1)[0])).get("E", e))
            elif line.startswith("; filament used [mm]"):
                metres = float(line.split("=", 1)[1]) / 1000.0
            elif line.startswith("; filament used [cm3]"):
                cm3 = float(line.split("=", 1)[1])

    def rect(b: _Box) -> tuple[float, float, float, float]:
        return (b.x0, b.y0, b.x1, b.y1)

    return GcodeScan(metres, cm3, frozenset(objects), rect(parts), rect(everything), z_max)


def fit_warnings(scan: GcodeScan, printer: str = DEFAULT_PRINTER) -> list[str]:
    """Soft warnings about a plate that PrusaSlicer did slice on the real bed."""
    prn = printer_of(printer)
    out: list[str] = []
    sx, sy, sz = scan.size
    lx, ly, lz = prn.limit
    if sx > lx or sy > ly:
        out.append(f"parts span {sx:.1f} x {sy:.1f} mm, over the {lx:g} x {ly:g} mm planning "
                   f"limit ({prn.size[0]:g} x {prn.size[1]:g} bed less room for the skirt)")
    if sz > lz:
        out.append(f"parts are {sz:.1f} mm tall, over the {lz:g} mm planning limit "
                   f"(the Z axis stops at {prn.size[2]:g})")
    x0, y0, x1, y1 = scan.skirt
    if x0 <= x1:
        (bx0, by0), (bx1, by1) = prn.origin, (prn.origin[0] + prn.size[0],
                                              prn.origin[1] + prn.size[1])
        if x0 < bx0 - 0.5 or y0 < by0 - 0.5 or x1 > bx1 + 0.5 or y1 > by1 + 0.5:
            out.append(f"the skirt reaches X {x0:.1f}..{x1:.1f}, Y {y0:.1f}..{y1:.1f}, past the bed "
                       f"(X {bx0:g}..{bx1:g}, Y {by0:g}..{by1:g}); the start G-code primes along "
                       "X 2 on the left")
    return out


def print_warnings(output: str) -> list[str]:
    """PrusaSlicer's `print warning:` blocks from its console output, each as one line."""
    found: list[str] = []
    cur: list[str] | None = None
    for line in output.splitlines():
        if re.match(r"^\d+ => ", line) or line.startswith("Slicing result"):
            cur = None
        elif line.startswith("print warning:"):
            cur = [line.split(":", 1)[1].strip()]
            found.append("")
        elif cur is not None and line.strip():
            cur.append(line.strip())
        if cur is not None:
            found[-1] = " ".join(cur)
    return [f"PrusaSlicer: {w}" for w in found if w]


# ---------------------------------------------------------------------------------------
# Slicing.


class EnderSliceError(RuntimeError):
    """PrusaSlicer failed (crash, bad option, bad input)."""


@dataclass(frozen=True)
class EnderResult:
    """One slice: figures from the G-code footer, the printed parts' size, and what to look at."""

    model: Path
    gcode: Path | None  # None when PrusaSlicer wrote nothing
    printer: str  # the Printer's label
    material: str
    time_s: float  # PrusaSlicer's estimate (normal mode); heat-up is not in it
    grams: float
    metres: float  # 1.75 mm filament
    cm3: float
    layers: int
    size: tuple[float, float, float]  # X, Y, Z of the printed parts, no skirt (mm)
    fits: bool  # every object of the model is inside the 220 x 220 x 250 bed
    warnings: tuple[str, ...]
    types: Mapping[str, int]  # extrusion moves per `;TYPE:`

    @property
    def minutes(self) -> float:
        return self.time_s / 60.0

    @property
    def hours_minutes(self) -> str:
        total = round(self.time_s / 60.0)
        return f"{total // 60}h {total % 60:02d}m"


def _cli_flag(key: str) -> str:
    return "--" + key.strip().lstrip("-").replace("_", "-")


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False,
                              timeout=SLICE_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise EnderSliceError(f"PrusaSlicer took more than {SLICE_TIMEOUT_S:g} s: {e}") from e


def count_objects(exe: str, model: Path) -> int:
    """How many objects PrusaSlicer finds in `model` (one `--info` block each; 0 if unknown)."""
    out = _run([exe, "--info", str(model)]).stdout
    return sum(1 for line in out.splitlines() if re.fullmatch(r"\[.+\]", line.strip()))


def _gcode_target(model: Path, out: Path, material: str) -> Path:
    if out.suffix.lower() == ".gcode":
        return out
    return out / f"{model.stem}-ender-{material.lower()}.gcode"


def _hotend_warnings(overrides: Mapping[str, str]) -> list[str]:
    out: list[str] = []
    for key, value in overrides.items():
        if key.replace("-", "_") in ("temperature", "first_layer_temperature"):
            try:
                hot = float(value) > HOTEND_MAX_C
            except ValueError:
                continue
            if hot:
                out.append(f"{key} {value} C is over the {HOTEND_MAX_C:g} C the stock PTFE-lined "
                           "hotend should see")
    return out


def _slice(exe: str, config: Path, model: Path, gcode: Path, flags: Iterable[str],
           centre: tuple[float, float] | None) -> str:
    """Run PrusaSlicer; returns its console output. Raises `EnderSliceError` if it fails."""
    gcode.unlink(missing_ok=True)  # PrusaSlicer exits 0 without writing when nothing fits
    where = ["--center", f"{centre[0]:g},{centre[1]:g}"] if centre else []
    cmd = [exe, "--load", str(config), *flags, *where, "--export-gcode", "-o", str(gcode),
           str(model)]
    proc = _run(cmd)
    text = proc.stdout + proc.stderr
    if proc.returncode != 0:
        tail = "\n".join(text.strip().splitlines()[-6:])
        raise EnderSliceError(f"PrusaSlicer exited {proc.returncode} on {model.name}:\n{tail}")
    return text


def slice_ender(model: Path, out: Path, *, material: str = "PETG", printer: str = DEFAULT_PRINTER,
                overrides: Mapping[str, str] | None = None, centre: bool = True) -> EnderResult:
    """Slice `model` (STL or 3MF plate) for the Ender and read the footer.

    `printer` is "V2NEO" (default) or "PRO". `out` is the G-code file (a name ending in
    `.gcode`) or a folder for `<stem>-ender-<material>.gcode`. `overrides` are PrusaSlicer
    options (`{"perimeters": "3", "fill-density": "20%"}`, dashes or underscores), applied last;
    an unknown one raises `EnderSliceError`. The model is centred on the bed unless `centre` is
    False (a plate already laid out for the bed). If it does not fit (objects outside the bed
    shape) the result has `fits=False`, the figures come from a slice on a virtual bed and the
    G-code is named `*-NOFIT.gcode`. Raises `FileNotFoundError` if PrusaSlicer or the profile
    is missing.
    """
    exe = slicer.find_prusaslicer()
    if exe is None:
        raise FileNotFoundError("PrusaSlicer not found; brew install --cask prusaslicer")
    prn = printer_of(printer)
    config = profile_path(material, printer)
    model = Path(model)
    if not model.is_file():
        raise FileNotFoundError(model)
    flags = [f"{_cli_flag(k)}={v}" for k, v in (overrides or {}).items()]
    warnings = _hotend_warnings(overrides or {})
    gcode = _gcode_target(model, Path(out), material)
    gcode.parent.mkdir(parents=True, exist_ok=True)

    expected = count_objects(exe, model)
    text = _slice(exe, config, model, gcode, flags, prn.centre if centre else None)
    printed = len(scan_gcode(gcode).objects) if gcode.is_file() else 0
    fits = gcode.is_file() and printed >= expected
    if not fits:
        gcode.unlink(missing_ok=True)  # partial: PrusaSlicer left the objects outside the bed out
        gcode = gcode.with_name(f"{gcode.stem}-NOFIT.gcode")
        warnings.append(
            f"{expected - printed} of {expected} objects lie outside the {prn.label} bed "
            f"(X {prn.origin[0]:g}..{prn.origin[0] + prn.size[0]:g}, Y {prn.origin[1]:g}.."
            f"{prn.origin[1] + prn.size[1]:g}, Z up to {prn.size[2]:g}; PrusaSlicer would leave "
            "them out of the G-code); the figures are for the whole model on a virtual bed, an "
            "estimate only")
        virtual = (VIRTUAL_BED / 2, VIRTUAL_BED / 2)
        text = _slice(exe, config, model, gcode,
                      [f"--bed-shape={bed_shape((0.0, 0.0), (VIRTUAL_BED, VIRTUAL_BED))}",
                       f"--max-print-height={VIRTUAL_BED:g}", *flags], virtual)
        if not gcode.is_file():
            raise EnderSliceError(f"PrusaSlicer wrote no G-code for {model.name}:\n{text[-300:]}")
    stats = slicer.parse_gcode(gcode)
    scan = scan_gcode(gcode)
    warnings += print_warnings(text)
    if fits:
        warnings += fit_warnings(scan, printer)
    else:
        sx, sy, sz = scan.size
        warnings.append(f"as laid out the parts span {sx:.1f} x {sy:.1f} x {sz:.1f} mm: re-pack "
                        "the plate in its generator or split it over two beds")
    return EnderResult(model, gcode, prn.label, material.upper(), stats.time_s, stats.grams,
                       scan.metres, scan.cm3, stats.layers, scan.size, fits, tuple(warnings),
                       stats.types)


# ---------------------------------------------------------------------------------------
# The command line.


def _parse_overrides(items: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise argparse.ArgumentTypeError(f"--set wants KEY=VALUE, got {item!r}")
        out[key.strip()] = value
    return out


def format_table(results: Sequence[EnderResult]) -> str:
    """One line per slice, a total row for several, then the warnings."""
    rows = [("file", "time", "grams", "metres", "layers", "parts X x Y x Z (mm)", "fits")]
    for r in results:
        sx, sy, sz = r.size
        rows.append((r.model.name, r.hours_minutes, f"{r.grams:.1f}", f"{r.metres:.2f}",
                     str(r.layers), f"{sx:.1f} x {sy:.1f} x {sz:.1f}", "yes" if r.fits else "NO"))
    if len(results) > 1:
        total = sum(r.time_s for r in results)
        h, m = divmod(round(total / 60.0), 60)
        rows.append(("total", f"{h}h {m:02d}m", f"{sum(r.grams for r in results):.1f}",
                     f"{sum(r.metres for r in results):.2f}", "", "", ""))
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths, strict=True)).rstrip() for row in rows]
    for r in results:
        if r.gcode is not None:
            lines.append(f"{r.model.name}: {r.gcode}")
        lines += [f"  ! {w}" for w in r.warnings]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m cad.gen.ender", description=__doc__.splitlines()[0])
    ap.add_argument("models", nargs="*", type=Path, help="STL or 3MF files, each sliced on its own")
    ap.add_argument("--material", default="PETG", type=str.upper, choices=sorted(MATERIALS))
    ap.add_argument("--printer", default=DEFAULT_PRINTER, type=str.upper, choices=sorted(PRINTERS),
                    help="the Ender-3 V2 Neo (default) or the Ender-3 Pro")
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="folder for the G-code "
                    "(default out/ender/)")
    ap.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                    help="a PrusaSlicer option for this run, e.g. perimeters=3 (repeatable)")
    ap.add_argument("--regen-profiles", action="store_true",
                    help="rewrite cad/slicer/ender3*-0.2.ini from the installed PrusaSlicer")
    ap.add_argument("--check-profiles", action="store_true",
                    help="exit 1 if those files differ from the installed PrusaSlicer's bundle")
    args = ap.parse_args(argv)
    cli_logging()
    if args.regen_profiles or args.check_profiles:
        changed = regen_profiles(check=args.check_profiles)
        verb = "differ from the bundle" if args.check_profiles else "rewritten"
        log.info("%s: %s", ", ".join(changed) if changed else "profiles", verb if changed else
                 "up to date")
        return 1 if changed and args.check_profiles else 0
    if not args.models:
        ap.error("give at least one STL or 3MF file (or --regen-profiles)")
    try:
        overrides = _parse_overrides(args.overrides)
    except argparse.ArgumentTypeError as e:
        ap.error(str(e))
    log.info("Creality %s, %s, 0.20 mm: %s (%s)", printer_of(args.printer).label, args.material,
             profile_path(args.material, args.printer).relative_to(PROFILE_DIR.parents[1]),
             slicer.version() or "PrusaSlicer")
    results: list[EnderResult] = []
    failed = False
    for model in args.models:
        try:
            results.append(slice_ender(model, args.out, material=args.material,
                                       printer=args.printer, overrides=overrides))
        except (EnderSliceError, FileNotFoundError) as e:
            log.error("%s: %s", model, e)
            failed = True
    if results:
        print(format_table(results))
    return 1 if failed or any(not r.fits for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

