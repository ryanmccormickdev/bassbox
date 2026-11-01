"""fbdev fallback display backend (same API as backend_drm.DrmDisplay).

/dev/fb0 on the panel is DRM's fbdev emulation with deferred I/O: writes land in a shadow
buffer, and a worker flushes the touched 4 KiB pages (= full-width row bands, 640 B/row) about
50 ms later. So a write returns immediately but becomes visible later, always full-width, and
small rects cost a full-width band. Prefer DrmDisplay; this exists for when DRM master is
unavailable.
"""
import fcntl
import glob
import mmap
import os

import numpy as np

from backend_drm import KD_GRAPHICS, KD_TEXT, KDSETMODE, _rects_to_clips, xrgb_to_rgb565


def find_panel_fb():
    for sysfs in sorted(glob.glob("/sys/class/graphics/fb[0-9]*")):
        name = open(f"{sysfs}/name").read().strip().lower()
        if "vc4" not in name:
            return f"/dev/{os.path.basename(sysfs)}"
    raise RuntimeError("no SPI panel framebuffer found (is the overlay loaded?)")


class FbDisplay:
    fmt = "rgb565"
    dtype = np.uint16

    def __init__(self, dev=None, take_vt=True):
        self.dev = dev or find_panel_fb()
        sysfs = f"/sys/class/graphics/{os.path.basename(self.dev)}"
        self.width, self.height = map(int, open(f"{sysfs}/virtual_size").read().split(","))
        bpp = int(open(f"{sysfs}/bits_per_pixel").read())
        self.pitch = int(open(f"{sysfs}/stride").read())
        if bpp != 16:
            raise RuntimeError(f"{self.dev}: expected 16bpp, got {bpp}")
        self.fd = os.open(self.dev, os.O_RDWR | os.O_CLOEXEC)
        self.mm = mmap.mmap(self.fd, self.pitch * self.height)
        self.buf = np.frombuffer(self.mm, dtype=np.uint16).reshape(
            self.height, self.pitch // 2)[:, : self.width]
        self.tty = None
        if take_vt:
            try:
                self.tty = os.open("/dev/tty0", os.O_RDWR)
                fcntl.ioctl(self.tty, KDSETMODE, KD_GRAPHICS)
            except OSError:
                self.tty = None

    def flush(self, src, rects=None, separate=None):
        """Write `rects` [(x, y, w, h)] of `src` (uint16 RGB565 or uint32 XRGB) into the fb.

        Only the touched pages get flushed by deferred I/O (~50 ms later). `separate` is
        accepted for API compatibility and ignored.
        """
        clips = [(0, 0, self.width, self.height)] if rects is None else \
            _rects_to_clips(rects, self.width, self.height)
        convert = src.dtype == np.uint32
        for x1, y1, x2, y2 in clips:
            region = src[y1:y2, x1:x2]
            self.buf[y1:y2, x1:x2] = xrgb_to_rgb565(region) if convert else region

    def close(self):
        if self.fd is None:
            return
        if self.tty is not None:
            try:
                fcntl.ioctl(self.tty, KDSETMODE, KD_TEXT)
            except OSError:
                pass
            os.close(self.tty)
            self.tty = None
        self.buf = None
        try:
            self.mm.close()
        except BufferError:
            pass
        os.close(self.fd)
        self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
