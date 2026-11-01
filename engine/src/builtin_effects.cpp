// bassbox engine: effects built into the host, so the engine can be tested end to end without
// the effects library. Hidden from the UI catalog (see catalog.cpp isHostTestEffect) but
// loadable by type ("host_gain") over the socket and in bb-render.
#include <cmath>
#include <memory>

#include "effect.h"

namespace {

// Clean gain with a 10 ms linear ramp per change; 0 dB = bit-exact passthrough.
class HostGain final : public bb::Effect {
public:
    void prepare(float sampleRate, int) override { step_ = 1.f / (0.01f * sampleRate); }
    void reset() override { g_ = target_; }
    void setParam(int index, float value) override {
        if (index == 0) target_ = std::pow(10.f, value / 20.f);
    }
    void process(float* buf, int n) override {
        float pk = 0.f;
        for (int i = 0; i < n; ++i) {
            if (g_ != target_) g_ += std::fmax(-step_, std::fmin(step_, target_ - g_));
            buf[i] *= g_;
            pk = std::fmax(pk, std::fabs(buf[i]));
        }
        activity_ = std::fmin(1.f, pk);
    }
    float activity() const override { return activity_; }

private:
    float g_ = 1.f, target_ = 1.f, step_ = 1.f / 480.f, activity_ = 0.f;
};

bb::ParamSpec gainParam() {
    bb::ParamSpec p;
    p.id = "gain";
    p.name = "Gain";
    p.min = -60.f;
    p.max = 24.f;
    p.def = 0.f;
    p.unit = "dB";
    return p;
}

}  // namespace

BB_REGISTER_EFFECT({"host_gain", "Test Gain", "utility", "Host test effect: clean gain (0 dB = passthrough).",
                    {gainParam()}, false, [] { return std::make_unique<HostGain>(); }});
