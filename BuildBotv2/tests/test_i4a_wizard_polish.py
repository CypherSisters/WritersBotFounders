"""Lane I4a: what the second live fresh-install proof (Windows, Python 3.11) found in the wizard and CLI.

1 Done lists every job in plain words; 2 the plan sentence once; 3 the brain list; 4 the PIN typed again;
5 the Bridge in the same words everywhere; 6 ``puffpai add`` on an installed package; 7 ``puffpai list``
says "ready" for packages with no process; 8 Ctrl+C and Ctrl+Break stop everything (real consoles);
9 what a Yes does on a build card. Wizard cases run against BOTH renderers (03 1.6).
"""

import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import FIXTURES, REPO
from wizard_kit import CODE, RENDERERS, BrowserRun, Kit, TerminalRun, vault_inputs
from puffpai_core.wizard import lines as L
from puffpai_core.wizard.terminal import chat_line

#: Raw state words that must never reach the owner's screen.
RAW = re.compile(r"\b(not_yet|nothing_found|waiting_you|waiting_vault|waiting_yes|not_downloaded|timed_out|"
                 r"needs_you)\b")


@pytest.fixture(params=RENDERERS, ids=lambda r: r.name)
def renderer(request):
    return request.param


def _owner_text(run) -> str:
    return "\n".join(run.visible()) if isinstance(run, BrowserRun) else run.all_text()


# -- 1. Done lists every job, in plain words ----------------------------------------------------------
RESULTS = [
    {"name": "Pantry", "state": "working", "reason": "FreshTable is already on your bot: it can list what is in "
                                                     "the pantry, and its health check passes."},
    {"name": "Shopping list", "state": "not_yet",
     "reason": "No app worked yet. Mataroa: Mataroa: You said No to trying it, so Toolsmith moves on to the next app.",
     "tried": [{"name": "Mataroa", "why": "Mataroa: You said No to trying it, so Toolsmith moves on to the next app."},
               {"name": "Servas", "why": "it did not reach a working page within 12 minutes"}]},
    {"name": "Dinner planner", "state": "nothing_found",
     "reason": "Nothing found yet: the Bridge is closed. Paste a GitHub address for this job."},
]


def _finished_build(kit):
    kit.toolsmith.results = lambda build: [dict(r) for r in RESULTS]
    kit.toolsmith.progress = lambda build: {"jobs": [], "steps": [], "cards": [], "finished": True}


def test_done_lists_every_job_in_plain_words(state, renderer):
    kit = Kit(state)
    _finished_build(kit)
    run = renderer(kit)
    run.engine.set_build("b1")
    run.step("scan", {})
    run.step("brain", {"model": ""})
    run.step("vault", vault_inputs())
    run.step("yes", {"yes": "yes"})
    run.step("build", {})
    prove = run.step("prove", {})
    done = run.step("done", {})
    assert done["lines"] == [L.DONE_SOME, "Pantry: works.",
                             "Shopping list: not working yet: none of the 2 apps Toolsmith tried works yet.",
                             "Dinner planner: nothing found: the Bridge is closed. Paste a GitHub address for this job.",
                             L.DONE_NEXT, L.STORE_MORE, L.MODELS_LATER, chat_line()]
    # Prove has the long reasons, each tried app named once; Done does not repeat them
    assert "Shopping list: not working yet. Toolsmith tried Mataroa, Servas:" in prove["lines"]
    assert "Mataroa: You said No to trying it, so Toolsmith moves on to the next app." in prove["lines"]
    assert "Mataroa: Mataroa" not in " ".join(prove["lines"])
    assert not set(done["lines"]) & set(prove["lines"])
    text = _owner_text(run)
    for line in done["lines"]:
        assert line in text                                  # every job is on the Done screen
    assert not RAW.search(text), RAW.search(text)


def test_the_build_end_says_no_raw_state(state):
    kit = Kit(state)
    _finished_build(kit)
    kit.toolsmith.progress = lambda build: {"jobs": [{"id": "j", "name": "Shopping list", "status": "not_yet",
                                                      "line": "No app worked yet.", "lines": [], "now": ""}],
                                            "steps": [], "cards": [], "finished": True}
    eng = kit.engine()
    eng.set_build("b1")
    screen = eng.submit("build", {})
    assert screen["status"] == "done" and screen["lines"][-1] == L.BUILD_ENDED
    assert not RAW.search(" ".join(screen["lines"]))


