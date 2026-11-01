import json

import pytest

from conftest import FIXTURES
from puffpai_core.packages import manifest
from puffpai_core.slots import SLOTS

EXAMPLES = json.loads((FIXTURES / "doc_examples.json").read_text(encoding="utf-8"))


def test_freshtable_from_02_is_valid(freshtable):
    assert manifest.validate(freshtable) == []


@pytest.mark.parametrize("name", sorted(EXAMPLES["valid"]))
def test_doc_examples_are_valid(name):
    assert manifest.validate(EXAMPLES["valid"][name]) == []


@pytest.mark.parametrize("name", sorted(EXAMPLES["invalid_in_docs"]))
def test_known_bad_doc_examples_fail(name):
    assert manifest.validate(EXAMPLES["invalid_in_docs"][name]) != []


def only(problems):
    assert len(problems) >= 1, problems
    text = " | ".join(problems)
    assert "Failed validating" not in text and "\u2014" not in text
    return text


@pytest.mark.parametrize("change, expect", [
    (dict(colour="red"), "does not know: 'colour'"),
    (dict(schema="puffpai-package/2"), "only knows manifests"),
    (dict(kind="gizmo"), "kind must be one of"),
    (dict(id="FreshTable"), "The id must be"),
    (dict(version="two"), "semver"),
    (dict(drop=["consent"]), "needs 'consent'"),
    (dict(drop=["run"]), "An app needs a run block"),
    (dict(drop=["health"]), "needs a health check"),
    (dict(origin="somewhere"), "origin is written by the Core"),
])
def test_bad_manifest_plain_message(make, change, expect):
    drop = change.pop("drop", ())
    assert expect in only(manifest.validate(make(drop=drop, **change)))


def test_unknown_placeholder(make):
    m = make()
    m["run"]["windows"]["start"] = ["{python}", "-m", "freshtable", "--port", "{portt}"]
    text = only(manifest.validate(m))
    assert "run.windows.start[4]" in text and "{port}, {python}" in text


def test_health_must_be_loopback(make):
    m = make(health={"url": "http://0.0.0.0:{port}/health", "expect": 200})
    assert "health is one of" in only(manifest.validate(m))


def test_app_needs_a_page_or_open(make):
    m = make()
    m["ui"] = {"tray": m["ui"]["tray"]}
    assert "ui.pages entry or ui.open" in only(manifest.validate(m))


def test_wildcard_host_refused(make):
    m = make()
    m["needs"]["hosts"] = ["*.com"]
    assert "no wildcards" in only(manifest.validate(m))


def test_setup_step_shape(make):
    good = {"id": "docker", "kind": "run_command", "shell": "powershell", "admin": False,
            "title": "Install Docker Desktop", "why": "This app runs in Docker.",
            "text": "winget install -e --id Docker.DockerDesktop", "check": {"command": ["docker", "version"]}}
    assert manifest.validate(make(setup_steps=[good])) == []
    no_text = {k: v for k, v in good.items() if k != "text"}
    assert "needs text" in only(manifest.validate(make(setup_steps=[no_text])))
    assert "package and blocks are filled in by the Core" in only(manifest.validate(make(setup_steps=[{**good, "package": "x"}])))
    secret = {"id": "key", "kind": "enter_secret", "title": "Paste your NASA API key"}
    assert "needs key" in only(manifest.validate(make(setup_steps=[secret])))


def test_provider_rules():
    base = EXAMPLES["valid"]["Z1V 7 provider vault-store-nas"]
    assert "never plain data" in only(manifest.validate({**base, "reads": "raw"}))
    assert "must declare reads" in only(manifest.validate({k: v for k, v in base.items() if k != "reads"}))
    assert "Unknown slot" in only(manifest.validate({**base, "provides": [{"slot": "zone1.vault", "contract": 1}]}))
    monitor = {**base, "provides": [{"slot": "zone3.net-monitor", "contract": 1}], "reads": "none",
               "abilities": {**base["abilities"], "changes": ["block"]}}
    assert "may not list any abilities.changes" in only(manifest.validate(monitor))
    runtime = {**base, "provides": [{"slot": "zone2.runtime", "contract": 1}], "reads": "gated"}
    assert "reads: raw" in only(manifest.validate(runtime))


def test_provider_fields_only_on_providers(make):
    assert "for kind provider only" in only(manifest.validate(make(reads="none")))


def test_wizard_secret_field_needs_home():
    w = {"schema": "puffpai-package/1", "id": "wiz", "kind": "wizard", "name": "Wizard", "version": "1.0.0",
         "description": "First run.", "author": "Cypher Sisters", "license": "MIT", "consent": "Runs once.",
         "steps": [{"id": "toolsmith", "title": "Toolsmith's model",
                    "fields": [{"id": "key", "type": "secret", "label": "Paste your key"}]}]}
    assert "must name its home" in only(manifest.validate(w))
    w["steps"][0]["fields"][0]["home"] = "vault:toolsmith.anthropic"
    assert manifest.validate(w) == []


def test_not_an_object():
    assert manifest.validate([]) == ["puffpai.json must be one JSON object {...}."]


def test_schema_slots_match_slot_table():
    assert set(manifest.schema()["$defs"]["slot"]["enum"]) == set(SLOTS)


def test_load_and_errors(tmp_path):
    with pytest.raises(manifest.ManifestError, match="No puffpai.json"):
        manifest.load(tmp_path)
    (tmp_path / "puffpai.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(manifest.ManifestError, match="not valid JSON: line 1"):
        manifest.load(tmp_path)


def test_linked_folder_manifest(tmp_path, freshtable):
    (tmp_path / ".buildbot").mkdir()
    (tmp_path / ".buildbot" / "puffpai.json").write_text(json.dumps(freshtable), encoding="utf-8")
    data, path = manifest.load(tmp_path)
    assert data["id"] == "freshtable" and path.parent.name == ".buildbot"


def test_check_files(tmp_path, freshtable):
    assert manifest.check_files(FIXTURES / "freshtable", freshtable) == []
    problems = manifest.check_files(tmp_path, freshtable)
    assert problems == ["run.default.file: the file 'compose.yaml' is not in the package.",
                        "ui.tray[0].icon: the file 'icons/pot.svg' is not in the package."]
    skill = EXAMPLES["valid"]["04 skill plant-care"]
    assert "a skill needs SKILL.md" in manifest.check_files(tmp_path, skill)[0]
