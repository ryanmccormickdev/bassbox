// Bass cabinet voicings as tuned biquad cascades (used by cab.cpp and amp.cpp's built-in cab).
// Each voicing approximates a published-style response: box/port resonance and low roll-off,
// low-mid body, cone midrange colour, a breakup dip and the steep top-end roll-off of big cones.
// A voicing is normalised over the bass band (40 Hz - 2 kHz, bass-weighted) so switching cabs
// keeps the loudness. Mic = off-axis amount (darker), Low = proximity/low shelf.
#pragma once

#include "dsp.h"

namespace bb {
namespace dsp {

struct CabBand {
    char type;  // 'h' HP, 'l' LP, 'p' peak, 'L' low shelf, 'H' high shelf
    float f, q, db;
};
constexpr int kCabBands = 9;
constexpr int kCabTypes = 4;
// 1x15, 4x10, 8x10, 2x12
constexpr CabBand kCabVoicing[kCabTypes][kCabBands] = {
    {   // 1x15: deep, round, early roll-off (B-15 / ported 15)
        {'h', 42.f, 0.9f, 0}, {'p', 75.f, 1.0f, 3.f}, {'p', 380.f, 1.0f, -3.f}, {'p', 850.f, 1.3f, 2.5f},
        {'p', 1800.f, 3.0f, -4.f}, {'l', 2600.f, 0.8f, 0}, {'l', 4200.f, 0.6f, 0}, {'p', 160.f, 1.2f, 1.f},
        {'p', 1200.f, 2.f, 1.f}},
    {   // 4x10: tight lows, punchy low mids, 10" cone growl around 2 kHz
        {'h', 58.f, 1.1f, 0}, {'p', 110.f, 1.0f, 3.f}, {'p', 350.f, 1.0f, -2.5f}, {'p', 2100.f, 1.5f, 4.f},
        {'p', 3400.f, 4.0f, -4.f}, {'l', 4600.f, 0.9f, 0}, {'l', 6000.f, 0.6f, 0}, {'p', 800.f, 1.4f, 1.f},
        {'p', 1300.f, 3.f, -1.5f}},
    {   // 8x10: huge low-mids, honky upper mids, dark top (many cones beam and comb)
        {'h', 52.f, 1.0f, 0}, {'p', 100.f, 0.8f, 4.f}, {'p', 210.f, 1.0f, 2.f}, {'p', 520.f, 1.2f, -2.f},
        {'p', 1200.f, 1.2f, 3.f}, {'p', 2600.f, 3.0f, -5.f}, {'l', 3600.f, 1.0f, 0}, {'l', 4600.f, 0.6f, 0},
        {'p', 1800.f, 3.f, 1.5f}},
    {   // 2x12: mid-forward, brighter presence, lighter lows
        {'h', 68.f, 1.2f, 0}, {'p', 120.f, 1.2f, 3.f}, {'p', 450.f, 1.0f, -2.f}, {'p', 1800.f, 1.2f, 4.f},
        {'p', 3200.f, 2.0f, 3.f}, {'p', 4000.f, 4.0f, -4.f}, {'l', 5000.f, 1.1f, 0}, {'l', 6500.f, 0.6f, 0},
        {'p', 950.f, 2.f, -1.f}},
};

inline BiquadCoefs cabBandCoefs(const CabBand& b, float fs) {
    switch (b.type) {
        case 'h': return rbj::highpass(fs, b.f, b.q);
        case 'l': return rbj::lowpass(fs, b.f, b.q);
        case 'L': return rbj::lowShelf(fs, b.f, b.db, b.q);
        case 'H': return rbj::highShelf(fs, b.f, b.db, b.q);
        default: return rbj::peaking(fs, b.f, b.q, b.db);
    }
}

// one voicing's filter cascade
struct CabChain {
    Biquad bq[kCabBands];
    float norm = 1.f;
    void design(int type, float fs) {
        double num = 0, den = 0;
        for (int k = 0; k < kCabBands; ++k) bq[k].c = cabBandCoefs(kCabVoicing[type][k], fs);
        for (int i = 0; i < 48; ++i) {
            const double f = 40.0 * std::pow(50.0, i / 47.0);  // 40 Hz .. 2 kHz
            double h = 1;
            for (int k = 0; k < kCabBands; ++k) h *= biquadMag(bq[k].c, fs, f);
            const double w = 1.0 / (1.0 + (f / 250.0) * (f / 250.0));  // DI bass spectrum weight
            num += w * h * h;
            den += w;
        }
        norm = float(std::sqrt(den / num));
    }
    inline float process(float x) {
        for (int k = 0; k < kCabBands; ++k) x = bq[k].process(x);
        return x * norm;
    }
    void reset() { for (auto& b : bq) b.reset(); }
    void flushState() { for (auto& b : bq) b.flushState(); }
};

// Cab with click-free type switching (30 ms crossfade), mic (off-axis LPF + presence shelf) and
// low (proximity shelf). Call setType/setMic/setLow from setParam, process per sample, and
// tick() once per kSub samples.
struct CabSim {
    float fs = 48000.f;
    CabChain chain[2];
    int cur = 0, type = 1, pendingType = -1;
    float xf = 1.f, xfInc = 0.f;  // crossfade from chain[1-cur] to chain[cur]
    Smoother mic, low;
    Biquad micLp, micShelf, lowShelf;
    bool primed = false;

