// bassbox effects harness: renders every registered effect through test signals, checks for
// NaN/Inf/denormals/runaway levels at defaults, extremes, random corners and under parameter
// automation, measures level match and CPU, dumps the catalog JSON and probe data for plots.
//
//   fxtest test     [--only a,b] [--out DIR] [--repo DIR] [--nowav] [--quick]
//   fxtest bench    [--only a,b] [--out DIR] [--ms 600]     (64-frame blocks, defaults + all-max)
//   fxtest catalog  [--out FILE]                            (engine/catalog.json)
//   fxtest probe    [--only a,b] [--out DIR]                (data for tests/plots.py)
//   fxtest selftest                                         (dsp.h oversampler + crossover checks)
//   fxtest list
//   fxtest presets                                          (factory presets through the riff -> out/presets.md)
//   fxtest levels   [--only a,b]                            (level vs. each param -> out/levels.md)
#include "effect.h"
#include "dsp.h"
#include "dsp_drive.h"
#include "wav.h"

#include <algorithm>
#include <chrono>
#include <cctype>
#include <cfloat>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <vector>

#if defined(__aarch64__) && defined(__linux__)
#define BB_HAVE_FPCR 1
#endif

using namespace bb;

namespace {

constexpr float kFs = 48000.f;
constexpr int kMaxBlock = 256;

struct Opts {
    std::string mode = "test";
    std::string out;
    std::string repo;
    std::set<std::string> only;
    bool nowav = false, quick = false;
    double benchMs = 600;
    bool ftz = false;
};
Opts g;

void mkdirs(const std::string& p) {
    std::string cur;
    std::stringstream ss(p);
    std::string part;
    if (!p.empty() && p[0] == '/') cur = "/";
    while (std::getline(ss, part, '/')) {
        if (part.empty()) continue;
        cur += part + "/";
        ::mkdir(cur.c_str(), 0755);
    }
}

void setFtz(bool on) {
#ifdef BB_HAVE_FPCR
    uint64_t fpcr;
    asm volatile("mrs %0, fpcr" : "=r"(fpcr));
    if (on) fpcr |= (1ull << 24); else fpcr &= ~(1ull << 24);
    asm volatile("msr fpcr, %0" : : "r"(fpcr));
#else
    (void)on;
#endif
}

// ------------------------------------------------------------------------------------------
// signals
std::vector<float> g_riff;

std::vector<float> silence(float secs) { return std::vector<float>(size_t(secs * kFs), 0.f); }
void append(std::vector<float>& a, const std::vector<float>& b) { a.insert(a.end(), b.begin(), b.end()); }

std::vector<float> sweep(float f0, float f1, float secs, float amp) {
    const size_t n = size_t(secs * kFs);
    std::vector<float> x(n);
    const double k = std::log(f1 / f0);
    for (size_t i = 0; i < n; ++i) {
        const double t = double(i) / kFs;
        const double ph = 2 * M_PI * f0 * secs / k * (std::exp(t / secs * k) - 1);
        const double fade = std::min(1.0, std::min(t / 0.01, (secs - t) / 0.01));
        x[i] = float(amp * std::sin(ph) * fade);
    }
    return x;
}

// additive picked-string model (matches scripts/make_test_signals.py), peak-normalised
std::vector<float> pluck(float f, float secs, float peak, uint32_t seed) {
    const size_t n = size_t(secs * kFs);
    std::vector<double> x(n, 0.0);
    dsp::Rng rng; rng.s = seed;
    for (int k = 1; k < 60; ++k) {
        const double fk = k * f * (1 + 0.0002 * k * k);
        if (fk > 10000) break;
        double a = std::fabs(std::sin(M_PI * k * 0.2)) / k;
        a /= std::sqrt(1 + std::pow(fk / 2500, 4));
        const double tau = 1.8 / (1 + 0.25 * k);
        const double ph = (rng.next() + 1) * M_PI;
        for (size_t i = 0; i < n; ++i) {
            const double t = double(i) / kFs;
            x[i] += a * std::sin(2 * M_PI * fk * t + ph) * std::exp(-t / tau);
        }
    }
    double pk = 0;
    for (size_t i = 0; i < n; ++i) {
        const double t = double(i) / kFs;
        x[i] *= std::min(1.0, t / 0.002) * std::min(1.0, (secs - t) / 0.01);
        pk = std::max(pk, std::fabs(x[i]));
    }
    std::vector<float> y(n);
    for (size_t i = 0; i < n; ++i) y[i] = float(x[i] / pk * peak);
    return y;
}

std::vector<float> sine(float f, float secs, float amp) {
    std::vector<float> x(size_t(secs * kFs));
    for (size_t i = 0; i < x.size(); ++i) x[i] = float(amp * std::sin(2 * M_PI * f * double(i) / kFs));
    return x;
}

std::vector<float> burst() {  // 0 dBFS: square-ish 82 Hz + noise, 0.5 s, then silence
    std::vector<float> x(size_t(0.5f * kFs));
    dsp::Rng r;
    for (size_t i = 0; i < x.size(); ++i) {
        const double t = double(i) / kFs;
        const double sq = std::sin(2 * M_PI * 82.4 * t) > 0 ? 1.0 : -1.0;
        x[i] = float(std::max(-1.0, std::min(1.0, 0.8 * sq + 0.2 * r.next())));
    }
    return x;
}

std::vector<float> impulse(float secs) {
    std::vector<float> x = silence(secs);
    x[0] = 1.f;
    return x;
}

std::vector<float> riffSeg(float secs) {
    std::vector<float> x(g_riff.begin(), g_riff.begin() + std::min(g_riff.size(), size_t(secs * kFs)));
    return x;
}

// ------------------------------------------------------------------------------------------
// stats
struct Stats {
    double sumsq = 0, sum = 0;
    float peak = 0;
    long nan = 0, inf = 0, den = 0, n = 0;
    void add(const float* x, size_t m) {
        for (size_t i = 0; i < m; ++i) {
            const float v = x[i];
            if (std::isnan(v)) { ++nan; continue; }
            if (std::isinf(v)) { ++inf; continue; }
            if (v != 0.f && std::fabs(v) < FLT_MIN) ++den;
            sumsq += double(v) * v;
            sum += v;
            peak = std::max(peak, std::fabs(v));
            ++n;
        }
    }
    double rms() const { return n ? std::sqrt(sumsq / double(n)) : 0.0; }
    double mean() const { return n ? sum / double(n) : 0.0; }
    bool bad() const { return nan || inf || den; }
};
Stats stats(const std::vector<float>& x, size_t from = 0, size_t to = SIZE_MAX) {
    Stats s;
    to = std::min(to, x.size());
    if (from < to) s.add(x.data() + from, to - from);
    return s;
}
double db(double v) { return 20 * std::log10(std::max(v, 1e-12)); }

// ------------------------------------------------------------------------------------------
// rendering
std::vector<float> defaults(const EffectInfo& e) {
    std::vector<float> v;
    for (auto& p : e.params) v.push_back(p.def);
    return v;
}

float randomValue(const ParamSpec& p, dsp::Rng& r) {
    const float u = 0.5f * (r.next() + 1.f);
    if (p.type != ParamType::Float) return std::floor(u * (p.max - p.min + 0.999f) + p.min);
    if (p.curve == Curve::Log && p.min > 0) return p.min * std::pow(p.max / p.min, u);
    return p.min + u * (p.max - p.min);
}

struct Instance {
    std::unique_ptr<Effect> fx;
    explicit Instance(const EffectInfo& e, const std::vector<float>& vals) {
        fx = e.create();
        fx->prepare(kFs, kMaxBlock);
        for (size_t i = 0; i < vals.size(); ++i) fx->setParam(int(i), vals[i]);
        fx->ready();  // effect.h: after prepare() + initial params, before processing
    }
};

using AutoFn = void (*)(Effect&, const EffectInfo&, size_t pos, dsp::Rng&);

// block: >0 fixed, 0 random 1..256
std::vector<float> render(const EffectInfo& e, const std::vector<float>& vals, const std::vector<float>& in,
                          int block = 64, AutoFn autom = nullptr, uint32_t seed = 1, float* actMax = nullptr) {
    Instance inst(e, vals);
    std::vector<float> out = in;
    dsp::Rng r; r.s = seed;
    size_t pos = 0;
    float am = 0.f;
    while (pos < out.size()) {
        int n = block > 0 ? block : int(1 + (r.nextU() % kMaxBlock));
        n = int(std::min<size_t>(size_t(n), out.size() - pos));
        if (autom) autom(*inst.fx, e, pos, r);
        inst.fx->process(out.data() + pos, n);
        const float a = inst.fx->activity();
        if (!(a >= 0.f && a <= 1.0001f)) am = 99.f;  // flags out-of-range / NaN activity
        else am = std::max(am, a);
        pos += size_t(n);
    }
    if (actMax) *actMax = am;
    return out;
}

void autoRandom(Effect& fx, const EffectInfo& e, size_t, dsp::Rng& r) {
    if (e.params.empty()) return;
    const int i = int(r.nextU() % e.params.size());
    fx.setParam(i, randomValue(e.params[size_t(i)], r));
}

// ------------------------------------------------------------------------------------------
// reporting
struct Result {
    std::string id, name;
    double lvlDb = 0, peakDb = -200, dcOff = 0;
    double silenceDb = -200, tailDb = -200, worstPeakDb = -200, blockDiffDb = -200;
    double actMax = 0;
    long bad = 0;
    int fails = 0, warns = 0;
    std::vector<std::string> notes;
    void fail(const std::string& s) { add("FAIL " + s, fails); }
    void warn(const std::string& s) { add("warn " + s, warns); }
    void add(const std::string& s, int& count) {
        if (std::find(notes.begin(), notes.end(), s) != notes.end()) return;  // dedupe repeats
        ++count;
        notes.push_back(s);
    }
};

std::string fmt(const char* f, double v) {
    char b[64];
    std::snprintf(b, sizeof b, f, v);
    return b;
}

std::string paramLabel(const ParamSpec& p, float v) {
    if (p.type == ParamType::Enum && !p.options.empty()) return p.id + "=" + p.options[size_t(std::max(0, std::min(int(p.options.size()) - 1, int(v))))];
    return p.id + "=" + fmt("%g", v);
}

// failPeak/warnPeak are absolute output ceilings (linear). Linear gain at extreme settings is
// legitimate, so "runaway" means +40 dBFS or a growing tail (see checkTail).
void checkRender(Result& R, const std::string& what, const std::vector<float>& y, float failPeak, float warnPeak) {
    const Stats s = stats(y);
    if (s.nan || s.inf) R.fail(what + ": " + std::to_string(s.nan) + " NaN, " + std::to_string(s.inf) + " Inf");
    if (s.den) R.fail(what + ": " + std::to_string(s.den) + " denormal samples");
    R.bad += s.nan + s.inf + s.den;
    R.worstPeakDb = std::max(R.worstPeakDb, db(s.peak));
    if (s.peak > failPeak) R.fail(what + ": runaway peak " + fmt("%+.1f dBFS", db(s.peak)));
    else if (s.peak > warnPeak) R.warn(what + ": hot peak " + fmt("%+.1f dBFS", db(s.peak)));
}

// after the input stops at `end`, the output must not grow (feedback runaway / instability)
// after the input stops at `end`, the output must not grow (feedback runaway / instability).
// Tails effects get 1.5 s windows so a 1.5 s / 95 % delay's sparse echoes compare fairly.
void checkTail(Result& R, const std::string& what, const std::vector<float>& y, size_t end, bool tails) {
    const size_t w = size_t((tails ? 1.5f : 0.25f) * kFs), skip = size_t(0.25f * kFs);
    if (y.size() < end + skip + 2 * w) return;
    const Stats a = stats(y, end + skip, end + skip + w);
    const Stats b = stats(y, y.size() - w);
    if (b.rms() > 1e-3 && b.rms() > a.rms() * 1.12) R.fail(what + ": tail grows after input stops (" + fmt("%.1f", db(a.rms())) + " -> " + fmt("%.1f dB RMS)", db(b.rms())));
    else if (!tails && b.peak > 0.5f) R.warn(what + ": self-oscillates after input stops (" + fmt("%.1f dBFS)", db(b.peak)));
}

void validateSpec(const EffectInfo& e, Result& R) {
    static const std::set<std::string> cats = {"dynamics", "drive", "amp", "filter", "eq", "modulation", "pitch", "time", "utility"};
    if (!cats.count(e.category)) R.fail("bad category '" + e.category + "'");
    if (e.description.empty()) R.warn("no description");
    if (e.params.size() < 2 || e.params.size() > 6) {
        if (e.id != "geq7") R.warn(std::to_string(e.params.size()) + " params (want 2-6)");
    }
    std::set<std::string> ids;
    for (auto& p : e.params) {
        if (!ids.insert(p.id).second) R.fail("duplicate param id " + p.id);
        if (p.name.size() > 8) R.warn("param name '" + p.name + "' > 8 chars");
        const bool singleEnum = p.type == ParamType::Enum && p.options.size() == 1 && p.min == 0 && p.max == 0;
        if (!(p.min < p.max) && !singleEnum) R.fail("param " + p.id + " min >= max");
        if (p.def < p.min || p.def > p.max) R.fail("param " + p.id + " default out of range");
        if (p.curve == Curve::Log && !(p.min > 0)) R.fail("param " + p.id + " log curve with min <= 0");
        if (p.type == ParamType::Enum && int(p.options.size()) != int(p.max) + 1) R.fail("param " + p.id + " enum options/max mismatch");
        if (p.type == ParamType::Bool && (p.min != 0 || p.max != 1)) R.fail("param " + p.id + " bool range");
    }
}

std::string jsonEsc(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += c; }
        else if (c == '\n') o += "\\n";
        else o += c;
    }
    return o;
}
std::string jnum(double v) {
    if (!std::isfinite(v)) return "0";
    char b[32];
    std::snprintf(b, sizeof b, "%.6g", v);
    return b;
}

