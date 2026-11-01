# bb-render check (bassbox, 2026-10-06)

Input: test riff + 3 s tail, block 64, host DC blocker + output limiter on. Level = RMS vs. an empty chain (-19.7 dBFS).

| chain | level dB | out peak dBFS | cpu avg % | worst block % (wall clock, includes OS jitter) | NaN resets |
|---|---|---|---|---|---|
| gate | +0.0 | -6.0 | 0.27 | 2.10 | 0 |
| comp | +0.5 | -2.5 | 0.55 | 3.55 | 0 |
| limiter | +0.0 | -6.0 | 1.04 | 4.20 | 0 |
| overdrive | +0.0 | -7.3 | 1.42 | 4.83 | 0 |
| distortion | +0.1 | -7.3 | 1.21 | 4.98 | 0 |
| fuzz | -0.4 | -9.6 | 1.56 | 3.67 | 0 |
| octafuzz | -0.6 | -7.6 | 1.38 | 4.80 | 0 |
| bitcrush | +0.0 | -5.9 | 0.37 | 2.62 | 0 |
| amp | +0.7 | -6.7 | 2.38 | 4.26 | 0 |
| cab | +0.2 | -6.1 | 0.46 | 3.17 | 0 |
| nam | -1.0 | -9.2 | 16.65 | 35.30 | 0 |
| env_filter | +0.2 | -5.0 | 1.01 | 3.41 | 0 |
| wah | -1.8 | -8.2 | 0.76 | 3.68 | 0 |
| synth_filter | -1.4 | -7.5 | 0.96 | 2.69 | 0 |
| eq3 | +0.0 | -6.0 | 0.26 | 7.24 | 0 |
| geq7 | +0.0 | -6.0 | 0.22 | 2.60 | 0 |
| cut | +0.0 | -5.5 | 0.36 | 1.75 | 0 |
| chorus | -0.4 | -6.3 | 0.87 | 4.19 | 0 |
| flanger | -0.9 | -7.8 | 0.73 | 2.22 | 0 |
| phaser | -0.2 | -7.2 | 1.10 | 4.17 | 0 |
| tremolo | +0.1 | -5.4 | 0.34 | 2.81 | 0 |
| vibrato | -0.1 | -6.2 | 0.53 | 3.66 | 0 |
| univibe | -0.7 | -7.5 | 0.93 | 4.45 | 0 |
| ringmod | -0.2 | -5.3 | 0.76 | 3.68 | 0 |
| octaver | +0.6 | -6.4 | 0.59 | 2.79 | 0 |
| pitch | -0.1 | -6.0 | 0.64 | 2.66 | 0 |
| subsynth | +0.4 | -4.2 | 1.40 | 8.78 | 0 |
| delay | +0.2 | -5.8 | 0.70 | 4.50 | 0 |
| tape | +0.2 | -5.6 | 0.86 | 3.91 | 0 |
| reverb | +0.2 | -6.2 | 1.89 | 5.14 | 0 |
| shimmer | +0.1 | -6.3 | 2.13 | 5.48 | 0 |
| boost | +0.0 | -6.0 | 0.24 | 2.99 | 0 |
| looper | +0.0 | -6.0 | 0.19 | 2.99 | 0 |
| preset: clean_di | +0.2 | -3.6 | 0.66 | 4.01 | 0 |
| preset: doom | +0.3 | -9.1 | 5.60 | 9.55 | 0 |
| preset: dub_echo | +0.3 | -6.7 | 3.75 | 6.76 | 0 |
| preset: fretless_chorus | +0.2 | -3.1 | 3.02 | 7.41 | 0 |
| preset: funk_wah | -0.1 | -0.6 | 1.41 | 5.03 | 0 |
| preset: fuzz_sub | +0.2 | -8.1 | 2.00 | 5.69 | 0 |
| preset: grit | +0.1 | -7.0 | 2.13 | 6.00 | 0 |
| preset: modern_grind | +0.0 | -6.3 | 2.13 | 5.48 | 0 |
| preset: motown | +0.1 | -6.1 | 3.01 | 7.22 | 0 |
| preset: night_drive | -0.1 | -6.6 | 2.38 | 8.87 | 0 |
| preset: synth_bass | -1.2 | -1.9 | 2.26 | 6.13 | 0 |
