"""FitFam's own server (packages/fitfam/server.py): people from the Core's list,
workouts, sets, streaks, goals, the workout maker, the page's changes, the
widget, the MCP tools and every route's token.

Every server here listens on a free port on 127.0.0.1 and is stopped by the
test that started it; nothing is left running."""

import contextlib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest



PKG = Path(__file__).resolve().parents[2] / "packages" / "fitfam"
TOKEN = "test-token-" + "f" * 20

_spec = importlib.util.spec_from_file_location("fitfam_server", PKG / "server.py")
ff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ff)

_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


@contextlib.contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@contextlib.contextmanager
def fake_core(people):
    """A stand-in for the Core API's people route; it records who asked."""
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append({"path": self.path, "auth": self.headers.get("Authorization")})
            out = json.dumps(people).encode()
            self.send_response(200 if self.headers.get("Authorization") == f"Bearer {TOKEN}" else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    with running(ThreadingHTTPServer(("127.0.0.1", 0), H)) as url:
        yield url, seen


FAMILY = [{"id": "ada", "name": "Ada", "emoji": "🏃", "role": "adult"},
          {"id": "bo", "name": "Bo Park", "emoji": "", "role": "child"}]


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data" / "fitfam"


@pytest.fixture
def base(data):
    with running(ff.make_server(0, data, TOKEN, None)) as url:
        yield url


@pytest.fixture
def family(data):
    with fake_core(FAMILY) as (api, seen), running(ff.make_server(0, data, TOKEN, api)) as url:
        yield url, seen


def call(url, method="GET", body=None, token=TOKEN, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if token:
        h["Authorization"] = f"Bearer {token}"
    raw = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=raw, method=method, headers=h)
    try:
        with _open(req, timeout=10) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text and "json" in r.headers.get("Content-Type", "") else text)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        return e.code, (json.loads(text) if text.startswith("{") else text)


def mcp(base, method, params=None, token=TOKEN, id=1):
    return call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}, token)


def tool(base, tool_name, /, **args):
    code, out = mcp(base, "tools/call", {"name": tool_name, "arguments": args})
    assert code == 200 and not out["result"]["isError"], out
    return out["result"]["content"][0]["text"]


def tool_error(base, tool_name, /, **args):
    code, out = mcp(base, "tools/call", {"name": tool_name, "arguments": args})
    assert code == 200 and out["result"]["isError"], out
    return out["result"]["content"][0]["text"]


# ------------------------------------------------------------ the library and the page

def test_the_library_is_whole():
    text = (PKG / "exercises.json").read_text(encoding="utf-8")
    assert text.startswith('{"provenance": "Harvested from PuffPAIBuildBot@774e27a4:dashboard/public/fitfam-app.jsx')
    lib = json.loads(text)
    assert {k: len(v) for k, v in lib["exercises"].items()} == {"calisthenics": 15, "kettlebell": 12, "rom": 15, "gym": 8}
    for pool in lib["exercises"].values():
        for e in pool:
            assert set(e["levels"]) == set(lib["levels"]) and e["name"] and e["desc"] and e["muscle"]
    assert all(set(t["pool"]) <= set(lib["exercises"]) for t in lib["types"])


def test_health_page_and_empty_widget(base):
    assert call(base + "/health") == (200, "ok")
    code, page = call(base + "/")
    assert code == 200 and "FitFam" in page and "BASE + 'api/'" in page
    assert "/api/pai" not in page and "vendor/" not in page and "http://" not in page and "https://" not in page
    assert call(base + "/widget/streaks") == (200, {"items": [{"text": "You: 0-day streak, 0 workouts"}]})
    assert call(base + "/people")[1] == [{"id": "me", "name": "You", "emoji": "", "role": "adult"}]


# ------------------------------------------------------------ one person ("You")

