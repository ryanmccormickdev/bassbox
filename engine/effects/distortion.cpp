// Distortion: RAT / Darkglass-style hard clipper for bass.
//   hi band -> RAT-like gain stage (extra gain above 1.5 kHz for clank, gain-bandwidth limited
//   low-pass so max drive gets smoother, not fizzier) -> 4x oversampled cubic clip with ADAA ->
//   Mid peaking (+-12 dB @ 600 Hz) -> RAT "filter" tone (1-pole 475 Hz..16 kHz) + fixed 9 kHz
//   2-pole anti-fizz -> auto makeup -> clean sub below Low Keep added back -> Blend / Level.
#include "dsp_drive.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Distortion final : public Effect {
public:
    enum { kDrive, kBlend, kTone, kMid, kLevel, kLowKeep };
    void prepare(float fs, int) override {
        fs_ = fs;
        dc_.prepare(fs);
        os_.init(4);
        gain_.setTime(30.f, fs);
        gbw_.setTime(30.f, fs / kSub);
        tone_.setTime(30.f, fs / kSub);
        mid_.setTime(30.f, fs / kSub);
        emph_.set(fs, 1500.f);
        fizz_.c = rbj::lowpass(fs, 9000.0, 0.7071);
        tone_.snap(std::log(toneHz(50.f)));
        mid_.snap(0.f);
        static DriveCal cache;
        dc_.calibrate(cache, [&](float d, float* buf, int n) {
            resetHi();
            gain_.snap(driveGain(d));
            gbw_.snap(gbwHz(driveGain(d)));
            updateFilters();
            processHi(buf, n);
        });
        resetHi();
        dc_.reset();
    }
    void reset() override {
        resetHi();
        dc_.reset();
        gain_.snap();
        gbw_.snap();
        tone_.snap();
        mid_.snap();
        updateFilters();
    }
    void setParam(int i, float v) override {
        const bool p = dc_.primed;
        switch (i) {
            case kDrive:
                gain_.set(driveGain(v), p);
                gbw_.set(gbwHz(driveGain(v)), p);
                dc_.setDrive(v);
                break;
            case kBlend: dc_.setBlend(v); break;
            case kTone: tone_.set(std::log(toneHz(v)), p); break;
            case kMid: mid_.set(v, p); break;
            case kLevel: dc_.setLevel(v); break;
            case kLowKeep: dc_.setLowKeep(v); break;
            default: break;
        }
        if (!p) updateFilters();
    }
    void process(float* x, int n) override {
        dc_.primed = true;
        float pk = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!tone_.settled() || !mid_.settled() || !gbw_.settled()) {
                tone_.next(); mid_.next(); gbw_.next();
                updateFilters();
            }
            pk = std::max(pk, driveBlock(dc_, x + i0, m, [this](float* h, int mm) { processHi(h, mm); }));
        }
        dc_.flushState();
        os_.flushState();
        emph_.flushState();
        gbwF_.flushState();
        midF_.flushState();
        toneF_.flushState();
        fizz_.flushState();
        flushBlock(x, n);
        env_ = std::max(pk, env_ * 0.8f);
        activity_ = clampf(env_ * 2.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    static float driveGain(float pct) { return dbToLin(12.f + 0.48f * clampf(pct, 0.f, 100.f)); }  // +12..+60 dB
    static float gbwHz(float g) { return clampf(4.0e6f / g, 2500.f, 20000.f); }
    static float toneHz(float pct) { return 475.f * std::pow(34.f, clampf(pct, 0.f, 100.f) * 0.01f); }
    void updateFilters() {
        gbwF_.set(fs_ * 4.f, gbw_.cur);
        midF_.c = rbj::peaking(fs_, 600.0, 0.8, mid_.cur);
        toneF_.c = rbj::lowpass1(fs_, std::exp(tone_.cur));
    }
    void resetHi() {
        os_.reset(); emph_.reset(); gbwF_.reset(); midF_.reset(); toneF_.reset(); fizz_.reset(); clip_.reset();
    }
    void processHi(float* hi, int m) {
        for (int i = 0; i < m; ++i) {
            const float g = gain_.next();
            // RAT pre-emphasis: +12 dB shelf above ~1.5 kHz before the clipper
            const float e = hi[i] + 3.f * emph_.hp(hi[i]);
            float u[4];
            os_.up4(e * g, u);
            for (int k = 0; k < 4; ++k) u[k] = clip_.process(gbwF_.lp(u[k]));
            float y = os_.down4(u);
            y = midF_.process(y);
            y = toneF_.process(y);
            hi[i] = fizz_.process(y);
        }
    }
    float fs_ = 48000.f;
    DriveCommon dc_;
    Oversampler os_;
    AdaaClip clip_;
    OnePole emph_, gbwF_;
    Biquad midF_, toneF_, fizz_;
    Smoother gain_, gbw_, tone_, mid_;
    float env_ = 0.f, activity_ = 0.f;
};

}  // namespace

BB_REGISTER_EFFECT({"distortion", "Distortion", "drive", "RAT/Darkglass-style grind over a clean sub.",
                    {bb::dsp::pPct("drive", "Drive", 50.f),
                     bb::dsp::pPct("blend", "Blend", 100.f),
                     bb::dsp::pPct("tone", "Tone", 50.f),
                     bb::dsp::pDb("mid", "Mid", -12.f, 12.f, 0.f),
                     bb::dsp::pDb("level", "Level", -24.f, 12.f, 0.f),
                     bb::dsp::pLog("lowkeep", "Low Keep", 40.f, 400.f, 150.f, "Hz")},
                    false, [] { return std::make_unique<Distortion>(); }});
