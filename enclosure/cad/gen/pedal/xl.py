"""Prusa XL profile for the pedal enclosure in clear PETG, plus the sliced plate.

`python -m cad.gen.pedal.xl [OUT_DIR]` resolves PrusaSlicer's own presets
(`Original Prusa XL Input Shaper 0.4 nozzle`, `0.15mm STRUCTURAL @XLIS 0.4`, `Generic PETG @XL`)
through `cad.gen.ender`'s bundle reader, puts the clear-PETG settings below on top, and writes:

* `prusa-xl-clear-petg-0.15.ini`: the flat profile (PrusaSlicer: File > Import > Import Config);
* `pedal-plate-xl-clear-petg.3mf`: the plate as a PrusaSlicer project with the settings inside;
* `pedal-plate-xl-clear-petg.gcode`: the slice (plain text; the XL reads it).

Clear PETG shows whatever is inside a wall, so the walls are all perimeters (5 x 0.45 fills the
2.0 mm walls: no infill or gap fill shows through), the thin floor and bezel are solid, and the
outer wall runs slow and hot for gloss. Units: mm, mm/s, C, %.
"""

from __future__ import annotations

import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from cad.gen import ender, export, slicer

NAMES = {
    "printer": "Original Prusa XL Input Shaper 0.4 nozzle",
    "print": "0.15mm STRUCTURAL @XLIS 0.4",
    "filament": "Generic PETG @XL",
}
PLATE = export.DRAFTS_DIR / "pedal" / "pedal-plate.3mf"
OUT = export.DRAFTS_DIR / "pedal" / "prusa-xl"
STEM = "pedal-plate-xl-clear-petg"
BED_CENTRE = "180,180"  # the XL's 360 x 360 bed

CLEAR_PETG = (
    ender.Override("perimeters", "5", "5 x 0.45 fills the 2.0 walls: nothing shows through clear PETG"),
    ender.Override("top_solid_layers", "7", "the 1.6 bezel and 2.0 floor print solid"),
    ender.Override("bottom_solid_layers", "7", "as top"),
    ender.Override("fill_density", "100%", "the little sparse infill left (bosses, webs) goes solid"),
    ender.Override("fill_pattern", "rectilinear", "solid fill"),
    ender.Override("solid_fill_pattern", "monotonic", "even solid layers read cleaner through clear"),
    ender.Override("top_fill_pattern", "monotonic", "as solid"),
    ender.Override("bottom_fill_pattern", "monotonic", "the lid's top face is printed on the bed"),
    ender.Override("external_perimeter_speed", "30", "slow outer wall: glossier, clearer"),
    ender.Override("perimeter_speed", "60", "inner walls"),
    ender.Override("small_perimeter_speed", "25", "posts, bosses, the jack holes"),
    ender.Override("first_layer_speed", "20", "the lid's visible top is the first layer"),
    ender.Override("temperature", "245", "hotter fuses clear PETG layers better (Generic PETG @XL: 240)"),
    ender.Override("first_layer_temperature", "240", "first layer"),
    ender.Override("bed_temperature", "80", "PETG"),
    ender.Override("first_layer_bed_temperature", "85", "PETG on the XL sheet"),
    ender.Override("min_fan_speed", "20", "low fan: stronger, clearer layers"),
    ender.Override("max_fan_speed", "40", "small layers only"),
    ender.Override("bridge_fan_speed", "60", "vent slots, snap windows, letters"),
    ender.Override("disable_fan_first_layers", "3", "first layers"),
    ender.Override("seam_position", "aligned", "one tidy seam line"),
    ender.Override("avoid_crossing_perimeters", "1", "fewer travel strings across visible faces"),
    ender.Override("elefant_foot_compensation", "0.2", "repo setting"),
    ender.Override("support_material", "0", "nothing needs support"),
    ender.Override("binary_gcode", "0", "plain-text G-code"),
)


def profile(names: dict[str, str] = NAMES) -> dict[str, str]:
    """The flat config: vendor presets (XL by default) with their `inherits` chains, then CLEAR_PETG."""
    creality = ender.find_bundle()  # the Creality bundle; Prusa's sits next to it
    bundle = None if creality is None else creality.with_name("PrusaResearch.ini")
    if bundle is None or not bundle.is_file():
        raise SystemExit("PrusaSlicer's PrusaResearch.ini bundle not found")
    sections = ender.parse_bundle(bundle.read_text(encoding="utf-8"))
    cfg: dict[str, str] = {}
    for kind, name in names.items():
        cfg.update(ender.resolve(sections, kind, name))
    for k in [k for k in cfg if k in ender.NOT_SETTINGS or k in ender.OBSOLETE]:
        del cfg[k]
    for key, legacy in ender.LEGACY_VALUES.items():
        if key in cfg:
            cfg[key] = legacy.get(cfg[key], cfg[key])
    cfg["printer_settings_id"] = names["printer"]
    cfg["print_settings_id"] = names["print"]
    cfg["filament_settings_id"] = f'"{names["filament"]}"'
    for o in CLEAR_PETG:
        cfg[o.key] = o.value
    return cfg


def slicer_version() -> str:
    """The installed PrusaSlicer's version, e.g. `2.9.6`."""
    exe = slicer.find_prusaslicer()
    out = "" if exe is None else subprocess.run([exe, "--help"], capture_output=True, text=True,
                                                check=False).stdout
    return out.split(" ", 1)[0].removeprefix("PrusaSlicer-") or "2.9"


def embed_config(three: Path, cfg: dict[str, str]) -> None:
    """Write the config into the 3MF as PrusaSlicer's projects carry it (the CLI's --export-3mf
    leaves it out), so opening the project loads these settings."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%d at %H:%M:%S UTC")
    text = f"; generated by PrusaSlicer {slicer_version()} on {stamp}\n"  # as the GUI writes it
    text += "".join(f"; {k} = {v}\n" for k, v in sorted(cfg.items()))
    with zipfile.ZipFile(three, "a", compression=zipfile.ZIP_DEFLATED) as z:
        if "Metadata/Slic3r_PE.config" not in z.namelist():
            z.writestr("Metadata/Slic3r_PE.config", text)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else OUT
    out.mkdir(parents=True, exist_ok=True)
    ini = out / "prusa-xl-clear-petg-0.15.ini"
    cfg = profile()
    ini.write_text("".join(f"{k} = {v}\n" for k, v in sorted(cfg.items())), encoding="utf-8")
    exe = slicer.find_prusaslicer()
    if exe is None:
        raise SystemExit("PrusaSlicer not found")
    common = [exe, "--load", str(ini), "--center", BED_CENTRE]
    for flag, path in (("--export-gcode", out / f"{STEM}.gcode"), ("--export-3mf", out / f"{STEM}.3mf")):
        proc = subprocess.run([*common, flag, "-o", str(path), str(PLATE)],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print(proc.stdout, proc.stderr)
            return proc.returncode
    embed_config(out / f"{STEM}.3mf", cfg)
    stats = slicer.parse_gcode(out / f"{STEM}.gcode")
    print(f"wrote {out}: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
