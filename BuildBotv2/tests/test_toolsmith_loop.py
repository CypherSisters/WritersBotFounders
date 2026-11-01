"""The try loop with a scripted model and fake ports (no model, no network).

Toolsmith's side (``tries``) talks to the Core through the real Toolsmith-token
routes (``World.link``); the Core runs the commands. The one real process
here is the tiny fixture app (``python -m http.server`` serving
tests/fixtures/toolsmith/hello-page), started by the Core broker in a job
object and stopped by it.
"""

import os
import sys
from pathlib import Path

import pytest

from toolsmith_fakes import FIXTURES, Script, World, last_result, make_venv, tool
from puffpai_core.zone3.toolsmith import commands, tries
from puffpai_core.zone3.toolsmith.errors import CloudHeld, CloudOffline, JobHeld
from puffpai_core.zone3.toolsmith_host import trials as trials_mod
from puffpai_core.zone3.toolsmith_host.sender import OFFLINE_LINE

HELLO = FIXTURES / "toolsmith" / "hello-page"
PY = Path(sys.executable).as_posix()


class Hooks(tries.Hooks):
    def __init__(self):
        self.lines, self.states = [], []

    def say(self, line):
        self.lines.append(line)

    def status(self, state, line=""):
        self.states.append((state, line))

    def wait_step(self, step_id):
        return self.answer

    answer = "done"


def cand(**kw):
    return {"name": "Hello Page", "full_name": "example/hello-page", "stars": 50, "description": "A news board",
            "readme_steps": ["python -m http.server 8000"], "runtime": "python", "tries_in": "native",
            "programs": ["python"], "source": "github", **kw}


@pytest.fixture
def world(tmp_path, state):
    w = World(tmp_path, state)
    w.ports.fixture_repo = HELLO
    yield w
    w.host.stop(timeout=10)


@pytest.fixture
def ports(world):
    return world.ports


def make_try(world, clock=None, **budgets):
    b = world.build_with_yes([cand()])
    link = world.link(b["id"])
    s = link.build()["settings"]
    s["budgets"].update(budgets)
    hooks = Hooks()
    job = b["jobs"][0]
    extra = {"clock": clock} if clock else {}
    t = tries.Try(job, job["plan"]["candidates"][0], link=link, settings=s, hooks=hooks, **extra)
    return t, hooks


VENV = {"program": "python", "args": ["-m", "venv", "--without-pip", ".venv"]}


def serve(t):
    """The fixture app's start, as one program and its arguments (no shell, D60): the try's own Python."""
    return {"program": "python", "args": ["-m", "http.server", str(t.port), "--bind", "127.0.0.1"]}


def checked_with_venv(t):
    """Downloaded, checked, and the try's own Python made by the Core (straight through the link, so the
    try's own record of what ran stays empty)."""
    t.call("download", {})
    make_venv(t.link, t.try_id)


def test_adopts_the_fixture_app_end_to_end(world, ports, state):
    t, hooks = make_try(world)
    script = Script([tool("download"),
                     lambda m: tool("run", **VENV),
                     lambda m: tool("start_server", **serve(t)),
                     lambda m: tool("wait_for_page", url=f"http://127.0.0.1:{t.port}/"),
                     lambda m: tool("done", url=f"http://127.0.0.1:{t.port}/")])
    ports.local_replies = script
    try:
        tries.drive(t, tries.Models(t.link))
        assert t.result and t.result["title"] == "Hello Page", (t.ended, hooks.lines)
        assert t.folder == state / "packages" / "hello-page" and (t.folder / "index.html").is_file()
        # The README went to the model with the AI-aimed line hidden (Drawbridge, in the Core).
        first = script.seen[1][-1]["content"][0]["content"]
        assert any(r["command"] == "python -m venv .venv" and r["ok"] for r in t.ran)   # kept as {"venv": true}
        assert "Quick start" in first and "ignore previous instructions" not in first.lower()
        # The Core checked the zone on the start line before it ran it.
        assert any(k == "command" and "http.server" in v for k, v in ports.zone_calls)
        rec = world.host.trials.get(t.link.build_id, t.try_id)
        started = list(rec.broker.started)
        assert started and started[0].job.alive()
    finally:
        t.close()
    assert all(not s.job.alive() for s in started)                # stopped by its job object
    assert not (state / "packages" / "hello-page").exists()      # its folder went with it


