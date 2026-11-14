"""Editor's Desk 0.1.0 against a stand-in Core and brain: the server runs as its own process, the Core here answers
``/v1/owner-files`` and ``/v1/brain/chat`` with canned JSON, and a fixture novel has planted problems. Run from the package
folder: ``python -m pytest tests -q``.
"""

from __future__ import annotations

import hashlib
import io
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
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(PKG / "fixtures"))
import engine  # noqa: E402
import finders  # noqa: E402
import frame_book  # noqa: E402
import make_book  # noqa: E402
import study_book  # noqa: E402
from book import Book, locate  # noqa: E402

TOKEN = "test-token-789"
TITLE = make_book.TITLE
REAL = "The silver key stayed in her coat"
MADE_UP = "She buried the silver key beneath the old oak at midnight"


class Core:
    """Where My files is, and a brain that answers by the words of each ask. ``slow`` holds a call for that long."""

    def __init__(self, made: Path) -> None:
        self.made, self.asks, self.slow, self.fail = made, [], 0.0, False
        self.bodies: list[dict] = []
        self.too_long, self.timed_out, self.limit = "", 0, 0
        core = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _out(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._out(200, {"made": str(core.made)})

            def do_POST(self):
                got = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                if core.fail:
                    return self._out(503, {"error": "resting"})
                if core.too_long and core.too_long in got["messages"][1]["content"] and len(got["messages"][1]["content"]) > core.limit:
                    core.timed_out += 1
                    return self._out(503, {"error": "did not answer in time", "state": "timed_out"})
                core.bodies.append(got)
                system, user = got["messages"][0]["content"], got["messages"][1]["content"]
                core.asks.append((system, user))
                if core.slow:
                    time.sleep(core.slow)
                self._out(200, {"text": json.dumps(answer(system, user)), "model": "stand-in", "usage": {"prompt_tokens": len(user) // 4, "completion_tokens": 50}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]


JUDGE_NO = False
EVIDENCE = ["\"Hold the line, and keep the mule between you and the wind,\" Harrow said", "February 20, 1891 was the coldest day",
            "It opens what your grandfather buried", "Morning showed them tracks, three men and a cart", "There are", "On Monday, March 3, 1891"]


def study_answer(user: str):
    """A stand-in editor for Study a book: notes that quote real words of each passage, then the pointers in order."""
    if "Passage A" not in user:
        return [{"try": "Let a chapter close on a line of talk.", "why": "It leaves the reader leaning in.", "from": "ending"}]
    a = user.split("Passage A, from the book she admires:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
    b = user.split("Passage B, from her manuscript:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
    few = lambda t: " ".join(t.split("\n\n")[0].split()[:6])      # noqa: E731 - exactly as written
    return {"theirs_does": "Long sentences that carry the setting.", "mine_does": "Short lines, mostly talk.",
            "pointers": [{"try": "Let one long sentence set the place before the talk starts.", "why": "It gives the reader somewhere to stand.",
                          "their_quote": few(a), "my_quote": few(b)}]}


def answer(system: str, user: str):
    if "helping a writer learn from a book she admires" in system:
        return study_answer(user)
    if "check an editor's notes" in system:
        return {"keep": [1, 2, 3]}
    if "check an editor's note" in system:
        cited = user.split("The words it cites: ", 1)[1].split(" | ")[0].split("\n")[0] if "The words it cites: " in user else ""
        # a frame story's "late" narrator: the model says it holds, but can point at nothing that shows a problem
        # the model points at the first words it was given; a finding between two places needs both to stand
        return {"holds": "REJECT THIS" not in user, "evidence": cited, "reason": "shown"}
    if "Order these notes" in system:
        return {"order": [1], "big": [1]}
    if '"verdict"' in system:
        if JUDGE_NO:
            return {"verdict": "question", "why": "It may be a memory."}
        if "seventeen years" in user or "papers" in user.split("The passages")[0]:
            return {"verdict": "no", "why": "The frame says it goes back."}
        if "tide" in user.split("The passages")[0].lower():
            return {"verdict": "no"}
        ev = next((e for e in EVIDENCE if e in user), "")
        return {"verdict": "problem" if ev else "question", "why": "A reader would notice.", "fix": "Look again.", "size": "big", "evidence": ev}
    if "Write her letter" in system:
        return {"book": "A short hard book about a woman walking north.", "working": ["The salt flats are vivid."], "problems": [{"n": 1, "line": "The first problem, which matters most."}, {"n": 99, "line": "A made-up one."}], "first": "Settle the key."}
    if "one plot thread only" in system and "The silver key" in user:
        return {"findings": [
            {"what": "Mara carries it north and no lock ever opens.", "why": "The book promises it.", "fix": "Use it at the cairn.", "size": "big", "quotes": [{"section": 7, "quote": REAL}]},
            {"what": "The key is buried under an oak, which the book never says.", "why": "x", "fix": "y", "size": "big", "quotes": [{"section": 2, "quote": MADE_UP}]},
            {"what": "REJECT THIS one, says the second pass.", "why": "x", "fix": "y", "size": "medium", "quotes": [{"section": 1, "quote": "Her father had pressed the key into her hand"}]}]}
    if "real errors of grammar" in system and "the the" in user:
        return {"findings": [{"what": "A doubled word.", "why": "Typo.", "fix": "Drop one.", "size": "small", "quotes": [{"section": 1, "quote": "find the the money"}], "correct": "find the money"}]}
    if "She is thinking of this change" in system:
        return {"findings": [{"what": "If Elias lives on, this speech needs revisiting.", "why": "It carries his fear.", "fix": "See what he fears now.", "size": "medium", "quotes": [{"section": 4, "quote": "I came because you did"}]}]}
    if "Never say whether a fact is right" in system and "Section 5" in user:
        return {"findings": [{"what": "REALWORLD: this date is incorrect.", "why": "x", "fix": "y", "quotes": [{"section": 5, "quote": "February 20, 1891 was the coldest"}]},
                             {"what": "Look up how a salt works kept a ledger then.", "why": "A reader in the trade would know.", "fix": "Look it up.", "quotes": [{"section": 5, "quote": "a ledger with her grandfather"}]}]}
    if "one plot thread only" in system and "The cult" in user:
        return {"findings": [
            {"what": "Edmund Pryce is called late in the first pages, before he dies.", "why": "x", "fix": "y", "size": "medium",
             "quotes": [{"section": 1, "quote": "Found among the papers of the late Edmund Pryce"}, {"section": 8, "quote": "Edmund Pryce died in the spring"}]},
            {"what": f"Remove the search artifact {frame_book.HANDLE} from the manuscript.", "why": "x", "fix": "y", "size": "medium", "quotes": [{"section": 2, "quote": "a young sculptor named Wilcox"}]}]}
    if "You are her editor" in system:
        return "The words \"stayed in her coat\" are plain and strong."
    if "one-page synopsis" in system:
        return "A synopsis draft."
    if "query letter" in system:
        return "A query draft."
    return {"findings": []}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(port, method, path, body=None, token=TOKEN):
    data = None if body is None else json.dumps(body).encode()
    head = {"Content-Type": "application/json"}
    if token:
        head["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=head)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw and r.headers.get("Content-Type", "").startswith("application/json") else raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, (json.loads(raw) if raw else {})


def q(**kw) -> str:
    return urllib.parse.urlencode(kw)


def tool(port, name, **args):
    code, got = call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    assert code == 200, got
    res = got["result"]
    return res["content"][0]["text"], res.get("isError", False)


class Box:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.made = root / "My files" / "Made by bot"
        self.made.mkdir(parents=True)
        self.shelf = root / "My files" / "Manuscripts"
        self.shelf.mkdir(parents=True)
        self.core = Core(self.made)
        self.data = root / "data"
        self.port, self.proc = 0, None
        self.start()

    def start(self) -> None:
        self.port = free_port()
        env = {k: v for k, v in os.environ.items() if not k.upper().endswith("_PROXY")}
        env.update(PUFFPAI_API=f"http://127.0.0.1:{self.core.port}", PUFFPAI_TOKEN=TOKEN, PUFFPAI_KEYRING="stub", PUFFPAI_DATA=str(self.data), EDITORS_DESK_POLITE_S="0.2")
        self.proc = subprocess.Popen([sys.executable, "server.py", "--port", str(self.port)], cwd=str(PKG), env=env, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if call(self.port, "GET", "/health")[0] == 200:
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def stop(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=10)

    def close(self) -> None:
        self.stop()
        self.core.server.shutdown()

    def run(self, check, title=TITLE, plan="", seconds=60, wait=True) -> dict:
        code, got = call(self.port, "POST", "/api/run", {"title": title, "check": check, "plan": plan})
        assert code == 200, got
        if wait:
            return self.done(check, title, seconds)
        return got

    def done(self, check, title=TITLE, seconds=60) -> dict:
        end = time.time() + seconds
        while time.time() < end:
            v = self.view(title)
            st = v["status"] or {}
            if st.get("check") == check and st.get("state") in ("done", "failed", "stopped"):
                return v
            time.sleep(0.1)
        raise AssertionError(f"{check} never finished: {v['status']}")

    def view(self, title=TITLE) -> dict:
        code, got = call(self.port, "GET", "/api/desk?" + q(title=title))
        assert code == 200, got
        return got


@pytest.fixture(scope="module")
def box(tmp_path_factory):
    b = Box(tmp_path_factory.mktemp("desk"))
    make_book.write_book(b.shelf)
    call(b.port, "POST", "/api/open", {"title": TITLE})
    yield b
    b.close()


def found(v, check) -> list[dict]:
    return [f for f in v["findings"] if f["check"] == check]


# -- code finds the planted problems, with no brain ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def plain_book(tmp_path_factory):
    return Book(make_book.write_book(tmp_path_factory.mktemp("code")))


def test_finds_the_dropped_thread(plain_book):
    got = finders.run("open_threads", plain_book)
    key = next(c for c in got if "silver key" in c["what"])
    assert "section 2 of 8" in key["what"] and key["size"] == "big"
    assert {w["section"] for w in key["where"]} == {1, 2}


def test_finds_the_dead_man_speaking(plain_book):
    got = finders.run("dead_speaking", plain_book)
    assert len(got) == 1 and "Harrow" in got[0]["what"]
    assert [w["section"] for w in got[0]["where"]] == [3, 6]
    assert "Hold the line" in got[0]["where"][1]["quote"]


def test_finds_the_dates_running_backward(plain_book):
    got = finders.run("dates_backward", plain_book)
    assert len(got) == 1 and "March 3, 1891" in got[0]["what"] and "February 20, 1891" in got[0]["what"]


def test_finds_the_eye_colour_changing(plain_book):
    got = finders.run("looks_changed", plain_book)
    assert len(got) == 1 and "grey in section 1" in got[0]["what"] and "brown in section 7" in got[0]["what"]


def test_more_finders(plain_book):
    assert any("Ellias" in c["what"] for c in finders.run("names_spelled", plain_book))
    day = finders.run("wrong_weekday", plain_book)[0]["what"]
    assert "March 3, 1891" in day and "was a Tuesday, not a Monday" in day
    proof = finders.run("proof_marks", plain_book)
    double = next(c for c in proof if "doubled" in c["what"])
    assert "the money" in double["correct"] and "the the" not in double["correct"]
    assert any("harbor" in c["what"] and "harbour" in c["what"] for c in finders.run("style_sheet_findings", plain_book))
    assert any("six" in c["what"] for c in finders.run("world_numbers", plain_book))
    for name in finders.FINDERS:                       # every quote a finder gives is really in the text
        for c in finders.run(name, plain_book):
            for w in c.get("where") or []:
                assert locate(plain_book.section(w["section"]).text, w["quote"]), (name, w)


def test_a_book_read_under_0_3_2_or_not_at_all(tmp_path):
    """No looks, geography or structure: the text still gives the eye colour. An empty bible breaks nothing."""
    old = {k: v for k, v in make_book.BIBLE.items() if k not in ("structure", "scenes", "voice", "briefs", "geography", "themes", "science", "world_rules")}
    b = Book(make_book.write_book(tmp_path, bible=old))
    assert finders.run("looks_changed", b)
    assert finders.run("travel_times", b) == [] and finders.run("shape", b) == []
    none = Book(make_book.write_book(tmp_path, title="Unread", bible=None))
    for name in finders.FINDERS:
        assert isinstance(finders.run(name, none), list)
    assert "Style sheet" in finders.style_sheet(none)


# -- a check, whole -----------------------------------------------------------------------------------------------------

def test_a_check_starts_only_on_her_yes(box):
    asked = len(box.core.asks)
    text, err = tool(box.port, "desk_run", title=TITLE, check="plot")
    assert not err and "has not started" in text and "Ask the writer" in text
    time.sleep(0.3)
    assert len(box.core.asks) == asked and not (box.view()["status"] or {}).get("check")


def test_plot_check_made_up_quote_is_dropped(box):
    v = box.run("plot")
    assert v["status"]["state"] == "done", v["status"]
    mine = found(v, "plot")
    words = " ".join(f["what"] for f in mine)
    assert "no lock ever opens" in words
    assert "oak" not in words, "a finding with a made-up quote is dropped"
    assert "REJECT" not in words, "the second pass drops what it can't support"
    # every quote really is in the text, in the section given, even when the model named the wrong one
    b = Book(box.shelf / TITLE)
    for f in mine:
        assert f["where"] and f["why"] is not None and f["fix"] is not None
        for w in f["where"]:
            if f.get("found_by") != "old desk":
                assert locate(b.section(w["section"]).text, w["quote"]), w
    used = next(f for f in mine if "no lock ever" in f["what"])
    assert used["where"][0]["section"] == 2, "the section is where the words really are, not what the model said"
    assert any(f["what"].startswith("The thread") for f in mine) or any("silver key" in f["what"] for f in mine)
    # the asks were focused: each small, with the passages it concerns
    assert max(engine.tokens(s) + engine.tokens(u) for s, u in box.core.asks) < 5200
    assert all(x["as_written"] is True for x in box.core.bodies)


def test_letter_is_written_from_real_findings(box):
    v = box.view()
    lt = v["letter"]
    assert lt["book"] and lt["working"] and lt["first"] == "Settle the key."
    ids = {f["id"] for f in v["findings"]}
    assert lt["problems"] and all(p["id"] in ids for p in lt["problems"])
    assert not any("made-up one" in p["line"] for p in lt["problems"])
    assert v["plan"], "a revision plan from the open findings"


def test_continuity_check_finds_the_planted_facts(box):
    v = box.run("continuity")
    mine = " ".join(f["what"] for f in v["findings"])
    assert "eyes change" in mine and "dates run backward" in mine and "Ellias" in mine and "not a Monday" in mine


def test_a_doubting_model_makes_a_question_not_a_finding(tmp_path):
    global JUDGE_NO
    box2 = Box(tmp_path)
    try:
        make_book.write_book(box2.shelf)
        JUDGE_NO = True
        v = box2.run("continuity")
        dead = next(f for f in v["findings"] if "dies in section 3" in f["what"])
        assert dead["kind"] == "question" and dead["what"].startswith("Check: ") and dead["reason"] == "It may be a memory."
        assert v["stages"][1]["big_open"] == 0 and not v["letter"]["problems"] or all(p["id"] != dead["id"] for p in v["letter"]["problems"])
    finally:
        JUDGE_NO = False
        box2.close()


def test_real_world_never_states_a_fact_as_checked(tmp_path):
    box2 = Box(tmp_path)
    try:
        make_book.write_book(box2.shelf)
        box2.run("real-world")
        assert not any("REALWORLD" in f["what"] for f in box2.view()["findings"])
        assert any("ledger" in f["what"] for f in box2.view()["findings"])
    finally:
        box2.close()


def test_a_fix_never_carries_new_story_text(plain_book):
    fix = "Mark it as a memory, perhaps starting with 'Mara closed her eyes and remembered his voice saying'."
    assert "closed her eyes" not in engine.no_new_prose(fix, plain_book) and "Mark it as a memory" in engine.no_new_prose(fix, plain_book)
    real = "Cut 'Hold the line, and keep the mule' from section 6."
    assert engine.no_new_prose(real, plain_book) == real, "her own words can be named"


def test_marks_survive_a_rerun(box):
    v = box.view()
    a = next(f for f in found(v, "plot") if "no lock ever" in f["what"])
    b = next(f for f in found(v, "plot") if f["what"].startswith("The thread \"The three men") or "three men" in f["what"])
    code, _ = call(box.port, "POST", "/api/mark", {"title": TITLE, "id": a["id"], "state": "not_a_problem", "note": "It pays off in book two"})
    assert code == 200
    code, _ = call(box.port, "POST", "/api/mark", {"title": TITLE, "id": b["id"], "state": "fixed"})
    assert code == 200
    asks = len(box.core.asks)
    v = box.run("plot")
    a2 = next(f for f in v["findings"] if f["id"] == a["id"])
    assert a2["state"] == "not_a_problem" and a2["note"] == "It pays off in book two"
    b2 = next(f for f in v["findings"] if f["id"] == b["id"])
    assert b2["state"] == "fixed" and b2.get("still_broken") is True, "a re-run says when something marked Fixed still looks broken"
    assert sum(1 for f in v["findings"] if "no lock ever" in f["what"]) == 1
    new = box.core.asks[asks:]
    assert all("Write her letter" in s for s, _u in new), "nothing changed, so only the letter (her marks moved) is asked again"


def test_resume_after_a_restart(tmp_path):
    box2 = Box(tmp_path)
    try:
        make_book.write_book(box2.shelf)
        box2.core.slow = 0.25
        box2.run("grammar", wait=False)
        end = time.time() + 30
        while len(box2.core.asks) < 3 and time.time() < end:
            time.sleep(0.05)
        done_before = len(box2.core.asks)
        assert done_before >= 3
        box2.stop()                                     # the process dies in the middle
        jobs = json.loads((box2.data / "jobs.json").read_text(encoding="utf-8"))
        assert jobs and jobs[0]["check"] == "grammar" and jobs[0]["state"] in ("running", "waiting")
        answered = {u for _s, u in box2.core.asks}
        count = len(box2.core.asks)
        box2.core.slow = 0
        box2.start()                                    # nobody presses anything: it goes on by itself
        v = box2.done("grammar")
        assert v["status"]["state"] == "done"
        again = box2.core.asks[count:]
        assert len(again) < 8 * 2, "the saved steps are not asked again"
        repeats = sum(1 for _s, u in again if u in answered)
        assert repeats <= 1, "at most the call that was cut off"
        assert any(f["check"] == "grammar" for f in v["findings"])
    finally:
        box2.close()


def test_waits_while_manuscript_reads(tmp_path):
    box2 = Box(tmp_path)
    try:
        folder = make_book.write_book(box2.shelf)
        (folder / "status.json").write_text(json.dumps({"state": "reading", "step": "Reading section 1 of 8"}), encoding="utf-8")
        box2.run("characters", wait=False)
        end = time.time() + 20
        while time.time() < end and (box2.view()["status"] or {}).get("state") != "waiting":
            time.sleep(0.05)
        st = box2.view()["status"]
        assert st["state"] == "waiting" and "Manuscript reads" in st["step"]
        assert not box2.core.asks
        (folder / "status.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")
        assert box2.done("characters")["status"]["state"] == "done"
        # a status left behind by a Manuscript that stopped does not hold the desk for ever
        (folder / "status.json").write_text(json.dumps({"state": "reading"}), encoding="utf-8")
        old = time.time() - engine.STALE_READING_S - 60
        os.utime(folder / "status.json", (old, old))
        assert box2.run("themes")["status"]["state"] == "done"
    finally:
        box2.close()


# -- the text ------------------------------------------------------------------------------------------------------------

def test_save_keeps_versions_and_restore_brings_them_back(box):
    folder = box.shelf / TITLE
    original = (folder / "original.txt").read_bytes()
    code, sec = call(box.port, "GET", "/api/section?" + q(title=TITLE, n=2))
    assert code == 200 and "Monday, March 3" in sec["text"] and not sec["text"].startswith("#")
    before = sec["text"]
    code, got = call(box.port, "POST", "/api/save", {"title": TITLE, "n": 2, "text": before + "\n\nA new paragraph of hers.", "sha": sec["sha"]})
    assert code == 200 and got["message"] == "Saved." and got["changed"]
    file = folder / "sections" / "02 - Chapter Two.md"
    raw = file.read_text(encoding="utf-8")
    assert raw.startswith("# Chapter Two\n\n") and "A new paragraph of hers." in raw
    idx = json.loads((folder / "manuscript.json").read_text(encoding="utf-8"))
    e = next(x for x in idx["sections"] if x["n"] == 2)
    assert e["sha"] == hashlib.sha256(raw.encode()).hexdigest() and e["words"] == len((before + " A new paragraph of hers.").split())
    assert next(x for x in idx["chapters"] if x["n"] == 2)["sha"] == e["sha"]
    # a stale page is refused and loses nothing
    code, err = call(box.port, "POST", "/api/save", {"title": TITLE, "n": 2, "text": "old page", "sha": sec["sha"]})
    assert code == 400 and "changed somewhere else" in err["error"] and "old page" not in file.read_text(encoding="utf-8")
    # shown as changed since last read
    _, now_ = call(box.port, "GET", "/api/section?" + q(title=TITLE, n=2))
    assert now_["unread"] is True and len(now_["versions"]) == 1
    assert 2 in box.view()["unread"]
    # an earlier version can be seen and restored; the one replaced is kept too
    vid = now_["versions"][0]["id"]
    _, ver = call(box.port, "GET", "/api/version?" + q(title=TITLE, n=2, id=vid))
    assert ver["text"] == before
    code, back = call(box.port, "POST", "/api/restore", {"title": TITLE, "n": 2, "id": vid, "sha": now_["sha"]})
    assert code == 200 and back["text"] == before
    _, end = call(box.port, "GET", "/api/section?" + q(title=TITLE, n=2))
    assert end["text"] == before and len(end["versions"]) == 2 and end["unread"] is False, "back to the old text: nothing to re-read"
    assert (folder / "original.txt").read_bytes() == original, "her upload is never touched"


def test_a_finding_says_when_its_section_changed(box):
    v = box.view()
    f = next(f for f in found(v, "plot") if f["where"] and f["where"][0]["section"] == 1 and f.get("sha"))
    _, sec = call(box.port, "GET", "/api/section?" + q(title=TITLE, n=1))
    call(box.port, "POST", "/api/save", {"title": TITLE, "n": 1, "text": sec["text"] + "\n\nOne more line.", "sha": sec["sha"]})
    assert next(x for x in box.view()["findings"] if x["id"] == f["id"])["changed"] is True
    _, sec2 = call(box.port, "GET", "/api/section?" + q(title=TITLE, n=1))
    call(box.port, "POST", "/api/restore", {"title": TITLE, "n": 1, "id": sec2["versions"][0]["id"], "sha": sec2["sha"]})


@pytest.mark.parametrize("bad", ["../../secret", "..\\..\\secret", "/etc/passwd", "99", "2/../../x", ""])
def test_path_escapes_refused(box, bad):
    (box.root / "secret").write_text("secret", encoding="utf-8")
    for method, path, body in [("GET", "/api/section?" + q(title=TITLE, n=bad), None), ("POST", "/api/save", {"title": TITLE, "n": bad, "text": "x"}),
                               ("GET", "/api/version?" + q(title=TITLE, n=2, id=bad), None), ("POST", "/api/restore", {"title": TITLE, "n": 2, "id": bad}),
                               ("GET", "/api/document?" + q(title=TITLE, id=bad), None)]:
        code, _got = call(box.port, method, path, body)
        assert code == 400, (method, path)
    assert (box.root / "secret").read_text(encoding="utf-8") == "secret"
    code, _ = call(box.port, "POST", "/api/save", {"title": bad or "nobody", "n": 1, "text": "x"})
    assert code == 400


def test_a_section_file_pointing_out_is_refused(tmp_path):
    box2 = Box(tmp_path)
    try:
        folder = make_book.write_book(box2.shelf)
        outside = tmp_path / "outside.md"
        outside.write_text("# x\n\nkeep me\n", encoding="utf-8")
        idx = json.loads((folder / "manuscript.json").read_text(encoding="utf-8"))
        idx["sections"][0]["file"] = "../../../outside.md"
        (folder / "manuscript.json").write_text(json.dumps(idx), encoding="utf-8")
        code, _ = call(box2.port, "POST", "/api/save", {"title": TITLE, "n": 1, "text": "overwrite"})
        assert code == 400 and outside.read_text(encoding="utf-8") == "# x\n\nkeep me\n"
        for name in ("../server.py", "..%2fserver.py", "..%5cserver.py"):
            code, _ = call(box2.port, "GET", "/" + name)
            assert code == 404
    finally:
        box2.close()


def test_the_bot_tools_cannot_write_text(box):
    folder = box.shelf / TITLE
    shas = {p.name: p.read_bytes() for p in (folder / "sections").iterdir()}
    _, listed = call(box.port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {"desk_checks", "desk_run", "desk_status", "desk_findings", "desk_mark", "desk_letter", "desk_ask", "desk_documents", "desk_stop",
                     "desk_study", "desk_study_report"}
    for t in listed["result"]["tools"]:                 # nothing that could carry her text, or read as a file argument
        props = set(t["inputSchema"]["properties"])
        assert not props & {"text", "body", "content", "path", "file", "section", "document", "correct"}, t["name"]
    tool(box.port, "desk_checks", title=TITLE)
    tool(box.port, "desk_status", title=TITLE)
    tool(box.port, "desk_findings", title=TITLE)
    tool(box.port, "desk_letter", title=TITLE)
    tool(box.port, "desk_documents", title=TITLE)
    tool(box.port, "desk_ask", title=TITLE, question="What would change?")
    tool(box.port, "desk_study", title=TITLE)
    tool(box.port, "desk_study_report", title=TITLE)
    last = [f for f in box.view()["findings"] if f["state"] == "open" and not f.get("carried")][-1]
    text, _ = tool(box.port, "desk_mark", title=TITLE, id=last["id"], state="later", note="after the holidays")
    assert text.startswith("Marked Later")
    assert {p.name: p.read_bytes() for p in (folder / "sections").iterdir()} == shas


def test_every_route_needs_the_token(box):
    for method, path in (("GET", "/"), ("GET", "/health"), ("GET", "/api/list"), ("GET", "/api/desk?" + q(title=TITLE)), ("POST", "/api/save"), ("POST", "/api/use"), ("POST", "/mcp"), ("GET", "/app.js")):
        for token in (None, "wrong"):
            code, _ = call(box.port, method, path, {} if method == "POST" else None, token=token)
            assert code == 401, (method, path)


def test_use_this_only_on_grammar_and_undoable(tmp_path):
    box2 = Box(tmp_path)
    try:
        folder = make_book.write_book(box2.shelf)
        v = box2.run("grammar")
        f = next(x for x in found(v, "grammar") if x.get("correct"))
        assert f["can_use"] and f["correct"] == "find the money"
        file = folder / "sections" / "01 - Chapter One.md"
        code, got = call(box2.port, "POST", "/api/use", {"title": TITLE, "id": f["id"]})
        assert code == 200 and "find the money" in file.read_text(encoding="utf-8") and "the the" not in file.read_text(encoding="utf-8")
        assert next(x for x in box2.view()["findings"] if x["id"] == f["id"])["state"] == "fixed"
        _, sec = call(box2.port, "GET", "/api/section?" + q(title=TITLE, n=1))
        assert sec["versions"][0]["by"] == "Use this"
        call(box2.port, "POST", "/api/restore", {"title": TITLE, "n": 1, "id": sec["versions"][0]["id"], "sha": sec["sha"]})
        assert "the the" in file.read_text(encoding="utf-8")
        p = box2.run("plot")
        plot = next(x for x in found(p, "plot") if x["found_by"] != "old desk")
        assert not plot.get("can_use")
        code, _ = call(box2.port, "POST", "/api/use", {"title": TITLE, "id": plot["id"]})
        assert code == 400
    finally:
        box2.close()


# -- the old desk ---------------------------------------------------------------------------------------------------------

def test_old_desk_carries_over_once(box):
    v = box.view()
    kept = next(f for f in v["findings"] if f["id"] == "d-kept000001")
    assert kept["state"] == "later" and kept["note"] == "I know, working on this" and kept["carried"] == "kept"
    assert not any(f["id"] == "d-dism000002" for f in v["findings"]), "\"Why a mule?\" says too little to carry"
    assert json.loads((box.shelf / TITLE / "desk.json").read_text(encoding="utf-8")) == make_book.DESK, "desk.json is left as it was"
    call(box.port, "POST", "/api/open", {"title": TITLE})
    assert sum(1 for f in box.view()["findings"] if f["id"] == "d-kept000001") == 1
    assert next(f for f in box.run("plot")["findings"] if f["id"] == "d-kept000001")["state"] == "later", "a re-run keeps it"


def test_get_changes_nothing(tmp_path):
    box2 = Box(tmp_path)
    try:
        folder = make_book.write_book(box2.shelf)
        box2.view()
        assert not list((box2.data).rglob("carried.json")), "only a POST brings the old desk over"
        assert not (folder / "editors-desk.json").exists()
    finally:
        box2.close()


# -- the stages and the other checks ------------------------------------------------------------------------------------------

def test_stages_and_next(box):
    v = box.view()
    assert [s["name"] for s in v["stages"]] == ["The story", "The facts", "The prose", "The polish", "Getting it out"]
    assert [c["id"] for c in v["stages"][0]["checks"]] == ["plot", "structure", "characters", "point-of-view", "opening-ending", "themes"]
    assert v["next"]["check"] in ("structure", None) and "polishing" in v["next"]["why"] or "Settle" in v["next"]["why"]
    assert [c["id"] for c in v["anytime"]] == ["future"]
    for s in v["stages"]:
        for c in s["checks"]:
            assert c["says"] and c["trust"], c["id"]


def test_a_new_check_is_a_new_file(tmp_path):
    (tmp_path / "x.md").write_text("---\nname: Weather\nstage: facts\nsays: The weather.\n---\n\n## section\nLook at the weather.\n", encoding="utf-8")
    c = engine.parse_check((tmp_path / "x.md").read_text(encoding="utf-8"), "x")
    assert c["name"] == "Weather" and c["stage"] == "facts" and "section" in c["blocks"]


def test_future_plan_traces_the_change_and_stays_with_it(box):
    code, err = call(box.port, "POST", "/api/run", {"title": TITLE, "check": "future", "plan": ""})
    assert code == 400 and "box" in err["error"]
    v = box.run("future", plan="What if Elias dies at Grey Cross?")
    mine = found(v, "future")
    assert mine and all(f["plan"] == "What if Elias dies at Grey Cross?" for f in mine)
    assert any(f["where"][0]["section"] == 4 for f in mine)
    assert any("Elias" in u and "What if" in u for _s, u in box.core.asks)
    v2 = box.run("future", plan="What if the mule talks?")
    assert any(f.get("plan") == "What if Elias dies at Grey Cross?" for f in v2["findings"]), "one plan doesn't wipe another"
    assert not any(f.get("plan") for f in v2["plan"]), "the revision plan is the book's open findings, not what-ifs"


def test_documents_never_touch_the_book(box):
    folder = box.shelf / TITLE
    shas = {p.name: p.read_bytes() for p in (folder / "sections").iterdir()}
    v = box.run("style-sheet")
    doc = next(d for d in v["documents"] if d["kind"] == "style-sheet")
    _, got = call(box.port, "GET", "/api/document?" + q(title=TITLE, id=doc["id"]))
    assert "Names, as you use them" in got["text"] and "harbor" in got["text"] and "Mara Quill" in got["text"]
    assert found(v, "style-sheet"), "and what is inconsistent, as findings"
    code, saved = call(box.port, "POST", "/api/document", {"title": TITLE, "id": doc["id"], "text": "hers now"})
    assert code == 200 and saved["message"] == "Saved."
    v = box.run("style-sheet")
    assert len([d for d in v["documents"] if d["kind"] == "style-sheet"]) == 2, "a new run never replaces what she rewrote"
    _, again = call(box.port, "GET", "/api/document?" + q(title=TITLE, id=doc["id"]))
    assert again["text"] == "hers now"
    v = box.run("synopsis-query")
    kinds = {d["kind"] for d in v["documents"]}
    assert {"synopsis", "query-letter"} <= kinds
    v = box.run("length-fit", plan="fantasy")
    _, note = call(box.port, "GET", "/api/document?" + q(title=TITLE, id=next(d for d in v["documents"] if d["kind"] == "length")["id"]))
    assert "Rough ranges only" in note["text"] and "fantasy" in note["text"]
    assert any("under the rough range" in f["what"] for f in found(v, "length-fit"))
    assert {p.name: p.read_bytes() for p in (folder / "sections").iterdir()} == shas


def test_word_file_is_standard_format_and_new_each_time(box):
    v = box.run("submission-format", plan="A. Writer\n1 Road, Town\na@example.org")
    doc = next(d for d in v["documents"] if d["kind"] == "submission")
    path = box.root / "My files" / "Editor's Desk" / TITLE / doc["file"]
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
        head = z.read("word/header1.xml").decode("utf-8")
    assert "Courier New" in xml and 'w:line="480"' in xml and "A. Writer" in xml and "Chapter Eight" in xml and "pageBreakBefore" in xml
    assert 'w:left="1440"' in xml and "Writer / The Salt Road" in head and "PAGE" in head and "About " in xml
    pytest.importorskip("docx")
    import docx
    d = docx.Document(str(path))
    assert any("Mara Quill came down" in p.text for p in d.paragraphs)
    v = box.run("submission-format", plan="A. Writer")
    assert len([x for x in v["documents"] if x["kind"] == "submission"]) == 2


def test_no_bookkeeping_shows_and_no_temp_files(tmp_path):
    box2 = Box(tmp_path)
    try:
        folder = make_book.write_book(box2.shelf)
        before = {p.name for p in folder.iterdir()}
        box2.run("grammar")
        box2.run("style-sheet")
        _, sec = call(box2.port, "GET", "/api/section?" + q(title=TITLE, n=1))
        call(box2.port, "POST", "/api/save", {"title": TITLE, "n": 1, "text": sec["text"] + " More.", "sha": sec["sha"]})
        assert {p.name for p in folder.iterdir()} - before == {"editors-desk.json"}, "one small file per book, no more"
        assert (folder / "editors-desk.json").stat().st_size < 1000
        assert [p.name.split(" 20")[0] for p in (box2.root / "My files" / "Editor's Desk" / TITLE).iterdir()] == ["Style sheet"], "what she asked for, where she can see it"
        answers = list((box2.data / "books").glob("*/answers/*"))
        assert sorted(p.name for p in answers) == ["grammar.json"], "one file for a check's saved steps"
        everywhere = list(box2.data.rglob("*")) + list(folder.rglob("*")) + list((box2.root / "My files").rglob("*"))
        assert not [p for p in everywhere if p.name.startswith(".tmp") or p.suffix == ".tmp"], "no check leaves temporary files"
        # asks no longer used go at the end of a run: the saved steps don't grow. Section 1 changed, so its asks are new.
        was = len(json.loads(answers[0].read_text(encoding="utf-8")))
        box2.run("grammar")
        assert len(json.loads(answers[0].read_text(encoding="utf-8"))) <= was
        box2.run("style-sheet")
        assert len(list((box2.root / "My files" / "Editor's Desk" / TITLE).iterdir())) == 1, "the same sheet again makes no second copy"
    finally:
        box2.close()


def test_old_versions_are_thinned_by_the_rule(tmp_path):
    from datetime import datetime, timedelta
    import book as bk
    base = tmp_path / "v"
    base.mkdir()
    now_ = datetime(2026, 11, 2, 12)
    names = []
    for i in range(400):                               # a save every 6 hours for 100 days
        d = now_ - timedelta(hours=6 * i)
        names.append(d.strftime("%Y%m%d-%H%M%S-000"))
        (base / f"{names[-1]}.md").write_text("# x\n\nt\n", encoding="utf-8")
    bk.thin(base, now_)
    left = sorted(p.stem for p in base.glob("*.md"))
    assert names[-1] in left, "the very first is always kept"
    assert all(n in left for n in names[:20]), "every one of the last 20"
    assert len(left) <= bk.KEEP_MOST
    old = [n for n in left if n[:8] < (now_ - timedelta(days=40)).strftime("%Y%m%d") and n != names[-1]]
    assert len({n[:6] for n in old}) == len(old), "one a month, past 30 days"
    assert len(left) < 60


def test_an_ask_that_runs_out_of_time_is_skipped_not_repeated(tmp_path):
    box2 = Box(tmp_path)
    try:
        make_book.write_book(box2.shelf)
        box2.run("plot")
        had = {f["id"] for f in found(box2.view(), "plot") if f["found_by"] == "brain"}
        assert had
        box2.core.too_long = "The silver key"          # the unit that found them, and a later run where it can't answer
        box2.core.limit = 0
        (box2.shelf / TITLE / "bible.json").write_text(json.dumps({**make_book.BIBLE, "threads": [{**t, "kind": "main plot, read again"} for t in make_book.BIBLE["threads"]]}), encoding="utf-8")
        v = box2.run("plot")
        assert v["status"]["state"] == "done" and box2.core.timed_out >= 2 and "ran out of time" in v["status"]["step"]
        assert "Reading the thread" in v["status"]["step"], "it says which step"
        row = next(c for c in v["stages"][0]["checks"] if c["id"] == "plot")
        assert row["skipped"] and "Reading the thread" in row["skipped"][0]
        assert any("took too long" in p for p in v["status"]["problems"])
        assert had <= {f["id"] for f in found(v, "plot")}, "a run cut short takes no finding away"
    finally:
        box2.close()


def test_a_long_ask_that_runs_out_of_time_goes_again_smaller(tmp_path):
    box2 = Box(tmp_path)
    try:
        make_book.write_book(box2.shelf)
        box2.run("plot")
        long = max(len(u) for _s, u in box2.core.asks if "Thread: The silver key" in u)
        shutil.rmtree(next((box2.data / "books").iterdir()) / "answers")
        box2.core.too_long, box2.core.limit = "Thread: The silver key", long - 1
        v = box2.run("plot")
        assert box2.core.timed_out == 1 and "ran out of time" not in v["status"]["step"], "half the passages, and it answered"
    finally:
        box2.close()


# -- the first real run's book: a frame story, front matter, vault handles. Nothing big there. --------------------------

@pytest.fixture(scope="module")
def frame(tmp_path_factory):
    return Book(frame_book.write_frame_book(tmp_path_factory.mktemp("frame")))


def test_front_matter_is_not_the_story(frame):
    assert "October 18, 2024" in frame.sections[0].text and "October 18, 2024" not in frame.sections[0].story
    assert "the late Edmund Pryce" in frame.sections[0].story, "after the START line it is the story"
    assert "Updated editions" not in frame.sections[-1].story and "Edmund Pryce died" in frame.sections[-1].story
    assert all(d["date"].year != 2024 and d["date"].year != 2010 for d in finders._dates(frame))


def test_evidence_must_be_her_words(plain_book):
    run = engine.Run(plain_book.folder, engine.load_checks()["plot"], {}, None, None, lambda: 5000, None, lambda **k: None, lambda: False, None)
    loose = ("'Captain Harrow drowned within sight of the shore, and they found him on the rocks at dawn. They buried him...'\n\nvs.\n\n"
             "'Harrow said, as they set out across the white.'")
    got = run.evident(loose)
    assert got.startswith("Captain Harrow drowned within sight of the shore") and "Harrow said, as they set out across the white" in got
    two = [{"section": 3, "quote": "Captain Harrow drowned within sight of the shore"}, {"section": 6, "quote": "Harrow said, as they set out across the white"}]
    assert engine.covered(got, two) == 2 and engine.covered("Captain Harrow drowned within sight of the shore, and", two) == 1
    assert run.evident("The dates run backward: Monday, March 3, 1891 in section 2, then February 20, 1891 in section 5.") == "", "a restatement is no evidence"
    assert run.evident("She buried the silver key beneath the old oak at midnight.") == ""
    assert run.evident("{{vault:h83ecd42684294a39017a}} Captain Harrow drowned within sight of the shore") == ""


def test_a_mention_is_not_a_return(frame):
    assert finders.run("dead_speaking", frame) == []
    hale = next(p for p in finders.people(frame) if p["name"].endswith("Hale"))
    import book as bk
    for line in ["I had largely given over my inquiries into what Professor Hale called the cult.",
                 "Hale's notes lay open on the desk beside me.",
                 "Hale had said that the stones were older than the sea.",
                 "In the diary Hale says, \"The sea is rising.\"",
                 "In his last letter Hale wrote, \"Burn every page of this.\"",
                 "I remembered how Hale smiled at the sea.",
                 "\"Hale knew all of it,\" said Mrs. Corr.",
                 "Hale walked into my room in a dream, and I woke shaking.",
                 "Mrs. Corr said that Hale told her everything."]:
        assert finders.returns(hale, bk.Section(9, "x", "x", "", line, "s")) is None, line
    for line in ["\"Hold fast,\" Hale said, and stepped onto the deck.", "The door opened. Hale walked in and sat down."]:
        assert finders.returns(hale, bk.Section(9, "x", "x", "", line, "s")) is not None, line


def test_dated_chapters_are_in_the_order_she_chose(frame):
    assert finders.run("dates_backward", frame) == [], "the reading marked the inspector's tale as told earlier"
    unmarked = {**frame.bible, "events": [{**e, "placement": ""} for e in frame.bible["events"]]}
    frame.bible = unmarked
    got = finders.run("dates_backward", frame)
    assert [c["where"][1]["section"] for c in got] == [4], "only the undated jump back goes to be weighed, never the dated chapters"
    assert all(c["size"] != "big" and c["judge"] for c in got)


def test_the_frame_story_gives_no_false_finding(tmp_path):
    box2 = Box(tmp_path)
    try:
        frame_book.write_frame_book(box2.shelf)
        call(box2.port, "POST", "/api/open", {"title": frame_book.TITLE})
        v = box2.run("plot", title=frame_book.TITLE)
        real = [f for f in v["findings"] if f["state"] == "open" and f.get("kind") != "question"]
        assert real == [], [f["what"] for f in real]
        assert v["status"]["step"].startswith("Nothing big found on plot and plot holes. Here is what I checked:")
        asks = [f for f in v["findings"] if f.get("kind") == "question"]
        pryce = next(f for f in asks if "Pryce" in f["what"])
        assert pryce["what"].startswith("Check: "), "the model's own smaller guess is a question, folded away"
        assert not any("{{vault:" in json.dumps(f) for f in v["findings"])
        assert not any("{{vault:" in s + u for s, u in box2.core.asks), "no handle goes to the brain"
        assert any("[something kept private]" in u for _s, u in box2.core.asks)
        assert v["private"] is True
        old = {f["id"]: f for f in v["findings"] if f.get("found_by") == "old desk"}
        assert set(old) == {"d-hers000004"} and old["d-hers000004"]["state"] == "later"
        assert not v["letter"]["problems"], "nothing big, so the letter names no problem"
        assert v["open_now"] == 0
    finally:
        box2.close()


def test_the_planted_book_still_gives_its_findings(tmp_path):
    box2 = Box(tmp_path)
    try:
        make_book.write_book(box2.shelf)
        box2.run("plot")
        v = box2.run("continuity")
        real = " | ".join(f["what"] for f in v["findings"] if f.get("kind") != "question" and f["state"] == "open")
        for planted in ("The silver key", "Harrow dies in section 3", "dates run backward", "eyes change"):
            assert planted in real, planted
        print("PLANTED:", real)
    finally:
        box2.close()


def test_every_check_runs_on_the_stand_in(tmp_path):
    box2 = Box(tmp_path)
    try:
        make_book.write_book(box2.shelf)
        for cid, c in engine.load_checks().items():
            v = box2.run(cid, plan=c["box"] and "fantasy", seconds=90)
            assert v["status"]["state"] == "done", (cid, v["status"])
    finally:
        box2.close()


def test_context_size_sets_the_room(tmp_path):
    box2 = Box(tmp_path)
    try:
        make_book.write_book(box2.shelf)
        box2.run("characters")
        small = max(len(u) for _s, u in box2.core.asks)
        box2.data.mkdir(exist_ok=True)
        (box2.data / "settings.json").write_text(json.dumps({"context_tokens": 1024}), encoding="utf-8")
        box2.run("characters")
        assert box2.view()["context"] == 1024
        tiny = max(len(u) for _s, u in box2.core.asks[-4:])
        assert tiny < small
    finally:
        box2.close()


def test_page_files(box):
    code, page = call(box.port, "GET", "/")
    assert code == 200 and b"Editor's Desk" in page
    for name in ("app.js", "app.css"):
        code, body = call(box.port, "GET", "/" + name)
        assert code == 200 and body
    words = "".join((PKG / "web" / n).read_text(encoding="utf-8") for n in ("index.html", "app.js", "write.js", "write.css", "app.css"))
    for bad in ("—", "LLM", "warts", "bible.json", "API", "the model", " AI "):
        assert bad not in words, bad
    code, body = call(box.port, "GET", "/write.js")
    assert code == 200 and body
    assert "style=" not in (PKG / "web" / "index.html").read_text(encoding="utf-8"), "no inline styles: the page's policy allows none"


# -- the Writing Room ------------------------------------------------------------------------------------------------------

def _mark_up(folder: Path) -> None:
    """Italic and bold in two sections, as she would have them, and a scene break."""
    for name, old, new in (("01 - Chapter One.md", "with the silver key in her coat", "with the *silver key* in her coat"),
                           ("01 - Chapter One.md", "the night he died", "the night he **died**"),
                           ("02 - Chapter Two.md", "The silver key stayed in her coat", "The silver key ***stayed*** in her coat\n\n* * *")):
        p = folder / "sections" / name
        p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8", newline="\n")


def test_the_whole_book_in_one_call_and_reading_changes_nothing(tmp_path):
    box2 = Box(tmp_path)
    try:
        folder = make_book.write_book(box2.shelf)
        _mark_up(folder)
        before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
        code, got = call(box2.port, "GET", "/api/book?" + q(title=TITLE))
        assert code == 200 and [s["n"] for s in got["sections"]] == list(range(1, 9))
        one = got["sections"][0]
        assert set(one) >= {"n", "heading", "text", "sha", "unread", "versions"} and one["heading"] == "Chapter One"
        assert one["text"].startswith("Mara Quill came down") and "with the *silver key* in her coat" in one["text"], "marks reach the page as they are"
        assert one["versions"] == 0 and one["unread"] is True, "marked up after reading, so changed since"
        assert "\n\n* * *" in got["sections"][1]["text"]
        raw = (folder / "sections" / "01 - Chapter One.md").read_bytes()
        assert one["sha"] == hashlib.sha256(raw).hexdigest()
        assert before == {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}, "opening the book writes nothing"
        assert not list(box2.data.rglob("versions/*"))
    finally:
        box2.close()


def test_marks_never_reach_the_engine(tmp_path):
    folder = make_book.write_book(tmp_path)
    _mark_up(folder)
    b = Book(folder)
    s1, s2 = b.section(1), b.section(2)
    assert "*silver key*" in s1.raw and "silver key" in s1.text and "*silver" not in s1.text and "**died**" not in s1.text
    assert "***stayed***" in s2.raw and "The silver key stayed in her coat" in s2.text and "* * *" in s2.text, "a scene break stays"
    assert s1.story == s1.text or "*" not in s1.story.replace("* * *", "")
    for name in finders.FINDERS:                       # every finder's quote still locates, in the words and in the raw text
        for c in finders.run(name, b):
            for w in c.get("where") or []:
                sec = b.section(w["section"])
                assert locate(sec.text, w["quote"]) and locate(sec.raw, w["quote"]), (name, w)
    a, z = locate(s1.raw, "with the silver key in her coat")
    assert s1.raw[a:z] == "with the *silver key* in her coat", "the place is right in the raw text"
    a, z = locate(s2.raw, "The Silver Key ‘stayed’ in her coat".replace("‘", "").replace("’", ""))
    assert s2.raw[a:z] == "The silver key ***stayed*** in her coat"


def test_autosave_keeps_a_version_at_most_every_ten_minutes(tmp_path):
    box2 = Box(tmp_path)
    try:
        folder = make_book.write_book(box2.shelf)
        file = folder / "sections" / "03 - Chapter Three.md"
        first = file.read_text(encoding="utf-8")
        _, sec = call(box2.port, "GET", "/api/section?" + q(title=TITLE, n=3))
        sha, text = sec["sha"], sec["text"]
        for i in range(4):                             # she types; it saves as she goes
            text += f" Word{i}."
            code, got = call(box2.port, "POST", "/api/save", {"title": TITLE, "n": 3, "text": text, "sha": sha, "auto": True})
            assert code == 200 and got["changed"], got
            sha = got["sha"]
            assert len(got["versions"]) == 1, "one version for a run of autosaves"
        assert got["version"] is None and file.read_text(encoding="utf-8").rstrip("\n").endswith("Word3.")
        _, v1 = call(box2.port, "GET", "/api/version?" + q(title=TITLE, n=3, id=got["versions"][0]["id"]))
        assert "# " not in v1["text"][:2] and v1["text"] == first.split("\n\n", 1)[1].rstrip("\n"), "the very first, before she changed it, is kept"
        # Ctrl+S, or any save that is not auto, keeps one as today
        text += " Saved by hand."
        code, got = call(box2.port, "POST", "/api/save", {"title": TITLE, "n": 3, "text": text, "sha": sha})
        assert code == 200 and len(got["versions"]) == 2
        sha = got["sha"]
        # ten minutes on, an autosave keeps one again
        vdir = next((box2.data / "books").iterdir()) / "versions" / "3"
        from datetime import datetime, timedelta
        for p in vdir.glob("*.md"):
            old = (datetime.strptime(p.stem[:15], "%Y%m%d-%H%M%S") - timedelta(minutes=11)).strftime("%Y%m%d-%H%M%S") + p.stem[15:]
            p.rename(vdir / f"{old}.md")
        text += " Later."
        code, got = call(box2.port, "POST", "/api/save", {"title": TITLE, "n": 3, "text": text, "sha": sha, "auto": True})
        assert code == 200 and len(got["versions"]) == 3 and got["version"]
    finally:
        box2.close()


def test_a_save_over_changed_text_keeps_both(tmp_path):
    box2 = Box(tmp_path)
    try:
        folder = make_book.write_book(box2.shelf)
        _, sec = call(box2.port, "GET", "/api/section?" + q(title=TITLE, n=4))
        file = folder / "sections" / "04 - Chapter Four.md"
        file.write_text(file.read_text(encoding="utf-8") + "\nChanged in another window.\n", encoding="utf-8")
        mine = sec["text"] + "\n\nMy new line."
        code, err = call(box2.port, "POST", "/api/save", {"title": TITLE, "n": 4, "text": mine, "sha": sec["sha"], "auto": True})
        assert code == 400 and err["stale"] is True and "Changed in another window." in err["text"] and err["sha"] != sec["sha"]
        assert "My new line." not in file.read_text(encoding="utf-8")
        code, got = call(box2.port, "POST", "/api/save", {"title": TITLE, "n": 4, "text": mine, "sha": err["sha"]})   # Keep mine
        assert code == 200 and "My new line." in file.read_text(encoding="utf-8")
        _, other = call(box2.port, "GET", "/api/version?" + q(title=TITLE, n=4, id=got["versions"][0]["id"]))
        assert "Changed in another window." in other["text"], "the other text goes to the versions"
    finally:
        box2.close()


def test_ask_your_editor(tmp_path):
    box2 = Box(tmp_path)
    try:
        folder = make_book.write_book(box2.shelf)
        _mark_up(folder)
        before = {p.name: p.read_bytes() for p in (folder / "sections").iterdir()}
        code, got = call(box2.port, "POST", "/api/ask", {"title": TITLE, "n": 2, "passage": "The silver key ***stayed*** in her coat", "question": "Does this drag?"})
        assert code == 200, got
        a = got["ask"]
        assert a["state"] == "done" and "stayed in her coat" in a["answer"] and a["quote"] == "The silver key stayed in her coat"
        system, user = box2.core.asks[-1]
        assert "You are her editor" in system and "Never write replacement sentences" in system and "never write her story" in system
        assert "Does this drag?" in user and "The silver key stayed in her coat" in user and "*" not in user.replace("* * *", "")
        assert "Elias Wren" in user or "He laughed" in user, "the paragraphs around it go too"
        saved = json.loads(next(box2.data.rglob("asks.json")).read_text(encoding="utf-8"))
        assert saved[-1]["id"] == a["id"] and saved[-1]["answer"] == a["answer"] and saved[-1]["n"] == 2
        _, listed = call(box2.port, "GET", "/api/asks?" + q(title=TITLE))
        assert [x["id"] for x in listed["asks"]] == [a["id"]]
        code, _ = call(box2.port, "POST", "/api/asks", {"title": TITLE, "id": a["id"]})
        assert code == 200 and call(box2.port, "GET", "/api/asks?" + q(title=TITLE))[1]["asks"] == []
        code, err = call(box2.port, "POST", "/api/ask", {"title": TITLE, "n": 2, "passage": "  ", "question": "x"})
        assert code == 400
        assert {p.name: p.read_bytes() for p in (folder / "sections").iterdir()} == before, "an ask never writes her text"
    finally:
        box2.close()


def test_asks_keep_the_last_hundred(tmp_path):
    import server
    folder = make_book.write_book(tmp_path / "shelf")
    desk = server.Desk.__new__(server.Desk)
    desk.asks_lock = threading.Lock()
    desk._asks_file = lambda f: tmp_path / "asks.json"
    for i in range(105):
        desk._ask_put(folder, {"id": f"a{i}", "n": 1, "quote": "x"})
    got = json.loads((tmp_path / "asks.json").read_text(encoding="utf-8"))
    assert len(got) == 100 and got[0]["id"] == "a5" and got[-1]["id"] == "a104"


def _lum(h: str) -> float:
    r = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    r = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in r]
    return 0.2126 * r[0] + 0.7152 * r[1] + 0.0722 * r[2]


def test_contrast_on_both_looks():
    css = (PKG / "web" / "app.css").read_text(encoding="utf-8")
    light = dict(re.findall(r"--d-([a-z-]+):\s*(#[0-9a-fA-F]{6})", css.split("@media (prefers-color-scheme: dark)")[0]))
    dark = {**light, **dict(re.findall(r"--d-([a-z-]+):\s*(#[0-9a-fA-F]{6})", css.split("@media (prefers-color-scheme: dark)")[1].split("}")[0]))}
    for look in (light, dark):
        for fg, bg in (("ink", "bg"), ("ink", "card"), ("muted", "bg"), ("muted", "card"), ("accent", "bg"), ("accent", "card"), ("on", "accent"), ("big", "card"), ("error", "bg"), ("ok", "card"), ("mark-ink", "mark"), ("ink", "chosen"),
                       ("ink", "note"), ("ink", "note-big"), ("ink", "note-q"), ("ink", "asked"), ("muted", "note-q")):
            a, b = _lum(look[fg]), _lum(look[bg])
            ratio = (max(a, b) + 0.05) / (min(a, b) + 0.05)
            assert ratio >= 4.5, (fg, bg, ratio)



# -- Study a book ------------------------------------------------------------------------------------------------------

THEIRS = make_book.TITLE                 # the book she admires
MINE = study_book.TITLE                  # her manuscript, in these tests


def study_box(root: Path) -> Box:
    b = Box(root)
    make_book.write_book(b.shelf)
    idx_path = b.shelf / THEIRS / "manuscript.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    idx["study"] = True                   # added with "Add a book to study"
    idx_path.write_text(json.dumps(idx), encoding="utf-8")
    study_book.write_study_book(b.shelf)
    return b


def study_asks(b: Box) -> list:
    return [u for s_, u in b.core.asks if "learn from a book she admires" in s_]


def study_done(b: Box, seconds=60) -> dict:
    end = time.time() + seconds
    while time.time() < end:
        st = b.view(MINE)["status"] or {}
        if st.get("check") == "study" and st.get("state") in ("done", "failed", "stopped"):
            return st
        time.sleep(0.1)
    raise AssertionError(f"the comparison never finished: {st}")


@pytest.fixture(scope="module")
def sbox(tmp_path_factory):
    b = study_box(tmp_path_factory.mktemp("study"))
    yield b
    b.close()


def test_study_books_and_numbers_need_no_brain(sbox):
    code, got = call(sbox.port, "GET", "/api/study/books?" + q(title=MINE))
    assert code == 200 and got["books"] == [{"title": THEIRS, "study": True, "words": got["books"][0]["words"]}]
    listed = {b["title"]: b for b in call(sbox.port, "GET", "/api/list")[1]["books"]}
    assert listed[THEIRS]["study"] is True and listed[MINE]["study"] is False
    before = len(sbox.core.asks)
    code, n = call(sbox.port, "GET", "/api/study/numbers?" + q(title=MINE, theirs=THEIRS))
    assert code == 200, n
    assert n["numbers"]["mine"]["words"] > 200 and n["numbers"]["theirs"]["words"] > 200
    assert n["apart"] and all(x["line"] for x in n["apart"])
    assert n["pairs_count"] >= 3 and n["steps"] == n["pairs_count"] + 1
    assert "time_left" in n and "alike" in n
    assert len(sbox.core.asks) == before, "the numbers never ask the brain"
    homes = list((sbox.data / "books").glob("*/study/measure.json"))
    assert len(homes) == 2                # each book's measure, kept by its sections' shas
    again = call(sbox.port, "GET", "/api/study/numbers?" + q(title=MINE, theirs=THEIRS))[1]
    assert again == n
    assert call(sbox.port, "GET", "/api/study?" + q(title=MINE, theirs=THEIRS))[1]["report"] is None
    assert call(sbox.port, "GET", "/api/study?" + q(title=MINE))[1] == {"reports": []}
    code, got = call(sbox.port, "GET", "/api/study/numbers?" + q(title=MINE, theirs=MINE))
    assert code == 400 and "different book" in got["error"]
    assert "\u2014" not in json.dumps(n["apart"] + n["alike"], ensure_ascii=False)


def test_study_tools_start_nothing_without_her_yes(sbox):
    text, err = tool(sbox.port, "desk_study", title=MINE)
    assert not err and THEIRS in text and "a book to study" in text
    before = len(sbox.core.asks)
    text, err = tool(sbox.port, "desk_study", title=MINE, theirs=THEIRS)
    assert not err and text.startswith("This has not started. Ask the writer:") and "confirm true" in text
    text, err = tool(sbox.port, "desk_study", title=MINE, theirs=THEIRS, confirm="yes")
    assert not err and text.startswith("This has not started.")
    assert not json.loads((sbox.data / "jobs.json").read_text(encoding="utf-8")) if (sbox.data / "jobs.json").exists() else True
    assert (sbox.view(MINE)["status"] or {}).get("check") != "study"
    text, err = tool(sbox.port, "desk_study_report", title=MINE, theirs=THEIRS)
    assert not err and "Where the two books differ most:" in text and "has not compared passages" in text
    time.sleep(0.5)
    assert len(sbox.core.asks) == before
    names = {t["name"]: t for t in call(sbox.port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})[1]["result"]["tools"]}
    assert names["desk_study"]["annotations"]["readOnlyHint"] is False and names["desk_study_report"]["annotations"]["readOnlyHint"] is True


def test_study_runs_in_the_line_and_a_second_run_asks_nothing_again(sbox):
    before = len(study_asks(sbox))
    code, got = call(sbox.port, "POST", "/api/study", {"title": MINE, "theirs": THEIRS, "focus": "how she moves between scenes"})
    assert code == 200 and got["job"]["check"] == "study", got
    code, again = call(sbox.port, "POST", "/api/study", {"title": MINE, "theirs": THEIRS})
    assert code == 400 and "already comparing" in again["error"]
    st = study_done(sbox)
    assert st["state"] == "done" and "compared" in st["step"], st
    asked = study_asks(sbox)[before:]
    n = call(sbox.port, "GET", "/api/study/numbers?" + q(title=MINE, theirs=THEIRS))[1]
    assert len(asked) == n["steps"]
    r = call(sbox.port, "GET", "/api/study?" + q(title=MINE, theirs=THEIRS))[1]["report"]
    assert r["state"] == "done" and r["theirs"] == THEIRS and r["focus"] == "how she moves between scenes"
    done = [p for p in r["pairs"] if p["state"] == "done"]
    assert done and all(p["pointers"] and p["pointers"][0]["my_quote"] in p["mine"]["text"] for p in done)
    assert r["pointers"] and r["pointers"][0]["try"]
    assert call(sbox.port, "GET", "/api/study?" + q(title=MINE))[1]["reports"][0]["theirs"] == THEIRS
    text, err = tool(sbox.port, "desk_study_report", title=MINE)
    assert not err and "What to try first:" in text and "1. Let a chapter close on a line of talk." in text
    # the same pair and focus again: every answer is saved, so your editor is not asked again
    count = len(study_asks(sbox))
    text, err = tool(sbox.port, "desk_study", title=MINE, theirs=THEIRS, confirm=True, focus="how she moves between scenes")
    assert not err and "is comparing passages" in text
    assert study_done(sbox)["state"] == "done"
    assert len(study_asks(sbox)) == count, "a run over saved answers makes no second call"
    files = sorted(x.name for x in (next((sbox.data / "books").glob("Night Ferry*")) / "study").iterdir())
    assert len(files) == 4, files        # measure, one report, its saved answers, its step count: nothing grows


def test_study_stops_and_goes_on_after_a_restart(tmp_path):
    b = study_box(tmp_path)
    try:
        b.core.slow = 0.4
        code, got = call(b.port, "POST", "/api/study", {"title": MINE, "theirs": THEIRS})
        assert code == 200, got
        end = time.time() + 30
        while len(study_asks(b)) < 1 and time.time() < end:
            time.sleep(0.05)
        assert call(b.port, "POST", "/api/stop", {"title": MINE})[0] == 200
        st = study_done(b)
        assert st["state"] == "stopped", st
        assert call(b.port, "GET", "/api/study?" + q(title=MINE, theirs=THEIRS))[1]["report"] is None
        stopped_at = len(study_asks(b))
        assert stopped_at < 4
        # pressed again, then the app dies in the middle; started again, it goes on by itself
        call(b.port, "POST", "/api/study", {"title": MINE, "theirs": THEIRS})
        end = time.time() + 30
        while len(study_asks(b)) < stopped_at + 1 and time.time() < end:
            time.sleep(0.05)
        b.stop()
        answered = set(study_asks(b))
        count = len(study_asks(b))
        b.core.slow = 0
        b.start()
        assert study_done(b)["state"] == "done"
        again = study_asks(b)[count:]
        assert sum(1 for u in again if u in answered) <= 1, "at most the call that was cut off"
        n = call(b.port, "GET", "/api/study/numbers?" + q(title=MINE, theirs=THEIRS))[1]
        assert len(set(study_asks(b))) == n["steps"], "each ask paid for once, but the one cut off"
        assert call(b.port, "GET", "/api/study?" + q(title=MINE, theirs=THEIRS))[1]["report"]["state"] == "done"
    finally:
        b.close()


def test_word_copy_is_a_reading_copy_in_her_files(sbox):
    folder = sbox.shelf / THEIRS
    before = {p.name: p.read_bytes() for p in (folder / "sections").iterdir()}
    code, got = call(sbox.port, "POST", "/api/wordcopy", {"title": THEIRS})
    assert code == 200, got
    assert got["message"] == "Saved in your files at: " + got["where"]
    assert got["where"].startswith(f"My files/Editor's Desk/{THEIRS}/") and got["where"].endswith(".docx")
    path = sbox.root / got["where"]
    with zipfile.ZipFile(path) as z:
        doc = z.read("word/document.xml").decode("utf-8")
    assert "Times New Roman" in doc and "Courier" not in doc and "Mara Quill came down to the harbor" in doc
    assert {p.name: p.read_bytes() for p in (folder / "sections").iterdir()} == before
    code, d = call(sbox.port, "GET", "/api/desk?" + q(title=THEIRS))
    assert any(x["kind"] == "reading-copy" for x in d["documents"])
    code, got = call(sbox.port, "POST", "/api/wordcopy", {"title": "No Such Book"})
    assert code == 400
