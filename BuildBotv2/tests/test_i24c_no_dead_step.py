"""D114: her Your turn never asks for a command that can't run, and a desktop app's line says what it is.

Her case: a step said "Run node_modules/.bin/vite" for a folder with no ``node_modules\\.bin``, and the job
sat on ``waiting_you``. Three unrelated kinds of app run through these tests. The broker's refusals are
asserted exactly as they were, because this lane only changes what is asked of her.
"""

from __future__ import annotations

import shlex
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from toolsmith_fakes import Script, World, tool
from puffpai_core.wizard import lines as L
from puffpai_core.zone3.toolsmith import agent, tries
from puffpai_core.zone3.toolsmith_host import allowlist as A

WIN = sys.platform == "win32"
windows = pytest.mark.skipif(not WIN, reason="PowerShell forms are Windows only")

ELECTRON = {"package.json": json.dumps({"name": "recipe-box", "scripts": {"dev": "electron-vite dev"},
                                        "devDependencies": {"electron": "^37.0.0"}}),
            "README.md": "# Recipe box\n\nA desktop app for recipes.\n"}
VITE_CONFIG = {"electron.vite.config.ts": "export default {}\n", "README.md": "# Bird log\n\nLog birds you see.\n",
               "package.json": json.dumps({"name": "bird-log", "scripts": {"build": "x"}})}
TAURI = {"src-tauri/tauri.conf.json": "{}\n", "package.json": json.dumps({"name": "invoice-desk"}),
         "README.md": "# Invoice desk\n\nInvoices for a small business.\n"}
TAURI_DEP = {"package.json": json.dumps({"name": "invoice-tool", "devDependencies": {"@tauri-apps/cli": "^2"}}),
             "README.md": "# Invoice tool\n"}
WEB = {"package.json": json.dumps({"name": "garden-board", "scripts": {"start": "node server.js"}}),
       "server.js": "// a page\n", "README.md": "# Garden board\n\nA web page for your garden.\n"}
PYTHON = {"requirements.txt": "flask\n", "app.py": "# a page\n", "README.md": "# Bird counts\n\nA web page.\n"}
PLAIN_PNPM = {**WEB, "pnpm-lock.yaml": "lockfileVersion: 9\n"}


class Hooks(tries.Hooks):
    def __init__(self):
        self.states, self.said, self.asked = [], [], []
        self.answer = "done"

    def say(self, line):
        self.said.append(line)

    def status(self, state, line=""):
        self.states.append((state, line))

    def wait_step(self, step_id):
        self.asked.append(step_id)
        return self.answer


@pytest.fixture
def world(tmp_path, state):
    w = World(tmp_path, state)
    yield w
    w.host.stop(timeout=10)


def make_try(world, tmp_path, name, files, **budgets):
    repo = tmp_path / "repos" / name
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    world.ports.fixture_repo = repo
    cand = {"name": name, "full_name": f"example/{name}", "stars": 5, "description": "An app", "readme_steps": [],
            "runtime": "node", "tries_in": "native", "programs": [], "source": "github"}
    b = world.build_with_yes([cand], {"name": f"Job for {name}", "does": "Helps", "sample_ask": "What now?"})
    link = world.link(b["id"])
    s = link.build()["settings"]
    s["budgets"].update(budgets)
    hooks = Hooks()
    job = b["jobs"][0]
    t = tries.Try(job, job["plan"]["candidates"][0], link=link, settings=s, hooks=hooks)
    assert t.call("download", {})["ok"]
    return t, hooks


def steps(world):
    return world.ports.checklist.steps


def no_step(world, hooks):
    assert steps(world) == [] and hooks.asked == []
    assert not [s for s in hooks.states if s[0] == "waiting_you"]


def file_at(folder: Path, rel: str, text: str = "") -> Path:
    p = folder / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# -- a program that isn't there ----------------------------------------------------------------------------

