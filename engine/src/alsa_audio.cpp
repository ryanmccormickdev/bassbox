// bassbox engine: ALSA full-duplex backend (hw:wm8960soundcard, 48 kHz).
//
// Timing model: capture and playback are linked (one start, one clock: the I2S LRCLK). The
// playback buffer (period * periods frames) is pre-filled with silence, so each period the RT
// thread blocks in readi() until a capture period is complete, processes it, and writei()s it
// behind the queued periods: nominal latency = periods * period frames + codec filters, and the
// processing deadline is (periods - 1) periods. Both directions use the same sample format
// (the shared I2S frame forces it; the driver refuses S16 playback next to S32 capture).
//
// RT thread rules: the loop only calls snd_pcm_readi/writei (and, on an xrun, the ALSA
// recovery calls); conversion buffers are allocated in open(); stats are atomics.
#include "audio.h"
#include "catalog.h"

#if defined(BB_HAVE_ALSA)

#include <alsa/asoundlib.h>
#include <pthread.h>

#include <algorithm>
#include <atomic>
#include <cmath>
#include <cstring>
#include <thread>
#include <vector>

namespace bb {

namespace {

enum class Fmt { S16, S24, S32 };

const char* fmtName(Fmt f) { return f == Fmt::S16 ? "S16_LE" : f == Fmt::S24 ? "S24_LE" : "S32_LE"; }
snd_pcm_format_t fmtAlsa(Fmt f) {
    return f == Fmt::S16 ? SND_PCM_FORMAT_S16_LE : f == Fmt::S24 ? SND_PCM_FORMAT_S24_LE : SND_PCM_FORMAT_S32_LE;
}

class AlsaAudio final : public AudioBackend {
public:
    ~AlsaAudio() override { stop(); }

    bool open(const AudioConfig& cfg, AudioStats& stats, std::string& err) override {
        cfg_ = cfg;
        stats_ = &stats;
        std::vector<Fmt> fmts;
        if (cfg.format == "auto") fmts = {Fmt::S32, Fmt::S16};
        else if (cfg.format == "S32_LE") fmts = {Fmt::S32};
        else if (cfg.format == "S24_LE") fmts = {Fmt::S24};
        else if (cfg.format == "S16_LE") fmts = {Fmt::S16};
        else {
            err = "unknown format " + cfg.format;
            return false;
        }
        std::string lastErr;
        for (Fmt f : fmts) {
            if (openWith(f, lastErr)) {
                fmt_ = f;
                break;
            }
            closePcm();
        }
        if (!cap_) {
            err = lastErr;
            return false;
        }
        const size_t ch = 2;
        capBuf_.assign(size_t(period_) * ch * 4, 0);
        playBuf_.assign(size_t(period_) * ch * 4, 0);
        silence_.assign(size_t(period_) * ch * 4, 0);
        in_.assign(period_, 0.f);
        out_.assign(period_, 0.f);
        stats.info = json{{"backend", "alsa"},
                          {"device", cfg.device},
                          {"format", fmtName(fmt_)},
                          {"rate", rate_},
                          {"period", period_},
                          {"periods", periods_},
                          {"buffer", buffer_},
                          {"linked", linked_},
                          {"nominal_latency_frames", period_ * periods_},
                          {"nominal_latency_ms", jnum(1000.f * float(period_ * periods_) / float(rate_))}};
        return true;
    }

    bool start(Processor& proc, std::string& err) override {
        proc_ = &proc;
        running_.store(true);
        std::atomic<int> ready{0};
        pthread_attr_t attr;
        pthread_attr_init(&attr);
        pthread_attr_setstacksize(&attr, 512 * 1024);
        struct Arg {
            AlsaAudio* self;
            std::atomic<int>* ready;
        };
        auto* arg = new Arg{this, &ready};
        const int rc = pthread_create(
            &thread_, &attr,
            [](void* p) -> void* {
                auto* a = static_cast<Arg*>(p);
                AlsaAudio* self = a->self;
                self->rtLog_ = makeThreadRealtime(self->cfg_.priority, self->cfg_.cpu);
                a->ready->store(1);
                delete a;
                self->rtMain();
                return nullptr;
            },
            arg);
        pthread_attr_destroy(&attr);
        if (rc != 0) {
            delete arg;
            running_.store(false);
            err = std::string("pthread_create: ") + std::strerror(rc);
            return false;
        }
        threadStarted_ = true;
        while (!ready.load()) std::this_thread::yield();
        stats_->info["rt"] = rtLog_;
        return true;
    }

