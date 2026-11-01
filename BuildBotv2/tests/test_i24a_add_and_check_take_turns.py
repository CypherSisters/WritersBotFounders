"""D112: the Core's start-up check and the add's move of the try folder take turns.

The bug in the owner's live test: a check of a running try was still going when the add stopped the app and moved
the folder aside. It took the Core's own move for a change by the try ("changed Python's start-up files"), then
deleted the moved folder during the Package Manager's copy, so the add failed with "No .buildbot/puffpai.json".

These tests hold one check back until the add has moved the folder (or for a short time, when the add rightly
waits for it). Nothing here is one app's special case: a static page, a Python app with a real venv and a Node app
with node_modules take the same path.

The later tests hold what the add waits for: every check and command in flight reaches its verdict first, none of
them waits for another, and no try with a verdict against it is handed over. Compose tests use a stand-in for
``docker compose down``; nothing here runs Docker.
"""

import threading
import time
from pathlib import Path

import pytest

from test_d61_allowlist import _checked, needs_jobs
from test_i2a_part_c import NODE, RealPackages, SERVE, _adopted_manifest, needs_node
from toolsmith_fakes import World, make_venv, script
from puffpai_core.packages import manifest as manifest_mod
from puffpai_core.zone3.toolsmith.errors import Refused, TryEnded
from puffpai_core.zone3.toolsmith_host import allowlist
from puffpai_core.zone3.toolsmith_host import trials as trials_mod
from puffpai_core.zone3.toolsmith_host.trials import CORE_HOOK_LINE, TRY_HOOK_LINE

HELD_S = 2.0        # how long a check waits for the move; with the fix the add waits for the check instead


@pytest.fixture
def make_world(tmp_path, state):
    made = []

    def make(files):
        repo = tmp_path / "repo"
        for rel, text in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_text(text, encoding="utf-8")
        ports = RealPackages(tmp_path)
        w = World(tmp_path, state, ports=ports)
        ports.attach(w.core, w.registry, state)
        w.core.packages.start_after_install = False
        ports.fixture_repo = repo
        made.append(w)
        return w
    yield make
    for w in made:
        w.host.stop(timeout=10)
        w.core.steward.stop_all(remember=False)


class HeldCheck:
    """One other job's step: its start-up check of this try is running, and is slow to reach the try folder."""

    def __init__(self, w, rec, monkeypatch, on_held=None):
        self.trials, self.rec, self.on_held = w.host.trials, rec, on_held
        self.began, self.moved, self.result, self.cards = threading.Event(), threading.Event(), None, []
        real_guard, real_aside = self.trials._new_guard, self.trials._move_aside
        card = w.host.card
        monkeypatch.setattr(w.host, "card", lambda c: (self.cards.append(c), card(c))[1])

        def new_guard(folder):
            guard = real_guard(folder)
            if threading.current_thread() is self.thread:
                inner = guard._snapshot

                def snapshot():
                    self.began.set()
                    if self.on_held:
                        self.on_held()
                    self.moved.wait(HELD_S)
                    return inner()
                guard._snapshot = snapshot
            return guard

        def move_aside(record):
            got = real_aside(record)
            record.folder = got                     # the add's next line, so the check ends what the add holds
            self.moved.set()
            self.thread.join(30)                    # its verdict lands before the Package Manager's copy
            return got
        monkeypatch.setattr(self.trials, "_new_guard", new_guard)
        monkeypatch.setattr(self.trials, "_move_aside", move_aside)
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        self.result = self.trials.rescan(self.rec, force=True)

    def start(self):
        self.thread.start()
        assert self.began.wait(30)


def _assert_added(w, link, try_id, held, manifest):
    got = link.try_add(try_id, "local", manifest)
    held.thread.join(30)
    assert got.get("ok") and got.get("waiting"), got
    assert [c for c in held.cards if c.get("kind") == "try_hook_changed"] == []
    assert held.result == {}                                # a clean check, not a false alarm
    staged = w.core.packages._pending[got["consent_id"]].folder
    assert manifest_mod.load(staged, adopted=True)[0] == manifest
    return staged


