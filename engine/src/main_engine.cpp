// bassbox-engine: the pedal's audio engine.
//
// Threads (that's all of them):
//   main / CONTROL thread (CPU0-2, SCHED_OTHER): poll() loop over the unix socket, GPIO jack
//     events and a 10 ms tick: commands, chain building, presets, autosave, tuner analysis,
//     meters. May allocate, block on files, log.
//   AUDIO thread (CPU3, SCHED_FIFO 80, created by the backend): ALSA read -> Processor ->
//     ALSA write. Never blocked by the control thread or any client.
//
//   bassbox-engine [--config FILE] [--device D] [--period N] [--periods N] [--format F]
//                  [--cpu N] [--priority N] [--socket PATH] [--state-dir DIR]
//                  [--factory-dir DIR] [--jacks-conf FILE] [--null-audio [--null-input WAV]]
//                  [--test-effects] [--no-autosave] [--startup-mute S]
//   bassbox-engine --latency-test SECONDS [--period N --periods N]   loopback latency, JSON out
#include <signal.h>
#include <sys/mman.h>
#include <unistd.h>
#if defined(__linux__)
#include <sched.h>
#endif

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cmath>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <string>
#include <thread>

#include "audio.h"
#include "catalog.h"
#include "host.h"
#include "jacks.h"
#include "server.h"

using namespace bb;

