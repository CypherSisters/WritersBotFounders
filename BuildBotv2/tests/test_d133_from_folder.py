"""D133: her own code folder with no manifest, run in place, on the same Yes card."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai_core.packages import from_folder, manifest as manifest_mod

NODE_APP = {"name": "soup-list", "description": "A list of soups.", "scripts": {"start": "node server.js"}}


def _node_folder(tmp_path, name="Soup List") -> Path:
    folder = tmp_path / name
    folder.mkdir()
    (folder / "package.json").write_text(json.dumps(NODE_APP), encoding="utf-8")
    (folder / "server.js").write_text("require('http').createServer((q, s) => s.end('soup')).listen(process.env.PORT);\n", encoding="utf-8")
    return folder


def test_it_writes_the_cores_own_file_and_never_at_the_top_of_her_code(tmp_path):
    folder = _node_folder(tmp_path)
    before = sorted(p.name for p in folder.iterdir())
    assert from_folder.write_manifest(folder) == ""
    assert sorted(p.name for p in folder.iterdir()) == sorted(before + [".buildbot"]) and not (folder / "puffpai.json").exists()
    data, path, _sha = manifest_mod.load_with_sha(folder)
    assert path == folder / ".buildbot" / "puffpai.json"
    assert data["id"] == "soup-list" and data["kind"] == "app" and data["name"] == "soup-list" and data["description"] == "A list of soups."
    assert data["needs"]["hosts"] == [] and "your own code" in data["consent"] and str(folder) in data["consent"]
    assert data["ui"]["pages"][0]["show"] == "new_tab"
    text = json.dumps(data["run"])
    assert "{port}" in text or "PORT" in text


@pytest.mark.parametrize("name,want", [("My Code!", "my-code"), ("2fast", "fast"), ("___", "my-code"), ("x", "my-code"), ("A" * 60, "a" * 40)])
def test_the_id_follows_the_manifests_rule(tmp_path, name, want):
    assert from_folder.package_id(tmp_path / name) == want


def test_nothing_to_say_how_it_starts_writes_nothing(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    (folder / "todo.txt").write_text("buy leeks", encoding="utf-8")
    assert from_folder.write_manifest(folder) == from_folder.NOTHING_SAYS
    assert not (folder / ".buildbot").exists()


def test_a_buildbot_folder_that_is_a_link_is_refused(tmp_path):
    folder, elsewhere = _node_folder(tmp_path), tmp_path / "elsewhere"
    elsewhere.mkdir()
    try:
        (folder / ".buildbot").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pytest.skip("this account can't make a link")
    assert "link" in from_folder.write_manifest(folder) and not list(elsewhere.iterdir())


def test_the_manager_writes_it_only_with_no_manifest_and_only_for_a_link(state, tmp_path, freshtable):
    from puffpai_core.api import create_app
    core = create_app(state, port=51234).extensions["puffpai"]
    folder = _node_folder(tmp_path)
    got = core.packages.add(str(folder), link=True)
    assert got["id"] == "soup-list" and got["consent_id"] and (folder / ".buildbot" / "puffpai.json").exists()
    assert core.consent.get(got["consent_id"]) is not None                  # the same one Yes card
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(Exception, match="says how to start"):
        core.packages.add(str(empty), link=True)
    copied = _node_folder(tmp_path, "Not Linked")
    with pytest.raises(Exception):
        core.packages.add(str(copied), link=False)                           # a copy still needs its own manifest
    assert not (copied / ".buildbot").exists()