@needs_jobs
def test_python_app_with_a_real_venv_is_not_lost_when_a_check_is_running(make_world, monkeypatch):
    w = make_world({"index.html": "<!doctype html><title>Maps</title>\n", "README.md": "# Maps\n"})
    _b, link, try_id, rec = _checked(w)
    assert link.try_run(try_id, {"program": "python", "args": ["-m", "venv", ".venv"]})["ok"]   # as the live app did
    assert any(p.endswith(".pth") for p in rec.base["try"]), "a venv's own .pth is in the baseline, not an alarm"
    assert script(link, try_id, "serve.py", SERVE, start=True)["ok"]
    held = HeldCheck(w, rec, monkeypatch)
    held.start()
    staged = _assert_added(w, link, try_id, held, _adopted_manifest(rec.pkg_id))
    assert (staged / "index.html").is_file() and not (staged / ".venv").exists()


@needs_jobs
def test_a_plain_static_site_goes_the_same_way(make_world, monkeypatch):
    w = make_world({"index.html": "<!doctype html><title>Recipes</title>\n", "css/site.css": "p {}\n"})
    _b, link, try_id, rec = _checked(w)
    make_venv(link, try_id)
    assert script(link, try_id, "serve.py", SERVE, start=True)["ok"]
    held = HeldCheck(w, rec, monkeypatch)
    held.start()
    staged = _assert_added(w, link, try_id, held, _adopted_manifest(rec.pkg_id))
    assert (staged / "css" / "site.css").is_file()


@needs_jobs
@needs_node
def test_a_node_app_with_node_modules_goes_the_same_way(make_world, monkeypatch):
    server = "require('http').createServer((q, s) => s.end('ok')).listen(process.env.PORT || 0, '127.0.0.1');\n"
    w = make_world({"package.json": '{"name": "invoices", "version": "1.0.0"}\n', "server.js": server,
                    "node_modules/left/index.js": "module.exports = 1;\n"})
    _b, link, try_id, rec = _checked(w)
    assert link.try_start(try_id, {"program": "node", "args": ["server.js"]})["ok"]
    held = HeldCheck(w, rec, monkeypatch)
    held.start()
    manifest = _adopted_manifest(rec.pkg_id, python=False, start=[NODE, "server.js"])
    staged = _assert_added(w, link, try_id, held, manifest)
    assert (staged / "server.js").is_file() and not (staged / "node_modules").exists()


@needs_jobs
def test_a_real_change_found_by_that_check_still_ends_the_try_and_nothing_is_added(make_world, monkeypatch):
    w = make_world({"index.html": "<!doctype html><title>Birds</title>\n"})
    _b, link, try_id, rec = _checked(w)
    make_venv(link, try_id)
    assert script(link, try_id, "serve.py", SERVE, start=True)["ok"]
    folder = Path(rec.folder)
    hook = folder / ".venv" / "Lib" / "site-packages" / "late.pth"
    held = HeldCheck(w, rec, monkeypatch, on_held=lambda: hook.write_text("import os\n", encoding="utf-8"))
    added = []
    real_add = w.ports.packages_add
    monkeypatch.setattr(w.ports, "packages_add", lambda *a, **k: (added.append(a), real_add(*a, **k))[1])
    held.start()
    got = w.host.trials.add(rec, "local", _adopted_manifest(rec.pkg_id))     # past the route: not ended yet
    held.thread.join(30)
    assert got == {"ok": False, "ended": True, "said": TRY_HOOK_LINE}
    assert held.result["ended"] and rec.ended and not hook.exists() and not folder.exists()
    assert [c["kind"] for c in held.cards] == ["try_hook_changed"] and added == []


# -- every check still judges, none waits for another, and a verdict stops the hand-over ------------------------

HOOK = ".venv/Lib/site-packages/late.pth"


def _running(w):
    _b, link, try_id, rec = _checked(w)
    make_venv(link, try_id)
    assert script(link, try_id, "serve.py", SERVE, start=True)["ok"]
    return link, try_id, rec


def _spy_add(w, monkeypatch):
    added = []
    real_add = w.ports.packages_add
    monkeypatch.setattr(w.ports, "packages_add", lambda *a, **k: (added.append(a), real_add(*a, **k))[1])
    return added


