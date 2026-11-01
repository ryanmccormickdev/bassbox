// bb-selftest: engine host tests (no audio hardware). Exit 0 = all pass.
//   chain edits are click-free (add/remove/move/enable/mix/bypass/load), tails ring out and
//   stop, surviving uids keep their state, NaN guard, limiter, presets, slot GC, tuner.
#include <unistd.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "catalog.h"
#include "host.h"
#include "presets.h"
#include "processor.h"
#include "tuner.h"

using namespace bb;

namespace {

// ---- test-only effects (registered only in this binary)
class Echo final : public Effect {  // 100 ms single echo, tails
public:
    void prepare(float sr, int) override { buf_.assign(size_t(sr * 0.1f), 0.f); }
    void reset() override { std::fill(buf_.begin(), buf_.end(), 0.f); pos_ = 0; }
    void setParam(int, float v) override { fb_ = v; }
    void process(float* x, int n) override {
        for (int i = 0; i < n; ++i) {
            const float d = buf_[pos_];
            buf_[pos_] = x[i] + d * fb_;
            x[i] = x[i] + d;
            pos_ = (pos_ + 1) % buf_.size();
        }
    }
    std::vector<float> buf_;
    size_t pos_ = 0;
    float fb_ = 0.5f;
};
class Nan final : public Effect {
public:
    void prepare(float, int) override {}
    void reset() override {}
    void setParam(int, float v) override { on_ = v > 0.5f; }
    void process(float* x, int n) override {
        if (on_) x[n / 2] = std::nanf("");
    }
    bool on_ = false;
};
ParamSpec spec(const char* id, float mn, float mx, float d) {
    ParamSpec p;
    p.id = id;
    p.name = id;
    p.min = mn;
    p.max = mx;
    p.def = d;
    return p;
}
BB_REGISTER_EFFECT({"test_echo", "Echo", "time", "test", {spec("fb", 0, 0.95f, 0.5f)}, true,
                    [] { return std::make_unique<Echo>(); }});
struct ReadyProbe {
    int calls = 0;
    float paramAtReady = -1.f;
    bool processedBeforeReady = false;
} g_ready;
class ReadyFx final : public Effect {
public:
    void prepare(float, int) override {}
    void ready() override {
        ++g_ready.calls;
        g_ready.paramAtReady = p_;
    }
    void reset() override {}
    void setParam(int, float v) override { p_ = v; }
    void process(float*, int) override {
        if (!g_ready.calls) g_ready.processedBeforeReady = true;
    }
    float p_ = 0.f;
};
BB_REGISTER_EFFECT({"test_ready", "Ready", "utility", "test", {spec("p", 0, 10, 1)}, false,
                    [] { return std::make_unique<ReadyFx>(); }});
BB_REGISTER_EFFECT({"test_nan", "NaN", "utility", "test", {spec("on", 0, 1, 0)}, false,
                    [] { return std::make_unique<Nan>(); }});

int g_fail = 0, g_pass = 0;
void check(bool ok, const char* what, double a = 0, double b = 0) {
    if (ok) {
        ++g_pass;
        std::printf("  ok    %s\n", what);
    } else {
        ++g_fail;
        std::printf("  FAIL  %s  (%g vs %g)\n", what, a, b);
    }
}

constexpr float SR = 48000.f;
constexpr int B = 64;

struct Rig {
    Processor proc{SR, kMaxBlock};
    Host host;
    double t = 0;
    explicit Rig(const HostConfig& hc = defaults()) : host(proc, hc, nullptr) {
        proc.setStartupMute(0.f);
        proc.ctl.dcBlock.store(false);
        proc.ctl.limiter.store(false);
    }
    static HostConfig defaults() {
        HostConfig h;
        h.sampleRate = SR;
        h.autosave = false;
        h.testEffects = true;
        return h;
    }
    json cmd(const json& j) {
        CommandContext c;
        return host.handle(j, c);
    }
    // run `frames` of input through, ticking the host every block like the control loop
    void run(const float* in, float* out, size_t frames) {
        for (size_t p = 0; p < frames; p += B) {
            host.tick(int64_t(t * 1e9));
            const int n = int(std::min<size_t>(B, frames - p));
            proc.process(in + p, out + p, n);
            t += n / SR;
        }
    }
};

std::vector<float> sine(float f, float a, size_t n) {
    std::vector<float> v(n);
    for (size_t i = 0; i < n; ++i) v[i] = a * std::sin(2.f * float(M_PI) * f * float(i) / SR);
    return v;
}

// largest |y[i+1] - y[i]|: a click shows as a jump far above the signal's own slope
double maxStep(const std::vector<float>& y, size_t from = 1) {
    double m = 0;
    for (size_t i = std::max<size_t>(from, 1); i < y.size(); ++i) m = std::max(m, double(std::fabs(y[i] - y[i - 1])));
    return m;
}

void testPassthrough() {
    std::puts("passthrough");
    Rig r;
    auto x = sine(220, 0.5f, 4800);
    std::vector<float> y(x.size());
    r.run(x.data(), y.data(), x.size());
    double e = 0;
    for (size_t i = 0; i < x.size(); ++i) e = std::max(e, double(std::fabs(x[i] - y[i])));
    check(e == 0.0, "empty chain is bit-exact (DC blocker/limiter off)", e, 0);
    r.cmd({{"cmd", "add"}, {"type", "host_gain"}});
    r.run(x.data(), y.data(), x.size());  // fade-in happens here
    r.run(x.data(), y.data(), x.size());
    e = 0;
    for (size_t i = 0; i < x.size(); ++i) e = std::max(e, double(std::fabs(x[i] - y[i])));
    check(e == 0.0, "host_gain at 0 dB is bit-exact once faded in", e, 0);
}

void testClickFree() {
    std::puts("click-free edits (100 Hz sine at -6 dBFS, ramps <= 10 ms)");
    Rig r;
    const size_t N = 48000;
    auto x = sine(100, 0.5f, N);
    std::vector<float> y(N);
    const double natural = 2 * M_PI * 100 / SR * 0.5;  // max slope of the input
    const double limit = 2.5 * natural;
    r.cmd({{"cmd", "add"}, {"type", "host_gain"}, {"params", {{"gain", -6}}}});
    r.run(x.data(), y.data(), 9600);
    struct Step {
        const char* what;
        json cmd;
    };
    std::vector<Step> steps = {
        {"add at front (+6 dB)", {{"cmd", "add"}, {"type", "host_gain"}, {"index", 0}, {"params", {{"gain", 6}}}}},
        {"move", {{"cmd", "move"}, {"uid", 1}, {"index", 0}}},
        {"disable", {{"cmd", "set_enabled"}, {"uid", 2}, {"enabled", false}}},
        {"enable", {{"cmd", "set_enabled"}, {"uid", 2}, {"enabled", true}}},
        {"mix 0.3", {{"cmd", "set_mix"}, {"uid", 2}, {"mix", 0.3}}},
        {"bypass on", {{"cmd", "set_master"}, {"bypass", true}}},
        {"bypass off", {{"cmd", "set_master"}, {"bypass", false}}},
        {"remove", {{"cmd", "remove"}, {"uid", 1}}},
        {"load_state (parallel crossfade)",
         {{"cmd", "load_state"},
          {"state", {{"chain", json::array({{{"type", "host_gain"}, {"params", {{"gain", -12}}}}})}}}}},
        {"out gain -20 dB", {{"cmd", "set_master"}, {"out_gain_db", -20}}},
    };
    size_t pos = 9600;
    for (auto& s : steps) {
        json resp = r.cmd(s.cmd);
        if (!resp.value("ok", false)) std::printf("    %s -> %s\n", s.what, resp.dump().c_str());
        r.run(x.data() + pos, y.data() + pos, 2400);
        std::vector<float> seg(y.begin() + long(pos) - 1, y.begin() + long(pos) + 2400);
        const double m = maxStep(seg);
        char b[96];
        std::snprintf(b, sizeof b, "%s: max step %.4f (limit %.4f)", s.what, m, limit);
        check(m < limit, b, m, limit);
        pos += 2400;
    }
}

void testTails() {
    std::puts("tails");
    Rig r;
    r.cmd({{"cmd", "add"}, {"type", "test_echo"}, {"params", {{"fb", 0.5}}}});
    const size_t N = 48000 * 7;
    std::vector<float> x(N, 0.f), y(N, 0.f);
    x[4800] = 1.f;  // impulse at 0.1 s, then disable at 0.15 s
    r.run(x.data(), y.data(), 7200);
    r.cmd({{"cmd", "set_enabled"}, {"uid", 1}, {"enabled", false}});
    r.run(x.data() + 7200, y.data() + 7200, N - 7200);
    // echoes at 0.2 s (0.5? the dry impulse itself is before the disable), 0.3 s (0.5^1 fb) ...
    const float e1 = std::fabs(y[4800 + 4800]), e2 = std::fabs(y[4800 + 9600]);
    check(e1 > 0.4f && e2 > 0.2f, "disabled tails effect keeps ringing (echo 1, 2)", e1, e2);
    double late = 0;
    for (size_t i = size_t(SR * 6.0); i < N; ++i) late = std::max(late, double(std::fabs(y[i])));
    check(late < 1e-6, "tail stops after it decays / 5 s", late, 0);

    // surviving uid keeps state across a move
    Rig q;
    q.cmd({{"cmd", "add"}, {"type", "host_gain"}});
    q.cmd({{"cmd", "add"}, {"type", "test_echo"}, {"params", {{"fb", 0.0}}}});
    std::vector<float> a(48000, 0.f), b(48000, 0.f);
    a[9600] = 1.f;
    q.run(a.data(), b.data(), 9600 + 960);   // impulse in, 20 ms later
    q.cmd({{"cmd", "move"}, {"uid", 2}, {"index", 0}});
    q.run(a.data() + 10560, b.data() + 10560, 48000 - 10560);
    check(std::fabs(b[9600 + 4800]) > 0.9f, "moved delay keeps its buffer (echo survives the move)", b[9600 + 4800], 1);
}

void testGuards() {
    std::puts("guards");
    Rig r;
    r.cmd({{"cmd", "add"}, {"type", "test_nan"}, {"params", {{"on", 1}}}});
    auto x = sine(220, 0.5f, 4800);
    std::vector<float> y(x.size());
    r.run(x.data(), y.data(), x.size());
    bool finite = true;
    for (float v : y) finite = finite && std::isfinite(v);
    check(finite && r.proc.meters.nanResets.load() > 0, "NaN from an effect is caught (output finite, counted)");

    Rig l;
    l.proc.ctl.limiter.store(true);
    auto hot = sine(100, 2.0f, 4800);
    auto soft = sine(100, 0.89f, 4800);
    std::vector<float> yh(hot.size()), ys(soft.size());
    l.run(hot.data(), yh.data(), hot.size());
    l.run(soft.data(), ys.data(), soft.size());
    double pk = 0, dev = 0;
    for (float v : yh) pk = std::max(pk, double(std::fabs(v)));
    for (size_t i = 0; i < soft.size(); ++i) dev = std::max(dev, double(std::fabs(ys[i] - soft[i])));
    check(pk <= 1.0, "limiter keeps +6 dBFS input under full scale", pk, 1);
    check(dev == 0.0, "limiter is transparent at -1.0 dBFS", dev, 0);

    Rig c;
    json bad = c.cmd({{"cmd", "set_param"}, {"uid", 99}, {"param", "gain"}, {"value", 1}});
    check(!bad.value("ok", true), "bad uid -> error response");
    c.cmd({{"cmd", "add"}, {"type", "host_gain"}});
    json cl = c.cmd({{"cmd", "set_param"}, {"uid", 1}, {"param", "gain"}, {"value", 1000}});
    check(cl.value("value", 0.0) == 24.0, "set_param clamps to the param range", cl.value("value", 0.0), 24);
}

void testReadyAndLooper() {
    std::puts("effect ready() + looper keeps its loop");
    Rig r;
    r.cmd({{"cmd", "load_state"}, {"state", {{"chain", json::array({{{"type", "test_ready"}, {"params", {{"p", 7}}}}})}}}});
    std::vector<float> z(9600, 0.f), y(9600);
    r.run(z.data(), y.data(), z.size());
    check(g_ready.calls == 1 && g_ready.paramAtReady == 7.f && !g_ready.processedBeforeReady,
          "ready() called once, with the preset's params, before the first process()", g_ready.paramAtReady, 7);

    if (!findEffect("looper")) {
        std::puts("  (looper not built, skipped)");
        return;
    }
    check(!findEffect("looper")->tails, "looper registered tails=false");
    Rig q;
    q.cmd({{"cmd", "add"}, {"type", "looper"}});
    auto rms = [](const std::vector<float>& v, size_t from) {
        double s = 0;
        for (size_t i = from; i < v.size(); ++i) s += double(v[i]) * v[i];
        return std::sqrt(s / double(v.size() - from));
    };
    auto x = sine(220, 0.3f, 48000);
    std::vector<float> out(48000), sil(48000 * 6, 0.f), o6(sil.size()), s1(24000, 0.f), o1(s1.size());
    q.cmd({{"cmd", "set_param"}, {"uid", 1}, {"param", "state"}, {"value", "Record"}});
    q.run(x.data(), out.data(), x.size());
    q.cmd({{"cmd", "set_param"}, {"uid", 1}, {"param", "state"}, {"value", "Play"}});
    q.run(s1.data(), o1.data(), s1.size());
    const double playing = rms(o1, 2400);
    q.cmd({{"cmd", "set_enabled"}, {"uid", 1}, {"enabled", false}});
    q.run(sil.data(), o6.data(), sil.size());  // 6 s bypassed: longer than the 5 s tail limit
    double byp = 0;
    for (size_t i = 4800; i < o6.size(); ++i) byp = std::max(byp, double(std::fabs(o6[i])));
    q.cmd({{"cmd", "set_enabled"}, {"uid", 1}, {"enabled", true}});
    q.run(s1.data(), o1.data(), s1.size());
    const double again = rms(o1, 2400);
    char b[128];
    std::snprintf(b, sizeof b, "loop plays (%.3f), silent while bypassed (%.1e), plays again after 6 s bypass (%.3f)", playing, byp, again);
    check(playing > 0.1 && byp < 1e-4 && again > 0.1, b, again, 0.1);
    q.cmd({{"cmd", "set_param"}, {"uid", 1}, {"param", "state"}, {"value", "Clear"}});
    q.run(s1.data(), o1.data(), s1.size());
    q.cmd({{"cmd", "set_param"}, {"uid", 1}, {"param", "state"}, {"value", "Play"}});
    q.run(s1.data(), o1.data(), s1.size());
    check(rms(o1, 0) < 1e-6, "Clear erases the loop", rms(o1, 0), 0);
}

void testTone() {
    std::puts("test tone");
    Rig r;
    r.proc.ctl.inputMute.store(true);  // IN jack unplugged: the tone must still play
    json resp = r.cmd({{"cmd", "test_tone"}, {"on", true}, {"hz", 1000}});
    check(resp.value("ok", false) && resp.value("db", 0.0) == -18.0, "test_tone on (defaults -18 dB)");
    const size_t N = 48000;
    std::vector<float> z(N, 0.f), y(N);
    r.run(z.data(), y.data(), N);
    double ss = 0;
    int zc = 0;
    for (size_t i = 24000; i < N; ++i) {
        ss += double(y[i]) * y[i];
        zc += (y[i - 1] < 0.f) != (y[i] < 0.f);
    }
    const double rms = 20 * std::log10(std::sqrt(ss / 24000));
    check(std::fabs(rms - (-18 - 3.0103)) < 0.1, "tone level -18 dBFS peak with the input muted", rms, -21.01);
    check(std::abs(zc - 1000) <= 2, "tone frequency 1 kHz (zero crossings in 0.5 s)", zc, 1000);
    check(std::fabs(y[0]) < 0.001 && maxStep(y) < 0.05, "tone fades in (no click)", y[0], 0);
    check(r.cmd({{"cmd", "meters"}}).value("test_tone", false), "meters.test_tone true while on");
    std::vector<float> big(48000 * 30, 0.f), yb(big.size());
    r.run(big.data(), yb.data(), big.size());  // 31 s total
    double tail = 0;
    for (size_t i = yb.size() - 4800; i < yb.size(); ++i) tail = std::max(tail, double(std::fabs(yb[i])));
    check(tail == 0.0 && !r.cmd({{"cmd", "meters"}}).value("test_tone", true), "tone switches itself off after 30 s", tail, 0);
}

void testGcAndPresets() {
    std::puts("slot GC + presets");
    char tmpl[] = "/tmp/bbselftest.XXXXXX";
    const char* dir = mkdtemp(tmpl);
    std::string fac = std::string(dir) + "/factory-src";
    makeDirs(fac);
    writeFileAtomic(fac + "/01-clean.json",
                    R"({"name":"Clean","master":{"in_gain_db":0,"out_gain_db":-3},"chain":[{"type":"host_gain","params":{"gain":-1}}]})");
    HostConfig hc = Rig::defaults();
    hc.stateDir = std::string(dir) + "/state";
    hc.factoryDir = fac;
    hc.autosave = true;
    hc.autosaveDelayS = 0.0;
    {
        Rig r(hc);
        r.host.start();
        json st = r.cmd({{"cmd", "state"}});
        check(st["state"]["name"] == "Clean" && st["state"]["chain"].size() == 1, "first boot loads the first factory preset");
        std::vector<float> z(4800, 0.f), o(4800);
        for (int i = 0; i < 6; ++i) r.cmd({{"cmd", "add"}, {"type", "host_gain"}});
        r.run(z.data(), o.data(), 4800);
        for (int i = 2; i <= 7; ++i) r.cmd({{"cmd", "remove"}, {"uid", i}}), r.run(z.data(), o.data(), 960);
        r.run(z.data(), o.data(), 4800);
        json s = r.cmd({{"cmd", "status"}});
        check(s.value("live_slots", 0) == 1, "removed slots are freed after their chain retires", s.value("live_slots", 0), 1);
        check(r.cmd({{"cmd", "state"}})["state"]["dirty"] == false, "back to the preset's content -> not dirty");
        r.cmd({{"cmd", "set_param"}, {"uid", 1}, {"param", "gain"}, {"value", -4}});
        check(r.cmd({{"cmd", "presets"}})["dirty"] == true, "edit -> dirty");
        json sv = r.cmd({{"cmd", "save_preset"}, {"name", "Mine"}});
        check(sv.value("ok", false), "save_preset");
        json pl = r.cmd({{"cmd", "presets"}});
        check(pl["presets"].size() == 2 && pl["current"] == "Mine" && pl["dirty"] == false, "preset list + current");
        check(!r.cmd({{"cmd", "delete_preset"}, {"name", "Clean"}}).value("ok", true), "factory preset can't be deleted");
        r.cmd({{"cmd", "save_preset"}, {"name", "Clean"}});  // user copy overrides factory
        json pl2 = r.cmd({{"cmd", "presets"}});
        check(pl2["presets"][0]["factory"] == false, "overwriting a factory preset makes a user copy");
        check(r.cmd({{"cmd", "delete_preset"}, {"name", "Clean"}}).value("ok", false), "deleting the copy restores factory");
        r.cmd({{"cmd", "set_master"}, {"out_gain_db", -12}});
        r.host.tick(int64_t(100e9));
        r.host.flushAutosave();
    }
    {
        Rig r(hc);
        r.host.start();
        json st = r.cmd({{"cmd", "state"}})["state"];
        check(st["master"]["out_gain_db"] == -12.0 && st["name"] == "Clean" && st["chain"].size() == 1, "live state restored after restart");
    }
    std::string rm = std::string("rm -rf ") + dir;
    if (std::system(rm.c_str()) != 0) std::puts("  (could not remove temp dir)");
}

void testTuner() {
    std::puts("tuner (bass-like plucks: weak fundamental, strong 2nd harmonic, noise)");
    struct Case {
        const char* name;
        float hz;
        const char* note;
        int oct;
    } cases[] = {{"low B", 30.868f, "B", 0}, {"open E", 41.203f, "E", 1}, {"open A", 55.f, "A", 1},
                 {"open D", 73.416f, "D", 2}, {"open G", 97.999f, "G", 2}, {"E +12c", 41.203f * 1.00696f, "E", 1},
                 {"G 12th fret", 196.0f, "G", 3}};
    unsigned seed = 1;
    for (auto& c : cases) {
        Tuner t(SR);
        const size_t N = size_t(SR * 0.6f);
        std::vector<float> x(N);
        const float amps[6] = {0.25f, 1.0f, 0.6f, 0.35f, 0.2f, 0.1f};
        for (size_t i = 0; i < N; ++i) {
            const float tt = float(i) / SR;
            float v = 0;
            for (int h = 0; h < 6; ++h) v += amps[h] * std::sin(2.f * float(M_PI) * c.hz * float(h + 1) * tt + 0.3f * float(h));
            seed = seed * 1664525u + 1013904223u;
            v = 0.2f * v * std::exp(-tt * 1.5f) + 1e-3f * (float(seed >> 8) / 16777216.f - 0.5f);
            x[i] = v;
        }
        TunerResult r;
        for (size_t p = 0; p < N; p += 2400) {  // 20 Hz analysis like the engine
            t.feed(x.data() + p, int(std::min<size_t>(2400, N - p)));
            r = t.analyze();
        }
        const float wantCents = 1200.f * std::log2(c.hz / (440.f * std::exp2(std::round(12.f * std::log2(c.hz / 440.f)) / 12.f)));
        char b[128];
        std::snprintf(b, sizeof b, "%-12s %7.2f Hz -> %s%d %+5.1f c (conf %.2f, %.2f Hz)", c.name, c.hz, r.note.c_str(), r.octave,
                      r.cents, r.confidence, r.hz);
        check(r.note == c.note && r.octave == c.oct && std::fabs(r.cents - wantCents) < 3.f, b, r.cents, wantCents);
    }
}

}  // namespace

int main() {
    enableFlushToZero();
    testPassthrough();
    testClickFree();
    testTails();
    testGuards();
    testTone();
    testReadyAndLooper();
    testGcAndPresets();
    testTuner();
    std::printf("\n%d passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
