"""D155: Toolsmith makes a simple app that runs, with a small local model or with none. Real made-up goals, with a stub brain
and with no brain, to a package that passes ``puffpai check`` (the real Drawbridge, the real Steward), goes in through the
normal path and answers on its port and its tools."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import pytest

from conftest import wait_for, yes
from puffpai_core.packages import manifest as manifest_mod
from puffpai_core.packages import mcp_tools
from puffpai_core.steward.steward import run_block
from puffpai_core.zone3.toolsmith import assemble

GOAL = "Help me keep up with the birds in my backyard and what I spend on seed"
JOB = {"id": "j1", "name": "Bird sightings log", "does": "Log which birds came to the feeder, and when"}
GOOD = {"shape": 3, "title": "Backyard Birds", "thing": "sighting", "ask": "Add a sighting of a cardinal",
        "fields": [{"name": "Bird", "kind": 1}, {"name": "Seen on", "kind": 4}, {"name": "How many", "kind": 3}, {"name": "Notes", "kind": 2}]}


def stub(*answers):
    """A brain that says these in turn, and writes down what it was asked."""
    asked = []

    def brain(system, user):
        asked.append((system, user))
        got = answers[min(len(asked) - 1, len(answers) - 1)]
        if isinstance(got, Exception):
            raise got
        return got
    brain.asked = asked
    return brain


def _post(port, token, path, body, method="POST", kind="application/json", extra=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": kind, **(extra or {})})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw[:1] in (b"{", b"[") else raw
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw[:1] in (b"{", b"[") else raw


def _tool(port, token, name, args):
    status, said = _post(port, token, "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    assert status == 200
    return said["result"]


# -- what the brain is asked, and what becomes of its answer --------------------------------------------------------------
def test_the_brain_only_picks_and_gets_the_whole_goal_and_what_is_on_the_bot(tmp_path):
    brain = stub(GOOD)
    made = assemble.make_app(GOAL, JOB, on_bot=[{"id": "notes", "name": "Family Notes"}, "Photos"], brain=brain, folder=tmp_path / "a")
    assert made.ok and made.from_brain and made.name == "Backyard Birds" and made.shape == assemble.TRACKER
    system, user = brain.asked[0]
    assert GOAL in user and JOB["name"] in user and JOB["does"] in user and "Family Notes" in user and "Photos" in user
    assert "1. notes" in user and "4. records" in user and "JSON" in system
    assert [f["kind"] for f in made.spec["fields"]] == ["text", "date", "number", "long"]
    assert made.spec["tools"] == {"add": "add_sighting", "list": "list_sightings", "change": "change_sighting", "remove": "remove_sighting"}


def test_a_wrong_answer_gets_the_validators_words_back_then_can_be_right(tmp_path):
    bad = {**GOOD, "shape": 9, "fields": [{"name": "Bird", "kind": 77}]}
    brain = stub(bad, GOOD)
    made = assemble.make_app(GOAL, JOB, brain=brain, folder=tmp_path / "a")
    assert made.ok and made.from_brain and len(brain.asked) == 2
    assert "shape: give the number" in brain.asked[1][1] and '"Bird" needs the number' in brain.asked[1][1]


@pytest.mark.parametrize("answers", [
    [None], ["I am sorry, I can't do that."], [RuntimeError("the model stopped")], [["a", "list"]], [{"nonsense": True}] * 5,
    [{**GOOD, "thing": "Not A Word"}] * 5, [{**GOOD, "fields": []}] * 5,
], ids=["none", "words", "raises", "a-list", "nonsense", "bad-thing", "no-fields"])
def test_no_useful_answer_still_makes_an_app(tmp_path, answers):
    brain = stub(*answers)
    made = assemble.make_app(GOAL, JOB, brain=brain, folder=tmp_path / "a")
    assert made.ok and not made.from_brain and made.shape == assemble.TRACKER and len(brain.asked) <= assemble.ASKS
    assert (tmp_path / "a" / "puffpai.json").is_file()


def test_a_field_for_a_secret_is_refused(tmp_path):
    answer = {**GOOD, "fields": [{"name": "Site", "kind": 1}, {"name": "Password", "kind": 1}]}
    brain = stub(answer)
    made = assemble.make_app(GOAL, JOB, brain=brain, folder=tmp_path / "a")
    assert "for the vault" in brain.asked[1][1]
    assert not made.from_brain and "Password" not in json.dumps(made.spec)


def test_what_a_model_says_carries_no_markup_into_the_app(tmp_path):
    hostile = {**GOOD, "title": "<script>alert(1)</script>Birds http://evil.test/x [click](http://evil.test)",
               "fields": [{"name": "<img src=x onerror=alert(1)>Bird", "kind": 1}, {"name": "`rm -rf` Notes", "kind": 2}]}
    made = assemble.make_app(GOAL, JOB, brain=stub(hostile), folder=tmp_path / "a")
    text = "".join((tmp_path / "a" / n).read_text(encoding="utf-8") for n in ("spec.json", "puffpai.json", "README.md"))
    assert made.ok and "<script" not in text and "onerror" not in text and "evil.test" not in text and "`" not in text


def test_a_field_can_not_take_the_name_of_what_a_row_keeps_itself(tmp_path):
    answer = {**GOOD, "shape": 4, "fields": [{"name": "id", "kind": 1}, {"name": "Added", "kind": 4}, {"name": "Bird", "kind": 1}]}
    made = assemble.make_app(GOAL, JOB, brain=stub(answer), folder=tmp_path / "a")
    assert made.from_brain and [f["key"] for f in made.spec["fields"]] == ["added_2", "bird"]


@pytest.mark.parametrize("thing, plural", [("note", "notes"), ("entry", "entries"), ("box", "boxes"), ("supplies", "supplies"), ("day", "days")])
def test_tools_are_named_in_plain_words(tmp_path, thing, plural):
    made = assemble.make_app(GOAL, JOB, brain=stub({**GOOD, "thing": thing}), folder=tmp_path / "a")
    assert made.spec["tools"]["add"] == f"add_{thing}" and made.spec["tools"]["list"] == f"list_{plural}"


def test_a_fields_own_name_settles_its_kind_and_it_reads_plainly(tmp_path):
    answer = {**GOOD, "fields": [{"name": "student_name", "kind": 1}, {"name": "total price", "kind": 2}, {"name": "due date", "kind": 1},
                                 {"name": "website", "kind": 1}, {"name": "pricey things", "kind": 2}]}
    made = assemble.make_app(GOAL, JOB, brain=stub(answer), folder=tmp_path / "a")
    assert [(f["name"], f["kind"]) for f in made.spec["fields"]] == [
        ("Student name", "text"), ("Total price", "number"), ("Due date", "date"), ("Website", "link"), ("Pricey things", "long")]


def test_a_tracker_with_no_number_gets_asked_again(tmp_path):
    brain = stub({**GOOD, "fields": [{"name": "Bird", "kind": 1}]}, GOOD)
    made = assemble.make_app(GOAL, JOB, brain=brain, folder=tmp_path / "a")
    assert made.from_brain and "one field has to be a number" in brain.asked[1][1]


def test_the_generated_code_is_never_the_models(tmp_path):
    made = assemble.make_app(GOAL, JOB, brain=stub({**GOOD, "title": "import os; os.system('x')"}), folder=tmp_path / "a")
    assert made.ok
    for name in assemble.RUNTIME_FILES:
        assert (tmp_path / "a" / name).read_text(encoding="utf-8") == (assemble.RUNTIME / name).read_text(encoding="utf-8")


def test_a_clone_with_windows_line_endings_makes_the_same_app_to_the_byte(tmp_path, monkeypatch):
    """What it writes never depends on how git checked out its own program, so neither does ``puffpai check``'s hash."""
    from puffpai_core.packages import fsutil
    plain = assemble.make_app(GOAL, JOB, folder=tmp_path / "plain")
    windows = tmp_path / "runtime"
    windows.mkdir()
    for name in assemble.RUNTIME_FILES:
        text = (assemble.RUNTIME / name).read_text(encoding="utf-8")
        (windows / name).write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    monkeypatch.setattr(assemble, "RUNTIME", windows)
    made = assemble.make_app(GOAL, JOB, folder=tmp_path / "crlf")
    assert plain.ok and made.ok
    for name in plain.files:
        got = (tmp_path / "crlf" / name).read_bytes()
        assert got == (tmp_path / "plain" / name).read_bytes() and b"\r" not in got, name
    assert fsutil.tree_hash(tmp_path / "crlf") == fsutil.tree_hash(tmp_path / "plain")
    ok, why = assemble.check_made(tmp_path / "crlf", timeout_s=60)
    assert ok, why
    written = {}
    assert assemble.make_app(GOAL, JOB, write=lambda path, text: written.update({path: text})).ok
    assert not any("\r" in text for text in written.values())       # through a try, too


