#!/usr/bin/env python3
"""bassbox touchscreen UI.

    sudo python3 /opt/bassbox/ui/main.py                       # the pedal (DRM panel + touch + real engine)
    python3 ui/main.py --backend headless --engine /tmp/bassbox-mock.sock --debug /tmp/bassbox-ui-debug.sock

Options: --engine SOCK (default /run/bassbox/engine.sock), --debug SOCK (default /run/bassbox/ui-debug.sock),
--backend drm|fb|headless (default: drm, falling back to fb, then headless), --no-touch, --no-splash.
Drive it remotely with ui/tools/inject.py (same gesture pipeline as the panel).
"""
import argparse
import os
import signal
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(1, os.path.join(HERE, "..", "display"))

import numpy as np  # noqa: E402

import core  # noqa: E402
from chrome import Banner, Header, snackbar  # noqa: E402
from model import Model  # noqa: E402
from screens import base  # noqa: E402
from screens.board import Board  # noqa: E402
from screens.edit import Edit  # noqa: E402
from screens.library import Library  # noqa: E402
from screens.presets import Presets, SaveAs  # noqa: E402
from screens.settings import Calibrate, Settings  # noqa: E402
from screens.splash import Splash  # noqa: E402
from screens.audiocheck import AudioCheck  # noqa: E402
from screens.tuner import Tuner  # noqa: E402


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def set_priority():
    """CPU0-2 only (CPU3 is the audio core), nice 5: the engine always wins."""
    try:
        os.sched_setaffinity(0, {c for c in (0, 1, 2) if c < os.cpu_count()})
    except (AttributeError, OSError):
        pass
    try:
        cur = os.getpriority(os.PRIO_PROCESS, 0)
        if cur < 5:
            os.setpriority(os.PRIO_PROCESS, 0, 5)
    except (AttributeError, OSError):
        pass


class Headless:
    """Fake display: frames live in App.out565 (screenshots); flush is free."""
    width, height = 320, 480

    def flush(self, src, rects=None, separate=None):
        pass

    def close(self):
        pass


def open_backend(kind):
    if kind in ("drm", "auto"):
        try:
            from backend_drm import DrmDisplay
            return DrmDisplay(fmt="rgb565"), "drm"
        except Exception as e:
            log("drm backend unavailable:", e)
            if kind == "drm":
                pass
    if kind in ("drm", "fb", "auto"):
        try:
            from backend_fb import FbDisplay
            return FbDisplay(), "fb"
        except Exception as e:
            log("fb backend unavailable:", e)
    return Headless(), "headless"


class Navigator:
    def __init__(self, model):
        self.m = model
        self.s = {}
        self.banner = Banner("ENGINE OFFLINE · RECONNECTING…")
        self.started = time.monotonic()

    def _get(self, cls, key=None):
        key = key or cls.__name__
        if key not in self.s:
            self.s[key] = cls()
        return self.s[key]

    def _show(self, scr):
        core.app.show(scr)
        self.update_banner()

    def splash(self):
        self.started = time.monotonic()
        self._show(self._get(Splash))
        self._wait_engine()

    def _wait_engine(self):
        el = time.monotonic() - self.started
        if (self.m.state_loaded and el >= 0.6) or el >= 3.0:
            self.board()
        else:
            if el > 1.2:
                self.s["Splash"].set_status("waiting for the engine…")
            core.app.after(0.05, self._wait_engine)

    def board(self):
        self._show(self._get(Board))

    def edit(self, uid):
        e = self._get(Edit)
        e.open(uid)
        self._show(e)

    def library(self, index=None):
        lib = self._get(Library)
        lib.open(len(self.m.chain) if index is None else index)
        self._show(lib)

    def presets(self):
        self._show(self._get(Presets))

    def save_as(self):
        self._show(self._get(SaveAs))

    def tuner(self):
        self._show(self._get(Tuner))

    def settings(self):
        self._show(self._get(Settings))

    def audio_check(self):
        self._show(self._get(AudioCheck))

    def calibrate(self):
        if "Calibrate" not in self.s:
            self.s["Calibrate"] = Calibrate(on_done=self.settings)
        self._show(self.s["Calibrate"])

    def update_banner(self):
        scr = core.app.screen
        want = (not self.m.connected) and scr is not None and scr.name not in ("splash", "calibrate")
        if want and self.banner not in core.app.overlays:
            core.app.add_overlay(self.banner)
        elif not want and self.banner in core.app.overlays:
            core.app.remove_overlay(self.banner)


