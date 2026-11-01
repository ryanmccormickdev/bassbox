// Filter: low cut (high-pass) + high cut (low-pass), Butterworth, 12 or 24 dB/oct.
// Cutoffs glide in log space; slope changes crossfade between the two filter orders.
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Cut final : public Effect {
public:
    enum { kLow, kHigh, kSlope };
    void prepare(float fs, int) override {
        fs_ = fs;
        lo_.setTime(30.f, fs / kSub);
        hi_.setTime(30.f, fs / kSub);
        xf_.setTime(15.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        for (auto& b : hp_) b.reset();
        for (auto& b : lp_) b.reset();
        lo_.snap();
        hi_.snap();
        xf_.snap();
        update();
    }
    void setParam(int i, float v) override {
        if (i == kLow) lo_.set(std::log(v), primed_);
        else if (i == kHigh) hi_.set(std::log(v), primed_);
        else if (i == kSlope) xf_.set(v >= 0.5f ? 1.f : 0.f, primed_);
        if (!primed_) update();
    }
    void process(float* x, int n) override {
        primed_ = true;
        float env = env_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!lo_.settled() || !hi_.settled()) { lo_.next(); hi_.next(); update(); }
            for (int i = i0; i < i0 + m; ++i) {
                // 12 dB: one HP + one LP biquad (Q .707). 24 dB: Butterworth 4th order (Q .541/1.307)
                const float in = x[i];
                const float y12 = lp2_.process(hp2_.process(in));
                const float y24 = lp_[1].process(lp_[0].process(hp_[1].process(hp_[0].process(in))));
                const float t = xf_.next();
                const float y = y12 + t * (y24 - y12);
                x[i] = y;
                env += 0.002f * (std::fabs(y) - env);
            }
        }
        for (auto& b : hp_) b.flushState();
        for (auto& b : lp_) b.flushState();
        hp2_.flushState();
        lp2_.flushState();
        env_ = flush(env);
        flushBlock(x, n);
        activity_ = clampf(env_ * 4.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    void update() {
        const double fl = std::exp(lo_.cur), fh = std::exp(hi_.cur);
        hp2_.c = rbj::highpass(fs_, fl, 0.70710678);
        lp2_.c = rbj::lowpass(fs_, fh, 0.70710678);
        hp_[0].c = rbj::highpass(fs_, fl, 0.5411961);
        hp_[1].c = rbj::highpass(fs_, fl, 1.3065630);
        lp_[0].c = rbj::lowpass(fs_, fh, 0.5411961);
        lp_[1].c = rbj::lowpass(fs_, fh, 1.3065630);
    }
    float fs_ = 48000.f;
    Smoother lo_, hi_, xf_;
    Biquad hp_[2], lp_[2], hp2_, lp2_;
    float env_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"cut", "Filter", "eq", "Low cut and high cut to trim mud, rumble and fizz.",
                    {bb::dsp::pLog("lowcut", "Low Cut", 10.f, 500.f, 20.f, "Hz"),
                     bb::dsp::pLog("highcut", "High Cut", 800.f, 20000.f, 20000.f, "Hz"),
                     bb::dsp::pEnum("slope", "Slope", {"12 dB", "24 dB"}, 0)},
                    false, [] { return std::make_unique<Cut>(); }});
