"""The first real packages (lane L7): each manifest is valid for the contract,
names files that are there, and drops into the Registry with no Core edit."""

import json
from pathlib import Path

import pytest

from puffpai_core.cli import main
from puffpai_core.packages import manifest
from puffpai_core.registry import Registry, row_from_manifest

REPO = Path(__file__).resolve().parents[2]
PACKAGES = REPO / "packages"


def _raw(pkg: str) -> dict:
    return json.loads((PACKAGES / pkg / "puffpai.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("pkg", ["freshtable", "example-page"])
def test_manifest_is_valid_and_its_files_are_there(pkg, capsys, drawbridge, no_brain):
    data, _ = manifest.load(PACKAGES / pkg)
    assert manifest.check_files(PACKAGES / pkg, data) == []
    assert main(["check", str(PACKAGES / pkg)]) == 0
    assert "PASS  1" in capsys.readouterr().out


def test_example_page_is_static_and_drops_in_with_no_core_edit():
    data, _ = manifest.load(PACKAGES / "example-page")
    assert data["kind"] == "page" and "run" not in data
    widget = json.loads((PACKAGES / "example-page" / data["ui"]["widgets"][0]["file"]).read_text(encoding="utf-8"))
    assert widget["items"] and all(isinstance(i["text"], str) for i in widget["items"])
    reg = Registry()
    reg.add(row_from_manifest(data, "local:packages/example-page"))
    row = reg.get("example-page")
    assert row["kind"] == "page" and row["ui"]["tray"][0]["opens"] == "main"


def test_freshtable_registers():
    data, _ = manifest.load(PACKAGES / "freshtable")
    Registry().add(row_from_manifest(data, "made"))
    assert Registry().get("freshtable")["kind"] == "app"


def test_cloud_avatar_passes_check(capsys, drawbridge, no_brain):
    """D48a: preview (SVG or PNG) is required, a .glb model is optional. Puff ships as an image."""
    data, _ = manifest.load(PACKAGES / "cloud-avatar")
    assert "model" not in data["avatar"] and not list((PACKAGES / "cloud-avatar").glob("*.glb"))
    assert manifest.check_files(PACKAGES / "cloud-avatar", data) == []
    assert main(["check", str(PACKAGES / "cloud-avatar")]) == 0
    assert "PASS  1" in capsys.readouterr().out


def test_avatar_preview_is_required_and_must_be_an_image():
    raw = _raw("cloud-avatar")
    assert manifest.validate({**raw, "avatar": {"model": "face.glb", "voice": None}}) == ["avatar needs 'preview'."]
    assert manifest.validate({**raw, "avatar": {"preview": "face.jpg"}})
    assert manifest.validate({**raw, "avatar": {"preview": "face.png", "model": "face.zip"}})
    assert manifest.validate({**raw, "avatar": {"preview": "face.png", "model": "face.glb"}}) == []


def test_cloud_avatar_preview_is_there():
    data = _raw("cloud-avatar")
    assert data["avatar"]["voice"] is None
    assert (PACKAGES / "cloud-avatar" / data["avatar"]["preview"]).is_file()


def test_writing_rules_in_packages():
    bad, wrong_name = [], "beth" + "any"
    for path in PACKAGES.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json", ".md", ".html", ".svg", ".txt"} and ".venv" not in path.parts:
            text = path.read_text(encoding="utf-8")
            if chr(0x2014) in text:
                bad.append(f"{path}: em dash")
            if wrong_name in text.lower():   # spelled apart so the repo-wide rule test passes
                bad.append(f"{path}: the wrong owner name")
    assert bad == []
