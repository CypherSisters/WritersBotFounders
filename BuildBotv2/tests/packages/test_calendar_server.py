"""Calendar's own server (packages/calendar/server.py): plain-word dates, repeats, its tools, the
Core people list, the month view, the widget, the page's routes and the token on every route.

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
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from puffpai_core.packages import manifest

PKG = Path(__file__).resolve().parents[2] / "packages" / "calendar"
TOKEN = "test-token-" + "c" * 20

_spec = importlib.util.spec_from_file_location("calendar_server", PKG / "server.py")
cal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cal)

_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
RAW = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 28, 15, 0)       # a Wednesday afternoon


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


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data" / "calendar"


@pytest.fixture
def app(data):
    return cal.Calendar(cal.Store(data))


@pytest.fixture
def base(data):
    with running(cal.make_server(0, data, TOKEN, None)) as url:
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
        return e.code, (json.loads(text) if text.startswith(("{", "[")) else text)


def mcp(base, method, params=None, token=TOKEN):
    return call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, token)


def tool(base, name, /, **args):
    code, out = mcp(base, "tools/call", {"name": name, "arguments": args})
    assert code == 200 and not out["result"]["isError"], out
    return out["result"]["content"][0]["text"]


def _doc(data):
    return json.loads((data / "calendar.json").read_text(encoding="utf-8"))


def _soon(days=1, hour=10):
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


# ------------------------------------------------------------ dates and repeats

@pytest.mark.parametrize("words,want,timed", [
    ("2026-10-30", datetime(2026, 10, 30), False),
    ("2026-10-30T10:00", datetime(2026, 10, 30, 10), True),
    ("2026-10-30 14:30", datetime(2026, 10, 30, 14, 30), True),
    ("today", datetime(2026, 10, 28), False),
    ("tomorrow at 3pm", datetime(2026, 10, 29, 15), True),
    ("friday at 10am", datetime(2026, 10, 30, 10), True),
    ("Friday 10:30 AM", datetime(2026, 10, 30, 10, 30), True),
    ("wednesday", datetime(2026, 10, 28), False),
    ("next wednesday", datetime(2026, 11, 4), False),
    ("tonight at 7", datetime(2026, 10, 28, 19), True),
    ("November 5", datetime(2026, 11, 5), False),
    ("oct 30th at 9am", datetime(2026, 10, 30, 9), True),
    ("5 march", datetime(2027, 3, 5), False),
    ("3 january 2028", datetime(2028, 1, 3), False),
    ("2026-11-03 at 3pm", datetime(2026, 11, 3, 15), True),
    ("next Monday", datetime(2026, 11, 2), False),
    ("in 3 days", datetime(2026, 10, 31), False),          # 2.0 read this as 3:00 AM today
    ("in 2 weeks at 9am", datetime(2026, 11, 11, 9), True),
    ("next week", datetime(2026, 11, 4), False),
    ("tomorrow at noon", datetime(2026, 10, 29, 12), True),
    ("yesterday at 9pm", datetime(2026, 10, 27, 21), True),
])
def test_dates_in_plain_words(words, want, timed):
    assert cal.parse_when(words, NOW) == (want, timed)


@pytest.mark.parametrize("words", ["", "someday", "friday at 25pm"])
def test_dates_it_cannot_read_say_so(words):
    with pytest.raises(ValueError):
        cal.parse_when(words, NOW)


def test_repeats_from_the_v1_rules():
    span = (datetime(2026, 11, 1), datetime(2026, 11, 30, 23, 59))
    weekly = {"start": "2026-10-05T18:00", "rec": "weekly"}                  # Mondays
    assert [d.day for d in cal.occurrences(weekly, *span)] == [2, 9, 16, 23, 30]
    weekdays = {"start": "2026-11-01T08:00", "rec": "weekdays"}
    assert len(cal.occurrences(weekdays, *span)) == 21
    biweekly = {"start": "2026-11-02", "rec": "biweekly"}
    assert [d.day for d in cal.occurrences(biweekly, *span)] == [2, 16, 30]
    monthly = {"start": "2026-01-31", "rec": "monthly"}                     # the 31st: the month's last day
    assert [d.day for d in cal.occurrences(monthly, *span)] == [30]
    assert [d.month for d in cal.occurrences(monthly, datetime(2027, 1, 1), datetime(2027, 3, 31))] == [1, 2, 3]
    assert cal.occurrences({"start": "2026-11-02", "rec": {"freq": "daily", "count": 3}}, *span)[-1].day == 4
    assert cal.occurrences({"start": "2026-12-02"}, *span) == []


# ------------------------------------------------------------ the logic

def test_events_add_list_edit_and_delete(app, data):
    start = _soon(1)
    said = app.add_event("Dentist", start.isoformat(timespec="minutes"), category="medical", location="Main St")
    assert said.startswith(f"Added Dentist on {start:%A %b} {start.day}, 10:00 AM")
    e = _doc(data)["events"][0]
    assert e["end"] == (start + timedelta(hours=1)).isoformat(timespec="minutes") and e["cat"] == "medical"
    listed = app.list_events()
    assert "Dentist at Main St (medical)" in listed and listed.startswith("1 event in the next 7 days")
    assert "Dentist" in app.list_events(day=start.date().isoformat())
    assert app.list_events(day="2030-01-01").startswith("Nothing is on the calendar on ")
    moved = _soon(2, 14)
    assert app.edit_event("dentist", start=moved.isoformat(timespec="minutes")).startswith("Changed the start of Dentist")
    e = _doc(data)["events"][0]
    assert e["start"] == moved.isoformat(timespec="minutes") and e["end"] == (moved + timedelta(hours=1)).isoformat(timespec="minutes")
    assert app.edit_event("dentist") == "Nothing to change on Dentist: say what to change."
    assert app.delete_event("dentist") == "Deleted the event Dentist."
    assert _doc(data)["events"] == []


def test_a_weekly_event_is_listed_every_week(app):
    app.add_event("Swim class", _soon(1, 17).isoformat(timespec="minutes"), repeat="weekly")
    assert app.list_events(days_ahead=21).count("Swim class") == 3


def test_delete_never_takes_every_partial_match(app, data):
    """v1 bug: cal_delete_event removed every event whose title held the words (app_data.py:954)."""
    app.add_event("Soccer practice", _soon(1).isoformat())
    app.add_event("Soccer game", _soon(2).isoformat())
    assert app.delete_event("soccer").startswith("Several events match 'soccer': Soccer practice, Soccer game")
    assert len(_doc(data)["events"]) == 2
    assert app.delete_event("x") == "I need the event's name to find it."
    assert app.delete_event("Soccer game") == "Deleted the event Soccer game."


def test_tasks_carry_their_calendar_entry(app, data):
    due = _soon(3).date().isoformat()
    assert app.add_task("Take the bins out", due, priority="high").startswith("Task added: Take the bins out, due ")
    d = _doc(data)
    assert d["events"][0]["taskId"] == d["tasks"][0]["id"] and d["events"][0]["start"].endswith("T09:00")
    assert "Take the bins out, due " in app.list_tasks() and "[high]" in app.list_tasks()
    later = _soon(4).date().isoformat()
    app.edit_task("bins", due=later)
    assert _doc(data)["events"][0]["start"] == f"{later}T09:00"
    assert app.complete_task("bins") == "Done: Take the bins out is marked complete."
    assert app.list_tasks() == "No open tasks." and "1 done task" in app.list_tasks("completed")
    assert _doc(data)["events"][0]["done"] is True
    assert app.delete_task("bins") == "Deleted the task Take the bins out."
    assert _doc(data)["events"] == [] and _doc(data)["tasks"] == []


def test_meds_are_taken_for_the_day(app, data):
    assert app.add_med("Vitamin D", "one tablet", how_often="daily", next_time="8:00 AM") == \
        "Medicine added: Vitamin D (one tablet), daily at 8:00 AM, for everyone."
    assert "Vitamin D (one tablet): daily at 8:00 AM, for everyone, due" in app.list_meds()
    assert app.take_med("vitamin").startswith("Noted: Vitamin D taken at ")
    assert "taken today, last taken " in app.list_meds()
    d = _doc(data)
    d["meds"][0]["log"] = ["2020-01-01T08:00"]                                 # an old dose
    (data / "calendar.json").write_text(json.dumps(d), encoding="utf-8")
    assert ", due, last taken " in app.list_meds()


def test_a_v1_taken_medicine_still_reads(app, data):
    app.add_med("Iron")
    d = _doc(data)
    d["meds"][0].update(status="taken", takenOn="2020-01-01")                  # v1: no dose log
    (data / "calendar.json").write_text(json.dumps(d), encoding="utf-8")
    assert app.list_meds().endswith(", due")


def test_take_med_logs_each_dose_with_its_time(app, data):
    app.add_med("Inhaler", "2 puffs", how_often="twice-daily")
    now = datetime.now()
    early = now.replace(hour=0, minute=1) if now.hour or now.minute > 1 else now
    said = app.take_med("inhaler", when=f"today at {early:%H:%M}")
    assert said == f"Noted: Inhaler taken at {early.hour % 12 or 12}:{early.minute:02d} {'AM' if early.hour < 12 else 'PM'} today, 1 of 2 taken today."
    assert "1 of 2 taken today" in app.list_meds()
    app.take_med("inhaler")
    assert "Inhaler (2 puffs): twice-daily at 8:00 AM, for everyone, taken today" in app.list_meds()
    assert len(_doc(data)["meds"][0]["log"]) == 2 and all("T" in x for x in _doc(data)["meds"][0]["log"])
    assert app.take_med("inhaler", when="tomorrow at 8am").startswith("That time is still to come")
    assert len(_doc(data)["meds"][0]["log"]) == 2


def test_delete_med(app, data):
    app.add_med("Vitamin D")
    app.take_med("vitamin d")
    assert app.delete_med("vitamin d") == "Stopped tracking Vitamin D; it and its dose log are deleted."
    assert _doc(data)["meds"] == [] and app.list_meds() == "No medicines are tracked."


def test_whats_on_today_and_this_week(app):
    today = date.today()
    assert app.whats_on() == "Nothing is on the calendar today, and nothing is overdue."
    app.add_event("Dentist", f"{today.isoformat()}T23:00")
    app.add_task("Call the vet", today.isoformat())
    app.add_bill("Water", 20, (today + timedelta(days=3)).isoformat())
    app.add_med("Vitamin D", "one tablet")
    app.add_task("Return library books", (today - timedelta(days=2)).isoformat())
    said = app.whats_on()
    assert said.startswith("Today:") and "Dentist" in said and "Tasks due:\nCall the vet" in said
    assert "Water" not in said and "Medicines still to take today:\nVitamin D (one tablet) at 8:00 AM" in said
    assert "Overdue:\nTask Return library books, was due " in said
    week = app.whats_on(days_ahead=6)
    assert week.startswith("Today and the next 6 days:") and "Bills due:\nWater: $20.00" in week
    assert "Call the vet" not in app.whats_on(day=(today + timedelta(days=1)).isoformat())


def test_list_overdue_and_the_overdue_marks(app):
    assert app.list_overdue().startswith("Nothing is overdue")
    past = (date.today() - timedelta(days=3)).isoformat()
    app.add_bill("Gas", 40, past)
    app.add_task("Mow the lawn", past)
    said = app.list_overdue()
    assert "1 overdue task:\nMow the lawn, was due " in said and "1 overdue bill, $40.00 in all:\nGas: $40.00" in said
    assert "(overdue)" in app.list_bills() and "(overdue)" in app.list_tasks()
    app.pay_bill("gas")
    app.complete_task("mow")
    assert app.list_overdue().startswith("Nothing is overdue")


def test_a_tasks_or_bills_calendar_entry_takes_the_task_or_bill_along(app, data):
    app.add_task("Bins", _soon(2).date().isoformat())
    assert app.delete_event("bins") == "Deleted the task Bins."
    assert _doc(data)["tasks"] == [] and _doc(data)["events"] == []
    app.add_bill("Phone", 30, "2026-11-20")
    later = "2026-11-25T09:00"
    app.edit_event("phone", start=later)
    assert _doc(data)["bills"][0]["due"] == "2026-11-25"


def test_bills_with_a_total(app, data):
    assert app.add_bill("Electric", "$84.50", "2026-11-05") == "Bill tracked: Electric, $84.50, due Thursday Nov 5."
    app.add_bill("Water", 20, "2026-11-12", autopay=True)
    assert app.list_bills().startswith("2 unpaid bills, $104.50 in all:") and "(autopay)" in app.list_bills()
    assert app.pay_bill("electric") == "Marked Electric as paid."
    assert app.list_bills("paid").startswith("1 paid bill, $84.50")
    assert next(e for e in _doc(data)["events"] if e.get("billId"))["done"] is True
    with pytest.raises(ValueError):                                              # a plain error over MCP
        app.add_bill("Gas", 10, "")


def test_who_comes_from_the_core_people_list(data):
    class Fake(cal.People):
        def __init__(self):
            pass

        def list(self, fallback=None):
            return [{"id": "sam", "name": "Sam", "emoji": ""}, {"id": "ari", "name": "Ari", "emoji": ""}]
    app = cal.Calendar(cal.Store(data), Fake())
    assert app.add_task("Homework", _soon(1).date().isoformat(), who="Sam").endswith(", for Sam.")
    assert _doc(data)["tasks"][0]["to"] == "sam"
    assert "for Sam" in app.list_tasks()
    assert app.add_event("Movie", _soon(1).isoformat(), who="everyone").startswith("Added Movie")
    assert _doc(data)["events"][-1]["to"] == "all"


def test_a_v1_file_reads_as_is_and_its_family_is_the_fallback(data):
    data.mkdir(parents=True)
    start = _soon(1).isoformat()
    v1 = {"family": [{"id": "me", "name": "Me", "color": "#38bdf8", "emoji": "", "role": "parent"}],
          "events": [{"id": "e1", "title": "Old event", "cat": "other", "start": start, "end": start, "to": "me",
                      "prio": "medium", "src": "puffpai", "rec": None, "loc": None}],
          "tasks": [], "meds": [], "bills": [], "last_updated": "x"}
    (data / "calendar.json").write_text(json.dumps(v1), encoding="utf-8")
    app = cal.Calendar(cal.Store(data))
    assert "Old event (other, Me)" in app.list_events()


def test_month_and_widget(app):
    start = _soon(1, 11)
    app.add_event("Lunch", start.isoformat(timespec="minutes"))
    app.add_task("Call the vet", start.date().isoformat())
    app.add_med("Iron", "5 mg")
    month = app.month(start.year, start.month)
    rows = month["days"][start.date().isoformat()]
    assert [r["kind"] for r in rows] == ["event", "task"] and rows[0]["time"] == "11:00"
    today = date.today()
    if (today.year, today.month) == (start.year, start.month):
        assert month["days"][today.isoformat()][-1]["kind"] == "med"
    items = app.widget_upcoming()["items"]
    assert any(i["text"].endswith(": Lunch") for i in items) and any("Call the vet" in i["text"] for i in items)


def test_unreadable_file_is_never_written_over(data):
    data.mkdir(parents=True)
    (data / "calendar.json").write_text("{not json", encoding="utf-8")
    with running(cal.make_server(0, data, TOKEN, None)) as base:
        assert call(base + "/data")[0] == 500
        code, out = mcp(base, "tools/call", {"name": "add_event", "arguments": {"title": "x", "start": "tomorrow"}})
        assert out["result"]["isError"]
    assert (data / "calendar.json").read_text(encoding="utf-8") == "{not json"


# ------------------------------------------------------------ the server

def test_health_page_widget_and_month(base):
    assert call(base + "/health") == (200, "ok")
    code, page = call(base + "/")
    assert code == 200 and "Calendar" in page and "BASE + 'api/'" in page and "/api/pai" not in page
    assert 'id="today"' in page and "key === todayKey()" in page and "delete_med" in page
    assert call(base + "/widget/upcoming") == (200, {"items": []})
    assert call(base + "/month?y=2026&m=10")[1] == {"year": 2026, "month": 10, "days": {}}
    assert call(base + "/month?y=2026&m=13")[0] == 400
    assert call(base + "/month?y=x")[0] == 400
    assert call(base + "/people") == (200, [])


def test_the_page_buttons_call_the_same_functions(base):
    code, out = call(base + "/api/add_task", "POST", {"title": "Rake leaves", "due": "tomorrow"})
    assert code == 200 and out["message"].startswith("Task added: Rake leaves")
    assert call(base + "/data")[1]["tasks"][0]["title"] == "Rake leaves"
    code, out = call(base + "/api/add_event", "POST", {"title": "x", "start": "someday"})
    assert code == 400 and "not a date I can read" in out["error"]
    assert call(base + "/api/nope", "POST", {})[0] == 404
    assert call(base + "/data", "PUT", {"events": []})[0] == 405


def test_people_are_read_from_the_core_with_the_package_token(data):
    seen = []

    class Core(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            out = json.dumps([{"id": "sam", "name": "Sam", "emoji": "", "role": "child"}]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    core = ThreadingHTTPServer(("127.0.0.1", 0), Core)
    with running(core) as api, running(cal.make_server(0, data, TOKEN, api)) as base:
        assert call(base + "/people")[1] == [{"id": "sam", "name": "Sam", "emoji": ""}]
        assert tool(base, "add_event", title="Swim", start="tomorrow at 5pm", who="sam").endswith(", for Sam.")
    assert seen[0] == ("/v1/people", f"Bearer {TOKEN}")


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/", None), ("GET", "/health", None), ("GET", "/data", None), ("GET", "/updated", None),
    ("GET", "/widget/upcoming", None), ("GET", "/month", None), ("GET", "/people", None),
    ("POST", "/api/add_task", {"title": "x", "due": "tomorrow"}), ("POST", "/mcp", {}),
])
def test_every_route_refuses_without_the_token(base, method, path, body):
    assert call(base + path, method, body, token=None)[0] == 401
    assert call(base + path, method, body, token="wrong")[0] == 401
    assert call(base + "/data")[1]["tasks"] == []


def test_mcp_refuses_everything_when_started_without_a_token(data):
    with running(cal.make_server(0, data, None, None)) as base:
        assert mcp(base, "tools/list", token=None)[0] == 401


def test_manifest_is_valid_and_its_files_are_there():
    m, _ = manifest.load(PKG)
    assert manifest.check_files(PKG, m) == []


def test_abilities_tools_match_the_server_word_for_word(base):
    """abilities.tools (lane i10c) must say exactly what the MCP server says about each tool."""
    live = {t["name"]: t["description"] for t in mcp(base, "tools/list")[1]["result"]["tools"]}
    assert RAW["abilities"]["tools"] == live
    assert RAW["abilities"]["tools"] == {n: d for n, d, _p, _r in cal.TOOLS}


def test_manifest_matches_the_live_tool_list(base):
    m = RAW
    tools = mcp(base, "tools/list")[1]["result"]["tools"]
    names = {t["name"] for t in tools}
    assert set(m["abilities"]["examples"]) == names                       # a sample ask for every tool
    assert {t["name"] for t in tools if not t["annotations"]["readOnlyHint"]} == set(m["abilities"]["changes"])
    assert all(t["description"] and t["inputSchema"]["type"] == "object" for t in tools)
    assert m["needs"]["hosts"] == [] and m["data"]["labels"] == [{"path": "meds[]", "label": "health.medication"}]


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
                res = await s.call_tool("add_task", {"title": "Bins", "due": "tomorrow"})
                return [t.name for t in listed.tools], res.content[0].text

    names, text = asyncio.run(go())
    assert "list_events" in names and text.startswith("Task added: Bins")


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
            pytest.fail("Calendar did not answer its health check")
        base = f"http://127.0.0.1:{port}"
        assert mcp(base, "tools/list", token=None)[0] == 401
        assert tool(base, "add_event", title="Tea", start="tomorrow at 4pm").startswith("Added Tea")
        assert (tmp_path / "data" / "calendar.json").is_file()
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
