"""L4's HookGuard around every command the Core runs for Toolsmith (09 section 3, X23, the post-command scan)."""

from pathlib import Path

import pytest

from toolsmith_fakes import FIXTURES, World, make_venv, script

# Every program here runs the way a try runs code (D60, D61): a script file, by the try's own .venv Python.
PRINT = {"program": "python", "args": ["p.py"]}


@pytest.fixture
def world(tmp_path, state):
    w = World(tmp_path, state)
    w.ports.fixture_repo = FIXTURES / "toolsmith" / "hello-page"
    yield w
    w.host.stop(timeout=10)


def _checked_try(w):
    b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = w.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
    assert link.try_download(opened["try_id"])["ok"]
    got = link.try_check(opened["try_id"])
    assert got["ok"]
    make_venv(link, opened["try_id"])
    assert link.try_write(opened["try_id"], "p.py", "print(1)\n")["ok"]
    return b, link, opened["try_id"], Path(got["folder"])


def _write(link, try_id, path: Path) -> dict:
    """A script file in the try that writes *path* (a script runs as the owner: the guard catches it)."""
    code = (f"import pathlib\np = pathlib.Path({path.as_posix()!r})\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\np.write_text('import os')\n")
    return script(link, try_id, "w.py", code)


def test_a_new_start_up_hook_in_the_try_ends_it_and_is_undone(world, state):
    b, link, try_id, folder = _checked_try(world)
    hook = folder / ".venv" / "Lib" / "site-packages" / "evil.pth"
    got = _write(link, try_id, hook)
    assert not got["ok"] and got["ended"] and "start-up files" in got["said"]
    assert not hook.exists()                                         # undone, then the try folder went
    assert not folder.exists()
    with pytest.raises(Exception):
        link.try_run(try_id, PRINT)                                  # the try is over
    assert world.ports.safe == []                                    # the Core's own Python is untouched


def test_a_change_to_the_cores_own_python_is_safe_mode(world, tmp_path):
    core_site = tmp_path / "core-site-packages"
    core_site.mkdir()
    world.ports.hook_core_sites = [str(core_site)]                   # stands in for the Core's site-packages
    b, link, try_id, folder = _checked_try(world)
    got = _write(link, try_id, core_site / "sitecustomize.py")
    assert not got["ok"] and got["ended"] and "safe mode" in got["said"]
    assert world.ports.safe == ["core_python_hook_changed"]
    assert world.host._stopping                                      # Toolsmith stops
    assert any(c.get("kind") == "safe_mode" for c in world.host.cards)
    assert (core_site / "sitecustomize.py").exists()                 # left in place as evidence (L4's rule)


def test_an_ordinary_command_passes_the_guard(world):
    b, link, try_id, folder = _checked_try(world)
    assert link.try_write(try_id, "p42.py", "print(42)\n")["ok"]
    got = link.try_run(try_id, {"program": "python", "args": ["p42.py"]})
    assert got["ok"] and "42" in got["output"]
    link.try_end(try_id)


def test_no_guard_means_no_command(world):
    from puffpai_core.zone3.toolsmith.errors import NotBuilt

    def missing(*a, **k):
        raise NotBuilt("The Python start-up check", "L4")
    world.ports.hook_guard = missing
    b = world.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = world.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
    got = link.try_download(opened["try_id"])                       # no guard: not even the download
    assert not got["ok"] and "not built yet" in got["said"]
    assert not (world.state / "packages" / "hello-page").exists()


# -- servers that change start-up files AFTER they start (the verifier's repro) ------------------

def _late_writer(link, try_id, path: Path, delay: float = 1.0) -> dict:
    """A server (a script file in the try) that writes *path* after it started."""
    code = (f"import pathlib, time\ntime.sleep({delay})\np = pathlib.Path({path.as_posix()!r})\n"
            "p.parent.mkdir(parents=True, exist_ok=True)\np.write_text('x')\ntime.sleep(30)\n")
    return script(link, try_id, "late.py", code, start=True)


def _wait_for(path: Path, s: float = 10.0) -> None:
    import time
    end = time.monotonic() + s
    while time.monotonic() < end and not path.exists():
        time.sleep(0.1)
    assert path.exists()