def test_a_building_line_ends_as_a_sentence():
    r = [{"name": "Shopping", "state": "building: RecipeSage", "reason": "RecipeSage"}]
    assert L.job_result_lines(r) == ["Shopping: Toolsmith is still working on it. RecipeSage."]
    assert L.prove_lines(r) == ["Shopping: Toolsmith is still working on it. RecipeSage."]


def test_every_state_has_plain_words():
    from puffpai_core.zone3.toolsmith_host.store import STATES
    for st in STATES:
        said = L.job_result_lines([{"name": "Job", "state": st, "reason": ""}])[0]
        assert not RAW.search(said) and ("_" not in st or st not in said), said
        assert st == "working" or "works" not in said, said


# -- 2. the plan sentence prints once --------------------------------------------------------------------
def test_the_plan_sentence_once_in_the_terminal(state):
    kit = Kit(state)
    run = TerminalRun(kit)
    run.engine.set_build("b1")
    run.step("plan", {})
    run.step("yes", {"yes": "no"})
    summary = kit.toolsmith.plan("b1")["summary"]
    assert run.all_text().count(summary) == 1
    assert "Build this plan?" in run.all_text()


def test_the_yes_page_shows_the_plan_sentence_once(state):
    kit = Kit(state)
    run = BrowserRun(kit)
    run.engine.set_build("b1")
    screen = run.post("yes", {})
    summary = kit.toolsmith.plan("b1")["summary"]
    assert screen["field"]["label"] == "Build this plan?"
    assert screen["lines"].count(summary) == 1


# -- 3. the brain list ----------------------------------------------------------------------------------
class HereModels:
    """The proof's menu: a 9B Recommended download, and a 7B already on this computer."""

    def __init__(self, rec_installed=False):
        self.pulled, self.rec_installed = [], rec_installed

    def fit(self, census):
        rec = {"id": "qwen3.5:9b", "label": "Qwen3.5 9B", "disk_gb": 6.6, "installed": self.rec_installed,
               "recommended": True,
               "reason": "Recommended: Qwen3.5 9B (" + ("Already downloaded" if self.rec_installed else
                                                         "7 GB download") +
                         "). It fits your 63.7 GB and is big enough (9B or more) to use tools well."}
        return {"options": [{"id": "qwen3:30b-a3b", "label": "Qwen3 30B", "disk_gb": 19, "installed": False}, rec,
                            {"id": "qwen2.5:7b-instruct", "label": "Qwen2.5 7B", "disk_gb": 4.7, "installed": True},
                            {"id": "qwen2.5:3b", "label": "Qwen2.5 3B", "disk_gb": 2, "installed": False}],
                "message": None}

    def state(self, name):
        return {"state": "ready"}

    def pull(self, name):
        self.pulled.append(name)


def test_the_brain_list_names_each_model_once(state, renderer):
    kit = Kit(state)
    kit.models = HereModels()
    run = renderer(kit)
    screen = run.engine.submit("brain", {}, renderer=run.name)
    notes = {o["label"]: o.get("note", "") for o in screen["field"]["options"]}
    assert "Qwen3.5 9B" not in notes["Qwen3.5 9B"]
    assert notes["Qwen3.5 9B"].startswith("Recommended. 7 GB download. It fits your 63.7 GB")


def test_a_model_already_here_is_the_default_and_needs_no_download(state, renderer):
    kit = Kit(state)
    kit.models = HereModels()
    run = renderer(kit)
    first = run.engine.submit("brain", {}, renderer=run.name)
    assert first["field"]["default"] == "qwen2.5:7b-instruct"
    assert ("Qwen2.5 7B is already downloaded, so it is the default and needs no download. Qwen3.5 9B is "
            "Recommended, a 6.6 GB download if you pick it.") in first["lines"]
    if run.name == "terminal":
        assert first["field"]["hint"] == "Type a number, or press Enter for Qwen2.5 7B."
    done = run.step("brain", {"model": ""})                 # Enter (terminal), Next as it is (browser)
    assert done["status"] == "done" and kit.models.pulled == []
    assert "It is already downloaded, so it starts at once." in done["lines"]


