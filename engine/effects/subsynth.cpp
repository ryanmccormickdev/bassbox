// Sub Synth: analog bass-synth pedal vibe. The PitchTracker (dsp_pitch.h) gives a validated
// period; a PolyBLEP oscillator (square / saw / 25% pulse) runs at f0 * 2^-octave with ~12 ms
// portamento, follows the input envelope and is gated by the tracker's voicing (ducked while a
// note change is being confirmed, so a wrong pitch never sounds at full level). It then goes
// through a resonant SVF low-pass with a little envelope tracking. Waveform switches crossfade.
// Sub tones that would land below 20 Hz fold up an octave (they would be inaudible).
#include "dsp.h"
#include "dsp_filter.h"
#include "dsp_pitch.h"

namespace {
using namespace bb;
using namespace bb::dsp;

class SubSynth final : public Effect {
public:
    enum { kSynth, kFilter, kDry, kWave, kOctave, kReso };
    void prepare(float fs, int) override {
        fs_ = fs;
        tr_.init(fs);
        synth_.setTime(20.f, fs);
        dry_.setTime(20.f, fs);
        for (auto& w : wave_) w.setTime(8.f, fs);
        filt_.setTime(30.f, fs);
        reso_.setTime(30.f, fs);
        glide_ = onePoleCoef(12.f, fs);
        envCoef_ = onePoleCoef(6.f, fs);
        gateAtk_ = onePoleCoef(3.f, fs);
        gateRel_ = onePoleCoef(18.f, fs);
        hp_.c = rbj::highpass(fs, 18.0, 0.70710678);
        primed_ = false;
        reset();
    }
    void reset() override {
        tr_.reset();
        synth_.snap(); dry_.snap(); filt_.snap(); reso_.snap();
        for (auto& w : wave_) w.snap();
        lp_.reset();
        hp_.reset();
        phase_ = 0.f;
        fLog_ = std::log(55.f);
        env_ = gate_ = 0.f;
        silent_ = 1 << 20;
        fold_ = false;
        level_ = activity_ = 0.f;
    }
    void setParam(int i, float v) override {
        switch (i) {
            case kSynth: synth_.set(0.01f * v, primed_); break;
            case kFilter: filt_.set(std::log(v), primed_); break;
            case kDry: dry_.set(0.01f * v, primed_); break;
            case kWave: {
                const int w = clampi(int(v + 0.5f), 0, 2);
                for (int k = 0; k < 3; ++k) wave_[k].set(k == w ? 1.f : 0.f, primed_);
                break;
            }
            case kOctave: oct_ = clampi(int(v + 0.5f), 0, 2); break;
            case kReso: reso_.set(0.01f * v, primed_); break;
            default: break;
        }
    }
    void process(float* x, int n) override {
        primed_ = true;
        float pk = 0.f;
        for (int i = 0; i < n; ++i) {
            const float in = x[i];
            tr_.tick(in);
            // pitch: target from the validated period, octave-shifted, folded up if < 20 Hz
            const float on = tr_.voiced ? (tr_.stable ? 1.f : 0.3f) : 0.f;
            if (tr_.voiced) {
                float f = tr_.hz() * kOctMul[oct_];
                if (fold_) { if (f > 20.5f) fold_ = false; }
                else if (f < 19.5f) fold_ = true;
                if (fold_) f *= 2.f;
                const float tl = std::log(f);
                if (silent_ > int(0.05f * fs_)) fLog_ = tl;  // new phrase: no glide from the old note
                else if (tr_.stable) fLog_ += glide_ * (tl - fLog_);
                silent_ = 0;
            } else if (silent_ < (1 << 20)) {
                ++silent_;
            }
            gate_ += (on > gate_ ? gateAtk_ : gateRel_) * (on - gate_);
            env_ += envCoef_ * (tr_.envIn - env_);
            // oscillator
            const float dt = std::min(0.45f, std::exp(fLog_) / fs_);
            phase_ += dt;
            if (phase_ >= 1.f) phase_ -= 1.f;
            const float p = phase_;
            const float saw = 2.f * p - 1.f - polyBlep(p, dt);
            float p2 = p + 0.5f; if (p2 >= 1.f) p2 -= 1.f;
            const float sq = (p < 0.5f ? 1.f : -1.f) + polyBlep(p, dt) - polyBlep(p2, dt);
            float p4 = p + 0.75f; if (p4 >= 1.f) p4 -= 1.f;
            const float pu = (p < 0.25f ? 1.f : -1.f) + polyBlep(p, dt) - polyBlep(p4, dt) + 0.5f;
            const float osc = wave_[0].next() * sq + wave_[1].next() * saw * 1.75f + wave_[2].next() * pu * 1.2f;
            const float e = env_ * gate_;
            // filter: base cutoff * up to +1.5 octaves of envelope tracking
            const float et = clampf(env_ * 3.f, 0.f, 1.f);
            const float fc = std::exp(filt_.next()) * fastExp2(1.5f * et);
            const float r = reso_.next();
            const float q = 0.6f * std::exp(r * 2.8f);  // 0.6 .. ~10
            lp_.setG(fastG(fc, fs_), 1.f / q);
            lp_.tick(osc * e * kVoice);
            // resonance can peak hard: soft-limit the voice, and keep infrasonics out
            const float v = hp_.process(fastTanh(lp_.lp * (1.f / (1.f + 0.15f * q))) );
            const float y = dry_.next() * in + synth_.next() * v;
            x[i] = y;
            pk = std::max(pk, std::fabs(v));
        }
        tr_.flushState();
        lp_.flushState();
        hp_.flushState();
        env_ = flush(env_);
        gate_ = flush(gate_);
        flushBlock(x, n);
        level_ = std::max(pk, level_ * 0.9f);
        activity_ = clampf(level_ * 2.f, 0.f, 1.f);
    }
    float activity() const override { return activity_; }

private:
    static int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }
    static constexpr float kOctMul[3] = {1.f, 0.5f, 0.25f};
    static constexpr float kVoice = 0.8f;
    float fs_ = 48000.f;
    PitchTracker tr_;
    Smoother synth_, dry_, filt_, reso_, wave_[3];
    Svf lp_;
    Biquad hp_;
    int oct_ = 1;
    float phase_ = 0.f, fLog_ = 4.f, glide_ = 0.f;
    float env_ = 0.f, envCoef_ = 0.f, gate_ = 0.f, gateAtk_ = 0.f, gateRel_ = 0.f;
    int silent_ = 0;
    bool fold_ = false;
    float level_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"subsynth", "Sub Synth", "pitch", "Tracks your notes and adds a fat square, saw or pulse sub.",
                    {bb::dsp::pPct("synth", "Synth", 80.f),
                     bb::dsp::pLog("filter", "Filter", 60.f, 4000.f, 500.f, "Hz"),
                     bb::dsp::pPct("dry", "Dry", 50.f),
                     bb::dsp::pEnum("wave", "Wave", {"Square", "Saw", "Pulse"}, 0),
                     bb::dsp::pEnum("octave", "Octave", {"0", "-1", "-2"}, 1),
                     bb::dsp::pPct("reso", "Reso", 30.f)},
                    false, [] { return std::make_unique<SubSynth>(); }});
