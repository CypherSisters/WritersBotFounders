"""D60 and D61: the Core runs every program a try asks for WITHOUT a shell, and only from an allowlist.

Toolsmith sends ``{program, args, cwd}`` (or ONE plain program line); the Core
resolves the program to a real file (the try's own .venv, the known Node.js,
the known Docker CLI, the try folder) and decides on that file, never the
typed name. These tests drive the real broker through the real Toolsmith
routes (``World.link``), with real processes in job objects.
"""

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

#: Never a real program: a refused kill line that slipped through would end nothing (lane I4a).
NO_SUCH = "puffpai-no-such-program-7f3a"

from toolsmith_fakes import FIXTURES, World, make_venv, script
from puffpai_core.steward.jobs import pid_alive
from puffpai_core.zone3.toolsmith import tries
from puffpai_core.zone3.toolsmith_host import allowlist as A
from puffpai_core.zone3.toolsmith_host.broker import NEEDS_WALL

WIN = sys.platform == "win32"
needs_jobs = pytest.mark.skipif(not WIN, reason="job objects are Windows only")
HELLO = FIXTURES / "toolsmith" / "hello-page"
SYSTEM_TOOL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "hostname.exe"


@pytest.fixture
def world(tmp_path, state):
    w = World(tmp_path, state)
    w.ports.fixture_repo = HELLO
    yield w
    w.host.stop(timeout=10)


def _checked(w, repo: Path | None = None):
    if repo is not None:
        w.ports.fixture_repo = repo
    b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = w.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
    assert link.try_download(opened["try_id"])["ok"]
    got = link.try_check(opened["try_id"])
    assert got["ok"], got
    return b, link, opened["try_id"], w.host.trials.get(b["id"], opened["try_id"])


def _never_ran(got: dict) -> None:
    assert not got["ok"] and "exit" not in got, got


# -- allowed programs run, with no shell ---------------------------------------------------------------------

@needs_jobs
def test_the_trys_own_python_runs_a_script_file_with_no_shell(world):
    _b, link, try_id, rec = _checked(world)
    made = make_venv(link, try_id)
    assert rec.broker.venv and (rec.folder / ".venv" / "pyvenv.cfg").is_file(), made
    got = script(link, try_id, "hello.py", "import sys\nprint('from', sys.prefix)\n")
    assert got["ok"] and "from" in got["output"], got
    assert os.path.normcase(str(rec.folder / ".venv")) in os.path.normcase(got["output"])     # the try's own
    # A path to it, written either way, is the same resolved file.
    for program in (".venv/Scripts/python.exe", ".venv\\Scripts\\python", str(rec.folder / ".venv/Scripts/python.exe")):
        assert link.try_run(try_id, {"program": program, "args": ["hello.py"]})["ok"], program
    # One plain program line is still taken (older callers), split with no shell.
    assert link.try_run(try_id, 'python "hello.py"')["ok"]
    # A folder inside the try as cwd (instead of cd).
    assert link.try_write(try_id, "web/where.py", "import os\nprint(os.getcwd())\n")["ok"]
    got = link.try_run(try_id, {"program": "python", "args": ["where.py"], "cwd": "web"})
    assert got["ok"] and got["output"].strip().lower().endswith("web"), got


@needs_jobs
def test_pip_runs_as_the_venvs_python_dash_m_pip(world):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    assert link.try_write(try_id, "requirements.txt", "flask\n")["ok"]      # part E: pip's file is read first
    plan = rec.broker.plan({"program": "pip", "args": ["install", "-r", "requirements.txt"]})
    vpy = os.path.normcase(str(A.venv_python(rec.folder)))
    if isinstance(plan, dict):                                       # --without-pip: there is no pip.exe to find
        plan = rec.broker.plan({"program": "python", "args": ["-m", "pip", "install", "-r", "requirements.txt"]})
    assert os.path.normcase(plan.argv[0]) == vpy and plan.argv[1:3] == ["-m", "pip"], plan
    for bad in (["-m", "pip", "install", "--user", "x"], ["-m", "pip", "install", "--target", "C:\\elsewhere", "x"]):
        got = link.try_run(try_id, {"program": "python", "args": bad})
        _never_ran(got)


