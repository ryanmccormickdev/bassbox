# Third-party code

Not committed. `fetch.sh` downloads it into this folder:

```
engine/third_party/fetch.sh [--force]
```

| Name | Version | Licence | Used by | Pinned to |
|---|---|---|---|---|
| [nlohmann/json](https://github.com/nlohmann/json) | 3.12.0 | MIT | `engine/src/json.h`: every JSON message and preset | SHA-256 of `single_include/nlohmann/json.hpp` |
| [NeuralAmpModelerCore](https://github.com/sdatkinson/NeuralAmpModelerCore) | 0.6.0 | MIT | `engine/effects/nam.cpp`: the Neural Amp effect | tag `v0.6.0`, commit `0b3d3c97b0859a3a8c92a8628c4dd89a25eb5842`, plus `patches/nam-a2-full-neon.patch` |
| [Eigen](https://gitlab.com/libeigen/eigen) | 3.4.0 | MPL 2.0 | NeuralAmpModelerCore | tag `3.4.0`, commit `3147391d946bb4b6c68edd901f2add6ac1f31f8c` |

nlohmann/json is required by the whole engine. NeuralAmpModelerCore and Eigen are needed only by the
Neural Amp effect: `nam.cpp` checks for them with `__has_include`, and without them it builds as a
pass-through. `engine/CMakeLists.txt` and `engine/tests/Makefile` add `third_party/eigen` and
`third_party/nlohmann` as system include directories, which is all NeuralAmpModelerCore needs.

`fetch.sh` needs `git`, `curl` and `shasum` or `sha256sum`. Run it where there is internet and sync
the tree to the Pi afterwards (`scripts/push.sh`); the pedal never needs the network.

## Why NeuralAmpModelerCore 0.6.0

The factory amp models PiPedal ships are `.nam` files of file version 0.7.0 with the
`SlimmableContainer` architecture: an A2-Lite (3-channel) and an A2-Full (8-channel) WaveNet in one
file. NeuralAmpModelerCore 0.6.0 is needed to load them. It builds as C++17 (its own CMake asks for C++20, but nothing in it
needs it).

`nam.cpp` is a unity build: it includes the sixteen NeuralAmpModelerCore `.cpp` files, with
`NAM_SAMPLE_FLOAT` and `NAM_ENABLE_A2_FAST` defined, so the engine's glob over `effects/*.cpp`
builds it with no extra target. Do not also compile those files separately, or the link fails with
duplicate symbols. That one translation unit takes about 1 min 50 s at `-O3` on the Pi.

No `.nam` model files are included. The effect scans `$BB_NAM_DIRS` (colon separated),
`/var/lib/bassbox/nam` and `/var/pipedal/audio_uploads/NeuralAmpModels`, once at start-up.

## The patch

`patches/nam-a2-full-neon.patch` changes one file, `NAM/wavenet/a2_fast.cpp`. For the A2-Full
layers (8 channels) upstream issues one small Eigen matrix product per tap, 161 per 64-frame block,
and on a Cortex-A72 the packing inside Eigen's product dominates the cost. The patch adds a
register-tiled NEON kernel: the convolution accumulators for 4 frames stay in NEON registers across
all taps and through the bias, mixin, LeakyReLU, head-sum and 1x1 tail. The output matches
upstream to -125 dB (float rounding). On the Pi 4 (GCC 14, pinned to CPU2) the A2-Full model went
from 440 to 220 microseconds per 64-frame block. Build with `-DBB_A2_NO_NEON` to get the upstream
path back.

To move to a newer NeuralAmpModelerCore, change the tag and commit in `fetch.sh` and check that the
patch still applies (`git apply --check`) against `_layer_forward_k` in `a2_fast.cpp`.
