"""Manuscript 0.4.1 against a stand-in Core: the server runs as its own process, the Core here answers
``/v1/owner-files`` and ``/v1/brain/chat`` with canned JSON. Run from the package folder: ``python -m pytest tests -q``.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
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


# 0.4: a small book for the corrections, every tab's data in it
FIX_ONE = {
    "characters": [
        {"name": "Mara Voss", "aliases": ["Mara"], "role": "protagonist", "traits": ["stubborn", "loyal"],
         "wants": "to leave the island", "fears": "deep water", "beat": "Mara finds the letter.",
         "looks": [{"detail": "green eyes", "quote": "her green eyes"}], "age_words": "a girl of twelve"},
        {"name": "Elias Ward", "role": "keeper", "traits": ["quiet"], "wants": "to keep the light", "fears": "fire",
         "beat": "Elias climbs the lighthouse.",
         "looks": [{"detail": "a scar on his chin", "quote": "the scar on his chin"}, {"detail": "a limp", "quote": "he limped"}]},
        {"name": "Tom Reed", "role": "sailor", "beat": "Tom waits at the dock."},
        {"name": "Anna", "role": "cook", "traits": ["kind"], "beat": "Anna feeds them."}],
    "relationships": [{"a": "Mara Voss", "b": "Elias Ward", "kind": "niece", "note": "she lives with him"}],
    "places": [{"name": "The Lighthouse", "kind": "tower", "notes": "white and tall",
                "details": [{"detail": "white walls", "quote": "its white walls"}]},
               {"name": "Port Ash", "kind": "town", "notes": "a fishing town"}],
    "events": [{"what": "The storm sinks the ferry.", "who": ["Elias Ward", "Mara Voss"], "where": "Port Ash", "when": "autumn"},
               {"what": "Elias climbs the lighthouse at night.", "who": ["Elias Ward"], "where": "The Lighthouse",
                "when": "that night"}],
    "threads": [{"name": "The letter", "kind": "main plot", "setup": "Mara finds it"}],
    "themes": [{"name": "leaving home", "quote": "She wanted out."}, {"name": "duty", "quote": "The light must burn."}],
    "world_rules": ["The tide turns every nine hours."],
    "science": [{"claim": "A nine-hour tide", "note": "Earth's tides turn about every six hours.", "plausible": "no"}],
    "continuity": [{"kind": "mismatch", "note": "The ferry sails on a Sunday and a Monday."}],
    "editing": [{"kind": "pacing", "note": "The opening drags."}],
    "history": [{"when": "twenty years ago", "what": "The old war ended."}],
    "setting": {"world": "our world", "place": "a northern island", "era": "the 1920s", "technology": "steam"},
    "geography": [{"from": "Port Ash", "to": "The Lighthouse", "note": "two hours on foot", "quote": "two hours on foot"}],
    "setting_look": [{"detail": "oil lamps"}], "summary": "Mara finds a letter. The storm comes."}
FIX_TWO = {
    "characters": [{"name": "Mara Voss", "beat": "Mara reads the letter."},
                   {"name": "Annie", "role": "cook", "traits": ["loud"], "beat": "Annie sings."},
                   {"name": "Elias Ward", "beat": "Elias lights the lamp."}],
    "events": [{"what": "Mara reads the letter.", "who": ["Mara Voss"], "where": "Port Ash", "when": "the next day"}],
    "summary": "Mara reads the letter."}
FIX_BOOK = "# One\n\nFIXONE. Mara Voss looked out with her green eyes.\n\n# Two\n\nFIXTWO. Mara read the letter.\n"
FIX_ORDER = [(1, "One"), (2, "Two")]


class Core:
    """The stand-in Core: owner files, and a brain that answers by what the system message asks."""

    def __init__(self, made: Path) -> None:
        self.made = made
        self.calls: list[list[dict]] = []
        self.bodies: list[dict] = []             # 0.3.3: every brain request body, to see as_written
        self.log: list[str] = []                 # what each brain call was, in the order they came
        self.fix_answer = "{}"                   # 0.4: what the brain answers a correction's ask
        self.prose: dict | None = None           # 0.4: when set, what it writes for a character's prose
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
        if "You fix facts" in system:
            return "fix"
        if "make one view" in system:
            return "show"
        if "think about her own manuscript" in system:
            return "ask"
        return "other"

    def answer(self, msgs: list[dict]) -> str:
        system = msgs[0]["content"]
        user = msgs[1]["content"]
        repair = msgs[-1]["content"] == REPAIR
        if "You fix facts" in system:
            return self.fix_answer
        if "for her story bible" in system:
            if "FIXONE" in user:
                return json.dumps(FIX_ONE)
            if "FIXTWO" in user:
                return json.dumps(FIX_TWO)
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
        if "see her characters" in system and self.prose is not None:
            return json.dumps(self.prose)
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
             "voice": ["table", "list", "table"]}
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
    assert "desk" not in b and "desk" not in b["briefs"]                  # 0.5: editing is Editor's Desk's
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
    assert set(b["briefs"]) == {"characters", "plot", "timeline", "structure", "world", "science", "themes", "voice"}
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


def test_the_desk_is_gone(env):
    """0.5: editing is in Editor's Desk. No desk route, tool, tab or ask; a call for one says where it went."""
    port = env["port"]
    wait_done(port, "Storm Harbor")
    assert call(port, "GET", "/api/desk" + q(title="Storm Harbor"))[0] == 404
    assert call(port, "POST", "/api/desk", {"title": "Storm Harbor", "action": "list"})[0] == 404
    code, got = call(port, "GET", "/api/tab" + q(title="Storm Harbor", tab="desk"))
    assert code == 400 and "Editor's Desk" in got["error"]
    code, got = call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                            "params": {"name": "manuscript_desk", "arguments": {"action": "list"}}})
    assert code == 200 and ("error" in got or got["result"].get("isError")), got
    line, err = tool(port, "manuscript_status", title="Storm Harbor")
    assert not err and "desk" not in line
    assert not [m for m in env["core"].calls if "editor's desk" in m[0]["content"].lower()]


def test_new_notes_files(env):
    port = env["port"]
    folder = env["shelf"] / "Four"
    assert not (folder / "voice.md").exists() and not (folder / "editing.md").exists()   # 0.4.1: made on first write
    code, got = call(port, "GET", "/api/notes" + q(title="Four", tab="voice"))
    assert code == 200 and got["text"] == "# Voice\n"
    text, err = tool(port, "manuscript_notes_append", title="Four", tab="voice", text="Too many adverbs.")
    assert not err and "Too many adverbs." in (folder / "voice.md").read_text(encoding="utf-8")
    text, err = tool(port, "manuscript_notes_append", title="Four", tab="desk", text="Check the ending.")
    assert err and "Editor's Desk" in text and not (folder / "editing.md").exists()
    code, lst = call(port, "GET", "/api/list")
    assert [t["id"] for t in lst["tabs"]] == ["characters", "plot", "timeline", "structure", "world", "science",
                                              "themes", "voice"]


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
    text, err = tool(port, "manuscript_notes_append", title="Storm Harbor", tab="structure", text="Chapter 2 drags.")
    assert not err
    code, notes = call(port, "GET", "/api/notes" + q(title="Storm Harbor", tab="structure"))
    assert "Chapter 2 drags." in notes["text"]
    code, got = call(port, "POST", "/api/notes", {"title": "Storm Harbor", "tab": "structure", "text": "x",
                                                  "stamp": "stale"})
    assert code == 400                                                    # the stale-save refusal
    code, got = call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in got["result"]["tools"]}
    assert names == {"manuscript_list", "manuscript_read", "manuscript_status", "manuscript_analyze", "manuscript_ask", "manuscript_remove", "manuscript_add_file",
                     "manuscript_show", "manuscript_point", "manuscript_notes_read", "manuscript_notes_append",
                     "manuscript_correct", "manuscript_correct_undo", "manuscript_corrections"}


