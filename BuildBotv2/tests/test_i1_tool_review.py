"""Integration lane I1, verifier fixes: every package tool call goes through Drawbridge (X27, 09 section 3),
a changing tool outside her approved scope gets a ``tool_call`` Yes card for that one call, Yes cards show
what they grant (CLI and Desktop), a broken gate is a plain 503, the Core is wired in ``start_core`` only,
and an install goes straight from "installing" to its start."""

import json
import shutil
import subprocess
import threading

import pytest

from conftest import REPO, wait_for, yes
from puffpai_core.consent import card_sha, card_view
from puffpai_core.zone3.drawbridge import Scope, tool_changes
from zone2_doubles import FakeRuntime, GateDouble, call


def use(tool, **args):
    return {"tool_calls": [{"id": "c1", "name": tool, "arguments": args}]}

NODE_OK = shutil.which("node") is not None


@pytest.fixture
def wired(core):
    from puffpai_core.api.app import wire_core
    wire_core(core)
    return core


def _chat_brain(core, *script, gate=None):
    """The Core's own Brain (``chat.brain()``: Steward's token, Drawbridge's review), on a scripted model."""
    from puffpai_core.api.groups import chat
    from puffpai_core.zone2 import settings as model_settings
    model_settings.save(core.state, address="http://127.0.0.1:9", model="fake:1b")
    with core.app.app_context():
        brain = chat.brain()
    brain._runtime = FakeRuntime(*script)
    brain._gate = (gate or GateDouble()).gate
    return brain


def _owner(core):
    return {"Authorization": "Bearer " + core.tokens.issue_owner_key()}


def _device(core):
    return {"Authorization": "Bearer " + core.tokens.issue_device("test phone")}


def _in_thread(fn):
    box = {}
    t = threading.Thread(target=lambda: box.setdefault("got", fn()), daemon=True)
    t.start()
    return t, box


def _pending(core, kind="tool_call"):
    return wait_for(lambda: [c for c in core.consent.list("pending") if c["scope"]["kind"] == kind], 30)