def test_no_model_here_keeps_the_recommended_default(state):
    kit = Kit(state)
    eng = kit.engine()
    screen = eng.submit("brain", {})
    assert screen["field"]["default"] == "qwen3.5:9b"
    assert not any("needs no download" in line for line in screen["lines"])


def test_the_recommended_model_already_here_is_the_default(state):
    kit = Kit(state)
    kit.models = HereModels(rec_installed=True)
    screen = kit.engine().submit("brain", {})
    assert screen["field"]["default"] == "qwen3.5:9b"
    assert "Qwen3.5 9B is already downloaded, so it is the default and needs no download." in screen["lines"]


# -- 4. the PIN typed again -------------------------------------------------------------------------------
def test_the_pin_is_typed_again_hidden(state, renderer):
    kit = Kit(state)
    run = renderer(kit)
    assert run.step("vault", vault_inputs())["status"] == "done"
    assert run.hidden_reads.index("pin") < run.hidden_reads.index("pin_again") < \
        run.hidden_reads.index("recovery_confirm")
    assert "482913" not in run.all_text()
    assert kit.vault.created is not None


def test_a_pin_that_does_not_match_asks_for_both_again(state, renderer):
    kit = Kit(state)
    run = renderer(kit)
    inputs = dict(vault_inputs(), pin=["482913", "582914"], pin_again=["482914", "582914"])
    assert run.step("vault", inputs)["status"] == "done"
    assert run.hidden_reads[:4] == ["pin", "pin_again", "pin", "pin_again"]
    assert L.PIN_MISMATCH in run.all_text()
    assert kit.vault.created is not None                 # sealed once, with the PIN typed the same twice
    for pin in ("482913", "482914", "582914"):
        assert pin not in run.all_text()


def test_the_vault_is_not_made_before_the_pin_matches(state):
    kit = Kit(state)
    eng = kit.engine()
    screen = eng.submit("vault", {"where": "here", "pin": "482913"})
    assert screen["field"]["id"] == "pin_again" and screen["field"]["hidden"] is True
    assert kit.vault.created is None
    screen = eng.submit("vault", {"pin_again": "000000"})
    assert screen["field"]["id"] == "pin" and screen["error"] == L.PIN_MISMATCH and kit.vault.created is None
    assert "482913" not in (state / "settings" / "wizard.json").read_text(encoding="utf-8")


def test_a_weak_pin_is_refused_before_it_is_typed_again(state):
    screen = Kit(state).engine().submit("vault", {"where": "here", "pin": "12"})
    assert screen["field"]["id"] == "pin" and "6 digits" in screen["error"]


def test_the_core_checks_the_pin_with_the_vaults_own_rule(state):
    from puffpai_core.wiring import WizardVault
    assert "at least" in WizardVault.check_pin("1234")
    assert WizardVault.check_pin("Tq7#mz2Lp!") == ""


# -- 5. the Bridge in the same words everywhere ---------------------------------------------------------
from puffpai_core.wiring import WizardBridge  # noqa: E402
from puffpai_core.zone3.bridge import CLOSED_LINE, SEARCH_ONLY_LINE, SEARCH_PLACES, SOME_PLACES  # noqa: E402


def test_search_only_reads_as_closed():
    from puffpai_core.zone3.bridge import plain
    assert plain({"state": "closed"}) == {"shown": "closed", "lines": [CLOSED_LINE]}
    search = {"state": "open", "switches": {"toolsmith:search": True}, "grants": []}
    # an older Core's posture names no host: it can't be told which places, so it never claims any
    assert plain(search) == {"shown": "closed", "lines": [SEARCH_ONLY_LINE.replace(SEARCH_PLACES, SOME_PLACES)]}
    during = dict(search, grants=[{"subject": "toolsmith:search", "host": h, "phase": "build"}
                                  for h in WizardBridge.SEARCH_HOSTS])
    # its own grants while a build runs: then it really searches, so it reads open (lane I6b)
    from puffpai_core.zone3.bridge import SEARCH_BUILDING_LINE
    assert plain(during) == {"shown": "open", "lines": [SEARCH_BUILDING_LINE]}
    one = dict(search, grants=[{"subject": "toolsmith:search", "host": "api.github.com", "phase": "build"}])
    assert plain(one) == {"shown": "open", "lines": [SEARCH_BUILDING_LINE.replace(SEARCH_PLACES, "GitHub")]}
    for line in (SEARCH_ONLY_LINE, CLOSED_LINE, SEARCH_BUILDING_LINE):
        assert "door" not in line.lower() and "\u2014" not in line