ROUTES = [("GET", "/"), ("GET", "/health"), ("GET", "/api/list"), ("GET", "/api/section?title=Short&n=1"),
          ("GET", "/api/chapter?title=Short&chapter=1"), ("GET", "/api/status?title=Short"),
          ("GET", "/api/bible?title=Short"), ("GET", "/api/tab?title=Short&tab=plot"), ("GET", "/api/views?title=Short"),
          ("GET", "/api/notes?title=Short&tab=plot"), ("GET", "/api/pointer?title=Short"), ("POST", "/mcp"),
          ("POST", "/api/correct"),
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
    old["desk"] = [{"id": "d-3f2a9c01be", "kind": "continuity", "severity": "medium", "sections": [1, 2],   # as 0.3.2 read it
                    "note": "Tom's eyes change.", "fix": "Pick one.", "state": "kept", "writer_note": ""}]
    old["briefs"]["desk"] = "The old letter."
    assert [b["type"] for b in blocks.tab_blocks(old, "world")] == TAB_KINDS["world"]
    assert all("Looks" not in c["text"] for c in blocks.cards(old))
    for name in ("bible.json", "bible.base.json"):
        (env["shelf"] / "Pictures" / name).write_text(json.dumps(old), encoding="utf-8")
    for tab in TAB_KINDS:
        code, got = call(port, "GET", "/api/tab" + q(title="Pictures", tab=tab))
        assert code == 200 and got["blocks"], (tab, got)
    world = call(port, "GET", "/api/tab" + q(title="Pictures", tab="world"))[1]["blocks"]
    assert world[-1]["columns"][4] == "Details" and all(r[4] == "" for r in world[-1]["rows"])
    assert call(port, "GET", "/api/bible" + q(title="Pictures"))[1]["desk"] == old["desk"]   # an older desk loads, untouched


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
    want = {"characters": 6, "main": 0, "threads": 3, "places": 3, "events": 12}
    books = {m["title"]: m for m in call(port, "GET", "/api/list")[1]["manuscripts"]}
    assert books["Pictures"]["counts"] == want
    line, err = tool(port, "manuscript_status", title="Pictures")
    assert not err and line.split("\n")[1] == "Found: 6 characters (0 main), 3 plot threads, 3 places, 12 events."
    storm = bible_of(env, "Storm Harbor")
    assert books["Storm Harbor"]["counts"] == {
        "characters": len(storm["characters"]),
        "main": sum(1 for c in storm["characters"] if c["importance"] == "main"),
        "threads": len(storm["threads"]), "places": len(storm["places"]), "events": len(storm["events"])}
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
    assert "manuscript_add_file" in manifest["abilities"]["changes"] and manifest["version"] == "0.5.1"
    names = {t["name"] for t in got["result"]["tools"]}
    assert set(manifest["abilities"]["tools"]) == names and set(manifest["abilities"]["examples"]) <= names
    assert (PKG / "requirements.txt").read_text(encoding="utf-8").split() == ["python-docx==1.2.0", "pypdf==6.21.4", "cryptography==50.0.2"]
    assert call(pdfenv["port"], "GET", "/api/list")[0] == 200


# -- 0.4: real fact-fixing ------------------------------------------------------------------------------------------

def fix_merge():
    import analysis
    return analysis.merge("Fix", FIX_ORDER, {1: json.loads(json.dumps(FIX_ONE)), 2: json.loads(json.dumps(FIX_TWO))})


def person(b, cid):
    return next(c for c in b["characters"] if c["id"] == cid)


def op_rec(*ops, **kw):
    return {"id": "c-1", "what": "x", "correction": "", "made_at": "t", "by": "hand", "ops": list(ops), "line": "x",
            "state": "done", **kw}


def hand(b, *asked):
    import fixing
    return fixing.hand_ops(b, list(asked))


def again(records):
    """A re-read: the merge runs again, the same facts come back, and her records go on top."""
    import fixing
    b = fix_merge()
    done = fixing.apply_corrections(b, records)
    return b, done


def key(kind, e):
    from blocks import entry_key
    return entry_key(kind, e)


def test_fix_the_bible_has_every_kind():
    import fixing
    b = fix_merge()
    b["structure"] = {"acts": [{"name": "Act one", "from": 1, "to": 2}],
                      "turning_points": [{"name": "The letter", "section": 1, "note": "It starts."}],
                      "midpoint": {"section": 1, "note": "m"}, "climax": {"section": 2, "note": "c"}}
    b["voice"] = {"by_section": [{"section": 1, "pov": "third", "tense": "past", "slips": ["a slip"]}],
                  "tics": [{"phrase": "grey", "count": 3, "sections": [1]}],
                  "dialogue_habits": [{"character": "Mara Voss", "habit": "short answers"}]}
    b["scenes"] = [{"section": 1, "n": 1, "purpose": "opens", "pov": "third", "tense": "past", "words": 9, "dialogue_share": 0}]
    for kind, spec in fixing.KINDS.items():
        if spec.get("one"):
            assert isinstance(fixing._holder(b, spec), dict), kind
        else:
            assert fixing.rows_of(b, kind), kind


def rich():
    b = fix_merge()
    b["structure"] = {"acts": [{"name": "Act one", "from": 1, "to": 2}],
                      "turning_points": [{"name": "The letter", "section": 1, "note": "It starts."}],
                      "midpoint": {"section": 1, "note": "m"}, "climax": {"section": 2, "note": "c"}}
    b["voice"] = {"by_section": [{"section": 1, "pov": "third", "tense": "past", "slips": ["a slip"]}],
                  "tics": [{"phrase": "grey", "count": 3, "sections": [1]}],
                  "dialogue_habits": [{"character": "Mara Voss", "habit": "short answers"}]}
    b["scenes"] = [{"section": 1, "n": 1, "purpose": "opens", "pov": "third", "tense": "past", "words": 9, "dialogue_share": 0}]
    b["summary"]["one_line"], b["summary"]["paragraph"] = "line", "para"
    b["setting"]["one_paragraph"] = "An island."
    b["briefs"]["themes"], b["cast_summary"] = "The themes brief.", "A small cast."
    return b


SET_EACH = [("character", "wants", "to stay"), ("character", "name", "Maren Voss"), ("place", "kind", "beacon"),
            ("event", "when", "winter"), ("event", "section", 2), ("thread", "name", "The note"), ("theme", "name", "exile"),
            ("world_rule", "rule", "The tide turns every six hours."), ("science", "plausible", "yes"),
            ("continuity", "note", "It sails on a Sunday."), ("history", "when", "ten years ago"),
            ("geography", "note", "three hours on foot"), ("scene", "purpose", "closes"),
            ("act", "name", "Act two"), ("turning_point", "note", "It ends."), ("midpoint", "note", "middle"),
            ("climax", "section", 1), ("setting", "era", "the 1930s"), ("voice_section", "tense", "present"),
            ("tic", "phrase", "gray"), ("habit", "habit", "long answers"), ("summary", "one_line", "new line"),
            ("section_summary", "summary", "new summary"),
            ("brief", "themes", "A new brief."), ("cast", "cast_summary", "A new cast.")]


@pytest.mark.parametrize("kind,field,value", SET_EACH)
def test_fix_set_on_every_kind(kind, field, value):
    import fixing
    b = rich()
    spec = fixing.KINDS[kind]
    e = fixing._holder(b, spec) if spec.get("one") else fixing.rows_of(b, kind)[0]
    k = (f"brief:{field}" if kind == "brief" else f"{kind}:") if spec.get("one") else key(kind, e)
    ops = hand(b, {"key": k, "action": "set", "field": field, "value": value})
    assert ops[0]["says"]
    done = fixing.apply_corrections(b, [op_rec(*ops)])
    assert done[0]["applied"] and done[0]["line"]
    got = fixing._holder(b, spec) if spec.get("one") else fixing.rows_of(b, kind)
    if spec.get("one"):
        assert got[field] == value
    else:
        assert any(x.get(field) == value for x in got)


REMOVE_EACH = ["character", "place", "event", "thread", "theme", "world_rule", "science", "continuity", "history", "geography",
               "scene", "act", "turning_point", "voice_section", "tic", "habit", "section_summary"]


@pytest.mark.parametrize("kind", REMOVE_EACH)
def test_fix_remove_on_every_list_stays_removed(kind):
    import fixing
    b = rich()
    e = fixing.rows_of(b, kind)[0]
    n = len(fixing.rows_of(b, kind))
    ops = hand(b, {"key": key(kind, e), "action": "remove"})
    done = fixing.apply_corrections(b, [op_rec(*ops)])
    assert done[0]["applied"] and len(fixing.rows_of(b, kind)) == n - 1
    again_b = rich()                                       # the same entry found again by a reading
    fixing.apply_corrections(again_b, [op_rec(*ops)])
    assert len(fixing.rows_of(again_b, kind)) == n - 1


ADD_EACH = [("character", {"name": "Ida Moss", "role": "aunt"}), ("place", {"name": "The Quay", "kind": "dock"}),
            ("event", {"what": "A bell rings.", "section": 2, "when": "dawn"}), ("thread", {"name": "The bell"}),
            ("theme", {"name": "grief"}), ("world_rule", {"rule": "Bells ring at dawn."}),
            ("science", {"claim": "Bells carry", "note": "They do."}), ("continuity", {"note": "A gap in the week."}),
            ("history", {"what": "The bell was cast.", "when": "long ago"}),
            ("geography", {"from": "Port Ash", "to": "The Quay", "note": "next door"}), ("act", {"name": "Act two", "from": 2, "to": 2}),
            ("turning_point", {"name": "The bell", "section": 2, "note": "It rings."}),
            ("habit", {"character": "Tom Reed", "habit": "whistles"})]


@pytest.mark.parametrize("kind,value", ADD_EACH)
def test_fix_add_on_every_list_once(kind, value):
    import fixing
    b = rich()
    n = len(fixing.rows_of(b, kind))
    ops = hand(b, {"key": f"{kind}:", "action": "add", "value": value})
    rec = op_rec(*ops)
    assert fixing.apply_corrections(b, [rec])[0]["applied"] and len(fixing.rows_of(b, kind)) == n + 1
    fixing.apply_corrections(b, [rec])                     # safe to run again
    assert len(fixing.rows_of(b, kind)) == n + 1
    from blocks import tab_blocks
    assert tab_blocks(b, fixing.KINDS[kind]["tab"])


def test_fix_lists_on_a_character_add_set_remove():
    import fixing
    b = fix_merge()
    mara = key("character", person(b, "mara"))
    look = fixing.fact_key("green eyes")
    ops = hand(b, {"key": mara, "action": "set", "field": "looks", "item": look, "value": "grey eyes"},
               {"key": mara, "action": "add", "field": "traits", "value": "shy"},
               {"key": mara, "action": "remove", "field": "traits", "item": "stubborn"},
               {"key": mara, "action": "set", "field": "age_words", "value": "a girl of thirteen"},
               {"key": mara, "action": "add", "field": "aliases", "value": "Red"})
    fixing.apply_corrections(b, [op_rec(*ops)])
    m = person(b, "mara")
    assert m["looks"] == [{"detail": "grey eyes", "section": 1, "quote": "", "corrected": True}]   # the section stays
    assert m["traits"] == ["loyal", "shy"] and m["age_words"] == {"words": "a girl of thirteen", "section": 1}
    assert "Red" in m["aliases"] and m["corrected"] is True


def test_fix_move_looks_with_section_and_quote_and_the_rest():
    import fixing
    b = fix_merge()
    elias, tom = key("character", person(b, "elias")), key("character", person(b, "tom"))
    ops = hand(b, {"key": elias, "action": "move", "field": "looks", "item": fixing.fact_key("a scar on his chin"), "to": tom},
               {"key": elias, "action": "move", "field": "traits", "item": "quiet", "to": tom},
               {"key": elias, "action": "move", "field": "wants", "to": tom},
               {"key": elias, "action": "move", "field": "fears", "to": tom},
               {"key": elias, "action": "move", "field": "appearances", "item": "2", "to": tom})
    assert "moved looks \"a scar on his chin\" from Elias Ward to Tom Reed" in ops[0]["says"]
    b2, done = again([op_rec(*ops)])
    e, t2 = person(b2, "elias"), person(b2, "tom")
    assert done[0]["applied"]
    assert t2["looks"] == [{"detail": "a scar on his chin", "section": 1, "quote": "the scar on his chin"}]
    assert [x["detail"] for x in e["looks"]] == ["a limp"] and t2["traits"] == ["quiet"] and e["traits"] == []
    assert t2["wants"] == "to keep the light" and e["wants"] == "" and t2["fears"] == "fire"
    assert 2 in t2["appearances"] and 2 not in e["appearances"]


def test_fix_move_a_relationship_turns_the_other_side_too():
    import fixing
    b = fix_merge()
    person(b, "elias")["relationships"] = [{"to": "mara", "kind": "uncle", "note": ""}]
    person(b, "elias")["family"]["children"] = ["mara"]
    person(b, "mara")["family"]["parents"] = ["elias"]
    mara = person(b, "mara")
    item = fixing.item_key("rels", mara["relationships"][0])
    ops = hand(b, {"key": key("character", mara), "action": "move", "field": "relationships", "item": item,
                   "to": key("character", person(b, "tom"))})
    fixing.apply_corrections(b, [op_rec(*ops)])
    assert person(b, "mara")["relationships"] == []
    assert person(b, "tom")["relationships"][0]["to"] == "elias"
    assert person(b, "elias")["relationships"][0]["to"] == "tom"
    assert person(b, "elias")["family"]["children"] == ["tom"]


def test_fix_merge_two_people_and_two_places_and_rename():
    import fixing
    b = fix_merge()
    ops = hand(b, {"key": key("character", person(b, "annie")), "action": "merge", "to": key("character", person(b, "anna"))})
    assert "one character now" in ops[0]["says"]
    b["places"].append({"id": "lamp-house", "name": "Lamp House", "kind": "", "notes": "", "where": "", "appearances": [2],
                        "details": [{"detail": "a red door", "section": 2, "quote": ""}]})
    b["events"][1]["where"] = "lamp-house"
    b["geography"].append({"from": "Lamp House", "to": "Port Ash", "note": "close", "section": 2, "quote": ""})
    spot = next(p for p in b["places"] if p["id"] == "the-lighthouse")
    ops += hand(b, {"key": key("place", b["places"][-1]), "action": "merge", "to": key("place", spot)})
    ops += hand(b, {"key": key("character", person(b, "mara")), "action": "set", "field": "name", "value": "Maren Voss"})
    fixing.apply_corrections(b, [op_rec(*ops)])
    anna = person(b, "anna")
    assert not any(c["id"] == "annie" for c in b["characters"]) and "Annie" in anna["aliases"]
    assert anna["traits"] == ["kind", "loud"] and anna["appearances"] == [1, 2]
    assert b["events"][1]["where"] == "the-lighthouse" and not any(p["id"] == "lamp-house" for p in b["places"])
    assert {x["detail"] for x in next(p for p in b["places"] if p["id"] == "the-lighthouse")["details"]} >= {"a red door"}
    assert next(g for g in b["geography"] if g["note"] == "close")["from"] == "The Lighthouse"
    assert person(b, "mara")["name"] == "Maren Voss"


def test_fix_survives_a_reread_by_the_same_operations():
    import fixing
    b = fix_merge()
    elias = key("character", person(b, "elias"))
    ops = hand(b, {"key": elias, "action": "move", "field": "looks", "item": fixing.fact_key("a scar on his chin"),
                   "to": key("character", person(b, "tom"))},
               {"key": key("character", person(b, "mara")), "action": "remove", "field": "looks",
                "item": fixing.fact_key("green eyes")})
    rec = op_rec(*ops)
    b2, _ = again([rec])
    b3, _ = again([rec])
    assert b2 == b3                                        # the same every time, and no brain anywhere near it
    assert person(b2, "mara")["looks"] == []
    fixing.apply_corrections(b2, [rec])                    # and safe to run again on the same bible
    assert person(b2, "mara")["looks"] == [] and len(person(b2, "tom")["looks"]) == 1


def test_fix_removed_look_stays_removed_in_other_small_words():
    import fixing
    b = fix_merge()
    ops = hand(b, {"key": key("character", person(b, "mara")), "action": "remove", "field": "looks",
                   "item": fixing.fact_key("green eyes")})
    again_b = fix_merge()
    person(again_b, "mara")["looks"] = [{"detail": "her green eyes", "section": 3, "quote": ""}]
    fixing.apply_corrections(again_b, [op_rec(*ops)])
    assert person(again_b, "mara")["looks"] == []


def test_fix_target_gone_after_a_reread_stays_as_a_note():
    import fixing
    b = fix_merge()
    ops = hand(b, {"key": key("character", person(b, "tom")), "action": "set", "field": "wants", "value": "to sail"})
    gone = fix_merge()
    gone["characters"] = [c for c in gone["characters"] if c["id"] != "tom"]
    done = fixing.apply_corrections(gone, [op_rec(*ops, what="Tom wants to sail")])
    assert done[0]["what"] == "Tom wants to sail" and done[0]["applied"] is False and "no longer" in done[0]["why"]


def test_fix_old_records_work_as_before():
    import fixing
    b = fix_merge()
    old = [{"id": "c-1", "what": "Tom is a minor character", "correction": "", "made_at": "t"},
           {"id": "c-2", "what": "The Lighthouse is a village", "correction": "", "made_at": "t"},
           {"id": "c-3", "what": "The sky is green", "correction": "", "made_at": "t"}]
    done = fixing.apply_corrections(b, old)
    assert [d["applied"] for d in done] == [True, True, False]
    assert done[0]["line"] == "Tom Reed is a minor character now" and person(b, "tom")["importance"] == "minor"
    assert next(p for p in b["places"] if p["id"] == "the-lighthouse")["kind"] == "village"
    assert done[2]["tab"] == "characters" and "tabs" in done[2]


def test_fix_rules_only_take_a_plain_shape():
    from writer import rule_plan
    b = fix_merge()
    assert rule_plan(b, "Tom Reed is a minor character")["line"] == "Tom Reed is a minor character now"
    assert rule_plan(b, "Mara is Elias's cousin, not his niece")["line"] == "Mara Voss is Elias Ward's cousin now"
    assert rule_plan(b, "Tom is also called Bear")["line"] == "Tom Reed is also called Bear now"
    assert rule_plan(b, "Port Ash is a village, not a town")["line"] == "Port Ash is a village now"
    for loose in ("Mara is scared of the main road", "Elias's sister Mara never met Tom", "Mara has grey eyes, not green",
                  "Anna and Annie are the same person", "Tom's mother is Anna, not Mara"):
        assert rule_plan(b, loose) is None, loose


# what the brain's answer may and may not do


def plan(answer, words):
    import fixing
    b = fix_merge()
    view, refs = fixing.build_view(b, words)
    return b, view, refs, fixing.read_answer(b, answer(refs, b) if callable(answer) else answer, refs, words)


def ref_of(refs, text):
    return next(k for k, v in refs.items() if text in str(v.get("text") or ""))


def char_ref(refs, b, cid):
    return next(k for k, v in refs.items() if v.get("e") is person(b, cid) and not v.get("field"))


def test_fix_view_is_small_and_names_what_matters():
    import analysis
    import fixing
    b = fix_merge()
    for i in range(300):
        b["events"].append({"id": f"z{i}", "section": 1, "order": i, "what": f"Filler event number {i} with storm in it",
                            "who": [], "where": "", "when": "", "placement": "", "story_order": i})
    view, refs = fixing.build_view(b, "Mara has grey eyes, not green")
    assert "green eyes" in view and "write_up" not in view and analysis.tokens(view) < 3000
    view, refs = fixing.build_view(b, "the storm is in chapter 2, not chapter 1")
    assert analysis.tokens(view) <= fixing.VIEW_ROOM + 50 and "Sections, by number" in view


def test_fix_good_answers_become_operations():
    _b, _v, _r, (ops, dropped) = plan(lambda r, b: {"ops": [{"do": "change", "ref": ref_of(r, "green eyes"), "to": "grey eyes"}]},
                                      "Mara has grey eyes, not green")
    assert dropped == 0 and ops[0]["action"] == "set" and ops[0]["value"] == "grey eyes" and ops[0]["old"] == "green eyes"
    _b, _v, _r, (ops, dropped) = plan(lambda r, b: {"ops": [{"do": "move", "ref": ref_of(r, "a scar"), "owner": char_ref(r, b, "tom")}]},
                                      "that scar belongs to Tom, not Elias")
    assert dropped == 0 and ops[0]["action"] == "move" and ops[0]["to"]["id"] == "tom"
    _b, _v, _r, (ops, dropped) = plan(lambda r, b: {"ops": [{"do": "merge", "ref": char_ref(r, b, "annie"), "into": char_ref(r, b, "anna")}]},
                                      "Anna and Annie are the same person")
    assert dropped == 0 and ops[0]["action"] == "merge"


def test_fix_section_means_her_chapter():
    import fixing
    b = fix_merge()
    b["sections"] = [{"n": 0, "heading": "Front", "words": 1}, {"n": 1, "heading": "Chapter One", "words": 1},
                     {"n": 2, "heading": "Chapter Two", "words": 1}]
    words = "the storm is in chapter 2, not chapter 1"
    _v, refs = fixing.build_view(b, words)
    r = ref_of(refs, "section: 1")
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": r, "to": 2}]}, refs, words)
    assert not dropped and ops[0]["value"] == 2
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": r, "to": 1}]}, refs, words)
    assert dropped == 1 and not ops


@pytest.mark.parametrize("words,answer", [
    ("Mara has grey eyes, not green", lambda r, b: {"ops": [{"do": "change", "ref": "C99.looks.1", "to": "grey eyes"}]}),   # not in the bible
    ("Mara has grey eyes, not green", lambda r, b: {"ops": [{"do": "change", "ref": ref_of(r, "green eyes"), "to": "green eyes"}]}),  # what she denied
    ("Mara has grey eyes, not green", lambda r, b: {"ops": [{"do": "change", "ref": ref_of(r, "green eyes"), "to": "bright blue eyes"}]}),  # the model's own
    ("Mara has grey eyes, not green", lambda r, b: {"ops": [{"do": "change", "ref": ref_of(r, "to leave the island"), "to": "grey eyes"}]}),  # the wrong field
    ("that scar belongs to Tom, not Elias", lambda r, b: {"ops": [{"do": "move", "ref": ref_of(r, "a scar"), "owner": char_ref(r, b, "elias")}]}),  # backwards
    ("that scar belongs to Tom, not Elias", lambda r, b: {"ops": [{"do": "move", "ref": ref_of(r, "a scar"), "owner": char_ref(r, b, "mara")}]}),  # someone she did not name
    ("that scar belongs to Tom, not Elias", lambda r, b: {"ops": [{"do": "merge", "ref": char_ref(r, b, "tom"), "into": char_ref(r, b, "elias")}]}),  # merged the two
    ("Anna is not Annie", lambda r, b: {"ops": [{"do": "merge", "ref": char_ref(r, b, "annie"), "into": char_ref(r, b, "anna")}]}),
    ("Mara has grey eyes", lambda r, b: {"ops": [{"do": "remove", "ref": ref_of(r, "green eyes")}]}),  # no word of removing
    ("Mara has grey eyes, not green", lambda r, b: {"ops": [{"do": "add", "on": char_ref(r, b, "mara"), "field": "traits", "to": "cheerful"}]}),
    ("Mara has grey eyes, not green", lambda r, b: {"ops": [{"do": "new", "kind": "character", "to": {"name": "Zed"}}]}),
    ("Mara has grey eyes, not green", lambda r, b: {"ops": [{"do": "explode", "ref": "C1"}]}),
    ("the ferry sinks in chapter 2, not chapter 1", lambda r, b: {"ops": [{"do": "change", "ref": "E9.section", "to": 2}]}),
])
def test_fix_bad_answers_are_dropped(words, answer):
    _b, _v, _r, (ops, dropped) = plan(answer, words)
    assert ops == [] and dropped == 1


@pytest.mark.parametrize("answer", ["not json at all", "", '{"ops": "none"}', "[1, 2, 3]", '{"ops":[{"ref": 7}]}', "null"])
def test_fix_awkward_answers_leave_nothing_changed(answer):
    import analysis
    import fixing
    b = fix_merge()
    before = json.dumps(b, sort_keys=True)

    class Brain:
        def chat(self, messages, patient=True):
            return answer, "m"

    got = fixing.plan_words(Brain(), b, "Mara has grey eyes, not green")
    assert got["ops"] == [] and got["why"] and json.dumps(b, sort_keys=True) == before
    assert analysis.parse_json("") is None


def test_fix_answer_in_a_fence_with_thinking_still_reads():
    import fixing
    b = fix_merge()

    class Brain:
        calls = 0

        def chat(self, messages, patient=True):
            Brain.calls += 1
            _v, refs = fixing.build_view(b, "Mara has grey eyes, not green")
            r = ref_of(refs, "green eyes")
            text = json.dumps({"ops": [{"do": "change", "ref": r, "to": "grey eyes"}]})
            return ("Sure! {broken" if Brain.calls == 1 else f"<think>hm</think>```json\n{text}\n```"), "m"

    got = fixing.plan_words(Brain(), b, "Mara has grey eyes, not green")
    assert Brain.calls == 2 and got["ops"][0]["value"] == "grey eyes" and "green eyes" in got["line"]


# who a fact lands on (the merge)


def test_merge_two_full_names_stay_two_people():
    import analysis
    b = analysis.merge("T", [(1, "a"), (2, "b")], {
        1: {"characters": [{"name": "Tom Hale", "looks": [{"detail": "red hair"}]}]},
        2: {"characters": [{"name": "Tom Ross", "looks": [{"detail": "a black beard"}]}]}})
    assert [c["name"] for c in b["characters"]] == ["Tom Hale", "Tom Ross"]
    assert [x["detail"] for x in b["characters"][1]["looks"]] == ["a black beard"]


def test_merge_first_name_still_joins_one_person():
    import analysis
    b = analysis.merge("T", [(1, "a"), (2, "b")], {1: {"characters": [{"name": "Jennifer Hale"}]},
                                                   2: {"characters": [{"name": "Jennifer", "aliases": ["Jen"]}]}})
    assert [c["name"] for c in b["characters"]] == ["Jennifer Hale"] and "Jen" in b["characters"][0]["aliases"]


def test_merge_a_pronoun_is_nobody():
    import analysis
    b = analysis.merge("T", [(1, "a"), (2, "b")], {
        1: {"characters": [{"name": "Mara", "aliases": ["she", "her"], "looks": [{"detail": "green eyes"}]},
                           {"name": "she", "traits": ["tall"], "looks": [{"detail": "a long scar"}]}]},
        2: {"characters": [{"name": "He", "looks": [{"detail": "a limp"}]}]}})
    assert [c["name"] for c in b["characters"]] == ["Mara"]
    assert [x["detail"] for x in b["characters"][0]["looks"]] == ["green eyes"] and b["characters"][0]["traits"] == []
    assert b["characters"][0]["aliases"] == []


def test_merge_a_description_is_no_alias_across_people():
    import analysis
    b = analysis.merge("T", [(1, "a"), (2, "b")], {
        1: {"characters": [{"name": "Elias Ward", "aliases": ["the doctor"], "looks": [{"detail": "a scar"}]}]},
        2: {"characters": [{"name": "Thomas Reed", "aliases": ["the doctor"], "looks": [{"detail": "a limp"}]},
                           {"name": "Elias", "aliases": ["Doc"]}, {"name": "Rose Lane", "aliases": ["Doc"]}]}})
    assert [c["name"] for c in b["characters"]] == ["Elias Ward", "Thomas Reed", "Rose Lane"]
    assert [x["detail"] for x in b["characters"][1]["looks"]] == ["a limp"]


def test_merge_the_name_itself_comes_before_an_alias():
    import analysis
    b = analysis.merge("T", [(1, "a"), (2, "b")], {
        1: {"characters": [{"name": "Elias", "aliases": ["Red"]}, {"name": "Tom"}]},
        2: {"characters": [{"name": "Tom", "aliases": ["Red"], "looks": [{"detail": "a limp"}]}]}})
    tom = next(c for c in b["characters"] if c["name"] == "Tom")
    assert [x["detail"] for x in tom["looks"]] == ["a limp"]


def test_extract_ask_says_whose_a_look_is():
    import analysis
    assert "you can't tell who, leave it out" in analysis.EXTRACT_LOOKS_SYSTEM and "never he, she or they" in analysis.EXTRACT_SYSTEM


# the server


@pytest.fixture(scope="module")
def fix_env(tmp_path_factory):
    root = tmp_path_factory.mktemp("fixdata")
    proc, core, port = start_server(root)
    try:
        code, got = call(port, "POST", "/api/upload", {"file_name": "fix.md", "title": "Fix",
                                                       "content_b64": base64.b64encode(FIX_BOOK.encode()).decode()})
        assert code == 200, got
        wait_done(port, "Fix")
        folder = root / "My files" / "Manuscripts" / "Fix"
        shas = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob("sections/*")}
        shas["original.md"] = hashlib.sha256((folder / "original.md").read_bytes()).hexdigest()
        yield {"port": port, "core": core, "folder": folder, "shas": shas}
    finally:
        stop_server(proc, core)


