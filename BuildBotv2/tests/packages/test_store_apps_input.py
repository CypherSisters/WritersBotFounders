"""Lane I10b2 verifier fixes: Calendar and AlarmFlow check every input before saving, never send
invalid JSON, never drop a connection, and AlarmFlow's /health tells the truth about its clock.

Every server here listens on a free port on 127.0.0.1 and is stopped by the test that started it."""

import contextlib
import importlib.util
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PACKAGES = Path(__file__).resolve().parents[2] / "packages"
TOKEN = "test-token-" + "i" * 20
_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


def _load(pid):
    spec = importlib.util.spec_from_file_location(f"{pid}_server_input", PACKAGES / pid / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cal = _load("calendar")
af = _load("alarmflow")


def _strict(text):
    """JSON as a browser reads it: NaN and Infinity are not JSON."""
    def refuse(word):
        raise ValueError(f"not JSON: {word}")
    return json.loads(text, parse_constant=refuse)


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
        thread.join(5)
        assert not thread.is_alive()


def call(url, method="GET", body=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"})
    try:
        with _open(req, timeout=10) as r:
            text = r.read().decode()
            return r.status, (_strict(text) if "json" in r.headers.get("Content-Type", "") else text)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        return e.code, (_strict(text) if "json" in (e.headers.get("Content-Type") or "") else text)


def mcp_call(base, name, args):
    code, out = call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                             "params": {"name": name, "arguments": args}})
    assert code == 200, out
    return out["result"]


@pytest.fixture
def cal_data(tmp_path):
    return tmp_path / "data" / "calendar"


@pytest.fixture
def cal_base(cal_data):
    with running(cal.make_server(0, cal_data, TOKEN, None)) as url:
        yield url


@pytest.fixture
def af_data(tmp_path):
    return tmp_path / "data" / "alarmflow"


@pytest.fixture
def af_base(af_data):
    with running(af.make_server(0, af_data, TOKEN, None, every_s=0.05)) as url:
        yield url


# ------------------------------------------------------------ 1. Calendar amounts

@pytest.mark.parametrize("amount", ["nan", "NaN", "inf", "-inf", "Infinity", float("nan"), float("inf"), -1,
                                    10_000_000.01, "1e400", True, [5], {"a": 1}, "ten dollars"])
def test_a_bill_amount_must_be_a_finite_number_in_range(cal_base, cal_data, amount):
    body = json.dumps({"name": "Electric", "amount": amount, "due": "2026-11-05"}, allow_nan=True)
    code, out = call(cal_base + "/api/add_bill", "POST", raw=body.encode())
    assert code == 400 and "amount" in out["error"]
    res = mcp_call(cal_base, "add_bill", {"name": "Electric", "amount": amount if not isinstance(amount, float) else str(amount),
                                          "due": "2026-11-05"})
    assert res["isError"]
    assert not (cal_data / "calendar.json").exists()                     # nothing saved
    assert call(cal_base + "/data")[0] == 200                             # and /data still reads as JSON


def test_amounts_keep_two_decimals_and_the_top_of_the_range():
    assert cal.amount_of("$1,234.567") == 1234.57
    assert cal.amount_of(10_000_000) == 10_000_000.0
    assert cal.amount_of(0) == 0.0


def test_a_nan_already_saved_is_repaired_and_data_is_valid_json(cal_data, cal_base):
    cal_data.mkdir(parents=True, exist_ok=True)
    (cal_data / "calendar.json").write_text(
        '{"events": [], "tasks": [], "meds": [], "bills": [{"id": "b1", "name": "Old", "amt": NaN, "due": "2026-11-05",'
        ' "status": "pending"}, {"id": "b2", "name": "Huge", "amt": Infinity, "due": "2026-11-06", "status": "pending"}]}',
        encoding="utf-8")
    code, doc = call(cal_base + "/data")                                  # _strict: no NaN reaches the page
    assert code == 200 and [b["amt"] for b in doc["bills"]] == [0.0, 0.0]
    assert "$0.00 in all" in mcp_call(cal_base, "list_bills", {})["content"][0]["text"]
    mcp_call(cal_base, "pay_bill", {"bill": "old"})                       # the next save writes the repair
    _strict((cal_data / "calendar.json").read_text(encoding="utf-8"))


def test_no_save_can_write_nan(cal_data):
    store = cal.Store(cal_data)
    with pytest.raises(ValueError):
        store.save({**cal.default_doc(), "bills": [{"amt": float("nan")}]})
    assert not (cal_data / "calendar.json").exists()


def test_delete_bill_takes_its_calendar_entry_too(cal_base, cal_data):
    mcp_call(cal_base, "add_bill", {"name": "Water", "amount": 20, "due": "2026-11-12"})
    mcp_call(cal_base, "add_bill", {"name": "Electric", "amount": 84.5, "due": "2026-11-05"})
    res = mcp_call(cal_base, "delete_bill", {"bill": "water"})
    assert res["content"][0]["text"] == "Deleted the bill Water."
    doc = json.loads((cal_data / "calendar.json").read_text(encoding="utf-8"))
    assert [b["name"] for b in doc["bills"]] == ["Electric"]
    assert [e["title"] for e in doc["events"]] == ["Electric ($84.50)"]
    raw = json.loads((PACKAGES / "calendar" / "puffpai.json").read_text(encoding="utf-8"))
    assert "delete_bill" in raw["abilities"]["changes"] and raw["abilities"]["examples"]["delete_bill"]


