# bassbox architecture

A Raspberry Pi 4 with a RaspiAudio ULTRA++ (WM8960) HAT and a 4" SPI touchscreen, running a
custom real-time engine. The engine and the touchscreen UI are separate processes that talk over a
unix socket. If the UI hangs or crashes, audio keeps running; if the engine restarts, the UI
reconnects.

```
 bass ─► panel IN jack ─► HAT LINE IN (L) ─► WM8960 ADC ─┐
                                                          │ I2S, 48 kHz
 bassbox-engine (C++, CPU3, SCHED_FIFO 80, mlockall)      ▼
   ALSA hw:wm8960soundcard ─► [in gain] ─► slot1 ─► slot2 ─► … ─► slotN ─► [out gain] ─► DAC L+R
        ▲  control thread (CPU0-2): unix socket /run/bassbox/engine.sock, presets, GPIO jack sense
        │
 bassbox-ui (Python, CPU0-2, nice 5)  ──JSON lines──►  engine
   DRM 320x480 RGB565, dirty-rect flushes, XPT2046 touch (evdev)
 (optional) USB footswitch / knobs: evdev HID ─► ui/controls.py ─► same JSON commands
```

## 1. Directory layout

| Path | What |
|---|---|
| `engine/include/effect.h` | the effect interface: a mono, in-place block processor |
| `engine/src/` | host: ALSA I/O, RT thread, chain, IPC server, presets, GPIO jack sense, tuner |
| `engine/effects/` | one `.cpp` per effect, plus shared `dsp*.h` helpers |
| `engine/tests/` | offline render tests, CPU benchmark, NaN/denormal checks |
| `engine/CMakeLists.txt` | builds `bassbox-engine`, `bb-render` (offline WAV renderer), `bb-catalog`, `bb-selftest`; effects are globbed from `engine/effects/*.cpp` |
| `ui/` | touchscreen app (`ui/main.py`), assets, fonts |
| `presets/factory/*.json` | factory presets, synced to `/var/lib/bassbox/presets/factory` at every engine start |
| `config/` | engine, jack-sense and codec-mixer settings, systemd units, device-tree overlay |
| `display/` | panel and touch library, panel init-sequence generator |
| `history/v1-pipedal/` | the first version, built on PiPedal |

Build output never lives in the synced tree: build in `engine/build/` (excluded by `scripts/push.sh`)
and install to `/usr/local/bin`. Device state lives in `/var/lib/bassbox/` (it survives
`push.sh`, which deletes anything else under `/opt/bassbox`).

## 2. Real-time plan

- Kernel command line: `isolcpus=3 nohz_full=3 rcu_nocbs=3 irqaffinity=0-2`. CPU3 is reserved for
  the audio thread and all IRQs stay on CPU0-2. (`nohz_full` and `rcu_nocbs` are inert on the stock
  Pi kernel; `isolcpus` and `irqaffinity` take effect. See [realtime.md](realtime.md).)
- Audio thread: `SCHED_FIFO` priority 80, pinned to CPU3, `mlockall(MCL_CURRENT|MCL_FUTURE)`, stack
  pre-faulted, flush-to-zero set (`FPCR.FZ`), no allocation, locks or syscalls except ALSA read and
  write. ALSA runs in blocking mode on `hw:wm8960soundcard`, S32_LE, 48 kHz, capture and playback
  linked. The shipped setting is 64-frame periods x 3.
- Chain edits: the control thread builds a new immutable chain (all allocation happens there) and
  publishes it through an atomic mailbox; old chains come back through an SPSC ring and are freed
  on the control thread. A slot whose uid survives an edit is reused, so delay and reverb state is
  kept. Removed and moved slots first fade to an exact identity over about 10 ms, then the chain
  swaps and new and moved slots fade in, so add, remove and reorder never click. A preset load
  (no shared slots) runs both chains in parallel for a 10 ms crossfade.
- Parameter changes: per-slot atomic targets plus a change counter. The audio thread re-reads a
  slot's parameters only when the counter moved, which coalesces 60 Hz drags and cannot overflow.
- Meters: the audio thread writes atomics (in/out peak and RMS, per-slot `activity()`, DSP load,
  xrun count); the control thread reads them when a client asks. The audio thread never touches
  the socket.
- UI process: CPU affinity 0-2, `nice 5`, `SCHED_OTHER`. It renders off-screen, diffs against the
  last frame and flushes only dirty rectangles, capped at 30 fps and idle when nothing changes.
  A full-frame SPI flush takes about 81 ms at a 32 MHz SPI clock (the panel is configured for
  48 MHz in `config/display.txt`), and it blocks touch reads on the shared SPI bus, so full redraws
  are reserved for screen transitions and large redraws are flushed in bands.
- Engine priority over UI: a hung or crashed UI does not affect audio; the engine unit restarts
  itself within a second if it dies.

## 3. Engine IPC

