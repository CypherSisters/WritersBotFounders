"""Trivia's own server (packages/trivia/server.py): the question bank, the scores
document, the page's saves, the MCP tools and every route's token.

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
from pathlib import Path

import pytest



PKG = Path(__file__).resolve().parents[2] / "packages" / "trivia"
TOKEN = "test-token-" + "t" * 20

_spec = importlib.util.spec_from_file_location("trivia_server", PKG / "server.py")
tv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tv)

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


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data" / "trivia"


@pytest.fixture
def base(data):
    with running(tv.make_server(0, data, TOKEN)) as url:
        yield url


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


# ------------------------------------------------------------ the bank

def test_the_bank_is_whole_and_every_answer_is_one_of_its_options():
    bank = json.loads((PKG / "questions.json").read_text(encoding="utf-8"))
    assert bank["provenance"].startswith("Harvested from PuffPAIBuildBot@774e27a4:")
    assert (PKG / "questions.json").read_text(encoding="utf-8").startswith('{"provenance": "Harvested from')
    ids = [c["id"] for c in bank["categories"]]
    assert ids[0] == "random" and set(bank["questions"]) == set(ids[1:])
    count = 0
    for cat, by in bank["questions"].items():
        assert set(by) == {"beginner", "mid", "jeopardy"}, cat
        for diff, items in by.items():
            assert items, (cat, diff)
            for q in items:
                assert q["q"].strip() and len(q["options"]) == 4 and len(set(q["options"])) == 4
                assert 0 <= q["answer"] < 4
                count += 1
    assert count == 165
    assert set(bank["kids_categories"]) <= set(ids[1:]) and "action" not in bank["kids_categories"]


def test_page_and_bank_are_served(base):
    assert call(base + "/health") == (200, "ok")
    code, page = call(base + "/")
    assert code == 200 and "AeroTrivia" in page and "BASE + 'questions'" in page
    assert "/api/pai" not in page and "vendor/" not in page and "http://" not in page and "https://" not in page
    code, bank = call(base + "/questions")
    assert code == 200 and len(bank["categories"]) == 12


# ------------------------------------------------------------ tools

def test_no_games_yet(base):
    assert tool(base, "stats") == "No trivia games played yet."


def _bank_answers():
    bank = json.loads((PKG / "questions.json").read_text(encoding="utf-8"))
    return {q["q"]: q["options"][q["answer"]] for by in bank["questions"].values() for v in by.values() for q in v}


def _letters(out, answers=None):
    """(question, the right letter, a wrong letter) from an ask_question answer."""
    head, *options = out.splitlines()
    question = head.split("): ", 1)[1]
    right = (answers or _bank_answers())[question]
    i = [o[3:] for o in options].index(right)
    return question, "ABCD"[i], "ABCD"[(i + 1) % 4]


def test_ask_question_keeps_the_answer_back_and_check_answer_grades_it(base):
    """HomeBot TRIV-03: an answer handed over with the question reached people before they guessed."""
    answers = _bank_answers()
    out = tool(base, "ask_question", category="Science", difficulty="easy")
    head, *options = out.splitlines()
    assert head.startswith("Question 1 of this round. Science (beginner): ")
    assert [o[:3] for o in options] == ["A. ", "B. ", "C. ", "D. "] and "answer" not in out.lower()
    question, right, _ = _letters(out, answers)
    got = tool(base, "check_answer", answer=right.lower() + ".")
    assert got.startswith(f"Right! The answer is {right}. {answers[question]}.")
    assert got.endswith("Score: you: 1 of 1 right (100 points).")
    assert tool(base, "check_answer", answer="A") == "There is no question waiting for an answer. Ask one first."


def test_a_round_keeps_score_per_player_and_end_round_saves_it(base, data):
    answers = _bank_answers()
    _, right, _ = _letters(tool(base, "ask_question", category="history", difficulty="mid", player="Sam"), answers)
    assert "Sam: 1 of 1 right (200 points)" in tool(base, "check_answer", answer=right)
    out = tool(base, "ask_question", category="history", difficulty="mid", player="Ada")
    assert "Question 2 of this round, for Ada." in out
    _, _, wrong = _letters(out, answers)
    got = tool(base, "check_answer", answer=wrong)
    assert got.startswith(f"Not quite, it is not {wrong}.") and "Ada: 0 of 1 right (0 points)" in got
    question, _, _ = _letters(tool(base, "ask_question", category="history", difficulty="mid", player="Ada"), answers)
    assert "Right!" in tool(base, "check_answer", answer=answers[question])     # the words of the option
    assert "A round is going in chat: Sam: 1 of 1" in tool(base, "stats")
    out = tool(base, "end_round")
    assert out == ("Round over and saved. Sam: 1 of 1 right (200 points), Ada: 1 of 2 right (200 points). "
                   "A tie: Sam and Ada.")
    saved = json.loads((data / "trivia.json").read_text(encoding="utf-8"))["results"]
    assert saved[-1]["score"] == [{"name": "Sam", "score": 200}, {"name": "Ada", "score": 200}]
    assert saved[-1]["category"] == "history" and saved[-1]["difficulty"] == "mid" and saved[-1]["mode"] == "chat"
    assert tool(base, "end_round") == "No round to end: nobody has answered a question yet."


def test_a_guess_that_names_no_option_leaves_the_question_waiting(base):
    out = tool(base, "ask_question", category="geography")
    assert "answer is a letter A to D" in tool_error(base, "check_answer", answer="purple elephants")
    _, right, _ = _letters(out)
    assert "Right!" in tool(base, "check_answer", answer=right)
    assert "not saved" in tool(base, "end_round", save=False)
    assert call(base + "/data")[1]["results"] == []


def test_kids_mode_asks_only_kids_categories(base):
    bank = json.loads((PKG / "questions.json").read_text(encoding="utf-8"))
    kids = {q["q"] for c in bank["kids_categories"] for q in bank["questions"][c]["beginner"]}
    for _ in range(12):
        out = tool(base, "ask_question", difficulty="kids")
        assert "(kids): " in out and out.splitlines()[0].split("): ", 1)[1] in kids
    assert "no kids questions in Action Movies" in tool(base, "ask_question", category="action", difficulty="kids")
    assert "for kids mode" in tool(base, "list_categories")


def test_add_and_remove_your_own_question(base, data):
    out = tool(base, "add_question", question="What is the name of our dog?", right_answer="Biscuit",
               wrong_answers=["Rex", "Fido", "Max"], category="Our Family", difficulty="kids")
    assert out == ("Added to Our Family (kids): What is the name of our dog? The answer: Biscuit. "
                   "It starts a new category, Our Family.")
    assert "Our Family: 1 question, 1 of them yours, 1 for kids mode" in tool(base, "list_categories")
    bank = call(base + "/questions")[1]
    assert bank["categories"][-1]["name"] == "Our Family"
    assert bank["questions"]["our-family"]["kids"][0]["options"][0] == "Biscuit"
    assert "What is the name of our dog?" in tool(base, "ask_question", category="our family", difficulty="kids")
    assert "Right!" in tool(base, "check_answer", answer="biscuit")
    assert "already in the bank" in tool_error(base, "add_question", question="what is the name of our dog?",
                                               right_answer="a", wrong_answers=["b", "c", "d"], category="history")
    assert "4 different answers" in tool_error(base, "add_question", question="Which is it?", right_answer="a",
                                               wrong_answers=["A", "c", "d"], category="history")
    assert "3 wrong answers" in tool_error(base, "add_question", question="Which is it?", right_answer="a",
                                           wrong_answers=["b"], category="history")
    assert "Random mixes them all" in tool_error(base, "add_question", question="Which is it?", right_answer="a",
                                                 wrong_answers=["b", "c", "d"], category="random")
    assert "came with Trivia" in tool(base, "remove_question", question="What is the capital of France?")
    assert tool(base, "remove_question", question="what is the name of our DOG?") == \
        "Removed: what is the name of our DOG?"
    saved = json.loads((data / "trivia.json").read_text(encoding="utf-8"))
    assert saved["my_questions"] == [] and saved["my_categories"] == []
    assert len(call(base + "/questions")[1]["categories"]) == 12


def test_the_page_reads_the_banks_field_names():
    """2.0.0's page read q.opts and q.a, which questions.json does not have: the first question stopped the game."""
    page = (PKG / "web" / "index.html").read_text(encoding="utf-8")
    assert "q.options[q.answer]" in page and "kids_categories" in page and "selectDifficulty('kids'" in page
    assert "<span>${esc(opt)}</span>" in page and "${esc(c.name)}" in page