# ------------------------------------------------------------ 2. Calendar dates and types

@pytest.mark.parametrize("start", ["9999-12-31T23:30", "9999-12-31", "1899-12-31", "2201-01-01", "december 31 9999"])
def test_dates_outside_1900_to_2200_are_refused_plainly(cal_base, cal_data, start):
    code, out = call(cal_base + "/api/add_event", "POST", {"title": "Far", "start": start})
    assert code == 400 and ("between 1900 and 2200" in out["error"] or "not a date" in out["error"])
    assert mcp_call(cal_base, "add_task", {"title": "Far", "due": start})["isError"]
    assert not (cal_data / "calendar.json").exists()


def test_a_stored_year_9999_never_crashes_a_list(cal_data, cal_base):
    cal_data.mkdir(parents=True, exist_ok=True)
    (cal_data / "calendar.json").write_text(json.dumps({"events": [
        {"id": "e1", "title": "Odd", "start": "9999-12-31T23:30", "end": "9999-12-31T23:59", "rec": "daily"}],
        "tasks": [{"id": "t1", "title": "Odd task", "due": "9999-12-31", "status": "pending"}],
        "meds": [], "bills": []}), encoding="utf-8")
    assert not mcp_call(cal_base, "list_events", {"days_ahead": 366})["isError"]
    assert not mcp_call(cal_base, "list_tasks", {})["isError"]
    assert call(cal_base + "/widget/upcoming")[0] == 200
    assert call(cal_base + "/month?y=2200&m=12")[0] == 200
    res = mcp_call(cal_base, "edit_event", {"event": "odd", "start": "tomorrow at 9am"})
    assert not res["isError"], res


@pytest.mark.parametrize("args", [{"title": 5, "start": "tomorrow"}, {"title": ["x"], "start": "tomorrow"},
                                  {"title": "x", "start": 20261030}, {"title": "x", "start": "tomorrow", "who": {"a": 1}},
                                  {"title": "x", "start": "tomorrow", "category": 3}])
def test_calendar_text_arguments_must_be_text(cal_base, cal_data, args):
    assert call(cal_base + "/api/add_event", "POST", args)[0] == 400
    assert mcp_call(cal_base, "add_event", args)["isError"]
    assert not (cal_data / "calendar.json").exists()


@pytest.mark.parametrize("days", [10**30, "lots", [1], True])
def test_days_ahead_must_be_a_number(cal_base, days):
    res = mcp_call(cal_base, "list_events", {"days_ahead": days})
    assert res["isError"] or "next 366 days" in res["content"][0]["text"]


# ------------------------------------------------------------ 2. AlarmFlow types and ranges

@pytest.mark.parametrize("tool,args", [
    ("create_timer", {"desc": 5, "minutes": 1}),
    ("create_timer", {"desc": ["x"], "minutes": 1}),
    ("create_timer", {"desc": "x", "hours": 10**15}),
    ("create_timer", {"desc": "x", "minutes": "ten"}),
    ("create_timer", {"desc": "x", "seconds": float("inf")}),
    ("create_timer", {"desc": "x", "minutes": -5}),
    ("create_timer", {"desc": "x", "sound": 3, "minutes": 1}),
    ("create_alarm", {"desc": {"a": 1}, "hour": 7}),
    ("create_alarm", {"desc": "x", "hour": 7, "repeat": ["daily"]}),
])
def test_alarmflow_checks_every_argument_before_saving(af_base, af_data, tool, args):
    body = json.dumps(args, allow_nan=True).encode()
    assert call(af_base + "/api/" + tool, "POST", raw=body)[0] == 400
    res = mcp_call(af_base, tool, args if "inf" not in body.decode().lower() else {**args, "seconds": "inf"})
    assert res["isError"]
    assert not (af_data / "alarmflow.json").exists()


def test_timers_run_at_most_seven_days(af_base, af_data):
    res = mcp_call(af_base, "create_timer", {"desc": "Long", "hours": 168, "seconds": 1})
    assert res["content"][0]["text"].startswith("A timer can run for at most 7 days")
    assert not (af_data / "alarmflow.json").exists()
    assert mcp_call(af_base, "create_timer", {"desc": "Week", "hours": 168})["content"][0]["text"].startswith("Timer set")


@pytest.mark.parametrize("tool,minutes", [("add_time", 10**12), ("add_time", -10**12), ("add_time", 24 * 60 + 1),
                                          ("snooze", 10**9), ("snooze", 0.5)])
def test_add_time_and_snooze_move_at_most_a_day(af_base, af_data, tool, minutes):
    mcp_call(af_base, "create_timer", {"desc": "Tea", "minutes": 5})
    before = (af_data / "alarmflow.json").read_text(encoding="utf-8")
    assert mcp_call(af_base, tool, {"item": "tea", "minutes": minutes})["isError"]
    assert (af_data / "alarmflow.json").read_text(encoding="utf-8") == before
    assert not mcp_call(af_base, "list", {})["isError"]
    assert call(af_base + "/widget/running")[0] == 200


