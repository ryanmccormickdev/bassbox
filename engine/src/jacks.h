// bassbox engine: panel jack sense over GPIO (libgpiod v2), control thread only.
// Config: config/jacks.conf (key = value). Defaults match the 2026-10-06 wiring:
// GPIO5 = IN jack (reads 1 when plugged), GPIO6 = OUT jack (reads 0 when plugged), pull-ups,
// 100 ms debounce. Edge events wake the control loop; the state is accepted once the line
// has been stable for the debounce time (plus a 1 s re-read as a safety net).
#pragma once

#include <cstdint>
#include <string>

namespace bb {

struct JacksConfig {
    bool enabled = true;
    std::string chip = "/dev/gpiochip0";
    int inputLine = 5;
    int inputPluggedLevel = 1;
    int outputLine = 6;
    int outputPluggedLevel = 0;
    int debounceMs = 100;
    bool muteOnUnplug = true;  // fade the output to silence while the input jack is empty
};

bool loadJacksConfig(const std::string& path, JacksConfig& c, std::string& err);

class Jacks {
public:
    Jacks();
    ~Jacks();
    bool open(const JacksConfig& cfg, std::string& err);
    bool available() const { return req_ != nullptr; }
    int fd() const;  // poll for POLLIN; -1 if unavailable
    void onReadable(int64_t nowNs);
    // Debounced state; returns true when it changed (first call after open always true).
    bool update(int64_t nowNs, bool& input, bool& output);

private:
    bool readRaw(bool& input, bool& output);
    JacksConfig cfg_;
    [[maybe_unused]] void* chip_ = nullptr;   // gpiod_chip*
    void* req_ = nullptr;   // gpiod_line_request*
    [[maybe_unused]] void* evbuf_ = nullptr;  // gpiod_edge_event_buffer*
    bool in_ = true, out_ = true, first_ = true;
    int64_t lastEdge_ = 0, lastRead_ = 0;
    bool pending_ = false;
};

}  // namespace bb
