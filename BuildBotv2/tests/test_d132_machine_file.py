"""D132: the machine wrap off Windows, a file wrap with the same honest limit as DPAPI CurrentUser.

The suite's guard blocks the real thing in every other test. Only the ``wrap`` fixture here turns it back on, with
HOME and XDG_DATA_HOME inside the test's own folder, so the real home is never touched. Keys here are made up on the
spot. Nothing here calls DPAPI.
"""

import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import textwrap
import threading
from types import SimpleNamespace

import pytest

from puffpai_core import anchor, machine_file, paths
from puffpai_core.bus import Bus
from puffpai_core.zone1 import machine_key
from puffpai_core.zone1.archivist import Archivist
from puffpai_core.zone1.vault import Refused
from z1kit import PIN, FakeClock

# The real functions, taken at import: the suite's guards replace them once fixtures run.
REAL_WRAP_KEY = machine_file._wrap_key
REAL_PROTECT = machine_key.protect
REAL_UNPROTECT = machine_key.unprotect
REAL_DEFAULT_MACHINE = anchor.default_machine

posix = pytest.mark.skipif(os.name != "posix", reason="the file wrap is used off Windows only")
SIZE = 32


@pytest.fixture
def wrap(tmp_path, monkeypatch):
    """The real file wrap, in a home of this test's own. Returns the folder that holds machine.key."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_DATA_HOME", str(home / "xdg"))
    monkeypatch.setattr(machine_file, "_wrap_key", REAL_WRAP_KEY)
    monkeypatch.setattr(machine_key, "protect", REAL_PROTECT)
    monkeypatch.setattr(machine_key, "unprotect", REAL_UNPROTECT)
    monkeypatch.setattr(anchor, "default_machine", REAL_DEFAULT_MACHINE)
    root = paths.data_root()
    assert home in root.parents
    return root


def _key_file(root, data=None, mode=0o600):
    """A machine.key made by hand, to test what the wrap does with one it finds."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / machine_file.KEY_FILE
    path.write_bytes(secrets.token_bytes(SIZE) if data is None else data)
    path.chmod(mode)
    return path


def _mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


# -- the words, on every system --------------------------------------------------------------------------------------
def test_the_card_says_what_is_true_here():
    if sys.platform == "win32":
        assert machine_key.CARD_LINE == ("This key works after a restart without your PIN. "
                                         "Anyone who can sign in to your Windows account could use it.")
    else:
        assert machine_key.CARD_LINE == ("This key works after a restart without your PIN. "
                                         "Anyone who can sign in to your account on this computer could use it.")


def test_a_refused_tier_never_says_it_needs_windows():
    assert "Windows" not in machine_key.NOT_AVAILABLE_LINE
    assert machine_key.not_available_line(object()) == machine_key.NOT_AVAILABLE_LINE
    assert machine_key.not_available_line(SimpleNamespace(reason="The why.")).endswith(" The why.")