def test_fixed_questions_have_one_right_option():
    answers = _bank_answers()
    bank = json.loads((PKG / "questions.json").read_text(encoding="utf-8"))
    opts = {q["q"]: q["options"] for by in bank["questions"].values() for v in by.values() for q in v}
    turning = next(q for q in opts if "most Oscar nominations without a win" in q)
    assert "The Color Purple" not in opts[turning]
    assert answers["Which is the oldest of cycling's three Grand Tours?"] == "Tour de France"
    assert answers["What is the driest non-polar desert on Earth?"] == "Atacama"
    danakil = next(q for q in opts if "Danakil" in q)
    assert "Eritrea" not in opts[danakil] and "Djibouti" not in opts[danakil]


def test_ask_question_does_not_repeat_until_the_pool_is_used(base):
    seen = {tool(base, "ask_question", category="history", difficulty="mid").splitlines()[0].split("): ")[1]
            for _ in range(5)}
    assert len(seen) == 5                                  # history has 5 mid questions; none twice


def test_ask_question_random_and_unknown(base):
    assert tool(base, "ask_question").startswith("Question 1 of this round. ")
    assert "no 'knitting' category" in tool_error(base, "ask_question", category="knitting")
    assert "beginner, mid, jeopardy or kids" in tool_error(base, "ask_question", difficulty="impossible")
    assert "Food & Drink" in tool(base, "list_categories")