def test_log_workout_and_status(base, data):
    assert tool(base, "log_workout", type="run", minutes=30) == "Logged your 30-minute run workout."
    saved = json.loads((data / "fitfam.json").read_text(encoding="utf-8"))
    h = saved["users"]["me"]["history"][0]
    assert h["type"] == "run" and h["duration"] == 30 and saved["users"]["me"]["totalWorkouts"] == 1
    out = tool(base, "status")
    assert out.startswith("You: 1-day streak, 1 workout in all, 1 this week. Last workout: run (30 min) on ")
    assert call(base + "/widget/streaks")[1] == {"items": [{"text": "You: 1-day streak, 1 workout"}]}
    assert "minutes is a number from 1 to 1440" in tool_error(base, "log_workout", type="run", minutes=0)


def test_log_set_matches_the_library(base, data):
    assert tool(base, "log_set", exercise="bench press", reps=5, weight=135, rpe=8) == \
        "Logged a set of Bench Press for you: 135 lb x 5 at RPE 8."
    assert tool(base, "log_set", exercise="pull-ups", reps=8) == "Logged a set of Pull-Up / Inverted Rows for you: 8 reps."
    s = json.loads((data / "fitfam.json").read_text(encoding="utf-8"))["users"]["me"]["sets"]
    assert [x["exerciseId"] for x in s] == ["g2", "c8"]
    u = json.loads((data / "fitfam.json").read_text(encoding="utf-8"))["users"]["me"]
    assert u["totalWorkouts"] == 1                               # two sets on one day count once (as v1)
    out = tool(base, "history")
    assert "Recent sets:" in out and "Bench Press: 135 lb x 5" in out
    assert "RPE is a number from 0 to 10" in tool_error(base, "log_set", exercise="squat", reps=5, rpe=11)


def test_streak_counts_days_in_a_row():
    today = date(2026, 10, 20)
    iso = lambda d: datetime(d.year, d.month, d.day, 9).isoformat()
    u = {"history": [{"date": iso(today - timedelta(days=n))} for n in (1, 2, 3, 5)], "sets": []}
    assert ff.streak_of(u, today) == 3                            # nothing today yet: still alive from yesterday
    u["sets"].append({"date": iso(today)})
    assert ff.streak_of(u, today) == 4
    assert ff.streak_of({"history": [{"date": iso(today - timedelta(days=2))}], "sets": []}, today) == 0


def test_suggest_workout_makes_a_plan_and_logs_nothing(base, data):
    out = tool(base, "suggest_workout", type="kettlebell", minutes=30, level="hard")
    lines = out.splitlines()
    assert lines[0] == "A 30-minute Kettlebell workout (challenging):"
    assert len([ln for ln in lines if re.match(r"^\d+\. ", ln)]) == 5
    assert not (data / "fitfam.json").exists()
    assert "Range of Motion" in tool(base, "suggest_workout", type="yoga", minutes=15)
    assert "no 'juggling' workout" in tool_error(base, "suggest_workout", type="juggling")
    code, plan = call(base + "/suggest?type=gym&minutes=60&level=easy")
    assert code == 200 and plan["label"] == "Gym Strength" and len(plan["exercises"]) == 8   # the gym pool has 8
    assert all(e["prescription"] == e["levels"]["easy"] for e in plan["exercises"])


def test_goals(base):
    assert tool(base, "list_goals") == "No fitness goals yet for you."
    assert tool(base, "add_goal", goal="Hold a 2-minute plank", target=120, unit="seconds") == \
        "Added a goal for you: Hold a 2-minute plank (120 seconds)."
    assert tool(base, "update_goal", goal="plank", current=90) == \
        "Updated your goal Hold a 2-minute plank: 90 of 120 seconds."
    assert "Goal reached!" in tool(base, "update_goal", goal="plank", current=150)
    assert "(100%) done" in tool(base, "list_goals")
    assert "no goal like 'swim'" in tool_error(base, "update_goal", goal="swim", current=1)


