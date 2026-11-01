// bassbox engine: the control-side core (state model, commands, presets, autosave, tuner).
// Shared by bassbox-engine (driven by the socket server) and bb-render (driven by a script).
//
// Runs on the CONTROL thread only. It owns every Slot and Chain: it builds them (all
// allocation and effect prepare() happen here), publishes chains to the Processor, frees the
// chains the audio thread hands back, and frees a Slot once no live chain references it.
// Parameter/enable/mix/master changes are plain atomic stores into objects the audio thread
// already sees; structural edits are coalesced and published one transition at a time.
#pragma once

#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <string>
#include <vector>

#include "json.h"
#include "presets.h"
#include "processor.h"
#include "tuner.h"

namespace bb {

// Filled in by the audio backend (ALSA or null); read by the Host for `meters`/`status`.
struct AudioStats {
    std::atomic<uint32_t> xruns{0};
    std::atomic<uint32_t> xrunsCapture{0}, xrunsPlayback{0};  // which side reported it
    std::atomic<uint32_t> lateMax{0};  // max capture backlog (frames) seen right after a read, last ~1 s
    AtomicF dspLoad{0.f};     // smoothed (~0.5 s) fraction of the period spent processing
    AtomicF dspLoadMax{0.f};  // max over the last ~1 s
    std::atomic<bool> running{false};
    std::atomic<bool> failed{false};  // backend gave up (device gone); engine exits -> restart
    json info;                        // static description (device, format, period...), set before start
};

struct HostConfig {
    float sampleRate = 48000.f;
    int maxBlock = kMaxBlock;
    std::string stateDir;    // "" = no presets / live state (bb-render)
    std::string factoryDir;  // repo presets/factory
    bool autosave = true;
    bool testEffects = false;  // list host test effects in the catalog
    double autosaveDelayS = 2.0;
};

// Per-client flags owned by the server; commands may update them.
struct ClientFlags {
    int id = 0;
    bool subscribed = false;
    bool meters = false;
};

struct CommandContext {
    ClientFlags* client = nullptr;
    bool stateChanged = false;    // -> `state` event to the other subscribed clients
    bool presetsChanged = false;  // -> `presets` event to the other subscribed clients
};

class Host {
public:
    Host(Processor& proc, HostConfig cfg, AudioStats* stats = nullptr);
    ~Host();  // the audio thread must be stopped

    // Restore live.json (or the first factory preset, or an empty chain) and publish it.
    std::string start();

    // One request -> one response ({"ok": ...}); never throws.
    json handle(const json& req, CommandContext& ctx);

    // Call every ~10 ms (and after every command): publishes pending chain edits, frees
    // retired chains and slots, runs the tuner, autosaves. nowNs = monotonic clock.
    void tick(int64_t nowNs);

    json stateJson() const;
    json presetJson() const;   // State minus uid/dirty (preset file format)
    json metersJson() const;
    json presetsJson() const;
    json jacksJson() const;
    json statusJson() const;

    // Jack sense (from the GPIO watcher). Returns true if anything changed.
    bool setJacks(bool input, bool output, bool muteOnUnplug);

    // Load a State/preset object as the live state (bb-render, `load_state`).
    bool loadState(const json& preset, const std::string& name, std::string& err,
                   std::vector<std::string>* warnings = nullptr);
    void flushAutosave();
    // true once every structural edit has been published (bb-render uses it to settle)
    bool settled() const { return !structureDirty_ && proc_.mailboxEmpty(); }
    const std::vector<uint32_t> uids() const;

private:
    struct SlotModel {
        uint32_t uid = 0;
        const EffectInfo* info = nullptr;
        bool enabled = true;
        float mix = 1.f;
        std::vector<float> params;
        Slot* slot = nullptr;
    };

    json cmd(const std::string& c, const json& req, CommandContext& ctx);
    SlotModel* find(uint32_t uid);
    int indexOf(uint32_t uid) const;
    SlotModel makeSlot(const EffectInfo* info, const json* params, bool enabled, float mix,
                       std::vector<std::string>* warnings);
    bool applyPreset(const json& p, std::string& err, std::vector<std::string>* warnings);
    void setMaster(float inDb, float outDb, bool bypass);
    json normalized() const;
    bool dirty() const;
    void changed(CommandContext* ctx);
    void publishIfPossible();
    void collectRetired();
    void gcSlots();
    void writeLive();

    Processor& proc_;
    HostConfig cfg_;
    AudioStats* stats_;
    PresetStore store_;
    std::unique_ptr<FileWorker> files_;  // durable writes off the control thread

    std::vector<SlotModel> chain_;
    float inDb_ = 0.f, outDb_ = 0.f;
    bool bypass_ = false;
    std::string name_;
    json baseline_;

    std::map<uint32_t, std::unique_ptr<Slot>> slots_;
    std::deque<Chain*> live_;  // published and not yet retired, oldest first
    bool structureDirty_ = false;
    uint32_t nextUid_ = 1;
    uint64_t serial_ = 0;

    bool liveDirty_ = false;
    int64_t liveChangedAt_ = 0, now_ = 0;
    std::string livePath_;

    Tuner tuner_;
    bool tunerOn_ = false;
    TunerResult tunerResult_;
    int64_t lastTunerNs_ = 0;
    std::vector<float> tunerBuf_;

    bool toneOn_ = false;
    float toneHz_ = 110.f, toneDb_ = -18.f;
    int64_t toneStartNs_ = 0;

    bool jackIn_ = true, jackOut_ = true, jacksKnown_ = false;
    int64_t startNs_ = 0;
};

}  // namespace bb
