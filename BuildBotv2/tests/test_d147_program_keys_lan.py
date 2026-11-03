"""D147: a program on another computer uses the bot as a model. A ``puffpai apikey`` key works on the home-network listener,
on ``/openai/v1`` and ``/ollama/api`` and nowhere else.

A real listener, on real sockets, bound to 127.0.0.2, never every interface and never this computer's real address. The peer
check and the address lookup are stood in for on the ``Network`` object, except in the cases that turn the real peer check
back on. A stub brain answers: no real model. The source addresses 127.0.0.x are real separate peers to the listener, so the
limit is counted as it would be on a real network.
"""

from __future__ import annotations

import http.client
import json
import re
import shutil
import socket
import subprocess
import threading
import time
import types
from pathlib import Path

import pytest

from puffpai_core import cli, corelock, layout, pairing, you
from puffpai_core.api import auth, network
from puffpai_core.api.app import TERMINAL_LABEL, make_servers, stop_servers, wire_core
from puffpai_core.api.groups import chat as chat_group
from puffpai_core.api.network import NETWORK_LISTENER
from puffpai_core.zone1 import archivist as archivist_mod
from puffpai_core.zone1 import gate as zone1_gate
from puffpai_core.zone1.folders import Library
from puffpai_core.zone2 import conversations, persona
from puffpai_core.zone2 import settings as model_settings
from puffpai_core.zone2.gate import production
from test_api_openai import ASK, StubBrain
from test_lan_pairing import _add, _free_port, _refused, _routes, _url
from z1kit import canary_key, make as make_vault
from zone2_doubles import FakeRuntime, make_brain

REPO = Path(__file__).resolve().parents[1]
STAND_IN = "127.0.0.2"                 # a real socket, on no real network
ADDRESS, PHONE, BIRTH, EMAIL = "1428 Elm Street", "415-555-0137", "March 3, 1961", "writer@example.com"
CARD = "4111 1111 1111 1111"
HER_WORDS = (ADDRESS, PHONE, BIRTH, EMAIL, CARD, "prescribed", "therapist")
KEY = canary_key()
NEVER = ("swordfish", KEY, "737", "482913")
STORY = (f"The river ran low that summer. She lived at {ADDRESS} with her dog. Call her on {PHONE} before nine. "
         f"She was born on {BIRTH}. Write to {EMAIL} about it. Her card number is {CARD} and it expired long ago. "
         f"The card CVV: 737. The one-time code is 482913. The login password is swordfish, she said. "
         f"Her doctor prescribed lithium and her therapist agreed. api_key: {KEY}. The end came quietly.")
PROGRAM_ROUTES = [("GET", "/openai/v1/models", None), ("POST", "/openai/v1/chat/completions", ASK),
                  ("GET", "/ollama/api/tags", None), ("POST", "/ollama/api/chat", ASK)]
#: One of every kind of route a key must never reach on this listener.
OTHER_ROUTES = [
    ("GET", "/v1/vault/status", None), ("POST", "/v1/vault/unlock", {"method": "pin", "secret": "123456"}),
    ("PUT", "/v1/vault/secrets/x", {"value": "y"}), ("GET", "/v1/toolsmith/keys", None), ("PUT", "/v1/toolsmith/keys/x", {"value": "y"}),
    ("GET", "/v1/settings", None), ("POST", "/v1/shutdown", {}), ("POST", "/v1/dev/shell", {"shell": "cmd"}), ("GET", "/v1/dev/shell", None),
    ("POST", "/v1/pair", {"code": "x"}), ("GET", "/v1/network", None), ("PUT", "/v1/network", {"on": False}),
    ("POST", "/v1/network/code", None), ("GET", "/v1/network/pages", None), ("GET", "/v1/devices", None), ("GET", "/v1/registry", None),
    ("POST", "/v1/chat", {"text": "hello"}), ("POST", "/v1/chat/stream", {"text": "hello"}), ("GET", "/v1/chat/history", None),
    ("POST", "/v1/consent/any/answer", {"answer": "yes"}), ("GET", "/v1/audit", None), ("GET", "/v1/programs", None),
    ("POST", "/v1/wizard/no-such-step", {}), ("GET", "/v1/toolsmith/agent/build", None), ("GET", "/v1/model", None),
    ("GET", "/v1/health", None), ("GET", "/v1/signin", None), ("GET", "/", None), ("GET", "/pair", None), ("GET", "/window/", None),
    ("GET", "/ui/logic.js", None),
]


class Got:
    def __init__(self, status, headers, data):
        self.status, self.headers, self.data = status, headers, data

    @property
    def json(self):
        return json.loads(self.data)

    def same_answer(self, other) -> bool:
        """The whole answer but its Date: nothing here can tell one apart from the other."""
        mine = sorted((k, v) for k, v in self.headers.items() if k != "Date")
        theirs = sorted((k, v) for k, v in other.headers.items() if k != "Date")
        return (self.status, self.data, mine) == (other.status, other.data, theirs)


def read(c, method, path, headers, body=None):
    payload = None if body is None else json.dumps(body)
    h = dict(headers)
    if payload is not None:
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=payload, headers=h)
    r = c.getresponse()
    return Got(r.status, r.headers, r.read())


def ask(net, method, path, *, key=None, headers=None, body=None, source=None, host=None, port=None):
    """One real request over a real socket. *source*: which 127.0.0.x it comes from, a separate peer to the listener."""
    port = port or net.port
    c = http.client.HTTPConnection(STAND_IN, port, timeout=30, source_address=(source, 0) if source else None)
    try:
        h = {"Host": host or f"{STAND_IN}:{port}", **(headers or {})}
        if key is not None:
            h["Authorization"] = f"Bearer {key}"
        return read(c, method, path, h, body)
    finally:
        c.close()


@pytest.fixture(autouse=True)
def _no_given_addresses(monkeypatch):
    monkeypatch.delenv(network.ADDRESSES_ENV, raising=False)


@pytest.fixture
def brain(core):
    b = StubBrain()
    core.app.extensions["puffpai_brain"] = b
    return b


@pytest.fixture
def lan(core, brain):
    net = core.app.extensions[network.EXTENSION]
    network.save_on(core.state, True)
    net.accepting, net.bind_host, net.port = True, STAND_IN, _free_port(STAND_IN)
    net.addresses, net.names, net.peer_ok = (lambda: {STAND_IN}), (lambda: set()), (lambda a: True)
    assert net.open() is True
    yield net
    net.close()


@pytest.fixture
def key(core):
    return core.tokens.issue_program_key("home assistant")


def wrong_count(net) -> int:
    return sum(len(v) for v in net.limit._wrong.values())


def pair_browser(core, net, source=None):
    """A real pairing over the real socket. Returns the cookie line and the headers a paired browser sends."""
    r = ask(net, "POST", "/v1/pair", body={"code": pairing.new_code(core.state)}, source=source,
            headers={"Origin": f"http://{STAND_IN}:{net.port}", "X-PuffPAI": "1"})
    assert r.status == 200
    cookie = r.headers.get("Set-Cookie").split(";", 1)[0]
    return cookie, {"Cookie": cookie, auth.DEVICE_HEADER: r.json["device_key"]}


def no_cross_origin_headers(r) -> bool:
    return not [k for k in r.headers.keys() if k.lower().startswith("access-control-")]


# -- the key works, on these routes ------------------------------------------------------------------------------------
def test_there_are_exactly_four_program_routes(core):
    """A new route with access ``program`` would open to the home network with a key. It has to be added here on purpose."""
    found = {rule.rule for rule in core.app.url_map.iter_rules()
             if getattr(core.app.view_functions[rule.endpoint], "puffpai_access", None) == auth.PROGRAM}
    assert found == {path for _, path, _ in PROGRAM_ROUTES}


@pytest.mark.parametrize("method,path,body", PROGRAM_ROUTES)
def test_a_key_works_on_both_families_with_no_cookie_and_no_browser_headers(lan, key, brain, method, path, body):
    r = ask(lan, method, path, key=key, body=body)
    assert r.status == 200, r.data
    assert "Set-Cookie" not in r.headers
    assert no_cross_origin_headers(r)
    if body is not None:
        assert brain.calls[-1]["personal"] is False and brain.calls[-1]["text"] == "say hello"
    assert wrong_count(lan) == 0


def test_the_answers_are_the_same_shape_as_on_this_computer(lan, key):
    r = ask(lan, "POST", "/openai/v1/chat/completions", key=key, body=ASK)
    assert r.json["choices"][0]["message"]["content"] == "Hello there." and r.json["model"] == "puffpai"
    assert [m["id"] for m in ask(lan, "GET", "/openai/v1/models", key=key).json["data"]] == ["puffpai"]
    r = ask(lan, "POST", "/ollama/api/chat", key=key, body=ASK)
    assert r.json["message"]["content"] == "Hello there." and r.json["done"] is True
    assert [m["name"] for m in ask(lan, "GET", "/ollama/api/tags", key=key).json["models"]] == ["puffpai"]


