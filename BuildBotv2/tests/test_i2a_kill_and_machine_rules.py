"""Lane I2a security fix: a try may stop only its own processes, and never changes the computer outside itself.

A try's ``taskkill /F /PID <n>`` passed the zone check, so a try could end any program on the computer by its
number. Now (``toolsmith_zone.process_refusal``, checked by the zone AND by the exec broker):
- a stop runs only when every process it names is in this try's own job objects (the broker's jobs);
- a stop by name or pattern (``/IM``, ``-Name``, ``pkill``, ``killall``) never runs;
- Toolsmith stops its own servers with ``stop_server`` (the broker ends the job).
And (``toolsmith_zone.machine_refusal``) installs for the whole computer, registry writes, scheduled tasks,
services, ``setx`` and Python outside the app's venv never run in a try: they are a "Your turn" step she runs
herself (03 3.5), so her steps may hold them.

Safety in this file: a by-name stop is only ever tried with a program name that does not exist, and the one
outside process is this test's own sleep, stopped at the end by its own recorded handle.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from puffpai_core.steward.jobs import pid_alive
from puffpai_core.zone3 import toolsmith_zone as tz
from toolsmith_fakes import FIXTURES, FakePorts, World, make_venv, script
from puffpai_core.zone3.toolsmith_host.ports import ZoneAnswer

PY = Path(sys.executable).as_posix()
OWN = 4242
NO_SUCH = "puffpai-no-such-program-7f3a"          # never a real program: a by-name stop that slipped would end nothing
NO_SUCH_PORT = 1                                   # a port no app of hers listens on (tcpmux, never used)


def wait_for(fn, limit_s=15.0, step=0.1):
    end = time.monotonic() + limit_s
    while time.monotonic() < end:
        if fn():
            return True
        time.sleep(step)
    return bool(fn())


@pytest.fixture
def try_zone(state):
    folder = state / "packages" / "demo-app"
    folder.mkdir(parents=True)
    return folder, tz.zone_for(str(folder), state=str(state))


def _check(try_zone, command, **kw):
    folder, zone = try_zone
    return tz.check(command, folder=str(folder), kind="command", zone=zone, **kw)


# -- 1. stopping processes --------------------------------------------------------------------------

BY_NAME = [
    f"taskkill /F /IM {NO_SUCH}.exe", f"taskkill //F //IM {NO_SUCH}.exe", f"taskkill -im {NO_SUCH}.exe -f",
    f'taskkill /FI "IMAGENAME eq {NO_SUCH}.exe" /F', f"Stop-Process -Name {NO_SUCH}", f"Stop-Process -ProcessName {NO_SUCH} -Force",
    f"spps -n {NO_SUCH}", f"kill -Name {NO_SUCH}", f"kill {NO_SUCH}", f"pkill {NO_SUCH}", f"pkill -f {NO_SUCH}.js", f"killall {NO_SUCH}",
    f"tskill {NO_SUCH}", f"pskill {NO_SUCH}", f"npx kill-port {NO_SUCH_PORT}", f"fuser -k {NO_SUCH_PORT}/tcp",
    f"wmic process where name='{NO_SUCH}.exe' delete", f"wmic process where \"name='{NO_SUCH}.exe'\" call terminate",
    f'cmd /c "taskkill /F /IM {NO_SUCH}.exe"', f'powershell -Command "Stop-Process -Name {NO_SUCH}"',
    f"C:\\Windows\\System32\\taskkill.exe /IM {NO_SUCH}.exe", f"task^kill /IM {NO_SUCH}.exe", f"ntsd -pn {NO_SUCH}.exe -c q",
]


@pytest.mark.parametrize("command", BY_NAME)
def test_a_stop_by_name_or_pattern_is_always_denied(try_zone, command):
    v = _check(try_zone, command, own_pids={OWN})
    assert v.verdict == "deny" and v.line == tz.KILL_BY_NAME, (command, v)
    assert _check(try_zone, command, own_pids={OWN}, owner_step=True).verdict == "deny"


UNCHECKABLE = [
    f"Get-Process {NO_SUCH} | Stop-Process", "Get-Process -Id 4242 | Stop-Process", "(Get-Process -Id 4242).Kill()",
    "[System.Diagnostics.Process]::GetProcessById(4242).Kill()",
    'Get-CimInstance Win32_Process -Filter "ProcessId=4242" | Invoke-CimMethod -MethodName Terminate',
    'python -c "import os; os.kill(4242, 9)"', 'node -e "process.kill(4242)"', "taskkill /PID $pid /F",
    "kill %1", "kill -- -4242", "taskkill /S otherpc /PID 4242", f"pgrep {NO_SUCH} | xargs kill",
    "powershell -EncodedCommand ZQBjAGgAbwAgAGgAaQA=", "pwsh -enc ZQBjAGgAbwA=",
]


@pytest.mark.parametrize("command", UNCHECKABLE)
def test_a_stop_that_cant_be_checked_is_denied(try_zone, command):
    v = _check(try_zone, command, own_pids={OWN})
    assert v.verdict == "deny" and v.line in (tz.KILL_UNCHECKABLE, tz.KILL_BY_NAME, tz.HIDDEN), (command, v)


NOT_OURS = ["taskkill /F /PID 999999", "taskkill /F /PID 4242 /PID 4243", "Stop-Process -Id 4242,4243",
            "kill -9 4243", "wmic process where processid=4243 delete", "tskill 4243", "kill 0", "Stop-Process 1"]


@pytest.mark.parametrize("command", NOT_OURS)
def test_a_stop_of_a_process_outside_the_trys_job_is_denied(try_zone, command):
    v = _check(try_zone, command, own_pids={OWN})
    assert v.verdict == "deny" and v.line == tz.KILL_NOT_OURS, (command, v)


OURS = ["taskkill /F /PID 4242", "taskkill //F //T //PID 4242", "taskkill -F -PID 4242", "taskkill /PID:4242",
        "Stop-Process -Id 4242 -Force", "Stop-Process 4242", "spps -Id 4242", "kill -9 4242", "kill 4242",
        "kill -s TERM 4242", "kill -KILL 4242", "wmic process where processid=4242 delete",
        "wmic process 4242 call terminate", "tskill 4242"]


@pytest.mark.parametrize("command", OURS)
def test_a_stop_of_the_trys_own_process_is_allowed(try_zone, command):
    assert _check(try_zone, command, own_pids={OWN}).verdict == "allow", command
    assert _check(try_zone, command).verdict == "deny", "no pids given: none of them is the try's own"
    assert _check(try_zone, command, own_pids={OWN}, owner_step=True).verdict == "deny", \
        "her steps never stop processes: Toolsmith stops its own servers with stop_server"


@pytest.mark.parametrize("command", ["tasklist /FI \"IMAGENAME eq node.exe\"", "wmic process list brief",
                                     "npm run kill-server", "npm install", "echo killing time"])
def test_reads_and_look_alikes_are_not_stops(try_zone, command):
    assert _check(try_zone, command).verdict == "allow", command


# -- 2. changing the computer outside the try -------------------------------------------------------

#: Another place on this computer, and the try's own pip, written the way this computer writes them.
ELSEWHERE = "C:/Users/Public/elsewhere" if sys.platform == "win32" else "/opt/elsewhere"
VENV_PIP = ".venv/Scripts/pip" if sys.platform == "win32" else ".venv/bin/pip"
MACHINE = [
    "winget install -e --id Git.Git", "winget upgrade --all", "choco install nodejs -y", "scoop install go",
    "msiexec /i node.msi /qn", "Start-Process msiexec -ArgumentList '/i x.msi'", "Start-Process .\\setup.msi",
    "py install 3.12", "pymanager install 3.13", "pip install --user requests",
    f"{VENV_PIP} install --target {ELSEWHERE} requests",
    f"python -m pip install --prefix={ELSEWHERE} requests", f"pip install -t{ELSEWHERE} requests",
    "pip --isolated install requests", "PIP_REQUIRE_VIRTUALENV=0 pip install requests",
    "$env:PIP_REQUIRE_VIRTUALENV=''; pip install requests", "pip install --break-system-packages requests",
    "uv pip install --system requests", "uv tool install ruff", "pipx install httpie", "pip config set global.user true",
    "reg add HKCU\\Software\\X /v Y /d 1 /f", "reg delete HKLM\\Software\\X /f", "reg import settings.reg",
    'New-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" -Name LongPathsEnabled '
    '-Value 1 -PropertyType DWORD -Force',
    "Set-ItemProperty -Path HKCU:\\Software\\X -Name Y -Value 1", "Remove-Item -Path Registry::HKEY_CURRENT_USER\\X",
    'powershell -Command "Set-ItemProperty HKCU:\\Software\\X -Name a -Value 1"', "regedit /s settings.reg",
    "schtasks /create /tn x /tr calc.exe /sc onlogon", "schtasks //create //tn x //tr calc.exe //sc onlogon",
    "schtasks /delete /tn x /f", "Register-ScheduledTask -TaskName x -Action $a",
    "sc create foo binPath= C:\\x.exe", "sc.exe config foo start= auto", "sc stop Spooler",
    "New-Service -Name foo -BinaryPathName C:\\x.exe", "Stop-Service Spooler", "net stop spooler",
    'setx PATH "%PATH%;C:\\x"', "cmd /c setx FOO 1", "[Environment]::SetEnvironmentVariable('FOO','1','User')",
    "npm install -g pnpm", "npm i --global yarn", "pnpm add -g serve", "yarn global add serve",
    "npx -y pnpm@9.1.0 add -g serve", "corepack enable", "npm link",
    "Install-Module PSReadLine -Scope CurrentUser", "Add-AppxPackage app.msix", "Set-ExecutionPolicy Unrestricted",
    "Set-MpPreference -DisableRealtimeMonitoring $true", "Add-MpPreference -ExclusionPath C:\\",
    "netsh advfirewall set allprofiles state off", "New-NetFirewallRule -DisplayName x -Direction Inbound",
    "shutdown /r /t 0", "Restart-Computer", "bcdedit /set testsigning on", "wsl --shutdown",
    "Enable-WindowsOptionalFeature -Online -FeatureName x",
]


@pytest.mark.parametrize("command", MACHINE)
def test_a_change_to_the_computer_never_runs_in_a_try(try_zone, command):
    v = _check(try_zone, command, own_pids={OWN})
    assert v.verdict == "deny" and "changes this computer outside the app's folder" in v.line, (command, v)
    assert "ask_owner" in v.line


@pytest.mark.parametrize("command", [
    "winget install -e --id Docker.DockerDesktop", "choco install ruby -y", "msiexec /i node.msi",
    'New-ItemProperty -Path "HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem" -Name LongPathsEnabled '
    '-Value 1 -PropertyType DWORD -Force',
    "reg add HKCU\\Software\\X /v Y /d 1 /f", "schtasks /create /tn x /tr calc.exe /sc onlogon",
    "sc config foo start= auto", "setx FOO 1", "npm install -g pnpm"])
def test_the_same_change_is_fine_as_her_own_step(try_zone, command):
    assert _check(try_zone, command, owner_step=True).verdict != "deny", command


@pytest.mark.parametrize("command", [
    "winget search git", "winget --version", "choco list", "reg query HKCU\\Software\\Microsoft", "schtasks /query",
    "sc query", ".venv/Scripts/pip install -r requirements.txt", ".venv/Scripts/python -m pip install -e .",
    ".venv/Scripts/python -m pip install --target ./vendor requests", "python -m venv .venv", "npm install",
    "npm run build", "npx -y pnpm@9.1.0 install", "pip --version", "Set-ExecutionPolicy -Scope Process Bypass",
    "npm link ./packages/ui"])
def test_work_inside_the_try_still_runs(try_zone, command):
    assert _check(try_zone, command).verdict == "allow", command


# -- through the Core: the gate, the broker, stop_server, and her steps ------------------------------

class RealZonePorts(FakePorts):
    """Fake ports with L4's REAL zone check behind them (the rest stays fake). A place outside the app's folder
    (this test's own Python, for its sleep server) would wait for her Yes: that Yes is taken as given here, since
    it is not what these tests check. A deny stays a deny."""

    def zone_check(self, target, *, kind, folder, write=False, cwd="", logs="", scratch="", pids=(),
                   owner_step=False):
        self.zone_calls.append((kind, target))
        self.zone_kw.append({"pids": tuple(pids or ()), "owner_step": owner_step})
        got = tz.check(str(target), folder=str(folder), kind=kind, write=write, cwd=str(cwd or ""),
                       logs=str(logs or ""), scratch=str(scratch or ""), own_pids=tuple(pids or ()),
                       owner_step=owner_step)
        return ZoneAnswer("allow" if got.verdict == "ask" else got.verdict, got.line, tuple(got.outside))


@pytest.fixture
def world(tmp_path, state):
    w = World(tmp_path, state, ports=RealZonePorts(tmp_path))
    w.ports.fixture_repo = FIXTURES / "toolsmith" / "hello-page"
    yield w
    w.host.stop(timeout=10)


def _checked_try(w):
    b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = w.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
    assert link.try_download(opened["try_id"])["ok"]
    assert link.try_check(opened["try_id"])["ok"]
    make_venv(link, opened["try_id"])                  # the try's own Python: what its servers run (D61)
    return b, link, opened["try_id"], w.host.trials.get(b["id"], opened["try_id"])


def _server(link, try_id):
    got = script(link, try_id, "sleep.py", "import time\ntime.sleep(120)\n", start=True)
    assert got["ok"], got
    return int(got["pid"])


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows only")
def test_a_try_stops_its_own_server_only_through_stop_server(world):
    _b, link, try_id, rec = _checked_try(world)
    pid = _server(link, try_id)
    assert wait_for(lambda: pid in rec.broker.own_pids())
    # No program stops anything for a try now (D61): not even its own server, by taskkill.
    ran = link.try_run(try_id, f"taskkill -F -T -PID {pid}")
    assert not ran["ok"] and "exit" not in ran and "stop_server" in ran["said"], ran
    assert pid_alive(pid)
    # An allowed program's gate is still given the try's own job pids.
    assert link.try_write(try_id, "p.py", "print(1)\n")["ok"]
    assert link.try_run(try_id, {"program": "python", "args": ["p.py"]})["ok"]
    assert pid in world.ports.zone_kw[-1]["pids"], "the gate was given the try's own job pids"
    assert link.try_stop(try_id, pid)["ok"]
    assert wait_for(lambda: not rec.broker.alive()), "the server and what it started are gone"
    assert not pid_alive(pid)


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows only")
def test_a_try_can_not_stop_a_process_outside_its_job(world):
    _b, link, try_id, rec = _checked_try(world)
    _server(link, try_id)
    outsider = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(120)"],
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        assert outsider.pid not in rec.broker.own_pids()
        for line in (f"taskkill -F -PID {outsider.pid}", f"taskkill //F //PID {outsider.pid}",
                     f"Stop-Process -Id {outsider.pid} -Force", f"kill -9 {outsider.pid}"):
            ran = link.try_run(try_id, line)
            assert not ran["ok"] and "exit" not in ran, (line, ran)             # it never reached a shell
            assert "only processes this try started" in ran["said"]
            # the broker alone (no zone in front of it) refuses it too
            direct = rec.broker.run(line)
            assert direct.get("refused") and direct["said"] == tz.KILL_NOT_OURS, (line, direct)
        time.sleep(0.5)
        assert outsider.poll() is None and pid_alive(outsider.pid), "the outside process survived"
        assert rec.broker.alive(), "and the try's own server still runs"
    finally:
        outsider.kill()                       # this test's own process, by the handle it recorded
        outsider.wait(10)
    assert not pid_alive(outsider.pid)


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows only")
@pytest.mark.parametrize("line", [f"taskkill -F -IM {NO_SUCH}.exe", f"taskkill //F //IM {NO_SUCH}.exe",
                                  f"Stop-Process -Name {NO_SUCH}", f"kill -Name {NO_SUCH}", f"pkill {NO_SUCH}",
                                  f"killall {NO_SUCH}"])
def test_by_name_forms_are_denied_by_the_zone_and_the_broker(world, line):
    _b, link, try_id, rec = _checked_try(world)
    ran = link.try_run(try_id, line)
    assert not ran["ok"] and "exit" not in ran and ran["said"] == tz.KILL_BY_NAME, ran
    direct = rec.broker.run(line)
    assert direct.get("refused") and direct["said"] == tz.KILL_BY_NAME
    assert rec.broker.start(line, None, rec.logs / "x.log").get("refused")


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows only")
def test_the_broker_refuses_a_machine_change_even_behind_a_zone_that_allows_it(tmp_path, state):
    w = World(tmp_path, state)                 # the plain fake zone allows everything
    w.ports.fixture_repo = FIXTURES / "toolsmith" / "hello-page"
    try:
        _b, link, try_id, rec = _checked_try(w)
        for line in ("setx PUFFPAI_TEST_NEVER 1", "reg add HKCU\\Software\\PuffPAITestNever /v a /d 1 /f",
                     "winget install -e --id Git.Git", "pip install --user requests"):
            ran = link.try_run(try_id, line)
            assert not ran["ok"] and "exit" not in ran and "changes this computer" in ran["said"], (line, ran)
    finally:
        w.host.stop(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="job objects are Windows only")
def test_stop_server_ends_the_servers_job(world):
    _b, link, try_id, rec = _checked_try(world)
    a, b = _server(link, try_id), _server(link, try_id)
    assert not link.try_stop(try_id, 999999)["ok"], "a number that is not its own server stops nothing"
    got = link.try_stop(try_id, a)
    assert got["ok"], got
    assert wait_for(lambda: not pid_alive(a)) and pid_alive(b)
    assert a not in rec.broker.own_pids(), "a stopped job's number is never its own again (it could be reused)"
    assert link.try_stop(try_id)["ok"]                                        # none given: all of them
    assert wait_for(lambda: not pid_alive(b)) and rec.broker.alive() == []
    assert "Stop it with stop_server" in script(link, try_id, "one.py", "print(1)\n", start=True)["said"]


def test_her_steps_are_checked_as_her_steps(world):
    b, link, try_id, _rec = _checked_try(world)
    job = b["jobs"][0]["id"]
    got = link.step_add(job, {"kind": "run_command", "title": "Install Docker Desktop",
                              "text": "winget install -e --id Docker.DockerDesktop", "admin": True}, try_id)
    assert got["id"] and world.ports.zone_kw[-1]["owner_step"] is True
    from puffpai_core.zone3.toolsmith.errors import Refused
    with pytest.raises(Refused) as e:
        link.step_add(job, {"kind": "run_command", "title": "Free the port",
                            "text": f"taskkill /F /IM {NO_SUCH}.exe"}, try_id)
    assert "by name or pattern" in str(e.value)


def test_toolsmith_has_a_stop_server_tool_that_goes_to_the_core():
    from puffpai_core.zone3.toolsmith import tries

    class Link:
        stopped: list = []

        def try_open(self, job_id, ref):
            return {"try_id": "t1", "port": 18999}

        def try_stop(self, try_id, pid=None):
            Link.stopped.append((try_id, pid))
            return {"ok": True, "said": "Stopped 1 server(s) this try started, and what they started."}

    settings = {"budgets": {"adopt_steps": 20, "adopt_minutes": 12, "reads_per_try": 15, "repeats_to_end": 3},
                "timing": {}}
    t = tries.Try({"id": "j1", "name": "News"}, {"ref": "github:x/y", "name": "Y"}, link=Link(),
                  settings=settings, hooks=tries.Hooks())
    t.folder = Path(".")
    assert "stop_server" in tries.TOOL_NAMES
    assert t.call("stop_server", {"pid": "4242"})["ok"] and Link.stopped == [("t1", 4242)]
    assert not t.call("stop_server", {"pid": "node"})["ok"]
    assert "stop them only with stop_server" in tries.SYSTEM
