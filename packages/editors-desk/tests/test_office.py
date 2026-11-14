"""Editor's Desk 0.4.0, the editor-in-chief and her staff (EDITOR_IN_CHIEF.md), against the same stand-in Core and brain as
``test_server.py``: no real model is ever called. Run from the package folder: ``python -m pytest tests/test_office.py -q``.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_server import PKG, TITLE, Box, call, q, tool  # noqa: E402

sys.path.insert(0, str(PKG))
sys.path.insert(0, str(PKG / "fixtures"))
import engine  # noqa: E402
import make_book  # noqa: E402
import server  # noqa: E402
import staff  # noqa: E402
from book import Book  # noqa: E402

DASHES = (chr(0x2014), chr(0x2013))      # the long dashes, never used


def office(b: Box) -> dict:
    code, got = call(b.port, "GET", "/api/office?" + q(title=TITLE))
    assert code == 200, got
    assert len(got["meeting"]["lines"]) <= server.MEETING_MOST
    assert not any(d in json.dumps(got, ensure_ascii=False) for d in DASHES)
    return got


def jobs(b: Box) -> list[dict]:
    p = b.data / "jobs.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def until(fn, seconds: float = 30):
    end = time.time() + seconds
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    raise AssertionError("never happened")


def kinds(o: dict) -> list[str]:
    return [x["kind"] for x in o["meeting"]["lines"]]


@pytest.fixture(scope="module")
def obox(tmp_path_factory):
    b = Box(tmp_path_factory.mktemp("office"))
    make_book.write_book(b.shelf)
    call(b.port, "POST", "/api/open", {"title": TITLE})
    yield b
    b.close()


# -- the staff ------------------------------------------------------------------------------------------------------------

def test_the_staff_and_their_checks():
    assert staff.IDS == ["chief", "story", "facts", "line", "copy", "reader", "board", "art"]
    assert [staff.name(s) for s in staff.IDS] == ["Dewey", "Margot", "Ines", "Theo", "Pearl", "Sam", "Kit", "Wren"]
    checks = engine.load_checks()
    for s in engine.STAGES:                           # every stage has one desk
        sid = staff.BY_STAGE[s["id"]]
        mine = staff.checks_of(sid, checks)
        assert mine and all(c["stage"] == s["id"] for c in mine)
        assert [c["order"] for c in mine] == sorted(c["order"] for c in mine)
    assert [c["id"] for c in staff.checks_of("chief", checks)] == ["future", "letter"]
    assert staff.checks_of("board", checks) == [] and staff.checks_of("art", checks) == []
    assert staff.of_check(checks["plot"]) == "story" and staff.of_check(checks["grammar"]) == "copy" and staff.of_check({}) == "chief"
    assert staff.of_check({"stage": "facts", "id": "weather"}) == "facts", "a new check file joins a desk by its stage"
    assert set(staff.public("story")) == {"id", "name", "role", "does", "img"} and staff.public("story")["img"] == "img/staff/story.png"
    for s in staff.STAFF:
        assert s["does"] and not any(d in s["does"] + s["lens"] for d in DASHES)
    assert all(staff.BY_ID[s]["lens"] for s in ("chief", "story", "facts", "line", "copy", "reader"))


def test_the_server_types_no_staff_name():
    code = (PKG / "server.py").read_text(encoding="utf-8")
    for s in staff.STAFF:
        assert not re.search(rf"\b{s['name']}\b", code), s["name"]
    for f in ("server.py", "staff.py"):
        text = (PKG / f).read_text(encoding="utf-8")
        assert not any(d in text for d in DASHES) and not re.search(r"\b" + "be" + "ta" + r"\b", text, re.I), f


# -- a pass ---------------------------------------------------------------------------------------------------------------

def _desk(tmp_path: Path) -> server.Desk:
    """Enough of a Desk to plan with, no server and no worker."""
    d = server.Desk.__new__(server.Desk)
    d.costs, d.timing, d.room = {}, server.Timing(tmp_path / "timing.json"), lambda: 5120
    return d


def test_the_plan_leaves_out_what_cannot_start(tmp_path):
    d = _desk(tmp_path)
    checks = engine.load_checks()
    unread = Book(make_book.write_book(tmp_path / "a", title="Unread", bible=None))
    p = d._plan(unread, checks, [], "story")
    assert p["can_start"] is False and p["checks"] == [] and "has not been read yet" in p["why_not"]
    assert [x["id"] for x in p["left_out"]] == [c["id"] for c in staff.checks_of("story", checks)]
    assert all("story bible" in x["why"] for x in p["left_out"])
    copy = d._plan(unread, checks, [], "copy")        # grammar, style sheet and proofread need no reading
    assert copy["can_start"] and [c["id"] for c in copy["checks"]] == ["grammar", "style-sheet", "proofread"]
    read = Book(make_book.write_book(tmp_path / "b"))
    boxed = {**checks, "weather": {**checks["themes"], "id": "weather", "name": "Weather", "order": 9, "box_needed": True, "box": "Say the season."}}
    lined = [{"folder": read.folder.name, "check": "plot"}]
    p = d._plan(read, boxed, lined, "story")
    why = {x["id"]: x["why"] for x in p["left_out"]}
    assert why == {"plot": "It is already in line.", "weather": "It needs your words in its box first: Say the season."}
    assert p["can_start"] and "plot" not in [c["id"] for c in p["checks"]] and p["checks"][0]["estimate"]
    assert p["time"] == "", "not timed yet"
    d.timing.n, d.timing.total = 10, 600.0          # a minute a call
    assert d._plan(read, checks, [], "story")["time"].startswith("about ")
    every = [{"folder": read.folder.name, "check": c["id"]} for c in checks.values()]
    p = d._plan(read, checks, every, "facts")
    assert not p["can_start"] and p["why_not"] == f"All of it is already in line for {read.title}."
    with pytest.raises(server.Refused):
        d._pass_who("board")
    assert d._pass_who("Margot") == "story" and d._pass_who(" ALL ") == "all"


def test_all_is_the_full_edit_then_the_letter(obox):
    code, p = call(obox.port, "GET", "/api/pass?" + q(title=TITLE, who="all"))
    assert code == 200, p
    checks = engine.load_checks()
    ids = [c["id"] for c in p["checks"]]
    want = [c["id"] for st in ("story", "facts", "prose", "polish") for c in checks.values() if c["stage"] == st]
    assert ids == want + ["letter"], ids
    assert not any(checks[i]["stage"] == "out" for i in ids), "getting it out is for the finished book"
    assert set(p) == {"who", "name", "checks", "left_out", "time", "can_start", "why_not"}
    assert p["who"] == "all" and p["can_start"] and p["why_not"] == ""
    code, err = call(obox.port, "GET", "/api/pass?" + q(title=TITLE, who="nobody"))
    assert code == 400 and "all for the full edit" in err["error"]
    assert not jobs(obox), "a plan starts nothing"


# -- the office -----------------------------------------------------------------------------------------------------------

def test_the_office_for_a_new_book(obox):
    asked = len(obox.core.asks)
    o = office(obox)
    assert o["version"] == "0.4.0" and o["title"] == TITLE
    assert o["chief"] == {"id": "chief", "name": staff.name("chief"), "role": "Editor-in-chief", "img": "img/staff/chief.png"}
    m = o["meeting"]
    assert m["first"] is True and m["since"] is None
    assert kinds(o) == ["hello", "next"]
    assert staff.name("chief") in m["lines"][0]["text"]
    nxt = m["lines"][-1]
    assert nxt["who"] == "story" and nxt["do"] == {"pass": "story"} and staff.name("story") in nxt["text"]
    assert m["say"] == "\n\n".join(x["text"] for x in m["lines"])
    assert [s["id"] for s in o["staff"]] == staff.IDS and all(s["state"] == "new" for s in o["staff"])
    story = o["staff"][1]
    assert story["pass"]["can_start"] and story["pass"]["label"] == f"Ask {staff.name('story')} to go over the book"
    assert [c["id"] for c in story["checks"]][0] == "plot" and story["open"] == 0 and story["top"] == []
    assert o["staff"][0]["pass"] is None and o["staff"][6]["pass"] is None
    assert o["pass"] is None and o["board"] == {"panels": 0, "made_at": None, "painted": 0}
    assert o["has_bible"] is True and o["unread"] == []
    assert len(obox.core.asks) == asked, "the office never asks the brain"


def test_the_board_and_pictures_are_read_with_care(obox):
    folder = obox.shelf / TITLE
    home = engine.home_of(folder)
    sb = obox.data / "books" / home.name / "storyboard"
    sb.mkdir(parents=True, exist_ok=True)
    pics = folder / "pictures.json"
    try:
        (sb / "board.json").write_text(json.dumps({"made_at": "2026-11-14T02:10:00Z", "panels": [{"id": "s1-1"}, {"id": "s1-2"}, {"id": "s2-1"}]}), encoding="utf-8")
        pics.write_text(json.dumps({"pictures": [{"id": "p1", "panel": "s1-1", "state": "ready"}, {"id": "p2", "panel": "s1-2", "state": "making"},
                                                 {"id": "p3", "kind": "portrait", "state": "ready"}]}), encoding="utf-8")
        o = office(obox)
        assert o["board"] == {"panels": 3, "made_at": "2026-11-14T02:10:00Z", "painted": 1}
        by = {s["id"]: s for s in o["staff"]}
        assert by["board"]["state"] == "idle" and by["board"]["last_worked"] == "2026-11-14T02:10:00Z" and by["art"]["state"] == "idle"
        (sb / "board.json").write_text("{not json", encoding="utf-8")
        pics.write_text(json.dumps({"pictures": "nope"}), encoding="utf-8")
        assert office(obox)["board"] == {"panels": 0, "made_at": None, "painted": 0}
    finally:
        (sb / "board.json").unlink(missing_ok=True)
        pics.unlink(missing_ok=True)


def test_a_visit_then_an_edit_is_news(obox):
    code, got = call(obox.port, "POST", "/api/visit", {"title": TITLE})
    assert code == 200 and got["at"]
    o = office(obox)
    assert o["meeting"]["first"] is False and o["meeting"]["since"] == got["at"]
    assert o["meeting"]["lines"][0]["text"].startswith("Welcome back.") and "You were last here on" in o["meeting"]["lines"][0]["text"]
    assert "news" not in kinds(o), "nothing changed yet"
    _, sec = call(obox.port, "GET", "/api/section?" + q(title=TITLE, n=2))
    code, saved = call(obox.port, "POST", "/api/save", {"title": TITLE, "n": 2, "text": sec["text"] + "\n\nFive more words are here.", "sha": sec["sha"]})
    assert code == 200 and saved["changed"]
    o = office(obox)
    news = [x for x in o["meeting"]["lines"] if x["kind"] == "news"]
    assert news and news[0]["text"] == "Since then you changed section 2: 5 more words."
    assert news[0]["go"] == {"view": "write", "section": 2}
    wait = next(x for x in o["meeting"]["lines"] if x["kind"] == "waiting")
    assert wait["text"].startswith("Section 2 changed since we last read it.") and "Read again in Manuscript" in wait["text"]
    assert o["unread"] == [2]
    assert kinds(o) == ["hello", "news", "waiting", "next"], "in the design's order"
    call(obox.port, "POST", "/api/restore", {"title": TITLE, "n": 2, "id": saved["version"], "sha": saved["sha"]})


def test_a_pass_goes_in_line_in_stage_order(obox):
    asked = len(obox.core.asks)
    text, err = tool(obox.port, "desk_assign", title=TITLE, who="story")
    assert not err and text.startswith("This has not started. Ask the writer:") and "confirm true" in text and "Plot and plot holes" in text
    text, err = tool(obox.port, "desk_assign", title=TITLE, who="story", confirm="yes")
    assert not err and text.startswith("This has not started.")
    time.sleep(0.3)
    assert not jobs(obox) and len(obox.core.asks) == asked, "nothing starts without her yes"
    obox.core.slow = 0.5
    try:
        code, got = call(obox.port, "POST", "/api/pass", {"title": TITLE, "who": "story"})
        assert code == 200, got
        assert set(got) >= {"pass", "queued", "left_out", "time", "message"} and staff.name("story") in got["message"]
        story = [c["name"] for c in staff.checks_of("story", engine.load_checks())]
        assert got["queued"] == story and got["left_out"] == []
        line = jobs(obox)
        assert [j["check_name"] for j in line] == story, "in the desk's order"
        assert all(j["who"] == "story" and j["pass"] == got["pass"] for j in line)
        code, again = call(obox.port, "POST", "/api/pass", {"title": TITLE, "who": "story"})
        assert code == 400 and "already in line" in again["error"]
        _, plan = call(obox.port, "GET", "/api/pass?" + q(title=TITLE, who="story"))
        assert plan["can_start"] is False and all(x["why"] == "It is already in line." for x in plan["left_out"])
        # while it runs
        o = until(lambda: (lambda o: o if {s["id"]: s for s in o["staff"]}["story"]["state"] == "working" else None)(office(obox)))
        story_row = {s["id"]: s for s in o["staff"]}["story"]
        assert story_row["doing"].startswith("Plot and plot holes") and story_row["checks"][0]["running"] == "running"
        assert o["pass"]["id"] == got["pass"] and o["pass"]["who"] == "story" and o["pass"]["total"] == len(story)
        assert o["pass"]["left"][0] == "Plot and plot holes" and o["pass"]["done"] == len(story) - len(o["pass"]["left"])
        work = next(x for x in o["meeting"]["lines"] if x["kind"] == "working")
        assert work["who"] == "story" and work["text"].startswith(f"{staff.name('story')} is working on Plot and plot holes")
        assert f"In line: {staff.name('story')} with {len(story) - 1} checks." in work["text"]
        assert "next" not in kinds(o), "the story pass is already in line"
        assert office(obox)["status"]["check"] == "plot"
        call(obox.port, "POST", "/api/stop", {"title": TITLE})
        obox.done("plot")
        assert not jobs(obox) and office(obox)["pass"] is None, "Stop works as today"
    finally:
        obox.core.slow = 0


def test_the_office_after_findings(obox):
    v = obox.run("plot")
    assert v["status"]["state"] == "done", v["status"]
    assert all(f["who"] == staff.of_check(engine.load_checks().get(f["check"]) or {}) for f in v["findings"])
    assert [s["id"] for s in v["staff"]] == staff.IDS and set(v["staff"][0]) == {"id", "name", "role", "does", "img"}
    o = office(obox)
    by = {s["id"]: s for s in o["staff"]}
    story = by["story"]
    assert story["state"] == "idle" and story["last_worked"] and story["open"] > 0 and story["big"] > 0
    assert 0 < len(story["top"]) <= 3 and story["top"][0]["size"] == "big" and story["top"][0]["check_name"] == "Plot and plot holes"
    assert set(story["top"][0]) == {"id", "what", "size", "section", "check_name"}
    sizes = [engine.SIZE_RANK[t["size"]] for t in story["top"]]
    assert sizes == sorted(sizes), "biggest first"
    assert next(c for c in story["checks"] if c["id"] == "plot")["last_run"]
    found = [x for x in o["meeting"]["lines"] if x["kind"] == "found"]
    assert found and found[0]["who"] == "story" and found[0]["text"].startswith(f"{staff.name('story')} found something big: ")
    assert found[0]["go"]["finding"] in {f["id"] for f in v["findings"]} and found[0]["go"]["view"] == "desk"
    news = [x for x in o["meeting"]["lines"] if x["kind"] == "news"]
    assert any(x["text"].startswith(f"{staff.name('story')} finished Plot and plot holes: ") and "big thing" in x["text"] for x in news), news
    nxt = o["meeting"]["lines"][-1]
    assert nxt["kind"] == "next" and nxt.get("do") != {"pass": "story"}, "a check has run, so the desk's own Next"
    assert nxt.get("do") or nxt.get("go")
    order = ["hello", "news", "working", "found", "waiting", "next"]
    assert [order.index(k) for k in kinds(o)] == sorted(order.index(k) for k in kinds(o))


def test_asking_one_of_the_staff(obox):
    code, got = call(obox.port, "POST", "/api/ask", {"title": TITLE, "n": 2, "passage": "The silver key stayed in her coat", "question": "Does it hold?", "who": "story"})
    assert code == 200 and got["ask"]["who"] == "story", got
    system, _user = obox.core.asks[-1]
    assert "You are her editor" in system and staff.BY_ID["story"]["lens"] in system
    code, got = call(obox.port, "POST", "/api/ask", {"title": TITLE, "n": 2, "passage": "The silver key stayed in her coat", "question": "Grammar?"})
    assert code == 200 and got["ask"]["who"] == "chief" and staff.BY_ID["chief"]["lens"] in obox.core.asks[-1][0]
    saved = call(obox.port, "GET", "/api/asks?" + q(title=TITLE))[1]["asks"]
    assert [a["who"] for a in saved[-2:]] == ["story", "chief"]
    code, err = call(obox.port, "POST", "/api/ask", {"title": TITLE, "n": 2, "passage": "The silver key", "who": "board"})
    assert code == 400 and staff.name("story") in err["error"]
    text, err = tool(obox.port, "desk_ask", title=TITLE, question="What would change?", who="facts")
    assert not err and staff.BY_ID["facts"]["lens"] in obox.core.asks[-1][0]


def test_the_three_tools(obox):
    _, listed = call(obox.port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {t["name"]: t for t in listed["result"]["tools"]}
    assert tools["desk_assign"]["annotations"]["readOnlyHint"] is False and "desk_assign" in server.CHANGES
    assert tools["desk_meeting"]["annotations"]["readOnlyHint"] is True and tools["desk_staff"]["annotations"]["readOnlyHint"] is True
    assert tools["desk_assign"]["inputSchema"]["required"] == ["who"] and "who" in tools["desk_ask"]["inputSchema"]["properties"]
    asked = len(obox.core.asks)
    text, err = tool(obox.port, "desk_meeting", title=TITLE)
    o = office(obox)
    rows = text.split("\n")
    assert not err and staff.name("chief") in rows[0] and len(rows) == len(o["meeting"]["lines"]) + 1
    assert rows[1].startswith("- Welcome back.")
    assert f"({staff.name('story')} is the story editor.)" in text and text.count("is the story editor") == 1, "the role the first time only"
    text, err = tool(obox.port, "desk_staff", title=TITLE)
    rows = text.split("\n")
    assert not err and len(rows) == len(staff.IDS) + 1
    assert rows[2].startswith(f"- {staff.name('story')}, story editor [story]: ") and "big" in rows[2]
    assert "No storyboard yet." in rows[7] and "Nothing painted yet." in rows[8]
    assert len(obox.core.asks) == asked, "the meeting and the staff ask the brain nothing"
    text, err = tool(obox.port, "desk_assign", title=TITLE, who="copy", confirm=True)
    assert not err and "is going over" in text and "Grammar and syntax" in text
    line = jobs(obox)
    assert line and all(j["who"] == "copy" and j.get("pass") for j in line)
    obox.done("proofread", seconds=90)
    text, err = tool(obox.port, "desk_assign", title=TITLE, who="nobody")
    assert err