def test_both_stream_over_the_real_socket(lan, key):
    r = ask(lan, "POST", "/openai/v1/chat/completions", key=key, body={**ASK, "stream": True})
    assert r.status == 200 and r.headers.get("Content-Type").startswith("text/event-stream")
    lines = [line[len("data: "):] for line in r.data.decode("utf-8").split("\n\n") if line.strip()]
    assert lines[-1] == "[DONE]"
    assert "".join(json.loads(d)["choices"][0]["delta"].get("content", "") for d in lines[:-1]) == "Hello there."
    r = ask(lan, "POST", "/ollama/api/chat", key=key, body={**ASK, "stream": True})
    assert r.status == 200 and r.headers.get("Content-Type").startswith("application/x-ndjson")
    rows = [json.loads(line) for line in r.data.decode("utf-8").splitlines() if line.strip()]
    assert "".join(row["message"]["content"] for row in rows) == "Hello there." and rows[-1]["done"] is True


def test_the_other_key_header_counts_as_on_this_computer(lan, key):
    """``auth.credential`` reads it too: the same secret in another header, no more reach."""
    assert ask(lan, "GET", "/openai/v1/models", headers={"X-PuffPAI-Key": key}).status == 200
    assert ask(lan, "GET", "/v1/settings", headers={"X-PuffPAI-Key": key}).json["error"] == network.KEYS_REFUSED


# -- a program key reaches nothing else --------------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,body", OTHER_ROUTES)
def test_a_key_is_refused_on_every_other_kind_of_route(core, lan, key, brain, method, path, body):
    r = ask(lan, method, path, key=key, body=body)
    assert r.status == 403 and r.json == {"error": network.KEYS_REFUSED}, r.data
    assert wrong_count(lan) == 0 and brain.calls == []
    assert network.saved_on(core.state) is True and lan.is_open() is True


def test_on_every_route_there_is_a_key_gets_one_answer_and_no_route_looks_it_up(core, lan, key, monkeypatch):
    """Every route but the four, right key or wrong: so no route tells a guesser a key is real, and no route counts it."""
    d = core.app.test_client(use_cookies=False)
    looked = []
    real = core.tokens.lookup
    monkeypatch.setattr(core.tokens, "lookup", lambda token: (looked.append(token), real(token))[1])
    env = {"puffpai.listener": NETWORK_LISTENER, "REMOTE_ADDR": "192.168.1.50"}
    seen = 0
    for rule, method in _routes(core):
        access = getattr(core.app.view_functions[rule.endpoint], "puffpai_access")
        if access == auth.PROGRAM:
            continue
        for token in (key, "ppk_not_a_real_key"):
            r = d.open(_url(rule), method=method, base_url=f"http://{STAND_IN}:{lan.port}", environ_base=env,
                       headers={"Authorization": f"Bearer {token}"}, json={} if method != "GET" else None)
            if rule.endpoint.startswith("pages."):
                assert r.status_code == 404, rule.rule
            else:
                assert r.status_code == 403 and r.json == {"error": network.KEYS_REFUSED}, (method, rule.rule)
        seen += 1
    assert seen > 100
    assert looked == [] and wrong_count(lan) == 0


def test_a_key_on_the_pair_route_uses_no_code_and_pairs_nothing(core, lan, key):
    code = pairing.new_code(core.state)
    r = ask(lan, "POST", "/v1/pair", key=key, body={"code": code},
            headers={"Origin": f"http://{STAND_IN}:{lan.port}", "X-PuffPAI": "1"})
    assert r.status == 403 and "Set-Cookie" not in r.headers
    assert core.tokens.browsers() == []
    r = ask(lan, "POST", "/v1/pair", body={"code": code}, headers={"Origin": f"http://{STAND_IN}:{lan.port}", "X-PuffPAI": "1"})
    assert r.status == 200       # the code was not used up


def test_a_key_never_gets_a_shell_or_a_shutdown(core, lan, key):
    from puffpai_core.api.groups.shutdown import STOP
    assert ask(lan, "POST", "/v1/dev/shell", key=key, body={"shell": "cmd"}).status == 403
    assert ask(lan, "GET", "/v1/dev/shell", key=key).status == 403
    stop = core.app.extensions.get(STOP)
    assert ask(lan, "POST", "/v1/shutdown", key=key, body={}).status == 403
    assert stop is None or not stop.is_set()


@pytest.fixture
def page_port(core, lan, tmp_path):
    """A real page listener for one package, on the stand-in."""
    base = _free_port(STAND_IN)
    lan.page_range = range(base, base + 5)
    _add(core, tmp_path, "made-app", "made")
    return lan.open_pages()["made-app"]


def test_the_page_ports_still_refuse_every_key(core, lan, key, page_port, brain):
    owner = core.tokens.issue_owner_key()
    for token in (key, owner, "ppk_not_a_real_key"):
        for path in ("/", "/openai/v1/models", "/ollama/api/tags"):
            r = ask(lan, "GET", path, key=token, port=page_port)
            assert r.status == 403 and r.json == {"error": network.KEYS_REFUSED}, (path, r.data)
    r = ask(lan, "POST", "/openai/v1/chat/completions", key=key, port=page_port, body=ASK)
    assert r.status == 403
    assert brain.calls == [] and wrong_count(lan) == 0


# -- the checks before any route still hold ----------------------------------------------------------------------------
def test_a_peer_off_the_home_network_is_refused_with_a_right_key(lan, key, brain):
    lan.peer_ok = network.peer_allowed             # the real check: this socket's peer is loopback, so not a home peer
    for method, path, body in PROGRAM_ROUTES:
        r = ask(lan, method, path, key=key, body=body)
        assert r.status == 403 and r.data == network.PEER_WORDS, path
    assert brain.calls == [] and wrong_count(lan) == 0


def test_no_forwarded_header_makes_a_peer_a_home_one(lan, key):
    lan.peer_ok = network.peer_allowed
    for name in ("X-Forwarded-For", "X-Real-IP", "Forwarded", "X-Forwarded-Host"):
        r = ask(lan, "GET", "/openai/v1/models", key=key, headers={name: "192.168.1.50"})
        assert r.status == 403 and r.data == network.PEER_WORDS, name


@pytest.mark.parametrize("host", ["evil.example", "evil.example:{port}", "127.0.0.1:{port}", "localhost:{port}",
                                  "puffpai.localhost:{port}", f"{STAND_IN}:1", "mybox.evil.example:{port}"])
def test_a_wrong_host_is_refused_with_a_right_key(lan, key, brain, host):
    r = ask(lan, "GET", "/openai/v1/models", key=key, host=host.format(port=lan.port))
    assert r.status == 421 and r.data == network.HOST_WORDS
    assert wrong_count(lan) == 0 and brain.calls == []


def test_a_forwarded_host_does_not_replace_a_wrong_host(lan, key):
    r = ask(lan, "GET", "/openai/v1/models", key=key, host="evil.example", headers={"X-Forwarded-Host": f"{STAND_IN}:{lan.port}"})
    assert r.status == 421


# -- a key that was taken back -----------------------------------------------------------------------------------------
def test_a_revoked_key_stops_at_once_even_on_a_connection_left_open(core, lan, key):
    c = http.client.HTTPConnection(STAND_IN, lan.port, timeout=30)
    try:
        h = {"Host": f"{STAND_IN}:{lan.port}", "Authorization": f"Bearer {key}"}
        assert read(c, "GET", "/openai/v1/models", h).status == 200
        assert core.tokens.revoke_program_key("home assistant") == 1
        r = read(c, "GET", "/openai/v1/models", h)
        assert r.status == 403 and r.json == {"error": network.KEYS_REFUSED}
    finally:
        c.close()
    assert wrong_count(lan) == 1


def test_a_turn_already_running_ends_by_itself_when_its_key_is_revoked(core, lan, key):
    """What this does not do, said plainly: a stream already open runs to its end. Nothing new starts."""
    release, started = threading.Event(), threading.Event()

    class Slow(StubBrain):
        def turn(self, text, **kw):
            started.set()
            assert release.wait(30)
            return super().turn(text, **kw)
    core.app.extensions["puffpai_brain"] = Slow()
    got = {}

    def go():
        got["r"] = ask(lan, "POST", "/ollama/api/chat", key=key, body={**ASK, "stream": True})
    t = threading.Thread(target=go, daemon=True)
    t.start()
    assert started.wait(30)
    core.tokens.revoke_program_key("home assistant")
    assert ask(lan, "POST", "/ollama/api/chat", key=key, body=ASK).status == 403
    release.set()
    t.join(30)
    assert got["r"].status == 200 and b"Hello" in got["r"].data


