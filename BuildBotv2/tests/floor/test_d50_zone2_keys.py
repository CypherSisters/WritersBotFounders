"""Floor X6 (Zone 2 side) after the merge chat's re-check, and D50.

1. A package token is a non-blank string or no token at all: junk never
   builds ``Authorization: Bearer <junk>`` or a stdio ``PUFFPAI_TOKEN``.
2. Tool results of every shape (tuples, sets, bytes, dict keys, nesting) and
   error text pass the gate, on /v1/chat/tool, in the brain loop and in
   package_chat.
3. Registry descriptions and MCP tool descriptions and schemas reach the
   model only through the gate and the floor key check.
4. D50: random-looking tokens are held (known prefix, next to a key word, or
   32+ random characters); git commit ids, UUIDs and labelled file hashes stay.
"""

import hashlib
import json
import random
import sys
import types
import uuid

import pytest
from mcp.server.mcpserver import MCPServer

from puffpai_core.api import create_app
from puffpai_core.zone2 import mcp
from puffpai_core.zone2.concierge import Concierge
from puffpai_core.zone2.gate import HELD, as_json, no_keys
from puffpai_core.zone2.mcp import Target
from zone2_doubles import ConsentDouble, FakeRuntime, GateDouble, call, make_brain

KEY = "sk-" + "Zz9Yy8Xx7Ww6Vv5Uu4Tt3Ss2"          # a fake key the gate double holds
PHONE = "555-0134"


URLSAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
HEXDIGITS = "0123456789abcdef"
_RNG = random.Random(50)                   # seeded: the same fake values every run, so no flaky test


def fake(n: int, alphabet: str = URLSAFE) -> str:
    """*n* random-looking characters (a fake key, never a real one), starting with a letter or digit."""
    return _RNG.choice(alphabet[:62] if alphabet is URLSAFE else alphabet) + \
        "".join(_RNG.choice(alphabet) for _ in range(n - 1))


def ppt_token() -> str:
    """A fake package token in the Core's own shape (ppt_ + 32 random characters)."""
    return "ppt_" + fake(32)


# -- 1. tokens ------------------------------------------------------------------

JUNK = [None, "", "   ", "\n", "\t \r\n", 0, 12345, False, True, [], ["ppt_x"], {"a": 1}, b"ppt_bytes",
        "ppt_with space", "ppt_\x00nul"]
HTTP = {"abilities": {"mcp": {"transport": "http", "url": "http://127.0.0.1:{port}/mcp"}}}
STDIO = {"abilities": {"mcp": {"transport": "stdio", "command": [sys.executable, "-c", "pass"]}}}


@pytest.fixture
def spy(monkeypatch):
    """Records every header set and stdio env the MCP client would build, and sends nothing."""
    import mcp.client.stdio as stdio
    import mcp.shared._httpx_utils as hu
    seen = []

    def http(headers=None, **_kw):
        seen.append(("http", headers))
        raise RuntimeError("stopped before sending")

    class Params:
        def __init__(self, **kw):
            seen.append(("stdio", kw.get("env")))
            raise RuntimeError("stopped before starting")
    monkeypatch.setattr(hu, "create_mcp_http_client", http)
    monkeypatch.setattr(stdio, "StdioServerParameters", Params)
    return seen


@pytest.mark.parametrize("manifest", [HTTP, STDIO], ids=["http", "stdio"])
@pytest.mark.parametrize("junk", JUNK, ids=repr)
def test_junk_token_is_no_token(state, spy, manifest, junk):
    c = Concierge(registry=None, state=state, keep=str, token=lambda _pid: junk,
                  folder=lambda _pid: state / "packages" / "x")      # where the Core installed it (lane I2a)
    assert c.token_for("x") is None
    target = c._target({"id": "x", "name": "X", "port": 9}, manifest)
    assert target.token is None
    got = mcp.call_tool(target, "a", {})
    assert got == {"ok": False, "text": "no token for x", "error": "token"}
    assert spy == []                                      # no header, no env was ever built
    raw = Target(package="x", kind=target.kind, url=target.url, command=target.command, token=junk)
    assert mcp.call_tool(raw, "a", {})["error"] == "token" and spy == []   # a Target built by hand too


@pytest.mark.parametrize("manifest,kind", [(HTTP, "http"), (STDIO, "stdio")], ids=["http", "stdio"])
def test_good_token_is_sent_stripped(state, spy, manifest, kind):
    tok = ppt_token()
    c = Concierge(registry=None, state=state, keep=str, token=lambda _pid: f"  {tok}\n",
                  folder=lambda _pid: state / "packages" / "x")      # where the Core installed it (lane I2a)
    mcp.call_tool(c._target({"id": "x", "name": "X", "port": 9}, manifest), "a", {})
    want = {"Authorization": f"Bearer {tok}"} if kind == "http" else {"PUFFPAI_TOKEN": tok}
    assert len(spy) == 1 and spy[0][0] == kind
    got = dict(spy[0][1])
    if kind == "stdio":                                    # lane I2a part D: git stays in the package folder
        assert got.pop("GIT_CEILING_DIRECTORIES", None)
    assert got == want


