#!/usr/bin/env python3
"""Build /lib/firmware/st7796s.bin for the panel-mipi-dbi-spi kernel driver.

File format (drivers/gpu/drm/tiny/panel-mipi-dbi.c): 15-byte magic
"MIPI DBI" + 7 NULs, 1 version byte (=1), then repeated
[cmd, nparams, params...]. cmd 0x00 with one param = delay in ms.
"""
import sys

DELAY = 0x00
MADCTL_MY, MADCTL_MX, MADCTL_MV, MADCTL_BGR = 0x80, 0x40, 0x20, 0x08

# Portrait 320x480. --rotate=180 flips it for the as-built enclosure orientation.
# (0/90/270 would need width/height swapped in config.txt for 90/270.)
ROTATIONS = {
    0: MADCTL_MX | MADCTL_BGR,
    90: MADCTL_MV | MADCTL_BGR,
    180: MADCTL_MY | MADCTL_BGR,
    270: MADCTL_MX | MADCTL_MY | MADCTL_MV | MADCTL_BGR,
}
ROTATE = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--rotate=")), 0))
MADCTL = ROTATIONS[ROTATE]
INVERT = "--invert" in sys.argv

seq = [
    (0x01, []), (DELAY, [120]),             # SWRESET
    (0x11, []), (DELAY, [120]),             # SLPOUT
    (0xF0, [0xC3]), (0xF0, [0x96]),         # unlock command set 2
    (0x36, [MADCTL]),                       # MADCTL
    (0x3A, [0x55]),                         # 16-bit RGB565
    (0xB4, [0x01]),                         # 1-dot inversion
    (0xB6, [0x80, 0x02, 0x3B]),             # display function control
    (0xE8, [0x40, 0x8A, 0x00, 0x00, 0x29, 0x19, 0xA5, 0x33]),
    (0xC1, [0x06]), (0xC2, [0xA7]),
    (0xC5, [0x18]), (DELAY, [120]),         # VCOM
    (0xE0, [0xF0, 0x09, 0x0B, 0x06, 0x04, 0x15, 0x2F, 0x54, 0x42, 0x3C, 0x17, 0x14, 0x18, 0x1B]),
    (0xE1, [0xE0, 0x09, 0x0B, 0x06, 0x04, 0x03, 0x2B, 0x43, 0x42, 0x3B, 0x16, 0x14, 0x17, 0x1B]),
    (DELAY, [120]),
    (0xF0, [0x3C]), (0xF0, [0x69]),         # lock command set 2
]
if INVERT:
    seq.append((0x21, []))                  # INVON (some IPS panels need it)
seq += [(0x29, []), (DELAY, [50])]          # DISPON

out = bytearray(b"MIPI DBI" + b"\0" * 7 + bytes([1]))
for cmd, params in seq:
    out += bytes([cmd, len(params), *params])

path = next((a for a in sys.argv[1:] if not a.startswith("--")), "st7796s.bin")
open(path, "wb").write(out)
print(f"wrote {path} ({len(out)} bytes, rotate={ROTATE}, invert={INVERT})")
