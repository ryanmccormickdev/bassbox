// Neural Amp: NeuralAmpModelerCore (fetched into engine/third_party, see third_party/README.md) as a bassbox
// effect. Unity build: the NAM sources are #included below, so this one file needs no extra
// include paths or link targets (only -I engine/include, which every effect already needs).
//
// Models: every *.nam under $BB_NAM_DIRS (colon-separated), /var/lib/bassbox/nam and
// /var/pipedal/audio_uploads/NeuralAmpModels (recursive) is scanned ONCE at registration
// (static init) and exposed as the "Model" enum. Adding models needs an engine restart.
//
// Threading (effect.h rules: no allocation, locks or syscalls on the audio thread):
//   * prepare()  (control thread) allocates the I/O buffers only.
//   * ready()    (control thread, after the initial setParam() calls) loads the model the
//     preset asked for synchronously (so a recalled preset starts with the right amp, no swap),
//     Reset()s and prewarms it, then starts a per-instance loader thread. A host that never
//     calls ready() gets pass-through.
//   * setParam(model/size) (audio thread) only stores the requested key in an atomic.
//   * The loader thread (nice 10, so it can't starve the UI) polls every 25 ms for a new request, builds + prewarms the
//     model off the audio thread and publishes it through a single-slot atomic pointer.
//   * The audio thread picks the pending model up at block start, crossfades old -> new over
//     20 ms (both run during the fade), then hands the old one back through another single-slot
//     atomic; the loader thread deletes it. The audio thread never frees anything. It takes a
//     new model only when the retire slot is empty, so neither slot can overflow.
//   * Destructor (control thread) aborts an in-flight load (checked between calibration
//     blocks), joins the loader, then frees everything.
//
// Signal path: in -> Input gain (+ model input calibration) -> [Low Keep LR4 split: sub stays
// clean] -> NAM -> loudness normalisation -> + clean sub -> DC block -> Blend with dry -> Level.
#include "dsp.h"

#if __has_include("../third_party/NeuralAmpModelerCore/NAM/get_dsp.h") && \
    __has_include("../third_party/eigen/Eigen/Dense") && __has_include("../third_party/nlohmann/json.hpp")
#define BB_HAVE_NAM 1
#else
#define BB_HAVE_NAM 0
#endif

#include <atomic>
#if defined(__linux__)
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <mutex>
#include <string>
#include <system_error>
#include <thread>
#include <vector>

#if BB_HAVE_NAM
#define NAM_SAMPLE_FLOAT 1
#define NAM_ENABLE_A2_FAST 1
// third-party code: silence its warnings here only (our own code below stays -Wall -Wextra clean)
#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wunused-parameter"
#pragma clang diagnostic ignored "-Wunused-variable"
#pragma clang diagnostic ignored "-Wunused-but-set-variable"
#pragma clang diagnostic ignored "-Wsign-compare"
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#elif defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wunused-variable"
#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
#pragma GCC diagnostic ignored "-Wsign-compare"
#pragma GCC diagnostic ignored "-Wmaybe-uninitialized"
#pragma GCC diagnostic ignored "-Wclass-memaccess"
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#endif
#include "../third_party/NeuralAmpModelerCore/NAM/activations.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/container.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/conv1d.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/convnet.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/dsp.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/get_dsp.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/linear.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/lstm.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/nam_file.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/ring_buffer.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/sequential.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/util.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/wav.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/wavenet/a2_fast.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/wavenet/model.cpp"
#include "../third_party/NeuralAmpModelerCore/NAM/wavenet/slimmable.cpp"
#if defined(__clang__)
#pragma clang diagnostic pop
#elif defined(__GNUC__)
#pragma GCC diagnostic pop
#endif
#endif  // BB_HAVE_NAM

