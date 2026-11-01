// Tape Echo: Space-Echo-flavoured tape delay, up to 1 s.
//  * Time glides like a motor speed change (critically damped, soft speed limit 0.5x..1.5x),
//    so moves bend the pitch of the repeats instead of clicking.
//  * Wow (~0.6 Hz sine + smoothed random drift) and flutter (~7 Hz + 11 Hz), both scaled by Wow.
//  * Record-head saturation (tanh, unity small-signal gain) then tape/playback losses
//    (100 Hz high-pass + Tone-controlled low-pass) inside the loop: each repeat is warmer,
//    darker and thinner. Fdbk 100% = unity small-signal loop gain: repeats sustain (saturation
//    bounds loud ones) but never grow.
//  * Sat also warms the wet path itself (it is the record head the first repeat goes through).
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr float kMaxMs = 1000.f;
constexpr float kWowMs = 2.5f;      // peak wow excursion at Wow = 100%
constexpr float kFlutterMs = 0.1f;  // peak flutter excursion at Wow = 100%

class Tape final : public Effect {
public:
    enum { kTime, kFdbk, kMix, kWow, kTone, kSat };

    void prepare(float fs, int) override {
        fs_ = fs;
        offs_ = 0.001f * (kWowMs + kFlutterMs) * fs + 2.f;
        line_.init(int(0.001f * kMaxMs * fs + 2.f * offs_) + 16);
        fb_.setTime(40.f, fs);
        mix_.setTime(30.f, fs);
        wow_.setTime(100.f, fs);
        sat_.setTime(40.f, fs);
        tone_.setTime(40.f, fs / kSub);
        // motor model: critically damped 2nd-order glide (tau 100 ms) with a soft speed limit
        motorTau_ = 0.1f * fs;
        motorB_ = 4.f / motorTau_;
        slew_ = 0.5f;  // max change of delay per sample -> read speed 0.5x .. 1.5x
        hp_.c = rbj::highpass(fs, 100.0, 0.6);
        wowLfo_.setRate(0.61f, fs);
        flLfo1_.setRate(7.1f, fs);
        flLfo2_.setRate(11.3f, fs);
        driftA_ = onePoleCoef(1000.f / (kTwoPi * 0.9f), fs);  // ~0.9 Hz smoothing of the random drift
        envRel_ = std::exp(-1.f / (0.25f * fs));
        primed_ = false;
        reset();
    }

    void reset() override {
        line_.clear();
        lp_.reset();
        hp_.reset();
        cur_ = target_;
        vel_ = 0.f;
        fb_.snap();
        mix_.snap();
        wow_.snap();
        sat_.snap();
        tone_.snap();
        drift1_ = drift2_ = driftTgt_ = 0.f;
        driftCnt_ = 0;
        env_ = 0.f;
        activity_ = 0.f;
        update();
    }

    void setParam(int i, float v) override {
        switch (i) {
            case kTime:
                target_ = clampf(v, 40.f, kMaxMs) * 0.001f * fs_;
                if (!primed_) { cur_ = target_; vel_ = 0.f; }
                break;
            case kFdbk: fb_.set(clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kMix: mix_.set(clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kWow: wow_.set(clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kTone: tone_.set(clampf(v, 0.f, 100.f) * 0.01f, primed_); break;
            case kSat: {
                // tanh drive: 0.4 (clean, clips at 2.5) .. 2.6 (clips at 0.38)
                sat_.set(0.4f + 2.2f * clampf(v, 0.f, 100.f) * 0.01f, primed_);
                break;
            }
            default: break;
        }
        if (!primed_) update();
    }

    void process(float* x, int n) override {
        primed_ = true;
        const float wowS = 0.001f * kWowMs * fs_, flS = 0.001f * kFlutterMs * fs_;
        float env = env_;
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!tone_.settled()) { tone_.next(); update(); }
            for (int i = i0; i < i0 + m; ++i) {
                const float in = x[i];
                // motor: commanded speed change soft-limited, actual speed follows it smoothly,
                // so the pitch bends without corners at the start or the end of a move
                const float vd = slew_ * fastTanh((target_ - cur_) / (motorTau_ * slew_));
                vel_ += motorB_ * (vd - vel_);
                cur_ += vel_;
                // wow + flutter
                // random drift: a new random target every ~0.35 s, smoothed by two ~0.9 Hz poles
                if (--driftCnt_ <= 0) {
                    driftTgt_ = rng_.next();
                    driftCnt_ = int(0.35f * fs_);
                }
                drift1_ += driftA_ * (driftTgt_ - drift1_);
                drift2_ += driftA_ * (drift1_ - drift2_);
                const float w = wow_.next();
                const float wowSig = 0.65f * fastSin2Pi(wowLfo_.tick()) + 0.35f * drift2_;
                const float flSig = 0.75f * fastSin2Pi(flLfo1_.tick()) + 0.25f * fastSin2Pi(flLfo2_.tick());
                const float d = cur_ + w * (wowS * wowSig + flS * flSig);  // centred on the set time
                const float y = line_.readHermite(d);
                // record head: saturation with unity small-signal gain, then tape losses
                const float k = sat_.next();
                const float s = in + fb_.next() * y;
                const float rec = fastTanh(k * s) / k;
                const float t = lp_.process(hp_.process(rec));
                line_.push(flush(t));
                x[i] = in + mix_.next() * y;
                const float a = std::fabs(y);
                env = a > env ? a : env * envRel_;
            }
        }
        lp_.flushState();
        hp_.flushState();
        drift1_ = flush(drift1_);
        drift2_ = flush(drift2_);
        env_ = flush(env);
        flushBlock(x, n);
        activity_ = clampf(env_ * 2.f, 0.f, 1.f);
    }

    float activity() const override { return activity_; }

private:
    void update() {
        // Tone 0..1 -> loop low-pass 1.2 kHz .. 9 kHz (no resonance)
        const double fc = 1200.0 * std::pow(9000.0 / 1200.0, double(tone_.cur));
        lp_.c = rbj::lowpass(fs_, fc, 0.6);
    }

    float fs_ = 48000.f;
    DelayLine line_;
    Biquad lp_, hp_;
    Smoother fb_, mix_, wow_, sat_, tone_;
    Lfo wowLfo_, flLfo1_, flLfo2_;
    Rng rng_;
    float drift1_ = 0.f, drift2_ = 0.f, driftA_ = 0.f, driftTgt_ = 0.f;
    int driftCnt_ = 0;
    float target_ = 300.f * 48.f, cur_ = 300.f * 48.f, vel_ = 0.f, slew_ = 0.5f, offs_ = 130.f;
    float motorTau_ = 4800.f, motorB_ = 1.f / 1200.f;
    float env_ = 0.f, envRel_ = 0.9999f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"tape", "Tape Echo", "time", "Tape echo with wow, flutter and dark, saturating repeats.",
                    {bb::dsp::pLog("time", "Time", 40.f, 1000.f, 300.f, "ms"),
                     bb::dsp::pPct("fdbk", "Fdbk", 40.f),
                     bb::dsp::pPct("mix", "Mix", 30.f),
                     bb::dsp::pPct("wow", "Wow", 30.f),
                     bb::dsp::pPct("tone", "Tone", 50.f),
                     bb::dsp::pPct("sat", "Sat", 30.f)},
                    true, [] { return std::make_unique<Tape>(); }});
