// Reverb: 8-line modulated FDN (Hadamard feedback) with 4 input diffusers.
//   input -> PreDly -> Low Cut (12 dB/oct HP) -> 9 kHz LP -> 4 allpass diffusers -> FDN
//   out = dry (unity) + Mix * wet
// Decay is the low-frequency RT60 in seconds (per-line gains from line lengths); Damp sets a
// one-pole low-pass in every line so highs die faster; Size scales all line lengths (glides,
// so moving it warbles briefly instead of clicking). The wet level is half-normalised against
// the FDN's steady-state gain so long decays are not much louder than short ones.
#include "dsp_time.h"

namespace {
using namespace bb;
using namespace bb::dsp;
using namespace bb::dsp::tfx;

constexpr float kMaxPreMs = 200.f;
constexpr float kMinScale = 0.25f, kMaxScale = 1.0f;

class Reverb final : public Effect {
public:
    enum { kMix, kDecay, kSize, kDamp, kPreDly, kLowCut };

    void prepare(float fs, int) override {
        fs_ = fs;
        fdn_.init(fs, kMaxScale, 6.f * fs / 48000.f);
        pre_.init(int(0.001f * kMaxPreMs * fs) + 8);
        static const int kAp[4] = {142, 107, 379, 277};
        static const float kApG[4] = {0.70f, 0.70f, 0.62f, 0.62f};
        for (int k = 0; k < 4; ++k) ap_[k].init(int(float(kAp[k]) * fs / 48000.f), kApG[k]);
        inLp_.c = rbj::lowpass(fs, 9000.0, 0.70710678);
        mix_.setTime(30.f, fs);
        scale_.setTime(150.f, fs);
        preSm_.setTime(70.f, fs);
        decay_.setTime(60.f, fs / kSub);
        damp_.setTime(40.f, fs / kSub);
        low_.setTime(40.f, fs / kSub);
        envRel_ = std::exp(-1.f / (0.3f * fs));
        primed_ = false;
        reset();
    }

    void reset() override {
        fdn_.clear();
        pre_.clear();
        for (auto& a : ap_) a.clear();
        hp_.reset();
        inLp_.reset();
        mix_.snap();
        scale_.snap();
        preSm_.snap();
        decay_.snap();
        damp_.snap();
        low_.snap();
        env_ = 0.f;
        activity_ = 0.f;
        update();
    }

    void setParam(int i, float v) override {
        switch (i) {
            case kMix: mix_.set(clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kDecay: decay_.set(std::log(clampf(v, 0.1f, 30.f)), primed_); break;
            case kSize: scale_.set(kMinScale + (kMaxScale - kMinScale) * clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kDamp: damp_.set(clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kPreDly: preSm_.set(clampf(v, 0.f, kMaxPreMs) * 0.001f * fs_, primed_); break;
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
            const bool dirty = !decay_.settled() || !damp_.settled() || !low_.settled() || !scale_.settled();
            if (dirty) {
                decay_.next();
                damp_.next();
                low_.next();
                update();
            }
            for (int i = i0; i < i0 + m; ++i) {
                const float in = x[i];
                pre_.push(in);
                float s = pre_.readLinear(preSm_.next());
                s = inLp_.process(hp_.process(s));
                for (auto& a : ap_) s = a.tick(s);
                const float y = fdn_.tick(s, scale_.next()) * wetGain_;
                x[i] = in + mix_.next() * y;
                const float a = std::fabs(y);
                env = a > env ? a : env * envRel_;
            }
        }
        fdn_.flushState();
        hp_.flushState();
        inLp_.flushState();
        env_ = flush(env);
        flushBlock(x, n);
        activity_ = clampf(env_ * 2.5f, 0.f, 1.f);
    }

    float activity() const override { return activity_; }

private:
    void update() {
        const float rt = std::exp(decay_.cur);
        fdn_.setDecay(rt, scale_.s2);
        // Damp 0..1 -> in-loop low-pass 16 kHz .. 1.5 kHz
        fdn_.setDamp(16000.f * std::pow(1500.f / 16000.f, damp_.cur));
        hp_.c = rbj::highpass(fs_, std::exp(low_.cur), 0.70710678);
        // half-normalise the steady-state power gain 1/(1-g^2); kWet calibrated on the riff
        const float g = fdn_.gMean;
        wetGain_ = kWet * std::pow(std::max(1e-4f, 1.f - g * g), 0.25f);
    }

    static constexpr float kWet = 2.3f;
    float fs_ = 48000.f;
    Fdn8 fdn_;
    DelayLine pre_;
    AllpassDiffuser ap_[4];
    Biquad hp_, inLp_;
    Smoother mix_, decay_, damp_, low_;
    Glide2 scale_, preSm_;
    float wetGain_ = 1.f;
    float env_ = 0.f, envRel_ = 0.9999f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"reverb", "Reverb", "time", "Smooth reverb with a low cut that keeps the bass clear.",
                    {bb::dsp::pPct("mix", "Mix", 25.f),
                     bb::dsp::pLog("decay", "Decay", 0.2f, 10.f, 2.f, "s"),
                     bb::dsp::pPct("size", "Size", 60.f),
                     bb::dsp::pPct("damp", "Damp", 50.f),
                     bb::dsp::pLin("predly", "PreDly", 0.f, 200.f, 10.f, "ms"),
                     bb::dsp::pLog("lowcut", "Low Cut", 20.f, 500.f, 150.f, "Hz")},
                    true, [] { return std::make_unique<Reverb>(); }});