namespace {
using namespace bb;
using namespace bb::dsp;

// ---------------------------------------------------------------------------------------------
// model discovery (registration time)
struct ModelFile {
    std::string name, path;
};

std::vector<ModelFile>& modelFiles() {
    static std::vector<ModelFile> files;
    return files;
}

std::string displayName(const std::filesystem::path& p) {
    std::string s = p.stem().string();
    for (char& c : s)
        if (c == '_') c = ' ';
    if (s.rfind("[AMP] ", 0) == 0) s = s.substr(6);
    while (!s.empty() && s.back() == ' ') s.pop_back();
    while (!s.empty() && s.front() == ' ') s.erase(s.begin());
    return s.empty() ? p.filename().string() : s;
}

void scanModels() {
    std::vector<std::string> dirs;
    if (const char* env = std::getenv("BB_NAM_DIRS")) {
        std::string e = env;
        size_t a = 0;
        while (a <= e.size()) {
            const size_t b = e.find(':', a);
            const std::string d = e.substr(a, b == std::string::npos ? std::string::npos : b - a);
            if (!d.empty()) dirs.push_back(d);
            if (b == std::string::npos) break;
            a = b + 1;
        }
    }
    dirs.push_back("/var/lib/bassbox/nam");
    dirs.push_back("/var/pipedal/audio_uploads/NeuralAmpModels");
    std::vector<ModelFile> found;
    for (const auto& d : dirs) {
        std::error_code ec;
        if (!std::filesystem::is_directory(d, ec)) continue;
        auto it = std::filesystem::recursive_directory_iterator(
            d, std::filesystem::directory_options::skip_permission_denied, ec);
        for (; !ec && it != std::filesystem::recursive_directory_iterator(); it.increment(ec)) {
            std::error_code fe;
            if (!it->is_regular_file(fe)) continue;
            std::string ext = it->path().extension().string();
            for (char& c : ext) c = char(std::tolower(static_cast<unsigned char>(c)));
            if (ext != ".nam") continue;
            found.push_back({displayName(it->path()), it->path().string()});
        }
    }
    std::sort(found.begin(), found.end(), [](const ModelFile& a, const ModelFile& b) {
        return a.name != b.name ? a.name < b.name : a.path < b.path;
    });
    // same name in two folders: keep both, disambiguate the label
    for (size_t i = 1; i < found.size(); ++i)
        if (found[i].name == found[i - 1].name) found[i].name += " (" + std::to_string(i) + ")";
    modelFiles() = std::move(found);
}

// Default model: the full-rig capture bassbox v1 shipped with (known to work on bass), if present.
int defaultModelIndex() {
    const auto& f = modelFiles();
    for (size_t i = 0; i < f.size(); ++i)
        if (f[i].name.find("JM50LD I Crunch2 FAT CAB") != std::string::npos) return int(i);
    return 0;
}

std::vector<ParamSpec> namParams() {
    scanModels();
    ParamSpec model;
    model.id = "model";
    model.name = "Model";
    model.type = ParamType::Enum;
    model.step = 1.f;
    for (const auto& f : modelFiles()) model.options.push_back(f.name);
    if (model.options.empty()) model.options.push_back("(none)");
    model.min = 0.f;
    model.max = float(model.options.size() - 1);
    model.def = float(modelFiles().empty() ? 0 : defaultModelIndex());
    return {model,
            pDb("input", "Input", -24.f, 24.f, 0.f),
            pDb("level", "Level", -24.f, 12.f, 0.f),
            pPct("blend", "Blend", 100.f),
            pLog("lowkeep", "Low Keep", 40.f, 400.f, 40.f, "Hz"),
            pEnum("size", "Size", {"Standard", "Lite"}, 0)};
}

#if BB_HAVE_NAM
// ---------------------------------------------------------------------------------------------
// A loaded, reset and prewarmed model plus its level calibration.
struct LoadedModel {
    std::unique_ptr<nam::DSP> dsp;
    int key = -1;
    float inCal = 1.f;   // model input calibration (linear)
    float outNorm = 1.f; // loudness normalisation (linear)
    float bias = 0.f;    // model output with silent input (networks are rarely exactly 0 there)
};

constexpr int kSizeCount = 2;
inline int makeKey(int model, int size) { return model * kSizeCount + size; }

// Calibration reference: bassbox v1 (PiPedal) ran the factory captures with the WM8960 full scale
// (+2.2 dBu) plus +6 dB input gain, which suits a bass DI at -18 dBFS RMS. Models that don't say
// what input level they were captured at are assumed to be at 15 dBu.
constexpr float kRefInputDbu = 8.2f;
constexpr float kDefaultModelInputDbu = 15.f;
// Output: every model is level-matched at load time by running a synthetic bass phrase (picked
// E1/A1/D2 notes, -6 dBFS peak / -18.6 dBFS RMS, like the DI test riff) through it and normalising its
// output RMS to the phrase's RMS, so switching models doesn't jump in level. NAM's "loudness"
// metadata is only the fallback if that measurement is degenerate.
constexpr float kTargetLoudnessDb = -21.f;

// 1.2 s calibration phrase, built once and cached (control/loader threads; returns a copy so
// several instances' loader threads can't race on it)
std::vector<float> calibrationPhrase(float fs) {
    static std::mutex mu;
    static std::vector<float> x;
    static float builtFor = 0.f;
    std::lock_guard<std::mutex> lock(mu);
    if (builtFor == fs && !x.empty()) return x;
    const float notes[3] = {41.2f, 55.f, 73.4f};
    const int per = int(0.4f * fs);
    x.assign(size_t(3 * per), 0.f);
    double pk = 0.0;
    for (int n = 0; n < 3; ++n) {
        for (int k = 1; k <= 40; ++k) {
            const double fk = double(notes[n]) * k;
            if (fk > 9000.0) break;
            const double a = std::fabs(std::sin(3.14159265358979 * k * 0.2)) / k / std::sqrt(1 + std::pow(fk / 2500.0, 4));
            const double tau = 0.17 / (1 + 0.25 * k);  // fast decay: crest factor like the test riff (12.6 dB)
            for (int i = 0; i < per; ++i) {
                const double t = double(i) / fs;
                x[size_t(n * per + i)] += float(a * std::sin(2 * 3.14159265358979 * fk * t + 0.7 * k) * std::exp(-t / tau) *
                                               std::min(1.0, t / 0.002) * std::min(1.0, (0.4 - t) / 0.01));
            }
        }
    }
    for (float v : x) pk = std::max(pk, double(std::fabs(v)));
    for (float& v : x) v = float(v / pk * 0.5);  // -6 dBFS peak
    builtFor = fs;
    return x;
}

// abort (optional): checked between processing blocks; returns nullptr when it is set.
LoadedModel* loadModel(int key, float fs, int maxBlock, const std::atomic<bool>* abort = nullptr) {
    auto aborted = [abort] { return abort && abort->load(std::memory_order_relaxed); };
    const int idx = key / kSizeCount, size = key % kSizeCount;
    const auto& files = modelFiles();
    if (idx < 0 || idx >= int(files.size())) return nullptr;
    try {
        nam::DspLoadOptions opt;
        opt.prewarm = false;  // prewarm once below, after the size and rate are set
        auto dsp = nam::get_dsp(std::filesystem::path(files[size_t(idx)].path), opt);
        if (!dsp || dsp->NumInputChannels() != 1 || dsp->NumOutputChannels() < 1) return nullptr;
        if (auto* slim = dynamic_cast<nam::SlimmableModel*>(dsp.get()))
            slim->SetSlimmableSize(size == 1 ? 0.25 : 1.0);
        dsp->SetPrewarmOnReset(true);
        dsp->Reset(double(fs), maxBlock);  // allocates + prewarms (control/loader thread only)
        if (aborted()) return nullptr;
        auto m = std::make_unique<LoadedModel>();
        const float inDbu = dsp->HasInputLevel() ? float(dsp->GetInputLevel()) : kDefaultModelInputDbu;
        m->inCal = dbToLin(kRefInputDbu - inDbu);
        m->outNorm = dsp->HasLoudness() ? dbToLin(kTargetLoudnessDb - float(dsp->GetLoudness())) : 1.f;
        std::vector<float> in(size_t(maxBlock), 0.f), out(size_t(maxBlock), 0.f);
        float* ip = in.data();
        float* op = out.data();
        // output bias with silent input (networks are rarely exactly 0 there)
        double acc = 0.0;
        for (int k = 0; k < 16; ++k) {
            dsp->process(&ip, &op, maxBlock);
            if (k >= 8)
                for (int i = 0; i < maxBlock; ++i) acc += out[size_t(i)];
        }
        m->bias = float(acc / (8.0 * maxBlock));
        // loudness calibration on the synthetic phrase (through the input calibration)
        const std::vector<float> ph = calibrationPhrase(fs);
        double inSq = 0.0, outSq = 0.0;
        for (size_t p = 0; p + size_t(maxBlock) <= ph.size(); p += size_t(maxBlock)) {
            if (aborted()) return nullptr;
            for (int i = 0; i < maxBlock; ++i) in[size_t(i)] = ph[p + size_t(i)] * m->inCal;
            dsp->process(&ip, &op, maxBlock);
            for (int i = 0; i < maxBlock; ++i) {
                inSq += double(ph[p + size_t(i)]) * ph[p + size_t(i)];
                const double o = double(out[size_t(i)]) - m->bias;
                outSq += o * o;
            }
        }
        if (outSq > 1e-12 && inSq > 0.0 && std::isfinite(outSq))
            m->outNorm = clampf(float(std::sqrt(inSq / outSq)), dbToLin(-30.f), dbToLin(30.f));
        // settle again from silence so the first real block starts clean and warm
        dsp->Reset(double(fs), maxBlock);
        std::fill(in.begin(), in.end(), 0.f);
        for (int k = 0; k < 8; ++k) dsp->process(&ip, &op, maxBlock);
        m->dsp = std::move(dsp);
        m->key = key;
        return m.release();
    } catch (const std::exception& e) {
        std::fprintf(stderr, "nam: failed to load '%s': %s\n", files[size_t(idx)].path.c_str(), e.what());
    } catch (...) {
        std::fprintf(stderr, "nam: failed to load '%s'\n", files[size_t(idx)].path.c_str());
    }
    return nullptr;
}
#endif

// ---------------------------------------------------------------------------------------------
class NeuralAmp final : public Effect {
public:
    enum { kModel, kInput, kLevel, kBlend, kLowKeep, kSize };
    ~NeuralAmp() override { stopLoader(); freeAll(); }

