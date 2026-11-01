"""bassbox display + touch primitives (no GUI toolkit, no X/Wayland).

The 4.0" ST7796S panel is a plain Linux framebuffer (/dev/fb0, 320x480 portrait,
RGB565) driven by the kernel's panel-mipi-dbi-spi driver; rotation is baked into
/lib/firmware/st7796s.bin. Touch is the XPT2046 via the ads7846 driver, read raw
from evdev and mapped to screen pixels with an affine calibration.

    from screen import Screen
    with Screen() as s:              # takes over the console (needs root)
        img, d = s.canvas()          # PIL Image + ImageDraw, s.W x s.H
        d.text((10, 10), "hi", font=font(24), fill="white")
        s.show(img)
        x, y = s.wait_tap()          # debounced tap, in screen pixels

Needs: python3-numpy, python3-pil. Run as root (fb, input, tty).
"""
import fcntl
import glob
import json
import mmap
import os
import select
import struct
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
# Calibration is per-device state: saved outside /opt/bassbox because scripts/push.sh
# rsyncs with --delete. The repo copy is the fallback for a fresh install.
CAL_PATH = "/var/lib/bassbox/touch_cal.json"
CAL_DEFAULT = os.path.join(HERE, "..", "config", "touch_cal.json")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

EV_SYN, EV_KEY, EV_ABS = 0, 1, 3
SYN_REPORT, SYN_DROPPED = 0, 3
ABS_X, ABS_Y, ABS_PRESSURE = 0, 1, 0x18
BTN_TOUCH = 0x14A
EVENT_FMT = "llHHi"
EVENT_SIZE = struct.calcsize(EVENT_FMT)
KDSETMODE, KD_TEXT, KD_GRAPHICS = 0x4B3A, 0, 1

_fonts = {}


def font(size, path=FONT):
    if (size, path) not in _fonts:
        _fonts[size, path] = ImageFont.truetype(path, size)
    return _fonts[size, path]


# ---------------------------------------------------------------- display