def test_unknown_tool_is_refused(world):
    t, _ = make_try(world)
    assert "no tool named" in t.call("format_disk", {})["said"]


def test_drawbridge_block_ends_the_try_and_removes_the_folder(world, ports, state):
    ports.drawbridge = "block"
    t, _ = make_try(world)
    t.call("download", {})
    assert t.ended.startswith("Drawbridge did not pass it") and t.folder is None
    assert not any((state / "packages" / ".staging").iterdir())


def test_nothing_runs_before_drawbridge_checked_it(world, ports, state):
    t, _ = make_try(world)
    t.link.try_download(t.try_id)                                 # downloaded, not checked yet
    got = t.link.try_run(t.try_id, "echo hi")
    assert not got["ok"] and "Drawbridge checks the download" in got["said"]
    assert not any(k == "command" for k, _v in ports.zone_calls)


def test_zone_refusal_runs_nothing(world, ports):
    t, _ = make_try(world)
    checked_with_venv(t)
    t.link.try_write(t.try_id, "show.py", "import sys\nprint(open(sys.argv[1]).read())\n")
    ports.zone_refuse = ["registry.db"]
    got = t.call("run", {"program": "python", "args": ["show.py", "../../registry.db"]})
    assert not got["ok"] and "outside what Toolsmith may touch" in got["said"]
    assert t.ran == []


def test_zone_ask_pauses_for_her_yes_then_runs_exactly_that(world, ports):
    t, hooks = make_try(world)
    checked_with_venv(t)
    t.link.try_write(t.try_id, "p42.py", "print(42)\n")
    ports.zone_ask = ["../shared-config"]
    got = t.call("run", {"program": "python", "args": ["p42.py", "../shared-config"]})
    step = ports.checklist.steps[-1]
    assert step["kind"] == "confirm" and step["text"] == "python p42.py ../shared-config" and "outside" in step["title"]
    assert ("waiting_you", step["title"]) in hooks.states and hooks.states[-1][0] == "building"
    assert got["ok"] and "42" in got["said"]


def test_zone_ask_skipped_ends_the_try(world, ports):
    t, hooks = make_try(world)
    checked_with_venv(t)
    t.link.try_write(t.try_id, "p.py", "print(1)\n")
    ports.zone_ask = ["../shared"]
    hooks.answer = "skipped"
    ports.checklist.answer = "skipped"
    t.call("run", {"program": "python", "args": ["p.py", "../shared"]})
    assert t.ended == "Skipped by you" and t.ran == []


def test_missing_or_stopped_zone3_refuses_everything(tmp_path, state):
    """No L4 code: "not built"; L4 here but its Zone 3 not started for this Core: "not running"."""
    import importlib.util
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    p = Ports(state, zone=lambda: None)                       # this Core's Zone 3 is not up
    if importlib.util.find_spec("puffpai_core.zone3.toolsmith_zone") is None:
        got = p.zone_check("ls", kind="command", folder=tmp_path)
        assert got.verdict == "deny" and "not built yet" in got.line
    else:                                                     # L4 merged: its own check answers (no Zone 3 needed)
        assert p.zone_check(str(state / "registry.db"), kind="path", folder=tmp_path).verdict == "deny"
    verdict = p.drawbridge_check(tmp_path, None)
    assert verdict["verdict"] == "block" and ("not built yet" in verdict["line"] or "not running" in verdict["line"])
    assert p.bridge_allowed("toolsmith:search", "api.github.com") is False
    assert p.verify_tree(tmp_path, {"verdict": "allow", "tree_sha": "x"}) is False


def test_repeat_guard_holds_the_same_failed_command_then_ends(world):
    t, _ = make_try(world, repeats_to_end=3)
    checked_with_venv(t)
    t.link.try_write(t.try_id, "exit3.py", "import sys\nsys.exit(3)\n")
    bad = {"program": "python", "args": ["exit3.py"]}
    assert not t.call("run", bad)["ok"]
    assert tries.REPEAT_LINE in t.call("run", bad)["said"]
    assert tries.REPEAT_LINE in t.call("run", {"command": "python exit3.py"})["said"]    # the same, as one line
    t.call("write_file", {"path": ".env", "text": "A=1"})          # a change: it may run again
    assert "exit code 3" in t.call("run", bad)["said"]
    t.call("run", bad)
    t.call("run", bad)
    assert t.ended == "it kept repeating the same failing command"


