// bassbox engine: minimal WAV reader/writer (PCM 16/24/32-bit, IEEE float 32/64, any channel
// count, WAVE_FORMAT_EXTENSIBLE). Control thread / offline tools only.
#pragma once

#include <string>
#include <vector>

namespace bb {

struct Wav {
    int rate = 48000;
    int channels = 1;
    std::vector<float> data;  // interleaved, nominal -1..1
    size_t frames() const { return channels ? data.size() / size_t(channels) : 0; }
    std::vector<float> channel(int c) const;
};

bool readWav(const std::string& path, Wav& w, std::string& err);
// bits: 16, 24 (PCM) or 32 (IEEE float)
bool writeWav(const std::string& path, const Wav& w, int bits, std::string& err);

}  // namespace bb
