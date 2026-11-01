// Shared plumbing for the drive effects (overdrive, distortion, fuzz, octafuzz, bitcrush).
//
//   dry ──────────────────────────────────────────────┐
//   in ─► LR4 crossover @ Low Keep ─► lo (clean sub) ─┤ wet = lo + makeup * hiPath(hi)
//                                   └► hi ─► hiPath ──┘ out = level * (dry + blend * (wet - dry))
//
// hiPath is the effect's own pre-EQ -> oversampled waveshaper(s) -> tone. Its output level is
// auto-compensated: prepare() renders a synthetic bass signal through hiPath at 9 drive settings
// and stores a makeup table, so turning Drive changes the character, not the loudness.
#pragma once

#include "dsp.h"

namespace bb {
namespace dsp {

// Synthetic DI-bass test signal for calibration: harmonics of 55 Hz (1/k, pickup low-pass) with a
// slow amplitude envelope, normalised to -18 dBFS RMS (the level of samples/test-bass-riff.wav).
inline void bassCalSignal(std::vector<float>& x, float fs, int n) {
    x.assign(size_t(n), 0.f);
    double sumsq = 0;
    for (int i = 0; i < n; ++i) {
        const double t = double(i) / fs;
        double v = 0;
        for (int k = 1; k <= 40; ++k) {
            const double fk = 55.0 * k;
            if (fk > 8000) break;
            const double a = std::fabs(std::sin(3.14159265 * k * 0.2)) / k / std::sqrt(1 + std::pow(fk / 2500, 4));
            v += a * std::sin(2 * 3.14159265358979 * fk * t + 0.7 * k);
        }
        const double env = 0.55 + 0.45 * std::cos(2 * 3.14159265358979 * 6.0 * t);  // 6 Hz "plucks"
        x[size_t(i)] = float(v * env);
        sumsq += v * env * v * env;
    }
    const float g = float(0.1175 / std::sqrt(sumsq / n));  // -18.6 dBFS RMS
    for (auto& v : x) v *= g;
}

// Makeup table cache: calibration is deterministic per effect type and sample rate, so the first
// instance pays (~0.1 s on the control thread) and later instances copy it.
struct DriveCal {
    float fs = 0.f;
    float table[3][9] = {};
};

struct DriveCommon {
    static constexpr float kLowPts[3] = {40.f, 126.5f, 400.f};  // log-spaced Low Keep grid
    float fs = 48000.f;
    Crossover xo;
    Smoother lowLog, blend, level, makeup;
    DcBlock dc;
    bool primed = false;
    float drivePct = 50.f, lowHz = 120.f;
    DriveCal cal;  // makeup (dB) over [low keep][drive 0..100 in 12.5 steps]

    void prepare(float fs_) {
        fs = fs_;
        lowLog.setTime(40.f, fs / kSub);
        blend.setTime(20.f, fs);
        level.setTime(20.f, fs);
        makeup.setTime(40.f, fs);
        dc.set(fs, 8.f);
        primed = false;
        makeup.snap(1.f);
    }
    void reset() {
        xo.reset();
        dc.reset();
        lowLog.snap();
        blend.snap();
        level.snap();
        makeup.snap();
        xo.set(fs, std::exp(lowLog.cur));
    }
    void setLowKeep(float hz) {
        lowHz = hz;
        lowLog.set(std::log(hz), primed);
        if (!primed) xo.set(fs, hz);
        updateMakeup();
    }
    void setBlend(float pct) { blend.set(0.01f * pct, primed); }
    void setLevel(float db) { level.set(dbToLin(db), primed); }
    void setDrive(float pct) { drivePct = pct; updateMakeup(); }
    void updateMakeup() {
        const float p = clampf(drivePct, 0.f, 100.f) * 0.08f;
        const int i = std::min(7, int(p));
        const float t = p - float(i);
        const float l = clampf(std::log(lowHz / kLowPts[0]) / std::log(kLowPts[2] / kLowPts[0]) * 2.f, 0.f, 2.f);
        const int j = std::min(1, int(l));
        const float u = l - float(j);
        auto at = [&](int jj) { return cal.table[jj][i] + t * (cal.table[jj][i + 1] - cal.table[jj][i]); };
        makeup.set(dbToLin(at(j) + u * (at(j + 1) - at(j))), primed);
    }
    // per sub-block: update the crossover if Low Keep is gliding, then split
    inline void split(const float* x, float* lo, float* hi, int m) {
        if (!lowLog.settled()) { lowLog.next(); xo.set(fs, std::exp(lowLog.cur)); }
        for (int i = 0; i < m; ++i) xo.split(x[i], lo[i], hi[i]);
    }
    // out = level * (lo + k(b) * ((1-b) * cleanHi + b * makeup * hi)). The lows are always the
    // phase-coherent crossover output, so Blend never comb-filters the sub. k(b) compensates the
    // power dip of crossfading two partly-correlated signals (rho ~ 0.3). Returns peak |wet hi|.
    inline float combine(float* x, const float* lo, const float* cleanHi, const float* hi, int m) {
        float pk = 0.f;
        for (int i = 0; i < m; ++i) {
            const float h = dc.process(hi[i]) * makeup.next();
            pk = std::max(pk, std::fabs(h));
            const float b = blend.next();
            const float a = 1.f - b;
            const float k = 1.f / std::sqrt(a * a + b * b + 0.6f * a * b);
            x[i] = (lo[i] + k * (a * cleanHi[i] + b * h)) * level.next();
        }
        return pk;
    }
    void flushState() { xo.flushState(); dc.flushState(); }

