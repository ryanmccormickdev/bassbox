# Status

Written from the project's own logs. The last dated entry is 2026-10-18.

## Measured or run

| What | Result | Details |
|---|---|---|
| Engine on the Pi 4 with the HAT | runs at boot: 64 x 3 frames, S32_LE, 48 kHz, audio thread on CPU3 at `SCHED_FIFO` 80 | [realtime.md](realtime.md) |
| Buffer soak, 10 minutes, heavy 6-slot chain, background CPU load, UI running | 0 xruns; DSP load 12.9 % average, 38.5 % peak of a period | [realtime.md](realtime.md) |
| Power-on to audio | about 20 s | [realtime.md](realtime.md) |
| Codec capture with the input open | DC +0.00004 of full scale; noise about -92.7 dBFS (`arecord`) | [audio-path.md](audio-path.md) |
| DAC and I2S playback | a 1 kHz tone played out of the speaker output (volume -41 dB) was picked up acoustically by the onboard microphone | log, 2026-10-06 |
| `bb-selftest` | 47 checks pass on the Pi and on macOS | [realtime.md](realtime.md) |
| Effect test suite | 33 effects, 0 failures, 6 warnings; regenerates identically on macOS | `engine/tests/results/` |
| Display and touch | working; touch calibration accuracy about 1 px at the calibration points, about 9 px in the tap game | [hardware.md](hardware.md) |
| Tuner | within 0.2 cent on synthetic bass plucks (B0 to G3) | `bb-selftest` |
| Enclosure | 41 tests and the clash check against the bought parts pass; the print plate was sliced without supports | [`enclosure/`](../enclosure/) |

## Not verified

- **A bass has not been heard through the analog input.** An overnight loopback test (panel OUT to
  panel IN) passed no signal, and on 2026-10-18 a first test with a real bass and amp found the
  input sweep (LINPUT3 and RINPUT3 at 0 and +6 dB, LINPUT2 and RINPUT2 at +6 dB) sitting on the ADC
  noise floor (about -93 dBFS) with no 110 Hz tone from an open A string, while the onboard microphones picked the strings up
  acoustically at 109 Hz (-59 dBFS). The capture path works, so the suspect is the wiring from the
  panel IN jack to the HAT's LINE IN. The tuner reads nothing for the same reason. Nothing later
  is recorded.
- Because of that, **round-trip latency, loop gain, THD and the input gain calibration are
  unmeasured.** The 4 ms figure for a 64 x 3 buffer is the nominal buffer size, plus an estimated
  0.5 to 1 ms in the codec.
- On 2026-10-18 the output side produced a buzz on the amplifier that is analog; the engine's
  output was digitally silent at the time.
- Touch feel was tuned without a finger on the screen (`config/touch_tuning.json` documents each
  constant); recalibrate from Settings if taps land off target.
- USB footswitch and knob control was exercised only with a virtual input device.

## Known issues

- **White flash at boot.** The panel shows solid white for about 10 s while the Pi firmware boots:
  the backlight is wired straight to 3.3 V and the LCD is white until its driver initializes. A fix
  needs hardware (drive the LED pin from a free GPIO through a small NPN or MOSFET, for example
  GPIO12, and have the UI switch it on after the first frame).
- **Passive basses sound a little darker** without a buffer or DI in front, because of the boost
  path's input impedance ([audio-path.md](audio-path.md)).
- **`setup.sh` does not install `python3-cairo`**, which the UI needs.
- **The enclosure's design review left three open findings** between bought parts: a 6 mm heatsink
  on the SoC would hit the HAT's middle 3.5 mm jack (only 2.78 mm of room above the SoC) and its
  speaker, and the display's microSD socket overlaps the Pi's audio and HDMI solder tails by 0.1 mm
  in the model. See [`enclosure/DESIGN.md`](../enclosure/DESIGN.md).
- `ui/core.py` fails to create its debug socket when `--debug` is given a bare file name with no
  directory part (`os.makedirs` of an empty string); use `./name.sock`.
