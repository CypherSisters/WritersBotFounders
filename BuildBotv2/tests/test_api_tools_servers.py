"""Outside MCP tool servers the owner adds by their web address (lane api-tools, D137).

A real Core (``create_app`` + ``wire_core``): the real routes, vault, consent store, Bridge and Drawbridge, and real MCP
servers over streamable HTTP on 127.0.0.1 in this process. The one stand-in is the last step of a dial to a home-network
address: a test can't reach 192.168.1.20, so the MCP client's ``streamable_http_client`` records the address it was
given and goes to a local server instead. Every decision before that point (her Yes, the Bridge's answer) is the real code.
"""

from __future__ import annotations

import json
import secrets
import socket
import threading
import time
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from puffpai_core import tool_servers as ts
from puffpai_core.api import create_app
from puffpai_core.api.app import wire_core
from puffpai_core.zone2 import mcp
from puffpai_core.zone3 import addresses
from zone2_doubles import FakeRuntime, GateDouble, call

PIN = "Tq7#mz2Lp!"                                  # a made-up test PIN (tests/z1kit.py), nobody's real one
LAN = "192.168.1.20:8123"
LAN_URL = f"http://{LAN}/mcp"


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def serve():
    """``serve(password=None) -> (port, seen, items)``: a tool server on 127.0.0.1. ``seen`` is each request's
    Authorization header; with a password, any other is refused (401)."""
    import uvicorn
    started = []

    def make(password: str | None = None):
        srv = MCPServer("outside-test")
        items: list[str] = []
        seen: list[str] = []

        @srv.tool(annotations=ToolAnnotations(readOnlyHint=True))
        def show_list() -> str:
            """Show what is on the list."""
            return ", ".join(items) or "The list is empty."

        @srv.tool()
        def add_item(item: str) -> str:
            """Put an item on the list."""
            items.append(item)
            return f"Added {item}."

        inner = srv.streamable_http_app()

        async def app(scope, receive, send):
            if scope["type"] == "http":
                auth = dict(scope["headers"]).get(b"authorization", b"").decode()
                seen.append(auth)
                if password is not None and auth != f"Bearer {password}":
                    await send({"type": "http.response.start", "status": 401, "headers": []})
                    await send({"type": "http.response.body", "body": b"no"})
                    return
            await inner(scope, receive, send)

        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on"))
        t = threading.Thread(target=server.run, daemon=True)
        t.start()
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.05)
        started.append((server, t))
        return port, seen, items
    yield make
    for server, t in started:
        server.should_exit = True
        t.join(10)


@pytest.fixture
def world(state):
    app = create_app(state, port=18419)
    core = wire_core(app)
    core.app = app
    z = app.extensions["puffpai.zone3"]
    dev = bearer(core.tokens.issue_device("Test laptop"))
    owner = bearer(core.tokens.issue_owner_key())
    client = app.test_client()
    assert client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev).status_code == 201
    yield app, client, dev, owner, core, z
    z.stop()


@pytest.fixture
def dials(monkeypatch):
    """Every address the MCP client dials. A dial to the test's home-network address goes to ``dials.port`` instead."""
    import mcp.client.streamable_http as sh
    real = sh.streamable_http_client
    seen: list[str] = []

    def dial(url, *a, **kw):
        seen.append(url)
        if LAN in url:
            url = url.replace(LAN, f"127.0.0.1:{dial.port}")
        return real(url, *a, **kw)
    dial.port = 9
    dial.seen = seen
    monkeypatch.setattr(sh, "streamable_http_client", dial)
    return dial


def saved(state) -> dict:
    p = Path(state) / "settings" / "tool_servers.json"
    return json.loads(p.read_text(encoding="utf-8"))["servers"] if p.exists() else {}


def yes(client, dev, reply):
    r = client.post(f"/v1/consent/{reply['consent_id']}/answer", headers=dev,
                    json={"answer": "yes", "factor": "tap", "card_sha": reply["card_sha"]})
    assert r.status_code == 200, r.json


# -- on this computer -----------------------------------------------------------------------------------------------

