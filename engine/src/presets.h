// bassbox engine: preset files (control thread only).
//
// Layout under the state dir (default /var/lib/bassbox):
//   presets/factory/*.json   copies of the repo's presets/factory/*.json, re-synced at every
//                            start (so factory updates arrive with a repo push); read-only
//   presets/*.json           user presets. A user preset with the same name as a factory one
//                            overrides it ("modified factory"); deleting it reveals the
//                            factory version again. Pure factory presets can't be deleted.
//   live.json                autosaved live state (see Host)
// File format = State JSON minus "dirty" (uids are ignored on load).
#pragma once

#include <condition_variable>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "json.h"

namespace bb {

// Write via tmp file + rename: a power cut leaves the old or the new file. sync = also fsync
// the file and its directory (slow on SD: tens to hundreds of ms; keep it off the control
// thread at runtime, see FileWorker).
bool writeFileAtomic(const std::string& path, const std::string& data, std::string* err = nullptr, bool sync = true);
void fsyncPathAndDir(const std::string& path);

// Small worker thread for durable writes, so the control thread (socket replies, jack mute)
// never blocks on fsync. write(): atomic write + fsync, latest-wins per path (autosave).
// syncLater(): fsync a file that was already written/renamed (or removed) plus its directory.
class FileWorker {
public:
    FileWorker();
    ~FileWorker();  // flushes, then joins
    void write(const std::string& path, std::string data);
    void syncLater(const std::string& path);
    void flush();  // block until every queued job is done (shutdown, tests)

private:
    void run();
    std::mutex mu_;
    std::condition_variable cv_, idle_;
    std::map<std::string, std::string> writes_;
    std::vector<std::string> syncs_;
    bool busy_ = false, quit_ = false;
    std::thread th_;
};
bool readFile(const std::string& path, std::string& out);
bool makeDirs(const std::string& path);

class PresetStore {
public:
    struct Entry {
        std::string name;
        bool factory = false;  // true = the factory version is what loads
        bool overridesFactory = false;
        std::string path;
    };

    PresetStore(std::string factorySrcDir, std::string presetsDir);

    // Copy/refresh factory presets into presets/factory and drop ones removed from the repo.
    // Returns a log line.
    std::string syncFactory();
    std::vector<Entry> list() const;  // factory order (file name), then user presets by name
    bool exists(const std::string& name) const;
    bool load(const std::string& name, json& out, std::string& err) const;
    bool save(const std::string& name, const json& preset, std::string& err);
    bool remove(const std::string& name, std::string& err);
    bool enabled() const { return !dir_.empty(); }
    // With a worker, saves/deletes skip the inline fsync and queue it there instead.
    void setWorker(FileWorker* w) { worker_ = w; }

private:
    struct FileEntry {
        std::string name, path;
    };
    std::vector<FileEntry> scan(const std::string& dir) const;
    std::string userPathFor(const std::string& name) const;

    std::string factorySrc_, dir_, factoryDir_;
    FileWorker* worker_ = nullptr;
};

}  // namespace bb
