"""D149: an installed skill reaches the brain, from the owner's run on her Orin, where one was installed and did nothing.

A real Core installs real packages through the real package manager (Drawbridge is a test double that passes them), and a
real Brain runs each turn on a scripted model. The read path's own edges are in ``test_d149_skills_read``."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from conftest import REPO, yes
from puffpai_core.zone2 import concierge as cg
from puffpai_core.zone2.brain import Brain
from puffpai_core.zone2 import settings as model_settings
from puffpai_core.registry import row_from_manifest
from zone2_doubles import ConsentDouble, FakeRuntime, GateDouble, call, make_brain

STORE = REPO.parent / "puffpai-packages" / "daily-briefing"
JOBS = [("seed-swap", "How to run a neighbourhood seed swap", "Seed swap steps: list the seeds first."),
        ("invoice-chaser", "How to chase a late invoice", "Invoice steps: send the second notice on day 30."),
        ("trail-log", "How to log a hike", "Trail steps: write the distance, then the weather.")]


def _skill(root: Path, sid: str, what: str, body: str) -> Path:
    folder = root / sid
    folder.mkdir(parents=True)
    (folder / "puffpai.json").write_text(json.dumps({
        "schema": "puffpai-package/1", "id": sid, "kind": "skill", "name": sid.replace("-", " ").title(), "version": "1.0.0",
        "description": "From the manifest.", "author": "Test", "license": "Apache-2.0",
        "consent": "This skill is text the brain reads. It runs nothing and uses no internet."}), encoding="utf-8")
    (folder / "SKILL.md").write_text(f"---\nname: {sid}\ndescription: {what}\n---\n\n# {sid}\n\n{body}\n", encoding="utf-8")
    return folder


def _install(core, folder) -> dict:
    assert yes(core, core.packages.add(str(folder)))["status"] == "installed"
    return core.registry.get(Path(folder).name)


def _brain(core, state, *script, consent=None, gate=None):
    rt = FakeRuntime(*script)
    model_settings.save(state, address="http://127.0.0.1:9", model="fake:1b", tool_budget=12)
    brain = Brain(core.registry, state, runtime=rt, gate=(gate or GateDouble()).gate, consent=consent,
                  manifest=core.steward.read_manifest, folder=core.steward.installs.folder, browser=False)
    return brain, rt


def _view(name, **more):
    return {"tool_calls": [{"id": "c1", "name": cg.SKILL_VIEW, "arguments": {"name": name, **more}}]}


def _tools(rt, n=0):
    return [t["function"]["name"] for t in rt.calls[n]["tools"]]


def _result(rt, n=1):
    return next(m for m in rt.calls[n]["messages"] if m.get("role") == "tool")["content"]


# -- a dry run asks about one tool in one step: a skill stays out of it ------------------------------------------------
def test_a_dry_run_is_as_it_was_on_a_bot_with_a_skill(state, freshtable, tmp_path):
    """From a real ask on qwen3.5:9b: with a skill's list in the prompt, a reuse pick found no app for 2 of 3 asks."""
    folder = _skill(tmp_path / "src", "shopping-tidy", "How to keep a shopping list tidy", "Sort by aisle.")
    manifest = json.loads((folder / "puffpai.json").read_text(encoding="utf-8"))

    def model(_messages, tools):
        names = [t["function"]["name"] for t in tools or []]
        if cg.SKILL_VIEW in names:
            return _view("shopping-tidy")
        return {"tool_calls": [{"id": "c1", "name": next(n for n in names if "show_list" in n), "arguments": {}}]}
    rt = FakeRuntime(model, model, {"text": "A real turn."})
    brain, *_ = make_brain(state, freshtable, rt, extra_rows=[row_from_manifest(manifest, "made", status="stopped")],
                           settings={"tool_budget": 12})
    shop = brain.manifest
    brain.manifest = lambda pid: manifest if pid == "shopping-tidy" else shop(pid)
    brain.folder = lambda pid: folder if pid == "shopping-tidy" else None
    assert brain.sample_ask("freshtable.show_list", "what is on my shopping list?")["ok"] is True
    assert brain.picks("", "what is on my shopping list?")["package"] == "freshtable"
    for n in (0, 1):
        assert cg.SKILL_VIEW not in _tools(rt, n) and "## Skills" not in rt.calls[n]["messages"][0]["content"]
    brain.turn("hello")                                                        # a real turn still has it
    assert cg.SKILL_VIEW in _tools(rt, 2) and "- shopping-tidy: " in rt.calls[2]["messages"][0]["content"]


