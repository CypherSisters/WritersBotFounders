"""Lane I2a part D: git boundaries, the manifest she said Yes to, and leftover try TEMP folders.

1. Every try folder gets a git repository of its own (``gitfence.init_repo`` in ``Trials.check``), and every
   try program and every package program gets ``GIT_CEILING_DIRECTORIES`` at its folder's parent, so a git
   command in a try or package never sees a repository above it (this computer's home folder is one).
2. The test session itself: ``GIT_CEILING_DIRECTORIES`` at the session temp root's parent, and the git test
   helper (``conftest.git_init``) fails the test at once when ``git init`` fails.
3. The Registry row records the sha256 of the manifest file at the install Yes (``manifest_sha``). Steward
   refuses to start a package whose file changed, marks it broken with a plain reason and starts nothing.
   Built-in packages the same. An update records the new sha only through its own Yes.
4. At Core start, leftover try TEMP folders (``<bot>\\t\\<6 hex>``) go; nothing else there, and nothing
   through a link.

Process rule for these tests: no test stops any process it did not start itself.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import git_init, yes
from test_d61_allowlist import HELLO, WIN, _checked, needs_jobs, world  # noqa: F401 - shared
from toolsmith_fakes import World
from puffpai_core.steward import gitfence
from puffpai_core.steward.steward import MANIFEST_CHANGED, MANIFEST_UNRECORDED, StewardError
from puffpai_core.zone3.toolsmith_host import broker as broker_mod

GIT = gitfence.git_program()
needs_git = pytest.mark.skipif(GIT is None, reason="git is not on this computer")
IDENT = ["-c", "user.name=t", "-c", "user.email=t@t.invalid"]


def _home_repo(tmp_path: Path) -> Path:
    """A fake home folder that is a git repository with one commit (like this computer's own)."""
    home = tmp_path / "home"
    home.mkdir()
    git_init(home)
    subprocess.run([GIT, "-C", str(home), *IDENT, "commit", "-q", "--allow-empty", "-m", "home"], check=True,
                   capture_output=True)
    return home


def _same(a, b) -> bool:
    return os.path.normcase(os.path.realpath(str(a).strip())) == os.path.normcase(os.path.realpath(str(b)))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- 1. a try and a package never see a git repository above them -------------------------------------------

@needs_git
@needs_jobs
def test_a_try_folder_below_a_home_repo_gets_its_own_and_git_in_it_sees_only_that(tmp_path, monkeypatch):
    home = _home_repo(tmp_path)
    w = World(tmp_path, home / "AppData" / "Local" / "PuffPAI" / "bot")
    w.ports.fixture_repo = HELLO
    try:
        _b, _link, _try_id, rec = _checked(w)
        assert rec.git == "" and (rec.folder / ".git").is_dir()
        assert not list((rec.folder / ".git").glob("hooks/*")), "no template hooks in the try's own repo"
        env = rec.broker.env()
        assert _same(env["GIT_CEILING_DIRECTORIES"], rec.folder.parent)
        # The Core's own GIT_DIR (as under a git hook) is never passed to a try.
        monkeypatch.setenv("GIT_DIR", str(home / ".git"))
        assert "GIT_DIR" not in rec.broker.env()
        sub = rec.folder / "sub"
        sub.mkdir()
        got = rec.broker.core_run([GIT, "rev-parse", "--show-toplevel"], sub, "git rev-parse --show-toplevel")
        assert got["ok"], got
        assert _same(got["output"].strip().splitlines()[-1], rec.folder), got
        # Without its own .git, the ceiling alone keeps git from finding the home repository.
        shutil.rmtree(rec.folder / ".git")
        got = rec.broker.core_run([GIT, "rev-parse", "--show-toplevel"], sub, "git rev-parse --show-toplevel")
        assert got["exit"] != 0 and "not a git repository" in got["output"], got
        assert not _same(got["output"].strip(), home)
    finally:
        w.host.stop(timeout=10)


def test_with_no_git_on_the_computer_a_try_gets_nothing_and_its_record_says_why(tmp_path, monkeypatch, state):
    monkeypatch.setattr(gitfence, "git_program", lambda: None)
    folder = tmp_path / "try"
    folder.mkdir()
    assert gitfence.init_repo(folder) == gitfence.NO_GIT
    assert not os.path.lexists(folder / ".git")
    if not WIN:
        return
    w = World(tmp_path, state)
    w.ports.fixture_repo = HELLO
    try:
        _b, _link, _try_id, rec = _checked(w)
        assert rec.git == gitfence.NO_GIT and not os.path.lexists(rec.folder / ".git")
        assert _same(rec.broker.env()["GIT_CEILING_DIRECTORIES"], rec.folder.parent)   # the ceiling still holds
    finally:
        w.host.stop(timeout=10)


def test_a_git_init_that_fails_is_said_and_a_try_with_one_never_runs(tmp_path, monkeypatch, state):
    monkeypatch.setattr(gitfence, "git_program", lambda: sys.executable)     # a program that is not git: exit 2
    folder = tmp_path / "try"
    folder.mkdir()
    said = gitfence.init_repo(folder)
    assert said.startswith("git init failed") and not os.path.lexists(folder / ".git")
    if not WIN:
        return
    w = World(tmp_path, state)
    w.ports.fixture_repo = HELLO
    try:
        b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
        link = w.link(b["id"])
        opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
        assert link.try_download(opened["try_id"])["ok"]
        from puffpai_core.zone3.toolsmith.errors import Refused
        with pytest.raises(Refused, match="own git folder could not be made") as e:
            link.try_check(opened["try_id"])
        assert sys.executable not in str(e.value)                      # git's own words are not sent back
        assert not any(p.name != ".staging" for p in (state / "packages").iterdir()), "the try folder went"
    finally:
        w.host.stop(timeout=10)


@needs_jobs
def test_toolsmith_cannot_write_move_or_delete_the_trys_own_git(world):
    _b, link, try_id, rec = _checked(world)
    from puffpai_core.zone3.toolsmith_host.trials import CORE_FILE_LINE
    for rel in (".git", ".GIT", ".git.", ".git/config", ".git/hooks/pre-commit"):
        got = link.try_write(try_id, rel, "gitdir: C:/somewhere/else/.git")
        assert not got["ok"] and got["said"] == CORE_FILE_LINE, (rel, got)
    assert link.try_write(try_id, "notes.txt", "x")["ok"]
    for op, fields in (("copy", {"from": "notes.txt", "to": ".git"}), ("move", {"from": "notes.txt", "to": ".git"}),
                       ("mkdir", {"path": ".git/hooks"}), ("delete", {"path": ".git"})):
        got = link.try_fs(try_id, op, **fields)
        assert not got["ok"] and got["said"] == CORE_FILE_LINE, (op, fields, got)
    if GIT:
        assert (rec.folder / ".git").is_dir()
    # a .git deeper in the app (a vendored project) is the app's own, as before
    assert link.try_write(try_id, "vendor/.gitkeep", "")["ok"]


@needs_git
def test_steward_gives_every_package_program_a_git_ceiling_at_its_folders_parent(core, tmp_path, monkeypatch):
    home = _home_repo(tmp_path)
    folder = home / "packages" / "pkg"
    folder.mkdir(parents=True)
    core.installs.set("pkg", folder, False, str(folder))
    monkeypatch.setenv("GIT_DIR", str(home / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(home))
    for manifest in ({}, {"kind": "provider", "reads": "raw", "provides": []}):     # walled too
        env = core.steward.env("pkg", manifest, 18500, "tok", {"GIT_CEILING_DIRECTORIES": "C:\\"})
        assert _same(env["GIT_CEILING_DIRECTORIES"], folder.parent)
        assert "GIT_DIR" not in env and "GIT_WORK_TREE" not in env
    got = subprocess.run([GIT, "rev-parse", "--show-toplevel"], cwd=str(folder), env=env, capture_output=True,
                         text=True, check=False)
    assert got.returncode != 0 and "not a git repository" in got.stderr, (got.stdout, got.stderr)


def test_the_stdio_mcp_server_of_a_package_gets_the_git_ceiling_too(tmp_path, monkeypatch):
    import mcp
    from puffpai_core.packages import mcp_tools
    seen = {}

    class Stop(Exception):
        pass

    def params(**kw):
        seen.update(kw)
        raise Stop()
    monkeypatch.setattr(mcp, "StdioServerParameters", params)
    manifest = {"abilities": {"mcp": {"transport": "stdio", "command": ["{python}", "server.py"]}}}
    folder = tmp_path / "pkg"
    folder.mkdir()
    with pytest.raises(mcp_tools.McpError):
        mcp_tools.list_tools(manifest, {"python": sys.executable, "port": "", "pkg": str(folder)}, "tok",
                             cwd=folder)
    assert seen and _same(seen["env"]["GIT_CEILING_DIRECTORIES"], tmp_path)


# -- 2. the test session itself ---------------------------------------------------------------------------

@needs_git
def test_the_session_ceiling_keeps_git_below_the_session_temp_root(tmp_path, tmp_path_factory):
    ceiling = os.environ["GIT_CEILING_DIRECTORIES"].split(os.pathsep)
    assert any(_same(c, tmp_path_factory.getbasetemp().parent) for c in ceiling)
    # No repository here, and git does not look above the session's own temp root for one.
    got = subprocess.run([GIT, "-C", str(tmp_path), "rev-parse", "--show-toplevel"], capture_output=True,
                         text=True, check=False)
    assert got.returncode != 0 and "not a git repository" in got.stderr, (got.stdout, got.stderr)


@needs_git
def test_a_git_helper_whose_init_breaks_fails_the_test_at_once(tmp_path):
    not_a_folder = tmp_path / "a-file"
    not_a_folder.write_text("x", encoding="utf-8")
    with pytest.raises(pytest.fail.Exception, match="git init failed"):
        git_init(not_a_folder)                                       # git can't make a repository in a file
    with pytest.raises(pytest.fail.Exception, match="git init failed"):
        git_init(tmp_path / "repo", git=sys.executable)              # a "git" that fails
    assert not (tmp_path / "repo" / ".git").exists()
    good = tmp_path / "good"
    good.mkdir()
    git_init(good)
    assert (good / ".git").is_dir()


# -- 3. Steward runs the manifest she said Yes to ---------------------------------------------------------

@pytest.fixture
def quiet(core):
    core.packages.start_after_install = False
    return core


def _installed(core, folder, **add):
    got = yes(core, core.packages.add(str(folder), **add))
    assert got["status"] == "installed", got
    return got["id"]


def test_the_install_yes_records_the_sha_of_the_manifest_file(quiet, drawbridge, hello_copy):
    pkg = _installed(quiet, hello_copy)
    row = quiet.registry.get(pkg)
    assert row["manifest_sha"] == _sha(quiet.state / "packages" / pkg / "puffpai.json") == _sha(
        hello_copy / "puffpai.json")


def test_steward_refuses_a_package_whose_manifest_changed_and_marks_it_broken(quiet, drawbridge, hello_copy):
    pkg = _installed(quiet, hello_copy)
    path = quiet.state / "packages" / pkg / "puffpai.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["description"] = "changed after her Yes"
    path.write_text(json.dumps(data), encoding="utf-8")
    cards_before, notices_before = list(quiet.caretaker.cards), list(getattr(quiet.notices, "list", list)() or [])
    with pytest.raises(StewardError) as e:
        quiet.steward.start(pkg)
    assert str(e.value) == MANIFEST_CHANGED
    row = quiet.registry.get(pkg)
    assert row["status"] == "broken" and row["health"]["reason"] == MANIFEST_CHANGED
    assert not quiet.steward.alive(pkg) and quiet.steward.running() == []
    assert quiet.caretaker.cards == cards_before                       # nothing else is raised
    assert (list(getattr(quiet.notices, "list", list)() or [])) == notices_before
    # the same through the Core API's start route: a plain 409, nothing started
    r = quiet.app.test_client().post(f"/v1/packages/{pkg}/start",
                                     headers={"Authorization": f"Bearer {quiet.tokens.issue_device('laptop')}"})
    assert r.status_code == 409 and r.get_json()["error"] == MANIFEST_CHANGED, (r.status_code, r.get_json())
    assert not quiet.steward.alive(pkg)


def test_a_row_with_no_manifest_sha_is_never_started(quiet, drawbridge, hello_copy):
    pkg = _installed(quiet, hello_copy)
    row = quiet.registry.get(pkg)
    row.pop("manifest_sha")
    quiet.registry.remove(pkg)
    quiet.registry.add(row)
    with pytest.raises(StewardError, match="no record"):
        quiet.steward.start(pkg)
    assert quiet.registry.get(pkg)["status"] == "broken"
    assert quiet.registry.get(pkg)["health"]["reason"] == MANIFEST_UNRECORDED


def test_start_again_after_a_restart_leaves_a_changed_package_broken_with_its_reason(quiet, drawbridge, hello_copy):
    pkg = _installed(quiet, hello_copy)
    quiet.steward._save_settings({"was_running": [pkg]})
    path = quiet.state / "packages" / pkg / "puffpai.json"
    path.write_text(path.read_text(encoding="utf-8").replace("{", "{ ", 1), encoding="utf-8")   # one byte more
    quiet.steward.start_saved(background=False)
    row = quiet.registry.get(pkg)
    assert row["status"] == "broken" and row["health"]["reason"] == MANIFEST_CHANGED
    assert not quiet.steward.alive(pkg)


def test_an_adopted_app_is_held_to_the_core_s_manifest_file_not_the_projects_own(quiet, drawbridge, tmp_path):
    from test_i2a_part_c import URL, _two_manifests
    folder = tmp_path / "hello-page"
    _two_manifests(folder, ["{python}", "evil.py"], ["{python}", "serve.py"])
    pkg = _installed(quiet, folder, adopted=URL)
    installed = quiet.state / "packages" / pkg
    assert quiet.registry.get(pkg)["manifest_sha"] == _sha(installed / ".buildbot" / "puffpai.json")
    # The project's own top-level file is not her manifest: changing it changes nothing Steward runs.
    (installed / "puffpai.json").write_text("{}", encoding="utf-8")
    assert quiet.steward.manifest(pkg)["run"]["windows"]["start"] == ["{python}", "serve.py"]
    core_file = installed / ".buildbot" / "puffpai.json"
    data = json.loads(core_file.read_text(encoding="utf-8"))
    data["run"]["windows"]["start"] = ["{python}", "evil.py"]
    core_file.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StewardError, match="settings file changed"):
        quiet.steward.start(pkg)
    assert quiet.registry.get(pkg)["status"] == "broken"


def test_a_built_in_package_is_checked_the_same_way(quiet, drawbridge):
    from puffpai_core.desktop import builtin_dir
    pkg = _installed(quiet, builtin_dir() / "desktop")
    installed = quiet.state / "packages" / pkg / "puffpai.json"
    assert quiet.registry.get(pkg)["manifest_sha"] == _sha(installed) == _sha(builtin_dir() / "desktop" / "puffpai.json")
    assert quiet.steward.start(pkg)["status"] == "running"
    quiet.steward.stop(pkg)
    data = json.loads(installed.read_text(encoding="utf-8"))
    data["name"] = "Not the shipped one"
    installed.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StewardError, match="settings file changed"):
        quiet.steward.start(pkg)
    assert quiet.registry.get(pkg)["status"] == "broken"


