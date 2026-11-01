// Shared pieces for the pitch effects (octaver, subsynth, pitch): a monophonic bass pitch
// tracker and PolyBLEP oscillator helpers.
//
// PitchTracker: zero-crossing tracker built for bass (low B 30.9 Hz .. ~500 Hz).
//   1. 2nd-order HPF at 20 Hz (rumble, DC).
//   2. 4th-order Butterworth LPF whose cutoff follows 0.7 x the tracked f0 (floor 40 Hz, ceiling
//      400 Hz). Placing the corner *below* f0 puts the 2nd harmonic >= 15 dB under the
//      fundamental for every bass note, so the detector signal is a near-sinusoid. If the
//      tracker ever locks onto a harmonic, the corner lands below the harmonic and the
//      fundamental takes over again: lock errors self-correct downward.
//   3. Lobe-relative hysteresis: the detector arms when the signal dips below -25% of the
//      previous positive lobe; the event is the next upward zero crossing (sub-sample
//      interpolated). Being relative to the last lobe (not a slow envelope) it follows level
//      changes and new notes within one cycle.
//   4. Period validation: an interval within 10% of the current period refines it; a different
//      interval must be confirmed by a second, consistent one before the period switches (note
//      change), so single spurious crossings never cause octave flips.
//   5. Guard: crossings sooner than 0.6 periods after the last accepted one are not passed to
//      flip-flops (they still count as candidates for a genuine note change).
//   6. Recovery: a guard-rejected crossing or two disagreeing deviant intervals mean the
//      detector signal is not a clean sinusoid (e.g. a big jump down from a high note), so the
//      LPF corner drops straight to its 40 Hz floor and climbs back once the period is found.
#pragma once

#include "dsp.h"

namespace bb {
namespace dsp {

struct PitchTracker {
    static constexpr float kMinHz = 27.f, kMaxHz = 520.f;
    float fs = 48000.f;
    Biquad hp;
    Svf lp1, lp2;
    float fcLog = 3.69f, fcCoef = 0.1f;
    int sub = 0;
    float y1 = 0.f;
    float posPeak = 0.f, negPeak = 0.f, lastPos = 0.f, lastNeg = 0.f;
    float amp = 0.f;
    double lobeStart = 0.0;  // time of the last zero crossing (either direction)
    bool armed = false;
    double n = 0.0;  // sample clock (double keeps sub-sample precision for hours)
    double lastRaw = -1e9, lastAcc = -1e9;
    float period = 0.f;  // validated period in samples (0 = unknown)
    float cand = 0.f;
    bool voiced = false;
    bool stable = false;     // last interval agreed with the period (not mid note-change)
    bool dirty = false;      // inconsistent crossings seen: drop the LPF corner to its floor...
    bool floorHold = false;  // ...and keep it there until the period is confirmed again
    float envIn = 0.f, envRel = 0.999f;
    float gateLin = 0.0018f;  // -55 dBFS
    // per-sample outputs
    float y = 0.f;        // near-sinusoidal detector signal (lags the input by the LPF delay)
    bool event = false;   // accepted upward zero crossing happened on this sample
    float eventFrac = 0.f;  // ... this many samples (0..1) before the current sample

    void init(float sampleRate) {
        fs = sampleRate;
        hp.c = rbj::highpass(fs, 20.0, 0.70710678);
        fcCoef = onePoleCoef(20.f, fs / float(kSub));
        envRel = std::exp(-1.f / (0.060f * fs));
        reset();
    }
    void reset() {
        hp.reset(); lp1.reset(); lp2.reset();
        fcLog = std::log(40.f);
        setCutoff();
        sub = 0;
        y1 = posPeak = negPeak = lastPos = lastNeg = amp = 0.f;
        lobeStart = 0.0;
        armed = false;
        n = 0.0;
        lastRaw = lastAcc = -1e9;
        period = cand = 0.f;
        voiced = false;
        dirty = floorHold = false;
        stable = false;
        envIn = 0.f;
        y = 0.f; event = false; eventFrac = 0.f;
    }
    void setCutoff() {
        const float g = prewarp(std::exp(fcLog), fs);
        lp1.setG(g, 1.f / 0.5411961f);
        lp2.setG(g, 1.f / 1.3065630f);
    }
    float hz() const { return period > 0.f ? fs / period : 0.f; }
    // detector signal normalised to ~ +-1 (clamped), for OC-2 style polarity flipping
    float normalized() const {
        if (amp < 1e-9f) return 0.f;
        return clampf(y / amp, -1.5f, 1.5f);
    }
    void flushState() {
        hp.flushState(); lp1.flushState(); lp2.flushState();
        envIn = flush(envIn); y1 = flush(y1);
    }

