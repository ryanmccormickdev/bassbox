#include "catalog.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>

namespace bb {

namespace {
// Same lists as engine/tests/fxtest.cpp . Unknown ids sort to the end of
// their category, so a new effect shows up even if nobody updates these.
const std::vector<std::string> kCatOrder = {"dynamics", "drive", "amp",  "filter", "eq",
                                            "modulation", "pitch", "time", "utility"};
const std::vector<std::string> kIdOrder = {
    "gate",     "comp",      "limiter",                                         //
    "overdrive", "distortion", "fuzz",   "octafuzz", "bitcrush",               //
    "amp",      "cab",       "nam",                                             //
    "env_filter", "wah",     "synth_filter",                                    //
    "eq3",      "geq7",      "cut",                                             //
    "chorus",   "flanger",   "phaser",  "tremolo", "vibrato", "univibe", "ringmod",
    "octaver",  "pitch",     "subsynth",                                        //
    "delay",    "tape",      "reverb",  "shimmer",                              //
    "boost",    "looper"};

int rank(const std::vector<std::string>& l, const std::string& s) {
    auto it = std::find(l.begin(), l.end(), s);
    return it == l.end() ? 1000 : int(it - l.begin());
}
}  // namespace

bool isHostTestEffect(const std::string& id) { return id == "host_gain"; }

std::vector<const EffectInfo*> sortedEffects(bool includeTestEffects) {
    std::vector<const EffectInfo*> v;
    for (auto& e : registry())
        if (includeTestEffects || !isHostTestEffect(e.id)) v.push_back(&e);
    std::stable_sort(v.begin(), v.end(), [](const EffectInfo* a, const EffectInfo* b) {
        const int ca = rank(kCatOrder, a->category), cb = rank(kCatOrder, b->category);
        if (ca != cb) return ca < cb;
        const int ia = rank(kIdOrder, a->id), ib = rank(kIdOrder, b->id);
        if (ia != ib) return ia < ib;
        return a->id < b->id;
    });
    return v;
}

const EffectInfo* findEffect(const std::string& id) {
    for (auto& e : registry())
        if (e.id == id) return &e;
    return nullptr;
}

double jnum(float v) {
    if (!std::isfinite(v)) return 0.0;
    char b[32];
    std::snprintf(b, sizeof b, "%.6g", double(v));
    return std::strtod(b, nullptr);
}

json paramSpecJson(const ParamSpec& p) {
    json j;
    j["id"] = p.id;
    j["name"] = p.name;
    j["min"] = jnum(p.min);
    j["max"] = jnum(p.max);
    j["def"] = jnum(p.def);
    j["unit"] = p.unit;
    j["curve"] = p.curve == Curve::Log ? "log" : "lin";
    j["type"] = p.type == ParamType::Enum ? "enum" : p.type == ParamType::Bool ? "bool" : "float";
    j["options"] = p.options;
    j["step"] = jnum(p.step);
    return j;
}

json effectInfoJson(const EffectInfo& e) {
    json j;
    j["id"] = e.id;
    j["name"] = e.name;
    j["category"] = e.category;
    j["description"] = e.description;
    j["tails"] = e.tails;
    json ps = json::array();
    for (auto& p : e.params) ps.push_back(paramSpecJson(p));
    j["params"] = ps;
    return j;
}

json catalogJson(bool includeTestEffects) {
    json fx = json::array();
    for (auto* e : sortedEffects(includeTestEffects)) fx.push_back(effectInfoJson(*e));
    return json{{"version", 1}, {"effects", fx}};
}

float sanitizeParam(const ParamSpec& p, float v) {
    if (!std::isfinite(v)) v = p.def;
    const float lo = std::min(p.min, p.max), hi = std::max(p.min, p.max);
    if (p.type == ParamType::Enum || p.type == ParamType::Bool) v = std::round(v);
    if (p.step > 0.f && p.type == ParamType::Float) v = p.min + std::round((v - p.min) / p.step) * p.step;
    return std::min(hi, std::max(lo, v));
}

}  // namespace bb