# -- 2. tool results of every shape -------------------------------------------------

SHAPES = {
    "tuple": ("Phone " + PHONE, KEY),
    "set": {PHONE, KEY},
    "frozenset": frozenset({PHONE}),
    "bytes": (PHONE + " " + KEY).encode(),
    "bad bytes": b"\xff\xfe" + PHONE.encode(),
    "bytearray": bytearray(PHONE.encode()),
    "dict key": {PHONE + " " + KEY: 1},
    "tuple dict key": {(PHONE, 1): "x"},
    "nested": [{"a": ({PHONE},)}, [(KEY.encode(),)]],
    "object": type("Thing", (), {"__str__": lambda self: "call " + PHONE})(),
}


@pytest.fixture
def archivist(monkeypatch):
    return types.SimpleNamespace(search=None)        # the Brain's search (wiring.knowledge_search's place)


def _no_values(text: str) -> None:
    assert PHONE not in text and KEY not in text


@pytest.mark.parametrize("shape", list(SHAPES), ids=str)
def test_chat_tool_result_of_every_shape_is_gated(state, freshtable, archivist, shape):
    archivist.search = lambda q, n: SHAPES[shape]
    brain, _, _ = make_brain(state, freshtable, FakeRuntime(), gate=GateDouble(vault_open=True),
                             search=lambda q, n: archivist.search(q, n))
    got = brain.call_tool("search_knowledge", {"query": "x"})
    assert got["ok"] is True
    _no_values(json.dumps(got))                           # JSON types only: this would raise otherwise
    assert got == as_json(got)


def test_chat_tool_error_text_is_gated(state, freshtable, archivist):
    def boom(_q, _n):
        raise RuntimeError(f"db at {KEY} for {PHONE}")
    archivist.search = boom
    brain, _, _ = make_brain(state, freshtable, FakeRuntime(), gate=GateDouble(vault_open=True),
                             search=lambda q, n: archivist.search(q, n))
    got = brain.call_tool("search_knowledge", {"query": "x"})
    assert got["ok"] is False and "did not work" in got["error"]
    _no_values(json.dumps(got))


def test_chat_tool_over_http_tuple_and_dict_key(state, freshtable, archivist):
    app = create_app(state)
    core = app.extensions["puffpai"]
    brain, _, _ = make_brain(state, freshtable, FakeRuntime(), gate=GateDouble(vault_open=True),
                             search=lambda q, n: archivist.search(q, n))
    brain.registry = core.registry
    app.extensions["puffpai_brain"] = brain
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    client = app.test_client()
    for value in (("Phone " + PHONE,), {PHONE: KEY}):
        archivist.search = lambda q, n, v=value: v
        r = client.post("/v1/chat/tool", headers=owner, json={"tool": "search_knowledge", "args": {"query": "x"}})
        assert r.status_code == 200
        _no_values(r.get_data(as_text=True))


@pytest.mark.parametrize("vault_open", [False, True])
@pytest.mark.parametrize("shape", ["tuple", "set", "bytes", "dict key", "nested"], ids=str)
def test_brain_loop_tool_result_of_every_shape(state, freshtable, archivist, shape, vault_open):
    archivist.search = lambda q, n: SHAPES[shape]
    rt = FakeRuntime(call("search_knowledge", query="x"), {"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=vault_open),
                             search=lambda q, n: archivist.search(q, n))
    brain.turn("search my notes")
    assert KEY not in rt.seen(1)
    assert (PHONE in rt.seen(1)) is vault_open            # D25: real P2 values only while the vault is open
    assert "b'" not in rt.seen(1)                         # bytes became text, not a Python repr


def test_brain_loop_error_text_is_gated(state, freshtable, archivist):
    def boom(_q, _n):
        raise RuntimeError(f"db at {KEY} token {ppt_token()}")
    archivist.search = boom
    rt = FakeRuntime(call("search_knowledge", query="x"), {"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=True),
                             search=lambda q, n: archivist.search(q, n))
    brain.turn("search my notes")
    assert KEY not in rt.seen(1) and "ppt_" not in rt.seen(1) and "did not work" in rt.seen(1)


def test_package_chat_content_of_any_shape(state, freshtable):
    rt = FakeRuntime({"text": "Summary."})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=True))
    tok = ppt_token()
    brain.package_chat([{"role": "user", "content": [{"type": "text", "text": f"phone {PHONE} key {KEY}"}]},
                        {"role": "user", "content": f"my token is {tok}"}])
    seen = rt.seen(0)
    _no_values(seen)
    assert tok not in seen and all(isinstance(m["content"], str) for m in rt.calls[0]["messages"])


# -- 3. descriptions and tool specs ------------------------------------------------

