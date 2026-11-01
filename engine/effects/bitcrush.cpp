// Bitcrusher: sample-and-hold rate reduction + mid-tread bit-depth quantisation on the band above
// Low Keep (the sub stays clean). Deliberately aliasing (that is the sound); Bits glides
// continuously (fractional bit depths), Rate glides in log space.
#include "dsp_drive.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Bitcrush final : public Effect {
public:
    enum { kBits, kRate, kMix, kLowKeep, kLevel };
    void prepare(float fs, int) override {
        fs_ = fs;
        dc_.prepare(fs);
        bits_.setTime(40.f, fs / kSub);
        rate_.setTime(40.f, fs / kSub);
        dc_.makeup.snap(1.f);
        reset();
    }
    void reset() override {
        dc_.reset();
        dc_.makeup.snap(1.f);
        bits_.snap();
        rate_.snap();
        phase_ = 1.f;
        hold_ = 0.f;
        update();
    }
    void setParam(int i, float v) override {
        const bool p = dc_.primed;
        switch (i) {
            case kBits: bits_.set(v, p); break;
            case kRate: rate_.set(std::log(v), p); break;
            case kMix: dc_.setBlend(v); break;
            case kLowKeep: dc_.setLowKeep(v); break;
            case kLevel: dc_.setLevel(v); break;
            default: break;
        }
        if (!p) update();
    }
    void process(float* x, int n) override {
        dc_.primed = true;
        float err = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!bits_.settled() || !rate_.settled()) { bits_.next(); rate_.next(); update(); }
            driveBlock(dc_, x + i0, m, [&](float* hi, int mm) {
                for (int i = 0; i < mm; ++i) {
                    phase_ += inc_;
                    if (phase_ >= 1.f) {
                        phase_ -= std::floor(phase_);
                        hold_ = step_ * std::floor(hi[i] * invStep_ + 0.5f);
                    }
                    err = std::max(err, std::fabs(hold_ - hi[i]));
                    hi[i] = hold_;
                }
            });
        }
        dc_.flushState();
        flushBlock(x, n);
        env_ = std::max(err, env_ * 0.8f);
        activity_ = clampf(env_ * 4.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    void update() {
        step_ = std::exp2(1.f - bits_.cur);  // 16 bits -> 3e-5, 1 bit -> 1 (values -1, 0, 1)
        invStep_ = 1.f / step_;
        inc_ = clampf(std::exp(rate_.cur) / fs_, 0.f, 1.f);
    }
    float fs_ = 48000.f;
    DriveCommon dc_;
    Smoother bits_, rate_;
    float step_ = 1.f / 128.f, invStep_ = 128.f, inc_ = 0.25f, phase_ = 1.f, hold_ = 0.f;
    float env_ = 0.f, activity_ = 0.f;
};

}  // namespace

BB_REGISTER_EFFECT({"bitcrush", "Bitcrusher", "drive", "Lo-fi bit depth and sample-rate reduction above a clean sub.",
                    {bb::dsp::pLin("bits", "Bits", 1.f, 16.f, 8.f),
                     bb::dsp::pLog("rate", "Rate", 500.f, 48000.f, 12000.f, "Hz"),
                     bb::dsp::pPct("mix", "Mix", 100.f),
                     bb::dsp::pLog("lowkeep", "Low Keep", 40.f, 400.f, 100.f, "Hz"),
                     bb::dsp::pDb("level", "Level", -24.f, 12.f, 0.f)},
                    false, [] { return std::make_unique<Bitcrush>(); }});
