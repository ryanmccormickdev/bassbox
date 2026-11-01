#include "tuner.h"

#include <algorithm>
#include <cmath>

namespace bb {

namespace {
constexpr int kWin = 1024;       // decimated samples analysed
constexpr float kFmin = 27.f;    // below a low B (30.87 Hz) with margin
constexpr float kFmax = 500.f;
constexpr float kThresh = 0.12f;  // YIN absolute threshold
constexpr float kSilence = 3e-4f; // ~-70 dBFS RMS: no pitch below this
const char* kNames[12] = {"C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"};
}  // namespace

Tuner::Tuner(float sampleRate) : fs_(sampleRate) {
    decim_ = std::max(1, int(std::lround(sampleRate / 6000.f)));
    fsd_ = fs_ / float(decim_);
    // 4th-order Butterworth low-pass at 1 kHz as two biquads (Q = 0.5412, 1.3066).
    const float qs[2] = {0.5411961f, 1.3065630f};
    const float w0 = 2.f * float(M_PI) * 1000.f / fs_;
    for (int k = 0; k < 2; ++k) {
        const float alpha = std::sin(w0) / (2.f * qs[k]), c = std::cos(w0), a0 = 1.f + alpha;
        lp_[k].b0 = (1.f - c) / 2.f / a0;
        lp_[k].b1 = (1.f - c) / a0;
        lp_[k].b2 = (1.f - c) / 2.f / a0;
        lp_[k].a1 = -2.f * c / a0;
        lp_[k].a2 = (1.f - alpha) / a0;
    }
    hist_.assign(kWin, 0.f);
    win_.assign(kWin, 0.f);
    d_.assign(size_t(fsd_ / kFmin) + 2, 0.f);
}

void Tuner::reset() {
    for (auto& b : lp_) b.z1 = b.z2 = 0.f;
    std::fill(hist_.begin(), hist_.end(), 0.f);
    pos_ = filled_ = 0;
    phase_ = 0;
    nHist_ = 0;
}

void Tuner::feed(const float* x, int n) {
    for (int i = 0; i < n; ++i) {
        const float y = lp_[1].run(lp_[0].run(x[i]));
        if (++phase_ >= decim_) {
            phase_ = 0;
            hist_[pos_] = y;
            pos_ = (pos_ + 1) % hist_.size();
            if (filled_ < hist_.size()) ++filled_;
        }
    }
}

float Tuner::yin(float& confidence) {
    const int tauMax = std::min(int(d_.size()) - 1, kWin / 2);
    const int tauMin = std::max(2, int(fsd_ / kFmax));
    const int W = kWin - tauMax;
    // difference function d(tau) = sum_j (x_j - x_{j+tau})^2
    d_[0] = 0.f;
    for (int tau = 1; tau <= tauMax; ++tau) {
        float s = 0.f;
        for (int j = 0; j < W; ++j) {
            const float df = win_[j] - win_[j + tau];
            s += df * df;
        }
        d_[tau] = s;
    }
    // cumulative mean normalised difference
    float run = 0.f;
    d_[0] = 1.f;
    for (int tau = 1; tau <= tauMax; ++tau) {
        run += d_[tau];
        d_[tau] = run > 0.f ? d_[tau] * float(tau) / run : 1.f;
    }
    // first dip under the threshold, then down to its local minimum
    int best = -1;
    for (int tau = tauMin; tau <= tauMax; ++tau) {
        if (d_[tau] < kThresh) {
            while (tau + 1 <= tauMax && d_[tau + 1] < d_[tau]) ++tau;
            best = tau;
            break;
        }
    }
    if (best < 0) {  // no clear periodicity: global minimum, low confidence
        best = tauMin;
        for (int tau = tauMin; tau <= tauMax; ++tau)
            if (d_[tau] < d_[best]) best = tau;
    }
    confidence = std::max(0.f, 1.f - d_[best]);
    // parabolic interpolation
    float t = float(best);
    if (best > 1 && best < tauMax) {
        const float a = d_[best - 1], b = d_[best], c = d_[best + 1];
        const float den = a - 2.f * b + c;
        if (std::fabs(den) > 1e-12f) t += 0.5f * (a - c) / den;
    }
    return fsd_ / t;
}

TunerResult Tuner::describe(float hz, float confidence) {
    TunerResult r;
    if (!(hz > 0.f)) return r;
    const float midi = 69.f + 12.f * std::log2(hz / 440.f);
    const int nearest = int(std::lround(midi));
    r.hz = hz;
    r.note = kNames[((nearest % 12) + 12) % 12];
    r.octave = nearest / 12 - 1;
    r.cents = (midi - float(nearest)) * 100.f;
    r.confidence = confidence;
    return r;
}

TunerResult Tuner::analyze() {
    if (filled_ < hist_.size()) return {};
    // unroll the ring, oldest first
    const size_t N = hist_.size();
    float ss = 0.f;
    for (size_t i = 0; i < N; ++i) {
        win_[i] = hist_[(pos_ + i) % N];
        ss += win_[i] * win_[i];
    }
    if (std::sqrt(ss / float(N)) < kSilence) {
        nHist_ = 0;
        return {};
    }
    float conf = 0.f;
    const float hz = yin(conf);
    if (conf < 0.5f) {
        nHist_ = 0;
        TunerResult r;
        r.confidence = conf;
        return r;
    }
    // median of the last 3 estimates (in cents) to steady the needle
    const float c = 1200.f * std::log2(hz / 440.f);
    lastCents_[0] = lastCents_[1];
    lastCents_[1] = lastCents_[2];
    lastCents_[2] = c;
    if (nHist_ < 3) ++nHist_;
    float med = c;
    if (nHist_ == 3) {
        float a = lastCents_[0], b = lastCents_[1], d = lastCents_[2];
        // only smooth when the three agree within a semitone (no octave jumps)
        if (std::fabs(a - d) < 100.f && std::fabs(b - d) < 100.f)
            med = std::max(std::min(a, b), std::min(std::max(a, b), d));
    }
    return describe(440.f * std::exp2(med / 1200.f), conf);
}

}  // namespace bb