    void stop() override {
        if (threadStarted_) {
            if (proc_ && !cfg_.testHook) {
                proc_->ctl.hardMute.store(true);
                for (int i = 0; i < 100 && proc_->meters.muteGain.load() > 0.f && stats_->running.load(); ++i)
                    std::this_thread::sleep_for(std::chrono::milliseconds(1));
                std::this_thread::sleep_for(std::chrono::milliseconds(2 * periodMs()));  // let it reach the DAC
            }
            running_.store(false);
            pthread_join(thread_, nullptr);
            threadStarted_ = false;
        }
        closePcm();
    }

private:
    int periodMs() const { return std::max(1, int(1000 * period_ * periods_ / std::max(1u, rate_))); }

    bool setup(snd_pcm_t* h, Fmt f, std::string& err) {
        snd_pcm_hw_params_t* hw;
        snd_pcm_hw_params_alloca(&hw);
        int rc;
        auto fail = [&](const char* what) {
            err = std::string(what) + ": " + snd_strerror(rc);
            return false;
        };
        if ((rc = snd_pcm_hw_params_any(h, hw)) < 0) return fail("hw_params_any");
        if ((rc = snd_pcm_hw_params_set_rate_resample(h, hw, 0)) < 0) return fail("rate_resample");
        if ((rc = snd_pcm_hw_params_set_access(h, hw, SND_PCM_ACCESS_RW_INTERLEAVED)) < 0) return fail("access");
        if ((rc = snd_pcm_hw_params_set_format(h, hw, fmtAlsa(f))) < 0) return fail(fmtName(f));
        if ((rc = snd_pcm_hw_params_set_channels(h, hw, 2)) < 0) return fail("channels=2");
        if ((rc = snd_pcm_hw_params_set_rate(h, hw, cfg_.rate, 0)) < 0) return fail("rate");
        snd_pcm_uframes_t ps = cfg_.period;
        if ((rc = snd_pcm_hw_params_set_period_size(h, hw, ps, 0)) < 0) return fail("period size");
        if ((rc = snd_pcm_hw_params_set_periods(h, hw, cfg_.periods, 0)) < 0) return fail("periods");
        if ((rc = snd_pcm_hw_params(h, hw)) < 0) return fail("hw_params");
        snd_pcm_uframes_t bs = 0;
        snd_pcm_hw_params_get_buffer_size(hw, &bs);
        snd_pcm_hw_params_get_period_size(hw, &ps, nullptr);
        period_ = unsigned(ps);
        buffer_ = unsigned(bs);
        periods_ = cfg_.periods;
        rate_ = cfg_.rate;

        snd_pcm_sw_params_t* sw;
        snd_pcm_sw_params_alloca(&sw);
        snd_pcm_sw_params_current(h, sw);
        snd_pcm_uframes_t boundary = 0;
        snd_pcm_sw_params_get_boundary(sw, &boundary);
        snd_pcm_sw_params_set_avail_min(h, sw, ps);
        snd_pcm_sw_params_set_start_threshold(h, sw, boundary);  // we start explicitly
        snd_pcm_sw_params_set_stop_threshold(h, sw, bs);         // stop (xrun) when fully drained
        snd_pcm_sw_params_set_silence_threshold(h, sw, 0);
        if ((rc = snd_pcm_sw_params(h, sw)) < 0) return fail("sw_params");
        return true;
    }

