# bassbox UI design

320x480 portrait, RGB565 SPI panel (TN, viewed off-axis in an enclosure), resistive touch.
This file is the UI contract for Phase B. The mockups are in `ui/mockups/` (`dir{A,B,C}_{board,edit,library}.png`, plus
`contact_ABC.png`), made by a throwaway generator that is no longer in the tree. They are panel-accurate: dithered RGB565, exactly what the LCD gets.

## 1. Panel facts that drive the design
- **Dark end is crushed and the panel has a blue cast off-axis.** Grays 0–64 all look like the same navy, neutral gray reads blue, and orange drifts salmon.
  - The background is **pure black**.
  - Surfaces separate by an **outline** (≥ gray 0x3E) plus a lifted fill, never by a subtle gray step.
  - Accents are high-saturation; warm hues are pushed toward red or yellow.
- **Flush cost is linear in the dirty area** (DRM dumb buffer + DIRTYFB, XRGB8888, measured on the Pi):
  - 64x64: 2.2 ms;
  - 320x80: 13.5 ms;
  - full frame: 81 ms.

  Animations must be small-area: comets on the cable, knob arcs, meters. Screen changes are a cut, or a ≤ 4-frame wipe.
- Text ≥ 12 px everywhere, and ≥ 14 px for anything you need to read while playing. Secondary text is ≥ #A9B0BC on black.
- Taps near the left edge read 7–15 px further left. No interactive target starts before x = 8. Hit targets are ≥ 44x44, and edge targets are padded inward.

## 2. Screens and navigation
```
            ┌──────────── Board (home) ─────────────┐
 tap card ─►│ Edit (pedal)  ◄─ back / Done          │
 + Add    ─►│ Library ── tap tile ─► inserts, opens Edit of the new pedal
 title    ─►│ Presets ── load / save / save-as (name generator)
 TUNER    ─►│ Tuner (engine mutes output while open)
 SETTINGS ─►│ Settings: in/out gain, touch recal, CPU/DSP load, xruns, version
            └── splash ("connecting…") at boot / "reconnecting…" banner if the engine socket drops
```
- **Persistent header (68 px) on every screen:** IN jack at the top left, OUT jack at the top right, each with a label and an 8-segment meter. Context title in the middle. This keeps the signal visible while editing.
  - Board: preset name, with a dirty dot.
  - Edit: pedal name.
  - Library: "Add pedal".
  - On sub-screens, a back chevron sits left of the title (target x 56–104). The jacks never move.
  - Tapping a jack opens that side's gain (in gain / out level) in Settings.
- **Footer (48 px), board only:** BYPASS (global), TUNER, PRESETS, SETTINGS.
  - Edit footer: REMOVE, ON/OFF, RESET, DONE.
- **Back** is a back chevron, or a swipe right (drag > 80 px with mostly horizontal motion) on any sub-screen.

## 3. Gestures (one recogniser feeds all screens; the debug socket injects into the same pipeline)
| Gesture | Recognised as | Board | Edit | Library / lists |
|---|---|---|---|---|
| tap | press ≥ 60 ms, moved < 10 px, released | card → Edit; power → toggle; + → Library | footer actions | tile → insert |
| double-tap | 2 taps < 350 ms, < 24 px apart | - | control → reset to default | - |
| drag | moved ≥ 10 px | vertical scroll if chain overflows | **value drag**: vertical, relative, 200 px = full range | scroll |
| long-press | held ≥ 500 ms, moved < 10 px | pick up card → reorder (drag), drop to place; drag onto footer "remove" zone deletes | fine mode toggle | preview description |

- Touch filtering is reused from `display/screen.py`:
  - the affine calibration;
  - the release-bounce guard: ignore a down within 60 ms of an up, and drop presses < 60 ms;
  - drift trim: the position at release is the median of the press minus its last 3 samples.
- Value drag:
  - It starts in whatever control cell was pressed. The cell is the hit area, not the drawn knob.
  - Speed is relative: up = increase, 200 px = min→max on the param's curve (log for Hz/ms).
  - Holding still for 600 ms, or long-pressing first, enters **fine mode** (×¼), shown by a chip. Enum params step every 32 px.
  - A **large readout** shows while dragging: in the knob centre for hero knobs, in the header for small ones.
  - `set_param` is sent at ≤ 30 Hz, coalesced so only the latest value is in flight.
- Every action is reversible: removing a pedal shows an "Undo" snackbar for 4 s.

