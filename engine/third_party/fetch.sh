#!/bin/bash
# Fetch the third-party sources the engine builds against into engine/third_party/.
#
#   engine/third_party/fetch.sh [--force]
#
#   nlohmann/json 3.12.0        MIT    single header; the whole engine needs it (src/json.h)
#   NeuralAmpModelerCore 0.6.0  MIT    + patches/nam-a2-full-neon.patch (Neural Amp effect only)
#   Eigen 3.4.0                 MPL2   (Neural Amp effect only)
#
# Everything is pinned: the two git repos by commit, the json header by SHA-256. Nothing is
# built or installed here. Without NAM and Eigen the engine still builds and the Neural Amp
# effect passes audio through unchanged (effects/nam.cpp checks with __has_include).
# Needs: git, curl, and shasum or sha256sum. Run it on a machine with internet, then sync the
# tree to the Pi (scripts/push.sh): the pedal itself never needs the network.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

JSON_VERSION=v3.12.0
JSON_URL="https://raw.githubusercontent.com/nlohmann/json/${JSON_VERSION}"
JSON_SHA256=aaf127c04cb31c406e5b04a63f1ae89369fccde6d8fa7cdda1ed4f32dfc5de63
JSON_LICENSE_SHA256=46a65cffd1ea955132d95a8dd921640714a8d6b537d2e4e482d31145ae95b603

NAM_REPO=https://github.com/sdatkinson/NeuralAmpModelerCore
NAM_TAG=v0.6.0
NAM_COMMIT=0b3d3c97b0859a3a8c92a8628c4dd89a25eb5842

EIGEN_REPO=https://gitlab.com/libeigen/eigen.git
EIGEN_TAG=3.4.0
EIGEN_COMMIT=3147391d946bb4b6c68edd901f2add6ac1f31f8c

sha256() { if command -v sha256sum >/dev/null; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi; }
say() { printf '== %s\n' "$*"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ------------------------------------------------------------------ nlohmann/json
if [ $FORCE -eq 1 ] || [ ! -f "$HERE/nlohmann/json.hpp" ]; then
    say "nlohmann/json $JSON_VERSION"
    curl -fsSL -o "$TMP/json.hpp" "$JSON_URL/single_include/nlohmann/json.hpp"
    curl -fsSL -o "$TMP/LICENSE.MIT" "$JSON_URL/LICENSE.MIT"
    [ "$(sha256 "$TMP/json.hpp")" = "$JSON_SHA256" ] || { echo "json.hpp: SHA-256 mismatch" >&2; exit 1; }
    [ "$(sha256 "$TMP/LICENSE.MIT")" = "$JSON_LICENSE_SHA256" ] || { echo "LICENSE.MIT: SHA-256 mismatch" >&2; exit 1; }
    mkdir -p "$HERE/nlohmann"
    cp "$TMP/json.hpp" "$TMP/LICENSE.MIT" "$HERE/nlohmann/"
else
    say "nlohmann/json: already there (use --force to refetch)"
fi

# ------------------------------------------------------------------ NeuralAmpModelerCore + patch
if [ $FORCE -eq 1 ] || [ ! -f "$HERE/NeuralAmpModelerCore/NAM/get_dsp.h" ]; then
    say "NeuralAmpModelerCore $NAM_TAG + NEON patch"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$NAM_TAG" "$NAM_REPO" "$TMP/nam"
    [ "$(git -C "$TMP/nam" rev-parse HEAD)" = "$NAM_COMMIT" ] || { echo "NeuralAmpModelerCore: unexpected commit" >&2; exit 1; }
    git -C "$TMP/nam" apply "$HERE/patches/nam-a2-full-neon.patch"
    rm -rf "$HERE/NeuralAmpModelerCore"
    mkdir -p "$HERE/NeuralAmpModelerCore"
    cp -R "$TMP/nam/NAM" "$HERE/NeuralAmpModelerCore/NAM"
    cp "$TMP/nam/LICENSE" "$HERE/NeuralAmpModelerCore/LICENSE"
else
    say "NeuralAmpModelerCore: already there (use --force to refetch)"
fi

# ------------------------------------------------------------------ Eigen
if [ $FORCE -eq 1 ] || [ ! -f "$HERE/eigen/Eigen/Dense" ]; then
    say "Eigen $EIGEN_TAG"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$EIGEN_TAG" "$EIGEN_REPO" "$TMP/eigen"
    [ "$(git -C "$TMP/eigen" rev-parse HEAD)" = "$EIGEN_COMMIT" ] || { echo "Eigen: unexpected commit" >&2; exit 1; }
    rm -rf "$HERE/eigen"
    mkdir -p "$HERE/eigen/unsupported/Eigen/src"
    cp -R "$TMP/eigen/Eigen" "$HERE/eigen/Eigen"
    # unsupported/: only the FFT module, which NAM's linear.cpp uses
    cp -R "$TMP/eigen/unsupported/Eigen/FFT" "$HERE/eigen/unsupported/Eigen/FFT"
    cp -R "$TMP/eigen/unsupported/Eigen/src/FFT" "$HERE/eigen/unsupported/Eigen/src/FFT"
    cp "$TMP/eigen"/COPYING.* "$HERE/eigen/"
else
    say "Eigen: already there (use --force to refetch)"
fi

say "done: $HERE"