@needs_jobs
@pytest.mark.skipif(not A.known_node(), reason="Node.js is not on this computer")
def test_node_npm_and_npx_run_from_the_known_node_install_with_no_cmd(world):
    _b, link, try_id, rec = _checked(world)
    assert link.try_write(try_id, "hi.js", "console.log('node says hi')\n")["ok"]
    got = link.try_run(try_id, {"program": "node", "args": ["hi.js"]})
    assert got["ok"] and "node says hi" in got["output"], got
    plan = rec.broker.plan({"program": "npm", "args": ["--version"]})
    assert os.path.normcase(plan.argv[0]) == os.path.normcase(A.known_node()), plan
    assert plan.argv[1].endswith("npm-cli.js")                       # never npm.cmd (that needs cmd.exe)
    got = link.try_run(try_id, "npm --version")
    assert got["ok"] and got["output"].strip()[:1].isdigit(), got
    for bad in (["install", "-g", "x"], ["explore", "x", "--", "ls"], ["exec", "-c", "whoami"],
                ["--script-shell=cmd", "run", "x"]):
        _never_ran(link.try_run(try_id, {"program": "npm", "args": bad}))
    _never_ran(link.try_run(try_id, {"program": "npx", "args": ["-c", "whoami"]}))


@needs_jobs
@pytest.mark.skipif(not SYSTEM_TOOL.is_file(), reason="no hostname.exe to stand in for a shipped program")
def test_a_program_from_the_checked_download_runs_and_a_changed_or_new_one_does_not(world, tmp_path):
    repo = tmp_path / "repo"
    shutil.copytree(HELLO, repo)
    (repo / "bin").mkdir()
    shutil.copyfile(SYSTEM_TOOL, repo / "bin" / "hello.exe")
    _b, link, try_id, rec = _checked(world, repo)
    assert "bin/hello.exe" in rec.broker.checked
    got = link.try_run(try_id, {"program": "bin/hello.exe"})
    assert got["ok"] and got["output"].strip(), got
    # One that was not in the download Drawbridge checked is not run, even as a copy of the same file.
    shutil.copyfile(SYSTEM_TOOL, rec.folder / "bin" / "later.exe")
    got = link.try_run(try_id, {"program": "bin/later.exe"})
    _never_ran(got)
    assert got["not_run"]["program"] == "bin/later.exe" and "checked" in got["not_run"]["why"]
    # The checked one, changed after the check, is not run either.
    with open(rec.folder / "bin" / "hello.exe", "ab") as fh:
        fh.write(b"\0changed")
    _never_ran(link.try_run(try_id, {"program": "bin/hello.exe"}))


# -- not on the allowlist: not run, with a structured record ---------------------------------------------

@needs_jobs
def test_a_program_not_on_the_allowlist_is_not_run_and_names_itself(world, tmp_path):
    _b, link, try_id, rec = _checked(world)
    marker = tmp_path / "ran.txt"
    for request in ({"program": "hostname", "args": []}, {"program": str(SYSTEM_TOOL), "args": ["x"]},
                    {"program": "go", "args": ["build", "./..."]}, {"command": "where python"},
                    {"program": "ta\\skkill", "args": ["/F", "/IM", "x.exe"]}):
        got = link.try_run(try_id, request)
        _never_ran(got)
        nr = got["not_run"]
        assert nr["program"] == (request.get("program") or request["command"].split()[0]), got
        assert isinstance(nr["args"], list) and nr["why"], got
        assert "BuildBot didn't run" in got["said"]
    assert not marker.exists()
    # A Core tool does a familiar one: a hint, never a step for her.
    got = link.try_run(try_id, "cat README.md")
    assert got["not_run"]["use"].startswith("Read files with read_file")
    got = link.try_run(try_id, {"program": "git", "args": ["clone", "x"]})
    assert "download" in got["not_run"]["use"]


