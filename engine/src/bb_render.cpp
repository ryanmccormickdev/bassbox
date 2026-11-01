// bb-render: offline-render a WAV through a chain, with the live engine's code paths
// (Host builds/publishes the chain exactly as for socket commands; Processor runs the same
// per-slot mix/enable/tails ramps, chain crossfades, DC blocker, gains and limiter).
//
//   bb-render [options] IN.wav OUT.wav
//     --preset FILE     State/preset JSON (chain + master); default: empty chain
//     --json TEXT       same, inline
//     --events FILE     JSON array of timed commands, the socket protocol plus "t" (seconds):
//                         [{"t": 2.0, "cmd": "set_enabled", "slot": 0, "enabled": false},
//                          {"t": 3.0, "cmd": "set_param", "uid": 1, "param": "time", "value": 300}]
//                       "slot": N (0-based position at that moment) may replace "uid".
//                       Slots loaded from the preset get uids 1..N in chain order.
//     --block N         processing block size (default 64; the engine uses its ALSA period)
//     --tail S          render S seconds of silence after the input (default 0)
//     --channel N       input channel (default 0 = left)
//     --bits 16|24|32   output format (default 32 = float)
//     --stereo          write the output on two channels
//     --no-dcblock      skip the host input DC blocker
//     --no-limiter      skip the host output soft limiter
//     --quiet           no summary on stderr
// Exit status 0 = ok, 1 = usage/IO error, 2 = the chain produced NaN/Inf (blocks were zeroed).
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

#include "catalog.h"
#include "host.h"
#include "presets.h"
#include "processor.h"
#include "rt.h"
#include "wav.h"

using namespace bb;

static void usage() {
    std::fprintf(stderr,
                 "usage: bb-render [--preset FILE | --json TEXT] [--events FILE] [--block N] [--tail S]\n"
                 "                 [--channel N] [--bits 16|24|32] [--stereo] [--no-dcblock] [--no-limiter]\n"
                 "                 [--quiet] IN.wav OUT.wav\n");
}

static double dbfs(double v) { return v > 0 ? 20.0 * std::log10(v) : -999.0; }

