// Shared helpers for the modulation effects (chorus, flanger, phaser, vibrato, univibe, ringmod,
// tremolo). Header-only; allocation-free on the audio thread.
#pragma once

#include "dsp.h"

namespace bb {
namespace dsp {

// tan(x) for x in [0, ~1.45] ([5/4] Pade; rel. err < 1e-5 up to 1.41 rad = 0.45*pi)
inline float tanPade5(float x) {  // tan(x), [5/4] Pade; distinct name from dsp_filter.h fastTan (ODR)
    const float x2 = x * x;
    return x * (945.f + x2 * (-105.f + x2)) / (945.f + x2 * (-420.f + x2 * 15.f));
}

// TPT gain g/(1+g) for a one-pole at f (clamped to [5 Hz, 0.45 fs]), using tanPade5
inline float onePoleG(float f, float invFs) {
    const float w = kPi * clampf(f * invFs, 5.f * invFs, 0.45f);
    const float g = tanPade5(w);
    return g / (1.f + g);
}

// first-order TPT allpass: ap = 2*lp - x (0 .. -180 degrees), modulation-safe
struct Allpass1 {
    float s = 0.f;
    inline float process(float x, float G) {
        const float v = (x - s) * G;
        const float lp = v + s;
        s = lp + v;
        return 2.f * lp - x;
    }
    void reset() { s = 0.f; }
    void flushState() { s = flush(s); }
};

// LR4 low/high split whose crossover frequency glides (log domain, per sub-block). Everything
// below the split stays dry; effects process `hi` only and add `lo` back.
struct LowSplit {
    Crossover xo;
    Smoother f;  // log(Hz)
    float fs = 48000.f;
    void prepare(float fs_) {
        fs = fs_;
        f.setTime(40.f, fs_ / kSub);
    }
    void setFreq(float hz, bool primed) {
        f.set(std::log(clampf(hz, 10.f, 0.4f * fs)), primed);
        if (!primed) xo.set(fs, std::exp(f.cur));
    }
    // call once per sub-block
    inline void tick() {
        if (!f.settled()) {
            f.next();
            xo.set(fs, std::exp(f.cur));
        }
    }
    inline void split(float x, float& lo, float& hi) { xo.split(x, lo, hi); }
    void reset() {
        xo.reset();
        f.snap();
        xo.set(fs, std::exp(f.cur));
    }
    void flushState() { xo.flushState(); }
};

// dry/wet gains for a 0..1 mix with equal-power normalisation (unity at both ends, +3 dB on each
// at 50% so a decorrelated wet keeps the overall loudness)
inline void mixGains(float m, float& dry, float& wet) {
    const float d = 1.f - m;
    const float norm = 1.f / std::sqrt(d * d + m * m);
    dry = d * norm;
    wet = m * norm;
}

}  // namespace dsp
}  // namespace bb
