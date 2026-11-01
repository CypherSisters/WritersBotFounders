"""Lane I13 item 1: installed built-ins follow the code.

A built-in's install is a copy (``<bot>\\packages\\<id>``) of the repo's ``packages\\<id>``. After a code
update the copy was old. Now ``PackageManager.check_builtins`` (Core start, ``puffpai update``,
``POST /v1/packages/<id>/update``) compares each with the repo's copy (manifest version and tree hash):
a system part (desktop, wizard, cloud-avatar) is updated with no card and an audit line; an app gets a
notice and a Yes card. The swap is atomic: a failure leaves the old copy in place and running. Packages
that are not copies of a built-in (adopted, linked, outside folders) are never touched.

The "repo" here is a copy of the real built-ins in this test's own folder (``PUFFPAI_BUILTIN``), so the
test can change it the way a code update would.
"""

import json
import shutil
from pathlib import Path

import pytest

from conftest import record_install, yes
from puffpai_core.desktop import builtin_dir
from puffpai_core.packages import fsutil
from puffpai_core.packages.manager import AddError

REAL = builtin_dir()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A built-ins folder of our own: copies of the real desktop and freshtable (and example-page)."""
    base = tmp_path / "builtin"
    for pid in ("desktop", "freshtable", "example-page"):
        shutil.copytree(REAL / pid, base / pid, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    monkeypatch.setenv("PUFFPAI_BUILTIN", str(base))
    return base


@pytest.fixture
def wired(core):
    """The Core with its own Drawbridge and audit log (``wire_core``), nothing started."""
    from puffpai_core.api.app import wire_core
    wire_core(core.app)
    core.packages.start_after_install = False
    return core


def _install(core, pid):
    got = yes(core, core.packages.add(pid))
    assert got["status"] == "installed", got
    return core.registry.get(pid)


def _bump(folder, version, **changes):
    path = folder / "puffpai.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    data.update(version=version, **changes)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _audit_lines(core, action="builtin_update"):
    from puffpai_core.zone3.service import for_app
    lines = reversed(for_app(core.app).audit.read(500))                   # read() is newest first
    return [line for line in lines if line.get("action") == action]


def _desktop_fix(repo):
    """What a code update does to the Desktop: a fixed file and a new version."""
    page = next((repo / "desktop" / "web").rglob("*.js"))
    page.write_text(page.read_text(encoding="utf-8") + "\n// the fix\n", encoding="utf-8")
    _bump(repo / "desktop", "0.2.1")
    return page.relative_to(repo / "desktop")


def test_a_stale_desktop_is_updated_at_start_with_its_data_kept(core, repo):
    """The real Drawbridge (it says warn for the Desktop): no new reason, so no card."""
    from puffpai_core.api.app import start_core, stop_core
    from puffpai_core.api.app import wire_core
    wire_core(core.app)
    core.packages.start_after_install = False
    before = _install(core, "desktop")
    assert core.packages.builtin_state("desktop")["current"] is True
    data = core.state / "data" / "desktop" / "layout.json"
    data.write_text('{"tiles": "hers"}', encoding="utf-8")
    settings = core.state / "settings" / "desktop.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text('{"ui": "desktop", "avatar": null}', encoding="utf-8")
    rel = _desktop_fix(repo)
    assert core.packages.builtin_state("desktop") == {
        "id": "desktop", "name": "PuffPAI Desktop", "system": True, "have": "0.2.0", "repo": "0.2.1", "current": False}
    terminal = start_core(core)
    try:
        installed = core.state / "packages" / "desktop"
        assert (installed / rel).read_text(encoding="utf-8").endswith("// the fix\n")
        assert fsutil.tree_hash(installed) == fsutil.tree_hash(repo / "desktop")
        row = core.registry.get("desktop")
        assert row["version"] == "0.2.1" and row["manifest_sha"] != before["manifest_sha"]
        assert row["yes_record"] == before["yes_record"]                   # her install Yes stays the record
        assert core.steward.manifest("desktop")["version"] == "0.2.1"      # the Core's one reader takes it
        assert data.read_text(encoding="utf-8") == '{"tiles": "hers"}'   # data kept
        assert settings.read_text(encoding="utf-8") == '{"ui": "desktop", "avatar": null}'
        assert core.consent.list(status="pending") == []                   # no card
        line = _audit_lines(core)[-1]
        assert line["package"] == "desktop" and line["from"] == "0.2.0" and line["to"] == "0.2.1"
        assert list((core.state / "packages" / ".staging").iterdir()) == []
        assert core.packages.builtin_state("desktop")["current"] is True
    finally:
        stop_core(core, terminal)


def test_the_same_content_with_the_same_version_is_left_alone(wired, repo, drawbridge):
    _install(wired, "desktop")
    assert wired.packages.check_builtins() == [
        {"id": "desktop", "status": "current", "version": "0.2.0", "line": "PuffPAI Desktop is up to date."}]
    assert _audit_lines(wired) == []


def test_an_app_update_waits_for_her_yes_and_keeps_its_data(wired, repo, drawbridge, monkeypatch):
    from puffpai_core.steward.steward import Steward
    monkeypatch.setattr(Steward, "setup", lambda self, id, expect_tree=None: None)   # no venv for this test
    before = _install(wired, "freshtable")
    store = wired.state / "data" / "freshtable" / "freshtable.json"
    store.write_text('{"shoppingList": [{"name": "Milk"}]}', encoding="utf-8")
    server = repo / "freshtable" / "server.py"
    server.write_text(server.read_text(encoding="utf-8") + "\n# the fix\n", encoding="utf-8")
    _bump(repo / "freshtable", "2.1.1")
    got = wired.packages.check_builtins(notices=wired.notices)
    assert [r["status"] for r in got] == ["card"]
    card = got[0]
    assert card["card"]["title"] == "FreshTable has an update" and card["what_changed"]["version"] == ["2.1.0", "2.1.1"]
    notices = [n for n in wired.notices.list() if n["title"] == "FreshTable has an update"]
    assert len(notices) == 1 and "Its data stays." in notices[0]["text"]
    installed = wired.state / "packages" / "freshtable" / "server.py"
    assert not installed.read_text(encoding="utf-8").endswith("# the fix\n")          # nothing before her Yes
    assert wired.registry.get("freshtable")["version"] == "2.1.0" and _audit_lines(wired) == []
    again = wired.packages.check_builtins(notices=wired.notices)                      # the same card, no pile
    assert again[0]["consent_id"] == card["consent_id"]
    assert len([n for n in wired.notices.list() if n["title"] == "FreshTable has an update"]) == 1
    assert len(wired.consent.list(status="pending")) == 1
    assert yes(wired, card)["status"] == "installed"
    assert installed.read_text(encoding="utf-8").endswith("# the fix\n")
    row = wired.registry.get("freshtable")
    assert row["version"] == "2.1.1" and row["yes_record"] == card["consent_id"] != before["yes_record"]
    assert store.read_text(encoding="utf-8") == '{"shoppingList": [{"name": "Milk"}]}'   # data kept


def test_a_system_part_that_asks_for_more_gets_a_card(wired, repo, drawbridge):
    _install(wired, "desktop")
    _bump(repo / "desktop", "0.3.0", needs={"hosts": ["api.example.com"]})
    got = wired.packages.check_builtins(notices=wired.notices)
    assert got[0]["status"] == "card" and got[0]["what_changed"]["hosts"]["added"] == ["api.example.com"]
    assert wired.registry.get("desktop")["version"] == "0.2.0" and _audit_lines(wired) == []


def test_a_system_part_changed_since_her_yes_is_not_overwritten(wired, repo, drawbridge):
    _install(wired, "desktop")
    path = wired.state / "packages" / "desktop" / "puffpai.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    changed = path.read_bytes()
    _bump(repo / "desktop", "0.2.1")
    got = wired.packages.check_builtins()
    assert got[0]["status"] == "not_updated" and "changed" in got[0]["line"]
    assert path.read_bytes() == changed                                  # left as it is, for her to see
    assert wired.registry.get("desktop")["version"] == "0.2.0" and _audit_lines(wired) == []


@pytest.mark.parametrize("fail_at", ["move_aside", "move_in", "setup"])
def test_a_failed_update_leaves_the_old_copy_running(wired, repo, drawbridge, monkeypatch, fail_at):
    import puffpai_core.packages.manager as manager
    from puffpai_core.steward.steward import Steward, StewardError
    before = _install(wired, "desktop")
    assert wired.steward.start("desktop")["status"] == "running"        # a static Desktop: served by the Core
    old_files = fsutil.tree_hash(wired.state / "packages" / "desktop")
    _desktop_fix(repo)
    real_replace = manager.os.replace
    calls = []

    def replace(src, dst):
        if not Path(src).is_dir():
            return real_replace(src, dst)                                # a settings file, not a folder move
        calls.append((Path(src).name, Path(dst).name))
        if fail_at == "move_aside" and len(calls) == 1:
            raise PermissionError(13, "The process cannot access the file")
        if fail_at == "move_in" and len(calls) == 2:
            raise PermissionError(13, "The process cannot access the file")
        return real_replace(src, dst)
    monkeypatch.setattr(manager.os, "replace", replace)
    if fail_at == "setup":
        monkeypatch.setattr(Steward, "setup", lambda self, id, expect_tree=None: (_ for _ in ()).throw(
            StewardError("Setup step 1 failed (exit 3). See its log.")))
    got = wired.packages.check_builtins()
    assert got[0]["status"] == "not_updated", got
    assert "old version" in got[0]["line"]
    installed = wired.state / "packages" / "desktop"
    assert fsutil.tree_hash(installed) == old_files                     # the old copy, as it was
    row = wired.registry.get("desktop")
    assert row["status"] == "running" and wired.steward.alive("desktop")  # and running again
    assert row["version"] == "0.2.0" and row["manifest_sha"] == before["manifest_sha"]
    assert wired.steward.manifest("desktop")["version"] == "0.2.0"
    assert list((wired.state / "packages" / ".staging").iterdir()) == []
    assert _audit_lines(wired)[-1]["result"] == "rolled_back"


def test_outside_packages_are_never_touched(wired, repo, drawbridge, tmp_path):
    # weddingly-free: an app Toolsmith adopted, in packages\<id>, with an adopted: origin.
    adopted = wired.state / "packages" / "weddingly-free"
    shutil.copytree(REAL / "example-page", adopted)
    manifest = json.loads((adopted / "puffpai.json").read_text(encoding="utf-8"))
    manifest.update(id="weddingly-free", name="Weddingly", origin="adopted:https://example.invalid/weddingly")
    (adopted / ".buildbot").mkdir()
    (adopted / ".buildbot" / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
    from puffpai_core.packages import manifest as manifest_mod
    sha = manifest_mod.load_with_sha(adopted, adopted=True)[2]
    from puffpai_core.registry import row_from_manifest
    wired.installs.set("weddingly-free", adopted, False, str(adopted))
    wired.registry.add(row_from_manifest(manifest, "adopted:https://example.invalid/weddingly", manifest_sha=sha))
    # An outside folder that happens to use a built-in's id, and the repo's own folder linked in place.
    outside = tmp_path / "mine" / "example-page"
    shutil.copytree(REAL / "example-page", outside)
    _install(wired, str(outside))
    linked = yes(wired, wired.packages.add(str(repo / "freshtable"), link=True))
    assert linked["status"] == "installed", linked
    hashes = {p: fsutil.tree_hash(wired.installs.folder(p)) for p in ("weddingly-free", "example-page", "freshtable")}
    for pid in ("example-page", "freshtable"):
        _bump(repo / pid, "9.9.9")
    for pid in hashes:
        assert wired.packages.builtin_of(pid) is None, pid
    assert wired.packages.check_builtins(notices=wired.notices) == []
    assert wired.consent.list(status="pending") == [] and _audit_lines(wired) == []
    assert {p: fsutil.tree_hash(wired.installs.folder(p)) for p in hashes if p != "freshtable"} == {
        p: h for p, h in hashes.items() if p != "freshtable"}
    assert wired.registry.get("weddingly-free")["origin"].startswith("adopted:")
    with pytest.raises(AddError) as e:
        wired.packages.update_builtin("weddingly-free")
    assert e.value.status == 404


def test_the_update_route_and_cli(wired, repo, drawbridge, capsys, monkeypatch):
    _install(wired, "desktop")
    _desktop_fix(repo)
    c = wired.app.test_client()
    key = {"Authorization": "Bearer " + wired.tokens.issue_owner_key()}
    assert c.post("/v1/packages/desktop/update", headers=key).status_code == 403     # an API key can't
    device = {"Authorization": "Bearer " + wired.tokens.issue_device("Sam's laptop")}
    r = c.post("/v1/packages/desktop/update", headers=device)
    assert r.status_code == 200 and r.json["status"] == "updated" and r.json["to"] == "0.2.1"
    r = c.post("/v1/packages/desktop/update", headers=device)
    assert r.status_code == 200 and r.json["status"] == "current"
    # puffpai update, with no Core running: the same check in this process.
    from puffpai_core import cli
    _bump(repo / "desktop", "0.2.2")
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    assert cli._builtin_ids() == ["desktop"]
    assert cli.main(["update"]) == 0
    assert "updated to version 0.2.2" in capsys.readouterr().out


# -- verifier round 1: a system part updates silently only when its manifest changes in words alone --------
_RUN = {"type": "command", "start": ["{python}", "server.py", "--port", "{port}"]}
_HEALTH = {"url": "http://127.0.0.1:{port}/health", "expect": 200, "every_s": 60}
WIDER = {
    "kind_ui_to_app": lambda m: m.update(kind="app", run={"default": _RUN}, health=_HEALTH, ui={"pages": [
        {"id": "main", "title": "Main", "path": "/", "show": "in_desktop"}]}),
    "kind_ui_to_tool": lambda m: m.update(kind="tool", run={"default": _RUN}, health=_HEALTH, abilities={
        "mcp": {"transport": "http", "url": "http://127.0.0.1:{port}/mcp"}}),
    "command_run_with_health": lambda m: m.update(
        run={"default": _RUN}, health={"url": "http://127.0.0.1:{port}/health", "expect": 200, "every_s": 60}),
    "needs_admin": lambda m: m.setdefault("needs", {}).update(admin=True),
    "needs_owner_files_write": lambda m: m.setdefault("needs", {}).update(owner_files="write"),
    "needs_packages": lambda m: m.setdefault("needs", {}).update(packages=[{"id": "freshtable"}]),
    "needs_brain": lambda m: m.setdefault("needs", {}).update(brain={"text": True, "images": True}),
    "needs_hardware": lambda m: m.setdefault("needs", {}).update(hardware={"ram_gb": 8}),
    "new_ui_page": lambda m: m.setdefault("ui", {}).setdefault("pages", []).append(
        {"id": "extra", "title": "Extra", "file": "web/index.html", "show": "in_desktop"}),
    "accepts": lambda m: m.update(accepts=[{"types": [".txt"], "tool": "import_notes"}]),
    "admin_run_command_step": lambda m: m.update(setup_steps=[
        {"id": "boost", "title": "Run this as admin", "kind": "run_command", "shell": "powershell",
         "text": "Set-ExecutionPolicy Unrestricted", "admin": True}]),
    "consent_words": lambda m: m.update(consent="Different words from the ones her Yes covered."),
    "a_new_field": lambda m: m.update(license="Proprietary"),
}


@pytest.mark.parametrize("change", sorted(WIDER))
def test_a_system_part_manifest_change_beyond_words_gets_a_card(wired, repo, drawbridge, change):
    """Drawbridge (the test double) passes everything here, so only the manifest check stands between
    each change and a silent update. Every one must end in a Yes card, with nothing applied."""
    before = _install(wired, "desktop")
    installed = wired.state / "packages" / "desktop"
    files = fsutil.tree_hash(installed)
    path = repo / "desktop" / "puffpai.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    WIDER[change](data)
    data["version"] = "0.3.0"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    got = wired.packages.check_builtins(notices=wired.notices)
    assert [r["status"] for r in got] == ["card"], got
    assert got[0]["card"]["title"] == "PuffPAI Desktop has an update"
    assert fsutil.tree_hash(installed) == files                              # nothing applied
    row = wired.registry.get("desktop")
    assert row["version"] == "0.2.0" and row["manifest_sha"] == before["manifest_sha"]
    assert row["kind"] == "ui" and _audit_lines(wired) == []
    assert len(wired.consent.list(status="pending")) == 1


def test_words_and_the_picture_alone_stay_silent(wired, repo, drawbridge):
    _install(wired, "desktop")
    (repo / "desktop" / "web" / "new-page.js").write_text("// new code, same manifest\n", encoding="utf-8")
    shutil.copy(REAL / "freshtable" / "icons" / "pot.svg", repo / "desktop" / "web" / "icon.svg")
    _bump(repo / "desktop", "0.2.5", name="PuffPAI Desktop", description="Better words.", icon="web/icon.svg")
    got = wired.packages.check_builtins()
    assert got[0]["status"] == "updated", got
    assert (wired.state / "packages" / "desktop" / "web" / "new-page.js").is_file()
    assert wired.registry.get("desktop")["description"] == "Better words."
    assert wired.consent.list(status="pending") == []


def test_manifest_difference_names_every_field_but_the_silent_ones():
    from puffpai_core.packages.manager import manifest_difference
    old = {"id": "x", "kind": "ui", "version": "1", "name": "A", "needs": {"hosts": []}}
    assert manifest_difference(old, {**old, "version": "2", "name": "B", "description": "d", "icon": "i.svg"}) == []
    assert manifest_difference(old, {**old, "needs": {"hosts": [], "admin": True}}) == ["needs"]
    assert manifest_difference(old, {k: v for k, v in old.items() if k != "needs"}) == ["needs"]   # removed
    assert manifest_difference(old, {**old, "kind": "app", "run": {}}) == ["kind", "run"]


# -- verifier round 1: any error in a swap puts the old version back --------------------------------------
@pytest.mark.parametrize("error", [RuntimeError, ValueError, KeyError])
def test_any_error_in_setup_rolls_a_system_part_back(wired, repo, drawbridge, monkeypatch, error):
    from puffpai_core.steward.steward import Steward
    before = _install(wired, "desktop")
    assert wired.steward.start("desktop")["status"] == "running"
    old_files = fsutil.tree_hash(wired.state / "packages" / "desktop")
    _desktop_fix(repo)

    def boom(self, id, expect_tree=None):
        raise error("something odd")
    monkeypatch.setattr(Steward, "setup", boom)
    got = wired.packages.check_builtins()
    assert got[0]["status"] == "not_updated" and "old version is back" in got[0]["line"], got
    assert error.__name__ in got[0]["line"]
    row = wired.registry.get("desktop")
    assert row["status"] == "running" and wired.steward.alive("desktop")
    assert row["version"] == "0.2.0" and row["manifest_sha"] == before["manifest_sha"]
    assert fsutil.tree_hash(wired.state / "packages" / "desktop") == old_files
    assert list((wired.state / "packages" / ".staging").iterdir()) == []      # no orphaned old-* folder


def test_a_running_app_is_rolled_back_with_its_port_and_a_token(wired, repo, drawbridge, monkeypatch):
    """FreshTable as a real process (its own server.py, on this test's Python, no venv)."""
    from puffpai_core.steward.steward import Steward
    path = repo / "freshtable" / "puffpai.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    data["run"] = {"default": {"type": "command", "start": ["{python}", "server.py", "--port", "{port}"]}}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    before = _install(wired, "freshtable")
    row = wired.steward.start("freshtable")
    assert row["status"] == "running" and wired.steward.alive("freshtable")
    port, pids = row["port"], wired.steward.pids("freshtable")
    assert wired.tokens.lookup(wired.steward.token("freshtable")).kind == "package"
    server = repo / "freshtable" / "server.py"
    server.write_text(server.read_text(encoding="utf-8") + "\n# the fix\n", encoding="utf-8")
    _bump(repo / "freshtable", "2.1.1")
    card = wired.packages.check_builtins(notices=wired.notices)[0]
    assert card["status"] == "card"

    def boom(self, id, expect_tree=None):
        raise RuntimeError("setup broke in a way nobody planned")
    monkeypatch.setattr(Steward, "setup", boom)
    try:
        result = yes(wired, card)
        assert result["status"] == "failed" and "old version is back" in result["problems"][0], result
        row = wired.registry.get("freshtable")
        assert row["status"] == "running" and wired.steward.alive("freshtable")
        assert row["port"] == port and wired.steward.info("freshtable").port == port      # the same port
        token = wired.steward.token("freshtable")
        assert token and wired.tokens.lookup(token).kind == "package"                      # a live token
        assert wired.steward.pids("freshtable") != pids                                     # started again
        assert row["version"] == "2.1.0" and row["manifest_sha"] == before["manifest_sha"]
        assert row["yes_record"] == before["yes_record"]
        installed = wired.state / "packages" / "freshtable" / "server.py"
        assert not installed.read_text(encoding="utf-8").endswith("# the fix\n")
        assert list((wired.state / "packages" / ".staging").iterdir()) == []
    finally:
        wired.steward.stop("freshtable")