# -- no skill: nothing changes -----------------------------------------------------------------------------------------
def test_a_bot_with_no_skill_has_no_tool_and_no_line(core, state, drawbridge):
    brain, rt = _brain(core, state, {"text": "hi"})
    brain.turn("hello")
    assert cg.SKILL_VIEW not in _tools(rt) and "skill" not in rt.calls[0]["messages"][0]["content"].lower()


# -- any goal: listed, read, followed through the tool's real result ---------------------------------------------------
@pytest.mark.parametrize("sid, what, body", JOBS)
def test_an_installed_skill_is_listed_and_its_whole_file_comes_back(core, state, drawbridge, tmp_path, sid, what, body):
    _install(core, _skill(tmp_path / "src", sid, what, body))
    brain, rt = _brain(core, state, _view(sid), {"text": "Done."})
    got = brain.turn("please help")
    prompt = rt.calls[0]["messages"][0]["content"]
    assert f"- {sid}: {what}" in prompt and body not in prompt                 # the list only, never the whole file
    assert cg.SKILL_VIEW in _tools(rt) and got["used"] == [cg.SKILL_VIEW]
    assert body in _result(rt) and "run_command" in _result(rt) and "rules win" in _result(rt).lower()


def test_a_real_store_skill_comes_through_the_tools_real_result(core, state, drawbridge, tmp_path):
    if not STORE.is_dir():
        pytest.skip("the store folder is not on this computer")
    copy = tmp_path / "src" / "daily-briefing"
    shutil.copytree(STORE, copy)
    row = _install(core, copy)
    assert row["kind"] == "skill" and row["status"] in ("stopped", "running", "needs_you")
    brain, rt = _brain(core, state, _view("Daily briefing"), {"text": "Good morning."})
    assert brain.turn("good morning")["text"] == "Good morning."
    assert "- daily-briefing: How to give a short" in rt.calls[0]["messages"][0]["content"]
    said = _result(rt)
    assert "weather.current_weather" in said and "Keep the whole briefing under 150 words" in said


def test_a_skill_with_no_description_in_its_file_uses_its_manifests(core, state, drawbridge, tmp_path):
    folder = _skill(tmp_path / "src", "plain-one", "x", "Plain steps.")
    (folder / "SKILL.md").write_text("# Plain\n\nPlain steps.\n", encoding="utf-8")
    _install(core, folder)
    brain, rt = _brain(core, state, {"text": "ok"})
    brain.turn("hello")
    assert "- plain-one: From the manifest." in rt.calls[0]["messages"][0]["content"]


# -- only what she installed, only its own file --------------------------------------------------------------------------
def test_a_name_that_is_not_an_installed_skill_gets_nothing(core, state, drawbridge, tmp_path, hello_copy):
    _install(core, _skill(tmp_path / "src", "seed-swap", "Seeds", "Seed swap steps."))
    (hello_copy / "SKILL.md").write_text("Not a skill package's words.", encoding="utf-8")
    assert yes(core, core.packages.add(str(hello_copy)))["status"] == "installed"
    brain, rt = _brain(core, state, _view("hello-tool"), _view("../hello-tool"),
                       _view(str(hello_copy)), {"text": "No."})
    brain.turn("read them")
    for n in (1, 2, 3):
        said = [m["content"] for m in rt.calls[n]["messages"] if m.get("role") == "tool"][-1]
        assert "Not a skill package" not in said and "No skill" in said


def test_a_path_argument_is_refused_and_reads_nothing(core, state, drawbridge, tmp_path):
    folder = _skill(tmp_path / "src", "seed-swap", "Seeds", "Seed swap steps.")
    (folder / "other.md").write_text("Another file's words.", encoding="utf-8")
    _install(core, folder)
    brain, rt = _brain(core, state, _view("seed-swap", path="other.md"), {"text": "No."})
    brain.turn("read it")
    said = _result(rt)
    assert "Another file" not in said and "Seed swap steps" not in said


