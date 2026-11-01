# bassbox pedal enclosure: design (r1, 2026-10-19)

The requirements: a printable, sturdy, **small** box for the pedal (Pi 4, RaspiAudio ULTRA++ HAT,
4.0" SPI touchscreen, two 1/4" jacks), so the wiring cannot short or come unplugged in a backpack.
The screen is on top; **all Pi 4 IO** is exposed on the sides (USB pedals plug into the USB-A
ports, power is USB-C). The thickness of the dupont wires is accepted as it is, and there is no
padding. Sticky feet go on the bottom. The inputs were the display's drawing, the Pi 4 mechanical
drawing, RaspiAudio's 3D model of the HAT, photos, and caliper measurements; the display rotation
and pin map are in [`docs/hardware.md`](../docs/hardware.md).

All numbers live in `cad/gen/pedal/dims.py`, which both the bought-part models and the printed
parts import. This file explains them.

## Frame B

Origin at the inner corner: USB-end wall inner face (X = 0), IO-side wall inner face (Y = 0), floor
top (Z = 0). +X runs to the jack end, +Y to the HAT-header side, +Z up (screen). Cavity
112.44 x 62.14; walls 2.0; outer box **116.44 x 66.14 x 48.5** (Z -2.0 to 46.5), R3 vertical
corners.

Orientation for the player: the UI is portrait with rotation 180, which is upright when the
display's 14-pin header end is at the top. So the jack end (+X) is the UI top, the player sits at
the USB end, the IO side (-Y: USB-C, 2 x micro-HDMI, audio) is on the player's **right**, and the
HAT-header side on the left.

## Stack (Z, frame B)

| Z | what |
|---|---|
| 46.5 | top surface (bezel plate 1.6) |
| 44.9 | bezel underside |
| 44.55 | touch-glass top (0.35 air gap at nominal; 0.2 to 0.7 over the stack tolerance) |
| 40.5 / 38.9 | display PCB front / back (bosses 4.4 long hold it) |
| 38.9 .. 35.9 | display back: SMD up to 3.0, microSD socket 1.9, header plastic 2.5 |
| 36.4 .. 22.4 | display header duponts (14 mm housings), wires turn below |
| 35.5 / 34.1 | Pi 4 bottom face / **top face = lid-base split plane** (gap to the display 3.4: room for M2.5 heads, kapton) |
| 34.1 .. 18.1 | Pi ports hang from the top face: USB 16.0, Ethernet 13.5, audio 6.0, USB-C 3.2, HDMI 3.0 |
| 24.19 / 22.49 | ULTRA++ underside / component face (9.91 above the Pi: 2.5 + 6.9 + 0.51, vendor model) |
| 19.48 .. 5.48 | HAT pass-through duponts (plastic 2.5, housings 14) |
| 5.48 .. 0 | dupont wires turn along the floor (5.5 mm; they arc 6.4 mm free on the loose stack, so they are pressed about 1 mm) |
| 0 / -2.0 | floor top / bottom |

Check against a caliper measurement: on the loose stack, glass top to the top of the dupont wires
was 45.9. Here glass top (44.55) to the floor (0) is 44.55, the same stack with the wire arcs about
1.4 mm flatter.

## Layout (top view)

- **Pi 4 upside down**, top face toward the floor, USB/Ethernet end against the X = 0 wall, IO edge
  against the Y = 0 wall (0.2 gaps). Port faces end up flush with the outside: USB/Ethernet overhang
  2.8 through a 2.0 wall (0.6 proud); the IO-edge ports overhang 1.2 into a **recessed IO panel**
  (0.8 deep outside, wall 1.2 there) so plugs seat fully. Openings are sized for plug overmolds.
- **ULTRA++ is a 65 x 30 board on the header half only** (vendor model), so the Pi's two IO-side
  mounting holes are uncovered: two tall posts (Z 0 to 34.1) carry the Pi there with M2.5 x 8 screws
  driven from the Pi's underside before the lid goes on. Two short posts under the HAT's holes
  (Z 0 to 22.49) support the header side (optional M2.5 x 20 screws with 9.85 mm printed spacers).
- **Display** sits in the lid, 4.2 from the USB-end wall (`DISPLAY_X0`), held by 3 x M2.5 x 6 into
  bezel bosses at its corner holes, plus a locating pin at the fourth corner (that corner is over the
  Pi's USB2 port, where a screw head would land on through-hole tails). The 4.2 offset moves the
  display's header duponts toward the jack end to open the jack bay, and nearly centres the window
  (borders 15.2 and 16.9).
- **Jacks**: side-exit, back to back across the jack bay (X 86.2 to 108.4) between the HAT's terminal
  block (X 85.7) and the display's header duponts (X 108.97): axis X 97.3, Z 24.4 (22 mm below the
  top, about mid-height). IN on the player's right (IO side), OUT on the left, the guitar-pedal
  convention. Bushing hole 10.2 (3/8-32 bushing; the front metal ring scales to about 9.7 against
  the 16.4 nut), an 18 mm spot face outside, anti-rotation ribs inside either side of the body. Lugs
  face the floor. The display's wires run down past the jack-end wall, between the two jack bodies,
  and along the floor to the HAT header.