## 4. Layout grid
- 4 px base unit, 8 px rhythm, 12 px outer margins. Header 0–68. Content 68–432. Footer 432–480.
- **Board, Direction A:**
  - The signal line sits in gutters at x = 20 (down, left) and x = 300 (up, right), with a U-turn at y = 424.
  - Cards are x 40–284 (244 wide), 86 tall, with a 10 px gap.
  - Up to 3 cards show full size with an "Add pedal" slot; 4–8 cards compress to 44 px rows (name, hero 1 value, power).
  - Past 8, the list scrolls with momentum and the line scrolls with it.
- **Edit:**
  - Hero row: 2 knobs, r = 54, cells 160x150.
  - Secondary row: 3 knobs, r = 32, cells 106x110.
  - 7+ params use a second page indicator.
  - Mix is always a full-width slider.
- **Library:**
  - The chip row (h 34, scrolls horizontally) sits at y 80.
  - The 2-column grid of 145x104 tiles has a 10 px gutter and scrolls vertically.
  - "Insert at N" in the header kicker is set by where you tapped "+"; the default is the end of the chain.

## 5. Type scale (static OFL instances, `ui/fonts/static`, built by `tools/make_fonts.py`)
| Role | A "Signal Line" | B "Pedalboard" | C "Orbit" |
|---|---|---|---|
| Display / title | Inter Display 19/650 | Archivo Expanded 15–26/800–900, caps | Unbounded 22–30/800 |
| Card name | Inter 17/650 | Archivo Expanded 13–15/900 | Space Grotesk 15/700 |
| Body / description | Inter 12/450 | Inter 12/450 | Space Grotesk 12/500 |
| Label (caps, +1.2 tracking) | Inter 12/600 | Archivo Expanded 12/700 | Space Grotesk 12/600 |
| Values | JetBrains Mono 14–30/600–700 (tabular by nature) | JetBrains Mono 12–18 | Space Grotesk 14/700 |

Text is rasterised by Pillow/FreeType with BASIC layout.
- raqm shaping is off: HarfBuzz positions glyphs unhinted against FreeType's hinted advances, which leaves uneven gaps.
- Masks are cached per (string, font) and composited with cairo.

## 6. Colour
- Base colours:
  - background #000000;
  - card #17191E with a 1.5 px #3E434D outline;
  - text #F4F6F8 / #A9B0BC / #7C8491 (tertiary, non-essential only);
  - neutral signal #DCE2EA.
- Meter segments: green #3CF06E, yellow #FFD500 at the 7th segment, red #FF3B30 at the 8th segment. Clip latches the red segment for 1 s.

| Category | Accent | Notes |
|---|---|---|
| dynamics | #00E0C0 | cyan-teal |
| drive | #FF8A00 | pushed toward yellow (plain orange goes salmon on the panel) |
| amp | #FF3B30 | red |
| filter | #FF45C8 | magenta |
| eq | #FFD500 | warm yellow (plain yellow goes lime) |
| modulation | #A472FF | violet |
| pitch | #3CF06E | green |
| time | #2F9BFF | blue |
| utility | #C2CAD6 | silver |

- **Signal colour:** each cable segment takes the accent of the last *active* pedal upstream. Neutral leaves IN, and OUT shows what the sound has become.
- Bypassed pedals:
  - are drawn at ~45% contrast with a hollow node;
  - get a dashed stub, while the line runs straight past them.
- Gradients (glows, knob caps) are ordered-dithered (4x4 Bayer, position-stable, so re-rendering identical content produces identical pixels and the dirty-rect diff stays small) before RGB565 truncation. That avoids banding.

## 7. Motion (all ≤ 30 fps, small dirty rects, 0% CPU when idle)
- **Signal comets:** bright heads with fading tails travel the cable.
  - Spawn rate and brightness follow the live RMS of the segment's level: the IN meter before the first pedal, then the per-slot `activity()`, then the OUT level.
  - Silence means no comets and no redraws.
  - Each comet dirties only its own ~24x24 box.
- **Jack plug in** (~350 ms):
  1. The plug slides up into the socket (ease-out, a 2 px overshoot, then a snap).
  2. The contacts light and a glow ring pulses at the mouth.
  3. The cable straightens.
- **Jack unplug** (~400 ms):
  1. The plug pops 26 px down with a 12° tilt.
  2. The cable sags into a slack curve and goes gray.
  3. **IN unplugged:** the whole chain dims to 40% (the engine mutes), with an "Input unplugged" hint under the header.
  4. **OUT unplugged:** only the OUT side grays.
- **Power toggle:** the node fills or empties, and the line re-routes, with a 150 ms crossfade on that card only.
- **Reorder:** the lifted card follows the finger at 30 fps (dirty = old ∪ new rect), and other cards slide in 4 frames.
- **Screen change:** a cut, with an optional 3-frame wipe for the header title. No full-screen slides.