def test_record_result_then_stats(base, data):
    assert tool(base, "record_result", correct=7, total_questions=10, category="history") == \
        "Saved: 7 of 10 right in History (beginner), 700 points."
    assert "for Sam" in tool(base, "record_result", correct=3, total_questions=5, category="science",
                             difficulty="jeopardy", player="Sam")
    saved = json.loads((data / "trivia.json").read_text(encoding="utf-8"))
    assert [r["score"] for r in saved["results"]] == [700, 900] and saved["last_updated"]
    out = tool(base, "stats")
    assert out.startswith("Trivia stats: 2 games played. Average score: 800.0. Best: 900 by Sam in Science (jeopardy).")
    assert "Last game: 900 in Science (jeopardy)." in out
    assert "correct is a count" in tool_error(base, "record_result", correct=11, total_questions=10)


def test_page_result_and_group_games_count_by_their_best_player(base):
    """v1 trivia_get_stats summed the scores, and a group game's score is a list: it broke."""
    group = {"date": "2026-10-01T20:00:00", "mode": "group", "category": "music", "difficulty": "mid",
             "score": [{"name": "Ada", "score": 400}, {"name": "Bo", "score": 1200}], "bestStreak": 3, "totalQuestions": 10}
    assert call(base + "/api/result", "POST", group)[0] == 200
    assert call(base + "/api/result", "POST", {"mode": "casual", "category": "tech", "difficulty": "beginner", "score": 300})[0] == 200
    assert call(base + "/api/result", "POST", {"mode": "casual"})[0] == 400
    doc = call(base + "/data")[1]
    assert [r["source"] for r in doc["results"]] == ["page", "page"] and doc["results"][0]["date"] == group["date"]
    assert "Best: 1200 by Bo in Music (mid)." in tool(base, "stats")
    assert call(base + "/updated")[1]["last_updated"] == doc["last_updated"]


