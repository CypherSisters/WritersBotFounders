"""The Package Manager: add (copy, link, git), the one Yes, install in place, remove, update."""

import json
import shutil
import subprocess

import pytest

from conftest import HELLO, git_init, yes
from puffpai_core.consent import ConsentError
from puffpai_core.packages.manager import AddError


@pytest.fixture
def quiet(core):
    core.packages.start_after_install = False
    return core


def _write(folder, **changes):
    path = folder / "puffpai.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")
    return data


def test_add_copies_into_staging_and_waits_for_the_yes(quiet, drawbridge, hello_copy, state):
    staged = quiet.packages.add(str(hello_copy))
    assert staged["id"] == "hello-tool" and staged["floor"] is False
    assert staged["card"]["title"] == "Add Hello Tool?" and staged["card"]["changes"] == ["write_note"]
    assert quiet.registry.get("hello-tool") is None                       # nothing before the Yes
    assert len(list((state / "packages" / ".staging").iterdir())) == 1
    assert [c["status"] for c in quiet.consent.list()] == ["pending"]
    result = yes(quiet, staged)
    assert result["status"] == "installed", result
    row = quiet.registry.get("hello-tool")
    assert row["origin"] == f"local:{hello_copy.resolve()}" and row["status"] == "stopped"
    assert row["yes_record"] == staged["consent_id"] and row["health"]["checked_at"] is None
    assert (state / "packages" / "hello-tool" / "server.py").is_file()
    assert list((state / "packages" / ".staging").iterdir()) == []         # renamed, not copied again
    assert (state / "data" / "hello-tool").is_dir()
    assert drawbridge and all(p.endswith(tuple("0123456789abcdef")) for p, _ in drawbridge)  # checked in staging


def test_a_no_leaves_nothing(quiet, drawbridge, hello_copy, state):
    staged = quiet.packages.add(str(hello_copy))
    quiet.consent.answer(staged["consent_id"], "no", by="test")
    assert quiet.packages.wait(staged["consent_id"], 10)["status"] == "declined"
    assert quiet.registry.get("hello-tool") is None
    assert list((state / "packages" / ".staging").iterdir()) == []


def test_drawbridge_says_no(quiet, monkeypatch, hello_copy, state):
    from conftest import drawbridge_double
    drawbridge_double(monkeypatch, lambda p, m: {
        "verdict": "block", "reasons": ["hidden_loader"], "line": "Hello: stopped because its page hides encoded code."})
    with pytest.raises(AddError) as e:
        quiet.packages.add(str(hello_copy))
    assert e.value.status == 403 and "hides encoded code" in e.value.problems[0]
    assert list((state / "packages" / ".staging").iterdir()) == []


def test_a_change_after_the_check_is_refused(quiet, drawbridge, hello_copy, state):
    staged = quiet.packages.add(str(hello_copy))
    staging = next((state / "packages" / ".staging").iterdir())
    (staging / "server.py").write_text("print('swapped')", encoding="utf-8")
    result = yes(quiet, staged)
    assert result["status"] == "failed" and "changed after its check" in result["problems"][0]
    assert quiet.registry.get("hello-tool") is None and not (state / "packages" / "hello-tool").exists()


def test_bad_manifest_and_twice(quiet, drawbridge, hello_copy):
    _write(hello_copy, colour="red")
    with pytest.raises(AddError) as e:
        quiet.packages.add(str(hello_copy))
    assert "does not know: 'colour'" in e.value.problems[0]
    with pytest.raises(AddError):
        quiet.packages.add("C:/no/such/folder")
    ok = shutil.copytree(HELLO, hello_copy.parent / "again")
    yes(quiet, quiet.packages.add(str(ok)))
    with pytest.raises(AddError) as e:
        quiet.packages.add(str(ok))
    assert e.value.status == 409 and "already installed" in e.value.problems[0]