# ====================================================================== debug / injection
def debug_handlers(app, model, nav, ctx):
    def after_input(done, extra=0.0):
        app.after(extra, lambda: core.when_idle(lambda: done()))

    def tap(m, done):
        x, y = float(m["x"]), float(m["y"])
        app.inject("down", x, y, 0)
        app.inject("move", x, y, 0.03)
        app.inject("up", x, y, float(m.get("ms", 90)) / 1000)
        after_input(done, float(m.get("ms", 90)) / 1000 + 0.02)

    def doubletap(m, done):
        x, y = float(m["x"]), float(m["y"])
        for t0 in (0, 0.16):
            app.inject("down", x, y, t0)
            app.inject("up", x, y, t0 + 0.08)
        after_input(done, 0.3)

    def drag(m, done):
        x1, y1, x2, y2 = (float(m[k]) for k in ("x1", "y1", "x2", "y2"))
        ms = float(m.get("ms", 300)) / 1000
        n = max(2, int(ms / 0.016))
        app.inject("down", x1, y1, 0)
        for i in range(1, n + 1):
            t = i / n
            app.inject("move", x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, 0.02 + ms * t)
        app.inject("up", x2, y2, 0.04 + ms)
        after_input(done, ms + 0.06)

    def longpress(m, done):
        x, y = float(m["x"]), float(m["y"])
        hold = float(m.get("ms", 800)) / 1000
        app.inject("down", x, y, 0)
        t = 0.05
        while t < hold:
            app.inject("move", x, y, t)
            t += 0.05
        end = hold
        if "x2" in m:
            x2, y2 = float(m["x2"]), float(m["y2"])
            dms = float(m.get("drag_ms", 400)) / 1000
            n = max(2, int(dms / 0.016))
            for i in range(1, n + 1):
                f = i / n
                app.inject("move", x + (x2 - x) * f, y + (y2 - y) * f, hold + dms * f)
            end = hold + dms
            x, y = x2, y2
        app.inject("up", x, y, end + 0.02)
        after_input(done, end + 0.05)

    def jack(m, done):
        model.simulate_jack(m.get("which", "in"), bool(int(m.get("plugged", 1))))
        after_input(done, 0.5)

    def screenshot(m, done):
        path = m.get("path") or "/tmp/bassbox-ui.png"

        def shoot():
            app.render()
            if m.get("stream"):
                import base64
                import io
                from PIL import Image
                from gfx import rgb565_to_rgb
                buf = io.BytesIO()
                Image.fromarray(rgb565_to_rgb(app.out565), "RGB").save(buf, "PNG")
                done({"png_b64": base64.b64encode(buf.getvalue()).decode()})
            else:
                app.screenshot(path)
                done({"path": path})
        if m.get("now"):
            shoot()
        else:
            core.when_idle(shoot)

    def state(m, done):
        scr = app.screen
        done({"screen": scr.name if scr else None, "connected": model.connected, "jacks": model.jacks,
              "bypass": model.bypassed(), "preset": model.state.get("name"), "dirty": model.state.get("dirty"),
              "chain": [{"uid": s["uid"], "type": s["type"], "enabled": s.get("enabled", True), "mix": s.get("mix"),
                         "params": s.get("params")} for s in model.chain],
              "overlays": [type(o).__name__ for o in app.overlays], "flushes": app.flushes,
              "edit_uid": getattr(scr, "uid", None), "deviations": sorted(model.deviations),
              "controls": {"devices": [d.name for d in getattr(ctx, "controls", None).devs.values()] if getattr(ctx, "controls", None) else [],
                           "actions": getattr(getattr(ctx, "controls", None), "actions", [])}})

    def stats(m, done):
        st = list(app.stats)
        since = float(m.get("since", 0))
        st = [s for s in st if s["t"] >= since]
        if m.get("reset"):
            app.stats.clear()
        done({"frames": st[-int(m.get("n", 60)):], "flushes": app.flushes, "now": time.monotonic(),
              "rtt_ms": model.client.rtt_ms[-20:]})

    def wait_idle(m, done):
        core.when_idle(lambda: done(), timeout=float(m.get("timeout", 5)))

    def goto(m, done):
        name = m.get("screen")
        if name == "edit":
            uid = m.get("uid")
            if uid is None and model.chain:
                uid = model.chain[int(m.get("index", 0))]["uid"]
            nav.edit(int(uid))
        elif name == "library":
            nav.library(m.get("index"))
        else:
            getattr(nav, {"saveas": "save_as", "audiocheck": "audio_check"}.get(name, name))()
        core.when_idle(lambda: done())

    def engine(m, done):
        msg = dict(m.get("msg") or {})
        cmd = msg.pop("cmd")
        model.client.request(cmd, cb=lambda r: done({"response": r}), **msg)

    def profile(m, done):
        """{"cmd":"profile","secs":10} -> top functions by cumulative time while the app runs."""
        import cProfile
        import io
        import pstats
        pr = cProfile.Profile()
        pr.enable()

        def stop():
            pr.disable()
            out = io.StringIO()
            pstats.Stats(pr, stream=out).sort_stats(m.get("sort", "tottime")).print_stats(int(m.get("n", 25)))
            done({"profile": out.getvalue()})
        app.after(float(m.get("secs", 10)), stop)

    def mem(m, done):
        import gc
        import gfx
        ws = [o for o in gc.get_objects() if isinstance(o, core.Widget)]
        cached = sum(1 for w in ws if w._cache is not None)
        cbytes = sum(w._cache[1].get_stride() * w._cache[1].get_height() for w in ws if w._cache is not None)
        try:
            rss = int(open("/proc/self/statm").read().split()[1]) * os.sysconf("SC_PAGE_SIZE") >> 20
        except OSError:
            rss = None
        done({"fonts": len(gfx._fonts), "masks": len(gfx._masks), "mask_kb": gfx._mask_bytes[0] >> 10,
              "widgets": len(ws), "cached_layers": cached, "layer_kb": cbytes >> 10, "gc": gc.get_count(),
              "timers": len(app.timers), "io": len(app.io), "rss_mb": rss})

    def refresh(m, done):
        model.refresh_state(lambda r: core.when_idle(lambda: done()))

    return {"tap": tap, "doubletap": doubletap, "drag": drag, "longpress": longpress, "jack": jack,
            "screenshot": screenshot, "state": state, "stats": stats, "wait_idle": wait_idle, "goto": goto,
            "engine": engine, "refresh": refresh, "profile": profile, "mem": mem}


