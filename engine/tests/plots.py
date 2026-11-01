#!/usr/bin/env python3
"""bassbox effects plots. Reads the probe renders written by `fxtest probe` (out/probe/) and writes
PNGs to out/plots/:

  eq_responses.png        eq3 / geq7 / cut / boost magnitude responses
  cab_responses.png       Cab Sim voicings x mic position
  amp_tone.png            Amp tone stack + cab voicing (small-signal)
  modfilter_lowcut.png    small-signal response of modulation/filter effects at defaults
  drive_shapers.png       static waveshaper curves + dynamic transfer (220 Hz in vs out)
  drive_spectra.png       harmonic spectra at 110 Hz and 41.2 Hz (low E) input
  drive_aliasing.png      3.1 kHz input: harmonics vs aliased components (4x oversampling check)

usage: plots.py <out dir>      (needs numpy + matplotlib; `make plots` runs it via uv if present)
"""
import json
import os
import sys
import wave

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SR = 48000
# validated categorical order (dataviz reference palette, light surface)
C = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
DASH = ["-", "-", "--", "-", "-.", "--", "-", ":"]  # secondary encoding for low-contrast slots
SURF, INK, INK2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
    "text.color": INK, "axes.titlesize": 10, "axes.titleweight": "bold", "axes.labelsize": 8.5,
    "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 7.5, "legend.frameon": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "lines.linewidth": 1.5,
    "axes.spines.top": False, "axes.spines.right": False, "font.family": "DejaVu Sans",
})


def read_float_wav(path):
    # python's wave module rejects WAVE_FORMAT_IEEE_FLOAT, so parse the chunks directly
    with open(path, "rb") as f:
        d = f.read()
    pos = 12
    while pos + 8 <= len(d):
        cid, sz = d[pos:pos + 4], int.from_bytes(d[pos + 4:pos + 8], "little")
        if cid == b"data":
            return np.frombuffer(d[pos + 8:pos + 8 + sz], dtype="<f4").astype(np.float64)
        pos += 8 + sz + (sz & 1)
    raise ValueError(path)


def bandpass(x, fc, q):
    """RBJ constant-peak bandpass, direct-form loop (scipy-free)."""
    w = 2 * np.pi * fc / SR
    al = np.sin(w) / (2 * q)
    b0, b2, a0, a1, a2 = al, -al, 1 + al, -2 * np.cos(w), 1 - al
    b0, b2, a1, a2 = b0 / a0, b2 / a0, a1 / a0, a2 / a0
    y = np.zeros_like(x)
    x1 = x2 = y1 = y2 = 0.0
    for i, v in enumerate(x.tolist()):
        o = b0 * v + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, o
        y[i] = o
    return y


def mag_db(ir, ref=0.25):
    n = max(len(ir), 65536)
    H = np.fft.rfft(ir, n) / ref
    f = np.fft.rfftfreq(n, 1 / SR)
    return f, 20 * np.log10(np.maximum(np.abs(H), 1e-9))


def spectrum_dbfs(y):
    n = min(len(y), 32768)
    y = y[-n:]
    k = np.arange(n) / (n - 1)  # 4-term Blackman-Harris: -92 dB sidelobes (plain Blackman's -58 dB would mask the floor)
    w = 0.35875 - 0.48829 * np.cos(2 * np.pi * k) + 0.14128 * np.cos(4 * np.pi * k) - 0.01168 * np.cos(6 * np.pi * k)
    S = np.fft.rfft(y * w) / (w.sum() / 2)
    f = np.fft.rfftfreq(n, 1 / SR)
    return f, 20 * np.log10(np.maximum(np.abs(S), 1e-10))


