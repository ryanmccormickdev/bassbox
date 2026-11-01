// Definition of the global effect registry declared in effect.h. Lives with the effects so the
// offline test harness and the engine both link it. Function-local static avoids static-init order issues.
#include "effect.h"

namespace bb {
std::vector<EffectInfo>& registry() {
    static std::vector<EffectInfo> r;
    return r;
}
}  // namespace bb