def bible_file(fix_env):
    return json.loads((fix_env["folder"] / "bible.json").read_text(encoding="utf-8"))


def no_section_changed(fix_env):
    f = fix_env["folder"]
    now = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in f.glob("sections/*")}
    now["original.md"] = hashlib.sha256((f / "original.md").read_bytes()).hexdigest()
    assert now == fix_env["shas"]


def wait_fix(port, title, rid, tab="characters", seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        got = call(port, "GET", "/api/tab" + q(title=title, tab=tab))[1]
        c = next((x for x in got["corrections"] if x["id"] == rid), None)
        if c and c["state"] != "working" and not got["rewriting"] and c.get("prose_state") != "rewriting":
            return c, got
        time.sleep(0.1)
    raise AssertionError(f"{rid} was not done: {c}")


def view_ref(fix_env, words, text, tab=""):
    import fixing
    _v, refs = fixing.build_view(bible_file(fix_env), words, tab)
    return ref_of(refs, text)


def char_view(fix_env, words, cid):
    import fixing
    b = bible_file(fix_env)
    _v, refs = fixing.build_view(b, words)
    return next(k for k, v in refs.items() if v["e"].get("id") == cid and not v.get("field"))


def test_correct_in_her_words_then_undo_restores(fix_env):
    port, core = fix_env["port"], fix_env["core"]
    before = (fix_env["folder"] / "bible.base.json").read_bytes()
    bib = bible_file(fix_env)
    words = "Mara has grey eyes, not green"
    core.fix_answer = json.dumps({"ops": [{"do": "change", "ref": view_ref(fix_env, words, "green eyes"), "to": "grey eyes"}]})
    core.prose = {"people": [{"name": "Mara Voss", "personality": "Steady.", "write_up": "Mara, with her grey eyes, leads [1]."}]}
    n = len(core.calls)
    code, got = call(port, "POST", "/api/correct", {"title": "Fix", "what": words, "tab": "characters"})
    assert code == 200 and got["correction"]["applied"], got
    assert "green eyes" in got["message"] and "grey eyes" in got["message"]
    assert "Paint again" in got["message"]                   # a look changed
    assert "the fix" not in core.log[n:]
    c, tab = wait_fix(port, "Fix", got["correction"]["id"])
    assert "You" not in c["line"] and "grey eyes" in c["line"] and c["applied"]
    new = bible_file(fix_env)
    mara = person(new, "mara")
    assert [x["detail"] for x in mara["looks"]] == ["grey eyes"] and mara["corrected"] is True
    assert mara["write_up"] == "Mara, with her grey eyes, leads [1]."      # the paragraph followed the fix
    assert (fix_env["folder"] / "bible.base.json").read_bytes() == before  # the bible as read is untouched
    no_section_changed(fix_env)
    line, err = tool(port, "manuscript_correct_undo", title="Fix", id="last")
    assert not err and line.startswith("Took that back") and "as it was" in line
    back = bible_file(fix_env)
    assert person(back, "mara") == person(bib, "mara")       # looks and write-up, as they were, with no re-read
    corr = json.loads((fix_env["folder"] / "corrections.json").read_text(encoding="utf-8"))["corrections"]
    assert not any(x["what"] == words for x in corr)
    no_section_changed(fix_env)
    core.prose = None


def test_correct_answer_shows_on_each_tab_it_touched_and_tools(fix_env):
    port, core = fix_env["port"], fix_env["core"]
    words = "that scar belongs to Tom, not Elias"
    core.fix_answer = json.dumps({"ops": [{"do": "move", "ref": view_ref(fix_env, words, "a scar"),
                                           "owner": char_view(fix_env, words, "tom")}]})
    line, err = tool(port, "manuscript_correct", title="Fix", what=words)
    assert not err and line.startswith("Changed it: moved looks") and "from Elias Ward to Tom Reed" in line
    assert "Paint again" in line
    rid = line[line.rindex("[") + 1:-1]
    c, tab = wait_fix(port, "Fix", rid)
    assert c["applied"]
    listed, _err = tool(port, "manuscript_corrections", title="Fix")
    assert rid in listed and "from Elias Ward to Tom Reed" in listed
    b = bible_file(fix_env)
    assert any(x["detail"] == "a scar on his chin" and x["quote"] == "the scar on his chin" and x["section"] == 1
               for x in person(b, "tom")["looks"])
    assert not any(x["detail"] == "a scar on his chin" for x in person(b, "elias")["looks"])
    # Maps paints from bible.json, so the moved look is in Tom's next portrait and its proof, and out of Elias's
    sys.path.insert(0, str(PKG.parent / "maps"))
    try:
        import pictures
        tom_prompt, _about, proof = pictures.prompt_and_proof(b, "portrait", "tom", "")
        eli_prompt, _about, _p = pictures.prompt_and_proof(b, "portrait", "elias", "")
    finally:
        sys.path.remove(str(PKG.parent / "maps"))
    assert "a scar on his chin" in tom_prompt and "a scar on his chin" not in eli_prompt
    assert any(e["detail"] == "a scar on his chin" and e["section"] == 1 for e in proof)
    line, _err = tool(port, "manuscript_correct_undo", title="Fix", id=rid)
    assert line.startswith("Took that back")
    assert any(x["detail"] == "a scar on his chin" for x in person(bible_file(fix_env), "elias")["looks"])
    assert tool(port, "manuscript_correct_undo", title="Fix", id="c-nothing")[1] is True


def test_correct_model_naming_nothing_real_stays_a_note(fix_env):
    port, core = fix_env["port"], fix_env["core"]
    before = person(bible_file(fix_env), "mara")
    core.fix_answer = json.dumps({"ops": [{"do": "change", "ref": "C77.looks.9", "to": "grey eyes"}]})
    code, got = call(port, "POST", "/api/correct", {"title": "Fix", "what": "Mara has grey eyes, not green", "tab": "science"})
    assert code == 200 and not got["correction"]["applied"]
    assert 'You said: Mara has grey eyes, not green' in got["message"] and "nothing changed" in got["message"]
    assert person(bible_file(fix_env), "mara") == before
    sci = call(port, "GET", "/api/tab" + q(title="Fix", tab="science"))[1]
    c = next(x for x in sci["corrections"] if x["what"] == "Mara has grey eyes, not green")   # shown where she said it
    assert c["applied"] is False and c["why"]
    core.fix_answer = "I am sorry, I cannot do that."
    code, got = call(port, "POST", "/api/correct", {"title": "Fix", "what": "Mara is sad"})
    assert code == 200 and not got["correction"]["applied"] and "You said: Mara is sad" in got["message"]
    # the note gets to the ask
    n = len(core.calls)
    answer, _err = tool(port, "manuscript_ask", title="Fix", question="What colour are Mara's eyes?")
    assert answer
    user = next(m for m in core.calls[n:] if "think about her own manuscript" in m[0]["content"])[1]["content"]
    assert "The writer said: Mara is sad" in user and "green eyes" in user
    for rec in list(json.loads((fix_env["folder"] / "corrections.json").read_text(encoding="utf-8"))["corrections"]):
        tool(port, "manuscript_correct_undo", title="Fix", id=rec["id"])
    core.fix_answer = "{}"


def test_ask_and_views_answer_from_the_corrected_bible(fix_env):
    port, core = fix_env["port"], fix_env["core"]
    b = bible_file(fix_env)
    from blocks import entry_key
    got = call(port, "POST", "/api/edit", {"title": "Fix", "ops": [
        {"key": entry_key("character", person(b, "mara")), "action": "set", "field": "looks",
         "item": "eyes green", "value": "violet eyes"}]})[1]
    rid = got["correction"]["id"]
    assert got["correction"]["applied"] and "violet eyes" in got["message"]
    n = len(core.calls)
    tool(port, "manuscript_ask", title="Fix", question="What colour are Mara's eyes?")
    user = next(m for m in core.calls[n:] if "think about her own manuscript" in m[0]["content"])[1]["content"]
    assert "looks: violet eyes" in user and "looks: green eyes" not in user
    n = len(core.calls)
    line, view = call(port, "POST", "/api/show", {"title": "Fix", "tab": "characters", "asked": "show me Mara's looks"})[1].values()
    wait_view(port, "Fix", view["id"])
    m = next(m for m in core.calls[n:] if "make one view" in m[0]["content"])
    assert "violet eyes" in m[1]["content"] and "settled" in m[0]["content"]
    call(port, "DELETE", "/api/views" + q(title="Fix", id=view["id"]))
    call(port, "POST", "/api/correct/undo", {"title": "Fix", "id": rid})


def test_hand_edit_has_no_brain_call_and_keeps_its_prose(fix_env):
    port, core = fix_env["port"], fix_env["core"]
    b = bible_file(fix_env)
    from blocks import entry_key
    mara = entry_key("character", person(b, "mara"))
    n = len(core.calls)
    code, got = call(port, "POST", "/api/edit", {"title": "Fix", "ops": [
        {"key": mara, "action": "set", "field": "write_up", "value": "My own words on Mara."},
        {"key": mara, "action": "set", "field": "wants", "value": "to stay"}]})
    assert code == 200 and got["correction"]["by"] == "hand" and got["correction"]["applied"]
    time.sleep(0.3)
    assert len(core.calls) == n                             # no brain call
    hand_id = got["correction"]["id"]
    assert person(bible_file(fix_env), "mara")["write_up"] == "My own words on Mara."
    # a correction after it refreshes the paragraph, but never over her own
    words = "Mara has grey eyes, not green"
    core.fix_answer = json.dumps({"ops": [{"do": "change", "ref": view_ref(fix_env, words, "green eyes"), "to": "grey eyes"}]})
    core.prose = {"people": [{"name": "Mara Voss", "personality": "New personality.", "write_up": "The brain's words."}]}
    got = call(port, "POST", "/api/correct", {"title": "Fix", "what": words})[1]
    c, _tab = wait_fix(port, "Fix", got["correction"]["id"])
    m = person(bible_file(fix_env), "mara")
    assert m["write_up"] == "My own words on Mara." and m["personality"] == "New personality."
    # and a re-read keeps it too; the reading's own synthesis is told the corrections are settled
    n = len(core.calls)
    tool(port, "manuscript_analyze", title="Fix", confirm=True)
    time.sleep(0.2)
    wait_done(port, "Fix")
    m = person(bible_file(fix_env), "mara")
    assert m["write_up"] == "My own words on Mara." and [x["detail"] for x in m["looks"]] == ["grey eyes"]
    assert m["wants"] == "to stay"
    syn = [x for x in core.calls[n:] if "see her characters" in x[0]["content"]]
    assert syn and "They are settled" in syn[0][0]["content"] and "Mara has grey eyes, not green" in syn[0][0]["content"]
    assert "green eyes" not in syn[0][1]["content"]
    base = json.loads((fix_env["folder"] / "bible.base.json").read_text(encoding="utf-8"))
    assert [x["detail"] for x in person(base, "mara")["looks"]] == ["green eyes"]   # undo still has what to go back to
    no_section_changed(fix_env)
    for rid in (got["correction"]["id"], hand_id):
        call(port, "POST", "/api/correct/undo", {"title": "Fix", "id": rid})
    assert [x["detail"] for x in person(bible_file(fix_env), "mara")["looks"]] == ["green eyes"]
    core.prose = None
    core.fix_answer = "{}"


def test_prose_the_brain_cant_fix_says_so(fix_env):
    port, core = fix_env["port"], fix_env["core"]
    was = person(bible_file(fix_env), "mara")["write_up"]
    words = "Mara has grey eyes, not green"
    core.fix_answer = json.dumps({"ops": [{"do": "change", "ref": view_ref(fix_env, words, "green eyes"), "to": "grey eyes"}]})
    core.prose = {"people": [{"name": "Mara Voss", "personality": "P.", "write_up": "Mara and her green eyes lead."}]}
    got = call(port, "POST", "/api/correct", {"title": "Fix", "what": words})[1]
    rid = got["correction"]["id"]
    c, tab = wait_fix(port, "Fix", rid)
    assert c["prose_state"] == "failed" and c["applied"]
    assert person(bible_file(fix_env), "mara")["write_up"] == was   # it still said the wrong thing, so it was not kept
    call(port, "POST", "/api/correct/undo", {"title": "Fix", "id": rid})
    core.prose = None
    core.fix_answer = "{}"


def test_tab_answer_has_what_the_edit_controls_need(fix_env):
    port = fix_env["port"]
    for tab in ("characters", "plot", "timeline", "structure", "world", "science", "themes", "voice"):
        got = call(port, "GET", "/api/tab" + q(title="Fix", tab=tab))[1]
        ed = got["edits"]
        keyed = [k for b in got["blocks"] for k in (b.get("keys") or [it.get("key") for it in b.get("items") or [] if isinstance(it, dict)])
                 if k]
        assert all(k in ed["items"] for k in keyed), tab
    chars = call(port, "GET", "/api/tab" + q(title="Fix", tab="characters"))[1]
    card = chars["cards"][0]
    item = chars["edits"]["items"][card["key"]]
    assert {f["name"] for f in item["fields"]} >= {"name", "role", "importance", "aliases", "traits", "wants", "fears", "age_words",
                                                    "looks", "personality", "write_up", "relationships"}
    looks = next(f for f in item["fields"] if f["name"] == "looks")
    assert looks["moves"] and looks["items"][0]["key"]
    assert chars["block_keys"]["0"] == "brief:characters"
    assert [a["kind"] for a in chars["edits"]["adds"]] == ["character"]


def test_correct_before_any_reading_is_a_note_with_no_brain(tmp_path_factory):
    root = tmp_path_factory.mktemp("noread")
    proc, core, port = start_server(root)
    try:
        folder = root / "My files" / "Manuscripts" / "Plain"
        (folder / "sections").mkdir(parents=True)
        (folder / "sections" / "01 - One.md").write_text("# One\n\nText.\n", encoding="utf-8")
        (folder / "manuscript.json").write_text(json.dumps({"title": "Plain", "sections": [
            {"n": 1, "heading": "One", "file": "01 - One.md", "words": 1, "sha": "x"}]}), encoding="utf-8")
        code, got = call(port, "POST", "/api/correct", {"title": "Plain", "what": "Mara has grey eyes"})
        assert code == 200 and not got["correction"]["applied"] and "You said" in got["message"]
        assert core.calls == []
    finally:
        stop_server(proc, core)


def test_old_book_gets_a_base_and_old_record_undo_says_so(tmp_path_factory):
    root = tmp_path_factory.mktemp("oldbook")
    proc, core, port = start_server(root)
    try:
        folder = root / "My files" / "Manuscripts" / "Old"
        (folder / "sections").mkdir(parents=True)
        (folder / "sections" / "01 - One.md").write_text("# One\n\nText.\n", encoding="utf-8")
        (folder / "manuscript.json").write_text(json.dumps({"title": "Old", "sections": [
            {"n": 1, "heading": "One", "file": "01 - One.md", "words": 1, "sha": "x"}]}), encoding="utf-8")
        b = fix_merge()
        person(b, "tom")["importance"] = "minor"
        (folder / "bible.json").write_text(json.dumps(b), encoding="utf-8")
        (folder / "corrections.json").write_text(json.dumps({"corrections": [
            {"id": "c-old", "what": "Tom is a minor character", "correction": "", "made_at": "2026-10-31T00:00:00Z"}]}), encoding="utf-8")
        tab = call(port, "GET", "/api/tab" + q(title="Old", tab="characters"))[1]
        assert [c["applied"] for c in tab["corrections"]] == [True]
        assert not (folder / "bible.base.json").exists()    # reading a tab writes nothing
        from blocks import entry_key
        got = call(port, "POST", "/api/edit", {"title": "Old", "ops": [
            {"key": entry_key("character", person(b, "mara")), "action": "set", "field": "wants", "value": "to stay"}]})[1]
        assert got["correction"]["applied"] and (folder / "bible.base.json").exists()
        line, _err = tool(port, "manuscript_correct_undo", title="Old", id="c-old")
        assert "before Undo existed" in line
        line, _err = tool(port, "manuscript_correct_undo", title="Old", id="last")
        assert person(json.loads((folder / "bible.json").read_text(encoding="utf-8")), "mara")["wants"] == "to leave the island"
    finally:
        stop_server(proc, core)


def test_correction_during_a_reading_comes_through(tmp_path_factory):
    root = tmp_path_factory.mktemp("during")
    proc, core, port = start_server(root, MANUSCRIPT_CORRECT_WAIT_S="0.1")
    try:
        slow = "# One\n\nFIXONE SLOW. Mara Voss looked out.\n\n# Two\n\nFIXTWO SLOW. Mara read the letter.\n"
        code, got = call(port, "POST", "/api/upload", {"file_name": "slow.md", "title": "Slow",
                                                       "content_b64": base64.b64encode(slow.encode()).decode()})
        assert code == 200
        folder = root / "My files" / "Manuscripts" / "Slow"
        end = time.time() + 20
        while time.time() < end:
            b = json.loads((folder / "bible.json").read_text(encoding="utf-8")) if (folder / "bible.json").exists() else {}
            if b.get("characters"):
                break
            time.sleep(0.1)
        assert b.get("characters")
        words = "Mara has grey eyes, not green"
        import fixing
        _v, refs = fixing.build_view(b, words)
        core.fix_answer = json.dumps({"ops": [{"do": "change", "ref": ref_of(refs, "green eyes"), "to": "grey eyes"}]})
        code, got = call(port, "POST", "/api/correct", {"title": "Slow", "what": words, "tab": "characters"})
        assert code == 200 and got["correction"]["state"] == "working", got
        assert "working that out" in got["message"] and "Nothing has changed yet" in got["message"]
        rid = got["correction"]["id"]
        c, _tab = wait_fix(port, "Slow", rid, seconds=30)
        assert c["applied"]
        st = wait_done(port, "Slow")
        assert st["state"] == "done"
        c, _tab = wait_fix(port, "Slow", rid, seconds=30)    # the paragraphs wait for the reading's end
        b = json.loads((folder / "bible.json").read_text(encoding="utf-8"))
        assert [x["detail"] for x in person(b, "mara")["looks"]] == ["grey eyes"]
    finally:
        stop_server(proc, core)


def test_undo_while_the_brain_works_drops_it(tmp_path_factory):
    root = tmp_path_factory.mktemp("undoworking")
    proc, core, port = start_server(root, MANUSCRIPT_CORRECT_WAIT_S="0")
    try:
        call(port, "POST", "/api/upload", {"file_name": "fix.md", "title": "Fix2",
                                           "content_b64": base64.b64encode(FIX_BOOK.encode()).decode()})
        wait_done(port, "Fix2")
        folder = root / "My files" / "Manuscripts" / "Fix2"
        before = person(json.loads((folder / "bible.json").read_text(encoding="utf-8")), "mara")
        got = call(port, "POST", "/api/correct", {"title": "Fix2", "what": "Mara is slowly going grey"})[1]
        call(port, "POST", "/api/correct/undo", {"title": "Fix2", "id": got["correction"]["id"]})
        time.sleep(0.5)
        assert json.loads((folder / "corrections.json").read_text(encoding="utf-8"))["corrections"] == []
        assert person(json.loads((folder / "bible.json").read_text(encoding="utf-8")), "mara") == before
    finally:
        stop_server(proc, core)


def test_manifest_and_tools_agree_on_the_new_tools():
    manifest = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(PKG))
    import server
    assert manifest["version"] == server.VERSION == "0.5.1"
    assert {"manuscript_correct_undo", "manuscript_corrections"} <= set(manifest["abilities"]["tools"])
    assert set(manifest["abilities"]["tools"]) == set(server.TOOLS)
    assert "any fact" in manifest["abilities"]["tools"]["manuscript_correct"]
    assert "manuscript_correct_undo" in server.TOOLS and "last" in server.TOOLS["manuscript_correct_undo"][0]


