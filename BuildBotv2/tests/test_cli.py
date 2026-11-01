import json

import pytest

from conftest import FIXTURES
from puffpai_core.cli import main
from puffpai_core.registry import Registry, row_from_manifest


def test_check_runs_the_levels_on_freshtable(capsys):
    # The fixture is a manifest without FreshTable's code, so item 1 passes and it can't run.
    assert main(["check", str(FIXTURES / "freshtable")]) == 1
    out = capsys.readouterr().out
    assert "freshtable 2.0.0 (app)" in out and "PASS  1" in out and "does not install" in out


def test_check_fails_in_plain_words(tmp_path, capsys, freshtable):
    del freshtable["consent"]
    freshtable["colour"] = "red"
    (tmp_path / "puffpai.json").write_text(json.dumps(freshtable), encoding="utf-8")
    assert main(["check", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  1" in out and "needs 'consent'" in out and "does not know: 'colour'" in out


def test_check_missing_files(tmp_path, capsys, freshtable):
    (tmp_path / "puffpai.json").write_text(json.dumps(freshtable), encoding="utf-8")
    assert main(["check", str(tmp_path)]) == 1
    assert "'icons/pot.svg' is not in the package" in capsys.readouterr().out


def test_check_no_manifest(tmp_path, capsys):
    assert main(["check", str(tmp_path)]) == 1
    assert "No puffpai.json" in capsys.readouterr().out


def test_list(capsys, freshtable):
    assert main(["list"]) == 0
    assert "Nothing installed yet." in capsys.readouterr().out
    Registry().add(row_from_manifest(freshtable, "made"))
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    assert "freshtable" in out and "installing" in out and "not checked yet" in out


@pytest.mark.parametrize("cmd", ["start", "stop"])
def test_start_stop_need_the_core(capsys, cmd):
    assert main([cmd, "x"]) == 1
    assert "The Core is not running" in capsys.readouterr().out


def test_add_logs_remove_without_a_core(capsys, monkeypatch, drawbridge, hello_copy, state):
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")
    assert main(["add", str(hello_copy)]) == 0
    out = capsys.readouterr().out
    assert "Add Hello Tool?" in out and "Installed hello-tool" in out and "puffpai start hello-tool" in out
    assert main(["logs", "hello-tool"]) == 1 and "No log for hello-tool yet." in capsys.readouterr().out
    assert main(["list"]) == 0 and "hello-tool" in capsys.readouterr().out
    assert main(["remove", "hello-tool", "--delete-data"]) == 0
    assert "Removed hello-tool." in capsys.readouterr().out


def test_add_answered_no(capsys, monkeypatch, drawbridge, hello_copy):
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert main(["add", str(hello_copy)]) == 0 and "Not added." in capsys.readouterr().out