# -- item 4: create_app builds only; start_core (serve) wires -------------------------------------------------
def test_create_app_builds_the_app_only_and_start_core_wires_it(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import start_core, stop_core
    app = create_app(state, port=18419)
    core = app.extensions["puffpai"]
    assert app.extensions.get("puffpai.zone3") is None and core.zone3 is None
    assert "puffpai.tool_review" not in app.extensions and core.packages.drawbridge is None
    core.proxy_port = 0
    terminal = start_core(app)                                  # what serve calls
    try:
        assert core.zone3 is app.extensions["puffpai.zone3"] and core.packages.drawbridge is core.zone3.drawbridge
        assert callable(app.extensions["puffpai.tool_review"]) and core.zone3.proxy is not None
        assert core.zone3.watch is not None
        host = app.extensions["puffpai_toolsmith"]                      # lane L6's host, made by the one start
        proxy, watch = core.zone3.proxy, core.zone3.watch
        assert start_core(app) == terminal                              # once: nothing starts twice
        assert app.extensions["puffpai_toolsmith"] is host and core.zone3.proxy is proxy and core.zone3.watch is watch
    finally:
        stop_core(core, terminal)
        core.caretaker.stop()


# -- item 1: one rule for "is this tool changing?" -------------------------------------------------------------
def test_tool_changes_is_drawbridges_one_rule():
    m = {"abilities": {"changes": ["add_pantry"]}}
    assert tool_changes("add_pantry", m)                                        # the manifest says so
    assert tool_changes("add_shopping", {})                                     # its name says so
    assert tool_changes("tally", {}, {"annotations": {"readOnlyHint": False}})   # its own MCP hint
    assert tool_changes("tally", {}, {"annotations": {"destructiveHint": True, "readOnlyHint": True}})
    assert not tool_changes("list_shopping", {})                                # a plain read
    assert tool_changes("list_shopping", {}, None, "adopted:https://x")         # Toolsmith wrote it: unknown
    assert not tool_changes("list_shopping", {}, {"annotations": {"readOnlyHint": True}}, "made")
    assert tool_changes("", {}) and tool_changes(None, {})                      # no usable name: changing
    # A package's hint never lowers the manifest or the name rule.
    assert tool_changes("add_pantry", m, {"annotations": {"readOnlyHint": True}})
    assert tool_changes("add_shopping", {}, {"annotations": {"readOnlyHint": True}})


def test_every_package_tool_call_goes_through_drawbridge(wired, hello_copy, state):
    core = wired
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    review = core.app.extensions["puffpai.tool_review"]
    base = {"package": "hello-tool", "args": {"text": "hi"}, "summary": ["text: hi"]}
    got = review({**base, "name": "write_note", "entry": {"name": "write_note", "annotations": {}}})
    assert got["run"] is True and got["changing"] is True and got["asked"] is False      # on her install Yes
    got = review({**base, "name": "say_hello", "entry": {"name": "say_hello", "annotations": {}}})
    assert got["run"] is True and got["changing"] is False                               # read-only: runs
    got = review({**base, "package": "nothing-here", "name": "x"})
    assert got["run"] is False and got["end"] is True and core.consent.list("pending") == []
    audit = (state / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"in_scope"' in audit and '"not_installed"' in audit


def test_an_unknown_tool_asks_and_an_unanswered_card_expires_plainly(wired, hello_copy):
    from puffpai_core import wiring
    core = wired
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    review = wiring.tool_review(core, core.zone3.drawbridge, wait_s=0.5)
    got = review({"package": "hello-tool", "name": "tally", "args": {}, "summary": ["(no details)"]})
    assert got["run"] is False and got["asked"] is True and got["end"] is True
    assert got["line"] == "The Yes card for Hello Tool's tally got no answer in time, so it did not run. " \
                          "Nothing was changed."
    (rec,) = [c for c in core.consent.list() if c["scope"]["kind"] == "tool_call"]
    assert rec["status"] == "expired" and rec["scope"]["target"] == "hello-tool.tally"


def test_a_floor_level_tool_call_needs_the_pin(wired, hello_copy, archivist):
    from puffpai_core.consent import ConsentError
    core = wired
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    core.zone3.drawbridge.set_scope("hello-tool", Scope(tools={"reset_router": "changing"},
                                                        floor_tools={"reset_router"}))
    review = core.app.extensions["puffpai.tool_review"]
    t, box = _in_thread(lambda: review({"package": "hello-tool", "name": "reset_router", "args": {},
                                        "summary": ["(no details)"]}))
    (card,) = _pending(core)
    assert card["floor"] is True and card_view(card["card"], card["floor"])["lines"][-1].startswith("This one needs your PIN")
    with pytest.raises(ConsentError) as e:
        core.consent.answer(card["consent_id"], "yes", "tap", card_sha=card["card_sha"])
    assert e.value.code == "needs_pin"
    core.consent.answer(card["consent_id"], "yes", "pin", "4321", card_sha=card["card_sha"])
    t.join(10)
    assert box["got"]["run"] is True and box["got"]["asked"] is True


# -- item 1: the verifier's repro, on the real FreshTable ------------------------------------------------------
def _freshtable_without_add_shopping(tmp_path):
    src = tmp_path / "src" / "freshtable"
    shutil.copytree(REPO / "packages" / "freshtable", src, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    m = json.loads((src / "puffpai.json").read_text(encoding="utf-8"))
    m["abilities"]["changes"].remove("add_shopping")
    (src / "puffpai.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    return src


def _shopping(core):
    from puffpai_core.zone2 import mcp
    row = core.registry.get("freshtable")
    target = mcp.target_for(row, core.steward.manifest("freshtable"), core.steward.token("freshtable"),
                            core.state / "packages" / "freshtable")
    return mcp.call_tool(target, "list_shopping", {})["text"].lower()


def test_repro_a_changing_tool_left_off_the_install_card_asks_in_chat(wired, tmp_path, state):
    core = wired
    staged = core.packages.add(str(_freshtable_without_add_shopping(tmp_path)))
    assert "add_shopping" not in staged["card"]["changes"]
    assert yes(core, staged, timeout_s=600)["status"] == "installed"
    assert wait_for(lambda: core.registry.get("freshtable")["status"] == "running", 120)
    client, answers, owner, device = core.app.test_client(), core.app.test_client(), _owner(core), _device(core)
    milk = use("freshtable__add_shopping", name="milk")

    # 1. Her No: the tool does not run, the turn ends with one plain line, nothing is listed as changed.
    _chat_brain(core, milk, {"text": "Milk has been added."})
    t, box = _in_thread(lambda: client.post("/v1/chat", headers=owner, json={"text": "put milk on the shopping list"}))
    (card,) = _pending(core)
    assert card["scope"] == {"kind": "tool_call", "target": "freshtable.add_shopping"} and card["floor"] is False
    shown = answers.get("/v1/consent?status=pending", headers=owner).json[0]
    assert shown["title"] == "Let FreshTable add shopping?"
    assert "App: FreshTable (freshtable)" in shown["lines"] and "Tool: add_shopping" in shown["lines"]
    assert "With: name: milk" in shown["lines"]
    assert any("was not on FreshTable's install card" in ln for ln in shown["lines"])
    r = answers.post(f"/v1/consent/{card['consent_id']}/answer", headers=device, json={"answer": "no"})
    assert r.status_code == 200
    t.join(30)
    r = box["got"]
    assert r.status_code == 200 and r.json["changed"] == []
    assert r.json["text"] == "You said No, so FreshTable's add_shopping did not run. Nothing was changed."
    assert "milk" not in _shopping(core)

    # 2. Her Yes, for exactly this call: it runs once, and the reply lists it as changed.
    _chat_brain(core, milk, {"text": "Milk is on the list."})
    t, box = _in_thread(lambda: client.post("/v1/chat", headers=owner, json={"text": "put milk on the shopping list"}))
    (card,) = _pending(core)
    r = answers.post(f"/v1/consent/{card['consent_id']}/answer", headers=device,
                     json={"answer": "yes", "card_sha": card["card_sha"]})
    assert r.status_code == 200
    t.join(60)
    r = box["got"]
    assert r.status_code == 200 and r.json["changed"] == [{"tool": "freshtable.add_shopping"}]
    assert "milk" in _shopping(core)

    # 3. Inside her approved scope (on the install card): no card, it runs, logged.
    _chat_brain(core, use("freshtable__add_pantry", name="rice"), {"text": "Rice is in the pantry."})
    r = client.post("/v1/chat", headers=owner, json={"text": "add a bag of rice to the pantry"})
    assert r.status_code == 200 and r.json["changed"] == [{"tool": "freshtable.add_pantry"}]
    # 4. A read: no card, nothing listed as changed.
    _chat_brain(core, call("freshtable__list_shopping"), {"text": "Milk."})
    r = client.post("/v1/chat", headers=owner, json={"text": "what is on the shopping list"})
    assert r.status_code == 200 and r.json["changed"] == []
    assert [c for c in core.consent.list() if c["scope"]["kind"] == "tool_call" and c["status"] == "pending"] == []
    audit = (state / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"in_scope"' in audit and '"needs_yes"' in audit


# -- item 3: a broken gate is a plain 503 ----------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["raises", "none", "prepare_raises"])
def test_a_broken_gate_is_503_no_gate_with_no_traceback(wired, bad):
    from puffpai_core.zone2.gate import NO_GATE, Gate
    core = wired
    brain = _chat_brain(core, {"text": "hi"})

    def boom(*_a):
        raise RuntimeError("gate down")
    brain._gate = {"raises": boom, "none": lambda: None,
                   "prepare_raises": lambda: Gate(prepare=boom)}[bad]
    r = core.app.test_client().post("/v1/chat", headers=_owner(core), json={"text": "hello"})
    assert r.status_code == 503 and r.json == {"error": NO_GATE, "state": "no_gate"}
    assert b"Traceback" not in r.data


# -- item 2: every card shows what its Yes grants ---------------------------------------------------------------
def _grant_everything(hello_copy):
    m = json.loads((hello_copy / "puffpai.json").read_text(encoding="utf-8"))
    m["needs"] = {"hosts": ["api.example.com"], "keys": [{"name": "HELLO_KEY", "why": "To say hello upstream."}]}
    m["setup_steps"] = [{"id": "plug", "kind": "confirm", "title": "Plug in the hello lamp"}]
    (hello_copy / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")
    (hello_copy / "big.bson").write_bytes(b"\x00\x01 a kind the checks can't read")


WANT = ["What it is: Hello Tool 1.0.0 (tool), from local:",
        "Hello Tool keeps short notes in its own data folder",
        "Reaches these places on the internet: api.example.com",
        "Keys it uses: HELLO_KEY",
        "Your turn (setup steps): Plug in the hello lamp",
        "Changes it makes without asking you: write_note",
        "Any other tool of it that changes something asks you each time.",
        "Drawbridge: Hello Tool: waiting for your Yes. Some of its files could not be checked (too big, a link, "
        "or a kind PuffPAI can't read).",
        "Drawbridge couldn't check some of its files. 1 file of a kind Drawbridge can't read: big.bson. "
        "Your Yes adds them anyway."]


def _has_all(lines):
    for want in WANT:
        assert any(ln.startswith(want) for ln in lines), (want, lines)


def test_the_cli_card_shows_every_grant_and_the_hold(wired, hello_copy, capsys, monkeypatch):
    from puffpai_core.cli import _show_card
    _grant_everything(hello_copy)
    staged = wired.packages.add(str(hello_copy))
    _show_card(staged["card"], staged["floor"])
    out = [ln.strip() for ln in capsys.readouterr().out.splitlines()]
    assert out[0] == "Add Hello Tool?"
    _has_all(out)
    assert "PuffPAI" in "\n".join(out)                                     # names keep their capitals
    # What she read is what the Yes covers: the same card, the same fingerprint.
    assert wired.consent.get(staged["consent_id"])["card_sha"] == card_sha(staged["card"])


def test_the_cli_add_command_prints_the_full_card(drawbridge, hello_copy, capsys, monkeypatch):
    from puffpai_core.cli import main
    _grant_everything(hello_copy)
    (hello_copy / "big.bson").unlink()
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main(["add", str(hello_copy)]) == 0
    out = capsys.readouterr().out
    for want in WANT[:7]:
        assert want in out, want
    assert "Not added." in out


def test_the_desktop_card_model_shows_every_grant_the_hold_and_the_pin(wired, hello_copy, tmp_path):
    if not NODE_OK:
        pytest.skip("node is not installed")
    core = wired
    _grant_everything(hello_copy)
    core.packages.add(str(hello_copy))
    floor_id = core.consent.request({"kind": "tool_call", "target": "hello-tool.reset_router"},
                                    {"title": "Let Hello Tool reset router?", "package": "hello-tool",
                                     "package_name": "Hello Tool", "tool": "reset_router",
                                     "arguments": ["(no details)"], "why": "This changes your network."}, True)
    served = core.app.test_client().get("/v1/consent?status=pending", headers=_owner(core)).json
    path = tmp_path / "cards.json"
    path.write_text(json.dumps(served), encoding="utf-8")
    proc = subprocess.run([shutil.which("node"), str(REPO / "tests" / "js" / "consent_view.js"), str(path)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    models = {m["id"]: m for m in json.loads(proc.stdout)}
    floor = models.pop(floor_id)
    (package,) = models.values()
    want_sha = next(c["card_sha"] for c in served if c["consent_id"] == package["id"])
    assert package["title"] == "Add Hello Tool?" and package["cardSha"] == want_sha
    _has_all(package["lines"])
    assert [b["id"] for b in package["buttons"]] == ["yes", "no"] and package["needsPin"] is False
    assert floor["needsPin"] is True
    assert floor["lines"][-1] == "This one needs your PIN. A tap is not enough."
    assert "Tool: reset_router" in floor["lines"] and floor["buttons"][0]["label"] == "Yes, with my PIN"


def test_a_card_with_no_details_offers_only_no():
    if not NODE_OK:
        pytest.skip("node is not installed")
    script = ("const L=require(process.argv[1]);process.stdout.write(JSON.stringify("
              "L.consentCard({consent_id:'c1',card_sha:'ab'})))")
    proc = subprocess.run([shutil.which("node"), "-e", script, str(REPO / "packages" / "desktop" / "web" / "logic.js")],
                          capture_output=True, text=True, timeout=60)
    got = json.loads(proc.stdout)
    assert [b["id"] for b in got["buttons"]] == ["no"]


# -- item 5: no brief "stopped" during an install ---------------------------------------------------------------
def test_an_install_goes_from_installing_to_running_with_no_stopped_between(core, drawbridge, hello_copy):
    seen = []
    real = core.registry.update

    def spy(id, **kw):
        if "status" in kw:
            seen.append(kw["status"])
        return real(id, **kw)
    core.registry.update = spy
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    assert seen and seen[0] == "running" and "stopped" not in seen, seen


# -- no test binds the real Core's ports (conftest guard) ------------------------------------------------------
def test_the_guard_refuses_the_fixed_core_ports(no_fixed_core_ports):
    import socket
    for port in (8420, 8421, 8422):
        with socket.socket() as s, pytest.raises(OSError):
            s.bind(("127.0.0.1", port))
    assert [a[1] for a in no_fixed_core_ports] == [8420, 8421, 8422]
    no_fixed_core_ports.clear()                       # seen and refused here on purpose; any other test fails
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))                      # port 0 (the OS picks) is how tests listen
        assert s.getsockname()[1] not in (8420, 8421, 8422)
