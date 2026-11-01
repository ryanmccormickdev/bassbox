#!/usr/bin/env python3
"""bassbox: synthesize test signals (48 kHz mono, 24-bit WAV). Needs numpy.

  make_test_signals.py riff  <out.wav>   plucked-string bass riff (Karplus-Strong), ~8 s
  make_test_signals.py tones <out.wav>   stepped sines 40 Hz..8 kHz, 0.5 s each, -12 dBFS
                                         (used by measure_response.py)
"""
import sys
import wave

import numpy as np

SR = 48000
TONE_FREQS = [40, 60, 80, 100, 150, 200, 250, 300, 400, 500, 700, 1000, 1500, 2000, 3000, 5000, 8000]
TONE_SECS = 0.5
TONE_GAP = 0.1


def write(path, x):
    x = np.clip(x, -1, 1)
    pcm = (x * (2**23 - 1)).astype("<i4")
    raw = b"".join(int(v).to_bytes(4, "little", signed=True)[:3] for v in pcm)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(3)
        w.setframerate(SR)
        w.writeframes(raw)


def pluck(freq, secs, rng):
    """Additive model of a picked bass string through a passive pickup (DI-like spectrum):
    pluck-position comb (1/5 of the string), 1/k roll-off, pickup low-pass ~2.5 kHz,
    upper harmonics decaying faster than the fundamental."""
    t = np.arange(int(SR * secs)) / SR
    out = np.zeros_like(t)
    beta = 0.2 + rng.uniform(-0.02, 0.02)
    for k in range(1, 60):
        fk = k * freq * (1 + 0.0002 * k * k)          # slight string inharmonicity
        if fk > 10000:
            break
        a = abs(np.sin(np.pi * k * beta)) / k
        a /= np.sqrt(1 + (fk / 2500) ** 4)            # pickup resonance/low-pass
        tau = 1.8 / (1 + 0.25 * k)
        out += a * np.sin(2 * np.pi * fk * t + rng.uniform(0, 2 * np.pi)) * np.exp(-t / tau)
    env = np.minimum(1, t / 0.002) * np.minimum(1, (secs - t) / 0.01)   # attack, damped release
    return out * env


def riff():
    rng = np.random.default_rng(7)
    E1, G1, A1, B1, D2, E2 = 41.2, 49.0, 55.0, 61.7, 73.4, 82.4
    bpm = 100
    eighth = 60 / bpm / 2
    pattern = [E1, E1, E2, E1, G1, A1, E1, B1,
               A1, A1, E2, A1, D2, B1, A1, G1] * 2
    total = int(SR * (eighth * len(pattern) + 1.5))
    x = np.zeros(total)
    for k, f in enumerate(pattern):
        start = int(SR * k * eighth)
        note = pluck(f, eighth * 1.6 if k == len(pattern) - 1 else eighth * 1.05, rng)
        accent = 1.0 if k % 4 == 0 else 0.75
        end = min(total, start + len(note))
        x[start:end] += note[: end - start] * accent
    return x / np.max(np.abs(x)) * 0.5   # -6 dBFS peak


def tones():
    parts = []
    amp = 10 ** (-12 / 20)
    t = np.arange(int(SR * TONE_SECS)) / SR
    fade = np.minimum(1, np.minimum(t, TONE_SECS - t) / 0.01)
    for f in TONE_FREQS:
        parts.append(amp * np.sin(2 * np.pi * f * t) * fade)
        parts.append(np.zeros(int(SR * TONE_GAP)))
    return np.concatenate(parts)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("riff", "tones"):
        print(__doc__)
        sys.exit(64)
    write(sys.argv[2], riff() if sys.argv[1] == "riff" else tones())
