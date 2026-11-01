// bassbox engine: real-time primitives shared by the audio thread and the control thread.
//
// Everything here is safe to call from the audio thread: no allocation after construction,
// no locks, no syscalls (clock_gettime(CLOCK_MONOTONIC) is a vDSO read on Linux/arm64 and macOS).
#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <ctime>
#include <memory>
#include <type_traits>

#if defined(__x86_64__) || defined(__i386__)
#include <xmmintrin.h>
#endif

namespace bb {

constexpr std::size_t kCacheLine = 64;

// Flush denormals to zero for the calling thread. On AArch64 FPCR.FZ (bit 24) flushes both
// denormal inputs and results for single/double precision (it covers what x86 splits into
// FTZ + DAZ). Recursive filters decaying towards silence would otherwise hit the slow path.
inline void enableFlushToZero() {
#if defined(__aarch64__)
    uint64_t fpcr;
    asm volatile("mrs %0, fpcr" : "=r"(fpcr));
    fpcr |= (uint64_t(1) << 24);
    asm volatile("msr fpcr, %0" : : "r"(fpcr));
#elif defined(__x86_64__) || defined(__i386__)
    _mm_setcsr(_mm_getcsr() | 0x8040);  // FTZ | DAZ
#endif
}

inline bool flushToZeroEnabled() {
#if defined(__aarch64__)
    uint64_t fpcr;
    asm volatile("mrs %0, fpcr" : "=r"(fpcr));
    return (fpcr >> 24) & 1;
#elif defined(__x86_64__) || defined(__i386__)
    return (_mm_getcsr() & 0x8040) == 0x8040;
#else
    return false;
#endif
}

inline int64_t nowNs() {
    timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return int64_t(ts.tv_sec) * 1000000000 + ts.tv_nsec;
}

// Single-producer single-consumer ring of trivially copyable T. Capacity is rounded up to a
// power of two; one producer thread calls push*, one consumer thread calls pop*. Wait-free.
// Storage is allocated once in the constructor (on the control thread).
template <typename T>
class SpscRing {
    static_assert(std::is_trivially_copyable<T>::value, "SpscRing<T> needs a trivially copyable T");

public:
    explicit SpscRing(std::size_t capacity) {
        std::size_t c = 2;
        while (c < capacity) c <<= 1;
        cap_ = c;
        mask_ = c - 1;
        buf_.reset(new T[c]());
    }
    std::size_t capacity() const { return cap_; }

    // producer
    bool push(const T& v) {
        const std::size_t w = w_.load(std::memory_order_relaxed);
        if (w - r_.load(std::memory_order_acquire) >= cap_) return false;
        buf_[w & mask_] = v;
        w_.store(w + 1, std::memory_order_release);
        return true;
    }
    // Pushes as many of n items as fit; returns the number written.
    std::size_t pushMany(const T* v, std::size_t n) {
        const std::size_t w = w_.load(std::memory_order_relaxed);
        const std::size_t space = cap_ - (w - r_.load(std::memory_order_acquire));
        if (n > space) n = space;
        for (std::size_t i = 0; i < n; ++i) buf_[(w + i) & mask_] = v[i];
        w_.store(w + n, std::memory_order_release);
        return n;
    }

    // consumer
    bool pop(T& v) {
        const std::size_t r = r_.load(std::memory_order_relaxed);
        if (r == w_.load(std::memory_order_acquire)) return false;
        v = buf_[r & mask_];
        r_.store(r + 1, std::memory_order_release);
        return true;
    }
    std::size_t popMany(T* v, std::size_t n) {
        const std::size_t r = r_.load(std::memory_order_relaxed);
        const std::size_t avail = w_.load(std::memory_order_acquire) - r;
        if (n > avail) n = avail;
        for (std::size_t i = 0; i < n; ++i) v[i] = buf_[(r + i) & mask_];
        r_.store(r + n, std::memory_order_release);
        return n;
    }
    // consumer: discard everything currently queued
    void clear() { r_.store(w_.load(std::memory_order_acquire), std::memory_order_release); }

private:
    std::unique_ptr<T[]> buf_;
    std::size_t cap_ = 0, mask_ = 0;
    alignas(kCacheLine) std::atomic<std::size_t> w_{0};
    alignas(kCacheLine) std::atomic<std::size_t> r_{0};
};

// Lock-free float stored as an atomic (std::atomic<float> is lock-free on arm64/x86 but we
// want the relaxed load/store spelled out at every call site).
struct AtomicF {
    std::atomic<float> v;
    explicit AtomicF(float x = 0.f) : v(x) {}
    float load() const { return v.load(std::memory_order_relaxed); }
    void store(float x) { v.store(x, std::memory_order_relaxed); }
};

}  // namespace bb