// curated order for the catalog/library (unknown ids sort to the end of their category)
const std::vector<std::string> kCatOrder = {"dynamics", "drive", "amp", "filter", "eq", "modulation", "pitch", "time", "utility"};
const std::vector<std::string> kIdOrder = {
    "gate", "comp", "limiter",
    "overdrive", "distortion", "fuzz", "octafuzz", "bitcrush",
    "amp", "cab", "nam",
    "env_filter", "wah", "synth_filter",
    "eq3", "geq7", "cut",
    "chorus", "flanger", "phaser", "tremolo", "vibrato", "univibe", "ringmod",
    "octaver", "pitch", "subsynth",
    "delay", "tape", "reverb", "shimmer",
    "boost", "looper"};

std::vector<const EffectInfo*> sortedRegistry() {
    std::vector<const EffectInfo*> v;
    for (auto& e : registry()) v.push_back(&e);
    auto rank = [](const std::vector<std::string>& l, const std::string& s) {
        auto it = std::find(l.begin(), l.end(), s);
        return it == l.end() ? 1000 : int(it - l.begin());
    };
    std::stable_sort(v.begin(), v.end(), [&](const EffectInfo* a, const EffectInfo* b) {
        const int ca = rank(kCatOrder, a->category), cb = rank(kCatOrder, b->category);
        if (ca != cb) return ca < cb;
        const int ia = rank(kIdOrder, a->id), ib = rank(kIdOrder, b->id);
        if (ia != ib) return ia < ib;
        return a->id < b->id;
    });
    return v;
}