def test_skill_stays_under_the_cap_and_says_the_rules():
    text = (PKG.parent / "novel-mode" / "SKILL.md").read_text(encoding="utf-8")
    body = re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.DOTALL)
    assert len(body) < 7000
    assert "manuscript_correct_undo" in body and "never say something changed" in body.lower()
    assert "\u2014" not in text


def test_fix_a_section_number_alone_is_not_the_entry_she_means():
    """The worst answer the real model gave: it moved the wrong event, because both were in the section she named."""
    import fixing
    b = fix_merge()
    words = "the storm is in chapter 2, not chapter 1"
    view, refs = fixing.build_view(b, words)
    assert "storm" in view and "Elias climbs the lighthouse at night" not in view     # the other event is not even shown
    wrong = {"kind": "event", "e": next(e for e in b["events"] if "climbs" in e["what"]), "field": "section",
             "item": None, "text": "section: 1"}
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": "wrong", "to": 2}]}, {"WRONG": wrong}, words)
    assert not ops and dropped == 1
    right = next(k for k, v in refs.items() if v["kind"] == "event" and v.get("field") == "section")
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": right, "to": 2}]}, refs, words)
    assert ops[0]["value"] == 2 and "storm" in ops[0]["says"] and not dropped


def test_fix_a_fact_on_someone_she_did_not_name_is_dropped():
    import fixing
    b = fix_merge()
    person(b, "tom")["looks"] = [{"detail": "green eyes", "section": 1, "quote": ""}]
    words = "Mara has grey eyes, not green"
    tom = {"kind": "character", "e": person(b, "tom"), "field": "looks", "item": fixing.fact_key("green eyes"), "text": "looks: green eyes"}
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": "T", "to": "grey eyes"}]}, {"T": tom}, words)
    assert not ops and dropped == 1


