"""bassbox UI core: retained-mode widgets, dirty-rect renderer, gestures, timers, main loop, debug socket.

One thread renders (the main loop, a select() over the engine socket, the debug socket and the touch
wake-pipe). The touch reader runs in its own thread and only queues events. Nothing animates unless a
widget asks for ticks, so an idle UI sleeps in select() with no timeout: 0% CPU, zero flushes.
"""
import heapq
import json
import math
import os
import select
import socket
import threading
import time
from collections import deque

import cairo
import numpy as np

from gfx import Canvas, rgb565_to_rgb, to_rgb565

W, H = 320, 480
FRAME_MIN = 1 / 30      # render cap
BAND_AREA = 320 * 120   # px per DIRTYFB call at most (full frame = 4 bands of ~15 ms at 48 MHz)


def _tuning():
    """Touch filter constants: /var/lib/bassbox/touch_tuning.json overrides config/touch_tuning.json."""
    t = {"bounce_s": 0.045, "drift_samples": 3, "min_samples": 3, "drag_px": 10, "long_press_s": 0.5,
         "double_tap_s": 0.35, "double_tap_px": 24, "edge_correction_px": 0}
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "..", "config", "touch_tuning.json"), "/var/lib/bassbox/touch_tuning.json"):
        try:
            t.update({k: v for k, v in json.load(open(p)).items() if not k.startswith("_")})
        except (OSError, ValueError):
            pass
    return t


TUNING = _tuning()
TAP_MOVE = TUNING["drag_px"]
LONG_PRESS = TUNING["long_press_s"]
DOUBLE_TAP = TUNING["double_tap_s"]
DOUBLE_DIST = TUNING["double_tap_px"]

app = None              # the running App (widgets reach it through this)


def _isect(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    return x1 > x0 and y1 > y0


def _union(a, b):
    x0, y0 = min(a[0], b[0]), min(a[1], b[1])
    x1, y1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
    return (x0, y0, x1 - x0, y1 - y0)


def merge_rects(rects, slack=1.15):
    """Integer-align and merge rects whose union isn't much bigger than the parts (SPI cost = area)."""
    seen = set()
    out = []
    for x, y, w, h in rects:
        x0, y0 = max(0, int(math.floor(x)) - 1), max(0, int(math.floor(y)) - 1)
        x1, y1 = min(W, int(math.ceil(x + w)) + 1), min(H, int(math.ceil(y + h)) + 1)
        r = (x0, y0, x1 - x0, y1 - y0)
        if x1 > x0 and y1 > y0 and r not in seen:
            seen.add(r)
            out.append(r)
    # drop rects fully inside another
    out = [a for i, a in enumerate(out) if not any(
        j != i and b[0] <= a[0] and b[1] <= a[1] and b[0] + b[2] >= a[0] + a[2] and b[1] + b[3] >= a[1] + a[3]
        and (b != a) for j, b in enumerate(out))]
    changed = True
    while changed and len(out) > 1:
        changed = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                u = _union(a, b)
                if u[2] * u[3] <= slack * (a[2] * a[3] + b[2] * b[3]):  # overlap alone is no reason
                    out[i] = u
                    del out[j]
                    changed = True
                    break
            if changed:
                break
    return out


# ====================================================================== widgets
class Widget:
    gestures = ()          # subset of {"tap", "double", "long", "drag", "press"}
    pad = 0                # extra hit padding (px)
    overflow = 0           # drawing may exceed the rect by this much (glows)
    cache = False          # draw once into an offscreen layer and blit it (call recache() on change)
    _cache = None
    contain = False        # children lie inside bounds: skip the subtree when bounds miss the clip

    def __init__(self, x=0, y=0, w=0, h=0):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.children = []
        self.parent = None
        self.visible = True
        self.enabled = True

    # tree
    def add(self, *ws):
        for w in ws:
            w.parent = self
            self.children.append(w)
        return ws[0] if ws else None

    def clear(self):
        for c in self.children:
            c.release()
        self.children = []

    persistent = False     # shared widgets (the header) survive their parent's clear()

    def release(self):
        """Detach and drop cached layers (big ARGB surfaces live outside Python's GC accounting)."""
        self.parent = None
        if self.persistent:
            return
        self._cache = None
        for c in self.children:
            c.release()
        self.children = []

    @property
    def rect(self):
        return (self.x, self.y, self.w, self.h)

    @property
    def bounds(self):
        o = self.overflow
        return (self.x - o, self.y - o, self.w + 2 * o, self.h + 2 * o)

    def attached(self):
        w = self
        while w.parent is not None:
            w = w.parent
        return app is not None and (w is app.screen or w in app.overlays)

    def invalidate(self, r=None):
        if app is not None and self.visible and self.attached():
            app.dirty(r or self.bounds)

    def set_visible(self, v):
        if v != self.visible:
            if not v:
                self.invalidate()
                self.visible = False
            else:
                self.visible = True
                self.invalidate()

    def contains(self, px, py):
        p = self.pad
        return self.x - p <= px < self.x + self.w + p and self.y - p <= py < self.y + self.h + p

    def hit_chain(self, px, py):
        """Deepest-first list of widgets under (px, py)."""
        if not self.visible:
            return []
        for c in reversed(self.children):
            ch = c.hit_chain(px, py)
            if ch:
                return ch + ([self] if self.contains(px, py) else [])
        return [self] if self.contains(px, py) else []

    def render(self, cv, clip):
        if not self.visible:
            return
        hit = _isect(self.bounds, clip)
        if hit:
            if self.cache:
                self._blit(cv)
            else:
                self.draw(cv)
        elif self.contain:
            return
        for c in self.children:
            c.render(cv, clip)

    def recache(self):
        self._cache = None
        self.invalidate()

    def _blit(self, cv):
        x, y, w, h = self.bounds
        bx, by = int(math.floor(x)), int(math.floor(y))
        bw, bh = int(math.ceil(x + w)) - bx, int(math.ceil(y + h)) - by
        key = (bx, by, bw, bh)
        if self._cache is None or self._cache[0] != key:
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, bw), max(1, bh))
            sub = Canvas(bw, bh, surface=surf)
            sub.cr.translate(-bx, -by)
            self.draw(sub)
            surf.flush()
            self._cache = (key, surf)
        cv.cr.set_source_surface(self._cache[1], bx, by)
        cv.cr.paint()

    def draw(self, cv):
        pass

    # gestures (g: Gesture). Return value ignored.
    def on_press(self, g): pass
    def on_release(self, g): pass
    def on_tap(self, g): pass
    def on_double_tap(self, g): pass
    def on_long_press(self, g): pass
    def on_long_release(self, g): pass
    def on_drag_start(self, g): pass
    def on_drag(self, g): pass
    def on_drag_end(self, g): pass

    # animation: return next wanted time (monotonic) or None to stop ticking
    def tick(self, now):
        return None


