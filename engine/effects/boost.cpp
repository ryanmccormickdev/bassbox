// Boost: clean gain with a gentle tilt EQ (pivot ~650 Hz). Zero latency, no saturation.
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Boost final : public Effect {
public:
    void prepare(float fs, int) override {
        fs_ = fs;
        gain_.setTime(20.f, fs);
        tilt_.setTime(30.f, fs / kSub);
        primed_ = false;
        reset();
    }
    void reset() override {
        eq_.reset();
        gain_.snap();
        tilt_.snap();
        update();
    }
    void setParam(int i, float v) override {
        if (i == 0) gain_.set(dbToLin(v), primed_);
        else if (i == 1) tilt_.set(v, primed_);
        if (!primed_) update();
    }
    void process(float* x, int n) override {
        primed_ = true;
        float pk = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!tilt_.settled()) { tilt_.next(); update(); }
            for (int i = i0; i < i0 + m; ++i) {
                const float y = eq_.process(x[i]) * comp_ * gain_.next();
                x[i] = y;
                pk = std::max(pk, std::fabs(y));
            }
        }
        eq_.flushState();
        level_ = std::max(pk, level_ * 0.9f);
        flushBlock(x, n);
        activity_ = clampf(level_, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    void update() {
        const float t = tilt_.cur;
        eq_.c = rbj::highShelf(fs_, 650.0, t, 0.5);  // q 0.5: broad, tilt-like slope
        comp_ = dbToLin(-0.5f * t);
    }
    float fs_ = 48000.f;
    Smoother gain_, tilt_;
    Biquad eq_;
    float comp_ = 1.f, level_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"boost", "Boost", "utility", "Clean volume boost or cut with a tilt tone control.",
                    {bb::dsp::pDb("gain", "Gain", -12.f, 20.f, 0.f),
                     bb::dsp::pDb("tone", "Tone", -6.f, 6.f, 0.f)},
                    false, [] { return std::make_unique<Boost>(); }});