@pytest.mark.parametrize("name,files", [("recipe-box", ELECTRON), ("invoice-desk", TAURI), ("garden-board", WEB)])
def test_a_missing_program_raises_no_step_and_tells_the_model_why(world, tmp_path, name, files):
    t, hooks = make_try(world, tmp_path, name, files)
    got = t.call("run", {"program": "node_modules/.bin/vite", "args": ["preview", "--port", "18649"]})
    assert not got["ok"] and "node_modules/.bin/vite isn't there" in got["said"], got
    assert "npm" in got["said"] and "npx" in got["said"], got
    no_step(world, hooks)
    assert not t.finished
    t.close()


def test_a_pnpm_project_is_told_the_way_the_broker_allows_it(world, tmp_path):
    t, hooks = make_try(world, tmp_path, "garden-pnpm", PLAIN_PNPM)
    got = t.call("run", {"program": "node_modules/.bin/vite", "args": ["preview"]})
    assert "npx -y pnpm" in got["said"], got
    no_step(world, hooks)
    t.close()


def test_a_missing_program_in_an_app_with_no_node_is_told_other_ways(world, tmp_path):
    t, hooks = make_try(world, tmp_path, "bird-counts", PYTHON)
    got = t.call("run", {"program": "puffpai-no-such-program-7f3a", "args": ["build"]})
    assert not got["ok"] and "isn't there" in got["said"] and "install_program" in got["said"], got
    assert "npx" not in got["said"], got
    no_step(world, hooks)
    t.close()


def test_a_missing_server_program_raises_no_step_and_never_sends_it_to_ask_owner(world, tmp_path):
    t, hooks = make_try(world, tmp_path, "garden-board", WEB)
    got = t.call("start_server", {"program": "node_modules/.bin/vite", "args": ["preview"]})
    assert not got["ok"] and "isn't there" in got["said"] and "ask_owner" not in got["said"], got
    no_step(world, hooks)
    t.close()


def test_ask_owner_gets_the_same_answer_for_a_program_found_missing(world, tmp_path):
    t, hooks = make_try(world, tmp_path, "garden-board", WEB)
    t.call("run", {"program": "node_modules/.bin/vite", "args": ["preview"]})
    for text in ("node_modules/.bin/vite preview", "& 'node_modules/.bin/vite' 'preview'",
                 "Set-Location -LiteralPath 'x'\n& '.\\node_modules\\.bin\\vite' 'preview'"):
        got = t.call("ask_owner", {"title": "Run it", "kind": "run_command", "text": text})
        assert not got["ok"] and "can't run here" in got["said"], (text, got)
    no_step(world, hooks)
    # Another step only she can do still reaches her.
    got = t.call("ask_owner", {"title": "Run the other one", "kind": "run_command", "text": "npx vite preview"})
    assert got["ok"] and steps(world)[-1]["title"] == "Run the other one"
    t.close()


def test_the_same_missing_program_cant_loop_it_ends_with_no_step(world, tmp_path, state):
    t, hooks = make_try(world, tmp_path, "garden-board", WEB, repeats_to_end=3)
    world.ports.local_replies = Script(
        [lambda m: tool("run", program="node_modules/.bin/vite", args=["preview"]) for _ in range(40)])
    tries.drive(t, tries.Models(t.link))
    assert t.ended == "it kept repeating the same failing command"
    assert t.steps <= 5
    no_step(world, hooks)


def test_changing_the_ask_each_time_still_stops_at_the_step_limit(world, tmp_path):
    t, hooks = make_try(world, tmp_path, "garden-board", WEB, adopt_steps=6)
    world.ports.local_replies = Script(
        [(lambda n: lambda m: tool("run", program=f"node_modules/.bin/tool{n}", args=[]))(n) for n in range(40)])
    tries.drive(t, tries.Models(t.link))
    assert t.ended == "it did not reach a working page within 6 steps"
    no_step(world, hooks)


