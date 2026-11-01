// Helpers for per-sample modulated filters (env_filter, wah, synth_filter, subsynth).
#pragma once

#include "dsp.h"

namespace bb {
namespace dsp {

// tan(x), [3/2] Pade approximant; |rel err| < 0.6% for 0 <= x <= 1.2 (clamped above 1.3).
inline float fastTan(float x) {
    x = x < 1.3f ? x : 1.3f;
    const float x2 = x * x;
    return x * (15.f - x2) / (15.f - 6.f * x2);
}

// prewarped TPT gain for cutoff fc (Hz), cheap enough to call every sample
inline float fastG(float fc, float fs) {
    return fastTan(kPi * clampf(fc, 10.f, 0.4f * fs) / fs);
}

// exp2 for modulation in octaves (|x| < ~20), cheap polynomial on the fractional part
inline float fastExp2(float x) {
    const float fl = std::floor(x);
    const float f = x - fl;
    // 2^f on [0,1): 4th-order weighted least-squares fit, exact at 0 and 1, rel err < 4e-6
    const float p = 1.f + f * (0.69302315f + f * (0.24140407f + f * (0.05202490f + f * 0.01354788f)));
    return std::ldexp(p, int(fl));
}

}  // namespace dsp
}  // namespace bb
