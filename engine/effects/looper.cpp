// Looper: up to 60 s mono loop with overdub. The dry signal always passes through untouched.
// State (an enum the UI should show as buttons, not a knob):
//   Stop    : fade the loop out (10 ms) and park the playhead at the start; the loop is kept.
//   Record  : start a NEW loop (from any state). Punch-in ramps over 5 ms.
//   Play    : from Record, close the loop and play it from the top; otherwise (re)start playback.
//   Overdub : layer the input on top while playing (from Record: close the loop, then dub;
//             from Stop with no loop: same as Record).
//   Clear   : like Stop, then erase the loop once the fade-out is done (behaves as Stop with no
//             loop afterwards; Record or Overdub start a fresh one).
// The loop seam is crossfaded: after closing, 10 ms of "post-roll" input is recorded past the
// loop end and blended into the loop start, so the wrap is continuous. Recording that reaches
// 60 s closes the loop and plays it. Overdubs are soft-limited above 0 dBFS so stacking
// layers can never run away.
// Bypass (tails = false): the host stops processing the slot, so the loop falls silent but is
// KEPT. reset() (the host calls it on re-enable and after a NaN guard trip) never erases the
// loop: it stops transport and ramps, closes a take in progress (kept if long enough), and
// re-arms the current State, so re-enabling with State = Play or Overdub plays the loop
// from the top again; with Record the closed take is kept, stopped. Only Clear erases.
#include "dsp.h"

namespace {
using namespace bb;
using namespace bb::dsp;

constexpr float kMaxSec = 60.f;
constexpr float kSeamMs = 10.f;
constexpr float kRampMs = 5.f;
constexpr float kFadeMs = 10.f;
constexpr float kMinLoopSec = 0.1f;

// linear gain ramp toward 0 or 1
struct Ramp {
    float v = 0.f, target = 0.f, inc = 0.01f;
    inline float next() {
        if (v < target) v = std::min(target, v + inc);
        else if (v > target) v = std::max(target, v - inc);
        return v;
    }
};

// transparent below 1, smoothly bounded at 2
inline float softLimit(float x) {
    const float a = std::fabs(x);
    if (a <= 1.f) return x;
    const float y = 1.f + fastTanh(a - 1.f);
    return x < 0.f ? -y : y;
}

class Looper final : public Effect {
public:
    enum { kState, kLevel };
    enum { kStop, kRecord, kPlay, kOverdub, kClear };

    void prepare(float fs, int) override {
        fs_ = fs;
        cap_ = int(kMaxSec * fs);
        seam_ = std::max(16, int(0.001f * kSeamMs * fs));
        buf_.assign(size_t(cap_ + seam_ + 4), 0.f);
        recRamp_.inc = 1.f / (0.001f * kRampMs * fs);
        dub_.inc = recRamp_.inc;
        play_.inc = 1.f / (0.001f * kFadeMs * fs);
        level_.setTime(30.f, fs);
        primed_ = false;
        len_ = 0;  // a fresh buffer holds no loop
        req_ = kStop;
        reset();
    }

    // Keeps buf_ and len_ (see header). Audio thread: no allocation.
    void reset() override {
        if (recording_) closeLoop(false, false);  // keep the take (discarded only if too short)
        if (postIdx_ >= 0) postIdx_ = -1;          // no input to finish the seam with
        if (clearPending_) eraseLoop();
        pos_ = 0;
        recLen_ = 0;
        recording_ = playing_ = stopping_ = clearPending_ = false;
        recRamp_.v = recRamp_.target = 0.f;
        dub_.v = dub_.target = 0.f;
        play_.v = play_.target = 0.f;
        level_.snap();
        // re-arm: Play/Overdub restart playback at the next process(); Record stays stopped
        applied_ = (req_ == kRecord) ? kRecord : kStop;
        activity_ = 0.f;
    }

    void setParam(int i, float v) override {
        if (i == kState) req_ = int(clampf(v, 0.f, 4.f) + 0.5f);
        else if (i == kLevel) level_.set(dbToLin(clampf(v, -60.f, 12.f)), primed_);
    }

