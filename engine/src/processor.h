// bassbox engine: the audio-side processing core (shared by bassbox-engine and bb-render).
//
// Thread ownership
// ----------------
//   CONTROL thread (Host): creates Slots and Chains (all allocation happens there), writes the
//     atomic targets in Slot/MasterControls, publishes a Chain with publish(), frees Chains it
//     gets back from takeRetired(), and frees a Slot once no live Chain references it.
//   AUDIO thread: calls process() once per period. It never allocates, locks, logs or makes a
//     syscall; it only reads atomics, runs the effects, and hands old Chains back through a
//     wait-free SPSC ring.
//
// Chain edits never click:
//   * A Chain is an immutable list of Slot pointers. A Slot (one effect instance) survives an
//     edit when its uid does, so delay lines/reverbs keep their state.
//   * Every slot has a "presence" gain p (0 = the slot is an exact identity). An edit A -> B
//     ("morph"): slots of A that are not in the longest common subsequence of A and B (removed
//     or moved ones) first ramp p to 0 over ~10 ms while A is still running; then the audio
//     thread swaps to B, where new and moved slots start at p = 0 and ramp to 1. Identity slots
//     can be inserted or dropped anywhere without changing the output, so the swap is seamless.
//   * If A and B share no slots (load_preset), both run in parallel for ~10 ms and the outputs
//     are crossfaded.
// Per-slot enable and dry/wet mix are host-side ramps (~10 ms). A disabled tails=true effect
// keeps running on silence and its output is added until it decays (or ~5 s), then fades out.
#pragma once

#include <atomic>
#include <cstdint>
#include <memory>
#include <vector>

#include "effect.h"
#include "rt.h"

namespace bb {

constexpr int kMaxBlock = 256;   // effects are prepared for blocks up to this size
constexpr int kMaxSlots = 16;    // longest chain the host accepts
constexpr int kMaxParams = 16;   // params per effect the host supports

// One effect instance in the chain.
struct Slot {
    Slot(uint32_t uid, const EffectInfo* info, std::unique_ptr<Effect> fx);
    Slot(const Slot&) = delete;
    Slot& operator=(const Slot&) = delete;

    const uint32_t uid;
    const EffectInfo* const info;
    const std::unique_ptr<Effect> fx;
    const int nparams;
    const bool tails;

    // ---- control -> audio. Control stores values (already clamped), then bumps paramSeq with
    // release order; audio re-reads the params only when paramSeq moved. Coalesces bursts
    // (60 Hz drags) and never loses the latest value, even for a slot whose Chain hasn't been
    // swapped in yet.
    std::atomic<float> param[kMaxParams];
    std::atomic<uint32_t> paramSeq{0};
    std::atomic<float> enabled{1.f};  // target, 0 or 1
    std::atomic<float> mix{1.f};      // target, 0..1

    // ---- audio-thread state. The control thread initialises it before the Slot is first
    // published (the release/acquire on the chain mailbox makes it visible), then never
    // touches it again.
    float p = 1.f, pTarget = 1.f;  // presence ramp (structural fades)
    float e = 1.f, m = 1.f;        // ramped enable / mix
    float applied[kMaxParams];     // last value passed to setParam
    uint32_t seenSeq = 0;
    bool running = true;           // effect is being processed (false = idle, saves CPU)
    float tf = 1.f, tfTarget = 1.f;  // tail fade (tails effects, disabled)
    int32_t tailAge = 0, tailQuiet = 0;

