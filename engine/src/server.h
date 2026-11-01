// bassbox engine: unix-socket JSON-lines server (control thread).
// Non-blocking sockets in a poll() loop: a slow or stuck client can never block the control
// thread (its output is buffered up to a limit, meters are skipped while it lags, and it is
// dropped if it stops reading altogether). The audio thread never touches any of this.
#pragma once

#include <poll.h>

#include <memory>
#include <string>
#include <vector>

#include "host.h"

namespace bb {

class Server {
public:
    ~Server();
    bool listen(const std::string& path, std::string& err);
    void addPollFds(std::vector<pollfd>& fds) const;
    // Handle readiness for the fds this server added (looked up by fd).
    void process(const std::vector<pollfd>& fds, Host& host);
    // Periodic: state/presets events (<= 20 Hz) and meters events (30 Hz).
    void tick(int64_t nowNs, Host& host);
    void broadcast(const json& event);  // to every subscribed client
    size_t clients() const { return clients_.size(); }
    void closeAll();

private:
    struct Client {
        int fd = -1;
        ClientFlags flags;
        std::string in, out;
        bool needState = false, needPresets = false, dead = false;
    };
    void accept();
    void readFrom(Client& c, Host& host);
    void flush(Client& c);
    void send(Client& c, const json& j);
    void handleLine(Client& c, const std::string& line, Host& host);

    std::string path_;
    int listenFd_ = -1;
    int nextId_ = 1;
    std::vector<std::unique_ptr<Client>> clients_;
    int64_t lastStateNs_ = 0, lastMetersNs_ = 0;
};

}  // namespace bb
