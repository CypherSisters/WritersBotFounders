import copy
import json
import os
from pathlib import Path

import pytest

import no_real_browser

no_real_browser.install()

# No real Windows Credential Manager or keyring writes in tests, ever.
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"
# D130: the home network is on by default. No Core a test starts, in this process or any it runs, opens a socket on her
# real network or pops a firewall prompt at her: it binds this stand-in, not every interface.
os.environ["BUILDBOT_NETWORK_BIND"] = "127.0.0.2"

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[1]


#: Settings that tell git which repository to use: never passed into a test session (lane I2a part D).
_GIT_LOCATORS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY", "GIT_COMMON_DIR",
                 "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_NAMESPACE", "GIT_PREFIX", "GIT_DISCOVERY_ACROSS_FILESYSTEM")


@pytest.fixture(scope="session", autouse=True)
def git_stays_below_the_session(tmp_path_factory):
    """No git command in a test ever reaches a real repository above it (lane I2a part D). The home folder
    on this computer is itself a git repository, and a test that ran ``git commit`` after a failed ``git
    init`` committed there. ``GIT_CEILING_DIRECTORIES`` is the session temp root's parent (and the
    worktree's parent), added in front of any ceiling the shell already set; ``GIT_DIR`` and the other
    repository settings are taken out for the session."""
    base = tmp_path_factory.getbasetemp()
    before = {k: os.environ.get(k) for k in ("GIT_CEILING_DIRECTORIES", *_GIT_LOCATORS)}
    ceilings = [os.path.realpath(base.parent), os.path.realpath(REPO.parent)]
    if before["GIT_CEILING_DIRECTORIES"]:
        ceilings.append(before["GIT_CEILING_DIRECTORIES"])
    os.environ["GIT_CEILING_DIRECTORIES"] = os.pathsep.join(ceilings)
    for name in _GIT_LOCATORS:
        os.environ.pop(name, None)
    yield os.environ["GIT_CEILING_DIRECTORIES"]
    for name, value in before.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def record_install(core, folder, manifest, origin, **extra):
    """A package dropped into *folder* by hand, recorded as the Package Manager records an install: its
    Installs folder and a Registry row with the sha256 of its manifest file (lane I2a part E: the Core reads
    an installed package's manifest only through ``packages.manifest.installed``, by that sha)."""
    from puffpai_core.packages import manifest as _manifest_mod
    from puffpai_core.registry import row_from_manifest as _row
    _data, _path, sha = _manifest_mod.load_with_sha(folder)
    core.installs.set(manifest["id"], folder, False, str(folder))
    row = _row(manifest, origin, manifest_sha=sha, **extra)
    core.registry.add(row)
    return row


def git_init(repo, git="git", pre=()):
    """``git init -q`` in *repo* for a test, or the test fails at once (never a ``git commit`` that walks up
    to some other repository). *repo* must then be the top of its own repository. *pre*: options before
    the command (``-c core.longpaths=true``)."""
    import subprocess
    got = subprocess.run([git, *pre, "-C", str(repo), "init", "-q"], capture_output=True, text=True, check=False)
    if got.returncode != 0:
        pytest.fail(f"git init failed in {repo} (exit {got.returncode}): {got.stderr.strip()[:300]}", pytrace=False)
    top = subprocess.run([git, *pre, "-C", str(repo), "rev-parse", "--show-toplevel"], capture_output=True,
                         text=True, check=False)
    if top.returncode != 0 or (os.path.normcase(os.path.realpath(top.stdout.strip()))
                               != os.path.normcase(os.path.realpath(repo))):
        pytest.fail(f"git init in {repo} did not make a repository of its own there "
                    f"(git sees {top.stdout.strip() or top.stderr.strip()[:200]!r})", pytrace=False)
    return got


def manifest_sha(folder, adopted=False):
    """The sha256 of the manifest file Steward reads in *folder*: what the install Yes records in the
    Registry row (``manifest_sha``). For tests that add an installed row by hand."""
    from puffpai_core.packages import manifest as manifest_mod
    return manifest_mod.load_with_sha(folder, adopted=adopted)[2]


