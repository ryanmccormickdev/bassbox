// Phaser: cascade of 8 first-order TPT allpasses swept exponentially around 750 Hz (+-2 octaves
// at Depth 100%, i.e. ~190 Hz - 3 kHz), with soft-saturated feedback. Stage count (4/6/8)
// crossfades between taps so switching never clicks. Lows below Low Cut stay dry.
#include "dsp_mod.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr int kStages = 8;
constexpr float kCenter = 750.f;
constexpr float kOct = 2.f;

class Phaser final : public Effect {
public:
    enum { kRate, kDepth, kFdbk, kStagesP, kMix, kLowCut };
    void prepare(float fs, int) override {
        fs_ = fs;
        split_.prepare(fs);
        rate_.setTime(50.f, fs);
        depth_.setTime(40.f, fs);
        fb_.setTime(30.f, fs);
        mix_.setTime(20.f, fs);
        for (auto& w : w_) w.setTime(25.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        for (auto& a : ap_) a.reset();
        split_.reset();
        last_ = 0.f;
        rate_.snap(); depth_.snap(); fb_.snap(); mix_.snap();
        for (auto& w : w_) w.snap();
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kRate: rate_.set(v, primed_); break;
            case kDepth: depth_.set(0.01f * v * kOct, primed_); break;
            case kFdbk: fb_.set(0.01f * v, primed_); break;
            case kStagesP: {
                const int s = int(clampf(std::round(v), 0.f, 2.f));
                for (int k = 0; k < 3; ++k) w_[k].set(k == s ? 1.f : 0.f, primed_);
                break;
            }
            case kMix: mix_.set(0.01f * v, primed_); break;
            case kLowCut: split_.setFreq(v, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        const float invFs = 1.f / fs_;
        const float ln2 = 0.69314718f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            split_.tick();
            for (int i = i0; i < i0 + m; ++i) {
                float lo, hi;
                split_.split(x[i], lo, hi);
                lfo_.inc = rate_.next() * invFs;
                const float s = fastSin2Pi(lfo_.tick());
                const float f = kCenter * std::exp(depth_.next() * s * ln2);
                const float G = onePoleG(f, invFs);
                const float fb = fb_.next();
                float v = hi + fb * fastTanh(last_);
                float o4 = 0.f, o6 = 0.f;
                for (int k = 0; k < kStages; ++k) {
                    v = ap_[k].process(v, G);
                    if (k == 3) o4 = v;
                    else if (k == 5) o6 = v;
                }
                const float wet = w_[0].next() * o4 + w_[1].next() * o6 + w_[2].next() * v;
                last_ = wet;
                const float wetN = wet * std::sqrt(std::max(0.f, 1.f - fb * fb));
                float gd, gw;
                mixGains(mix_.next(), gd, gw);
                x[i] = lo + gd * hi + gw * wetN;
            }
        }
        for (auto& a : ap_) a.flushState();
        split_.flushState();
        last_ = flush(last_);
        flushBlock(x, n);
        activity_ = 0.5f + 0.5f * fastSin2Pi(lfo_.phase);
    }
    float activity() const override { return activity_; }

private:
    float fs_ = 48000.f;
    LowSplit split_;
    Allpass1 ap_[kStages];
    Lfo lfo_;
    Smoother rate_, depth_, fb_, mix_, w_[3];
    float last_ = 0.f;
    float activity_ = 0.5f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"phaser", "Phaser", "modulation", "Swirling all-pass phaser, 4 to 8 stages; lows stay dry.",
                    {bb::dsp::pLog("rate", "Rate", 0.05f, 8.f, 0.5f, "Hz"),
                     bb::dsp::pPct("depth", "Depth", 70.f),
                     bb::dsp::pLin("fdbk", "Fdbk", 0.f, 95.f, 40.f, "%"),
                     bb::dsp::pEnum("stages", "Stages", {"4", "6", "8"}, 0),
                     bb::dsp::pPct("mix", "Mix", 50.f),
                     bb::dsp::pLog("lowcut", "Low Cut", 40.f, 400.f, 100.f, "Hz")},
                    false, [] { return std::make_unique<Phaser>(); }});
