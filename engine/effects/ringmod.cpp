// Ring Mod: the band above Low Cut is multiplied by a pure sine carrier (Freq, glides in log
// space), optionally swept +-1 octave by a sine LFO (Rate/Depth). The product is scaled by
// sqrt(2) to make up the carrier's RMS, then mixed equal-power with the dry band. Lows stay dry.
#include "dsp_mod.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class RingMod final : public Effect {
public:
    enum { kFreq, kMix, kRate, kDepth, kLowCut };
    void prepare(float fs, int) override {
        fs_ = fs;
        split_.prepare(fs);
        freq_.setTime(40.f, fs);
        mix_.setTime(20.f, fs);
        rate_.setTime(60.f, fs);
        depth_.setTime(40.f, fs);
        env_.set(5.f, 120.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        split_.reset();
        env_.reset();
        carPhase_ = 0.0;
        freq_.snap(); mix_.snap(); rate_.snap(); depth_.snap();
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kFreq: freq_.set(std::log(v), primed_); break;
            case kMix: mix_.set(0.01f * v, primed_); break;
            case kRate: rate_.set(v, primed_); break;
            case kDepth: depth_.set(0.01f * v, primed_); depthT_ = 0.01f * v; break;
            case kLowCut: split_.setFreq(v, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        const float invFs = 1.f / fs_;
        const float ln2 = 0.69314718f;
        float lfoS = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            split_.tick();
            for (int i = i0; i < i0 + m; ++i) {
                float lo, hi;
                split_.split(x[i], lo, hi);
                lfo_.inc = rate_.next() * invFs;
                lfoS = fastSin2Pi(lfo_.tick());
                const float f = std::exp(freq_.next() + depth_.next() * lfoS * ln2);
                carPhase_ += double(f * invFs);
                if (carPhase_ >= 1.0) carPhase_ -= 1.0;
                const float car = std::sin(kTwoPi * float(carPhase_));
                const float wet = hi * car * 1.41421356f;
                float gd, gw;
                mixGains(mix_.next(), gd, gw);
                const float yh = gd * hi + gw * wet;
                env_.tick(yh);
                x[i] = lo + yh;
            }
        }
        split_.flushState();
        env_.flushState();
        flushBlock(x, n);
        // with the LFO in use show its sweep, otherwise how hard the modulator is being driven
        activity_ = depthT_ > 0.01f ? 0.5f + 0.5f * lfoS : clampf(env_.env * 4.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    float fs_ = 48000.f;
    LowSplit split_;
    Lfo lfo_;
    Smoother freq_, mix_, rate_, depth_;
    EnvFollower env_;
    double carPhase_ = 0.0;
    float depthT_ = 0.f;
    float activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"ringmod", "Ring Mod", "modulation", "Metallic ring modulation above a clean low end.",
                    {bb::dsp::pLog("freq", "Freq", 20.f, 2000.f, 300.f, "Hz"),
                     bb::dsp::pPct("mix", "Mix", 50.f),
                     bb::dsp::pLog("rate", "Rate", 0.05f, 10.f, 0.5f, "Hz"),
                     bb::dsp::pPct("depth", "Depth", 0.f),
                     bb::dsp::pLog("lowcut", "Low Cut", 40.f, 400.f, 150.f, "Hz")},
                    false, [] { return std::make_unique<RingMod>(); }});