    void prepare(float fs_) {
        fs = fs_;
        mic.setTime(40.f, fs / kSub);
        low.setTime(40.f, fs / kSub);
        xfInc = 1.f / (0.03f * fs);
        chain[0].design(type, fs);
        chain[1].design(type, fs);
        primed = false;
        updateTone();
    }
    void reset() {
        chain[0].reset();
        chain[1].reset();
        micLp.reset(); micShelf.reset(); lowShelf.reset();
        mic.snap(); low.snap();
        xf = 1.f;
        if (pendingType >= 0) { type = pendingType; pendingType = -1; chain[cur].design(type, fs); }
        updateTone();
    }
    void setType(int t) {
        t = std::max(0, std::min(kCabTypes - 1, t));
        if (!primed) { type = t; chain[cur].design(t, fs); return; }
        if (t == type && pendingType < 0) return;
        pendingType = t;
    }
    void setMic(float pct) { mic.set(pct, primed); if (!primed) updateTone(); }
    void setLow(float db) { low.set(db, primed); if (!primed) updateTone(); }
    void updateTone() {
        const float m = mic.cur * 0.01f;
        micLp.c = rbj::lowpass(fs, 12000.f * std::pow(0.22f, m), 0.6);
        micShelf.c = rbj::highShelf(fs, 2500.0, 3.f - 7.f * m, 0.7);
        lowShelf.c = rbj::lowShelf(fs, 110.0, low.cur, 0.7);
    }
    // per sub-block housekeeping (coefficient design happens here, on the audio thread, but it is
    // bounded: 9 biquad designs at most once per 16 samples while a switch is pending)
    void tick() {
        if (pendingType >= 0 && xf >= 1.f) {
            cur = 1 - cur;
            chain[cur].reset();
            chain[cur].design(pendingType, fs);
            type = pendingType;
            pendingType = -1;
            xf = 0.f;
        }
        if (!mic.settled() || !low.settled()) { mic.next(); low.next(); updateTone(); }
    }
    inline float process(float x) {
        float y = chain[cur].process(x);
        if (xf < 1.f) {
            const float o = chain[1 - cur].process(x);
            y = o + xf * (y - o);
            xf += xfInc;
        }
        return lowShelf.process(micShelf.process(micLp.process(y)));
    }
    void flushState() {
        chain[0].flushState(); chain[1].flushState();
        micLp.flushState(); micShelf.flushState(); lowShelf.flushState();
    }
};

}  // namespace dsp
}  // namespace bb