def test_fix_a_new_fear_is_added_to_the_old():
    import fixing
    b = fix_merge()
    words = "Mara is afraid of fire"
    _v, refs = fixing.build_view(b, words)
    ops, _d = fixing.read_answer(b, {"ops": [{"do": "add", "on": char_ref(refs, b, "mara"), "field": "fears", "to": "fire"}]}, refs, words)
    assert ops[0]["value"] == "deep water; fire"


def test_fix_the_climax_stays_out_of_it_unless_she_says():
    import fixing
    b = rich()
    b["climax"] = None
    b["structure"]["climax"]["note"] = "the storm breaks"
    words = "the storm is in chapter 2, not chapter 1"
    view, refs = fixing.build_view(b, words)
    assert "CLI" not in refs
    view, refs = fixing.build_view(b, words, "structure")
    assert "CLI" in refs
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": "CLI.section", "to": 2}]}, refs, words)
    assert not ops and dropped == 1                         # she spoke of the storm, not the climax
    words = "the climax is in chapter 2, not chapter 1"
    view, refs = fixing.build_view(b, words)
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": "CLI.section", "to": 2}]}, refs, words)
    assert ops[0]["value"] == 2 and not dropped


# -- 0.4.1: her lists, fewer files, and the tidy --------------------------------------------------------------------