def test_link_runs_in_place_and_remove_keeps_the_folder(quiet, drawbridge, hello_copy, state):
    result = yes(quiet, quiet.packages.add(str(hello_copy), link=True))
    assert result["status"] == "installed"
    assert quiet.installs.folder("hello-tool") == hello_copy and not (state / "packages" / "hello-tool").exists()
    assert quiet.registry.get("hello-tool")["origin"] == f"local:{hello_copy}"
    out = quiet.packages.remove("hello-tool")
    assert out["removed"] and out["linked_folder_kept"] and (hello_copy / "server.py").is_file()


@pytest.mark.parametrize("bad, words", [("relative/folder", "full path"), ("C:\\", "whole drive")])
def test_link_refusals(quiet, drawbridge, bad, words):
    with pytest.raises(AddError) as e:
        quiet.packages.add(bad, link=True)
    assert words in e.value.problems[0]


def test_link_refuses_the_bots_own_folder(quiet, drawbridge, state):
    with pytest.raises(AddError) as e:
        quiet.packages.add(str(state), link=True)
    assert "state folder" in e.value.problems[0]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_add_from_git(quiet, drawbridge, hello_copy, tmp_path):
    git = ["git", "-C", str(hello_copy), "-c", "user.name=Cypher Sisters", "-c", "user.email=test@example.invalid"]
    git_init(hello_copy)                  # fails the test at once if init fails (conftest)
    for args in (["add", "-A"], ["commit", "-q", "-m", "hello"]):
        subprocess.run(git + args, check=True, capture_output=True)
    url = hello_copy.resolve().as_uri()
    result = yes(quiet, quiet.packages.add(url))
    assert result["status"] == "installed", result
    assert quiet.registry.get("hello-tool")["origin"] == f"github:{url}"


def test_remove_asks_about_data(quiet, drawbridge, hello_copy, state):
    yes(quiet, quiet.packages.add(str(hello_copy)))
    (state / "data" / "hello-tool" / "notes.txt").write_text("milk", encoding="utf-8")
    out = quiet.packages.remove("hello-tool")
    assert out["removed"] is False and out["question"] == "keep_data"
    assert quiet.registry.get("hello-tool") is not None
    out = quiet.packages.remove("hello-tool", keep_data="keep")
    assert out["removed"] and out["data_kept"] and out["folder_removed"]
    assert (state / "data" / "hello-tool" / "notes.txt").is_file()
    assert not (state / "packages" / "hello-tool").exists() and quiet.registry.get("hello-tool") is None


def test_remove_delete_data(quiet, drawbridge, hello_copy, state):
    yes(quiet, quiet.packages.add(str(hello_copy)))
    (state / "data" / "hello-tool" / "notes.txt").write_text("milk", encoding="utf-8")
    assert quiet.packages.remove("hello-tool", keep_data="delete")["data_kept"] is False
    assert not (state / "data" / "hello-tool").exists()


def test_update_shows_what_changed(quiet, drawbridge, hello_copy, state):
    yes(quiet, quiet.packages.add(str(hello_copy)))
    (state / "data" / "hello-tool" / "notes.txt").write_text("milk", encoding="utf-8")
    _write(hello_copy, version="1.1.0", needs={"hosts": ["api.example.com"]})
    staged = quiet.packages.update("hello-tool")
    changed = staged["what_changed"]
    assert changed["version"] == ["1.0.0", "1.1.0"] and changed["hosts"]["added"] == ["api.example.com"]
    assert yes(quiet, staged)["status"] == "installed"
    row = quiet.registry.get("hello-tool")
    assert row["version"] == "1.1.0" and row["needs"]["hosts"] == ["api.example.com"]
    assert (state / "data" / "hello-tool" / "notes.txt").read_text(encoding="utf-8") == "milk"   # data kept
    assert list((state / "packages" / ".staging").iterdir()) == []


def test_python_true_makes_its_venv_in_the_final_folder(quiet, drawbridge, hello_copy, state):
    run = {"type": "command", "python": True,
           "setup": ['{python} -c "import sys, pathlib; pathlib.Path(\'made.txt\').write_text(sys.prefix)"'],
           "start": ["{python}", "server.py"]}
    _write(hello_copy, run={"windows": run, "default": run})
    assert yes(quiet, quiet.packages.add(str(hello_copy)))["status"] == "installed"
    final = state / "packages" / "hello-tool"
    made = (final / "made.txt").read_text(encoding="utf-8")
    assert (final / ".venv").is_dir() and made.lower() == str(final / ".venv").lower()