bool selected(const EffectInfo& e) { return g.only.empty() || g.only.count(e.id); }

// ------------------------------------------------------------------------------------------
int cmdCatalog() {
    std::string path = g.out.empty() ? g.repo + "/engine/catalog.json" : g.out;
    std::string j = "{\n  \"version\": 1,\n  \"effects\": [\n";
    auto v = sortedRegistry();
    for (size_t k = 0; k < v.size(); ++k) {
        const EffectInfo& e = *v[k];
        j += "    {\"id\": \"" + jsonEsc(e.id) + "\", \"name\": \"" + jsonEsc(e.name) + "\", \"category\": \"" +
             jsonEsc(e.category) + "\", \"description\": \"" + jsonEsc(e.description) + "\", \"tails\": " +
             (e.tails ? "true" : "false") + ",\n     \"params\": [\n";
        for (size_t i = 0; i < e.params.size(); ++i) {
            const ParamSpec& p = e.params[i];
            j += "       {\"id\": \"" + jsonEsc(p.id) + "\", \"name\": \"" + jsonEsc(p.name) + "\", \"min\": " + jnum(p.min) +
                 ", \"max\": " + jnum(p.max) + ", \"def\": " + jnum(p.def) + ", \"unit\": \"" + jsonEsc(p.unit) +
                 "\", \"curve\": \"" + (p.curve == Curve::Log ? "log" : "lin") + "\", \"type\": \"" +
                 (p.type == ParamType::Enum ? "enum" : p.type == ParamType::Bool ? "bool" : "float") + "\", \"options\": [";
            for (size_t o = 0; o < p.options.size(); ++o) j += (o ? ", " : "") + std::string("\"") + jsonEsc(p.options[o]) + "\"";
            j += "], \"step\": " + jnum(p.step) + "}" + (i + 1 < e.params.size() ? "," : "") + "\n";
        }
        j += std::string("     ]}") + (k + 1 < v.size() ? "," : "") + "\n";
    }
    j += "  ]\n}\n";
    FILE* f = std::fopen(path.c_str(), "w");
    if (!f) { std::fprintf(stderr, "cannot write %s\n", path.c_str()); return 1; }
    std::fwrite(j.data(), 1, j.size(), f);
    std::fclose(f);
    std::printf("wrote %s (%zu effects)\n", path.c_str(), v.size());
    return 0;
}

int cmdList() {
    for (auto* e : sortedRegistry()) {
        std::printf("%-13s %-11s %-16s %s\n", e->id.c_str(), e->category.c_str(), e->name.c_str(), e->tails ? "tails" : "");
        for (auto& p : e->params)
            std::printf("    %-9s %-9s [%g .. %g] def %g %s%s\n", p.id.c_str(), p.name.c_str(), p.min, p.max, p.def,
                        p.unit.c_str(), p.curve == Curve::Log ? " log" : "");
    }
    return 0;
}

