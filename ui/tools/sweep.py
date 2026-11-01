#!/usr/bin/env python3
"""Render every effect's board card and edit face with defaults; write contact sheets.
    python3 ui/tools/sweep.py OUTDIR      (needs a running UI + engine; mock recommended)
"""
import base64
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image  # noqa: E402
from shots import eng, set_chain, ui  # noqa: E402


def grab():
    r = ui({"cmd": "screenshot", "stream": True})
    return Image.open(io.BytesIO(base64.b64decode(r["png_b64"]))).convert("RGB")


def sheet(ims, cols, scale, path):
    w, h = int(320 * scale), int(480 * scale)
    rows = (len(ims) + cols - 1) // cols
    s = Image.new("RGB", (cols * (w + 6) + 6, rows * (h + 6) + 6), (50, 50, 56))
    for i, im in enumerate(ims):
        s.paste(im.resize((w, h), Image.LANCZOS), (6 + (i % cols) * (w + 6), 6 + (i // cols) * (h + 6)))
    s.save(path)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    os.makedirs(out, exist_ok=True)
    cat = eng("catalog")["effects"]
    faces, boards = [], []
    for i in range(0, len(cat), 3):
        set_chain([(e["id"], True, {}) for e in cat[i:i + 3]])
        ui({"cmd": "goto", "screen": "board"})
        boards.append(grab())
        st = ui({"cmd": "state"})
        for slot in st["chain"]:
            ui({"cmd": "goto", "screen": "edit", "uid": slot["uid"]})
            im = grab()
            im.save(os.path.join(out, f"face_{slot['type']}.png"))
            faces.append(im)
    set_chain([])
    sheet(faces, 11, 0.5, os.path.join(out, "faces.png"))
    sheet(boards, 6, 0.5, os.path.join(out, "boards.png"))
    print(len(faces), "faces,", len(boards), "boards")


if __name__ == "__main__":
    main()