    void prepare(float fs, int maxBlock) override {
        stopLoader();
        freeAll();
        fs_ = fs;
        maxBlock_ = std::max(1, maxBlock);
        in_.assign(size_t(maxBlock_), 0.f);
        outA_.assign(size_t(maxBlock_), 0.f);
        outB_.assign(size_t(maxBlock_), 0.f);
        sub_.assign(size_t(maxBlock_), 0.f);
        tmp_.assign(size_t(maxBlock_), 0.f);
        inGain_.setTime(30.f, fs);
        level_.setTime(30.f, fs);
        blend_.setTime(20.f, fs);
        split_.setTime(20.f, fs);
        xover_.setTime(40.f, fs / kSub);
        dc_.set(fs, 5.f);
        fadeLen_ = std::max(1, int(0.020f * fs));
        // the model is loaded in ready(), once the preset's model/size params are known
        primed_ = false;
        reset();
    }

    void ready() override {
#if BB_HAVE_NAM
        stopLoader();
        if (!modelFiles().empty() && !cur_) {
            const int key = makeKey(model_, size_);
            requested_.store(key, std::memory_order_relaxed);
            cur_ = loadModel(key, fs_, maxBlock_);
            if (cur_) loaded_ = key;
            else failed_ = key;  // stays pass-through until another model is chosen
        }
        abort_.store(false);
        running_.store(true);
        loader_ = std::thread([this] { loaderLoop(); });
#endif
    }