def test_units_are_kept_and_shown(base, data):
    """2.1.0: a set's weight is lb or kg, a workout can carry a distance in mi or km."""
    assert tool(base, "log_set", exercise="goblet squat", reps=10, weight=16, unit="kilos") ==         "Logged a set of Goblet Squat for you: 16 kg x 10."
    assert tool(base, "log_workout", type="run", minutes=30, distance=5, unit="km") ==         "Logged your 30-minute run workout (5 km)."
    me = json.loads((data / "fitfam.json").read_text(encoding="utf-8"))["users"]["me"]
    assert me["sets"][0]["unit"] == "kg" and me["history"][0]["distance"] == 5 and me["history"][0]["distanceUnit"] == "km"
    out = tool(base, "history")
    assert "run (30 min, 5 km)" in out and "Goblet Squat: 16 kg x 10" in out
    assert "unit is kg or lb" in tool_error(base, "log_set", exercise="squat", reps=5, weight=10, unit="stone")


def test_personal_records(base):
    assert tool(base, "personal_records") == "No personal records yet for you."
    tool(base, "log_set", exercise="bench press", reps=5, weight=135)
    tool(base, "log_set", exercise="bench press", reps=1, weight=75, unit="kg")      # 75 kg once beats 135 lb x 5 (about 71 kg)
    tool(base, "log_set", exercise="push-ups", reps=20)
    out = tool(base, "personal_records")
    assert out.splitlines()[0] == "Your personal records:"
    assert "Bench Press: 75 kg x 1" in out and "Push-Ups: 20 reps" in out
    assert "135 lb" not in out
    assert "Bench Press" not in tool(base, "personal_records", exercise="push-ups")


def test_list_exercises_by_muscle_and_kind(base):
    out = tool(base, "list_exercises", muscle="legs")
    assert out.splitlines()[1].startswith("legs: Bodyweight Squats") and "Barbell Back Squat" in out
    assert "posterior: Kettlebell Swings" in tool(base, "list_exercises", muscle="glutes")
    out = tool(base, "list_exercises", type="kettlebell")
    assert "Goblet Squat" in out and "Push-Ups" not in out
    assert "Muscle groups:" in tool_error(base, "list_exercises", muscle="ears")


def test_the_bot_can_undo(family):
    base, _seen = family
    tool(base, "log_workout", type="run", minutes=20, person="Ada")
    tool(base, "log_workout", type="swim", minutes=30, person="Ada")
    assert tool(base, "remove_workout", person="Ada", workout="run").startswith("Removed Ada's run workout from ")
    assert "no run workout logged for Ada" in tool_error(base, "remove_workout", person="Ada", workout="run")
    tool(base, "log_set", exercise="bench press", reps=5, weight=100, person="Bo")
    tool(base, "log_set", exercise="deadlift", reps=3, weight=200, person="Bo")
    assert tool(base, "remove_set", person="Bo", exercise="bench") == "Removed Bo Park's set of Bench Press: 100 lb x 5."
    assert "Deadlift" in tool(base, "history", person="Bo")
    tool(base, "add_goal", goal="Hold a 2-minute plank", target=120, unit="seconds", person="Ada")
    assert tool(base, "remove_goal", goal="plank", person="Ada") == "Removed Ada's goal Hold a 2-minute plank."
    code, out = call(base + "/api/remove_set", "POST", {"person": "bo", "id": "nope"})
    assert code == 400


# ------------------------------------------------------------ the Core's people (D12)

def test_people_come_from_the_core_with_the_token(family):
    base, seen = family
    assert [p["name"] for p in call(base + "/people")[1]] == ["Ada", "Bo Park"]
    assert seen[0] == {"path": "/v1/people", "auth": f"Bearer {TOKEN}"}
    assert "Logged Ada's 20-minute swim workout." == tool(base, "log_workout", type="swim", minutes=20, person="ada")
    assert "Logged Bo Park's" in tool(base, "log_workout", type="yoga", minutes=15, person="Bo")    # a first name
    assert "say who it is for" in tool_error(base, "log_workout", type="run", minutes=10)
    assert "does not know 'Sam'" in tool_error(base, "log_workout", type="run", minutes=10, person="Sam")
    status = tool(base, "status")
    assert status.splitlines()[0].startswith("Ada: 1-day streak") and status.splitlines()[1].startswith("Bo Park: 1-day")
    assert len(seen) == 1                                           # read once, then kept for a while


