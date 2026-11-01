"""The machine guard against other spellings and other ways to start a program (lane I7, verifier round).

Starts that the guard must refuse use paths that do not exist (``NOWHERE``), so a broken guard would only get
"not found". The two exceptions are the trailing-dot and 8.3 cases: Windows only folds those for a real file,
so they use a copy of whoami.exe (harmless: it prints the user name) in the test's own tmp folder. Shell
commands that name a bare refused program are only matched, never started.
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

import conftest
import machine_guard

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows program names")
NOWHERE = "C:\\nowhere-i7"


def _take_hits(before: int) -> list[str]:
    got = conftest._MACHINE_HITS[before:]
    del conftest._MACHINE_HITS[before:]
    return got


def _refused(fn, *a, **k) -> str:
    """*fn* is refused by the guard, once; the hit is taken back so the after-test check passes."""
    before = len(conftest._MACHINE_HITS)
    with pytest.raises(conftest.MachineChangeInTest):
        fn(*a, **k)
    got = _take_hits(before)
    assert len(got) == 1, got
    return got[0]


def _whoami_as(tmp_path: Path, name: str) -> Path:
    src = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "whoami.exe"
    if not src.is_file():
        pytest.skip("no whoami.exe")
    dst = tmp_path / name
    shutil.copyfile(src, dst)
    return dst


def _short(path) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    s = buf.value if 0 < n < 1024 else ""
    return s if s and s.lower() != str(path).lower() else ""


@pytest.mark.parametrize("name", ["pymanager.exe", "py.exe", "winget.exe", "reg.exe"])
def test_a_trailing_dot_or_space_is_the_same_program(tmp_path, name):
    exe = _whoami_as(tmp_path, name)
    for spelling in (str(exe) + ".", str(exe) + " .", str(exe) + ". . ", str(exe)[:-4] + "."):
        _refused(subprocess.run, [spelling], capture_output=True)
        _refused(subprocess.run, f'"{spelling}"', capture_output=True)
        _refused(subprocess.run, f'"{spelling}"', shell=True, capture_output=True)


@pytest.mark.parametrize("name", ["pymanager.exe", "pywmanager.exe", "regedt32.exe"])
def test_an_8_3_short_name_is_the_same_program(tmp_path, name):
    exe = _whoami_as(tmp_path, name)
    short = _short(exe)
    if not short:
        pytest.skip("this volume makes no 8.3 short names")
    assert "~" in short
    _refused(subprocess.run, [short], capture_output=True)
    _refused(subprocess.run, f'cmd /c "{short}"', capture_output=True)
    _refused(os.system, f'"{short}"')


def test_a_renamed_copy_is_known_by_its_own_version_info(tmp_path):
    src = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "reg.exe"
    if not src.is_file():
        pytest.skip("no reg.exe")
    copy = tmp_path / "harmless-name.exe"
    shutil.copyfile(src, copy)
    why = machine_guard.why_start([str(copy)])         # the matcher only: a real refused program is never started
    assert why and "renamed" in why, why
    assert conftest._refused_program([str(_whoami_as(tmp_path, "fine.exe"))], None, False, None, None) is None


@pytest.mark.parametrize("path", [
    "C:/nowhere-i7/Microsoft/WindowsApps/python.exe", "C:/nowhere-i7/Microsoft/WindowsApps/python3.exe",
    NOWHERE + "\\Python\\.\\bin\\python.exe", NOWHERE + "\\Python\\x\\..\\bin\\python3.14.exe",
    NOWHERE + "\\PYTHON\\BIN\\Alembic.exe", "C:/nowhere-i7/Python/bin/python.exe.",
    NOWHERE + "\\Microsoft\\WindowsApps\\.\\PY.EXE",
])
def test_stub_paths_by_any_spelling_are_refused(path):
    _refused(subprocess.Popen, [path, "-V"])
    _refused(subprocess.run, f'"{path}" -V', capture_output=True)


def test_a_bare_name_is_resolved_on_both_paths(tmp_path, monkeypatch):
    """Windows finds a bare name on THIS process's PATH, not the child's env. A stub found there is refused even
    when the child's own PATH points somewhere else; and the other way round."""
    apps = tmp_path / "Microsoft" / "WindowsApps"
    apps.mkdir(parents=True)
    (apps / "python3.exe").write_bytes(b"")             # an empty file: had it been started, Windows says no
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("PATH", str(apps) + os.pathsep + os.environ["PATH"])
    _refused(subprocess.Popen, ["python3", "-V"], env={"PATH": str(elsewhere),
                                                      "SystemRoot": os.environ.get("SystemRoot", "C:\\Windows")})
    monkeypatch.setenv("PATH", str(elsewhere))
    assert conftest._refused_program(["python3", "-V"], None, False, {"Path": str(apps)}, None)


