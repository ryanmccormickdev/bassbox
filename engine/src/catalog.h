// bassbox engine: effect catalog (registry -> JSON) shared by the IPC `catalog` command,
// bb-catalog and bb-render.
#pragma once

#include <string>
#include <vector>

#include "effect.h"
#include "json.h"

namespace bb {

// Effects built into the host for testing (engine/src/builtin_effects.cpp). They are always
// loadable by type but only listed in the catalog when asked for.
bool isHostTestEffect(const std::string& id);

// Registry sorted the way the library screen shows it: category order, then a curated id
// order (kept identical to engine/tests/fxtest.cpp so both catalog writers agree), then id.
std::vector<const EffectInfo*> sortedEffects(bool includeTestEffects);

const EffectInfo* findEffect(const std::string& id);

json paramSpecJson(const ParamSpec& p);
json effectInfoJson(const EffectInfo& e);
// {"version": 1, "effects": [...]}: the `catalog` response body and engine/catalog.json.
json catalogJson(bool includeTestEffects);

// Clamp/quantise a value to what the parameter accepts (range, enum/bool rounding, step).
float sanitizeParam(const ParamSpec& p, float v);

// float -> JSON number with ~6 significant digits (0.6f prints as 0.6, not 0.60000002384).
double jnum(float v);

}  // namespace bb