def old(path: Path, hours: float = 3) -> Path:
    then = time.time() - hours * 3600
    os.utime(path, (then, then))
    return path


def sized(path: Path, n: int, fill: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((fill * (n // len(fill) + 1))[:n])
    return old(path)


def sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Shelf:
    """The app without its server: the Core only says where the shelf is."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def shelf(self) -> Path:
        return self.path


def a_novel(shelf: Path, title: str = "Novel", leftovers: bool = True) -> Path:
    """A book as a 15,000-word novel's folder is on a real test bot, with sizes to match: 12 sections, their cache entries,
    the bible, boards, five pictures, nine empty notes files (editing.md from before 0.5 too). With ``leftovers``, what a tidy is for as well."""
    folder = shelf / title
    index = []
    for n in range(1, 13):
        text = f"# Chapter {n}\n\n" + f"Words of chapter {n}. " * 500
        name = f"{n:02} - Chapter {n}.md"
        (folder / "sections").mkdir(parents=True, exist_ok=True)
        (folder / "sections" / name).write_text(text, encoding="utf-8", newline="\n")
        old(folder / "sections" / name)
        sha = sha_text(text)
        index.append({"n": n, "heading": f"Chapter {n}", "file": name, "words": 2000, "sha": sha})
        sized(folder / "cache" / f"{sha}.json", 5000)
    sized(folder / "original.docx", 92 * 1024)
    (folder / "manuscript.json").write_text(json.dumps({"title": title, "sections": index, "chapters": index}), encoding="utf-8")
    (folder / "bible.json").write_text(json.dumps({"title": title, "sections_read": 12}) + " " * 48000, encoding="utf-8")
    (folder / "bible.base.json").write_text((folder / "bible.json").read_text(encoding="utf-8"), encoding="utf-8")
    (folder / "status.json").write_text(json.dumps({"state": "done", "step": "Read."}), encoding="utf-8")
    (folder / "views.json").write_text(json.dumps({"views": [{"id": "v-1", "tab": "plot", "title": "Mine"}]}), encoding="utf-8")
    (folder / "corrections.json").write_text(json.dumps({"corrections": [{"id": "c-1", "what": "Her words"}]}), encoding="utf-8")
    (folder / "desk.json").write_text(json.dumps({"items": [{"id": "d-1", "state": "kept"}]}), encoding="utf-8")
    sized(folder / "boards.json", 16 * 1024)
    pics = [{"id": f"p-20261101-00000{i}-portrait", "kind": "portrait", "who": f"who{i}", "state": "ready"} for i in range(5)]
    (folder / "pictures.json").write_text(json.dumps({"pictures": pics, "seeds": {}}), encoding="utf-8")
    for p in pics:
        sized(folder / "pictures" / f"{p['id']}.png", 800 * 1024)
    for key, label in (("world", "World"), ("characters", "Characters"), ("plot", "Plot and subplots"), ("themes", "Themes"),
                       ("science", "Science"), ("timeline", "Timeline"), ("structure", "Structure"), ("voice", "Voice"),
                       ("editing", "Editor's desk")):
        (folder / f"{key}.md").write_text(f"# {label}\n", encoding="utf-8")
    if leftovers:
        sized(folder / "cache" / f"{sha_text('a section text that is gone')}.json", 5000)
        sized(folder / "cache" / f"{sha_text('another that is gone')}.json", 5000)
        sized(folder / "pictures" / "p-20261001-000000-portrait.png", 800 * 1024)
        sized(folder / ".tmp-abc.json", 2000)
        sized(folder / "cache" / ".tmp-def.json", 2000)
        sized(folder / "pictures" / ".tmp-maps-ghi.png", 2000)
        sized(folder / "sections" / ".tmp-jkl.md", 2000)
        sized(folder / "download.part", 2000)
    return folder


def tidy_module():
    sys.path.insert(0, str(PKG))
    import tidy
    return tidy


def tidy_app(shelf: Path, data: Path, monkeypatch):
    sys.path.insert(0, str(PKG))
    monkeypatch.setenv("PUFFPAI_DATA", str(data))
    import server
    return server.Manuscripts(Shelf(shelf))


def count_and_size(folder: Path) -> tuple[int, int]:
    files = [p for p in folder.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def test_the_tidy_keeps_what_matters_and_clears_the_rest(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf)
    (folder / "world.md").write_text("# World\n\nThe tide turns every nine hours.\n", encoding="utf-8")
    (folder / "voice.md").write_text("# Too many adverbs\n", encoding="utf-8")        # one heading, but hers
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}
    n0, b0 = count_and_size(folder)
    gone = tidy_app(shelf, tmp_path / "data", monkeypatch).tidy_book(folder)
    n1, b1 = count_and_size(folder)
    print(f"\nA book with leftovers: {n0} files, {b0} bytes, to {n1} files, {b1} bytes")
    assert gone == {"cache": 2, "pictures": 1, "temps": 5, "notes": 7, "records": 0}
    left = {p for p in folder.rglob("*") if p.is_file()}
    for p, sha in before.items():                       # what stays is byte for byte what it was
        if p in left:
            assert hashlib.sha256(p.read_bytes()).hexdigest() == sha
    names = {p.relative_to(folder).as_posix() for p in left}
    assert {"original.docx", "manuscript.json", "bible.json", "bible.base.json", "status.json", "views.json", "corrections.json",
            "desk.json", "boards.json", "pictures.json", "world.md", "voice.md"} <= names
    assert len([n for n in names if n.startswith("sections/")]) == 12 and len([n for n in names if n.startswith("cache/")]) == 12
    assert len([n for n in names if n.startswith("pictures/")]) == 5
    assert not [n for n in names if ".tmp" in n or n.endswith(".part")]
    assert not {"characters.md", "plot.md", "editing.md", "timeline.md"} & names


def test_a_book_as_it_is_today_loses_only_its_empty_notes(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf, leftovers=False)
    n0, b0 = count_and_size(folder)
    tidy_app(shelf, tmp_path / "data", monkeypatch).tidy_book(folder)
    n1, b1 = count_and_size(folder)
    print(f"\nA book as it is today: {n0} files, {b0} bytes, to {n1} files, {b1} bytes")
    assert n0 - n1 == 9 and b0 - b1 < 200


def test_a_file_the_tidy_does_not_know_stays(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf)
    strangers = [sized(folder / "her photo.jpg", 100), sized(folder / "scribbles.md", 0),
                 sized(folder / "cache" / "not-a-sha.json", 100), sized(folder / "cache" / "notes.txt", 100),
                 sized(folder / "pictures" / "my own drawing.png", 100), sized(folder / "pictures" / "p-1.png", 100),
                 sized(folder / "editors-desk" / "desk.json", 100), sized(folder / "editors-desk" / ".tmp-x.json", 100),
                 sized(folder / "maybe" / "deep" / "thing.bin", 100)]
    tidy_app(shelf, tmp_path / "data", monkeypatch).tidy_book(folder)
    assert all(p.exists() for p in strangers)


def test_when_it_cannot_be_sure_it_keeps(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    app = tidy_app(shelf, tmp_path / "data", monkeypatch)
    folder = a_novel(shelf)
    (folder / "pictures.json").write_text("{not json", encoding="utf-8")         # no telling what is named
    stale = folder / "cache" / f"{sha_text('a section text that is gone')}.json"
    (folder / "sections" / "03 - Chapter 3.md").unlink()                          # no telling what is current
    app.tidy_book(folder)
    assert (folder / "pictures" / "p-20261001-000000-portrait.png").exists() and stale.exists()
    second = a_novel(shelf, "Young")
    orphan, entry = second / "pictures" / "p-20261001-000000-portrait.png", second / "cache" / f"{sha_text('another that is gone')}.json"
    temp = second / ".tmp-abc.json"
    for p in (orphan, entry, temp):                                               # just written: maybe Maps' or the Desk's
        os.utime(p, None)
    app.tidy_book(second)
    assert orphan.exists() and entry.exists() and temp.exists()


def test_nothing_runs_during_a_reading(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf)
    app = tidy_app(shelf, tmp_path / "data", monkeypatch)
    release = threading.Event()
    t = threading.Thread(target=release.wait, daemon=True)
    t.start()
    app.reader.running[str(folder)] = t
    before = {p: p.stat().st_mtime_ns for p in folder.rglob("*") if p.is_file()}
    assert app.tidy_book(folder) is None
    app.tidy_due()
    assert {p: p.stat().st_mtime_ns for p in folder.rglob("*") if p.is_file()} == before
    assert not (tmp_path / "data" / "tidied.json").exists()
    release.set()
    t.join()
    (folder / "status.json").write_text('{"state": "reading"}', encoding="utf-8")   # left by a stop: it holds nothing back
    assert app.tidy_book(folder)["notes"] == 9


def test_once_a_day_at_most(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf)
    app = tidy_app(shelf, tmp_path / "data", monkeypatch)
    assert app.tidy_book(folder)
    (folder / "world.md").write_text("# World\n", encoding="utf-8")
    assert app.tidy_book(folder) is None and (folder / "world.md").exists()
    stamps = json.loads((tmp_path / "data" / "tidied.json").read_text(encoding="utf-8"))
    stamps[folder.name] -= 25 * 3600
    (tmp_path / "data" / "tidied.json").write_text(json.dumps(stamps), encoding="utf-8")
    assert app.tidy_book(folder)["notes"] == 1 and not (folder / "world.md").exists()


def test_a_crash_leaves_nothing_half_done(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder, same = a_novel(shelf), a_novel(shelf, "Same")
    t = tidy_module()
    app = tidy_app(shelf, tmp_path / "data", monkeypatch)
    real, left = t._gone, [3]

    def stops(path):
        if not left[0]:
            raise KeyboardInterrupt
        left[0] -= 1
        return real(path)
    monkeypatch.setattr(t, "_gone", stops)
    with pytest.raises(KeyboardInterrupt):
        app.tidy_book(folder)
    assert not (tmp_path / "data" / "tidied.json").exists()    # it did not finish, so it is still due
    assert app.lock.acquire(blocking=False)                    # and it holds nothing
    app.lock.release()
    monkeypatch.setattr(t, "_gone", real)
    assert app.tidy_book(folder)
    app.tidy_book(same)
    assert sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()) == \
        sorted(p.relative_to(same).as_posix() for p in same.rglob("*") if p.is_file())


def test_removed_books_stay_as_they_are(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    away = a_novel(shelf / "Removed", "Old 20261101-000000")
    a_novel(shelf)
    before = sorted(p for p in away.rglob("*") if p.is_file())
    tidy_app(shelf, tmp_path / "data", monkeypatch).tidy_due()
    assert sorted(p for p in away.rglob("*") if p.is_file()) == before


def test_an_older_version_s_entry_goes_and_the_current_one_stays(tmp_path, monkeypatch):
    """Editor's Desk keeps older versions in its own data folder. The text a section has now keeps its entry; the text it
    had goes, so a version restored later is read again, that one section."""
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf, leftovers=False)
    was = (folder / "sections" / "02 - Chapter 2.md").read_text(encoding="utf-8")
    now = "# Chapter 2\n\nChanged in Editor's Desk.\n"
    (folder / "sections" / "02 - Chapter 2.md").write_text(now, encoding="utf-8", newline="\n")
    entry = sized(folder / "cache" / f"{sha_text(now)}.json", 100)
    idx = json.loads((folder / "manuscript.json").read_text(encoding="utf-8"))
    idx["sections"][1]["sha"] = sha_text(now)                      # as Editor's Desk's save leaves the index
    (folder / "manuscript.json").write_text(json.dumps({**idx, "chapters": idx["sections"]}), encoding="utf-8")
    tidy_app(shelf, tmp_path / "data", monkeypatch).tidy_book(folder)
    assert entry.exists() and not (folder / "cache" / f"{sha_text(was)}.json").exists()
    assert (folder / "cache" / f"{sha_text((folder / 'sections' / '01 - Chapter 1.md').read_text(encoding='utf-8'))}.json").exists()


def test_a_section_changed_on_disk_keeps_its_current_entry(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf, leftovers=False)
    now = "# Chapter 2\n\nChanged in the Editor's Desk.\n"
    (folder / "sections" / "02 - Chapter 2.md").write_text(now, encoding="utf-8", newline="\n")
    entry = sized(folder / "cache" / f"{sha_text(now)}.json", 100)
    tidy_app(shelf, tmp_path / "data", monkeypatch).tidy_book(folder)
    assert entry.exists()


def test_empty_records_go_and_the_app_reads_as_before(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf)
    app = tidy_app(shelf, tmp_path / "data", monkeypatch)
    (folder / "views.json").write_text('{"views": []}', encoding="utf-8")
    (folder / "corrections.json").write_text('{"corrections": []}', encoding="utf-8")
    (folder / "desk.json").write_text('{"items": []}', encoding="utf-8")
    assert app.tidy_book(folder)["records"] == 3
    idx = app._index(folder)
    assert app._views(folder) == [] and app._corrections(folder) == []
    assert app._bible(folder, idx)["title"] == "Novel"


def test_notes_are_made_on_first_write_and_a_tidied_file_never_refuses_her(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf)
    app = tidy_app(shelf, tmp_path / "data", monkeypatch)
    opened = app.notes_view("Novel", "world")
    assert opened["text"] == "# World\n" and opened["stamp"]
    app.tidy_book(folder)
    assert not (folder / "world.md").exists()
    assert app.notes_view("Novel", "world") == {"title": "Novel", "tab": "world", "text": "# World\n", "stamp": ""}
    got = app.notes_save("Novel", "world", "# World\n\nHer note, typed before the tidy.\n", opened["stamp"])    # the page she had open
    assert got["stamp"] and "Her note" in (folder / "world.md").read_text(encoding="utf-8")
    assert app.notes_save("Novel", "plot", "# Plot and subplots\n", "")["stamp"] == ""       # a Save with nothing in it makes no file
    assert not (folder / "plot.md").exists()
    assert app.manuscript_notes_append("Novel", "plot", "A note from the bot.") and (folder / "plot.md").is_file()
    with pytest.raises(Exception, match="Editor's Desk"):                # 0.5: editing notes are Editor's Desk's to show
        app.manuscript_notes_append("Novel", "desk", "One for the desk.")
    assert not (folder / "desk.md").exists()


def test_an_old_bible_with_a_desk_loads_and_desk_json_is_left_for_editors_desk(tmp_path, monkeypatch):
    """A book read before 0.5: its bible has a desk and her marks are in desk.json. Manuscript reads it on every tab, never
    writes desk.json or her editing notes, and Editor's Desk carries the marks over from it once."""
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf, leftovers=False)
    old_desk = [{"id": "d-kept000001", "kind": "plot_hole", "severity": "high", "sections": [3], "note": "A hole.",
                 "fix": "Fill it.", "state": "open", "writer_note": "", "why": "", "where": []}]
    bible = {"schema": "manuscript-bible/2", "title": "Novel", "sections_read": 12, "characters": [], "desk": old_desk,
             "briefs": {"desk": "The old letter."}}
    for name in ("bible.json", "bible.base.json"):
        (folder / name).write_text(json.dumps(bible), encoding="utf-8")
    marks = {"items": [{"id": "d-kept000001", "kind": "plot_hole", "severity": "high", "sections": [3], "note": "A hole.",
                        "fix": "Fill it.", "state": "kept", "writer_note": "Book two.", "changed_at": "2026-11-01T10:00:00Z"}]}
    (folder / "desk.json").write_text(json.dumps(marks), encoding="utf-8")
    (folder / "editing.md").write_text("# Editor's desk\n\nHer old editing notes.\n", encoding="utf-8")
    keep = {n: (folder / n).read_bytes() for n in ("desk.json", "editing.md")}
    app = tidy_app(shelf, tmp_path / "data", monkeypatch)
    idx = app._index(folder)
    assert app._bible(folder, idx)["desk"] == old_desk                    # loads, as it was
    for tab in server_tabs():
        assert app.tab_view("Novel", tab)["tab"] == tab
    assert app.view()["manuscripts"][0]["counts"] == {"characters": 0, "main": 0, "threads": 0, "places": 0, "events": 0}
    app.edit("Novel", [{"key": "character:", "action": "add", "value": {"name": "Ada Quill"}}])   # a rebuild of bible.json
    app.tidy_book(folder)
    assert {n: (folder / n).read_bytes() for n in keep} == keep
    desk = PKG.parent / "editors-desk"
    got = subprocess.run([sys.executable, "-I", "-c",
                          "import json, sys; sys.path.insert(0, sys.argv[1]); import engine; from pathlib import Path;"
                          "f = Path(sys.argv[2]); n = engine.carry_old_desk(f);"
                          "print(json.dumps([n, [(x['id'], x['state'], x['note'], x['carried']) for x in engine.load_findings(f)]]))",
                          str(desk), str(folder)], capture_output=True, text=True, timeout=60,
                         env={**os.environ, "PUFFPAI_DATA": str(tmp_path / "desk data"), "PUFFPAI_KEYRING": "stub"})
    assert got.returncode == 0, got.stderr
    assert json.loads(got.stdout) == [1, [["d-kept000001", "open", "Book two.", "kept"]]]
    assert {n: (folder / n).read_bytes() for n in keep} == keep            # Editor's Desk leaves it as it was too


def server_tabs():
    sys.path.insert(0, str(PKG))
    import server
    return list(server.TABS)


def test_the_line_on_the_settings_fold(tmp_path, monkeypatch):
    shelf = tmp_path / "Manuscripts"
    folder = a_novel(shelf, leftovers=False)
    t = tidy_module()
    app = tidy_app(shelf, tmp_path / "data", monkeypatch)
    got = app.size_view("Novel")
    assert re.fullmatch(r"This book uses 4\.\d MB: pictures 3\.9 MB\.", got["line"]), got["line"]
    assert got["pictures"] == 5 * 800 * 1024 and got["files"] == count_and_size(folder)[0]
    pics = json.loads((folder / "pictures.json").read_text(encoding="utf-8"))
    pics["pictures"].insert(0, {**pics["pictures"][0], "id": "p-20261102-000000-portrait"})        # Paint again: the new one first
    (folder / "pictures.json").write_text(json.dumps(pics), encoding="utf-8")
    sized(folder / "pictures" / "p-20261102-000000-portrait.png", 800 * 1024)
    line = app.size_view("Novel")["line"]
    assert "pictures 4.7 MB" in line and "800 KB is older paintings that Paint again replaced" in line
    assert t.usage_line({"bytes": 300 * 1024, "pictures": 0, "older": 0, "files": 3}) == "This book uses 300 KB."
    big = t.usage_line({"bytes": 900 * 1024 * 1024, "pictures": 880 * 1024 * 1024, "older": 0, "files": 3})
    assert big.startswith("This book uses 900.0 MB: pictures 880.0 MB.") and "that stays your choice" in big


def test_an_old_bots_shelf_gets_its_mark_and_its_tidy_at_start(tmp_path):
    shelf = tmp_path / "My files" / "Manuscripts"
    folder = a_novel(shelf)
    proc, core, port = start_server(tmp_path)
    try:
        for _ in range(100):
            if (shelf / ".app-files").is_file() and not (folder / "world.md").exists():
                break
            time.sleep(0.1)
        else:
            raise AssertionError("no mark or no tidy at start")
        assert (tmp_path / "package data" / "tidied.json").is_file() and core.calls == []      # by code: no brain call
        got = call(port, "GET", "/api/list")[1]
        assert [b["title"] for b in got["manuscripts"]] == ["Novel"]
        code, size = call(port, "GET", "/api/size" + q(title="Novel"))
        assert code == 200 and size["line"].startswith("This book uses")
        assert call(port, "GET", "/api/size" + q(title="Novel"), token=None)[0] == 401
    finally:
        stop_server(proc, core)


def test_a_reading_tidies_after_itself_and_a_reread_still_uses_the_cache_and_undo_works(tmp_path):
    proc, core, port = start_server(tmp_path)
    shelf = tmp_path / "My files" / "Manuscripts"
    try:
        code, got = call(port, "POST", "/api/upload", {"file_name": "four.md", "title": "Four",
                                                       "content_b64": base64.b64encode(FOUR.encode()).decode()})
        assert code == 200, got
        folder = shelf / "Four"
        assert (shelf / ".app-files").is_file()
        assert [p.name for p in folder.glob("*.md")] == ["original.md"]   # no empty notes at upload
        assert wait_done(port, "Four")["state"] == "done"
        stamps = tmp_path / "package data" / "tidied.json"
        for _ in range(50):                                         # told last of all, as the reading's thread ends
            if stamps.is_file() and "Four" in stamps.read_text(encoding="utf-8"):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("the reading did not tidy after itself")
        entries = sorted(p.name for p in (folder / "cache").glob("*.json"))
        assert len(entries) == 4

        code, fixed = call(port, "POST", "/api/correct", {"title": "Four", "what": "Jennifer Hale is a minor character"})
        assert code == 200 and fixed["correction"]["applied"], fixed
        bible = json.loads((folder / "bible.json").read_text(encoding="utf-8"))
        assert next(c for c in bible["characters"] if c["name"] == "Jennifer Hale")["importance"] == "minor"

        for p in folder.rglob("*"):                                 # a day on: the same tidy the app runs
            if p.is_file():
                old(p, 30)
        t = tidy_module()
        import server
        idx = json.loads((folder / "manuscript.json").read_text(encoding="utf-8"))
        files = [folder / "sections" / c["file"] for c in idx["sections"]]
        shas = [c["sha"] for c in idx["sections"]]
        assert t.tidy(folder, "sections", files, shas, server.NOTES_HEADINGS)["cache"] == 0
        assert sorted(p.name for p in (folder / "cache").glob("*.json")) == entries

        line, err = tool(port, "manuscript_correct_undo", title="Four", id="last")
        assert not err and line.startswith("Took that back"), line
        bible = json.loads((folder / "bible.json").read_text(encoding="utf-8"))
        assert next(c for c in bible["characters"] if c["name"] == "Jennifer Hale").get("importance") != "minor"

        assert t.tidy(folder, "sections", files, shas, server.NOTES_HEADINGS)["records"] == 1      # nothing left in it
        code, fixed = call(port, "POST", "/api/correct", {"title": "Four", "what": "Jennifer Hale is a minor character"})
        assert code == 200 and fixed["correction"]["applied"], fixed           # and the next fix works
        line, err = tool(port, "manuscript_correct_undo", title="Four", id="last")
        assert not err and line.startswith("Took that back"), line

        n = len(core.calls)
        assert tool(port, "manuscript_analyze", title="Four", confirm=True)[0]
        time.sleep(0.2)
        assert wait_done(port, "Four")["state"] == "done"
        assert core.calls[n:] == []                                  # 0.5: nothing changed, so nothing is asked
        assert call(port, "GET", "/api/status" + q(title="Four"))[1]["unchanged"] is True

        name = sorted((folder / "sections").glob("*.md"))[1]          # Editor's Desk saves her edit of one section
        name.write_text(name.read_text(encoding="utf-8").replace("\n\n", "\n\nShe adds a line.\n\n", 1), encoding="utf-8",
                        newline="\n")
        n2 = int(json.loads((folder / "manuscript.json").read_text(encoding="utf-8"))["sections"][1]["n"])
        assert call(port, "GET", "/api/section" + q(title="Four", n=n2))[1]["changed"] is True
        assert call(port, "GET", "/api/section" + q(title="Four", n=n2 - 1))[1]["changed"] is False
        assert call(port, "POST", "/api/analyze", {"title": "Four"})[1]["message"].startswith("Your bot started reading")
        time.sleep(0.2)
        assert wait_done(port, "Four")["state"] == "done"
        said = [m[-1]["content"] for m in core.calls[n:]]
        assert len([u for u in said if "She adds a line." in u]) >= 1                     # that section, asked again
        assert not [u for u in said if "DELTA" in u and "She adds a line." not in u]      # no other section
        assert call(port, "GET", "/api/section" + q(title="Four", n=n2))[1]["changed"] is False
        assert not call(port, "GET", "/api/status" + q(title="Four"))[1].get("unchanged")
    finally:
        stop_server(proc, core)


# -- the real bot's test: the subject of her sentence must be the thing changed

def fix_real():
    """As the real bot read the fixture book: no storm event, only a passing mention of one."""
    import analysis
    return analysis.merge("Fix", [(n, f"Chapter {n}") for n in (1, 2, 3, 4)], {
        1: {"characters": [{"name": "Elias Ward", "beat": "Elias climbed the lighthouse and walked to Port Ash."},
                           {"name": "Anna", "aliases": ["Annie"]}, {"name": "Tom Reed", "beat": "Tom waits."}]},
        2: {"events": [{"what": "Elias walked to Port Ash", "who": ["Elias Ward"], "where": "Port Ash",
                        "when": "same night as storm"},
                       {"what": "The letter comes.", "who": [], "where": "", "when": "autumn"}],
            "places": [{"name": "Port Ash", "kind": "town"}]}})


def asked(answer):
    class Brain:
        calls = 0

        def chat(self, messages, patient=True):
            Brain.calls += 1
            return answer(messages[1]["content"]) if callable(answer) else answer, "m"
    return Brain()


def ref_in(user, text):
    return next(ln.strip()[1:ln.strip().index("]")] for ln in user.split("\n") if text in ln and ln.strip().startswith("["))


NO_STORM = ("There is no storm event in the story bible, so nothing changed. To add one, use Add: event on the Timeline tab.")


def test_real_a_word_in_when_is_not_what_the_event_is():
    """On the real bot: the event "Elias walked to Port Ash" moved, because its when said "same night as storm"."""
    import fixing
    b = fix_real()
    words = "the storm is in chapter 4, not chapter 2"
    before = json.dumps(b, sort_keys=True)
    ask = asked(lambda u: json.dumps({"ops": [{"do": "change", "ref": ref_in(u, "section: 2").split(".")[0] + ".section", "to": 4}]}))
    got = fixing.plan_words(ask, b, words, "", "timeline")
    assert got["ops"] == [] and got["why"] == NO_STORM
    assert json.dumps(b, sort_keys=True) == before
    assert fixing.plan_words(asked('{"ops":[]}'), b, words)["why"] == NO_STORM     # the model saying nothing: the same


def test_real_the_storm_still_moves_when_there_is_a_storm_event():
    import fixing
    b = fix_real()
    b["events"].append({"id": "e9", "section": 2, "order": 9, "story_order": 9, "what": "The storm sinks the ferry.", "who": [],
                        "where": "", "when": "", "placement": ""})
    words = "the storm is in chapter 4, not chapter 2"
    _v, refs = fixing.build_view(b, words)
    right = next(k for k, v in refs.items() if v.get("field") == "section" and "storm" in v["e"]["what"])
    wrong = next((k for k, v in refs.items() if v.get("field") == "section" and "Elias" in v["e"]["what"]), None)
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": right, "to": 4}]}, refs, words)
    assert ops[0]["value"] == 4 and "The storm sinks the ferry." in ops[0]["says"] and not dropped
    if wrong:
        ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": wrong, "to": 4}]}, refs, words)
        assert not ops and dropped == 1


def test_real_the_letter_comes_in_the_spring():
    import fixing
    b = fix_real()
    words = "the letter comes in the spring, not the autumn"
    _v, refs = fixing.build_view(b, words)
    r = next(k for k, v in refs.items() if v.get("field") == "when" and "letter" in v["e"]["what"])
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "change", "ref": r, "to": "spring"}]}, refs, words)
    assert ops[0]["value"] == "spring" and "autumn" in ops[0]["says"] and not dropped