## 8. Components (retained-mode widget tree; each widget knows its rect, draws itself, marks itself dirty)
- `Header`, `JackIcon` (insert/tilt/glow/live), `Meter`
- `SignalLine` (path, segment colours, comets), `PedalCard` (full/compact), `AddSlot`
- `Knob` (hero/small, arc + end-cap; Direction B's chunky knurled variant), `Slider` (mix), `Fader` (Direction C), `EnumSelector` (segmented), `Toggle`
- `ChipRow` (h-scroll), `TileGrid` (v-scroll), `List` (presets), `NameGenerator` (adjective + noun, with shuffle)
- `FooterBar`, `Snackbar` (undo), `Banner` ("reconnecting…", "input unplugged"), `Splash`
- `TunerView`: a big note name, a cents needle arc, and a ±50 ct strip in green within ±3 ct.

## 9. Directions explored (Phase A)
- **A "Signal Line":** a Swiss / transit-map look.
  - Cards hang off a live signal line, as stations, that runs down the left, U-turns under the chain and climbs to OUT.
  - It is flat and crisp, uses Inter and JetBrains Mono, and has ring knobs.
  - Scales cleanly from 1 to 8+ pedals, and is the most legible at an angle.
- **B "Pedalboard":** a top-down board.
  - Real stompbox enclosures in category colours are joined by rubber patch cables in a U: down the left column, across the bottom, up the right column.
  - Chunky knurled knobs, LEDs and footswitches. Archivo Expanded silkscreen type.
  - The most distinct of the three. Densest at 6–8 pedals (2x4 grid, 84 px pedals).
- **C "Orbit":** the signal is a luminous horseshoe track from IN round to OUT, and pedals are stations on it.
  - The hub inside the U carries big Unbounded type. Edit uses tall faders, since vertical drag is the natural axis.
  - The most graphic. It needs 3-letter codes at 6+ pedals.

## 10. Phase B implementation (chosen: A board/library + B pedal-face edit)
- Code:
  - `ui/main.py` is the entry point: priority, backends, navigator, debug handlers.
  - `ui/core.py`: widgets, dirty rects, gestures, the select() loop, the debug socket, the touch thread.
  - `ui/model.py` and `ui/engine_client.py`: engine state, optimistic edits, non-blocking reconnect, coalesced set_param.
  - `ui/chrome.py`: the header with animated jacks and meters, footer, buttons, snackbar, banner, dialog.
  - `ui/screens/*`: board, edit, library, presets with save-as, tuner, settings with calibration, splash.
  - `ui/values.py`: the single value formatter.
- Display: DRM dumb buffer (RGB565) with one DIRTYFB call per merged rect.
  - Each frame is rendered by cairo into XRGB8888, then a 4x4 Bayer LUT dither converts it to RGB565.
  - Static widgets are cached layers (`Widget.cache`). Comets are drawn on separate layers, so only their own boxes flush.
- Mix: if the effect has its own `mix` param, the face's MIX strip controls it. Otherwise the strip controls the host slot mix (`set_mix`).
- Paging instead of scrolling (library, presets): one flush per page on the SPI panel.
- Idle behaviour: with no signal the UI polls `meters` at 3 Hz, and at 20 Hz while there is signal or the tuner is open. Comets run at 20 fps only with signal. Measured idle cost: about 0.3% CPU and zero flushes.

## 11. Remote control (`ui/tools/inject.py`; socket `/run/bassbox/ui-debug.sock`)
```
ssh <user>@<pi-host> python3 /opt/bassbox/ui/tools/inject.py <cmd>
  tap X Y | doubletap X Y | drag X1 Y1 X2 Y2 [MS] | longpress X Y [MS] [X2 Y2 [DRAG_MS]]
  jack in|out 0|1          (simulated jack event; the engine's next real jack event overrides it)
  screenshot /tmp/x.png    (the exact RGB565 frame on the glass) | state | stats [N] | wait | refresh
  goto board|library|presets|saveas|tuner|settings|calibrate|edit [UID]
  engine '{"cmd":"state"}' (raw engine passthrough)
```
- Every command returns once its result is on screen.
- Injected gestures enter the same recogniser as the panel. The touch thread's bounce and drift filtering is the only stage they skip.
- Other tools:
  - `tools/shots.py OUTDIR [--cleanup]` captures every screen.
  - `tools/timing.py` measures tap, drag and idle.
  - `tools/perf.py SECS` reports CPU and frame stats.
  - `mock_engine.py --socket PATH` is the mock engine.
- Headless on the Mac: `python3 ui/main.py --backend headless --engine /tmp/bassbox-mock.sock --debug /tmp/bassbox-ui-debug.sock`.

## 12. Phase C additions
- **Values** (`values.py`):
  - Unitless 0..1 params show as % (wah Position 68%).
  - `OVERRIDES[(effect, param)]` maps special values to labels, e.g. wah Auto 0 = "Manual" and several "0 = Off" params.
  - Bools show ON/OFF. Enums show their option label.
- **Edit face**:
  - Enum params are labelled segmented switches: horizontal when the segments fit, otherwise a stacked list. Tap a segment to select it, or drag to step.
  - The looper's `state` param becomes REC / PLAY / DUB / STOP buttons. The engine has no Clear state.
  - Wah Position is a vertical heel/toe treadle.
  - The header shows "◀ MOVE n/m ▶" and has no back chevron. DONE goes back, as does a swipe right on the face background.
- **Library**:
  - Home is a grid of category tiles (All + 9), each with a colour chip and an effect count.
  - A category opens a list of full-width rows (chip, name, full description up to 3 lines), packed into pages.
  - A swipe right returns to the categories.
- **Tuner**:
  - String row B0 E1 A1 D2 G2, with the nearest string highlighted.
  - A full-width ±50 ct arc with ♭/♯ and a needle.
  - The note is 124 px Archivo, with the octave as a subscript.
  - Within ±3 ct it locks: an IN TUNE pill and the arc turns green.
  - Cents and Hz readouts at the bottom. Idle shows a dim dash and "PLAY A STRING".
- **Jacks** (`art.jack`, `art.jack_pose`):
  - The socket hangs below a hex nut on the pedal; the plug enters from above and its cable runs off the top edge.
  - The plug shows tip (brass), insulator, ring, insulator, sleeve, knurled barrel with a colour band, and strain relief.
  - Plug-in: the plug drops, snaps, the ring glows and the cable settles. Unplug: the plug pops out with overshoot, tilts, and the cable goes slack and grey.
  - The full socket appears on the board only. Sub-screens use a compact jack.
  - `tools/jack_strip.py` renders the animation frames.
- **Presets**:
  - The current preset has a highlighted row plus a green (loaded) or orange (edited) bar.
  - Category-coloured dots show each preset's chain; hollow dots are bypassed pedals. They are read from the preset files.
- **Hardware controls** (`ui/controls.py`):
  - Rescans /dev/input every 2 s and reads every device except the touchscreen, without grabbing it.
  - Mapping lives in `/var/lib/bassbox/controls.json` (default `config/controls.json`):
    - KEY_1..8 toggle slot n;
    - KEY_LEFT/RIGHT and PAGEUP/DOWN step through presets;
    - KEY_B toggles bypass; KEY_T opens the tuner; KEY_ESC goes to the board;
    - REL_DIAL/WHEEL and ABS_* adjust the last knob touched on the edit screen.
  - Test: `sudo python3 ui/tools/uinput_test.py` (virtual footswitch + dial).
- **Touch tuning**:
  - `config/touch_tuning.json`, overridden by `/var/lib/bassbox/touch_tuning.json`. Restart bassbox-ui after editing.
  - Keys: bounce_s, drift_samples, min_samples, drag_px, long_press_s, double_tap_s, double_tap_px, edge_correction_px. Each is documented in the file.
- **Tools**:
  - `tools/sweep.py` renders every effect's card and face.
  - `inject.py screenshot` streams the PNG over the socket and writes it as the calling user, so no sudo is needed.

## 13. Robustness (review fixes)
- **Touch during full redraws:** any rect above 320x120 px is flushed as horizontal bands, one DIRTYFB call each (`core.BAND_AREA`). A full frame is 4 calls, so the XPT2046's SPI messages interleave instead of waiting out a 60+ ms transfer.
- **SYN_DROPPED** (`display/screen.py`):
  - After the kernel reports an overrun, events are discarded until the next SYN_REPORT.
  - BTN_TOUCH is then re-read with EVIOCGKEY, and ABS_X/Y with EVIOCGABS.
  - An interrupted press ends as `cancel` (release feedback, no tap). A touch still held becomes a fresh press.
- **Debug socket:** mode 0660, group = the owner group of /opt/bassbox, so `inject.py` works without sudo. Replies are buffered and non-blocking, and a client with more than 8 MB pending is dropped.
- **Memory:**
  - The text-mask cache is an LRU (512 entries or 2 MB).
  - `Widget.clear()` releases cached layers recursively; the shared header is `persistent`.
  - `gc.collect()` runs on each screen change, folded into the full redraw.
  - Debug command `{"cmd":"mem"}` reports fonts, masks, widgets, cached layers and RSS.
- **Looper:**
  - CLEAR (state index 4) is sent once, then the UI sets Stop (0) itself, because the effect can't write its param back.
  - The CLEAR button appears only when the catalog offers the 5th option.
