# scripts

Run on the developer machine ("Mac") unless marked Pi. The Pi's address comes from
`BASSBOX_PI` (`<user>@<pi-host>`).

| Script | Where | What |
|---|---|---|
| `push.sh [--setup]` | Mac | rsync the repo to `/opt/bassbox` on the Pi (setting the Pi's clock first: it has no RTC); `--setup` also runs `setup.sh` through a temporary reverse SOCKS tunnel |
| `setup.sh [--proxy]` | Pi, root | build the pedal from a fresh Raspberry Pi OS Lite image, idempotently: [`docs/system-changes.md`](../docs/system-changes.md) |
| `mixer.sh apply\|verify\|dump\|present` | Pi | apply `config/mixer.conf` to the WM8960 with `amixer`, or compare it with the live card |
| `cmdline.py [--check]` | Pi, root | add the real-time and quiet-boot tokens to `/boot/firmware/cmdline.txt`, touching nothing else |
| `bbctl.py` | either | command-line client for the engine socket (`bbctl.py state`, `set_param uid=3 param=time value=350`) |
| `check.sh` | Mac | one-minute health check over ssh: services, buffer and RT placement, xruns, DSP load, throttling, self-test, boot-to-audio time |
| `play-file.sh FILE [--ab]` | Mac | render any audio file through the pedal's current chain with a local `bb-render` build and play it |
| `soak.sh SECONDS [PERIODxN ...]` | Pi, root | xrun soak and loopback latency for each ALSA buffer setting ([`docs/realtime.md`](../docs/realtime.md)) |
| `fuzz_soak.py MINUTES` | Pi | randomized stress run: preset loads, 30 Hz parameter bursts, chain edits and UI gestures, logging xruns, DSP load, memory and restarts |
| `make_test_signals.py riff\|tones OUT.wav` | either | the synthetic bass riff and stepped sines used by the tests |

The PiPedal-era scripts are in [`history/v1-pipedal/`](../history/v1-pipedal/).
