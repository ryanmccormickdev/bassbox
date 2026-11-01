"""DRM/KMS display backend for the ST7796S SPI panel (panel-mipi-dbi-spi), pure ctypes + ioctl.

Partial updates go through DRM_IOCTL_MODE_DIRTYFB with clip rects, which the mipi-dbi driver
turns into a windowed SPI transfer of just that region. The ioctl is synchronous: it returns
once the SPI transfer is done, so its duration is the real time-to-glass.

Notes (measured on the Pi, see ui/tools/bench_display.py):
  * The kernel merges all clips of one DIRTYFB call into their bounding box, so disjoint rects
    are sent with one call each (see `flush(..., separate=)`).
  * The dumb buffer is write-combined DMA memory: only ever write it (never read it back).
  * XRGB8888 is cairo FORMAT_RGB24's exact memory layout (little-endian 0x00RRGGBB), so a
    cairo surface can be copied in with no conversion; the kernel converts to RGB565.

    from backend_drm import DrmDisplay
    with DrmDisplay() as d:                    # fmt='xrgb8888' (default) or 'rgb565'
        frame = np.zeros((d.height, d.width), d.dtype)
        d.flush(frame)                         # full frame
        d.flush(frame, [(0, 0, 64, 64)])       # just a rect: (x, y, w, h)
"""
import ctypes as C
import fcntl
import glob
import mmap
import os

import numpy as np

# ---------------------------------------------------------------- ioctl numbers

_IOC_WRITE, _IOC_READ = 1, 2


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("d") << 8) | nr


def _IO(nr):
    return _ioc(0, nr, 0)


def _IOWR(nr, struct):
    return _ioc(_IOC_READ | _IOC_WRITE, nr, C.sizeof(struct))


def _fourcc(s):
    return ord(s[0]) | ord(s[1]) << 8 | ord(s[2]) << 16 | ord(s[3]) << 24


DRM_FORMAT_XRGB8888 = _fourcc("XR24")
DRM_FORMAT_RGB565 = _fourcc("RG16")
DRM_MODE_CONNECTED = 1
KDSETMODE, KD_TEXT, KD_GRAPHICS = 0x4B3A, 0, 1


class drm_mode_card_res(C.Structure):
    _fields_ = [("fb_id_ptr", C.c_uint64), ("crtc_id_ptr", C.c_uint64),
                ("connector_id_ptr", C.c_uint64), ("encoder_id_ptr", C.c_uint64),
                ("count_fbs", C.c_uint32), ("count_crtcs", C.c_uint32),
                ("count_connectors", C.c_uint32), ("count_encoders", C.c_uint32),
                ("min_width", C.c_uint32), ("max_width", C.c_uint32),
                ("min_height", C.c_uint32), ("max_height", C.c_uint32)]


class drm_mode_modeinfo(C.Structure):
    _fields_ = [("clock", C.c_uint32),
                ("hdisplay", C.c_uint16), ("hsync_start", C.c_uint16), ("hsync_end", C.c_uint16),
                ("htotal", C.c_uint16), ("hskew", C.c_uint16),
                ("vdisplay", C.c_uint16), ("vsync_start", C.c_uint16), ("vsync_end", C.c_uint16),
                ("vtotal", C.c_uint16), ("vscan", C.c_uint16),
                ("vrefresh", C.c_uint32), ("flags", C.c_uint32), ("type", C.c_uint32),
                ("name", C.c_char * 32)]


class drm_mode_crtc(C.Structure):
    _fields_ = [("set_connectors_ptr", C.c_uint64), ("count_connectors", C.c_uint32),
                ("crtc_id", C.c_uint32), ("fb_id", C.c_uint32), ("x", C.c_uint32), ("y", C.c_uint32),
                ("gamma_size", C.c_uint32), ("mode_valid", C.c_uint32), ("mode", drm_mode_modeinfo)]


class drm_mode_get_encoder(C.Structure):
    _fields_ = [("encoder_id", C.c_uint32), ("encoder_type", C.c_uint32), ("crtc_id", C.c_uint32),
                ("possible_crtcs", C.c_uint32), ("possible_clones", C.c_uint32)]