def test_people_already_in_a_v1_file_still_count(data):
    data.mkdir(parents=True)
    v1 = {"users": {"alex": {"name": "Adult 1", "emoji": "", "prefs": {"level": "mid", "type": "full", "duration": 45},
                             "goals": [], "history": [{"date": "2026-09-01T10:00:00", "type": "run", "duration": 30,
                                                       "notes": ""}], "streak": 0, "totalWorkouts": 1}},
          "last_updated": "2026-09-01T10:00:00"}
    (data / "fitfam.json").write_text(json.dumps(v1), encoding="utf-8")
    with running(ff.make_server(0, data, TOKEN, None)) as base:
        assert [p["id"] for p in call(base + "/people")[1]] == ["alex"]
        assert tool(base, "status").startswith("Adult 1: 0-day streak, 1 workout in all")
        assert "Logged a set of Goblet Squat for Adult 1" in tool(base, "log_set", exercise="goblet squat", reps=10, weight=35)
        assert "Recent sets" in tool(base, "history") and "run (30 min)" in tool(base, "history")


def test_no_core_answer_means_fitfam_alone(data):
    with running(ff.make_server(0, data, TOKEN, "http://127.0.0.1:1")) as base:
        assert [p["id"] for p in call(base + "/people")[1]] == ["me"]


# ------------------------------------------------------------ the page's changes

def test_page_actions(base):
    code, out = call(base + "/api/complete_workout", "POST", {"person": "me", "typeName": "Full Session", "level": "mid",
                                                              "duration": 30, "exerciseCount": 5, "elapsed": 1500})
    assert code == 200 and out["data"]["users"]["me"]["history"][0]["exerciseCount"] == 5
    assert call(base + "/api/save_prefs", "POST", {"person": "me", "type": "gym", "level": "easy", "duration": 15})[0] == 200
    assert call(base + "/data")[1]["users"]["me"]["prefs"] == {"type": "gym", "level": "easy", "duration": 15}
    assert call(base + "/api/save_prefs", "POST", {"person": "me", "type": "nap", "level": "easy", "duration": 15})[0] == 400
    out = call(base + "/api/add_goal", "POST", {"person": "me", "goal": "10 pull-ups", "target": 10})[1]
    gid = out["data"]["users"]["me"]["goals"][0]["id"]
    assert call(base + "/api/update_goal", "POST", {"person": "me", "goal": gid, "current": 4})[0] == 200
    assert call(base + "/api/remove_goal", "POST", {"person": "me", "goal": gid})[1]["data"]["users"]["me"]["goals"] == []
    out = call(base + "/api/remove_workout", "POST", {"person": "me", "index": 0})[1]
    assert out["data"]["users"]["me"]["history"] == [] and out["data"]["users"]["me"]["totalWorkouts"] == 0
    assert call(base + "/api/remove_workout", "POST", {"person": "me", "index": 3})[0] == 400
    assert call(base + "/api/log_workout", "POST", {"type": "run", "minutes": 5})[0] == 404     # tools go through /mcp
    assert call(base + "/api/__init__", "POST", {})[0] == 404


def test_unreadable_file_is_never_written_over(data):
    data.mkdir(parents=True)
    (data / "fitfam.json").write_text("{not json", encoding="utf-8")
    with running(ff.make_server(0, data, TOKEN, None)) as base:
        assert call(base + "/data")[0] == 500
        assert call(base + "/api/save_prefs", "POST", {"person": "me", "type": "gym", "level": "easy", "duration": 15})[0] == 409
        assert "will not write over it" in tool_error(base, "log_workout", type="run", minutes=5)
    assert (data / "fitfam.json").read_text(encoding="utf-8") == "{not json"


# ------------------------------------------------------------ MCP and its token