@pytest.mark.parametrize("name, does, goal, shape", [
    ("Meal planner", "Plan meals for the week", "", assemble.LIST),
    ("Trip journal", "", "", assemble.NOTES),
    ("Fuel tracker", "", "", assemble.TRACKER),
    ("Tool library", "Who borrowed which tool", "", assemble.RECORDS),
    ("Rocket parts", "", "keep a log of what I build", assemble.TRACKER),
    ("Something", "", "", assemble.RECORDS),
])
def test_with_no_brain_the_jobs_own_words_pick_the_shape(tmp_path, name, does, goal, shape):
    made = assemble.make_app(goal, {"name": name, "does": does}, folder=tmp_path / "a")
    assert made.ok and made.shape == shape and not made.from_brain and made.name == name


def test_no_job_no_place_or_a_bad_id_is_one_plain_line(tmp_path):
    assert assemble.make_app("", {"name": ""}, folder=tmp_path / "a").why == assemble.NO_WORK
    assert assemble.make_app(GOAL, JOB).why == assemble.NO_PLACE
    assert assemble.make_app(GOAL, JOB, folder=tmp_path / "a", write=print).why == assemble.NO_PLACE
    assert assemble.make_app(GOAL, JOB, folder=tmp_path / "a", package_id="../up").why == assemble.BAD_ID


