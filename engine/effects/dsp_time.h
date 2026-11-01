// bassbox time-domain helpers shared by reverb/shimmer (header-only): an 8-line modulated
// feedback delay network, Schroeder allpass diffusers and a dual-head pitch shifter.
// All allocation happens in init(); tick() is allocation-free.
// Lives in bb::dsp::tfx so names cannot collide (ODR) with other family headers.
#pragma once

#include "dsp.h"

namespace bb {
namespace dsp {
namespace tfx {

// Two cascaded one-poles: a glide whose rate of change also starts smoothly. Use it for delay
// times / line lengths, where a one-pole's instant change of read speed would splatter.
struct Glide2 {
    float a = 1.f, s1 = 0.f, s2 = 0.f, target = 0.f;
    void setTime(float ms, float rate) { a = onePoleCoef(ms, rate); }
    void snap(float t) { target = s1 = s2 = t; }
    void snap() { s1 = s2 = target; }
    void set(float t, bool primed) { if (primed) target = t; else snap(t); }
    inline float next() {
        s1 += a * (target - s1);
        s2 += a * (s1 - s2);
        if (std::fabs(target - s2) < 1e-6f * (1.f + std::fabs(target))) s1 = s2 = target;
        return s2;
    }
    bool settled() const { return s2 == target; }
};

// in-place 8-point fast Walsh-Hadamard transform, orthonormal (scaled by 1/sqrt(8))
inline void hadamard8(float* v) {
    for (int h = 1; h < 8; h <<= 1)
        for (int i = 0; i < 8; i += 2 * h)
            for (int j = i; j < i + h; ++j) {
                const float a = v[j], b = v[j + h];
                v[j] = a + b;
                v[j + h] = a - b;
            }
    for (int i = 0; i < 8; ++i) v[i] *= 0.35355339f;
}

// Schroeder allpass: y = -g*x + x[n-D] + g*y[n-D] (single buffer form)
struct AllpassDiffuser {
    DelayLine buf;
    int d = 1;
    float g = 0.6f;
    void init(int len, float gain) { d = std::max(1, len); g = gain; buf.init(d + 2); }
    void clear() { buf.clear(); }
    inline float tick(float x) {
        const float z = buf.at(d - 1);  // sample written d pushes ago
        const float v = x + g * z;
        buf.push(flush(v));
        return z - g * v;
    }
};

// 8-line FDN. Line lengths = base * scale (scale smoothed by the caller, per sample), each line
// slowly modulated (+-modDepth samples) against metallic ringing, one-pole damping per line,
// orthonormal Hadamard feedback, per-line gains from RT60 so the low-frequency decay time is exact.
struct Fdn8 {
    static constexpr int N = 8;
    DelayLine line[N];
    OnePole damp[N];
    float base[N] = {};
    float g[N] = {};
    float ph[N] = {}, inc[N] = {};
    float modDepth = 6.f;
    float fs = 48000.f;
    float gMean = 0.f;

    void init(float fs_, float maxScale, float modDepthSamples) {
        fs = fs_;
        // mutually prime lengths, 40..85 ms at scale 1 (48 kHz)
        static const float kBase48[N] = {1933.f, 2213.f, 2539.f, 2791.f, 3121.f, 3433.f, 3767.f, 4093.f};
        static const float kRate[N] = {0.31f, 0.43f, 0.57f, 0.67f, 0.79f, 0.89f, 1.03f, 1.13f};
        modDepth = modDepthSamples;
        for (int i = 0; i < N; ++i) {
            base[i] = kBase48[i] * fs / 48000.f;
            line[i].init(int(base[i] * maxScale + modDepth * 2.f + 8.f));
            inc[i] = kRate[i] / fs;
            ph[i] = float(i) * 0.137f;
        }
        clear();
    }
    void clear() {
        for (int i = 0; i < N; ++i) { line[i].clear(); damp[i].reset(); }
    }
    void flushState() { for (int i = 0; i < N; ++i) damp[i].flushState(); }
    void setDamp(float fc) { for (int i = 0; i < N; ++i) damp[i].set(fs, fc); }
    // rt60 in seconds for the current scale
    void setDecay(float rt60, float scale) {
        float s = 0.f;
        for (int i = 0; i < N; ++i) {
            const float L = base[i] * scale + modDepth + 1.f;
            g[i] = std::pow(10.f, -3.f * L / (std::max(0.05f, rt60) * fs));
            s += g[i];
        }
        gMean = s / float(N);
    }
    // returns the mono output; `in` is distributed to all lines with alternating signs
    inline float tick(float in, float scale) {
        float o[N];
        for (int i = 0; i < N; ++i) {
            ph[i] += inc[i];
            if (ph[i] >= 1.f) ph[i] -= 1.f;
            const float d = base[i] * scale + modDepth * (1.f + fastSin2Pi(ph[i]));
            o[i] = line[i].readLinear(d);
        }
        const float y = (o[0] - o[1] + o[2] - o[3] + o[4] - o[5] + o[6] - o[7]) * 0.35355339f;
        float v[N];
        for (int i = 0; i < N; ++i) v[i] = damp[i].lp(o[i]) * g[i];
        hadamard8(v);
        const float u = in * 0.35355339f;
        for (int i = 0; i < N; ++i) line[i].push(flush(v[i] + ((i & 1) ? -u : u)));
        return y;
    }
};

// Dual-head delay-line pitch shifter: two taps sweep a window W half a period apart with
// sin^2 gains (sum = 1). ratio = 2^(semitones/12).
struct DualHeadShifter {
    DelayLine buf;
    float W = 2400.f, ph = 0.f, step = 0.f;
    void init(int windowSamples) {
        W = float(std::max(64, windowSamples));
        buf.init(int(W) + 8);
        ph = 0.f;
    }
    void clear() { buf.clear(); ph = 0.f; }
    void setRatio(float r) { step = (1.f - r) / W; }
    inline float tick(float x) {
        buf.push(x);
        ph += step;
        ph -= std::floor(ph);
        float p2 = ph + 0.5f;
        if (p2 >= 1.f) p2 -= 1.f;
        const float s1 = fastSin2Pi(0.5f * ph), s2 = fastSin2Pi(0.5f * p2);  // sin(pi*ph)
        const float a = buf.readHermite(2.f + W * ph) * (s1 * s1);
        const float b = buf.readHermite(2.f + W * p2) * (s2 * s2);
        return a + b;
    }
};

}  // namespace tfx
}  // namespace dsp
}  // namespace bb
