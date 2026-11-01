// bassbox engine: null audio backend. A thread paced by the monotonic clock calls
// Processor::process once per period with silence (or a looped WAV) as input and discards the
// output. Lets the full engine (socket, presets, tuner, meters) run on a Mac or without the HAT.
#include <atomic>
#include <chrono>
#include <cmath>
#include <thread>
#include <vector>

#include "audio.h"
#include "wav.h"

namespace bb {

namespace {

class NullAudio final : public AudioBackend {
public:
    ~NullAudio() override { stop(); }

    bool open(const AudioConfig& cfg, AudioStats& stats, std::string& err) override {
        cfg_ = cfg;
        stats_ = &stats;
        if (!cfg.nullInput.empty()) {
            Wav w;
            if (!readWav(cfg.nullInput, w, err)) return false;
            loop_ = w.channel(0);
        }
        in_.assign(cfg.period, 0.f);
        out_.assign(cfg.period, 0.f);
        stats.info = json{{"backend", "null"}, {"rate", cfg.rate}, {"period", cfg.period}, {"periods", cfg.periods},
                          {"input", cfg.nullInput.empty() ? "silence" : cfg.nullInput}};
        return true;
    }

    bool start(Processor& proc, std::string&) override {
        proc_ = &proc;
        running_ = true;
        thread_ = std::thread([this] { run(); });
        return true;
    }

    void stop() override {
        if (thread_.joinable()) {
            proc_->ctl.hardMute.store(true);
            std::this_thread::sleep_for(std::chrono::milliseconds(30));
            running_ = false;
            thread_.join();
        }
    }

private:
    void run() {
        stats_->info["rt"] = makeThreadRealtime(0, -1);  // never RT: this is a dev backend
        const auto period = std::chrono::nanoseconds(int64_t(1e9 * double(cfg_.period) / double(cfg_.rate)));
        const double periodNs = double(period.count());
        auto next = std::chrono::steady_clock::now();
        float avg = 0.f, winMax = 0.f;
        unsigned winN = 0;
        stats_->running.store(true);
        while (running_.load(std::memory_order_relaxed)) {
            next += period;
            std::this_thread::sleep_until(next);
            for (unsigned i = 0; i < cfg_.period; ++i) {
                in_[i] = loop_.empty() ? 0.f : loop_[pos_];
                if (!loop_.empty() && ++pos_ >= loop_.size()) pos_ = 0;
            }
            const int64_t t0 = nowNs();
            if (cfg_.testHook) {
                // simulated loopback for --latency-test: input = output 300 frames ago, -6 dB
                for (unsigned i = 0; i < cfg_.period; ++i) in_[i] = 0.5f * sim_[(simPos_ + i) % sim_.size()];
                cfg_.testHook(cfg_.testCtx, in_.data(), out_.data(), int(cfg_.period));
                for (unsigned i = 0; i < cfg_.period; ++i) sim_[(simPos_ + i + kSimDelay) % sim_.size()] = out_[i];
                simPos_ = (simPos_ + cfg_.period) % sim_.size();
            } else {
                proc_->process(in_.data(), out_.data(), int(cfg_.period));
            }
            const float load = float(double(nowNs() - t0) / periodNs);
            avg += 0.01f * (load - avg);
            winMax = std::max(winMax, load);
            if (++winN >= cfg_.rate / cfg_.period) {
                stats_->dspLoadMax.store(winMax);
                winMax = 0.f;
                winN = 0;
            }
            stats_->dspLoad.store(avg);
        }
        stats_->running.store(false);
    }

    AudioConfig cfg_;
    AudioStats* stats_ = nullptr;
    Processor* proc_ = nullptr;
    static constexpr size_t kSimDelay = 300;
    std::vector<float> loop_, in_, out_, sim_ = std::vector<float>(4096, 0.f);
    size_t pos_ = 0, simPos_ = 0;
    std::atomic<bool> running_{false};
    std::thread thread_;
};

}  // namespace

std::unique_ptr<AudioBackend> makeNullAudio() { return std::make_unique<NullAudio>(); }

}  // namespace bb
