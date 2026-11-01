"""The store apps Trivia and FitFam (lane I10b) and Calendar and AlarmFlow (lane I10b2) as store packages:
they ship off, show in the PuffPAI store (``GET /v1/packages/available``), and each one installs through the
real Package Manager (its Yes card, real Drawbridge), is started by the real Steward on a free port, passes
the Caretaker's health check and answers one tool call over its MCP endpoint with its own token.

Each Core here is the test's own (``core`` fixture: its own bot folder); the Steward's job objects stop
what it started at the end of each test. Combined from both lanes by lane I14."""

import json
from pathlib import Path

import pytest

from conftest import wait_for, yes
from puffpai_core.api import create_app
from puffpai_core.packages.manager import builtin_source
from puffpai_core.registry import row_from_manifest
from puffpai_core.steward.jobs import pid_alive
from puffpai_core.wizard.terminal import BUILTINS
from puffpai_core.zone2 import mcp

REPO = Path(__file__).resolve().parents[2]
#: One tool call per app: (tool, arguments, what its answer starts with).
CALLS = {
    "alarmflow": ("create_timer", {"desc": "Pasta", "minutes": 10}, "Timer set: Pasta, 10 minutes."),
    "calendar": ("add_task", {"title": "Take the bins out", "due": "tomorrow"}, "Task added: Take the bins out"),
    "fitfam": ("log_workout", {"type": "run", "minutes": 25}, "Logged your 25-minute run workout."),
    "trivia": ("record_result", {"correct": 6, "total_questions": 10, "category": "science"},
               "Saved: 6 of 10 right in Science (beginner), 600 points."),
}
#: Lane I10b: the answer word for word, and the file the app saves it in (in its data folder).
SAVED_IN = {"fitfam": "fitfam.json", "trivia": "trivia.json"}


def _owner(core):
    return {"Authorization": f"Bearer {core.tokens.issue_owner_key()}"}


def _manifest(pid):
    return json.loads((REPO / "packages" / pid / "puffpai.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("pid", sorted(CALLS))
def test_ships_off_and_shows_in_the_store(core, pid):
    assert pid not in BUILTINS                                          # setup does not install it
    assert builtin_source(pid) == str((REPO / "packages" / pid).resolve())
    listed = core.app.test_client().get("/v1/packages/available", headers=_owner(core)).json
    row = dict(next(r for r in listed if r["id"] == pid))
    icon = row.pop("icon")
    m = _manifest(pid)
    assert row == {k: m[k] for k in ("id", "name", "kind", "description", "version", "author")}
    assert row["kind"] == "app" and icon.startswith("data:image/svg+xml;base64,")     # its own icon (lane I10a)
    assert core.registry.get(pid) is None


def test_an_installed_app_leaves_the_store(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    try:
        client, owner = app.test_client(), _owner(core)
        assert set(CALLS) <= {p["id"] for p in client.get("/v1/packages/available", headers=owner).json}
        core.registry.add(row_from_manifest(_manifest("trivia"), "made"))
        listed = {p["id"] for p in client.get("/v1/packages/available", headers=owner).json}
        assert "trivia" not in listed and "fitfam" in listed
    finally:
        core.steward.stop_all(remember=False)


@pytest.mark.parametrize("pid", sorted(CALLS))
def test_puffpai_check_passes_level_3(pid, capsys, drawbridge, no_brain):
    """Item 12 (a sample ask) needs the local model; lane I14 ran it by hand against 127.0.0.1:11435."""
    from puffpai_core.cli import main
    assert main(["check", str(REPO / "packages" / pid)]) == 0
    out = capsys.readouterr().out
    assert "FAIL" not in out and "PASS  16" in out and "passes up to Level 3" in out   # only item 12 is ----


@pytest.mark.parametrize("pid", sorted(CALLS))
def test_installs_starts_and_answers_one_tool_call(core, state, pid):
    """The real Drawbridge checks each app on its Yes card (lane I10b's path), for all four."""
    tool, args, said = CALLS[pid]
    staged = core.packages.add(pid)                                     # the store's built-in id
    assert staged["id"] == pid and staged["consent_id"] and staged["floor"] is False
    result = yes(core, staged)
    assert result["status"] == "installed", result

    row = wait_for(lambda: (core.registry.get(pid) or {}).get("status") == "running" and core.registry.get(pid), 120)
    assert row, core.registry.get(pid)
    assert 1024 < row["port"] < 65536 and row["port"] not in (8420, 8421, 8422)
    assert row["origin"].startswith("local:")
    row = wait_for(lambda: core.caretaker.check(pid)["health"]["ok"] and core.registry.get(pid), 60, 0.5)
    assert row["health"]["ok"] is True
    shown = {a["tool"] for a in row["abilities"]}
    assert {f"{pid}.{t}" for t in _manifest(pid)["abilities"]["tools"]} <= shown     # every tool it says it has

    folder = state / "packages" / pid
    manifest = core.steward.manifest(pid)
    got = mcp.call_tool(mcp.target_for(row, manifest, core.steward.token(pid), folder), tool, args)
    assert got["ok"] is True and got["text"].startswith(said), got
    if pid in SAVED_IN:
        assert got["text"] == said
        saved = json.loads((core.steward.data_dir(pid) / SAVED_IN[pid]).read_text(encoding="utf-8"))
        assert saved["last_updated"]
    refused = mcp.call_tool(mcp.target_for(row, manifest, "ppt_wrong", folder), tool, args)
    assert refused["ok"] is False                                       # the MCP endpoint refuses another token

    pids = core.steward.pids(pid)
    assert pids
    r = core.app.test_client().delete(f"/v1/packages/{pid}?keep_data=delete", headers=_owner(core))
    assert r.status_code == 200 and r.json["removed"] and r.json["nothing_running"]
    assert not any(pid_alive(p) for p in pids)
    assert not folder.exists()


def test_alarmflow_rings_through_the_core_with_no_page_open(core, drawbridge, state):
    """The ringing clock runs in AlarmFlow's own process and posts to the real Core notify route
    (the Core's own listener, on a free port, so the package's PUFFPAI_API reaches it)."""
    import threading

    from waitress import create_server

    from puffpai_core.api.app import CORE_LISTENER, stamp
    listener = create_server(stamp(core.app, CORE_LISTENER), host="127.0.0.1", port=0, threads=4)
    thread = threading.Thread(target=listener.run, daemon=True)
    thread.start()
    core.steward.core_api = f"http://127.0.0.1:{listener.effective_port}"
    try:
        staged = core.packages.add("alarmflow")
        assert yes(core, staged)["status"] == "installed"
        row = wait_for(lambda: (core.registry.get("alarmflow") or {}).get("status") == "running"
                       and core.registry.get("alarmflow"), 120)
        assert row
        target = mcp.target_for(row, core.steward.manifest("alarmflow"), core.steward.token("alarmflow"),
                                state / "packages" / "alarmflow")
        assert mcp.call_tool(target, "create_timer", {"desc": "Eggs", "seconds": 2})["text"].startswith("Timer set: Eggs")
        notices = core.app.extensions["puffpai.notices"]
        got = wait_for(lambda: [n for n in notices.list() if n["package"] == "alarmflow"], 30, 0.5)
        assert got and got[0]["title"] == "Eggs" and got[0]["urgency"] == "high"
        assert "Eggs: ringing now" in mcp.call_tool(target, "list", {})["text"]
    finally:
        core.steward.stop_all(remember=False)
        listener.close()
        thread.join(10)