// ------------------------------------------------------------------------------------------
int cmdTest() {
    const std::string outDir = g.out.empty() ? g.repo + "/engine/tests/out" : g.out;
    const std::string wavDir = outDir + "/wav";
    if (!g.nowav) mkdirs(wavDir);
    mkdirs(outDir);

    // fixed signal set
    struct Sig { std::string name; std::vector<float> x; };
    std::vector<Sig> sigs;
    sigs.push_back({"riff", g_riff});
    sigs.push_back({"sweep", sweep(20, 10000, 6, 0.25f)});
    sigs.push_back({"pluckE", pluck(41.2f, 3, 0.5f, 11)});
    sigs.push_back({"pluckB", pluck(30.87f, 3, 0.5f, 12)});
    sigs.push_back({"impulse", impulse(2)});
    sigs.push_back({"silence", silence(2)});
    {
        auto b = burst();
        append(b, silence(1.5f));
        sigs.push_back({"burst", b});
    }
    const float riffInRms = float(stats(g_riff).rms());

    std::vector<Result> results;
    int totalFails = 0, totalWarns = 0;
    for (const EffectInfo* ep : sortedRegistry()) {
        const EffectInfo& e = *ep;
        if (!selected(e)) continue;
        Result R;
        R.id = e.id;
        R.name = e.name;
        validateSpec(e, R);
        const auto def = defaults(e);
        const float tail = e.tails ? 4.f : 1.f;
        const std::string wd = wavDir + "/" + e.id;
        if (!g.nowav) mkdirs(wd);

        // 1) defaults through every signal
        for (auto& s : sigs) {
            std::vector<float> in = s.x;
            append(in, silence(tail));
            float am = 0;
            auto y = render(e, def, in, 64, nullptr, 1, &am);
            checkRender(R, "default/" + s.name, y, 1000.f, 31.6f);
            if (s.name == "burst") checkTail(R, "default/burst", y, s.x.size(), e.tails);
            R.actMax = std::max(R.actMax, double(am));
            if (am > 1.0001f) R.fail("activity() out of 0..1 or NaN on " + s.name);
            if (s.name == "riff") {
                const Stats so = stats(y, 0, g_riff.size());
                R.lvlDb = db(so.rms()) - db(riffInRms);
                R.peakDb = db(so.peak);
                R.dcOff = so.mean();
                if (so.peak > 2.f) R.warn("riff peak at defaults " + fmt("%+.1f dBFS", db(so.peak)));
                if (std::fabs(R.lvlDb) > 4.5) R.fail("level at defaults " + fmt("%+.1f dB", R.lvlDb));
                else if (std::fabs(R.lvlDb) > 3.0) R.warn("level at defaults " + fmt("%+.1f dB", R.lvlDb));
                if (std::fabs(R.dcOff) > 0.005) R.warn("DC offset " + fmt("%.4f", R.dcOff));
                if (!g.nowav) wav::write(wd + "/default_riff.wav", y, int(kFs), 24);
                // block-size independence
                auto y2 = render(e, def, in, 0, nullptr, 7);
                double md = 0;
                for (size_t i = 0; i < y.size(); ++i) md = std::max(md, double(std::fabs(y[i] - y2[i])));
                R.blockDiffDb = db(md);
                checkRender(R, "random-blocks/riff", y2, 1000.f, 31.6f);
            } else if (s.name == "silence") {
                const Stats ss = stats(y, size_t(0.1f * kFs));
                R.silenceDb = db(ss.peak);
                if (ss.peak > 1e-3f) R.fail("self-noise on silence " + fmt("%.1f dBFS", db(ss.peak)));
                else if (ss.peak > 3e-5f) R.warn("self-noise on silence " + fmt("%.1f dBFS", db(ss.peak)));
            } else if (s.name == "burst") {
                const Stats st = stats(y, y.size() - size_t(0.25f * kFs));
                R.tailDb = db(st.peak);
                const double lim = e.tails ? -30 : -50;
                if (R.tailDb > lim) R.warn("still ringing " + fmt("%.0f dBFS", R.tailDb) + " at end of burst+silence");
                if (!g.nowav) wav::write(wd + "/default_burst.wav", y, int(kFs), 16);
            } else if (!g.nowav && (s.name == "sweep" || s.name == "pluckB")) {
                wav::write(wd + "/default_" + s.name + ".wav", y, int(kFs), 16);
            }
        }

        // 2) single-param extremes (others at default)
        std::vector<float> ext = riffSeg(g.quick ? 2.f : 4.f);
        { auto b = burst(); append(ext, b); }
        const size_t extEnd = ext.size();
        append(ext, silence(1.5f + (e.tails ? 3.f : 0.f)));
        for (size_t i = 0; i < e.params.size(); ++i) {
            for (int side = 0; side < 2; ++side) {
                auto v = def;
                v[i] = side ? e.params[i].max : e.params[i].min;
                float am = 0;
                auto y = render(e, v, ext, 64, nullptr, 1, &am);
                checkRender(R, "extreme " + paramLabel(e.params[i], v[i]), y, 1000.f, 31.6f);
                checkTail(R, "extreme " + paramLabel(e.params[i], v[i]), y, extEnd, e.tails);
                if (am > 1.0001f) R.fail("activity() out of range at " + paramLabel(e.params[i], v[i]));
            }
        }
        // 3) corners: all-min, all-max, random corners
        dsp::Rng cr; cr.s = 1234;
        const int ncorner = g.quick ? 6 : 24;
        for (int c = 0; c < ncorner + 2; ++c) {
            std::vector<float> v(e.params.size());
            std::string lab;
            for (size_t i = 0; i < v.size(); ++i) {
                const auto& p = e.params[i];
                if (c == 0) v[i] = p.min;
                else if (c == 1) v[i] = p.max;
                else {
                    const uint32_t k = cr.nextU() % 3;
                    v[i] = k == 0 ? p.min : k == 1 ? p.max : randomValue(p, cr);
                }
                lab += (i ? "," : "") + paramLabel(p, v[i]);
            }
            auto y = render(e, v, ext, 64);
            const std::string cl = std::string(c == 0 ? "all-min" : c == 1 ? "all-max" : "corner") + " {" + lab + "}";
            checkRender(R, cl, y, 1000.f, 31.6f);
            checkTail(R, cl, y, extEnd, e.tails);
            if (!g.nowav && c < 2) wav::write(wd + (c == 0 ? "/allmin.wav" : "/allmax.wav"), y, int(kFs), 16);
        }
        // 4) automation stress: a random param jumps to a random value every block
        {
            auto in = riffSeg(g.quick ? 3.f : 8.f);
            append(in, burst());
            append(in, silence(1));
            for (uint32_t seed = 1; seed <= (g.quick ? 1u : 3u); ++seed) {
                auto y = render(e, def, in, 64, autoRandom, seed);
                checkRender(R, "automation seed " + std::to_string(seed), y, 1000.f, 31.6f);
            }
            // 5) zipper check render: each continuous param swept min->max->default on the riff
            if (!g.nowav) {
                std::vector<float> in2;
                const size_t per = size_t(2.0f * kFs);
                while (in2.size() < per * std::max<size_t>(1, e.params.size())) append(in2, g_riff);
                in2.resize(per * std::max<size_t>(1, e.params.size()));
                struct Ctx { const EffectInfo* e; size_t per; };
                static Ctx ctx;
                ctx = {&e, per};
                auto y = render(e, def, in2, 64, [](Effect& fx, const EffectInfo& ei, size_t pos, dsp::Rng&) {
                    const size_t k = pos / ctx.per;
                    if (k >= ei.params.size()) return;
                    const auto& p = ei.params[k];
                    const float t = float(pos % ctx.per) / float(ctx.per);
                    float v;
                    if (t < 0.8f) {
                        const float u = 0.5f - 0.5f * std::cos(t / 0.8f * 2.f * dsp::kPi);  // min->max->min
                        const float lo = p.min, hi = p.max;
                        v = (p.curve == Curve::Log) ? lo * std::pow(hi / lo, u) : lo + u * (hi - lo);
                        if (p.type != ParamType::Float) v = std::round(v);
                    } else {
                        v = p.def;
                    }
                    for (size_t j = 0; j < k; ++j) fx.setParam(int(j), ei.params[j].def);
                    fx.setParam(int(k), v);
                });
                checkRender(R, "param sweeps", y, 1000.f, 31.6f);
                wav::write(wd + "/param_sweeps.wav", y, int(kFs), 16);
            }
        }
        totalFails += R.fails;
        totalWarns += R.warns;
        std::printf("%-13s lvl %+5.1f dB  peak %+5.1f  worst %+5.1f  %s%s\n", e.id.c_str(), R.lvlDb, R.peakDb,
                    R.worstPeakDb, R.fails ? "FAIL" : (R.warns ? "warn" : "ok"),
                    R.fails || R.warns ? (" (" + std::to_string(R.fails) + "F/" + std::to_string(R.warns) + "W)").c_str() : "");
        for (auto& n : R.notes) std::printf("      %s\n", n.c_str());
        std::fflush(stdout);
        results.push_back(R);
    }

    // report
    std::string md = "# bassbox effects test report\n\n";
    md += "Input: `samples/test-bass-riff.wav` (" + fmt("%.1f", db(riffInRms)) + " dBFS RMS, -6 dBFS peak) plus sweeps, plucks (E1 41.2 Hz, B0 30.9 Hz), impulse, silence, 0 dBFS burst.\n";
    md += "Each effect: defaults on all signals, every param at min and max, all-min, all-max, random corners, random per-block automation (3 seeds), random block sizes 1..256.\n";
    md += "FAIL = NaN/Inf/denormal output, runaway (peak > +60 dBFS, or output still growing after the input stops), level at defaults off by > 4.5 dB, or self-noise on silence > -60 dBFS at defaults. Warn = level off by 3..4.5 dB, riff peak > +6 dBFS at defaults, peak > +24 dBFS at an extreme, non-tail effect still ringing.\n\n";
    md += "| id | name | level dB (riff, defaults) | out peak dBFS | worst peak dBFS (any setting) | silence out dBFS | burst tail dBFS | rand-block diff dB | max activity | bad samples | result |\n";
    md += "|---|---|---|---|---|---|---|---|---|---|---|\n";
    for (auto& R : results) {
        md += "| " + R.id + " | " + R.name + " | " + fmt("%+.1f", R.lvlDb) + " | " + fmt("%+.1f", R.peakDb) + " | " +
              fmt("%+.1f", R.worstPeakDb) + " | " + (R.silenceDb < -199 ? std::string("-inf") : fmt("%.0f", R.silenceDb)) + " | " +
              (R.tailDb < -199 ? std::string("-inf") : fmt("%.0f", R.tailDb)) + " | " +
              (R.blockDiffDb < -199 ? std::string("exact") : fmt("%.0f", R.blockDiffDb)) + " | " + fmt("%.2f", R.actMax) + " | " +
              std::to_string(R.bad) + " | " + (R.fails ? "**FAIL**" : R.warns ? "warn" : "pass") + " |\n";
    }
    md += "\n## Notes\n\n";
    for (auto& R : results)
        for (auto& n : R.notes) md += "- `" + R.id + "` " + n + "\n";
    md += "\nTotal: " + std::to_string(results.size()) + " effects, " + std::to_string(totalFails) + " failures, " +
          std::to_string(totalWarns) + " warnings.\n";
    if (g.only.empty()) {
        FILE* f = std::fopen((outDir + "/report.md").c_str(), "w");
        if (f) { std::fwrite(md.data(), 1, md.size(), f); std::fclose(f); }
        std::printf("\nwrote %s/report.md\n", outDir.c_str());
    }
    std::printf("TOTAL: %zu effects, %d failures, %d warnings\n", results.size(), totalFails, totalWarns);
    return totalFails ? 1 : 0;
}