def test_a_server_on_this_computer_that_answers_is_kept_with_its_tools(world, serve, state):
    _app, client, dev, owner, _core, _z = world
    port, seen, _items = serve()
    r = client.post("/v1/tools/servers", headers=dev, json={"name": "My tools", "address": f"http://127.0.0.1:{port}/mcp"})
    assert r.status_code == 200, r.json
    assert r.json["state"] == "ready" and "2 tools" in r.json["line"]
    assert {t["name"] for t in r.json["server"]["tools"]} == {"show_list", "add_item"}
    rec = saved(state)["my_tools"]
    assert rec["state"] == "ready" and rec["kind"] == "http" and rec["added_by"] == "owner" and rec["has_password"] is False
    assert set(rec) == {"id", "name", "url", "kind", "has_password", "added_at", "added_by", "tools", "state"}
    assert seen and all(a == "" for a in seen)                          # no password: no Authorization header at all
    got = client.get("/v1/tools/servers", headers=owner)
    assert got.status_code == 200 and [s["id"] for s in got.json["servers"]] == ["my_tools"]
    assert got.json["servers"][0]["where"] == "here" and got.json["servers"][0]["tool_count"] == 2
    # the same name again is refused; the routes take a paired device only for changes
    assert client.post("/v1/tools/servers", headers=dev, json={"name": "my tools", "address": f"http://127.0.0.1:{port}/mcp"}).status_code == 409
    assert client.post("/v1/tools/servers", headers=owner, json={"name": "x", "address": f"http://127.0.0.1:{port}/mcp"}).status_code == 403
    assert client.get("/v1/tools/servers").status_code == 401


def test_one_that_does_not_answer_is_not_kept(world, state):
    _app, client, dev, owner, _core, _z = world
    r = client.post("/v1/tools/servers", headers=dev, json={"name": "gone", "address": f"http://127.0.0.1:{_free_port()}/mcp"})
    assert r.status_code == 502 and "Nothing was kept." in r.json["error"]
    assert saved(state) == {} and client.get("/v1/tools/servers", headers=owner).json["servers"] == []


@pytest.mark.parametrize("address, words", [
    ("python server.py", "http://"), ("http://user:pw@127.0.0.1:9/mcp", "password box"), ("http://127.0.0.1:9/mcp?x=1", "no ?"),
    ("http://127.0.0.2:9/mcp", "127.0.0.1 or localhost"), ("http://127.0.0.1:9/sse", "older kind"), ("ftp://127.0.0.1/", "http://"),
    ("http://169.254.169.254/mcp", "home network"),
])
def test_addresses_refused_plainly(world, address, words, state):
    _app, client, dev, _owner, _core, z = world
    r = client.post("/v1/tools/servers", headers=dev, json={"name": "x", "address": address})
    assert r.status_code == 400 and words in r.json["error"], r.json
    assert saved(state) == {} and addresses.cards(z.consents) == []


def test_a_password_in_the_query_string_is_refused(world, serve, state):
    _app, client, dev, _owner, _core, _z = world
    port, seen, _ = serve()
    r = client.post("/v1/tools/servers?password=pw-1", headers=dev, json={"name": "a", "address": f"http://127.0.0.1:{port}/mcp"})
    assert r.status_code == 400 and "never sent in the address" in r.json["error"]
    assert saved(state) == {} and seen == []


