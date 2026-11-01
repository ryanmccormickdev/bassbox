// bb-catalog: print the effect catalog JSON ({"version":1,"effects":[...]}), the same content
// the engine answers to {"cmd":"catalog"}. The build runs it to refresh engine/catalog.json.
// The text layout matches engine/tests/fxtest.cpp `catalog` byte for byte, so whichever tool
// regenerates the file, it doesn't churn.
//
//   bb-catalog [--out FILE] [--all]     --all also lists the host's test effects
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>

#include "catalog.h"

using namespace bb;

static std::string esc(const std::string& s) {
    std::string o;
    for (char c : s) {
        if (c == '"' || c == '\\') {
            o += '\\';
            o += c;
        } else if (c == '\n') {
            o += "\\n";
        } else {
            o += c;
        }
    }
    return o;
}

static std::string num(double v) {
    if (!std::isfinite(v)) return "0";
    char b[32];
    std::snprintf(b, sizeof b, "%.6g", v);
    return b;
}

int main(int argc, char** argv) {
    std::string out;
    bool all = false;
    for (int i = 1; i < argc; ++i) {
        if (!std::strcmp(argv[i], "--out") && i + 1 < argc) out = argv[++i];
        else if (!std::strcmp(argv[i], "--all")) all = true;
        else {
            std::fprintf(stderr, "usage: bb-catalog [--out FILE] [--all]\n");
            return 1;
        }
    }
    std::string j = "{\n  \"version\": 1,\n  \"effects\": [\n";
    auto v = sortedEffects(all);
    for (size_t k = 0; k < v.size(); ++k) {
        const EffectInfo& e = *v[k];
        j += "    {\"id\": \"" + esc(e.id) + "\", \"name\": \"" + esc(e.name) + "\", \"category\": \"" + esc(e.category) +
             "\", \"description\": \"" + esc(e.description) + "\", \"tails\": " + (e.tails ? "true" : "false") +
             ",\n     \"params\": [\n";
        for (size_t i = 0; i < e.params.size(); ++i) {
            const ParamSpec& p = e.params[i];
            j += "       {\"id\": \"" + esc(p.id) + "\", \"name\": \"" + esc(p.name) + "\", \"min\": " + num(p.min) +
                 ", \"max\": " + num(p.max) + ", \"def\": " + num(p.def) + ", \"unit\": \"" + esc(p.unit) +
                 "\", \"curve\": \"" + (p.curve == Curve::Log ? "log" : "lin") + "\", \"type\": \"" +
                 (p.type == ParamType::Enum ? "enum" : p.type == ParamType::Bool ? "bool" : "float") + "\", \"options\": [";
            for (size_t o = 0; o < p.options.size(); ++o) j += (o ? ", " : "") + std::string("\"") + esc(p.options[o]) + "\"";
            j += "], \"step\": " + num(p.step) + "}" + (i + 1 < e.params.size() ? "," : "") + "\n";
        }
        j += std::string("     ]}") + (k + 1 < v.size() ? "," : "") + "\n";
    }
    j += "  ]\n}\n";
    if (out.empty()) {
        std::fwrite(j.data(), 1, j.size(), stdout);
        return 0;
    }
    FILE* f = std::fopen(out.c_str(), "w");
    if (!f) {
        std::fprintf(stderr, "bb-catalog: cannot write %s\n", out.c_str());
        return 1;
    }
    std::fwrite(j.data(), 1, j.size(), f);
    std::fclose(f);
    std::fprintf(stderr, "bb-catalog: wrote %s (%zu effects)\n", out.c_str(), v.size());
    return 0;
}