def test_what_is_let_through_is_said_by_name():
    from puffpai_core.zone3.bridge import plain
    more = {"setting": "open", "search": True,
            "live": [{"subject": "toolsmith:search", "phase": "build"},
                     {"subject": "pkg:news", "host": "x.example", "phase": "run"},
                     {"subject": "toolsmith:cloud", "phase": "build"},
                     {"subject": "pkg:toolsmith-build-abc123", "host": "registry.npmjs.org", "phase": "install"}]}
    said = plain(more, {"news": "World News"})
    assert said == {"shown": "open", "lines": [
        "The Bridge is open.", f"While Toolsmith builds, it may search {SOME_PLACES}.",
        "While Toolsmith builds, it may use the cloud model you picked.",
        "Let through now: World News (while it runs), the apps Toolsmith is trying (while it installs)."]}
    assert "pkg:" not in " ".join(said["lines"]) and "toolsmith:" not in " ".join(said["lines"])


def _real_core(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    app = create_app(state)
    return app, wire_core(app)


def test_cli_desktop_and_wizard_agree_after_the_search_choice(state, monkeypatch, capsys):
    from puffpai_core import cli
    app, core = _real_core(state)
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    c = app.test_client()
    core.bridge.set_posture("search")                        # the wizard's step 4 answer
    view = c.get("/v1/bridge", headers=owner).json
    settings = c.get("/v1/settings", headers=owner).json
    assert view["said"] == settings["bridge_said"] == core.bridge.said() == [SEARCH_ONLY_LINE]
    assert view["shown"] == "closed"
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "t"})
    monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None: (200, view))
    assert cli._cmd_bridge(type("A", (), {"action": "status"})()) == 0
    assert capsys.readouterr().out.strip() == SEARCH_ONLY_LINE
    core.bridge.set_posture("closed")
    assert c.get("/v1/settings", headers=owner).json["bridge_said"] == [CLOSED_LINE]
    core.zone3.stop()


def test_the_wizard_says_the_real_posture_not_what_she_asked(state):
    """A cloud Yes turned toolsmith:cloud on before her search choice: the Bridge is open, and the wizard
    says so in the same words as plain(), never "closed... nothing else goes out" (verifier probe)."""
    from puffpai_core.wizard import Engine
    from puffpai_core.wizard.services import default_services
    app, core = _real_core(state)
    store = core.zone3.consents                     # a real cloud Yes (lane I6b: a bare switch lets nothing out)
    card = store.ask("cloud", "toolsmith:cloud", {"hosts": ["api.anthropic.com"], "phase": "build"})
    store.answer(card["consent_id"], "yes", "tap", by="owner", card_sha=card["card_sha"])
    assert core.zone3.bridge.posture()["switches"].get("toolsmith:cloud") is True
    eng = Engine(core.state, default_services(core), registry=core.registry, bus=core.bus, census_fn=lambda: {})
    screen = eng.submit("bridge", {"posture": "search"})
    assert screen["status"] == "done"
    assert screen["lines"] == core.zone3.bridge.view()["said"]
    assert screen["lines"][0] == "The Bridge is open." and SEARCH_ONLY_LINE not in screen["lines"]
    core.zone3.stop()


def test_the_wizard_bridge_step_says_the_same(state, renderer):
    kit = Kit(state)
    run = renderer(kit)
    assert SEARCH_ONLY_LINE in run.step("bridge", {"posture": "search"})["lines"]


# -- 6. puffpai add on an installed package -----------------------------------------------------------
def test_add_on_an_installed_package_gives_the_exact_commands(capsys, monkeypatch, drawbridge, state):
    from puffpai_core.cli import main
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main(["add", "example-page"]) == 0
    capsys.readouterr()
    assert main(["add", "example-page"]) == 1
    out = capsys.readouterr().out
    assert ("My notes (an example page you can copy) (example-page) is already installed. To add it again, run: puffpai remove example-page "
            "--keep-data, then: puffpai add example-page") in out
    assert "Update it instead" not in out


