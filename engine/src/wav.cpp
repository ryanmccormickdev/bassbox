#include "wav.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>

namespace bb {

std::vector<float> Wav::channel(int c) const {
    std::vector<float> v(frames());
    if (channels <= 0) return v;
    c = std::max(0, std::min(channels - 1, c));
    for (size_t i = 0; i < v.size(); ++i) v[i] = data[i * size_t(channels) + size_t(c)];
    return v;
}

namespace {
uint32_t u32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }
uint16_t u16(const uint8_t* p) { return uint16_t(p[0] | p[1] << 8); }
void put32(std::string& s, uint32_t v) {
    for (int i = 0; i < 4; ++i) s += char((v >> (8 * i)) & 0xff);
}
void put16(std::string& s, uint16_t v) {
    s += char(v & 0xff);
    s += char(v >> 8);
}
}  // namespace

bool readWav(const std::string& path, Wav& w, std::string& err) {
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        err = "cannot open " + path;
        return false;
    }
    std::vector<uint8_t> b((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    if (b.size() < 12 || std::memcmp(b.data(), "RIFF", 4) != 0 || std::memcmp(b.data() + 8, "WAVE", 4) != 0) {
        err = path + ": not a RIFF/WAVE file";
        return false;
    }
    int fmtTag = 0, ch = 0, bits = 0;
    size_t dataOff = 0, dataLen = 0;
    for (size_t p = 12; p + 8 <= b.size();) {
        const uint32_t len = u32(&b[p + 4]);
        const size_t body = p + 8;
        if (std::memcmp(&b[p], "fmt ", 4) == 0 && body + 16 <= b.size()) {
            fmtTag = u16(&b[body]);
            ch = u16(&b[body + 2]);
            w.rate = int(u32(&b[body + 4]));
            bits = u16(&b[body + 14]);
            if (fmtTag == 0xFFFE && len >= 40 && body + 26 <= b.size()) fmtTag = u16(&b[body + 24]);  // extensible
        } else if (std::memcmp(&b[p], "data", 4) == 0) {
            dataOff = body;
            dataLen = std::min<size_t>(len, b.size() - body);
            break;
        }
        p = body + len + (len & 1);
    }
    if (!ch || !dataOff) {
        err = path + ": missing fmt or data chunk";
        return false;
    }
    w.channels = ch;
    const size_t bps = size_t(bits / 8);
    if (!bps) {
        err = path + ": bad bit depth";
        return false;
    }
    const size_t n = dataLen / bps;
    w.data.resize(n);
    const uint8_t* d = &b[dataOff];
    for (size_t i = 0; i < n; ++i, d += bps) {
        float v = 0.f;
        if (fmtTag == 3 && bits == 32) {
            std::memcpy(&v, d, 4);
        } else if (fmtTag == 3 && bits == 64) {
            double dv;
            std::memcpy(&dv, d, 8);
            v = float(dv);
        } else if (fmtTag == 1 && bits == 16) {
            v = float(int16_t(u16(d))) / 32768.f;
        } else if (fmtTag == 1 && bits == 24) {
            int32_t s = int32_t(uint32_t(d[0]) << 8 | uint32_t(d[1]) << 16 | uint32_t(d[2]) << 24) >> 8;
            v = float(s) / 8388608.f;
        } else if (fmtTag == 1 && bits == 32) {
            v = float(int32_t(u32(d))) / 2147483648.f;
        } else if (fmtTag == 1 && bits == 8) {
            v = (float(d[0]) - 128.f) / 128.f;
        } else {
            err = path + ": unsupported format tag " + std::to_string(fmtTag) + " / " + std::to_string(bits) + " bits";
            return false;
        }
        w.data[i] = v;
    }
    return true;
}

bool writeWav(const std::string& path, const Wav& w, int bits, std::string& err) {
    if (bits != 16 && bits != 24 && bits != 32) {
        err = "bits must be 16, 24 or 32 (float)";
        return false;
    }
    const uint16_t tag = bits == 32 ? 3 : 1;
    const uint32_t bps = uint32_t(bits / 8);
    const uint32_t dataLen = uint32_t(w.data.size() * bps);
    std::string s;
    s.reserve(44 + dataLen);
    s += "RIFF";
    put32(s, 36 + dataLen);
    s += "WAVEfmt ";
    put32(s, 16);
    put16(s, tag);
    put16(s, uint16_t(w.channels));
    put32(s, uint32_t(w.rate));
    put32(s, uint32_t(w.rate) * uint32_t(w.channels) * bps);
    put16(s, uint16_t(uint32_t(w.channels) * bps));
    put16(s, uint16_t(bits));
    s += "data";
    put32(s, dataLen);
    for (float v : w.data) {
        if (bits == 32) {
            char c[4];
            std::memcpy(c, &v, 4);
            s.append(c, 4);
            continue;
        }
        const float x = std::max(-1.f, std::min(1.f, std::isfinite(v) ? v : 0.f));
        if (bits == 16) {
            put16(s, uint16_t(int16_t(std::lrint(x * 32767.f))));
        } else {
            const int32_t q = int32_t(std::lrint(double(x) * 8388607.0));
            s += char(q & 0xff);
            s += char((q >> 8) & 0xff);
            s += char((q >> 16) & 0xff);
        }
    }
    std::ofstream f(path, std::ios::binary);
    if (!f) {
        err = "cannot write " + path;
        return false;
    }
    f.write(s.data(), std::streamsize(s.size()));
    return bool(f);
}

}  // namespace bb