@pytest.mark.parametrize("method,path,body", [
    ("GET", "/", None), ("GET", "/health", None), ("GET", "/data", None), ("GET", "/updated", None),
    ("GET", "/people", None), ("GET", "/exercises", None), ("GET", "/suggest", None), ("GET", "/widget/streaks", None),
    ("POST", "/api/complete_workout", {"person": "me", "typeName": "x", "level": "mid", "duration": 1, "exerciseCount": 1}),
    ("PUT", "/data", {"users": {}}),
    ("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
])
def test_every_route_refuses_without_the_token(base, method, path, body):
    """D48d: not only /mcp. The Desktop reaches the page through /pkg/, which adds the token."""
    assert call(base + path, method, body, token=None)[0] == 401
    assert call(base + path, method, body, token="wrong")[0] == 401


def test_refused_calls_change_nothing(base):
    assert call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": "log_workout", "arguments": {"type": "run", "minutes": 5}}},
                token=None)[0] == 401
    assert call(base + "/api/complete_workout", "POST", {"person": "me", "typeName": "x", "level": "mid",
                                                         "duration": 1, "exerciseCount": 1}, token=None)[0] == 401
    assert call(base + "/data")[1]["users"] == {}


def test_started_without_a_token_it_refuses_everything(data):
    with running(ff.make_server(0, data, None, None)) as base:
        assert call(base + "/health", token=None)[0] == 401
        assert call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    token=None, headers={"Authorization": "Bearer "})[0] == 401


def test_mcp_basics(base):
    code, out = mcp(base, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "0"}})
    assert code == 200 and out["result"]["serverInfo"]["name"] == "fitfam"
    assert call(base + "/mcp", "POST", {"jsonrpc": "2.0", "method": "notifications/initialized"},
                token=None, headers={"X-PuffPAI-Key": TOKEN})[0] == 202
    assert mcp(base, "tools/call", {"name": "complete_workout"})[1]["error"]["code"] == -32602   # page only
    assert mcp(base, "nope")[1]["error"]["code"] == -32601


def test_manifest_matches_the_live_tool_list(base):
    """Level 2 items 9 to 11, and a sample ask for every tool."""
    m = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))     # raw: abilities.tools needs lane i10c's schema
    tools = mcp(base, "tools/list")[1]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert set(m["abilities"]["changes"]) <= names
    assert set(m["abilities"]["examples"]) == names
    assert all(m["abilities"]["examples"][n] and m["abilities"]["examples"][n][0].strip() for n in names)
    assert {t["name"] for t in tools if not t["annotations"]["readOnlyHint"]} == set(m["abilities"]["changes"])
    assert all(t["description"] and t["inputSchema"]["type"] == "object" for t in tools)
    assert m["needs"]["hosts"] == []
    widget = m["ui"]["widgets"][0]
    assert widget["type"] == "list" and widget["path"] == "/widget/streaks"


