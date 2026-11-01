// Envelope Filter (Mu-Tron III style auto-wah).
// Envelope: peak follower on the input (2 ms attack, 120 ms release, like the Mu-Tron's
// LED/photocell) plus a 4 ms smoother so low notes don't ripple the cutoff. Sens sets how hard
// the envelope drives the sweep (soft-saturating curve). Up sweeps from 150 Hz to Range, Down
// from Range to 150 Hz, exponentially. Filter: Simper SVF with per-sample cutoff and LP/BP/HP
// modes; resonant peaks are tamed by a Q-dependent input trim and a soft clipper (the Mu-Tron
// also overloads gracefully). Mix blends the dry signal back in.
#include "dsp.h"
#include "dsp_filter.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class EnvFilter final : public Effect {
public:
    enum { kSens, kQ, kRange, kDir, kMode, kMix };
    void prepare(float fs, int) override {
        fs_ = fs;
        env_.set(2.f, 120.f, fs);
        envSm_ = onePoleCoef(4.f, fs);
        sens_.setTime(30.f, fs);
        q_.setTime(30.f, fs);
        range_.setTime(30.f, fs);
        mix_.setTime(20.f, fs);
        dir_.setTime(25.f, fs);
        for (auto& m : mode_) m.setTime(15.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        env_.reset();
        e2_ = 0.f;
        svf_.reset();
        sens_.snap(); q_.snap(); range_.snap(); mix_.snap(); dir_.snap();
        for (auto& m : mode_) m.snap();
        activity_ = 0.f;
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kSens: sens_.set(v, primed_); break;
            case kQ: q_.set(std::log(v), primed_); break;
            case kRange: range_.set(std::log(v), primed_); break;
            case kDir: dir_.set(v >= 0.5f ? 1.f : 0.f, primed_); break;
            case kMode: {
                const int md = int(v + 0.5f);
                for (int k = 0; k < 3; ++k) mode_[k].set(k == md ? 1.f : 0.f, primed_);
                break;
            }
            case kMix: mix_.set(0.01f * v, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        const float lo = std::log2(150.f);
        float sweepMax = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            // parameter-only terms at sub-block rate
            for (int k = 0; k < kSub; ++k) { sens_.next(); q_.next(); range_.next(); }
            // sens 0..100 -> envelope gain -18..+30 dB (50: a -10 dBFS note sweeps ~3/4 of the
            // way); soft-saturating response 0..1
            const float g = 2.2f * dbToLin(-18.f + 0.48f * sens_.cur);
            const float lq = q_.cur;
            const float q = std::exp(lq);
            const float k = 1.f / q;
            const float bpComp = 4.7f * std::exp(0.5f * (lq - 1.6094f)) * k;    // * sqrt(q/5) / q
            const float hpComp = std::exp(0.43f * (1.6094f - lq));             // (5/q)^0.43
            const float hi = range_.cur * 1.4426950f;                          // log2(range)
            const float qTrim = 0.16f * (q - 0.7f);
            for (int i = i0; i < i0 + m; ++i) {
                const float in = x[i];
                env_.tick(in);
                e2_ += envSm_ * (env_.env - e2_);
                const float s = 1.f - std::exp(-e2_ * g);
                const float d = dir_.next();
                const float sweep = s + d * (1.f - 2.f * s);  // Down: 1 - s
                const float fc = fastExp2(lo + sweep * (hi - lo));
                svf_.setG(fastG(fc, fs_), k);
                // resonance parked on the strong low harmonics of a bass note is loud: trim the
                // input as Q rises, most when the cutoff is low (where the bass energy lives)
                const float low = 1.f - 0.75f * sweep;
                const float trim = 1.f / (1.f + qTrim * low * low);
                svf_.tick(in * trim);
                // BP/HP lose the bass fundamental, more so as they sweep up: position- and
                // Q-dependent makeup keeps the three modes at similar loudness
                const float up2 = fastExp2(1.9f * sweep);
                const float mLp = mode_[0].next(), mBp = mode_[1].next(), mHp = mode_[2].next();
                float y = mLp * svf_.lp + (mBp * svf_.bp * bpComp + mHp * svf_.hp * hpComp) * up2;
                y = softClip(y * kMakeup);
                const float mx = mix_.next();
                x[i] = in + mx * (y - in);
                sweepMax = std::max(sweepMax, d > 0.5f ? 1.f - sweep : sweep);
            }
        }
        env_.flushState();
        e2_ = flush(e2_);
        svf_.flushState();
        flushBlock(x, n);
        activity_ = clampf(sweepMax, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    static constexpr float kMakeup = 1.05f;
    // transparent below ~0.6, rounds off above (ceiling ~1.1)
    static float softClip(float v) {
        const float a = std::fabs(v);
        if (a < 0.6f) return v;
        const float s = v > 0.f ? 1.f : -1.f;
        return s * (0.6f + 0.5f * fastTanh((a - 0.6f) * 2.f));
    }
    float fs_ = 48000.f;
    EnvFollower env_;
    float e2_ = 0.f, envSm_ = 0.f;
    Smoother sens_, q_, range_, mix_, dir_, mode_[3];
    Svf svf_;
    float activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"env_filter", "Envelope Filter", "filter", "Mu-Tron-style auto-wah that quacks with your attack.",
                    {bb::dsp::pPct("sens", "Sens", 50.f),
                     bb::dsp::pLog("q", "Q", 0.7f, 12.f, 5.f),
                     bb::dsp::pLog("range", "Range", 400.f, 4000.f, 1500.f, "Hz"),
                     bb::dsp::pEnum("dir", "Dir", {"Up", "Down"}, 0),
                     bb::dsp::pEnum("mode", "Mode", {"LP", "BP", "HP"}, 0),
                     bb::dsp::pPct("mix", "Mix", 100.f)},
                    false, [] { return std::make_unique<EnvFilter>(); }});