def test_a_password_goes_to_the_vault_and_never_into_the_file_a_reply_or_a_log(world, serve, state):
    _app, client, dev, owner, core, _z = world
    pw = "pw-" + secrets.token_hex(12)
    port, seen, _ = serve(password=pw)
    url = f"http://127.0.0.1:{port}/mcp"
    # without it, or a wrong one: it does not answer, so nothing is kept
    assert client.post("/v1/tools/servers", headers=dev, json={"name": "locked tools", "address": url}).status_code == 502
    r = client.post("/v1/tools/servers", headers=dev, json={"name": "locked tools", "address": url, "password": "pw-wrong"})
    assert r.status_code == 502 and "did not accept its password" in r.json["error"] and saved(state) == {}
    # with the vault locked it can't be kept, so nothing is sent at all
    n = len(seen)
    assert client.post("/v1/vault/lock", headers=owner).status_code == 200
    r = client.post("/v1/tools/servers", headers=dev, json={"name": "locked tools", "address": url, "password": pw})
    assert r.status_code == 409 and "Unlock your vault" in r.json["error"] and len(seen) == n
    assert client.post("/v1/vault/unlock", json={"method": "pin", "secret": PIN}, headers=dev).status_code == 200
    r = client.post("/v1/tools/servers", headers=dev, json={"name": "locked tools", "address": url, "password": pw})
    assert r.status_code == 200 and r.json["server"]["has_password"] is True
    assert seen[-1] == f"Bearer {pw}"
    from puffpai_core.zone1 import archivist
    assert archivist.for_state(state, core.bus).vault.meta("tool_server.locked_tools") is not None
    listed = client.get("/v1/tools/servers", headers=owner)
    assert pw not in listed.get_data(as_text=True) and pw not in r.get_data(as_text=True)
    assert saved(state)["locked_tools"]["has_password"] is True
    for p in Path(state).rglob("*"):
        if p.is_file() and p.suffix in (".json", ".jsonl", ".log", ".txt", ".tmp", ".db"):
            assert pw.encode() not in p.read_bytes(), p.name
    # typed as "Bearer <it>" is sent as typed
    assert mcp.outside_auth("Bearer " + pw) == "Bearer " + pw and mcp.outside_auth(pw) == "Bearer " + pw
    assert mcp.outside_auth("two words") is None and mcp.outside_auth("") is None


def test_remove_drops_the_record_and_the_vault_entry(world, serve, state):
    _app, client, dev, owner, core, _z = world
    pw = "pw-" + secrets.token_hex(12)
    port, _seen, _ = serve(password=pw)
    assert client.post("/v1/tools/servers", headers=dev, json={"name": "kept", "address": f"http://127.0.0.1:{port}/mcp",
                                                               "password": pw}).status_code == 200
    from puffpai_core.zone1 import archivist
    vault = archivist.for_state(state, core.bus).vault
    assert client.post("/v1/vault/lock", headers=owner).status_code == 200
    r = client.delete("/v1/tools/servers/kept", headers=dev)
    assert r.status_code == 423 and "Nothing was removed" in r.json["error"] and "kept" in saved(state)
    assert client.post("/v1/vault/unlock", json={"method": "pin", "secret": PIN}, headers=dev).status_code == 200
    r = client.delete("/v1/tools/servers/kept", headers=dev)
    assert r.status_code == 200 and "out of your vault" in r.json["line"]
    assert saved(state) == {} and vault.meta("tool_server.kept") is None
    assert client.delete("/v1/tools/servers/kept", headers=dev).status_code == 404


# -- anywhere else: her Yes on the Bridge's address card first -------------------------------------------------------

def test_a_home_network_server_waits_for_the_yes_and_nothing_connects_before_it(world, serve, dials, state):
    app, client, dev, owner, core, z = world
    dials.port, _seen, _ = serve()
    r = client.post("/v1/tools/servers", headers=dev, json={"name": "Lamps", "address": LAN_URL})
    assert r.status_code == 202, r.json
    assert r.json["state"] == "waiting" and r.json["line"] == f"Waiting for your Yes on the Bridge page for {LAN}. Nothing connects before that."
    assert r.json["title"] == "Allow this address and port for Lamps?"
    assert f"Lamps (outside-lamps) asks to reach 192.168.1.20 port 8123 on your home network." in r.json["lines"]
    rec = z.consents.get(r.json["consent_id"])
    assert rec["scope"] == {"kind": "host", "target": "pkg:outside-lamps"} and rec["status"] == "pending"
    assert saved(state)["lamps"]["state"] == "waiting" and dials.seen == []
    # a refresh before the Yes shows the same card again, and still dials nothing
    again = client.post("/v1/tools/servers/lamps/refresh", headers=dev)
    assert again.status_code == 202 and again.json["consent_id"] == r.json["consent_id"] and dials.seen == []
    # the brain does not see it, and a call made anyway is refused before anything is sent
    s = ts.ToolServers(core, lambda: z)
    assert s.ready() == []
    with pytest.raises(mcp.McpError) as got:
        mcp.list_tools(s.target("lamps"))
    assert got.value.code == "bridge" and dials.seen == []
    assert z.bridge.policy.fresh().grants == []                          # nothing here granted anything


