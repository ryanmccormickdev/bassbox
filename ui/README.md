# ui

The pedal's touchscreen app, in Python. It draws a 320 x 480 portrait UI on the ST7796S panel,
reads the XPT2046 touch controller, and controls the engine over its unix socket
([protocol](../docs/architecture.md)). It starts at boot (`config/systemd/bassbox-ui.service`) and
keeps running if the engine is down, showing "reconnecting" until it comes back.

- **Rendering.** `gfx.py` draws vector shapes with cairo and text with Pillow/FreeType (glyph masks
  are cached) into an XRGB8888 surface, and dithers it to RGB565 with a 4x4 Bayer table.
  `backend_drm.py` flushes only the dirty rectangles through a DRM dumb buffer (`DIRTYFB`);
  `backend_fb.py` is a framebuffer fallback and `main.py` has a headless backend for development.
  An idle UI sleeps in `select()` and flushes nothing.
- **Toolkit.** `core.py` is a small retained-mode widget tree with dirty rectangles, a gesture
  recogniser (tap, double-tap, drag, long-press), timers and a debug socket. `chrome.py` holds the
  header with the animated jacks and level meters, the footer, snackbar and dialogs; `art.py` draws
  the effect icons, knobs and jacks.
- **Screens** (`screens/`): the pedal board, the pedal edit face, the effect library, presets with
  save-as (a name generator, so no typing), the tuner, settings with touch calibration, an audio
  check (input and output meters, auto-set gain, test tone) and a splash.
- **Engine side.** `engine_client.py` is a non-blocking JSON-lines client, `model.py` keeps the
  state with optimistic edits, and `controls.py` maps USB HID footswitches and knobs to commands.
- `DESIGN.md` is the design document: panel facts, gestures, layout grid, colours, motion, the
  three directions that were explored, robustness fixes. `mockups/` and `shots/` hold the
  mockups and screenshots it refers to (the `photo_*.jpg` files are photos of the panel).

## Run

On the Pi, as root: `sudo python3 /opt/bassbox/ui/main.py`. It needs `python3-numpy`,
`python3-pil` and `python3-cairo`; the fonts are in `fonts/static/` (SIL OFL, see
`fonts/README.md`).

On a laptop, against the mock engine, with no panel:

```
python3 ui/mock_engine.py --socket mock.sock &
python3 ui/main.py --backend headless --engine mock.sock --debug ./ui.sock --no-touch &
python3 ui/tools/inject.py --sock ./ui.sock screenshot board.png
```

`tools/inject.py` taps, drags and screenshots through the same gesture pipeline as the panel;
`tools/shots.py` captures every screen; `tools/perf.py` and `tools/timing.py` measure CPU and
latency; `tools/sweep.py` renders every effect's card and face.