def test_stored_values_out_of_range_never_crash_list_or_the_widget(af_data, af_base):
    af_data.mkdir(parents=True, exist_ok=True)
    (af_data / "alarmflow.json").write_text(json.dumps({"items": [
        {"id": "a1", "mode": "timer", "desc": "Huge", "tot": 10**15, "rem": 10**15, "status": "running", "ts": 10**20},
        {"id": "a2", "mode": "alarm", "desc": 7, "status": "running", "targetMs": 10**25},
        {"id": "a3", "mode": "timer", "desc": "Paused", "tot": "x", "rem": -5, "status": "paused", "ts": None},
        "not an item"]}), encoding="utf-8")
    assert not mcp_call(af_base, "list", {})["isError"]
    assert call(af_base + "/widget/running")[0] == 200
    assert call(af_base + "/data")[0] == 200
    time.sleep(0.2)                                                       # a few clock ticks over it
    assert call(af_base + "/health") == (200, "ok")


# ------------------------------------------------------------ 2. every route answers in JSON

@pytest.mark.parametrize("pid", ["calendar", "alarmflow"])
def test_an_unexpected_error_is_a_plain_answer_never_a_dropped_connection(tmp_path, monkeypatch, pid):
    mod = cal if pid == "calendar" else af
    server = mod.make_server(0, tmp_path / pid, TOKEN, None) if pid == "calendar" else \
        mod.make_server(0, tmp_path / pid, TOKEN, None, every_s=0.05)
    tool = "list_tasks" if pid == "calendar" else "list"
    with running(server) as base:
        def boom(*a, **k):
            raise RuntimeError("something odd")
        monkeypatch.setattr(server.app, tool, boom)
        monkeypatch.setattr(server.app, "view", boom)
        code, out = call(base + "/api/" + tool, "POST", {})
        assert code == 500 and "hit a problem (RuntimeError)" in out["error"]
        code, out = call(base + "/data")
        assert code == 500 and "Nothing was saved" in out["error"]
        res = mcp_call(base, tool, {})
        assert res["isError"] and "hit a problem" in res["content"][0]["text"]
        assert call(base + "/api/" + tool, "POST", ["not", "an", "object"])[0] == 400
        assert call(base + "/api/" + tool, "POST", raw=b"\xff\xfe")[0] in (400, 500)
        code, out = call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": ["x"]})
        assert code == 200 and out["error"]["code"] == -32602
        assert call(base + "/health")[0] in (200, 503)                    # still answering


# ------------------------------------------------------------ 3. AlarmFlow health tells the truth

def test_health_is_503_when_the_data_will_not_read(af_data):
    af_data.mkdir(parents=True)
    (af_data / "alarmflow.json").write_text("{not json", encoding="utf-8")
    with running(af.make_server(0, af_data, TOKEN, None, every_s=0.05)) as base:
        code, text = call(base + "/health")
        assert code == 503 and "will not read" in text
        code, out = call(base + "/updated")
        assert code == 500 and "will not read" in out["error"]


def test_health_is_503_after_several_failed_ticks(af_data, monkeypatch):
    server = af.make_server(0, af_data, TOKEN, None, every_s=0.05)
    with running(server) as base:
        assert call(base + "/health") == (200, "ok")

        def broken(*a, **k):
            raise RuntimeError("disk trouble")
        monkeypatch.setattr(server.app, "tick", broken)
        end = time.monotonic() + 5
        while server.clock.failures < af.Clock.FAILED_TICKS and time.monotonic() < end:
            time.sleep(0.05)
        code, text = call(base + "/health")
        assert code == 503 and "ringing clock" in text
        monkeypatch.undo()
        end = time.monotonic() + 5
        while server.clock.failures and time.monotonic() < end:
            time.sleep(0.05)
        assert call(base + "/health") == (200, "ok")                      # it recovers on a good tick


def test_the_page_shows_its_banner_when_updated_fails():
    page = (PACKAGES / "alarmflow" / "web" / "index.html").read_text(encoding="utf-8")
    assert "if (!chk.ok) { problem(u.error || UNREADABLE); return; }" in page


# ------------------------------------------------------------ 4. pages escape quotes, no ids in inline script

@pytest.mark.parametrize("pid", ["calendar", "alarmflow"])
def test_esc_escapes_quotes(pid):
    page = (PACKAGES / pid / "web" / "index.html").read_text(encoding="utf-8")
    line = next(ln for ln in page.splitlines() if "const esc = " in ln)
    assert "&quot;" in line and "&#39;" in line and "innerHTML" not in line


def test_alarmflow_buttons_carry_no_ids_in_inline_script():
    page = (PACKAGES / "alarmflow" / "web" / "index.html").read_text(encoding="utf-8")
    assert "A.call(" not in page.split("function render()")[1].split("function tick()")[0]
    assert "data-tool" in page and "card.dataset.id" in page
