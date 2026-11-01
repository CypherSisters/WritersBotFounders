"""Shared helpers for the Zone 3 tests (lane L4). No network: names never resolve, nothing leaves."""

from __future__ import annotations

import ssl

from puffpai_core.bus import Bus
from puffpai_core.zone3.service import Zone3


def zone(state, **kw) -> Zone3:
    return Zone3(state, bus=Bus(), **kw)


def manifest(pid="freshtable", hosts=(), install_hosts=(), changes=(), kind="app"):
    return {"schema": "puffpai-package/1", "id": pid, "kind": kind, "name": pid.capitalize(), "version": "1.0.0",
            "description": "A test package.", "needs": {"hosts": list(hosts), "install_hosts": list(install_hosts)},
            "abilities": {"changes": list(changes)}}


def grant(z: Zone3, m: dict, *, subhosts=None, open_bridge=True) -> dict:
    """The owner's install Yes for *m*'s hosts (a tap: a package card is everyday)."""
    card = z.bridge.package_card(m, subhosts=subhosts)
    rec = z.consents.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    if open_bridge:
        z.bridge.open()
    return rec


def client_hello(server_name: str) -> bytes:
    """A real TLS ClientHello with this SNI (made by the ssl module, never sent anywhere)."""
    ctx = ssl.create_default_context()
    incoming, outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
    obj = ctx.wrap_bio(incoming, outgoing, server_hostname=server_name)
    try:
        obj.do_handshake()
    except ssl.SSLWantReadError:
        pass
    return outgoing.read()


class HttpUpstream:
    """A local HTTP server for proxy tests: records every request it gets (one per connection),
    answers ``pong`` with a keep-alive head, and reads until the head and its body are in."""

    def __init__(self):
        import socket
        import threading
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.got: list[bytes] = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                c, _ = self.sock.accept()
            except OSError:
                return
            c.settimeout(3)
            data = b""
            try:
                while b"\r\n\r\n" not in data:
                    chunk = c.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                head, _, rest = data.partition(b"\r\n\r\n")
                low = head.lower()
                if b"content-length:" in low:
                    n = int(low.split(b"content-length:", 1)[1].split(b"\r\n", 1)[0])
                    while len(rest) < n:
                        rest += c.recv(65536)
                elif b"transfer-encoding: chunked" in low:
                    while not rest.endswith(b"0\r\n\r\n"):
                        chunk = c.recv(65536)
                        if not chunk:
                            break
                        rest += chunk
                try:                           # anything more the proxy sent on this connection
                    c.settimeout(0.3)
                    while True:
                        more = c.recv(65536)
                        if not more:
                            break
                        rest += more
                except OSError:
                    pass
                self.got.append(head + b"\r\n\r\n" + rest)
                c.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: keep-alive\r\n\r\npong")
            except OSError:
                pass
            finally:
                c.close()

    def close(self):
        self.sock.close()
        self.thread.join(5)


def send_all(port: int, data: bytes, timeout: float = 5) -> bytes:
    """Send *data* to the proxy and read until it closes the connection."""
    import socket
    s = socket.create_connection(("127.0.0.1", port), timeout)
    s.settimeout(timeout)
    out = b""
    try:
        s.sendall(data)
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            out += chunk
    except OSError:
        pass
    finally:
        s.close()
    return out
