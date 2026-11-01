# bassbox effects test report

Input: `samples/test-bass-riff.wav` (-18.6 dBFS RMS, -6 dBFS peak) plus sweeps, plucks (E1 41.2 Hz, B0 30.9 Hz), impulse, silence, 0 dBFS burst.
Each effect: defaults on all signals, every param at min and max, all-min, all-max, random corners, random per-block automation (3 seeds), random block sizes 1..256.
FAIL = NaN/Inf/denormal output, runaway (peak > +60 dBFS, or output still growing after the input stops), level at defaults off by > 4.5 dB, or self-noise on silence > -60 dBFS at defaults. Warn = level off by 3..4.5 dB, riff peak > +6 dBFS at defaults, peak > +24 dBFS at an extreme, non-tail effect still ringing.

| id | name | level dB (riff, defaults) | out peak dBFS | worst peak dBFS (any setting) | silence out dBFS | burst tail dBFS | rand-block diff dB | max activity | bad samples | result |
|---|---|---|---|---|---|---|---|---|---|---|
| gate | Noise Gate | -0.0 | -6.0 | -0.0 | -inf | -inf | exact | 1.00 | 0 | pass |
| comp | Compressor | +0.4 | -2.6 | +15.7 | -inf | -inf | exact | 1.00 | 0 | pass |
| limiter | Limiter | -0.0 | -6.0 | +0.0 | -inf | -inf | exact | 0.08 | 0 | pass |
| overdrive | Overdrive | -0.0 | -7.3 | +20.5 | -inf | -inf | exact | 0.63 | 0 | pass |
| distortion | Distortion | +0.1 | -7.3 | +19.7 | -inf | -inf | exact | 0.16 | 0 | pass |
| fuzz | Fuzz | -0.4 | -9.6 | +19.4 | -154 | -inf | exact | 0.20 | 0 | pass |
| octafuzz | Octave Fuzz | -0.7 | -7.6 | +19.7 | -inf | -inf | exact | 0.37 | 0 | pass |
| bitcrush | Bitcrusher | -0.0 | -5.7 | +19.7 | -inf | -inf | exact | 1.00 | 0 | pass |
| amp | Amp | +0.7 | -6.7 | +17.4 | -inf | -inf | exact | 1.00 | 0 | pass |
| cab | Cab Sim | +0.2 | -6.2 | +23.8 | -inf | -inf | exact | 1.00 | 0 | pass |
| nam | Neural Amp | -0.0 | -5.9 | +13.1 | -inf | -inf | exact | 1.00 | 0 | pass |
| env_filter | Envelope Filter | +0.2 | -5.1 | +0.8 | -inf | -inf | exact | 0.99 | 0 | pass |
| wah | Wah | -1.9 | -8.2 | +5.3 | -inf | -inf | exact | 0.50 | 0 | pass |
| synth_filter | Synth Filter | -1.4 | -7.5 | +5.0 | -inf | -inf | exact | 0.67 | 0 | pass |
| eq3 | 3-Band EQ | +0.0 | -6.0 | +29.2 | -inf | -inf | exact | 1.00 | 0 | pass |
| geq7 | Graphic EQ | -0.0 | -6.0 | +40.4 | -inf | -inf | exact | 1.00 | 0 | warn |
| cut | Filter | -0.1 | -5.5 | +7.2 | -inf | -inf | exact | 1.00 | 0 | pass |
| chorus | Chorus | -0.4 | -6.3 | +8.9 | -inf | -inf | exact | 1.00 | 0 | pass |
| flanger | Flanger | -0.9 | -7.6 | +7.7 | -inf | -inf | exact | 1.00 | 0 | pass |
| phaser | Phaser | -0.2 | -7.1 | +9.1 | -inf | -inf | exact | 1.00 | 0 | pass |
| tremolo | Tremolo | +0.1 | -5.5 | +4.0 | -inf | -inf | exact | 1.00 | 0 | pass |
| vibrato | Vibrato | -0.1 | -6.2 | +8.7 | -inf | -inf | exact | 1.00 | 0 | pass |
| univibe | Uni-Vibe | -0.7 | -7.4 | +8.2 | -inf | -inf | exact | 1.00 | 0 | pass |
| ringmod | Ring Mod | -0.2 | -5.2 | +11.8 | -inf | -inf | exact | 1.00 | 0 | pass |
| octaver | Octaver | +0.6 | -6.4 | +13.3 | -inf | -inf | exact | 1.00 | 0 | pass |
| pitch | Pitch Shifter | +0.1 | -5.3 | +3.2 | -inf | -inf | exact | 1.00 | 0 | pass |
| subsynth | Sub Synth | +0.4 | -4.2 | +6.8 | -inf | -inf | exact | 1.00 | 0 | pass |
| delay | Delay | +0.2 | -5.8 | +7.0 | -inf | -135 | exact | 1.00 | 0 | pass |
| tape | Tape Echo | +0.2 | -5.7 | +6.9 | -inf | -158 | exact | 1.00 | 0 | pass |
| reverb | Reverb | +0.1 | -6.3 | +16.4 | -inf | -168 | exact | 1.00 | 0 | pass |
| shimmer | Shimmer | +0.0 | -6.4 | +9.7 | -inf | -73 | exact | 1.00 | 0 | pass |
| boost | Boost | -0.0 | -6.0 | +25.7 | -inf | -inf | exact | 1.00 | 0 | pass |
| looper | Looper | -0.0 | -6.0 | +0.0 | -inf | -inf | exact | 0.00 | 0 | pass |

## Notes

- `geq7` warn all-max {b50=15,b120=15,b400=15,b500=15,b800=15,b4k5=15,b10k=15,level=15}: hot peak +40.4 dBFS
- `geq7` warn corner {b50=-15,b120=15,b400=15,b500=15,b800=15,b4k5=-5.68133,b10k=-15,level=8.82189}: hot peak +31.4 dBFS
- `geq7` warn corner {b50=15,b120=15,b400=15,b500=15,b800=-15,b4k5=-14.4497,b10k=-15,level=15}: hot peak +32.8 dBFS
- `geq7` warn corner {b50=-10.2435,b120=15,b400=-15,b500=15,b800=-15,b4k5=15,b10k=15,level=15}: hot peak +33.9 dBFS
- `geq7` warn corner {b50=13.9363,b120=-12.4328,b400=15,b500=15,b800=11.5298,b4k5=1.58421,b10k=15,level=15}: hot peak +34.5 dBFS
- `geq7` warn corner {b50=15,b120=15,b400=15,b500=15,b800=-15,b4k5=9.9991,b10k=15,level=15}: hot peak +33.8 dBFS

Total: 33 effects, 0 failures, 6 warnings.
