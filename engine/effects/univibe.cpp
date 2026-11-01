// Uni-Vibe: four first-order allpass stages with the original's widely staggered capacitor ratios
// (15n / 220n / 470p / 4.7n), all driven by one "photocell". The lamp follows a squared sine and
// the cell reacts fast to light and slowly to dark, giving the lopsided throb. Chorus mode mixes
// with dry (phase-cancellation throb), Vibrato mode is wet only. Lows below Low Cut stay dry.
#include "dsp_mod.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr int kStages = 4;
// stage pole frequency = base * mult; base sweeps 60 Hz (dark cell) .. 3.5 kHz (bright)
constexpr float kMult[kStages] = {1.f, 0.0682f, 31.9f, 3.19f};
constexpr float kDark = 60.f, kBright = 3500.f;

class Univibe final : public Effect {
public:
    enum { kRate, kDepth, kMode, kLowCut };
    void prepare(float fs, int) override {
        fs_ = fs;
        split_.prepare(fs);
        rate_.setTime(60.f, fs);
        depth_.setTime(40.f, fs);
        dry_.setTime(30.f, fs);
        atk_ = onePoleCoef(4.f, fs);
        rel_ = onePoleCoef(35.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        for (auto& a : ap_) a.reset();
        split_.reset();
        cell_ = 0.3f;
        rate_.snap(); depth_.snap(); dry_.snap();
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kRate: rate_.set(v, primed_); break;
            case kDepth: depth_.set(0.01f * v, primed_); break;
            case kMode: dry_.set(v >= 0.5f ? 0.f : 1.f, primed_); break;  // 1 = chorus
            case kLowCut: split_.setFreq(v, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        const float invFs = 1.f / fs_;
        const float lnSpan = std::log(kBright / kDark);
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            split_.tick();
            for (int i = i0; i < i0 + m; ++i) {
                float lo, hi;
                split_.split(x[i], lo, hi);
                lfo_.inc = rate_.next() * invFs;
                const float s = 0.5f + 0.5f * fastSin2Pi(lfo_.tick());
                const float lamp = s * s;
                cell_ += (lamp > cell_ ? atk_ : rel_) * (lamp - cell_);
                const float dep = depth_.next();
                const float pos = 0.45f + dep * (cell_ - 0.45f);
                const float base = kDark * std::exp(lnSpan * pos);
                float v = hi;
                for (int k = 0; k < kStages; ++k) v = ap_[k].process(v, onePoleG(base * kMult[k], invFs));
                // chorus: equal dry + wet (classic vibe mix), vibrato: wet only
                const float d = dry_.next();
                const float gw = 1.f - 0.29f * d;   // 0.71 in chorus mode
                x[i] = lo + 0.71f * d * hi + gw * v;
            }
        }
        for (auto& a : ap_) a.flushState();
        split_.flushState();
        cell_ = flush(cell_);
        flushBlock(x, n);
        activity_ = clampf(cell_, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    float fs_ = 48000.f;
    LowSplit split_;
    Allpass1 ap_[kStages];
    Lfo lfo_;
    Smoother rate_, depth_, dry_;
    float atk_ = 0.01f, rel_ = 0.001f, cell_ = 0.3f;
    float activity_ = 0.3f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"univibe", "Uni-Vibe", "modulation", "Throbbing photocell phase-shifter, chorus or vibrato.",
                    {bb::dsp::pLog("rate", "Rate", 0.2f, 10.f, 2.f, "Hz"),
                     bb::dsp::pPct("depth", "Depth", 70.f),
                     bb::dsp::pEnum("mode", "Mode", {"Chorus", "Vibrato"}, 0),
                     bb::dsp::pLog("lowcut", "Low Cut", 40.f, 400.f, 100.f, "Hz")},
                    false, [] { return std::make_unique<Univibe>(); }});
