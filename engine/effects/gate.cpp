// Noise Gate with hysteresis and hold (no chatter on decaying bass notes).
// Detector: peak follower (instant attack, 40 ms decay) on a 25 Hz high-passed key, so low-B
// ripple stays inside the hysteresis window. Gain glides in the log domain (attack/release).
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Gate final : public Effect {
public:
    enum { kThresh, kRelease, kHyst, kHold, kAttack, kRange };
    void prepare(float fs, int) override {
        fs_ = fs;
        keyHp_.c = rbj::highpass(fs, 25.0, 0.7071);
        decay_ = std::exp(-1.f / (0.040f * fs));
        reset();
    }
    void reset() override {
        keyHp_.reset();
        env_ = 0.f;
        open_ = false;
        holdLeft_ = 0;
        gDb_ = range_;
        activity_ = 0.f;
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kThresh: thresh_ = v; break;
            case kRelease: relMs_ = v; break;
            case kHyst: hyst_ = v; break;
            case kHold: holdMs_ = v; break;
            case kAttack: atkMs_ = v; break;
            case kRange: range_ = v; break;
            default: break;
        }
        // gain slews in dB: attack/release given as time for a 60 dB change, but never slower than
        // the range itself needs
        atkStep_ = 60.f / std::max(1.f, 0.001f * atkMs_ * fs_);
        relStep_ = 60.f / std::max(1.f, 0.001f * relMs_ * fs_);
        holdN_ = int(0.001f * holdMs_ * fs_);
    }
    void process(float* x, int n) override {
        const float openLin = dbToLin(thresh_);
        const float closeLin = dbToLin(thresh_ - hyst_);
        for (int i = 0; i < n; ++i) {
            const float k = std::fabs(keyHp_.process(x[i]));
            env_ = k > env_ ? k : env_ * decay_;
            if (open_) {
                if (env_ < closeLin) {
                    if (holdLeft_ > 0) --holdLeft_;
                    else open_ = false;
                } else {
                    holdLeft_ = holdN_;
                }
            } else if (env_ > openLin) {
                open_ = true;
                holdLeft_ = holdN_;
            }
            if (open_) gDb_ = std::min(0.f, gDb_ + atkStep_);
            else gDb_ = std::max(range_, gDb_ - relStep_);
            // smooth the corners of the dB ramp with a short one-pole on the linear gain
            const float g = gDb_ <= -89.9f ? 0.f : dbToLin(gDb_);
            gLin_ += 0.25f * (g - gLin_);
            x[i] *= gLin_;
        }
        keyHp_.flushState();
        env_ = flush(env_);
        gLin_ = flush(gLin_);
        const float floor = range_ <= -89.9f ? 0.f : dbToLin(range_);
        flushBlock(x, n);
        activity_ = clampf((gLin_ - floor) / std::max(1e-6f, 1.f - floor), 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    float fs_ = 48000.f;
    Biquad keyHp_;
    float decay_ = 0.999f, env_ = 0.f;
    float thresh_ = -60.f, relMs_ = 120.f, hyst_ = 6.f, holdMs_ = 50.f, atkMs_ = 1.f, range_ = -80.f;
    float atkStep_ = 1.f, relStep_ = 0.01f;
    int holdN_ = 0, holdLeft_ = 0;
    bool open_ = false;
    float gDb_ = -80.f, gLin_ = 0.f, activity_ = 0.f;
};

}  // namespace

BB_REGISTER_EFFECT({"gate", "Noise Gate", "dynamics", "Silences hum and hiss between notes, without chatter.",
                    {bb::dsp::pDb("thresh", "Thresh", -90.f, -20.f, -60.f),
                     bb::dsp::pLog("release", "Release", 5.f, 2000.f, 150.f, "ms"),
                     bb::dsp::pDb("hyst", "Hyst", 0.f, 20.f, 6.f),
                     bb::dsp::pLin("hold", "Hold", 0.f, 500.f, 50.f, "ms"),
                     bb::dsp::pLog("attack", "Attack", 0.1f, 50.f, 1.f, "ms"),
                     bb::dsp::pDb("range", "Range", -90.f, 0.f, -90.f)},
                    false, [] { return std::make_unique<Gate>(); }});