def test_reads_are_free_capped_and_unchanged_rereads_are_told(world):
    t, _ = make_try(world, reads_per_try=2)
    t.call("download", {})
    steps = t.steps
    got = t.call("read_file", {"path": "README.md"})
    assert got["ok"] and "Quick start" in got["said"]
    assert "already read README.md" in t.call("read_file", {"path": "README.md"})["said"]
    t.call("read_file", {"path": "index.html"})
    assert "no more reading" in t.call("list_files", {"path": ""})["said"]
    assert t.steps == steps


def test_step_budget_ends_the_try(world, ports, state):
    t, _ = make_try(world, adopt_steps=2)
    ports.local_replies = Script([tool("download"), tool("server_log"), tool("server_log")])
    tries.drive(t, tries.Models(t.link))
    assert t.ended == "it did not reach a working page within 2 steps"
    assert not (state / "packages" / "hello-page").exists()


def test_give_up_before_trying_is_sent_back_once(world):
    t, _ = make_try(world)
    t.call("download", {})
    assert t.call("give_up", {"reason": "not a fit"})["said"] == tries.START_FIRST
    t.call("give_up", {"reason": "it does not fit"})
    assert t.ended == "it does not fit"


def test_build_in_background_is_refused_and_pipe_cut():
    assert commands.build_line("npm install | tail -5") == ("npm install", "")
    assert commands.build_line("npm install &")[1] == commands.BUILD_FOREGROUND
    assert commands.never_ends("npm run dev") and not commands.never_ends("npm install")


def test_missing_program_pauses_for_your_turn_and_goes_on(world, ports):
    t, hooks = make_try(world)
    got = t.call("install_program", {"name": "ruby", "why": "It is a Ruby app."})
    assert got["ok"] and "Go on" in got["said"] and not t.finished
    step = ports.checklist.steps[0]
    assert step["kind"] == "run_command" and ("RubyInstallerTeam.Ruby" if os.name == "nt" else "ruby") in step["text"]
    assert ("waiting_you", "Install Ruby") in hooks.states and hooks.states[-1][0] == "building"


def test_your_turn_skip_ends_the_try(world):
    t, hooks = make_try(world)
    hooks.answer = "skipped"
    t.call("ask_owner", {"title": "Sign up for the news service", "kind": "open_url",
                         "get_it": "https://example.org/signup"})
    assert t.ended == "Skipped by you"


def test_paused_time_does_not_count(world):
    clock = [0.0]
    t, hooks = make_try(world, clock=lambda: clock[0])

    def slow(_step_id):
        clock[0] += 3600
        return "done"
    hooks.wait_step = slow
    t.call("ask_owner", {"title": "Install Docker Desktop", "kind": "confirm"})
    assert t.minutes_used() < 1 and t.over_budget() == ""


def test_done_needs_the_main_page(world, ports):
    t, _ = make_try(world)
    t.call("download", {})
    # The port is checked first (no GET to a port not its own), so these pages are on one its own server holds.
    world.host.trials.listens = lambda rec: {"port": t.port, "own": [["127.0.0.1", t.port]], "app_port": None, "exposed": [],
                                             "taken": [], "said": ""}
    ports.read_page_fn = lambda url: {"ok": True, "title": "", "text": '{"status": "healthy", "service": "news", "uptime": 12}'}
    assert "not its main page" in t.call("done", {"url": f"http://127.0.0.1:{t.port}/"})["said"]
    ports.read_page_fn = lambda url: {"ok": True, "title": "x", "text": "Hi"}
    assert "no real content" in t.call("done", {"url": f"http://127.0.0.1:{t.port}/"})["said"]
    assert "127.0.0.1" in t.call("done", {"url": "http://example.com/"})["said"]
    assert t.result is None


