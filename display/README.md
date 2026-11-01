# display

Bring-up tools for the 4.0" ST7796S panel and its XPT2046 touch controller on SPI0
([wiring and overlays](../docs/hardware.md)).

- `make_st7796s_bin.py` writes `/lib/firmware/st7796s.bin`, the init sequence the kernel's
  `panel-mipi-dbi-spi` driver loads. `--rotate=180` gives the orientation used in the enclosure.
- `screen.py` is a small library (no GUI toolkit): the framebuffer, a canvas, evdev touch with
  an affine calibration, and a debounced `wait_tap()` that handles the XPT2046's release bounce and
  position drift. The UI reuses its touch filtering.
- `touchtest.py` is the bring-up app: an orientation screen, a five-point calibration that writes
  `/var/lib/bassbox/touch_cal.json`, and a 20-target tap game that logs accuracy and reaction time.

Run as root on the Pi, for example `sudo python3 /opt/bassbox/display/touchtest.py --cal-only`.
Needs `python3-numpy` and `python3-pil`.