@needs_jobs
@pytest.mark.parametrize("where", ["core", "try"])
def test_a_check_that_arrives_while_another_is_on_its_way_to_its_verdict_still_judges(make_world, tmp_path,
                                                                                      monkeypatch, where):
    """The floor: the 1 s gate counts a finished check, so one that arrives during another is never gated away."""
    w = make_world({"index.html": "<!doctype html><title>Recipes</title>\n"})
    core = tmp_path / "coresite"
    core.mkdir()
    w.ports.hook_core_sites = [str(core)]
    _link, _try_id, rec = _running(w)
    trials = w.host.trials
    rec.scanned_at = 0.0                                    # past the gate
    walked, go_on, seen = threading.Event(), threading.Event(), {}
    real_guard = trials._new_guard

    def new_guard(folder):
        guard = real_guard(folder)
        if threading.current_thread() is first:
            inner = guard._snapshot

            def snapshot():
                got = inner()
                walked.set()
                go_on.wait(20)                              # past its walk, not yet at its verdict
                return got
            guard._snapshot = snapshot
        return guard
    monkeypatch.setattr(trials, "_new_guard", new_guard)
    first = threading.Thread(target=lambda: trials.rescan(rec), daemon=True)
    first.start()
    assert walked.wait(20)
    hook = core / "evil.pth" if where == "core" else Path(rec.folder) / HOOK
    hook.write_text("import os\n", encoding="utf-8")
    second = threading.Thread(target=lambda: seen.update(got=trials.rescan(rec)), daemon=True)
    second.start()                                          # as the add route's own step would
    second.join(0.5)
    go_on.set()
    first.join(30)
    second.join(30)
    assert seen["got"].get("ended"), seen
    assert seen["got"]["said"] == (CORE_HOOK_LINE if where == "core" else TRY_HOOK_LINE)
    assert rec.ended and rec.base is None                   # the first one's clean verdict does not undo that
    assert bool(w.ports.safe) == (where == "core")
    added = _spy_add(w, monkeypatch)
    assert trials.add(rec, "local", _adopted_manifest(rec.pkg_id))["ended"] and added == []


@needs_jobs
def test_a_slow_stop_holds_up_no_other_step_and_the_try_is_never_handed_over(make_world, monkeypatch):
    w = make_world({"index.html": "<!doctype html><title>Invoices</title>\n"})
    _link, _try_id, rec = _running(w)
    trials = w.host.trials
    folder = Path(rec.folder)
    stopping, release, seen = threading.Event(), threading.Event(), {}
    rec.compose = {"project": "puffpai-invoices", "files": ["compose.yaml"]}

    def slow_down(*_a, **_k):                               # docker compose down with no output, for a long time
        stopping.set()
        release.wait(30)
        return {"ok": True}
    rec.broker.core_run = slow_down
    added = _spy_add(w, monkeypatch)
    (folder / HOOK).write_text("import os\n", encoding="utf-8")
    checker = threading.Thread(target=lambda: seen.update(check=trials.rescan(rec, force=True)), daemon=True)
    other_step = threading.Thread(target=lambda: trials.rescan_all(rec.build_id), daemon=True)
    adder = threading.Thread(target=lambda: seen.update(add=trials.add(rec, "local", _adopted_manifest(rec.pkg_id))),
                             daemon=True)
    try:
        checker.start()
        assert stopping.wait(30)                            # the check has its verdict and is stopping the try
        assert not rec.ended
        other_step.start()
        other_step.join(5)
        assert not other_step.is_alive(), "another step waited for the slow stop"
        adder.start()
        adder.join(5)
        assert not adder.is_alive(), "the add waited for the slow stop"
    finally:
        release.set()
    checker.join(30)
    adder.join(30)
    assert seen["add"] == {"ok": False, "ended": True, "said": TRY_HOOK_LINE}
    assert seen["check"]["ended"] and rec.ended and not folder.exists() and added == []


