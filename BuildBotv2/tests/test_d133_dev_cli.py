"""D133 (lane dev-cli): the small developer commands v1 had, on the running bot's own routes.

The chat's commands run on a stand-in for the Core's answers. `where` needs no running bot."""

from __future__ import annotations

import io
import json
import types

import pytest

from puffpai_core import cli, termchat


class Answers:
    """The Core's answers by path, with what the chat asked for written down."""

    def __init__(self, table=None, status=None):
        self.table, self.status, self.calls, self.streams = table or {}, status or {}, [], []

    def call(self, method, path, body=None, timeout=termchat.CALL_TIMEOUT_S):
        self.calls.append((method, path, body, timeout))
        return self.status.get(path, 200), self.table.get(path, {"error": "no such thing"} if path not in self.table else None)

    def stream(self, path, body):
        self.streams.append((path, dict(body)))
        return 200, None, iter([{"type": "token", "text": "Hello."}, {"type": "done", "text": "Hello."}])


def _chat(api, keys=(), hidden=()):
    out = io.StringIO()
    keys, hidden = iter(keys), iter(hidden)

    def read(it):
        def go(prompt=""):
            try:
                return next(it)
            except StopIteration:
                raise EOFError from None
        return go
    chat = termchat.Chat(api, read_line=read(keys), read_hidden=read(hidden), open_url=lambda u: None, sleep=lambda s: None,
                         look=termchat.Look(out, env={}, tty=lambda s: False, vt_on=lambda s: False))
    return chat, out


# -- chat --once ----------------------------------------------------------------------------------------------------------
def test_once_says_one_answer_and_stops():
    api = Answers({"/v1/consent?status=pending": []})
    chat, out = _chat(api)
    assert chat.once("hi") is True
    assert api.streams == [("/v1/chat/stream", {"text": "hi"})] and "Hello." in out.getvalue()


def test_once_takes_no_yes(monkeypatch):
    chat, out = _chat(Answers())
    monkeypatch.setattr(chat, "turn", lambda t: (_ for _ in ()).throw(EOFError()))
    assert chat.once("add it") is False
    assert termchat.ONCE_CARD in out.getvalue()


def test_the_parser_takes_chat_once_and_no_words_is_the_chat(monkeypatch):
    seen = []
    monkeypatch.setattr(termchat, "main", lambda: seen.append("chat") or 0)
    monkeypatch.setattr(termchat, "run_verb", lambda fn, arg: seen.append((fn, arg)) or 0)
    assert cli.main(["chat"]) == 0 and cli.main(["chat", "--once", "hello there"]) == 0
    assert seen == ["chat", ("once", "hello there")]


# -- where ----------------------------------------------------------------------------------------------------------------
def test_where_says_the_folders_and_ports_and_never_a_key(state, monkeypatch, capsys):
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "ports.json").write_text(json.dumps({"port": 8430, "pages_port": 8432, "proxy_port": 8431}), encoding="utf-8")
    (state / "settings" / "terminal.json").write_text(json.dumps({"port": 8430, "token": "ppd_secret_value"}), encoding="utf-8")
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 8430, "token": "ppd_secret_value"})
    assert cli.main(["where"]) == 0
    text = capsys.readouterr().out
    assert str(state) in text and "8430 the Core, 8432 pages, 8431 the Bridge proxy" in text and "yes, on port 8430" in text
    assert "ppd_secret_value" not in text and "value not printed" in text


