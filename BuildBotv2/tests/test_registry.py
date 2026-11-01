import pytest

from puffpai_core.registry import NOT_CHECKED, Registry, RegistryError, row_from_manifest


@pytest.fixture
def reg(state):
    return Registry()


def test_default_path_is_the_bot_folder(reg, state):
    assert reg.path == state / "registry.db" and reg.path.exists()


def test_row_from_manifest_never_copies_health(freshtable):
    row = row_from_manifest(freshtable, "local:C:/code/freshtable")
    assert row["health"] == NOT_CHECKED and row["abilities"] == []
    assert row["needs"]["hardware"] == {"ram_gb": 1}
    assert row["ui"]["tray"][0]["opens"] == "main"
    with pytest.raises(RegistryError, match="probed"):
        row_from_manifest(freshtable, "made", health={"ok": True, "checked_at": None, "reason": ""})


def test_crud(reg, freshtable):
    row = reg.add(row_from_manifest(freshtable, "made"))
    assert reg.get("freshtable") == row
    assert [r["id"] for r in reg.list()] == ["freshtable"]
    assert reg.list(kind="tool") == [] and len(reg.list(kind="app", status="installing")) == 1
    reg.update("freshtable", status="running", port=18420)
    assert reg.get("freshtable")["status"] == "running"
    with pytest.raises(RegistryError, match="already installed"):
        reg.add(row_from_manifest(freshtable, "made"))
    assert reg.remove("freshtable") is True and reg.get("freshtable") is None
    assert reg.remove("freshtable") is False


def test_health_is_probed_only(reg, freshtable):
    row = row_from_manifest(freshtable, "made")
    with pytest.raises(RegistryError, match="probed"):
        reg.add({**row, "health": {"ok": True, "checked_at": "2026-10-21T00:00:00Z", "reason": "fine"}})
    reg.add(row)
    with pytest.raises(RegistryError, match="probed"):
        reg.update("freshtable", health={"ok": True, "checked_at": None, "reason": ""})
    probed = reg.set_health("freshtable", True, "Main page answers.")
    assert probed["health"]["ok"] is True and probed["health"]["checked_at"]


def test_rows_follow_the_schema(reg, freshtable):
    with pytest.raises(RegistryError, match="status"):
        reg.add({**row_from_manifest(freshtable, "made"), "status": "happy"})
    reg.add(row_from_manifest(freshtable, "made"))
    with pytest.raises(RegistryError, match="does not start with 'freshtable.'"):
        reg.update("freshtable", abilities=[{"tool": "other.add", "changes": True}])
    with pytest.raises(RegistryError, match="never changes"):
        reg.update("freshtable", id="x")


def test_validate_honesty(reg, freshtable):
    reg.add(row_from_manifest(freshtable, "made"))
    reg.update("freshtable", abilities=[
        {"tool": "freshtable.add_to_list", "description": "Add an item", "changes": True, "needs_yes": False},
        {"tool": "freshtable.clear_list", "description": "Clear the list", "changes": True},
    ])
    assert reg.validate({"freshtable.add_to_list", "freshtable.clear_list"}) == []
    problems = reg.validate(["freshtable.add_to_list"])
    assert problems == [{"id": "freshtable", "tool": "freshtable.clear_list",
                         "problem": "FreshTable claims freshtable.clear_list, but its MCP server does not list it."}]


def test_program_rows(reg):
    reg.add({"id": "git", "kind": "program", "name": "Git", "origin": "local:C:/Program Files/Git/cmd/git.exe",
             "status": "stopped", "health": dict(NOT_CHECKED)})
    assert reg.get("git")["kind"] == "program"
