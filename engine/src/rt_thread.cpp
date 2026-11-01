// bassbox engine: helpers to make a thread real-time (shared by the audio backends).
#include <pthread.h>
#include <sched.h>

#include <cerrno>
#include <cstring>
#include <string>

#include "audio.h"
#include "rt.h"

namespace bb {

namespace {
// Touch a chunk of stack now so the RT loop never takes a page fault growing it (mlockall
// with MCL_FUTURE then keeps it resident).
__attribute__((noinline)) void prefaultStack() {
    volatile char buf[128 * 1024];
    for (size_t i = 0; i < sizeof buf; i += 4096) buf[i] = 0;
}
}  // namespace

std::string makeThreadRealtime(int priority, int cpu) {
    std::string log;
#if defined(__linux__)
    if (cpu >= 0) {
        cpu_set_t set;
        CPU_ZERO(&set);
        CPU_SET(cpu, &set);
        const int rc = pthread_setaffinity_np(pthread_self(), sizeof set, &set);
        log += rc == 0 ? "pinned to CPU" + std::to_string(cpu) : std::string("affinity FAILED: ") + std::strerror(rc);
    }
#else
    (void)cpu;
    log += "no CPU pinning on this OS";
#endif
    if (priority > 0) {
        sched_param sp{};
        sp.sched_priority = priority;
        const int rc = pthread_setschedparam(pthread_self(), SCHED_FIFO, &sp);
        log += rc == 0 ? ", SCHED_FIFO " + std::to_string(priority)
                       : std::string(", SCHED_FIFO FAILED (") + std::strerror(rc) + "), running SCHED_OTHER";
    }
    enableFlushToZero();
    log += flushToZeroEnabled() ? ", FTZ on" : ", FTZ unavailable";
    prefaultStack();
    return log;
}

}  // namespace bb
