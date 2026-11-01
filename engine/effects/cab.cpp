// Cab Sim: bass cabinet voicings (1x15, 4x10, 8x10, 2x12) from tuned biquad cascades
// (dsp_cab.h), mic placement (on-axis bright .. edge dark), proximity Low shelf and Level.
// Type changes crossfade over 30 ms; every other control is smoothed.
#include "dsp_cab.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Cab final : public Effect {
public:
    enum { kType, kMic, kLow, kLevel };
    void prepare(float fs, int) override {
        cab_.prepare(fs);
        level_.setTime(20.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        cab_.reset();
        level_.snap();
        env_ = 0.f;
    }
    void setParam(int i, float v) override {
        cab_.primed = primed_;
        switch (i) {
            case kType: cab_.setType(int(v + 0.5f)); break;
            case kMic: cab_.setMic(v); break;
            case kLow: cab_.setLow(v); break;
            case kLevel: level_.set(dbToLin(v), primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        cab_.primed = true;
        float env = env_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            cab_.tick();
            for (int i = i0; i < i0 + m; ++i) {
                const float y = cab_.process(x[i]) * level_.next();
                x[i] = y;
                env += 0.002f * (std::fabs(y) - env);
            }
        }
        cab_.flushState();
        env_ = flush(env);
        flushBlock(x, n);
        activity_ = clampf(env_ * 4.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    CabSim cab_;
    Smoother level_;
    float env_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"cab", "Cab Sim", "amp", "Bass speaker cabinet voicing with mic placement.",
                    {bb::dsp::pEnum("cab", "Cab", {"1x15", "4x10", "8x10", "2x12"}, 1),
                     bb::dsp::pPct("mic", "Mic", 30.f),
                     bb::dsp::pDb("low", "Low", -12.f, 12.f, 0.f),
                     bb::dsp::pDb("level", "Level", -12.f, 12.f, 0.f)},
                    false, [] { return std::make_unique<Cab>(); }});