def test_failed_setup_marks_it_broken(quiet, drawbridge, hello_copy):
    run = {"type": "command", "setup": ["{python} -c \"raise SystemExit(3)\""], "start": ["{python}", "server.py"]}
    _write(hello_copy, run={"windows": run, "default": run})
    result = yes(quiet, quiet.packages.add(str(hello_copy)))
    assert result["status"] == "failed" and "Setup step 1" in result["problems"][0]
    row = quiet.registry.get("hello-tool")
    assert row["status"] == "broken" and "Setup step 1" in row["health"]["reason"]


def _provider(folder, **extra):
    data = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))
    data.update(kind="provider", provides=[{"slot": "zone2.embedder", "contract": 1}], reads="raw", **extra)
    data.pop("abilities", None)
    (folder / "puffpai.json").write_text(json.dumps(data), encoding="utf-8")


def test_a_raw_reader_provider_needs_the_pin(quiet, drawbridge, hello_copy, monkeypatch):
    _provider(hello_copy)
    staged = quiet.packages.add(str(hello_copy))
    assert staged["floor"] is True
    with pytest.raises(ConsentError) as e:
        quiet.consent.answer(staged["consent_id"], "yes", "tap", card_sha=staged["card_sha"])
    assert e.value.status == 403 and "PIN" in str(e.value)
    with pytest.raises(ConsentError) as e:                                 # no vault PIN set up yet: no Yes
        quiet.consent.answer(staged["consent_id"], "yes", "pin", "4321", card_sha=staged["card_sha"])
    assert e.value.status == 503 and "Set up your vault" in str(e.value)
    from puffpai_core.zone1.archivist import Archivist                     # L3's real name and signature
    monkeypatch.setattr(Archivist, "verify_pin", lambda self, pin: pin == "4321")
    with pytest.raises(ConsentError):
        quiet.consent.answer(staged["consent_id"], "yes", "pin", "1111", card_sha=staged["card_sha"])
    events = []
    quiet.bus.subscribe("provider.*", events.append)
    assert yes(quiet, staged, factor="pin", pin="4321")["status"] == "installed"
    assert [e["payload"] for e in events] == [{"slot": "zone2.embedder", "id": "hello-tool", "version": "1.0.0",
                                               "origin": "local", "floor_suite": "fail"}]
    assert quiet.registry.get("hello-tool")["floor"]["suite"] == "fail"


def test_community_provider_with_admin_never_installs(quiet, drawbridge, hello_copy):
    _provider(hello_copy, needs={"admin": True})
    with pytest.raises(AddError) as e:
        quiet.packages.add(str(hello_copy))
    assert "Verified" in e.value.problems[0]


def test_safe_mode_adds_nothing(quiet, drawbridge, hello_copy):
    quiet.caretaker.safe_mode("test")
    with pytest.raises(AddError) as e:
        quiet.packages.add(str(hello_copy))
    assert e.value.status == 503 and "safe mode" in e.value.problems[0]


def _junction(link, target):
    got = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if got.returncode != 0:
        pytest.skip("could not make a junction here")


def test_junctions_are_never_copied_or_followed(quiet, drawbridge, hello_copy, tmp_path, state):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("Sam's file", encoding="utf-8")
    _junction(hello_copy / "linked", outside)
    yes(quiet, quiet.packages.add(str(hello_copy)))
    final = state / "packages" / "hello-tool"
    assert not (final / "linked").exists()                                 # not copied in
    _junction(final / "node_modules", outside)                             # one made later, by setup
    assert quiet.packages.remove("hello-tool", keep_data="delete")["folder_removed"]
    assert not final.exists() and (outside / "keep.txt").read_text(encoding="utf-8") == "Sam's file"