# ====================================================================== touch
def start_touch(app, ctx):
    try:
        from screen import CAL_PATH, Calibration, Touch
    except Exception as e:
        log("touch: display/screen.py unavailable:", e)
        return None
    try:
        touch = Touch()
    except SystemExit as e:
        log("touch: no device:", e)
        return None
    cal = Calibration.load()
    if cal is None:
        log("touch: no calibration, using the repo default")
        cal = Calibration([[-0.0852, 0.0006, 328.2], [0.0012, 0.1283, -31.1]])
    th = core.TouchThread(touch, cal, app.post_touch)
    th.start()

    def inverse(x, y):
        M = th.cal.M
        A, b = M[:, :2], M[:, 2]
        r = np.linalg.solve(A, np.array([x, y]) - b)
        return float(r[0]), float(r[1])

    def apply(raws, targets):
        try:
            new = Calibration.fit(raws, targets)
            resid = [float(np.hypot(*(np.array(new(*r)) - t))) for r, t in zip(raws, targets)]
            if max(resid) > 25:
                return False, resid
            new.save(residual_px=[round(r, 1) for r in resid],
                     points=[{"target": list(t), "raw": list(r)} for r, t in zip(raws, targets)])
            th.cal = new
            log("touch: new calibration saved to", CAL_PATH, "resid", [round(r, 1) for r in resid])
            return True, resid
        except Exception as e:
            log("touch: calibration failed:", e)
            return False, []
    ctx.touch_cal_inverse = inverse
    ctx.apply_calibration = apply
    log("touch: reading", touch.dev)
    return th


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=os.environ.get("BASSBOX_ENGINE_SOCK", "/run/bassbox/engine.sock"))
    ap.add_argument("--debug", default=os.environ.get("BASSBOX_UI_DEBUG_SOCK", "/run/bassbox/ui-debug.sock"))
    ap.add_argument("--backend", default="auto", choices=["auto", "drm", "fb", "headless"])
    ap.add_argument("--no-touch", action="store_true")
    ap.add_argument("--no-splash", action="store_true")
    a = ap.parse_args()
    set_priority()
    backend, kind = open_backend(a.backend)
    log(f"bassbox-ui: backend={kind} engine={a.engine} debug={a.debug}")
    app = core.App(backend, log=log)
    ctx = base.ctx
    ctx.header = Header()
    model = Model(a.engine, log=log)
    ctx.model = model
    nav = Navigator(model)
    ctx.nav = nav
    if a.debug:
        try:
            core.DebugServer(a.debug, debug_handlers(app, model, nav, ctx))
        except OSError as e:
            log("debug socket unavailable:", e)
    if kind != "headless" and not a.no_touch:
        start_touch(app, ctx)
    if kind != "headless":
        import controls
        ctx.controls = controls.Controls(model, nav, log=log)

    def bye(*_):
        app.quit = True
        try:
            backend.close()
        finally:
            os._exit(0)
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)
    model.client.tick()
    if a.no_splash:
        nav.started -= 10
        nav.board()
    else:
        nav.splash()
    try:
        app.run()
    finally:
        backend.close()


if __name__ == "__main__":
    main()
