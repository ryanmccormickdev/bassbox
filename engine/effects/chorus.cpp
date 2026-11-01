// Chorus: three BBD-style voices (9.5-13.5 ms, sine LFOs spread 120 degrees with slightly
// detuned rates) on the band above Low Cut; the band below stays dry and mono-solid.
// Wet is gently darkened (one-pole ~7 kHz) like a bucket-brigade line.
#include "dsp_mod.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr int kVoices = 3;
constexpr float kBaseMs[kVoices] = {11.f, 13.5f, 9.5f};
constexpr float kRateMul[kVoices] = {1.f, 0.87f, 1.13f};
constexpr float kMaxSweepMs = 4.f;  // +- excursion at Depth 100%

class Chorus final : public Effect {
public:
    enum { kRate, kDepth, kMix, kLowCut };
    void prepare(float fs, int) override {
        fs_ = fs;
        dl_.init(int(0.030f * fs));
        split_.prepare(fs);
        rate_.setTime(50.f, fs);
        depth_.setTime(40.f, fs);
        mix_.setTime(20.f, fs);
        tone_.set(fs, 7000.f);
        primed_ = false;
        for (int v = 0; v < kVoices; ++v) lfo_[v].phase = float(v) / float(kVoices);
        reset();
    }
    void reset() override {
        dl_.clear();
        split_.reset();
        tone_.reset();
        rate_.snap();
        depth_.snap();
        mix_.snap();
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kRate: rate_.set(v, primed_); break;
            case kDepth: depth_.set(0.01f * v * kMaxSweepMs * 0.001f * fs_, primed_); break;
            case kMix: mix_.set(0.01f * v, primed_); break;
            case kLowCut: split_.setFreq(v, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        const float invFs = 1.f / fs_;
        float base[kVoices];
        for (int v = 0; v < kVoices; ++v) base[v] = kBaseMs[v] * 0.001f * fs_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            split_.tick();
            for (int i = i0; i < i0 + m; ++i) {
                float lo, hi;
                split_.split(x[i], lo, hi);
                dl_.push(hi);
                const float r = rate_.next() * invFs;
                const float dep = depth_.next();
                float wet = 0.f;
                for (int v = 0; v < kVoices; ++v) {
                    lfo_[v].inc = r * kRateMul[v];
                    const float s = fastSin2Pi(lfo_[v].tick());
                    wet += dl_.readHermite(base[v] + dep * s);
                }
                wet = tone_.lp(wet * (1.f / kVoices) * 1.6f);
                float gd, gw;
                mixGains(mix_.next(), gd, gw);
                x[i] = lo + gd * hi + gw * wet;
            }
        }
        split_.flushState();
        tone_.flushState();
        flushBlock(x, n);
        activity_ = 0.5f + 0.5f * fastSin2Pi(lfo_[0].phase);
    }
    float activity() const override { return activity_; }

private:
    float fs_ = 48000.f;
    DelayLine dl_;
    LowSplit split_;
    Lfo lfo_[kVoices];
    Smoother rate_, depth_, mix_;
    OnePole tone_;
    float activity_ = 0.5f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"chorus", "Chorus", "modulation", "Lush chorus; everything below Low Cut stays dry and solid.",
                    {bb::dsp::pLog("rate", "Rate", 0.05f, 5.f, 0.6f, "Hz"),
                     bb::dsp::pPct("depth", "Depth", 50.f),
                     bb::dsp::pPct("mix", "Mix", 50.f),
                     bb::dsp::pLog("lowcut", "Low Cut", 40.f, 400.f, 120.f, "Hz")},
                    false, [] { return std::make_unique<Chorus>(); }});
