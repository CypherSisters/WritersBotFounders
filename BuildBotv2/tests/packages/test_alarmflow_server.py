"""AlarmFlow's own server (packages/alarmflow/server.py): its tools, the ringing clock with no page
open, the Core notify call, the widget, the page's routes and the token on every route.

Every server here listens on a free port on 127.0.0.1 and is stopped by the test that started it."""

import contextlib
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from puffpai_core.packages import manifest

PKG = Path(__file__).resolve().parents[2] / "packages" / "alarmflow"
TOKEN = "test-token-" + "a" * 20

_spec = importlib.util.spec_from_file_location("alarmflow_server", PKG / "server.py")
af = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(af)

_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
RAW = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))


@contextlib.contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        clock = getattr(server, "clock", None)
        if clock is not None:
            clock.halt.set()
            clock.join(5)
            assert not clock.is_alive()
        thread.join(5)
        assert not thread.is_alive()


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data" / "alarmflow"


@pytest.fixture
def app(data):
    return af.AlarmFlow(af.Store(data))


@pytest.fixture
def base(data):
    with running(af.make_server(0, data, TOKEN, None, every_s=0.1)) as url:
        yield url


def call(url, method="GET", body=None, token=TOKEN):
    h = {"Content-Type": "application/json"}
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


def mcp(base, method, params=None, token=TOKEN):
    return call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, token)


def tool(base, name, /, **args):
    code, out = mcp(base, "tools/call", {"name": name, "arguments": args})
    assert code == 200 and not out["result"]["isError"], out
    return out["result"]["content"][0]["text"]


def _items(data):
    return json.loads((data / "alarmflow.json").read_text(encoding="utf-8"))["items"]


# ------------------------------------------------------------ the logic

def test_timer_list_pause_resume_and_stop(app, data):
    assert app.create_timer("Pasta", minutes=10).startswith("Timer set: Pasta, 10 minutes.")
    assert "Pasta: 9m 5" in app.list() or "Pasta: 10m 00s" in app.list()
    assert app.pause("the pasta timer").startswith("Paused Pasta with ")
    assert "(paused)" in app.list()
    assert app.resume("pasta").startswith("Resumed Pasta")
    assert app.add_time("pasta", 5) == "Added 5 minutes on Pasta."
    assert _items(data)[0]["tot"] == 15 * 60
    assert app.dismiss("pasta") == "Stopped Pasta."
    assert app.list() == "No timers or alarms are set."
    assert app.create_timer("Nothing") == "A timer needs a length: some hours, minutes or seconds."


def test_alarm_is_set_for_the_next_time_that_clock_comes_round(app, data):
    soon = datetime.now() - timedelta(minutes=1)
    said = app.create_alarm("Wake up", hour=soon.hour, minute=soon.minute)
    assert said.startswith("Alarm set: Wake up, ringing at ") and said.endswith("tomorrow.")
    item = _items(data)[0]
    assert item["mode"] == "alarm" and item["targetMs"] > time.time() * 1000
    assert app.pause("wake") == "Alarms ring at a set time, so they can't be paused. Snooze or stop it instead."
    assert "Wake up: alarm at " in app.list()
    assert app.create_alarm("Bad", hour=25).startswith("An alarm needs an hour from 0 to 23")


def test_names_that_match_twice_ask_which_one(app):
    app.create_timer("Pasta water", minutes=5)
    app.create_timer("Pasta sauce", minutes=8)
    assert app.dismiss("pasta").startswith("Several match 'pasta': Pasta water, Pasta sauce")
    assert app.delete("rice") == "No timer or alarm matches 'rice'."
    assert app.delete("pasta sauce") == "Deleted Pasta sauce."
    assert app.dismiss_all() == "Stopped 1 timer or alarm."