def freq_axis(ax, lo=20, hi=20000):
    ax.set_xscale("log")
    ax.set_xlim(lo, hi)
    ticks = [20, 30, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
    ticks = [t for t in ticks if lo <= t <= hi]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{t // 1000}k" if t >= 1000 else str(t) for t in ticks])
    ax.set_xlabel("Hz")


def main(out):
    pdir, odir = os.path.join(out, "probe"), os.path.join(out, "plots")
    os.makedirs(odir, exist_ok=True)
    idx = json.load(open(os.path.join(pdir, "index.json")))
    load = lambda e: read_float_wav(os.path.join(pdir, e["file"]))  # noqa: E731
    by = {}
    for e in idx:
        by.setdefault(e["effect"], []).append(e)
    made = []

    # ---- EQ family ------------------------------------------------------------------------
    fig, axs = plt.subplots(2, 2, figsize=(11, 7.5))
    for ax, eff, title, ylim in [(axs[0, 0], "eq3", "3-Band EQ (Bass 90 Hz shelf, Mid peak, Treble 4 kHz shelf)", (-16, 16)),
                                 (axs[0, 1], "geq7", "Graphic EQ (50/120/400/500/800/4.5k/10k, +-15 dB)", (-18, 28)),
                                 (axs[1, 0], "cut", "Filter (Low Cut / High Cut, 12 or 24 dB/oct)", (-40, 3)),
                                 (axs[1, 1], "boost", "Boost Tone (tilt around 650 Hz), Gain 0 dB", (-8, 8))]:
        es = [e for e in by.get(eff, []) if e["kind"] == "ir" and e["label"] != "defaults"]
        for i, e in enumerate(es[:8]):
            f, m = mag_db(load(e))
            ax.plot(f, m, color=C[i % 8], ls=DASH[i % 8], label=e["label"])
        freq_axis(ax)
        ax.set_ylim(*ylim)
        ax.set_ylabel("dB")
        ax.set_title(title, loc="left")
        ax.legend(loc="lower left" if eff != "cut" else "lower center", ncol=2)
    fig.tight_layout()
    fig.savefig(os.path.join(odir, "eq_responses.png"), dpi=130)
    plt.close(fig)
    made.append("eq_responses.png")

    # ---- Cab voicings ---------------------------------------------------------------------
    fig, axs = plt.subplots(2, 2, figsize=(11, 7.5), sharey=True)
    cabs = ["1x15", "4x10", "8x10", "2x12"]
    es = [e for e in by.get("cab", []) if e["label"] != "defaults"]
    for k, cab in enumerate(cabs):
        ax = axs[k // 2, k % 2]
        sel = [e for e in es if e["label"].startswith(cab)]
        for i, e in enumerate(sel):
            f, m = mag_db(load(e))
            ax.plot(f, m, color=C[i], ls=DASH[i], label=e["label"].replace(cab + " ", "").replace("default mic", "mic 30% (default)").replace("on-axis", "mic 0% (on-axis)").replace("off-axis", "mic 100% (off-axis)"))
        freq_axis(ax)
        ax.set_ylim(-42, 12)
        ax.set_ylabel("dB")
        ax.set_title(f"Cab Sim {cab}", loc="left")
        ax.legend(loc="lower left")
    fig.tight_layout()
    fig.savefig(os.path.join(odir, "cab_responses.png"), dpi=130)
    plt.close(fig)
    made.append("cab_responses.png")

    # ---- Modulation / filter small-signal responses at defaults ------------------------------
    # ringmod (frequency shifting) and tremolo (time-varying gain) are not LTI: an impulse "response" means nothing
    sel = [e for e in idx if e["label"] == "defaults" and e["category"] in ("modulation",) and e["effect"] not in ("ringmod", "tremolo")]
    if sel:
        fig, ax = plt.subplots(figsize=(11, 4.8))
        for i, e in enumerate(sel[:8]):
            f, m = mag_db(load(e))
            ax.plot(f, m, color=C[i], ls=DASH[i], label=e["effect"])
        freq_axis(ax)
        ax.axvspan(20, 120, color="#f0efec", zorder=0)
        ax.text(24, 4.2, "below Low Cut (120 Hz default): dry, flat", color=INK2, fontsize=8)
        ax.set_ylim(-30, 6)
        ax.set_ylabel("dB")
        ax.set_title("Modulation effects, small-signal magnitude at defaults (1 s impulse window; modulated bands comb)", loc="left")
        ax.legend(loc="lower left", ncol=4)
        fig.tight_layout()
        fig.savefig(os.path.join(odir, "modulation_lowcut.png"), dpi=130)
        plt.close(fig)
        made.append("modulation_lowcut.png")

    # ---- Drives: static shapers + dynamic transfer -------------------------------------------
    sh = np.genfromtxt(os.path.join(pdir, "shapers.csv"), delimiter=",", names=True)
    fig, axs = plt.subplots(1, 4, figsize=(15, 4.0))
    ax = axs[0]
    for i, (col, lab) in enumerate([("diode_soft", "diode (overdrive)"), ("cubic", "cubic + ADAA (distortion, octafuzz)"),
                                    ("fuzz", "fuzz (2 stages)"), ("tube", "triode (amp)"), ("tanh", "tanh (reference)")]):
        ax.plot(sh["x"], sh[col], color=C[i], ls=DASH[i], label=lab)
    ax.set_xlim(-4, 4)
    ax.set_title("Static waveshapers", loc="left")
    ax.set_xlabel("in")
    ax.set_ylabel("out")
    ax.legend(loc="upper left")
    for ax, eff in zip(axs[1:], ["overdrive", "distortion", "fuzz"]):
        es = [e for e in by.get(eff, []) if e["label"].startswith("220 Hz")]
        for i, e in enumerate(es):
            y = load(e)
            x = read_float_wav(os.path.join(pdir, e["input"]))
            n = int(SR / 220 * 3)
            ax.plot(x[-n:], y[-n:], color=C[i], ls=DASH[i], lw=1.2, label="drive " + e["label"].split("drive ")[-1] + "%")
        ax.set_title(f"{eff}: 220 Hz in vs out", loc="left")
        ax.set_xlabel("input")
        ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(os.path.join(odir, "drive_shapers.png"), dpi=130)
    plt.close(fig)
    made.append("drive_shapers.png")

    # ---- Drives: harmonic spectra (110 Hz and low E 41.2 Hz) ---------------------------------
    drives = [d for d in ["overdrive", "distortion", "fuzz", "octafuzz", "amp", "bitcrush"] if d in by]
    for tag, f0, fname in [("110 Hz", 110.0, "drive_spectra_110.png"), ("41.2 Hz", 41.2, "drive_spectra_41.png")]:
        fig, axs = plt.subplots(2, 3, figsize=(15, 7.5), sharey=True)
        for k, d in enumerate(drives):
            ax = axs[k // 3, k % 3]
            e = [e for e in by[d] if e["label"].startswith(tag)][0]
            f, s = spectrum_dbfs(load(e))
            ax.plot(f, s, color=C[0], lw=0.9, label="output")
            fi, si = spectrum_dbfs(read_float_wav(os.path.join(pdir, e["input"])))
            ax.plot(fi, si, color=C[1], lw=0.9, ls="--", label="input (-12 dBFS sine)")
            hk = [h for h in range(1, 12)]
            hv = [s[np.argmin(np.abs(f - h * f0))] for h in hk]
            ax.plot([h * f0 for h in hk], hv, "o", ms=4, color=C[0], mfc=SURF, mew=1.2)
            freq_axis(ax, 20, 20000)
            ax.set_ylim(-130, 5)
            ax.set_ylabel("dBFS")
            fund = hv[0]
            ax.set_title(f"{d} @ defaults\nfundamental {fund:+.1f} dBFS, H2 {hv[1]-fund:+.0f} dB, H3 {hv[2]-fund:+.0f} dB", loc="left", fontsize=9)
            if k == 0:
                ax.legend(loc="lower left")
        fig.suptitle(f"Drive harmonic spectra, {tag} sine input at -12 dBFS (circles = harmonics 1..11)", x=0.01, ha="left", fontsize=11, fontweight="bold")
        fig.tight_layout()
        fig.savefig(os.path.join(odir, fname), dpi=130)
        plt.close(fig)
        made.append(fname)

    # ---- Drives: aliasing at 3.1 kHz --------------------------------------------------------
    fig, axs = plt.subplots(2, 3, figsize=(15, 7.5), sharey=True)
    rows = []
    for k, d in enumerate(drives):
        ax = axs[k // 3, k % 3]
        e = [e for e in by[d] if e["label"].startswith("3.1 kHz")][0]
        y = load(e)
        f, s = spectrum_dbfs(y)
        f0 = 3100.0
        df = f[1] - f[0]
        harm = np.zeros_like(f, dtype=bool)
        for h in range(1, int(24000 / f0) + 1):
            harm |= np.abs(f - h * f0) <= 6 * df
        floor = (f > 20) & ~harm
        P = 10 ** (s / 10)
        fund = s[np.argmin(np.abs(f - f0))]
        alias_db = 10 * np.log10(P[floor].sum() + 1e-30) - fund
        top_alias = s[floor].max() - fund
        rows.append((d, alias_db, top_alias))
        ax.plot(f[harm], s[harm], ".", ms=2.5, color=C[0], label="harmonics (k x 3.1 kHz)")
        ax.plot(f[floor], s[floor], lw=0.6, color=C[7], label="everything else (aliases + noise)")
        ax.set_xlim(0, 24000)
        ax.set_ylim(-150, 5)
        ax.set_xlabel("Hz (linear)")
        ax.set_ylabel("dBFS")
        ax.set_title(f"{d}\nnon-harmonic energy {alias_db:.0f} dB re fundamental, worst bin {top_alias:.0f} dB", loc="left", fontsize=9)
        if k == 0:
            ax.legend(loc="lower right")
    fig.suptitle("Aliasing check: 3.1 kHz sine at -12 dBFS, defaults (harmonics above 24 kHz fold back unless oversampled)",
                 x=0.01, ha="left", fontsize=11, fontweight="bold")
    fig.tight_layout()
    fig.savefig(os.path.join(odir, "drive_aliasing.png"), dpi=130)
    plt.close(fig)
    made.append("drive_aliasing.png")
    with open(os.path.join(odir, "aliasing.txt"), "w") as fh:
        fh.write("effect, non-harmonic energy re fundamental (dB), worst single non-harmonic bin (dB)\n")
        for r in rows:
            fh.write(f"{r[0]}, {r[1]:.1f}, {r[2]:.1f}\n")

    # ---- Filters at defaults + amp ---------------------------------------------------------
    sel = [e for e in idx if e["label"] == "defaults" and e["effect"] in ("amp", "cab", "env_filter", "wah", "synth_filter", "octaver")]
    if sel:
        fig, ax = plt.subplots(figsize=(11, 4.8))
        for i, e in enumerate(sel[:8]):
            f, m = mag_db(load(e))
            ax.plot(f, m, color=C[i], ls=DASH[i], label=e["effect"])
        freq_axis(ax)
        ax.set_ylim(-50, 15)
        ax.set_ylabel("dB")
        ax.set_title("Amp / cab / filters: small-signal magnitude at defaults (-12 dBFS impulse after 1 s settle)", loc="left")
        ax.legend(loc="lower left", ncol=3)
        fig.tight_layout()
        fig.savefig(os.path.join(odir, "amp_filters_defaults.png"), dpi=130)
        plt.close(fig)
        made.append("amp_filters_defaults.png")

    # ---- Time effects: reverb energy decay (Schroeder) + echo trains ---------------------------
    rv = [e for e in idx if e["kind"] == "longir" and e["effect"] in ("reverb", "shimmer")]
    ec = [e for e in idx if e["kind"] == "longir" and e["effect"] in ("delay", "tape")]
    if rv or ec:
        fig, axs = plt.subplots(1, 2, figsize=(14, 4.6))
        ax = axs[0]
        for i, e in enumerate(rv):
            y = load(e).copy()
            y[0] -= 0.25  # remove the unity dry impulse (out = dry + mix * wet)
            yb = bandpass(y, 354.0, 0.7)  # ~250-500 Hz, where Decay (RT60) is specified
            edc = np.cumsum((yb ** 2)[::-1])[::-1]
            edc_db = 10 * np.log10(edc / edc[0] + 1e-30)
            t = np.arange(len(y)) / SR
            sel = (edc_db < -5) & (edc_db > -35)
            rt = np.nan
            if sel.sum() > 10:
                slope = np.polyfit(t[sel], edc_db[sel], 1)[0]
                rt = -60 / slope
            ax.plot(t, edc_db, color=C[i], ls=DASH[i], label=f'{e["effect"]} {e["label"]}: measured T30 {rt:.2f} s')
        ax.set_xlim(0, 8)
        ax.set_ylim(-80, 2)
        ax.set_xlabel("s")
        ax.set_ylabel("dB")
        ax.set_title("Reverb energy decay (250-500 Hz band, Schroeder integral)", loc="left")
        ax.legend(loc="upper right")
        ax = axs[1]
        for i, e in enumerate(ec):
            y = load(e)
            n = 480  # peak per 10 ms bin
            m = len(y) // n
            pk = np.abs(y[: m * n]).reshape(m, n).max(axis=1) / 0.25
            t = (np.arange(m) + 0.5) * n / SR
            ax.plot(t, 20 * np.log10(pk + 1e-9), color=C[i], label=f'{e["effect"]} {e["label"]}')
        ax.set_xlim(0, 5)
        ax.set_ylim(-80, 0)
        ax.set_xlabel("s")
        ax.set_ylabel("dB re impulse")
        ax.set_title("Delay / Tape Echo: peak per 10 ms after a -12 dBFS impulse, defaults (dry at 0 dB)", loc="left")
        ax.legend(loc="upper right")
        fig.tight_layout()
        fig.savefig(os.path.join(odir, "time_decay.png"), dpi=130)
        plt.close(fig)
        made.append("time_decay.png")

    print("wrote", ", ".join(made), "to", odir)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