# -- every other kind of key: one answer, as a wrong one ---------------------------------------------------------------
@pytest.fixture
def other_keys(core, lan):
    core.terminal_label = f"{TERMINAL_LABEL} (run t)"
    cookie, _ = pair_browser(core, lan)
    return {"unknown": "ppk_not_a_real_key", "owner": core.tokens.issue_owner_key(), "package": core.tokens.issue_package_token("some-app"),
            "toolsmith": core.tokens.issue_toolsmith("b1"), "terminal": core.tokens.issue_device(core.terminal_label),
            "plain device": core.tokens.issue_device("a program's device key"), "browser": cookie.split("=", 1)[1]}


@pytest.mark.parametrize("method,path,body", PROGRAM_ROUTES)
def test_every_other_key_gets_exactly_a_wrong_keys_answer(core, lan, other_keys, brain, method, path, body):
    wrong = ask(lan, method, path, key="ppk_wrong", body=body, source="127.0.0.9")
    assert wrong.status == 403 and wrong.json == {"error": network.KEYS_REFUSED}
    for i, (who, token) in enumerate(other_keys.items()):
        r = ask(lan, method, path, key=token, body=body, source=f"127.0.0.{20 + i}")
        assert r.same_answer(wrong), (who, r.status, r.data)
    assert brain.calls == []
    assert wrong_count(lan) == 1 + len(other_keys)     # each one counted, so the count can't tell them apart either


def test_a_revoked_key_gets_a_wrong_keys_answer_too(core, lan, key):
    wrong = ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", source="127.0.0.9")
    core.tokens.revoke_program_key("home assistant")
    assert ask(lan, "GET", "/openai/v1/models", key=key, source="127.0.0.10").same_answer(wrong)


def test_a_browsers_own_token_with_its_second_half_is_no_key(core, lan, brain):
    cookie, paired = pair_browser(core, lan)
    second = paired[auth.DEVICE_HEADER]
    r = ask(lan, "POST", "/openai/v1/chat/completions", key=cookie.split("=", 1)[1], headers={auth.DEVICE_HEADER: second}, body=ASK)
    assert r.status == 403 and r.json == {"error": network.KEYS_REFUSED} and brain.calls == []


@pytest.mark.parametrize("value", ["Bearer ", "Basic dXNlcjpwYXNz", "ppk_no_bearer_word", "Bearer ppk_a, Bearer ppk_b"])
def test_any_other_authorization_line_is_a_wrong_key(lan, value):
    r = ask(lan, "GET", "/openai/v1/models", headers={"Authorization": value})
    assert r.status == 403 and r.json == {"error": network.KEYS_REFUSED}, value
    assert wrong_count(lan) == 1


# -- wrong keys count, and a right key never does ------------------------------------------------------------------------
def test_the_limit_is_the_one_pairing_uses_with_its_numbers_unchanged(lan):
    assert isinstance(lan.limit, network.PairLimit)
    assert (network.PairLimit.PER_PEER, network.PairLimit.IN_ALL, network.PairLimit.WINDOW_S) == (5, 30, 300.0)


def test_wrong_keys_count_then_wait_and_a_right_key_never_does(core, lan, key, brain):
    a = "127.0.0.31"
    for _ in range(network.PairLimit.PER_PEER):
        assert ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", source=a).status == 403
    r = ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", source=a)
    assert r.status == 429 and r.json == {"error": network.KEYS_WAIT, "code": "key_wait"}
    assert "key" in r.json["error"]
    counted = wrong_count(lan)
    assert counted == network.PairLimit.PER_PEER
    # the right key, from the very address that is waiting: it works and is not counted
    for method, path, body in PROGRAM_ROUTES:
        assert ask(lan, method, path, key=key, body=body, source=a).status == 200, path
    assert wrong_count(lan) == counted
    # someone else's wrong key still has its own tries
    assert ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", source="127.0.0.32").status == 403
    # and a key on any other route is the same refusal as ever, limited or not, not counted
    assert ask(lan, "GET", "/v1/settings", key="ppk_wrong", source=a).status == 403
    assert wrong_count(lan) == counted + 1


def test_a_right_key_is_never_taken_from_the_limit(lan, key, monkeypatch):
    monkeypatch.setattr(lan.limit, "take", lambda *a, **k: pytest.fail("a right key was counted"))
    monkeypatch.setattr(lan.limit, "forgive", lambda *a, **k: pytest.fail("a right key needed forgiving"))
    for method, path, body in PROGRAM_ROUTES:
        assert ask(lan, method, path, key=key, body=body).status == 200


def test_no_forwarded_header_dodges_the_count(lan):
    a = "127.0.0.33"
    for i in range(network.PairLimit.PER_PEER):
        assert ask(lan, "GET", "/ollama/api/tags", key="ppk_wrong", source=a, headers={"X-Forwarded-For": f"192.168.1.{i + 1}"}).status == 403
    r = ask(lan, "GET", "/ollama/api/tags", key="ppk_wrong", source=a, headers={"X-Forwarded-For": "192.168.1.200"})
    assert r.status == 429
    assert set(lan.limit._wrong) == {a}


def test_wrong_keys_make_pairing_wait_and_wrong_codes_make_a_wrong_key_wait(core, lan, key):
    """The side effect the owner should know of: one limit, so a program set up wrong can make pairing wait."""
    a = "127.0.0.34"
    browser = {"Origin": f"http://{STAND_IN}:{lan.port}", "X-PuffPAI": "1"}
    for _ in range(network.PairLimit.PER_PEER):
        ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", source=a)
    r = ask(lan, "POST", "/v1/pair", body={"code": pairing.new_code(core.state)}, headers=browser, source=a)
    assert r.status == 429 and r.json["code"] == "pair_wait"          # even a right code waits, on this address
    b = "127.0.0.35"
    for _ in range(network.PairLimit.PER_PEER):
        assert ask(lan, "POST", "/v1/pair", body={"code": "wrong"}, headers=browser, source=b).status == 401
    assert ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", source=b).status == 429
    assert ask(lan, "GET", "/openai/v1/models", key=key, source=b).status == 200


def test_the_total_stops_every_address_for_pairing_too_but_never_a_right_key(core, lan, key):
    browser = {"Origin": f"http://{STAND_IN}:{lan.port}", "X-PuffPAI": "1"}
    assert network.PairLimit.IN_ALL % network.PairLimit.PER_PEER == 0
    peers = network.PairLimit.IN_ALL // network.PairLimit.PER_PEER
    for p in range(peers):
        for _ in range(network.PairLimit.PER_PEER):
            assert ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", source=f"127.0.0.{40 + p}").status == 403
    fresh = "127.0.0.90"                                              # an address with no wrong try of its own
    assert ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", source=fresh).status == 429
    r = ask(lan, "POST", "/v1/pair", body={"code": pairing.new_code(core.state)}, headers=browser, source=fresh)
    assert r.status == 429 and r.json["code"] == "pair_wait"
    assert ask(lan, "GET", "/openai/v1/models", key=key, source=fresh).status == 200
    assert ask(lan, "POST", "/ollama/api/chat", key=key, body=ASK, source=fresh).status == 200
    # the window passes: pairing and wrong keys go on
    assert lan.limit.take(fresh, now=time.monotonic() + network.PairLimit.WINDOW_S + 1) is True