#: The real Core's fixed ports: the Core (8420), the Bridge proxy (8421), the pages address (8422).
FIXED_PORTS = frozenset({8420, 8421, 8422})


@pytest.fixture(autouse=True)
def _no_real_window(monkeypatch):
    """No test starts a real PuffPAI window or makes shortcuts on her real desktop or Start menu (D81). A test gives
    its own folders and stubs (tests/test_window_shell.py)."""
    from puffpai_core import window

    def refuse(*_a, **_k):
        raise AssertionError("a test tried to start a real window or use her real desktop folder")
    monkeypatch.setattr(window, "_spawn", refuse)
    monkeypatch.setattr(window, "_shell_folders", refuse)
    from puffpai_core import window_app
    real_start = window_app._start

    def start(path, args):                                 # D144: a test's own small program may run; a browser never
        if Path(path).name in ("chromium", "chromium-browser", "chrome", "google-chrome", "google-chrome-stable", "Google Chrome"):
            refuse()
        return real_start(path, args)
    monkeypatch.setattr(window_app, "_start", start)
    # A real console on her taskbar: a test stubs this, or stubs Popen to read what it would start.
    import subprocess

    from puffpai_core import cli
    real_launch, real_popen = cli._launch_serve, subprocess.Popen

    def launch(argv, title):
        if subprocess.Popen is real_popen:
            raise AssertionError("a test tried to start a real serve in a console")
        return real_launch(argv, title)
    monkeypatch.setattr(cli, "_launch_serve", launch)


@pytest.fixture(autouse=True)
def no_fixed_core_ports(monkeypatch):
    """No test binds 8420, 8421 or 8422 (orchestrator rule 10-22): two suites at once (the merge chat on main
    and a lane on its branch) would collide or answer each other. Tests use port 0 (the OS picks) and read
    the real bound port back. A bind to a fixed port is refused here AND fails the test, even when the code
    under test catches the error (a busy proxy port is a card, not a crash)."""
    import socket
    hits = []
    real = socket.socket.bind

    def bind(self, address, *rest):
        port = address[1] if isinstance(address, tuple) and len(address) >= 2 else None
        if port in FIXED_PORTS:
            hits.append(address)
            raise OSError(10013, f"tests never bind port {port} (conftest guard): use port 0")
        return real(self, address, *rest)
    monkeypatch.setattr(socket.socket, "bind", bind)
    yield hits
    if hits:
        pytest.fail(f"this test tried to bind a fixed Core port {sorted(set(a[1] for a in hits))}: use port 0")


@pytest.fixture(autouse=True)
def no_real_ollama_found(monkeypatch):
    """The wizard's scan never finds this computer's real Ollama in a test (it asks 127.0.0.1 for a version);
    tests of the look itself pass their own ``ask``."""
    from puffpai_core.zone2 import settings as model_settings
    monkeypatch.setattr(model_settings, "_ask_version", lambda url, timeout_s=1.5: False)


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    """Every test gets its own bot folder; nothing touches %LOCALAPPDATA%."""
    path = tmp_path / "state"
    monkeypatch.setenv("PUFFPAI_STATE", str(path))
    return path


@pytest.fixture
def freshtable():
    return json.loads((FIXTURES / "freshtable" / "puffpai.json").read_text(encoding="utf-8"))


@pytest.fixture
def make(freshtable):
    """A FreshTable manifest with changes: make(drop=[...], **set)."""
    def _make(drop=(), **changes):
        m = copy.deepcopy(freshtable)
        for key in drop:
            m.pop(key, None)
        m.update(changes)
        return m
    return _make