namespace {

std::atomic<bool> g_quit{false};
void onSignal(int) { g_quit.store(true); }

void logf(const char* fmt, ...) __attribute__((format(printf, 1, 2)));
void logf(const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    std::vfprintf(stderr, fmt, ap);
    va_end(ap);
    std::fputc('\n', stderr);
    std::fflush(stderr);
}

struct Options {
    AudioConfig audio;
    std::string socket = "/run/bassbox/engine.sock";
    std::string stateDir = "/var/lib/bassbox";
    std::string factoryDir = "/opt/bassbox/presets/factory";
    std::string jacksConf = "/opt/bassbox/config/jacks.conf";
    bool nullAudio = false;
    bool testEffects = false;
    bool autosave = true;
    float startupMute = 0.5f;
    std::string controlCpus = "0-2";
    float latencyTest = 0.f;  // > 0: measure loopback latency for this many seconds and exit
};

// Loopback latency probe: replaces the Processor in the RT loop (same ALSA path and buffer
// arithmetic as the engine). Every 0.25 s it plays a Hann-windowed 300-6000 Hz chirp at
// -12 dBFS and records the input; the control thread then cross-correlates. The input is never
// routed to the output, so this can't feed back.
struct LatencyTest {
    std::vector<float> chirp, rec;
    size_t pos = 0, interval = 12000;
    static void hook(void* c, const float* in, float* out, int n) {
        auto* t = static_cast<LatencyTest*>(c);
        for (int i = 0; i < n; ++i, ++t->pos) {
            if (t->pos < t->rec.size()) t->rec[t->pos] = in[i];
            const size_t k = t->pos % t->interval;
            out[i] = (t->pos >= t->interval && k < t->chirp.size()) ? t->chirp[k] : 0.f;
        }
    }
};

int runLatencyTest(const Options& o) {
    Processor proc(float(o.audio.rate), kMaxBlock);
    AudioStats stats;
    LatencyTest lt;
    const float sr = float(o.audio.rate);
    lt.interval = size_t(sr / 4);
    const size_t L = 512;
    lt.chirp.resize(L);
    for (size_t j = 0; j < L; ++j) {
        const double t = double(j) / sr, T = double(L) / sr;
        const double ph = 2 * M_PI * (300.0 * t + 0.5 * (6000.0 - 300.0) / T * t * t);
        lt.chirp[j] = float(0.25 * std::sin(ph) * (0.5 - 0.5 * std::cos(2 * M_PI * double(j) / double(L - 1))));
    }
    lt.rec.assign(size_t(sr * o.latencyTest), 0.f);
    AudioConfig ac = o.audio;
    ac.testHook = &LatencyTest::hook;
    ac.testCtx = &lt;
    auto audio = o.nullAudio ? makeNullAudio() : makeAlsaAudio();
    std::string err;
    if (!audio || !audio->open(ac, stats, err) || !audio->start(proc, err)) {
        logf("latency test: audio: %s", err.c_str());
        return 2;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(int(o.latencyTest * 1000) + 200));
    audio->stop();
    double e = 0;
    for (float v : lt.chirp) e += double(v) * v;
    const size_t maxLag = size_t(sr / 10);
    std::vector<double> lat, gain, snr;
    for (size_t s0 = lt.interval; s0 + maxLag + L < lt.rec.size(); s0 += lt.interval) {
        std::vector<double> c(maxLag);
        for (size_t lag = 0; lag < maxLag; ++lag) {
            double acc = 0;
            for (size_t j = 0; j < L; ++j) acc += double(lt.rec[s0 + lag + j]) * lt.chirp[j];
            c[lag] = std::fabs(acc);
        }
        const size_t pk = size_t(std::max_element(c.begin(), c.end()) - c.begin());
        std::vector<double> sorted = c;
        std::nth_element(sorted.begin(), sorted.begin() + long(sorted.size() / 2), sorted.end());
        const double med = std::max(1e-12, sorted[sorted.size() / 2]);
        double frac = 0;
        if (pk > 0 && pk + 1 < maxLag) {
            const double a = c[pk - 1], b = c[pk], d = c[pk + 1], den = a - 2 * b + d;
            if (std::fabs(den) > 1e-15) frac = 0.5 * (a - d) / den;
        }
        snr.push_back(20 * std::log10(c[pk] / med));
        if (c[pk] / med > 30) {
            lat.push_back(double(pk) + frac);
            gain.push_back(20 * std::log10(c[pk] / e));
        }
    }
    auto median = [](std::vector<double> v) {
        if (v.empty()) return 0.0;
        std::sort(v.begin(), v.end());
        return v[v.size() / 2];
    };
    json r{{"period", stats.info.value("period", 0)}, {"periods", stats.info.value("periods", 0)},
           {"format", stats.info.value("format", "")}, {"probes", snr.size()}, {"valid", lat.size()},
           {"xruns", stats.xruns.load()}, {"snr_db", jnum(float(median(snr)))}};
    if (lat.empty()) {
        r["error"] = "no probe on the input: is the loopback cable connected (HAT headphone out -> LINE IN)?";
    } else {
        const double m = median(lat);
        r["latency_frames"] = jnum(float(m));
        r["latency_ms"] = jnum(float(1000.0 * m / sr));
        r["latency_min_frames"] = jnum(float(*std::min_element(lat.begin(), lat.end())));
        r["latency_max_frames"] = jnum(float(*std::max_element(lat.begin(), lat.end())));
        r["gain_db"] = jnum(float(median(gain)));
        r["nominal_frames"] = stats.info.value("nominal_latency_frames", 0);
    }
    std::printf("%s\n", r.dump().c_str());
    return lat.empty() ? 1 : 0;
}

std::string trim(const std::string& s) {
    const auto a = s.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) return "";
    return s.substr(a, s.find_last_not_of(" \t\r\n") - a + 1);
}

bool setOpt(Options& o, const std::string& k, const std::string& v) {
    try {
        if (k == "device") o.audio.device = v;
        else if (k == "rate") o.audio.rate = unsigned(std::stoul(v));
        else if (k == "period") o.audio.period = unsigned(std::stoul(v));
        else if (k == "periods") o.audio.periods = unsigned(std::stoul(v));
        else if (k == "format") o.audio.format = v;
        else if (k == "cpu") o.audio.cpu = std::stoi(v);
        else if (k == "priority") o.audio.priority = std::stoi(v);
        else if (k == "socket") o.socket = v;
        else if (k == "state-dir" || k == "state_dir") o.stateDir = v;
        else if (k == "factory-dir" || k == "factory_dir") o.factoryDir = v;
        else if (k == "jacks-conf" || k == "jacks_conf") o.jacksConf = v;
        else if (k == "null-input" || k == "null_input") o.audio.nullInput = v;
        else if (k == "startup-mute" || k == "startup_mute") o.startupMute = std::stof(v);
        else if (k == "control-cpus" || k == "control_cpus") o.controlCpus = v;
        else if (k == "latency-test") o.latencyTest = std::stof(v);
        else return false;
    } catch (...) {
        logf("bad value for %s: '%s'", k.c_str(), v.c_str());
        return false;
    }
    return true;
}