class drm_mode_get_connector(C.Structure):
    _fields_ = [("encoders_ptr", C.c_uint64), ("modes_ptr", C.c_uint64),
                ("props_ptr", C.c_uint64), ("prop_values_ptr", C.c_uint64),
                ("count_modes", C.c_uint32), ("count_props", C.c_uint32), ("count_encoders", C.c_uint32),
                ("encoder_id", C.c_uint32), ("connector_id", C.c_uint32),
                ("connector_type", C.c_uint32), ("connector_type_id", C.c_uint32),
                ("connection", C.c_uint32), ("mm_width", C.c_uint32), ("mm_height", C.c_uint32),
                ("subpixel", C.c_uint32), ("pad", C.c_uint32)]


class drm_mode_create_dumb(C.Structure):
    _fields_ = [("height", C.c_uint32), ("width", C.c_uint32), ("bpp", C.c_uint32),
                ("flags", C.c_uint32), ("handle", C.c_uint32), ("pitch", C.c_uint32),
                ("size", C.c_uint64)]


class drm_mode_map_dumb(C.Structure):
    _fields_ = [("handle", C.c_uint32), ("pad", C.c_uint32), ("offset", C.c_uint64)]


class drm_mode_destroy_dumb(C.Structure):
    _fields_ = [("handle", C.c_uint32)]


class drm_mode_fb_cmd2(C.Structure):
    _fields_ = [("fb_id", C.c_uint32), ("width", C.c_uint32), ("height", C.c_uint32),
                ("pixel_format", C.c_uint32), ("flags", C.c_uint32),
                ("handles", C.c_uint32 * 4), ("pitches", C.c_uint32 * 4),
                ("offsets", C.c_uint32 * 4), ("modifier", C.c_uint64 * 4)]


class drm_clip_rect(C.Structure):
    _fields_ = [("x1", C.c_uint16), ("y1", C.c_uint16), ("x2", C.c_uint16), ("y2", C.c_uint16)]


class drm_mode_fb_dirty_cmd(C.Structure):
    _fields_ = [("fb_id", C.c_uint32), ("flags", C.c_uint32), ("color", C.c_uint32),
                ("num_clips", C.c_uint32), ("clips_ptr", C.c_uint64)]


DRM_IOCTL_SET_MASTER = _IO(0x1E)
DRM_IOCTL_DROP_MASTER = _IO(0x1F)
DRM_IOCTL_MODE_GETRESOURCES = _IOWR(0xA0, drm_mode_card_res)
DRM_IOCTL_MODE_GETCRTC = _IOWR(0xA1, drm_mode_crtc)
DRM_IOCTL_MODE_SETCRTC = _IOWR(0xA2, drm_mode_crtc)
DRM_IOCTL_MODE_GETENCODER = _IOWR(0xA6, drm_mode_get_encoder)
DRM_IOCTL_MODE_GETCONNECTOR = _IOWR(0xA7, drm_mode_get_connector)
DRM_IOCTL_MODE_RMFB = _ioc(_IOC_READ | _IOC_WRITE, 0xAF, 4)
DRM_IOCTL_MODE_DIRTYFB = _IOWR(0xB1, drm_mode_fb_dirty_cmd)
DRM_IOCTL_MODE_CREATE_DUMB = _IOWR(0xB2, drm_mode_create_dumb)
DRM_IOCTL_MODE_MAP_DUMB = _IOWR(0xB3, drm_mode_map_dumb)
DRM_IOCTL_MODE_DESTROY_DUMB = _IOWR(0xB4, drm_mode_destroy_dumb)
DRM_IOCTL_MODE_ADDFB2 = _IOWR(0xB8, drm_mode_fb_cmd2)


def _ioctl(fd, req, arg):
    fcntl.ioctl(fd, req, arg, True)
    return arg


def find_panel_card(driver="panel-mipi-dbi"):
    """The /dev/dri/cardN whose device driver name contains `driver`."""
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]")):
        drv = os.path.basename(os.path.realpath(f"{card}/device/driver"))
        if driver in drv:
            return "/dev/dri/" + os.path.basename(card)
    raise RuntimeError(f"no DRM card with a {driver} driver (is the mipi-dbi-spi overlay loaded?)")


# ---------------------------------------------------------------- helpers

def xrgb_to_rgb565(a):
    """uint32 0x00RRGGBB (cairo RGB24) -> uint16 RGB565 (truncating)."""
    a = a.astype(np.uint32, copy=False)
    return (((a >> 8) & 0xF800) | ((a >> 5) & 0x07E0) | ((a >> 3) & 0x001F)).astype(np.uint16)


