#!/usr/bin/env python3
"""Show a 320x480 PNG on the panel through DRM, hold it, then restore the console. Root.

    sudo python3 /opt/bassbox/ui/tools/show_png.py mock.png [--seconds N]
    sudo nohup python3 /opt/bassbox/ui/tools/show_png.py mock.png --seconds 120 >/dev/null 2>&1 &

Without --seconds it holds until SIGTERM/SIGINT. Images that aren't 320x480 are letterboxed.
"""
import argparse
import os
import signal
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from backend_drm import DrmDisplay  # noqa: E402


def to_xrgb(img, w, h):
    img = img.convert("RGB")
    if img.size != (w, h):
        img.thumbnail((w, h))
        canvas = Image.new("RGB", (w, h))
        canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
        img = canvas
    a = np.asarray(img, dtype=np.uint32)
    return (a[..., 0] << 16) | (a[..., 1] << 8) | a[..., 2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--seconds", type=float, default=None)
    ap.add_argument("--fmt", default="xrgb8888", choices=["xrgb8888", "rgb565"])
    a = ap.parse_args()
    stop = []
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.append(1))
    with DrmDisplay(fmt=a.fmt) as d:
        d.flush(np.ascontiguousarray(to_xrgb(Image.open(a.path), d.width, d.height)))
        end = None if a.seconds is None else time.monotonic() + a.seconds
        while not stop and (end is None or time.monotonic() < end):
            time.sleep(0.2)


if __name__ == "__main__":
    main()