bool loadConfig(Options& o, const std::string& path) {
    std::ifstream f(path);
    if (!f) {
        logf("config %s not readable, using defaults", path.c_str());
        return false;
    }
    std::string line;
    while (std::getline(f, line)) {
        if (auto h = line.find('#'); h != std::string::npos) line.resize(h);
        const auto eq = line.find('=');
        if (eq == std::string::npos) continue;
        const std::string k = trim(line.substr(0, eq)), v = trim(line.substr(eq + 1));
        if (!k.empty() && !setOpt(o, k, v)) logf("config %s: unknown key '%s'", path.c_str(), k.c_str());
    }
    return true;
}

void pinControlThread(const std::string& cpus) {
#if defined(__linux__)
    cpu_set_t set;
    CPU_ZERO(&set);
    int a = -1, b = -1;
    if (std::sscanf(cpus.c_str(), "%d-%d", &a, &b) == 2) {
        for (int c = a; c <= b; ++c) CPU_SET(c, &set);
    } else if (std::sscanf(cpus.c_str(), "%d", &a) == 1) {
        CPU_SET(a, &set);
    } else {
        return;
    }
    if (sched_setaffinity(0, sizeof set, &set) != 0) logf("control thread affinity %s failed", cpus.c_str());
#else
    (void)cpus;
#endif
}

void usage() {
    std::fprintf(stderr,
                 "usage: bassbox-engine [--config FILE] [--device D] [--period N] [--periods N]\n"
                 "         [--format auto|S32_LE|S24_LE|S16_LE] [--cpu N] [--priority N] [--socket PATH]\n"
                 "         [--state-dir DIR] [--factory-dir DIR] [--jacks-conf FILE] [--startup-mute S]\n"
                 "         [--null-audio [--null-input WAV]] [--test-effects] [--no-autosave]\n");
}

}  // namespace

