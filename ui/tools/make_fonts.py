"""Build static, Latin-subset font instances from the variable OFL sources in ui/fonts/src.

Static instances matter: HarfBuzz (via Pillow/raqm) otherwise shapes with default-instance
advances while FreeType renders the requested weight, which breaks spacing.
    python3 ui/tools/make_fonts.py      (needs fonttools; run on the Mac, output is committed)
"""
import os
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer
from fontTools import subset

HERE = os.path.dirname(os.path.abspath(__file__))
FD = os.path.join(HERE, "..", "fonts")
# family: (source, [(name suffix, axis location)])
BUILD = {
    "Inter": ("Inter.ttf", [(f"{w}", {"wght": w, "opsz": 14}) for w in (400, 500, 600, 700, 800)]
              + [(f"{w}d", {"wght": w, "opsz": 32}) for w in (500, 600, 700, 800)]),
    "JetBrainsMono": ("JetBrainsMono.ttf", [(f"{w}", {"wght": w}) for w in (500, 600, 700, 800)]),
    "SpaceGrotesk": ("SpaceGrotesk.ttf", [(f"{w}", {"wght": w}) for w in (400, 500, 600, 700)]),
    "Archivo": ("Archivo.ttf", [(f"{w}", {"wght": w, "wdth": 100}) for w in (500, 600, 700, 800)]
                + [(f"{w}x", {"wght": w, "wdth": 125}) for w in (600, 700, 800, 900)]
                + [(f"{w}c", {"wght": w, "wdth": 75}) for w in (700, 800, 900)]),
    "Unbounded": ("Unbounded.ttf", [(f"{w}", {"wght": w}) for w in (400, 500, 600, 700, 800, 900)]),
}
UNICODES = list(range(0x20, 0x7F)) + list(range(0xA0, 0x180)) + [
    0x2013, 0x2014, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2026, 0x2032, 0x2033, 0x2190, 0x2191, 0x2192,
    0x2193, 0x2195, 0x2212, 0x221E, 0x2248, 0x2264, 0x2265, 0x266D, 0x266F, 0x00D7, 0x00BC, 0x00BD, 0x25B2, 0x25BC,
    0x25B6, 0x25C0, 0x2713, 0x2715, 0x00B7, 0x2009, 0x200A, 0x202F]
os.makedirs(os.path.join(FD, "static"), exist_ok=True)
for fam, (src, insts) in BUILD.items():
    for suffix, loc in insts:
        vf = TTFont(os.path.join(FD, "src", src))
        axes = {a.axisTag for a in vf["fvar"].axes}
        f = instancer.instantiateVariableFont(vf, {k: v for k, v in loc.items() if k in axes}, updateFontNames=False)
        opts = subset.Options()
        opts.layout_features = ["kern", "liga", "calt", "tnum", "case", "ss01", "ss02", "cv05", "zero", "frac"]
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=UNICODES)
        sub.subset(f)
        out = os.path.join(FD, "static", f"{fam}-{suffix}.ttf")
        f.save(out)
        print(out, os.path.getsize(out) // 1024, "KB")