def test_a_v1_file_reads_as_is(data):
    data.mkdir(parents=True)
    v1 = {"results": [{"date": "2026-09-01T10:00:00", "mode": "brain", "category": "geography",
                       "difficulty": "beginner", "score": 650, "bestStreak": 4, "totalQuestions": 10}],
          "last_updated": "2026-09-01T10:00:00"}
    (data / "trivia.json").write_text(json.dumps(v1), encoding="utf-8")
    with running(tv.make_server(0, data, TOKEN)) as base:
        assert "1 game played" in tool(base, "stats") and "Geography" in tool(base, "stats")


def test_unreadable_file_is_never_written_over(data):
    data.mkdir(parents=True)
    (data / "trivia.json").write_text("{not json", encoding="utf-8")
    with running(tv.make_server(0, data, TOKEN)) as base:
        assert call(base + "/data")[0] == 500
        assert call(base + "/api/result", "POST", {"score": 100})[0] == 409
        assert "will not write over it" in tool_error(base, "record_result", correct=1, total_questions=1)
    assert (data / "trivia.json").read_text(encoding="utf-8") == "{not json"


# ------------------------------------------------------------ MCP and its token

@pytest.mark.parametrize("method,path,body", [
    ("GET", "/", None), ("GET", "/health", None), ("GET", "/data", None), ("GET", "/updated", None),
    ("GET", "/questions", None), ("POST", "/api/result", {"score": 1}), ("PUT", "/data", {"results": []}),
    ("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
])
def test_every_route_refuses_without_the_token(base, method, path, body):
    """D48d: not only /mcp. The Desktop reaches the page through /pkg/, which adds the token."""
    assert call(base + path, method, body, token=None)[0] == 401
    assert call(base + path, method, body, token="wrong")[0] == 401


def test_refused_save_changes_nothing(base):
    assert call(base + "/api/result", "POST", {"score": 100}, token=None)[0] == 401
    assert call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": "record_result", "arguments": {"correct": 1, "total_questions": 1}}},
                token=None)[0] == 401
    assert call(base + "/data")[1]["results"] == []


def test_started_without_a_token_it_refuses_everything(data):
    with running(tv.make_server(0, data, None)) as base:
        assert call(base + "/health", token=None)[0] == 401
        assert call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    token=None, headers={"Authorization": "Bearer "})[0] == 401


def test_mcp_basics(base):
    code, out = mcp(base, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "0"}})
    assert code == 200 and out["result"]["serverInfo"]["name"] == "trivia"
    assert call(base + "/mcp", "POST", {"jsonrpc": "2.0", "method": "notifications/initialized"},
                token=None, headers={"X-PuffPAI-Key": TOKEN})[0] == 202
    assert mcp(base, "tools/call", {"name": "nope"})[1]["error"]["code"] == -32602
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
    assert m["needs"]["hosts"] == [] and "widgets" not in m["ui"]


def test_tool_words_pass_drawbridges_screen(base):
    from puffpai_core.zone3.drawbridge import screen_tool_words
    assert screen_tool_words(mcp(base, "tools/list")[1]["result"]["tools"]) == {}


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
                res = await s.call_tool("record_result", {"correct": 2, "total_questions": 4})
                return [t.name for t in listed.tools], res.content[0].text

    names, text = asyncio.run(go())
    assert "ask_question" in names and text.startswith("Saved: 2 of 4 right")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_page_script_parses(tmp_path):
    page = (PKG / "web" / "index.html").read_text(encoding="utf-8")
    assert page.splitlines()[1].startswith("<!-- Harvested from PuffPAIBuildBot@774e27a4:dashboard/public/trivia-app.html")
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
    env = {**os.environ, "PUFFPAI_PORT": str(port), "PUFFPAI_DATA": str(tmp_path / "data"), "PUFFPAI_TOKEN": TOKEN}
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
            pytest.fail("Trivia did not answer its health check")
        base = f"http://127.0.0.1:{port}"
        assert mcp(base, "tools/list", token=None)[0] == 401
        assert "Saved" in tool(base, "record_result", correct=1, total_questions=2)
        assert (tmp_path / "data" / "trivia.json").is_file()
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


