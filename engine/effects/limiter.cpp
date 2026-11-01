// Limiter: brickwall peak limiter with 1 ms lookahead (48 samples of latency).
// Gain = moving average (L) of a release-smoothed sliding minimum (L+1) of the required gain,
// which provably never lets a sample exceed the ceiling, without clipping distortion.
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr int kLook = 48;          // samples of lookahead (1 ms at 48 kHz)
constexpr int kRing = 64;          // power of two >= kLook + 1

class Limiter final : public Effect {
public:
    enum { kInput, kCeiling, kRelease };
    void prepare(float fs, int) override {
        fs_ = fs;
        in_.setTime(20.f, fs);
        ceil_.setTime(20.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        for (int i = 0; i < kRing; ++i) { dly_[i] = 0.f; req_[i] = 1.f; rel_[i] = 1.f; }
        w_ = 0;
        relState_ = 1.f;
        avgSum_ = float(kLook);
        in_.snap();
        ceil_.snap();
        gr_ = 0.f;
        activity_ = 0.f;
    }
    void setParam(int i, float v) override {
        if (i == kInput) in_.set(dbToLin(v), primed_);
        else if (i == kCeiling) ceil_.set(dbToLin(v), primed_);
        else if (i == kRelease) relCoef_ = onePoleCoef(v, fs_);
    }
    void process(float* x, int n) override {
        primed_ = true;
        float minG = 1.f;
        for (int i = 0; i < n; ++i) {
            const float c = ceil_.next();
            const float s = x[i] * in_.next();
            const float a = std::fabs(s);
            const float req = a > c ? c / a : 1.f;
            const int w = w_;
            dly_[w] = s;
            req_[w] = req;
            // sliding min over the last kLook+1 required gains
            float h = 1.f;
            for (int k = 0; k <= kLook; ++k) h = std::min(h, req_[(w - k) & (kRing - 1)]);
            // instant attack, exponential release
            relState_ = h < relState_ ? h : relState_ + relCoef_ * (h - relState_);
            // moving average over kLook samples (running sum, renormalised each block)
            avgSum_ += relState_ - rel_[(w - kLook) & (kRing - 1)];
            rel_[w] = relState_;
            const float g = avgSum_ * (1.f / float(kLook));
            const float y = dly_[(w - kLook) & (kRing - 1)] * g;
            x[i] = clampf(y, -c, c);  // never engages in practice; guards float rounding
            minG = std::min(minG, g);
            w_ = (w + 1) & (kRing - 1);
        }
        // recompute the running sum exactly once per block to stop float drift
        float sum = 0.f;
        for (int k = 0; k < kLook; ++k) sum += rel_[(w_ - 1 - k) & (kRing - 1)];
        avgSum_ = sum;
        for (int k = 0; k < kRing; ++k) dly_[k] = flush(dly_[k]);
        gr_ = std::max(-linToDb(minG), gr_ * 0.85f);
        flushBlock(x, n);
        activity_ = clampf(gr_ * (1.f / 12.f), 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    float fs_ = 48000.f;
    Smoother in_, ceil_;
    float relCoef_ = 0.001f;
    float dly_[kRing] = {}, req_[kRing] = {}, rel_[kRing] = {};
    int w_ = 0;
    float relState_ = 1.f, avgSum_ = float(kLook);
    float gr_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"limiter", "Limiter", "dynamics", "Transparent brickwall limiter that catches every peak.",
                    {bb::dsp::pDb("input", "Input", 0.f, 24.f, 0.f),
                     bb::dsp::pDb("ceiling", "Ceiling", -24.f, 0.f, -1.f),
                     bb::dsp::pLog("release", "Release", 10.f, 1000.f, 120.f, "ms")},
                    false, [] { return std::make_unique<Limiter>(); }});
