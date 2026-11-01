#include "presets.h"

#include <dirent.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>

namespace bb {

bool readFile(const std::string& path, std::string& out) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return false;
    std::ostringstream ss;
    ss << f.rdbuf();
    out = ss.str();
    return true;
}

bool makeDirs(const std::string& path) {
    if (path.empty()) return false;
    std::string cur;
    for (size_t i = 0; i <= path.size(); ++i) {
        if (i == path.size() || path[i] == '/') {
            if (!cur.empty() && mkdir(cur.c_str(), 0755) != 0 && errno != EEXIST) return false;
        }
        if (i < path.size()) cur += path[i];
    }
    struct stat st;
    return stat(path.c_str(), &st) == 0 && S_ISDIR(st.st_mode);
}

void fsyncPathAndDir(const std::string& path) {
    int fd = ::open(path.c_str(), O_RDONLY);
    if (fd >= 0) {
        ::fsync(fd);
        ::close(fd);
    }
    const auto slash = path.rfind('/');
    const std::string dir = slash == std::string::npos ? "." : path.substr(0, slash);
    int dfd = ::open(dir.c_str(), O_RDONLY);
    if (dfd >= 0) {
        ::fsync(dfd);
        ::close(dfd);
    }
}

FileWorker::FileWorker() : th_([this] { run(); }) {}

FileWorker::~FileWorker() {
    flush();
    {
        std::lock_guard<std::mutex> l(mu_);
        quit_ = true;
    }
    cv_.notify_all();
    th_.join();
}

void FileWorker::write(const std::string& path, std::string data) {
    {
        std::lock_guard<std::mutex> l(mu_);
        writes_[path] = std::move(data);  // latest wins
    }
    cv_.notify_all();
}

void FileWorker::syncLater(const std::string& path) {
    {
        std::lock_guard<std::mutex> l(mu_);
        syncs_.push_back(path);
    }
    cv_.notify_all();
}

void FileWorker::flush() {
    std::unique_lock<std::mutex> l(mu_);
    idle_.wait(l, [this] { return writes_.empty() && syncs_.empty() && !busy_; });
}

void FileWorker::run() {
    std::unique_lock<std::mutex> l(mu_);
    for (;;) {
        cv_.wait(l, [this] { return quit_ || !writes_.empty() || !syncs_.empty(); });
        if (writes_.empty() && syncs_.empty()) {
            if (quit_) return;
            continue;
        }
        auto writes = std::move(writes_);
        auto syncs = std::move(syncs_);
        writes_.clear();
        syncs_.clear();
        busy_ = true;
        l.unlock();
        for (auto& w : writes) {
            std::string err;
            if (!writeFileAtomic(w.first, w.second, &err, true)) std::fprintf(stderr, "file worker: %s\n", err.c_str());
        }
        for (auto& s : syncs) fsyncPathAndDir(s);
        l.lock();
        busy_ = false;
        if (writes_.empty() && syncs_.empty()) idle_.notify_all();
    }
}

bool writeFileAtomic(const std::string& path, const std::string& data, std::string* err, bool sync) {
    const std::string tmp = path + ".tmp";
    int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        if (err) *err = "open " + tmp + ": " + std::strerror(errno);
        return false;
    }
    size_t off = 0;
    while (off < data.size()) {
        ssize_t w = ::write(fd, data.data() + off, data.size() - off);
        if (w < 0) {
            if (errno == EINTR) continue;
            if (err) *err = "write " + tmp + ": " + std::strerror(errno);
            ::close(fd);
            ::unlink(tmp.c_str());
            return false;
        }
        off += size_t(w);
    }
    if (sync) ::fsync(fd);
    ::close(fd);
    if (::rename(tmp.c_str(), path.c_str()) != 0) {
        if (err) *err = "rename " + path + ": " + std::strerror(errno);
        ::unlink(tmp.c_str());
        return false;
    }
    if (sync) {
        const auto slash = path.rfind('/');
        const std::string dir = slash == std::string::npos ? "." : path.substr(0, slash);
        int dfd = ::open(dir.c_str(), O_RDONLY);
        if (dfd >= 0) {
            ::fsync(dfd);
            ::close(dfd);
        }
    }
    return true;
}

namespace {

std::vector<std::string> listJson(const std::string& dir) {
    std::vector<std::string> v;
    DIR* d = opendir(dir.c_str());
    if (!d) return v;
    while (dirent* e = readdir(d)) {
        std::string n = e->d_name;
        if (n.size() > 5 && n.compare(n.size() - 5, 5, ".json") == 0 && n[0] != '.') v.push_back(n);
    }
    closedir(d);
    std::sort(v.begin(), v.end());
    return v;
}

std::string lower(std::string s) {
    for (auto& c : s) c = char(std::tolower((unsigned char)c));
    return s;
}

// Preset name -> file name: keep it readable, filesystem-safe and case-insensitive-unique.
std::string slug(const std::string& name) {
    std::string s;
    for (unsigned char c : name) {
        if (std::isalnum(c)) s += char(std::tolower(c));
        else if (c == ' ' || c == '-' || c == '_') {
            if (!s.empty() && s.back() != '-') s += '-';
        }
    }
    while (!s.empty() && s.back() == '-') s.pop_back();
    if (s.empty()) s = "preset";
    // distinguish names that slug identically ("A+B" vs "AB")
    unsigned h = 2166136261u;
    for (unsigned char c : name) h = (h ^ c) * 16777619u;
    char b[16];
    std::snprintf(b, sizeof b, "-%04x", h & 0xffff);
    return s + b;
}

}  // namespace

