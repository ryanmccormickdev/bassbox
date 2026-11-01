#!/usr/bin/env python3
"""bassbox audio helpers (numpy). Runs on the Pi (python3-numpy) or the Mac.

  audio_tools.py align <wet.wav> <dry.wav> <out.wav>
        shift <wet> so it lines up with <dry> (cross-correlation), trimmed to the dry length + 2 s
  audio_tools.py response <rec.wav>
        frequency response from a recording of `make_test_signals.py tones` (dB re. -12 dBFS input)
  audio_tools.py rms <file.wav>
        per-channel peak and RMS in dBFS
  audio_tools.py tone <file.wav> <freq>
        per-channel level of the <freq> component (dBFS RMS) and THD+N (dB), skipping 0.5 s
        at each end. Machine-readable: "ch<N> level=<dB> thdn=<dB>"
  audio_tools.py sine <out.wav> <freq> <dBFS> <secs>
        stereo sine test signal (both channels), 48 kHz 24-bit
"""
import sys
import wave

import numpy as np

sys.dont_write_bytecode = True   # often run as root on the Pi
sys.path.insert(0, __file__.rsplit("/", 1)[0])
from make_test_signals import SR, TONE_FREQS, TONE_GAP, TONE_SECS  # noqa: E402


def read(path):
    with wave.open(path) as w:
        n, ch, sw, sr = w.getnframes(), w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = np.frombuffer(w.readframes(n), dtype=np.uint8).reshape(-1, sw)
    # little-endian signed PCM of any width -> float
    x = np.zeros(len(raw), dtype=np.int64)
    for i in range(sw):
        x |= raw[:, i].astype(np.int64) << (8 * i)
    full = 1 << (8 * sw - 1)
    x = np.where(x >= full, x - 2 * full, x) / full
    return x.reshape(-1, ch), sr


def write(path, x, sr):
    x = np.clip(np.atleast_2d(x.T).T, -1, 1)
    pcm = (x * (2**23 - 1)).astype("<i4")
    b = pcm.reshape(-1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(3)
        w.setframerate(sr)
        w.writeframes(b)


def db(v):
    return 20 * np.log10(max(v, 1e-12))


def lag_of(wet, dry, max_lag):
    n = len(wet) + len(dry)
    c = np.fft.irfft(np.fft.rfft(wet, n) * np.conj(np.fft.rfft(dry, n)), n)
    lag = int(np.argmax(c[:max_lag]))
    peak = c[lag] / (np.sqrt(np.sum(wet**2) * np.sum(dry**2)) + 1e-12)
    return lag, peak


def align(wet_path, dry_path, out_path):
    wet, sr = read(wet_path)
    dry, _ = read(dry_path)
    lag, corr = lag_of(wet[:, 0], dry[:, 0], int(sr * 2.0))
    if corr < 0.05:          # correlation too weak (e.g. preset fully re-shapes the signal):
        lag = int(sr * 0.5)  # fall back to dropping render.sh's 0.5 s lead-in
    out = wet[lag: lag + len(dry) + 2 * sr]
    write(out_path, out, sr)
    print(f"align: shifted by {lag} samples ({1000 * lag / sr:.1f} ms), correlation {corr:.2f}")


def response(rec_path):
    rec, sr = read(rec_path)
    x = rec[:, 0]
    seg = int(sr * TONE_SECS)
    step = int(sr * (TONE_SECS + TONE_GAP))
    # find the start of the first tone (40 Hz): first point the envelope exceeds -40 dBFS
    env = np.convolve(np.abs(x), np.ones(480) / 480, mode="same")
    start = int(np.argmax(env > 10 ** (-40 / 20))) - 240
    ref = 10 ** (-12 / 20) / np.sqrt(2)
    print(" freq_Hz  gain_dB  (fundamental, re. input)")
    rows = []
    for i, f in enumerate(TONE_FREQS):
        a = start + i * step + seg // 4
        s = x[a: a + seg // 2]
        if len(s) < seg // 2:
            break
        t = np.arange(len(s)) / sr
        # amplitude of the fundamental only (lock-in)
        amp = 2 * np.abs(np.mean(s * np.exp(-2j * np.pi * f * t))) / np.sqrt(2)
        rows.append((f, db(amp / ref)))
        print(f"{f:8d} {db(amp / ref):8.1f}")
    return rows


def rms(path):
    x, sr = read(path)
    for c in range(x.shape[1]):
        v = x[:, c]
        print(f"ch{c + 1}: peak {db(np.max(np.abs(v))):6.1f} dBFS  rms {db(np.sqrt(np.mean(v**2))):6.1f} dBFS")


def tone(path, freq):
    x, sr = read(path)
    x = x[int(0.5 * sr): len(x) - int(0.5 * sr)]
    t = np.arange(len(x)) / sr
    ref = np.exp(-2j * np.pi * freq * t)
    for c in range(x.shape[1]):
        v = x[:, c] - np.mean(x[:, c])
        z = np.mean(v * ref)
        level = 2 * np.abs(z) / np.sqrt(2)                    # RMS of the fundamental
        fundamental = 2 * np.real(z * np.conj(ref))           # reconstructed sine
        residual = np.sqrt(np.mean((v - fundamental) ** 2))   # everything else
        print(f"ch{c + 1} level={db(level):.1f} thdn={db(residual / max(level, 1e-12)):.1f}")


def sine(path, freq, dbfs, secs):
    t = np.arange(int(SR * float(secs))) / SR
    v = 10 ** (float(dbfs) / 20) * np.sin(2 * np.pi * float(freq) * t)
    v *= np.minimum(1, np.minimum(t, t[-1] - t) / 0.02)          # 20 ms fades
    write(path, np.stack([v, v], axis=1), SR)


if __name__ == "__main__":
    cmd = sys.argv[1:2]
    if cmd == ["align"] and len(sys.argv) == 5:
        align(*sys.argv[2:5])
    elif cmd == ["response"] and len(sys.argv) == 3:
        response(sys.argv[2])
    elif cmd == ["rms"] and len(sys.argv) == 3:
        rms(sys.argv[2])
    elif cmd == ["tone"] and len(sys.argv) == 4:
        tone(sys.argv[2], float(sys.argv[3]))
    elif cmd == ["sine"] and len(sys.argv) == 6:
        sine(*sys.argv[2:6])
    else:
        print(__doc__)
        sys.exit(64)