    void reset() override {
        xo_.reset();
        dc_.reset();
        inGain_.snap();
        level_.snap();
        blend_.snap();
        split_.snap();
        xover_.snap();
        xo_.set(fs_, std::exp(xover_.cur));
        env_ = 0.f;
        activity_ = 0.f;
    }

    void setParam(int i, float v) override {
        switch (i) {
            case kModel: model_ = std::max(0, int(v + 0.5f)); requestModel(); break;
            case kInput: inGain_.set(dbToLin(v), primed_); break;
            case kLevel: level_.set(dbToLin(v), primed_); break;
            case kBlend: blend_.set(0.01f * v, primed_); break;
            case kLowKeep:
                // the minimum (40 Hz) means "off": everything goes through the model
                split_.set(v > 40.5f ? 1.f : 0.f, primed_);
                xover_.set(std::log(std::max(40.f, v)), primed_);
                if (!primed_) xo_.set(fs_, std::exp(xover_.cur));
                break;
            case kSize: size_ = v >= 0.5f ? 1 : 0; requestModel(); break;
            default: break;
        }
    }

    void process(float* x, int n) override {
        primed_ = true;
#if BB_HAVE_NAM
        // pick up a freshly loaded model (only when not already fading and the retire slot is free)
        if (!next_ && !retired_.load(std::memory_order_acquire)) {
            if (LoadedModel* p = pending_.exchange(nullptr, std::memory_order_acq_rel)) {
                if (cur_) { next_ = p; fadePos_ = 0; }
                else cur_ = p;
            }
        }
#endif
        // 1) input stage: gain, optional clean-sub split
        float* in = in_.data();
        float* sub = sub_.data();
        for (int i0 = 0; i0 < n; i0 += kSub) {
            const int m = std::min(kSub, n - i0);
            if (!xover_.settled()) { xover_.next(); xo_.set(fs_, std::exp(xover_.cur)); }
            for (int i = i0; i < i0 + m; ++i) {
                float lo, hi;
                xo_.split(x[i], lo, hi);
                const float s = split_.next();
                in[i] = (x[i] + s * (hi - x[i])) * inGain_.next();
                sub[i] = s * lo;  // clean sub, re-added after the model
            }
        }

        // 2) the model(s)
#if BB_HAVE_NAM
        float* ip = in;
        if (cur_ && cur_->dsp) {
            // apply this model's input calibration in place
            const float c = cur_->inCal;
            if (next_ && next_->dsp) {
                // during a crossfade both models need their own calibrated input
                for (int i = 0; i < n; ++i) tmp_[size_t(i)] = in[i] * next_->inCal;
            }
            for (int i = 0; i < n; ++i) in[i] *= c;
            float* oa = outA_.data();
            cur_->dsp->process(&ip, &oa, n);
            const float na = cur_->outNorm, ba = cur_->bias;
            for (int i = 0; i < n; ++i) outA_[size_t(i)] = (outA_[size_t(i)] - ba) * na;
            if (next_ && next_->dsp) {
                float* tp = tmp_.data();
                float* ob = outB_.data();
                next_->dsp->process(&tp, &ob, n);
                const float nb = next_->outNorm, bb = next_->bias;
                for (int i = 0; i < n; ++i) {
                    const float t = std::min(1.f, float(fadePos_ + i) / float(fadeLen_));
                    const float g = 0.5f - 0.5f * std::cos(kPi * t);  // raised-cosine fade
                    outA_[size_t(i)] += g * ((outB_[size_t(i)] - bb) * nb - outA_[size_t(i)]);
                }
                fadePos_ += n;
                if (fadePos_ >= fadeLen_) {
                    retired_.store(cur_, std::memory_order_release);
                    cur_ = next_;
                    next_ = nullptr;
                }
            }
        } else
#endif
        {
            // no model (none installed, or it failed to load): exact pass-through, Input ignored
            for (int i = 0; i < n; ++i) outA_[size_t(i)] = x[i] - sub[i];
        }

        // 3) recombine, blend, level
        float env = env_;
        for (int i = 0; i < n; ++i) {
            float w = outA_[size_t(i)];
            if (!(std::fabs(w) < 64.f)) w = 0.f;  // a misbehaving model must not poison the chain
            w = dc_.process(w + sub[i]);
            const float b = blend_.next();
            const float y = (x[i] + b * (w - x[i])) * level_.next();
            x[i] = y;
            env += 0.004f * (std::fabs(y) - env);
        }
        xo_.flushState();
        dc_.flushState();
        env_ = flush(env);
        flushBlock(x, n);
        activity_ = clampf(env_ * 4.f, 0.f, 1.f);
    }