def test_the_guard_blocks_the_real_key_in_every_other_test():
    with pytest.raises(AssertionError):
        machine_file.available()
    with pytest.raises(AssertionError):
        machine_file.protect(b"x", b"purpose")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_stays_on_dpapi_and_never_makes_the_file(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(machine_file, "_wrap_key", REAL_WRAP_KEY)
    assert isinstance(REAL_DEFAULT_MACHINE(), anchor.Dpapi)     # its class only: no DPAPI call is made
    assert machine_key.available() is True
    assert machine_file.available() is False                    # a plain reason, nothing made
    assert list(tmp_path.iterdir()) == []


# -- the wrap --------------------------------------------------------------------------------------------------------
@posix
def test_round_trip_and_a_fresh_nonce(wrap):
    data = secrets.token_bytes(SIZE)
    one, two = machine_file.protect(data, b"p"), machine_file.protect(data, b"p")
    assert one != two and one.startswith(b"pmf1:") and data not in one
    assert machine_file.unprotect(one, b"p") == data
    assert machine_file.unprotect(two, b"p") == data


@posix
def test_made_once_private_and_in_the_folder_above_any_bot(wrap, state):
    assert machine_file.key_path() == wrap / "machine.key"
    assert machine_file.available() is True
    path = wrap / "machine.key"
    first = path.read_bytes()
    assert len(first) == SIZE
    assert _mode(path) == 0o600 and _mode(wrap) == 0o700
    assert machine_file.available() is True
    assert path.read_bytes() == first
    assert wrap not in state.parents and state not in wrap.parents


@posix
def test_the_two_purposes_cant_read_each_other(wrap):
    an, z1 = anchor.default_machine(), machine_key.Dpapi()
    assert isinstance(an, machine_file.FileWrap)
    data = secrets.token_bytes(SIZE)
    assert an.unprotect(an.protect(data)) == data
    assert z1.unprotect(z1.protect(data)) == data
    with pytest.raises(anchor.AnchorUnavailable):
        an.unprotect(z1.protect(data))
    with pytest.raises(machine_key.MachineKeyUnavailable):
        z1.unprotect(an.protect(data))


@posix
@pytest.mark.parametrize("spoil", [lambda b: b[:-1] + bytes([b[-1] ^ 1]), lambda b: b[:10], lambda b: b"pmf2:" + b[5:],
                                   lambda b: b"", lambda b: b[:5] + bytes([b[5] ^ 1]) + b[6:]])
def test_a_damaged_blob_is_refused(wrap, spoil):
    blob = machine_key.protect(b"x" * SIZE)
    with pytest.raises(machine_key.MachineKeyUnavailable):
        machine_key.unprotect(spoil(blob))


@posix
def test_a_wrong_key_is_refused_and_the_file_is_not_replaced(wrap):
    blob = machine_key.protect(b"x" * SIZE)
    other = secrets.token_bytes(SIZE)
    path = _key_file(wrap, other)
    with pytest.raises(machine_key.MachineKeyUnavailable) as e:
        machine_key.unprotect(blob)
    assert "Windows" not in str(e.value)
    assert path.read_bytes() == other


# -- what it finds on disk -------------------------------------------------------------------------------------------
@posix
@pytest.mark.parametrize("dangling", [False, True])
def test_a_link_is_refused_and_left_alone(wrap, tmp_path, dangling):
    wrap.mkdir(parents=True, mode=0o700)
    target = tmp_path / "elsewhere.key"
    if not dangling:
        target.write_bytes(secrets.token_bytes(SIZE))
        target.chmod(0o644)
    (wrap / "machine.key").symlink_to(target)
    assert machine_file.available() is False
    assert "is a link" in machine_file.why_not()
    assert (wrap / "machine.key").is_symlink()
    assert target.exists() is not dangling                      # a dangling link is never followed to make a file
    if not dangling:
        assert _mode(target) == 0o644                           # and a real target is never touched


@posix
@pytest.mark.parametrize("size", [0, 31, 33, 4096])
def test_a_wrong_size_is_refused_and_the_file_is_not_replaced(wrap, size):
    data = secrets.token_bytes(size)
    path = _key_file(wrap, data)
    assert machine_file.available() is False
    assert "wrong size" in machine_file.why_not()
    assert path.read_bytes() == data
    with pytest.raises(machine_key.MachineKeyUnavailable):
        machine_key.protect(b"x")


@posix
def test_a_folder_or_a_pipe_in_its_place_is_refused_and_never_hangs(wrap):
    wrap.mkdir(parents=True, mode=0o700)
    path = wrap / "machine.key"
    path.mkdir()
    assert "not a regular file" in machine_file.why_not()
    path.rmdir()
    os.mkfifo(path)
    assert "not a regular file" in machine_file.why_not()
    assert stat.S_ISFIFO(os.lstat(path).st_mode)


def _owned_by_another(monkeypatch, is_it):
    real = os.fstat

    def fstat(fd):
        st = real(fd)
        uid = st.st_uid + 1 if is_it(st.st_mode) else st.st_uid
        return SimpleNamespace(st_mode=st.st_mode, st_uid=uid, st_size=st.st_size)
    monkeypatch.setattr(machine_file.os, "fstat", fstat)


@posix
def test_a_file_of_another_user_is_refused_and_not_replaced(wrap, monkeypatch):
    data = secrets.token_bytes(SIZE)
    path = _key_file(wrap, data, mode=0o644)
    _owned_by_another(monkeypatch, stat.S_ISREG)
    assert "belongs to another user" in machine_file.why_not()
    assert path.read_bytes() == data
    assert _mode(path) == 0o644                                 # refused first: never chmodded


@posix
def test_a_folder_of_another_user_is_refused(wrap, monkeypatch):
    wrap.mkdir(parents=True, mode=0o755)
    wrap.chmod(0o755)
    _owned_by_another(monkeypatch, stat.S_ISDIR)
    assert "belongs to another user" in machine_file.why_not()
    assert not (wrap / "machine.key").exists()
    assert _mode(wrap) == 0o755


@posix
@pytest.mark.parametrize("loose", [0o644, 0o640, 0o604, 0o666, 0o777])
def test_a_loose_file_is_tightened_not_refused(wrap, loose):
    data = secrets.token_bytes(SIZE)
    path = _key_file(wrap, data, mode=loose)
    assert machine_file.why_not() == ""
    assert _mode(path) == loose & 0o700
    assert path.read_bytes() == data


@posix
def test_a_disk_that_ignores_modes_means_not_available(wrap, monkeypatch):
    path = _key_file(wrap, mode=0o644)
    monkeypatch.setattr(machine_file.os, "fchmod", lambda *_a: None)
    assert "could not be made private" in machine_file.why_not()
    assert path.exists()


@posix
def test_a_folder_a_bot_made_first_is_tightened(wrap):
    wrap.mkdir(parents=True)
    wrap.chmod(0o755)
    assert machine_file.available() is True
    assert _mode(wrap) == 0o700


@posix
def test_the_folder_may_be_reached_through_a_link_of_her_own(wrap, tmp_path):
    bigger = tmp_path / "bigger-disk"
    bigger.mkdir()
    wrap.parent.mkdir(parents=True, exist_ok=True)
    wrap.symlink_to(bigger)
    assert machine_file.available() is True
    assert (bigger / "machine.key").is_file()


@posix
def test_a_second_hard_link_left_by_a_crash_still_works(wrap):
    path = _key_file(wrap)
    os.link(path, wrap / "machine.key.deadbeef.tmp")
    assert machine_file.available() is True


@posix
def test_a_disk_that_cant_link_means_not_available_and_leaves_nothing(wrap, monkeypatch):
    def no_links(*_a, **_k):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(machine_file.os, "link", no_links)
    assert "could not be made" in machine_file.why_not()
    assert list(wrap.iterdir()) == []


@posix
def test_threads_making_it_at_once_get_one_key(wrap):
    for _round in range(10):
        shutil.rmtree(wrap, ignore_errors=True)
        got, gate = [], threading.Barrier(8)

        def go():
            gate.wait(30)
            got.append(machine_file._wrap_key())
        threads = [threading.Thread(target=go) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert len(got) == 8 and len(set(got)) == 1
        assert [p.name for p in wrap.iterdir()] == ["machine.key"]


RACE = textwrap.dedent('''
    import hashlib, pathlib, sys, time
    from puffpai_core import machine_file
    go, end = pathlib.Path(sys.argv[1]), time.time() + 60
    print("ready", flush=True)
    while not go.exists() and time.time() < end:
        pass
    print(hashlib.sha256(machine_file._wrap_key()).hexdigest(), flush=True)
''')


@posix
def test_two_cores_starting_at_once_get_one_key(wrap, tmp_path):
    script, go = tmp_path / "race.py", tmp_path / "go"
    script.write_text(RACE, encoding="utf-8")
    procs = [subprocess.Popen([sys.executable, "-I", str(script), str(go)], stdout=subprocess.PIPE, text=True)
             for _ in range(6)]
    try:
        for p in procs:
            assert p.stdout.readline().strip() == "ready"
        go.write_text("", encoding="utf-8")
        digests = {p.communicate(timeout=120)[0].strip() for p in procs}
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
                p.wait(30)
    assert digests == {hashlib.sha256((wrap / "machine.key").read_bytes()).hexdigest()}
    assert [p.name for p in wrap.iterdir()] == ["machine.key"]


# -- the anchor ------------------------------------------------------------------------------------------------------
@posix
def test_the_anchor_is_available_and_an_edited_mac_fails(wrap, tmp_path):
    a = anchor.Anchor(tmp_path / "settings" / "anchor.key")
    assert a.available and a.problem == ""
    mac = a.mac("policy", "body")
    assert a.check("policy", "body", mac)
    assert not a.check("policy", "body", mac[:-1] + ("0" if mac[-1] != "0" else "1"))
    assert not a.check("policy", "other body", mac)
    again = anchor.Anchor(tmp_path / "settings" / "anchor.key")
    assert again.check("policy", "body", mac)


@posix
def test_a_damaged_anchor_key_is_never_replaced(wrap, tmp_path):
    path = tmp_path / "settings" / "anchor.key"
    assert anchor.Anchor(path).available
    bad = path.read_bytes()[:-1] + bytes([path.read_bytes()[-1] ^ 1])
    path.write_bytes(bad)
    a = anchor.Anchor(path)
    assert not a.available and a.problem == "machine_key_unreadable"
    assert path.read_bytes() == bad
    assert a.no_key_line().startswith(anchor.NO_KEY_LINE + " ")
    assert not a.check("policy", "body", "00")


@posix
def test_a_lost_wrap_key_closes_and_never_replaces_the_anchor_key(wrap, tmp_path):
    path = tmp_path / "settings" / "anchor.key"
    assert anchor.Anchor(path).available
    held = path.read_bytes()
    (wrap / "machine.key").unlink()
    a = anchor.Anchor(path)
    assert not a.available and path.read_bytes() == held


@posix
def test_no_usable_wrap_key_closes_with_the_plain_reason(wrap, tmp_path):
    _key_file(wrap, b"short")
    a = anchor.Anchor(tmp_path / "settings" / "anchor.key")
    assert not a.available and a.problem == "no_machine_key"
    assert a.no_key_line().startswith(anchor.NO_KEY_LINE + " ") and "wrong size" in a.no_key_line()
    assert not (tmp_path / "settings" / "anchor.key").exists()
    with pytest.raises(anchor.AnchorUnavailable):
        a.mac("policy", "body")


@posix
def test_a_bot_folder_copied_without_the_wrap_key_stays_closed(wrap, tmp_path, monkeypatch):
    path = tmp_path / "settings" / "anchor.key"
    assert anchor.Anchor(path).available
    monkeypatch.setenv("PUFFPAI_HOME", str(tmp_path / "another-computer"))     # moves the wrap key on every system
    a = anchor.Anchor(path)
    assert not a.available and a.problem == "machine_key_unreadable"


# -- the unattended tier ---------------------------------------------------------------------------------------------
def _archivist(state):
    return Archivist(state, bus=Bus(), clock=FakeClock(), machine=machine_key.Dpapi())


@posix
def test_a_ticked_key_works_after_a_restart_and_waits_for_the_pin_without_the_wrap_key(wrap, state):
    a = _archivist(state)
    a.setup(PIN)
    value = secrets.token_hex(16)
    a.put_secret("K", value, unattended=True)
    a.lock()
    b = _archivist(state)
    assert b.inject("p", ["K"]) == {"K": value}
    (wrap / "machine.key").write_bytes(secrets.token_bytes(SIZE))
    c = _archivist(state)
    assert c.inject("p", ["K"]) == {}                           # closed, no error, and the key is still in the vault
    c.unlock("pin", PIN)
    assert bytes(c.vault.get("K")).decode() == value
    c.lock()


@posix
def test_ticking_with_no_usable_wrap_key_is_a_plain_no(wrap, state):
    a = _archivist(state)
    a.setup(PIN)
    a.put_secret("K", "value")
    _key_file(wrap, b"short")
    with pytest.raises(Refused) as e:
        a.vault.set_unattended("K", True)
    assert str(e.value).startswith(machine_key.NOT_AVAILABLE_LINE) and "wrong size" in str(e.value)
    assert "Windows" not in str(e.value)
    a.lock()


# -- a real Core, restarted ------------------------------------------------------------------------------------------
CORE = textwrap.dedent('''
    import json, os, sys
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    z = wire_core(create_app(sys.argv[1])).zone3
    if sys.argv[2] == "change":
        z.bridge.set_switch("toolsmith:search", True)
    p = z.bridge.posture()
    print(json.dumps({"available": z.anchor.available, "damaged": p["damaged"], "version": p["version"], "sha": p["sha"],
                      "switch": p["switches"].get("toolsmith:search"), "cards": [c["kind"] for c in z.bridge.cards]}),
          flush=True)
    os._exit(0)
''')


def _core(script, state, what):
    done = subprocess.run([sys.executable, "-I", str(script), str(state), what], capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@posix
def test_a_real_core_restart_keeps_the_anchor(wrap, state, tmp_path):
    script = tmp_path / "core_once.py"
    script.write_text(CORE, encoding="utf-8")
    first = _core(script, state, "change")
    assert first["available"] and not first["damaged"] and first["switch"] is True
    assert "no_machine_key" not in first["cards"]
    wrap_key, anchor_key = (wrap / "machine.key").read_bytes(), (state / "settings" / "anchor.key").read_bytes()

    second = _core(script, state, "read")
    assert second["available"] and not second["damaged"]
    assert (second["version"], second["sha"], second["switch"]) == (first["version"], first["sha"], True)
    assert not {"no_machine_key", "bridge_damaged"} & set(second["cards"])
    assert (wrap / "machine.key").read_bytes() == wrap_key
    assert (state / "settings" / "anchor.key").read_bytes() == anchor_key

    policy = state / "settings" / "bridge" / "policy.json"
    raw = json.loads(policy.read_text(encoding="utf-8"))
    raw["mac"] = raw["mac"][:-1] + ("0" if raw["mac"][-1] != "0" else "1")
    policy.write_text(json.dumps(raw), encoding="utf-8")
    third = _core(script, state, "read")
    assert third["available"] and third["damaged"] and "bridge_damaged" in third["cards"]
    assert (wrap / "machine.key").read_bytes() == wrap_key