@needs_jobs
def test_a_change_found_while_compose_comes_down_stops_the_hand_over(make_world, monkeypatch):
    w = make_world({"index.html": "<!doctype html><title>Birds</title>\n"})
    _link, _try_id, rec = _running(w)
    rec.broker.stop_all()
    folder = Path(rec.folder)
    rec.compose = {"project": "puffpai-birds", "files": ["compose.yaml"]}
    rec.broker.core_run = lambda *_a, **_k: {"ok": True}
    added = _spy_add(w, monkeypatch)
    (folder / HOOK).write_text("import os\n", encoding="utf-8")
    got = w.host.trials.add(rec, "local", _adopted_manifest(rec.pkg_id))
    assert got == {"ok": False, "ended": True, "said": TRY_HOOK_LINE}
    assert rec.ended and not folder.exists() and added == []
    assert [c["kind"] for c in w.host.cards] == ["try_hook_changed"]


@needs_jobs
@pytest.mark.parametrize("changes", [True, False], ids=["a_real_change", "no_change"])
def test_a_command_that_overlaps_the_add_gets_its_verdict_before_the_move(make_world, monkeypatch, changes):
    """Its verdict is held back a little, so without the wait it lands after the move."""
    w = make_world({"index.html": "<!doctype html><title>Garden</title>\n"})
    link, try_id, rec = _running(w)
    trials = w.host.trials
    folder = Path(rec.folder)
    hook = folder / HOOK
    (folder / ".venv/Lib/site-packages/distutils-precedence.pth").write_text("import os\n", encoding="utf-8")
    rec.base = trials._new_guard(rec.folder)._snapshot()    # in the baseline, as in a real venv: not an alarm
    write = f"pathlib.Path(r'{HOOK}').write_text('import os')\n" if changes else ""
    code = f"import pathlib, time\n{write}pathlib.Path('began.txt').write_text('x')\ntime.sleep(60)\n"
    assert link.try_write(try_id, "slow.py", code)["ok"]
    real_guard, seen = trials._new_guard, {}

    def new_guard(folder_):
        guard = real_guard(folder_)
        if threading.current_thread() is runner:
            inner = guard._snapshot

            def snapshot():
                time.sleep(0.3)
                return inner()
            guard._snapshot = snapshot
        return guard
    monkeypatch.setattr(trials, "_new_guard", new_guard)
    added = _spy_add(w, monkeypatch)
    runner = threading.Thread(daemon=True, target=lambda: seen.update(
        run=trials.run(rec, allowlist.Request(program="python", args=["slow.py"]))))
    runner.start()
    until = time.monotonic() + 30
    while not (folder / "began.txt").exists() and time.monotonic() < until:
        time.sleep(0.02)
    assert (folder / "began.txt").exists()
    assert hook.exists() == changes
    got = trials.add(rec, "local", _adopted_manifest(rec.pkg_id))
    runner.join(30)
    if changes:
        assert got == {"ok": False, "ended": True, "said": TRY_HOOK_LINE}
        assert seen["run"]["ended"] and rec.ended and added == [] and not folder.exists()
        assert [c["kind"] for c in w.host.cards] == ["try_hook_changed"]
    else:
        assert got.get("ok") and got.get("waiting"), got
        assert not seen["run"].get("ended") and not rec.ended and len(added) == 1
        assert w.host.cards == []
        staged = w.core.packages._pending[got["consent_id"]].folder
        assert manifest_mod.load(staged, adopted=True)[0] == _adopted_manifest(rec.pkg_id)


@needs_jobs
def test_a_refused_add_ends_the_try_so_its_moved_folder_is_never_misjudged(make_world, monkeypatch):
    w = make_world({"index.html": "<!doctype html><title>Library</title>\n"})
    link, try_id, rec = _running(w)
    seen = {}

    def no(folder, **_k):
        seen["folder"] = Path(folder)
        (Path(folder) / HOOK).write_text("import os\n", encoding="utf-8")     # a real change, during the copy
        raise Refused("Not this app.")
    monkeypatch.setattr(w.ports, "packages_add", no)
    got = link.try_add(try_id, "local", _adopted_manifest(rec.pkg_id))
    assert got == {"ok": False, "ended": True, "said": "Not this app."}
    assert rec.ended and not seen["folder"].exists()        # the change went with the folder, and nothing was added
    assert w.host.cards == []                               # no false notice
    with pytest.raises(TryEnded, match="Not this app"):     # the next step is told plainly
        link.try_run(try_id, {"program": "python", "args": ["-m", "venv", ".venv"]})