@pytest.mark.parametrize("vault_open", [False, True])
def test_registry_descriptions_reach_the_model_gated(state, freshtable, vault_open):
    tok = ppt_token()
    rows = [{"id": "notes", "kind": "tool", "name": "Notes", "origin": "made", "status": "stopped",
             "description": f"Notes app, api key {KEY}, call {PHONE}, token {tok}"}]
    rt = FakeRuntime({"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=vault_open), extra_rows=rows)
    brain.turn("take a note")
    sysmsg = rt.calls[0]["messages"][0]["content"]
    assert "Notes app" in sysmsg
    for value in (KEY, PHONE, tok):
        assert value not in sysmsg and value not in rt.seen(0)


def test_mcp_descriptions_reach_the_model_gated(state, freshtable):
    tok = ppt_token()
    srv = MCPServer("freshtable")

    @srv.tool(description=f"Add an item. Use key {KEY} or token {tok}.")
    def add_to_list(item: str) -> str:
        return "ok"
    rt = FakeRuntime({"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt)
    brain.targets = lambda pid: Target(package=pid, kind="inproc", server=srv) if pid == "freshtable" else None
    brain.turn("put milk on the shopping list")
    specs = json.dumps(rt.calls[0]["tools"])
    assert "freshtable__add_to_list" in specs and "Add an item" in specs
    assert KEY not in specs and tok not in specs


def test_mcp_schemas_and_tool_names_reach_the_model_gated(state, freshtable, monkeypatch):
    tok = ppt_token()
    schema = {"type": "object", "properties": {
        "item": {"type": "string", "description": f"Item, like {PHONE}, sent with {tok}",
                 "default": KEY, "enum": ["milk", KEY]},
        PHONE: {"type": "string"}}}
    listed = [{"name": "add_to_list", "description": "Put an item on the list.", "input_schema": schema},
              {"name": tok, "description": "A tool named like a key.", "input_schema": {}}]
    monkeypatch.setattr(mcp, "list_tools", lambda _target: listed)
    rt = FakeRuntime({"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt)
    brain.turn("put milk on the shopping list")
    specs = json.dumps(rt.calls[0]["tools"])
    assert "freshtable__add_to_list" in specs
    for value in (KEY, tok, PHONE):
        assert value not in specs


def test_generated_token_in_tool_result_is_held(state, freshtable):
    tok = ppt_token()
    srv = MCPServer("freshtable")

    @srv.tool()
    def add_to_list(item: str) -> str:
        """Put an item on the shopping list."""
        return f"my token is {tok}, and here it is bare: {tok}"
    rt = FakeRuntime(call("freshtable__add_to_list", item="milk"), {"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt, consent=ConsentDouble(True), gate=GateDouble(vault_open=True))
    brain.targets = lambda pid: Target(package=pid, kind="inproc", server=srv) if pid == "freshtable" else None
    brain.turn("put milk on the shopping list")
    assert tok not in rt.seen(1) and HELD in rt.seen(1)


# -- 4. D50 in both directions ------------------------------------------------------

SHA1 = hashlib.sha1(b"puff puff pass").hexdigest()
SHA256 = hashlib.sha256(b"puff puff pass").hexdigest()
UUID = str(uuid.UUID(int=0x1234567890ABCDEF1234567890ABCDEF))


@pytest.mark.parametrize("make_text", [
    lambda t: t,
    lambda t: f"my token is {t}",
    lambda t: json.dumps({"result": t}),
    lambda t: f"see {t}.",
    lambda t: f"/data/{t}",
], ids=["bare", "sentence", "json", "punctuated", "path"])
def test_d50_ppt_token_is_held(make_text):
    tok = ppt_token()
    out = no_keys(make_text(tok))
    assert tok not in out and tok[:12] not in out and HELD in out


@pytest.mark.parametrize("text", [
    "ghp_" + "Ab1" * 12,
    "github_pat_" + "11ABCDEFG0" + "x9Y" * 10,
    "xoxb-" + "1234567890-" + "aB3" * 8,
    "AKIA" + "Z7Q4" * 4,
    "AIza" + "Sy" + "Ab3_Cd4-Ef5" * 3,
    "sk-proj-" + "Ab3" * 10,
    "api key: " + fake(16),          # 16 random characters next to a key word
    "Bearer " + fake(16),
    fake(16) + " is my api key",
    "my api key is " + UUID,                             # a key word wins over a harmless shape
    "token " + SHA1,
    fake(43),                      # 43 random characters, no context
    fake(64, HEXDIGITS),                          # an unlabelled 64-hex value
])
def test_d50_random_looking_values_are_held(text):
    out = no_keys(text)
    assert HELD in out, out


@pytest.mark.parametrize("text", [
    f"commit {SHA1} on main",
    SHA1,
    f"order {UUID} shipped",
    UUID,
    f"sha256: {SHA256}",
    f"SHA256 (puffpai.zip) = {SHA256}",
    f"sha256 of the file puffpai.zip is {SHA256}",
    json.dumps({"sha256": SHA256, "id": UUID, "commit": SHA1}),
    f"image@sha256:{SHA256}",
    "C:/example/core/puffpai_core/zone2/concierge.py",
    "qwen3-coder-30b-a3b-instruct-q4_K_M is installed",
    "The token count is 4096 and the api version is 2024-10-22. Use the key on the hook.",
    "{{vault:" + "a1b2c3d4" * 4 + "}} (an API key)",
    "puff puff pass",
])
def test_d50_harmless_shapes_stay_readable(text):
    assert no_keys(text) == text