@pytest.mark.parametrize("words,kind,text,do", [
    ("the storm is at Port Ash, not the lighthouse", "event", "climbs", {"do": "change", "field": "where", "to": "Port Ash"}),  # what she denied is no subject
    ("the storm never reaches the lighthouse", "event", "climbs", {"do": "remove"}),
])
def test_real_an_entry_that_only_shares_a_word_is_not_her_subject(words, kind, text, do):
    import fixing
    b = fix_merge()
    e = next(x for x in b["events"] if text in x["what"])
    field = do.get("field")
    refs = {"X": {"kind": kind, "e": e, **({"field": field, "item": None, "text": f"{field}: x"} if field else {})}}
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": do["do"], "ref": "X", "to": do.get("to")}]}, refs, words)
    assert not ops and dropped == 1


def test_real_a_fact_changes_only_when_her_sentence_is_about_it():
    import fixing
    b = fix_merge()
    words = "the storm never touched Mara's green eyes"
    r = {"kind": "character", "e": person(b, "mara"), "field": "looks", "item": fixing.fact_key("green eyes"), "text": "looks: green eyes"}
    ops, dropped = fixing.read_answer(b, {"ops": [{"do": "remove", "ref": "X"}]}, {"X": r}, words)
    assert not ops and dropped == 1


