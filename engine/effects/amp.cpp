// Amp: tube bass preamp + Ampeg-style active tone stack + optional built-in cab voicing.
//   in -> HP 35 Hz -> SVT-ish pre-voicing (+2 dB @ 60 Hz, -2 dB @ 450 Hz, bright +2 dB @ 2.5 kHz)
//   -> 4x oversampled { stage 1: gain -> asymmetric triode -> coupling HP 25 Hz / Miller LP 10 kHz
//      -> stage 2 (inverting, so the asymmetry alternates like real cascaded triodes) -> LP 12 kHz }
//   -> tone stack (Bass shelf 80 Hz, Mid peak 500 Hz, Treble shelf 3 kHz, +-15 dB)
//   -> auto makeup (calibrated over Gain) -> gentle power-stage saturation -> 8x10 cab (Cab on)
//   -> Level.
#include "dsp_cab.h"
#include "dsp_drive.h"

namespace {
using namespace bb;
using namespace bb::dsp;

struct AmpCal {
    float fs = 0.f;
    float table[9] = {};
};

class Amp final : public Effect {
public:
    enum { kGain, kBass, kMid, kTreble, kLevel, kCab };
    void prepare(float fs, int) override {
        fs_ = fs;
        os_.init(4);
        const float f4 = fs * 4.f;
        pre_[0].c = rbj::highpass(fs, 35.0, 0.7071);
        pre_[1].c = rbj::lowShelf(fs, 60.0, 2.0, 0.7);
        pre_[2].c = rbj::peaking(fs, 450.0, 0.7, -2.0);
        pre_[3].c = rbj::highShelf(fs, 2500.0, 2.0, 0.7);
        cpl_.set(f4, 25.f);
        mil_.set(f4, 10000.f);
        lp2_.set(f4, 12000.f);
        dcb_.set(fs, 8.f);
        g1_.setTime(30.f, fs);
        g2_.setTime(30.f, fs);
        makeup_.setTime(40.f, fs);
        level_.setTime(20.f, fs);
        cabMix_.setTime(30.f, fs);
        for (auto& s : tone_) s.setTime(25.f, fs / kSub);
        cab_.prepare(fs);
        cab_.setType(2);  // 8x10
        cab_.setMic(30.f);
        // calibrate makeup over Gain with a flat tone stack (cached per process)
        static AmpCal cache;
        if (cache.fs != fs) {
            const int n = int(fs * 0.35f), from = int(fs * (0.35f - 1.f / 6.f));
            std::vector<float> sig, buf;
            bassCalSignal(sig, fs, n);
            double in = 0;
            for (int i = from; i < n; ++i) in += double(sig[size_t(i)]) * sig[size_t(i)];
            for (int k = 0; k < 9; ++k) {
                buf = sig;
                resetPath();
                g1_.snap(gain1(12.5f * float(k)));
                g2_.snap(gain2(12.5f * float(k)));
                for (auto& s : tone_) s.snap(0.f);
                updateTone();
                for (int i = 0; i < n; ++i) buf[size_t(i)] = preamp(buf[size_t(i)]);
                double out = 0;
                for (int i = from; i < n; ++i) out += double(buf[size_t(i)]) * buf[size_t(i)];
                cache.table[k] = clampf(float(10.0 * std::log10((in + 1e-20) / (out + 1e-20))), -60.f, 40.f);
            }
            cache.fs = fs;
        }
        cal_ = cache;
        primed_ = false;
        reset();
    }
    void reset() override {
        resetPath();
        cab_.reset();
        g1_.snap(); g2_.snap(); makeup_.snap(); level_.snap(); cabMix_.snap();
        for (auto& s : tone_) s.snap();
        updateTone();
    }
    void setParam(int i, float v) override {
        cab_.primed = primed_;
        switch (i) {
            case kGain: {
                g1_.set(gain1(v), primed_);
                g2_.set(gain2(v), primed_);
                const float p = clampf(v, 0.f, 100.f) * 0.08f;
                const int k = std::min(7, int(p));
                makeup_.set(dbToLin(cal_.table[k] + (p - float(k)) * (cal_.table[k + 1] - cal_.table[k])), primed_);
                break;
            }
            case kBass: case kMid: case kTreble:
                tone_[i - kBass].set(v, primed_);
                if (!primed_) updateTone();
                break;
            case kLevel: level_.set(dbToLin(v), primed_); break;
            case kCab: cabMix_.set(v >= 0.5f ? 1.f : 0.f, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        cab_.primed = true;
        float env = env_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            bool dirty = false;
            for (auto& s : tone_) if (!s.settled()) { s.next(); dirty = true; }
            if (dirty) updateTone();
            cab_.tick();
            for (int i = i0; i < i0 + m; ++i) {
                float y = preamp(x[i]) * makeup_.next();
                // power stage: transparent below ~-6 dBFS, rounds off big peaks
                y = 1.4f * fastTanh(y * (1.f / 1.4f));
                const float c = cabMix_.next();
                const float cy = cab_.process(y);  // always run so toggling Cab never starts cold
                y = y + c * (cy - y);
                y *= level_.next();
                x[i] = y;
                env += 0.002f * (std::fabs(y) - env);
            }
        }
        for (auto& b : pre_) b.flushState();
        for (auto& b : stack_) b.flushState();
        os_.flushState(); cpl_.flushState(); mil_.flushState(); lp2_.flushState(); dcb_.flushState();
        cab_.flushState();
        env_ = flush(env);
        flushBlock(x, n);
        activity_ = clampf(env_ * 4.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    static float gain1(float pct) { return dbToLin(-4.f + 0.34f * clampf(pct, 0.f, 100.f)); }  // -4..+30 dB
    static float gain2(float pct) { return dbToLin(0.18f * clampf(pct, 0.f, 100.f)); }        //  0..+18 dB
    void updateTone() {
        stack_[0].c = rbj::lowShelf(fs_, 80.0, tone_[0].cur, 0.7);
        stack_[1].c = rbj::peaking(fs_, 500.0, 0.8, tone_[1].cur);
        stack_[2].c = rbj::highShelf(fs_, 3000.0, tone_[2].cur, 0.7);
    }
    void resetPath() {
        for (auto& b : pre_) b.reset();
        for (auto& b : stack_) b.reset();
        os_.reset(); cpl_.reset(); mil_.reset(); lp2_.reset(); dcb_.reset();
    }
    inline float preamp(float x) {
        float v = x;
        for (auto& b : pre_) v = b.process(v);
        const float a = g1_.next(), b2 = g2_.next();
        float u[4];
        os_.up4(v, u);
        for (int k = 0; k < 4; ++k) {
            float s = shapeTube(a * u[k]);
            s = mil_.lp(cpl_.hp(s));
            s = -shapeTube(-b2 * s);  // inverting stage
            u[k] = lp2_.lp(s);
        }
        float y = dcb_.process(os_.down4(u));
        for (auto& b : stack_) y = b.process(y);
        return y;
    }
    float fs_ = 48000.f;
    Oversampler os_;
    Biquad pre_[4], stack_[3];
    OnePole cpl_, mil_, lp2_;
    DcBlock dcb_;
    Smoother g1_, g2_, makeup_, level_, cabMix_, tone_[3];
    CabSim cab_;
    AmpCal cal_;
    float env_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"amp", "Amp", "amp", "Tube bass preamp, Ampeg-style EQ, optional 8x10 cab.",
                    {bb::dsp::pPct("gain", "Gain", 30.f),
                     bb::dsp::pDb("bass", "Bass", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("mid", "Mid", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("treble", "Treble", -15.f, 15.f, 0.f),
                     bb::dsp::pDb("level", "Level", -24.f, 12.f, 0.f),
                     bb::dsp::pBool("cab", "Cab", true)},
                    false, [] { return std::make_unique<Amp>(); }});