def test_refresh_after_the_yes_connects_and_a_yes_taken_back_stops_it(world, serve, dials, state):
    app, client, dev, owner, core, z = world
    dials.port, _seen, items = serve()
    r = client.post("/v1/tools/servers", headers=dev, json={"name": "Lamps", "address": LAN_URL})
    yes(client, dev, r.json)
    # her Yes with the Bridge closed: still nothing goes out
    closed = client.post("/v1/tools/servers/lamps/refresh", headers=dev)
    assert closed.status_code == 409 and "Bridge is closed" in closed.json["error"] and dials.seen == []
    z.bridge.open()
    done = client.post("/v1/tools/servers/lamps/refresh", headers=dev)
    assert done.status_code == 200, done.json
    assert done.json["state"] == "ready" and {t["name"] for t in done.json["server"]["tools"]} == {"show_list", "add_item"}
    assert dials.seen == [LAN_URL] and saved(state)["lamps"]["state"] == "ready"
    s = ts.ToolServers(core, lambda: z)
    assert mcp.call_tool(s.target("lamps"), "add_item", {"item": "bulbs"})["ok"] is True and items == ["bulbs"]
    # she takes the Yes back on the Bridge page: the next call is refused, never sent
    row = [x for x in client.get("/v1/bridge/addresses", headers=owner).json["addresses"] if x["package"] == "outside-lamps"][0]
    assert client.delete(f"/v1/bridge/addresses/{row['consent_id']}", headers=dev).status_code == 200
    n = len(dials.seen)
    got = mcp.call_tool(s.target("lamps"), "add_item", {"item": "more"})
    assert got["ok"] is False and got["error"] == "bridge" and len(dials.seen) == n and items == ["bulbs"]
    # removing it takes its address card and grant back too
    assert client.delete("/v1/tools/servers/lamps", headers=dev).status_code == 200
    assert [c for c in addresses.cards(z.consents, "outside-lamps") if c["status"] in ("pending", "granted")] == []


# -- the brain: <id>.<tool>, and Drawbridge's review --------------------------------------------------------------

def _chat_brain(core, *script):
    from puffpai_core.api.groups import chat
    from puffpai_core.zone2 import settings as model_settings
    model_settings.save(core.state, address="http://127.0.0.1:9", model="fake:1b")
    with core.app.app_context():
        brain = chat.brain()
    brain._runtime = FakeRuntime(*script)
    brain._gate = GateDouble().gate
    return brain


def test_the_brain_sees_id_tool_and_each_call_goes_through_drawbridge(world, serve):
    from puffpai_core import wiring
    app, client, dev, owner, core, z = world
    port, _seen, items = serve()
    assert client.post("/v1/tools/servers", headers=dev, json={"name": "My tools", "address": f"http://127.0.0.1:{port}/mcp"}).status_code == 200
    brain = _chat_brain(core, call("my_tools__show_list"), {"text": "It is empty."}, {"text": "None."})
    got = brain.turn("show my list")
    names = {t["function"]["name"] for t in brain._runtime.calls[0]["tools"]}
    assert {"my_tools__show_list", "my_tools__add_item"} <= names
    assert not names & {"add_tool_server", "remove_tool_server", "list_tool_servers"}      # no slot spent on them here
    brain.turn("which tool servers do I have?")
    assert {"add_tool_server", "remove_tool_server", "list_tool_servers"} <= {t["function"]["name"] for t in brain._runtime.calls[-1]["tools"]}
    assert "Tool servers the owner added" in brain._runtime.seen(0) and "my_tools: My tools" in brain._runtime.seen(0)
    assert got["used"] == ["my_tools.show_list"] and got["changed"] == [] and "The list is empty." in brain._runtime.seen(1)
    assert [c for c in core.consent.list("pending") if c["scope"]["kind"] == "tool_call"] == []     # read-only: no card
    # a tool with no readOnlyHint counts as changing: Drawbridge asks, with her card for this one call
    review = wiring.tool_review(core, z.drawbridge, wait_s=0.5)
    verdict = review({"package": "outside-my_tools", "name": "add_item", "args": {"item": "eggs"}, "summary": ["item: eggs"],
                      "entry": {"name": "add_item", "annotations": {}}})
    assert verdict["run"] is False and verdict["asked"] is True and verdict["changing"] is True and items == []
    card = [c for c in core.consent.list() if c["scope"] == {"kind": "tool_call", "target": "outside-my_tools.add_item"}]
    assert len(card) == 1 and card[0]["card"]["title"] == "Let My tools add item?"
    read = review({"package": "outside-my_tools", "name": "show_list", "args": {}, "summary": [],
                   "entry": {"name": "show_list", "annotations": {"readOnlyHint": True}}})
    assert read["run"] is True and read["changing"] is False and read["asked"] is False


