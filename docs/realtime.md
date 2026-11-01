# Real-time setup and measurements

Everything below was measured on the Pi on 2026-10-06 unless it says otherwise. The commands that
produced it are in the repo (`scripts/soak.sh`, `scripts/check.sh`, `engine/tests`).

## How the audio core is protected

| Setting | Where | Effect |
|---|---|---|
| `isolcpus=3 irqaffinity=0-2` on the kernel command line | `scripts/cmdline.py` | CPU3 runs only the audio thread; all IRQs stay on CPU0-2. `/sys/devices/system/cpu/isolated` reads `3`. |
| `nohz_full=3 rcu_nocbs=3` on the kernel command line | `scripts/cmdline.py` | Inert: the stock Pi kernel has no `CONFIG_NO_HZ_FULL` or `CONFIG_RCU_NOCB_CPU` (`dmesg` prints "Housekeeping: nohz unsupported"). Kept for the day the kernel changes; no kernel change was made. |
| Audio thread `SCHED_FIFO` 80, pinned to CPU3, `mlockall`, stack pre-faulted, `FPCR.FZ` | `engine/src/rt_thread.cpp`, `engine/src/main_engine.cpp`, `config/engine.conf` | No page faults, no denormal slow paths. |
| `bassbox-engine.service`: `CPUAffinity=0-2`, `LimitRTPRIO=95`, `LimitMEMLOCK=infinity`, `Nice=-5`, `Restart=always` | `config/systemd/` | The control thread stays off the audio core; a crash restarts in 1 s. |
| `bassbox-ui.service`: `CPUAffinity=0-2`, `Nice=5` | `config/systemd/` | The screen can never take CPU from the audio core. |
| CPU governor `performance` | `bassbox-governor.service` | Stable latency. |
| Quiet boot, no login prompt on the panel | `scripts/cmdline.py`, `getty@tty1` masked | The panel stays black until the UI draws. |

The IRQ threads for the jack-sense GPIOs run on CPU0. Moving the I2S DMA IRQs (18 and 19) to CPU3
was tried and made 64 x 2 worse (7586 xruns), so they stay on CPU0.

## Buffer size

`scripts/soak.sh` stops the engine, runs the engine binary with each ALSA period and period count,
loads a heavy chain (the factory "Doom" and "Dub Echo" chains stacked, 6 slots; `bb-render` puts
Doom alone at 5.6 % of a 64-frame period on average), sets the output to -60 dB, keeps CPU0-2 busy
with `stress-ng --cpu 3 --vm 1 --vm-bytes 128M` at nice 19, and has the UI running throughout.
Xruns in a 90 second screen (each number is a separate run):

| period x periods | nominal buffer | xruns, 90 s screen | 10 minutes |
|---|---|---|---|
| 32 x 2 | 64 frames, 1.33 ms | storm, then the 3 s watchdog fires | not run |
| 32 x 3 | 96 frames, 2.0 ms | 2748; 5052 | not run |
| 64 x 2 | 128 frames, 2.67 ms | 2205; 7586 (with the I2S IRQs on CPU3); 7723 (7721 of them playback underruns) | not run |
| **64 x 3** | **192 frames, 4.0 ms** | **0** | **0 in 600 s** (DSP average 12.9 %, peak 38.5 % of a period) |
| 128 x 2 | 256 frames, 5.33 ms | 0 | not run |
| 128 x 3 | 384 frames, 8.0 ms | 0 on a retest (the first run, 1896 xruns, was contaminated by another client editing the state) | not run |

64 x 3 is the shipped setting (`config/engine.conf`).

**Why two periods fail.** The capture backlog after each read is at most 12 frames, so the audio
thread wakes on time and almost every xrun is a playback underrun. The I2S DMA fetches about 32
frames ahead of the DAC, so the real write slack is (periods - 1) x period - 32 frames: 32 frames
(0.67 ms) at 64 x 2 and 96 frames (2 ms) at 64 x 3. The heavy chain's 0.5 ms processing peaks plus
wake-up jitter do not fit in 0.67 ms. A possible gain, not done: prefill about 2.5 periods into a
three-period buffer for about 3.3 ms nominal.

**Latency.** The nominal I/O latency is period x periods frames plus the codec's ADC and DAC
filters (roughly 0.5 to 1 ms). The round-trip latency was not measured, because the analog loopback
carried no signal (see [status.md](status.md)). `bassbox-engine --latency-test SECONDS` measures it
in one command once a cable works; on the simulated 300-frame loop of `--null-audio` it reports
300.0 frames and -6.02 dB.

## Boot to sound

Cold reboot on 2026-10-06: the firmware loads `kernel8.img` at 8.6 s; systemd reports 2.3 s of
kernel and 17.5 s of userspace to `multi-user.target`; `bassbox-engine` is running 10.8 s after
kernel start and audio is streaming at 11.4 s. Power-on to audio is about 20 s. The live state was
restored exactly (preset, output level, chain, parameters and the edited flag). The critical chain
is `alsa-restore` then `sound.target` then `bassbox-mixer` (0.7 s) then `bassbox-engine`. A later
`scripts/check.sh` run recorded boot to audio at 10.5 s after kernel start.

## Engine tests

- `bb-selftest` (47 checks, no audio hardware needed): chain edits are click-free (add, remove,
  move, enable, mix, bypass, preset load: the largest sample step stays at the signal's own slope),
  tails ring out and stop, surviving slots keep their state, a NaN from an effect is caught,
  the limiter is transparent at -1 dBFS, presets and slot cleanup, and the tuner reads synthetic
  bass plucks (B0, E1, A1, D2, G2, G3 and E + 12 cents) to within 0.2 cent. It passes on the Pi and
  on macOS.
- `engine/tests` (`make test`) renders every effect on a bass riff and several corner cases (all
  parameters at minimum and maximum, random corners, random per-block automation, random block
  sizes 1 to 256) and fails on NaN, infinity, denormals, runaway feedback, a default level more
  than 4.5 dB off, or self-noise on silence. Result: 33 effects, 0 failures, 6 warnings (all in
  the 7-band EQ at extreme boosts). See `engine/tests/results/report.md`.
- `engine/tests/results/cpu.md`: CPU per 64-frame block for every effect on the Cortex-A72, for
  example `cab` 0.3 %, `overdrive` 1.3 %, `amp` 2.3 %, `reverb` 1.9 %, and the neural amp model
  20.7 % of one core.
- `engine/tests/results/bb-render.md`: every effect and factory preset rendered through the real
  host code; the heaviest factory preset (Doom) averages 5.6 % of a period.
