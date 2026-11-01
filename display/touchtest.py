#!/usr/bin/env python3
"""Touchscreen bring-up / calibration app for the ST7796S + XPT2046 panel.

1. Orientation screen: colored, numbered corners + a TOP arrow (framebuffer top).
2. Calibration: tap 5 crosshairs; fits the affine raw->screen map and saves it
   to /var/lib/bassbox/touch_cal.json (copy it to config/touch_cal.json to keep it).
3. Game: 20 shrinking targets. Logs accuracy (px) and reaction time.

    sudo python3 /opt/bassbox/display/touchtest.py [--skip-cal] [--cal-only]

Every touch is logged as JSON lines to /tmp/touchtest.log (and stdout).
"""
import json
import random
import sys
import time

import numpy as np

from screen import Calibration, Screen, font

LOG_PATH = "/tmp/touchtest.log"


class App:
    def __init__(self, s):
        self.s = s
        self.W, self.H = s.W, s.H
        self.log = open(LOG_PATH, "a", buffering=1)
        self.emit("start", fb=s.fb.dev, size=[self.W, self.H],
                  touch=s.touch.dev, ranges=s.touch.ranges)

    def emit(self, kind, **kw):
        line = json.dumps({"t": round(time.time(), 3), "kind": kind, **kw})
        self.log.write(line + "\n")
        print(line, flush=True)

    # -- phase 1: orientation
    def orientation(self):
        img, d = self.s.canvas()
        W, H = self.W, self.H
        for (cx, cy), col in [((0, 0), (255, 0, 0)), ((W, 0), (0, 255, 0)),
                              ((W, H), (0, 0, 255)), ((0, H), (255, 255, 0))]:
            d.rectangle([cx - 60, cy - 60, cx + 60, cy + 60], fill=col)
        f = font(18)
        d.text((8, 64), "1 RED", font=f, fill=(255, 80, 80))
        d.text((W - 8, 64), "2 GREEN", font=f, fill=(80, 255, 80), anchor="ra")
        d.text((W - 8, H - 64), "3 BLUE", font=f, fill=(120, 120, 255), anchor="rd")
        d.text((8, H - 64), "4 YELLOW", font=f, fill=(255, 255, 80), anchor="ld")
        d.polygon([(W / 2, 20), (W / 2 - 40, 80), (W / 2 + 40, 80)], fill=(255, 255, 255))
        d.rectangle([W / 2 - 14, 80, W / 2 + 14, 150], fill=(255, 255, 255))
        d.text((W / 2, 165), "TOP", font=font(40), fill=(255, 255, 255), anchor="ma")
        d.text((W / 2, H / 2 + 20), f"{W} x {H}", font=font(22), fill=(180, 180, 180), anchor="mm")
        d.text((W / 2, H / 2 + 60), "tap anywhere", font=font(22), fill=(180, 180, 180), anchor="mm")
        d.text((W / 2, H / 2 + 90), "to continue", font=font(22), fill=(180, 180, 180), anchor="mm")
        self.s.show(img)
        self.emit("orientation_tap", raw=self.s.wait_tap(raw=True))

    # -- phase 2: calibration
    def calibrate(self):
        m = 30
        targets = [(m, m), (self.W - m, m), (self.W - m, self.H - m), (m, self.H - m),
                   (self.W / 2, self.H / 2)]
        raws = []
        for i, (tx, ty) in enumerate(targets):
            img, d = self.s.canvas()
            d.text((self.W / 2, self.H / 2 - 40 if i == 4 else self.H / 2),
                   f"Calibrate {i + 1}/{len(targets)}", font=font(24),
                   fill=(255, 255, 255), anchor="mm")
            d.text((self.W / 2, self.H / 2 + 30 if i < 4 else self.H / 2 + 60),
                   "tap the crosshair", font=font(18), fill=(160, 160, 160), anchor="mm")
            d.line([tx - 20, ty, tx + 20, ty], fill=(255, 60, 60), width=2)
            d.line([tx, ty - 20, tx, ty + 20], fill=(255, 60, 60), width=2)
            d.ellipse([tx - 8, ty - 8, tx + 8, ty + 8], outline=(255, 255, 255), width=2)
            self.s.show(img)
            raw = self.s.wait_tap(raw=True)
            raws.append(raw)
            self.emit("cal_point", target=[tx, ty], raw=raw)
        cal = Calibration.fit(raws, targets)
        resid = [round(float(np.hypot(*(np.array(cal(*r)) - t))), 1) for r, t in zip(raws, targets)]
        cal.save(residual_px=resid, points=[{"target": t, "raw": r} for t, r in zip(targets, raws)])
        self.s.cal = cal
        self.emit("calibrated", M=cal.M.tolist(), hints=cal.overlay_hints(), residual_px=resid)

    # -- phase 3: game
    def game(self, rounds=20):
        W, H, s = self.W, self.H, self.s
        rng = random.Random()
        hits, errs, rts, trail = 0, [], [], []
        for n in range(rounds):
            radius = max(14, 48 - n * 2)
            tx = rng.uniform(radius + 4, W - radius - 4)
            ty = rng.uniform(radius + 60, H - radius - 4)
            s.drain()
            t0 = time.time()
            last_draw, cur, press, t_down, done = 0, None, [], None, None
            while not done:
                now = time.time()
                if now - last_draw > 0.06:
                    img, d = s.canvas()
                    d.text((8, 8), f"round {n + 1}/{rounds}", font=font(16), fill=(200, 200, 200))
                    d.text((W - 8, 8), f"hits {hits}", font=font(16), fill=(120, 255, 120), anchor="ra")
                    if errs:
                        d.text((8, 30), f"avg err {np.mean(errs):.0f}px   avg {np.mean(rts) * 1000:.0f}ms",
                               font=font(14), fill=(160, 160, 160))
                    d.ellipse([tx - radius, ty - radius, tx + radius, ty + radius], fill=(255, 140, 0))
                    d.ellipse([tx - 3, ty - 3, tx + 3, ty + 3], fill=(0, 0, 0))
                    for px, py in trail[-40:]:
                        d.point((px, py), fill=(0, 200, 255))
                    if cur:
                        cx, cy = cur
                        d.ellipse([cx - 6, cy - 6, cx + 6, cy + 6], outline=(0, 255, 255), width=2)
                    s.show(img)
                    last_draw = now
                for kind, rx, ry, p in s.touch.poll(0.02):
                    sx, sy = s.to_screen(rx, ry)
                    if kind == "down":
                        press, t_down = [], time.time()
                        self.emit("down", raw=[rx, ry], p=p, screen=[round(sx), round(sy)])
                    if kind in ("down", "move") and t_down:
                        cur = (sx, sy)
                        press.append(cur)
                        trail.append(cur)
                    if kind == "up" and t_down:
                        # Ignore bounce blips; use median of press minus the drifty lift-off samples.
                        if time.time() - t_down >= 0.08 and len(press) >= 3:
                            done = tuple(np.median(np.array(press[: max(1, len(press) - 3)]), axis=0))
                        cur, press, t_down = None, [], None
            sx, sy = done
            err = float(np.hypot(sx - tx, sy - ty))
            rt = time.time() - t0
            hit = err <= radius
            hits += hit
            errs.append(err)
            rts.append(rt)
            self.emit("tap", round=n + 1, target=[round(tx), round(ty)], r=radius,
                      screen=[round(sx), round(sy)], err_px=round(err, 1),
                      dx=round(sx - tx, 1), dy=round(sy - ty, 1), rt_ms=round(rt * 1000), hit=hit)
            trail.clear()
            img, d = s.canvas((0, 120, 0) if hit else (120, 0, 0))
            d.text((W / 2, H / 2), "HIT" if hit else "MISS", font=font(48), fill=(255, 255, 255), anchor="mm")
            d.text((W / 2, H / 2 + 50), f"{err:.0f}px off", font=font(20), fill=(255, 255, 255), anchor="mm")
            s.show(img)
            time.sleep(0.35)

        img, d = s.canvas()
        lines = [("DONE", 44), (f"{hits}/{rounds} hits", 28),
                 (f"avg error {np.mean(errs):.1f}px", 20),
                 (f"avg reaction {np.mean(rts) * 1000:.0f}ms", 20), ("tap to play again", 18)]
        y = H / 2 - 100
        for text, size in lines:
            d.text((W / 2, y), text, font=font(size), fill=(255, 255, 255), anchor="ma")
            y += size + 22
        s.show(img)
        self.emit("game_over", hits=hits, rounds=rounds, avg_err_px=round(float(np.mean(errs)), 1),
                  avg_rt_ms=round(float(np.mean(rts)) * 1000))
        s.wait_tap(raw=True)

    def run(self, skip_cal=False, cal_only=False):
        self.orientation()
        if not (skip_cal and self.s.cal):
            self.calibrate()
        if cal_only:
            return
        while True:
            self.game()


if __name__ == "__main__":
    with Screen() as screen:
        try:
            App(screen).run(skip_cal="--skip-cal" in sys.argv, cal_only="--cal-only" in sys.argv)
        except KeyboardInterrupt:
            pass
