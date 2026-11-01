// Synth Filter: resonant 4-pole ladder low-pass (Moog style) swept by an envelope and an LFO.
// Zero-delay-feedback ladder of four TPT one-poles: the linear ZDF solution predicts the output,
// the feedback sum is saturated (tanh) and the stages are run with it, so resonance stays stable
// up to Reso = 100% (k = 3.85, just short of self-oscillation) and loud resonant peaks compress
// instead of exploding. Passband loss from resonance is partly made up (the classic ladder gets
// thin at high Reso; this one keeps the bass). Cutoff = Cutoff * 2^(Env*env*4 oct + LFO*sin*2 oct),
// computed per sample with cheap exp2/tan approximations, all params smoothed.
#include "dsp.h"
#include "dsp_filter.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class SynthFilter final : public Effect {
public:
    enum { kCutoff, kReso, kEnv, kLfo, kRate, kMix };
    void prepare(float fs, int) override {
        fs_ = fs;
        cut_.setTime(25.f, fs);
        reso_.setTime(25.f, fs / kSub);
        env_.setTime(25.f, fs / kSub);
        lfo_.setTime(25.f, fs / kSub);
        rate_.setTime(50.f, fs / kSub);
        mix_.setTime(20.f, fs);
        ef_.set(3.f, 160.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        cut_.snap(); reso_.snap(); env_.snap(); lfo_.snap(); rate_.snap(); mix_.snap();
        ef_.reset();
        for (auto& s : s_) s = 0.f;
        phase_ = 0.f;
        activity_ = 0.f;
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kCutoff: cut_.set(std::log2(v), primed_); break;
            case kReso: reso_.set(0.01f * v, primed_); break;
            case kEnv: env_.set(0.01f * v, primed_); break;
            case kLfo: lfo_.set(0.01f * v, primed_); break;
            case kRate: rate_.set(v, primed_); break;
            case kMix: mix_.set(0.01f * v, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        float lastFc = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            reso_.next(); env_.next(); lfo_.next(); rate_.next();
            const float k = 3.85f * reso_.cur;
            const float comp = 1.25f + 0.6f * k;     // partial passband make-up
            const float envOct = 4.f * env_.cur;     // +-4 octaves at +-100%
            const float lfoOct = 2.f * lfo_.cur;     // +-2 octaves at 100%
            const float inc = rate_.cur / fs_;
            for (int i = i0; i < i0 + m; ++i) {
                const float in = x[i];
                const float e = clampf(ef_.tick(in) * 3.f, 0.f, 1.f);
                phase_ += inc;
                if (phase_ >= 1.f) phase_ -= 1.f;
                const float l = fastSin2Pi(phase_);
                const float lfc = clampf(cut_.next() + envOct * e + lfoOct * l, 4.3f, 14.2f);  // 20 Hz .. 19 kHz
                lastFc = lfc;
                const float g = fastG(fastExp2(lfc), fs_);
                const float G = g / (1.f + g);
                const float b = 1.f - G;
                // linear ZDF prediction of the 4th stage output
                const float S = G * G * G * b * s_[0] + G * G * b * s_[1] + G * b * s_[2] + b * s_[3];
                const float G4 = G * G * G * G;
                const float y4 = (G4 * in + S) / (1.f + k * G4);
                // saturated feedback sum, then run the stages for real
                float u = sat(in - k * y4);
                for (int st = 0; st < 4; ++st) {
                    const float v = G * (u - s_[st]);
                    const float y = v + s_[st];
                    s_[st] = y + v;
                    u = y;
                }
                const float out = u * comp;
                x[i] = in + mix_.next() * (out - in);
            }
        }
        for (auto& s : s_) s = flush(s);
        ef_.flushState();
        flushBlock(x, n);
        activity_ = clampf((lastFc - 4.3f) / (14.2f - 4.3f), 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    // unity slope, soft ceiling ~1.4: transparent for normal bass levels
    static float sat(float v) { return 1.4f * fastTanh(v * (1.f / 1.4f)); }
    float fs_ = 48000.f;
    Smoother cut_, reso_, env_, lfo_, rate_, mix_;
    EnvFollower ef_;
    float s_[4] = {};
    float phase_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"synth_filter", "Synth Filter", "filter", "Resonant 4-pole ladder low-pass swept by envelope and LFO.",
                    {bb::dsp::pLog("cutoff", "Cutoff", 40.f, 8000.f, 500.f, "Hz"),
                     bb::dsp::pPct("reso", "Reso", 50.f),
                     bb::dsp::pLin("env", "Env", -100.f, 100.f, 50.f, "%"),
                     bb::dsp::pPct("lfo", "LFO", 0.f),
                     bb::dsp::pLog("rate", "Rate", 0.05f, 10.f, 1.f, "Hz"),
                     bb::dsp::pPct("mix", "Mix", 100.f)},
                    false, [] { return std::make_unique<SynthFilter>(); }});