def test_where_needs_no_running_bot(state, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    assert cli.main(["where"]) == 0
    assert "running:     no" in capsys.readouterr().out


# -- agents ---------------------------------------------------------------------------------------------------------------
def test_agents_names_all_seven_with_what_each_does_now():
    chat, out = _chat(Answers({"/v1/registry": [{"id": "a", "name": "App A", "status": "running", "health": {"ok": False}}], "/v1/vault/status": {"state": "locked"},
                               "/v1/model": {"settings": {"model": "qwen3:8b"}}, "/v1/checklist": [{}], "/v1/toolsmith": {"builds": [{}]}}))
    assert chat.cmd_agents() == 0
    text = out.getvalue()
    for name in ("Archivist", "Concierge", "Steward", "Caretaker", "Majordomo", "Drawbridge", "Toolsmith"):
        assert name + ":" in text
    assert "Your vault is locked." in text and "qwen3:8b" in text and "Not working: App A." in text and "1 things on Your turn" in text


def test_agents_stays_up_when_a_route_says_no():
    chat, out = _chat(Answers(status={"/v1/registry": 500, "/v1/model": 500, "/v1/vault/status": 500, "/v1/checklist": 500, "/v1/toolsmith": 500}))
    assert chat.cmd_agents() == 0 and "Toolsmith:" in out.getvalue()


# -- outside --------------------------------------------------------------------------------------------------------------
LEDGER = [{"t": "2026-10-31T10:00:00", "subject": "freshtable", "host": "example.com", "outcome": "done", "code": "granted"},
          {"t": "2026-10-31T10:01:00", "subject": "x", "host": "bad.example", "outcome": "refused", "code": "bridge_closed"},
          {"event": "chain_restarted"}]


def test_outside_says_the_bridge_and_who_tried_to_leave_in_her_words():
    chat, out = _chat(Answers({"/v1/bridge": {"state": "closed"}, "/v1/bridge/ledger?limit=20": LEDGER, "/v1/registry": [{"id": "freshtable", "name": "FreshTable"}]}))
    assert chat.cmd_outside() == 0
    text = out.getvalue()
    assert "FreshTable reached example.com: you said Yes to it." in text
    assert "x was refused bad.example: the Bridge was closed." in text and "The outside log started again." in text


def test_outside_log_asks_for_as_many_as_she_says_only():
    api = Answers({"/v1/bridge/ledger?limit=5": [], "/v1/registry": []})
    chat, out = _chat(api)
    assert chat.cmd_outside("log 5") == 0
    assert "Nothing has tried to leave" in out.getvalue() and not any(p == "/v1/bridge" for _m, p, _b, _t in api.calls)


# -- keys -----------------------------------------------------------------------------------------------------------------
def test_keys_lists_names_and_never_a_value():
    chat, out = _chat(Answers({"/v1/toolsmith/providers": [{"id": "anthropic", "company": "Anthropic"}],
                               "/v1/toolsmith/keys": [{"provider": "anthropic", "kept": True, "value": "sk-secret"}],
                               "/v1/github/key": {"linked": False, "line": "Not linked."}, "/v1/toolsmith/apis": {"apis": [{"name": "Weather"}]},
                               "/v1/checklist": [{"kind": "enter_secret", "title": "Paste the map key"}]}))
    assert chat.cmd_keys() == 0
    text = out.getvalue()
    assert "Anthropic (anthropic), for Toolsmith: saved" in text and "GitHub: Not linked." in text and "Weather" in text and "Paste the map key" in text
    assert "sk-secret" not in text


def test_keys_add_types_hidden_and_never_says_it_back():
    api = Answers({"/v1/toolsmith/keys/anthropic": {"line": "Saved in your vault."}})
    chat, out = _chat(api, hidden=["  sk-typed-value  "])
    assert chat.cmd_keys("add anthropic") == 0
    assert api.calls[-1][:3] == ("PUT", "/v1/toolsmith/keys/anthropic", {"value": "sk-typed-value"})
    assert "sk-typed-value" not in out.getvalue() and "Saved in your vault." in out.getvalue()


def test_keys_add_with_nothing_typed_saves_nothing():
    api = Answers()
    chat, _out = _chat(api, hidden=[""])
    assert chat.cmd_keys("add anthropic") == 1 and api.calls == []


def test_keys_remove_goes_to_the_route_with_the_name_quoted():
    api = Answers({"/v1/toolsmith/keys/a%2Fb": {"line": "The key is removed."}})
    chat, _out = _chat(api)
    assert chat.cmd_keys("remove a/b") == 0
    assert api.calls[-1][:2] == ("DELETE", "/v1/toolsmith/keys/a%2Fb")


def test_keys_says_how_when_it_cant_tell_what_she_meant():
    chat, out = _chat(Answers())
    assert chat.cmd_keys("nonsense") == 1 and "keys add <company>" in out.getvalue()


# -- prove ----------------------------------------------------------------------------------------------------------------
GOOD = {"/v1/health": {"ok": True, "version": "0.1.0"}, "/v1/model": {"settings": {"model": "m"}, "state": {"state": "ready"}}, "/v1/vault/status": {"state": "open"},
        "/v1/bridge": {"state": "closed"}, "/v1/registry": [{"id": "a", "name": "App A", "status": "running", "health": {"ok": True}}], "/v1/chat": {"text": "OK"}}


def test_prove_passes_with_a_real_chat_turn():
    api = Answers(GOOD)
    chat, out = _chat(api)
    assert chat.cmd_prove() == 0
    assert "It works." in out.getvalue() and "App A: working." in out.getvalue()
    method, path, body, timeout = api.calls[-1]
    assert (method, path) == ("POST", "/v1/chat") and body["conversation"] == "prove" and timeout == termchat.PROVE_TIMEOUT_S


@pytest.mark.parametrize("change,word", [({"/v1/chat": {"text": ""}}, "One real chat turn"), ({"/v1/model": {"settings": {"model": "m"}, "state": {"state": "error", "reason": "no model"}}}, "no model"),
                                         ({"/v1/registry": [{"id": "a", "status": "running", "health": {"ok": False, "reason": "down"}}]}, "not working: down")])
def test_prove_says_what_did_not_pass(change, word):
    chat, out = _chat(Answers({**GOOD, **change}))
    assert chat.cmd_prove() == 1
    assert word in out.getvalue() and "1 of these did not pass" in out.getvalue()


# -- the same names as commands ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["agents", "outside", "keys", "prove"])
def test_each_is_a_chat_command_a_shell_verb_and_is_found_by_search(name):
    assert any(c["name"] == name and c["here"] for c in termchat.all_commands())
    assert callable(getattr(termchat.Chat, "cmd_" + name))
    assert cli.build_parser().parse_args([name]).fn is cli._cmd_verb
    assert name not in termchat.NOT_YET and termchat.COMMAND_WORDS.get(name)


# -- puffpai new ----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("kind", cli.NEW_KINDS)
def test_new_writes_a_package_that_passes_check(state, tmp_path, monkeypatch, capsys, kind):
    from puffpai_core.packages.check import check
    monkeypatch.chdir(tmp_path)
    assert cli.main(["new", kind, "my-thing"]) == 0
    folder = tmp_path / "my-thing"
    manifest = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "my-thing" and manifest["kind"] == kind and manifest["name"] == "My Thing"
    assert not [p for p in folder.rglob("*") if p.is_file() and ("__ID__" in p.read_text(encoding="utf-8") or "__NAME__" in p.read_text(encoding="utf-8"))]
    report = check(folder, start_timeout_s=60)
    assert report.passed() >= 2, "\n".join(report.lines())


def test_new_refuses_what_it_cant_start_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["new", "provider", "x"]) == 2 and cli.main(["new", "tool", "Bad Name"]) == 2 and cli.main(["new", "tool", "../x"]) == 2
    assert list(tmp_path.iterdir()) == []
    (tmp_path / "taken").mkdir()
    assert cli.main(["new", "tool", "taken"]) == 1
    assert list((tmp_path / "taken").iterdir()) == []


