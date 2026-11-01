// bassbox engine: chromatic tuner (control thread).
//
// The audio thread copies its input into an SPSC ring while the tuner is on; the control
// thread drains it every tick, low-passes (4th-order Butterworth, 1 kHz) and decimates by 8
// to 6 kHz, and about 20 times a second runs YIN over the last 1024 decimated samples
// (170 ms, >= 5 periods of a low B at 30.9 Hz). Range 27..500 Hz covers a 5-string's low B
// through high harmonics of the G string.
#pragma once

#include <string>
#include <vector>

namespace bb {

struct TunerResult {
    float hz = 0.f;          // 0 = no pitch
    std::string note;        // "E", "F#", ... ("" when no pitch)
    int octave = 0;          // scientific pitch: E1 = 41.2 Hz, B0 = 30.87 Hz
    float cents = 0.f;       // -50..+50 from the nearest equal-tempered note (A4 = 440 Hz)
    float confidence = 0.f;  // 0..1 (1 - YIN aperiodicity), 0 when silent
};

class Tuner {
public:
    explicit Tuner(float sampleRate);
    void reset();
    void feed(const float* x, int n);  // full-rate samples
    TunerResult analyze();             // runs YIN on the newest window
    // pure helper, exposed for tests
    static TunerResult describe(float hz, float confidence);

private:
    struct Biquad {
        float b0, b1, b2, a1, a2, z1 = 0, z2 = 0;
        float run(float x) {
            const float y = b0 * x + z1;
            z1 = b1 * x - a1 * y + z2;
            z2 = b2 * x - a2 * y;
            return y;
        }
    };
    float yin(float& confidence);

    float fs_, fsd_;
    int decim_ = 8, phase_ = 0;
    Biquad lp_[2];
    std::vector<float> hist_;  // ring of decimated samples
    size_t pos_ = 0, filled_ = 0;
    std::vector<float> win_, d_;
    float lastCents_[3] = {0, 0, 0};
    int nHist_ = 0;
};

}  // namespace bb
