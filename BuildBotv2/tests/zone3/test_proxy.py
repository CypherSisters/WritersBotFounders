"""The built-in proxy (lane L4): identity, refusals, the SNI rule, plain HTTP, and the ledger.

Real sockets on random 127.0.0.1 ports. Names never reach a resolver: the proxy gets a stub that
answers 127.0.0.1, and the private-answer rule is relaxed only in the tests that need a fake upstream.
"""

import base64
import socket
import threading

import pytest

from puffpai_core.zone3 import proxy as proxy_mod
from puffpai_core.zone3.proxy import Proxy, proxy_env
from z3helpers import client_hello, grant, manifest, zone


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


class Upstream:
    """A one-shot TCP server that records what it got and answers 'pong'."""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.got = b""
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        self.sock.settimeout(5)
        try:
            c, _ = self.sock.accept()
        except OSError:
            return
        c.settimeout(5)
        try:
            self.got = c.recv(65536)
            c.sendall(b"pong")
        except OSError:
            pass
        finally:
            c.close()

    def close(self):
        self.sock.close()
        self.thread.join(5)


@pytest.fixture
def setup(state):
    z = zone(state)
    up = Upstream()
    grant(z, manifest(hosts=[f"api.example.com:{up.port}"]))
    px = Proxy(z._proxy_decide, z.bridge.ledger, z.credentials, port=0, resolve=lambda h, p: ["127.0.0.1"])
    port = px.start()
    yield z, px, port, up
    px.stop()
    up.close()


def _auth(url):
    userinfo = url.split("//", 1)[1].rsplit("@", 1)[0]
    return "Basic " + base64.b64encode(userinfo.encode()).decode()


def _send(port, head: bytes, then: bytes = b"") -> bytes:
    s = socket.create_connection(("127.0.0.1", port), 5)
    s.settimeout(5)
    try:
        s.sendall(head)
        first = s.recv(4096)
        if then and first.startswith(b"HTTP/1.1 200"):
            s.sendall(then)
            try:
                first += s.recv(4096)
            except OSError:
                pass
        return first
    finally:
        s.close()


