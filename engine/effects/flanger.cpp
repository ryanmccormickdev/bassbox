// Flanger: short modulated delay (Manual = centre, Depth sweeps +-1.5 octaves of delay with a
// triangle LFO) with soft-saturated, slightly damped feedback (+-95%). Only the band above
// Low Cut is flanged; wet is scaled by sqrt(1 - fb^2) so heavy feedback doesn't jump in level.
#include "dsp_mod.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr float kOct = 1.5f;  // +- sweep in octaves of delay at Depth 100%

class Flanger final : public Effect {
public:
    enum { kRate, kDepth, kFdbk, kManual, kMix, kLowCut };
    void prepare(float fs, int) override {
        fs_ = fs;
        dl_.init(int(0.040f * fs));
        split_.prepare(fs);
        rate_.setTime(50.f, fs);
        depth_.setTime(40.f, fs);
        fb_.setTime(30.f, fs);
        man_.setTime(60.f, fs);
        mix_.setTime(20.f, fs);
        damp_.set(fs, 9000.f);
        primed_ = false;
        reset();
    }
    void reset() override {
        dl_.clear();
        split_.reset();
        damp_.reset();
        tap_ = 0.f;
        rate_.snap(); depth_.snap(); fb_.snap(); man_.snap(); mix_.snap();
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kRate: rate_.set(v, primed_); break;
            case kDepth: depth_.set(0.01f * v * kOct, primed_); break;
            case kFdbk: fb_.set(0.01f * v, primed_); break;
            case kManual: man_.set(std::log(v * 0.001f * fs_), primed_); break;  // log(samples)
            case kMix: mix_.set(0.01f * v, primed_); break;
            case kLowCut: split_.setFreq(v, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        const float invFs = 1.f / fs_;
        const float ln2 = 0.69314718f;
        const float maxD = float(dl_.size() - 8);
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            split_.tick();
            for (int i = i0; i < i0 + m; ++i) {
                float lo, hi;
                split_.split(x[i], lo, hi);
                lfo_.inc = rate_.next() * invFs;
                const float tri = triFromPhase(lfo_.tick());
                const float d = clampf(std::exp(man_.next() + depth_.next() * tri * ln2), 2.f, maxD);
                const float fb = fb_.next();
                // feedback path: damped, soft-saturated
                const float fbSig = fastTanh(damp_.lp(tap_));
                dl_.push(hi + fb * fbSig);
                tap_ = dl_.readHermite(d);
                const float wet = tap_ * std::sqrt(std::max(0.f, 1.f - fb * fb));
                float gd, gw;
                mixGains(mix_.next(), gd, gw);
                x[i] = lo + gd * hi + gw * wet;
            }
        }
        split_.flushState();
        damp_.flushState();
        tap_ = flush(tap_);
        flushBlock(x, n);
        activity_ = 0.5f + 0.5f * triFromPhase(lfo_.phase);
    }
    float activity() const override { return activity_; }

private:
    float fs_ = 48000.f;
    DelayLine dl_;
    LowSplit split_;
    Lfo lfo_;
    Smoother rate_, depth_, fb_, man_, mix_;
    OnePole damp_;
    float tap_ = 0.f;
    float activity_ = 0.5f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"flanger", "Flanger", "modulation", "Jet-plane flanger with feedback; lows stay dry.",
                    {bb::dsp::pLog("rate", "Rate", 0.05f, 5.f, 0.3f, "Hz"),
                     bb::dsp::pPct("depth", "Depth", 60.f),
                     bb::dsp::pLin("fdbk", "Fdbk", -95.f, 95.f, 50.f, "%"),
                     bb::dsp::pLog("manual", "Manual", 0.5f, 10.f, 2.f, "ms"),
                     bb::dsp::pPct("mix", "Mix", 50.f),
                     bb::dsp::pLog("lowcut", "Low Cut", 40.f, 400.f, 120.f, "Hz")},
                    false, [] { return std::make_unique<Flanger>(); }});
