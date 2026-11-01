// Fuzz: Big Muff-style two-stage clipper for bass.
//   hi band -> [Sputter: smooth asymmetric dead zone = starved bias, notes break up and spit as
//   they decay] -> 4x oversampled { gain (Fuzz, +10..+50 dB) -> biased cubic clip (ADAA) -> coupling HP 90 Hz
//   + 6 kHz LP -> +20 dB -> clip -> 5 kHz LP } -> Muff tone stack (LP 450 Hz <-> HP 1.1 kHz blend,
//   mid scoop at noon) -> auto makeup -> clean sub below Low Keep -> Blend / Level.
#include "dsp_drive.h"

namespace {
using namespace bb;
using namespace bb::dsp;

// static output of each biased stage at zero input (subtracted so silence stays silent)
const float kB1 = float(cubicClip(0.2)), kB2 = float(cubicClip(-0.1));

class Fuzz final : public Effect {
public:
    enum { kFuzz, kTone, kLevel, kBlend, kLowKeep, kSputter };
    void prepare(float fs, int) override {
        fs_ = fs;
        dc_.prepare(fs);
        os_.init(4);
        gain_.setTime(30.f, fs);
        tone_.setTime(25.f, fs);
        dead_.setTime(30.f, fs);
        const float f4 = fs * 4.f;
        cpl_.set(f4, 90.f);
        lp1_.set(f4, 6000.f);
        lp2_.set(f4, 5000.f);
        tLp_.set(fs, 450.f);
        tHp_.set(fs, 1100.f);
        tone_.snap(0.5f);
        dead_.snap(0.f);
        static DriveCal cache;
        dc_.calibrate(cache, [&](float d, float* buf, int n) {
            resetHi();
            gain_.snap(fuzzGain(d));
            processHi(buf, n);
        });
        resetHi();
        dc_.reset();
    }
    void reset() override {
        resetHi();
        dc_.reset();
        gain_.snap();
        tone_.snap();
        dead_.snap();
    }
    void setParam(int i, float v) override {
        const bool p = dc_.primed;
        switch (i) {
            case kFuzz: gain_.set(fuzzGain(v), p); dc_.setDrive(v); break;
            case kTone: tone_.set(0.01f * v, p); break;
            case kLevel: dc_.setLevel(v); break;
            case kBlend: dc_.setBlend(v); break;
            case kLowKeep: dc_.setLowKeep(v); break;
            case kSputter: { const float s = 0.01f * v; dead_.set(0.06f * s * s, p); break; }
            default: break;
        }
    }
    void process(float* x, int n) override {
        dc_.primed = true;
        float pk = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            pk = std::max(pk, driveBlock(dc_, x + i0, m, [this](float* h, int mm) { processHi(h, mm); }));
        }
        dc_.flushState();
        os_.flushState();
        cpl_.flushState(); lp1_.flushState(); lp2_.flushState(); tLp_.flushState(); tHp_.flushState();
        flushBlock(x, n);
        env_ = std::max(pk, env_ * 0.8f);
        activity_ = clampf(env_ * 2.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    static float fuzzGain(float pct) { return dbToLin(10.f + 0.40f * clampf(pct, 0.f, 100.f)); }
    void resetHi() { os_.reset(); cpl_.reset(); lp1_.reset(); lp2_.reset(); tLp_.reset(); tHp_.reset(); clip1_.reset(); clip2_.reset(); }
    // smooth dead zone: ~0 for |x| << d, x -/+ d for |x| >> d (no hard corners -> little aliasing)
    static inline float deadZone(float x, float d) {
        if (d < 1e-6f) return x;
        return x - d * fastTanh(x / d);
    }
    void processHi(float* hi, int m) {
        for (int i = 0; i < m; ++i) {
            const float g = gain_.next();
            const float d = dead_.next();
            // starved bias: the negative half is more starved than the positive (asymmetric sputter)
            const float s = hi[i] >= 0.f ? deadZone(hi[i], d) : deadZone(hi[i], 1.7f * d);
            float u[4];
            os_.up4(s * g, u);
            for (int k = 0; k < 4; ++k) {
                // biased (asymmetric) soft clippers with ADAA; the coupling HP removes the bias DC
                float v = clip1_.process(u[k] + 0.2f) - kB1;
                v = lp1_.lp(cpl_.hp(v));
                v = clip2_.process(10.f * v - 0.1f) - kB2;
                u[k] = lp2_.lp(v);
            }
            const float y = os_.down4(u);
            const float t = tone_.next();
            hi[i] = (1.f - t) * tLp_.lp(y) + t * tHp_.hp(y) * 0.9f;
        }
    }
    float fs_ = 48000.f;
    DriveCommon dc_;
    Oversampler os_;
    AdaaClip clip1_, clip2_;
    OnePole cpl_, lp1_, lp2_, tLp_, tHp_;
    Smoother gain_, tone_, dead_;
    float env_ = 0.f, activity_ = 0.f;
};

}  // namespace

BB_REGISTER_EFFECT({"fuzz", "Fuzz", "drive", "Big Muff-style wall of fuzz, scooped tone, optional sputter.",
                    {bb::dsp::pPct("fuzz", "Fuzz", 60.f),
                     bb::dsp::pPct("tone", "Tone", 50.f),
                     bb::dsp::pDb("level", "Level", -24.f, 12.f, 0.f),
                     bb::dsp::pPct("blend", "Blend", 100.f),
                     bb::dsp::pLog("lowkeep", "Low Keep", 40.f, 400.f, 100.f, "Hz"),
                     bb::dsp::pPct("sputter", "Sputter", 0.f)},
                    false, [] { return std::make_unique<Fuzz>(); }});