def test_the_line_names_a_folder_source_as_typed():
    from puffpai_core.packages.manager import already_installed
    said = already_installed("Hello", "hello-tool", r"C:\My Apps\hello")
    assert said.endswith('then: puffpai add "C:\\My Apps\\hello"')


# -- 7. puffpai list: ready for packages with no process ---------------------------------------------
def test_list_says_ready_for_pages_and_avatars(capsys, monkeypatch, drawbridge, state):
    from puffpai_core.cli import main
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    for pid in ("example-page", "cloud-avatar"):
        assert main(["add", pid]) == 0
    assert main(["add", str(FIXTURES / "hello-tool")]) == 0
    capsys.readouterr()
    assert main(["list"]) == 0
    rows = {line.split()[0]: line.split()[2] for line in capsys.readouterr().out.splitlines()[1:]}
    assert rows == {"cloud-avatar": "ready", "example-page": "ready", "hello-tool": "stopped"}


def test_list_status_words_are_plain():
    from puffpai_core.cli import status_word
    assert status_word({"id": "x", "status": "needs_you"}) == "needs you"
    assert status_word({"id": "x", "status": "running"}) == "running"


# -- 8. Ctrl+C and Ctrl+Break stop everything, in a real console ---------------------------------------
def _free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


def _bot_with_a_running_package(state, monkeypatch):
    """hello-tool installed (a real process in its own job object) and marked to start with the Core."""
    from puffpai_core.cli import main
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main(["add", str(FIXTURES / "hello-tool")]) == 0
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "steward.json").write_text(json.dumps({"was_running": ["hello-tool"]}), encoding="utf-8")


def _child_env(state):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST")}
    env.update(PUFFPAI_STATE=str(state), PYTHONUNBUFFERED="1", PIP_REQUIRE_VIRTUALENV="1",
               PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring",
               PYTHONPATH=os.pathsep.join([str(REPO / "core"), str(REPO / "tests")]))
    return env


windows_only = pytest.mark.skipif(os.name != "nt", reason="Ctrl+C and Ctrl+Break in a Windows console")


@windows_only
@pytest.mark.parametrize("mode", ["c", "break"])
@pytest.mark.parametrize("open_plan", [False, True], ids=["idle", "plan-open"])
def test_serve_stops_everything_on_ctrl_c_and_ctrl_break(state, monkeypatch, tmp_path, drawbridge, mode, open_plan):
    """Everything the Core started is in the kit's job; "left" is what is still in it after the stop.
    plan-open: a POST /v1/toolsmith/plan is waiting on Toolsmith's own process (its brain never answers)."""
    import console_kit
    _bot_with_a_running_package(state, monkeypatch)
    core_port, pages_port, proxy_port, hang_port = _free_ports(4)
    during = None
    if open_plan:
        # A picked brain whose address answers nothing: Toolsmith's plan asks it whether hello-tool already
        # does the job, and waits. The Bridge stays closed, so nothing reaches the internet.
        from puffpai_core.zone2 import settings as model_settings
        model_settings.save(state, model="qwen2.5:7b-instruct", address=f"http://127.0.0.1:{hang_port}")
        during = console_kit.hold_plan_argv(hang_port, state)
    argv = [sys.executable, str(REPO / "tests" / "console_core.py"), "serve", "--port", str(core_port),
            "--pages-port", str(pages_port), "--proxy-port", str(proxy_port)]
    got = console_kit.run(argv, mode, _child_env(state), "(Ctrl+C stops it)", tmp_path / "console",
                          settle_s=6.0, during=during, min_pids=7 if open_plan else 3)
    out = got["output"]
    assert got["sent"] and got["exit"] is not None, out[-2000:]
    assert got["stop_s"] < 8, got
    assert len(got["pids"]) >= (7 if open_plan else 3), got      # the Core, hello-tool (and Toolsmith's process)
    assert got["left"] == [], got                                # nothing of ours is still in the job
    assert out.rstrip().endswith("BuildBot stopped."), out[-2000:]
    assert "Traceback" not in out and "still running" not in out, out[-3000:]
    assert not (state / "settings" / "terminal.json").exists()
    assert json.loads((state / "settings" / "steward.json").read_text(encoding="utf-8"))["was_running"] ==         ["hello-tool"]                                       # it stopped cleanly, so it starts again next time