def test_after_her_install_step_a_bare_name_gets_its_step_again(world, tmp_path, monkeypatch):
    """Toolsmith's PATH is from when it started, so her install can't show there: her Done counts."""
    t, hooks = make_try(world, tmp_path, "bird-counts", PYTHON)
    assert "isn't there" in t.call("run", {"program": "puffpai-no-such-tool", "args": []})["said"]
    assert t.call("install_program", {"name": "puffpai-no-such-tool", "why": "It needs it."})["ok"]
    got = t.call("run", {"program": "puffpai-no-such-tool", "args": ["x"]})
    assert got["ok"] and tries.OWNER_RAN.format(line="puffpai-no-such-tool x") in got["said"], got
    assert ("& 'puffpai-no-such-tool' 'x'" if WIN else "puffpai-no-such-tool x") in steps(world)[-1]["text"]
    t.close()


# -- a program that can run keeps its step -------------------------------------------------------------------

GO = "go.exe" if WIN else "go"


def _on_path(tmp_path, monkeypatch, name=GO):
    d = tmp_path / "on-path"
    d.mkdir(exist_ok=True)
    (d / name).write_text("", encoding="utf-8")                # never run: the check only looks
    (d / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(d) + os.pathsep + os.environ.get("PATH", ""))


@pytest.mark.parametrize("name,files", [("recipe-box", ELECTRON), ("bird-counts", PYTHON)])
def test_a_program_on_path_keeps_its_step_word_for_word(world, tmp_path, monkeypatch, name, files):
    _on_path(tmp_path, monkeypatch)
    t, hooks = make_try(world, tmp_path, name, files)
    got = t.call("run", {"program": "go", "args": ["build", "-o", "app.exe", "."]})
    assert got["ok"] and got["said"] == tries.OWNER_RAN.format(line="go build -o app.exe .")
    step = steps(world)[-1]
    assert step["text"] == (f"Set-Location -LiteralPath '{os.path.realpath(t.folder)}'\n& 'go' 'build' '-o' 'app.exe' '.'" if WIN else
                            f"cd {shlex.quote(os.path.realpath(t.folder))}\ngo build -o app.exe .")
    assert step["title"] == f"Run go for {name}"
    assert step["why"].startswith("BuildBot runs only the app's own Python, Node.js, docker compose and programs from "
                                  "its checked download, so this one is yours to run: go build -o app.exe .")
    assert step["why"].endswith("It runs as you.")
    assert hooks.asked
    t.close()


def test_a_name_on_path_is_never_swapped_for_a_file_in_the_folder(world, tmp_path, monkeypatch):
    """Her step ran the program on PATH before; it must not start running an unchecked file instead."""
    _on_path(tmp_path, monkeypatch)
    t, _hooks = make_try(world, tmp_path, "garden-board", WEB)
    file_at(t.folder, GO)
    t.call("run", {"program": "go", "args": ["build"]})
    assert ("& 'go' 'build'" if WIN else "\ngo build") in steps(world)[-1]["text"]
    t.close()


@windows
@pytest.mark.parametrize("program,spelled", [("bin/later.exe", r".\bin\later.exe"), ("bin\\later.exe", r".\bin\later.exe"),
                                             ("later.exe", r".\later.exe")])
def test_a_file_in_the_folder_gets_a_form_powershell_can_run(world, tmp_path, program, spelled):
    t, _hooks = make_try(world, tmp_path, "garden-board", WEB)
    file_at(t.folder, "bin/later.exe")
    file_at(t.folder, "later.exe")
    got = t.call("run", {"program": program, "args": ["x"]})
    assert got["ok"], got
    step = steps(world)[-1]
    assert step["title"] == f"Run {program} for garden-board"          # same words, only the path form differs
    assert step["text"].endswith(f"& '{spelled}' 'x'"), step["text"]
    t.close()