// ------------------------------------------------------------------------------------------
int cmdBench() {
    setFtz(true);  // the engine runs with FPCR.FZ set
    const std::string outDir = g.out.empty() ? g.repo + "/engine/tests/out" : g.out;
    mkdirs(outDir);
    char host[128] = "host";
    if (FILE* p = popen("uname -n", "r")) { if (std::fgets(host, sizeof host, p)) host[std::strcspn(host, "\n")] = 0; pclose(p); }
    const double periodUs = 64.0 / kFs * 1e6;
    std::string md = std::string("# CPU per 64-frame block on ") + host + "\n\n";
    md += "Mean and p99 microseconds per 64-frame block (" + fmt("%.0f", periodUs) +
          " us of audio); % = mean / period, i.e. share of one core. Worst of defaults and all-max settings. FTZ on.\n\n";
    md += "| id | mean us (defaults) | mean us (all-max) | p99 us | % of core |\n|---|---|---|---|---|\n";
    std::printf("%-13s %10s %10s %8s %8s\n", "id", "def us", "max us", "p99 us", "% core");
    for (const EffectInfo* ep : sortedRegistry()) {
        const EffectInfo& e = *ep;
        if (!selected(e)) continue;
        double meanUs[2] = {0, 0}, p99 = 0;
        for (int cfg = 0; cfg < 2; ++cfg) {
            std::vector<float> v = defaults(e);
            if (cfg == 1) for (size_t i = 0; i < v.size(); ++i) v[i] = e.params[i].max;
            Instance inst(e, v);
            std::vector<float> buf(64);
            size_t pos = 0;
            std::vector<double> times;
            const auto t0 = std::chrono::steady_clock::now();
            int blocks = 0;
            while (true) {
                for (int i = 0; i < 64; ++i) { buf[size_t(i)] = g_riff[pos]; pos = (pos + 1) % g_riff.size(); }
                const auto a = std::chrono::steady_clock::now();
                inst.fx->process(buf.data(), 64);
                const auto b = std::chrono::steady_clock::now();
                if (blocks >= 200) times.push_back(std::chrono::duration<double, std::micro>(b - a).count());
                ++blocks;
                if (std::chrono::duration<double, std::milli>(b - t0).count() > g.benchMs && blocks > 1000) break;
            }
            double sum = 0;
            for (double t : times) sum += t;
            meanUs[cfg] = sum / double(times.size());
            std::sort(times.begin(), times.end());
            p99 = std::max(p99, times[size_t(double(times.size()) * 0.99)]);
        }
        const double worst = std::max(meanUs[0], meanUs[1]);
        std::printf("%-13s %10.2f %10.2f %8.2f %7.2f%%\n", e.id.c_str(), meanUs[0], meanUs[1], p99, worst / periodUs * 100);
        std::fflush(stdout);
        md += "| " + e.id + " | " + fmt("%.2f", meanUs[0]) + " | " + fmt("%.2f", meanUs[1]) + " | " + fmt("%.2f", p99) + " | " +
              fmt("%.2f%%", worst / periodUs * 100) + " |\n";
    }
    if (g.only.empty()) {
        const std::string path = outDir + "/bench-" + host + ".md";
        FILE* f = std::fopen(path.c_str(), "w");
        if (f) { std::fwrite(md.data(), 1, md.size(), f); std::fclose(f); }
        std::printf("wrote %s\n", path.c_str());
    }
    return 0;
}

// ------------------------------------------------------------------------------------------
// probe: data for plots. Each probe = effect + param overrides + stimulus -> float WAV + index
struct Probe {
    std::string effect, label, kind;  // kind: "ir" | "sine" | "sweep"
    std::vector<std::pair<std::string, float>> params;
    float freq = 0, amp = 0;
};

