// Octave Fuzz: Octavia-style. The hi band is focused (2-pole LP 1 kHz), gained, then inside a 4x
// oversampled block full-wave rectified (|x| = octave up) and blended with the straight signal
// by Octave, then clipped (cubic, ADAA). The rectifier's envelope DC is removed (HP 70 Hz), then Tone
// (2-pole LP 700 Hz..9 kHz), auto makeup, clean sub below Low Keep, Blend / Level.
#include "dsp_drive.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class OctaFuzz final : public Effect {
public:
    enum { kFuzz, kOctave, kTone, kLevel, kBlend, kLowKeep };
    void prepare(float fs, int) override {
        fs_ = fs;
        dc_.prepare(fs);
        os_.init(4);
        gain_.setTime(30.f, fs);
        oct_.setTime(30.f, fs);
        tone_.setTime(30.f, fs / kSub);
        focus_.c = rbj::lowpass(fs, 1000.0, 0.6);
        dcCut_.c = rbj::highpass(fs, 70.0, 0.7071);
        tone_.snap(std::log(toneHz(50.f)));
        oct_.snap(0.7f);
        static DriveCal cache;
        dc_.calibrate(cache, [&](float d, float* buf, int n) {
            resetHi();
            gain_.snap(fuzzGain(d));
            updateTone();
            processHi(buf, n);
        });
        resetHi();
        dc_.reset();
    }
    void reset() override {
        resetHi();
        dc_.reset();
        gain_.snap();
        oct_.snap();
        tone_.snap();
        updateTone();
    }
    void setParam(int i, float v) override {
        const bool p = dc_.primed;
        switch (i) {
            case kFuzz: gain_.set(fuzzGain(v), p); dc_.setDrive(v); break;
            case kOctave: oct_.set(0.01f * v, p); break;
            case kTone: tone_.set(std::log(toneHz(v)), p); if (!p) updateTone(); break;
            case kLevel: dc_.setLevel(v); break;
            case kBlend: dc_.setBlend(v); break;
            case kLowKeep: dc_.setLowKeep(v); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        dc_.primed = true;
        float pk = 0.f;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!tone_.settled()) { tone_.next(); updateTone(); }
            pk = std::max(pk, driveBlock(dc_, x + i0, m, [this](float* h, int mm) { processHi(h, mm); }));
        }
        dc_.flushState();
        os_.flushState();
        focus_.flushState(); dcCut_.flushState(); toneF_.flushState();
        flushBlock(x, n);
        env_ = std::max(pk, env_ * 0.8f);
        activity_ = clampf(env_ * 2.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    static float fuzzGain(float pct) { return dbToLin(6.f + 0.36f * clampf(pct, 0.f, 100.f)); }  // +6..+42 dB
    static float toneHz(float pct) { return 700.f * std::pow(13.f, clampf(pct, 0.f, 100.f) * 0.01f); }
    void updateTone() { toneF_.c = rbj::lowpass(fs_, std::exp(tone_.cur), 0.6); }
    void resetHi() { os_.reset(); focus_.reset(); dcCut_.reset(); toneF_.reset(); clip_.reset(); rect_.reset(); }
    void processHi(float* hi, int m) {
        for (int i = 0; i < m; ++i) {
            const float g = gain_.next();
            const float o = oct_.next();
            float u[4];
            os_.up4(focus_.process(hi[i]) * g, u);
            for (int k = 0; k < 4; ++k) {
                // |x| has its 2nd harmonic at 4/(3 pi) of the input amplitude: x2.4 matches levels
                const float r = 2.4f * rect_.process(u[k]);
                u[k] = clip_.process(u[k] + o * (r - u[k]));
            }
            // the straight (octave 0) signal clips ~3.5 dB louder than the rectified one: even it out
            const float oc = dbToLin(-3.6f * std::max(0.f, 0.7f - o) * (1.f / 0.7f) + 1.0f * std::max(0.f, o - 0.7f) * (1.f / 0.3f));
            float y = os_.down4(u) * oc;
            y = dcCut_.process(y);
            hi[i] = toneF_.process(y);
        }
    }
    float fs_ = 48000.f;
    DriveCommon dc_;
    Oversampler os_;
    AdaaClip clip_;
    AdaaAbs rect_;
    Biquad focus_, dcCut_, toneF_;
    Smoother gain_, oct_, tone_;
    float env_ = 0.f, activity_ = 0.f;
};

}  // namespace

BB_REGISTER_EFFECT({"octafuzz", "Octave Fuzz", "drive", "Octavia-style fuzz with a ringing upper octave.",
                    {bb::dsp::pPct("fuzz", "Fuzz", 60.f),
                     bb::dsp::pPct("octave", "Octave", 70.f),
                     bb::dsp::pPct("tone", "Tone", 50.f),
                     bb::dsp::pDb("level", "Level", -24.f, 12.f, 0.f),
                     bb::dsp::pPct("blend", "Blend", 100.f),
                     bb::dsp::pLog("lowkeep", "Low Keep", 40.f, 400.f, 150.f, "Hz")},
                    false, [] { return std::make_unique<OctaFuzz>(); }});