def test_chat_adds_lists_and_removes_with_a_yes_and_never_asks_for_a_password(world, serve, state):
    from puffpai_core.zone2.concierge import Concierge
    from puffpai_core.zone2.tools import call as call_tool
    app, client, dev, owner, core, z = world
    port, _seen, _ = serve(password="pw-" + secrets.token_hex(8))
    c = Concierge(core.registry, core.state, keep=lambda t: t, outside=ts.ToolServers(core, lambda: z))
    table = c.table_for("add my tool server", 0)
    asked = []
    ctx = {"ask": lambda q: asked.append(q) or True}
    got = call_tool(table, "list_tool_servers", {}, ctx)
    assert got["ok"] and got["result"]["said"] == "No tool servers are added yet." and asked == []
    add = table.find("add_tool_server")
    assert add.changes is True and "password" not in add.schema["properties"]
    # it needs a password: chat says the Developer page takes it, and nothing is kept
    got = call_tool(table, "add_tool_server", {"name": "locked", "address": f"http://127.0.0.1:{port}/mcp"}, ctx)
    assert asked[-1]["kind"] == "core_change" and asked[-1]["tool"] == "add_tool_server"
    assert got["result"]["ok"] is False and "Developer page" in got["result"]["said"] and saved(state) == {}
    # off this computer: the Bridge card's line
    got = call_tool(table, "add_tool_server", {"name": "Lamps", "address": LAN_URL}, ctx)
    assert got["result"]["said"] == f"Waiting for your Yes on the Bridge page for {LAN}. Nothing connects before that."
    assert saved(state)["lamps"]["added_by"] == "chat"
    listed = call_tool(table, "list_tool_servers", {}, ctx)["result"]["servers"]
    assert [(x["id"], x["state"]) for x in listed] == [("lamps", "waiting")]
    n = len(asked)
    got = call_tool(table, "remove_tool_server", {"name": "Lamps"}, ctx)
    assert got["result"]["ok"] is True and len(asked) == n + 1 and saved(state) == {}
    # a No from her: nothing changes
    got = call_tool(table, "add_tool_server", {"name": "Lamps", "address": LAN_URL}, {"ask": lambda q: False})
    assert got["ok"] is False and saved(state) == {}


def test_a_core_change_from_chat_raises_its_own_yes_card(world):
    from puffpai_core import wiring
    _app, _client, _dev, _owner, core, _z = world
    ask = wiring.core_question(core, wait_s=0.5)
    assert ask({"kind": "core_change", "tool": "add_tool_server", "title": "May I add the tool server Lamps?",
                "arguments": ["name: Lamps"]}) is None                    # no answer in time: not done
    card = [c for c in core.consent.list() if c["scope"] == {"kind": "tool_call", "target": "core.add_tool_server"}]
    assert len(card) == 1 and card[0]["status"] == "expired" and card[0]["card"]["title"] == "May I add the tool server Lamps?"


# -- the Desktop ------------------------------------------------------------------------------------------------------

def test_the_desktop_block_by_node_and_where_it_is_loaded():
    import shutil
    import subprocess
    from conftest import REPO
    web = REPO / "packages" / "desktop" / "web"
    for shell, src in (("index.html", "/ui/tool_servers.js"), ("window.html", '"tool_servers.js"')):
        html = (web / shell).read_text(encoding="utf-8")
        assert src in html and html.index(src) < html.index("pages/developer.js"), shell
    assert "window.PuffToolServers.block()" in (web / "pages" / "developer.js").read_text(encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    assert subprocess.run([node, "--check", str(web / "tool_servers.js")], capture_output=True, timeout=60).returncode == 0
    got = subprocess.run([node, str(REPO / "tests" / "js" / "tool_servers.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr
