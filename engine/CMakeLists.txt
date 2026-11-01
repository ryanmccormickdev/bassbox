# bassbox engine build.
#
#   cmake -S engine -B engine/build && cmake --build engine/build -j4
#   sudo cmake --install engine/build            # -> /usr/local/bin (Pi)
#
# Targets: bassbox-engine (ALSA on Linux; --null-audio everywhere), bb-render, bb-catalog,
# bb-selftest. Effects are globbed from effects/*.cpp; leave a broken one out temporarily with
#   -DBB_EXCLUDE_EFFECTS="foo.cpp;bar.cpp"
# Building bb-catalog regenerates engine/catalog.json (-DBB_WRITE_CATALOG=OFF to skip).
cmake_minimum_required(VERSION 3.16)
project(bassbox LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -DNDEBUG")
if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)
  set(CMAKE_INSTALL_PREFIX /usr/local CACHE PATH "" FORCE)
endif()

option(BB_WRITE_CATALOG "Write engine/catalog.json after building bb-catalog" ON)
set(BB_EXCLUDE_EFFECTS "" CACHE STRING "Effect sources (file names) to leave out of the build")

add_compile_options(-Wall -Wextra)
# Raspberry Pi 4 (Cortex-A72). Native builds on the Pi only; the Mac keeps its defaults.
if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64")
  add_compile_options(-mcpu=cortex-a72)
endif()
# This Mac's CommandLineTools has a stray, partial usr/include/c++/v1 that shadows the SDK's
# libc++ headers (same workaround as engine/tests/Makefile).
if(APPLE)
  execute_process(COMMAND xcrun --show-sdk-path OUTPUT_VARIABLE BB_SDK OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
  if(BB_SDK AND EXISTS "${BB_SDK}/usr/include/c++/v1/cstdint")
    add_compile_options(-nostdinc++ "SHELL:-isystem ${BB_SDK}/usr/include/c++/v1")
  endif()
endif()

find_package(Threads REQUIRED)

# ---------------------------------------------------------------- effects library (globbed)
file(GLOB BB_EFFECT_SOURCES CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/effects/*.cpp)
foreach(x IN LISTS BB_EXCLUDE_EFFECTS)
  list(FILTER BB_EFFECT_SOURCES EXCLUDE REGEX "/${x}$")
  message(STATUS "bassbox: excluding effect ${x}")
endforeach()
# OBJECT library: every effect object is linked, so BB_REGISTER_EFFECT static registrars
# can't be dropped by the linker the way they would from a static archive.
add_library(bb_effects OBJECT ${BB_EFFECT_SOURCES})
target_include_directories(bb_effects PUBLIC include effects)
# effects/nam.cpp builds NeuralAmpModelerCore, which wants <Eigen/Dense> and "json.hpp" on the include
# path. third_party/fetch.sh puts them there; without them nam.cpp degrades to pass-through.
target_include_directories(bb_effects SYSTEM PRIVATE third_party/eigen third_party/nlohmann)

# ---------------------------------------------------------------- host core (portable)
add_library(bb_core OBJECT
  src/processor.cpp src/host.cpp src/catalog.cpp src/presets.cpp src/tuner.cpp
  src/builtin_effects.cpp src/wav.cpp)
target_include_directories(bb_core PUBLIC include src third_party)

# ---------------------------------------------------------------- tools
add_executable(bb-render src/bb_render.cpp)
target_link_libraries(bb-render PRIVATE bb_core bb_effects Threads::Threads)

add_executable(bb-catalog src/bb_catalog.cpp)
target_link_libraries(bb-catalog PRIVATE bb_core bb_effects)
if(BB_WRITE_CATALOG)
  add_custom_command(TARGET bb-catalog POST_BUILD
    COMMAND bb-catalog --out ${CMAKE_CURRENT_SOURCE_DIR}/catalog.json
    COMMENT "bb-catalog -> engine/catalog.json" VERBATIM)
endif()

add_executable(bb-selftest src/selftest.cpp)
target_link_libraries(bb-selftest PRIVATE bb_core bb_effects Threads::Threads)

# ---------------------------------------------------------------- engine
add_executable(bassbox-engine
  src/main_engine.cpp src/server.cpp src/jacks.cpp src/alsa_audio.cpp src/null_audio.cpp src/rt_thread.cpp)
target_link_libraries(bassbox-engine PRIVATE bb_core bb_effects Threads::Threads)

if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  find_package(ALSA)
  if(ALSA_FOUND)
    target_compile_definitions(bassbox-engine PRIVATE BB_HAVE_ALSA=1)
    target_link_libraries(bassbox-engine PRIVATE ALSA::ALSA)
  else()
    message(WARNING "ALSA not found: bassbox-engine will only support --null-audio (apt install libasound2-dev)")
  endif()
  find_package(PkgConfig)
  if(PKG_CONFIG_FOUND)
    pkg_check_modules(GPIOD IMPORTED_TARGET libgpiod>=2.0)
  endif()
  if(GPIOD_FOUND)
    target_compile_definitions(bassbox-engine PRIVATE BB_HAVE_GPIOD=1)
    target_link_libraries(bassbox-engine PRIVATE PkgConfig::GPIOD)
  else()
    message(WARNING "libgpiod v2 not found: jack sense disabled (apt install libgpiod-dev)")
  endif()
endif()

install(TARGETS bassbox-engine bb-render bb-catalog RUNTIME DESTINATION bin)

if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/tests/CMakeLists.txt)
  add_subdirectory(tests)
endif()
