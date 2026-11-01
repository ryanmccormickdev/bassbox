"""Prusa MK3.9S profile for the pedal's base alone in clear PETG (a reprint after a jam).

`python -m cad.gen.pedal.mk39s [OUT_DIR]` takes Prusa's MK3.9S presets
(`Original Prusa MK3.9S 0.4 nozzle`, `0.15mm STRUCTURAL @MK4S 0.4`, `Generic PETG @MK4S`), puts
`xl.CLEAR_PETG` on top, and writes the profile `.ini`, the base as a PrusaSlicer project with the
settings inside, and the G-code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from cad.gen import export, slicer
from cad.gen.pedal import xl

NAMES = {
    "printer": "Original Prusa MK3.9S 0.4 nozzle",
    "print": "0.15mm STRUCTURAL @MK4S 0.4",
    "filament": "Generic PETG @MK4S",
}
PART = export.DRAFTS_DIR / "pedal" / "base.3mf"  # already floor down
OUT = export.DRAFTS_DIR / "pedal" / "prusa-mk39s"
STEM = "pedal-base-mk39s-clear-petg"
BED_CENTRE = "125,105"  # the MK3.9S's 250 x 210 bed


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else OUT
    out.mkdir(parents=True, exist_ok=True)
    ini = out / "prusa-mk39s-clear-petg-0.15.ini"
    cfg = xl.profile(NAMES)
    ini.write_text("".join(f"{k} = {v}\n" for k, v in sorted(cfg.items())), encoding="utf-8")
    exe = slicer.find_prusaslicer()
    if exe is None:
        raise SystemExit("PrusaSlicer not found")
    common = [exe, "--load", str(ini), "--center", BED_CENTRE]
    for flag, path in (("--export-gcode", out / f"{STEM}.gcode"), ("--export-3mf", out / f"{STEM}.3mf")):
        proc = subprocess.run([*common, flag, "-o", str(path), str(PART)],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            print(proc.stdout, proc.stderr)
            return proc.returncode
    xl.embed_config(out / f"{STEM}.3mf", cfg)
    stats = slicer.parse_gcode(out / f"{STEM}.gcode")
    print(f"wrote {out}: {stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
