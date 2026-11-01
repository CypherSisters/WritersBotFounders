"""install.ps1's Python finder (lane I7), run in real Windows PowerShell 5.1.

The owner's first live run said "Python 3.11 or newer is not on this computer" on a PC whose Pythons all come from
the Python install manager (%LOCALAPPDATA%\\Python\\pythoncore-3.x-64), with only Store stubs on the PATH and a
registry key left pointing at a folder that was gone.

These tests never read or write the real registry: the finder's registry roots are a JSON stand-in, and
LOCALAPPDATA is a folder in tmp_path. The Pythons in it are junctions to real interpreters on this computer
(read only: each run uses -I -B, so nothing is written there). Every junction is removed before the folder is.
"""

from __future__ import annotations

import pytest

pytest.importorskip("_winapi")

import _winapi
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.ps1"
PS = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
pytestmark = pytest.mark.skipif(os.name != "nt" or not PS.is_file(), reason="Windows PowerShell 5.1 only")

#: Runs only the finder's functions from install.ps1 (taken out with PowerShell's own parser).
HARNESS = r'''
param([string]$Install, [string]$Spec)
$ErrorActionPreference = "Stop"
$errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Install, [ref]$null, [ref]$errs)
if ($errs.Count -gt 0) { throw ("install.ps1 does not parse: " + $errs[0].Message) }
$want = @("Say", "Test-Python", "Remove-PathPrefix", "Get-RealPath", "Get-PythonFile", "Get-RegistryPythons", "Find-Python")
foreach ($f in $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)) {
    if ($want -contains $f.Name) { . ([scriptblock]::Create($f.Extent.Text)) }
}
$s = Get-Content -LiteralPath $Spec -Raw | ConvertFrom-Json
if ($s.file) { Write-Host ("RESULT=" + ((Get-PythonFile ([string]$s.file)) | ConvertTo-Json -Compress)); exit 0 }
$p = @{ RegistryRoots = @($s.roots); LocalAppData = [string]$s.lad }
if ($s.wanted) { $p.Wanted = [string]$s.wanted }
if ($s.min) { $p.MinMinor = [int]$s.min }
$r = Find-Python @p
if ($r) { Write-Host ("RESULT=" + ($r | ConvertTo-Json -Compress)) } else { Write-Host "RESULT=null" }
'''


def _real_pythons() -> dict[int, Path]:
    """Real interpreter folders on this computer, by minor version: this suite's own base Python and the
    install manager's folders. Read only."""
    found: dict[int, Path] = {}
    base = Path(getattr(sys, "_base_executable", sys.executable)).parent
    if (base / "python.exe").is_file():
        found[sys.version_info[1]] = base
    lad = os.environ.get("LOCALAPPDATA", "")
    if lad and (Path(lad) / "Python").is_dir():
        for d in (Path(lad) / "Python").glob("pythoncore-3.*-64"):
            m = re.match(r"pythoncore-3\.(\d+)-64$", d.name)
            if m and (d / "python.exe").is_file():
                found.setdefault(int(m.group(1)), d)
    return found


REAL = _real_pythons()


def _need(*minors: int) -> None:
    missing = [m for m in minors if m not in REAL]
    if missing:
        pytest.skip(f"no real Python 3.{missing[0]} on this computer to point a junction at")


def _newer() -> int:
    newer = sorted(m for m in REAL if m > 11)
    if 11 not in REAL or not newer:
        pytest.skip("needs a real 3.11 and a newer Python on this computer")
    return newer[-1]


def _is_junction(p: Path) -> bool:
    try:
        return bool(os.lstat(p).st_file_attributes & 0x400) and p.is_dir()     # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return False