int main(int argc, char** argv) {
    std::string presetFile, presetText, eventsFile, inPath, outPath;
    int block = 64, channel = 0, bits = 32;
    double tail = 0.0;
    bool stereo = false, dc = true, limiter = true, quiet = false;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto next = [&]() -> std::string {
            if (i + 1 >= argc) {
                usage();
                std::exit(1);
            }
            return argv[++i];
        };
        if (a == "--preset") presetFile = next();
        else if (a == "--json") presetText = next();
        else if (a == "--events") eventsFile = next();
        else if (a == "--block") block = std::atoi(next().c_str());
        else if (a == "--tail") tail = std::atof(next().c_str());
        else if (a == "--channel") channel = std::atoi(next().c_str());
        else if (a == "--bits") bits = std::atoi(next().c_str());
        else if (a == "--stereo") stereo = true;
        else if (a == "--no-dcblock") dc = false;
        else if (a == "--no-limiter") limiter = false;
        else if (a == "--quiet") quiet = true;
        else if (a == "-h" || a == "--help") {
            usage();
            return 0;
        } else if (!a.empty() && a[0] == '-') {
            std::fprintf(stderr, "unknown option %s\n", a.c_str());
            usage();
            return 1;
        } else if (inPath.empty()) inPath = a;
        else if (outPath.empty()) outPath = a;
        else {
            usage();
            return 1;
        }
    }
    if (inPath.empty() || outPath.empty() || block < 1 || block > kMaxBlock) {
        usage();
        return 1;
    }
    enableFlushToZero();  // same FP mode as the engine's audio thread

    std::string err;
    Wav in;
    if (!readWav(inPath, in, err)) {
        std::fprintf(stderr, "bb-render: %s\n", err.c_str());
        return 1;
    }
    json preset = json::object();
    if (!presetFile.empty()) {
        std::string txt;
        if (!readFile(presetFile, txt)) {
            std::fprintf(stderr, "bb-render: cannot read %s\n", presetFile.c_str());
            return 1;
        }
        preset = json::parse(txt, nullptr, false);
    } else if (!presetText.empty()) {
        preset = json::parse(presetText, nullptr, false);
    }
    if (preset.is_discarded() || !preset.is_object()) {
        std::fprintf(stderr, "bb-render: preset is not a JSON object\n");
        return 1;
    }
    json events = json::array();
    if (!eventsFile.empty()) {
        std::string txt;
        if (!readFile(eventsFile, txt)) {
            std::fprintf(stderr, "bb-render: cannot read %s\n", eventsFile.c_str());
            return 1;
        }
        events = json::parse(txt, nullptr, false);
        if (events.is_discarded() || !events.is_array()) {
            std::fprintf(stderr, "bb-render: events must be a JSON array\n");
            return 1;
        }
        std::stable_sort(events.begin(), events.end(),
                         [](const json& a, const json& b) { return a.value("t", 0.0) < b.value("t", 0.0); });
    }

    const float sr = float(in.rate);
    Processor proc(sr, kMaxBlock);
    proc.setStartupMute(0.f);
    proc.ctl.dcBlock.store(dc);
    proc.ctl.limiter.store(limiter);
    HostConfig hc;
    hc.sampleRate = sr;
    hc.autosave = false;
    hc.testEffects = true;
    Host host(proc, hc, nullptr);
    std::vector<std::string> warnings;
    if (!host.loadState(preset, preset.value("name", std::string()), err, &warnings)) {
        std::fprintf(stderr, "bb-render: %s\n", err.c_str());
        return 1;
    }
    for (auto& w : warnings) std::fprintf(stderr, "bb-render: warning: %s\n", w.c_str());

    std::vector<float> x = in.channel(channel);
    const size_t inFrames = x.size();
    x.resize(inFrames + size_t(std::max(0.0, tail) * sr), 0.f);
    std::vector<float> y(x.size(), 0.f);

    size_t ev = 0;
    double cpuSum = 0, cpuMax = 0;
    size_t blocks = 0;
    const double blockNs = 1e9 * double(block) / double(sr);
    for (size_t pos = 0; pos < x.size(); pos += size_t(block)) {
        const double t = double(pos) / double(sr);
        while (ev < events.size() && events[ev].value("t", 0.0) <= t + 1e-9) {
            json req = events[ev++];
            if (req.contains("slot") && !req.contains("uid")) {
                const auto u = host.uids();
                const int k = req["slot"].get<int>();
                if (k >= 0 && k < int(u.size())) req["uid"] = u[size_t(k)];
            }
            req.erase("t");
            req.erase("slot");
            CommandContext ctx;
            json resp = host.handle(req, ctx);
            if (!resp.value("ok", false) || !quiet)
                std::fprintf(stderr, "bb-render: t=%.3f %s -> %s\n", t, req.dump().c_str(), resp.dump().c_str());
        }
        host.tick(int64_t(t * 1e9));
        const int n = int(std::min<size_t>(size_t(block), x.size() - pos));
        const int64_t t0 = nowNs();
        proc.process(x.data() + pos, y.data() + pos, n);
        const double dt = double(nowNs() - t0);
        cpuSum += dt;
        cpuMax = std::max(cpuMax, dt);
        ++blocks;
    }
    host.tick(int64_t(double(x.size()) / sr * 1e9));

    Wav out;
    out.rate = in.rate;
    out.channels = stereo ? 2 : 1;
    if (stereo) {
        out.data.resize(y.size() * 2);
        for (size_t i = 0; i < y.size(); ++i) out.data[2 * i] = out.data[2 * i + 1] = y[i];
    } else {
        out.data = y;
    }
    if (!writeWav(outPath, out, bits, err)) {
        std::fprintf(stderr, "bb-render: %s\n", err.c_str());
        return 1;
    }
    const uint32_t nan = proc.meters.nanResets.load();
    if (!quiet) {
        double ip = 0, is = 0, op = 0, os = 0;
        for (size_t i = 0; i < inFrames; ++i) {
            ip = std::max(ip, double(std::fabs(x[i])));
            is += double(x[i]) * x[i];
        }
        for (float v : y) {
            op = std::max(op, double(std::fabs(v)));
            os += double(v) * v;
        }
        std::fprintf(stderr,
                     "bb-render: %zu frames @ %d Hz, block %d, %zu slots | in peak %.1f dBFS rms %.1f | out peak %.1f "
                     "dBFS rms %.1f | cpu/block avg %.2f%% max %.2f%% of real time | nan resets %u\n",
                     y.size(), in.rate, block, host.uids().size(), dbfs(ip), dbfs(std::sqrt(is / double(std::max<size_t>(1, inFrames)))),
                     dbfs(op), dbfs(std::sqrt(os / double(std::max<size_t>(1, y.size())))),
                     100.0 * cpuSum / double(std::max<size_t>(1, blocks)) / blockNs, 100.0 * cpuMax / blockNs, nan);
    }
    return nan ? 2 : 0;
}