@needs_jobs
def test_an_add_that_waits_too_long_for_a_check_changes_nothing_and_can_go_again(make_world, monkeypatch):
    w = make_world({"index.html": "<!doctype html><title>Choir</title>\n"})
    link, try_id, rec = _running(w)
    folder = Path(rec.folder)
    release = threading.Event()
    monkeypatch.setattr(trials_mod, "SETTLE_S", 0.3)
    held = HeldCheck(w, rec, monkeypatch, on_held=lambda: release.wait(30))
    held.start()
    got = w.host.trials.add(rec, "local", _adopted_manifest(rec.pkg_id))
    assert got["ok"] is False and "still going" in got["said"] and not got.get("ended")
    assert Path(rec.folder) == folder and folder.is_dir() and not (folder / ".buildbot" / "puffpai.json").exists()
    release.set()
    held.thread.join(30)
    assert held.result == {}
    assert link.try_add(try_id, "local", _adopted_manifest(rec.pkg_id)).get("waiting")


@needs_jobs
def test_while_an_add_is_under_way_no_command_starts_and_no_second_add_goes_in(make_world, monkeypatch):
    w = make_world({"index.html": "<!doctype html><title>Stamps</title>\n"})
    link, try_id, rec = _running(w)
    trials = w.host.trials
    folder = Path(rec.folder)
    code = "import pathlib\npathlib.Path('ran.txt').write_text('x')\n"
    assert link.try_write(try_id, "late.py", code)["ok"]
    release, seen = threading.Event(), {}
    held = HeldCheck(w, rec, monkeypatch, on_held=lambda: release.wait(30))
    held.start()
    adder = threading.Thread(daemon=True, target=lambda: seen.update(
        add=trials.add(rec, "local", _adopted_manifest(rec.pkg_id))))
    adder.start()
    try:
        until = time.monotonic() + 20
        while not rec.add_step and time.monotonic() < until:
            time.sleep(0.01)
        assert rec.add_step
        ran = trials.run(rec, allowlist.Request(program="python", args=["late.py"]))
        assert ran["ok"] is False and "being added" in ran["said"] and not (folder / "ran.txt").exists()
        again = trials.add(rec, "local", _adopted_manifest(rec.pkg_id))
        assert again["ok"] is False and "being added already" in again["said"]
    finally:
        release.set()
    adder.join(60)
    assert seen["add"].get("waiting"), seen


def _check_loop(trials, rec, stop):
    def go():
        while not stop.is_set():
            trials.rescan(rec, force=True)
            time.sleep(0.005)
    threads = [threading.Thread(target=go, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    return threads


@needs_jobs
def test_a_normal_end_works_under_a_loop_of_checks(make_world):
    w = make_world({"index.html": "<!doctype html><title>Quilts</title>\n"})
    _link, _try_id, rec = _running(w)
    folder = Path(rec.folder)
    stop = threading.Event()
    threads = _check_loop(w.host.trials, rec, stop)
    time.sleep(0.2)
    assert w.host.trials.end(rec) == {"ok": True, "removed": True}
    stop.set()
    for t in threads:
        t.join(30)
        assert not t.is_alive()
    assert rec.ended and not folder.exists()


@needs_jobs
def test_safe_mode_works_under_a_loop_of_checks(make_world, tmp_path):
    w = make_world({"index.html": "<!doctype html><title>Tides</title>\n"})
    core = tmp_path / "coresite"
    core.mkdir()
    w.ports.hook_core_sites = [str(core)]
    _link, _try_id, rec = _running(w)
    folder = Path(rec.folder)
    stop = threading.Event()
    threads = _check_loop(w.host.trials, rec, stop)
    time.sleep(0.2)
    (core / "evil.pth").write_text("import os\n", encoding="utf-8")
    until = time.monotonic() + 20
    while not rec.ended and time.monotonic() < until:
        time.sleep(0.01)
    stop.set()
    for t in threads:
        t.join(30)
        assert not t.is_alive()
    assert w.ports.safe and w.host._paused and rec.ended and not folder.exists()
    assert (core / "evil.pth").exists()                     # left in place as evidence