std::vector<Probe> probes() {
    std::vector<Probe> P;
    auto ir = [&](const std::string& e, const std::string& lab, std::vector<std::pair<std::string, float>> p) {
        P.push_back({e, lab, "ir", std::move(p), 0, 0});
    };
    auto sn = [&](const std::string& e, const std::string& lab, std::vector<std::pair<std::string, float>> p, float f, float a) {
        P.push_back({e, lab, "sine", std::move(p), f, a});
    };
    // EQ / filter / cab / amp frequency responses (impulse after settling)
    ir("eq3", "flat", {});
    ir("eq3", "bass +12", {{"bass", 12}});
    ir("eq3", "bass -12", {{"bass", -12}});
    ir("eq3", "mid +12 @ 800", {{"mid", 12}});
    ir("eq3", "mid -12 @ 400", {{"mid", -12}, {"midfreq", 400}});
    ir("eq3", "mid +12 @ 2k", {{"mid", 12}, {"midfreq", 2000}});
    ir("eq3", "treble +12", {{"treble", 12}});
    ir("eq3", "treble -12", {{"treble", -12}});
    ir("geq7", "flat", {});
    ir("geq7", "smile", {{"b50", 10}, {"b120", 6}, {"b400", -6}, {"b500", -8}, {"b800", -6}, {"b4k5", 6}, {"b10k", 10}});
    ir("geq7", "each band +15", {{"b50", 15}, {"b400", 15}, {"b4k5", 15}});
    ir("geq7", "120/500/800 +15", {{"b120", 15}, {"b500", 15}, {"b800", 15}, {"b10k", 15}});
    ir("geq7", "all -15", {{"b50", -15}, {"b120", -15}, {"b400", -15}, {"b500", -15}, {"b800", -15}, {"b4k5", -15}, {"b10k", -15}});
    ir("cut", "default", {});
    ir("cut", "80 Hz / 5 kHz, 12 dB", {{"lowcut", 80}, {"highcut", 5000}, {"slope", 0}});
    ir("cut", "80 Hz / 5 kHz, 24 dB", {{"lowcut", 80}, {"highcut", 5000}, {"slope", 1}});
    ir("cut", "200 Hz / 2 kHz, 24 dB", {{"lowcut", 200}, {"highcut", 2000}, {"slope", 1}});
    for (int c = 0; c < 4; ++c) {
        static const char* names[] = {"1x15", "4x10", "8x10", "2x12"};
        ir("cab", std::string(names[c]) + " on-axis", {{"cab", float(c)}, {"mic", 0}});
        ir("cab", std::string(names[c]) + " default mic", {{"cab", float(c)}});
        ir("cab", std::string(names[c]) + " off-axis", {{"cab", float(c)}, {"mic", 100}});
    }
    ir("boost", "tone 0", {});
    ir("boost", "tone +6", {{"tone", 6}});
    ir("boost", "tone -6", {{"tone", -6}});
    // long impulse responses for the time effects (8 s)
    auto lir = [&](const std::string& e, const std::string& lab, std::vector<std::pair<std::string, float>> p) {
        P.push_back({e, lab, "longir", std::move(p), 0, 0});
    };
    lir("reverb", "decay 0.5 s", {{"mix", 100}, {"decay", 0.5f}, {"predly", 0}});
    lir("reverb", "decay 2 s", {{"mix", 100}, {"decay", 2.f}, {"predly", 0}});
    lir("reverb", "decay 6 s", {{"mix", 100}, {"decay", 6.f}, {"predly", 0}});
    lir("shimmer", "defaults, mix 100", {{"mix", 100}});
    lir("delay", "defaults", {});
    lir("tape", "defaults", {});
    // modulation crossovers etc. are covered by the generic per-effect IR below
    // drives: harmonic spectra and aliasing
    for (const char* d : {"overdrive", "distortion", "fuzz", "octafuzz", "amp", "bitcrush"}) {
        sn(d, "110 Hz -12 dBFS", {}, 110, 0.25f);
        sn(d, "41.2 Hz -12 dBFS", {}, 41.2f, 0.25f);
        sn(d, "1 kHz -12 dBFS", {}, 1000, 0.25f);
        sn(d, "3.1 kHz -12 dBFS (aliasing)", {}, 3100, 0.25f);
    }
    for (float dr : {10.f, 50.f, 90.f}) {
        for (const char* d : {"overdrive", "distortion", "fuzz"}) {
            const std::string pid = std::string(d) == "fuzz" ? "fuzz" : "drive";
            sn(d, "220 Hz -6 dBFS drive " + fmt("%.0f", dr), {{pid, dr}, {"blend", 100}}, 220, 0.5f);
        }
    }
    return P;
}

int cmdProbe() {
    const std::string outDir = (g.out.empty() ? g.repo + "/engine/tests/out" : g.out) + "/probe";
    mkdirs(outDir);
    std::map<std::string, const EffectInfo*> byId;
    for (auto& e : registry()) byId[e.id] = &e;
    std::string idx = "[\n";
    int count = 0;
    auto run = [&](const Probe& p) {
        auto it = byId.find(p.effect);
        if (it == byId.end()) return;
        const EffectInfo& e = *it->second;
        if (!selected(e)) return;
        auto vals = defaults(e);
        for (auto& kv : p.params)
            for (size_t i = 0; i < e.params.size(); ++i)
                if (e.params[i].id == kv.first) vals[i] = kv.second;
        std::vector<float> in;
        size_t skip = 0;
        if (p.kind == "ir" || p.kind == "longir") {
            in = silence(p.kind == "ir" ? 1.0f : 0.2f);
            skip = in.size();
            auto im = impulse(p.kind == "ir" ? 1.0f : 8.0f);
            im[0] = 0.25f;  // -12 dBFS impulse keeps nonlinear stages near their small-signal behaviour
            append(in, im);
        } else {
            in = sine(p.freq, 1.5f, p.amp);
            skip = size_t(0.75f * kFs);
        }
        auto y = render(e, vals, in, 64);
        std::vector<float> ys(y.begin() + long(skip), y.end());
        std::vector<float> xs(in.begin() + long(skip), in.end());
        const std::string base = p.effect + "_" + std::to_string(count);
        wav::write(outDir + "/" + base + ".wav", ys, int(kFs), 32);
        if (p.kind == "sine") wav::write(outDir + "/" + base + "_in.wav", xs, int(kFs), 32);
        std::string ps;
        for (auto& kv : p.params) ps += (ps.empty() ? "" : ", ") + ("\"" + kv.first + "\": " + jnum(kv.second));
        idx += std::string(count ? ",\n" : "") + "  {\"file\": \"" + base + ".wav\", \"input\": \"" + base + "_in.wav\", \"effect\": \"" +
               p.effect + "\", \"category\": \"" + e.category + "\", \"label\": \"" + jsonEsc(p.label) + "\", \"kind\": \"" + p.kind +
               "\", \"freq\": " + jnum(p.freq) + ", \"amp\": " + jnum(p.amp) + ", \"params\": {" + ps + "}}";
        ++count;
    };
    for (auto& p : probes()) run(p);
    // generic: every effect's small-signal impulse response at defaults
    for (auto* e : sortedRegistry()) run({e->id, "defaults", "ir", {}, 0, 0});
    idx += "\n]\n";
    FILE* f = std::fopen((outDir + "/index.json").c_str(), "w");
    if (f) { std::fwrite(idx.data(), 1, idx.size(), f); std::fclose(f); }
    // static waveshaper curves
    FILE* c = std::fopen((outDir + "/shapers.csv").c_str(), "w");
    if (c) {
        std::fprintf(c, "x,diode_soft,hard,tube,rational,tanh,cubic,fuzz\n");
        for (int i = -400; i <= 400; ++i) {
            const float x = float(i) / 100.f;
            std::fprintf(c, "%g,%g,%g,%g,%g,%g,%g,%g\n", x, dsp::shapeDiodeSoft(x), dsp::shapeHard(x), dsp::shapeTube(x),
                         dsp::shapeRational(x), dsp::fastTanh(x), dsp::shapeCubic(x), dsp::shapeFuzz(x));
        }
        std::fclose(c);
    }
    std::printf("wrote %d probes to %s\n", count, outDir.c_str());
    return 0;
}

// ------------------------------------------------------------------------------------------
double goertzel(const std::vector<float>& x, size_t from, size_t n, double f, double fs) {
    const double w = 2 * M_PI * f / fs, c = 2 * std::cos(w);
    double s1 = 0, s2 = 0;
    for (size_t i = 0; i < n; ++i) {
        const double win = 0.5 - 0.5 * std::cos(2 * M_PI * double(i) / double(n));
        const double s = x[from + i] * win + c * s1 - s2;
        s2 = s1; s1 = s;
    }
    return std::sqrt(s1 * s1 + s2 * s2 - c * s1 * s2) / (double(n) / 4);
}