@windows
def test_a_cmd_sibling_gets_a_command_powershell_really_runs(world, tmp_path):
    t, _hooks = make_try(world, tmp_path, "garden-board", WEB)
    mark = t.folder / "ran.txt"
    file_at(t.folder, "node_modules/.bin/tool.js.cmd", f'@echo %1> "{mark}"\r\n')
    got = t.call("run", {"program": "node_modules/.bin/tool.js", "args": ["serve"]})
    assert got["ok"], got
    assert not mark.exists()                                  # the check ran nothing
    text = steps(world)[-1]["text"]
    assert text.endswith(r"& '.\node_modules\.bin\tool.js.cmd' 'serve'"), text
    done = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", text],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and mark.read_text().strip() == "serve", done
    t.close()


# -- it must be a file PowerShell can start, really in the folder --------------------------------------------

@windows
def test_a_shell_script_with_no_extension_raises_no_step(world, tmp_path):
    t, hooks = make_try(world, tmp_path, "garden-board", WEB)
    file_at(t.folder, "node_modules/.bin/vite", "#!/bin/sh\nexec node x\n")
    got = t.call("run", {"program": "node_modules/.bin/vite", "args": ["preview"]})
    assert not got["ok"] and "can't be started" in got["said"], got
    no_step(world, hooks)
    t.close()


def test_a_path_outside_the_folder_is_not_accepted(world, tmp_path):
    t, hooks = make_try(world, tmp_path, "garden-board", WEB)
    out = file_at(tmp_path, "outside/tool.exe")
    for program in (str(out), os.path.relpath(out, os.path.realpath(t.folder))):
        got = t.call("run", {"program": program, "args": []})
        assert not got["ok"] and "can't be started" in got["said"], (program, got)
    no_step(world, hooks)
    t.close()


@windows
def test_a_junction_out_of_the_folder_is_not_accepted(world, tmp_path):
    t, hooks = make_try(world, tmp_path, "garden-board", WEB)
    file_at(tmp_path, "outside/tool.exe")
    (t.folder / "node_modules").mkdir()
    link = t.folder / "node_modules" / ".bin"
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(tmp_path / "outside")], check=True, capture_output=True)
    try:
        got = t.call("run", {"program": "node_modules/.bin/tool.exe", "args": []})
        assert not got["ok"] and "can't be started" in got["said"], got
        no_step(world, hooks)
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(link)], check=True, capture_output=True)
    t.close()


@pytest.mark.parametrize("to_outside", [True, False])
def test_a_link_is_not_accepted_wherever_it_points(world, tmp_path, to_outside):
    t, hooks = make_try(world, tmp_path, "garden-board", WEB)
    target = file_at(tmp_path, "outside/real.exe") if to_outside else file_at(t.folder, "bin/real.exe")
    try:
        (t.folder / "bin").mkdir(exist_ok=True)
        os.symlink(target, t.folder / "bin" / "tool.exe")
    except OSError:
        pytest.skip("this account can't make a file link")
    got = t.call("run", {"program": "bin/tool.exe", "args": []})
    assert not got["ok"] and "can't be started" in got["said"], got
    no_step(world, hooks)
    t.close()


def test_a_file_link_is_not_its_own_file(tmp_path, monkeypatch):
    """The same rule where this account can't make a real link: a file whose real place differs is a link."""
    real = file_at(tmp_path, "app/bin/real.exe")
    link = file_at(tmp_path, "app/bin/tool.exe")
    assert tries._own_file(link, tmp_path / "app")
    plain = os.path.realpath
    monkeypatch.setattr(tries.os.path, "realpath", lambda p, **kw: plain(real if Path(p) == link else p, **kw))
    assert not tries._own_file(link, tmp_path / "app")


# -- the broker's refusals, exactly as they were ---------------------------------------------------------------