def test_the_clock_rings_a_due_item_once_and_tells_the_owner(data):
    told = []
    app = af.AlarmFlow(af.Store(data), notify=lambda title, text: told.append((title, text)) or True)
    app.create_timer("Tea", seconds=1)
    assert app.tick(af._ms()) == []
    rang = app.tick(af._ms() + 1500)
    assert len(rang) == 1 and told == [("Tea", "Timer: Tea.")]
    assert app.tick(af._ms() + 3000) == [] and len(told) == 1         # once per ring
    assert "Tea: ringing now" in app.list()
    assert app.widget_running() == {"items": [{"text": "Tea: ringing"}]}
    assert app.snooze("", 1).startswith("Snoozed Tea for 1 minute.")    # the only one: no name needed
    assert app.tick(af._ms() + 61_000) and len(told) == 2


def test_an_item_due_while_buildbot_was_off_rings_late_and_says_so(data):
    told = []
    app = af.AlarmFlow(af.Store(data), notify=lambda t, x: told.append(x))
    app.create_timer("Bread", minutes=1)
    app.tick(af._ms() + 31 * 60_000)
    assert told == ["Timer: Bread. It was due 30 minutes ago, while BuildBot was off."]


def test_a_repeating_alarm_moves_to_its_next_day_when_stopped(app, data):
    soon = datetime.now() + timedelta(minutes=2)
    app.create_alarm("Pills", hour=soon.hour, minute=soon.minute, repeat="daily")
    first = _items(data)[0]["targetMs"]
    app.tick(first + 1000)
    said = app.dismiss("pills")
    assert said.startswith("Stopped Pills. It rings again at ")
    item = _items(data)[0]
    assert item["status"] == "running" and item["targetMs"] > first and item["repeat"] == "daily"
    assert app.dismiss("pills") == "Stopped Pills."                     # not ringing: cancelled
    assert _items(data)[0]["status"] == "stopped"


def test_next_repeat_skips_to_the_right_days():
    friday_9 = datetime(2026, 10, 30, 9, 0)                            # a Friday
    item = {"targetMs": int(friday_9.timestamp() * 1000), "repeat": "weekdays"}
    nxt = datetime.fromtimestamp(af.next_repeat(item, friday_9 + timedelta(minutes=1)) / 1000)
    assert (nxt.weekday(), nxt.hour) == (0, 9)                         # Monday 9:00
    item["repeat"] = "weekends"
    nxt = datetime.fromtimestamp(af.next_repeat(item, friday_9 + timedelta(minutes=1)) / 1000)
    assert nxt.weekday() == 5
    assert af.next_repeat({**item, "repeat": "none"}, friday_9) is None


def test_a_v1_file_reads_as_is(data):
    """docs/05 3.1: same shape as v1, so a v1 alarmflow.json can be copied in."""
    data.mkdir(parents=True)
    now = af._ms()
    v1 = {"items": [{"id": "a1x", "mode": "timer", "desc": "Old", "tot": 600, "rem": 600, "sound": "chime",
                     "status": "running", "source": "puffpai", "ts": now, "targetMs": None}], "last_updated": "x"}
    (data / "alarmflow.json").write_text(json.dumps(v1), encoding="utf-8")
    app = af.AlarmFlow(af.Store(data))
    assert "Old: 9m 5" in app.list() or "Old: 10m 00s" in app.list()


def test_unreadable_file_is_never_written_over(data):
    data.mkdir(parents=True)
    (data / "alarmflow.json").write_text("{not json", encoding="utf-8")
    with running(af.make_server(0, data, TOKEN, None, every_s=0.05)) as base:
        assert call(base + "/data")[0] == 500
        code, out = mcp(base, "tools/call", {"name": "create_timer", "arguments": {"desc": "x", "minutes": 1}})
        assert out["result"]["isError"]
        time.sleep(0.2)                                                  # a few clock ticks over the bad file
        assert call(base + "/updated")[0] == 500
        assert call(base + "/health")[0] == 503                          # nothing could ring: health says so
    assert (data / "alarmflow.json").read_text(encoding="utf-8") == "{not json"


# ------------------------------------------------------------ the server