int cmdSelftest() {
    int fails = 0;
    for (int factor : {2, 4}) {
        dsp::Oversampler os;
        os.init(factor);
        std::printf("oversampler %dx (stage1 %d coefs):\n", factor, os.s1.n);
        for (float f : {50.f, 1000.f, 10000.f, 15000.f, 18000.f, 20000.f}) {
            std::vector<float> in = sine(f, 0.5f, 0.5f), hi, lo;
            os.reset();
            for (float x : in) {
                float h[4];
                os.up(x, h);
                for (int k = 0; k < factor; ++k) hi.push_back(h[k]);
                lo.push_back(os.down(h));
            }
            const double fsHi = kFs * factor;
            const size_t n = 8192;
            const double pass = goertzel(lo, lo.size() - n, n, f, kFs) / 0.5;
            // image of f in the upsampled stream sits at 48k - f
            const double img = goertzel(hi, hi.size() - n * size_t(factor), n * size_t(factor), kFs - f, fsHi) / 0.5;
            std::printf("  %6.0f Hz: up+down gain %+6.2f dB, image at %5.0f Hz %7.1f dB\n", f, db(pass), kFs - f, db(img));
            if (f <= 15000 && std::fabs(db(pass)) > 0.5) ++fails;
            if (f <= 15000 && db(img) > -80) ++fails;
        }
    }
    {
        dsp::Crossover x;
        x.set(kFs, 150);
        std::printf("LR4 crossover @150 Hz, lo+hi magnitude:\n");
        for (float f : {30.f, 100.f, 150.f, 250.f, 1000.f}) {
            auto in = sine(f, 1.f, 0.5f);
            std::vector<float> sum(in.size()), lo(in.size());
            x.reset();
            for (size_t i = 0; i < in.size(); ++i) { float l, h; x.split(in[i], l, h); sum[i] = l + h; lo[i] = l; }
            const size_t n = 16384;
            const double g1 = goertzel(sum, sum.size() - n, n, f, kFs) / 0.5, g2 = goertzel(lo, lo.size() - n, n, f, kFs) / 0.5;
            std::printf("  %5.0f Hz: sum %+5.2f dB, lo band %+6.1f dB\n", f, db(g1), db(g2));
            if (std::fabs(db(g1)) > 0.1) ++fails;
        }
    }
    std::printf("selftest: %s\n", fails ? "FAIL" : "pass");
    return fails ? 1 : 0;
}


// ------------------------------------------------------------------------------------------
// levels: riff RMS change (dB) with each param at 0/25/50/75/100% of its range (others default)
int cmdLevels() {
    const std::string outDir = g.out.empty() ? g.repo + "/engine/tests/out" : g.out;
    mkdirs(outDir);
    const double inDb = db(stats(g_riff).rms());
    std::string md = "# Level vs. parameter (riff RMS out - in, dB; other params at default)\n\n";
    md += "| effect | param | 0% | 25% | 50% | 75% | 100% |\n|---|---|---|---|---|---|---|\n";
    for (const EffectInfo* ep : sortedRegistry()) {
        const EffectInfo& e = *ep;
        if (!selected(e)) continue;
        for (size_t i = 0; i < e.params.size(); ++i) {
            const auto& p = e.params[i];
            std::string row = "| " + e.id + " | " + p.id + " |";
            std::printf("%-13s %-9s", e.id.c_str(), p.id.c_str());
            for (int k = 0; k <= 4; ++k) {
                const float u = 0.25f * float(k);
                float v = (p.curve == Curve::Log) ? p.min * std::pow(p.max / p.min, u) : p.min + u * (p.max - p.min);
                if (p.type != ParamType::Float) v = std::round(v);
                auto vals = defaults(e);
                vals[i] = v;
                auto y = render(e, vals, g_riff, 64);
                const double d = db(stats(y).rms()) - inDb;
                row += " " + fmt("%+.1f", d) + " |";
                std::printf(" %+6.1f", d);
            }
            std::printf("\n");
            md += row + "\n";
        }
    }
    if (g.only.empty()) {
        FILE* f = std::fopen((outDir + "/levels.md").c_str(), "w");
        if (f) { std::fwrite(md.data(), 1, md.size(), f); std::fclose(f); }
    }
    return 0;
}

// ------------------------------------------------------------------------------------------
// presets: render presets/factory/*.json (State JSON) through the riff with host semantics
// (enabled, per-slot dry/wet mix, master in/out gain) and report loudness vs. the input.
struct JVal {
    enum T { Null, Bool, Num, Str, Arr, Obj } t = Null;
    double n = 0;
    bool b = false;
    std::string s;
    std::vector<JVal> a;
    std::vector<std::pair<std::string, JVal>> o;
    const JVal* get(const std::string& k) const {
        for (auto& kv : o) if (kv.first == k) return &kv.second;
        return nullptr;
    }
};
struct JParse {
    const std::string& src;
    size_t p = 0;
    bool ok = true;
    explicit JParse(const std::string& s) : src(s) {}
    void ws() { while (p < src.size() && std::isspace((unsigned char)src[p])) ++p; }
    std::string str() {
        std::string r;
        ++p;
        while (p < src.size() && src[p] != '"') {
            if (src[p] == '\\' && p + 1 < src.size()) { ++p; r += src[p] == 'n' ? '\n' : src[p]; }
            else r += src[p];
            ++p;
        }
        ++p;
        return r;
    }
    JVal val() {
        ws();
        JVal v;
        if (p >= src.size()) { ok = false; return v; }
        const char c = src[p];
        if (c == '{') {
            v.t = JVal::Obj; ++p; ws();
            if (src[p] == '}') { ++p; return v; }
            while (ok) {
                ws();
                if (src[p] != '"') { ok = false; break; }
                std::string k = str();
                ws();
                if (src[p] != ':') { ok = false; break; }
                ++p;
                v.o.emplace_back(k, val());
                ws();
                if (src[p] == ',') { ++p; continue; }
                if (src[p] == '}') { ++p; break; }
                ok = false;
            }
        } else if (c == '[') {
            v.t = JVal::Arr; ++p; ws();
            if (src[p] == ']') { ++p; return v; }
            while (ok) {
                v.a.push_back(val());
                ws();
                if (src[p] == ',') { ++p; continue; }
                if (src[p] == ']') { ++p; break; }
                ok = false;
            }
        } else if (c == '"') {
            v.t = JVal::Str; v.s = str();
        } else if (!src.compare(p, 4, "true")) { v.t = JVal::Bool; v.b = true; p += 4; }
        else if (!src.compare(p, 5, "false")) { v.t = JVal::Bool; p += 5; }
        else if (!src.compare(p, 4, "null")) { p += 4; }
        else {
            char* end = nullptr;
            v.t = JVal::Num; v.n = std::strtod(src.c_str() + p, &end);
            if (end == src.c_str() + p) ok = false;
            p = size_t(end - src.c_str());
        }
        return v;
    }
};