def test_the_brokers_refusals_are_unchanged(world, tmp_path):
    """The broker's own answers, straight from the Core route: this lane changes none of them."""
    t, _hooks = make_try(world, tmp_path, "garden-board", WEB)
    link, try_id, where = t.link, t.try_id, os.path.realpath(t.folder)
    missing = link.try_run(try_id, {"program": "node_modules/.bin/vite", "args": ["preview"]})
    why = ("it is not the app's own Python, Node.js, npm, npx, docker compose or a program from its checked download.")
    assert missing == {"ok": False, "refused": True, "said": f"BuildBot didn't run node_modules/.bin/vite: {why}",
                       "not_run": {"program": "node_modules/.bin/vite", "args": ["preview"], "cwd": ".", "folder": where,
                                   "resolved": "", "why": why, "kind": A.YOUR_TURN}}
    later = file_at(t.folder, "bin/later.exe")
    got = link.try_run(try_id, {"program": "bin/later.exe", "args": ["x"]})
    assert "exit" not in got and got["not_run"]["kind"] == A.YOUR_TURN
    assert got["not_run"]["why"] == "it was not in the download Drawbridge checked." and got["said"].endswith(got["not_run"]["why"])
    assert Path(got["not_run"]["resolved"]) == Path(where) / "bin" / later.name
    if WIN:
        file_at(t.folder, "node_modules/.bin/vite.cmd", "@echo x\r\n")
        got = link.try_run(try_id, {"program": "node_modules/.bin/vite", "args": ["preview"]})
        assert "exit" not in got and got["not_run"]["kind"] == A.HINT and got["not_run"]["use"] == A.BATCH_USE
        assert got["not_run"]["why"] == A.BATCH_WHY
    hint = link.try_run(try_id, {"program": "pnpm", "args": ["install"]})
    assert hint["not_run"]["kind"] == A.HINT and "npx -y pnpm install" in hint["not_run"]["use"]
    denied = link.try_run(try_id, {"program": "powershell", "args": ["-Command", "echo x"]})
    assert "exit" not in denied and denied["not_run"]["kind"] == A.DENIED and denied["said"] == denied["not_run"]["why"]
    t.close()


def test_what_the_broker_tells_the_model_itself_still_goes_back_as_it_did(world, tmp_path):
    """A hint or a denied answer never was a step, and keeps its words."""
    t, hooks = make_try(world, tmp_path, "garden-board", WEB)
    hint = t.link.try_run(t.try_id, {"program": "pnpm", "args": ["install"]})
    assert t.call("run", {"program": "pnpm", "args": ["install"]})["said"] == hint["said"]
    denied = t.link.try_run(t.try_id, {"program": "powershell", "args": ["-Command", "echo x"]})
    assert t.call("run", {"program": "powershell", "args": ["-Command", "echo x"]})["said"] == denied["said"]
    no_step(world, hooks)
    t.close()


# -- a desktop app's line ------------------------------------------------------------------------------------

DESKTOPS = [("recipe-box", ELECTRON, "Electron"), ("bird-log", VITE_CONFIG, "Electron"),
            ("invoice-desk", TAURI, "Tauri"), ("invoice-tool", TAURI_DEP, "Tauri")]


def _gives_up(t, reason="vite preview never answered"):
    t.call("give_up", {"reason": reason})
    t.call("give_up", {"reason": reason})


@pytest.mark.parametrize("name,files,what", DESKTOPS)
def test_a_desktop_apps_line_reads_plain_on_her_job(world, tmp_path, name, files, what):
    t, hooks = make_try(world, tmp_path, name, files)
    _gives_up(t)
    assert t.finished
    assert t.ended.startswith(f"it looks like a desktop app ({what})"), t.ended
    assert "no web page" in t.ended and "install it yourself" in t.ended and "different app" in t.ended
    assert "\u2014" not in t.ended
    # What she reads: the job's line on the Building card, and the Prove step.
    tried = [{"name": name, "why": agent.why_of(name, t.ended)}]
    assert "desktop app" in f"No app worked yet. {agent.tried_line(tried)}"[:600]
    assert any("desktop app" in x for x in L.prove_lines([{"name": "A job", "state": "not_yet", "tried": tried}]))
    assert any("vite preview never answered" in x for x in hooks.said)      # the model's own words stay in the log


