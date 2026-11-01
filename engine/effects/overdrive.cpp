// Overdrive: tube-screamer topology, re-voiced for bass.
//   hi band (above Low Keep) -> 4x oversampled { y = x + diode(g * HP1_350Hz(x)) }   (TS: the clean
//   part always passes, only the gained mids clip, so dynamics and note definition survive)
//   -> 2-pole tone low-pass (600 Hz..9 kHz) -> auto makeup -> lo band added back clean -> Blend/Level.
// The TS corner (720 Hz) is moved to 350 Hz so low mids get grit too; the sub below Low Keep
// never touches the clipper.
#include "dsp_drive.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class Overdrive final : public Effect {
public:
    enum { kDrive, kTone, kLevel, kBlend, kLowKeep };
    void prepare(float fs, int) override {
        fs_ = fs;
        dc_.prepare(fs);
        os_.init(4);
        gain_.setTime(30.f, fs);
        tone_.setTime(30.f, fs / kSub);
        hp4_.set(fs * 4.f, 350.f);
        lp4_.set(fs * 4.f, 7000.f);  // op-amp/51 pF style roll-off inside the clipping loop
        // calibrate makeup with tone at noon
        tone_.snap(std::log(toneHz(50.f)));
        static DriveCal cache;
        dc_.calibrate(cache, [&](float d, float* buf, int n) {
            resetHi();
            gain_.snap(driveGain(d));
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
        tone_.snap();
        updateTone();
    }
    void setParam(int i, float v) override {
        const bool p = dc_.primed;
        switch (i) {
            case kDrive: gain_.set(driveGain(v), p); dc_.setDrive(v); break;
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
        hp4_.flushState();
        lp4_.flushState();
        toneF_.flushState();
        flushBlock(x, n);
        env_ = std::max(pk, env_ * 0.8f);
        activity_ = clampf(env_ * 2.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    static float driveGain(float pct) { return dbToLin(6.f + 0.40f * clampf(pct, 0.f, 100.f)); }  // +6..+46 dB
    static float toneHz(float pct) { return 600.f * std::pow(15.f, clampf(pct, 0.f, 100.f) * 0.01f); }
    void updateTone() { toneF_.c = rbj::lowpass(fs_, std::exp(tone_.cur), 0.6); }
    void resetHi() { os_.reset(); hp4_.reset(); lp4_.reset(); toneF_.reset(); }
    void processHi(float* hi, int m) {
        for (int i = 0; i < m; ++i) {
            const float g = gain_.next();
            float u[4];
            os_.up4(hi[i], u);
            for (int k = 0; k < 4; ++k) {
                const float c = shapeDiodeSoft(lp4_.lp(g * hp4_.hp(u[k])) * 1.6f) * 0.6f;
                u[k] = u[k] + c;
            }
            hi[i] = toneF_.process(os_.down4(u));
        }
    }
    float fs_ = 48000.f;
    DriveCommon dc_;
    Oversampler os_;
    OnePole hp4_, lp4_;
    Biquad toneF_;
    Smoother gain_, tone_;
    float env_ = 0.f, activity_ = 0.f;
};

}  // namespace

BB_REGISTER_EFFECT({"overdrive", "Overdrive", "drive", "Warm tube-screamer push; the sub stays clean.",
                    {bb::dsp::pPct("drive", "Drive", 50.f),
                     bb::dsp::pPct("tone", "Tone", 50.f),
                     bb::dsp::pDb("level", "Level", -24.f, 12.f, 0.f),
                     bb::dsp::pPct("blend", "Blend", 100.f),
                     bb::dsp::pLog("lowkeep", "Low Keep", 40.f, 400.f, 120.f, "Hz")},
                    false, [] { return std::make_unique<Overdrive>(); }});
