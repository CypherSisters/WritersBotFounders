"""Lane L2 fixes after the verifier: one token seam (Steward.token), no tokenless MCP calls,
a gated and schema-checked checklist_add, gated /v1/chat/tool results."""

import sys
import types

import pytest

from puffpai_core.api import create_app
from puffpai_core.zone2 import mcp
from puffpai_core.zone2.brain import Brain
from puffpai_core.zone2.concierge import Concierge
from zone2_doubles import FakeRuntime, GateDouble, call, make_brain


class FakeSteward:
    def __init__(self, token="ppt_from-steward"):
        self.tok = token
        self.asked = []

    def token(self, pid):
        self.asked.append(pid)
        return self.tok

    def start(self, pid):
        return {"id": pid, "status": "running", "port": 18501}


HTTP = {"abilities": {"mcp": {"transport": "http", "url": "http://127.0.0.1:{port}/mcp"}}}


@pytest.mark.parametrize("manifest", [HTTP, {"abilities": {"mcp": {"transport": "stdio",
                                                                       "command": [sys.executable, "-c", "pass"]}}}])
def test_mcp_refuses_without_a_token(manifest, tmp_path):
    target = mcp.target_for({"id": "freshtable", "name": "FreshTable", "port": 9}, manifest, None, tmp_path)
    got = mcp.call_tool(target, "add_to_list", {"item": "milk"})
    assert got == {"ok": False, "text": "no token for freshtable", "error": "token"}
    with pytest.raises(mcp.McpError) as err:
        mcp.list_tools(target)
    assert err.value.code == "token"


def test_the_one_token_seam_is_stewards_token(state):
    steward = FakeSteward()
    c = Concierge(registry=None, state=state, keep=lambda t: t, token=steward.token)
    target = c._target({"id": "freshtable", "name": "FreshTable", "port": 18500}, HTTP)
    assert target.token == "ppt_from-steward" and steward.asked == ["freshtable"]
    assert Concierge(registry=None, state=state, keep=str)._target({"id": "x", "port": 1}, HTTP).token is None

    def broken(_pid):
        raise RuntimeError("steward is down")
    assert Concierge(registry=None, state=state, keep=str, token=broken).token_for("x") is None


def test_production_brain_is_wired_to_steward(state):
    app = create_app(state)
    steward = FakeSteward()
    app.extensions["puffpai"].steward = steward
    with app.app_context():
        from puffpai_core.api.groups.chat import brain
        b = brain()
    assert isinstance(b, Brain) and b.token("freshtable") == "ppt_from-steward"
    assert b.start("freshtable") == {"ok": True, "port": 18501}


def test_production_brain_without_steward_calls_nothing_tokenless(state, freshtable):
    rt = FakeRuntime(call("freshtable__add_to_list", item="milk"), {"text": "ok"})
    brain, items, reg = make_brain(state, freshtable, rt)
    brain.targets = None                                   # the real MCP path, with no token anywhere
    brain.manifest = lambda pid: {**freshtable, "abilities": HTTP["abilities"] | {"changes": ["add_to_list"]}}
    brain.turn("hello")
    assert items == []                                     # nothing was listed or called without a token


@pytest.fixture
def checklist(monkeypatch):
    class Added(list):                                # lane L5's Checklist, as the Brain calls it
        trusts: list = []

        def add(self, step, package, blocks=None, trust=None, raised_by=None):
            self.append(step)
            self.trusts.append((package, trust, raised_by))
            return {"id": "step_x", "title": step.get("title", "")}
    return Added()


def test_checklist_add_is_gated_and_fits_the_step_shape(state, freshtable, checklist):
    # Lane I6a: chat raises reminders only (confirm or plug_in); a run_command step is refused.
    rt = FakeRuntime(call("checklist_add", title="Call 555-0134", why="She asked"),
                     call("checklist_add", title="x", why="y", kind="run_command"),
                     {"text": "ok"})
    brain, _, _ = make_brain(state, freshtable, rt, gate=GateDouble(vault_open=True), checklist=checklist)
    brain.turn("help")
    assert len(checklist) == 1
    step = checklist[0]
    assert "555-0134" not in step["title"] and "{{vault:" in step["title"]
    assert step["kind"] == "confirm" and "shell" not in step and step["id"].startswith("brain-")
    assert "never a command to run" in rt.seen(2)          # a run_command step from chat is refused


def test_chat_tool_results_are_gated_in_dicts_and_lists(state, freshtable):
    rows = [{"id": "notes", "kind": "tool", "name": "Notes", "origin": "made", "status": "stopped",
             "description": "Phone Sam on 555-0134"}]
    brain, _, _ = make_brain(state, freshtable, FakeRuntime(), gate=GateDouble(vault_open=True), extra_rows=rows)
    got = brain.call_tool("list_installed", {})
    assert got["ok"] is True and isinstance(got["result"], list)
    assert "555-0134" not in str(got["result"]) and "{{vault:" in str(got["result"])


@pytest.mark.parametrize("args,why", [
    ({"kind": "run_command"}, "never a command to run"),         # lane I6a: chat raises reminders only
    ({"kind": "open_url"}, "never a command to run"),
    ({"kind": "confirm", "text": "echo hi"}, "unknown argument 'text'"),
    ({"extra": "z"}, "unknown argument 'extra'"),
    ({"kind": "confirm", "key": "API_KEY"}, "unknown argument 'key'"),
])
def test_checklist_add_refuses_blank_text_and_unknown_args(state, freshtable, checklist, args, why):
    brain, _, _ = make_brain(state, freshtable, FakeRuntime(), gate=GateDouble(), checklist=checklist)
    got = brain.call_tool("checklist_add", {"title": "t", "why": "w", **args})
    assert got["ok"] is False and got["bad_args"] is True and why in got["error"]
    assert checklist == []                                 # nothing was added


def test_chat_tool_route_answers_400_for_wrong_args(state, freshtable, checklist):
    app = create_app(state)
    core = app.extensions["puffpai"]
    brain, _, _ = make_brain(state, freshtable, FakeRuntime(), gate=GateDouble(), checklist=checklist)
    brain.registry = core.registry
    app.extensions["puffpai_brain"] = brain
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    client = app.test_client()
    r = client.post("/v1/chat/tool", headers=owner,
                    json={"tool": "checklist_add", "args": {"title": "t", "why": "w", "extra": "z"}})
    assert r.status_code == 400 and "unknown argument 'extra'" in r.json["error"] and checklist == []
    r = client.post("/v1/chat/tool", headers=owner,
                    json={"tool": "checklist_add", "args": {"title": "t", "why": "w", "kind": "run_command"}})
    assert r.status_code == 400 and "never a command to run" in r.json["error"]
    r = client.post("/v1/chat/tool", headers=owner, json={"tool": "checklist_add", "args": ["t", "w"]})
    assert r.status_code == 400                            # args that are not an object are refused, not dropped
    r = client.post("/v1/chat/tool", headers=owner, json={"tool": "checklist_add", "args": {"title": "t", "why": "w"}})
    assert r.status_code == 200 and len(checklist) == 1