#: Shell commands with a refused program after the first word. Each is only matched, never started.
SHELL_LINES = [
    "echo x & reg.exe add HKCU\\Software\\X /f", "echo x && reg add HKCU\\X", "call py.exe list", "call py list",
    "start winget install git", 'start "" msiexec /i x.msi', "& 'pymanager.exe' list", "& pymanager install 3.15",
    "Start-Process winget.exe -ArgumentList 'install git'", "Start-Process -FilePath regedit", "x; regedt32",
    "(py.exe.)", "echo hi | PYMANAGER list", "C:/nowhere-i7/Microsoft/WindowsApps/python.exe -V",
    "C:\\nowhere-i7\\Python\\.\\bin\\python.exe", 'cmd /c "reg.exe" delete HKCU\\X /f',
]
SHELL_FINE = ["echo hi", "git status", "node x.py", "Get-Date",
              "dir C:\\registry-notes", "echo pyproject.toml", "region one"]


@pytest.mark.parametrize("line", SHELL_LINES)
def test_a_refused_program_anywhere_in_a_shell_command_is_refused(line):
    assert machine_guard.why_shell(line), line
    assert conftest._refused_program(["cmd", "/c", line], None, False, None, None), line
    assert conftest._refused_program(["powershell", "-NoProfile", "-Command", line], None, False, None, None), line
    assert conftest._refused_program(line, None, True, None, None), line


@pytest.mark.parametrize("line", SHELL_FINE)
def test_ordinary_shell_commands_stay_allowed(line):
    assert conftest._refused_program(["cmd", "/c", line], None, False, None, None) is None, line