    float activity() const override { return activity_; }

private:
    void requestModel() {
#if BB_HAVE_NAM
        requested_.store(makeKey(model_, size_), std::memory_order_release);
#endif
    }

#if BB_HAVE_NAM
    void loaderLoop() {
#if defined(__linux__)
        // nice 10: a model load (~0.25 s of CPU) must not starve the UI (nice 5) on CPU0-2
        setpriority(PRIO_PROCESS, id_t(syscall(SYS_gettid)), 10);
#endif
        while (running_.load(std::memory_order_acquire)) {
            if (LoadedModel* r = retired_.exchange(nullptr, std::memory_order_acq_rel)) delete r;
            const int want = requested_.load(std::memory_order_acquire);
            if (want != loaded_ && want != failed_ && !pending_.load(std::memory_order_acquire)) {
                LoadedModel* m = loadModel(want, fs_, maxBlock_, &abort_);
                if (!m && abort_.load()) break;
                if (!m) {
                    failed_ = want;
                } else if (requested_.load(std::memory_order_acquire) != want) {
                    delete m;  // superseded while loading; go round again for the newest request
                } else {
                    loaded_ = want;
                    pending_.store(m, std::memory_order_release);
                }
                continue;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
    }
#endif

    void stopLoader() {
#if BB_HAVE_NAM
        abort_.store(true);  // an in-flight load gives up at the next block
        running_.store(false);
        if (loader_.joinable()) loader_.join();
#endif
    }

    void freeAll() {
#if BB_HAVE_NAM
        delete cur_;
        delete next_;
        delete pending_.exchange(nullptr);
        delete retired_.exchange(nullptr);
        cur_ = next_ = nullptr;
        loaded_ = failed_ = -1;
#endif
    }

    float fs_ = 48000.f;
    int maxBlock_ = 256;
    std::vector<float> in_, outA_, outB_, sub_, tmp_;
    Smoother inGain_, level_, blend_, split_, xover_;
    Crossover xo_;
    DcBlock dc_;
    int model_ = 0, size_ = 0;
    float env_ = 0.f, activity_ = 0.f;
    bool primed_ = false;
    int fadeLen_ = 960, fadePos_ = 0;
#if BB_HAVE_NAM
    LoadedModel* cur_ = nullptr;   // audio thread owns
    LoadedModel* next_ = nullptr;  // audio thread owns (crossfade target)
    std::atomic<LoadedModel*> pending_{nullptr};  // loader -> audio
    std::atomic<LoadedModel*> retired_{nullptr};  // audio -> loader
    std::atomic<int> requested_{-1};              // audio -> loader
    std::atomic<bool> running_{false};
    std::atomic<bool> abort_{false};              // control -> loader: give up the current load
    int loaded_ = -1, failed_ = -1;               // loader thread only (and ready())
    std::thread loader_;
#endif
};

}  // namespace

BB_REGISTER_EFFECT({"nam", "Neural Amp", "amp", "Neural amp captures (NAM): real amps, pedals and full rigs.",
                    namParams(), false, [] { return std::make_unique<NeuralAmp>(); }});