def test_compose_needs_the_docker_wall(world, ports):
    t, _ = make_try(world)
    t.call("download", {})
    (t.folder / "compose.yaml").write_text("services: {}\n")
    rec = world.host.trials.get(t.link.build_id, t.try_id)
    if rec.broker.docker:                                  # the Docker CLI is on this computer
        got = t.call("run", {"program": "docker", "args": ["compose", "up", "-d"]})
        assert not got["ok"] and "Docker wall is not built yet" in got["said"]
    ports.docker_wall = True
    argv, refusal = world.host.trials._compose(rec, ["-p", "theirs", "up", "-d"])
    assert refusal == "" and argv[1:] == ["compose", "-p", f"puffpai-{t.pkg_id}", "-f", "compose.yaml",
                                          "-f", ".buildbot/compose.puffpai.yml", "up", "-d"]
    assert ("compose", "services: {}\n") in ports.zone_calls            # their compose file was zone checked
    assert world.host.trials._compose(rec, ["run", "-v", "C:\\:/x", "svc"])[1]   # not a compose a try runs
    ports.zone_refuse = ["services"]
    assert world.host.trials._compose(rec, ["up", "-d"])[1]


def test_missing_readme_screen_holds_the_job(world, ports):
    ports.hide_missing = True
    t, _ = make_try(world)
    ports.local_replies = Script([tool("download")])
    with pytest.raises(JobHeld) as e:
        tries.drive(t, tries.Models(t.link))
    assert e.value.code == "not_built" and "Drawbridge" in e.value.reason


def test_no_local_model_holds_the_job(world):
    t, _ = make_try(world)
    with pytest.raises(JobHeld) as e:
        tries.drive(t, tries.Models(t.link))
    assert "not built yet" in e.value.reason


class CloudModels(tries.Models):
    """The frontier model chosen; its turns raise *exc* (the Core's answer)."""

    def __init__(self, link, exc):
        super().__init__(link, {"company": "Anthropic", "model": "claude-opus-5-5"})
        self.exc = exc

    def turn(self, system, messages, local_only):
        if not local_only:
            raise self.exc
        return super().turn(system, messages, True)


def test_offline_cloud_falls_back_to_the_local_model(world, ports):
    t, hooks = make_try(world)
    ports.local_replies = Script([tool("give_up", reason="x"), tool("give_up", reason="it really failed")])
    off = CloudOffline("offline", OFFLINE_LINE.format(company="Anthropic"))
    tries.drive(t, CloudModels(t.link, off))
    assert OFFLINE_LINE.format(company="Anthropic") in hooks.lines
    assert t.built_with == "local" and t.ended == "it really failed"


def test_cloud_cap_holds_the_job(world):
    t, _ = make_try(world)
    held = CloudHeld("cap", "Toolsmith reached the $5 limit you set for this build. Raise it, or let "
                            "Toolsmith use this computer.")
    with pytest.raises(JobHeld) as e:
        tries.drive(t, CloudModels(t.link, held))
    assert e.value.code == "cap" and "$5 limit" in e.value.reason and t.closed


def test_remove_try_folder_never_follows_a_junction(tmp_path):
    import os
    import subprocess
    root = tmp_path / "packages"
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("hers")
    try_dir = root / "app"
    try_dir.mkdir(parents=True)
    link = try_dir / "node_modules"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, check=True)
    else:
        os.symlink(outside, link)
    assert trials_mod.remove_try_folder(try_dir, root)
    assert (outside / "keep.txt").read_text() == "hers"
    assert not trials_mod.remove_try_folder(outside, root)          # never outside packages
    assert not trials_mod.remove_try_folder(root, root)             # never the packages folder itself


def test_model_sees_the_tool_result(world, ports):
    t, _ = make_try(world)
    script = Script([tool("server_log"), tool("give_up", reason="r"), tool("give_up", reason="r2")])
    ports.local_replies = script
    tries.drive(t, tries.Models(t.link))
    assert last_result(script.seen[1]) == "Call download first."


def test_a_try_is_only_for_a_candidate_on_the_plan(world):
    b = world.build_with_yes([cand()])
    link = world.link(b["id"])
    from puffpai_core.zone3.toolsmith.errors import Refused
    with pytest.raises(Refused):
        link.try_open(b["jobs"][0]["id"], "github:someone/else")