@pytest.fixture
def junctions(tmp_path):
    """``link(at, real_folder)`` makes a junction; at the end each one is removed (the link only, never what it
    points at) before anything else in tmp_path is."""
    made: list[Path] = []

    def link(at: Path, real: Path) -> Path:
        at.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(real), str(at))
        made.append(at)
        return at
    yield link
    for j in reversed(made):
        if _is_junction(j):
            os.rmdir(j)                                   # removes the junction itself, not its target
    for j in made:
        assert not os.path.lexists(j), f"junction left behind: {j}"


def _run(tmp_path, *, roots=(), lad=None, path_dirs=(), wanted="", min_minor=0, file="", cwd=None):
    """Run the finder; ``(result or None, the lines it said)``."""
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"roots": [str(r) for r in roots], "lad": str(lad or ""), "wanted": wanted,
                                "min": min_minor, "file": file}), encoding="utf-8")
    harness = tmp_path / "harness.ps1"
    harness.write_text(HARNESS, encoding="ascii")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "PYTEST"))}
    system32 = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")
    env["PATH"] = os.pathsep.join([*map(str, path_dirs), system32])
    env["LOCALAPPDATA"] = str(lad or tmp_path / "no-lad")       # the default the finder would use, never hers
    got = subprocess.run([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                          str(harness), str(INSTALL), str(spec)], capture_output=True, text=True, env=env,
                         cwd=str(cwd or tmp_path), timeout=120)
    assert got.returncode == 0, got.stdout + got.stderr
    lines = got.stdout.splitlines()
    res = [ln for ln in lines if ln.startswith("RESULT=")]
    assert len(res) == 1, got.stdout + got.stderr
    value = json.loads(res[0][len("RESULT="):])
    return value, [ln for ln in lines if ln.startswith("  Python: ")]


def _view(tmp_path, name, keys: dict) -> Path:
    """A stand-in for one PythonCore registry root: ``{tag: {"InstallPath": {...}} or {}}``."""
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(keys), encoding="utf-8")
    return p


def _ip(folder: Path, exe: bool = True) -> dict:
    d = {"(default)": str(folder)}
    if exe:
        d["ExecutablePath"] = str(folder / "python.exe")
    return {"InstallPath": d}


def _said(lines, path, words) -> str:
    hit = [ln for ln in lines if str(path) in ln]
    assert hit, f"no line for {path}:\n" + "\n".join(lines)
    assert any(words in ln for ln in hit), f"{path}: wanted {words!r} in:\n" + "\n".join(hit)
    return hit[0]


PREFIX = "\\\\?\\"                                   # the \\?\ prefix that skips Windows' own path folding


def _real(p) -> str:
    """Where *p* really is (junctions followed), for comparing with what the finder reports."""
    return os.path.normcase(os.path.realpath(str(p)))


def _whoami_as(path: Path) -> Path:
    """A copy of whoami.exe (harmless: it prints the user name) at *path*. Had the finder run it, it would say
    "ran but did not say its version", never "never run"."""
    src = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "whoami.exe"
    if not src.is_file():
        pytest.skip("no whoami.exe")
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, path)
    return path


def _stub_dirs(tmp_path, junctions):
    """A real WindowsApps folder and a real install-manager Python\\bin, each with python.exe and python3.exe
    that are whoami.exe copies. Never a real stub, never a real launcher."""
    local = tmp_path / "Users" / "x" / "AppData" / "Local"
    apps, pybin = local / "Microsoft" / "WindowsApps", local / "Python" / "bin"
    for d in (apps, pybin):
        for name in ("python.exe", "python3.exe"):
            _whoami_as(d / name)
    return apps, pybin