    bool openWith(Fmt f, std::string& err) {
        int rc;
        if ((rc = snd_pcm_open(&cap_, cfg_.device.c_str(), SND_PCM_STREAM_CAPTURE, 0)) < 0) {
            cap_ = nullptr;
            err = "open capture " + cfg_.device + ": " + snd_strerror(rc);
            return false;
        }
        if ((rc = snd_pcm_open(&play_, cfg_.device.c_str(), SND_PCM_STREAM_PLAYBACK, 0)) < 0) {
            play_ = nullptr;
            err = "open playback " + cfg_.device + ": " + snd_strerror(rc);
            return false;
        }
        std::string e;
        if (!setup(cap_, f, e)) {
            err = std::string("capture ") + fmtName(f) + ": " + e;
            return false;
        }
        const unsigned capPeriod = period_, capBuffer = buffer_;
        if (!setup(play_, f, e)) {
            err = std::string("playback ") + fmtName(f) + ": " + e;
            return false;
        }
        if (period_ != capPeriod || buffer_ != capBuffer) {
            err = "capture/playback period mismatch";
            return false;
        }
        linked_ = snd_pcm_link(cap_, play_) == 0;
        return true;
    }

    void closePcm() {
        if (cap_ && play_ && linked_) snd_pcm_unlink(cap_);
        if (cap_) snd_pcm_close(cap_);
        if (play_) snd_pcm_close(play_);
        cap_ = play_ = nullptr;
        linked_ = false;
    }

    // ---------------------------------------------------------------- RT thread

    // Fill the playback buffer with silence and start both streams.
    bool primeAndStart() {
        for (unsigned i = 0; i < periods_; ++i) {
            snd_pcm_sframes_t w = snd_pcm_writei(play_, silence_.data(), period_);
            if (w < 0) return false;
        }
        if (linked_) return snd_pcm_start(cap_) == 0;
        if (snd_pcm_start(play_) != 0) return false;
        return snd_pcm_start(cap_) == 0;
    }

    bool recover() {
        stats_->xruns.fetch_add(1, std::memory_order_relaxed);
        snd_pcm_drop(cap_);
        snd_pcm_drop(play_);
        if (snd_pcm_prepare(cap_) < 0) return false;
        if (snd_pcm_prepare(play_) < 0) return false;
        proc_->discontinuity();
        if (primeAndStart()) return true;
        // Start failed: drop back to a clean state and retry once; the read loop's
        // snd_pcm_wait timeout catches a stream that still never starts.
        snd_pcm_drop(cap_);
        snd_pcm_drop(play_);
        snd_pcm_prepare(cap_);
        snd_pcm_prepare(play_);
        return primeAndStart();
    }

    void toFloat(const uint8_t* src, float* dst, unsigned n) const {
        switch (fmt_) {
            case Fmt::S32: {
                const int32_t* s = reinterpret_cast<const int32_t*>(src);
                for (unsigned i = 0; i < n; ++i) dst[i] = float(s[2 * i]) * (1.f / 2147483648.f);
                break;
            }
            case Fmt::S24: {
                const int32_t* s = reinterpret_cast<const int32_t*>(src);
                for (unsigned i = 0; i < n; ++i) dst[i] = float(int32_t(uint32_t(s[2 * i]) << 8) >> 8) * (1.f / 8388608.f);
                break;
            }
            case Fmt::S16: {
                const int16_t* s = reinterpret_cast<const int16_t*>(src);
                for (unsigned i = 0; i < n; ++i) dst[i] = float(s[2 * i]) * (1.f / 32768.f);
                break;
            }
        }
    }

    // mono -> both channels
    void fromFloat(const float* src, uint8_t* dst, unsigned n) const {
        constexpr float kMaxF = 0.99999994f;  // largest float < 1
        switch (fmt_) {
            case Fmt::S32: {
                int32_t* d = reinterpret_cast<int32_t*>(dst);
                for (unsigned i = 0; i < n; ++i) {
                    const float v = std::min(kMaxF, std::max(-1.f, src[i]));
                    d[2 * i] = d[2 * i + 1] = int32_t(v * 2147483648.f);
                }
                break;
            }
            case Fmt::S24: {
                int32_t* d = reinterpret_cast<int32_t*>(dst);
                for (unsigned i = 0; i < n; ++i) {
                    const float v = std::min(kMaxF, std::max(-1.f, src[i]));
                    d[2 * i] = d[2 * i + 1] = int32_t(v * 8388607.f);
                }
                break;
            }
            case Fmt::S16: {
                int16_t* d = reinterpret_cast<int16_t*>(dst);
                for (unsigned i = 0; i < n; ++i) {
                    const float v = std::min(kMaxF, std::max(-1.f, src[i]));
                    d[2 * i] = d[2 * i + 1] = int16_t(std::lrint(v * 32767.f));
                }
                break;
            }
        }
    }