# -- changing the brain ------------------------------------------------------------------------------------------------
@pytest.fixture
def model_route(state, monkeypatch):
    from puffpai_core.api import create_app
    from puffpai_core.zone2 import settings as brain
    app = create_app(state, port=51234)
    core = app.extensions["puffpai"]
    ollama = {"up": True, "have": {"small:1b", "big:8b"}}
    monkeypatch.setattr(brain, "_ask_version", lambda url, timeout_s=1.5: ollama["up"])
    monkeypatch.setattr(brain, "_has_model", lambda url, model, timeout_s=2.0: model in ollama["have"])
    brain.save(state, model="small:1b")
    device = {"Authorization": f"Bearer {core.tokens.issue_device('laptop')}"}
    return types.SimpleNamespace(client=app.test_client(), core=core, ollama=ollama, state=state, device=device, brain=brain)


def test_the_brain_changes_for_her_paired_devices_only(model_route):
    r = model_route
    assert r.client.put("/v1/model", json={"model": "big:8b"}).status_code == 401
    pkg = {"Authorization": f"Bearer {r.core.tokens.issue_package_token('x')}"}
    assert r.client.put("/v1/model", headers=pkg, json={"model": "big:8b"}).status_code == 403
    key = {"Authorization": f"Bearer {r.core.tokens.issue_owner_key()}"}
    assert r.client.put("/v1/model", headers=key, json={"model": "big:8b"}).status_code == 403
    assert r.brain.load(r.state).model == "small:1b"
    got = r.client.put("/v1/model", headers=r.device, json={"model": "big:8b"})
    assert got.status_code == 200 and "big:8b" in got.json["line"] and r.brain.load(r.state).model == "big:8b"


@pytest.mark.parametrize("body", [None, {}, {"model": ""}, {"model": "  "}, {"model": 7}, {"model": "x" * 201}])
def test_no_name_saves_nothing(model_route, body):
    r = model_route
    assert r.client.put("/v1/model", headers=r.device, json=body).status_code == 400
    assert r.brain.load(r.state).model == "small:1b"


def test_a_model_not_here_or_no_ollama_saves_nothing(model_route):
    r = model_route
    got = r.client.put("/v1/model", headers=r.device, json={"model": "other:1b"})
    assert got.status_code == 400 and got.json["code"] == "model_missing"
    r.ollama["up"] = False
    assert r.client.put("/v1/model", headers=r.device, json={"model": "big:8b"}).status_code == 503
    assert r.brain.load(r.state).model == "small:1b"


def test_the_chat_command_changes_it_and_says_why_not():
    api = Answers({"/v1/model": {"line": "The brain is now big:8b. The next chat turn uses it."}})
    chat, out = _chat(api)
    assert chat.cmd_brain("big:8b") == 0
    assert api.calls[-1][:3] == ("PUT", "/v1/model", {"model": "big:8b"}) and "now big:8b" in out.getvalue()
    api = Answers({"/v1/model": {"error": "other is not on this computer yet"}}, status={"/v1/model": 400})
    chat, out = _chat(api)
    assert chat.cmd_brain("other") == 1 and "did not change" in out.getvalue()