def test_a_paired_browser_is_not_held_back_by_wrong_keys(core, lan):
    _, paired = pair_browser(core, lan, source="127.0.0.36")
    for p in range(network.PairLimit.IN_ALL // network.PairLimit.PER_PEER):
        for _ in range(network.PairLimit.PER_PEER):
            ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", source=f"127.0.0.{40 + p}")
    assert ask(lan, "GET", "/openai/v1/models", headers=paired, source="127.0.0.36").status == 200
    assert ask(lan, "GET", "/v1/settings", headers=paired, source="127.0.0.36").status == 200


# -- a key with a cookie beside it is only a key -------------------------------------------------------------------------
@pytest.fixture
def paired_with_pages(core, lan, tmp_path):
    """A paired browser, and a saved page port, so a paired caller's CSP names it and any other's does not."""
    base = _free_port(STAND_IN)
    lan.page_range = range(base, base + 5)
    _add(core, tmp_path, "made-app", "made")
    port = lan.page_ports()["made-app"]
    _, paired = pair_browser(core, lan)
    return paired, port


def test_a_cookie_beside_a_key_widens_nothing(lan, key, paired_with_pages, brain):
    paired, port = paired_with_pages
    alone = ask(lan, "GET", "/openai/v1/models", headers=paired)                  # the browser, as today
    assert alone.status == 200 and str(port) in alone.headers.get("Content-Security-Policy")
    r = ask(lan, "GET", "/openai/v1/models", key=key, headers=paired)             # the same browser's cookie with a key
    assert r.status == 200 and str(port) not in r.headers.get("Content-Security-Policy")
    assert "Set-Cookie" not in r.headers
    r = ask(lan, "POST", "/openai/v1/chat/completions", key=key, headers=paired, body=ASK)
    assert r.status == 200 and brain.calls[-1]["personal"] is False
    for method, path, body in OTHER_ROUTES:
        r = ask(lan, method, path, key=key, headers=paired, body=body)
        assert r.status == 403 and r.json == {"error": network.KEYS_REFUSED}, path


def test_a_wrong_key_with_a_good_cookie_is_still_a_wrong_key(lan, paired_with_pages, brain):
    paired, _ = paired_with_pages
    before = wrong_count(lan)
    r = ask(lan, "GET", "/openai/v1/models", key="ppk_wrong", headers=paired)
    assert r.status == 403 and r.json == {"error": network.KEYS_REFUSED}
    assert wrong_count(lan) == before + 1


def test_a_program_key_is_no_cookie(core, lan, key):
    name = network.cookie_name(lan.port)
    r = ask(lan, "GET", "/openai/v1/models", headers={"Cookie": f"{name}={key}"})
    assert r.status == 401 and r.json["code"] == auth.SIGN_IN_CODE
    assert ask(lan, "GET", "/v1/settings", headers={"Cookie": f"{name}={key}"}).status == 401
    r = ask(lan, "GET", "/", headers={"Cookie": f"{name}={key}"})
    assert r.status == 302 and r.headers.get("Location").endswith("/pair")


def test_a_key_needs_no_origin_and_gets_no_cross_origin_headers_whatever_it_sends(lan, key, paired_with_pages):
    paired, _ = paired_with_pages
    foreign = {"Origin": "http://evil.example"}
    r = ask(lan, "POST", "/openai/v1/chat/completions", key=key, headers=foreign, body=ASK)
    assert r.status == 200 and no_cross_origin_headers(r)
    assert r.headers.get("Cross-Origin-Resource-Policy") == "same-origin" and r.headers.get("X-Frame-Options") == "DENY"
    r = ask(lan, "POST", "/openai/v1/chat/completions", key=key, headers={**paired, **foreign}, body=ASK)
    assert r.status == 200 and no_cross_origin_headers(r)
    # no key: the cookie rule stands for a paired browser
    r = ask(lan, "POST", "/openai/v1/chat/completions", headers={**paired, **foreign, "X-PuffPAI": "1"}, body=ASK)
    assert r.status == 403 and r.json == {"error": auth.CROSS_SITE}


# -- OPTIONS, HEAD, a method a route does not have --------------------------------------------------------------------
def test_a_browser_page_gets_no_way_to_read_an_answer(lan, key):
    """A page on the home network would need a preflight to send a key. Nothing answers one with permission."""
    preflight = {"Origin": "http://192.168.1.77:3000", "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization,content-type"}
    for path in ("/openai/v1/chat/completions", "/openai/v1/models", "/ollama/api/chat", "/ollama/api/tags"):
        r = ask(lan, "OPTIONS", path, headers=preflight)
        assert r.status == 401 and no_cross_origin_headers(r), path
        r = ask(lan, "OPTIONS", path, key=key, headers=preflight)
        assert r.status == 200 and no_cross_origin_headers(r), path
    r = ask(lan, "GET", "/openai/v1/models", key=key, headers={"Origin": "http://192.168.1.77:3000"})
    assert r.status == 200 and no_cross_origin_headers(r)


def test_head_follows_the_same_rule(lan, key):
    assert ask(lan, "HEAD", "/openai/v1/models", key=key).status == 200
    assert ask(lan, "HEAD", "/ollama/api/tags", key=key).status == 200
    assert ask(lan, "HEAD", "/openai/v1/models").status == 401
    assert wrong_count(lan) == 0
    assert ask(lan, "HEAD", "/openai/v1/models", key="ppk_wrong").status == 403
    assert wrong_count(lan) == 1
    assert ask(lan, "HEAD", "/v1/settings", key=key).status == 403
    assert ask(lan, "OPTIONS", "/v1/settings", key=key).status == 403
    assert wrong_count(lan) == 1


def test_a_method_a_route_does_not_have_says_nothing_about_a_key(lan, key, monkeypatch, core):
    monkeypatch.setattr(core.tokens, "lookup", lambda token: pytest.fail("looked up a key on no route"))
    for token in (key, "ppk_wrong"):
        assert ask(lan, "DELETE", "/openai/v1/models", key=token).status == 405
        assert ask(lan, "GET", "/openai/v1/no-such-route", key=token).status == 404
    assert wrong_count(lan) == 0


# -- the listener off, safe mode ----------------------------------------------------------------------------------------
def test_with_the_listener_off_a_key_gets_nothing(core, lan, key, brain):
    network.save_on(core.state, False)
    r = ask(lan, "GET", "/openai/v1/models", key=key)         # a socket still open for a moment: the check before any route
    assert r.status == 403 and r.data == network.CLOSED_WORDS
    lan.apply()
    assert lan.is_open() is False and _refused(STAND_IN, lan.port)
    assert brain.calls == []


def test_safe_mode_changes_nothing_at_this_gate(core, lan, key, monkeypatch):
    """The listener stays open in safe mode (D130). The brain keeps its own safe-mode rules for tools."""
    monkeypatch.setattr(type(core.caretaker), "in_safe_mode", property(lambda self: True))
    assert ask(lan, "POST", "/openai/v1/chat/completions", key=key, body=ASK).status == 200
    assert ask(lan, "GET", "/v1/settings", key=key).status == 403


# -- everything else on the listener stays --------------------------------------------------------------------------------
def test_an_unpaired_browser_with_no_key_still_gets_the_pair_screen(lan):
    r = ask(lan, "GET", "/")
    assert r.status == 302 and r.headers.get("Location").endswith("/pair")
    assert b"pair" in ask(lan, "GET", "/pair").data.lower()
    for method, path, body in PROGRAM_ROUTES:
        r = ask(lan, method, path, body=body)
        assert r.status == 401 and r.json["code"] == auth.SIGN_IN_CODE, path
    assert ask(lan, "GET", "/v1/settings").status == 401
    assert wrong_count(lan) == 0                               # no key: no wrong try


def test_a_paired_browser_keeps_what_it_has(core, lan, brain):
    _, paired = pair_browser(core, lan)
    for path in ("/openai/v1/models", "/ollama/api/tags", "/v1/settings", "/v1/devices"):
        assert ask(lan, "GET", path, headers=paired).status == 200, path
    h = {**paired, "Origin": f"http://{STAND_IN}:{lan.port}", "X-PuffPAI": "1"}
    assert ask(lan, "POST", "/ollama/api/chat", headers=h, body=ASK).status == 200


# -- which conversation ---------------------------------------------------------------------------------------------------
def test_a_key_on_the_network_can_never_name_a_chat_of_hers(core, lan, key, brain):
    for name in ("main", "api", "kitchen"):
        assert ask(lan, "POST", "/openai/v1/chat/completions", key=key, body={**ASK, "conversation_id": name}).status == 200
        got = brain.calls[-1]["conversation"]
        assert got != name and got.startswith("lan") and conversations.valid_id(got), name
    assert ask(lan, "POST", "/ollama/api/chat", key=key, body={**ASK, "user": "main"}).status == 200
    assert brain.calls[-1]["conversation"] == brain.calls[0]["conversation"]          # one id, one conversation, both shapes
    assert len({c["conversation"] for c in brain.calls}) == 3
    assert ask(lan, "POST", "/ollama/api/chat", key=key, body=ASK).status == 200      # no id at all: its own too
    assert brain.calls[-1]["conversation"] != "api"


def test_a_paired_browser_on_the_network_still_names_its_own(core, lan, brain):
    _, paired = pair_browser(core, lan)
    h = {**paired, "Origin": f"http://{STAND_IN}:{lan.port}", "X-PuffPAI": "1"}
    assert ask(lan, "POST", "/ollama/api/chat", headers=h, body={**ASK, "conversation_id": "kitchen"}).status == 200
    assert brain.calls[-1]["conversation"] == "kitchen"


def test_her_words_do_not_reach_the_model_for_a_program_on_the_network(core, lan, key, freshtable):
    """Through a real Brain and a fake model server. On this computer the same id still reaches her chat, as D137 left it:
    that is the last two lines here, and it is the owner's to decide."""
    runtime = FakeRuntime({"text": "ok"}, {"text": "ok"})
    real, _items, _reg = make_brain(core.state, freshtable, runtime)
    core.app.extensions["puffpai_brain"] = real
    real.store.append("main", "user", "words from her own chat")
    assert ask(lan, "POST", "/openai/v1/chat/completions", key=key, body={**ASK, "conversation_id": "main"}).status == 200
    assert "words from her own chat" not in json.dumps(runtime.calls[-1]["messages"])
    assert core.app.test_client().post("/openai/v1/chat/completions", headers={"Authorization": f"Bearer {key}"},
                                       json={**ASK, "conversation_id": "main"}).status_code == 200
    assert "words from her own chat" in json.dumps(runtime.calls[-1]["messages"])


# -- the listeners on this computer are as they were ---------------------------------------------------------------------
def test_the_local_listener_is_unchanged(core, lan, key, brain):
    a, b = _free_port(), _free_port()
    servers = make_servers(core.app, a, b)
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers]
    for t in threads:
        t.start()

    def here(method, path, token=None, body=None):
        c = http.client.HTTPConnection("127.0.0.1", a, timeout=30)
        try:
            h = {"Host": f"127.0.0.1:{a}"}
            if token:
                h["Authorization"] = f"Bearer {token}"
            return read(c, method, path, h, body)
        finally:
            c.close()
    try:
        owner = core.tokens.issue_owner_key()
        for method, path, body in PROGRAM_ROUTES:
            assert here(method, path, key, body).status == 200, path
            assert here(method, path, owner, body).status == 200, path
            assert here(method, path, None, body).status == 401, path
            assert here(method, path, "ppk_wrong", body).status == 401, path
        r = here("GET", "/v1/registry", key)
        assert r.status == 403 and r.json == {"error": auth.PROGRAM_LIMIT}
        assert here("GET", "/v1/registry", owner).status == 200
        assert here("GET", "/v1/health", key).status == 200
        assert here("POST", "/ollama/api/chat", key, {**ASK, "conversation_id": "kitchen"}).status == 200
        assert brain.calls[-1]["conversation"] == "kitchen"
        assert wrong_count(lan) == 0                           # wrong keys here never reach the network's limit
    finally:
        stop_servers(servers)


