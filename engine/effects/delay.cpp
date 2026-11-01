// Delay: clean digital delay, up to 1.5 s.
//  * Tone (12 dB/oct low-pass) and Low Cut (12 dB/oct high-pass) sit inside the feedback loop,
//    so every repeat is darker and thinner than the last and never muds up the bass.
//  * Mod: subtle chorus-like drift of the read head (two slow incommensurate LFOs, <= 3 ms).
//  * Time changes never click: small moves (< 1 ms) glide; bigger moves crossfade (60 ms)
//    from the current read tap to a second tap at the new time. Further moves during a fade
//    are picked up when it finishes.
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr float kMaxMs = 1500.f;
constexpr float kModMaxMs = 3.f;
constexpr float kXfadeMs = 60.f;
constexpr float kGlideThreshMs = 1.f;

class Delay final : public Effect {
public:
    enum { kTime, kFdbk, kMix, kTone, kLowCut, kMod };

    void prepare(float fs, int) override {
        fs_ = fs;
        line_.init(int(fs * 0.001f * (kMaxMs + kModMaxMs)) + 16);
        fb_.setTime(30.f, fs);
        mix_.setTime(30.f, fs);
        mod_.setTime(80.f, fs);
        tone_.setTime(40.f, fs / kSub);
        low_.setTime(40.f, fs / kSub);
        xfInc_ = 1.f / (0.001f * kXfadeMs * fs);
        glide_ = onePoleCoef(60.f, fs);
        thresh_ = 0.001f * kGlideThreshMs * fs;
        envRel_ = std::exp(-1.f / (0.25f * fs));
        lfo1_.setRate(0.53f, fs);
        lfo2_.setRate(0.217f, fs);
        primed_ = false;
        reset();
    }

    void reset() override {
        line_.clear();
        lp_.reset();
        hp_.reset();
        cur_[0] = cur_[1] = target_;
        active_ = 0;
        fading_ = false;
        xf_ = 0.f;
        fb_.snap();
        mix_.snap();
        mod_.snap();
        tone_.snap();
        low_.snap();
        env_ = 0.f;
        activity_ = 0.f;
        update();
    }

    void setParam(int i, float v) override {
        switch (i) {
            case kTime:
                target_ = clampf(v, 20.f, kMaxMs) * 0.001f * fs_;
                if (!primed_) cur_[0] = cur_[1] = target_;
                break;
            case kFdbk: fb_.set(clampf(v, 0.f, 95.f) * 0.01f, primed_); break;
            case kMix: mix_.set(clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kTone: tone_.set(std::log(clampf(v, 200.f, 20000.f)), primed_); break;
            case kLowCut: low_.set(std::log(clampf(v, 10.f, 2000.f)), primed_); break;
            case kMod: mod_.set(clampf(v, 0.f, 100.f) * 0.01f * kModMaxMs * 0.001f * fs_, primed_); break;
            default: break;
        }
        if (!primed_) update();
    }

    void process(float* x, int n) override {
        primed_ = true;
        float env = env_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!tone_.settled() || !low_.settled()) {
                tone_.next();
                low_.next();
                update();
            }
            for (int i = i0; i < i0 + m; ++i) {
                const float in = x[i];
                // read-tap management: glide small moves, crossfade big ones
                if (!fading_) {
                    const float d = target_ - cur_[active_];
                    if (std::fabs(d) > thresh_) {
                        cur_[1 - active_] = target_;
                        fading_ = true;
                        xf_ = 0.f;
                    } else {
                        cur_[active_] += glide_ * d;
                    }
                }
                // modulation offset in [0, depth]
                const float depth = mod_.next();
                const float mo = depth * 0.5f *
                                 (1.f + 0.7f * fastSin2Pi(lfo1_.tick()) + 0.3f * fastSin2Pi(lfo2_.tick()));
                float y = line_.readHermite(cur_[active_] + mo);
                if (fading_) {
                    const float y2 = line_.readHermite(cur_[1 - active_] + mo);
                    xf_ += xfInc_;
                    const float t = std::min(xf_, 1.f);
                    const float gx = t * t * (3.f - 2.f * t);
                    y += gx * (y2 - y);
                    if (xf_ >= 1.f) {
                        active_ = 1 - active_;
                        fading_ = false;
                    }
                }
                // feedback path: filters apply to the input too, so the first repeat is
                // already shaped and each further repeat goes through them once more
                float w = hp_.process(lp_.process(in + fb_.next() * y));
                w = 1.5f * fastTanh(w * (1.f / 1.5f));  // transparent at normal levels; bounds the loop
                line_.push(flush(w));
                x[i] = in + mix_.next() * y;
                const float a = std::fabs(y);
                env = a > env ? a : env * envRel_;
            }
        }
        lp_.flushState();
        hp_.flushState();
        env_ = flush(env);
        flushBlock(x, n);
        activity_ = clampf(env_ * 2.f, 0.f, 1.f);
    }

    float activity() const override { return activity_; }

private:
    void update() {
        lp_.c = rbj::lowpass(fs_, std::exp(tone_.cur), 0.70710678);
        hp_.c = rbj::highpass(fs_, std::exp(low_.cur), 0.70710678);
    }

    float fs_ = 48000.f;
    DelayLine line_;
    Biquad lp_, hp_;
    Smoother fb_, mix_, mod_, tone_, low_;
    Lfo lfo1_, lfo2_;
    float target_ = 380.f * 48.f;
    float cur_[2] = {380.f * 48.f, 380.f * 48.f};
    int active_ = 0;
    bool fading_ = false;
    float xf_ = 0.f, xfInc_ = 0.f, glide_ = 0.f, thresh_ = 48.f;
    float env_ = 0.f, envRel_ = 0.9999f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"delay", "Delay", "time", "Digital delay with dark, thin repeats and subtle mod.",
                    {bb::dsp::pLog("time", "Time", 20.f, 1500.f, 380.f, "ms"),
                     bb::dsp::pLin("fdbk", "Fdbk", 0.f, 95.f, 35.f, "%"),
                     bb::dsp::pPct("mix", "Mix", 30.f),
                     bb::dsp::pLog("tone", "Tone", 500.f, 12000.f, 4000.f, "Hz"),
                     bb::dsp::pLog("lowcut", "Low Cut", 20.f, 500.f, 80.f, "Hz"),
                     bb::dsp::pPct("mod", "Mod", 15.f)},
                    true, [] { return std::make_unique<Delay>(); }});