@needs_jobs
def test_toolsmith_turns_not_run_into_a_your_turn_step_naming_the_program(world, tmp_path, monkeypatch):
    (tmp_path / "go.exe").write_text("", encoding="utf-8")     # D114: a step only for a program that can run (never run here)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    b, _link, _try_id, _rec = _checked(world)
    link = world.link(b["id"])
    s = link.build()["settings"]
    job = b["jobs"][0]

    class Hooks(tries.Hooks):
        def wait_step(self, step_id):
            return "done"
    t = tries.Try(job, job["plan"]["candidates"][0], link=link, settings=s, hooks=Hooks())
    t.call("download", {})
    got = t.call("run", {"program": "go", "args": ["build", "-o", "app.exe", "."]})
    assert got["ok"] and "The owner ran it: go build -o app.exe ." in got["said"], got
    step = world.ports.checklist.steps[-1]
    assert step["kind"] == "run_command" and step["title"].startswith("Run go for")
    assert "& 'go' 'build' '-o' 'app.exe' '.'" in step["text"] and "Set-Location -LiteralPath" in step["text"]
    assert "It runs as you." in step["why"]
    # Her own step, never recorded as something Toolsmith ran (adopt must not make it an automatic setup step).
    assert not any("go build" in r["command"] for r in t.ran)
    assert any(h.get("title", "").startswith("Run go for") for h in t.human_steps)
    # A Core tool's hint goes back to the model; no step for her.
    n = len(world.ports.checklist.steps)
    got = t.call("run", {"command": "ls -la"})
    assert not got["ok"] and "list_files" in got["said"] and len(world.ports.checklist.steps) == n
    t.close()


# -- shell syntax and shells ---------------------------------------------------------------------------------

@pytest.mark.parametrize("line", [
    "npm install && npm start", "npm install; npm start", "echo taskkill | sh", f"eval taskkill /F /IM {NO_SUCH}.exe",
    "node s.js > out.txt", "node s.js 2>&1", "python $(which x)", "python `id`", "cat $HOME/x", "type %USERPROFILE%\\x",
    "npm start &", "PORT=3000 node s.js", "set PORT=3000", "cd web", "export A=1", "source x.sh", ". ./x.sh",
    "python s.py # c", "ls ~", "rm *.pyc", "npm run (x)", "Invoke-WebRequest http://x", "node 'a.js", "x || y",
    "echo hi", "node s.js < in.txt", "python -c \"print($x)\"", "a ^& b"])
def test_shell_syntax_in_a_command_line_is_refused(line):
    with pytest.raises(A.Refusal) as e:
        A.parse_line(line)
    assert e.value.said.startswith(A.SHELL_LINE)


@needs_jobs
def test_shell_syntax_never_reaches_a_program_through_the_core(world, tmp_path):
    _b, link, try_id, rec = _checked(world)
    marker = tmp_path / "shell-ran.txt"
    for line in (f"echo x > {marker}", f"python -V && echo x > {marker}", f"cmd /c echo x > {marker}",
                 "npm install | tail", "bash -c 'echo hi'"):
        got = link.try_run(try_id, line)
        _never_ran(got)
        assert A.SHELL_LINE in got["said"] or "never runs a shell" in got["said"], (line, got)
    for program in ("bash", "sh", "cmd", "cmd.exe", "powershell", "pwsh", "C:\\Windows\\System32\\cmd.exe",
                    "wscript", "mshta", "wsl"):
        got = link.try_run(try_id, {"program": program, "args": ["/c", f"echo x > {marker}"]})
        _never_ran(got)
        assert "never runs a shell" in got["said"], (program, got)
        _never_ran(rec.broker.start({"program": program, "args": []}, None, rec.logs / "x.log"))
    assert not marker.exists()


# -- inline code ---------------------------------------------------------------------------------------------

