# Audio path and codec setup

## Signal path

```
bass ─► panel IN (1/4") ─► HAT LINE IN (middle 3.5 mm jack, left channel)
     ─► WM8960: LINPUT3 ─► left input boost mixer (0 dB) ─► ADC ─► I2S capture, 48 kHz S32_LE
     ─► bassbox-engine: in gain ─► effect chain (mono) ─► out gain ─► limiter
     ─► I2S playback ─► DAC ─► output mixer (PCM only) ─► headphone out (0 dB) ─► panel OUT (1/4")
```

The bass is a mono plug on a stereo input, so only the left channel carries signal; the engine
processes mono and sends the result to both output channels. The HAT's onboard microphones, mic
jack and speaker amplifier are not used and are switched off.

The mixer profile is `config/mixer.conf`, applied at boot by `scripts/mixer.sh` through
`amixer`. It makes the pedal unity gain end to end: the ADC and the DAC both have a full scale of
about 1 Vrms, and the level is set in the engine (`out_gain_db`). Analog bypass paths into the
output mixers are off, so no dry bass can leak around the engine. The codec's ALC and noise gate
are off, because they fight the dynamics of the effects.

## What had to be fixed to get a clean capture

On 2026-10-06 the left capture channel sat at +0.85 of full scale and rose by about 180 LSB per
second to full scale, whatever the routing. Four causes were found and fixed:

1. **The PGA was used in a mode that does not work.** The first profile routed LINPUT3 into the
   input PGA's non-inverting input with the inverting input (LINPUT1, which is the onboard
   microphone on this board) left open. With no inverting-side input the PGA has no reference and
   its output drifts to the rail; register dumps showed it, and sending LINPUT3 straight to the
   boost mixer gave +0.007 of full scale, flat. The fix is to leave the PGA unused: LINPUT3 goes
   to the boost mixer at 0 dB and then to the ADC.
2. **The ADC high-pass filter was off.** The driver's "ADC High Pass Filter Switch" is register
   R5 bit 0, `ADCHPD`, which disables the filter when set, so "on" removed it and the ADC's own
   DC offset passed through. Setting the control to `off` enables the filter. DC is then +0.00004
   of full scale, stable over 20 s after about 1 s of settling at stream start; the engine mutes
   its output for the first 0.5 s and fades in.
3. **`scripts/mixer.sh` could not parse single-character values.** Its `sed` expression needed
   at least two characters, so every value such as `0` or `5` silently failed to apply. It now
   splits on the first `=` in plain bash, and a leading `?` marks a control as optional.
4. **The class-D speaker amplifier added noise.** DAPM powers it whenever playback runs, even
   with the speaker volume at its hardware mute, and that raised the ADC noise floor from -93.6 to
   -82.9 dBFS RMS (22 Hz to 20 kHz, LINPUT3 at 0 dB, S32). `config/overlays/bassbox-wm8960.dts`
   routes all four speaker pins to one DAPM widget and exposes it as a pin switch, and
   `config/mixer.conf` turns it off. After a reboot the class-D stage reads as powered down while
   streaming, the engine's input RMS meter reads about -90 dBFS with the HAT input open, and
   `arecord` measures -92.7 dBFS.

Capture and playback must use the same sample format: with S32 capture running the driver refuses
S16 playback, because the I2S frame is shared. A 32-frame period needs S32 as well.

## Input level and impedance

The line input at 0 dB boost clips at roughly 1 Vrms, so a passive bass (0.5 to 1 V peak to peak)
peaks around -15 to -9 dBFS, leaving at least 9 dB of headroom. For a weak pickup, raise
`Left Input Boost Mixer LINPUT3 Volume` to 6 (+3 dB) or 7 (+6 dB) and trim with the engine's
`in_gain_db`. Settings, Audio check, has an auto-set gain that aims for peaks near -6 dBFS.

The boost path has a lower input impedance than the PGA (a WM8960 property, on the order of tens
of kilohms), so a passive pickup loses some top end. A buffered or active bass, a DI box or a
buffer pedal in front avoids that. The codec's high-impedance PGA input cannot be used on this
board, for the reason in cause 1.

## The first version

`history/v1-pipedal/` has the earlier build, which hosted a neural amp model in PiPedal. Its chain
was a noise gate, then a split into a neural amp branch and a clean branch low-passed near 200 Hz
(to keep the low end solid), then an output volume.
