#!/usr/bin/env python3
"""Measure panel flush cost per path and rect size. Run on the Pi as root:

    sudo nice -n 5 taskset -c 0-2 python3 /opt/bassbox/ui/tools/bench_display.py [-n 30]

DRM DIRTYFB is synchronous (returns after the SPI transfer), so DRM numbers are time-to-glass.
fbdev numbers are only the mmap write; deferred I/O sends full-width page bands ~50 ms later.
"""
import argparse
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from backend_drm import DrmDisplay, xrgb_to_rgb565  # noqa: E402
from backend_fb import FbDisplay  # noqa: E402

W, H = 320, 480
SIZES = [("64x64", (128, 208, 64, 64)), ("320x80", (0, 200, 320, 80)), ("full 320x480", (0, 0, W, H))]


def stats(ts):
    ts = np.array(ts) * 1000
    return np.median(ts), np.percentile(ts, 90)


def frames(dtype, k=4):
    """k distinct full frames so every flush carries new content."""
    out = []
    yy, xx = np.mgrid[0:H, 0:W]
    for i in range(k):
        r = (xx * 255 // W + i * 60) % 256
        g = (yy * 255 // H + i * 90) % 256
        b = ((xx + yy) // 4 + i * 40) % 256
        a = ((r << 16) | (g << 8) | b).astype(np.uint32)
        out.append(a if dtype == np.uint32 else xrgb_to_rgb565(a))
    return out


def bench_conv(n):
    fr = frames(np.uint32, 1)[0]
    rows = []
    for name, (x, y, w, h) in SIZES:
        ts = []
        for _ in range(n):
            t = time.perf_counter()
            xrgb_to_rgb565(fr[y:y + h, x:x + w])
            ts.append(time.perf_counter() - t)
        rows.append((f"numpy xrgb->565 {name}", *stats(ts)))
    return rows


def bench_drm(fmt, n):
    rows = []
    with DrmDisplay(fmt=fmt) as d:
        fr = frames(d.dtype)
        for name, rect in SIZES:
            tc, ti, tt = [], [], []
            for i in range(n):
                src = fr[i % len(fr)]
                x, y, w, h = rect
                t0 = time.perf_counter()
                d.buf[y:y + h, x:x + w] = src[y:y + h, x:x + w]
                t1 = time.perf_counter()
                d._dirty([(x, y, x + w, y + h)])
                t2 = time.perf_counter()
                tc.append(t1 - t0); ti.append(t2 - t1); tt.append(t2 - t0)
            rows.append((f"DRM {fmt} {name}: total", *stats(tt)))
            rows.append((f"   copy to dumb", *stats(tc)))
            rows.append((f"   DIRTYFB ioctl", *stats(ti)))
        # two disjoint 64x64 rects at opposite corners
        rects = [(0, 0, 64, 64), (W - 64, H - 64, 64, 64)]
        for sep in (False, True):
            ts = []
            for i in range(n):
                t = time.perf_counter()
                d.flush(fr[i % len(fr)], rects, separate=sep)
                ts.append(time.perf_counter() - t)
            rows.append((f"DRM {fmt} 2 disjoint 64x64, {'2 ioctls' if sep else '1 ioctl/2 clips'}", *stats(ts)))
        # full frame through flush() incl. conversion when src is xrgb and fb is 565
        if fmt == "rgb565":
            fr32 = frames(np.uint32)
            for name, rect in SIZES:
                ts = []
                for i in range(n):
                    t = time.perf_counter()
                    d.flush(fr32[i % len(fr32)], [rect])
                    ts.append(time.perf_counter() - t)
                rows.append((f"DRM rgb565 {name} via flush(xrgb src, converts)", *stats(ts)))
    return rows


def bench_fb(n):
    rows = []
    with FbDisplay() as f:
        fr = frames(np.uint16)
        for name, rect in SIZES:
            ts = []
            for i in range(n):
                t = time.perf_counter()
                f.flush(fr[i % len(fr)], [rect])
                ts.append(time.perf_counter() - t)
                time.sleep(0.06)  # let deferred io flush so we don't just coalesce
            rows.append((f"fbdev write-only {name}", *stats(ts)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=30)
    ap.add_argument("--skip-fb", action="store_true")
    a = ap.parse_args()
    print(f"pid {os.getpid()} affinity {sorted(os.sched_getaffinity(0))} nice {os.nice(0)}")
    rows = bench_conv(a.n)
    rows += bench_drm("xrgb8888", a.n)
    rows += bench_drm("rgb565", a.n)
    if not a.skip_fb:
        rows += bench_fb(a.n)
    print(f"{'case':58s} {'median ms':>9s} {'p90 ms':>8s}")
    for name, med, p90 in rows:
        print(f"{name:58s} {med:9.2f} {p90:8.2f}")


if __name__ == "__main__":
    main()