@pytest.mark.parametrize("name,files", [("garden-board", WEB), ("bird-counts", PYTHON)])
def test_a_web_app_keeps_the_models_reason(world, tmp_path, name, files):
    t, _hooks = make_try(world, tmp_path, name, files)
    _gives_up(t, "its server crashes on start")
    assert t.ended == "its server crashes on start"


def test_a_desktop_app_out_of_steps_says_what_it_is_too(world, tmp_path):
    t, _hooks = make_try(world, tmp_path, "recipe-box", ELECTRON, adopt_steps=3)
    world.ports.local_replies = Script([lambda m: tool("server_log") for _ in range(10)])
    tries.drive(t, tries.Models(t.link))
    assert t.ended.startswith("it looks like a desktop app (Electron)"), t.ended


def test_her_own_skip_keeps_its_words_on_a_desktop_app(world, tmp_path):
    t, hooks = make_try(world, tmp_path, "recipe-box", ELECTRON)
    hooks.answer = "skipped"
    t.call("ask_owner", {"title": "Sign in", "kind": "confirm"})
    assert t.ended == "Skipped by you" and t.skipped


@pytest.mark.parametrize("name,files", [("recipe-box", ELECTRON), ("invoice-desk", TAURI)])
def test_the_model_reads_the_desktop_signs_in_its_facts(world, tmp_path, name, files):
    t, _hooks = make_try(world, tmp_path, name, files)
    said = t.call("download", {})["said"]
    assert "desktop app" in said and "give_up" in said
    t.close()


def test_a_web_apps_facts_say_nothing_of_a_desktop(world, tmp_path):
    t, _hooks = make_try(world, tmp_path, "garden-board", WEB)
    assert "desktop app" not in t.call("download", {})["said"]
    t.close()


# -- the whole way, as the job card reads it ---------------------------------------------------------------

#: The shape from her test: an Electron app, pnpm's lock file, a node_modules folder with no .bin, no dist.
ELECTRON_PNPM = {**ELECTRON, "electron.vite.config.ts": "export default {}\n", "pnpm-lock.yaml": "lockfileVersion: 9\n",
                 "node_modules/.modules.yaml": "x\n"}


@pytest.mark.parametrize("name,files,line", [
    ("story-arcs", ELECTRON_PNPM, "it looks like a desktop app (Electron)"),
    ("invoice-desk", TAURI, "it looks like a desktop app (Tauri)"),
    ("garden-board", WEB, "its server crashes on start")])
def test_the_job_card_line_after_a_whole_build(world, tmp_path, name, files, line):
    repo = tmp_path / "repos" / name
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    world.ports.fixture_repo = repo
    cand = {"name": name, "full_name": f"example/{name}", "stars": 5, "description": "An app", "readme_steps": [],
            "runtime": "node", "tries_in": "native", "programs": [], "source": "github"}
    b = world.build_with_yes([cand], {"name": f"Job for {name}", "does": "Helps", "sample_ask": "What now?"})
    missing = tool("run", program="node_modules/.bin/vite", args=["preview", "--port", "18649", "--host", "127.0.0.1"])
    world.ports.local_replies = Script([tool("download"), missing, tool("give_up", reason="its server crashes on start"),
                                        tool("give_up", reason="its server crashes on start")])
    agent.Builder(world.link(b["id"])).run()
    job = world.host.view(b["id"])["jobs"][0]
    assert steps(world) == []                                 # no Your turn, and the job never sat on waiting_you
    assert job["status"] == "not_yet", job["run"]
    assert job["run"]["line"].startswith(f"No app worked yet. {name}: {line}"), job["run"]["line"]
    # The model read why, and went on by itself.
    seen = json.dumps(world.ports.local_replies.seen[2])
    assert "node_modules/.bin/vite isn't there" in seen