@needs_jobs
def test_inline_code_is_refused_and_a_script_file_outside_the_try_too(world, tmp_path):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    marker = tmp_path / "inline-ran.txt"
    code = f"open(r'{marker}', 'w').write('x')"
    for args in (["-c", code], ["-Ic", code], ["-I", "-c", code], ["-uBc", code], ["-"], ["-X", "utf8", "-c", code]):
        got = link.try_run(try_id, {"program": "python", "args": args})
        _never_ran(got)
        assert "inline code" in got["said"], (args, got)
    outside = tmp_path / "outside.py"
    outside.write_text(code)
    _never_ran(link.try_run(try_id, {"program": "python", "args": [str(outside)]}))
    _never_ran(link.try_run(try_id, {"program": "python", "args": ["../../outside.py"]}))
    if A.known_node():
        js = f"require('fs').writeFileSync(String.raw`{marker}`, 'x')"
        for args in (["-e", js], ["-p", js], ["--eval", js], [f"--eval={js}"], ["-pe", js], ["--print", js],
                     ["--run", "x"], ["-r", str(outside), "x.js"]):
            got = link.try_run(try_id, {"program": "node", "args": args})
            _never_ran(got)
    assert not marker.exists()


# -- matched by the resolved file -----------------------------------------------------------------------------

@needs_jobs
def test_a_copy_of_an_allowed_interpreter_elsewhere_is_not_trusted(world, tmp_path):
    _b, link, try_id, rec = _checked(world)
    # No .venv yet: a python.exe at the top of the try (written after the check) is not the try's own.
    vpy = Path(sys.executable)
    shutil.copyfile(vpy, rec.folder / "python.exe")
    got = link.try_run(try_id, {"program": "python", "args": ["-V"]})
    _never_ran(got)
    assert "not_run" in got and "venv" in got["said"].lower()
    _never_ran(link.try_run(try_id, {"program": "./python.exe", "args": ["-V"]}))
    make_venv(link, try_id)
    own = A.venv_python(rec.folder)
    # The try's own interpreter, copied into another folder (inside the try or out), is not trusted.
    (rec.folder / "tools").mkdir()
    shutil.copyfile(own, rec.folder / "tools" / "python.exe")
    shutil.copyfile(own / ".." / ".." / "pyvenv.cfg", rec.folder / "pyvenv.cfg")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.copyfile(own, elsewhere / "python.exe")
    shutil.copyfile(own / ".." / ".." / "pyvenv.cfg", tmp_path / "pyvenv.cfg")
    for program in ("tools/python.exe", str(elsewhere / "python.exe"), "./python.exe"):
        got = link.try_run(try_id, {"program": program, "args": ["-V"]})
        _never_ran(got)
        assert got["not_run"]["resolved"], got
    # The Core keeps the venv: Toolsmith's file tools never write into it.
    assert not link.try_write(try_id, ".venv/pyvenv.cfg", "home = C:\\elsewhere\n")["ok"]
    assert not link.try_fs(try_id, "copy", **{"from": "pyvenv.cfg", "to": ".venv/pyvenv.cfg"})["ok"]
    # ... and a venv changed behind its back (a script the try ran, say) is not run until it is made again.
    cfg = rec.folder / ".venv" / "pyvenv.cfg"
    cfg.write_text(cfg.read_text() + "\n# changed\n")
    got = link.try_run(try_id, {"program": "python", "args": ["-V"]})
    _never_ran(got)
    assert got["said"] == A.VENV_CHANGED
    assert link.try_fs(try_id, "delete", path=".venv")["ok"] and rec.broker.venv is None
    make_venv(link, try_id)
    assert link.try_run(try_id, {"program": "python", "args": ["-V"]})["ok"]