    void process(float* x, int n) override {
        primed_ = true;
        if (req_ != applied_) transition(req_);
        for (int i = 0; i < n; ++i) {
            const float in = std::isfinite(x[i]) ? x[i] : 0.f;  // a NaN upstream must not poison the loop
            if (recording_) {
                buf_[size_t(recLen_++)] = in * recRamp_.next();
                if (recLen_ >= cap_) closeLoop(true, false);  // 60 s: close and play
            }
            if (postIdx_ >= 0) {  // seam post-roll past the loop end
                buf_[size_t(len_ + postIdx_)] = in;
                if (++postIdx_ >= seam_) bakeSeam();
            }
            float out = in;
            if (playing_) {
                const float g = play_.next();
                const size_t p = size_t(pos_);
                const float y = buf_[p];
                const float d = dub_.next();
                if (d > 0.f) buf_[p] = softLimit(y + in * d);
                if (++pos_ >= len_) pos_ = 0;
                out += y * g * level_.next();
                if (stopping_ && g <= 0.f) {
                    playing_ = stopping_ = false;
                    if (clearPending_) eraseLoop();
                    pos_ = 0;
                }
            }
            x[i] = out;
        }
        if (playing_ && len_ > 0) activity_ = float(pos_) / float(len_);
        else if (recording_) activity_ = float(recLen_) / float(cap_);
        else activity_ = 0.f;
    }

    float activity() const override { return activity_; }

private:
    void eraseLoop() {
        len_ = 0;
        pos_ = 0;
        postIdx_ = -1;  // drop any pending seam post-roll too
        clearPending_ = false;
    }
    void startRecording() {
        recording_ = true;
        clearPending_ = false;  // the new take replaces the old loop anyway
        recLen_ = 0;
        recRamp_.v = 0.f;
        recRamp_.target = 1.f;
        postIdx_ = -1;  // abandon any unfinished seam of the previous loop
        dub_.target = 0.f;
        dub_.v = 0.f;
        if (playing_) { play_.target = 0.f; stopping_ = true; }  // fade the old loop out
    }
    // finish a recording; play: start playback from the top, dub: also overdub
    void closeLoop(bool play, bool dub) {
        recording_ = false;
        if (recLen_ < int(kMinLoopSec * fs_)) {  // too short to be a loop: discard
            len_ = 0;
            playing_ = stopping_ = false;
            return;
        }
        len_ = recLen_;
        postIdx_ = 0;
        pos_ = 0;
        stopping_ = false;
        playing_ = play || dub;
        play_.v = play_.target = playing_ ? 1.f : 0.f;  // the loop start already has its punch-in ramp
        dub_.v = 0.f;
        dub_.target = dub ? 1.f : 0.f;
    }
    // blend the post-roll (continuation past the loop end) into the loop start: raised cosine
    void bakeSeam() {
        for (int k = 0; k < seam_; ++k) {
            const float t = (float(k) + 0.5f) / float(seam_);
            const float w = 0.5f - 0.5f * std::cos(kPi * t);  // 0 -> 1
            const size_t a = size_t(k), b = size_t(len_ + k);
            buf_[a] = buf_[b] * (1.f - w) + buf_[a] * w;
        }
        postIdx_ = -1;
    }
    void startPlayback(bool dub) {
        if (len_ <= 0) return;
        if (!playing_ || stopping_) {
            if (!playing_) pos_ = 0;
            playing_ = true;
            stopping_ = false;
        }
        play_.target = 1.f;
        dub_.target = dub ? 1.f : 0.f;
    }
    void transition(int to) {
        applied_ = to;
        switch (to) {
            case kRecord: startRecording(); break;
            case kPlay:
                if (recording_) closeLoop(true, false);
                else startPlayback(false);
                break;
            case kOverdub:
                if (recording_) closeLoop(true, true);
                else if (len_ > 0) startPlayback(true);
                else startRecording();
                break;
            case kClear:
                clearPending_ = true;
                [[fallthrough]];
            case kStop:
            default:
                if (recording_) closeLoop(false, false);
                dub_.target = 0.f;
                if (playing_) { play_.target = 0.f; stopping_ = true; }
                if (clearPending_ && !playing_) eraseLoop();
                break;
        }
    }

    float fs_ = 48000.f;
    std::vector<float> buf_;
    int cap_ = 0, seam_ = 480;
    int len_ = 0, pos_ = 0, recLen_ = 0, postIdx_ = -1;
    bool recording_ = false, playing_ = false, stopping_ = false, clearPending_ = false;
    Ramp recRamp_, dub_, play_;
    Smoother level_;
    int req_ = kStop, applied_ = kStop;
    float activity_ = 0.f;
    bool primed_ = false;
};

}  // namespace

BB_REGISTER_EFFECT({"looper", "Looper", "utility", "Record a loop up to 60 s, play it back, overdub on top.",
                    {bb::dsp::pEnum("state", "State", {"Stop", "Record", "Play", "Overdub", "Clear"}, 0),
                     bb::dsp::pDb("level", "Level", -24.f, 6.f, 0.f)},
                    false, [] { return std::make_unique<Looper>(); }});
