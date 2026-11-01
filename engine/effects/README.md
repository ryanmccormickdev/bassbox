# How to add a new effect

Every effect is one `.cpp` file in `engine/effects/`. The engine's CMake globs `engine/effects/*.cpp`
and links them as an OBJECT library, so a new file registers itself and appears in the UI library
on the next build. The interface is `engine/include/effect.h` (don't change it).

## 1. Start from the template
```
cp engine/effects/wip/TEMPLATE.cpp engine/effects/wip/myfx.cpp
```
Work in `wip/`: it is **not** globbed, so a half-finished file can't break the engine build.
Rename the class, the id (`"myfx"`, lowercase, stable forever: presets refer to it) and the params.
Keep everything inside the anonymous `namespace { }`.

Useful helpers (all header-only):
- `dsp.h`: smoothers, biquads (`rbj::`), `Svf`, `OnePole`, `Crossover` (LR4 split), `Oversampler` (2x/4x), `DelayLine`, `Lfo`, `EnvFollower`, `fastTanh`, `flushBlock`.
- `dsp_drive.h`: the drive flow (low keep, blend, auto makeup) and ADAA clippers.
- `dsp_cab.h`: cab voicings.
- `dsp_mod.h`, `dsp_time.h`, `dsp_pitch.h`, `dsp_filter.h`: modulation, delay/reverb, pitch tracker and fast-filter helpers.

## 2. Parameters
- **2 to 6 params.** The **first two are the hero params** shown on the chain card: put the most-used knobs there.
- **Names are 8 characters or fewer** ("Drive", "Low Keep", "Mix").
- **Descriptions are 60 characters or fewer**, so they fit two lines in the library.
- **Real units:** dB (`pDb`), Hz and ms with `pLog` (log knob, min > 0), % 0..100 (`pPct`).
- **Enums** use `pEnum`, with min 0 and max = count-1. Only ever append new options, so saved presets keep their meaning.
- **Bools** use `pBool`.
- **Defaults must be usable and level-matched:** the riff at defaults should come out within ±3 dB RMS of the input.
- **Smooth everything** that clicks when it jumps: gains, cutoffs, delay times. Glide or crossfade.
- For **bass:**
  - Drives keep the sub clean (a crossover or "Low Keep").
  - Modulation leaves the lows dry (a "Low Cut" crossover).
- `tails = true` only for effects that should ring out when bypassed (delay, reverb, looper).

## 3. Real-time rules
| Where | What runs there | Rules |
|---|---|---|
| Control thread | `create()`, `prepare()`, destructor | Allocate and precompute here. |
| Audio thread | `setParam()`, `process()`, `reset()`, `activity()` | No allocation, no locks, no syscalls, no logging, no exceptions, bounded time. |

Also:
- **No NaN, Inf or denormals:** call `flushState()` on filters and `flushBlock(x, n)` at the end of `process()`.
- **Stay stable** at every corner of every param range.
- **CPU budget** per 64-frame block on the Pi: under 2% of a core for cheap effects, under 8% for reverb/amp/cab.

## 4. Test it (Mac or Pi)
```
cd engine/tests
make EFFECTS="../effects/registry.cpp ../effects/wip/myfx.cpp" BUILD=../build-fx/myfx ONLY=myfx test
make EFFECTS="..." BUILD=../build-fx/myfx ONLY=myfx levels bench
```
- **`test`:**
  - Renders the riff, sweeps, low-E and low-B plucks, an impulse, silence and a 0 dBFS burst.
  - Covers defaults, every param at min/max, all-min, all-max, random corners, random automation and random block sizes.
  - FAILs on NaN/Inf/denormals, runaway (tails that grow), level off by more than 4.5 dB, or self-noise.
  - WAVs land in `out/wav/myfx/`; listen to `default_riff.wav` and `param_sweeps.wav` (zipper check).
- **`levels`:** level vs. each param.
- **`bench`:** CPU. On the Pi, pin it with `taskset -c 1` or `-c 2`; core 3 is the audio core.

When it passes with **0 FAILs and 0 compiler warnings**, copy it into place:
```
cp engine/effects/wip/myfx.cpp engine/effects/myfx.cpp && rm engine/effects/wip/myfx.cpp
```
Use `cp`, not `mv`. `mv` keeps the old timestamp, and incremental builds may then skip the file.

Then run the whole suite: `make test`, then `make catalog` (regenerates `engine/catalog.json` for the UI). `make probe plots` draws the response plots.

Check it through the real host:
```
engine/build/bb-render --json '{"chain":[{"type":"myfx","params":{"gain":3}}]}' in.wav out.wav
engine/tests/bbrender_check.sh engine/build/bb-render    # every effect and preset, as a table
```

## 5. Deploy to the pedal
From the Mac:
```
BASSBOX_PI=<user>@<pi-host> scripts/push.sh --setup
```
This syncs the repo to `/opt/bassbox` and runs `setup.sh`. Step 6 of `setup.sh`:
1. incrementally rebuilds `engine/build`;
2. runs `bb-selftest`;
3. installs to `/usr/local/bin`;
4. restarts `bassbox-engine` only if the binary changed.

The new effect then shows up in the UI library. `push.sh` without `--setup` only syncs, so nothing gets rebuilt.

## 6. Factory presets
**Files and format:**
- `presets/factory/*.json` use the State format: `{"name", "master": {in_gain_db, out_gain_db, bypass}, "chain": [{"type", "enabled", "mix", "params": {id: value}}]}`.
- Missing params get their defaults, and unknown ones are ignored.

**How the pedal gets them:**
- They ship to `/opt/bassbox/presets/factory`.
- At startup the engine copies them into `/var/lib/bassbox/presets/factory`, refreshing any that changed.

**On the pedal:**
- Factory presets can't be deleted.
- Saving over one creates a user copy.

**When you add or edit one:**
- Render all of them with `make presets` (in `engine/tests`).
- Keep each within about ±1.5 dB of the input, so switching presets doesn't jump in level.
- Fix the level with the effects' Level params, or the master `out_gain_db`.
