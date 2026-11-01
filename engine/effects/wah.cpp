// Wah: bass wah in the Cry Baby Bass spirit.
// The input is split with an LR4 crossover at 110 Hz: the sub stays clean and the wah works on
// everything above (so the low end never disappears), recombined in phase. The wah is a
// resonant Simper SVF band-pass (+ a little low-pass body) swept exponentially across the Range.
// Position (0..1) is smoothed for a future expression pedal / knob at ~60 Hz updates. Auto > 0
// sweeps the full range with a smooth LFO; switching between manual and auto crossfades and the
// LFO starts from the current Position. Mix blends the full-range dry signal back in.
#include "dsp.h"
#include "dsp_filter.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr float kLo[3] = {250.f, 350.f, 450.f};
constexpr float kHi[3] = {1500.f, 2200.f, 3000.f};

class Wah final : public Effect {
public:
    enum { kPos, kAuto, kQ, kRange, kMix };
    void prepare(float fs, int) override {
        fs_ = fs;
        pos_.setTime(15.f, fs);
        autoW_.setTime(60.f, fs);
        rate_.setTime(50.f, fs / kSub);
        q_.setTime(30.f, fs / kSub);
        lo_.setTime(40.f, fs / kSub);
        hi_.setTime(40.f, fs / kSub);
        mix_.setTime(20.f, fs);
        xo_.set(fs, 110.f);
        primed_ = false;
        reset();
    }
    void reset() override {
        pos_.snap(); autoW_.snap(); rate_.snap(); q_.snap(); lo_.snap(); hi_.snap(); mix_.snap();
        svf_.reset();
        xo_.reset();
        activity_ = pos_.cur;
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kPos: pos_.set(clampf(v, 0.f, 1.f), primed_); break;
            case kAuto: {
                const bool on = v > 0.01f;
                if (on && autoW_.target < 0.5f) {
                    // start the LFO where the pedal is: p = 0.5 - 0.5 cos(2 pi phase)
                    phase_ = std::acos(clampf(1.f - 2.f * pos_.cur, -1.f, 1.f)) / kTwoPi;
                }
                autoW_.set(on ? 1.f : 0.f, primed_);
                if (on) rate_.set(v, primed_);
                break;
            }
            case kQ: q_.set(std::log(v), primed_); break;
            case kRange: {
                const int r = std::max(0, std::min(2, int(v + 0.5f)));
                lo_.set(std::log2(kLo[r]), primed_);
                hi_.set(std::log2(kHi[r]), primed_);
                break;
            }
            case kMix: mix_.set(0.01f * v, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        float p = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            rate_.next(); q_.next(); lo_.next(); hi_.next();
            const float inc = rate_.cur / fs_;
            const float q = std::exp(q_.cur);
            const float k = 1.f / q;
            const float peak = kPeak * std::sqrt(q * 0.2f);  // a narrower peak is a quieter peak
            const float lo = lo_.cur, span = hi_.cur - lo_.cur;
            for (int i = i0; i < i0 + m; ++i) {
                const float in = x[i];
                phase_ += inc;
                if (phase_ >= 1.f) phase_ -= 1.f;
                float ph = phase_ + 0.25f;
                if (ph >= 1.f) ph -= 1.f;
                const float pl = 0.5f - 0.5f * fastSin2Pi(ph);  // 0.5 - 0.5 cos(2 pi phase)
                const float a = autoW_.next();
                p = pos_.next() * (1.f - a) + pl * a;
                const float fc = fastExp2(lo + p * span);
                svf_.setG(fastG(fc, fs_), k);
                float l, h;
                xo_.split(in, l, h);
                svf_.tick(h);
                const float w = softClip((svf_.bp * k * peak + 0.25f * svf_.lp) * kMakeup);
                const float wet = l + w;
                x[i] = in + mix_.next() * (wet - in);
            }
        }
        svf_.flushState();
        xo_.flushState();
        flushBlock(x, n);
        activity_ = clampf(p, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    static constexpr float kPeak = 3.0f;
    static constexpr float kMakeup = 1.0f;
    static float softClip(float v) {
        const float a = std::fabs(v);
        if (a < 0.6f) return v;
        const float s = v > 0.f ? 1.f : -1.f;
        return s * (0.6f + 0.5f * fastTanh((a - 0.6f) * 2.f));
    }
    float fs_ = 48000.f;
    Smoother pos_, autoW_, rate_, q_, lo_, hi_, mix_;
    Svf svf_;
    Crossover xo_;
    float phase_ = 0.f;
    float activity_ = 0.5f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"wah", "Wah", "filter", "Bass wah: sweep Position by hand, or set Auto.",
                    {bb::dsp::pLin("pos", "Position", 0.f, 1.f, 0.5f),
                     bb::dsp::pLin("auto", "Auto", 0.f, 8.f, 0.f, "Hz"),
                     bb::dsp::pLog("q", "Q", 1.f, 12.f, 5.f),
                     bb::dsp::pEnum("range", "Range", {"Low", "Mid", "High"}, 1),
                     bb::dsp::pPct("mix", "Mix", 100.f)},
                    false, [] { return std::make_unique<Wah>(); }});
