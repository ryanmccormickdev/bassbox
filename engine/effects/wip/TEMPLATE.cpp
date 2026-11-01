// TEMPLATE: copy to engine/effects/wip/<id>.cpp, rename, build/test it there, then move it to
// engine/effects/<id>.cpp (see engine/effects/README.md). This file lives in wip/ so the engine
// build (which globs engine/effects/*.cpp) never compiles it.
//
// Example: "Tilt Gain" - a gain plus a tilt EQ, showing the patterns every effect uses:
// param smoothing, coefficient updates per 16-sample sub-block, denormal flushing, activity().
#include "dsp.h"  // shared helpers: Smoother, Biquad + rbj:: designs, Svf, Crossover, Oversampler, ...

namespace {  // everything private to this file, so names never clash with other effects
using namespace bb;
using namespace bb::dsp;

class TiltGain final : public Effect {
public:
    enum { kGain, kTilt };  // indices = order of the params in the registration below

    // CONTROL thread: allocate and precompute here (vectors, delay lines, tables).
    void prepare(float fs, int /*maxBlock*/) override {
        fs_ = fs;
        gain_.setTime(20.f, fs);        // per-sample smoother, 20 ms
        tilt_.setTime(30.f, fs / kSub);  // per-sub-block smoother (coefficients cost a tan())
        primed_ = false;                 // the host now sends every param once: snap, don't glide
        reset();
    }
    // AUDIO thread: clear state, no allocation.
    void reset() override {
        eq_.reset();
        gain_.snap();
        tilt_.snap();
        update();
    }
    // AUDIO thread, at block start, only when a value changes. Values are in real units and
    // already clamped to [min, max]. Enum/bool values arrive as 0, 1, 2, ...
    void setParam(int i, float v) override {
        if (i == kGain) gain_.set(dbToLin(v), primed_);
        else if (i == kTilt) tilt_.set(v, primed_);
        if (!primed_) update();
    }
    // AUDIO thread: mono, in place, n <= maxBlock. No allocation, locks, syscalls, logging,
    // exceptions or unbounded loops.
    void process(float* x, int n) override {
        primed_ = true;
        float peak = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!tilt_.settled()) { tilt_.next(); update(); }  // glide the EQ, no zipper noise
            for (int i = i0; i < i0 + m; ++i) {
                x[i] = eq_.process(x[i]) * norm_ * gain_.next();
                peak = std::max(peak, std::fabs(x[i]));
            }
        }
        eq_.flushState();   // recursive states never go denormal
        flushBlock(x, n);   // and neither does the output
        activity_ = std::max(peak, activity_ * 0.9f);  // 0..1, read by the UI for animation
        activity_ = clampf(activity_, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    void update() {
        eq_.c = rbj::highShelf(fs_, 650.0, tilt_.cur, 0.5);
        norm_ = dbToLin(-0.5f * tilt_.cur);  // keep the loudness level-matched
    }
    float fs_ = 48000.f;
    Smoother gain_, tilt_;
    Biquad eq_;
    float norm_ = 1.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

// id (stable, used in presets), display name, category, <= 60-char description,
// params (FIRST TWO = hero params on the chain card), tails, create function.
BB_REGISTER_EFFECT({"tiltgain", "Tilt Gain", "utility", "Clean gain with a tilt tone control.",
                    {bb::dsp::pDb("gain", "Gain", -12.f, 12.f, 0.f),
                     bb::dsp::pDb("tilt", "Tilt", -6.f, 6.f, 0.f)},
                    false, [] { return std::make_unique<TiltGain>(); }});
