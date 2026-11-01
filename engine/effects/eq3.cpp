// 3-Band EQ: bass shelf (90 Hz), sweepable peaking mid (200 Hz - 3 kHz), treble shelf (4 kHz).
// All gains/frequencies are smoothed and coefficients are recomputed per 16-sample sub-block.
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Eq3 final : public Effect {
public:
    enum { kBass, kMid, kMidFreq, kTreble, kLevel };
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
        update();
    }
    void setParam(int i, float v) override {
        if (i == kLevel) { level_.set(dbToLin(v), primed_); return; }
        // the mid frequency glides in log space so sweeps sound even
        sm_[i].set(i == kMidFreq ? std::log(v) : v, primed_);
        if (!primed_) update();
    }
    void process(float* x, int n) override {
        primed_ = true;
        float env = env_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            bool dirty = false;
            for (int k = 0; k < 4; ++k)
                if (!sm_[k].settled()) { sm_[k].next(); dirty = true; }
            if (dirty) update();
            for (int i = i0; i < i0 + m; ++i) {
                float y = bq_[0].process(x[i]);
                y = bq_[1].process(y);
                y = bq_[2].process(y);
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
    void update() {
        bq_[0].c = rbj::lowShelf(fs_, 90.0, sm_[kBass].cur, 0.6);
        bq_[1].c = rbj::peaking(fs_, std::exp(sm_[kMidFreq].cur), 0.8, sm_[kMid].cur);
        bq_[2].c = rbj::highShelf(fs_, 4000.0, sm_[kTreble].cur, 0.6);
    }
    float fs_ = 48000.f;
    Smoother sm_[4];
    Smoother level_;
    Biquad bq_[3];
    float env_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"eq3", "3-Band EQ", "eq", "Bass, sweepable mid and treble tone shaping.",
                    {bb::dsp::pDb("bass", "Bass", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("mid", "Mid", -15.f, 15.f, 0.f),
                     bb::dsp::pLog("midfreq", "Mid Freq", 200.f, 3000.f, 800.f, "Hz"),
                     bb::dsp::pDb("treble", "Treble", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("level", "Level", -12.f, 12.f, 0.f)},
                    false, [] { return std::make_unique<Eq3>(); }});