PresetStore::PresetStore(std::string factorySrcDir, std::string presetsDir)
    : factorySrc_(std::move(factorySrcDir)), dir_(std::move(presetsDir)) {
    if (!dir_.empty()) factoryDir_ = dir_ + "/factory";
}

std::string PresetStore::syncFactory() {
    if (dir_.empty()) return "presets disabled";
    makeDirs(factoryDir_);
    int copied = 0, removed = 0, kept = 0;
    const auto src = listJson(factorySrc_);
    for (auto& f : src) {
        std::string a, b;
        if (!readFile(factorySrc_ + "/" + f, a)) continue;
        if (readFile(factoryDir_ + "/" + f, b) && a == b) {
            ++kept;
            continue;
        }
        if (writeFileAtomic(factoryDir_ + "/" + f, a)) ++copied;
    }
    if (!factorySrc_.empty() && access(factorySrc_.c_str(), R_OK) == 0) {
        for (auto& f : listJson(factoryDir_)) {
            if (std::find(src.begin(), src.end(), f) == src.end()) {
                ::unlink((factoryDir_ + "/" + f).c_str());
                ++removed;
            }
        }
    }
    char b[160];
    std::snprintf(b, sizeof b, "factory presets: %d copied, %d unchanged, %d removed (from %s)", copied, kept,
                  removed, factorySrc_.c_str());
    return b;
}

std::vector<PresetStore::FileEntry> PresetStore::scan(const std::string& dir) const {
    std::vector<FileEntry> v;
    for (auto& f : listJson(dir)) {
        const std::string path = dir + "/" + f;
        std::string txt;
        if (!readFile(path, txt)) continue;
        json j = json::parse(txt, nullptr, false);
        if (j.is_discarded() || !j.is_object()) continue;
        std::string name = j.value("name", std::string());
        if (name.empty()) name = f.substr(0, f.size() - 5);
        v.push_back({name, path});
    }
    return v;
}

std::vector<PresetStore::Entry> PresetStore::list() const {
    std::vector<Entry> out;
    if (dir_.empty()) return out;
    const auto fac = scan(factoryDir_);
    auto user = scan(dir_);
    for (auto& f : fac) {
        Entry e{f.name, true, false, f.path};
        for (auto& u : user)
            if (lower(u.name) == lower(f.name)) {
                e.factory = false;
                e.overridesFactory = true;
                e.path = u.path;
            }
        out.push_back(e);
    }
    std::sort(user.begin(), user.end(), [](const FileEntry& a, const FileEntry& b) { return lower(a.name) < lower(b.name); });
    for (auto& u : user) {
        bool shadow = false;
        for (auto& f : fac) shadow = shadow || lower(u.name) == lower(f.name);
        if (!shadow) out.push_back({u.name, false, false, u.path});
    }
    return out;
}

bool PresetStore::exists(const std::string& name) const {
    for (auto& e : list())
        if (lower(e.name) == lower(name)) return true;
    return false;
}

bool PresetStore::load(const std::string& name, json& out, std::string& err) const {
    for (auto& e : list()) {
        if (lower(e.name) != lower(name)) continue;
        std::string txt;
        if (!readFile(e.path, txt)) {
            err = "cannot read " + e.path;
            return false;
        }
        out = json::parse(txt, nullptr, false);
        if (out.is_discarded() || !out.is_object()) {
            err = "bad JSON in " + e.path;
            return false;
        }
        out["name"] = e.name;
        return true;
    }
    err = "no preset named '" + name + "'";
    return false;
}

std::string PresetStore::userPathFor(const std::string& name) const {
    for (auto& u : scan(dir_))
        if (lower(u.name) == lower(name)) return u.path;
    return dir_ + "/" + slug(name) + ".json";
}

bool PresetStore::save(const std::string& name, const json& preset, std::string& err) {
    if (dir_.empty()) {
        err = "presets disabled";
        return false;
    }
    if (name.empty() || name.size() > 64) {
        err = "preset name must be 1..64 characters";
        return false;
    }
    makeDirs(dir_);
    json p = preset;
    p["name"] = name;
    // Written + renamed now (so list()/load() see it immediately); the fsync goes to the worker.
    const std::string path = userPathFor(name);
    if (!writeFileAtomic(path, p.dump(2) + "\n", &err, worker_ == nullptr)) return false;
    if (worker_) worker_->syncLater(path);
    return true;
}

bool PresetStore::remove(const std::string& name, std::string& err) {
    for (auto& u : scan(dir_)) {
        if (lower(u.name) != lower(name)) continue;
        if (::unlink(u.path.c_str()) != 0) {
            err = "cannot delete " + u.path + ": " + std::strerror(errno);
            return false;
        }
        if (worker_) worker_->syncLater(u.path);  // fsyncs the directory (the file is gone)
        return true;
    }
    for (auto& f : scan(factoryDir_))
        if (lower(f.name) == lower(name)) {
            err = "factory presets can't be deleted";
            return false;
        }
    err = "no preset named '" + name + "'";
    return false;
}

}  // namespace bb