int cmdPresets() {
    const std::string outDir = g.out.empty() ? g.repo + "/engine/tests/out" : g.out;
    const std::string dir = g.repo + "/presets/factory";
    mkdirs(outDir + "/wav/presets");
    std::vector<std::string> files;
    if (FILE* p = popen(("ls '" + dir + "'/*.json 2>/dev/null").c_str(), "r")) {
        char line[1024];
        while (std::fgets(line, sizeof line, p)) { line[std::strcspn(line, "\n")] = 0; files.push_back(line); }
        pclose(p);
    }
    std::map<std::string, const EffectInfo*> byId;
    for (auto& e : registry()) byId[e.id] = &e;
    const double inDb = db(stats(g_riff).rms());
    std::string md = "# Factory presets through the riff (host semantics: enabled, mix, master gains)\n\n";
    md += "| preset | chain | RMS out - in (dB) | peak dBFS | tail after riff (dBFS peak, last 0.5 s of 3 s) | problems |\n|---|---|---|---|---|---|\n";
    int fails = 0;
    for (auto& f : files) {
        FILE* fp = std::fopen(f.c_str(), "rb");
        if (!fp) continue;
        std::string txt;
        char buf[4096];
        size_t r;
        while ((r = std::fread(buf, 1, sizeof buf, fp)) > 0) txt.append(buf, r);
        std::fclose(fp);
        JParse jp(txt);
        JVal root = jp.val();
        std::string problems;
        if (!jp.ok || root.t != JVal::Obj) { std::printf("%s: JSON parse error\n", f.c_str()); ++fails; continue; }
        const JVal* nm = root.get("name");
        const std::string name = nm ? nm->s : f;
        double inGain = 0, outGain = 0;
        if (const JVal* m = root.get("master")) {
            if (const JVal* v = m->get("in_gain_db")) inGain = v->n;
            if (const JVal* v = m->get("out_gain_db")) outGain = v->n;
        }
        struct Slot { std::unique_ptr<Effect> fx; float mix = 1; bool on = true; std::string type; };
        std::vector<Slot> chain;
        std::string chainStr;
        if (const JVal* c = root.get("chain")) {
            for (auto& s : c->a) {
                const JVal* t = s.get("type");
                if (!t || !byId.count(t->s)) { problems += "unknown type " + (t ? t->s : "?") + "; "; continue; }
                const EffectInfo& e = *byId[t->s];
                Slot sl;
                sl.type = e.id;
                if (const JVal* v = s.get("mix")) sl.mix = float(v->n);
                if (const JVal* v = s.get("enabled")) sl.on = v->b;
                auto vals = defaults(e);
                if (const JVal* ps = s.get("params")) {
                    for (auto& kv : ps->o) {
                        bool found = false;
                        for (size_t i = 0; i < e.params.size(); ++i)
                            if (e.params[i].id == kv.first) {
                                vals[i] = std::max(e.params[i].min, std::min(e.params[i].max, float(kv.second.n)));
                                found = true;
                            }
                        if (!found) problems += e.id + "." + kv.first + " unknown; ";
                    }
                }
                Instance in(e, vals);
                sl.fx = std::move(in.fx);
                chainStr += (chainStr.empty() ? "" : " > ") + e.id + (sl.on ? "" : "(off)");
                chain.push_back(std::move(sl));
            }
        }
        std::vector<float> x = g_riff;
        append(x, silence(3.f));
        const float gi = float(std::pow(10.0, inGain / 20)), go = float(std::pow(10.0, outGain / 20));
        std::vector<float> dry(64);
        for (size_t pos = 0; pos < x.size(); pos += 64) {
            const int n = int(std::min<size_t>(64, x.size() - pos));
            float* b = x.data() + pos;
            for (int i = 0; i < n; ++i) b[i] *= gi;
            for (auto& sl : chain) {
                if (!sl.on) continue;
                std::memcpy(dry.data(), b, sizeof(float) * size_t(n));
                sl.fx->process(b, n);
                if (sl.mix < 1.f) for (int i = 0; i < n; ++i) b[i] = dry[size_t(i)] + sl.mix * (b[i] - dry[size_t(i)]);
            }
            for (int i = 0; i < n; ++i) b[i] *= go;
        }
        const Stats st = stats(x, 0, g_riff.size());
        const Stats tl = stats(x, x.size() - size_t(0.5f * kFs));
        const double lvl = db(st.rms()) - inDb;
        if (st.bad()) problems += "NaN/Inf/denormal; ";
        if (std::fabs(lvl) > 3.0) problems += "level off by > 3 dB; ";
        if (!problems.empty()) ++fails;
        wav::write(outDir + "/wav/presets/" + name + ".wav", x, int(kFs), 24);
        std::printf("%-18s %+5.1f dB  peak %+5.1f  tail %6.1f  %s  %s\n", name.c_str(), lvl, db(st.peak), db(tl.peak),
                    chainStr.c_str(), problems.c_str());
        md += "| " + name + " | " + chainStr + " | " + fmt("%+.1f", lvl) + " | " + fmt("%+.1f", db(st.peak)) + " | " +
              fmt("%.0f", db(tl.peak)) + " | " + (problems.empty() ? "-" : problems) + " |\n";
    }
    FILE* fo = std::fopen((outDir + "/presets.md").c_str(), "w");
    if (fo) { std::fwrite(md.data(), 1, md.size(), fo); std::fclose(fo); }
    std::printf("%zu presets, %d with problems\n", files.size(), fails);
    return fails ? 1 : 0;
}

}  // namespace

int main(int argc, char** argv) {
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        auto val = [&]() { return i + 1 < argc ? std::string(argv[++i]) : std::string(); };
        if (a == "--only") {
            std::stringstream ss(val());
            std::string t;
            while (std::getline(ss, t, ',')) if (!t.empty()) g.only.insert(t);
        } else if (a == "--out") g.out = val();
        else if (a == "--repo") g.repo = val();
        else if (a == "--nowav") g.nowav = true;
        else if (a == "--quick") g.quick = true;
        else if (a == "--ms") g.benchMs = std::atof(val().c_str());
        else if (a[0] != '-') g.mode = a;
        else { std::fprintf(stderr, "unknown option %s\n", a.c_str()); return 2; }
    }
    if (g.repo.empty()) g.repo = "../..";
    int rate = 0;
    if (!wav::read(g.repo + "/samples/test-bass-riff.wav", g_riff, rate) || rate != 48000) {
        std::fprintf(stderr, "note: samples/test-bass-riff.wav not found, using a synthetic riff\n");
        g_riff.clear();
        for (float f : {41.2f, 55.f, 61.7f, 73.4f, 41.2f, 82.4f, 30.9f, 49.f}) append(g_riff, pluck(f, 1.2f, 0.5f, uint32_t(f)));
    }
    if (g.mode == "test") return cmdTest();
    if (g.mode == "bench") return cmdBench();
    if (g.mode == "catalog") return cmdCatalog();
    if (g.mode == "probe") return cmdProbe();
    if (g.mode == "selftest") return cmdSelftest();
    if (g.mode == "list") return cmdList();
    if (g.mode == "levels") return cmdLevels();
    if (g.mode == "presets") return cmdPresets();
    std::fprintf(stderr, "unknown mode %s\n", g.mode.c_str());
    return 2;
}