def test_tool_words_pass_drawbridges_screen(base):
    from puffpai_core.zone3.drawbridge import screen_tool_words
    assert screen_tool_words(mcp(base, "tools/list")[1]["result"]["tools"]) == {}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_script_parses(tmp_path):
    page = (PKG / "web" / "index.html").read_text(encoding="utf-8")
    assert page.splitlines()[1].startswith("<!-- Harvested from PuffPAIBuildBot@774e27a4:dashboard/public/fitfam-app.jsx")
    script = re.findall(r"<script>(.*?)</script>", page, re.S)[-1]
    (tmp_path / "page.js").write_text(script, encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    got = subprocess.run([shutil.which("node"), "--check", str(tmp_path / "page.js")], capture_output=True,
                         text=True, timeout=60, creationflags=flags)
    assert got.returncode == 0, got.stderr


# ------------------------------------------------------------ started like Steward starts it

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_starts_from_its_run_block_and_stops_clean(tmp_path):
    m = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))     # raw: abilities.tools needs lane i10c's schema
    port = _free_port()
    start = [a.replace("{python}", sys.executable).replace("{port}", str(port)) for a in m["run"]["default"]["start"]]
    env = {**os.environ, "PUFFPAI_PORT": str(port), "PUFFPAI_DATA": str(tmp_path / "data"),
           "PUFFPAI_TOKEN": TOKEN, "PUFFPAI_API": "http://127.0.0.1:1"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(start, cwd=PKG, env=env, creationflags=flags,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        health = m["health"]["url"].replace("{port}", str(port))
        for _ in range(100):
            try:
                if call(health)[0] == m["health"]["expect"]:
                    break
            except urllib.error.URLError:
                time.sleep(0.1)
        else:
            pytest.fail("FitFam did not answer its health check")
        base = f"http://127.0.0.1:{port}"
        assert mcp(base, "tools/list", token=None)[0] == 401
        assert "Logged your" in tool(base, "log_workout", type="walk", minutes=40)
        assert (tmp_path / "data" / "fitfam.json").is_file()
    finally:
        proc.terminate()
        proc.wait(10)
        proc.stderr.close()
    assert proc.poll() is not None


# ------------------------------------------------------------ checked input (verifier, lane I10b)

def post_raw(base, path, text, token=TOKEN):
    """POST a body exactly as written (JSON with NaN, 1e400 and the like)."""
    req = urllib.request.Request(base + path, data=text.encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    try:
        with _open(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def raw_request(base, head: bytes, wait=8) -> bytes:
    """Send *head* on a plain socket and read the whole answer; a hang fails the test."""
    port = int(base.rsplit(":", 1)[1])
    out = b""
    with socket.create_connection(("127.0.0.1", port), timeout=wait) as s:
        s.sendall(head)
        try:
            while chunk := s.recv(4096):
                out += chunk
        except socket.timeout:
            pytest.fail(f"the server hung on {head[:60]!r}")
    return out


def mcp_raw(base, name, args_json):
    return post_raw(base, "/mcp", '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "'
                    + name + '", "arguments": ' + args_json + '}}')


NINES = "9" * 400
BAD_TOOL_CALLS = [
    ("log_workout", '{"type": "run", "minutes": NaN}'), ("log_workout", '{"type": "run", "minutes": 1e400}'),
    ("log_workout", '{"type": "run", "minutes": 0}'), ("log_workout", '{"type": "run", "minutes": 1441}'),
    ("log_workout", '{"type": "run", "minutes": "nan"}'), ("log_workout", '{"type": "run", "minutes": ' + NINES + '}'),
    ("log_workout", '{"type": "run", "minutes": 30, "notes": "' + "x" * 501 + '"}'),
    ("log_workout", '{"type": ["run"], "minutes": 30}'),
    ("log_set", '{"exercise": "squat", "reps": 5, "weight": NaN}'),
    ("log_set", '{"exercise": "squat", "reps": 5, "weight": "nan"}'),
    ("log_set", '{"exercise": "squat", "reps": 5, "weight": "inf"}'),
    ("log_set", '{"exercise": "squat", "reps": 5, "weight": Infinity}'),
    ("log_set", '{"exercise": "squat", "reps": 5, "weight": 1e400}'),
    ("log_set", '{"exercise": "squat", "reps": 5, "weight": -5}'),
    ("log_set", '{"exercise": "squat", "reps": 5, "weight": 2001}'),
    ("log_set", '{"exercise": "squat", "reps": 1001}'), ("log_set", '{"exercise": "squat", "reps": 2.5}'),
    ("log_set", '{"exercise": "squat", "reps": 0}'), ("log_set", '{"exercise": "squat", "reps": true}'),
    ("log_set", '{"exercise": "squat", "reps": 5, "rpe": NaN}'), ("log_set", '{"exercise": "squat", "reps": 5, "rpe": 11}'),
    ("log_set", '{"exercise": "", "reps": 5}'),
    ("add_goal", '{"goal": "plank", "target": NaN}'), ("add_goal", '{"goal": "plank", "target": "inf"}'),
    ("add_goal", '{"goal": "plank", "target": -1}'), ("add_goal", '{"goal": "plank", "target": 1000001}'),
    ("add_goal", '{"goal": "plank", "target": 1e400}'), ("add_goal", '{"goal": "' + "x" * 201 + '", "target": 5}'),
    ("history", '{"limit": NaN}'), ("history", '{"limit": -3}'),
    ("suggest_workout", '{"minutes": NaN}'), ("suggest_workout", '{"minutes": -15}'),
    ("status", '{"person": "' + "x" * 61 + '"}'),
]


@pytest.mark.parametrize("name,args", BAD_TOOL_CALLS)
def test_bad_numbers_in_tools_are_refused_and_nothing_is_saved(base, data, name, args):
    code, out = mcp_raw(base, name, args)
    assert code == 400 or (code == 200 and out["result"]["isError"]), out
    assert not (data / "fitfam.json").exists()


@pytest.mark.parametrize("args", ['{"goal": "plank", "current": NaN}', '{"goal": "plank", "current": -1}',
                                  '{"goal": "plank", "current": "inf"}', '{"goal": "plank", "current": 1e400}'])
def test_goal_progress_is_checked(base, data, args):
    tool(base, "add_goal", goal="Hold a 2-minute plank", target=120, unit="seconds")
    before = (data / "fitfam.json").read_text(encoding="utf-8")
    code, out = mcp_raw(base, "update_goal", args)
    assert code == 400 or out["result"]["isError"], out
    assert (data / "fitfam.json").read_text(encoding="utf-8") == before


BAD_PAGE_CALLS = [
    ("complete_workout", '{"typeName": "x", "level": "mid", "duration": -5, "exerciseCount": 1}'),
    ("complete_workout", '{"typeName": "x", "level": "mid", "duration": 1441, "exerciseCount": 1}'),
    ("complete_workout", '{"typeName": "x", "level": "mid", "duration": NaN, "exerciseCount": 1}'),
    ("complete_workout", '{"typeName": "x", "level": "mid", "duration": 30, "exerciseCount": 201}'),
    ("complete_workout", '{"typeName": "x", "level": "mid", "duration": 30, "exerciseCount": "5"}'.replace('"5"', '"five"')),
    ("complete_workout", '{"typeName": "x", "level": "mid", "duration": 30, "exerciseCount": 1, "elapsed": 1e400}'),
    ("complete_workout", '{"typeName": "x", "level": "nap", "duration": 30, "exerciseCount": 1}'),
    ("complete_workout", '{"typeName": "x", "level": ["mid"], "duration": 30, "exerciseCount": 1}'),
    ("save_prefs", '{"type": "gym", "level": "easy", "duration": -15}'),
    ("save_prefs", '{"type": "gym", "level": "easy", "duration": 20}'),
    ("save_prefs", '{"type": "gym", "level": "easy", "duration": NaN}'),
    ("save_prefs", '{"type": ["gym"], "level": "easy", "duration": 15}'),
    ("remove_workout", '{"index": -1}'), ("remove_workout", '{"index": "x"}'), ("remove_workout", '{"index": NaN}'),
    ("add_goal", '{"goal": "plank", "target": NaN}'),
]


@pytest.mark.parametrize("action,body", BAD_PAGE_CALLS)
def test_bad_numbers_from_the_page_are_refused_and_nothing_is_saved(base, data, action, body):
    code, out = post_raw(base, "/api/" + action, body)
    assert code == 400 and out["error"], out
    assert not (data / "fitfam.json").exists()


@pytest.mark.parametrize("query", ["minutes=nan", "minutes=inf", "minutes=1e400", "minutes=-15", "minutes=abc",
                                   "type=" + "x" * 41])
def test_suggest_checks_its_query(base, query):
    code, out = call(base + "/suggest?" + query)
    assert code == 400 and out["error"], out


def test_bad_values_already_in_the_file_are_repaired_on_load(data):
    data.mkdir(parents=True)
    (data / "fitfam.json").write_text(
        '{"users": {"me": {"name": "You", "emoji": "", "prefs": {"level": "mid", "type": "full", "duration": NaN},'
        ' "goals": [{"id": "g1", "text": "plank", "target": Infinity, "current": NaN, "unit": "seconds"}],'
        ' "history": [{"date": "2026-09-01T10:00:00", "type": "run", "duration": NaN}],'
        ' "sets": [{"id": "s1", "date": "2026-09-01T10:00:00", "exerciseId": "g2", "exercise": "Bench Press",'
        ' "weight": NaN, "reps": -Infinity}], "streak": 0, "totalWorkouts": "lots"}}, "last_updated": "x"}',
        encoding="utf-8")
    with running(ff.make_server(0, data, TOKEN, None)) as base:
        code, doc = call(base + "/data")
        assert code == 200
        me = doc["users"]["me"]
        assert me["prefs"]["duration"] == 0 and me["goals"][0]["target"] == 0 and me["sets"][0]["weight"] == 0
        assert tool(base, "status").startswith("You: 0-day streak, 0 workouts in all")
        assert "plank: 0 of 0 seconds (0%)" in tool(base, "list_goals")
        assert "Bench Press: 0 reps" in tool(base, "history")
        assert "A 30-minute" in tool(base, "suggest_workout")
        assert call(base + "/widget/streaks")[0] == 200
        assert "Logged your" in tool(base, "log_workout", type="run", minutes=10)
    saved = (data / "fitfam.json").read_text(encoding="utf-8")
    assert "NaN" not in saved and "Infinity" not in saved


@pytest.mark.parametrize("length", [b"-1", b"abc", b"1e3", b"99999999999", b" 5x"])
def test_a_bad_content_length_never_hangs(base, data, length):
    for path in (b"/api/save_prefs", b"/mcp"):
        for auth in (f"Authorization: Bearer {TOKEN}\r\n".encode(), b""):
            head = (b"POST " + path + b" HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n" + auth
                    + b"Content-Length: " + length + b"\r\n\r\n{}")
            answer = raw_request(base, head)
            assert answer.startswith(b"HTTP/1.0 400" if auth else b"HTTP/1.0 401"), answer[:80]
    assert not (data / "fitfam.json").exists()


def test_a_stalled_body_is_cut_off(data):
    server = ff.make_server(0, data, TOKEN, None)
    server.RequestHandlerClass.timeout = 1
    with running(server) as base:
        head = (b"POST /api/add_goal HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + TOKEN.encode()
                + b"\r\nContent-Length: 100\r\n\r\n{\"goal\":")
        start = time.monotonic()
        raw_request(base, head)
        assert time.monotonic() - start < 6
    assert not (data / "fitfam.json").exists()


def _boom(exc):
    def fail(*_a, **_k):
        raise exc
    return fail


def test_an_unexpected_error_is_plain_json_and_mcp_is_error(data, monkeypatch):
    server = ff.make_server(0, data, TOKEN, None)
    app = server.RequestHandlerClass.app
    monkeypatch.setattr(app, "status", _boom(RuntimeError("boom")))
    monkeypatch.setattr(app, "save_prefs", _boom(OverflowError("big")))
    monkeypatch.setattr(app, "widget_streaks", _boom(KeyError("x")))
    with running(server) as base:
        code, out = mcp(base, "tools/call", {"name": "status"})
        assert code == 200 and out["result"]["isError"] and "RuntimeError" in out["result"]["content"][0]["text"]
        code, out = call(base + "/api/save_prefs", "POST", {"type": "gym", "level": "easy", "duration": 15})
        assert code == 500 and "OverflowError" in out["error"]
        code, out = call(base + "/widget/streaks")
        assert code == 500 and "KeyError" in out["error"]
        assert call(base + "/health") == (200, "ok")            # the server is still up


def test_manifest_tools_are_the_live_descriptions(base):
    """abilities.tools (lane I10c's field): the same words as the MCP server's, for the store."""
    m = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))
    live = {t["name"]: t["description"] for t in mcp(base, "tools/list")[1]["result"]["tools"]}
    assert m["abilities"]["tools"] == live