# -- the words --------------------------------------------------------------------------------------------------------------
def test_the_refusal_is_true_and_plain():
    for words in (network.KEYS_REFUSED, network.KEYS_WAIT):
        assert "\u2014" not in words and "\t" not in words
    assert "/openai/v1 and /ollama/api" in network.KEYS_REFUSED and "paired browser" in network.KEYS_REFUSED
    assert "key" in network.KEYS_WAIT


def _new_key(capsys):
    assert cli.main(["apikey", "new", "--label", "home assistant"]) == 0
    return capsys.readouterr().out


@pytest.fixture
def addresses(monkeypatch):
    monkeypatch.setenv(network.ADDRESSES_ENV, "192.168.1.5,mybox.local")


def test_apikey_new_says_both_addresses_with_the_network_on(state, capsys, addresses):
    out = _new_key(capsys)
    assert out.count("ppk_") == 1
    assert cli.APIKEY_KEEP in out and cli.APIKEY_HERE in out and cli.APIKEY_THERE in out and cli.APIKEY_HOW in out
    for base in ("http://127.0.0.1:8420", "http://192.168.1.5:8423", "http://mybox.local:8423"):
        assert f"{base}/openai/v1" in out and f"{base}/ollama" in out, base
    assert "/ollama/api" in out                                # the routes, named in the plain line
    assert "uses one of these" not in out
    assert out.index("ppk_") < out.index(cli.APIKEY_THERE)      # the key first: nothing after it can hide it
    assert "\u2014" not in out and "\t" not in out


def test_apikey_new_says_how_to_turn_it_on_with_the_network_off(state, capsys, addresses):
    network.save_on(state, False)
    out = _new_key(capsys)
    assert out.count("ppk_") == 1
    assert "http://127.0.0.1:8420/openai/v1" in out            # this computer still works
    assert "closed to your home network" in out and "network on" in out and "Settings" in out
    assert cli.APIKEY_THERE not in out and ":8423" not in out and "192.168.1.5" not in out


def test_apikey_new_follows_the_running_core_and_the_bots_own_port(state, capsys, addresses, monkeypatch):
    monkeypatch.setattr(corelock, "holder", lambda s: {"kind": "core", "port": 8430})
    out = _new_key(capsys)
    assert "http://127.0.0.1:8430/openai/v1" in out and "http://192.168.1.5:8433/ollama" in out
    monkeypatch.setattr(corelock, "holder", lambda s: {"kind": "core", "port": "not a number"})
    assert "http://192.168.1.5:8423/openai/v1" in _new_key(capsys)
    monkeypatch.setattr(corelock, "holder", lambda s: None)
    from puffpai_core import botports
    monkeypatch.setattr(botports, "read", lambda s: {"port": 8440})
    assert "http://192.168.1.5:8443/openai/v1" in _new_key(capsys)


def test_apikey_new_with_no_home_network_address_says_so(state, capsys, monkeypatch):
    monkeypatch.setattr(network, "own_addresses", lambda: set())
    monkeypatch.setattr(network, "own_names", lambda: set())
    out = _new_key(capsys)
    assert cli.APIKEY_NO_ADDRESS.strip() in out and "http://127.0.0.1:8420/openai/v1" in out and out.count("ppk_") == 1


def test_the_help_says_it_too(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    assert "on this computer or another on your home network" in " ".join(capsys.readouterr().out.split())


def test_list_and_revoke_say_no_address(state, capsys, addresses):
    _new_key(capsys)
    assert cli.main(["apikey", "list"]) == 0
    assert "http://" not in capsys.readouterr().out
    assert cli.main(["apikey", "revoke", "home assistant"]) == 0
    assert "http://" not in capsys.readouterr().out


def test_the_network_status_says_a_program_key_also_works(state, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 8420, "token": "x"})
    answer = {"on": True, "open": True, "port": 8423, "line": "", "urls": ["http://192.168.1.5:8423/"], "worth_knowing": network.WORTH_KNOWING}
    monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None: (200, answer))
    assert cli.main(["network", "status"]) == 0
    out = capsys.readouterr().out
    assert "only a device you pair" in out and "program with a key" in out and network.WORTH_KNOWING in out