def test_rings_with_no_page_open_and_posts_to_the_core_notify_route(data):
    seen = []

    class Core(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append({"path": self.path, "auth": self.headers.get("Authorization"),
                         "proxy": self.headers.get("Proxy-Authorization"),
                         "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
            out = b'{"id": "n1"}'
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    core = ThreadingHTTPServer(("127.0.0.1", 0), Core)
    with running(core) as api, running(af.make_server(0, data, TOKEN, api, every_s=0.1)) as base:
        assert "Timer set: Eggs" in tool(base, "create_timer", desc="Eggs", seconds=1)
        end = time.monotonic() + 10
        while not seen and time.monotonic() < end:
            time.sleep(0.1)
        assert seen and seen[0]["path"] == "/v1/notify" and seen[0]["auth"] == f"Bearer {TOKEN}"
        assert seen[0]["body"]["title"] == "Eggs" and seen[0]["body"]["urgency"] == "high"
        assert seen[0]["body"]["sound"] is True
        assert "Eggs: ringing now" in tool(base, "list")
        assert call(base + "/widget/running")[1] == {"items": [{"text": "Eggs: ringing"}]}
        assert tool(base, "dismiss", item="eggs") == "Stopped Eggs."
        assert len(seen) == 1


def test_health_page_and_widget(base):
    assert call(base + "/health") == (200, "ok")
    code, page = call(base + "/")
    assert code == 200 and "AlarmFlow" in page and "BASE + 'api/'" in page and "/api/pai" not in page
    assert "SpeechRecognition" not in page and "localStorage" not in page
    assert call(base + "/widget/running") == (200, {"items": []})


def test_health_says_503_when_the_clock_stops(data):
    server = af.make_server(0, data, TOKEN, None, every_s=0.05)
    with running(server) as base:
        server.clock.halt.set()
        server.clock.join(5)
        assert call(base + "/health")[0] == 503


def test_the_page_buttons_call_the_same_functions(base):
    code, out = call(base + "/api/create_timer", "POST", {"desc": "Laundry", "minutes": 30})
    assert code == 200 and out["message"].startswith("Timer set: Laundry")
    doc = call(base + "/data")[1]
    assert doc["items"][0]["desc"] == "Laundry" and 1790 <= doc["items"][0]["left"] <= 1800
    assert call(base + "/api/pause", "POST", {"item": "laundry"})[1]["message"].startswith("Paused Laundry")
    assert call(base + "/api/nope", "POST", {})[0] == 404
    assert call(base + "/api/create_timer", "POST", {"bogus": 1})[0] == 400
    assert call(base + "/data", "PUT", {"items": []})[0] == 405          # the page never sends the whole document


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/", None), ("GET", "/health", None), ("GET", "/data", None), ("GET", "/updated", None),
    ("GET", "/widget/running", None), ("POST", "/api/create_timer", {"minutes": 1}), ("POST", "/mcp", {}),
])
def test_every_route_refuses_without_the_token(base, method, path, body):
    assert call(base + path, method, body, token=None)[0] == 401
    assert call(base + path, method, body, token="wrong")[0] == 401
    assert call(base + "/data")[1]["items"] == []


def test_mcp_refuses_everything_when_started_without_a_token(data):
    with running(af.make_server(0, data, None, None)) as base:
        assert mcp(base, "tools/list", token=None)[0] == 401
        assert call(base + "/health", token=None)[0] == 401


def test_manifest_is_valid_and_its_files_are_there():
    m, _ = manifest.load(PKG)
    assert manifest.check_files(PKG, m) == []


def test_abilities_tools_match_the_server_word_for_word(base):
    """abilities.tools (lane i10c) must say exactly what the MCP server says about each tool."""
    live = {t["name"]: t["description"] for t in mcp(base, "tools/list")[1]["result"]["tools"]}
    assert RAW["abilities"]["tools"] == live
    assert RAW["abilities"]["tools"] == {n: d for n, d, _p, _r in af.TOOLS}


def test_manifest_matches_the_live_tool_list(base):
    m = RAW
    tools = mcp(base, "tools/list")[1]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert set(m["abilities"]["changes"]) | set(m["abilities"]["examples"]) <= names
    assert set(m["abilities"]["examples"]) == names                       # a sample ask for every tool
    assert {t["name"] for t in tools if not t["annotations"]["readOnlyHint"]} == set(m["abilities"]["changes"])
    assert all(t["description"] and t["inputSchema"]["type"] == "object" for t in tools)
    assert m["needs"]["hosts"] == [] and m["kind"] == "app"


def test_the_official_mcp_client_can_use_it(base):
    import asyncio

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    async def go():
        client = create_mcp_http_client(headers={"Authorization": f"Bearer {TOKEN}"})
        async with client, streamable_http_client(base + "/mcp", http_client=client) as (r, w):
            async with ClientSession(r, w) as s:
                await s.initialize()
                listed = await s.list_tools()
                res = await s.call_tool("create_timer", {"desc": "Oven", "minutes": 20})
                return [t.name for t in listed.tools], res.content[0].text

    names, text = asyncio.run(go())
    assert "create_alarm" in names and text.startswith("Timer set: Oven")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_starts_from_its_run_block_and_stops_clean(tmp_path):
    m = RAW
    port = _free_port()
    start = [a.replace("{python}", sys.executable).replace("{port}", str(port)) for a in m["run"]["default"]["start"]]
    env = {**os.environ, "PUFFPAI_PORT": str(port), "PUFFPAI_DATA": str(tmp_path / "data"),
           "PUFFPAI_TOKEN": TOKEN, "PUFFPAI_API": "http://127.0.0.1:1"}
    proc = subprocess.Popen(start, cwd=PKG, env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
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
            pytest.fail("AlarmFlow did not answer its health check")
        base = f"http://127.0.0.1:{port}"
        assert mcp(base, "tools/list", token=None)[0] == 401
        assert tool(base, "create_timer", desc="Tea", minutes=3).startswith("Timer set: Tea")
        assert (tmp_path / "data" / "alarmflow.json").is_file()
    finally:
        proc.terminate()
        proc.wait(10)
        proc.stderr.close()
    assert proc.poll() is not None


@pytest.mark.parametrize("method,path,code", [("POST", "/api/nope", 404), ("PUT", "/data", 405),
                                              ("DELETE", "/data", 405)])
def test_a_reply_never_leaves_the_body_unread(base, method, path, code):
    """A big body on a route that does not use it: the server reads it first, so the answer arrives whole
    (an unread body resets the socket on Windows, WinError 10053). Several times on the same server."""
    for _ in range(5):
        assert call(base + path, method, {"pad": "x" * 400_000})[0] == code


# ------------------------------------------------------------ 2.1.0

@pytest.mark.parametrize("said,seconds", [
    ("20 minutes", 1200), ("an hour and a half", 5400), ("half an hour", 1800), ("1h30m", 5400),
    ("two and a half hours", 9000), ("90 seconds", 90), ("twenty five minutes", 1500),
    ("1.5 hours", 5400), ("a quarter of an hour", 900), ("1 hour 20 minutes", 4800), ("pasta", None),
])
def test_a_timer_length_is_read_the_way_people_say_it(said, seconds):
    assert af.parse_duration(said) == seconds


def test_create_timer_takes_a_said_length_or_part_hours(app, data):
    assert app.create_timer("Roast", duration="an hour and a half").startswith("Timer set: Roast, 1 hour and 30 minutes.")
    assert app.create_timer("Bread", hours=1.5).startswith("Timer set: Bread, 1 hour and 30 minutes.")
    assert [i["tot"] for i in _items(data)] == [5400, 5400]
    assert app.create_timer("x", duration="soon").startswith("I could not read 'soon' as a length.")


@pytest.mark.parametrize("said,hm", [("7:30 AM", (7, 30)), ("6pm", (18, 0)), ("18:30", (18, 30)), ("noon", (12, 0)),
                                     ("12 am", (0, 0)), ("6:30 in the evening", (18, 30)), ("7 a.m.", (7, 0)),
                                     ("13pm", None), ("whenever", None)])
def test_an_alarm_time_is_read_the_way_people_say_it(said, hm):
    assert af.parse_clock(said) == hm


def test_create_alarm_takes_a_said_time_weekly_and_refuses_an_unknown_repeat(app, data):
    assert app.create_alarm("Bins", time="7pm", repeat="weekly").startswith("Alarm set: Bins, ringing at 7:00 PM")
    item = _items(data)[0]
    assert item["repeat"] == "weekly" and (item["hour"], item["minute"]) == (19, 0)
    assert app.create_alarm("x", time="7pm", repeat="fortnightly") == "Repeat is once, daily, weekdays, weekends or weekly."
    assert app.create_alarm("x") == "An alarm needs a time, like 7:30 AM."
    assert len(_items(data)) == 1


def test_weekly_repeats_on_the_same_day():
    friday_9 = datetime(2026, 10, 30, 9, 0)
    item = {"targetMs": int(friday_9.timestamp() * 1000), "repeat": "weekly", "hour": 9, "minute": 0, "weekday": 4}
    nxt = datetime.fromtimestamp(af.next_repeat(item, friday_9 + timedelta(minutes=1)) / 1000)
    assert nxt == friday_9 + timedelta(days=7)


def test_a_snooze_never_moves_a_repeating_alarms_time(app, data):
    soon = datetime.now() + timedelta(minutes=2)
    app.create_alarm("Pills", hour=soon.hour, minute=soon.minute, repeat="daily")
    app.tick(_items(data)[0]["targetMs"] + 1000)
    app.snooze("pills", 9)
    app.tick(_items(data)[0]["targetMs"] + 1000)                       # the snooze rings
    app.dismiss("pills")
    nxt = datetime.fromtimestamp(_items(data)[0]["targetMs"] / 1000)
    assert (nxt.hour, nxt.minute) == (soon.hour, soon.minute) and nxt.date() > soon.date()


def test_delete_picks_the_set_item_over_an_old_stopped_one(app, data):
    app.create_timer("Pasta", minutes=5)
    app.dismiss("pasta")
    app.create_timer("Pasta", minutes=8)
    assert app.delete("pasta") == "Deleted Pasta."
    assert [i["status"] for i in _items(data)] == ["stopped"]


def test_stop_with_several_ringing_asks_and_stop_all_stops_all(app):
    app.create_timer("Tea", seconds=1)
    app.create_timer("Eggs", seconds=1)
    app.tick(af._ms() + 2000)
    assert app.dismiss("") == "Several are ringing: Tea, Eggs. Which one?"
    assert app.dismiss("all") == "Stopped 2 timers or alarms."


def test_a_ringing_item_beeps_with_no_page_for_ten_minutes_at_most(data):
    beeps = []
    app = af.AlarmFlow(af.Store(data), ring=beeps.append)
    app.create_timer("Tea", seconds=1, sound="bell")
    app.create_timer("Quiet", seconds=1, sound="none")
    t = af._ms() + 2000
    app.tick(t)
    app.tick(t + 1000)                                                   # not again within 5 seconds
    app.tick(t + af.RING_EVERY_MS)
    assert beeps == ["bell", "bell"]                                     # the silent one never beeps
    app.tick(t + af.RING_FOR_MS + 1)
    assert len(beeps) == 2
    assert af.Speaker()("none") is False


def test_timers_keep_the_right_time_left_across_a_restart(data):
    app = af.AlarmFlow(af.Store(data))
    app.create_timer("Stew", minutes=30)
    app.create_timer("Rice", minutes=10)
    app.pause("rice")
    again = af.AlarmFlow(af.Store(data))                                 # a new process over the same file
    items = {i["desc"]: i for i in again.view()["items"]}
    assert 1795 <= items["Stew"]["left"] <= 1800 and items["Rice"]["left"] in (599, 600)
    assert again.tick(af._ms() + 31 * 60_000) and "Stew: ringing now" in again.list()
