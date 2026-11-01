// bassbox engine: audio-side processing core. See processor.h for the threading contract.
//
// RT-safety notes for everything reachable from Processor::process():
//   * no new/delete/malloc (buffers are sized in the constructor; chains/slots are built by the
//     control thread), no std::vector growth, no exceptions, no locks, no logging, no syscalls;
//   * all cross-thread data is atomics (relaxed for independent values, acquire/release where
//     a pointer or a sequence number guards other data);
//   * every loop is bounded by the block size and kMaxSlots.
#include "processor.h"

#include <algorithm>
#include <cmath>
#include <cstring>

namespace bb {

namespace {

constexpr float kRampMs = 10.f;          // enable / mix / presence / bypass ramps
constexpr float kXfadeMs = 10.f;         // parallel chain crossfade
constexpr float kMuteMs = 20.f;          // tuner / jack / shutdown mute ramp
constexpr float kTailFadeMs = 50.f;      // fade applied when a tail is cut
constexpr float kTailQuiet = 1e-5f;      // -100 dBFS: tail considered finished ...
constexpr float kTailQuietMs = 200.f;    // ... after this long below it
constexpr float kTailMaxS = 5.f;         // tails are cut (faded) after this long regardless
constexpr float kDcR = 0.99974f;         // DC blocker pole: ~2 Hz at 48 kHz
constexpr float kLimitT = 0.8912509f;    // soft limiter knee, -1 dBFS
constexpr float kClipIn = 0.999f;        // input sample this close to full scale = ADC clip
constexpr float kClipOut = 0.999f;       // output exceeds full scale before the limiter
constexpr float kMeterTau = 0.3f;        // meter decay / RMS time constant, seconds
constexpr float kInsane = 1e4f;          // |sample| above this (or NaN/Inf) = broken effect

inline float clamp01(float v) { return v < 0.f ? 0.f : (v > 1.f ? 1.f : v); }

// Move cur toward target by at most d; lands exactly on target.
inline float approach(float cur, float target, float d) {
    if (cur < target) return (target - cur <= d) ? target : cur + d;
    if (cur > target) return (cur - target <= d) ? target : cur - d;
    return cur;
}

// Transparent below kLimitT; above it a tanh knee that is C1-continuous at the knee and
// approaches (never exceeds) full scale. Static, so zero latency; only acts on overs.
inline float softLimit(float v) {
    const float a = std::fabs(v);
    if (a <= kLimitT) return v;
    const float k = 1.f - kLimitT;
    const float y = kLimitT + k * std::tanh((a - kLimitT) / k);
    return v < 0.f ? -y : y;
}

}  // namespace

Slot::Slot(uint32_t uid_, const EffectInfo* info_, std::unique_ptr<Effect> fx_)
    : uid(uid_),
      info(info_),
      fx(std::move(fx_)),
      nparams(std::min<int>(int(info_->params.size()), kMaxParams)),
      tails(info_->tails) {
    for (int i = 0; i < kMaxParams; ++i) {
        const float d = i < nparams ? info_->params[i].def : 0.f;
        param[i].store(d, std::memory_order_relaxed);
        applied[i] = d;
    }
}

Processor::Processor(float sampleRate, int maxBlock, std::size_t tunerRingFrames)
    : sr_(sampleRate),
      maxBlock_(std::max(1, std::min(maxBlock, kMaxBlock))),
      x_(kMaxBlock),
      dry_(kMaxBlock),
      alt_(kMaxBlock),
      scratch_(kMaxBlock),
      retired_(16),
      tuner_(tunerRingFrames) {
    rampStep_ = 1.f / (kRampMs * 1e-3f * sr_);
    xfLen_ = std::max(1, int(kXfadeMs * 1e-3f * sr_));
    muteStep_ = 1.f / (kMuteMs * 1e-3f * sr_);
    tailFadeStep_ = 1.f / (kTailFadeMs * 1e-3f * sr_);
    tailQuietLen_ = int32_t(kTailQuietMs * 1e-3f * sr_);
    tailMaxLen_ = int32_t(kTailMaxS * sr_);
    gainCoef_ = 1.f - std::exp(-1.f / (0.005f * sr_));
}

Processor::~Processor() {
    // Only called after the audio thread has stopped. Chains are owned by the Host, which
    // drains the mailbox and the retire ring itself; nothing to free here.
}

// ------------------------------------------------------------------ control-thread API

bool Processor::publish(Chain* c) {
    Chain* expected = nullptr;
    return mailbox_.compare_exchange_strong(expected, c, std::memory_order_acq_rel);
}

Chain* Processor::takeRetired() {
    Chain* c = nullptr;
    return retired_.pop(c) ? c : nullptr;
}

// > 0: output muted for that long after start, then a 20 ms fade-in (live engine: ADC high-pass
// settling). 0: the output is open from the first sample (bb-render, tests).
void Processor::setStartupMute(float seconds) {
    startupMute_ = int64_t(seconds * sr_);
    mute_ = seconds > 0.f ? 0.f : 1.f;
}

// ------------------------------------------------------------------ audio thread

void Processor::discontinuity() {
    mute_ = 0.f;  // ramps back up over kMuteMs
    dcX_ = dcY_ = 0.f;
}

void Processor::process(const float* in, float* out, int n) {
    while (n > 0) {
        const int m = std::min(n, maxBlock_);
        processBlock(in, out, m);
        in += m;
        out += m;
        n -= m;
    }
}

void Processor::retire(Chain* c) {
    if (!c) return;
    if (!retired_.push(c)) {
        // Control thread is behind: keep it locally, retry next block. The backlog can only
        // grow by one per completed transition (>= 10 ms apart), so 8 entries is ample.
        if (retireBacklogN_ < 8) retireBacklog_[retireBacklogN_++] = c;
    }
}

void Processor::flushRetire() {
    while (retireBacklogN_ > 0 && retired_.push(retireBacklog_[0])) {
        for (int i = 1; i < retireBacklogN_; ++i) retireBacklog_[i - 1] = retireBacklog_[i];
        --retireBacklogN_;
    }
}

void Processor::handleMailbox() {
    if (phase_ != Phase::None) return;  // finish the current transition first
    Chain* c = mailbox_.exchange(nullptr, std::memory_order_acq_rel);
    if (!c) return;
    if (!cur_) {  // very first chain: nothing to fade from (output is muted at start anyway)
        cur_ = c;
        for (Slot* s : cur_->slots) s->pTarget = 1.f;
        meters.activeSerial.store(c->serial, std::memory_order_relaxed);
        return;
    }
    next_ = c;
    if (c->parallel) {
        phase_ = Phase::Parallel;
        xfPos_ = 0;
        for (Slot* s : next_->slots) s->pTarget = 1.f;
        return;
    }
    phase_ = Phase::FadeOut;
    for (Slot* s : c->fadeOutFirst) s->pTarget = 0.f;
    if (c->fadeOutFirst.empty()) swapToNext();
}

void Processor::swapToNext() {
    retire(cur_);
    cur_ = next_;
    next_ = nullptr;
    phase_ = Phase::None;
    for (Slot* s : cur_->slots) s->pTarget = 1.f;
    meters.activeSerial.store(cur_->serial, std::memory_order_relaxed);
    meters.transitions.fetch_add(1, std::memory_order_relaxed);
}

void Processor::resetAll() {
    auto rs = [](Chain* c) {
        if (!c) return;
        for (Slot* s : c->slots) s->fx->reset();
    };
    rs(cur_);
    rs(next_);
}

void Processor::runChain(const Chain& c, float* x, int n) {
    for (Slot* s : c.slots) runSlot(*s, x, n);
}

void Processor::runSlot(Slot& s, float* x, int n) {
    // Parameter changes since the last block (setParam only for values that moved).
    const uint32_t seq = s.paramSeq.load(std::memory_order_acquire);
    if (seq != s.seenSeq) {
        s.seenSeq = seq;
        for (int i = 0; i < s.nparams; ++i) {
            const float v = s.param[i].load(std::memory_order_relaxed);
            if (v != s.applied[i]) {
                s.applied[i] = v;
                s.fx->setParam(i, v);
            }
        }
    }

    const float d = rampStep_ * float(n);
    const float e0 = s.e, m0 = s.m, p0 = s.p;
    const float e1 = approach(e0, clamp01(s.enabled.load(std::memory_order_relaxed)), d);
    const float m1 = approach(m0, clamp01(s.mix.load(std::memory_order_relaxed)), d);
    const float p1 = approach(p0, s.pTarget, d);
    s.e = e1;
    s.m = m1;
    s.p = p1;
    if (p0 == 0.f && p1 == 0.f) return;  // structurally absent: exact identity
    const float inv = 1.f / float(n);
    float* w = scratch_.data();

    if (!s.tails) {
        if (e0 == 0.f && e1 == 0.f) {  // bypassed: identity, effect idles (no CPU)
            s.running = false;
            return;
        }
        if (!s.running) {  // re-enabled: start from clean state; the ramp masks the onset
            s.fx->reset();
            s.running = true;
        }
        std::memcpy(w, x, sizeof(float) * size_t(n));
        s.fx->process(w, n);
        // y = x + g (fx(x) - x), g = e*m*p interpolated across the block
        const float g0 = e0 * m0 * p0, g1 = e1 * m1 * p1;
        if (g0 == g1) {
            if (g1 == 1.f) {
                std::memcpy(x, w, sizeof(float) * size_t(n));
            } else {
                for (int i = 0; i < n; ++i) x[i] += g1 * (w[i] - x[i]);
            }
        } else {
            const float dg = (g1 - g0) * inv;
            for (int i = 0; i < n; ++i) x[i] += (g0 + dg * float(i + 1)) * (w[i] - x[i]);
        }
        return;
    }

    // tails effect: input is e*x, output y = x + p*m*(tf*fx(e*x) - e*x).
    // Disabled (e = 0) but running: the effect rings out on silence and its tail is added.
    if (e0 == 0.f && e1 == 0.f && !s.running) return;
    if (e1 > 0.f) {
        s.tfTarget = 1.f;
        s.tailAge = 0;
        s.tailQuiet = 0;
        s.running = true;
    }
    const float de = (e1 - e0) * inv;
    if (de == 0.f) {
        for (int i = 0; i < n; ++i) w[i] = e1 * x[i];
    } else {
        for (int i = 0; i < n; ++i) w[i] = (e0 + de * float(i + 1)) * x[i];
    }
    s.fx->process(w, n);

    const float tf0 = s.tf;
    if (e1 == 0.f) {
        float pk = 0.f;
        for (int i = 0; i < n; ++i) pk = std::max(pk, std::fabs(w[i]));
        s.tailAge += n;
        s.tailQuiet = (pk < kTailQuiet) ? s.tailQuiet + n : 0;
        if (s.tailQuiet >= tailQuietLen_ || s.tailAge >= tailMaxLen_) s.tfTarget = 0.f;
    }
    const float tf1 = approach(tf0, s.tfTarget, tailFadeStep_ * float(n));
    s.tf = tf1;

    const float a0 = p0 * m0, a1 = p1 * m1;  // output weight
    if (a0 == a1 && tf0 == tf1 && de == 0.f) {
        for (int i = 0; i < n; ++i) x[i] += a1 * (tf1 * w[i] - e1 * x[i]);
    } else {
        const float da = (a1 - a0) * inv, dt = (tf1 - tf0) * inv;
        for (int i = 0; i < n; ++i) {
            const float k = float(i + 1);
            x[i] += (a0 + da * k) * ((tf0 + dt * k) * w[i] - (e0 + de * k) * x[i]);
        }
    }
    if (e1 == 0.f && tf1 == 0.f) {  // tail finished (or cut): idle until re-enabled
        s.running = false;
        s.fx->reset();
        s.tf = s.tfTarget = 1.f;
        s.tailAge = s.tailQuiet = 0;
    }
}

void Processor::processBlock(const float* in, float* out, int n) {
    handleMailbox();
    if (retireBacklogN_) flushRetire();

    float* x = x_.data();
    const uint64_t frame0 = meters.frames.load(std::memory_order_relaxed);

    // 1. input: meters on the raw ADC signal, DC blocker, input gain
    {
        const bool dc = ctl.dcBlock.load(std::memory_order_relaxed);
        const float gT = ctl.inGain.load();
        float pk = 0.f, ss = 0.f;
        for (int i = 0; i < n; ++i) {
            const float v = in[i];
            const float a = std::fabs(v);
            pk = std::max(pk, a);
            ss += v * v;
            float y = v;
            if (dc) {
                y = v - dcX_ + kDcR * dcY_;
                dcX_ = v;
                dcY_ = y;
            }
            inG_ += gainCoef_ * (gT - inG_);
            x[i] = y * inG_;
        }
        if (pk >= kClipIn) meters.clipInAt.store(frame0 + 1, std::memory_order_relaxed);
        const float dec = std::exp(-float(n) / (kMeterTau * sr_));
        inPk_ = std::max(pk, inPk_ * dec);
        inMs_ += (1.f - dec) * (ss / float(n) - inMs_);
        meters.inPeak.store(inPk_);
        meters.inRms.store(std::sqrt(inMs_));
    }

    // 2. tuner tap (pre-chain, post input gain). Drops samples if the control thread lags.
    if (ctl.tunerOn.load(std::memory_order_relaxed)) tuner_.pushMany(x, size_t(n));

    // 3. chain(s)
    std::memcpy(dry_.data(), x, sizeof(float) * size_t(n));
    if (phase_ == Phase::Parallel) {
        float* y = alt_.data();
        std::memcpy(y, x, sizeof(float) * size_t(n));
        runChain(*cur_, x, n);
        runChain(*next_, y, n);
        const float inv = 1.f / float(xfLen_);
        for (int i = 0; i < n; ++i) {
            const float t = std::min(1.f, float(xfPos_ + i + 1) * inv);
            x[i] += t * (y[i] - x[i]);
        }
        xfPos_ += n;
        if (xfPos_ >= xfLen_) swapToNext();
    } else if (cur_) {
        runChain(*cur_, x, n);
        if (phase_ == Phase::FadeOut) {
            bool done = true;
            for (Slot* s : next_->fadeOutFirst) done = done && s->p == 0.f;
            if (done) swapToNext();
        }
    }

    // 4. master bypass (chain keeps running underneath so un-bypass is seamless)
    {
        const float b0 = byp_;
        const float b1 = approach(b0, ctl.bypass.load(std::memory_order_relaxed) ? 1.f : 0.f,
                                  rampStep_ * float(n));
        byp_ = b1;
        if (b0 != 0.f || b1 != 0.f) {
            const float db = (b1 - b0) / float(n);
            const float* dr = dry_.data();
            for (int i = 0; i < n; ++i) x[i] += (b0 + db * float(i + 1)) * (dr[i] - x[i]);
        }
    }

    // 5. output gain and mute (tuner on, input jack unplugged, shutdown, startup settling)
    {
        const bool muted = ctl.tunerOn.load(std::memory_order_relaxed) ||
                           ctl.inputMute.load(std::memory_order_relaxed) ||
                           ctl.hardMute.load(std::memory_order_relaxed) || startupMute_ > 0;
        if (startupMute_ > 0) startupMute_ -= n;
        const float mT = muted ? 0.f : 1.f;
        const float gT = ctl.outGain.load();
        for (int i = 0; i < n; ++i) {
            outG_ += gainCoef_ * (gT - outG_);
            mute_ = approach(mute_, mT, muteStep_);
            x[i] *= outG_ * mute_;
        }
        meters.muteGain.store(mute_);
    }

    // 5b. test tone: after out gain and mute, before the limiter. 20 ms linear fades; the
    // phase accumulator wraps every cycle so float precision is ample. No allocation.
    {
        const bool on = ctl.toneOn.load(std::memory_order_relaxed) && !ctl.hardMute.load(std::memory_order_relaxed);
        if (on || toneG_ > 0.f) {
            const float hz = std::min(20000.f, std::max(20.f, ctl.toneHz.load()));
            const float inc = hz / sr_, amp = ctl.toneAmp.load(), tT = on ? 1.f : 0.f;
            constexpr float kTwoPi = 6.28318530718f;
            for (int i = 0; i < n; ++i) {
                toneG_ = approach(toneG_, tT, muteStep_);
                x[i] += amp * toneG_ * std::sin(kTwoPi * tonePhase_);
                tonePhase_ += inc;
                if (tonePhase_ >= 1.f) tonePhase_ -= 1.f;
            }
            if (toneG_ == 0.f) tonePhase_ = 0.f;
        }
    }

    // 6. safety: NaN/Inf guard, clip detection, soft limiter, output meters
    {
        // std::max would silently skip a NaN, so test every sample explicitly.
        float pk = 0.f;
        bool insane = false;
        for (int i = 0; i < n; ++i) {
            const float a = std::fabs(x[i]);
            insane |= !(a <= kInsane);  // true for NaN and Inf too
            pk = a > pk ? a : pk;
        }
        if (insane) {
            std::memset(x, 0, sizeof(float) * size_t(n));
            resetAll();
            mute_ = 0.f;
            meters.nanResets.fetch_add(1, std::memory_order_relaxed);
            pk = 0.f;
        }
        if (pk > kClipOut) meters.clipOutAt.store(frame0 + 1, std::memory_order_relaxed);
        const bool lim = ctl.limiter.load(std::memory_order_relaxed);
        float ss = 0.f, opk = 0.f;
        for (int i = 0; i < n; ++i) {
            float v = x[i];
            if (lim) v = softLimit(v);
            out[i] = v;
            ss += v * v;
            opk = std::max(opk, std::fabs(v));
        }
        const float dec = std::exp(-float(n) / (kMeterTau * sr_));
        outPk_ = std::max(opk, outPk_ * dec);
        outMs_ += (1.f - dec) * (ss / float(n) - outMs_);
        meters.outPeak.store(outPk_);
        meters.outRms.store(std::sqrt(outMs_));
    }

    meters.frames.store(frame0 + uint64_t(n), std::memory_order_relaxed);
}

}  // namespace bb