def test_an_update_records_the_new_sha_only_through_its_own_yes(quiet, drawbridge, hello_copy):
    pkg = _installed(quiet, hello_copy)
    old = quiet.registry.get(pkg)["manifest_sha"]
    data = json.loads((hello_copy / "puffpai.json").read_text(encoding="utf-8"))
    data["version"] = "9.9.9"
    (hello_copy / "puffpai.json").write_text(json.dumps(data), encoding="utf-8")
    staged = quiet.packages.update(pkg)
    assert quiet.registry.get(pkg)["manifest_sha"] == old                # no Yes yet: the old sha stands
    assert quiet.steward.manifest(pkg)["version"] != "9.9.9"
    got = yes(quiet, staged)
    assert got["status"] == "installed", got
    row = quiet.registry.get(pkg)
    assert row["manifest_sha"] == _sha(hello_copy / "puffpai.json") != old and row["version"] == "9.9.9"
    assert quiet.steward.manifest(pkg)["version"] == "9.9.9"


def test_an_update_never_uses_a_changed_manifest_as_its_before(quiet, drawbridge, hello_copy):
    from puffpai_core.packages.manager import AddError
    pkg = _installed(quiet, hello_copy)
    path = quiet.state / "packages" / pkg / "puffpai.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(AddError) as e:
        quiet.packages.update(pkg)
    assert e.value.problems == [MANIFEST_CHANGED]


