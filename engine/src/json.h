// bassbox engine: JSON (nlohmann/json 3.12.0, MIT; fetched into engine/third_party/nlohmann).
// Used on the control thread only; the audio thread never touches JSON.
#pragma once

#include "nlohmann/json.hpp"

namespace bb {
using json = nlohmann::json;
}
