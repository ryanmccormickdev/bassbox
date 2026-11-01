// Tremolo: full-band amplitude modulation (sine / square / triangle). The gain passes through two
// cascaded ~2.5 ms one-poles so the square never clicks, and the waveform's RMS loss is made up
// (capped at +4 dB) so the effect stays level-matched at any depth.
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Tremolo final : public Effect {
public:
    enum { kRate, kDepth, kShape };
    void prepare(float fs, int) override {
        fs_ = fs;
        rate_.setTime(50.f, fs);
        depth_.setTime(30.f, fs);
        comp_.setTime(60.f, fs);
        slew_ = onePoleCoef(2.5f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        rate_.snap(); depth_.snap(); comp_.snap();
        g1_ = g2_ = 1.f;
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kRate: rate_.set(v, primed_); break;
            case kDepth: depth_.set(0.01f * v, primed_); dTarget_ = 0.01f * v; break;
            case kShape: shape_ = int(clampf(std::round(v), 0.f, 2.f)); break;
            default: break;
        }
        // RMS of g = 1 - d*(0.5 - 0.5*w): (1-d/2)^2 + (d/2)^2 * E[w^2]
        const float d = dTarget_;
        const float ew2 = shape_ == 0 ? 0.5f : (shape_ == 1 ? 1.f : 1.f / 3.f);
        const float ms = (1.f - 0.5f * d) * (1.f - 0.5f * d) + 0.25f * d * d * ew2;
        comp_.set(std::min(1.f / std::sqrt(ms), 1.585f), primed_);
    }
    void process(float* x, int n) override {
        primed_ = true;
        const float invFs = 1.f / fs_;
        for (int i = 0; i < n; ++i) {
            lfo_.inc = rate_.next() * invFs;
            const float p = lfo_.tick();
            float w;
            if (shape_ == 0) w = fastSin2Pi(p);
            else if (shape_ == 1) w = p < 0.5f ? 1.f : -1.f;
            else w = -triFromPhase(wrap01(p + 0.75f));  // tri in phase with the sine
            const float g = (1.f - depth_.next() * (0.5f - 0.5f * w)) * comp_.next();
            g1_ = flush(g1_ + slew_ * (g - g1_));  // gain can sit at 0 (depth 100%): no denormals
            g2_ = flush(g2_ + slew_ * (g1_ - g2_));
            x[i] *= g2_;
            w_ = w;
        }
        flushBlock(x, n);
        activity_ = 0.5f + 0.5f * w_;
    }
    float activity() const override { return activity_; }

private:
    float fs_ = 48000.f;
    Lfo lfo_;
    Smoother rate_, depth_, comp_;
    float dTarget_ = 0.5f;
    int shape_ = 0;
    float slew_ = 0.01f, g1_ = 1.f, g2_ = 1.f, w_ = 0.f;
    float activity_ = 0.5f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"tremolo", "Tremolo", "modulation", "Amp-style volume pulsing: sine, square or triangle.",
                    {bb::dsp::pLog("rate", "Rate", 0.5f, 15.f, 5.f, "Hz"),
                     bb::dsp::pPct("depth", "Depth", 50.f),
                     bb::dsp::pEnum("shape", "Shape", {"Sine", "Square", "Tri"}, 0)},
                    false, [] { return std::make_unique<Tremolo>(); }});