@windows_only
def test_setup_keeps_running_then_stops_on_ctrl_c(state, monkeypatch, tmp_path, drawbridge):
    import console_kit
    _bot_with_a_running_package(state, monkeypatch)
    steps = ["scan", "brain", "vault", "bridge", "toolsmith", "goal", "name", "look", "plan", "yes", "build", "prove",
             "done"]
    (state / "settings" / "wizard.json").write_text(
        json.dumps({"steps": {s: {"state": "done"} for s in steps}, "complete": True}), encoding="utf-8")
    core_port, pages_port, proxy_port = _free_ports(3)
    argv = [sys.executable, str(REPO / "tests" / "console_core.py"), "setup", "--port", str(core_port),
            "--pages-port", str(pages_port), "--proxy-port", str(proxy_port)]
    got = console_kit.run(argv, "c", _child_env(state), "To stop BuildBot", tmp_path / "console",
                          stdin="n\n" * 3 + "t\n/quit\n", settle_s=6.0)    # D94: No to 3 parts, then the chat
    out = got["output"]
    print("REPORT", json.dumps({k: v for k, v in got.items() if k != "output"}), out[-800:])
    print("REPORT", json.dumps({k: v for k, v in got.items() if k != "output"}))
    assert got["exit"] is not None and got["stop_s"] < 8, got
    assert got["left"] == [] and len(got["pids"]) >= 3, got
    assert "BuildBot is running now, in this window." in out
    assert out.index("Setup is done.") < out.index("Bye. BuildBot keeps running.") < out.index("BuildBot is running now")
    assert "To stop BuildBot, press Ctrl+C here, or close this window." in out
    assert "To start it again later" in out
    assert out.rstrip().endswith("BuildBot stopped."), out[-2000:]
    assert "Traceback" not in out, out[-3000:]
    assert not (state / "settings" / "terminal.json").exists()


def test_the_running_screen_says_how_to_stop_and_start_again(tmp_path, monkeypatch):
    from puffpai_core.wizard import terminal
    start = tmp_path / terminal.launcher_name("Start BuildBot.cmd")
    monkeypatch.setattr(terminal, "launcher", lambda: start)
    lines = terminal.running_lines(18430, "AAAA-BBBB-CCCC")
    # no launcher (run from source): the one command that runs here, since bare "puffpai" is on no PATH (D92(e))
    assert lines[-1] == "To start it again later, run this in a terminal: " + terminal.cli_command("serve")
    assert "run: puffpai" not in " ".join(lines)
    start.write_text("@echo off\n", encoding="ascii")
    lines = terminal.running_lines(18430, "AAAA-BBBB-CCCC")
    assert "BuildBot is running now, in this window." in lines
    assert "http://puffpai.localhost:18430/#pair=AAAA-BBBB-CCCC" in " ".join(lines)
    assert lines[-1] == f"To start it again later, {terminal.how_to_run(start)}."
    if os.name == "nt":
        assert lines[-1] == f"To start it again later, double-click Start BuildBot.cmd in {tmp_path}."