- **Wire routes** kept clear: display duponts (X 108.97 to 111.51, Y 13.3 to 48.9, Z 22.4 to 38.9)
  down to the floor at the jack end; the gap between the jacks (Y 24 to 38); the +Y channel between
  the Pi/HAT edge (Y 56.3) and the +Y wall (62.14); along the floor.

## Parts

Printed in PETG (the Pi 4 runs warm inside a closed box; PLA creeps near 55 C):

1. **Lid** (bezel): top plate 1.6 with the screen window (LCD active area + 0.4, R1 corners,
   0.8 x 45 degree chamfer on the top edge), skirt down to the split plane (Z 34.1), 4 bosses
   (3 tapped, 1 pin), 4 snap windows in the skirt, a rabbet, notches where the port openings cross
   the split, and "bassbox" engraved on top at the jack end. Prints face down, no supports.
2. **Base** (tub): floor and walls up to the split; port openings as open-top slots (the Pi drops
   in from above; one shared slot for both micro-HDMI plugs, a U for audio); 2 jack holes; 4 floor
   posts (the HAT ones Ø4.6, clear of a HAT SMD part); 4 snap tabs rising into the lid's windows;
   vents (a floor grille under the Pi and HAT, slots in the +Y wall and the jack-end wall); foot
   marks under the corners. Prints floor down, no supports.
3. **Spacers** (optional, 2): Ø5 / Ø2.8 x 9.85 tubes between the Pi and HAT at the header-side holes.

Hardware: 2 x M2.5 x 8 (Pi), 3 x M2.5 x 6 (display), optionally 2 x M2.5 x 20 (header side), the
jacks' own nuts, sticky feet. Kapton stays on the Pi's underside.

## Assembly

1. Jacks into the base from inside, bushing out through the side holes, body square between the
   ribs, lugs down; washer and nut outside.
2. Pi and HAT (wired to the jacks and the display) lowered into the base, HAT down: the USB/Ethernet
   and IO ports drop into their slots, the IO-side holes land on the tall posts. 2 x M2.5 x 8
   through the Pi.
3. Display face down into the lid, pin in the USB-end corner hole, 3 x M2.5 x 6 from the back.
4. Fold the display wires toward the jack end, swing the lid over, press until the 4 snaps click.
   To open, push the snap barbs in through their windows in the lid's skirt (2 on the +Y side,
   2 at the jack end).
5. Feet on the marks.

## As built (r1)

The generator in `cad/gen/pedal/enclosure.py` departs from the first layout above in these places;
each is explained where it is built.

1. **The snap tabs are on the base and the windows in the lid's skirt** (first design: tabs on the
   lid). A tab on the lid hangs inside the wall below Z 38.4, so in plan it lies 1.2 inside the
   display PCB's outline, and the display could not be put into the lid past the +Y tabs: the PCB
   is 61.74 wide and the clear width at the tabs is 60.59, so tilting it fails, and the jack-end
   tabs could only be passed by sliding the PCB under the locating pin. A base tab (6.0 wide, 1.4
   thick, 12 mm free length, tip at Z 38.4, 0.5 under the PCB) hooks 0.8 mm into a 6.6 x 1.96
   window in the skirt. Strain 1.17 %, about 3.8 N tip force, about 4.8 N per snap to close.
2. **One opening for both micro-HDMI plugs** (X 39.9 to 65.0). Separate openings leave a 1.9 x 1.2
   web (2.3 mm^2) standing free above the openings' bottoms, which would be a sliver.
3. **The audio opening is a U** (a Ø8 semicircle at the bottom, straight sides up to Z 35.1): the
   upper half of a round hole in the base would be a 41 degree overhang.
4. **USB-C and HDMI opening corners are R0.8.** With R1.5 the square-cornered plug models touched
   the corners; anything up to R1.19 clears them.
5. **Jack holes are teardrops with 50 degree flanks** (apex Z 32.3), and the spot faces' rims flare
   at 50 degrees (a Ø18 flat seat 0.4 deep, Ø18.95 at the face); a plain counterbore's ledge drew
   support pillars in the slicer probe.
6. **HAT posts are Ø4.6** (the layout says 5.0) because of a top-face SMD part on the HAT 2.55 mm
   from the hole at X 81.7.
