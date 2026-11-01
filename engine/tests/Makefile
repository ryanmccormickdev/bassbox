# bassbox effects harness. Standalone: does not touch engine/CMakeLists.txt.
# Works with Apple clang on the Mac and GCC 14 on the Pi (adds -mcpu=cortex-a72 there).
#
#   make                 build $(BIN)
#   make test            all checks + WAVs + out/report.md        (FAST=1 for a quick pass)
#   make bench           CPU per 64-frame block -> out/bench-<host>.md   (Pi: taskset -c 2)
#   make catalog         regenerate engine/catalog.json from the registry
#   make probe plots     probe renders + PNGs in out/plots/ (python3 with numpy+matplotlib)
#   make selftest        dsp.h oversampler / crossover checks
#   make ONLY=chorus,flanger test         subset of effects
#   make EFFECTS="../effects/registry.cpp ../effects/wip/foo.cpp" BUILD=../build-fx/foo test
#                                         build only some sources (e.g. work in progress)

ROOT    := $(abspath ..)
REPO    := $(abspath ../..)
UNAME_S := $(shell uname -s)
UNAME_M := $(shell uname -m)
BUILD   ?= $(ROOT)/build-fx/$(UNAME_S)-$(UNAME_M)
EFFECTS ?= $(wildcard $(ROOT)/effects/*.cpp)
SRCS    := $(abspath $(EFFECTS)) $(ROOT)/tests/fxtest.cpp
OBJS    := $(patsubst $(ROOT)/%.cpp,$(BUILD)/obj/%.o,$(SRCS))
BIN     := $(BUILD)/fxtest
OUT     ?= $(ROOT)/tests/out
PY      ?= $(shell command -v uv >/dev/null 2>&1 && echo "uv run --quiet --with numpy --with matplotlib python" || echo python3)

ifeq ($(UNAME_S),Linux)
  CXX      ?= g++
  ARCH     ?= -mcpu=cortex-a72
  LDLIBS   += -lpthread
  TASKSET  ?= taskset -c 2
else
  ARCH     ?=
  TASKSET  ?=
  # This Mac's CommandLineTools has a stray, partial usr/include/c++/v1 that shadows the SDK's
  # libc++ headers; point clang at the SDK copy explicitly.
  SDK      := $(shell xcrun --show-sdk-path 2>/dev/null)
  ifneq ($(wildcard $(SDK)/usr/include/c++/v1/cstdint),)
    CPPFLAGS += -nostdinc++ -isystem $(SDK)/usr/include/c++/v1
  endif
endif
CXXFLAGS ?= -std=c++17 -O3 $(ARCH) -Wall -Wextra
CPPFLAGS += -I$(ROOT)/include -I$(ROOT)/effects -MMD -MP
# the Neural Amp effect (effects/nam.cpp) needs Eigen and nlohmann/json from third_party/fetch.sh; without them it passes audio through
CPPFLAGS += -isystem $(ROOT)/third_party/eigen -isystem $(ROOT)/third_party/nlohmann

ONLYARG := $(if $(ONLY),--only $(ONLY),)
FASTARG := $(if $(FAST),--quick,)

.PHONY: all test bench catalog probe plots selftest list levels presets clean
all: $(BIN)

$(BIN): $(OBJS)
	$(CXX) $(CXXFLAGS) $(OBJS) -o $@ $(LDLIBS)

$(BUILD)/obj/%.o: $(ROOT)/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c $< -o $@

-include $(OBJS:.o=.d)

test: $(BIN)
	$(BIN) test --repo $(REPO) --out $(OUT) $(ONLYARG) $(FASTARG)

bench: $(BIN)
	$(TASKSET) $(BIN) bench --repo $(REPO) --out $(OUT) $(ONLYARG)

catalog: $(BIN)
	$(BIN) catalog --repo $(REPO)

probe: $(BIN)
	$(BIN) probe --repo $(REPO) --out $(OUT) $(ONLYARG)

plots: probe
	$(PY) $(ROOT)/tests/plots.py $(OUT)

selftest: $(BIN)
	$(BIN) selftest --repo $(REPO)

presets: $(BIN)
	$(BIN) presets --repo $(REPO) --out $(OUT)

levels: $(BIN)
	$(BIN) levels --repo $(REPO) --out $(OUT) $(ONLYARG)

list: $(BIN)
	$(BIN) list --repo $(REPO)

clean:
	rm -rf $(BUILD)