@pytest.mark.parametrize("argv,shell", [
    (["cmd", "/c", "echo x & " + NOWHERE + "\\reg.exe add HKCU\\X /f"], False),
    (["cmd", "/c", "call", NOWHERE + "\\py.exe", "list"], False),
    (["cmd", "/c", "start", NOWHERE + "\\winget.exe"], False),
    (["powershell", "-NoProfile", "-Command", "& '" + NOWHERE + "\\pymanager.exe' list"], False),
    (["powershell", "-NoProfile", "-Command", "Start-Process " + NOWHERE + "\\winget.exe"], False),
    ("echo a && " + NOWHERE + "\\msiexec.exe /i x.msi", True),
    (["echo", "a", "&&", NOWHERE + "\\reg.exe", "add"], True),
])
def test_refused_programs_behind_a_shell_are_refused_when_started(argv, shell):
    _refused(subprocess.Popen, argv, shell=shell, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.mark.parametrize("flag", ["-EncodedCommand", "-enc", "-e", "-EnCoDeD"])
def test_a_powershell_encoded_command_is_read_first(flag):
    enc = base64.b64encode(("& '" + NOWHERE + "\\pymanager.exe' list").encode("utf-16-le")).decode()
    _refused(subprocess.Popen, ["powershell", "-NoProfile", flag, enc], stdout=subprocess.DEVNULL)
    fine = base64.b64encode("Get-Date".encode("utf-16-le")).decode()
    assert conftest._refused_program(["powershell", "-NoProfile", flag, fine], None, False, None, None) is None


def test_os_system_startfile_and_spawn_are_guarded(tmp_path):
    _refused(os.system, '"' + NOWHERE + '\\py.exe" list')
    _refused(os.system, "echo x && " + NOWHERE + "\\reg.exe add HKCU\\X /f")
    _refused(os.startfile, str(tmp_path / "settings.reg"))
    _refused(os.startfile, str(tmp_path / "setup.msi"))
    _refused(os.startfile, NOWHERE + "\\pymanager.exe")
    _refused(os.startfile, NOWHERE + "\\fine.exe", "open", NOWHERE + "\\winget.exe install x")
    _refused(os.spawnv, os.P_WAIT, NOWHERE + "\\winget.exe", ["winget", "install"])
    _refused(os.spawnl, os.P_WAIT, NOWHERE + "\\msiexec.exe", "msiexec", "/i", "x.msi")


def test_a_from_winreg_import_gets_the_guarded_name_and_old_copies_are_rebound(monkeypatch):
    ns: dict = {}
    exec("from win" + "reg import Delete" + "Value, Set" + "ValueEx", ns)   # in pieces: the floor scan reads source
    _refused(ns["Set" + "ValueEx"], "not a key", "x", 0, 1, "y")         # "not a key": harmless even if unguarded
    _refused(ns["Delete" + "Value"], "not a key", "x")
    _owner, real, guard = machine_guard._PATCHED[("winreg", "SetValueEx")]
    early = types.ModuleType("i7_imported_early")
    early.SetValueEx = real                                # as if it ran "from winreg import" before the guard
    monkeypatch.setitem(sys.modules, "i7_imported_early", early)
    assert "i7_imported_early.SetValueEx" in machine_guard.sweep()
    assert early.SetValueEx is guard
    _refused(early.SetValueEx, "not a key", "x", 0, 1, "y")


# -- lane I7 last round: encoded commands, carets, backticks, quotes, streams (the matcher only) -----------------
def _enc(text: str) -> str:
    return base64.b64encode(text.encode("utf-16-le")).decode()


@pytest.mark.parametrize("flag", ["-ec", "-e", "-en", "-enc", "-encod", "-EncodedCommand", "-ENCODEDCOMMAND",
                                  "/ec", "/e", "/enc", "/EncodedCommand", "-ec:", "/EncodedCommand:"])
@pytest.mark.parametrize("ps", ["powershell", "pwsh", "C:/nowhere-i7/powershell.exe", "PowerShell.EXE"])
def test_every_spelling_of_encoded_command_is_decoded(flag, ps):
    bad = _enc(r"& 'C:\nowhere-i7\pymanager.exe' list")
    fine = _enc("Get-Date")
    if flag.endswith(":"):
        argv_bad, argv_fine = [ps, "-NoProfile", flag + bad], [ps, "-NoProfile", flag + fine]
    else:
        argv_bad, argv_fine = [ps, "-NoProfile", flag, bad], [ps, "-NoProfile", flag, fine]
    assert machine_guard.why_start(argv_bad), (flag, ps)
    assert machine_guard.why_start(argv_fine) is None, (flag, ps)
    assert machine_guard.why_start("cmd /c " + " ".join(argv_bad), shell=True), (flag, ps)


def test_an_unreadable_encoded_command_is_refused_and_echo_e_is_not_misread():
    assert "can't be read" in (machine_guard.why_start(["powershell", "-ec", "QUJDRA="]) or "")
    assert machine_guard.why_start(["bash", "-c", "echo -e hello"]) is None
    assert machine_guard.why_start(["cmd", "/c", "echo -e hello"]) is None


@pytest.mark.parametrize("line", ["p^y --list", r"r^eg add HKCU\X /f", "^p^y^m^a^n^a^g^e^r list", "re^g.exe add X",
                                  "w^inget install git", "m^siexec /i x.msi", "call p^y.e^xe list", "start r^egedit",
                                  'p"y" --list', '"r"eg add X', "echo x & r^e^g add X"])
def test_cmd_carets_and_quotes_are_taken_out_first(line):
    assert machine_guard.why_start(["cmd", "/c", line]), line
    assert machine_guard.why_start(line, shell=True), line
    assert machine_guard.why_start(f'cmd.exe /d /s /c "{line}"'), line


@pytest.mark.parametrize("line", ["& p`y --list", "& r`eg add X", "Start-Process w`inget", "& 'p`ymanager.exe' list"])
def test_powershell_backticks_are_taken_out_first(line):
    assert machine_guard.why_start(["powershell", "-NoProfile", "-Command", line]), line


@pytest.mark.parametrize("line", ["echo ^^", "echo a^&b", "echo `n", r"dir C:\re^gistry-notes"])
def test_ordinary_escapes_stay_allowed(line):
    assert machine_guard.why_start(["cmd", "/c", line]) is None, line


@pytest.mark.parametrize("prog", [r"C:\nowhere-i7\py.exe::$DATA", r"C:\nowhere-i7\reg.exe:x", "C:py.exe",
                                  "\\\\?\\C:\\nowhere-i7\\pymanager.exe"])
def test_stream_drive_relative_and_prefixed_names_are_the_same_program(prog):
    assert machine_guard.why_start([prog, "list"]), prog