@needs_jobs
def test_a_cwd_through_a_junction_out_of_the_try_is_refused(world, tmp_path):
    _b, link, try_id, rec = _checked(world)
    outside = tmp_path / "outside-dir"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    junction = rec.folder / "out"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if made.returncode != 0:
        pytest.skip("could not make a junction here")
    try:
        got = link.try_run(try_id, {"program": "python", "args": ["-V"], "cwd": "out"})
        _never_ran(got)
        assert got["said"] == A.CWD_LINE
        assert not link.try_fs(try_id, "read", path="out/keep.txt")["ok"]
        assert not link.try_fs(try_id, "copy", **{"from": "README.md", "to": "out/x.md"})["ok"]
        # delete removes the junction itself, never what it points at
        assert link.try_fs(try_id, "delete", path="out")["ok"]
        assert not os.path.lexists(junction) and (outside / "keep.txt").read_text() == "keep"
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction)                   # the junction only (never through it)


@needs_jobs
def test_network_and_device_paths_are_refused_before_any_lookup(world):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    share = "\\\\puffpai-no-such-host.invalid\\share"
    for request in ({"program": share + "\\x.exe"}, {"program": "//puffpai-no-such-host.invalid/s/x.exe"},
                    {"program": "python", "args": ["-V"], "cwd": share},
                    {"program": "python", "args": [share + "\\x.py"]},
                    {"program": "\\\\?\\C:\\Windows\\System32\\hostname.exe"},
                    {"program": "bin\\hello.exe:hidden"}):
        got = link.try_run(try_id, request)
        _never_ran(got)
        assert got["said"] == A.ODD_PATH, (request, got)
    # pip to a share: the broker's own text check (no lookup either) refuses it even before the allowlist
    got = link.try_run(try_id, {"program": "python", "args": ["-m", "pip", "install", "--target", share, "x"]})
    _never_ran(got)
    with pytest.raises(A.Refusal) as e:                  # --target is never passed, so no path is looked up
        A._pip_args(["install", "--target", share, "x"], rec.folder, rec.folder)
    assert "--target" in e.value.said
    with pytest.raises(A.Refusal) as e:                  # a log path pip writes to: a share is never looked up
        A._pip_args(["install", "--log", share, "x"], rec.folder, rec.folder)
    assert e.value.said == A.ODD_PATH


# -- the Core's file tools ---------------------------------------------------------------------------------

@needs_jobs
def test_the_core_file_tools_stay_inside_the_try(world, tmp_path):
    _b, link, try_id, rec = _checked(world)
    assert link.try_fs(try_id, "mkdir", path="conf/sub")["ok"] and (rec.folder / "conf" / "sub").is_dir()
    assert link.try_fs(try_id, "copy", **{"from": "README.md", "to": "conf/README.copy.md"})["ok"]
    assert link.try_fs(try_id, "move", **{"from": "conf/README.copy.md", "to": "conf/sub/r.md"})["ok"]
    assert (rec.folder / "conf" / "sub" / "r.md").is_file() and not (rec.folder / "conf" / "README.copy.md").exists()
    got = link.try_fs(try_id, "read", path="conf/sub/r.md")
    assert got["ok"] and "Quick start" in got["said"]
    assert "conf/" in link.try_fs(try_id, "list", path="")["said"]
    assert link.try_fs(try_id, "delete", path="conf")["ok"] and not (rec.folder / "conf").exists()
    outside = tmp_path / "secret.txt"
    outside.write_text("no")
    for op, fields in (("read", {"path": str(outside)}), ("read", {"path": "../../x"}),
                       ("copy", {"from": str(outside), "to": "x.txt"}), ("copy", {"from": "README.md", "to": "../x"}),
                       ("move", {"from": "README.md", "to": "C:/x.md"}), ("delete", {"path": ""}),
                       ("delete", {"path": ".."}), ("mkdir", {"path": "~/x"}), ("read", {"path": "a.txt:hidden"})):
        got = link.try_fs(try_id, op, **fields)
        assert not got["ok"], (op, fields, got)
    assert outside.read_text() == "no" and rec.folder.is_dir()


# -- a process outside the try is never touched by anything a try can run -------------------------------------