def test_her_case_install_manager_folders_only_and_a_bogus_key(tmp_path, junctions):
    """The owner's PC at 16:30: Pythons only in the install manager's folders, registry 3.14 plus a 3.15 key for a
    folder that is gone, Store stubs and install-manager shortcuts on the PATH. 3.11 is picked."""
    top = _newer()
    lad = tmp_path / "lad"
    p11 = junctions(lad / "Python" / "pythoncore-3.11-64", REAL[11])
    pnew = junctions(lad / "Python" / f"pythoncore-3.{top}-64", REAL[top])
    gone = tmp_path / "scratch" / "lad" / "Python" / "pythoncore-3.15-64"
    hkcu = _view(tmp_path, "hkcu", {f"3.{top}": _ip(pnew), "3.15": _ip(gone)})
    apps, pybin = _stub_dirs(tmp_path, junctions)
    found, lines = _run(tmp_path, roots=[hkcu, tmp_path / "no-hklm.json"], lad=lad, path_dirs=[apps, pybin])
    assert found and found["Minor"] == 11, (found, lines)
    assert os.path.normcase(found["Path"]) == _real(p11 / "python.exe"), found
    _said(lines, gone / "python.exe", "not used, the file is not there")
    _said(lines, p11 / "python.exe", "Python 3.11, can be used")
    _said(lines, pnew / "python.exe", f"Python 3.{top}, can be used")
    _said(lines, apps / "python.exe", "Microsoft Store stub (an app link) is never run")
    _said(lines, pybin / "python.exe", "install manager's shortcuts are never run")
    assert any("same file as above" in ln for ln in lines), lines   # registry 3.x and its folder: tried once


def test_a_bogus_key_first_never_stops_the_search(tmp_path, junctions):
    _need(11)
    p11 = junctions(tmp_path / "py311", REAL[11])
    hkcu = _view(tmp_path, "hkcu", {
        "3.15": _ip(tmp_path / "gone" / "pythoncore-3.15-64"),          # its folder was deleted
        "3.13": {},                                                     # no InstallPath at all
        "3.12": {"InstallPath": {"ExecutablePath": ""}},                # empty values
        "3.11": _ip(p11),
    })
    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    found, lines = _run(tmp_path, roots=[broken, hkcu], lad=tmp_path / "empty-lad")
    assert found and found["Minor"] == 11 and os.path.normcase(found["Path"]) == _real(p11 / "python.exe"), (found, lines)
    _said(lines, "pythoncore-3.15-64", "not used, the file is not there")
    assert any("3.13 names no python.exe, skipped" in ln for ln in lines), lines
    assert any("3.12 names no python.exe, skipped" in ln for ln in lines), lines


def test_only_the_store_stub_on_the_path_finds_nothing_and_runs_nothing(tmp_path, junctions):
    _need(11)
    apps, pybin = _stub_dirs(tmp_path, junctions)
    found, lines = _run(tmp_path, roots=[], lad=tmp_path / "empty-lad", path_dirs=[apps, pybin])
    assert found is None, (found, lines)
    _said(lines, apps / "python.exe", "never run")
    _said(lines, pybin / "python.exe", "never run")
    assert not any("can be used" in ln for ln in lines), lines


def test_314_and_311_picks_311_whatever_the_order(tmp_path, junctions):
    top = _newer()
    lad = tmp_path / "lad"
    pnew = junctions(tmp_path / "reg" / f"Python3{top}", REAL[top])
    p11 = junctions(lad / "Programs" / "Python" / "Python311", REAL[11])   # a python.org per-user folder
    hkcu = _view(tmp_path, "hkcu", {f"3.{top}": _ip(pnew)})               # the newer one is found first
    found, lines = _run(tmp_path, roots=[hkcu], lad=lad)
    assert found and found["Minor"] == 11 and os.path.normcase(found["Path"]) == _real(p11 / "python.exe"), (found, lines)


def test_with_311_too_old_the_lowest_newer_one_is_picked_and_the_old_one_says_why(tmp_path, junctions):
    newer = sorted(m for m in REAL if m > 11)
    if 11 not in REAL or not newer:
        pytest.skip("needs a real 3.11 and a newer Python on this computer")
    lad = tmp_path / "lad"
    p11 = junctions(lad / "Python" / "pythoncore-3.11-64", REAL[11])
    picks = {m: junctions(lad / "Python" / f"pythoncore-3.{m}-64", REAL[m]) for m in newer}
    found, lines = _run(tmp_path, roots=[], lad=lad, min_minor=12)
    assert found and found["Minor"] == newer[0], (found, lines)
    assert os.path.normcase(found["Path"]) == _real(picks[newer[0]] / "python.exe")
    _said(lines, p11 / "python.exe", "it is Python 3.11, and BuildBot needs 3.12 or newer")