def _rects_to_clips(rects, w, h):
    out = []
    for x, y, rw, rh in rects:
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(w, int(x + rw)), min(h, int(y + rh))
        if x2 > x1 and y2 > y1:
            out.append((x1, y1, x2, y2))
    return out


def _should_separate(clips):
    """Disjoint rects: one DIRTYFB per rect when the merged bounding box wastes > 30%."""
    if len(clips) < 2:
        return False
    area = sum((x2 - x1) * (y2 - y1) for x1, y1, x2, y2 in clips)
    bx1 = min(c[0] for c in clips); by1 = min(c[1] for c in clips)
    bx2 = max(c[2] for c in clips); by2 = max(c[3] for c in clips)
    return (bx2 - bx1) * (by2 - by1) > 1.3 * area


# ---------------------------------------------------------------- display

class DrmDisplay:
    """Owns the panel's DRM card as master, scans out one dumb buffer, flushes damage rects."""

    def __init__(self, fmt="xrgb8888", card=None, take_vt=True):
        if fmt not in ("xrgb8888", "rgb565"):
            raise ValueError(fmt)
        self.fmt = fmt
        self.bpp = 32 if fmt == "xrgb8888" else 16
        self.dtype = np.uint32 if fmt == "xrgb8888" else np.uint16
        self.card = card or find_panel_card()
        self.fd = os.open(self.card, os.O_RDWR | os.O_CLOEXEC)
        self.tty = None
        self.fb_id = self.handle = 0
        self.mm = None
        try:
            try:
                fcntl.ioctl(self.fd, DRM_IOCTL_SET_MASTER)
            except OSError:
                pass  # first opener is master already; a real conflict fails at SETCRTC
            self._setup()
        except Exception:
            self.close()
            raise
        if take_vt:
            try:
                self.tty = os.open("/dev/tty0", os.O_RDWR)
                fcntl.ioctl(self.tty, KDSETMODE, KD_GRAPHICS)  # stop fbcon cursor/text
            except OSError:
                self.tty = None

    # -- setup
    def _setup(self):
        res = _ioctl(self.fd, DRM_IOCTL_MODE_GETRESOURCES, drm_mode_card_res())
        crtcs = (C.c_uint32 * res.count_crtcs)()
        conns = (C.c_uint32 * res.count_connectors)()
        encs = (C.c_uint32 * max(1, res.count_encoders))()
        fbs = (C.c_uint32 * max(1, res.count_fbs))()
        res2 = drm_mode_card_res(fb_id_ptr=C.addressof(fbs), crtc_id_ptr=C.addressof(crtcs),
                                 connector_id_ptr=C.addressof(conns), encoder_id_ptr=C.addressof(encs),
                                 count_fbs=res.count_fbs, count_crtcs=res.count_crtcs,
                                 count_connectors=res.count_connectors, count_encoders=res.count_encoders)
        _ioctl(self.fd, DRM_IOCTL_MODE_GETRESOURCES, res2)

        self.conn_id = self.mode = None
        enc_id = 0
        for cid in conns:
            c = _ioctl(self.fd, DRM_IOCTL_MODE_GETCONNECTOR, drm_mode_get_connector(connector_id=cid))
            if c.connection != DRM_MODE_CONNECTED or c.count_modes == 0:
                continue
            modes = (drm_mode_modeinfo * c.count_modes)()
            cencs = (C.c_uint32 * max(1, c.count_encoders))()
            c2 = drm_mode_get_connector(connector_id=cid, modes_ptr=C.addressof(modes),
                                        count_modes=c.count_modes, encoders_ptr=C.addressof(cencs),
                                        count_encoders=c.count_encoders)
            _ioctl(self.fd, DRM_IOCTL_MODE_GETCONNECTOR, c2)
            self.conn_id, self.mode = cid, modes[0]
            enc_id = c2.encoder_id or (cencs[0] if c.count_encoders else 0)
            break
        if self.conn_id is None:
            raise RuntimeError(f"{self.card}: no connected connector")

        self.crtc_id = crtcs[0]
        if enc_id:
            e = _ioctl(self.fd, DRM_IOCTL_MODE_GETENCODER, drm_mode_get_encoder(encoder_id=enc_id))
            if e.crtc_id:
                self.crtc_id = e.crtc_id
            else:
                for i, cr in enumerate(crtcs):
                    if e.possible_crtcs & (1 << i):
                        self.crtc_id = cr
                        break
        self.width, self.height = self.mode.hdisplay, self.mode.vdisplay

        d = _ioctl(self.fd, DRM_IOCTL_MODE_CREATE_DUMB,
                   drm_mode_create_dumb(width=self.width, height=self.height, bpp=self.bpp))
        self.handle, self.pitch, self.size = d.handle, d.pitch, d.size
        fb = drm_mode_fb_cmd2(width=self.width, height=self.height,
                              pixel_format=DRM_FORMAT_XRGB8888 if self.fmt == "xrgb8888" else DRM_FORMAT_RGB565)
        fb.handles[0], fb.pitches[0] = self.handle, self.pitch
        _ioctl(self.fd, DRM_IOCTL_MODE_ADDFB2, fb)
        self.fb_id = fb.fb_id

        m = _ioctl(self.fd, DRM_IOCTL_MODE_MAP_DUMB, drm_mode_map_dumb(handle=self.handle))
        self.mm = mmap.mmap(self.fd, self.size, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                            offset=m.offset)
        bytes_pp = self.bpp // 8
        self.buf = np.frombuffer(self.mm, dtype=self.dtype).reshape(
            self.height, self.pitch // bytes_pp)[:, : self.width]
        self.buf[:] = 0  # write-only

        conn_arr = (C.c_uint32 * 1)(self.conn_id)
        crtc = drm_mode_crtc(set_connectors_ptr=C.addressof(conn_arr), count_connectors=1,
                             crtc_id=self.crtc_id, fb_id=self.fb_id, mode_valid=1, mode=self.mode)
        _ioctl(self.fd, DRM_IOCTL_MODE_SETCRTC, crtc)
        self._clips = (drm_clip_rect * 16)()

    # -- drawing
    def flush(self, src, rects=None, separate=None):
        """Copy `rects` [(x, y, w, h)] of `src` (H x W numpy) to the panel. rects=None: full frame.

        separate: None = auto (one DIRTYFB per rect if merging would waste >30% area),
        True = one ioctl per rect, False = a single ioctl (kernel merges to a bounding box).
        """
        W, H = self.width, self.height
        clips = [(0, 0, W, H)] if rects is None else _rects_to_clips(rects, W, H)
        if not clips:
            return
        convert = self.fmt == "rgb565" and src.dtype == np.uint32
        if self.fmt == "xrgb8888" and src.dtype != np.uint32:
            raise TypeError("xrgb8888 display needs a uint32 source")
        for x1, y1, x2, y2 in clips:
            region = src[y1:y2, x1:x2]
            self.buf[y1:y2, x1:x2] = xrgb_to_rgb565(region) if convert else region
        if separate is None:
            separate = _should_separate(clips)
        groups = [[c] for c in clips] if separate else [clips]
        for g in groups:
            self._dirty(g)

    def _dirty(self, clips):
        n = len(clips)
        arr = self._clips if n <= len(self._clips) else (drm_clip_rect * n)()
        for i, (x1, y1, x2, y2) in enumerate(clips):
            arr[i].x1, arr[i].y1, arr[i].x2, arr[i].y2 = x1, y1, x2, y2
        _ioctl(self.fd, DRM_IOCTL_MODE_DIRTYFB,
               drm_mode_fb_dirty_cmd(fb_id=self.fb_id, num_clips=n, clips_ptr=C.addressof(arr)))

    # -- teardown
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
        if self.mm is not None:
            try:
                self.mm.close()
            except BufferError:
                pass  # a caller still holds a view; the fd close below frees it anyway
            self.mm = None
        try:
            if self.fb_id:
                _ioctl(self.fd, DRM_IOCTL_MODE_RMFB, C.c_uint32(self.fb_id))
            if self.handle:
                _ioctl(self.fd, DRM_IOCTL_MODE_DESTROY_DUMB, drm_mode_destroy_dumb(handle=self.handle))
            fcntl.ioctl(self.fd, DRM_IOCTL_DROP_MASTER)
        except OSError:
            pass
        os.close(self.fd)  # last close: the kernel restores the fbdev console mode
        self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