def test_a_full_folder_is_left_alone(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "mine.txt").write_text("hers")
    made = assemble.make_app(GOAL, JOB, folder=tmp_path / "a")
    assert not made.ok and "not empty" in made.why and sorted(p.name for p in (tmp_path / "a").iterdir()) == ["mine.txt"]


def test_it_writes_through_a_try_and_takes_the_trys_id():
    got = {}
    made = assemble.make_app(GOAL, JOB, package_id="bird-log-1a2b", write=lambda path, text: got.update({path: text}))
    assert made.ok and made.id == "bird-log-1a2b" and sorted(got) == made.files
    manifest = json.loads(got["puffpai.json"])
    assert manifest["id"] == "bird-log-1a2b" and manifest["origin"] == "made" and manifest_mod.validate(manifest) == []


def test_a_try_that_says_no_is_a_plain_line():
    made = assemble.make_app(GOAL, JOB, write=lambda path, text: {"ok": False, "said": "The try is over."})
    assert not made.ok and "The try is over." in made.why


def test_an_id_already_on_the_bot_is_not_taken_again(tmp_path):
    made = assemble.make_app(GOAL, JOB, on_bot=[{"id": "bird-sightings-log", "name": "x"}], folder=tmp_path / "a")
    assert made.id == "bird-sightings-log-2"


def test_a_package_needs_no_host_no_key_and_lists_its_changes(tmp_path):
    made = assemble.make_app(GOAL, JOB, folder=tmp_path / "a")
    m = made.manifest
    assert m["needs"] == {"hosts": [], "keys": []} and m["origin"] == "made" and m["kind"] == "app"
    t = made.spec["tools"]
    assert m["abilities"]["changes"] == [t["add"], t["change"], t["remove"]]
    assert m["ui"]["pages"][0]["path"] == "/" and "does not use the internet" in m["consent"]


def test_its_program_reaches_nothing_outside_and_sets_only_text():
    code = (assemble.RUNTIME / "app.py").read_text(encoding="utf-8")
    assert not re.search(r"\b(urllib\.request|http\.client|socket|subprocess|requests)\b", code)
    page = (assemble.RUNTIME / "page.js").read_text(encoding="utf-8")
    assert "innerHTML" not in page and "outerHTML" not in page and "eval(" not in page


# -- real ones: puffpai check, then the normal way in --------------------------------------------------------------------
GOALS = [
    ("a birdwatching log for my backyard", {"name": "Bird sightings log", "does": "Log which birds came and when"}, None),
    ("run my little candle shop", {"name": "Candle inventory", "does": "What candles are in stock"}, None),
    ("get my chores under control", {"name": "Chore list", "does": ""}, None),
    ("write a family cookbook", {"name": "Recipe notes", "does": "Keep recipes to find later"}, None),
    (GOAL, JOB, GOOD),
]


@pytest.mark.parametrize("goal, job, answer", GOALS, ids=["log", "inventory", "list", "notes", "stub-brain"])
def test_a_made_app_passes_puffpai_check_with_the_real_drawbridge_and_steward(tmp_path, goal, job, answer):
    made = assemble.make_app(goal, job, brain=stub(answer) if answer else None, folder=tmp_path / "a")
    assert made.ok, made.why
    ok, why = assemble.check_made(tmp_path / "a", timeout_s=60)
    assert ok, why


