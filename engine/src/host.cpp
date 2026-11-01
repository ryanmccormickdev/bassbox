#include "host.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <set>

#include "catalog.h"

namespace bb {

namespace {

constexpr float kInDbMin = -24.f, kInDbMax = 24.f;
constexpr float kOutDbMin = -60.f, kOutDbMax = 12.f;

inline float dbToLin(float db) { return std::pow(10.f, db / 20.f); }
inline float clampf(float v, float lo, float hi) { return std::min(hi, std::max(lo, v)); }

json okResp() { return json{{"ok", true}}; }
json errResp(const std::string& e) { return json{{"ok", false}, {"error", e}}; }

// Thrown inside command handlers for bad arguments; turned into {"ok":false,"error":...}.
struct BadArg {
    std::string msg;
};

uint32_t argUid(const json& r) {
    auto it = r.find("uid");
    if (it == r.end() || !it->is_number_integer() || it->get<int64_t>() <= 0) throw BadArg{"'uid' (positive integer) required"};
    return uint32_t(it->get<int64_t>());
}
bool argBool(const json& r, const char* k, bool* present = nullptr) {
    auto it = r.find(k);
    if (it == r.end() || it->is_null()) {
        if (present) {
            *present = false;
            return false;
        }
        throw BadArg{std::string("'") + k + "' (bool) required"};
    }
    if (present) *present = true;
    if (it->is_boolean()) return it->get<bool>();
    if (it->is_number()) return it->get<double>() != 0.0;
    throw BadArg{std::string("'") + k + "' must be a bool"};
}
float argNum(const json& r, const char* k, bool* present = nullptr) {
    auto it = r.find(k);
    if (it == r.end() || it->is_null()) {
        if (present) {
            *present = false;
            return 0.f;
        }
        throw BadArg{std::string("'") + k + "' (number) required"};
    }
    if (present) *present = true;
    if (it->is_boolean()) return it->get<bool>() ? 1.f : 0.f;
    if (!it->is_number()) throw BadArg{std::string("'") + k + "' must be a number"};
    const double v = it->get<double>();
    if (!std::isfinite(v)) throw BadArg{std::string("'") + k + "' must be finite"};
    return float(v);
}
std::string argStr(const json& r, const char* k, bool required = true) {
    auto it = r.find(k);
    if (it == r.end() || it->is_null()) {
        if (required) throw BadArg{std::string("'") + k + "' (string) required"};
        return "";
    }
    if (!it->is_string()) throw BadArg{std::string("'") + k + "' must be a string"};
    return it->get<std::string>();
}

int paramIndex(const EffectInfo& info, const json& p) {
    if (p.is_number_integer()) {
        const int i = p.get<int>();
        if (i >= 0 && i < int(info.params.size())) return i;
    } else if (p.is_string()) {
        const std::string id = p.get<std::string>();
        for (size_t i = 0; i < info.params.size(); ++i)
            if (info.params[i].id == id) return int(i);
    }
    return -1;
}

// JSON value for a parameter -> float in the parameter's units (accepts bools and enum labels).
bool paramValue(const ParamSpec& ps, const json& v, float& out) {
    if (v.is_boolean()) {
        out = v.get<bool>() ? 1.f : 0.f;
        return true;
    }
    if (v.is_number()) {
        out = float(v.get<double>());
        return std::isfinite(out);
    }
    if (v.is_string() && ps.type == ParamType::Enum) {
        const std::string s = v.get<std::string>();
        for (size_t i = 0; i < ps.options.size(); ++i)
            if (ps.options[i] == s) {
                out = float(i);
                return true;
            }
    }
    return false;
}

}  // namespace

Host::Host(Processor& proc, HostConfig cfg, AudioStats* stats)
    : proc_(proc),
      cfg_(std::move(cfg)),
      stats_(stats),
      store_(cfg_.factoryDir, cfg_.stateDir.empty() ? "" : cfg_.stateDir + "/presets"),
      tuner_(cfg_.sampleRate),
      tunerBuf_(4096) {
    if (!cfg_.stateDir.empty()) {
        livePath_ = cfg_.stateDir + "/live.json";
        files_ = std::make_unique<FileWorker>();
        store_.setWorker(files_.get());
    }
    baseline_ = normalized();
    startNs_ = nowNs();
}

Host::~Host() {
    if (files_) files_->flush();
    // Audio is stopped: every published chain is ours again (mailbox, current, retired).
    for (Chain* c : live_) delete c;
    live_.clear();
    chain_.clear();
    slots_.clear();
}

// ------------------------------------------------------------------ state model

const std::vector<uint32_t> Host::uids() const {
    std::vector<uint32_t> v;
    for (auto& s : chain_) v.push_back(s.uid);
    return v;
}

Host::SlotModel* Host::find(uint32_t uid) {
    for (auto& s : chain_)
        if (s.uid == uid) return &s;
    return nullptr;
}

int Host::indexOf(uint32_t uid) const {
    for (size_t i = 0; i < chain_.size(); ++i)
        if (chain_[i].uid == uid) return int(i);
    return -1;
}

// Create a new effect instance (control thread: allocation + prepare happen here, before the
// audio thread can see it).
Host::SlotModel Host::makeSlot(const EffectInfo* info, const json* params, bool enabled, float mix,
                               std::vector<std::string>* warnings) {
    SlotModel m;
    m.uid = nextUid_++;
    m.info = info;
    m.enabled = enabled;
    m.mix = clampf(mix, 0.f, 1.f);
    const int np = std::min<int>(int(info->params.size()), kMaxParams);
    if (int(info->params.size()) > kMaxParams && warnings)
        warnings->push_back(info->id + ": only the first 16 params are supported");
    m.params.resize(size_t(np));
    for (int i = 0; i < np; ++i) m.params[size_t(i)] = sanitizeParam(info->params[size_t(i)], info->params[size_t(i)].def);
    if (params && params->is_object()) {
        for (auto it = params->begin(); it != params->end(); ++it) {
            const int i = paramIndex(*info, json(it.key()));
            float v;
            if (i < 0 || i >= np) {
                if (warnings) warnings->push_back(info->id + ": unknown param '" + it.key() + "' ignored");
                continue;
            }
            if (!paramValue(info->params[size_t(i)], it.value(), v)) {
                if (warnings) warnings->push_back(info->id + ": bad value for '" + it.key() + "'");
                continue;
            }
            m.params[size_t(i)] = sanitizeParam(info->params[size_t(i)], v);
        }
    }

    std::unique_ptr<Effect> fx = info->create();
    fx->prepare(cfg_.sampleRate, cfg_.maxBlock);
    auto slot = std::make_unique<Slot>(m.uid, info, std::move(fx));
    // Initial parameter values: the effect is not published yet, so calling its audio-thread
    // methods here cannot race. reset() afterwards so smoothers start at the targets.
    for (int i = 0; i < np; ++i) {
        slot->param[i].store(m.params[size_t(i)], std::memory_order_relaxed);
        slot->applied[i] = m.params[size_t(i)];
        slot->fx->setParam(i, m.params[size_t(i)]);
    }
    slot->fx->ready();  // effect.h: once, after the initial params, before the slot goes live
    slot->fx->reset();
    slot->enabled.store(enabled ? 1.f : 0.f, std::memory_order_relaxed);
    slot->mix.store(m.mix, std::memory_order_relaxed);
    slot->e = enabled ? 1.f : 0.f;
    slot->m = m.mix;
    slot->running = enabled;
    slot->seenSeq = slot->paramSeq.load(std::memory_order_relaxed);
    m.slot = slot.get();
    slots_[m.uid] = std::move(slot);
    return m;
}

void Host::setMaster(float inDb, float outDb, bool bypass) {
    inDb_ = clampf(inDb, kInDbMin, kInDbMax);
    outDb_ = clampf(outDb, kOutDbMin, kOutDbMax);
    bypass_ = bypass;
    proc_.ctl.inGain.store(dbToLin(inDb_));
    proc_.ctl.outGain.store(dbToLin(outDb_));
    proc_.ctl.bypass.store(bypass_, std::memory_order_relaxed);
}

bool Host::applyPreset(const json& p, std::string& err, std::vector<std::string>* warnings) {
    if (!p.is_object()) {
        err = "preset must be a JSON object";
        return false;
    }
    float inDb = 0.f, outDb = 0.f;
    bool byp = false;
    if (auto m = p.find("master"); m != p.end() && m->is_object()) {
        if (auto v = m->find("in_gain_db"); v != m->end() && v->is_number()) inDb = v->get<float>();
        if (auto v = m->find("out_gain_db"); v != m->end() && v->is_number()) outDb = v->get<float>();
        if (auto v = m->find("bypass"); v != m->end() && v->is_boolean()) byp = v->get<bool>();
    }
    std::vector<SlotModel> next;
    if (auto c = p.find("chain"); c != p.end()) {
        if (!c->is_array()) {
            err = "'chain' must be an array";
            return false;
        }
        for (auto& e : *c) {
            if (!e.is_object()) continue;
            const std::string type = e.value("type", std::string());
            const EffectInfo* info = findEffect(type);
            if (!info) {
                if (warnings) warnings->push_back("unknown effect type '" + type + "' skipped");
                continue;
            }
            if (next.size() >= size_t(kMaxSlots)) {
                if (warnings) warnings->push_back("chain truncated to 16 slots");
                break;
            }
            bool en = true;
            float mix = 1.f;
            if (auto v = e.find("enabled"); v != e.end() && v->is_boolean()) en = v->get<bool>();
            if (auto v = e.find("mix"); v != e.end() && v->is_number()) mix = v->get<float>();
            const json* params = nullptr;
            if (auto v = e.find("params"); v != e.end()) params = &*v;
            next.push_back(makeSlot(info, params, en, mix, warnings));
        }
    }
    chain_ = std::move(next);  // old SlotModels go; their Slots are freed by gcSlots()
    setMaster(inDb, outDb, byp);
    structureDirty_ = true;
    return true;
}

json Host::presetJson() const {
    json chain = json::array();
    for (auto& s : chain_) {
        json params = json::object();
        for (size_t i = 0; i < s.params.size(); ++i) params[s.info->params[i].id] = jnum(s.params[i]);
        chain.push_back(json{{"type", s.info->id}, {"enabled", s.enabled}, {"mix", jnum(s.mix)}, {"params", params}});
    }
    return json{{"name", name_},
                {"master", {{"in_gain_db", jnum(inDb_)}, {"out_gain_db", jnum(outDb_)}, {"bypass", bypass_}}},
                {"chain", chain}};
}

json Host::normalized() const {
    json j = presetJson();
    j.erase("name");
    return j;
}

bool Host::dirty() const { return normalized() != baseline_; }

json Host::stateJson() const {
    json chain = json::array();
    for (auto& s : chain_) {
        json params = json::object();
        for (size_t i = 0; i < s.params.size(); ++i) params[s.info->params[i].id] = jnum(s.params[i]);
        chain.push_back(json{{"uid", s.uid}, {"type", s.info->id}, {"enabled", s.enabled}, {"mix", jnum(s.mix)}, {"params", params}});
    }
    return json{{"name", name_},
                {"master", {{"in_gain_db", jnum(inDb_)}, {"out_gain_db", jnum(outDb_)}, {"bypass", bypass_}}},
                {"chain", chain},
                {"dirty", dirty()}};
}

json Host::presetsJson() const {
    json list = json::array();
    for (auto& e : store_.list()) {
        json j{{"name", e.name}, {"factory", e.factory}};
        if (e.overridesFactory) j["modified_factory"] = true;
        list.push_back(j);
    }
    return json{{"presets", list}, {"current", name_}, {"dirty", dirty()}};
}

json Host::jacksJson() const { return json{{"input", jackIn_}, {"output", jackOut_}}; }

json Host::metersJson() const {
    const Meters& m = proc_.meters;
    const uint64_t frames = m.frames.load(std::memory_order_relaxed);
    const uint64_t latch = uint64_t(cfg_.sampleRate);  // 1 s
    auto latched = [&](const std::atomic<uint64_t>& at) {
        const uint64_t a = at.load(std::memory_order_relaxed);
        return a != 0 && frames - (a - 1) < latch;
    };
    auto num = [](float v) { return std::isfinite(v) ? jnum(v) : 0.0; };
    json slots = json::object();
    for (auto& s : chain_) {
        float a = s.enabled ? s.slot->fx->activity() : 0.f;  // plain float read, see effect.h
        if (!std::isfinite(a)) a = 0.f;
        slots[std::to_string(s.uid)] = jnum(clampf(a, 0.f, 1.f));
    }
    json j{{"in_peak", num(m.inPeak.load())},
           {"in_rms", num(m.inRms.load())},
           {"out_peak", num(m.outPeak.load())},
           {"out_rms", num(m.outRms.load())},
           {"dsp_load", stats_ ? num(stats_->dspLoad.load()) : 0.0},
           {"dsp_load_max", stats_ ? num(stats_->dspLoadMax.load()) : 0.0},
           {"xruns", stats_ ? stats_->xruns.load(std::memory_order_relaxed) : 0u},
           {"slots", slots},
           {"clip_in", latched(m.clipInAt)},
           {"clip_out", latched(m.clipOutAt)},
           {"test_tone", toneOn_}};
    if (tunerOn_) {
        const TunerResult& t = tunerResult_;
        j["tuner"] = json{{"hz", jnum(t.hz)}, {"note", t.note}, {"octave", t.octave}, {"cents", jnum(t.cents)},
                          {"confidence", jnum(t.confidence)}};
    }
    return j;
}

json Host::statusJson() const {
    json j{{"engine", "bassbox-engine"},
           {"version", 1},
           {"sample_rate", cfg_.sampleRate},
           {"uptime_s", jnum(float(double(nowNs() - startNs_) * 1e-9))},
           {"frames", proc_.meters.frames.load(std::memory_order_relaxed)},
           {"transitions", proc_.meters.transitions.load(std::memory_order_relaxed)},
           {"nan_resets", proc_.meters.nanResets.load(std::memory_order_relaxed)},
           {"live_slots", slots_.size()},
           {"live_chains", live_.size()},
           {"tuner", tunerOn_}};
    if (stats_) {
        j["audio"] = stats_->info;
        j["audio_running"] = stats_->running.load();
        j["xruns"] = stats_->xruns.load();
        j["xruns_capture"] = stats_->xrunsCapture.load();
        j["xruns_playback"] = stats_->xrunsPlayback.load();
        j["late_max_frames"] = stats_->lateMax.load();
        j["dsp_load"] = jnum(stats_->dspLoad.load());
        j["dsp_load_max"] = jnum(stats_->dspLoadMax.load());
    }
    return j;
}

bool Host::setJacks(bool input, bool output, bool muteOnUnplug) {
    const bool ch = !jacksKnown_ || input != jackIn_ || output != jackOut_;
    jacksKnown_ = true;
    jackIn_ = input;
    jackOut_ = output;
    proc_.ctl.inputMute.store(muteOnUnplug && !input, std::memory_order_relaxed);
    return ch;
}

void Host::changed(CommandContext* ctx) {
    if (ctx) ctx->stateChanged = true;
    liveDirty_ = true;
    liveChangedAt_ = now_ ? now_ : nowNs();
}

bool Host::loadState(const json& preset, const std::string& name, std::string& err, std::vector<std::string>* warnings) {
    if (!applyPreset(preset, err, warnings)) return false;
    name_ = name;
    baseline_ = normalized();
    changed(nullptr);
    publishIfPossible();
    return true;
}

// ------------------------------------------------------------------ chain publication

void Host::publishIfPossible() {
    if (!structureDirty_ || !proc_.mailboxEmpty()) return;
    auto* c = new Chain;
    c->serial = ++serial_;
    for (auto& s : chain_) c->slots.push_back(s.slot);
    const Chain* prev = live_.empty() ? nullptr : live_.back();
    if (!prev) {
        for (Slot* s : c->slots) s->p = s->pTarget = 1.f;
    } else {
        const auto& A = prev->slots;
        const auto& B = c->slots;
        bool shared = false;
        for (Slot* s : B) shared = shared || std::find(A.begin(), A.end(), s) != A.end();
        if (!shared) {
            c->parallel = true;
            for (Slot* s : B) s->p = s->pTarget = 1.f;
        } else {
            // Longest common subsequence of A and B (by instance): those slots keep running at
            // full presence; the rest of A fades to identity before the swap, and B's new or
            // moved slots fade in after it. n <= 16, so the O(n^2) table is trivial.
            const size_t na = A.size(), nb = B.size();
            std::vector<std::vector<int>> L(na + 1, std::vector<int>(nb + 1, 0));
            for (size_t i = na; i-- > 0;)
                for (size_t j = nb; j-- > 0;)
                    L[i][j] = A[i] == B[j] ? L[i + 1][j + 1] + 1 : std::max(L[i + 1][j], L[i][j + 1]);
            std::set<Slot*> keep;
            for (size_t i = 0, j = 0; i < na && j < nb;) {
                if (A[i] == B[j]) {
                    keep.insert(A[i]);
                    ++i;
                    ++j;
                } else if (L[i + 1][j] >= L[i][j + 1]) {
                    ++i;
                } else {
                    ++j;
                }
            }
            for (Slot* s : A)
                if (!keep.count(s)) c->fadeOutFirst.push_back(s);
            for (Slot* s : B)
                if (!s->published) s->p = s->pTarget = 0.f;  // moved slots reach 0 in phase 1
        }
    }
    for (Slot* s : c->slots) s->published = true;
    if (!proc_.publish(c)) {  // cannot happen (mailbox checked, single producer); be safe
        delete c;
        return;
    }
    live_.push_back(c);
    structureDirty_ = false;
}

void Host::collectRetired() {
    bool any = false;
    while (Chain* r = proc_.takeRetired()) {
        auto it = std::find(live_.begin(), live_.end(), r);
        if (it != live_.end()) live_.erase(it);
        delete r;
        any = true;
    }
    if (any) gcSlots();
}

void Host::gcSlots() {
    std::set<Slot*> used;
    for (auto& s : chain_) used.insert(s.slot);
    for (Chain* c : live_)
        for (Slot* s : c->slots) used.insert(s);
    for (auto it = slots_.begin(); it != slots_.end();) {
        if (!used.count(it->second.get())) it = slots_.erase(it);  // ~Effect on the control thread
        else ++it;
    }
}

// ------------------------------------------------------------------ lifecycle

std::string Host::start() {
    std::string log;
    if (store_.enabled()) {
        makeDirs(cfg_.stateDir);
        log += store_.syncFactory() + "\n";
    }
    bool restored = false;
    std::string txt, err;
    if (!livePath_.empty() && readFile(livePath_, txt)) {
        json j = json::parse(txt, nullptr, false);
        if (!j.is_discarded() && j.is_object() && j.contains("state")) {
            std::vector<std::string> w;
            if (applyPreset(j["state"], err, &w)) {
                name_ = j.value("current", std::string());
                baseline_ = j.contains("baseline") ? j["baseline"] : normalized();
                restored = true;
                log += "restored live state (" + std::to_string(chain_.size()) + " slots, preset '" + name_ + "')\n";
                for (auto& s : w) log += "  warning: " + s + "\n";
            }
        }
        if (!restored) log += "live.json unreadable, ignored\n";
    }
    if (!restored) {
        auto list = store_.list();
        if (!list.empty()) {
            json p;
            std::vector<std::string> w;
            if (store_.load(list[0].name, p, err) && applyPreset(p, err, &w)) {
                name_ = list[0].name;
                baseline_ = normalized();
                log += "loaded preset '" + name_ + "'\n";
            }
        }
    }
    structureDirty_ = true;
    publishIfPossible();
    return log;
}

void Host::writeLive() {
    liveDirty_ = false;
    if (livePath_.empty() || !cfg_.autosave) return;
    json j{{"version", 1}, {"state", presetJson()}, {"current", name_}, {"baseline", baseline_}};
    // serialized here, written + fsynced by the worker (latest wins), so the control loop never
    // stalls on the SD card
    if (files_) files_->write(livePath_, j.dump(1) + "\n");
}

void Host::flushAutosave() {
    if (liveDirty_) writeLive();
    if (files_) files_->flush();
}

void Host::tick(int64_t nowNs_) {
    now_ = nowNs_;
    collectRetired();
    publishIfPossible();
    if (tunerOn_) {
        size_t n;
        while ((n = proc_.tunerRing().popMany(tunerBuf_.data(), tunerBuf_.size())) > 0) tuner_.feed(tunerBuf_.data(), int(n));
        if (now_ - lastTunerNs_ >= 50'000'000) {  // ~20 Hz
            lastTunerNs_ = now_;
            tunerResult_ = tuner_.analyze();
        }
    }
    if (toneOn_ && now_ - toneStartNs_ >= 30'000'000'000LL) {  // test tone switches itself off
        toneOn_ = false;
        proc_.ctl.toneOn.store(false, std::memory_order_relaxed);
    }
    if (liveDirty_ && double(now_ - liveChangedAt_) * 1e-9 >= cfg_.autosaveDelayS) writeLive();
}

// ------------------------------------------------------------------ commands

json Host::handle(const json& req, CommandContext& ctx) {
    json resp;
    if (!req.is_object()) {
        resp = errResp("request must be a JSON object");
    } else {
        auto c = req.find("cmd");
        if (c == req.end() || !c->is_string()) {
            resp = errResp("'cmd' (string) required");
        } else {
            try {
                resp = cmd(c->get<std::string>(), req, ctx);
            } catch (const BadArg& e) {
                resp = errResp(e.msg);
            } catch (const std::exception& e) {
                resp = errResp(std::string("internal error: ") + e.what());
            }
        }
    }
    if (req.is_object()) {
        if (auto id = req.find("id"); id != req.end()) resp["id"] = *id;
    }
    publishIfPossible();
    return resp;
}

json Host::cmd(const std::string& c, const json& r, CommandContext& ctx) {
    if (c == "catalog") {
        json j = catalogJson(cfg_.testEffects || r.value("all", false));
        j["ok"] = true;
        return j;
    }
    if (c == "state") return json{{"ok", true}, {"state", stateJson()}};
    if (c == "meters") {
        json j = metersJson();
        j["ok"] = true;
        return j;
    }
    if (c == "jacks") {
        json j = jacksJson();
        j["ok"] = true;
        return j;
    }
    if (c == "presets") {
        json j = presetsJson();
        j["ok"] = true;
        return j;
    }
    if (c == "status") {
        json j = statusJson();
        j["ok"] = true;
        return j;
    }
    if (c == "ping") return json{{"ok", true}, {"pong", true}};

    if (c == "subscribe") {
        if (!ctx.client) return errResp("subscribe needs a socket client");
        ctx.client->subscribed = true;
        ctx.client->meters = r.value("meters", false);
        return json{{"ok", true}, {"meters", ctx.client->meters}};
    }
    if (c == "unsubscribe") {
        if (ctx.client) ctx.client->subscribed = ctx.client->meters = false;
        return okResp();
    }

    if (c == "add") {
        const std::string type = argStr(r, "type");
        const EffectInfo* info = findEffect(type);
        if (!info) return errResp("unknown effect type '" + type + "'");
        if (chain_.size() >= size_t(kMaxSlots)) return errResp("chain is full (16 slots)");
        int index = int(chain_.size());
        if (auto it = r.find("index"); it != r.end() && !it->is_null()) {
            if (!it->is_number_integer()) throw BadArg{"'index' must be an integer"};
            index = std::max(0, std::min(int(chain_.size()), it->get<int>()));
        }
        bool hasEn = false, hasMix = false;
        const bool en = argBool(r, "enabled", &hasEn);
        const float mix = argNum(r, "mix", &hasMix);
        std::vector<std::string> warnings;
        const json* params = r.contains("params") ? &r["params"] : nullptr;
        SlotModel m = makeSlot(info, params, hasEn ? en : true, hasMix ? mix : 1.f, &warnings);
        const uint32_t uid = m.uid;
        chain_.insert(chain_.begin() + index, std::move(m));
        structureDirty_ = true;
        changed(&ctx);
        json j{{"ok", true}, {"uid", uid}, {"index", index}};
        if (!warnings.empty()) j["warnings"] = warnings;
        return j;
    }
    if (c == "remove") {
        const int i = indexOf(argUid(r));
        if (i < 0) return errResp("no slot with that uid");
        chain_.erase(chain_.begin() + i);
        structureDirty_ = true;
        changed(&ctx);
        return okResp();
    }
    if (c == "move") {
        const uint32_t uid = argUid(r);
        const int i = indexOf(uid);
        if (i < 0) return errResp("no slot with that uid");
        auto it = r.find("index");
        if (it == r.end() || !it->is_number_integer()) throw BadArg{"'index' (integer) required"};
        const int to = std::max(0, std::min(int(chain_.size()) - 1, it->get<int>()));
        if (to != i) {
            SlotModel m = std::move(chain_[size_t(i)]);
            chain_.erase(chain_.begin() + i);
            chain_.insert(chain_.begin() + to, std::move(m));
            structureDirty_ = true;
            changed(&ctx);
        }
        return json{{"ok", true}, {"index", to}};
    }
    if (c == "set_param") {
        SlotModel* s = find(argUid(r));
        if (!s) return errResp("no slot with that uid");
        auto p = r.find("param");
        if (p == r.end()) throw BadArg{"'param' (id) required"};
        const int i = paramIndex(*s->info, *p);
        if (i < 0 || i >= int(s->params.size())) return errResp("unknown param for " + s->info->id);
        auto v = r.find("value");
        float val;
        if (v == r.end() || !paramValue(s->info->params[size_t(i)], *v, val)) throw BadArg{"'value' (number) required"};
        val = sanitizeParam(s->info->params[size_t(i)], val);
        if (val != s->params[size_t(i)]) {
            s->params[size_t(i)] = val;
            s->slot->param[i].store(val, std::memory_order_relaxed);
            s->slot->paramSeq.fetch_add(1, std::memory_order_release);
            changed(&ctx);
        }
        return json{{"ok", true}, {"value", jnum(val)}};
    }
    if (c == "set_enabled") {
        SlotModel* s = find(argUid(r));
        if (!s) return errResp("no slot with that uid");
        const bool en = argBool(r, "enabled");
        if (en != s->enabled) {
            s->enabled = en;
            s->slot->enabled.store(en ? 1.f : 0.f, std::memory_order_relaxed);
            changed(&ctx);
        }
        return json{{"ok", true}, {"enabled", en}};
    }
    if (c == "set_mix") {
        SlotModel* s = find(argUid(r));
        if (!s) return errResp("no slot with that uid");
        const float mix = clampf(argNum(r, "mix"), 0.f, 1.f);
        if (mix != s->mix) {
            s->mix = mix;
            s->slot->mix.store(mix, std::memory_order_relaxed);
            changed(&ctx);
        }
        return json{{"ok", true}, {"mix", jnum(mix)}};
    }
    if (c == "set_master") {
        bool hi = false, ho = false, hb = false;
        const float in = argNum(r, "in_gain_db", &hi);
        const float out = argNum(r, "out_gain_db", &ho);
        const bool b = argBool(r, "bypass", &hb);
        if (!hi && !ho && !hb) throw BadArg{"give in_gain_db, out_gain_db and/or bypass"};
        const float oi = inDb_, oo = outDb_;
        const bool ob = bypass_;
        setMaster(hi ? in : inDb_, ho ? out : outDb_, hb ? b : bypass_);
        if (oi != inDb_ || oo != outDb_ || ob != bypass_) changed(&ctx);
        return json{{"ok", true}, {"master", {{"in_gain_db", jnum(inDb_)}, {"out_gain_db", jnum(outDb_)}, {"bypass", bypass_}}}};
    }
    if (c == "tuner") {
        const bool on = argBool(r, "on");
        if (on != tunerOn_) {
            tunerOn_ = on;
            tuner_.reset();
            tunerResult_ = TunerResult();
            proc_.tunerRing().clear();
            proc_.ctl.tunerOn.store(on, std::memory_order_relaxed);
        }
        return json{{"ok", true}, {"on", tunerOn_}};
    }
    if (c == "test_tone") {  // {on, hz = 110, db = -18}: output-path check, auto-off after 30 s
        const bool on = argBool(r, "on");
        bool hh = false, hd = false;
        const float hz = argNum(r, "hz", &hh), db = argNum(r, "db", &hd);
        toneHz_ = clampf(hh ? hz : 110.f, 20.f, 20000.f);
        toneDb_ = clampf(hd ? db : -18.f, -60.f, -6.f);
        proc_.ctl.toneHz.store(toneHz_);
        proc_.ctl.toneAmp.store(dbToLin(toneDb_));
        if (on) toneStartNs_ = now_;  // (re)starting restarts the 30 s timer
        toneOn_ = on;
        proc_.ctl.toneOn.store(on, std::memory_order_relaxed);
        return json{{"ok", true}, {"on", toneOn_}, {"hz", jnum(toneHz_)}, {"db", jnum(toneDb_)}, {"auto_off_s", 30}};
    }
    if (c == "load_preset") {
        const std::string name = argStr(r, "name");
        json p;
        std::string err;
        if (!store_.load(name, p, err)) return errResp(err);
        std::vector<std::string> w;
        if (!applyPreset(p, err, &w)) return errResp(err);
        name_ = p.value("name", name);
        baseline_ = normalized();
        changed(&ctx);
        json j{{"ok", true}, {"state", stateJson()}};
        if (!w.empty()) j["warnings"] = w;
        return j;
    }
    if (c == "load_state") {  // extension: load a State/preset object without saving it
        auto st = r.find("state");
        if (st == r.end()) throw BadArg{"'state' (object) required"};
        std::string err;
        std::vector<std::string> w;
        if (!applyPreset(*st, err, &w)) return errResp(err);
        name_ = st->value("name", std::string());
        baseline_ = r.value("clean", false) ? normalized() : json();
        changed(&ctx);
        json j{{"ok", true}, {"state", stateJson()}};
        if (!w.empty()) j["warnings"] = w;
        return j;
    }
    if (c == "save_preset") {
        std::string name = argStr(r, "name", false);
        if (name.empty()) name = name_;
        if (name.empty()) return errResp("no preset name given and no current preset");
        std::string err;
        json p = presetJson();
        if (!store_.save(name, p, err)) return errResp(err);
        name_ = name;
        baseline_ = normalized();
        ctx.presetsChanged = true;
        changed(&ctx);
        return json{{"ok", true}, {"name", name_}};
    }
    if (c == "delete_preset") {
        const std::string name = argStr(r, "name");
        std::string err;
        if (!store_.remove(name, err)) return errResp(err);
        ctx.presetsChanged = true;
        return okResp();
    }
    return errResp("unknown cmd '" + c + "'");
}

}  // namespace bb