@pytest.fixture(autouse=True)
def no_real_os_key_stores(monkeypatch):
    """No test ever touches Windows Credential Manager, a keyring, real DPAPI or the real file wrap's key.

    Any keyring import gets the failing backend; any real machine wrap call raises.
    Tests that need the unattended tier pass a fake (``machine=FakeMachine()``).
    Only tests/test_d132_machine_file.py turns the file wrap back on, with its own HOME.
    """
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    from puffpai_core import machine_file
    from puffpai_core.zone1 import machine_key

    def refuse(*_a, **_k):
        raise AssertionError("a test called real DPAPI; pass a fake machine key instead")

    monkeypatch.setattr(machine_key, "protect", refuse)
    monkeypatch.setattr(machine_key, "unprotect", refuse)
    monkeypatch.setattr(machine_file, "_wrap_key", refuse)


@pytest.fixture(autouse=True)
def drop_archivists():
    """Lock and forget every Archivist a test made through the Core API (stops its idle-lock thread), and
    unbind the gate, so no test sees an Archivist another test left behind."""
    yield
    from puffpai_core.zone1 import archivist, gate
    with archivist._BY_LOCK:
        made = list(archivist._BY_STATE.values())
        archivist._BY_STATE.clear()
    gate.bind(None)
    for a in made:
        a.lock("owner")


class FakeMachine:
    """A test stand-in for the machine wrap (DPAPI). Never calls Windows. Deterministic, so a key wrapped by
    one instance unwraps with another (lane L4's anchor makes a new one per bot folder); ``ok=False`` is a
    computer with no machine key."""

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok

    def available(self) -> bool:
        return self.ok

    def protect(self, data: bytes) -> bytes:
        return b"fake-wrap:" + bytes(b ^ 0x5A for b in data)

    def unprotect(self, data: bytes) -> bytes:
        if not data.startswith(b"fake-wrap:"):
            raise ValueError("not a fake wrap")
        return bytes(b ^ 0x5A for b in data[len(b"fake-wrap:"):])


@pytest.fixture(autouse=True)
def fake_machine_key(monkeypatch):
    """Lane L4's tamper anchors use a fake machine key; a real DPAPI call in the suite fails loudly."""
    from puffpai_core import anchor

    def no_real_dpapi(*_a, **_k):
        raise AssertionError("a test made a real DPAPI call")

    monkeypatch.setattr(anchor, "_dpapi", no_real_dpapi)
    monkeypatch.setattr(anchor, "default_machine", FakeMachine)
    return FakeMachine

# -- lane L1: packages, Steward, Caretaker ------------------------------------
import shutil as _shutil
import sys as _sys
import time as _time
import types as _types

HELLO = FIXTURES / "hello-tool"


def _double(monkeypatch, module: str, **functions):
    """A stand-in for another lane's module, in tests only (seams.find imports it by name)."""
    monkeypatch.setitem(_sys.modules, f"puffpai_core.{module}", _types.SimpleNamespace(**functions))


def drawbridge_double(monkeypatch, check_package):
    """Replace the Drawbridge seam with ``check_package(path, manifest) -> verdict | None`` (tests only).
    A verdict with no ``tree_sha`` gets lane L4's real hash of the folder, so it is bound to it."""
    from puffpai_core import seams
    from puffpai_core.zone3.drawbridge import tree_sha

    def check(folder, manifest, *, origin="local", drawbridge=None):
        got = check_package(Path(folder), manifest)
        if isinstance(got, dict) and "tree_sha" not in got:
            got = {**got, "tree_sha": tree_sha(folder)}
        return got
    monkeypatch.setattr(seams, "drawbridge_check", check)


@pytest.fixture
def drawbridge(monkeypatch):
    """Lane L4's Drawbridge, as a test double that passes everything it sees (and records it)."""
    seen = []

    def check_package(path, manifest):
        seen.append((str(path), manifest["id"]))
        return {"verdict": "allow", "reasons": [], "rows": [], "line": "passed every check."}
    drawbridge_double(monkeypatch, check_package)
    return seen


@pytest.fixture
def no_brain(monkeypatch):
    """No sample ask (``puffpai check`` item 12 skipped), so no test asks a real local model."""
    import puffpai_core.zone2.brain as brain
    monkeypatch.delattr(brain, "sample_ask")