int main(int argc, char** argv) {
    Options o;
    // config file first, command line overrides it
    for (int i = 1; i + 1 < argc; ++i)
        if (std::strcmp(argv[i], "--config") == 0) loadConfig(o, argv[i + 1]);
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--config") {
            ++i;
        } else if (a == "--null-audio") {
            o.nullAudio = true;
        } else if (a == "--test-effects") {
            o.testEffects = true;
        } else if (a == "--no-autosave") {
            o.autosave = false;
        } else if (a == "-h" || a == "--help") {
            usage();
            return 0;
        } else if (a.rfind("--", 0) == 0 && i + 1 < argc && setOpt(o, a.substr(2), argv[i + 1])) {
            ++i;
        } else {
            logf("unknown argument %s", a.c_str());
            usage();
            return 64;
        }
    }
    if (o.audio.period < 16 || o.audio.period > unsigned(kMaxBlock) || o.audio.periods < 2 || o.audio.periods > 8) {
        logf("period must be 16..%d frames and periods 2..8", kMaxBlock);
        return 64;
    }

    if (o.latencyTest > 0.f) {
        mlockall(MCL_CURRENT | MCL_FUTURE);
        return runLatencyTest(o);
    }
    signal(SIGTERM, onSignal);
    signal(SIGINT, onSignal);
    signal(SIGPIPE, SIG_IGN);
    pinControlThread(o.controlCpus);
    // Lock everything we have and will map (effect buffers allocated later included), so the
    // audio thread never page-faults. Failure is not fatal (dev runs without privileges).
    if (mlockall(MCL_CURRENT | MCL_FUTURE) != 0) logf("mlockall failed: %s (continuing)", std::strerror(errno));

    Processor proc(float(o.audio.rate), kMaxBlock);
    proc.setStartupMute(o.startupMute);
    AudioStats stats;
    HostConfig hc;
    hc.sampleRate = float(o.audio.rate);
    hc.maxBlock = kMaxBlock;
    hc.stateDir = o.stateDir;
    hc.factoryDir = o.factoryDir;
    hc.autosave = o.autosave;
    hc.testEffects = o.testEffects;
    Host host(proc, hc, &stats);
    {
        const std::string log = host.start();
        std::fprintf(stderr, "%s", log.c_str());
    }

    // ---- audio: wait (bounded) for the card; systemd restarts us if it never shows up
    std::unique_ptr<AudioBackend> audio = o.nullAudio ? makeNullAudio() : makeAlsaAudio();
    if (!audio) {
        logf("built without ALSA: use --null-audio");
        return 2;
    }
    std::string err;
    bool opened = false;
    for (int tries = 0; tries < 30 && !g_quit.load(); ++tries) {
        if (audio->open(o.audio, stats, err)) {
            opened = true;
            break;
        }
        if (tries == 0) logf("audio open failed (%s); retrying for 30 s", err.c_str());
        std::this_thread::sleep_for(std::chrono::seconds(1));
    }
    if (!opened) {
        logf("audio: giving up: %s", err.c_str());
        return 2;
    }
    if (!audio->start(proc, err)) {
        logf("audio start failed: %s", err.c_str());
        return 2;
    }
    logf("audio: %s", stats.info.dump().c_str());

    // ---- jack sense
    JacksConfig jc;
    if (!loadJacksConfig(o.jacksConf, jc, err) && !err.empty()) logf("jacks: %s", err.c_str());
    Jacks jacks;
    if (!jacks.open(jc, err)) logf("jacks: %s (assuming both plugged)", err.c_str());
    {
        bool in = true, out = true;
        jacks.update(nowNs(), in, out);
        host.setJacks(in, out, jc.muteOnUnplug);
        logf("jacks: input %s, output %s", in ? "plugged" : "empty", out ? "plugged" : "empty");
    }

    // ---- socket
    Server server;
    if (!server.listen(o.socket, err)) {
        logf("socket: %s", err.c_str());
        audio->stop();
        return 2;
    }
    logf("listening on %s", o.socket.c_str());

    // ---- control loop
    std::vector<pollfd> fds;
    uint32_t lastXruns = 0;
    uint64_t lastFrames = 0;
    int64_t lastProgress = nowNs(), lastXrunLog = 0;
    int rc = 0;
    while (!g_quit.load()) {
        fds.clear();
        server.addPollFds(fds);
        const int jfd = jacks.fd();
        if (jfd >= 0) fds.push_back({jfd, POLLIN, 0});
        ::poll(fds.data(), nfds_t(fds.size()), 10);
        const int64_t now = nowNs();
        for (auto& p : fds)
            if (p.fd == jfd && (p.revents & POLLIN)) jacks.onReadable(now);
        server.process(fds, host);

        bool in, out;
        if (jacks.update(now, in, out) && host.setJacks(in, out, jc.muteOnUnplug)) {
            logf("jacks: input %s, output %s", in ? "plugged" : "empty", out ? "plugged" : "empty");
            json ev = host.jacksJson();
            ev["event"] = "jacks";
            server.broadcast(ev);
        }
        host.tick(now);
        server.tick(now, host);

        // xrun log (rate limited) and watchdog: the audio thread must keep making progress
        const uint32_t x = stats.xruns.load();
        if (x != lastXruns && now - lastXrunLog > 1'000'000'000) {
            logf("xruns: %u (+%u)", x, x - lastXruns);
            lastXruns = x;
            lastXrunLog = now;
        }
        const uint64_t frames = proc.meters.frames.load(std::memory_order_relaxed);
        if (frames != lastFrames) {
            lastFrames = frames;
            lastProgress = now;
        } else if (now - lastProgress > 3'000'000'000LL) {
            logf("watchdog: audio thread stalled for 3 s, exiting for a restart");
            rc = 3;
            break;
        }
        if (stats.failed.load()) {
            logf("audio backend failed, exiting for a restart");
            rc = 3;
            break;
        }
    }
    logf("shutting down");
    host.flushAutosave();
    audio->stop();
    server.closeAll();
    return rc;
}