class Framebuffer:
    def __init__(self):
        self.dev = self._find()
        sysfs = f"/sys/class/graphics/{os.path.basename(self.dev)}"
        self.w, self.h = map(int, open(f"{sysfs}/virtual_size").read().split(","))
        self.bpp = int(open(f"{sysfs}/bits_per_pixel").read())
        self.stride = int(open(f"{sysfs}/stride").read())
        if self.bpp != 16:
            raise SystemExit(f"{self.dev}: expected 16bpp, got {self.bpp}")
        self.fd = os.open(self.dev, os.O_RDWR)
        self.mm = mmap.mmap(self.fd, self.stride * self.h)
        self.buf = np.frombuffer(self.mm, dtype=np.uint16).reshape(self.h, self.stride // 2)

    @staticmethod
    def _find():
        for sysfs in sorted(glob.glob("/sys/class/graphics/fb[0-9]*")):
            name = open(f"{sysfs}/name").read().strip().lower()
            if "vc4" not in name:
                return f"/dev/{os.path.basename(sysfs)}"
        raise SystemExit("no SPI panel framebuffer found (is the overlay loaded?)")

    def show(self, img):
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        rgb565 = ((a[..., 0] >> 3) << 11) | ((a[..., 1] >> 2) << 5) | (a[..., 2] >> 3)
        self.buf[:, : self.w] = rgb565


# ---------------------------------------------------------------- touch

class Touch:
    """Raw evdev reader for the ADS7846/XPT2046. Coordinates are raw ADC (0..4095)."""

    def __init__(self):
        self.dev = self._find()
        self.fd = os.open(self.dev, os.O_RDONLY | os.O_NONBLOCK)
        self.ranges = {ax: self._absinfo(ax) for ax in (ABS_X, ABS_Y, ABS_PRESSURE)}
        self.x = self.y = self.p = 0
        self.down = False
        self._pending = False
        self._dropped = False

    def _resync(self):
        """After SYN_DROPPED: re-read BTN_TOUCH (EVIOCGKEY) and the current ABS values (EVIOCGABS)."""
        keys = bytearray(96)
        try:
            fcntl.ioctl(self.fd, (2 << 30) | (96 << 16) | (ord("E") << 8) | 0x18, keys)
            pressed = bool(keys[BTN_TOUCH // 8] >> (BTN_TOUCH % 8) & 1)
        except OSError:
            pressed = False
        for ax in (ABS_X, ABS_Y):
            req = (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + ax)
            try:
                v = struct.unpack("6i", fcntl.ioctl(self.fd, req, bytes(24)))[0]
                if ax == ABS_X:
                    self.x = v
                else:
                    self.y = v
            except OSError:
                pass
        return pressed

    @staticmethod
    def _find():
        for name_path in glob.glob("/sys/class/input/event*/device/name"):
            name = open(name_path).read().strip()
            if "ADS7846" in name or "XPT2046" in name.upper():
                return "/dev/input/" + name_path.split("/")[4]
        raise SystemExit("no ADS7846 touch device found")

    def _absinfo(self, axis):
        req = (2 << 30) | (24 << 16) | (ord("E") << 8) | (0x40 + axis)
        val, mn, mx, *_ = struct.unpack("6i", fcntl.ioctl(self.fd, req, bytes(24)))
        return mn, mx

    def poll(self, timeout):
        """Return list of ('down'|'move'|'up', x, y, pressure) raw events."""
        out = []
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return out
        try:
            data = os.read(self.fd, EVENT_SIZE * 64)
        except BlockingIOError:
            return out
        for i in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
            _, _, typ, code, val = struct.unpack_from(EVENT_FMT, data, i)
            if typ == EV_SYN and code == SYN_DROPPED:
                self._dropped = True          # kernel buffer overran: ignore until the next SYN_REPORT
                continue
            if self._dropped:
                if typ == EV_SYN and code == SYN_REPORT:
                    self._dropped = False
                    pressed = self._resync()
                    if self.down or self._pending:
                        out.append(("cancel", self.x, self.y, self.p))   # never complete a press we lost
                    self.down, self._pending = False, pressed            # still touching: a fresh press
                continue
            if typ == EV_ABS:
                if code == ABS_X:
                    self.x = val
                elif code == ABS_Y:
                    self.y = val
                elif code == ABS_PRESSURE:
                    self.p = val
            elif typ == EV_KEY and code == BTN_TOUCH:
                if val:
                    self._pending = True
                else:
                    self.down = False
                    out.append(("up", self.x, self.y, self.p))
            elif typ == EV_SYN:
                if self._pending:
                    self.down = True
                    self._pending = False
                    out.append(("down", self.x, self.y, self.p))
                elif self.down:
                    out.append(("move", self.x, self.y, self.p))
        return out


# ---------------------------------------------------------------- calibration

class Calibration:
    """Affine map: [sx, sy] = M @ [rx, ry, 1]."""

    def __init__(self, M):
        self.M = np.asarray(M, dtype=float)

    def __call__(self, rx, ry):
        sx, sy = self.M @ (rx, ry, 1.0)
        return float(sx), float(sy)

    @classmethod
    def fit(cls, raw, screen):
        A = np.column_stack([np.asarray(raw, float), np.ones(len(raw))])
        M, *_ = np.linalg.lstsq(A, np.asarray(screen, float), rcond=None)
        return cls(M.T)

    def overlay_hints(self):
        # Which raw axis drives screen x? Sign tells us inversion.
        m = self.M[:, :2]
        swap = abs(m[0, 1]) > abs(m[0, 0])
        sx_coef = m[0, 1] if swap else m[0, 0]
        sy_coef = m[1, 0] if swap else m[1, 1]
        return {"swapxy": int(swap), "invx": int(sx_coef < 0), "invy": int(sy_coef < 0)}

    def save(self, path=CAL_PATH, **extra):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"M": self.M.tolist(), "hints": self.overlay_hints(), **extra}, f, indent=2)

    @classmethod
    def load(cls):
        for path in (CAL_PATH, CAL_DEFAULT):
            if os.path.exists(path):
                return cls(json.load(open(path))["M"])
        return None


# ---------------------------------------------------------------- screen

class Screen:
    """Framebuffer + calibrated, debounced touch. Use as a context manager."""

    def __init__(self, bg=(10, 10, 20)):
        self.fb = Framebuffer()
        self.touch = Touch()
        self.W, self.H = self.fb.w, self.fb.h
        self.bg = bg
        self.cal = Calibration.load()
        self.tty = None

    def __enter__(self):
        try:
            self.tty = os.open("/dev/tty0", os.O_RDWR)
            fcntl.ioctl(self.tty, KDSETMODE, KD_GRAPHICS)  # stop fbcon drawing over us
        except OSError:
            self.tty = None
        return self

    def __exit__(self, *exc):
        self.fb.show(Image.new("RGB", (self.W, self.H)))
        if self.tty is not None:
            fcntl.ioctl(self.tty, KDSETMODE, KD_TEXT)
            os.close(self.tty)

    def canvas(self, bg=None):
        img = Image.new("RGB", (self.W, self.H), bg or self.bg)
        return img, ImageDraw.Draw(img)

    def show(self, img):
        self.fb.show(img)

    def to_screen(self, rx, ry):
        if self.cal is None:
            raise RuntimeError("no touch calibration: run display/touchtest.py")
        return self.cal(rx, ry)

    def drain(self, quiet=0.4):
        """Discard input until the finger is up and no events arrive for `quiet` s.

        The XPT2046 bounces on release (extra down/up pairs), which would
        otherwise leak a previous tap into the next screen.
        """
        last = time.time()
        while time.time() - last < quiet or self.touch.down:
            if self.touch.poll(0.05):
                last = time.time()

    def wait_tap(self, timeout=None, raw=False, min_press=0.08, min_samples=3):
        """Block until a real tap (press >= min_press s) ends; return (x, y) or None on timeout.

        Position is the median over the press, minus the last few samples (XPT2046
        reads drift as the finger lifts). Screen pixels unless raw=True.
        """
        self.drain()
        samples, t_down = [], None
        deadline = None if timeout is None else time.time() + timeout
        while deadline is None or time.time() < deadline:
            for kind, x, y, p in self.touch.poll(0.05):
                if kind == "down":
                    samples, t_down = [(x, y)], time.time()
                elif kind == "move" and t_down:
                    samples.append((x, y))
                elif kind == "up" and t_down:
                    if time.time() - t_down >= min_press and len(samples) >= min_samples:
                        rx, ry = np.median(np.array(samples[: max(1, len(samples) - 3)]), axis=0)
                        return (float(rx), float(ry)) if raw else self.to_screen(rx, ry)
                    samples, t_down = [], None  # bounce blip, ignore
        return None