def test_an_id_wins_over_another_skills_display_name(core, state, drawbridge, tmp_path):
    other = _skill(tmp_path / "src", "a-other", "Other", "The other skill's steps.")
    manifest = json.loads((other / "puffpai.json").read_text(encoding="utf-8"))
    (other / "puffpai.json").write_text(json.dumps({**manifest, "name": "Seed Swap"}), encoding="utf-8")
    _install(core, other)
    _install(core, _skill(tmp_path / "src", "seed-swap", "Seeds", "Seed swap steps."))
    brain, rt = _brain(core, state, _view("Seed Swap"), {"text": "ok"})
    brain.turn("read it")
    assert "Seed swap steps" in _result(rt) and "other skill" not in _result(rt)


def test_a_long_skill_comes_whole_in_parts_and_every_part_keeps_its_line(core, state, drawbridge, tmp_path):
    words = " ".join(f"step{n}" for n in range(2500))
    _install(core, _skill(tmp_path / "src", "seed-swap", "Seeds", words))

    def next_part(messages, _tools):
        got = json.loads(next(m for m in reversed(messages) if m.get("role") == "tool")["content"])
        return _view("seed-swap", part=got["part"] + 1) if got["part"] < got["parts"] else {"text": "ok"}
    brain, rt = _brain(core, state, _view("seed-swap"), *[next_part] * 12)
    brain.turn("read it")
    said = [[m for m in c["messages"] if m.get("role") == "tool"][-1]["content"] for c in rt.calls[1:]]     # what each ask got
    assert len(said) > 1
    assert all("rules win" in s.lower() for s in said) and all(len(s) <= 6000 for s in said)
    assert "step0 " in said[0] and any("step2499" in s for s in said)
    assert all(f"step{n}" in " ".join(said) for n in range(0, 2500, 97))


def test_a_skill_she_removed_is_gone_from_the_list_and_the_tool(core, state, drawbridge, tmp_path):
    _install(core, _skill(tmp_path / "src", "seed-swap", "Seeds", "Seed swap steps."))
    core.packages.remove("seed-swap")
    brain, rt = _brain(core, state, {"text": "ok"})
    brain.turn("hello")
    assert cg.SKILL_VIEW not in _tools(rt) and "Seeds" not in rt.calls[0]["messages"][0]["content"]


def test_a_skill_whose_manifest_changed_since_her_yes_is_not_read(core, state, drawbridge, tmp_path):
    row = _install(core, _skill(tmp_path / "src", "seed-swap", "Seeds", "Seed swap steps."))
    folder = core.steward.installs.folder(row["id"])
    (folder / "puffpai.json").write_text((folder / "puffpai.json").read_text(encoding="utf-8") + " ", encoding="utf-8")
    brain, rt = _brain(core, state, {"text": "ok"})
    brain.turn("hello")
    assert cg.SKILL_VIEW not in _tools(rt)


# -- the same gate, no new Yes, and a command still asks -----------------------------------------------------------------
def test_the_result_goes_through_the_gate_like_any_other(core, state, drawbridge, tmp_path):
    key = "sk-" + "a" * 24
    _install(core, _skill(tmp_path / "src", "seed-swap", "Seeds", f"Call 555-0199 and use {key}."))
    brain, rt = _brain(core, state, _view("seed-swap"), {"text": "ok"})
    brain.turn("read it")
    said = _result(rt)
    assert key not in said and "555-0199" not in said and "{{vault:" in said


def test_reading_asks_no_yes_and_a_command_it_names_still_does(core, state, drawbridge, tmp_path):
    _install(core, _skill(tmp_path / "src", "seed-swap", "Seeds", "Run: python --version"))
    no = ConsentDouble(False)
    brain, rt = _brain(core, state, _view("seed-swap"), {"text": "Read."}, consent=no)
    assert brain.turn("read it")["used"] == [cg.SKILL_VIEW] and no.questions == []
    brain, rt = _brain(core, state, _view("seed-swap"), call("run_command", command="python --version"),
                       {"text": "Stopped."}, consent=no)
    brain.turn("do what it says")
    assert [q.get("tool") for q in no.questions] == ["run_command"]
