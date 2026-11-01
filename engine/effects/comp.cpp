// Compressor, opto-style: very soft knee, smoothed gain with a program-dependent release (the
// longer and harder it has been squeezing, the slower it lets go, like an LA-2A's cell),
// auto makeup gain, parallel Blend and an output Level trim. Zero latency.
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Comp final : public Effect {
public:
    enum { kThresh, kRatio, kAttack, kRelease, kBlend, kLevel };
    void prepare(float fs, int) override {
        fs_ = fs;
        blend_.setTime(20.f, fs);
        out_.setTime(30.f, fs);
        makeup_.setTime(60.f, fs);
        scHp_.c = rbj::highpass(fs, 30.0, 0.7071);
        pkDecay_ = std::exp(-1.f / (0.025f * fs));
        memCoef_ = onePoleCoef(1500.f, fs);
        primed_ = false;
        reset();
    }
    void reset() override {
        scHp_.reset();
        pk_ = 0.f;
        grDb_ = 0.f;
        mem_ = 0.f;
        blend_.snap();
        out_.snap();
        makeup_.snap();
        activity_ = 0.f;
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kThresh: thresh_ = v; break;
            case kRatio: ratio_ = std::max(1.f, v); break;
            case kAttack: atk_ = onePoleCoef(v, fs_); break;
            case kRelease: relMs_ = v; break;
            case kBlend: blend_.set(0.01f * v, primed_); break;
            case kLevel: out_.set(dbToLin(v), primed_); break;
            default: break;
        }
        // auto makeup: undo the reduction a typical DI peak (-10 dBFS) would get
        makeup_.set(dbToLin(staticGr(-10.f)), primed_);
    }
    void process(float* x, int n) override {
        primed_ = true;
        const float relBase = relMs_;
        for (int i = 0; i < n; ++i) {
            const float in = x[i];
            // peak detector with ~25 ms decay: smooth enough that a 31 Hz note does not ripple the gain
            const float k = std::fabs(scHp_.process(in));
            pk_ = k > pk_ ? k : pk_ * pkDecay_;
            const float target = staticGr(linToDb(pk_ + 1e-9f));  // dB of reduction (>= 0)
            if (target > grDb_) {
                // program-dependent attack: up to 4x faster when a hit overshoots by > 6 dB
                // (keeps the opto bloom on normal playing, tames slap/pop spikes)
                const float over = clampf((target - grDb_ - 6.f) * (1.f / 12.f), 0.f, 1.f);
                grDb_ += std::min(1.f, atk_ * (1.f + 3.f * over)) * (target - grDb_);
            } else {
                // opto memory: release slows from 1x to 4x as sustained reduction builds up
                const float rel = relBase * (1.f + 3.f * mem_);
                grDb_ += (1.f / std::max(1.f, 0.001f * rel * fs_)) * (target - grDb_);
            }
            mem_ += memCoef_ * (clampf(grDb_ * (1.f / 12.f), 0.f, 1.f) - mem_);
            const float wet = in * dbToLin(-grDb_) * makeup_.next();
            const float b = blend_.next();
            x[i] = (in + b * (wet - in)) * out_.next();
        }
        scHp_.flushState();
        pk_ = flush(pk_);
        grDb_ = flush(grDb_);
        mem_ = flush(mem_);
        flushBlock(x, n);
        activity_ = clampf(grDb_ * (1.f / 20.f), 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    // static curve with a 12 dB soft knee; returns gain reduction in dB (positive)
    float staticGr(float levelDb) const {
        const float knee = 12.f;
        const float over = levelDb - thresh_;
        const float slope = 1.f - 1.f / ratio_;
        if (over <= -0.5f * knee) return 0.f;
        if (over >= 0.5f * knee) return slope * over;
        const float t = over + 0.5f * knee;
        return slope * t * t / (2.f * knee);
    }
    float fs_ = 48000.f;
    float thresh_ = -24.f, ratio_ = 4.f, atk_ = 0.01f, relMs_ = 250.f;
    Smoother blend_, out_, makeup_;
    Biquad scHp_;
    float pk_ = 0.f, pkDecay_ = 0.999f, grDb_ = 0.f, mem_ = 0.f, memCoef_ = 0.f;
    float activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"comp", "Compressor", "dynamics", "Smooth opto leveling with auto makeup and parallel blend.",
                    {bb::dsp::pDb("thresh", "Thresh", -50.f, 0.f, -24.f),
                     bb::dsp::pLog("ratio", "Ratio", 1.f, 20.f, 4.f, "x"),
                     bb::dsp::pLog("attack", "Attack", 0.5f, 100.f, 6.f, "ms"),
                     bb::dsp::pLog("release", "Release", 30.f, 2000.f, 250.f, "ms"),
                     bb::dsp::pPct("blend", "Blend", 100.f),
                     bb::dsp::pDb("level", "Level", -12.f, 12.f, 0.f)},
                    false, [] { return std::make_unique<Comp>(); }});