def test_no_credential_is_407(setup):
    z, px, port, up = setup
    r = _send(port, f"CONNECT api.example.com:{up.port} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    assert r.startswith(b"HTTP/1.1 407")
    wrong = "Basic " + base64.b64encode(b"freshtable:not-the-secret").decode()
    r = _send(port, f"CONNECT api.example.com:{up.port} HTTP/1.1\r\nProxy-Authorization: {wrong}\r\n\r\n".encode())
    assert r.startswith(b"HTTP/1.1 407")
    assert any(line["code"] == "bad_credential" for line in z.bridge.ledger.read())


def test_undeclared_host_is_refused_through_the_proxy(setup):
    z, px, port, up = setup
    auth = _auth(z.credentials.issue("freshtable"))
    r = _send(port, f"CONNECT tracker.example.net:443 HTTP/1.1\r\nProxy-Authorization: {auth}\r\n\r\n".encode())
    assert r.startswith(b"HTTP/1.1 403")
    assert not up.got
    assert ("tracker.example.net", "not_on_list") in [(x["host"], x["code"]) for x in z.bridge.ledger.read()]


def test_one_package_cannot_use_anothers_grants(setup):
    z, px, port, up = setup
    other = _auth(z.credentials.issue("weather"))
    r = _send(port, f"CONNECT api.example.com:{up.port} HTTP/1.1\r\nProxy-Authorization: {other}\r\n\r\n".encode())
    assert r.startswith(b"HTTP/1.1 403")


def test_private_answers_are_refused(setup):
    z, px, port, up = setup
    auth = _auth(z.credentials.issue("freshtable"))
    r = _send(port, f"CONNECT api.example.com:{up.port} HTTP/1.1\r\nProxy-Authorization: {auth}\r\n\r\n".encode())
    assert r.startswith(b"HTTP/1.1 502"), "the name answered 127.0.0.1, which is never dialled"
    assert not up.got
    assert any(x["code"] == "network_filter" for x in z.bridge.ledger.read())


def test_allowed_tunnel_with_matching_sni(setup, monkeypatch):
    z, px, port, up = setup
    monkeypatch.setattr(proxy_mod, "answer_refusal", lambda addrs, lan_ok=False: "")
    auth = _auth(z.credentials.issue("freshtable"))
    hello = client_hello("api.example.com")
    r = _send(port, f"CONNECT api.example.com:{up.port} HTTP/1.1\r\nProxy-Authorization: {auth}\r\n\r\n".encode(),
              hello)
    assert r.startswith(b"HTTP/1.1 200") and r.endswith(b"pong")
    assert up.got == hello


def test_sni_for_another_host_is_refused(setup, monkeypatch):
    z, px, port, up = setup
    monkeypatch.setattr(proxy_mod, "answer_refusal", lambda addrs, lan_ok=False: "")
    auth = _auth(z.credentials.issue("freshtable"))
    r = _send(port, f"CONNECT api.example.com:{up.port} HTTP/1.1\r\nProxy-Authorization: {auth}\r\n\r\n".encode(),
              client_hello("evil.example.net"))
    assert b"pong" not in r and up.got == b""
    r2 = None
    for line in z.bridge.ledger.read():
        if line["host"] == "api.example.com" and line["via"] == "proxy" and line["method"] == "CONNECT":
            r2 = line
            break
    assert r2 and r2["outcome"] == "refused" and r2["code"] == "not_allowed"


def test_plain_http_needs_a_grant_on_that_port(state, monkeypatch):
    from z3helpers import HttpUpstream
    z = zone(state)
    up = HttpUpstream()
    grant(z, manifest(hosts=[f"api.example.com:{up.port}"]))
    px = Proxy(z._proxy_decide, z.bridge.ledger, z.credentials, port=0, resolve=lambda h, p: ["127.0.0.1"])
    port = px.start()
    monkeypatch.setattr(proxy_mod, "answer_refusal", lambda addrs, lan_ok=False: "")
    auth = _auth(z.credentials.issue("freshtable"))
    r = _send(port, f"GET http://api.example.com/ HTTP/1.1\r\nHost: api.example.com\r\n"
                    f"Proxy-Authorization: {auth}\r\n\r\n".encode())
    assert r.startswith(b"HTTP/1.1 403"), "port 80 was never granted"
    from z3helpers import send_all
    r = send_all(port, f"GET http://api.example.com:{up.port}/x?q=1 HTTP/1.1\r\nHost: api.example.com\r\n"
                       f"Proxy-Authorization: {auth}\r\nX-Keep: 1\r\n\r\n".encode())
    px.stop()
    up.close()
    assert r.startswith(b"HTTP/1.1 200 OK") and r.endswith(b"pong") and b"Connection: close" in r
    assert b"keep-alive" not in r
    assert len(up.got) == 1 and up.got[0].startswith(b"GET /x?q=1 HTTP/1.1")
    assert b"Proxy-Authorization" not in up.got[0] and b"X-Keep: 1" in up.got[0]


def test_proxy_listens_on_this_computer_only(state):
    z = zone(state)
    with pytest.raises(ValueError):
        Proxy(z._proxy_decide, z.bridge.ledger, z.credentials, host="0.0.0.0")


def test_package_env_names(state):
    env = proxy_env("http://freshtable:s3cr3t@127.0.0.1:8421")
    assert env["HTTPS_PROXY"] == env["npm_config_https_proxy"] == env["http_proxy"]
    assert env["NODE_USE_ENV_PROXY"] == "1" and "127.0.0.1" in env["NO_PROXY"]


def test_proxy_credential_is_not_the_core_token(state):
    z = zone(state)
    url = z.credentials.issue("freshtable")
    assert "ppt_" not in url and url.startswith("http://freshtable:")
    z.credentials.revoke("freshtable")
    assert z.credentials.check(_auth(url)) is None


def test_live_on_a_free_port_and_the_default_is_8421(state):
    """The Core's own proxy port is 8421 (D23); a test never binds it (another suite may be running), so it
    runs on a port the OS picks, read back from the proxy, and the credential names that port."""
    from puffpai_core.zone3 import proxy as proxy_mod
    assert proxy_mod.PORT == 8421
    z = zone(state)
    port = z.start_proxy(0)
    try:
        assert port not in (0, 8420, 8421, 8422) and z.proxy.port == port
        env = z.package_env("freshtable")
        assert env["HTTPS_PROXY"].endswith(f":{port}")
        r = _send(port, b"CONNECT a.example.com:443 HTTP/1.1\r\nProxy-Authorization: " +
                  _auth(env["HTTPS_PROXY"]).encode() + b"\r\n\r\n")
        assert r.startswith(b"HTTP/1.1 403")           # the Bridge is closed by default
    finally:
        z.stop()
    # Stopped: the port is free again, for a bind the way the Core binds it (on Linux a plain one waits out TIME_WAIT).
    from puffpai_core.corelock import exclusive_socket
    exclusive_socket("127.0.0.1", port).close()
