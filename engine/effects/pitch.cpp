// Pitch Shifter: dual-head delay-line shifter with pitch-synchronous splices.
// A read head moves through a delay line at (1 - ratio) samples per sample. When it runs out of
// room, a second head starts J samples away and the two crossfade (12 ms raised cosine). J is a
// whole number of periods of the note being played (from the shared PitchTracker), close to 30 ms,
// so the two heads read the same point of the waveform and the splice is inaudible on monophonic
// bass, even for low B (period 32 ms). Without a stable pitch J falls back to 30 ms.
// Shift/Fine glide (~40 ms) so knob moves sweep instead of jumping. Equal-power Mix.
#include "dsp.h"
#include "dsp_filter.h"
#include "dsp_pitch.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Pitch final : public Effect {
public:
    enum { kShift, kMix, kFine };
    void prepare(float fs, int) override {
        fs_ = fs;
        dl_.init(int(0.25f * fs));
        tr_.init(fs);
        semis_.setTime(40.f, fs / kSub);
        mix_.setTime(20.f, fs);
        xfLen_ = 0.012f * fs;
        jNom_ = 0.030f * fs;
        primed_ = false;
        reset();
    }
    void reset() override {
        dl_.clear();
        tr_.reset();
        semis_.snap();
        mix_.snap();
        d_[0] = d_[1] = 8.f;
        active_ = 0;
        fading_ = false;
        fade_ = 0.f;
        wetEnv_ = activity_ = 0.f;
        updateRatio();
    }
    void setParam(int i, float v) override {
        if (i == kShift) shift_ = v;
        else if (i == kFine) fine_ = v;
        else if (i == kMix) mix_.set(0.01f * v, primed_);
        if (i != kMix) {
            semis_.set(shift_ + 0.01f * fine_, primed_);
            if (!primed_) updateRatio();
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        float pk = 0.f;
        const float maxD = float(dl_.size() - 8);
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!semis_.settled()) { semis_.next(); updateRatio(); }
            const float rate = 1.f - ratio_;
            // lowest delay that leaves room for a head moving at (ratio-1) through a crossfade
            const float dLo = 4.f + xfLen_ * std::max(0.f, ratio_ - 1.f);
            for (int i = i0; i < i0 + m; ++i) {
                const float in = x[i];
                dl_.push(in);
                tr_.tick(in);
                d_[0] = clampf(d_[0] + rate, 2.f, maxD);
                d_[1] = clampf(d_[1] + rate, 2.f, maxD);
                const int a = active_;
                if (!fading_) {
                    const float J = spliceLen();
                    if (ratio_ > 1.0001f && d_[a] <= dLo) startSplice(d_[a] + J);
                    else if (ratio_ < 0.9999f && d_[a] >= dLo + J) startSplice(d_[a] - J);
                    else if (std::fabs(rate) <= 1e-4f && d_[a] > dLo + 64.f) {
                        // unity ratio: walk the latency back down in whole periods
                        const float P = tr_.voiced ? tr_.period : 0.f;
                        const float back = P > 0.f ? P * std::floor((d_[a] - dLo) / P) : d_[a] - dLo;
                        if (back > 1.f) startSplice(d_[a] - back);
                    }
                }
                float wet;
                if (fading_) {
                    fade_ += 1.f / xfLen_;
                    if (fade_ >= 1.f) { fade_ = 1.f; fading_ = false; active_ ^= 1; }
                    const float gNew = 0.5f - 0.5f * std::cos(kPi * fade_);
                    const int b = fading_ ? (a ^ 1) : active_;
                    const int o = fading_ ? a : (active_ ^ 1);
                    wet = gNew * dl_.readHermite(d_[b]) + (1.f - gNew) * dl_.readHermite(d_[o]);
                } else {
                    wet = dl_.readHermite(d_[active_]);
                }
                const float mx = mix_.next();
                const float gw = fastSin2Pi(0.25f * mx);           // sin(mix * pi/2)
                const float gd = fastSin2Pi(0.25f * mx + 0.25f);   // cos(mix * pi/2)
                x[i] = gd * in + gw * wet;
                pk = std::max(pk, std::fabs(wet) * gw);
            }
        }
        tr_.flushState();
        flushBlock(x, n);
        wetEnv_ = std::max(pk, wetEnv_ * 0.9f);
        activity_ = clampf(wetEnv_ * 3.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    float spliceLen() const {
        if (tr_.voiced && tr_.stable && tr_.period > 0.f) {
            const float P = tr_.period;
            const float k = std::max(1.f, std::floor(jNom_ / P + 0.5f));
            return k * P;
        }
        return jNom_;
    }
    void startSplice(float dNew) {
        const int b = active_ ^ 1;
        d_[b] = clampf(dNew, 2.f, float(dl_.size() - 8));
        fading_ = true;
        fade_ = 0.f;
    }
    void updateRatio() { ratio_ = fastExp2(semis_.cur * (1.f / 12.f)); }

    float fs_ = 48000.f;
    DelayLine dl_;
    PitchTracker tr_;
    Smoother semis_, mix_;
    float shift_ = 12.f, fine_ = 0.f, ratio_ = 2.f;
    float d_[2] = {8.f, 8.f};
    int active_ = 0;
    bool fading_ = false;
    float fade_ = 0.f, xfLen_ = 576.f, jNom_ = 1440.f;
    float wetEnv_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"pitch", "Pitch Shifter", "pitch", "Shift up or down by up to an octave, blended with dry.",
                    {bb::dsp::pLin("shift", "Shift", -12.f, 12.f, 12.f, "st", 1.f),
                     bb::dsp::pPct("mix", "Mix", 50.f),
                     bb::dsp::pLin("fine", "Fine", -50.f, 50.f, 0.f, "ct")},
                    false, [] { return std::make_unique<Pitch>(); }});