def results(base):
    return call(base + "/data")[1]["results"]


NINES = "9" * 400


@pytest.mark.parametrize("body", [
    '{"score": NaN}', '{"score": Infinity}', '{"score": -Infinity}', '{"score": 1e400}', '{"score": "nan"}',
    '{"score": "inf"}', '{"score": -5}', '{"score": 100001}', '{"score": 2.5}', '{"score": true}', '{"score": "lots"}',
    '{"score": ' + NINES + '}', '{"score": [{"name": "Ada", "score": "many"}]}',
    '{"score": [{"name": "Ada", "score": NaN}]}', '{"score": []}',
    '{"score": [' + ",".join(['{"name": "x", "score": 1}'] * 5) + ']}',
    '{"score": [{"name": "' + "x" * 41 + '", "score": 1}]}', '{"score": [7]}',
    '{"score": 10, "bestStreak": NaN}', '{"score": 10, "bestStreak": -1}', '{"score": 10, "totalQuestions": 1e400}',
    '{"score": 10, "category": "knitting"}', '{"score": 10, "difficulty": "impossible"}',
    '{"score": 10, "mode": "speedrun"}', '{"score": 10, "date": "yesterday"}', '{"score": 10, "category": ["a"]}',
    '{"score": 10, "date": 5}',
])
def test_a_bad_page_result_is_refused_and_nothing_is_saved(base, data, body):
    code, out = post_raw(base, "/api/result", body)
    assert code == 400 and out["error"], out
    assert results(base) == [] and not (data / "trivia.json").exists()


def test_a_good_page_result_is_kept_as_checked(base):
    body = {"date": "2026-10-25T20:00:00.000Z", "mode": "group", "category": "music", "difficulty": "mid",
            "score": [{"name": "  Ada ", "score": 1200}, {"name": "", "score": 0}], "bestStreak": 4,
            "totalQuestions": 10, "extra": "dropped"}
    assert call(base + "/api/result", "POST", body)[0] == 200
    r = results(base)[0]
    assert r["score"] == [{"name": "Ada", "score": 1200}, {"name": "Player 2", "score": 0}]
    assert "extra" not in r and r["source"] == "page" and r["date"] == body["date"]


@pytest.mark.parametrize("args", [
    {"correct": float("nan"), "total_questions": 10}, {"correct": 1, "total_questions": float("inf")},
    {"correct": "nan", "total_questions": 10}, {"correct": -1, "total_questions": 10},
    {"correct": 1, "total_questions": 1001}, {"correct": 1.5, "total_questions": 10},
    {"correct": True, "total_questions": 10}, {"correct": 1, "total_questions": 2, "player": "x" * 41},
    {"correct": 1, "total_questions": 2, "category": 7},
])
def test_record_result_checks_its_numbers(base, args):
    text = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "record_result", "arguments": args}})       # NaN goes as the JSON word
    code, out = post_raw(base, "/mcp", text)
    assert code in (200, 400), out
    if code == 200:
        assert out["result"]["isError"], out
    assert results(base) == []