LONG_BEAT = "Elias climbed the lighthouse and walked to Port Ash."


def test_real_a_beat_that_says_more_stays_and_she_is_told():
    import fixing
    b = fix_real()
    words = "Elias never goes to the lighthouse"
    ask = asked(lambda u: json.dumps({"ops": [{"do": "remove", "ref": ref_in(u, "beats: Elias climbed")}]}))
    got = fixing.plan_words(ask, b, words)
    assert got["ops"] == []
    assert got["why"] == f'The beat "{LONG_BEAT}" says more than that, so I left it as it is. Press Edit to change it yourself.'
    assert [x["beat"] for x in person(b, "elias")["arc"]] == [LONG_BEAT]


def test_real_a_short_beat_goes_and_the_long_one_is_flagged_beside_it():
    import fixing
    b = fix_real()
    person(b, "elias")["arc"].append({"section": 2, "beat": "Elias climbs the lighthouse."})
    words = "Elias never goes to the lighthouse"
    ask = asked(lambda u: json.dumps({"ops": [{"do": "remove", "ref": ref_in(u, "beats: Elias climbed")},
                                              {"do": "remove", "ref": ref_in(u, "beats: Elias climbs")}]}))
    got = fixing.plan_words(ask, b, words)
    assert len(got["ops"]) == 1 and not got["partial"]
    assert got["line"] == ('removed "Elias climbs the lighthouse." from Elias Ward\'s beats; left the beat '
                           f'"{LONG_BEAT}" as it is because it says more than that (press Edit to change it yourself)')


def test_real_an_event_that_says_more_stays_too():
    import fixing
    b = fix_merge()
    long = "Elias climbs the lighthouse and carries the letter to Port Ash."
    b["events"][1]["what"] = long
    words = "Elias never goes to the lighthouse"
    ask = asked(lambda u: json.dumps({"ops": [{"do": "remove", "ref": ref_in(u, "Event: Elias climbs").split(".")[0]}]}))
    got = fixing.plan_words(ask, b, words)
    assert got["ops"] == [] and got["why"].startswith(f'The event "{long}" says more than that')


@pytest.mark.parametrize("words", ["Anna and Annie are the same person", "Annie and Anna are the same person",
                                   "Annie is the same person as Anna", "Anna and Annie are one person."])
def test_real_already_one_person_says_so_with_no_brain_call(words):
    import fixing
    ask = asked('{"ops":[]}')
    got = fixing.plan_words(ask, fix_real(), words)
    assert ask.calls == 0 and got["ops"] == []
    assert got["why"] == "Anna and Annie are already one person in the story bible, shown as Anna, so nothing needed changing."


def test_real_two_people_still_go_to_the_brain():
    import fixing
    b = fix_merge()                                         # Anna and Annie are two entries here
    ask = asked(lambda u: json.dumps({"ops": [{"do": "merge", "ref": ref_in(u, "] Annie").split("]")[0], "into": ref_in(u, "] Anna").split("]")[0]}]}))
    got = fixing.already_one(b, "Anna and Annie are the same person")
    assert got == "" and fixing.already_one(fix_real(), "Anna is not Annie") == ""