7. **Spacers are 9.85 long** (the gap is 9.91): a spacer must never lift the HAT.
8. **No rabbet over the USB-end slots and pillars, or over the IO panel plus 1.5 each side.** Over
   the IO panel the wall is 1.2, which leaves no room for a 1.0 lip and a skirt band; those
   stretches are butt joints.
9. **The barbs' square retention faces print as 0.8 flat ledges**, the one overhang left. A face
   that resists the lid lifting faces down in the print pose of whichever part carries it. Carrying
   the barb on two end gussets (the base barb then printed as a 3 mm bridge) moved the cantilever
   into the lid's window and shrank the hook from 6.0 to 2.4 wide; stepped ledges (2 x 0.4, 4 x 0.2)
   were still flagged by the slicer. Both were reverted.
10. **The lid engraving is "bassbox" only.** The first version also engraved IN and OUT near the jack
    end; they were dropped after the first review.

## Checks

`python -m cad.gen.pedal` runs these and fails if any does not hold (`reports/enclosure-report.json`
has the numbers):

- each part is one valid solid and a watertight single-body mesh;
- wall thickness at 19 sampled places is at least 1.2 mm (the rabbet's two halves are 1.0 and 0.85
  by design); the thinnest walls are the IO panel (1.2) and the web between USB-C and HDMI (1.2);
- knife edges: 0 on all three parts; small section pieces (0.5 mm steps in the print pose): 0; the
  only hits are 5, all in the lid's engraving, which the sliver rule allows;
- overhangs in the print pose (B-rep scan, 50 degree rule): only the 45 degree bed chamfers, flat
  roofs that bridge (vent slots, snap windows, foot rings, letters) and the four barb ledges.
  PrusaSlicer's support probe with automatic supports forced on finds nothing on the lid; on the
  base it adds about 8.6 k support moves, all under the four barbs;
- lid and base overlap 0.000 mm^3; the snap tabs clear the lid by at least 0.064 mm; the glass gap
  is 0.35;
- the clash harness (`cad/gen/pedal/assembly.py`) checks every printed part against simplified
  models of the display, Pi 4, HAT, jacks, screws, mated cable plugs and keep-out zones (dupont
  wires and their bends, heatsink, jack lugs, solder tails): printed against bought parts is clean.
  Nearest gaps: spacer to the HAT 0.06, lid to the Pi's edge 0.20, lid to the glass 0.35, any
  face to a plug 0.30.

Print estimate (PrusaSlicer 2.9.6, Creality Ender-3 V2 Neo profile, PETG, 0.20 mm layers, 4 walls,
5 top and bottom layers, 40 % gyroid, no supports): one plate with the lid, base and two spacers,
**7 h 30 min and 63.0 g** (20.6 m of filament): base 48.2 g, lid 14.9 g, spacers about 0.2 g.

## Open risks

- **Jack body size and bushing** come from photos only: the bay leaves about 0.75 per side in X, and
  the 10.2 hole assumes a 3/8-32 bushing. Ream the holes if the bushing is bigger. The bushing
  length past the wall (3) is shorter than a washer and nut seated on the spot face need (4.2);
  check the real jack's thread.
- **Heatsink.** A 14 x 14 x 6 heatsink on the SoC hits the HAT's middle 3.5 mm jack and its left
  speaker: there are only 2.78 mm of room above the SoC at the jack and 4.46 to the speaker. A
  heatsink up to 2.7 mm fits; the model keeps the 6 mm keep-out and records the clash as a finding.
- **Display SD socket and the Pi's solder tails**: the display's microSD socket (1.9) sits over the
  Pi's audio and HDMI shield tails (1.6); 3.5 mm are needed and 3.4 are available, so the envelopes
  overlap 0.1 mm. The real tails are probably shorter.
- **Pi-HAT stack** is from the vendor model, assuming the HAT's female header bottoms on the Pi's
  plastic. The tall IO posts set the Pi's height; the HAT posts only support.
- **Dupont wire room** (5.5 mm under the HAT housings) is from one caliper reading plus standard
  housings.
- **Glass stack** is 4.05 by the drawing and 3.7 measured; the 4.4 mm bosses keep the glass off the
  bezel either way.
- **Barb ledges** may droop a little; a drooped ledge still hooks, a stringy one wants a file stroke.
- **Lid peel**: the snaps sit only on the +Y and jack-end walls (an IO-side tab would hit the Pi), so
  the IO / USB-end corner is held only by the rabbet's friction and by plugs in the IO openings.
  The base's tabs stand 4.3 above its rim, so handle the base with care until the lid is on.
- **Display pull tape.** The display drawing shows a protective-film pull tape that would cross the
  +Y wall; it comes off with the film and is not modelled.