def test_huge_numbers_over_mcp_are_a_plain_error(base):
    text = ('{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "record_result", '
            '"arguments": {"correct": 1e400, "total_questions": 10}}}')
    code, out = post_raw(base, "/mcp", text)
    assert code == 200 and out["result"]["isError"] and "correct" in out["result"]["content"][0]["text"]
    big = ('{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "record_result", '
           '"arguments": {"correct": ' + NINES + ', "total_questions": 10}}}')
    assert post_raw(base, "/mcp", big)[1]["result"]["isError"]
    assert post_raw(base, "/mcp", '{"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"x": NaN}}')[0] == 400
    assert results(base) == []


def test_bad_values_already_in_the_file_are_repaired_on_load(data):
    data.mkdir(parents=True)
    (data / "trivia.json").write_text(
        '{"results": [{"date": "2026-09-01T10:00:00", "category": "tech", "difficulty": "mid", "score": NaN},'
        ' {"date": "2026-09-02T10:00:00", "category": ["x"], "score": [{"name": "Bo", "score": "lots"}]},'
        ' {"date": "2026-09-03T10:00:00", "category": "food", "difficulty": "beginner", "score": Infinity},'
        ' "not a result", {"date": "2026-09-04T10:00:00", "category": "music", "score": 400}],'
        ' "last_updated": "2026-09-04T10:00:00"}', encoding="utf-8")
    with running(tv.make_server(0, data, TOKEN)) as base:
        code, doc = call(base + "/data")
        assert code == 200 and doc["results"][0]["score"] == 0 and doc["results"][2]["score"] == 0
        out = tool(base, "stats")
        assert out.startswith("Trivia stats: 4 games played. Average score: 100.0. Best: 400 in Music")
        assert "Saved" in tool(base, "record_result", correct=1, total_questions=1)
    saved = (data / "trivia.json").read_text(encoding="utf-8")
    assert "NaN" not in saved and "Infinity" not in saved


@pytest.mark.parametrize("length", [b"-1", b"abc", b"1e3", b"99999999999", b" 5x"])
def test_a_bad_content_length_never_hangs(base, length):
    for auth in (f"Authorization: Bearer {TOKEN}\r\n".encode(), b""):
        head = (b"POST /api/result HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n" + auth
                + b"Content-Length: " + length + b"\r\n\r\n{}")
        answer = raw_request(base, head)
        assert answer.startswith(b"HTTP/1.0 400" if auth else b"HTTP/1.0 401"), answer[:80]
    assert results(base) == []


def test_a_stalled_body_is_cut_off(data):
    server = tv.make_server(0, data, TOKEN)
    server.RequestHandlerClass.timeout = 1
    with running(server) as base:
        head = (b"POST /api/result HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer " + TOKEN.encode()
                + b"\r\nContent-Length: 100\r\n\r\n{\"score\":")
        start = time.monotonic()
        raw_request(base, head)
        assert time.monotonic() - start < 6
        assert results(base) == []


def _boom(exc):
    def fail(*_a, **_k):
        raise exc
    return fail


def test_an_unexpected_error_is_plain_json_and_mcp_is_error(data, monkeypatch):
    server = tv.make_server(0, data, TOKEN)
    app = server.RequestHandlerClass.app
    monkeypatch.setattr(app, "stats", _boom(RuntimeError("boom")))
    monkeypatch.setattr(app, "page_result", _boom(OverflowError("big")))
    with running(server) as base:
        code, out = mcp(base, "tools/call", {"name": "stats"})
        assert code == 200 and out["result"]["isError"] and "RuntimeError" in out["result"]["content"][0]["text"]
        code, out = call(base + "/api/result", "POST", {"score": 1})
        assert code == 500 and "OverflowError" in out["error"]
        assert call(base + "/health") == (200, "ok")            # the server is still up


def test_manifest_tools_are_the_live_descriptions(base):
    """abilities.tools (lane I10c's field): the same words as the MCP server's, for the store."""
    m = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))
    live = {t["name"]: t["description"] for t in mcp(base, "tools/list")[1]["result"]["tools"]}
    assert m["abilities"]["tools"] == live
