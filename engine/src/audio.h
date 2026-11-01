// bassbox engine: audio backends. Each owns the real-time thread that calls Processor::process.
//   AlsaAudio  hw:wm8960soundcard full duplex (Linux only, BB_HAVE_ALSA)
//   NullAudio  clock-paced thread with silence or a looped WAV as input (for development on a
//              Mac or without the HAT; same Processor, same timing model)
#pragma once

#include <memory>
#include <string>

#include "host.h"
#include "processor.h"

namespace bb {

struct AudioConfig {
    std::string device = "hw:wm8960soundcard";
    unsigned rate = 48000;
    unsigned period = 64;   // frames
    unsigned periods = 2;   // ALSA buffer = period * periods; playback is kept full
    std::string format = "auto";  // auto (S32_LE, then S16_LE) | S32_LE | S24_LE | S16_LE
    int cpu = 3;            // -1 = don't pin
    int priority = 80;      // SCHED_FIFO; 0 = don't request RT
    std::string nullInput;  // NullAudio: WAV to loop as input ("" = silence)
    // Test hook (latency measurement): when set, the RT loop calls it instead of the
    // Processor. Must be RT-safe (preallocated buffers only).
    void (*testHook)(void* ctx, const float* in, float* out, int n) = nullptr;
    void* testCtx = nullptr;
};

class AudioBackend {
public:
    virtual ~AudioBackend() = default;
    // Control thread. open() configures the device and fills stats.info.
    virtual bool open(const AudioConfig& cfg, AudioStats& stats, std::string& err) = 0;
    virtual bool start(Processor& proc, std::string& err) = 0;
    // Fades the output to silence (~20 ms), then stops the thread and closes the device.
    virtual void stop() = 0;
};

std::unique_ptr<AudioBackend> makeAlsaAudio();  // nullptr when built without ALSA
std::unique_ptr<AudioBackend> makeNullAudio();

// Shared by both backends: make the calling thread real-time (SCHED_FIFO prio, pinned to cpu)
// and pre-fault its stack. Returns a description of what was applied (for the log).
std::string makeThreadRealtime(int priority, int cpu);

}  // namespace bb
