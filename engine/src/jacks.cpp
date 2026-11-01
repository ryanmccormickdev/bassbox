#include "jacks.h"

#include <cctype>
#include <fstream>
#include <sstream>

#if defined(BB_HAVE_GPIOD)
#include <gpiod.h>
#endif

namespace bb {

namespace {
std::string trim(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && std::isspace((unsigned char)s[a])) ++a;
    while (b > a && std::isspace((unsigned char)s[b - 1])) --b;
    return s.substr(a, b - a);
}
}  // namespace

bool loadJacksConfig(const std::string& path, JacksConfig& c, std::string& err) {
    std::ifstream f(path);
    if (!f) {
        err = "cannot read " + path + " (using defaults)";
        return false;
    }
    std::string line;
    int n = 0;
    while (std::getline(f, line)) {
        ++n;
        if (auto h = line.find('#'); h != std::string::npos) line.resize(h);
        line = trim(line);
        if (line.empty()) continue;
        const auto eq = line.find('=');
        if (eq == std::string::npos) {
            err += path + ":" + std::to_string(n) + ": expected key = value\n";
            continue;
        }
        const std::string k = trim(line.substr(0, eq)), v = trim(line.substr(eq + 1));
        auto num = [&](int& dst) {
            try {
                dst = std::stoi(v);
            } catch (...) {
                err += path + ":" + std::to_string(n) + ": bad number for " + k + "\n";
            }
        };
        int tmp = 0;
        if (k == "enabled") { num(tmp); c.enabled = tmp != 0; }
        else if (k == "chip") c.chip = v;
        else if (k == "input_line") num(c.inputLine);
        else if (k == "input_plugged_level") num(c.inputPluggedLevel);
        else if (k == "output_line") num(c.outputLine);
        else if (k == "output_plugged_level") num(c.outputPluggedLevel);
        else if (k == "debounce_ms") num(c.debounceMs);
        else if (k == "mute_on_unplug") { num(tmp); c.muteOnUnplug = tmp != 0; }
        else err += path + ":" + std::to_string(n) + ": unknown key " + k + "\n";
    }
    return err.empty();
}

#if defined(BB_HAVE_GPIOD)

Jacks::Jacks() = default;

Jacks::~Jacks() {
    if (evbuf_) gpiod_edge_event_buffer_free(static_cast<gpiod_edge_event_buffer*>(evbuf_));
    if (req_) gpiod_line_request_release(static_cast<gpiod_line_request*>(req_));
    if (chip_) gpiod_chip_close(static_cast<gpiod_chip*>(chip_));
}

bool Jacks::open(const JacksConfig& cfg, std::string& err) {
    cfg_ = cfg;
    if (!cfg.enabled) {
        err = "jack sense disabled in config";
        return false;
    }
    gpiod_chip* chip = gpiod_chip_open(cfg.chip.c_str());
    if (!chip) {
        err = "gpiod_chip_open " + cfg.chip + " failed";
        return false;
    }
    chip_ = chip;
    gpiod_line_settings* s = gpiod_line_settings_new();
    gpiod_line_settings_set_direction(s, GPIOD_LINE_DIRECTION_INPUT);
    gpiod_line_settings_set_bias(s, GPIOD_LINE_BIAS_PULL_UP);
    gpiod_line_settings_set_edge_detection(s, GPIOD_LINE_EDGE_BOTH);
    gpiod_line_config* lc = gpiod_line_config_new();
    const unsigned int offs[2] = {unsigned(cfg.inputLine), unsigned(cfg.outputLine)};
    gpiod_line_config_add_line_settings(lc, offs, 2, s);
    gpiod_request_config* rc = gpiod_request_config_new();
    gpiod_request_config_set_consumer(rc, "bassbox-engine");
    gpiod_line_request* req = gpiod_chip_request_lines(chip, rc, lc);
    gpiod_request_config_free(rc);
    gpiod_line_config_free(lc);
    gpiod_line_settings_free(s);
    if (!req) {
        err = "gpiod_chip_request_lines failed (lines busy?)";
        return false;
    }
    req_ = req;
    evbuf_ = gpiod_edge_event_buffer_new(16);
    return true;
}

int Jacks::fd() const { return req_ ? gpiod_line_request_get_fd(static_cast<gpiod_line_request*>(req_)) : -1; }

void Jacks::onReadable(int64_t nowNs) {
    if (!req_) return;
    auto* req = static_cast<gpiod_line_request*>(req_);
    auto* buf = static_cast<gpiod_edge_event_buffer*>(evbuf_);
    // drain; we only care that something moved (values are re-read after the debounce)
    while (gpiod_line_request_wait_edge_events(req, 0) > 0) {
        if (gpiod_line_request_read_edge_events(req, buf, 16) <= 0) break;
    }
    lastEdge_ = nowNs;
    pending_ = true;
}

bool Jacks::readRaw(bool& input, bool& output) {
    auto* req = static_cast<gpiod_line_request*>(req_);
    const gpiod_line_value a = gpiod_line_request_get_value(req, unsigned(cfg_.inputLine));
    const gpiod_line_value b = gpiod_line_request_get_value(req, unsigned(cfg_.outputLine));
    if (a == GPIOD_LINE_VALUE_ERROR || b == GPIOD_LINE_VALUE_ERROR) return false;
    input = (a == GPIOD_LINE_VALUE_ACTIVE ? 1 : 0) == cfg_.inputPluggedLevel;
    output = (b == GPIOD_LINE_VALUE_ACTIVE ? 1 : 0) == cfg_.outputPluggedLevel;
    return true;
}

#else  // no libgpiod (macOS dev builds): jacks report "plugged"

Jacks::Jacks() = default;
Jacks::~Jacks() = default;
bool Jacks::open(const JacksConfig&, std::string& err) {
    err = "built without libgpiod";
    return false;
}
int Jacks::fd() const { return -1; }
void Jacks::onReadable(int64_t) {}
bool Jacks::readRaw(bool&, bool&) { return false; }

#endif

bool Jacks::update(int64_t nowNs, bool& input, bool& output) {
    if (!req_) {
        input = output = true;
        const bool f = first_;
        first_ = false;
        return f;
    }
    const int64_t deb = int64_t(cfg_.debounceMs) * 1'000'000;
    const bool due = first_ || (pending_ && nowNs - lastEdge_ >= deb) || (!pending_ && nowNs - lastRead_ >= 1'000'000'000);
    if (!due) {
        input = in_;
        output = out_;
        return false;
    }
    bool i, o;
    lastRead_ = nowNs;
    pending_ = false;
    if (!readRaw(i, o)) {
        input = in_;
        output = out_;
        return false;
    }
    const bool changed = first_ || i != in_ || o != out_;
    first_ = false;
    in_ = i;
    out_ = o;
    input = i;
    output = o;
    return changed;
}

}  // namespace bb