# -- the Developer page -----------------------------------------------------------------------------------------------------
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_developer_page_logic():
    proc = subprocess.run([NODE, str(REPO / "tests" / "js" / "d147.test.js")], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_changed_desktop_files_parse():
    web = REPO / "packages" / "desktop" / "web"
    for name in ("logic.js", "pages/developer.js"):
        proc = subprocess.run([NODE, "--check", str(web / name)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, (name, proc.stderr)


def test_the_developer_page_names_the_command_the_route_and_no_key():
    src = (REPO / "packages" / "desktop" / "web" / "pages" / "developer.js").read_text(encoding="utf-8")
    tile = src[src.index("function programKeyTile("):src.index("async function partsTile(")]
    assert 'P.api("GET", "/v1/network")' in tile and "L.programWhere" in tile and "L.PROGRAM_KEY_COMMAND" in tile
    assert "cliWhere()" in tile                                # "puffpai" is on no PATH (D92(e)): said from the Core
    assert "ppk_" not in tile and "P.api(\"POST\"" not in tile   # no key is made or shown on this page
    assert "programKeyTile()" in src[src.index("async function overview("):src.index("/* ---- Zone 1: Vault")]


# -- a key's turn on the network does what it does on this computer, through the real Brain and a scripted model ----------
@pytest.fixture
def world(core, lan, tmp_path, monkeypatch):
    """A real vault, a pinned document that holds her details, a persona and her notes, all wired as a running Core has them."""
    a, *_ = make_vault(core.state)
    zone1_gate.bind(a)
    monkeypatch.setitem(archivist_mod._BY_STATE, str(Path(core.state).resolve()), a)
    model_settings.save(core.state, address="http://127.0.0.1:9", model="fake:1b", tool_budget=12)
    core.app.extensions.pop("puffpai_brain", None)
    wire_core(core.app)
    with core.app.app_context():
        b = chat_group.brain()
    b._gate = production
    doc = tmp_path / "Novel.txt"
    doc.write_text(STORY, encoding="utf-8")
    lib = Library(a, core.state)
    lib.scan_file(lib.pin(doc))
    persona.save(core.state, {"text": f"Role: the helper, {PERSONA_WORD}.\nFirst questions it asks:\n1. {FIRST_QUESTION}", "source": "owner"})
    you.save(core.state, {"name": "Sister", "call_me": CALL_ME, "about": ABOUT_HER}, archivist=a)
    yield types.SimpleNamespace(a=a, brain=b, doc=doc, lib=lib)
    zone1_gate.bind(None)


PERSONA_WORD = "secret-persona-word"
FIRST_QUESTION = "What should I call you?"
CALL_ME = "Sister-Word"
ABOUT_HER = "about-her-word"

READS = {"tool_calls": [{"id": "1", "name": "read_pinned", "arguments": {"name": "Novel"}}]}


def echoes(messages, tools):
    return {"text": "ECHO " + json.dumps([m["content"] for m in messages], ensure_ascii=False)}


def tool_names(specs):
    return [s["function"]["name"] if "function" in s else s.get("name") for s in specs or []]


TURNS = [("/openai/v1/chat/completions", False), ("/openai/v1/chat/completions", True), ("/ollama/api/chat", False), ("/ollama/api/chat", True)]
THE_BOTS_OWN = {"read_pinned", "search_knowledge", "arrange_home", "checklist_add", "run_command", "list_installed", "ask_toolsmith"}


@pytest.fixture
def this_computer(core):
    """The real listener on 127.0.0.1, on a free port. Returns how to send it one turn."""
    a, b = _free_port(), _free_port()
    servers = make_servers(core.app, a, b)
    for s in servers:
        threading.Thread(target=s.run, daemon=True).start()

    def send(key, path, body):
        c = http.client.HTTPConnection("127.0.0.1", a, timeout=30)
        try:
            return read(c, "POST", path, {"Host": f"127.0.0.1:{a}", "Authorization": f"Bearer {key}"}, body)
        finally:
            c.close()
    yield send
    stop_servers(servers)


def both(world, lan, this_computer, key, path, body, *script):
    """The same turn on each listener, each with its own new scripted model: ``[(model, answer), (model, answer)]``."""
    out = []
    for send in (lambda: ask(lan, "POST", path, key=key, body=body), lambda: this_computer(key, path, body)):
        world.brain._runtime = rt = FakeRuntime(*script)
        out.append((rt, send()))
    return out


@pytest.mark.parametrize("vault_open", [True, False], ids=["vault-open", "vault-locked"])
@pytest.mark.parametrize("path,stream", TURNS)
def test_a_keys_turn_is_the_same_full_turn_on_both_listeners(core, lan, key, world, this_computer, path, stream, vault_open):
    if not vault_open:
        world.a.lock()
    persona.mark_opened(core.state)                # the first question has its own test: here the two prompts can be compared
    (net_model, net), (here_model, here) = both(world, lan, this_computer, key, path, {**ASK, "stream": stream}, READS, echoes)
    assert net.status == here.status == 200, (net.data, here.data)
    for model in (net_model, here_model):
        assert THE_BOTS_OWN <= set(tool_names(model.calls[0]["tools"]))
    assert tool_names(net_model.calls[0]["tools"]) == tool_names(here_model.calls[0]["tools"])
    # the same prompt: her persona and pinned document in, her "You" notes out, on both
    system = net_model.calls[0]["messages"][0]["content"]
    assert system == here_model.calls[0]["messages"][0]["content"]
    assert PERSONA_WORD in system and "Novel" in system and ("Installed on this bot" in system or "Nothing is installed" in system)
    assert CALL_ME not in system and ABOUT_HER not in system
    # the tool really ran, with the same result, and the answer holds the same words
    assert len(net_model.calls) == len(here_model.calls) == 2
    results = [[re.sub(r"\{\{vault:[^}]+\}\}", "{{vault}}", m["content"]) for m in model.calls[1]["messages"] if m.get("role") == "tool"]
               for model in (net_model, here_model)]      # a handle is new each time; its place in the words is not
    assert results[0] == results[1] and results[0]
    said = [[w for w in HER_WORDS if w in r.data.decode("utf-8")] for r in (net, here)]
    assert said[0] == said[1]
    assert bool(said[0]) is vault_open             # with the vault open her details come back, from either computer
    for word in NEVER:                             # what the floor holds back, it holds back on both
        assert word not in results[0][0], word


@pytest.mark.parametrize("listener", ["network", "this computer"])
def test_the_first_question_goes_to_a_key_on_either_listener(core, lan, key, world, this_computer, listener):
    assert persona.first_question_due(core.state) == FIRST_QUESTION
    world.brain._runtime = FakeRuntime({"text": "hello"})
    r = ask(lan, "POST", "/openai/v1/chat/completions", key=key, body=ASK) if listener == "network" \
        else this_computer(key, "/openai/v1/chat/completions", ASK)
    assert r.status == 200 and FIRST_QUESTION in r.data.decode("utf-8")
    assert persona.first_question_due(core.state) == ""


@pytest.mark.parametrize("listener", ["network", "this computer"])
def test_a_keys_tools_change_the_same_things_on_either_listener(core, lan, key, world, this_computer, listener):
    def turn():
        return ask(lan, "POST", "/openai/v1/chat/completions", key=key, body=ASK) if listener == "network" \
            else this_computer(key, "/openai/v1/chat/completions", ASK)
    assert layout.get_layout(core.state)["apps"]["view"] != "pages"
    world.brain._runtime = FakeRuntime({"tool_calls": [{"id": "1", "name": "arrange_home", "arguments": {"action": "apps_as_pages"}}]}, {"text": "done"})
    assert turn().status == 200
    assert layout.get_layout(core.state)["apps"]["view"] == "pages"
    world.brain._runtime = FakeRuntime({"tool_calls": [{"id": "1", "name": "checklist_add", "arguments": {"title": "Water the plants", "why": "She asked"}}]}, {"text": "done"})
    assert turn().status == 200
    assert [s.get("title") for s in core.app.extensions["puffpai.checklist"].list()] == ["Water the plants"]


@pytest.mark.parametrize("listener", ["network", "this computer"])
def test_a_command_still_needs_her_yes_from_either_listener(core, lan, key, world, this_computer, listener):
    """Always allow skips her Yes only for her own paired device. On the network the waiting turn holds a slot."""
    world.brain._runtime = FakeRuntime({"tool_calls": [{"id": "1", "name": "run_command", "arguments": {"command": "echo from-a-key"}}]}, echoes)
    box = {}

    def go():
        box["r"] = ask(lan, "POST", "/openai/v1/chat/completions", key=key, body=ASK) if listener == "network" \
            else this_computer(key, "/openai/v1/chat/completions", ASK)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    assert waits(lambda: list(core.consent.list("pending")))
    assert slots(lan) == (network.KEY_TURNS - 1 if listener == "network" else network.KEY_TURNS)
    card = list(core.consent.list("pending"))[0]
    cid = card.get("id") or card.get("consent_id")
    from puffpai_core.consent import card_view
    assert CARD_LINE in card_view(core.consent.get(cid)["card"], False)["lines"][0]    # what every surface draws
    core.consent.answer(cid, "no", by="test", card_sha=core.consent.get(cid)["card_sha"])
    t.join(30)
    assert box["r"].status == 200 and b"from-a-key" not in box["r"].data           # her No: it did not run
    assert waits(lambda: slots(lan) == network.KEY_TURNS)


CARD_LINE = 'A program asked for this, using your key "home assistant". It did not come from your own chat.'


@pytest.mark.parametrize("question", [
    {"kind": "command", "command": "echo hi", "folder": "here"}, {"kind": "open_program", "program": "p", "open_file": "f"},
    {"kind": "core_change", "tool": "arrange_home"}, {"kind": "start", "package": "an-app"}],
    ids=["command", "open", "change", "start"])
def test_every_core_card_a_keys_turn_raises_says_which_key(core, question):
    from puffpai_core import wiring
    question = {**question, "kind": wiring.CORE_CHANGE} if question["kind"] == "core_change" else question
    made = []

    class Store:
        def request(self, scope, card, floor):
            made.append(card)
            raise RuntimeError("no more of this card")
    ask = wiring.core_question(types.SimpleNamespace(consent=Store(), registry=core.registry), wait_s=0.1)
    assert ask({**question, "asked_by": "home assistant"}) is None and CARD_LINE in made[0]["what"]
    assert ask(question) is None and "A program asked" not in str(made[1].get("what"))


def test_a_package_tools_card_says_which_key(core):
    from puffpai_core import wiring
    made = []

    class Store:
        def request(self, scope, card, floor):
            made.append(card)
            raise RuntimeError("no more of this card")

    class Bridge:
        def review_action(self, *a, **k):
            return {"verdict": "ask", "changing": True, "reason_code": "x", "review_id": "r"}
    review = wiring.tool_review(types.SimpleNamespace(consent=Store(), registry=core.registry), Bridge(), wait_s=0.1)
    review({"package": "an-app", "name": "do", "args": {}, "asked_by": "home assistant"})
    review({"package": "an-app", "name": "do", "args": {}})
    assert CARD_LINE in made[0]["what"] and "A program asked" not in made[1]["what"]


def test_only_a_program_key_is_named_and_a_turn_passes_its_label_on_both_listeners(core, lan, key, this_computer):
    seen = []

    class Told(StubBrain):
        def turn(self, text, **kw):
            seen.append(kw.get("asked_by"))
            return super().turn(text, **kw)
    core.app.extensions["puffpai_brain"] = Told()
    for body in (ASK, {**ASK, "stream": True}):
        assert ask(lan, "POST", "/openai/v1/chat/completions", key=key, body=body).status == 200
        assert ask(lan, "POST", "/ollama/api/chat", key=key, body={**body, "stream": bool(body.get("stream"))}).status == 200
    assert this_computer(key, "/openai/v1/chat/completions", ASK).status == 200
    assert seen == ["home assistant"] * 5
    seen.clear()
    assert this_computer(core.tokens.issue_owner_key("mine"), "/openai/v1/chat/completions", ASK).status == 200
    assert seen == [""]


def test_a_keys_label_can_hide_nothing_on_a_card():
    from puffpai_core import wiring
    said = wiring._said_by({"what": "Run it."}, "a\u202eb")["what"]
    assert "\u202e" not in said and "[U+202E]" in said and said.startswith("Run it. ")
    assert wiring._said_by({"what": "Run it."}, "") == {"what": "Run it."}



@pytest.mark.parametrize("vault_open", [True, False], ids=["vault-open", "vault-locked"])
def test_held_values_a_key_sends_follow_one_rule_on_both_listeners(core, lan, key, world, this_computer, vault_open):
    if not vault_open:
        world.a.lock()
    asked = {"messages": [{"role": "user", "content": f"my card is {CARD}"}]}
    for model, r in both(world, lan, this_computer, key, "/openai/v1/chat/completions", asked, echoes):
        assert r.status == 200
        assert (CARD in json.dumps(model.calls[0]["messages"], ensure_ascii=False)) is vault_open


@pytest.mark.parametrize("path,stream", TURNS)
def test_tools_a_client_sends_are_not_read(core, lan, key, world, this_computer, path, stream):
    """The route never read them, on either listener: only messages, stream, conversation_id and user. So nothing a client
    sends is offered to the model or can run. A program that expects tool calls back gets words."""
    theirs = [{"type": "function", "function": {"name": "their_own_tool", "description": "x", "parameters": {"type": "object", "properties": {}}}}]
    body = {**ASK, "stream": stream, "tools": theirs, "tool_choice": "required", "functions": theirs}
    for model, r in both(world, lan, this_computer, key, path, body, {"text": "ok"}):
        assert r.status == 200
        assert "their_own_tool" not in tool_names(model.calls[0]["tools"]) and THE_BOTS_OWN <= set(tool_names(model.calls[0]["tools"]))


def test_on_this_computer_and_for_a_paired_browser_a_turn_is_as_it_was(core, lan, key, world):
    """The same turn, the same words, and everything is still offered."""
    _, paired = pair_browser(core, lan)
    h = {**paired, "Origin": f"http://{STAND_IN}:{lan.port}", "X-PuffPAI": "1"}
    world.brain._runtime = rt = FakeRuntime({"tool_calls": [{"id": "1", "name": "read_pinned", "arguments": {"name": "Novel"}}]}, echoes)
    r = ask(lan, "POST", "/openai/v1/chat/completions", headers=h, body=ASK)
    assert r.status == 200
    assert {"read_pinned", "search_knowledge", "arrange_home", "checklist_add"} <= set(tool_names(rt.calls[0]["tools"]))
    assert PERSONA_WORD in rt.calls[0]["messages"][0]["content"] and "Novel" in rt.calls[0]["messages"][0]["content"]
    assert len(rt.calls) == 2 and [w for w in HER_WORDS if w in r.data.decode("utf-8")]
    world.brain._runtime = rt = FakeRuntime({"tool_calls": [{"id": "1", "name": "read_pinned", "arguments": {"name": "Novel"}}]}, echoes)
    local = core.app.test_client().post("/ollama/api/chat", headers={"Authorization": f"Bearer {key}"}, json=ASK)
    assert local.status_code == 200
    assert {"read_pinned", "search_knowledge", "arrange_home", "checklist_add"} <= set(tool_names(rt.calls[0]["tools"]))
    assert PERSONA_WORD in rt.calls[0]["messages"][0]["content"]
    assert [w for w in HER_WORDS if w in local.get_data(as_text=True)]


# -- each key's conversations are its own ---------------------------------------------------------------------------------
def test_another_key_or_a_later_key_sees_nothing_of_a_keys_words(core, lan, key, freshtable):
    private = "first-keys-private-words"
    real, _items, _reg = make_brain(core.state, freshtable, FakeRuntime())
    core.app.extensions["puffpai_brain"] = real

    def turn(token, words="hello", **extra):
        real._runtime = FakeRuntime(echoes)
        return ask(lan, "POST", "/openai/v1/chat/completions", key=token, body={"messages": [{"role": "user", "content": words}], **extra}).data
    for extra in ({}, {"conversation_id": "kitchen"}, {"user": "kitchen"}):
        turn(key, private, **extra)
    other = core.tokens.issue_program_key("other program")
    for extra in ({}, {"conversation_id": "kitchen"}, {"user": "kitchen"}):
        assert private.encode() in turn(key, **extra), extra                 # the same key and id: the same conversation
        assert private.encode() not in turn(other, **extra), extra           # another key: never
    core.tokens.revoke_program_key("home assistant")
    later = core.tokens.issue_program_key("home assistant")                  # the same label too
    for extra in ({}, {"conversation_id": "kitchen"}):
        assert private.encode() not in turn(later, **extra), extra
    files = [p.name for p in (Path(core.state) / "chat").glob("*.jsonl")]
    assert files and all(conversations.valid_id(n[:-len(".jsonl")]) and n.startswith("lan") for n in files)
    for token in (key, other, later):
        assert not [n for n in files if token in n or token[:12] in n]
        assert not [p for p in (Path(core.state) / "chat").glob("*.jsonl") if token in p.read_text(encoding="utf-8")]


# -- at most KEY_TURNS at once, so the pair screen and paired devices keep room ---------------------------------------------
class HeldRuntime(FakeRuntime):
    """A real slow model: says its first word, waits, then says the rest, a word at a time."""

    def __init__(self, more=()):
        super().__init__()
        self.hold, self.then, self.more = threading.Event(), threading.Event(), list(more)
        self.entered, self.left = [], []

    def chat(self, messages, *, model, tools=None, think=False, options=None, on_token=None):
        self.entered.append(1)
        try:
            if on_token:
                on_token("one ")
            assert self.hold.wait(40)
            for word in self.more:
                time.sleep(0.05)
                if on_token:
                    on_token(word)
            assert self.then.wait(40) if self.more else True
            return {"text": "one " + "".join(self.more), "tool_calls": []}
        finally:
            self.left.append(1)


@pytest.fixture
def held(core, freshtable):
    rt = HeldRuntime()
    core.app.extensions["puffpai_brain"] = make_brain(core.state, freshtable, rt)[0]
    yield rt
    rt.hold.set()
    rt.then.set()


def slots(net) -> int:
    return net.key_turns._value


def waits(fn, timeout_s=15.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(0.02)
    return bool(fn())


def test_the_cap_leaves_room_in_named_numbers():
    assert network.KEY_TURNS == 8 and network.KEY_TURNS < network.NETWORK_WORKERS


def test_at_the_cap_the_pair_screen_and_paired_devices_answer_and_one_more_turn_is_told_busy(core, lan, key, held):
    _, paired = pair_browser(core, lan)
    got = []
    mix = [TURNS[i % 4] for i in range(network.KEY_TURNS)]
    threads = [threading.Thread(target=lambda p=p, s=s: got.append(ask(lan, "POST", p, key=key, body={**ASK, "stream": s})), daemon=True) for p, s in mix]
    for t in threads:
        t.start()
    assert waits(lambda: len(held.entered) == network.KEY_TURNS) and slots(lan) == 0
    # one more turn, either shape, at once: busy, in plain words
    for path, stream in TURNS:
        began = time.monotonic()
        r = ask(lan, "POST", path, key=key, body={**ASK, "stream": stream})
        assert r.status == 429 and r.json == {"error": network.KEY_TURNS_BUSY, "code": "key_busy"}
        assert time.monotonic() - began < 5
    assert len(held.entered) == network.KEY_TURNS and wrong_count(lan) == 0       # no lockout: nothing was counted
    # everything else still answers
    assert ask(lan, "GET", "/pair").status == 200
    assert ask(lan, "GET", "/").status == 302
    assert ask(lan, "GET", "/v1/settings", headers=paired).status == 200
    assert ask(lan, "GET", "/openai/v1/models", key=key).status == 200            # no turn, no slot
    assert ask(lan, "GET", "/openai/v1/models", key="ppk_wrong").status == 403
    assert pair_browser(core, lan)[1][auth.DEVICE_HEADER]                         # pairing, right now
    # the turns finish, and the slots come back
    held.hold.set()
    for t in threads:
        t.join(40)
    assert [r.status for r in got] == [200] * network.KEY_TURNS
    assert waits(lambda: slots(lan) == network.KEY_TURNS)
    assert ask(lan, "POST", "/ollama/api/chat", key=key, body=ASK).status == 200


def test_slots_come_back_on_every_way_a_turn_can_end(core, lan, key):
    from puffpai_core.zone2.errors import BrainError
    many = network.KEY_TURNS + 2
    for path in ("/openai/v1/chat/completions", "/ollama/api/chat"):
        for _ in range(many):                                                     # the route's own refusals
            assert ask(lan, "POST", path, key=key, body={"messages": []}).status == 400
        assert ask(lan, "POST", path, key=key, body={"messages": [{"role": "system", "content": "x"}]}).status == 400
        assert waits(lambda: slots(lan) == network.KEY_TURNS)
        for stream in (False, True):                                              # the brain refuses, before any word
            core.app.extensions["puffpai_brain"] = StubBrain(error=BrainError("Not now.", "busy"))
            for _ in range(many):
                assert ask(lan, "POST", path, key=key, body={**ASK, "stream": stream}).status == 503
            assert waits(lambda: slots(lan) == network.KEY_TURNS)
        core.app.extensions["puffpai_brain"] = StubBrain(mid_error=RuntimeError("broke"))    # breaks partway
        for _ in range(many):
            assert ask(lan, "POST", path, key=key, body={**ASK, "stream": True}).status == 200
        assert waits(lambda: slots(lan) == network.KEY_TURNS)
        core.app.extensions["puffpai_brain"] = StubBrain(error=RuntimeError("broke"))        # breaks with no answer at all
        for _ in range(many):
            assert ask(lan, "POST", path, key=key, body=ASK).status == 500
        assert waits(lambda: slots(lan) == network.KEY_TURNS)


def walk_away(net, key, path, stream, first_word=True):
    """A client that sends a turn, maybe reads its first word, and closes its socket."""
    payload = json.dumps({**ASK, "stream": stream}).encode("utf-8")
    s = socket.create_connection((STAND_IN, net.port), timeout=30)
    s.sendall(f"POST {path} HTTP/1.1\r\nHost: {STAND_IN}:{net.port}\r\nAuthorization: Bearer {key}\r\nContent-Type: application/json\r\n"
              f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload)
    if first_word:
        seen = b""
        while b"one" not in seen:
            seen += s.recv(4096)
    s.close()


@pytest.mark.parametrize("path", ["/openai/v1/chat/completions", "/ollama/api/chat"])
def test_a_stream_a_client_walks_away_from_gives_its_slot_back_once_a_word_cant_be_written(core, lan, key, held, path):
    """What really happens. While the model is silent nothing can notice: waitress does not read a socket with a request
    running, so the slot stays taken. Once the model says more, a write fails, the response closes and the slot comes back,
    even with the model still working. That model call then runs to its own end, without a slot."""
    held.more = [f"w{i} " for i in range(60)]
    walk_away(lan, key, path, True)
    assert waits(lambda: slots(lan) == network.KEY_TURNS - 1)
    time.sleep(0.5)
    assert slots(lan) == network.KEY_TURNS - 1                 # silent model: still taken
    held.hold.set()
    assert waits(lambda: slots(lan) == network.KEY_TURNS), "the write that failed did not give the slot back"
    assert held.left == []                                     # the model is still in its call, and the slot is free
    held.then.set()
    assert waits(lambda: held.left == [1])
    assert slots(lan) == network.KEY_TURNS                     # and it is not given back twice


@pytest.mark.parametrize("path", ["/openai/v1/chat/completions", "/ollama/api/chat"])
def test_a_whole_answer_a_client_walks_away_from_holds_its_slot_until_the_model_answers(core, lan, key, held, path):
    walk_away(lan, key, path, False, first_word=False)
    assert waits(lambda: held.entered == [1] and slots(lan) == network.KEY_TURNS - 1)
    time.sleep(0.5)
    assert slots(lan) == network.KEY_TURNS - 1
    held.hold.set()
    assert waits(lambda: slots(lan) == network.KEY_TURNS)


def test_the_cap_is_only_for_keys_on_this_listener(core, lan, key, held):
    """A paired browser's turn takes no slot, and on this computer there is no cap at all."""
    _, paired = pair_browser(core, lan)
    h = {**paired, "Origin": f"http://{STAND_IN}:{lan.port}", "X-PuffPAI": "1"}
    t = threading.Thread(target=lambda: ask(lan, "POST", "/ollama/api/chat", headers=h, body=ASK), daemon=True)
    t.start()
    assert waits(lambda: held.entered == [1]) and slots(lan) == network.KEY_TURNS
    a, b = _free_port(), _free_port()
    servers = make_servers(core.app, a, b)
    for s in servers:
        threading.Thread(target=s.run, daemon=True).start()
    got = []

    def here():
        c = http.client.HTTPConnection("127.0.0.1", a, timeout=40)
        try:
            got.append(read(c, "POST", "/ollama/api/chat", {"Host": f"127.0.0.1:{a}", "Authorization": f"Bearer {key}"}, ASK).status)
        finally:
            c.close()
    local = [threading.Thread(target=here, daemon=True) for _ in range(network.KEY_TURNS + 1)]
    try:
        for x in local:
            x.start()
        assert waits(lambda: len(held.entered) == 1 + network.KEY_TURNS + 1)      # all running, none told busy
        assert slots(lan) == network.KEY_TURNS
        held.hold.set()
        for x in local:
            x.join(40)
        assert got == [200] * (network.KEY_TURNS + 1)
    finally:
        held.hold.set()
        t.join(40)
        stop_servers(servers)


# -- round 2's words -----------------------------------------------------------------------------------------------------------
def test_the_words_say_the_same_for_both_listeners_and_the_one_difference():
    keep = cli.APIKEY_KEEP
    assert "/openai/v1 and /ollama/api" in keep and "full turn" in keep and "real values" in keep and "can't name your chat" in keep
    assert "one of these instead" not in cli.APIKEY_THERE and "may not work" in cli.APIKEY_THERE
    docs = (REPO / "docs" / "04_DEVELOPERS.md").read_text(encoding="utf-8")
    assert "and nothing else: never your Yes cards" not in docs and "wait a few minutes" not in docs
    assert 'conversation_id: "main"` puts your own chat in front of the model' in docs
    assert "for as long as it keeps retrying the wrong key" in docs
    src = (REPO / "packages" / "desktop" / "web" / "pages" / "developer.js").read_text(encoding="utf-8")
    assert "full turn of your bot" in src and "so it can't name your chat" in src
    for rel in ("core/puffpai_core/cli.py", "core/puffpai_core/api/auth.py", "core/puffpai_core/api/network.py", "core/puffpai_core/api/groups/openai_api.py",
                "core/puffpai_core/zone2/brain.py", "packages/desktop/web/logic.js", "packages/desktop/web/pages/developer.js",
                "docs/01_ARCHITECTURE.md", "docs/04_DEVELOPERS.md", "docs/09_AGENT_SECURITY.md", "contracts/openapi.yaml"):
        text = (REPO / rel).read_text(encoding="utf-8")
        for words in ("only a model", "model and nothing more", "model_only", "the model alone", "MODEL_ONLY"):
            assert words not in text, (rel, words)


def test_every_new_line_keeps_the_writing_rules():
    """No em dash and no tab in what this lane wrote, in a test too."""
    for path in (Path(__file__), REPO / "tests" / "js" / "d147.test.js"):
        text = path.read_text(encoding="utf-8")
        assert "\u2014" not in text and "\t" not in text, path.name
