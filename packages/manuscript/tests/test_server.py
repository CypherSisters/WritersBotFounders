"""Manuscript 0.3.5 against a stand-in Core: the server runs as its own process, the Core here answers
``/v1/owner-files`` and ``/v1/brain/chat`` with canned JSON. Run from the package folder: ``python -m pytest tests -q``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
TOKEN = "test-token-123"
REPAIR = "Return only the JSON."

ALPHA = {
    "characters": [
        {"name": "Jennifer Hale", "aliases": ["Jen"], "role": "protagonist", "traits": ["stubborn"],
         "wants": "to leave the harbor", "fears": "the sea", "beat": "Refuses the job on the ferry.",
         "parents": ["Maren Hale"], "children": [], "partners": []},
        {"name": "Maren Hale", "role": "mother", "beat": "Waits at the dock.",
         "children": ["Jennifer Hale", "Tom Hale"]}],
    "relationships": [{"a": "Jennifer Hale", "b": "Maren Hale", "kind": "daughter", "note": ""}],
    "places": [{"name": "Storm Harbor", "kind": "town", "notes": "a fishing town", "where": "the north coast"}],
    "setting": {"world": "our world", "place": "a northern coast", "era": "now", "technology": "radios"},
    "history": [{"when": "twenty years ago", "what": "The old ferry went down."}],
    "events": [{"what": "The ferry sinks.", "who": ["Jennifer Hale"], "where": "Storm Harbor", "when": "night"}],
    "threads": [{"name": "The ferry", "kind": "main plot", "setup": "the cracked hull", "payoff": ""}],
    "themes": [{"name": "leaving home", "quote": "She wanted out."}],
    "world_rules": ["The tide turns every nine hours."],
    "science": [{"claim": "A nine-hour tide", "note": "Earth's tides turn about every six hours.", "plausible": "no"}],
    "continuity": [], "editing": [{"kind": "pacing", "note": "The opening is slow."}],
    "summary": "Jennifer refuses the ferry job. The ferry sinks.",
}
BRAVO = {
    "characters": [{"name": "Jen", "traits": ["loyal"], "beat": "Argues with Tom."},
                   {"name": "Tom Hale", "role": "brother", "beat": "Comes home."}],
    "relationships": [{"a": "Jen", "b": "Tom Hale", "kind": "sister", "note": "they stop speaking"}],
    "places": [{"name": "storm harbor"}],
    "events": [{"what": "Tom comes home.", "who": ["Tom Hale", "Jen"], "where": "Storm Harbor", "when": "morning"}],
    "continuity": [{"kind": "mismatch", "note": "Tom's eyes are grey here."}],
    "summary": "Tom comes home. Jen argues with him.",
}
CHARLIE = {
    "characters": [{"name": "Jennifer", "beat": "Takes the wheel in the storm."}, {"name": "Tom", "beat": "Leaves."}],
    "events": [{"what": "The storm hits.", "who": ["Jennifer"], "where": "Storm Harbor"}],
    "threads": [{"name": "The ferry", "payoff": "it fails in the storm"}],
    "summary": "The storm hits. Jennifer takes the wheel.",
}
GENERIC = {"characters": [{"name": "Jennifer Hale", "beat": "Walks on."}], "events": [{"what": "Time passes."}],
           "summary": "Things go on."}
THREADS = {"threads": [{"name": "The ferry", "kind": "main plot", "sections": [1, 3],
                        "setups": ["section 1: the cracked hull"], "payoffs": ["section 3: it fails in the storm"],
                        "open": False}],
           "continuity": [{"kind": "gap", "sections": [2, 3], "note": "A week goes missing."}]}
THEMES = {"themes": [{"name": "leaving home", "evidence": [{"section": 1, "quote": "She wanted out."},
                                                          {"section": 2, "quote": "An invented line."}]}],
          "one_line": "A woman leaves her harbor.", "paragraph": "Jennifer wants out."}
ARC = {"arc": [{"section": 1, "beat": "ARC BEAT ONE"}, {"section": 4, "beat": "ARC BEAT FOUR"}],
       "wants": "to be free", "fears": "staying"}
SHOW = {"title": "Jennifer's arc", "blocks": [
    {"type": "timeline", "items": [{"label": "Refuses the job", "section": "1", "who": ["Jennifer"]}]},
    {"type": "bogus", "text": "dropped"},
    {"type": "markdown", "text": "x" * 3000},
    {"type": "list", "items": [{"text": "a thing", "section": "two"}]}]}
SETTING = {"world": "our world", "place_in_world": "a northern coast", "era": "the present day",
           "technology": "boats and radios", "one_paragraph": "Storm Harbor is a fishing town on a cold coast — "
                                                            "today, with boats and radios.",
           "history": [{"when": "twenty years ago", "what": "The old ferry went down.", "sections": [1]}]}
CRAFT = {"pov": "third person, Jennifer", "tense": "past",
         "scenes": [{"purpose": "Sets up the harbor.", "pov": "Jennifer", "tense": "past", "starts": ""}],
         "slips": [], "tics": [{"phrase": "grey"}], "dialogue_habits": [{"character": "Jen", "habit": "short answers"}]}
CRAFT_BRAVO = {**CRAFT, "tense": "present", "slips": ["Slips into Tom's head for a line."],
               "scenes": [{"purpose": "Brings Tom home.", "pov": "Jennifer", "tense": "present", "starts": ""}]}
STORY_ORDER = {"story_order": ["e2", "e1", "e3"]}           # Tom came home before the ferry sank: told out of order
STRUCTURE = {"acts": [{"name": "Act one", "from": 1, "to": 1}, {"name": "Act two", "from": 2, "to": 3}],
             "turning_points": [{"name": "The ferry sinks", "section": 1, "note": "Her way out is gone."}],
             "midpoint": {"section": 2, "note": "Tom comes home."}, "climax": {"section": "3", "note": "The storm."}}
DESK = {"desk": [{"kind": "character", "severity": "high", "sections": [1, 3],
                  "note": "Jennifer wants to leave the harbor, and the want never pays off.",
                  "why": "A reader waits for her to leave and the book never answers that wait.",
                  "where": [{"section": 7, "quote": "The ferry sinks."}, {"section": 1, "quote": "Not in any fact."}],
                  "fix": "Let her try to leave once, in section 2."},
                 {"item": 1, "note": "Tom's eye colour changes between sections.",
                  "why": "A careful reader notices and stops trusting the details.",
                  "where": [{"section": 9, "quote": "Tom comes home."}],
                  "fix": "Make Tom's eyes grey in every section, starting with section 2."}]}
LONG_QUOTE = "Ada pushed her light hair back from her face " * 5          # over 120 characters
ECHO = {
    "characters": [{"name": "Ada Quill", "role": "protagonist", "beat": "Rides out.",
                    "looks": [{"detail": "light hair", "quote": LONG_QUOTE, "section": 99},
                              {"detail": "Light hair.", "quote": "a second copy"},
                              {"detail": "a burn on her left hand", "quote": "the burn on her left hand"}],
                    "age_words": "a girl of twelve"}],
    "places": [{"name": "Glass Town", "kind": "town",
                "details": [{"detail": "domes of green glass", "quote": "the green glass domes", "section": 42},
                            {"detail": "smells of tar", "quote": "the tar smell"}, "cold light",
                            {"detail": "Domes of green glass", "quote": "again"}]}],
    "geography": [{"from": "glass town", "to": "Port Ash", "note": "five days by cart", "section": 7,
                   "quote": "five days by cart to Port Ash"},
                  {"from": "Glass Town", "to": "Port Ash", "note": "Five days by cart."}],
    "setting_look": [{"detail": "gas lamps", "section": 3}, "Gas lamps"],
    "events": [{"what": "Ada rides out.", "who": ["Ada Quill"], "where": "Glass Town"}],
    "summary": "Ada rides out of Glass Town. Nobody stops her.",
}
FOXTROT = {
    "characters": [{"name": "Ada", "beat": "Arrives.", "looks": [{"detail": f"mark {i}", "quote": f"mark {i}"}
                                                                  for i in range(14)]}],
    "places": [{"name": "Glass Town", "details": [f"stone {i}" for i in range(9)]}],
    "events": [{"what": "Ada arrives."}], "summary": "Ada arrives at Port Ash.",
}
LOOKS_SMALL = {"characters": [{"name": "Ada", "looks": [{"detail": "freckles", "quote": "freckled", "section": 5}]}],
               "places": [], "geography": [], "setting_look": [{"detail": "carts"}]}


class Core:
    """The stand-in Core: owner files, and a brain that answers by what the system message asks."""

    def __init__(self, made: Path) -> None:
        self.made = made
        self.calls: list[list[dict]] = []
        self.bodies: list[dict] = []             # 0.3.3: every brain request body, to see as_written
        self.log: list[str] = []                 # what each brain call was, in the order they came
        core = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.headers.get("Authorization") != f"Bearer {TOKEN}":
                    return self._send(401, {"error": "no"})
                if self.path.startswith("/v1/owner-files"):
                    return self._send(200, {"made": str(core.made)})
                self._send(404, {})

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.headers.get("Authorization") != f"Bearer {TOKEN}" or self.path != "/v1/brain/chat":
                    return self._send(401, {"error": "no"})
                msgs = body["messages"]
                core.bodies.append(body)
                core.calls.append(msgs)
                core.log.append(core.label(msgs))
                if "BUSY503" in msgs[1]["content"]:
                    return self._send(503, {"error": "busy"})
                user = msgs[1]["content"]
                if (core.log[-1].startswith("Section") and "SLOW" in user) or "slowly" in user:
                    time.sleep(3)                # the stand-in brain, made slow on demand
                self._send(200, {"text": core.answer(msgs), "model": "stand-in:7b",
                                 "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @staticmethod
    def label(msgs: list[dict]) -> str:
        system, user = msgs[0]["content"], msgs[1]["content"]
        if "for her story bible" in system:
            return user.split("\n\n")[1].split(":")[0]           # "Section 3"
        if "make one view" in system:
            return "show"
        if "think about her own manuscript" in system:
            return "ask"
        return "other"

    def answer(self, msgs: list[dict]) -> str:
        system = msgs[0]["content"]
        user = msgs[1]["content"]
        repair = msgs[-1]["content"] == REPAIR
        if "for her story bible" in system:
            if "ALPHA" in user:
                return json.dumps(ALPHA)
            if "BRAVO" in user:
                return "<think>Let me look at who is here.\n{\"not\": \"this\"}</think>\n" + json.dumps(BRAVO)
            if "CHARLIE" in user:
                return json.dumps(CHARLIE) if repair else "Sure! Here are the facts: {characters: [Jennifer"
            if "ECHO" in user:
                return json.dumps(ECHO)
            if "FOXTROT" in user:
                return json.dumps(FOXTROT)
            return json.dumps(GENERIC)
        if "how its people and places look" in system:              # 0.3.3: the small looks ask
            return json.dumps(LOOKS_SMALL if "ECHO" in user else {"characters": []})
        if "see the world of her manuscript" in system:
            return json.dumps(SETTING)
        if "see her characters" in system:
            names = [ln[len("Character: "):].split(" (also")[0] for ln in user.split("\n")
                     if ln.startswith("Character: ")]
            return json.dumps({"people": [{"name": nm, "personality": f"{nm} is steady.",
                                           "write_up": f"{nm} matters to the story [1]."} for nm in names]})
        if "brief at the top of one tab" in system:
            tab = user.split("\n")[0][len("Tab: "):]
            return json.dumps({"brief": f"The {tab} brief. It holds together [1].\n\nA second paragraph.",
                               "cast_summary": "A small cast, led by Jennifer." if tab == "characters" else ""})
        if "note how it is told" in system:
            return json.dumps(CRAFT_BRAVO if "BRAVO" in user else CRAFT)
        if "story's own time" in system:
            return json.dumps(STORY_ORDER)
        if "shape of her manuscript" in system:
            return json.dumps(STRUCTURE)
        if "editor's desk" in system:
            return json.dumps(DESK)
        if "one character's arc" in system:
            return json.dumps(ARC)
        if "check the plot" in system:
            return json.dumps(THREADS)
        if "see the themes" in system:
            return json.dumps(THEMES)
        if "make one view" in system:
            if "nonsense" in user:
                return "I would rather not."
            return "```json\n" + json.dumps(SHOW) + "\n```"
        if "think about her own manuscript" in system:
            return "<think>hmm</think>Jennifer wants to leave the harbor (section 1)."
        return "{}"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(port, method, path, body=None, token=TOKEN):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=60) as r:
            raw = r.read()
            code = r.status
    except urllib.error.HTTPError as exc:
        raw, code = exc.read(), exc.code
    try:
        return code, json.loads(raw) if raw else None
    except ValueError:
        return code, raw


def q(**kw) -> str:
    return "?" + urllib.parse.urlencode(kw)


def tool(port, name, **args):
    code, got = call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                            "params": {"name": name, "arguments": args}})
    assert code == 200, got
    return got["result"]["content"][0]["text"], bool(got["result"].get("isError"))


def make_docx() -> bytes:
    import docx
    d = docx.Document()
    d.add_heading("Chapter One", 1)
    d.add_paragraph("ALPHA. Jennifer Hale stood on the cracked hull of the ferry and looked at the grey water.")
    d.add_paragraph("She wanted out. The harbor had held her for twenty years.")
    d.add_heading("Chapter Two", 1)
    d.add_paragraph("BRAVO. Tom came home on the morning boat with his grey eyes and a bag over one shoulder.")
    d.add_paragraph("Jen did not speak to him at dinner.")
    d.add_heading("Chapter Three", 1)
    d.add_paragraph("CHARLIE. The storm hit at noon, and Jennifer took the wheel while the others held on.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


SCENES = ("The first scene starts in the kitchen with the kettle.\n\nShe waits.\n\n***\n\n"
          "The second scene is at the docks.\n\n* * *\n\nThe third scene ends the story on the hill.\n")
LONG = "\n\n".join(" ".join(f"word{i}x{j}" for j in range(100)) for i in range(60))      # 6,000 words, 60 paragraphs
THREE = "\n\n".join(" ".join(f"t{i}w{j}" for j in range(100)) for i in range(30))         # 3,000 words, no headings
PICTURES = ("# One\n\nECHO. Ada Quill, a girl of twelve, rode out of Glass Town under the gas lamps.\n\n"
            "# Two\n\nFOXTROT. Ada came to Port Ash five days later.\n")
FOUR = "".join(f"# Part {k}\n\nDELTA {k}. Jennifer Hale walks along the shore.\n\n" for k in ("One", "Two", "Three",
                                                                                                "Four"))


def start_server(root: Path, **extra):
    made = root / "My files" / "Made by WritersBot"
    made.mkdir(parents=True)
    core = Core(made)
    port = free_port()
    e = {k: v for k, v in os.environ.items() if not k.upper().endswith("_PROXY")}
    e.update(PUFFPAI_API=f"http://127.0.0.1:{core.port}", PUFFPAI_TOKEN=TOKEN, PUFFPAI_PORT=str(port),
             PUFFPAI_DATA=str(root / "package data"), **extra)
    proc = subprocess.Popen([sys.executable, "server.py", "--port", str(port)], cwd=str(PKG), env=e,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(100):
        try:
            if call(port, "GET", "/health")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    else:
        stop_server(proc, core)
        raise RuntimeError("the server did not start")
    return proc, core, port


def stop_server(proc, core):
    proc.terminate()                             # our own process, by its own handle
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    core.stop()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("data")
    proc, core, port = start_server(root)
    try:
        books = {}
        for name, raw, title in (("storm.docx", make_docx(), "Storm Harbor"), ("short.txt", SCENES.encode(), "Short"),
                                 ("long.txt", LONG.encode(), "Long"), ("four.md", FOUR.encode(), "Four"),
                                 ("three.txt", THREE.encode(), "Three"), ("pictures.md", PICTURES.encode(), "Pictures")):
            code, got = call(port, "POST", "/api/upload", {"file_name": name, "title": title,
                                                           "content_b64": base64.b64encode(raw).decode()})
            assert code == 200, got
            books[title] = got
        shelf = root / "My files" / "Manuscripts"
        shas = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in shelf.glob("*/sections/*.md")}
        yield {"port": port, "core": core, "shelf": shelf, "shas": shas, "books": books}
    finally:
        stop_server(proc, core)


def wait_done(port, title, seconds=60):
    end = time.time() + seconds
    while time.time() < end:
        code, st = call(port, "GET", "/api/status" + q(title=title))
        if code == 200 and st.get("state") in ("done", "failed"):
            return st
        time.sleep(0.2)
    raise AssertionError(f"{title} did not finish reading: {st}")


def wait_view(port, title, vid=None, seconds=20):
    """The view (the newest, or the one with that id) once it is no longer making."""
    end = time.time() + seconds
    while time.time() < end:
        views = call(port, "GET", "/api/views" + q(title=title))[1]["views"]
        v = next((x for x in views if vid is None or x["id"] == vid), None)
        if v is not None and v.get("state") != "making":
            return v
        time.sleep(0.2)
    raise AssertionError(f"the view in {title} was still being made after {seconds} seconds")


def index(env, title):
    return json.loads((env["shelf"] / title / "manuscript.json").read_text(encoding="utf-8"))


def test_splitting(env):
    storm = index(env, "Storm Harbor")
    assert [s["heading"] for s in storm["sections"]] == ["Chapter One", "Chapter Two", "Chapter Three"]
    assert [s["n"] for s in storm["sections"]] == [1, 2, 3]
    assert storm["chapters"] == storm["sections"]
    short = index(env, "Short")
    assert [s["heading"] for s in short["sections"]] == ["Scene 1", "Scene 2", "Scene 3"]
    code, sec = call(env["port"], "GET", "/api/section" + q(title="Short", n=1))
    assert code == 200 and "kettle.\n\nShe waits." in sec["text"]           # paragraph breaks kept
    long = index(env, "Long")                       # 0.3: no headings, so parts of about 1,000 words, not 2,200
    assert [s["heading"] for s in long["sections"]] == [f"Whole manuscript, part {i}" for i in range(1, 7)]
    assert all(s["words"] <= 1000 for s in long["sections"]) and sum(s["words"] for s in long["sections"]) == 6000
    three = index(env, "Three")
    assert [s["words"] for s in three["sections"]] == [1000, 1000, 1000]


def test_reading_builds_the_bible(env):
    port = env["port"]
    for title in ("Storm Harbor", "Short", "Long", "Four", "Three"):
        st = wait_done(port, title)
        assert st["state"] == "done", st
    st = json.loads((env["shelf"] / "Storm Harbor" / "status.json").read_text(encoding="utf-8"))
    assert st["state"] == "done" and st["done"] == st["total"] == 3 and st["problems"] == []
    bible = json.loads((env["shelf"] / "Storm Harbor" / "bible.json").read_text(encoding="utf-8"))
    assert bible["built_with"] == "stand-in:7b" and bible["sections_read"] == 3
    chars = {c["id"]: c for c in bible["characters"]}
    assert set(chars) == {"jennifer", "maren", "tom"}                    # Jen and Jennifer merged into Jennifer Hale
    jen = chars["jennifer"]
    assert jen["name"] == "Jennifer Hale" and "Jen" in jen["aliases"]
    assert jen["appearances"] == [1, 2, 3] and jen["first_seen"] == 1
    assert {"stubborn", "loyal"} <= set(jen["traits"])
    assert jen["family"]["parents"] == ["maren"]
    assert {"to": "tom", "kind": "sister", "note": "they stop speaking"} in jen["relationships"]
    assert chars["tom"]["appearances"] == [2, 3]
    assert [e["section"] for e in bible["events"]] == [1, 2, 3]
    assert len(bible["places"]) == 1 and bible["places"][0]["appearances"] == [1, 2]
    assert bible["threads"][0]["open"] is False                           # from the threads call
    assert any(c["kind"] == "gap" for c in bible["continuity"])
    assert any(c["kind"] == "mismatch" for c in bible["continuity"])
    assert bible["themes"] == [{"name": "leaving home", "evidence": [{"section": 1, "quote": "She wanted out."}]}]
    assert bible["summary"]["one_line"] == "A woman leaves her harbor."
    four = json.loads((env["shelf"] / "Four" / "bible.json").read_text(encoding="utf-8"))
    assert [b["beat"] for b in four["characters"][0]["arc"]] == ["ARC BEAT ONE", "ARC BEAT FOUR"]
    assert list((env["shelf"] / "Storm Harbor" / "cache").glob("*.json"))
    calls = env["core"].calls
    assert any(m[-1]["content"] == REPAIR for m in calls)                 # the malformed answer was repaired
    for m in calls:                                                       # every ask under about 5,000 tokens
        assert sum(len(x["content"].split()) for x in m) * 1.4 < 5000


TAB_KINDS = {"characters": ["table", "graph", "tree"], "plot": ["timeline", "table", "list"],
             "timeline": ["timeline", "table"], "structure": ["table", "list", "table"],
             "world": ["markdown", "timeline", "list", "table"], "science": ["table"], "themes": ["list", "quote"],
             "voice": ["table", "list", "table"], "desk": ["table"], "editing": ["table"]}   # editing: the desk's 0.2 name
TAB_KINDS = {k: ["markdown"] + v for k, v in TAB_KINDS.items()}                # every tab opens with its brief
TAB_KINDS["characters"] = ["markdown", "markdown", "markdown", "table", "graph", "tree"]   # brief, cast, Jennifer


def test_tabs(env):
    sys.path.insert(0, str(PKG))
    import blocks
    wait_done(env["port"], "Storm Harbor")
    for tab, kinds in TAB_KINDS.items():
        code, got = call(env["port"], "GET", "/api/tab" + q(title="Storm Harbor", tab=tab))
        assert code == 200, got
        assert [b["type"] for b in got["blocks"]] == kinds, (tab, got["blocks"])
        assert blocks.clean_blocks(got["blocks"]) == got["blocks"]        # every block is already valid
        assert isinstance(got["views"], list)


def bible_of(env, title):
    return json.loads((env["shelf"] / title / "bible.json").read_text(encoding="utf-8"))


def test_bible_2(env):
    wait_done(env["port"], "Storm Harbor")
    b = bible_of(env, "Storm Harbor")
    assert b["schema"] == "manuscript-bible/2"
    assert [(s["n"], s["heading"]) for s in b["sections"]] == [(1, "Chapter One"), (2, "Chapter Two"),
                                                                (3, "Chapter Three")]
    assert all(s["words"] > 0 for s in b["sections"])
    assert [(e["id"], e["order"], e["story_order"]) for e in b["events"]] == [("e1", 1, 2), ("e2", 1, 1), ("e3", 1, 3)]
    sc = b["scenes"]
    assert [(s["section"], s["n"]) for s in sc] == [(1, 1), (2, 1), (3, 1)] and sc[0]["purpose"] == "Sets up the harbor."
    assert sc[0]["words"] == b["sections"][0]["words"] and 0 <= sc[0]["dialogue_share"] <= 1
    st = b["structure"]
    assert [a["name"] for a in st["acts"]] == ["Act one", "Act two"] and st["climax"] == {"section": 3,
                                                                                          "note": "The storm."}
    v = b["voice"]
    assert [x["tense"] for x in v["by_section"]] == ["past", "present", "past"]
    assert any("present tense" in x for x in v["by_section"][1]["slips"])
    assert v["tics"] == [{"phrase": "grey", "count": 2, "sections": [1, 2]}]
    assert v["dialogue_habits"] == [{"character": "Jennifer Hale", "habit": "short answers"}]
    kinds = {d["kind"] for d in b["desk"]}
    assert {"timeline", "continuity", "science", "character"} <= kinds
    for d in b["desk"]:
        assert d["severity"] in ("high", "medium", "low") and d["state"] == "open" and d["id"].startswith("d-")
    tl = [d for d in b["desk"] if d["kind"] == "timeline"]
    assert len(tl) == 1 and tl[0]["sections"] == [1, 2] and "Tom comes home." in tl[0]["note"]
    sys.path.insert(0, str(PKG))
    import analysis
    for m in env["core"].calls:                                           # the new asks stay small too
        assert analysis.tokens(" ".join(x["content"] for x in m)) < 5200


def test_briefs(env):
    port = env["port"]
    wait_done(port, "Storm Harbor")
    b = bible_of(env, "Storm Harbor")
    assert b["setting"]["place_in_world"] == "a northern coast" and b["setting"]["era"] == "the present day"
    assert "—" not in b["setting"]["one_paragraph"]                    # no em dashes reach the writer
    assert b["history"] == [{"when": "twenty years ago", "what": "The old ferry went down.", "sections": [1]}]
    assert set(b["briefs"]) == {"characters", "plot", "timeline", "structure", "world", "science", "themes", "voice",
                                "desk"}
    assert all(v.startswith(f"The {k} brief.") and "\n\n" in v for k, v in b["briefs"].items())
    assert b["cast_summary"] == "A small cast, led by Jennifer."
    chars = {c["id"]: c for c in b["characters"]}
    assert [chars[k]["importance"] for k in ("jennifer", "tom", "maren")] == ["main", "supporting", "minor"]
    assert chars["jennifer"]["personality"] == "Jennifer Hale is steady."
    assert chars["tom"]["write_up"] == "Tom Hale matters to the story [1]."
    assert chars["maren"]["write_up"] == "Maren Hale, mother, is in section 1: Waits at the dock."
    people = [m for m in env["core"].calls if "see her characters" in m[0]["content"]]
    assert people and not any("Character: Maren Hale" in m[1]["content"] for m in people)   # no call for her
    assert b["places"][0]["where"] == "the north coast"
    for tab in ("world", "characters"):
        got = call(port, "GET", "/api/tab" + q(title="Storm Harbor", tab=tab))[1]
        assert got["blocks"][0] == {"type": "markdown", "text": b["briefs"][tab]}
    world = call(port, "GET", "/api/tab" + q(title="Storm Harbor", tab="world"))[1]["blocks"]
    assert world[1]["text"] == b["setting"]["one_paragraph"] and world[2]["type"] == "timeline"
    assert world[-1]["columns"][2] == "Where" and world[-1]["rows"][0][2] == "the north coast"
    got = call(port, "GET", "/api/tab" + q(title="Storm Harbor", tab="characters"))[1]
    assert got["blocks"][1]["text"] == "A small cast, led by Jennifer."
    assert [(c["id"], c["block"], c["importance"]) for c in got["cards"]] == [("jennifer", 2, "main")]
    assert "Jennifer Hale is steady." in got["blocks"][2]["text"]
    line, err = tool(port, "manuscript_correct", title="Storm Harbor", what="Maren is a main character")
    assert not err and line.startswith("Changed it: Maren Hale is a main character now"), line
    got = call(port, "GET", "/api/tab" + q(title="Storm Harbor", tab="characters"))[1]
    assert [c["id"] for c in got["cards"]] == ["jennifer", "maren"]
    assert tool(port, "manuscript_analyze", title="Storm Harbor", confirm=True)[0]
    assert wait_done(port, "Storm Harbor")["state"] == "done"
    maren = next(c for c in bible_of(env, "Storm Harbor")["characters"] if c["id"] == "maren")
    assert maren["importance"] == "main" and maren["write_up"] == "Maren Hale matters to the story [1]."


def test_correction_survives_a_reread(env):
    port = env["port"]
    wait_done(port, "Storm Harbor")
    line, err = tool(port, "manuscript_correct", title="Storm Harbor", what="Tom is Jennifer's cousin, not her brother")
    assert not err and line.startswith("Changed it: Tom Hale is Jennifer Hale's cousin now"), line

    def rel():
        jen = next(c for c in bible_of(env, "Storm Harbor")["characters"] if c["id"] == "jennifer")
        return [r for r in jen["relationships"] if r["to"] == "tom"], jen
    rels, jen = rel()
    assert [(r["kind"], r.get("corrected")) for r in rels] == [("cousin", True)] and jen["corrected"] is True
    code, got = call(port, "POST", "/api/correct", {"title": "Storm Harbor", "what": "The storm lasts three days"})
    assert code == 200 and not got["correction"]["applied"] and "You said: The storm lasts three days" in got["message"]
    corr = json.loads((env["shelf"] / "Storm Harbor" / "corrections.json").read_text(encoding="utf-8"))
    assert [c["what"] for c in corr["corrections"]][-2:] == ["Tom is Jennifer's cousin, not her brother",
                                                        "The storm lasts three days"]
    assert all({"id", "what", "correction", "made_at"} <= set(c) for c in corr["corrections"])
    tab = call(port, "GET", "/api/tab" + q(title="Storm Harbor", tab="timeline"))[1]
    assert [(c["what"], c["applied"]) for c in tab["corrections"]] == [("The storm lasts three days", False)]
    chars = call(port, "GET", "/api/tab" + q(title="Storm Harbor", tab="characters"))[1]
    assert [c["applied"] for c in chars["corrections"]] == [True, True]          # Maren, then Tom
    table = next(b for b in chars["blocks"] if b["type"] == "table")
    assert any("(you corrected this)" in str(r[0]) for r in table["rows"])
    line, err = tool(port, "manuscript_analyze", title="Storm Harbor", confirm=True)
    assert not err and "started reading" in line
    assert wait_done(port, "Storm Harbor")["state"] == "done"
    rels, _jen = rel()
    assert [(r["kind"], r.get("corrected")) for r in rels] == [("cousin", True)]       # the re-read kept it


def test_desk(env):
    port = env["port"]
    wait_done(port, "Storm Harbor")
    code, got = call(port, "GET", "/api/desk" + q(title="Storm Harbor"))
    items = got["items"]
    assert code == 200 and items and got["open"] == len(items)
    assert [SEV[d["severity"]] for d in items] == sorted(SEV[d["severity"]] for d in items)
    assert items[0]["kind"] == "character" and items[0]["severity"] == "high"
    line, err = tool(port, "manuscript_desk", title="Storm Harbor", action="list")
    lines = line.split("\n")
    assert not err and lines[0] == f"The editor's desk for Storm Harbor: {len(items)} open, most serious first."
    assert lines[1].startswith("1. High, character, sections 1 and 3: Jennifer wants") and items[0]["id"] in lines[1]
    first, second = items[0], items[1]
    line, err = tool(port, "manuscript_desk", title="Storm Harbor", action="keep", id="1")
    assert not err and line.startswith("Kept it on the desk")
    line, err = tool(port, "manuscript_desk", title="Storm Harbor", action="dismiss", id="1")   # now the old second
    assert not err and line.startswith("Dismissed it")
    code, got = call(port, "POST", "/api/desk", {"title": "Storm Harbor", "action": "note", "id": first["id"],
                                                 "note": "I know. It pays off in book two."})
    assert code == 200 and got["item"]["writer_note"] == "I know. It pays off in book two."
    code, got = call(port, "POST", "/api/desk", {"title": "Storm Harbor", "action": "note", "id": first["id"]})
    assert code == 400
    states = {d["id"]: d for d in call(port, "GET", "/api/desk" + q(title="Storm Harbor"))[1]["items"]}
    assert states[first["id"]]["state"] == "kept" and states[first["id"]]["writer_note"].startswith("I know")
    assert states[second["id"]]["state"] == "dismissed"
    assert list(states)[-1] == second["id"]                               # dismissed ones come last
    tab = call(port, "GET", "/api/tab" + q(title="Storm Harbor", tab="desk"))[1]
    assert [d["id"] for d in tab["desk"]] == list(states)
    assert tool(port, "manuscript_analyze", title="Storm Harbor", confirm=True)[0]
    assert wait_done(port, "Storm Harbor")["state"] == "done"
    again = {d["id"]: d for d in bible_of(env, "Storm Harbor")["desk"]}
    assert again[first["id"]]["state"] == "kept" and again[second["id"]]["state"] == "dismissed"
    line, _err = tool(port, "manuscript_desk", title="Storm Harbor", action="list")
    assert second["id"] not in line and first["id"] not in line


SEV = {"high": 0, "medium": 1, "low": 2}


def test_new_notes_files(env):
    port = env["port"]
    folder = env["shelf"] / "Four"
    assert not (folder / "voice.md").exists() and (folder / "editing.md").exists()
    code, got = call(port, "GET", "/api/notes" + q(title="Four", tab="voice"))
    assert code == 200 and got["text"] == "# Voice\n"
    text, err = tool(port, "manuscript_notes_append", title="Four", tab="voice", text="Too many adverbs.")
    assert not err and "Too many adverbs." in (folder / "voice.md").read_text(encoding="utf-8")
    text, err = tool(port, "manuscript_notes_append", title="Four", tab="desk", text="Check the ending.")
    assert not err and "Check the ending." in (folder / "editing.md").read_text(encoding="utf-8")
    assert not (folder / "desk.md").exists()
    code, lst = call(port, "GET", "/api/list")
    assert [t["id"] for t in lst["tabs"]] == ["characters", "plot", "timeline", "structure", "world", "science",
                                              "themes", "voice", "desk"]
    assert lst["tabs"][-1]["label"] == "Editor's desk"


def test_show_saves_a_view(env):
    port = env["port"]
    wait_done(port, "Storm Harbor")
    line, err = tool(port, "manuscript_show", title="Storm Harbor", tab="characters",
                     asked="show me the character arc of Jennifer")
    assert not err and line == "Making it. It shows under Characters when it is ready, and it is drawn in Maps."
    v = wait_view(port, "Storm Harbor")
    assert v["state"] == "ready" and v["title"] == "Jennifer's arc"
    code, got = call(port, "GET", "/api/views" + q(title="Storm Harbor"))
    assert code == 200 and len(got["views"]) == 1
    assert v["tab"] == "characters" and v["made_by"] == "chat" and v["id"].startswith("v-")
    assert [b["type"] for b in v["blocks"]] == ["timeline", "markdown", "list"]
    assert v["blocks"][0]["items"][0]["section"] == 1
    assert len(v["blocks"][1]["text"]) == 2000
    assert "section" not in v["blocks"][2]["items"][0]
    code, tab = call(port, "GET", "/api/tab" + q(title="Storm Harbor", tab="characters"))
    assert [x["id"] for x in tab["views"]] == [v["id"]] and v["board"] == v["id"]
    code, got = call(port, "POST", "/api/show", {"title": "Storm Harbor", "tab": "plot", "asked": "the ferry"})
    assert code == 200 and got["view"]["made_by"] == "page" and got["view"]["state"] == "making"
    assert got["message"] == "Making it. It shows under Plot and subplots when it is ready."
    assert wait_view(port, "Storm Harbor", got["view"]["id"])["state"] == "ready"
    code, got = call(port, "DELETE", "/api/views" + q(title="Storm Harbor", id=got["view"]["id"]))
    assert code == 200
    assert len(call(port, "GET", "/api/views" + q(title="Storm Harbor"))[1]["views"]) == 1


def test_show_with_a_drawing_twin_writes_a_board(env):
    port = env["port"]
    wait_done(port, "Storm Harbor")
    path = env["shelf"] / "Storm Harbor" / "boards.json"
    theirs = {"id": "b-20261101-120000-timeline", "kind": "timeline", "title": "Maps' own", "asked": "the timeline",
              "made_at": "2026-11-01T12:00:00Z", "spec": {"layout": "maps/1", "kind": "timeline"}}
    path.write_text(json.dumps({"boards": [theirs], "kept_by_maps": {"x": 1}}), encoding="utf-8")
    line, err = tool(port, "manuscript_show", title="Storm Harbor", tab="characters", asked="show me the Hale family")
    assert not err and line.endswith("and it is drawn in Maps."), line
    got = json.loads(path.read_text(encoding="utf-8"))
    assert got["kept_by_maps"] == {"x": 1} and theirs in got["boards"]      # what Maps wrote stays
    mine = got["boards"][0]
    views = call(port, "GET", "/api/views" + q(title="Storm Harbor"))[1]["views"]
    assert mine["id"] == views[0]["id"] == views[0]["board"] and "spec" not in mine
    assert mine["kind"] == "family" and mine["asked"] == "show me the Hale family" and mine["title"] and mine["made_at"]
    import importlib.util
    spec = importlib.util.spec_from_file_location("maps_draw", PKG.parent / "maps" / "draw.py")
    draw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(draw)
    kind = draw.pick_kind(mine.get("kind"), mine.get("asked"))
    laid = draw.build(bible_of(env, "Storm Harbor"), kind, mine["asked"])
    assert kind == "family" and laid["layout"] == "maps/1" and laid["nodes"]
    line, err = tool(port, "manuscript_show", title="Storm Harbor", tab="plot", asked="show me what Tom wants")
    assert not err and line == "Making it. It shows under Plot and subplots when it is ready."
    assert len(json.loads(path.read_text(encoding="utf-8"))["boards"]) == 2      # no twin, no board
    wait_view(port, "Storm Harbor")


def test_point(env):
    port = env["port"]
    text = call(port, "GET", "/api/section" + q(title="Storm Harbor", n=1))[1]["text"]
    exact = "stood on the cracked hull of the ferry"
    line, err = tool(port, "manuscript_point", title="Storm Harbor", section="1", quote=exact, note="Here")
    assert not err and "at the words you gave" in line
    p = call(port, "GET", "/api/pointer" + q(title="Storm Harbor"))[1]
    assert p["section"] == 1 and text[p["start"]:p["end"]] == exact and p["note"] == "Here" and p["set_at"]
    fuzzy = "Jen stood upon the cracked hull of the ferry, looking at the sea"
    line, err = tool(port, "manuscript_point", title="Storm Harbor", quote=fuzzy)          # no section: all
    assert not err and "words in a row" in line
    p = call(port, "GET", "/api/pointer" + q(title="Storm Harbor"))[1]
    assert p["section"] == 1 and text[p["start"]:p["end"]] == "the cracked hull of the ferry"
    line, err = tool(port, "manuscript_point", title="Storm Harbor", section="1", quote="zzqx vbnm plorf")
    assert not err and "did not find anything" in line
    p = call(port, "GET", "/api/pointer" + q(title="Storm Harbor"))[1]
    first = text.index("ALPHA")
    assert p["start"] == first and text[p["start"]:p["end"]].startswith("ALPHA") and p["match"] == "nothing"
    code, _ = call(port, "DELETE", "/api/pointer" + q(title="Storm Harbor"))
    assert code == 200 and call(port, "GET", "/api/pointer" + q(title="Storm Harbor"))[1]["set_at"] is None


def test_ask_and_status(env):
    port = env["port"]
    wait_done(port, "Storm Harbor")
    line, err = tool(port, "manuscript_ask", title="Storm Harbor", question="what does Jennifer want?")
    assert not err and line == "Jennifer wants to leave the harbor (section 1)."
    asked = env["core"].calls[-1][1]["content"]
    assert "Character Jennifer Hale" in asked and "Section 1: Chapter One" in asked
    line, err = tool(port, "manuscript_status", title="Storm Harbor")
    assert not err and "Read 3 of 3 sections." in line and "stand-in:7b" in line
    line, err = tool(port, "manuscript_analyze", title="Storm Harbor", confirm=True)
    assert not err and "started reading" in line
    assert wait_done(port, "Storm Harbor")["state"] == "done"


def test_old_routes_and_tools(env):
    port = env["port"]
    old = env["shelf"] / "Old Book"
    (old / "chapters").mkdir(parents=True)
    (old / "chapters" / "01 - One.md").write_text("# One\n\nAn old chapter.\n", encoding="utf-8")
    (old / "manuscript.json").write_text(json.dumps({"title": "Old Book", "chapters": [
        {"n": 1, "heading": "One", "file": "01 - One.md", "words": 3}]}), encoding="utf-8")
    code, got = call(port, "GET", "/api/chapter" + q(title="Old Book", chapter="1"))
    assert code == 200 and "An old chapter." in got["text"]
    text, err = tool(port, "manuscript_read", title="Storm Harbor", chapter="2")
    assert not err and "BRAVO" in text
    text, err = tool(port, "manuscript_read", title="Old Book", section="One")
    assert not err and "An old chapter." in text
    code, lst = call(port, "GET", "/api/list")
    titles = {m["title"]: m for m in lst["manuscripts"]}
    assert titles["Storm Harbor"]["sections"] == titles["Storm Harbor"]["chapters"]
    assert "status" in titles["Storm Harbor"]
    text, err = tool(port, "manuscript_notes_append", title="Storm Harbor", tab="editing", text="Chapter 2 drags.")
    assert not err
    code, notes = call(port, "GET", "/api/notes" + q(title="Storm Harbor", tab="editing"))
    assert "Chapter 2 drags." in notes["text"]
    code, got = call(port, "POST", "/api/notes", {"title": "Storm Harbor", "tab": "editing", "text": "x",
                                                  "stamp": "stale"})
    assert code == 400                                                    # the stale-save refusal
    code, got = call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in got["result"]["tools"]}
    assert names == {"manuscript_list", "manuscript_read", "manuscript_status", "manuscript_analyze", "manuscript_ask", "manuscript_remove", "manuscript_add_file",
                     "manuscript_show", "manuscript_point", "manuscript_notes_read", "manuscript_notes_append",
                     "manuscript_correct", "manuscript_desk"}


ROUTES = [("GET", "/"), ("GET", "/health"), ("GET", "/api/list"), ("GET", "/api/section?title=Short&n=1"),
          ("GET", "/api/chapter?title=Short&chapter=1"), ("GET", "/api/status?title=Short"),
          ("GET", "/api/bible?title=Short"), ("GET", "/api/tab?title=Short&tab=plot"), ("GET", "/api/views?title=Short"),
          ("GET", "/api/notes?title=Short&tab=plot"), ("GET", "/api/pointer?title=Short"), ("POST", "/mcp"),
          ("GET", "/api/desk?title=Short"), ("POST", "/api/desk"), ("POST", "/api/correct"),
          ("POST", "/api/upload"), ("POST", "/api/analyze"), ("POST", "/api/show"), ("POST", "/api/notes"),
          ("DELETE", "/api/views?title=Short&id=x"), ("DELETE", "/api/pointer?title=Short"), ("PUT", "/api/notes")]


@pytest.mark.parametrize("token", [None, "wrong"])
def test_every_route_needs_the_token(env, token):
    for method, path in ROUTES:
        body = {"title": "Short"} if method == "POST" else None
        code, _ = call(env["port"], method, path, body, token=token)
        assert code == 401, (method, path, code)


def test_evidence_for_pictures(env):
    port = env["port"]
    assert wait_done(port, "Pictures")["problems"] == []
    b = bible_of(env, "Pictures")
    ada = next(c for c in b["characters"] if c["name"] == "Ada Quill")
    assert len(ada["looks"]) == 12                                         # the cap holds
    assert ada["looks"][:2] == [{"detail": "light hair", "section": 1, "quote": LONG_QUOTE.strip()[:120]},
                                {"detail": "a burn on her left hand", "section": 1,
                                 "quote": "the burn on her left hand"}]     # the copy collapsed, section 99 ignored
    assert all(x["section"] == 2 for x in ada["looks"][2:]) and ada["looks"][-1]["detail"] == "mark 9"
    assert all(len(x["quote"]) <= 120 for x in ada["looks"])
    assert ada["age_words"] == {"words": "a girl of twelve", "section": 1}
    town = next(p for p in b["places"] if p["name"] == "Glass Town")
    assert len(town["details"]) == 10 and [x["section"] for x in town["details"][:3]] == [1, 1, 1]
    assert town["details"][0] == {"detail": "domes of green glass", "section": 1, "quote": "the green glass domes"}
    assert town["details"][2] == {"detail": "cold light", "section": 1, "quote": ""}
    assert b["geography"] == [{"from": "Glass Town", "to": "Port Ash", "note": "five days by cart", "section": 1,
                               "quote": "five days by cart to Port Ash"}]
    assert b["setting"]["look"] == [{"detail": "gas lamps", "section": 1}]   # kept through the setting call too
    assert all("looks" in c for c in b["characters"])
    full = call(port, "GET", "/api/bible" + q(title="Pictures"))[1]
    assert full["geography"] == b["geography"] and full["setting"]["look"] == b["setting"]["look"]
    entries = [json.loads(p.read_text(encoding="utf-8")) for p in (env["shelf"] / "Pictures" / "cache").glob("*.json")]
    assert len(entries) == 2 and all(e.get("looks") == {} for e in entries)  # one ask carried it all

    world = call(port, "GET", "/api/tab" + q(title="Pictures", tab="world"))[1]["blocks"]
    places = next(x for x in world if x["type"] == "table" and x["columns"][0] == "Place")
    assert places["columns"] == ["Place", "Kind", "Where", "Notes", "Details", "Sections"]
    assert places["rows"][0][4] == {"text": "domes of green glass [1]; smells of tar [1]; cold light [1]",
                                    "section": 1}
    at = world.index({"type": "markdown", "text": "### Distances and directions"})
    assert world[at + 1]["columns"] == ["From", "To", "Distance or direction", "In the text"]
    assert world[at + 1]["rows"] == [["Glass Town", "Port Ash", {"text": "five days by cart", "section": 1},
                                      {"text": "five days by cart to Port Ash", "section": 1}]]
    chars = call(port, "GET", "/api/tab" + q(title="Pictures", tab="characters"))[1]
    card = next(c for c in chars["cards"] if c["name"] == "Ada Quill")
    assert card["looks"][0]["section"] == 1 and len(card["looks"]) == 12
    assert "**Looks:** light hair [1]; a burn on her left hand [1]; mark 0 [2]" in chars["blocks"][card["block"]]["text"]


def test_old_cache_gets_the_small_looks_ask(env):
    port, core = env["port"], env["core"]
    wait_done(port, "Pictures")
    for p in (env["shelf"] / "Pictures" / "cache").glob("*.json"):           # as 0.3.2 left them
        e = json.loads(p.read_text(encoding="utf-8"))
        e.pop("looks")
        ext = e["extraction"]
        for k in ("geography", "setting_look"):
            ext.pop(k, None)
        for c in ext.get("characters") or []:
            c.pop("looks", None)
            c.pop("age_words", None)
        for pl in ext.get("places") or []:
            pl.pop("details", None)
        p.write_text(json.dumps(e), encoding="utf-8")
    before = len(core.calls)
    assert tool(port, "manuscript_analyze", title="Pictures", confirm=True)[0]
    assert wait_done(port, "Pictures")["problems"] == []
    new = core.calls[before:]
    small = [m for m in new if "how its people and places look" in m[0]["content"]]
    assert len(small) == 2 and not any(core.label(m).startswith("Section") for m in new)   # no facts ask again
    b = bible_of(env, "Pictures")
    ada = next(c for c in b["characters"] if c["name"] == "Ada Quill")
    assert ada["looks"] == [{"detail": "freckles", "section": 1, "quote": "freckled"}]
    assert b["setting"]["look"] == [{"detail": "carts", "section": 1}] and b["geography"] == []
    entries = [json.loads(p.read_text(encoding="utf-8")) for p in (env["shelf"] / "Pictures" / "cache").glob("*.json")]
    assert all(isinstance(e.get("looks"), dict) and e.get("craft") for e in entries)


def test_merge_and_the_long_section_rule():
    sys.path.insert(0, str(PKG))
    import analysis
    assert analysis.looks_fit("word " * 2200, {}) and not analysis.looks_fit("word " * 3200, {})
    small = {"characters": [{"name": "Ada Quill", "looks": [{"detail": "tall", "quote": "tall", "section": 8}]}],
             "places": [{"name": "Port Ash", "details": ["salt on everything"]}],
             "geography": [{"from": "Port Ash", "to": "Glass Town", "note": "south", "section": 4}],
             "setting_look": ["carts"]}
    b = analysis.merge("T", [(1, "One"), (2, "Two")], {1: {"characters": [{"name": "Ada Quill"}]}, 2: {"summary": "x"}},
                       looks={2: small})
    assert b["characters"][0]["looks"] == [{"detail": "tall", "section": 2, "quote": "tall"}]
    assert b["characters"][0]["appearances"] == [1, 2]
    assert b["places"][0]["details"] == [{"detail": "salt on everything", "section": 2, "quote": ""}]
    assert b["geography"][0]["section"] == 2 and b["setting"]["look"] == [{"detail": "carts", "section": 2}]
    for m in [m for m in ([analysis.LOOKS_SYSTEM, analysis.EXTRACT_LOOKS_SYSTEM])]:
        assert "—" not in m


def test_desk_items_say_why(env):
    port = env["port"]
    wait_done(port, "Storm Harbor")
    desk = bible_of(env, "Storm Harbor")["desk"]
    want = next(d for d in desk if d["note"].startswith("Jennifer wants to leave"))
    assert want["why"] == "A reader waits for her to leave and the book never answers that wait."
    assert want["where"] == [{"section": 1, "quote": "The ferry sinks."}]   # its own section; the made-up quote dropped
    assert want["fix"] == "Let her try to leave once, in section 2."
    eyes = next(d for d in desk if d["note"] == "Tom's eye colour changes between sections.")
    assert eyes["kind"] == "continuity" and eyes["where"] == [{"section": 2, "quote": "Tom comes home."}]
    assert eyes["fix"].startswith("Make Tom's eyes grey") and eyes["why"]
    general = [d for d in desk if d["kind"] in ("science", "timeline")]
    assert general and all(d["fix"].startswith("A general suggestion: ") and d["why"] == "" and d["where"] == []
                           for d in general)
    line, err = tool(port, "manuscript_desk", title="Storm Harbor", action="list")
    assert not err and "Why it matters: A careful reader notices" in line and "Fix: Make Tom's eyes grey" in line


def test_a_bible_from_0_3_2_still_shows(env):
    sys.path.insert(0, str(PKG))
    import blocks
    port = env["port"]
    wait_done(port, "Pictures")
    old = bible_of(env, "Storm Harbor")
    old.pop("geography", None)
    old["setting"].pop("look", None)
    for c in old["characters"]:
        c.pop("looks", None)
        c.pop("age_words", None)
    for p in old["places"]:
        p.pop("details", None)
    for d in old["desk"]:
        d.pop("why", None)
        d.pop("where", None)
    assert [b["type"] for b in blocks.tab_blocks(old, "world")] == TAB_KINDS["world"]
    assert all("Looks" not in c["text"] for c in blocks.cards(old))
    (env["shelf"] / "Pictures" / "bible.json").write_text(json.dumps(old), encoding="utf-8")
    for tab in TAB_KINDS:
        code, got = call(port, "GET", "/api/tab" + q(title="Pictures", tab=tab))
        assert code == 200 and got["blocks"], (tab, got)
    world = call(port, "GET", "/api/tab" + q(title="Pictures", tab="world"))[1]["blocks"]
    assert world[-1]["columns"][4] == "Details" and all(r[4] == "" for r in world[-1]["rows"])
    assert tool(port, "manuscript_desk", title="Pictures", action="list")[0]


def test_keep_manuscripts_out_of_the_vault(env):
    port, core = env["port"], env["core"]
    for title in ("Storm Harbor", "Pictures"):
        wait_done(port, title)
    assert core.bodies and all(b.get("as_written") is True for b in core.bodies)      # on by default
    assert call(port, "GET", "/api/settings")[1] == {"settings": {"vault_free": True}}
    assert call(port, "GET", "/api/status" + q(title="Storm Harbor"))[1]["vault"] == "out of the vault"
    line, _err = tool(port, "manuscript_status", title="Storm Harbor")
    assert "(out of the vault)" in line.split("\n")[0]
    code, got = call(port, "POST", "/api/settings", {"vault_free": False})
    assert code == 200 and got["settings"] == {"vault_free": False} and "read it again" in got["message"]
    assert call(port, "GET", "/api/settings")[1] == {"settings": {"vault_free": False}}
    before = len(core.bodies)
    tool(port, "manuscript_ask", title="Storm Harbor", question="what does Jennifer want?")
    assert len(core.bodies) > before and all(b.get("as_written") is False for b in core.bodies[before:])
    assert call(port, "GET", "/api/status" + q(title="Storm Harbor"))[1]["vault"] == "out of the vault"  # as read
    assert call(port, "POST", "/api/settings", {"vault_free": "no"})[0] == 400
    assert call(port, "POST", "/api/settings", {"vault_free": True})[1]["settings"] == {"vault_free": True}
    before = len(core.bodies)
    tool(port, "manuscript_ask", title="Storm Harbor", question="what does Tom want?")
    assert all(b.get("as_written") is True for b in core.bodies[before:])


def test_real_counts(env):
    port = env["port"]
    wait_done(port, "Pictures")
    sample = (PKG / "fixtures" / "bible.sample.json").read_text(encoding="utf-8")
    (env["shelf"] / "Pictures" / "bible.json").write_text(sample, encoding="utf-8")
    want = {"characters": 6, "main": 0, "threads": 3, "places": 3, "events": 12, "desk": 0, "desk_high": 0}
    books = {m["title"]: m for m in call(port, "GET", "/api/list")[1]["manuscripts"]}
    assert books["Pictures"]["counts"] == want
    line, err = tool(port, "manuscript_status", title="Pictures")
    assert not err and line.split("\n")[1] == ("Found: 6 characters (0 main), 3 plot threads, 3 places, 12 events, "
                                                "0 items on the editor's desk (0 high).")
    storm = bible_of(env, "Storm Harbor")
    desk_high = sum(1 for d in storm["desk"] if d["severity"] == "high")
    assert books["Storm Harbor"]["counts"] == {
        "characters": len(storm["characters"]),
        "main": sum(1 for c in storm["characters"] if c["importance"] == "main"),
        "threads": len(storm["threads"]), "places": len(storm["places"]), "events": len(storm["events"]),
        "desk": len(storm["desk"]), "desk_high": desk_high}
    listed, _err = tool(port, "manuscript_list")
    assert "  Found: 6 characters (0 main), 3 plot threads" in listed
    assert f"  Found: {len(storm['characters'])} characters (" in listed


def test_zz_section_text_never_changed(env):
    for title in ("Storm Harbor", "Short", "Long", "Four", "Three", "Pictures"):
        wait_done(env["port"], title)
    now = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in env["shelf"].glob("*/sections/*.md")}
    assert now == env["shas"] and len(now) == 21                       # 0.3.3: Pictures adds two
    for title in ("Storm Harbor", "Short", "Long", "Four", "Three"):
        for s in index(env, title)["sections"]:
            assert now[env["shelf"] / title / "sections" / s["file"]] == s["sha"]


def test_page_files(env):
    for name, ctype in (("app.js", "text/javascript"), ("blocks.js", "text/javascript"), ("app.css", "text/css")):
        if not (PKG / "web" / name).is_file():
            pytest.skip(f"web/{name} is not there yet")
        req = urllib.request.Request(f"http://127.0.0.1:{env['port']}/{name}",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
        with OPENER.open(req, timeout=10) as r:
            assert r.status == 200 and r.headers["Content-Type"] == ctype
            assert r.read() == (PKG / "web" / name).read_bytes()
        assert call(env["port"], "GET", "/" + name, token=None)[0] == 401
    assert call(env["port"], "GET", "/../server.py")[0] == 404


# -- the queue: a slow brain, a reading, and the owner's own asks ------------------------------------------

SLOW_BOOK = "".join(f"# Part {k}\n\nSLOW {k}. Jennifer Hale walks along the shore.\n\n"
                    for k in ("One", "Two", "Three", "Four"))


@pytest.fixture(scope="module")
def slow(tmp_path_factory):
    proc, core, port = start_server(tmp_path_factory.mktemp("slow"), MANUSCRIPT_ASK_WAIT_S="2")
    try:
        yield {"port": port, "core": core}
    finally:
        stop_server(proc, core)


def test_show_during_a_reading_waits_its_turn(slow):
    port, core = slow["port"], slow["core"]
    code, got = call(port, "POST", "/api/upload", {"file_name": "slow.md", "title": "Slow",
                                                   "content_b64": base64.b64encode(SLOW_BOOK.encode()).decode()})
    assert code == 200, got
    end = time.time() + 15                         # section 1 read, section 2's slow call under way
    while "Section 2" not in core.log:
        assert time.time() < end, core.log
        time.sleep(0.05)
    t0 = time.time()
    code, got = call(port, "POST", "/api/show", {"title": "Slow", "tab": "characters", "asked": "show me Jennifer"})
    assert time.time() - t0 < 1, "the show waited for the brain"
    assert code == 200 and got["message"] == "Making it. It shows under Characters when it is ready."
    v = got["view"]
    assert v["state"] == "making" and v["tab"] == "characters" and v["asked"] == "show me Jennifer"
    assert v["title"] and v["made_at"] and "blocks" not in v
    t0 = time.time()
    line, err = tool(port, "manuscript_show", title="Slow", tab="plot", asked="show me nonsense about Jennifer")
    assert time.time() - t0 < 1 and not err and line == "Making it. It shows under Plot and subplots when it is ready."
    bad = call(port, "GET", "/api/views" + q(title="Slow", tab="plot"))[1]["views"][0]
    assert bad["state"] == "making"

    ready = wait_view(port, "Slow", v["id"])
    assert ready["state"] == "ready" and ready["blocks"] and ready["title"] == "Jennifer's arc"
    failed = wait_view(port, "Slow", bad["id"])
    assert failed["state"] == "failed" and failed["why"] and "blocks" not in failed
    tab = call(port, "GET", "/api/tab" + q(title="Slow", tab="characters"))[1]
    assert tab["blocks"] and [x["id"] for x in tab["views"]] == [v["id"]]       # one fetch has both

    end = time.time() + 15
    while "Section 3" not in core.log:
        assert time.time() < end, core.log
        time.sleep(0.05)
    log = core.log
    shows = [i for i, x in enumerate(log) if x == "show"]
    assert len(shows) == 3, log                     # one view, and one asked twice (the repair)
    assert log.index("Section 2") < shows[0] and shows[-1] < log.index("Section 3"), log


def test_ask_is_bounded(slow):
    port = slow["port"]
    st = call(port, "GET", "/api/status" + q(title="Slow"))[1]
    assert st["state"] == "reading", st
    t0 = time.time()
    line, err = tool(port, "manuscript_ask", title="Slow", question="slowly, what does Jennifer want?")
    assert time.time() - t0 < 5 and not err
    assert line.startswith("Your bot is busy reading Slow right now (section "), line
    assert " of 4). Ask again in a few minutes, or ask me to show it under a tab and it will appear when ready." in line
    assert wait_done(port, "Slow")["state"] == "done"
    line, err = tool(port, "manuscript_ask", title="Slow", question="BUSY503 what does Jennifer want?")
    assert not err and line.startswith("Your bot is busy right now."), line
    line, err = tool(port, "manuscript_ask", title="Slow", question="what does Jennifer want?")
    assert not err and line == "Jennifer wants to leave the harbor (section 1)."


def test_remove_puts_a_manuscript_away(env):
    port, shelf = env["port"], env["shelf"]
    code, got = call(port, "POST", "/api/upload", {"file_name": "bye.txt", "title": "Goodbye",
                                                   "content_b64": base64.b64encode(THREE.encode()).decode()})
    assert code == 200, got
    wait_done(port, "Goodbye")                       # the upload starts a reading; let it finish first
    folder = shelf / "Goodbye"
    assert folder.is_dir()
    status = folder / "status.json"
    before = status.read_text(encoding="utf-8") if status.exists() else None
    status.write_text(json.dumps({"state": "reading", "step": "Reading section 1 of 3", "done": 0, "total": 3}), encoding="utf-8")
    # 0.3.4: a status file that says "reading" with no live reading behind it (Manuscript was restarted) no longer blocks
    code, st = call(port, "GET", "/api/status" + q(title="Goodbye"))
    assert st["state"] == "failed"                   # the server already reads a stale "reading" as stopped
    status.write_text(before if before is not None else json.dumps({"state": "done"}), encoding="utf-8")
    original = (folder / "original.txt").read_bytes() if (folder / "original.txt").exists() else None
    text, err = tool(port, "manuscript_remove", title="Goodbye")
    assert not err and "Nothing was deleted" in text, text
    assert not folder.exists()
    moved = [p for p in (shelf / "Removed").iterdir() if p.name.startswith("Goodbye ")]
    assert len(moved) == 1 and (moved[0] / "manuscript.json").exists()
    if original is not None:
        assert (moved[0] / "original.txt").read_bytes() == original
    code, lst = call(port, "GET", "/api/list")
    assert code == 200 and all(m["title"] != "Goodbye" for m in lst["manuscripts"])
    code, got = call(port, "DELETE", "/api/manuscript" + q(title="Goodbye"))
    assert code == 400 and "could not find" in got["error"]
    code, got = call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    me = [t for t in got["result"]["tools"] if t["name"] == "manuscript_remove"][0]
    assert me["annotations"]["readOnlyHint"] is False


def test_analyze_needs_the_writers_yes(env):
    port = env["port"]
    wait_done(port, "Four")
    code, before = call(port, "GET", "/api/status" + q(title="Four"))
    text, err = tool(port, "manuscript_analyze", title="Four")
    assert not err and text.startswith("Not started.") and "Shall I read Four now?" in text
    code, after = call(port, "GET", "/api/status" + q(title="Four"))
    assert after.get("started_at") == before.get("started_at")          # nothing started
    text, err = tool(port, "manuscript_analyze", title="Four", confirm=True)
    assert not err and "started reading Four" in text
    wait_done(port, "Four")


# -- 0.3.5: PDFs ---------------------------------------------------------------------------------------------

sys.path.insert(0, str(PKG))
sys.path.insert(0, str(PKG / "tests"))
import pdf_text  # noqa: E402
import pdf_fixtures as fx  # noqa: E402


def need_reportlab():
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def texts(book, heading=None):
    return [p.text for p in book.paragraphs if heading is None or bool(p.heading) == heading]


def test_pdf_novel_paragraphs_exactly():
    need_reportlab()
    book = pdf_text.read_pdf(fx.novel())
    got = texts(book)
    # A wrong join anywhere fails here: each paragraph is compared whole.
    start = got.index("Chapter 1")
    assert got[:start] == fx.FRONT_TEXT                       # the title page's big type is no chapter
    want = fx.PARAGRAPHS
    assert got[start + 1:start + 7] == [want["ferry"], want["tonight"], want["cafe"], want["times"], want["wind"], want["short"]]
    assert got[start + 7] == "* * *"                          # the mark, as it was
    assert got[start + 8] == want["rain"]
    assert got[start + 9:start + 12] == ["", "", ""]          # a large gap: three blank paragraphs, no invented words
    assert got[start + 12:start + 15] == [want["three"], want["whose"], want["bells"]]
    assert texts(book, heading=True) == ["Chapter 1", "Chapter 3", "IV."]
    joined = " ".join(got)
    assert "THE SALT LEDGER" not in joined and "Fine Blue Author" not in joined      # running heads gone
    assert not [t for t in got if t.strip().isdigit()]        # page numbers gone
    assert "\u201cNot tonight,\u201d" in joined and "caf\u00e9" in joined            # curly quotes and accents as they are
    assert "breakwater" in joined and "baptism" in joined and "unforgivable" in joined    # split words joined
    assert "well-known" in joined and "Anglo-Saxon" in joined                 # real hyphens kept
    assert "The Harbour Times at the long table" in joined    # italics read in place, with their spaces


def test_pdf_report_counts_what_it_did():
    need_reportlab()
    r = pdf_text.read_pdf(fx.novel()).report
    assert r["pages"] == 5 and r["pages_no_text"] == [] and r["chapters_found"] == 3 and r["parts_found"] == 0
    assert r["running_heads_removed"] == 3 and r["page_numbers_removed"] == 4
    assert r["hyphens_joined"] == 3 and r["scene_breaks"] == 2 and r["warnings"] == []
    words = sum(len(t.split()) for t in texts(pdf_text.read_pdf(fx.novel()), heading=False))
    assert r["words"] == words
    line = pdf_text.report_line(r)
    assert line == (f"From the PDF: 5 pages, {words} words, 3 chapters found, 7 running heads and page numbers removed, "
                    "3 hyphens joined.")
    assert "\u2014" not in line


def test_pdf_every_word_comes_from_the_page():
    need_reportlab()
    from pypdf import PdfReader
    raw = fx.novel()
    plain = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(raw)).pages)
    have = set(plain.split())
    book = pdf_text.read_pdf(raw)
    glued = {"breakwater", "baptism", "unforgivable", "well-known", "Anglo-Saxon"}
    for word in " ".join(texts(book)).split():
        assert word in have or word.strip(".,") in glued, word


def test_pdf_heads_keep_a_real_heading_and_a_contents_list_makes_no_sections():
    need_reportlab()
    trees = iter("oak ash elm yew fir bay box fig gum may nut oat pea rye sage teak vine wort yam lime".split())

    def lines():
        return [f"She walked the long way round the harbour wall, past the {next(trees)}, and did not look." for _ in range(4)]
    pages = [["Contents", "", "Chapter 1", "Chapter 2", "Chapter 3"], ["", "", "Chapter 1", ""] + lines()]
    pages += [["Chapter 1", "", "", ""] + lines() for _ in range(3)]
    pages += [["", "", "Chapter 2", ""] + lines()]
    book = pdf_text.read_pdf(fx.text_pages(pages))
    assert texts(book, heading=True) == ["Chapter 1", "Chapter 2"]     # the heading stays, the same words as a head go
    assert texts(book)[0:2] == ["Contents", "Chapter 1 Chapter 2 Chapter 3"]
    assert book.report["contents_lines"] == 3 and book.report["running_heads_removed"] == 3


def test_pdf_drop_cap_goes_back_on_its_word():
    need_reportlab()
    buf = io.BytesIO()
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(buf, pagesize=(fx.W, fx.H))
    c.setFont("Times-Bold", 40)
    c.drawString(54, 560, "T")
    c.setFont("Times-Roman", 11)
    c.drawString(84, 585, "he ferry left at dawn and nobody on the harbour came")
    c.drawString(84, 570, "down to see it go, and she counted the gulls alone")
    c.drawString(54, 555, "from the end of the wall, in the rain, as she always did.")
    c.save()
    assert texts(pdf_text.read_pdf(buf.getvalue())) == [
        "The ferry left at dawn and nobody on the harbour came down to see it go, and she counted the gulls alone "
        "from the end of the wall, in the rain, as she always did."]


def test_pdf_cleaning_keeps_what_the_writer_set():
    counts = __import__("collections").Counter()
    got = pdf_text._clean("\ufb01ne \ufb02oor\u00a0\u2009o\ufb00 \u201cquotes\u201d \u2014 caf\u00e9\u200b \u2019s", counts)
    assert got == "fine floor  off \u201cquotes\u201d \u2014 caf\u00e9 \u2019s"
    assert counts["ligatures"] == 3 and counts["odd_spaces"] == 3


def test_pdf_guessed_spaces_are_weighed_by_the_book():
    def sheet(*lines):
        s = pdf_text.Sheet(1, 0, [pdf_text.Line(54, 600 - 15 * i, 11, t, "", 1) for i, t in enumerate(lines)], 216)
        for ln in s.lines:
            ln.finish()
        return s
    g = pdf_text.GUESS
    s = sheet("He investigated the flood and the rope, and part of though she knew it", f"she inve{g}stigated it thoug{g}h the rope{g}frayed",
              f"and then a{g}part of the flood{g}, enthu{g}-")
    counts = __import__("collections").Counter()
    pdf_text._settle_guesses([s], counts)
    assert [ln.text for ln in s.lines][1:] == ["she investigated it though the rope frayed", "and then a part of the flood, enthu-"]
    assert counts["stray_spaces"] == 3
    # where the guesses mostly hold, a word the book does not know is left alone
    s = sheet("The flood and the rope and the ledger came", f"the{g}flood and{g}the rope{g}ledger and a{g}zzyzx{g}qwerty")
    pdf_text._settle_guesses([s], counts)
    assert s.lines[1].text == "the flood and the rope ledger and a zzyzx qwerty"


def test_pdf_no_chapters_says_so_and_scene_gaps_split_a_story():
    need_reportlab()
    para = ["The ferry left at dawn and nobody on the harbour came down to", "see it go, and she wrote it in the ledger that night by the",
            "stove with the door shut against the rain and the dark.", "", "", "", "",
            "In the morning she rowed out alone past the second buoy and", "let the boat drift while she ate her bread at the oars and did",
            "not look back at the town until the sun had come up."]
    raw = fx.text_pages([para])
    book = pdf_text.read_pdf(raw)
    assert book.report["chapters_found"] == 0 and book.report["warnings"] == [
        "I found no chapter headings, so the book will be split by its scene breaks or by length."]
    assert "I found no chapter headings" in pdf_text.report_line(book.report)
    assert texts(book)[1:4] == ["", "", ""] and len(texts(book)) == 5


def test_pdf_columns_and_tables_are_read_and_said():
    need_reportlab()
    book = pdf_text.read_pdf(fx.two_columns())
    got = " ".join(texts(book))
    assert got.index("Left column, line thorn") < got.index("Right column, line oak")      # the left column all before the right
    assert book.report["pages_two_up"] == [1] and any("side by side" in w for w in book.report["warnings"])
    book = pdf_text.read_pdf(fx.table_page())
    assert "cell 0-0" in " ".join(texts(book)) and book.report["pages_tables"] == [1]
    # the same lines, read as the writer would: each paragraph whole
    assert texts(pdf_text.read_pdf(fx.two_columns()))[0].startswith("Left column, line oak, in plain words here.")
    assert any("table" in w for w in book.report["warnings"])


@pytest.mark.parametrize("make,line", [
    (lambda: fx.scanned(), "This PDF is pictures of pages, so there is no text to read. Reading scans is not built."),
    (lambda: fx.locked(), "This PDF is locked with a password, so I cannot read it. Save a copy with no password and try again."),
    (lambda: fx.empty(), "This PDF has no text and no pictures. Its pages are empty."),
    (lambda: b"This is a letter to my sister, renamed.", "That file is not a real PDF, so it will not open as one."),
    (lambda: b"%PDF-1.7 and then nothing a reader could use", "That file is not a real PDF, so it will not open as one."),
])
def test_pdf_with_nothing_to_read_is_one_plain_line(make, line):
    need_reportlab()
    with pytest.raises(pdf_text.Refused) as got:
        pdf_text.read_pdf(make())
    assert str(got.value) == line


def test_pdf_mixed_names_the_pages_with_no_text():
    need_reportlab()
    book = pdf_text.read_pdf(fx.mixed())
    r = book.report
    assert r["pages_no_text"] == [3] and r["pages_pictures"] == [3] and "ferry left at dawn" in texts(book)[0]
    assert "Page 3 is a picture with no text, so its words are not here." in pdf_text.report_line(r)


@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-128", "AES-256"])
def test_pdf_with_only_an_owner_lock_opens_with_no_password(algorithm):
    need_reportlab()
    if algorithm.startswith("AES"):
        pytest.importorskip("cryptography")
    assert texts(pdf_text.read_pdf(fx.locked(user_password="", algorithm=algorithm)), heading=True) == ["Chapter 1", "Chapter 3", "IV."]


def test_pdf_warns_about_wrong_letters_and_a_scanners_hidden_text():
    need_reportlab()
    assert pdf_text._garbled([pdf_text.Para("\ue123\ue456 " * 150)]) is True
    assert pdf_text._garbled([pdf_text.Para("x7#q 9$kz " * 150)]) is True
    assert pdf_text._garbled([pdf_text.Para("She wrote it down in the ledger, as she always did. " * 20)]) is False
    book = pdf_text.read_pdf(fx.hidden_text())
    assert book.report["scanned_text"] is True and "scanner's hidden text" in pdf_text.report_line(book.report)
    assert "second morning" in " ".join(texts(book))


def test_novel_mode_fits_under_the_skill_cap_and_names_real_tools():
    skill = PKG.parent / "novel-mode" / "SKILL.md"
    if not skill.is_file():
        pytest.skip("Novel mode is not beside this package here")
    body = skill.read_text(encoding="utf-8").split("---", 2)[2].strip()
    assert len(body) < 7000                                   # zone2/skills.py MAX_CHARS; past it the end is cut off
    assert "manuscript.manuscript_add_file" in body and "pdf_to_word" in body and "send_document" in body


def test_pdf_module_is_the_same_file_in_both_packages():
    other = PKG.parent / "document-convert"
    if not other.is_dir():
        pytest.skip("Document convert is not beside this package here")
    for name in ("pdf_text.py", "tests/pdf_fixtures.py"):
        assert (PKG / name).read_bytes().replace(b"\r\n", b"\n") == (other / name).read_bytes().replace(b"\r\n", b"\n"), name


@pytest.fixture(scope="module")
def pdfenv(tmp_path_factory):
    root = tmp_path_factory.mktemp("pdfdata")
    proc, core, port = start_server(root)
    try:
        yield {"port": port, "root": root, "my": root / "My files", "shelf": root / "My files" / "Manuscripts"}
    finally:
        stop_server(proc, core)


def test_upload_takes_a_pdf_and_sections_split_at_its_chapters(pdfenv):
    need_reportlab()
    port, shelf = pdfenv["port"], pdfenv["shelf"]
    raw = fx.novel()
    code, got = call(port, "POST", "/api/upload", {"file_name": "The Salt Ledger.pdf", "content_b64": b64(raw)})
    assert code == 200, got
    words = got["report"]["words"]
    assert got["title"] == "The Salt Ledger" and got["message"].startswith("Added The Salt Ledger: 4 sections, ")
    assert got["message"].endswith("Your bot is reading it now. From the PDF: 5 pages, "
                                   f"{words} words, 3 chapters found, 7 running heads and page numbers removed, 3 hyphens joined.")
    folder = shelf / "The Salt Ledger"
    assert (folder / "original.pdf").read_bytes() == raw      # the original kept whole, as any upload is
    idx = json.loads((folder / "manuscript.json").read_text(encoding="utf-8"))
    assert [c["heading"] for c in idx["sections"]] == ["Before the first chapter", "Chapter 1", "Chapter 3", "IV."]
    assert idx["original_file"] == "original.pdf" and idx["pdf_report"]["chapters_found"] == 3
    code, sec = call(port, "GET", "/api/section" + q(title="The Salt Ledger", n=1))
    want = fx.PARAGRAPHS
    assert sec["text"].startswith(f"# Chapter 1\n\n{want['ferry']}\n\n{want['tonight']}\n\n{want['cafe']}\n\n{want['times']}\n\n")
    assert f"{want['rain']}\n\n{want['three']}" in sec["text"]     # blank paragraphs drop inside a chapter, as .docx
    assert "\n\n* * *\n\n" in sec["text"]
    st = wait_done(port, "The Salt Ledger")                   # a page upload starts the reading, as it always has
    assert st["state"] == "done"
    text, err = tool(port, "manuscript_list")
    assert not err and "From the PDF: 5 pages" in text


def test_upload_of_a_pdf_keeps_a_contents_list_as_text(pdfenv):
    need_reportlab()
    trees = iter("oak ash elm yew fir bay box fig".split())

    def lines():
        return [f"She walked the long way round the harbour wall, past the {next(trees)}, and did not look." for _ in range(4)]
    raw = fx.text_pages([["Contents", "", "Chapter 1", "Chapter 2", "Chapter 3"], ["Chapter 1", ""] + lines(), ["Chapter 2", ""] + lines()])
    code, got = call(pdfenv["port"], "POST", "/api/upload", {"file_name": "toc.pdf", "title": "With Contents", "content_b64": b64(raw)})
    assert code == 200, got
    code, lst = call(pdfenv["port"], "GET", "/api/list")
    book = [m for m in lst["manuscripts"] if m["title"] == "With Contents"][0]
    assert [s["heading"] for s in book["sections"]] == ["Before the first chapter", "Chapter 1", "Chapter 2"]
    code, sec = call(pdfenv["port"], "GET", "/api/section" + q(title="With Contents", n=0))
    assert sec["text"] == "# Before the first chapter\n\nContents\n\nChapter 1 Chapter 2 Chapter 3\n"
    wait_done(pdfenv["port"], "With Contents")


def test_upload_of_a_pdf_with_no_headings_splits_at_its_scene_gaps(pdfenv):
    need_reportlab()
    para = ["The ferry left at dawn and nobody on the harbour came down to", "see it go, and she wrote it in the ledger that night by the",
            "stove with the door shut against the rain and the dark.", "", "", "", "",
            "In the morning she rowed out alone past the second buoy and", "let the boat drift while she ate her bread at the oars and did",
            "not look back at the town until the sun had come up."]
    code, got = call(pdfenv["port"], "POST", "/api/upload", {"file_name": "story.pdf", "title": "Gap Story", "content_b64": b64(fx.text_pages([para]))})
    assert code == 200 and got["sections"] == 2 and "no chapter headings" in got["message"], got
    code, lst = call(pdfenv["port"], "GET", "/api/list")
    book = [m for m in lst["manuscripts"] if m["title"] == "Gap Story"][0]
    assert [s["heading"] for s in book["sections"]] == ["Scene 1", "Scene 2"]
    wait_done(pdfenv["port"], "Gap Story")


@pytest.mark.parametrize("make,word", [(lambda: fx.scanned(), "pictures of pages"), (lambda: fx.locked(), "locked with a password"),
                                       (lambda: fx.empty(), "pages are empty"), (lambda: b"not a pdf at all", "not a real PDF")])
def test_upload_refuses_a_pdf_it_cannot_read_and_adds_nothing(pdfenv, make, word):
    need_reportlab()
    before = sorted(p.name for p in pdfenv["shelf"].glob("*")) if pdfenv["shelf"].exists() else []
    code, got = call(pdfenv["port"], "POST", "/api/upload", {"file_name": "bad.pdf", "content_b64": b64(make())})
    assert code == 400 and word in got["error"], got
    after = sorted(p.name for p in pdfenv["shelf"].glob("*")) if pdfenv["shelf"].exists() else []
    assert after == before


def test_old_types_upload_as_before_and_the_page_takes_pdf(pdfenv):
    code, got = call(pdfenv["port"], "POST", "/api/upload", {"file_name": "a.exe", "content_b64": b64(b"MZ")})
    assert code == 400 and got["error"] == "Upload a Word file (.docx), a PDF (.pdf), a text file (.txt) or a Markdown file (.md)."
    assert 'accept=".docx,.pdf,.txt,.md"' in (PKG / "web" / "index.html").read_text(encoding="utf-8")
    code, got = call(pdfenv["port"], "POST", "/api/upload", {"file_name": "plain.txt", "title": "Plain", "content_b64": b64(SCENES.encode())})
    assert code == 200 and got["message"] == f"Added Plain: {got['sections']} sections, {sum_words(pdfenv, 'Plain')} words. Your bot is reading it now."
    assert "report" not in got and "words" not in got and "folder" not in got
    wait_done(pdfenv["port"], "Plain")


def sum_words(pdfenv, title):
    idx = json.loads((pdfenv["shelf"] / title / "manuscript.json").read_text(encoding="utf-8"))
    return sum(c["words"] for c in idx["sections"])


def test_add_file_adds_from_my_files_and_starts_no_reading(pdfenv):
    need_reportlab()
    port, my, shelf = pdfenv["port"], pdfenv["my"], pdfenv["shelf"]
    converted = my / "Converted"
    converted.mkdir(exist_ok=True)
    (converted / "Added Novel.pdf").write_bytes(fx.novel())
    (converted / "From Word.docx").write_bytes(make_docx())
    (my / "Loose Notes.md").write_text("# One\n\nALPHA. Some words.\n", encoding="utf-8")
    text, err = tool(port, "manuscript_add_file", source="Converted/Added Novel.pdf")
    assert not err and text.startswith("Added Added Novel from My files: 4 sections, "), text
    assert "From the PDF: 5 pages," in text and "The reading has not started; it waits for the writer's yes." in text
    assert "manuscript_analyze" in text and "confirm true" in text
    folder = shelf / "Added Novel"
    assert (folder / "original.pdf").read_bytes() == (converted / "Added Novel.pdf").read_bytes()
    assert (converted / "Added Novel.pdf").exists()           # the file stays where it was
    code, st = call(port, "GET", "/api/status" + q(title="Added Novel"))
    assert st.get("state") not in ("reading", "done", "failed") and not (folder / "bible.json").exists()
    assert not (folder / "cache").exists()
    # by bare name, in Converted or anywhere else in My files; a backslash path as Windows writes it
    text, err = tool(port, "manuscript_add_file", source="From Word.docx", title="Word Book")
    assert not err and text.startswith("Added Word Book from My files: 3 sections, "), text
    text, err = tool(port, "manuscript_add_file", source="Loose Notes")
    assert not err and "Added Loose Notes" in text
    text, err = tool(port, "manuscript_add_file", source="Converted\\From Word.docx", title="Word Again")
    assert not err and "Added Word Again" in text
    # her yes still starts it, the usual way
    text, err = tool(port, "manuscript_analyze", title="Added Novel")
    assert not err and text.startswith("Not started.")
    text, err = tool(port, "manuscript_analyze", title="Added Novel", confirm=True)
    assert not err and "started reading Added Novel" in text
    wait_done(port, "Added Novel")


def test_add_file_refuses_outside_my_files(pdfenv):
    port, root, my = pdfenv["port"], pdfenv["root"], pdfenv["my"]
    secret = root / "outside.txt"
    secret.write_text("Not hers to add.", encoding="utf-8")
    (my / "Converted").mkdir(exist_ok=True)
    (my / "Converted" / "runme.exe").write_bytes(b"MZ")
    before = sorted(p.name for p in pdfenv["shelf"].glob("*")) if pdfenv["shelf"].exists() else []
    for source in ("../outside.txt", "Converted/../../outside.txt", "..\\outside.txt", str(secret), secret.as_posix(), "/etc/passwd", "C:/Windows/win.ini"):
        text, err = tool(port, "manuscript_add_file", source=source)
        assert err and text == "I can only add a file from My files, and that one is outside it. Nothing was added.", (source, text)
    text, err = tool(port, "manuscript_add_file", source="Converted/runme.exe")
    assert err and text.startswith("I can add a Word file")
    text, err = tool(port, "manuscript_add_file", source="Converted/not here.pdf")
    assert err and "could not find" in text
    text, err = tool(port, "manuscript_add_file", source="")
    assert err and "Say which file" in text
    assert (sorted(p.name for p in pdfenv["shelf"].glob("*")) if pdfenv["shelf"].exists() else []) == before


def test_add_file_refuses_a_link_and_a_name_that_could_be_two(pdfenv):
    port, root, my = pdfenv["port"], pdfenv["root"], pdfenv["my"]
    (root / "outside dir").mkdir(exist_ok=True)
    (root / "outside dir" / "secret.txt").write_text("Not hers to add.", encoding="utf-8")
    (my / "a").mkdir(exist_ok=True)
    (my / "b").mkdir(exist_ok=True)
    (my / "a" / "twice.txt").write_text("One.", encoding="utf-8")
    (my / "b" / "twice.txt").write_text("Two.", encoding="utf-8")
    text, err = tool(port, "manuscript_add_file", source="twice.txt")
    assert err and "More than one file" in text and "a/twice.txt" in text and "b/twice.txt" in text
    try:
        os.symlink(root / "outside dir", my / "linked", target_is_directory=True)
        os.symlink(root / "outside dir" / "secret.txt", my / "secret link.txt")
    except OSError:
        pytest.skip("this computer does not let a test make links")
    for source in ("linked/secret.txt", "secret link.txt"):
        text, err = tool(port, "manuscript_add_file", source=source)
        assert err and "outside it" in text, (source, text)


def test_add_file_is_listed_as_a_change_with_no_argument_the_core_reads_as_her_file(pdfenv):
    code, got = call(pdfenv["port"], "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    me = [t for t in got["result"]["tools"] if t["name"] == "manuscript_add_file"][0]
    assert me["annotations"]["readOnlyHint"] is False and me["inputSchema"]["required"] == ["source"]
    # owner_files.FILE_ARGS in the Core: with one of these, "write" and a listed change, the tool is hidden while edits are off
    assert not set(me["inputSchema"]["properties"]) & {"path", "file", "file_path", "filepath", "filename", "file_name", "document"}
    manifest = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))
    assert "manuscript_add_file" in manifest["abilities"]["changes"] and manifest["version"] == "0.3.5"
    names = {t["name"] for t in got["result"]["tools"]}
    assert set(manifest["abilities"]["tools"]) == names and set(manifest["abilities"]["examples"]) <= names
    assert (PKG / "requirements.txt").read_text(encoding="utf-8").split() == ["python-docx==1.2.0", "pypdf==6.21.4", "cryptography==50.0.2"]
    assert call(pdfenv["port"], "GET", "/api/list")[0] == 200
