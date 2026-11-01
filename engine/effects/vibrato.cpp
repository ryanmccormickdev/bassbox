// Vibrato: pitch wobble on the band above Low Cut only, so the fundamental of low notes stays put.
// Depth is the peak pitch deviation (100% = +-1 semitone), independent of Rate; the sweep
// excursion A = dev / (2 pi rate) is capped at 8 ms. The low band runs through a matching
// (unmodulated) delay of the same centre time so both bands stay phase-aligned.
#include "dsp_mod.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr float kMaxExcMs = 8.f;
constexpr float kMarginMs = 0.6f;

class Vibrato final : public Effect {
public:
    enum { kRate, kDepth, kLowCut };
    void prepare(float fs, int) override {
        fs_ = fs;
        hiDl_.init(int(0.025f * fs));
        loDl_.init(int(0.025f * fs));
        split_.prepare(fs);
        rate_.setTime(60.f, fs);
        exc_.setTime(120.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        hiDl_.clear();
        loDl_.clear();
        split_.reset();
        rate_.snap();
        exc_.snap();
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kRate: rateT_ = v; rate_.set(v, primed_); break;
            case kDepth: depthT_ = 0.01f * v; break;
            case kLowCut: split_.setFreq(v, primed_); return;
            default: return;
        }
        const float dev = std::pow(2.f, depthT_ / 12.f) - 1.f;  // ratio deviation at Depth
        const float a = std::min(dev / (kTwoPi * std::max(rateT_, 0.05f)), kMaxExcMs * 0.001f);
        exc_.set(a * fs_, primed_);  // excursion in samples
    }
    void process(float* x, int n) override {
        primed_ = true;
        const float invFs = 1.f / fs_;
        const float margin = kMarginMs * 0.001f * fs_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            split_.tick();
            for (int i = i0; i < i0 + m; ++i) {
                float lo, hi;
                split_.split(x[i], lo, hi);
                hiDl_.push(hi);
                loDl_.push(lo);
                lfo_.inc = rate_.next() * invFs;
                const float s = fastSin2Pi(lfo_.tick());
                const float a = exc_.next();
                const float centre = a + margin;
                x[i] = loDl_.readHermite(centre) + hiDl_.readHermite(centre + a * s);
            }
        }
        split_.flushState();
        flushBlock(x, n);
        activity_ = 0.5f + 0.5f * fastSin2Pi(lfo_.phase);
    }
    float activity() const override { return activity_; }

private:
    float fs_ = 48000.f;
    DelayLine hiDl_, loDl_;
    LowSplit split_;
    Lfo lfo_;
    Smoother rate_, exc_;
    float rateT_ = 5.f, depthT_ = 0.4f;
    float activity_ = 0.5f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"vibrato", "Vibrato", "modulation", "Pitch wobble above the low cut; the fundamental stays put.",
                    {bb::dsp::pLog("rate", "Rate", 0.1f, 10.f, 5.f, "Hz"),
                     bb::dsp::pPct("depth", "Depth", 40.f),
                     bb::dsp::pLog("lowcut", "Low Cut", 40.f, 400.f, 120.f, "Hz")},
                    false, [] { return std::make_unique<Vibrato>(); }});