    // ---- control-thread bookkeeping
    bool published = false;
};

// Immutable once published. The plan fields describe the transition from the previously
// published chain.
struct Chain {
    std::vector<Slot*> slots;         // processing order
    std::vector<Slot*> fadeOutFirst;  // slots of the previous chain to ramp to identity first
    bool parallel = false;            // no shared slots with the previous chain: crossfade
    uint64_t serial = 0;
};

// Global controls written by the control thread, read by the audio thread every block.
struct MasterControls {
    AtomicF inGain{1.f};    // linear
    AtomicF outGain{1.f};   // linear
    std::atomic<bool> bypass{false};
    std::atomic<bool> tunerOn{false};    // mutes the output and feeds the tuner ring
    std::atomic<bool> inputMute{false};  // input jack unplugged
    std::atomic<bool> hardMute{false};   // shutdown / tests
    std::atomic<bool> dcBlock{true};
    std::atomic<bool> limiter{true};
    // Test tone (Settings -> Audio check): a sine summed in after out gain and mute (so it
    // plays with the IN jack unplugged), before the limiter. Absolute level.
    std::atomic<bool> toneOn{false};
    AtomicF toneHz{110.f};
    AtomicF toneAmp{0.125893f};  // linear peak (-18 dBFS)
};

// Written by the audio thread only (single writer), read by the control thread at any time.
struct Meters {
    AtomicF inPeak, inRms, outPeak, outRms;  // linear, decaying (~300 ms)
    std::atomic<uint64_t> frames{0};         // frames processed
    std::atomic<uint64_t> clipInAt{0};       // frames value at the last clip + 1 (0 = never)
    std::atomic<uint64_t> clipOutAt{0};
    std::atomic<uint32_t> nanResets{0};      // blocks discarded because the chain produced NaN/Inf
    std::atomic<uint32_t> transitions{0};    // chain swaps completed
    std::atomic<uint64_t> activeSerial{0};   // serial of the chain currently processed
    AtomicF muteGain{0.f};                   // current output mute ramp (1 = open)
};

class Processor {
public:
    Processor(float sampleRate, int maxBlock, std::size_t tunerRingFrames = 1 << 15);
    ~Processor();

    // ---------------- control thread
    // Hand a new chain to the audio thread. Returns false (and keeps ownership) while the
    // previous publication hasn't been picked up; the Host then retries on its next tick.
    bool publish(Chain* c);
    bool mailboxEmpty() const { return mailbox_.load(std::memory_order_acquire) == nullptr; }
    Chain* takeRetired();  // a chain the audio thread no longer uses, or nullptr
    SpscRing<float>& tunerRing() { return tuner_; }
    float sampleRate() const { return sr_; }
    int maxBlock() const { return maxBlock_; }
    // Output stays muted for `seconds` after start (ADC high-pass settling), then fades in.
    void setStartupMute(float seconds);

    MasterControls ctl;
    Meters meters;

    // ---------------- audio thread
    // in and out may alias. Any n is accepted (split into maxBlock pieces).
    void process(const float* in, float* out, int n);
    // Call after an xrun/restart: the output fades back in instead of jumping.
    void discontinuity();

private:
    void processBlock(const float* in, float* out, int n);
    void handleMailbox();
    void swapToNext();
    void retire(Chain* c);
    void flushRetire();
    void runChain(const Chain& c, float* x, int n);
    void runSlot(Slot& s, float* x, int n);
    void resetAll();

    const float sr_;
    const int maxBlock_;
    std::vector<float> x_, dry_, alt_, scratch_;  // pre-allocated block buffers

    // chain state (audio thread)
    std::atomic<Chain*> mailbox_{nullptr};
    Chain* cur_ = nullptr;
    Chain* next_ = nullptr;
    enum class Phase { None, FadeOut, Parallel } phase_ = Phase::None;
    int xfPos_ = 0;
    SpscRing<Chain*> retired_;
    Chain* retireBacklog_[8] = {};
    int retireBacklogN_ = 0;

    // host DSP state (audio thread)
    float dcX_ = 0.f, dcY_ = 0.f;
    float inG_ = 1.f, outG_ = 1.f, byp_ = 0.f, mute_ = 0.f;
    int64_t startupMute_ = 0;
    float inPk_ = 0.f, inMs_ = 0.f, outPk_ = 0.f, outMs_ = 0.f;
    float tonePhase_ = 0.f, toneG_ = 0.f;  // test tone phase accumulator (0..1) and fade gain

    // constants derived from the sample rate
    float rampStep_;      // per-sample step of the ~10 ms linear ramps
    int xfLen_;           // parallel crossfade length
    float muteStep_;      // ~20 ms mute ramp
    float tailFadeStep_;  // ~50 ms tail fade-out
    int32_t tailQuietLen_, tailMaxLen_;
    float gainCoef_;      // one-pole for in/out gain (~5 ms)

    SpscRing<float> tuner_;
};

}  // namespace bb