def test_a_file_that_is_not_a_program_is_rejected_and_says_so(tmp_path, junctions):
    lad = tmp_path / "lad"
    fake = lad / "Programs" / "Python" / "Python313" / "python.exe"
    fake.parent.mkdir(parents=True)
    fake.write_text("not a program", encoding="ascii")
    found, lines = _run(tmp_path, roots=[], lad=lad)
    assert found is None
    _said(lines, fake, "not used, it could not be started")


@pytest.mark.parametrize("name", ["py.exe", "pymanager.exe", "pyw.exe"])
def test_wanted_launchers_are_never_run(tmp_path, junctions, name):
    """Even when asked for by -Python: the py launcher and pymanager are refused by name, before any run."""
    _need(11)
    d = junctions(tmp_path / "tools", REAL[11])
    target = d / "python.exe"
    shim = tmp_path / "shim"
    shim.mkdir()
    exe = shim / name
    exe.write_bytes(target.read_bytes())          # a real python.exe under a launcher's name
    found, lines = _run(tmp_path, wanted=str(exe))
    assert found is None
    _said(lines, exe, "the py launcher or pymanager is never run")


def test_wanted_real_python_is_used(tmp_path, junctions):
    _need(11)
    d = junctions(tmp_path / "my python", REAL[11])
    found, lines = _run(tmp_path, wanted=str(d / "python.exe"))
    assert found and found["Minor"] == 11, (found, lines)
    _said(lines, d / "python.exe", "Python 3.11, can be used")


def test_the_whole_installer_stops_with_the_reason_in_install_log(tmp_path):
    """install.ps1 itself, copied to tmp (its transcript is written next to it): -Python names a file that is
    not there, so it stops at step 1, and install-log.txt has the line that says why."""
    copy = tmp_path / "BuildBotv2"
    copy.mkdir()
    shutil.copy2(INSTALL, copy / "install.ps1")
    missing = tmp_path / "nowhere" / "python.exe"
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "PYTEST"))}
    env.update(PUFFPAI_NO_PAUSE="1", LOCALAPPDATA=str(tmp_path / "lad"))
    got = subprocess.run([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                          str(copy / "install.ps1"), "-Bot", "test1", "-Python", str(missing)],
                         capture_output=True, text=True, env=env, cwd=str(tmp_path), stdin=subprocess.DEVNULL,
                         timeout=120)
    assert got.returncode == 1, got.stdout + got.stderr
    assert "Setup stopped:" in got.stdout and "The line above says why." in got.stdout, got.stdout
    log = (copy / "install-log.txt").read_text(encoding="utf-8", errors="replace")
    assert str(missing) in log and "the file is not there" in log, log
    assert not (copy / ".venv").exists() and not (tmp_path / "lad").exists()


def test_the_not_found_text_offers_the_python_switch_and_the_finder_never_names_a_launcher_to_run():
    text = INSTALL.read_text(encoding="ascii")                        # ASCII only (the old console)
    assert "or run this again with -Python <full path to python.exe>." in text
    block = text[text.index("function Test-Python"):text.index("$Found = Find-Python")]
    for word in ("& py", "py.exe -", "pymanager list", "pymanager install", "py list", "py install", "winget "):
        assert word not in block, word
    assert "-I -B -c" in block


# -- lane I7 verifier round: every spelling of a stub is the stub ---------------------------------------
def _short(path: Path) -> str:
    """The 8.3 short spelling of an existing path (GetShortPathNameW), or "" when the volume makes none."""
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, 1024)
    short = buf.value if 0 < n < 1024 else ""
    return short if short and short.lower() != str(path).lower() else ""