@pytest.mark.parametrize("answer", [None, GOOD], ids=["no-brain", "stub-brain"])
def test_it_goes_in_the_normal_way_starts_and_answers_on_its_port_and_tools(core, drawbridge, tmp_path, answer):
    made = assemble.make_app(GOAL, JOB, on_bot=core.registry.list(), brain=stub(answer) if answer else None, folder=tmp_path / "src" / "made")
    assert made.ok, made.why
    staged = core.packages.add(str(tmp_path / "src" / "made"), adopted="made")
    assert staged["card"]["origin"] == "made" and staged["card"]["hosts"] == []
    assert yes(core, staged)["status"] == "installed"
    assert core.registry.get(made.id)["origin"] == "made" and drawbridge
    if core.registry.get(made.id).get("status") != "running":
        core.steward.start(made.id)
    info = wait_for(lambda: core.steward.info(made.id), 30)
    port, token = info.port, info.token

    def healthy():
        try:
            return _post(port, token, "/health", None, method="GET")[0] == 200
        except OSError:
            return False
    assert wait_for(healthy, 30)
    assert _post(port, "wrong", "/health", None, method="GET")[0] == 401
    assert mcp_tools.refuses_without_token(f"http://127.0.0.1:{port}/mcp")

    # the page, as her browser gets it through the Core
    status, page = _post(port, token, "/", None, method="GET")
    assert status == 200 and b'id="add"' in page and b"page.js" in page
    assert _post(port, token, "/page.js", None, method="GET")[0] == 200

    # the brain's tools, read the way the Core reads them
    manifest = core.steward.read_manifest(made.id)
    values = core.steward.values(made.id, core.steward.folder(made.id), run_block(manifest) or {}, port)
    tools = mcp_tools.list_tools(manifest, values, token, cwd=core.steward.folder(made.id))
    t = made.spec["tools"]
    assert sorted(x["name"] for x in tools) == sorted(t.values()) and all(x["description"] and x["input_schema"] for x in tools)
    assert set(manifest["abilities"]["changes"]) <= {x["name"] for x in tools}

    words = next(f["key"] for f in made.spec["fields"] if f["kind"] == "text")
    added = _tool(port, token, t["add"], {words: "a cardinal"})
    assert not added["isError"]
    row = json.loads(added["content"][0]["text"])
    got = json.loads(_tool(port, token, t["list"], {"search": "cardinal"})["content"][0]["text"])
    assert got["count"] == 1 and got["rows"][0][words] == "a cardinal"
    assert json.loads(_tool(port, token, t["list"], {"search": "owl"})["content"][0]["text"])["count"] == 0
    assert not _tool(port, token, t["change"], {"id": row["id"], words: "two cardinals"})["isError"]

    # what her page sends
    assert _post(port, token, "/api/rows", {words: "a jay"})[0] == 200
    assert _post(port, token, "/api/rows", {words: "x"}, kind="text/plain")[0] == 400      # a form from another site can't write
    status, listed = _post(port, token, "/api/rows", None, method="GET")
    assert status == 200 and listed["count"] == 2
    jay = next(r for r in listed["rows"] if r[words] == "a jay")
    assert _post(port, token, f"/api/rows/{jay['id']}", None, method="DELETE")[0] == 400   # nor remove
    assert _post(port, token, f"/api/rows/{jay['id']}", None, method="DELETE", extra={"X-PuffPAI-App": "1"})[0] == 200
    assert _tool(port, token, t["remove"], {"id": "nothere"})["isError"]

    # it keeps its rows across a restart, in its own data folder
    core.steward.stop(made.id)
    core.steward.start(made.id)
    info = wait_for(lambda: core.steward.info(made.id), 30)
    port, token = info.port, info.token
    assert wait_for(healthy, 30)
    assert json.loads(_tool(port, token, t["list"], {})["content"][0]["text"])["count"] == 1


def test_bad_rows_are_said_in_plain_words(core, drawbridge, tmp_path):
    made = assemble.make_app(GOAL, JOB, brain=stub(GOOD), folder=tmp_path / "src" / "made")
    assert yes(core, core.packages.add(str(tmp_path / "src" / "made"), adopted="made"))["status"] == "installed"
    if core.registry.get(made.id).get("status") != "running":
        core.steward.start(made.id)
    info = wait_for(lambda: core.steward.info(made.id), 30)
    port, token = info.port, info.token
    assert wait_for(lambda: _post(port, token, "/health", None, method="GET")[0] == 200, 30)
    add = made.spec["tools"]["add"]
    said = _tool(port, token, add, {"bird": "x", "how_many": "lots"})
    assert said["isError"] and "How many is a number" in said["content"][0]["text"]
    assert _tool(port, token, add, {"bird": "x", "seen_on": "yesterday"})["isError"]
    assert _tool(port, token, add, {"nothing_like_this": "x"})["isError"]
    assert _tool(port, token, add, {})["isError"]
    assert _post(port, token, "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "no/such"})[1]["error"]["code"] == -32601
