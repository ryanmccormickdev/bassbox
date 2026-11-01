// bassbox shared DSP helpers (header-only, used by every engine/effects/*.cpp).
//
// Conventions:
//   * Everything that allocates (DelayLine::init, Oversampler::init, ...) is for prepare() only.
//     Everything else is allocation-free and safe on the audio thread.
//   * Effects live in an anonymous namespace in their own .cpp; helpers here are inline.
//   * Parameter smoothing: dsp::Smoother (one-pole). Filters whose coefficients cost a tan() are
//     updated per sub-block of kSub samples from a smoother clocked at fs/kSub.
//   * Denormals: the engine sets FTZ on the Pi, but effects must not emit denormals anyway
//     (the Mac harness runs without FTZ). Call flush() on recursive states once per block.
#pragma once

#include "effect.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <initializer_list>
#include <vector>

namespace bb {
namespace dsp {

constexpr float kPi = 3.14159265358979323846f;
constexpr float kTwoPi = 6.28318530717958647692f;
constexpr int kSub = 16;  // sub-block length for coefficient updates

// ---------------------------------------------------------------------------------------------
// small math
inline float clampf(float x, float lo, float hi) { return x < lo ? lo : (x > hi ? hi : x); }
inline float dbToLin(float db) { return std::exp(db * 0.1151292546497023f); }
inline float linToDb(float g) { return 20.f * std::log10(g > 1e-12f ? g : 1e-12f); }
inline float lerp(float a, float b, float t) { return a + (b - a) * t; }
// zero out values far below audibility (-300 dB) so recursive states never go denormal
inline float flush(float x) { return (x > -1e-15f && x < 1e-15f) ? 0.f : x; }
inline void flushRef(float& x) { x = flush(x); }
// flush a whole output block (cheap; vectorises). Use at the end of process() in effects with
// recursive filters so a non-FTZ host never sees denormals in the chain.
inline void flushBlock(float* x, int n) {
    for (int i = 0; i < n; ++i) x[i] = (x[i] > -1e-15f && x[i] < 1e-15f) ? 0.f : x[i];
}

// tanh, [7/6] Lambert continued fraction; |err| < 2e-7 for |x| < 4.97, clamped beyond.
inline float fastTanh(float x) {
    if (x > 4.97f) return 1.f;
    if (x < -4.97f) return -1.f;
    const float x2 = x * x;
    const float a = x * (135135.f + x2 * (17325.f + x2 * (378.f + x2)));
    const float b = 135135.f + x2 * (62370.f + x2 * (3150.f + x2 * 28.f));
    return a / b;
}

// sin(2*pi*p) for p in [0,1). Parabolic approximation with one refinement step (|err| < 1e-3).
inline float fastSin2Pi(float p) {
    const float u = 2.f * (p - 0.5f);
    float y = 4.f * u * (1.f - std::fabs(u));
    y = 0.225f * (y * std::fabs(y) - y) + y;
    return -y;
}

// tan(pi * f / fs) prewarp with clamping so that cutoffs near Nyquist stay stable.
inline float prewarp(float f, float fs) {
    const float w = kPi * clampf(f, 1.f, 0.49f * fs) / fs;
    return std::tan(w);
}

// ---------------------------------------------------------------------------------------------
// parameter specs: terse builders for BB_REGISTER_EFFECT tables
inline ParamSpec pLin(const char* id, const char* name, float mn, float mx, float def,
                      const char* unit = "", float step = 0.f) {
    ParamSpec p;
    p.id = id; p.name = name; p.min = mn; p.max = mx; p.def = def; p.unit = unit;
    p.curve = Curve::Linear; p.type = ParamType::Float; p.step = step;
    return p;
}
inline ParamSpec pLog(const char* id, const char* name, float mn, float mx, float def,
                      const char* unit = "") {
    ParamSpec p = pLin(id, name, mn, mx, def, unit);
    p.curve = Curve::Log;
    return p;
}
inline ParamSpec pPct(const char* id, const char* name, float def) {
    return pLin(id, name, 0.f, 100.f, def, "%");
}
inline ParamSpec pDb(const char* id, const char* name, float mn, float mx, float def) {
    return pLin(id, name, mn, mx, def, "dB");
}
inline ParamSpec pEnum(const char* id, const char* name, std::initializer_list<const char*> opts,
                       int def) {
    ParamSpec p;
    p.id = id; p.name = name; p.min = 0.f; p.max = float(opts.size() - 1); p.def = float(def);
    p.type = ParamType::Enum; p.step = 1.f;
    for (const char* o : opts) p.options.emplace_back(o);
    return p;
}
inline ParamSpec pBool(const char* id, const char* name, bool def) {
    ParamSpec p;
    p.id = id; p.name = name; p.min = 0.f; p.max = 1.f; p.def = def ? 1.f : 0.f;
    p.type = ParamType::Bool; p.step = 1.f;
    return p;
}

// ---------------------------------------------------------------------------------------------
// one-pole parameter smoother. setTime(ms, rate): rate = fs for per-sample next(), fs/kSub for
// per-sub-block next().
inline float onePoleCoef(float ms, float rate) {
    if (ms <= 0.f) return 1.f;
    return 1.f - std::exp(-1.f / (0.001f * ms * rate));
}
struct Smoother {
    float cur = 0.f, target = 0.f, a = 1.f;
    void setTime(float ms, float rate) { a = onePoleCoef(ms, rate); }
    void set(float t) { target = t; }
    void snap(float t) { cur = target = t; }
    void snap() { cur = target; }
    // glide unless this is the initial setParam after prepare (primed == false)
    void set(float t, bool primed) { if (primed) target = t; else snap(t); }
    float next() {
        cur += a * (target - cur);
        if (std::fabs(target - cur) < 1e-7f * (1.f + std::fabs(target))) cur = target;
        return cur;
    }
    bool settled() const { return cur == target; }
};

// ---------------------------------------------------------------------------------------------
// biquad (transposed direct form II) + RBJ cookbook designs (computed in double)
struct BiquadCoefs {
    float b0 = 1.f, b1 = 0.f, b2 = 0.f, a1 = 0.f, a2 = 0.f;
};
struct Biquad {
    BiquadCoefs c;
    float z1 = 0.f, z2 = 0.f;
    inline float process(float x) {
        const float y = c.b0 * x + z1;
        z1 = c.b1 * x - c.a1 * y + z2;
        z2 = c.b2 * x - c.a2 * y;
        return y;
    }
    void reset() { z1 = z2 = 0.f; }
    void flushState() { z1 = flush(z1); z2 = flush(z2); }
};

namespace rbj {
inline BiquadCoefs norm(double b0, double b1, double b2, double a0, double a1, double a2) {
    BiquadCoefs c;
    c.b0 = float(b0 / a0); c.b1 = float(b1 / a0); c.b2 = float(b2 / a0);
    c.a1 = float(a1 / a0); c.a2 = float(a2 / a0);
    return c;
}
inline double w0(double fs, double f) {
    f = std::min(std::max(f, 1.0), 0.49 * fs);
    return 2.0 * 3.14159265358979323846 * f / fs;
}
inline BiquadCoefs lowpass(double fs, double f, double q) {
    const double w = w0(fs, f), cw = std::cos(w), al = std::sin(w) / (2 * q);
    return norm((1 - cw) / 2, 1 - cw, (1 - cw) / 2, 1 + al, -2 * cw, 1 - al);
}
inline BiquadCoefs highpass(double fs, double f, double q) {
    const double w = w0(fs, f), cw = std::cos(w), al = std::sin(w) / (2 * q);
    return norm((1 + cw) / 2, -(1 + cw), (1 + cw) / 2, 1 + al, -2 * cw, 1 - al);
}
// constant 0 dB peak gain bandpass
inline BiquadCoefs bandpass(double fs, double f, double q) {
    const double w = w0(fs, f), cw = std::cos(w), al = std::sin(w) / (2 * q);
    return norm(al, 0, -al, 1 + al, -2 * cw, 1 - al);
}
inline BiquadCoefs notch(double fs, double f, double q) {
    const double w = w0(fs, f), cw = std::cos(w), al = std::sin(w) / (2 * q);
    return norm(1, -2 * cw, 1, 1 + al, -2 * cw, 1 - al);
}
inline BiquadCoefs allpass(double fs, double f, double q) {
    const double w = w0(fs, f), cw = std::cos(w), al = std::sin(w) / (2 * q);
    return norm(1 - al, -2 * cw, 1 + al, 1 + al, -2 * cw, 1 - al);
}
inline BiquadCoefs peaking(double fs, double f, double q, double db) {
    const double A = std::pow(10.0, db / 40), w = w0(fs, f), cw = std::cos(w),
                 al = std::sin(w) / (2 * q);
    return norm(1 + al * A, -2 * cw, 1 - al * A, 1 + al / A, -2 * cw, 1 - al / A);
}
// shelves with slope S = 1 (q = 0.7071 equivalent) unless q given
inline BiquadCoefs lowShelf(double fs, double f, double db, double q = 0.70710678) {
    const double A = std::pow(10.0, db / 40), w = w0(fs, f), cw = std::cos(w),
                 al = std::sin(w) / (2 * q), sA = 2 * std::sqrt(A) * al;
    return norm(A * ((A + 1) - (A - 1) * cw + sA), 2 * A * ((A - 1) - (A + 1) * cw),
                A * ((A + 1) - (A - 1) * cw - sA), (A + 1) + (A - 1) * cw + sA,
                -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sA);
}
inline BiquadCoefs highShelf(double fs, double f, double db, double q = 0.70710678) {
    const double A = std::pow(10.0, db / 40), w = w0(fs, f), cw = std::cos(w),
                 al = std::sin(w) / (2 * q), sA = 2 * std::sqrt(A) * al;
    return norm(A * ((A + 1) + (A - 1) * cw + sA), -2 * A * ((A - 1) + (A + 1) * cw),
                A * ((A + 1) + (A - 1) * cw - sA), (A + 1) - (A - 1) * cw + sA,
                2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sA);
}
// first-order sections expressed as biquads (b2 = a2 = 0)
inline BiquadCoefs lowpass1(double fs, double f) {
    const double k = std::tan(w0(fs, f) / 2);
    return norm(k, k, 0, 1 + k, k - 1, 0);
}
inline BiquadCoefs highpass1(double fs, double f) {
    const double k = std::tan(w0(fs, f) / 2);
    return norm(1, -1, 0, 1 + k, k - 1, 0);
}
}  // namespace rbj

// magnitude response of a biquad at frequency f (for tests / auto gain)
inline float biquadMag(const BiquadCoefs& c, double fs, double f) {
    const double w = 2.0 * 3.14159265358979323846 * f / fs;
    const double cr = std::cos(w), ci = -std::sin(w), c2r = std::cos(2 * w), c2i = -std::sin(2 * w);
    const double nr = c.b0 + c.b1 * cr + c.b2 * c2r, ni = c.b1 * ci + c.b2 * c2i;
    const double dr = 1 + c.a1 * cr + c.a2 * c2r, di = c.a1 * ci + c.a2 * c2i;
    return float(std::sqrt((nr * nr + ni * ni) / (dr * dr + di * di)));
}

// ---------------------------------------------------------------------------------------------
// TPT one-pole (stable under fast modulation)
struct OnePole {
    float g = 0.f, s = 0.f;
    void set(float fs, float fc) { const float w = prewarp(fc, fs); g = w / (1.f + w); }
    inline float lp(float x) {
        const float v = (x - s) * g;
        const float y = v + s;
        s = y + v;
        return y;
    }
    inline float hp(float x) { return x - lp(x); }
    void reset() { s = 0.f; }
    void flushState() { s = flush(s); }
};

// Cytomic/Simper TPT state-variable filter. setG() lets callers modulate per sample cheaply.
struct Svf {
    float g = 0.f, k = 1.f, a1 = 1.f, a2 = 0.f, a3 = 0.f;
    float ic1 = 0.f, ic2 = 0.f;
    float lp = 0.f, bp = 0.f, hp = 0.f;
    void set(float fs, float fc, float q) { setG(prewarp(fc, fs), 1.f / std::max(q, 0.05f)); }
    void setG(float g_, float k_) {
        g = g_; k = k_;
        a1 = 1.f / (1.f + g * (g + k));
        a2 = g * a1;
        a3 = g * a2;
    }
    inline void tick(float v0) {
        const float v3 = v0 - ic2;
        const float v1 = a1 * ic1 + a2 * v3;
        const float v2 = ic2 + a2 * ic1 + a3 * v3;
        ic1 = 2.f * v1 - ic1;
        ic2 = 2.f * v2 - ic2;
        lp = v2; bp = v1; hp = v0 - k * v1 - v2;
    }
    void reset() { ic1 = ic2 = lp = bp = hp = 0.f; }
    void flushState() { ic1 = flush(ic1); ic2 = flush(ic2); }
};

// DC blocker (one-pole high-pass, ~5-20 Hz)
struct DcBlock {
    float r = 0.999f, x1 = 0.f, y1 = 0.f;
    void set(float fs, float fc) { r = 1.f - kTwoPi * fc / fs; }
    inline float process(float x) {
        const float y = x - x1 + r * y1;
        x1 = x; y1 = y;
        return y;
    }
    void reset() { x1 = y1 = 0.f; }
    void flushState() { x1 = flush(x1); y1 = flush(y1); }
};

// Linkwitz-Riley 4th-order crossover: lo + hi sums to an allpass (flat magnitude).
// Use it for "low keep" (clean sub below f, effect above) and modulation low cuts.
struct Crossover {
    Biquad lp1, lp2, hp1, hp2;
    void set(float fs, float f) {
        const BiquadCoefs l = rbj::lowpass(fs, f, 0.70710678), h = rbj::highpass(fs, f, 0.70710678);
        lp1.c = lp2.c = l;
        hp1.c = hp2.c = h;
    }
    inline void split(float x, float& lo, float& hi) {
        lo = lp2.process(lp1.process(x));
        hi = hp2.process(hp1.process(x));
    }
    void reset() { lp1.reset(); lp2.reset(); hp1.reset(); hp2.reset(); }
    void flushState() { lp1.flushState(); lp2.flushState(); hp1.flushState(); hp2.flushState(); }
};

// ---------------------------------------------------------------------------------------------
// Polyphase IIR half-band filters (de Soras "hiir" design: two allpass chains).
// H(z) = 0.5 * (A0(z^2) + z^-1 A1(z^2)); coefficients alternate between A0 and A1.
struct HalfbandDesign {
    static double ipow(double x, int n) {
        double r = 1;
        for (int i = 0; i < n; ++i) r *= x;
        return r;
    }
    // transition: normalised to the high rate (e.g. 0.04 -> passband to 0.21*fs_high)
    static void design(double* coefs, int n, double transition) {
        const double PI = 3.14159265358979323846;
        double k = std::tan((1 - transition * 2) * PI / 4);
        k *= k;
        const double kksqrt = std::pow(1 - k * k, 0.25);
        const double e = 0.5 * (1 - kksqrt) / (1 + kksqrt);
        const double e2 = e * e, e4 = e2 * e2;
        const double q = e * (1 + e4 * (2 + e4 * (15 + 150 * e4)));
        const int order = n * 2 + 1;
        for (int idx = 0; idx < n; ++idx) {
            const int c = idx + 1;
            double num = 0, t;
            int i = 0, j = 1;
            do {
                t = ipow(q, i * (i + 1)) * std::sin((i * 2 + 1) * c * PI / order) * j;
                num += t; j = -j; ++i;
            } while (std::fabs(t) > 1e-100);
            double den = 0;
            i = 1; j = -1;
            do {
                t = ipow(q, i * i) * std::cos(i * 2 * c * PI / order) * j;
                den += t; j = -j; ++i;
            } while (std::fabs(t) > 1e-100);
            num *= std::pow(q, 0.25);
            den += 0.5;
            const double ww = num / den, wwsq = ww * ww;
            const double x = std::sqrt((1 - wwsq * k) * (1 - wwsq / k)) / (1 + wwsq);
            coefs[idx] = (1 - x) / (1 + x);
        }
    }
};

// One 2x stage (up and down share the design but keep separate state). Max 12 coefficients.
struct Halfband {
    static constexpr int kMax = 12;
    int n = 0;
    float c[kMax] = {};
    float ux[kMax] = {}, uy[kMax] = {};  // upsampler states
    float dx[kMax] = {}, dy[kMax] = {};  // downsampler states
    void init(int ncoefs, double transition) {
        n = std::min(ncoefs, kMax);
        double d[kMax];
        HalfbandDesign::design(d, n, transition);
        for (int i = 0; i < n; ++i) c[i] = float(d[i]);
        reset();
    }
    void reset() {
        for (int i = 0; i < kMax; ++i) ux[i] = uy[i] = dx[i] = dy[i] = 0.f;
    }
    void flushState() {
        for (int i = 0; i < n; ++i) {
            ux[i] = flush(ux[i]); uy[i] = flush(uy[i]);
            dx[i] = flush(dx[i]); dy[i] = flush(dy[i]);
        }
    }
    // allpass section in low-rate terms: y = a*(x - y1) + x1
    inline void up(float x, float& o0, float& o1) {
        float a = x, b = x;
        for (int i = 0; i < n; i += 2) {
            const float y = c[i] * (a - uy[i]) + ux[i];
            ux[i] = a; uy[i] = y; a = y;
        }
        for (int i = 1; i < n; i += 2) {
            const float y = c[i] * (b - uy[i]) + ux[i];
            ux[i] = b; uy[i] = y; b = y;
        }
        o0 = a; o1 = b;
    }
    // in0 is the earlier sample, in1 the later
    inline float down(float in0, float in1) {
        float a = in1, b = in0;
        for (int i = 0; i < n; i += 2) {
            const float y = c[i] * (a - dy[i]) + dx[i];
            dx[i] = a; dy[i] = y; a = y;
        }
        for (int i = 1; i < n; i += 2) {
            const float y = c[i] * (b - dy[i]) + dx[i];
            dx[i] = b; dy[i] = y; b = y;
        }
        return 0.5f * (a + b);
    }
};

// 2x or 4x oversampler. Usage per input sample:
//   float hi[4]; os.up(x, hi); for (k < os.factor) hi[k] = shape(hi[k]); y = os.down(hi);
struct Oversampler {
    int factor = 1;
    Halfband s1, s2;  // s1: fs <-> 2fs (steep), s2: 2fs <-> 4fs (gentle)
    void init(int f) {
        factor = (f >= 4) ? 4 : (f >= 2 ? 2 : 1);
        s1.init(10, 0.0415);  // ~ -95 dB stopband, passband to ~20 kHz at 48k
        s2.init(4, 0.21);     // images of a band-limited 2x signal: wide transition is enough
    }
    void reset() { s1.reset(); s2.reset(); }
    void flushState() { s1.flushState(); s2.flushState(); }
    inline void up(float x, float* out) {
        if (factor == 1) { out[0] = x; return; }
        float a, b;
        s1.up(x, a, b);
        if (factor == 2) { out[0] = a; out[1] = b; return; }
        s2.up(a, out[0], out[1]);
        s2.up(b, out[2], out[3]);
    }
    // fixed-4x variants (init(4) required): branch-free and always write all four samples
    inline void up4(float x, float* out) {
        float a, b;
        s1.up(x, a, b);
        s2.up(a, out[0], out[1]);
        s2.up(b, out[2], out[3]);
    }
    inline float down4(const float* in) {
        const float a = s2.down(in[0], in[1]);
        const float b = s2.down(in[2], in[3]);
        return s1.down(a, b);
    }
    inline float down(const float* in) {
        if (factor == 1) return in[0];
        if (factor == 2) return s1.down(in[0], in[1]);
        const float a = s2.down(in[0], in[1]);
        const float b = s2.down(in[2], in[3]);
        return s1.down(a, b);
    }
};

// ---------------------------------------------------------------------------------------------
// delay line (power-of-two ring buffer). push() then read(d): d = 0 is the newest sample.
struct DelayLine {
    std::vector<float> buf;
    uint32_t mask = 0, w = 0;
    void init(int maxSamples) {
        uint32_t n = 1;
        while (n < uint32_t(maxSamples) + 8u) n <<= 1;
        buf.assign(n, 0.f);
        mask = n - 1;
        w = 0;
    }
    void clear() { std::fill(buf.begin(), buf.end(), 0.f); w = 0; }
    inline void push(float x) { buf[w] = x; w = (w + 1) & mask; }
    inline float at(int d) const { return buf[(w - 1u - uint32_t(d)) & mask]; }
    inline float readLinear(float d) const {
        const int i = int(d);
        const float f = d - float(i);
        const float a = at(i), b = at(i + 1);
        return a + (b - a) * f;
    }
    // 4-point Hermite; needs d >= 1
    inline float readHermite(float d) const {
        const int i = int(d);
        const float f = d - float(i);
        const float xm1 = at(i - 1), x0 = at(i), x1 = at(i + 1), x2 = at(i + 2);
        const float c1 = 0.5f * (x1 - xm1);
        const float c2 = xm1 - 2.5f * x0 + 2.f * x1 - 0.5f * x2;
        const float c3 = 0.5f * (x2 - xm1) + 1.5f * (x0 - x1);
        return ((c3 * f + c2) * f + c1) * f + x0;
    }
    int size() const { return int(mask + 1); }
};

// ---------------------------------------------------------------------------------------------
// LFO phase accumulator (phase in [0,1))
struct Lfo {
    float phase = 0.f, inc = 0.f;
    void setRate(float hz, float fs) { inc = hz / fs; }
    inline float tick() {
        phase += inc;
        if (phase >= 1.f) phase -= 1.f;
        return phase;
    }
    void advance(int n) { phase += inc * float(n); phase -= std::floor(phase); }
};
inline float triFromPhase(float p) { return 1.f - 4.f * std::fabs(p - 0.5f); }  // -1 at p=0, +1 at p=0.5
inline float wrap01(float p) { return p - std::floor(p); }

// envelope follower on |x| (attack/release in ms)
struct EnvFollower {
    float env = 0.f, aA = 1.f, aR = 1.f;
    void set(float atkMs, float relMs, float fs) { aA = onePoleCoef(atkMs, fs); aR = onePoleCoef(relMs, fs); }
    inline float tick(float x) {
        const float r = std::fabs(x);
        env += (r > env ? aA : aR) * (r - env);
        return env;
    }
    void reset() { env = 0.f; }
    void flushState() { env = flush(env); }
};

// xorshift32 noise in [-1, 1)
struct Rng {
    uint32_t s = 0x9E3779B9u;
    inline uint32_t nextU() { s ^= s << 13; s ^= s >> 17; s ^= s << 5; return s; }
    inline float next() { return float(int32_t(nextU())) * (1.f / 2147483648.f); }
};

// ---------------------------------------------------------------------------------------------
// static waveshapers shared by the drive effects (also dumped by the test harness for plots)
// soft asymmetric "diode pair" (overdrive): tanh with a small DC bias for even harmonics
inline float shapeDiodeSoft(float x) {
    const float b = 0.12f;
    return fastTanh(x + b) - fastTanh(b);
}
// harder clip with a rounded knee (RAT-ish silicon to ground): x / (1+|x|^p)^(1/p), p = 2.5
inline float shapeHard(float x) {
    const float ax = std::fabs(x);
    if (ax < 1e-6f) return x;
    return x / std::pow(1.f + std::pow(ax, 2.5f), 0.4f);
}
// cheaper hard knee via rational: x / sqrt(1 + x^2) blended toward clip
inline float shapeRational(float x) { return x / std::sqrt(1.f + x * x); }
// triode-ish: grid conduction clips the positive swing early (ceiling 1); the negative swing
// heads into cutoff more softly (ceiling 1/0.55). Unity slope at 0; the asymmetry gives even
// harmonics, so follow it with a DC blocker.
inline float shapeTube(float x) {
    return x >= 0.f ? fastTanh(x) : fastTanh(0.55f * x) * (1.f / 0.55f);
}

}  // namespace dsp
}  // namespace bb
