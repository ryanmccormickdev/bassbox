// Minimal mono WAV reader/writer for the effects harness (PCM 16/24/32 and float32).
#pragma once

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace wav {

inline bool read(const std::string& path, std::vector<float>& out, int& rate) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    std::vector<uint8_t> d;
    uint8_t tmp[65536];
    size_t r;
    while ((r = std::fread(tmp, 1, sizeof tmp, f)) > 0) d.insert(d.end(), tmp, tmp + r);
    std::fclose(f);
    if (d.size() < 44 || std::memcmp(d.data(), "RIFF", 4) || std::memcmp(d.data() + 8, "WAVE", 4))
        return false;
    auto u16 = [&](size_t o) { return uint32_t(d[o]) | (uint32_t(d[o + 1]) << 8); };
    auto u32 = [&](size_t o) { return u16(o) | (u16(o + 2) << 16); };
    int fmt = 0, ch = 1, bits = 16;
    size_t pos = 12;
    rate = 48000;
    while (pos + 8 <= d.size()) {
        const uint32_t sz = u32(pos + 4);
        const size_t body = pos + 8;
        if (!std::memcmp(d.data() + pos, "fmt ", 4)) {
            fmt = int(u16(body));
            ch = int(u16(body + 2));
            rate = int(u32(body + 4));
            bits = int(u16(body + 14));
            if (fmt == 0xFFFE && sz >= 26) fmt = int(u16(body + 24));
        } else if (!std::memcmp(d.data() + pos, "data", 4)) {
            const int bps = bits / 8;
            const size_t frames = std::min<size_t>(sz, d.size() - body) / size_t(bps * ch);
            out.resize(frames);
            for (size_t i = 0; i < frames; ++i) {
                const uint8_t* p = d.data() + body + i * size_t(bps * ch);
                float v = 0.f;
                if (fmt == 3 && bits == 32) {
                    std::memcpy(&v, p, 4);
                } else if (bits == 16) {
                    v = float(int16_t(uint16_t(p[0] | (p[1] << 8)))) / 32768.f;
                } else if (bits == 24) {
                    int32_t s = int32_t(uint32_t(p[0]) << 8 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 24);
                    v = float(s >> 8) / 8388608.f;
                } else if (bits == 32) {
                    int32_t s;
                    std::memcpy(&s, p, 4);
                    v = float(double(s) / 2147483648.0);
                }
                out[i] = v;
            }
            return true;
        }
        pos = body + sz + (sz & 1);
    }
    return false;
}

// bits: 16, 24 (PCM, clipped to +-1) or 32 (float, unclipped)
inline bool write(const std::string& path, const std::vector<float>& x, int rate, int bits = 24) {
    FILE* f = std::fopen(path.c_str(), "wb");
    if (!f) return false;
    const int bps = bits / 8;
    const uint32_t dataBytes = uint32_t(x.size() * size_t(bps));
    auto w32 = [&](uint32_t v) { uint8_t b[4] = {uint8_t(v), uint8_t(v >> 8), uint8_t(v >> 16), uint8_t(v >> 24)}; std::fwrite(b, 1, 4, f); };
    auto w16 = [&](uint32_t v) { uint8_t b[2] = {uint8_t(v), uint8_t(v >> 8)}; std::fwrite(b, 1, 2, f); };
    std::fwrite("RIFF", 1, 4, f); w32(36 + dataBytes); std::fwrite("WAVE", 1, 4, f);
    std::fwrite("fmt ", 1, 4, f); w32(16); w16(bits == 32 ? 3 : 1); w16(1); w32(uint32_t(rate));
    w32(uint32_t(rate * bps)); w16(uint32_t(bps)); w16(uint32_t(bits));
    std::fwrite("data", 1, 4, f); w32(dataBytes);
    std::vector<uint8_t> buf(x.size() * size_t(bps));
    for (size_t i = 0; i < x.size(); ++i) {
        uint8_t* p = buf.data() + i * size_t(bps);
        float v = x[i];
        if (!std::isfinite(v)) v = 0.f;
        if (bits == 32) {
            std::memcpy(p, &v, 4);
        } else {
            v = v > 1.f ? 1.f : (v < -1.f ? -1.f : v);
            const int32_t s = int32_t(std::lrint(double(v) * (bits == 16 ? 32767.0 : 8388607.0)));
            p[0] = uint8_t(s); p[1] = uint8_t(s >> 8);
            if (bits == 24) p[2] = uint8_t(s >> 16);
        }
    }
    std::fwrite(buf.data(), 1, buf.size(), f);
    std::fclose(f);
    return true;
}

}  // namespace wav
