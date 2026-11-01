// Octaver: analog-style sub octaves for bass.
//   * PitchTracker (dsp_pitch.h) gives a near-sinusoidal detector signal y (f0 only) and
//     hysteresis-gated, guard-filtered upward zero crossings.
//   * Oct -1: a flip-flop toggles on every accepted crossing and flips the polarity of the
//     normalised detector signal (Boss OC-2 style). The flip happens exactly at a zero crossing,
//     so the voice is continuous and can never click, even when a crossing is rejected.
//   * Oct -2: a second flip-flop (toggles on every rising edge of the first) times |y|: a rounded
//     square at f0/4 that is also continuous at every flip.
//   * Subs follow the input envelope, are gated by the tracker's voicing (validated period +
//     level), low-passed by Tone, and high-passed at 20 Hz (keeps infrasonic halves of low-B
//     subs from eating headroom).
//   * Oct +1: full-wave rectified detector signal (clean 2 f0), DC-blocked, enveloped.
#include "dsp.h"
#include "dsp_pitch.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Octaver final : public Effect {
public:
    enum { kOct1, kOct2, kDry, kUp, kTone };
    void prepare(float fs, int) override {
        fs_ = fs;
        tr_.init(fs);
        for (auto& s : lvl_) s.setTime(20.f, fs);
        tone_.setTime(30.f, fs / kSub);
        envCoef_ = onePoleCoef(12.f, fs);
        gateAtk_ = onePoleCoef(3.f, fs);
        gateRel_ = onePoleCoef(25.f, fs);
        subHp_.c = rbj::highpass(fs, 20.0, 0.70710678);
        upDc_.set(fs, 20.f);
        primed_ = false;
        reset();
    }
    void reset() override {
        tr_.reset();
        for (auto& s : lvl_) s.snap();
        tone_.snap();
        toneLp_.reset();
        subHp_.reset();
        upDc_.reset();
        ff1_ = ff2_ = 1.f;
        env_ = gate_ = 0.f;
        subEnv_ = 0.f;
        activity_ = 0.f;
        updateTone();
    }
    void setParam(int i, float v) override {
        if (i >= kOct1 && i <= kUp) lvl_[i].set(0.01f * v, primed_);
        else if (i == kTone) {
            tone_.set(std::log(60.f) + 0.01f * v * std::log(1500.f / 60.f), primed_);
            if (!primed_) updateTone();
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        float subPk = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!tone_.settled()) { tone_.next(); updateTone(); }
            for (int i = i0; i < i0 + m; ++i) {
                const float in = x[i];
                tr_.tick(in);
                if (tr_.event) {
                    ff1_ = -ff1_;
                    if (ff1_ > 0.f) ff2_ = -ff2_;
                }
                const float yn = tr_.normalized();
                const float ay = std::fabs(yn);
                env_ += envCoef_ * (tr_.envIn - env_);
                const float gt = tr_.voiced ? 1.f : 0.f;
                gate_ += (gt > gate_ ? gateAtk_ : gateRel_) * (gt - gate_);
                const float e = env_ * gate_;
                const float o1 = lvl_[kOct1].next(), o2 = lvl_[kOct2].next();
                const float dry = lvl_[kDry].next(), up = lvl_[kUp].next();
                float sub = (o1 * ff1_ * yn + o2 * ff2_ * ay * 1.2f) * e * kSubGain;
                toneLp_.tick(sub);
                sub = subHp_.process(toneLp_.lp);
                const float upv = upDc_.process((ay - 0.63662f) * 2.35f * e) * up * kUpGain;
                x[i] = dry * in + sub + upv;
                subPk = std::max(subPk, std::fabs(sub) + std::fabs(upv));
            }
        }
        tr_.flushState();
        toneLp_.flushState();
        subHp_.flushState();
        upDc_.flushState();
        env_ = flush(env_);
        gate_ = flush(gate_);
        flushBlock(x, n);
        subEnv_ = std::max(subPk, subEnv_ * 0.9f);
        activity_ = clampf(subEnv_ * 3.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    static constexpr float kSubGain = 0.85f;
    static constexpr float kUpGain = 0.8f;
    void updateTone() { toneLp_.set(fs_, std::exp(tone_.cur), 0.65f); }
    float fs_ = 48000.f;
    PitchTracker tr_;
    Smoother lvl_[4], tone_;
    Svf toneLp_;
    Biquad subHp_;
    DcBlock upDc_;
    float ff1_ = 1.f, ff2_ = 1.f;
    float env_ = 0.f, gate_ = 0.f, envCoef_ = 0.f, gateAtk_ = 0.f, gateRel_ = 0.f;
    float subEnv_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"octaver", "Octaver", "pitch", "Analog-style sub octaves and octave up; tracks to low B.",
                    {bb::dsp::pPct("oct1", "Oct -1", 70.f),
                     bb::dsp::pPct("oct2", "Oct -2", 0.f),
                     bb::dsp::pPct("dry", "Dry", 80.f),
                     bb::dsp::pPct("up", "Oct +1", 0.f),
                     bb::dsp::pPct("tone", "Tone", 50.f)},
                    false, [] { return std::make_unique<Octaver>(); }});