@pytest.fixture
def archivist(monkeypatch):
    """Lane L3's real Archivist, with its real names; only ``Archivist.verify_pin(self, pin)`` is
    replaced, so the PIN is 4321 without a vault set up."""
    from puffpai_core.zone1.archivist import Archivist
    monkeypatch.setattr(Archivist, "verify_pin", lambda self, pin: pin == "4321")


@pytest.fixture
def core(state):
    from puffpai_core.api import create_app
    app = create_app(state, port=18419)
    c = app.extensions["puffpai"]
    c.app = app
    c.proxy_port = 0                               # start_core's Bridge proxy on a free port, never 8421
    yield c
    c.caretaker.stop()
    c.steward.stop_all(remember=False)


@pytest.fixture
def hello_copy(tmp_path):
    """A writable copy of tests/fixtures/hello-tool."""
    dst = tmp_path / "src" / "hello-tool"
    _shutil.copytree(HELLO, dst)
    return dst


def wait_for(fn, timeout_s=60.0, every_s=0.2):
    end = _time.monotonic() + timeout_s
    while _time.monotonic() < end:
        got = fn()
        if got:
            return got
        _time.sleep(every_s)
    return fn()


def yes(core, staged, factor="tap", pin=None, timeout_s=300):
    from puffpai_core.consent import card_sha
    core.consent.answer(staged["consent_id"], "yes", factor, pin, by="test", card_sha=card_sha(staged["card"]))
    return core.packages.wait(staged["consent_id"], timeout_s=timeout_s)

# No real Windows Credential Manager or keyring writes in tests, ever (orchestrator rule 10-22).
import os as _os
_os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"


# -- lane I7: no machine changes from a test ----------------------------------------------------------------
# Why: on 10-22 a verifier run with LOCALAPPDATA pointed at its scratch started the py launcher, the Python
# install manager installed a Python into that scratch and registered it in the owner's real HKCU, and that
# key outlived the folder. So a test may read the registry but never write it, and may never start a program
# that installs things or edits the registry, by any spelling of its name (tests/machine_guard.py says how).
# Installed now, while conftest is imported: before any test module, so a ``from winreg import`` in one gets
# the guarded name. A refusal also fails the test, even if the code under test caught it.
# Known gaps (lane I7, not chased further): ctypes (CreateProcessW, RegSetValueExW, ShellExecuteW) and
# multiprocessing; child processes, which have no guard of their own; names a shell builds at run time
# (cmd %VAR% expansion, "set x=py & call %x%", PowerShell string building such as & ('p'+'y'), iex,
# Invoke-Expression, -Command - from stdin); a hard link or copy of a refused program under a harmless name
# whose version info was removed; a winreg write name held in a variable (getattr) or built in exec; and in
# the floor scan, anything computed at run time.
import machine_guard as _machine_guard  # noqa: E402
from machine_guard import MachineChangeInTest  # noqa: E402,F401

_machine_guard.install()
_MACHINE_HITS = _machine_guard.HITS
_WINREG_WRITES = _machine_guard.WINREG_WRITES


def _refused_program(args, executable, shell, env, cwd):
    """Why this start would be refused (tests/machine_guard.py), or None. For the guard's own tests."""
    return _machine_guard.why_start(args, executable, shell, env, cwd)


@pytest.fixture(scope="session", autouse=True)
def no_machine_changes():
    """Session-wide: also rebinds any winreg write a module took with ``from winreg import`` before the guard."""
    _machine_guard.install()
    _machine_guard.sweep()
    yield


@pytest.fixture(autouse=True)
def no_machine_change_was_tried():
    """Fails the test that tried one, even when the code under test caught the refusal."""
    before = len(_MACHINE_HITS)
    yield
    tried = _MACHINE_HITS[before:]
    if tried:
        pytest.fail("this test tried to change the computer: " + "; ".join(tried), pytrace=False)