def _stub_spellings(tmp_path, junctions):
    apps, pybin = _stub_dirs(tmp_path, junctions)
    spell = {
        "forward slashes": str(apps / "python.exe").replace("\\", "/"),
        "dot segment": str(pybin.parent) + r"\.\bin\python.exe",
        "dot dot segment": str(apps) + r"\..\WindowsApps\python.exe",
        "trailing dot": str(apps / "python.exe") + ".",
        "trailing dots and spaces": str(pybin / "python.exe") + ". .",
        "mixed slashes and case": str(apps / "PYTHON.EXE").replace("\\", "/", 3),
    }
    spell["prefix"] = "\\\\?\\" + str(apps / "python.exe")
    spell["dot prefix"] = "\\\\.\\" + str(pybin / "python.exe")
    spell["prefix and trailing dot"] = "\\\\?\\" + str(apps / "python.exe") + "."
    link = junctions(tmp_path / "elsewhere", apps)                       # a junction to the stub's folder
    spell["junction to the folder"] = str(link / "python.exe")
    up = junctions(tmp_path / "local-link", apps.parent.parent)          # a junction to a folder above it
    spell["junction above"] = str(up / "Microsoft" / "WindowsApps" / "python.exe")
    short = _short(apps / "python.exe")
    if short:
        spell["8.3 short name"] = short
        spell["prefix and 8.3"] = "\\\\?\\" + short
    short_link = _short(up / "Microsoft" / "WindowsApps" / "python.exe")
    if short_link:
        spell["8.3 through a junction"] = short_link
    return apps, pybin, spell


@pytest.mark.parametrize("case", ["forward slashes", "dot segment", "dot dot segment", "trailing dot",
                                  "trailing dots and spaces", "mixed slashes and case", "8.3 short name",
                                  "prefix", "dot prefix", "prefix and trailing dot", "prefix and 8.3",
                                  "junction to the folder", "junction above", "8.3 through a junction"])
def test_any_spelling_of_a_stub_given_with_python_is_never_run(tmp_path, junctions, case):
    """The stubs are whoami.exe copies: had the finder run one, its line would say so, not "never run"."""
    _need(11)
    apps, pybin, spell = _stub_spellings(tmp_path, junctions)
    if case not in spell:
        pytest.skip("this volume makes no 8.3 short names")
    found, lines = _run(tmp_path, wanted=spell[case])
    assert found is None, (case, found, lines)
    assert len(lines) == 1 and "never run" in lines[0], lines
    assert "can be used" not in lines[0] and "did not run" not in lines[0], lines


def test_a_registry_key_with_a_stub_spelled_another_way_is_never_run(tmp_path, junctions):
    _need(11)
    apps, pybin, spell = _stub_spellings(tmp_path, junctions)
    hkcu = _view(tmp_path, "hkcu", {f"3.{i}": {"InstallPath": {"ExecutablePath": s}}
                                    for i, s in enumerate(spell.values(), start=20)})
    found, lines = _run(tmp_path, roots=[hkcu], lad=tmp_path / "empty-lad")
    assert found is None, (found, lines)
    assert lines and all("never run" in ln or "same file as above" in ln for ln in lines), lines
    assert sum("never run" in ln for ln in lines) == 2, lines      # two real files: the Store one, the bin one


@pytest.mark.parametrize("given", ["C:\\x<y>\\python.exe", "\\\\?\\C:\\a|b\\python.exe", "C:\\x\"\\python.exe"])
def test_a_path_windows_cannot_read_is_refused_with_a_plain_line(tmp_path, given):
    found, lines = _run(tmp_path, wanted=given)
    assert found is None and len(lines) == 1, lines
    assert lines[0].rstrip().endswith(("not used, Windows can't read that path as a file.",
                                       "not used, the file is not there.")), lines
    assert "Exception" not in lines[0], lines


