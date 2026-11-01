// Shimmer: ambient FDN reverb with a pitch shifter in its feedback path, so every pass through
// the tank adds a layer an interval higher (or lower): the classic "angelic" octave-up wash.
//   input -> Low Cut -> 9 kHz LP -> 4 diffusers -> (+ shimmer return) -> 8-line FDN -> wet
//   shimmer return = shift( HP/LP( normalised FDN output ) ) * Shimmer, soft-limited
// Stability: the FDN output is normalised to unity steady-state power gain before the shifter
// and the return gain is <= 0.9, so the outer loop gain stays below 1 at every setting; tanh
// bounds it anyway. Tone sets the in-tank damping (and the shimmer path's top end).
#include "dsp_time.h"

namespace {
using namespace bb;
using namespace bb::dsp;
using namespace bb::dsp::tfx;

constexpr float kScale = 0.9f;
constexpr float kRet = 0.9f;  // shimmer return gain at Shimmer = 100%

class Shimmer final : public Effect {
public:
    enum { kMix, kDecay, kAmount, kShift, kTone, kLowCut };

    void prepare(float fs, int) override {
        fs_ = fs;
        fdn_.init(fs, kScale, 8.f * fs / 48000.f);
        static const int kAp[4] = {142, 107, 379, 277};
        for (int k = 0; k < 4; ++k) ap_[k].init(int(float(kAp[k]) * fs / 48000.f), 0.65f);
        shifter_.init(int(0.05f * fs));
        inLp_.c = rbj::lowpass(fs, 9000.0, 0.70710678);
        mix_.setTime(30.f, fs);
        amt_.setTime(60.f, fs);
        decay_.setTime(60.f, fs / kSub);
        tone_.setTime(40.f, fs / kSub);
        low_.setTime(40.f, fs / kSub);
        envRel_ = std::exp(-1.f / (0.3f * fs));
        primed_ = false;
        reset();
    }

    void reset() override {
        fdn_.clear();
        for (auto& a : ap_) a.clear();
        shifter_.clear();
        hp_.reset();
        inLp_.reset();
        shHp_.reset();
        shLp_.reset();
        mix_.snap();
        amt_.snap();
        decay_.snap();
        tone_.snap();
        low_.snap();
        ret_ = 0.f;
        env_ = 0.f;
        activity_ = 0.f;
        update();
    }

    void setParam(int i, float v) override {
        switch (i) {
            case kMix: mix_.set(clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kDecay: decay_.set(std::log(clampf(v, 0.2f, 30.f)), primed_); break;
            case kAmount: amt_.set(clampf(v, 0.f, 100.f) * 0.01f * kRet, primed_); break;
            case kShift: {
                static const float kSemis[4] = {12.f, 7.f, 19.f, -12.f};
                const int k = int(clampf(v, 0.f, 3.f) + 0.5f);
                shifter_.setRatio(std::pow(2.f, kSemis[k] / 12.f));
                break;
            }
            case kTone: tone_.set(clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kLowCut: low_.set(std::log(clampf(v, 10.f, 2000.f)), primed_); break;
            default: break;
        }
        if (!primed_) update();
    }

    void process(float* x, int n) override {
        primed_ = true;
        float env = env_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!decay_.settled() || !tone_.settled() || !low_.settled()) {
                decay_.next();
                tone_.next();
                low_.next();
                update();
            }
            for (int i = i0; i < i0 + m; ++i) {
                const float in = x[i];
                float s = inLp_.process(hp_.process(in));
                for (auto& a : ap_) s = a.tick(s);
                const float y = fdn_.tick(s + ret_, kScale);
                // shimmer return for the next sample (one sample of extra loop delay is harmless)
                const float sh = shLp_.process(shHp_.process(y * norm_));
                ret_ = 1.2f * fastTanh(shifter_.tick(sh) * amt_.next() * (1.f / 1.2f));
                const float wet = y * wetGain_;
                x[i] = in + mix_.next() * wet;
                const float a = std::fabs(wet);
                env = a > env ? a : env * envRel_;
            }
        }
        fdn_.flushState();
        hp_.flushState();
        inLp_.flushState();
        shHp_.flushState();
        shLp_.flushState();
        ret_ = flush(ret_);
        env_ = flush(env);
        flushBlock(x, n);
        activity_ = clampf(env_ * 2.5f, 0.f, 1.f);
    }

    float activity() const override { return activity_; }

private:
    void update() {
        fdn_.setDecay(std::exp(decay_.cur), kScale);
        // Tone 0..1 -> in-tank damping 2.5 kHz .. 14 kHz; shimmer path tops out <= 6 kHz so an
        // octave (or a 12th) up never aliases
        const float fc = 2500.f * std::pow(14000.f / 2500.f, tone_.cur);
        fdn_.setDamp(fc);
        const float lc = std::exp(low_.cur);
        hp_.c = rbj::highpass(fs_, lc, 0.70710678);
        shHp_.c = rbj::highpass(fs_, std::max(lc, 120.f), 0.70710678);
        shLp_.c = rbj::lowpass(fs_, std::min(fc, 6000.f), 0.70710678);
        const float g = fdn_.gMean;
        const float p = std::max(1e-4f, 1.f - g * g);
        norm_ = std::sqrt(p);                  // unity steady-state power gain into the shifter
        wetGain_ = kWet * std::pow(p, 0.25f);  // half-normalised, like the reverb
    }

    static constexpr float kWet = 2.0f;
    float fs_ = 48000.f;
    Fdn8 fdn_;
    AllpassDiffuser ap_[4];
    DualHeadShifter shifter_;
    Biquad hp_, inLp_, shHp_, shLp_;
    Smoother mix_, amt_, decay_, tone_, low_;
    float ret_ = 0.f, norm_ = 1.f, wetGain_ = 1.f;
    float env_ = 0.f, envRel_ = 0.9999f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"shimmer", "Shimmer", "time", "Ambient reverb with octave-up shimmer in the tail.",
                    {bb::dsp::pPct("mix", "Mix", 30.f),
                     bb::dsp::pLog("decay", "Decay", 0.5f, 20.f, 5.f, "s"),
                     bb::dsp::pPct("amount", "Shimmer", 60.f),
                     bb::dsp::pEnum("shift", "Shift", {"+12", "+7", "+19", "-12"}, 0),
                     bb::dsp::pPct("tone", "Tone", 50.f),
                     bb::dsp::pLog("lowcut", "Low Cut", 40.f, 1000.f, 200.f, "Hz")},
                    true, [] { return std::make_unique<Shimmer>(); }});
