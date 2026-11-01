#!/usr/bin/env python3
"""Render the jack plug-in / unplug animation as a frame strip (8 frames each, 3x zoom).
    python3 ui/tools/jack_strip.py OUT.png"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PIL import Image  # noqa: E402

import art  # noqa: E402
from gfx import Canvas, to_panel_rgb  # noqa: E402

FW, FH, Z = 64, 112, 3


def frame(kind, p):
    cv = Canvas(FW, FH)
    cv.fill(0x000000)
    lift, tilt, live, bow, pulse = art.jack_pose(kind, p, plugged=(kind == "in"))
    cx = 22
    art.jack(cv, cx, 62, lift=lift, tilt=tilt, glow=pulse, color=0xFF45C8, live=live, bow=bow)
    if pulse > 0:
        cv.glow(cx, 66, 26, 0xFF45C8, 0.7 * pulse)
        cv.circle(cx, 66, 12 + 12 * (1 - pulse), stroke=0xFF45C8, lw=2, alpha=0.8 * pulse)
    cv.line([(cx, 100), (cx, FH)], 0xFF45C8 if live else 0x464B55, 5)
    return Image.fromarray(to_panel_rgb(cv.rgb888()), "RGB").resize((FW * Z, FH * Z), Image.NEAREST)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "jack_anim.png"
    strip = Image.new("RGB", (8 * (FW * Z + 6) + 6, 2 * (FH * Z + 6) + 6), (50, 50, 56))
    for row, kind in enumerate(("in", "out")):
        for i in range(8):
            strip.paste(frame(kind, i / 7), (6 + i * (FW * Z + 6), 6 + row * (FH * Z + 6)))
    strip.save(out)
    print(out)


if __name__ == "__main__":
    main()