    void rtMain() {
        const double periodNs = 1e9 * double(period_) / double(rate_);
        const float avgCoef = 1.f - std::exp(-1.f / (0.5f * float(rate_) / float(period_)));
        const unsigned winPeriods = std::max(1u, rate_ / period_);  // ~1 s
        float avg = 0.f, winMax = 0.f;
        unsigned winN = 0, late = 0;
        int failures = 0;
        const unsigned frameBytes = unsigned(snd_pcm_frames_to_bytes(cap_, 1));

        if (!primeAndStart()) {
            stats_->failed.store(true);
            return;
        }
        stats_->running.store(true);
        while (running_.load(std::memory_order_relaxed)) {
            // ---- capture one period (blocking; loops only on a short read)
            unsigned got = 0;
            bool xrun = false;
            while (got < period_) {
                // Never block forever: a stream that failed to (re)start would hang readi()
                // and silence the pedal; 200 ms without data counts as an xrun.
                if (snd_pcm_wait(cap_, 200) == 0) {
                    xrun = true;
                    break;
                }
                snd_pcm_sframes_t r = snd_pcm_readi(cap_, capBuf_.data() + got * frameBytes, period_ - got);
                if (r == -EAGAIN || r == -EINTR) continue;
                if (r < 0) {
                    xrun = true;
                    break;
                }
                got += unsigned(r);
            }
            if (xrun) {
                stats_->xrunsCapture.fetch_add(1, std::memory_order_relaxed);
                if (!recover() && ++failures > 50) break;
                continue;
            }
            // how far behind are we? (frames already waiting beyond the period just read)
            const snd_pcm_sframes_t backlog = snd_pcm_avail_update(cap_);
            if (backlog > 0) late = std::max(late, unsigned(backlog));

            // ---- process
            const int64_t t0 = nowNs();
            toFloat(capBuf_.data(), in_.data(), period_);
            if (cfg_.testHook) cfg_.testHook(cfg_.testCtx, in_.data(), out_.data(), int(period_));
            else proc_->process(in_.data(), out_.data(), int(period_));
            fromFloat(out_.data(), playBuf_.data(), period_);
            const float load = float(double(nowNs() - t0) / periodNs);
            avg += avgCoef * (load - avg);
            winMax = std::max(winMax, load);
            if (++winN >= winPeriods) {
                stats_->lateMax.store(late, std::memory_order_relaxed);
                late = 0;
                stats_->dspLoadMax.store(winMax);
                winMax = 0.f;
                winN = 0;
            }
            stats_->dspLoad.store(avg);

            // ---- playback (space for one period is free: the DAC consumed one meanwhile)
            snd_pcm_sframes_t w = snd_pcm_writei(play_, playBuf_.data(), period_);
            if (w == -EAGAIN || w == -EINTR) w = snd_pcm_writei(play_, playBuf_.data(), period_);
            if (w < 0) {
                stats_->xrunsPlayback.fetch_add(1, std::memory_order_relaxed);
                if (!recover() && ++failures > 50) break;
                continue;
            }
            failures = 0;
        }
        stats_->running.store(false);
        if (running_.load()) stats_->failed.store(true);  // left the loop on errors
    }

    AudioConfig cfg_;
    AudioStats* stats_ = nullptr;
    Processor* proc_ = nullptr;
    snd_pcm_t* cap_ = nullptr;
    snd_pcm_t* play_ = nullptr;
    bool linked_ = false;
    Fmt fmt_ = Fmt::S32;
    unsigned period_ = 64, periods_ = 2, buffer_ = 128, rate_ = 48000;
    std::vector<uint8_t> capBuf_, playBuf_, silence_;
    std::vector<float> in_, out_;
    std::atomic<bool> running_{false};
    pthread_t thread_{};
    bool threadStarted_ = false;
    std::string rtLog_;
};

}  // namespace

std::unique_ptr<AudioBackend> makeAlsaAudio() { return std::make_unique<AlsaAudio>(); }

}  // namespace bb

#else

namespace bb {
std::unique_ptr<AudioBackend> makeAlsaAudio() { return nullptr; }
}  // namespace bb

#endif
