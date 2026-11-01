// bassbox effect interface: the contract between the engine host (engine/src) and the
// effect library (engine/effects). Every effect is a mono, in-place, block processor.
//
// Threading rules (enforced by review, not by the compiler):
//   * create(), prepare(), ready(),
//     destructor                       : called on the CONTROL thread. Allocate here.
//   * setParam(), process(), reset(),
//     activity()                       : called on the AUDIO thread (SCHED_FIFO, isolated core).
//     No allocation, no locks, no syscalls, no logging, no exceptions, bounded time.
//   * activity() is also read by the control thread for UI animation, so keep it a plain
//     float member (a torn read of a float is harmless here).
//
// Parameter values are always in the parameter's real units (Hz, dB, ms, 0..1, enum index),
// never normalised. The host clamps to [min, max] before calling setParam(). The host calls
// setParam() only when a value changes, at block start, and once for every param right after
// prepare(). Effects must smooth any parameter whose jump would click (gains, cutoffs, times).
#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace bb {

enum class Curve : uint8_t {
    Linear,  // UI knob maps linearly
    Log,     // UI knob maps logarithmically (frequencies, times); min must be > 0
};

enum class ParamType : uint8_t {
    Float,
    Enum,  // value is an integer index into `options`; min = 0, max = options.size()-1
    Bool,  // 0 or 1
};

struct ParamSpec {
    std::string id;     // stable key used in presets, e.g. "drive", "tone", "time"
    std::string name;   // short UI label, <= 8 chars ideally ("Drive", "Tone", "Time")
    float min = 0.f, max = 1.f, def = 0.5f;
    std::string unit;   // "", "dB", "Hz", "ms", "%", "x", "st" (semitones), "s"
    Curve curve = Curve::Linear;
    ParamType type = ParamType::Float;
    std::vector<std::string> options;  // Enum labels
    float step = 0.f;   // 0 = continuous; otherwise UI snaps to multiples of step
};

class Effect {
public:
    virtual ~Effect() = default;

    // Control thread. Allocate buffers for up to maxBlock frames at sampleRate.
    virtual void prepare(float sampleRate, int maxBlock) = 0;

    // Control thread, called once after prepare() and the initial setParam() calls, before the
    // slot goes live. Do expensive setup that depends on the initial params here (e.g. load
    // the preset's model synchronously instead of a default one in prepare()). Hosts that
    // predate it never call it, so effects must still work (perhaps degraded) without it.
    virtual void ready() {}

    // Audio thread. Clear internal state (delay lines, filters) without allocating.
    virtual void reset() = 0;

    // Audio thread. index is the position in EffectInfo::params.
    virtual void setParam(int index, float value) = 0;

    // Audio thread. Mono, in place, n <= maxBlock. Input is nominally -1..1 (0 dBFS).
    // Output must be finite (no NaN/Inf) and should stay roughly level-matched to the
    // input at default settings (unity-ish loudness), so chains don't explode.
    virtual void process(float* buf, int n) = 0;

    // 0..1 "what is this effect doing right now", for UI animation: envelope level for
    // dynamics/filters, LFO phase for modulation, gain reduction for compressors, feedback
    // energy for delays. Return 0 if nothing meaningful.
    virtual float activity() const { return 0.f; }
};

struct EffectInfo {
    std::string id;           // stable type key, e.g. "overdrive", "env_filter"
    std::string name;         // display name, e.g. "Overdrive"
    std::string category;     // one of: dynamics, drive, amp, filter, eq, modulation,
                              //         pitch, time, utility
    std::string description;  // one short sentence for the library screen
    std::vector<ParamSpec> params;
    bool tails = false;       // true for delay/reverb: on bypass the host keeps running it with
                              // silent input so the tail rings out instead of cutting off
    std::function<std::unique_ptr<Effect>()> create;
};

// Global registry. Effects self-register from their .cpp via BB_REGISTER_EFFECT.
std::vector<EffectInfo>& registry();

struct Registrar {
    explicit Registrar(EffectInfo info) { registry().push_back(std::move(info)); }
};

}  // namespace bb

#define BB_CONCAT_(a, b) a##b
#define BB_CONCAT(a, b) BB_CONCAT_(a, b)
// Usage (at namespace scope in an effect .cpp):
//   BB_REGISTER_EFFECT({ "overdrive", "Overdrive", "drive", "Warm, amp-like push.",
//                        { {"drive","Drive",0,1,0.5f}, ... }, false,
//                        [] { return std::make_unique<Overdrive>(); } });
#define BB_REGISTER_EFFECT(...) \
    static ::bb::Registrar BB_CONCAT(bb_registrar_, __COUNTER__)(::bb::EffectInfo __VA_ARGS__)