def test_a_bare_name_with_python_is_refused_and_nothing_is_run(tmp_path, junctions):
    _need(11)
    tools = junctions(tmp_path / "tools", REAL[11])
    for bare in ("python.exe", "python"):
        found, lines = _run(tmp_path, wanted=bare, path_dirs=[tools], cwd=tools)
        assert found is None, (bare, found, lines)
        assert len(lines) == 1 and "Give the full path to python.exe" in lines[0], lines


def test_a_relative_path_with_python_is_resolved_and_the_one_real_path_is_run(tmp_path, junctions):
    _need(11)
    tools = junctions(tmp_path / "tools", REAL[11])
    found, lines = _run(tmp_path, wanted=r".\tools\.\python.exe", cwd=tmp_path)
    assert found and found["Minor"] == 11, (found, lines)
    assert _real(tools / "python.exe") in os.path.normcase(lines[0]) and r"given as .\tools\.\python.exe" in lines[0], lines


def test_the_whole_installer_refuses_a_bare_python_name(tmp_path):
    copy = tmp_path / "BuildBotv2"
    copy.mkdir()
    shutil.copy2(INSTALL, copy / "install.ps1")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("PYTHON", "PYTEST"))}
    env.update(PUFFPAI_NO_PAUSE="1", LOCALAPPDATA=str(tmp_path / "lad"))
    got = subprocess.run([str(PS), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                          str(copy / "install.ps1"), "-Bot", "test1", "-Python", "python.exe"],
                         capture_output=True, text=True, env=env, cwd=str(tmp_path), stdin=subprocess.DEVNULL,
                         timeout=120)
    assert got.returncode == 1, got.stdout + got.stderr
    assert "Setup stopped: Give the full path to python.exe with -Python, not just its name." in got.stdout, got.stdout


def test_a_copy_of_the_install_managers_launcher_is_known_by_its_own_version_info(tmp_path):
    """Get-PythonFile only reads the file (nothing is started): a copy of the real launcher, renamed python.exe,
    in an ordinary folder, is still the launcher."""
    lad = os.environ.get("LOCALAPPDATA", "")
    real = Path(lad) / "Python" / "bin" / "python.exe" if lad else None
    if not real or not real.is_file():
        pytest.skip("no install-manager launcher on this computer")
    copy = tmp_path / "plain folder" / "python.exe"
    copy.parent.mkdir()
    shutil.copyfile(real, copy)
    got, _lines = _run(tmp_path, file=str(copy))
    assert got["Path"] == str(copy) and "install manager's shortcuts are never run" in got["Why"], got


def test_a_real_python_by_any_spelling_is_the_same_one_file(tmp_path, junctions):
    _need(11)
    tools = junctions(tmp_path / "tools", REAL[11])
    for spelling in (str(tools / "python.exe").replace("\\", "/"), str(tools) + r"\.\python.exe",
                     str(tools / "python.exe") + "."):
        got, _lines = _run(tmp_path, file=spelling)
        assert os.path.normcase(got["Path"]) == _real(tools / "python.exe") and got["Why"] == "", (spelling, got)


def test_the_real_store_stub_by_any_spelling_is_known_without_running_it(tmp_path):
    r"""Get-PythonFile only reads (nothing is started): this computer's real Store stub, plain, with a \\?\ prefix
    and as 8.3, is the Store stub."""
    lad = os.environ.get("LOCALAPPDATA", "")
    stub = Path(lad) / "Microsoft" / "WindowsApps" / "python.exe" if lad else None
    if not stub or not os.path.lexists(stub):
        pytest.skip("no Store stub on this computer")
    spellings = [str(stub), PREFIX + str(stub), str(stub).replace("\\", "/")]
    short = _short(stub.parent)
    if short:
        spellings += [short + r"\python.exe", PREFIX + short + r"\python.exe"]
    for s in spellings:
        got, _lines = _run(tmp_path, file=s)
        assert "Microsoft Store stub" in got["Why"], (s, got)
