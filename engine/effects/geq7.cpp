// Graphic EQ (bass GE-7 layout): 50, 120, 400, 500, 800, 4.5k, 10k Hz, +-15 dB, plus Level.
// Constant-Q peaking bands; the outermost bands are shelves like the hardware's end bands.
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr int kBands = 7;
constexpr float kFreq[kBands] = {50.f, 120.f, 400.f, 500.f, 800.f, 4500.f, 10000.f};
constexpr float kQ[kBands] = {0.9f, 1.1f, 1.6f, 1.6f, 1.4f, 1.0f, 0.8f};

class Geq7 final : public Effect {
public:
    void prepare(float fs, int) override {
        fs_ = fs;
        for (auto& s : sm_) s.setTime(25.f, fs / kSub);
        level_.setTime(20.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        for (auto& b : bq_) b.reset();
        for (auto& s : sm_) s.snap();
        level_.snap();
        for (int k = 0; k < kBands; ++k) update(k);
    }
    void setParam(int i, float v) override {
        if (i == kBands) { level_.set(dbToLin(v), primed_); return; }
        if (i < 0 || i > kBands) return;
        sm_[i].set(v, primed_);
        if (!primed_) update(i);
    }
    void process(float* x, int n) override {
        primed_ = true;
        float env = env_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            for (int k = 0; k < kBands; ++k)
                if (!sm_[k].settled()) { sm_[k].next(); update(k); }
            for (int i = i0; i < i0 + m; ++i) {
                float y = x[i];
                for (int k = 0; k < kBands; ++k)
                    if (active_[k]) y = bq_[k].process(y);
                y *= level_.next();
                x[i] = y;
                env += 0.002f * (std::fabs(y) - env);
            }
        }
        for (auto& b : bq_) b.flushState();
        env_ = flush(env);
        flushBlock(x, n);
        activity_ = clampf(env_ * 4.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    void update(int k) {
        const float g = sm_[k].cur;
        if (k == 0) bq_[k].c = rbj::lowShelf(fs_, 70.0, g, 0.7);
        else if (k == kBands - 1) bq_[k].c = rbj::highShelf(fs_, 8000.0, g, 0.7);
        else bq_[k].c = rbj::peaking(fs_, kFreq[k], kQ[k], g);
        // skip exactly-flat bands (saves CPU), but keep state warm once a band is in use
        const bool on = std::fabs(g) > 1e-4f;
        if (on && !active_[k]) bq_[k].reset();
        active_[k] = on;
    }
    float fs_ = 48000.f;
    Smoother sm_[kBands];
    Smoother level_;
    Biquad bq_[kBands];
    bool active_[kBands] = {};
    float env_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"geq7", "Graphic EQ", "eq", "Seven-band bass graphic EQ, 50 Hz to 10 kHz.",
                    {bb::dsp::pDb("b50", "50", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("b120", "120", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("b400", "400", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("b500", "500", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("b800", "800", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("b4k5", "4.5k", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("b10k", "10k", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("level", "Level", -15.f, 15.f, 0.f)},
                    false, [] { return std::make_unique<Geq7>(); }});