    // Calibrate the makeup table. `run(drivePct, buf, n)` must reset the hiPath state, set the
    // drive, and process buf in place (the hi band of the calibration signal). Control thread only.
    template <class F>
    void calibrate(DriveCal& cache, F&& run) {
        if (cache.fs == fs) { cal = cache; updateMakeup(); return; }
        const int n = int(fs * 0.35f), from = int(fs * (0.35f - 1.f / 6.f));
        const size_t N = static_cast<size_t>(n);
        std::vector<float> sig, lo(N), hi(N), buf(N);
        bassCalSignal(sig, fs, n);
        for (int j = 0; j < 3; ++j) {
            Crossover x;
            x.set(fs, kLowPts[j]);
            for (size_t i = 0; i < N; ++i) x.split(sig[i], lo[i], hi[i]);
            double in = 0;
            for (int i = from; i < n; ++i) in += double(hi[size_t(i)]) * hi[size_t(i)];
            for (int k = 0; k < 9; ++k) {
                buf = hi;
                run(12.5f * float(k), buf.data(), n);
                double out = 0;
                for (int i = from; i < n; ++i) out += double(buf[size_t(i)]) * buf[size_t(i)];
                cal.table[j][k] = clampf(float(10.0 * std::log10((in + 1e-20) / (out + 1e-20))), -60.f, 40.f);
            }
        }
        cal.fs = fs;
        cache = cal;
        updateMakeup();
    }
};

// One sub-block of the standard drive flow; HiFn(float* hi, int m) is the effect's hi path.
template <class HiFn>
inline float driveBlock(DriveCommon& dc, float* x, int m, HiFn&& hiPath) {
    float lo[kSub], hi[kSub], clean[kSub];
    dc.split(x, lo, hi, m);
    std::memcpy(clean, hi, sizeof(float) * size_t(m));
    hiPath(hi, m);
    return dc.combine(x, lo, clean, hi, m);
}

// static fuzz shaper (silicon transistor + diode pair in feedback): softer than hard clip,
// harder than tanh; slight asymmetry
inline float shapeFuzz(float x) {
    const float y = x >= 0.f ? x : 0.85f * x;
    return y / std::sqrt(std::sqrt(1.f + y * y * y * y));
}

// Cubic soft clipper (unity slope at 0, smooth knee into a +-1 ceiling at |x| = 1.5) with
// first-order antiderivative anti-aliasing (ADAA, Parker/Zavalishin/Le Bihan 2016). Used inside
// the 4x oversampled block: ADAA's sinc response nulls the harmonics sitting near the
// oversampled rate, which are exactly the ones that would fold back into the audio band.
inline double cubicClip(double x) {
    if (x > 1.5) return 1.0;
    if (x < -1.5) return -1.0;
    return x - (4.0 / 27.0) * x * x * x;
}
inline float shapeCubic(float x) { return float(cubicClip(x)); }
struct AdaaClip {
    float x1 = 0.f;
    static double F(double x) {
        const double a = std::fabs(x);
        if (a > 1.5) return a - 0.5625;
        const double x2 = x * x;
        return 0.5 * x2 - x2 * x2 * (1.0 / 27.0);
    }
    // (F(x) - F(x1)) / (x - x1) in closed form where possible: no division and no cancellation
    // in the common cases (both samples in the cubic region, or both saturated on one side).
    inline float process(float x) {
        const float a = x1;
        x1 = x;
        const bool inX = std::fabs(x) <= 1.5f, inA = std::fabs(a) <= 1.5f;
        if (inX && inA) return (x + a) * (0.5f - (x * x + a * a) * (1.f / 27.f));
        if (x > 1.5f && a > 1.5f) return 1.f;
        if (x < -1.5f && a < -1.5f) return -1.f;
        const double d = double(x) - double(a);
        if (std::fabs(d) < 1e-6) return float(cubicClip(0.5 * (double(x) + double(a))));
        return float((F(x) - F(a)) / d);
    }
    void reset() { x1 = 0.f; }
};

// |x| (full-wave rectifier) with ADAA, F(x) = x|x|/2, in closed form:
// same sign -> |x + x1| / 2; opposite signs -> (x^2 + x1^2) / (2 (|x| + |x1|))
struct AdaaAbs {
    float x1 = 0.f;
    inline float process(float x) {
        const float a = x1;
        x1 = x;
        if ((x >= 0.f) == (a >= 0.f)) return 0.5f * std::fabs(x + a);
        return (x * x + a * a) / (2.f * (std::fabs(x) + std::fabs(a)));
    }
    void reset() { x1 = 0.f; }
};

}  // namespace dsp
}  // namespace bb
