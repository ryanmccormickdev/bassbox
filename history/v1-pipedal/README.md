# v1: PiPedal and a neural amp model

The first version of bassbox, built on 2026-10-01 before the codec HAT was on hand. It turned a
Raspberry Pi 4 with a RaspiAudio ULTRA++ HAT into a boot-to-play bass distortion pedal using
[PiPedal](https://github.com/rerdavies/pipedal) 2.0.110 (MIT) as the audio host, with a neural amp
model (NAM) as the drive, controlled from PiPedal's web UI in a browser. The goal was that a
power-on gives the distortion preset within about 90 s with no login and no clicks.
On 2026-10-06 the project moved to the custom engine in [`engine/`](../../engine/); PiPedal stayed
installed but disabled as a fallback (`scripts/setup.sh` still installs it, and
`config/pipedal.conf` pins its version and SHA-256).

## The "Bass Drive" preset

`scripts/pipedal_preset.py` writes PiPedal's on-disk bank, so the preset is reproducible from a
fresh image:

```
gate ─► split (mix) ┬─ A: neural amp model ("MRSH JM50LD I Crunch2 FAT CAB", a full rig, so no cab IR) ─┐ ─► output volume
                    └─ B: clean branch, low-passed near 200 Hz (graphic EQ + parametric EQ)           ─┘
```

The clean low branch keeps the low end solid, which is the usual way to distort a bass. ToobAmp
has no low-pass reaching 200 Hz, so the branch stacks two EQs. Measured response of the branch
(rendered offline): -0.8 dB at 100 Hz, -2.5 at 200, -4.2 at 250, -18.5 at 300, -24 at 400, and
-37 dB or lower from 700 Hz up. The neural amp input is calibrated for the codec (+2.2 dBu at full
scale) with +6 dB input gain and normalised output. A second preset, "Bypass (clean)", is an empty
pedalboard for checking that nothing leaks around the amp.

## What was verified (without the HAT)

- PiPedal installed and "Bass Drive" selected as the boot preset; `pipedald` active 36 s after
  boot, CPU governor `performance`, `get_throttled` 0x0, PipeWire masked.
- Offline rendering through the real PiPedal engine (`scripts/render.sh`, `scripts/play-file.sh`):
  an ALSA loopback card stands in for the HAT, so any audio file can be run through the live preset.
  A test riff at -6 dBFS peak (-18.6 dBFS RMS) came out at -5.3 dBFS peak (-17.8 dBFS RMS), so
  active and bypassed are about level-matched; harmonic energy from 0.8 to 12 kHz rose from 1.4 %
  to 16.4 %. The audio thread used about 18 % of one core. No xruns while rendering. "Bypass
  (clean)" rendered flat to 0.0 dB from 40 Hz to 8 kHz.
- The hardware scripts failed cleanly without the card, and their arithmetic was unit-tested.

Not done in v1: the input-wiring probe, latency table, gain calibration, buffer soak and listening
checks (all scripted in `scripts/bringup.sh`, which drives `scripts/hw.sh`). They needed the HAT.

## Files

| Path | What |
|---|---|
| `config/mixer.conf` | the original WM8960 mixer profile: LINPUT3 through the input PGA. This routing later turned out to make the capture drift to full scale; see [`docs/audio-path.md`](../../docs/audio-path.md) for the fix. |
| `config/pipedal.conf` | PiPedal version, SHA-256, card name, sample rate, buffer size |
| `config/systemd/` | CPU governor and mixer units, and a drop-in that orders `pipedald` after them |
| `scripts/pipedal_preset.py` | writes PiPedal's audio config, channel routing, and the preset bank |
| `scripts/setup.sh`, `push.sh`, `mixer.sh` | idempotent provisioning, rsync to `/opt/bassbox`, mixer apply/verify |
| `scripts/hw.sh`, `bringup.sh`, `check.sh` | hardware bring-up (detect, probe, latency, record, calibrate, soak, snapshot), the interactive driver for it, and the acceptance checks |
| `scripts/render.sh`, `play-file.sh` | render an audio file through the live preset |
| `scripts/audio_tools.py`, `make_test_signals.py` | analysis (alignment, frequency response, THD+N) and the synthetic test riff |

These are kept for reference and are not used by the current engine. Some comments mention helper
scripts that are not in the tree (`calibrate.sh`, `soak.sh`, `measure_lowpass.sh`; calibrate and soak
are subcommands of `hw.sh`), and `bringup.sh` and `check.sh` read and write a `NOTES.md` change log
that is not part of this tree.
The scripts take the Pi's address from `BASSBOX_PI` (`<user>@<pi-host>`).