    inline void tick(float x) {
        const float xh = hp.process(x);
        const float r = std::fabs(xh);
        envIn = r > envIn ? r : envIn * envRel;
        if (++sub >= kSub) {
            sub = 0;
            float target = (period > 0.f && !floorHold) ? 0.7f * fs / period : 40.f;
            target = clampf(target, 40.f, 400.f);
            if (dirty) {
                fcLog = std::log(40.f);  // floor: the fundamental dominates for any bass note
                dirty = false;
                floorHold = true;
            } else {
                fcLog += fcCoef * (std::log(target) - fcLog);
            }
            setCutoff();
        }
        lp1.tick(xh);
        lp2.tick(lp1.lp);
        const float v = lp2.lp;
        event = false;
        // a lobe counts only if it lasts long enough to belong to a <= 2*kMaxHz signal, so
        // wiggles around zero never reset the hysteresis reference
        const double minLobe = 0.25 * fs / kMaxHz;
        if (v >= 0.f) {
            if (y1 < 0.f) {  // upward zero crossing
                if (n - lobeStart >= minLobe) { lastNeg = negPeak; updateAmp(); }
                lobeStart = n;
                negPeak = 0.f;
                if (armed && envIn > gateLin) {
                    const float frac = y1 / (y1 - v);  // 0..1 between previous and current sample
                    onCrossing(n - 1.0 + double(frac));
                    eventFrac = 1.f - frac;
                }
                armed = false;
            }
            posPeak = std::max(posPeak, v);
        } else {
            if (y1 >= 0.f) {  // downward zero crossing
                if (n - lobeStart >= minLobe) { lastPos = posPeak; updateAmp(); }
                lobeStart = n;
                posPeak = 0.f;
            }
            negPeak = std::min(negPeak, v);
            if (v < -0.25f * lastPos) armed = true;
        }
        y1 = v;
        y = v;
        n += 1.0;
        // voicing: a validated period, enough level, and a recent crossing
        const float since = float(n - lastRaw);
        voiced = period > 0.f && envIn > gateLin && since < 1.6f * period + 0.004f * fs;
        if (!voiced && since > 0.3f * fs) { period = 0.f; cand = 0.f; stable = false; }
    }

private:
    // amplitude of the detector signal: follows each lobe peak (rises fast, falls in ~3 lobes)
    void updateAmp() {
        const float t = std::max(lastPos, -lastNeg);
        amp += (t > amp ? 0.7f : 0.5f) * (t - amp);
    }
    static bool near(float a, float b, float tol) { return std::fabs(a - b) < tol * b; }
    void onCrossing(double tc) {
        const float p = float(tc - lastRaw);
        lastRaw = tc;
        const float pMin = fs / kMaxHz, pMax = fs / kMinHz;
        if (p >= pMin && p <= pMax) {
            if (period > 0.f && near(p, period, 0.1f)) {
                period += 0.5f * (p - period);
                cand = 0.f;
                floorHold = false;
                stable = true;
            } else if (cand > 0.f && near(p, cand, 0.1f)) {
                period = 0.5f * (p + cand);  // two consistent intervals: (new) note
                cand = 0.f;
                floorHold = false;
                stable = true;
            } else {
                // two deviating intervals in a row that disagree: the detector signal carries
                // harmonics (corner too high for a new, lower note) -> recover at the floor
                if (cand > 0.f && period > 0.f) dirty = true;
                cand = p;
                stable = false;
            }
        } else if (p > pMax) {
            cand = 0.f;  // first crossing after a gap
            stable = false;
        }
        const float pa = float(tc - lastAcc);
        bool accept = pa > 0.9f * pMin;
        if (accept && period > 0.f && pa < 0.6f * period) { accept = false; dirty = true; }
        if (accept) { lastAcc = tc; event = true; }
    }
};

// PolyBLEP residual for a unit step at phase 0 (t in [0,1), dt = phase increment)
inline float polyBlep(float t, float dt) {
    if (t < dt) {
        t /= dt;
        return t + t - t * t - 1.f;
    }
    if (t > 1.f - dt) {
        t = (t - 1.f) / dt;
        return t * t + t + t + 1.f;
    }
    return 0.f;
}

}  // namespace dsp
}  // namespace bb