def test_a_server_that_changes_the_cores_python_later_is_safe_mode_at_the_next_command(world, tmp_path):
    from puffpai_core.zone3.toolsmith.errors import TryEnded
    core_site = tmp_path / "core-site-packages"
    core_site.mkdir()
    world.ports.hook_core_sites = [str(core_site)]
    b, link, try_id, folder = _checked_try(world)
    hook = core_site / "sitecustomize.py"
    assert _late_writer(link, try_id, hook)["ok"]         # its own "after" scan is clean: too early
    _wait_for(hook)
    with pytest.raises(TryEnded):
        link.try_run(try_id, PRINT)                # the next step sees it; never a new normal
    assert world.ports.safe == ["core_python_hook_changed"] and world.host._stopping
    assert hook.exists()                                             # left as evidence (L4's rule)
    assert not folder.exists()                                       # the try (and its server) ended


def test_a_server_that_writes_a_pth_later_ends_the_try_and_it_is_undone(world):
    from puffpai_core.zone3.toolsmith.errors import TryEnded
    b, link, try_id, folder = _checked_try(world)
    hook = folder / ".venv" / "Lib" / "site-packages" / "late.pth"
    assert _late_writer(link, try_id, hook)["ok"]
    _wait_for(hook)
    with pytest.raises(TryEnded) as e:
        link.try_run(try_id, PRINT)
    assert "start-up files" in str(e.value) and not hook.exists() and not folder.exists()
    assert world.ports.safe == []


def test_caretakers_tick_rescans_running_tries(world):
    from puffpai_core.zone3.toolsmith.errors import TryEnded

    class Caretaker:                                                 # L1's tick-hook list (on_tick)
        def __init__(self):
            self.on_tick = []

        def tick(self):
            self.ticks = getattr(self, "ticks", 0) + 1
            for fn in self.on_tick:
                fn()
    world.ports.caretaker = c = Caretaker()
    world.host._hook_caretaker()
    world.host._hook_caretaker()                                     # once only
    assert len(c.on_tick) == 1
    b, link, try_id, folder = _checked_try(world)
    hook = folder / ".venv" / "Lib" / "site-packages" / "late.pth"
    assert _late_writer(link, try_id, hook, 0.5)["ok"]
    _wait_for(hook)
    import time
    time.sleep(1.1)                                                  # past the re-scan throttle
    c.tick()                                                         # no Toolsmith step at all
    assert c.ticks == 1 and not hook.exists() and not folder.exists()
    with pytest.raises(TryEnded):
        link.try_log(try_id)


def test_any_toolsmith_step_rescans_its_running_tries(world):
    from puffpai_core.zone3.toolsmith.errors import TryEnded
    b, link, try_id, folder = _checked_try(world)
    hook = folder / ".venv" / "Lib" / "site-packages" / "late.pth"
    assert _late_writer(link, try_id, hook, 0.5)["ok"]
    _wait_for(hook)
    import time
    time.sleep(1.1)
    link.job_status(b["jobs"][0]["id"])                              # a plain step, not a command
    assert not hook.exists() and not folder.exists()
    with pytest.raises(TryEnded):
        link.try_run(try_id, PRINT)


def test_a_clean_command_moves_the_baseline_only_to_what_it_judged(world):
    b, link, try_id, folder = _checked_try(world)
    rec = world.host.trials.get(b["id"], try_id)
    before = rec.base
    assert link.try_run(try_id, PRINT)["ok"]
    assert rec.base is not before and rec.base["try"] == before["try"]
    link.try_end(try_id)


def test_a_download_that_ships_start_up_files_is_refused(world, tmp_path):
    repo = tmp_path / "repo"
    import shutil
    shutil.copytree(FIXTURES / "toolsmith" / "hello-page", repo)
    (repo / "lib" / "site-packages").mkdir(parents=True)
    (repo / "lib" / "site-packages" / "x.pth").write_text("import os")
    world.ports.fixture_repo = repo
    b = world.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = world.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
    got = link.try_download(opened["try_id"])
    assert not got["ok"] and "start-up files" in got["said"]
    assert not any((world.state / "packages" / ".staging").iterdir())
