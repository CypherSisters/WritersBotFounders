"""The conftest guards against machine changes (lane I7): registry writes and installer or registry-editor
starts raise, and fail the test even when caught.

Nothing here starts a real launcher: every start that the guard must refuse names a file in tmp_path that does
not exist, so a broken guard would only get "file not found". Bare names (``py``) are checked through the
guard's own matcher, never started.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

import conftest

windows_only = pytest.mark.skipif(os.name != "nt", reason="winreg and Windows program names")


def _take_hits(before: int) -> list[str]:
    """The hits this test made, removed so the after-test check does not fail the test on purpose ones."""
    got = conftest._MACHINE_HITS[before:]
    del conftest._MACHINE_HITS[before:]
    return got


@windows_only
@pytest.mark.parametrize("name", ["CreateKey", "CreateKeyEx", "SetValue", "SetValueEx", "DeleteKey",
                                  "DeleteKeyEx", "DeleteValue"])
def test_every_winreg_write_raises_before_it_reaches_the_registry(name):
    import winreg
    before = len(conftest._MACHINE_HITS)
    # "not a key" is no registry handle: if the guard were broken, the real call would only raise TypeError.
    with pytest.raises(conftest.MachineChangeInTest, match=name):
        getattr(winreg, name)("not a key", r"Software\PuffPAI-test-never", 0)
    assert _take_hits(before) == [f"winreg.{name}"]


@windows_only
def test_a_caught_winreg_write_is_still_recorded():
    import winreg
    before = len(conftest._MACHINE_HITS)
    write = conftest._WINREG_WRITES[3]                                      # SetValueEx, from the guard's list
    try:
        getattr(winreg, write)("not a key", "x", 0, winreg.REG_SZ, "y")     # no handle: harmless if unguarded
    except Exception:  # noqa: BLE001 - the code under test swallowing it, on purpose
        pass
    assert _take_hits(before) == [f"winreg.{write}"], "the after-test check would fail this test"


@windows_only
def test_winreg_reads_stay_allowed():
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software") as k:
        assert winreg.QueryInfoKey(k)[0] >= 0


@windows_only
@pytest.mark.parametrize("prog", ["py.exe", "pyw.exe", "pymanager.exe", "winget.exe", "msiexec.exe", "reg.exe",
                                  "regedit.exe", "PY.EXE"])
def test_starting_a_refused_program_raises_and_starts_nothing(tmp_path, prog):
    before = len(conftest._MACHINE_HITS)
    exe = tmp_path / "nowhere" / prog                       # never there: a broken guard gets FileNotFoundError
    with pytest.raises(conftest.MachineChangeInTest):
        subprocess.Popen([str(exe), "--version"])
    with pytest.raises(conftest.MachineChangeInTest):
        subprocess.run([str(exe), "list"], capture_output=True)
    with pytest.raises(conftest.MachineChangeInTest):
        subprocess.run(f'"{exe}" install 3.15', capture_output=True)
    assert len(_take_hits(before)) == 3


@windows_only
@pytest.mark.parametrize("argv,shell", [
    (["cmd", "/c", r"C:\nowhere\reg.exe add HKCU\Software\X /f"], False),
    (["powershell", "-NoProfile", "-Command", r"C:\nowhere\pymanager.exe list"], False),
    (r"C:\nowhere\msiexec.exe /i x.msi /qn", True),
])
def test_a_refused_program_behind_a_shell_is_refused(argv, shell):
    before = len(conftest._MACHINE_HITS)
    with pytest.raises(conftest.MachineChangeInTest):
        subprocess.Popen(argv, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    assert len(_take_hits(before)) == 1


@windows_only
def test_asyncio_starts_go_through_the_same_guard(tmp_path):
    before = len(conftest._MACHINE_HITS)

    async def go():
        await asyncio.create_subprocess_exec(str(tmp_path / "x" / "winget.exe"), "install", "git")
    with pytest.raises(conftest.MachineChangeInTest):
        asyncio.run(go())
    assert len(_take_hits(before)) == 1


@windows_only
def test_the_matcher_resolves_bare_names_and_stubs(tmp_path):
    """Checked through the matcher only: these are never started."""
    empty = {"PATH": str(tmp_path)}
    for argv in (["py", "list"], ["pymanager", "install", "3.15"], ["winget", "install", "x"], ["REG", "add"],
                 ["msiexec.exe", "/i", "x"]):
        assert conftest._refused_program(argv, None, False, empty, None), argv
    apps = tmp_path / "Microsoft" / "WindowsApps"
    apps.mkdir(parents=True)
    (apps / "python.exe").write_bytes(b"")
    assert conftest._refused_program(["python", "-V"], None, False, {"PATH": str(apps)}, None)
    assert conftest._refused_program([str(tmp_path / "Python" / "bin" / "python3.exe")], None, False, empty, None)
    assert conftest._refused_program("x", r"C:\nowhere\py.exe", False, empty, None)
    for ok in ([sys.executable, "-V"], ["git", "status"], ["node", "x.js"], ["cmd", "/c", "echo hi"],
               ["powershell", "-Command", "Get-Date"], "python -V"):
        assert conftest._refused_program(ok, None, False, empty, None) is None, ok


def test_this_suites_own_python_still_starts():
    got = subprocess.run([sys.executable, "-I", "-c", "print(42)"], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "42"