def test_an_open_event_stream_ends_when_the_core_stops(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import end_streams
    app = create_app(state)
    core = app.extensions["puffpai"]
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    c = app.test_client()
    ended = threading.Event()

    def read():
        r = c.get("/v1/events?timeout=30", headers=owner, buffered=True)
        assert r.status_code == 200
        ended.set()
    t = threading.Thread(target=read, daemon=True)
    t.start()
    time.sleep(0.5)
    t0 = time.monotonic()
    end_streams(app)
    assert ended.wait(5) and time.monotonic() - t0 < 5
    ended.clear()                                          # a stream opened later still works (and times out)
    r = c.get("/v1/events?timeout=0", headers=owner, buffered=True)
    assert r.status_code == 200


def test_toolsmith_stops_at_once_at_core_stop():
    from puffpai_core.zone3.toolsmith_host.service import ToolsmithHost

    class Child:
        def __init__(self):
            self.done, self.stopped = threading.Event(), False

        def wait(self, timeout=None):
            return self.done.wait(timeout)

        def stop(self):
            self.stopped = True
            self.done.set()

    child = Child()
    host = ToolsmithHost.__new__(ToolsmithHost)            # only what stop() reads
    host._lock, host._children = threading.Lock(), {"b": child}
    host._senders, host._gh_leases = {}, {}                 # D68: stop() also ends any keys a Yes lent
    host.store = type("S", (), {"all": lambda self: []})()
    t0 = time.monotonic()
    host.stop(grace_s=0)
    assert child.stopped and time.monotonic() - t0 < 1


# -- 9. what a Yes does on a build card ---------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["hold", "install"])
def test_a_card_says_what_its_yes_does_now(state, kind):
    kit = Kit(state)
    card = {"consent_id": "c1", "title": "Let Toolsmith try RecipeSage?", "lines": ["What it is: RecipeSage"],
            "floor": False, "kind": kind}
    kit.toolsmith.progress = lambda build: {"jobs": [], "steps": [], "cards": [card], "finished": False}
    eng = kit.engine()
    eng.set_build("b1")
    screen = eng.submit("build", {})
    assert screen["field"]["id"] == "card"
    if kind == "hold":
        assert screen["field"]["help"] == "Your Yes lets Toolsmith try it; nothing from it runs before that."
    else:
        assert screen["field"]["help"] == "Toolsmith got this app working. Your Yes adds it to your bot."


from test_i3b_bridge_cards import _run_build, held_core  # noqa: E402,F401 - lane I3b's real held try


def test_the_real_hold_card_is_marked_hold(held_core):
    """The Core's own build cards (``WizardToolsmith._cards``): a try's hold card says it is one."""
    from puffpai_core.wiring import WizardToolsmith
    app, core, host, ports = held_core
    seen = []

    def on_hold(rec):
        b = host.store.all()[-1]
        seen.extend(WizardToolsmith(app)._cards(host, b["id"]))
        return "no"
    _run_build(host, core, on_hold)
    assert seen and seen[0]["kind"] == "hold", seen


def test_the_registry_route_says_which_packages_run_a_process(monkeypatch, drawbridge, state):
    """The Desktop's Installed list reads ``process`` from GET /v1/registry (the Core's one manifest reader)."""
    from puffpai_core.api import create_app
    from puffpai_core.cli import main
    from puffpai_core import desktop
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    for pid in ("example-page", "cloud-avatar", str(FIXTURES / "hello-tool")):
        assert main(["add", pid]) == 0
    app = create_app(state)
    core = app.extensions["puffpai"]
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    rows = {r["id"]: r for r in app.test_client().get("/v1/registry", headers=owner).json}
    assert {k: r["process"] for k, r in rows.items()} == {"cloud-avatar": False, "example-page": False,
                                                          "hello-tool": True}
    assert "process" not in core.registry.get("example-page")        # never stored
    # an installed page whose settings file no longer matches its Yes: never the built-in's file, so not "ready"
    folder = core.installs.folder("example-page")
    path = folder / "puffpai.json"
    path.write_text(path.read_text(encoding="utf-8").replace("My notes", "My notes!"), encoding="utf-8")
    assert desktop.has_process(state, "example-page", core.registry) is True
    assert desktop.has_process(state, "no-such-package", core.registry) is True   # can't tell: not "ready"


@pytest.mark.parametrize("project", ["", "https://github.com/koala73/worldmonitor"])
def test_the_plan_page_shows_the_summary_once(state, project):
    """The Desktop adds each screen of a step under the last one: the plan summary shows once, also when she
    pastes a project (verifier: twice, three times with a project)."""
    kit = Kit(state)
    run = BrowserRun(kit)
    run.engine.set_build("b1")
    run.step("plan", {"project": project})
    summary = kit.toolsmith.plan("b1")["summary"]
    assert sum(" ".join(sc["lines"]).count(summary) for sc in run.screens) == 1
    assert " ".join(run.visible()).count(summary) == 1
    if project:
        assert any("Toolsmith tries github.com/koala73/worldmonitor only." in sc["lines"] for sc in run.screens)


# -- the page reader's browser lives in a job of its own (coordinator decision, lane I4a) -----------------
def test_after_a_read_nothing_from_the_browsers_job_remains(tmp_path, monkeypatch):
    import http.server
    from puffpai_core.steward import jobs
    from puffpai_core.zone2 import page_reader
    if page_reader.browser_path() is None:
        pytest.skip("no Chromium-family browser on this computer")

    class Page(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"<html><title>Hello</title><body><p>Today's list: milk and eggs</p></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    seen = []
    real_close = jobs.Job.close

    def close(self):
        seen.append({"contained": self.contained, "pids": self.pids(), "active": self.active()})
        real_close(self)
    monkeypatch.setattr(jobs.Job, "close", close)
    try:
        got = page_reader.read(f"http://127.0.0.1:{srv.server_address[1]}/", wait_s=3)
    finally:
        srv.shutdown()
    assert got is not None and "milk and eggs" in got["text"]
    assert seen and seen[-1]["contained"] is True
    assert seen[-1]["pids"] == [] and seen[-1]["active"] == 0, seen   # the browser and every helper ended
    assert not hasattr(page_reader, "stop_profile_browsers")


# -- the Desktop's tick boxes against lane I4b's real routes (after the merge of aaf1921c) ---------------
from test_i4b_build_flow import offer_world  # noqa: E402,F401 - lane I4b's planned build with an offer


def _desktop(app, core):
    """A test client signed in like the Desktop: the paired-device cookie and its own headers (D49)."""
    from desktop_kit import DESKTOP, desktop_client
    c = desktop_client(app)                          # D79: the Desktop's own address
    c.sign_in(core.tokens.issue_device("test browser"))
    return c, DESKTOP


def _desktop_ticks(record, ticked):
    import shutil
    import subprocess as sp
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed, so the Desktop's own logic can't be run")
    got = sp.run([node, str(REPO / "tests" / "js" / "card_ticks.js")], input=json.dumps(
        {"record": record, "ticked": ticked}), capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout)


@pytest.mark.parametrize("tick", [False, True], ids=["nothing-ticked", "one-ticked"])
def test_the_desktop_hold_ticks_reach_the_real_route(held_core, state, tick):
    from test_i4b_build_flow import _held_build, _held_try
    app, core, host, ports = held_core
    b, link = _held_build(held_core)                            # both apps held
    jid = b["jobs"][0]["id"]
    for ref in ("github:example/news-board", "github:example/hello-page"):
        assert _held_try(link, jid, ref).get("held")
    card = link.holds_ask(jid)
    c, owner = _desktop(app, core)
    rec = next(r for r in c.get("/v1/consent?status=pending", headers=owner).json
               if r["consent_id"] == card["consent_id"])        # what the Desktop reads
    t = _desktop_ticks(rec, ["github:example/news-board"] if tick else [])
    assert t["kind"] == "holds" and sorted(t["values"]) == ["github:example/hello-page", "github:example/news-board"]
    r = c.put(t["route"], json=t["body"], headers=owner)        # sent before her Yes, as desktop.js does
    assert r.status_code == 200, r.json
    r = c.post(f"/v1/consent/{card['consent_id']}/answer", headers=owner,
               json={"answer": "yes", "factor": "tap", "card_sha": rec["card_sha"]})
    assert r.status_code == 200, r.json
    assert link.holds_state(jid)["try"] == (["github:example/news-board"] if tick else [])   # nothing ticked: none


def test_the_desktop_offer_ticks_reach_the_real_route(offer_world):
    from test_i4b_build_flow import _offer_plan
    w = offer_world
    b = _offer_plan(w)
    cid = b["consent"]["id"]
    # The plan card as the Core's consent store keeps it (consent/store.py, the L4 form): covers = the plan
    record = {"consent_id": cid, "card_sha": "s", "title": "Build this plan?", "lines": b["card"]["lines"],
              "card": {"scope": {"kind": "package", "target": "x"},
                       "covers": {"build": b["id"], "plan_sha": b["plan_sha"], "card": b["card"]}}}
    t = _desktop_ticks(record, [b["card"]["offers"][0]["job"]])
    assert t["kind"] == "offers"
    c, owner = _desktop(w.app, w.core)
    r = c.put(t["route"], json=t["body"], headers=owner)
    assert r.status_code == 200, r.json
    assert list(r.json["use"].values()) == ["freshtable"]