class Screen(Widget):
    name = "screen"
    bg = 0x000000

    def __init__(self):
        super().__init__(0, 0, W, H)

    def draw(self, cv):
        pass  # renderer paints the background

    def on_show(self):
        pass

    def on_hide(self):
        pass

    def on_model(self, what):
        """Model changed: what in {'chain','master','meters','jacks','presets','connection','catalog','tuner'}."""
        pass


class Gesture:
    def __init__(self, x, y, t, synthetic=False):
        self.x0, self.y0, self.t0 = x, y, t
        self.x, self.y, self.t = x, y, t
        self.moved = False
        self.long = False         # long-press fired before movement
        self.dragging = False
        self.synthetic = synthetic
        self.chain = []
        self.target = None

    @property
    def dx(self):
        return self.x - self.x0

    @property
    def dy(self):
        return self.y - self.y0


# ====================================================================== app
class App:
    def __init__(self, backend, log=print):
        global app
        app = self
        self.backend = backend
        self.log = log
        self.cv = Canvas(W, H)
        self.src = self.cv.rgb888()
        self.out565 = np.zeros((H, W), np.uint16)
        self.screen = None
        self.overlays = []
        self._dirty = []
        self.full_dirty = True
        self.timers = []          # heap of (t, seq, fn)
        self._seq = 0
        self.animating = {}       # widget -> next time
        self.last_frame = 0.0
        self.g = None             # active gesture
        self.last_tap = None      # (t, x, y)
        self.touch_q = deque()
        self.wake_r, self.wake_w = os.pipe()
        os.set_blocking(self.wake_r, False)
        os.set_blocking(self.wake_w, False)
        self.synthetic = []       # heap of (t, seq, kind, x, y)
        self.dragging_value = False   # widgets set this while a value drag is live (comets pause)
        self.stats = deque(maxlen=400)
        self.flushes = 0
        self.io = []              # extra fds: objects with fileno/on_readable/wants_write/on_writable
        self.debug = None
        self.quit = False
        self.frame_listeners = []  # callbacks run after each render (debug waiters)

    # ---------------------------------------------------------------- dirty + render
    def dirty(self, r):
        self._dirty.append(r)

    def invalidate_all(self):
        self.full_dirty = True

    def show(self, screen):
        if self.screen is not None:
            self.screen.on_hide()
        self.screen = screen
        self.animating = {w: t for w, t in self.animating.items() if w.attached() or w in self.overlays}
        self.full_dirty = True
        screen.on_show()
        import gc
        gc.collect()        # free dead widget cycles (and their cairo layers) during the full redraw anyway

    def add_overlay(self, w):
        if w not in self.overlays:
            self.overlays.append(w)
            w.invalidate()

    def remove_overlay(self, w):
        if w in self.overlays:
            w.invalidate()
            self.overlays.remove(w)

    def render(self):
        if not (self._dirty or self.full_dirty) or self.screen is None:
            return False
        t0 = time.perf_counter()
        raw = [tuple(int(v) for v in r) for r in self._dirty[:40]]
        rects = [(0, 0, W, H)] if self.full_dirty else merge_rects(self._dirty)
        self._dirty = []
        self.full_dirty = False
        cv, cr = self.cv, self.cv.cr
        for r in rects:
            cr.save()
            cr.rectangle(*r)
            cr.clip()
            cv.fill(self.screen.bg)
            self.screen.render(cv, r)
            for o in self.overlays:
                o.render(cv, r)
            cr.restore()
        self.cv.surf.flush()
        t1 = time.perf_counter()
        for x, y, w, h in rects:
            self.out565[y:y + h, x:x + w] = to_rgb565(self.src[y:y + h, x:x + w], x, y)
        t2 = time.perf_counter()
        # Big rects go out as horizontal bands, one DIRTYFB each, so the touch controller's SPI messages
        # (same bus) interleave instead of waiting out a ~60 ms full-frame transfer.
        out = []
        for x, y, w, h in rects:
            if w * h > BAND_AREA:
                rows = max(1, BAND_AREA // w)
                out += [(x, yy, w, min(rows, y + h - yy)) for yy in range(y, y + h, rows)]
            else:
                out.append((x, y, w, h))
        self.backend.flush(self.out565, out, separate=True)
        t3 = time.perf_counter()
        area = sum(r[2] * r[3] for r in rects)
        self.flushes += 1
        lat = None
        if self.input_t is not None:
            lat = (t3 - self.input_t) * 1e3
            self.input_t = None
        self.stats.append({"input_to_glass_ms": lat, "input": self.input_kind,"t": time.monotonic(), "render_ms": (t1 - t0) * 1e3, "convert_ms": (t2 - t1) * 1e3,
                           "flush_ms": (t3 - t2) * 1e3, "total_ms": (t3 - t0) * 1e3, "rects": len(rects), "bands": len(out), "area": area, "rl": rects, "raw": raw,
                           "screen": self.screen.name})
        self.last_frame = time.monotonic()
        for cb in list(self.frame_listeners):
            cb()
        return True

    def screenshot(self, path):
        from PIL import Image
        Image.fromarray(rgb565_to_rgb(self.out565), "RGB").save(path)
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass

    # ---------------------------------------------------------------- timers + animation
    def after(self, delay, fn):
        self._seq += 1
        item = [time.monotonic() + delay, self._seq, fn]
        heapq.heappush(self.timers, item)
        return item

    def cancel(self, item):
        if item:
            item[2] = None

    def animate(self, widget, when=None):
        """Ask for widget.tick(now) at `when` (default: next frame)."""
        t = when if when is not None else time.monotonic()
        cur = self.animating.get(widget)
        if cur is None or t < cur:
            self.animating[widget] = t

    def _run_timers(self, now):
        while self.timers and self.timers[0][0] <= now:
            _, _, fn = heapq.heappop(self.timers)
            if fn:
                try:
                    fn()
                except Exception as e:  # never let one callback kill the UI
                    self.log("timer error:", repr(e))
                    import traceback
                    traceback.print_exc()

    def _run_anims(self, now):
        due = [w for w, t in self.animating.items() if t <= now]
        for w in due:
            del self.animating[w]
            try:
                nxt = w.tick(now)
            except Exception as e:
                self.log("tick error:", repr(e))
                import traceback
                traceback.print_exc()
                nxt = None
            if nxt is not None and (w.attached() or w in self.overlays):
                self.animate(w, max(nxt, now + 0.001))

    # ---------------------------------------------------------------- input
    def post_touch(self, kind, x, y, t=None, raw=None):
        """Thread-safe: queue a touch event ('down'|'move'|'up'|'cancel') in screen coords."""
        self.touch_q.append((kind, x, y, t or time.monotonic(), raw))
        try:
            os.write(self.wake_w, b"t")
        except BlockingIOError:
            pass

    def inject(self, kind, x, y, delay):
        self._seq += 1
        heapq.heappush(self.synthetic, (time.monotonic() + delay, self._seq, kind, x, y))

    def _hit(self, x, y):
        for o in reversed(self.overlays):
            ch = o.hit_chain(x, y)
            if ch or getattr(o, "modal", False):
                return ch
        return self.screen.hit_chain(x, y) if self.screen else []

    @staticmethod
    def _first(chain, kind):
        for w in chain:
            if kind in w.gestures and w.enabled:
                return w
        return None

    input_t = None
    input_kind = None

    def _touch(self, kind, x, y, t, raw=None, synthetic=False):
        self.input_t = time.perf_counter()
        self.input_kind = kind
        if self.raw_listener is not None:      # calibration screen takes raw touches
            self.raw_listener(kind, raw, (x, y))
            return
        g = self.g
        if kind == "down":
            if g is not None:
                self._touch("up", g.x, g.y, t)
            g = self.g = Gesture(x, y, t, synthetic)
            g.chain = self._hit(x, y)
            w = self._first(g.chain, "press")
            if w:
                w.on_press(g)
            g.long_timer = self.after(LONG_PRESS, self._long_fire)
            return
        if g is None:
            return
        g.x, g.y, g.t = x, y, t
        if kind == "move":
            if not g.dragging and math.hypot(g.dx, g.dy) >= TAP_MOVE:
                g.dragging = True
                self.cancel(g.long_timer)
                g.target = self._first(g.chain, "drag")
                if g.target:
                    g.target.on_drag_start(g)
            elif g.dragging and g.target:
                g.target.on_drag(g)
        elif kind in ("up", "cancel"):
            self.cancel(g.long_timer)
            self.g = None
            pw = self._first(g.chain, "press")
            if pw:
                pw.on_release(g)
            if kind == "cancel":
                if g.dragging and g.target:
                    g.target.on_drag_end(g)
                return
            if g.dragging:
                if g.target:
                    g.target.on_drag(g)
                    g.target.on_drag_end(g)
            elif g.long:
                if g.target:
                    g.target.on_long_release(g)
            else:
                lt = self.last_tap
                dw = self._first(g.chain, "double")
                if dw and lt and t - lt[0] < DOUBLE_TAP and math.hypot(x - lt[1], y - lt[2]) < DOUBLE_DIST and lt[3] is dw:
                    self.last_tap = None
                    dw.on_double_tap(g)
                else:
                    tw = self._first(g.chain, "tap")
                    self.last_tap = (t, x, y, dw)
                    if tw:
                        tw.on_tap(g)

    raw_listener = None   # calibration screen hooks raw touch here

    def _long_fire(self):
        g = self.g
        if g is None or g.dragging:
            return
        w = self._first(g.chain, "long")
        if w:
            g.long = True
            g.target = w
            w.on_long_press(g)

    def _drain_touch(self):
        try:
            os.read(self.wake_r, 4096)
        except BlockingIOError:
            pass
        while self.touch_q:
            kind, x, y, t, raw = self.touch_q.popleft()
            self._touch(kind, x, y, t, raw)

    def _run_synthetic(self, now):
        while self.synthetic and self.synthetic[0][0] <= now:
            _, _, kind, x, y = heapq.heappop(self.synthetic)
            self._touch(kind, x, y, now, synthetic=True)

    # ---------------------------------------------------------------- main loop
    def run(self):
        while not self.quit:
            now = time.monotonic()
            self._run_synthetic(now)
            self._run_timers(now)
            self._run_anims(now)
            for o in self.io:
                if hasattr(o, "tick"):
                    o.tick(now)
            if self._dirty or self.full_dirty:
                wait = self.last_frame + FRAME_MIN - time.monotonic()
                if wait <= 0 or self.full_dirty:
                    self.render()
            timeout = self._timeout()
            rl, wl = [self.wake_r], []
            for o in self.io:
                fd = o.fileno()
                if fd is not None:
                    rl.append(o)
                    if o.wants_write():
                        wl.append(o)
            try:
                r, w, _ = select.select(rl, wl, [], timeout)
            except InterruptedError:
                continue
            for o in r:
                if o == self.wake_r:
                    self._drain_touch()
                else:
                    o.on_readable()
            for o in w:
                if o.fileno() is not None:
                    o.on_writable()

    def _timeout(self):
        now = time.monotonic()
        ts = []
        if self._dirty or self.full_dirty:
            ts.append(self.last_frame + FRAME_MIN)
        if self.timers:
            while self.timers and self.timers[0][2] is None:
                heapq.heappop(self.timers)
            if self.timers:
                ts.append(self.timers[0][0])
        if self.animating:
            ts.append(min(self.animating.values()))
        if self.synthetic:
            ts.append(self.synthetic[0][0])
        for o in self.io:
            d = o.next_deadline() if hasattr(o, "next_deadline") else None
            if d is not None:
                ts.append(d)
        if not ts:
            return None
        return max(0.0, min(ts) - now)


# ====================================================================== debug socket
class DebugServer:
    """Unix socket, JSON lines. Drives the same gesture pipeline as the real panel.

    {"cmd":"tap","x":160,"y":120}            {"cmd":"doubletap","x":..,"y":..}
    {"cmd":"drag","x1":..,"y1":..,"x2":..,"y2":..,"ms":300}
    {"cmd":"longpress","x":..,"y":..,"ms":700}   (optionally "x2","y2": drag after the long press)
    {"cmd":"jack","which":"in"|"out","plugged":0|1}
    {"cmd":"screenshot","path":"/tmp/x.png"}   {"cmd":"state"}   {"cmd":"stats"}   {"cmd":"wait_idle"}
    {"cmd":"goto","screen":"tuner"}
    Each reply is sent once the action has played out and the resulting frame is on the panel.
    """

    def __init__(self, path, handlers):
        self.path = path
        self.handlers = handlers
        self.ls = None
        self.listener = _Listener(self)
        self._bind()
        app.io.append(self.listener)
        app.after(3.0, self._watch)

    def _bind(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if self.ls is not None:
            self.ls.close()
        self.ls = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.ls.bind(self.path)
        # 0660, group = owner group of the repo (the install user on the Pi), so inject.py works as that user without sudo
        try:
            gid = int(os.environ.get("BASSBOX_DEBUG_GID", os.stat(os.path.join(os.path.dirname(__file__), "..")).st_gid))
            os.chown(self.path, -1, gid)
        except (OSError, ValueError):
            pass
        os.chmod(self.path, 0o660)
        self.ls.listen(4)
        self.ls.setblocking(False)
        self.ino = os.stat(self.path).st_ino

    def _watch(self):
        """/run/bassbox may be a systemd RuntimeDirectory of the engine and vanish when it restarts."""
        try:
            ok = os.stat(self.path).st_ino == self.ino
        except OSError:
            ok = False
        if not ok:
            try:
                self._bind()
                app.log("debug socket re-created", self.path)
            except OSError as e:
                app.log("debug socket:", e)
        app.after(3.0, self._watch)

    def reply(self, c, obj):
        """Buffered, non-blocking: a stalled client can never freeze rendering or touch."""
        if c.sock is None:
            return
        c.wbuf += (json.dumps(obj) + "\n").encode()
        if len(c.wbuf) > 8 << 20:
            c.close()
            return
        c.on_writable()

    def handle(self, c, msg):
        cmd = msg.get("cmd")
        fn = self.handlers.get(cmd)
        if fn is None:
            return self.reply(c, {"ok": False, "error": f"unknown cmd {cmd}"})
        try:
            fn(msg, lambda obj=None: self.reply(c, dict({"ok": True}, **(obj or {}))))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.reply(c, {"ok": False, "error": repr(e)})


class _Listener:
    def __init__(self, srv):
        self.srv = srv

    def fileno(self):
        return self.srv.ls.fileno()

    def wants_write(self):
        return False

    def on_readable(self):
        try:
            s, _ = self.srv.ls.accept()
        except BlockingIOError:
            return
        s.setblocking(False)
        app.io.append(_Client(self.srv, s))


class _Client:
    def __init__(self, srv, s):
        self.srv, self.sock, self.buf, self.wbuf = srv, s, b"", b""

    def fileno(self):
        return self.sock.fileno() if self.sock else None

    def wants_write(self):
        return bool(self.wbuf)

    def on_writable(self):
        if self.sock is None or not self.wbuf:
            return
        try:
            n = self.sock.send(self.wbuf)
            self.wbuf = self.wbuf[n:]
        except (BlockingIOError, InterruptedError):
            pass
        except OSError:
            self.close()

    def close(self):
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        if self in app.io:
            app.io.remove(self)

    def on_readable(self):
        try:
            d = self.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            d = b""
        if not d:
            self.close()
            return
        self.buf += d
        while b"\n" in self.buf:
            line, self.buf = self.buf.split(b"\n", 1)
            if line.strip():
                try:
                    msg = json.loads(line)
                except ValueError:
                    self.srv.reply(self, {"ok": False, "error": "bad json"})
                    continue
                self.srv.handle(self, msg)


def when_idle(cb, settle=0.05, timeout=5.0):
    """Call cb() once nothing is dirty, no gesture/synthetic input is pending and animations are idle
    (ignoring endless ambient ones that set widget.ambient = True)."""
    t_end = time.monotonic() + timeout

    def check():
        busy = (app._dirty or app.full_dirty or app.synthetic or app.g is not None or
                any(not getattr(w, "ambient", False) for w in app.animating))
        if busy and time.monotonic() < t_end:
            app.after(0.02, check)
        else:
            cb()
    app.after(settle, check)


class TouchThread(threading.Thread):
    """Reads the XPT2046 (via display/screen.py's Touch), maps raw->screen with the affine
    calibration, filters the release bounce and the end-of-press drift, and posts clean events.

    - 'down' is posted once 3 samples agree (median), so contact noise never picks a target.
    - moves are posted 3 samples late, and the last 3 samples before lift-off are dropped (drift).
    - an 'up' followed by a 'down' within 45 ms is a bounce: the press simply continues.
    - presses with < 3 samples never surface (release blips).
    """
    DRIFT = int(TUNING["drift_samples"])
    BOUNCE = float(TUNING["bounce_s"])
    MINS = max(1, int(TUNING["min_samples"]))
    EDGE = float(TUNING["edge_correction_px"])

    def __init__(self, touch, cal, post):
        super().__init__(daemon=True)
        self.touch, self.cal, self.post = touch, cal, post

    def map(self, rx, ry):
        x, y = self.cal(rx, ry)
        if self.EDGE and x < 60:
            x += self.EDGE * (1 - x / 60)
        return x, y

    def run(self):
        samples = []
        down = emitted = False
        up_at = None
        while True:
            timeout = 0.5 if up_at is None else max(0.0, up_at - time.monotonic())
            try:
                evs = self.touch.poll(timeout)
            except OSError as e:
                print("touch error:", e)
                time.sleep(1)
                continue
            now = time.monotonic()
            for kind, rx, ry, _p in evs:
                if kind == "cancel":                 # SYN_DROPPED: the press is unreliable; never tap it
                    if emitted:
                        self.post("cancel", 0, 0, now, None)
                    down, emitted, samples, up_at = False, False, [], None
                    continue
                if kind == "down":
                    if up_at is not None:            # bounce: continue the same press
                        up_at = None
                        down = True
                        continue
                    down, emitted, samples = True, False, [(rx, ry)]
                elif kind == "move" and down:
                    samples.append((rx, ry))
                    if not emitted and len(samples) >= self.MINS:
                        mx, my = np.median(np.array(samples[:self.MINS]), axis=0)
                        self.post("down", *self.map(mx, my), now, (float(mx), float(my)))
                        emitted = True
                    elif emitted and len(samples) > self.DRIFT + 3:
                        rx2, ry2 = samples[-1 - self.DRIFT]
                        self.post("move", *self.map(rx2, ry2), now, (rx2, ry2))
                elif kind == "up" and down:
                    down = False
                    up_at = now + self.BOUNCE
            if up_at is not None and time.monotonic() >= up_at:
                up_at = None
                if emitted:
                    keep = samples[:-self.DRIFT] if len(samples) > self.DRIFT + 2 else samples[:3]
                    arr = np.array(keep)
                    a0, a1 = self.cal(*arr[0]), self.cal(*arr[-1])
                    still = math.hypot(a1[0] - a0[0], a1[1] - a0[1]) < 10
                    mx, my = np.median(arr, axis=0) if still else arr[-1]
                    sx, sy = self.map(mx, my)
                    self.post("up", sx, sy, time.monotonic(), (float(mx), float(my)))
                samples, emitted = [], False