@needs_jobs
def test_an_outside_process_survives_everything_a_try_can_run(world, tmp_path):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    sleeper = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(300)"],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    pid = sleeper.pid                                            # recorded: this test's own process
    try:
        assert pid not in rec.broker.own_pids()
        attempts = [
            f"taskkill /F /PID {pid}", f"taskkill -F -T -PID {pid}", f"Stop-Process -Id {pid} -Force",
            f"kill -9 {pid}", f"taskkill /F /IM {NO_SUCH}.exe", f"cmd /c taskkill /F /PID {pid}",
            f"echo taskkill /F /PID {pid} | cmd", f"eval taskkill /F /PID {pid}", f"ta\\skkill /F /PID {pid}",
            {"program": "taskkill", "args": ["/F", "/PID", str(pid)]},
            {"program": "C:\\Windows\\System32\\taskkill.exe", "args": ["/F", "/PID", str(pid)]},
            {"program": "powershell", "args": ["-c", f"Stop-Process -Id {pid}"]},
            {"program": "python", "args": ["-c", f"import os; os.kill({pid}, 9)"]},
            {"program": "wmic", "args": ["process", "where", f"processid={pid}", "delete"]},
        ]
        if A.known_node():
            attempts += [{"program": "node", "args": ["-e", f"process.kill({pid})"]},
                         {"program": "npx", "args": ["-c", f"taskkill /F /PID {pid}"]}]
        for request in attempts:
            got = link.try_run(try_id, request)
            _never_ran(got)
            _never_ran(rec.broker.start(request, None, rec.logs / "x.log"))
        assert not link.try_stop(try_id, pid)["ok"], "stop_server stops only this try's own servers"
        time.sleep(0.5)
        assert sleeper.poll() is None and pid_alive(pid), "the outside process is untouched"
    finally:
        sleeper.kill()                                           # stopped by this test, by its own handle
        sleeper.wait(10)
    assert not pid_alive(pid)


@needs_jobs
def test_a_try_stops_its_own_server_only_through_stop_server(world):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    got = script(link, try_id, "serve.py", "import time\ntime.sleep(120)\n", start=True)
    assert got["ok"], got
    pid = int(got["pid"])
    _never_ran(link.try_run(try_id, f"taskkill /F /PID {pid}"))      # not even its own, by a program
    assert pid_alive(pid)
    assert link.try_stop(try_id, pid)["ok"]
    end = time.monotonic() + 10
    while time.monotonic() < end and pid_alive(pid):
        time.sleep(0.1)
    assert not pid_alive(pid) and rec.broker.alive() == []


# -- docker compose goes to the wall path, never straight from the broker --------------------------------------

def test_docker_compose_is_planned_for_the_wall_and_its_own_project(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    fake_docker = tmp_path / "docker" / ("docker.exe" if WIN else "docker")
    fake_docker.parent.mkdir()
    fake_docker.write_bytes(b"MZ not really")
    place = A.Place(folder=folder, docker=str(A._real(fake_docker)))
    plan = A.decide(A.Request(str(fake_docker), ["compose", "-p", "other", "-f", "x.yml", "up", "-d"]), place)
    assert plan.kind == "compose" and plan.rest == ["-p", "other", "-f", "x.yml", "up", "-d"]
    assert A.compose_rest(plan.rest) == ["up", "-d"]                 # her project name and files are the Core's
    for bad in (["run", "-v", "C:\\:/host", "svc"], ["cp", "C:\\secret", "svc:/x"], ["--project-directory", "C:\\", "up"]):
        with pytest.raises(A.Refusal):
            A.compose_rest(bad)
    with pytest.raises(A.Refusal) as e:
        A.decide(A.Request(str(fake_docker), ["run", "-v", "C:\\:/x", "alpine"]), place)
    assert e.value.not_run and "docker compose" in e.value.not_run["why"]


@needs_jobs
def test_the_broker_alone_never_starts_compose_without_the_wall(world):
    _b, _link, _try_id, rec = _checked(world)
    if not rec.broker.docker:
        pytest.skip("no Docker CLI on this computer")
    got = rec.broker.run({"program": "docker", "args": ["compose", "up", "-d"]})
    assert got["said"] == NEEDS_WALL and "exit" not in got