Unix socket `/run/bassbox/engine.sock`, newline-delimited JSON. Request:
`{"id": 7, "cmd": "<name>", ...args}`. Response: `{"id": 7, "ok": true, ...}` or
`{"id": 7, "ok": false, "error": "..."}`. Events (unsolicited, no id) look like
`{"event": "<name>", ...}`; a client sends `{"cmd":"subscribe"}` to receive them.
`scripts/bbctl.py` is a small command-line client.

| cmd | args | response / notes |
|---|---|---|
| `catalog` | none | `effects: [EffectInfo as JSON]` (id, name, category, description, tails, params[{id,name,min,max,def,unit,curve:"lin"/"log",type:"float"/"enum"/"bool",options,step}]) |
| `state` | none | `state: State` (below) |
| `add` | `type`, `index` (optional, default end) | `uid` |
| `remove` | `uid` | |
| `move` | `uid`, `index` | |
| `set_param` | `uid`, `param` (id), `value` | cheap; the UI sends up to 30 per second while dragging |
| `set_enabled` | `uid`, `enabled` | |
| `set_mix` | `uid`, `mix` 0..1 | per-slot dry/wet, done by the host for every effect |
| `set_master` | any of `in_gain_db`, `out_gain_db`, `bypass` (bool) | global |
| `meters` | none | `in_peak`, `in_rms`, `out_peak`, `out_rms` (linear 0..1+), `dsp_load` (0..1 of a period), `xruns`, `slots: {uid: activity}`, `clip_in`, `clip_out` (latched 1 s) |
| `jacks` | none | `input`, `output` (bool, plugged) |
| `tuner` | `on` (bool) | while on: output muted, `meters` also returns `tuner: {hz, note, octave, cents, confidence}` |
| `presets` | none | `presets: [{name, factory: bool}]`, `current`, `dirty` |
| `load_preset` | `name` | replaces chain + master |
| `save_preset` | `name` (optional; default current) | |
| `delete_preset` | `name` | factory presets can't be deleted (overwriting one makes a user copy) |
| `subscribe` | none | then events: `jacks` (on change), `state` (on change from another client), `meters` at 30 Hz if `meters: true` given |

Also implemented: `status`, `ping`, `unsubscribe`; `load_state {state, clean?}` (load a State object
without a preset file); `add` accepts optional `params`, `enabled` and `mix`; `catalog` carries
`"version": 1`; `meters` adds `dsp_load_max`; `set_param` accepts an enum label as a string;
`test_tone {on, hz=110, db=-18}` adds a sine after the output gain and before the limiter (20 ms
fades, off after 30 s) so the output path can be proven with the input unplugged.

**State JSON** (also the preset file format, `presets/*.json`, minus `dirty`):
```json
{ "name": "Grit", "master": {"in_gain_db": 0, "out_gain_db": 0, "bypass": false},
  "chain": [ {"uid": 3, "type": "comp", "enabled": true, "mix": 1.0, "params": {"thresh": -18, "ratio": 4}},
             {"uid": 4, "type": "overdrive", "enabled": true, "mix": 0.7, "params": {"drive": 0.6}} ],
  "dirty": false }
```
In presets, `uid`s are ignored and reassigned on load. Unknown params are ignored; missing params
get defaults. The engine autosaves the live state to `/var/lib/bassbox/live.json` (2 s after the
last change, atomic write and fsync on a worker thread) and restores it at boot, so the pedal
powers on exactly as it was left.

Jack sense: GPIO5 is the input jack and GPIO6 the output jack (libgpiod v2, internal pull-ups, a
100 ms debounce); polarity is set in `config/jacks.conf`. When the input is unplugged the engine
fades the output to silence over 20 ms, so an open input cannot hum or pop.

## 4. Effects contract

See `engine/include/effect.h`. Mono, in place, 48 kHz, block of at most 256 frames. Each effect at
its default settings is roughly loudness-neutral. CPU budget for one 64-frame block on a Pi 4
core: cheap effects under 2 %, reverb and amp under 8 %, any NAM model under 35 %. Each effect has
2 to 6 parameters; the first two are the "hero" parameters shown on the card in the board view.
`engine/effects/README.md` covers adding one. Measured CPU per effect is in
`engine/tests/results/cpu.md`.

Categories: dynamics, drive, amp, filter, eq, modulation, pitch, time, utility (33 effects).

## 5. Optional hardware controls

`ui/controls.py` watches `/dev/input/by-id/*` for USB HID devices (a footswitch that sends keys,
or knobs sending REL_DIAL/REL_WHEEL) and maps them through `/var/lib/bassbox/controls.json` (default
`config/controls.json`) to the same JSON commands, for example `KEY_1` toggles slot 1 and
`KEY_RIGHT` loads the next preset. It is hot-plug safe and was exercised with a virtual input
device (`ui/tools/uinput_test.py`).
