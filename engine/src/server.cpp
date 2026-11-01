#include "server.h"

#include <fcntl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>

#include "presets.h"

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0  // macOS: SIGPIPE is ignored process-wide instead
#endif

namespace bb {

namespace {
constexpr size_t kMaxLine = 1 << 20;        // 1 MB request line
constexpr size_t kMaxOut = 8 << 20;         // drop a client that stops reading
constexpr size_t kMetersSkip = 256 << 10;   // don't queue meters behind 256 KB of backlog
constexpr int64_t kStateEventNs = 50'000'000;   // state events at most every 50 ms
constexpr int64_t kMetersEventNs = 33'333'333;  // 30 Hz

bool setNonBlocking(int fd) {
    const int fl = fcntl(fd, F_GETFL, 0);
    return fl >= 0 && fcntl(fd, F_SETFL, fl | O_NONBLOCK) == 0 && fcntl(fd, F_SETFD, FD_CLOEXEC) == 0;
}
}  // namespace

Server::~Server() { closeAll(); }

bool Server::listen(const std::string& path, std::string& err) {
    path_ = path;
    const auto slash = path.rfind('/');
    if (slash != std::string::npos && slash > 0) makeDirs(path.substr(0, slash));
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    if (path.size() >= sizeof addr.sun_path) {
        err = "socket path too long";
        return false;
    }
    std::strncpy(addr.sun_path, path.c_str(), sizeof addr.sun_path - 1);
    listenFd_ = ::socket(AF_UNIX, SOCK_STREAM, 0);
    if (listenFd_ < 0) {
        err = std::string("socket: ") + std::strerror(errno);
        return false;
    }
    ::unlink(path.c_str());  // stale socket from a previous run
    if (::bind(listenFd_, reinterpret_cast<sockaddr*>(&addr), sizeof addr) != 0) {
        err = "bind " + path + ": " + std::strerror(errno);
        return false;
    }
    ::chmod(path.c_str(), 0666);  // UI and tools may run as other users
    if (::listen(listenFd_, 16) != 0 || !setNonBlocking(listenFd_)) {
        err = std::string("listen: ") + std::strerror(errno);
        return false;
    }
    return true;
}

void Server::closeAll() {
    for (auto& c : clients_)
        if (c->fd >= 0) ::close(c->fd);
    clients_.clear();
    if (listenFd_ >= 0) {
        ::close(listenFd_);
        listenFd_ = -1;
        ::unlink(path_.c_str());
    }
}

void Server::addPollFds(std::vector<pollfd>& fds) const {
    if (listenFd_ >= 0) fds.push_back({listenFd_, POLLIN, 0});
    for (auto& c : clients_) fds.push_back({c->fd, short(POLLIN | (c->out.empty() ? 0 : POLLOUT)), 0});
}

void Server::accept() {
    for (;;) {
        const int fd = ::accept(listenFd_, nullptr, nullptr);
        if (fd < 0) return;
#if defined(SO_NOSIGPIPE)
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof one);
#endif
        setNonBlocking(fd);
        auto c = std::make_unique<Client>();
        c->fd = fd;
        c->flags.id = nextId_++;
        clients_.push_back(std::move(c));
    }
}

void Server::flush(Client& c) {
    while (!c.out.empty() && !c.dead) {
        const ssize_t w = ::send(c.fd, c.out.data(), c.out.size(), MSG_NOSIGNAL | MSG_DONTWAIT);
        if (w > 0) {
            c.out.erase(0, size_t(w));
        } else if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            break;
        } else if (w < 0 && errno == EINTR) {
            continue;
        } else {
            c.dead = true;
        }
    }
    if (c.out.size() > kMaxOut) c.dead = true;
}

void Server::send(Client& c, const json& j) {
    if (c.dead) return;
    c.out += j.dump(-1, ' ', false, json::error_handler_t::replace);
    c.out += '\n';
    flush(c);
}

void Server::handleLine(Client& c, const std::string& line, Host& host) {
    if (line.find_first_not_of(" \t\r") == std::string::npos) return;
    json req = json::parse(line, nullptr, false);
    if (req.is_discarded()) {
        send(c, json{{"ok", false}, {"error", "bad JSON"}});
        return;
    }
    CommandContext ctx;
    ctx.client = &c.flags;
    json resp = host.handle(req, ctx);
    send(c, resp);
    if (ctx.stateChanged || ctx.presetsChanged) {
        for (auto& o : clients_) {
            if (o.get() == &c) continue;
            if (ctx.stateChanged) o->needState = true;
            if (ctx.presetsChanged) o->needPresets = true;
        }
    }
}

void Server::readFrom(Client& c, Host& host) {
    char buf[16384];
    for (;;) {
        const ssize_t r = ::recv(c.fd, buf, sizeof buf, MSG_DONTWAIT);
        if (r > 0) {
            c.in.append(buf, size_t(r));
            size_t start = 0, nl;
            while ((nl = c.in.find('\n', start)) != std::string::npos) {
                handleLine(c, c.in.substr(start, nl - start), host);
                start = nl + 1;
            }
            c.in.erase(0, start);
            if (c.in.size() > kMaxLine) {
                send(c, json{{"ok", false}, {"error", "request line too long"}});
                c.dead = true;
                return;
            }
        } else if (r == 0) {
            c.dead = true;
            return;
        } else if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return;
        } else if (errno != EINTR) {
            c.dead = true;
            return;
        }
    }
}

void Server::process(const std::vector<pollfd>& fds, Host& host) {
    for (auto& p : fds) {
        if (!p.revents) continue;
        if (p.fd == listenFd_) {
            accept();
            continue;
        }
        for (auto& c : clients_) {
            if (c->fd != p.fd) continue;
            if (p.revents & (POLLERR | POLLNVAL)) c->dead = true;
            if (p.revents & (POLLIN | POLLHUP)) readFrom(*c, host);
            if (p.revents & POLLOUT) flush(*c);
        }
    }
    for (auto it = clients_.begin(); it != clients_.end();) {
        if ((*it)->dead) {
            ::close((*it)->fd);
            it = clients_.erase(it);
        } else {
            ++it;
        }
    }
}

void Server::broadcast(const json& event) {
    for (auto& c : clients_)
        if (c->flags.subscribed) send(*c, event);
}

void Server::tick(int64_t nowNs, Host& host) {
    if (nowNs - lastStateNs_ >= kStateEventNs) {
        lastStateNs_ = nowNs;
        json state, presets;
        for (auto& c : clients_) {
            if (!c->flags.subscribed) {
                c->needState = c->needPresets = false;
                continue;
            }
            if (c->needState) {
                if (state.is_null()) state = json{{"event", "state"}, {"state", host.stateJson()}};
                send(*c, state);
                c->needState = false;
            }
            if (c->needPresets) {
                if (presets.is_null()) {
                    presets = host.presetsJson();
                    presets["event"] = "presets";
                }
                send(*c, presets);
                c->needPresets = false;
            }
        }
    }
    if (nowNs - lastMetersNs_ >= kMetersEventNs) {
        lastMetersNs_ = nowNs;
        json m;
        for (auto& c : clients_) {
            if (!c->flags.subscribed || !c->flags.meters || c->out.size() > kMetersSkip) continue;
            if (m.is_null()) {
                m = host.metersJson();
                m["event"] = "meters";
            }
            send(*c, m);
        }
    }
    for (auto it = clients_.begin(); it != clients_.end();) {
        if ((*it)->dead) {
            ::close((*it)->fd);
            it = clients_.erase(it);
        } else {
            ++it;
        }
    }
}

}  // namespace bb