def test_an_update_whose_staged_manifest_changed_after_her_card_is_not_swapped_in(quiet, drawbridge, hello_copy):
    pkg = _installed(quiet, hello_copy)
    old = quiet.registry.get(pkg)["manifest_sha"]
    data = json.loads((hello_copy / "puffpai.json").read_text(encoding="utf-8"))
    data["version"] = "2.0.0"
    (hello_copy / "puffpai.json").write_text(json.dumps(data), encoding="utf-8")
    staged = quiet.packages.update(pkg)
    pending = quiet.packages._pending[staged["consent_id"]]
    pending.manifest_sha = "0" * 64                                   # as if her card showed another file
    got = yes(quiet, staged)
    assert got["status"] == "failed", got
    assert quiet.registry.get(pkg)["manifest_sha"] == old
    assert quiet.steward.manifest(pkg)["version"] != "2.0.0"           # the old version is still in place


# -- 4. leftover try TEMP folders at Core start ------------------------------------------------------------

def _junction(link: Path, target: Path) -> bool:
    got = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    return got.returncode == 0


def test_core_start_removes_only_the_try_temp_folders_the_broker_names(tmp_path):
    bot = tmp_path / "bot"
    t = bot / "t"
    made = broker_mod.make_temp(bot)                                  # the broker's own naming
    (made / "npm-cache").mkdir()
    (made / "npm-cache" / "x.txt").write_text("x", encoding="utf-8")
    (t / "a1b2c3").mkdir()
    keep = {"ABCDEF": "dir", "abc": "dir", "a1b2c3d": "dir", "zzzzzz": "dir", "d4e5f6": "file"}
    for name, kind in keep.items():
        (t / name).mkdir() if kind == "dir" else (t / name).write_text("mine", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me", encoding="utf-8")
    linked = WIN and _junction(t / "0f0f0f", outside)                  # a link with the right name: not ours
    inner = WIN and _junction(t / "a1b2c3" / "out", outside)          # a link inside one of ours: never followed
    assert linked == inner == WIN
    assert broker_mod.clean_temps(bot) == 2
    assert not made.exists() and not (t / "a1b2c3").exists()
    for name in keep:
        assert (t / name).exists(), name
    if linked:
        assert os.path.lexists(t / "0f0f0f")
    if linked or inner:
        assert (outside / "precious.txt").read_text(encoding="utf-8") == "keep me"


@needs_jobs
def test_a_t_folder_that_is_a_link_is_never_gone_into(tmp_path):
    bot = tmp_path / "bot"
    bot.mkdir()
    outside = tmp_path / "outside"
    (outside / "abcdef").mkdir(parents=True)
    (outside / "abcdef" / "precious.txt").write_text("keep me", encoding="utf-8")
    assert _junction(bot / "t", outside)
    assert broker_mod.clean_temps(bot) == 0
    assert (outside / "abcdef" / "precious.txt").read_text(encoding="utf-8") == "keep me"


def test_start_core_cleans_leftover_try_temps(core):
    from puffpai_core.api.app import start_core, stop_core
    left = core.state / "t" / "abcdef"
    left.mkdir(parents=True)
    (left / "x.tmp").write_text("x", encoding="utf-8")
    other = core.state / "t" / "keep-me"
    other.mkdir()
    core.proxy_port = 0
    terminal = start_core(core)
    try:
        assert not left.exists() and other.is_dir()
    finally:
        stop_core(core, terminal)
