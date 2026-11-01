"""Non-blocking JSON-lines client for the engine socket (docs/architecture.md section 3).

Never blocks the render loop: the app's select() loop calls fileno()/wants_write()/on_readable()/
on_writable()/tick(). Reconnects automatically. Requests get an id; responses fire callbacks.

    c = EngineClient("/run/bassbox/engine.sock", on_event=..., on_connect=..., on_disconnect=...)
    c.request("state", cb=lambda resp: ...)
    c.set_param(uid, "drive", 62.0)        # coalesced: at most one in flight per (uid, param), <= 30 Hz
"""
import errno
import json
import os
import socket
import time


class EngineClient:
    RECONNECT_S = 0.7
    PARAM_MIN_INTERVAL = 1 / 30
    TIMEOUT_S = 3.0

    def __init__(self, path, on_event=None, on_connect=None, on_disconnect=None, log=None):
        self.path = path
        self.on_event = on_event or (lambda ev: None)
        self.on_connect = on_connect or (lambda: None)
        self.on_disconnect = on_disconnect or (lambda: None)
        self.log = log or (lambda *a: None)
        self.sock = None
        self.connected = False
        self.rbuf = b""
        self.wbuf = b""
        self.next_id = 1
        self.pending = {}           # id -> (cb, t_sent, cmd)
        self.next_attempt = 0.0
        self.params = {}            # (uid, pid) -> latest value not yet sent
        self.param_inflight = {}    # (uid, pid) -> t_sent
        self.param_last = {}        # (uid, pid) -> t_sent
        self.rtt_ms = []            # recent round trips (diagnostics)

    # ------------------------------------------------------------- select() integration
    def fileno(self):
        return self.sock.fileno() if self.sock else None

    def wants_write(self):
        return bool(self.sock and self.wbuf)

    def next_deadline(self):
        now = time.monotonic()
        ts = []
        if not self.connected:
            ts.append(self.next_attempt)
        if self.params and self.connected:
            ts.append(min(max(self.param_last.get(k, 0) + self.PARAM_MIN_INTERVAL,
                              self.param_inflight[k] + 0.25 if k in self.param_inflight else 0) for k in self.params))
        if self.pending:
            ts.append(min(t for _, t, _ in self.pending.values()) + self.TIMEOUT_S)
        return max(now, min(ts)) if ts else None

    def tick(self, now=None):
        now = now or time.monotonic()
        if not self.connected and now >= self.next_attempt:
            self._connect()
        if self.connected:
            self._flush_params(now)
            for rid, (cb, t, cmd) in list(self.pending.items()):
                if now - t > self.TIMEOUT_S:
                    del self.pending[rid]
                    self.log("engine timeout", cmd)
                    if cb:
                        cb({"ok": False, "error": "timeout"})

    def _connect(self):
        self.next_attempt = time.monotonic() + self.RECONNECT_S
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.setblocking(False)
            s.connect(self.path)
        except OSError:
            try:
                s.close()
            except Exception:
                pass
            return
        self.sock, self.connected, self.rbuf, self.wbuf = s, True, b"", b""
        self.log("engine connected", self.path)
        self.on_connect()

    def _drop(self, why):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        was = self.connected
        self.sock, self.connected = None, False
        self.next_attempt = time.monotonic() + self.RECONNECT_S
        pend, self.pending = self.pending, {}
        self.param_inflight.clear()
        for cb, _, _ in pend.values():
            if cb:
                cb({"ok": False, "error": "disconnected"})
        if was:
            self.log("engine disconnected:", why)
            self.on_disconnect()

    def on_readable(self):
        try:
            data = self.sock.recv(65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError as e:
            return self._drop(str(e))
        if not data:
            return self._drop("eof")
        self.rbuf += data
        while b"\n" in self.rbuf:
            line, self.rbuf = self.rbuf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.log("engine: bad json", line[:120])
                continue
            self._dispatch(msg)

    def on_writable(self):
        try:
            n = self.sock.send(self.wbuf)
            self.wbuf = self.wbuf[n:]
        except (BlockingIOError, InterruptedError):
            pass
        except OSError as e:
            self._drop(str(e))

    def _dispatch(self, msg):
        if "id" in msg and msg["id"] in self.pending:
            cb, t, cmd = self.pending.pop(msg["id"])
            self.rtt_ms = (self.rtt_ms + [(time.monotonic() - t) * 1000])[-50:]
            if not msg.get("ok", True):
                self.log("engine error:", cmd, msg.get("error"))
            if cb:
                cb(msg)
        elif "event" in msg:
            self.on_event(msg)
        elif "id" in msg:
            pass  # late reply to a timed-out request
        else:
            self.log("engine: unexpected", str(msg)[:120])

    # ------------------------------------------------------------- API
    def request(self, cmd, cb=None, **args):
        if not self.connected:
            if cb:
                cb({"ok": False, "error": "disconnected"})
            return None
        rid = self.next_id
        self.next_id += 1
        msg = {"id": rid, "cmd": cmd}
        msg.update(args)
        self.pending[rid] = (cb, time.monotonic(), cmd)
        self.wbuf += (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        self.on_writable()  # opportunistic, non-blocking
        return rid

    def set_param(self, uid, pid, value):
        self.params[(uid, pid)] = value
        self._flush_params(time.monotonic())

    def _flush_params(self, now):
        for key in list(self.params):
            if key in self.param_inflight and now - self.param_inflight[key] < 0.25:
                continue  # one in flight per key; the latest value goes when it returns
            if now - self.param_last.get(key, 0) < self.PARAM_MIN_INTERVAL:
                continue
            uid, pid = key
            v = self.params.pop(key)
            self.param_inflight[key] = now
            self.param_last[key] = now
            done = lambda r, k=key: self.param_inflight.pop(k, None)  # noqa: E731
            if pid == "__mix":
                self.request("set_mix", cb=done, uid=uid, mix=v)
            else:
                self.request("set_param", cb=done, uid=uid, param=pid, value=v)

    def close(self):
        self._drop("close")
