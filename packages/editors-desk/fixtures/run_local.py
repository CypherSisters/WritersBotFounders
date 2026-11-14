"""Editor's Desk on this computer, to look at in a browser, with no PuffPAI and no model.

    python fixtures/run_local.py --port 8765

It copies the fixture novel (make_book.py) into a temp folder, adds a few italic and bold marks and some open findings with
real quotes, puts a second book beside it as a book to study (study_book.py, Night Ferry), and starts three small things on
127.0.0.1: a stand-in Core (where My files is, and a brain that gives the same short answer to a question, and for Study a
book, notes that quote real words of the two passages it was sent), the Editor's Desk server, and a tiny proxy on the given port that adds the token, the way the
PuffPAI Desktop does. Open the printed address. Stop with Ctrl+C; the temp folder is left for you to look at.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(HERE))

ANSWER = ("The words \"stayed in her coat\" are plain, and they work: the key is still and she is not. "
          "The sentence before it already says she told him nothing, so the two sit close together.\n\n"
          "You might try letting one of them carry the moment alone, and see which reads stronger.")

MARKS = [("01 - Chapter One.md", "with the silver key in her coat", "with the *silver key* in her coat"),
         ("01 - Chapter One.md", "the night he died", "the night he **died**"),
         ("02 - Chapter Two.md", "The silver key stayed in her coat", "The silver key ***stayed*** in her coat"),
         ("03 - Chapter Three.md", "She felt nothing at all", "She felt *nothing* at all"),
         ("06 - Chapter Six.md", "Someone else wanted what was on the road.", "Someone else wanted what was on the road.\n\n* * *")]

FINDINGS = [
    {"id": "local-key", "check": "plot", "what": "The silver key is carried north and never opens anything.",
     "where": [{"section": 2, "quote": "The silver key stayed in her coat"}], "why": "The book promises the key matters, and a reader waits for it.",
     "fix": "Let the key open something at the cairn, or let Mara see why it never will.", "size": "big"},
    {"id": "local-eyes", "check": "continuity", "what": "Mara's eyes are grey in chapter one and brown in chapter seven.",
     "where": [{"section": 1, "quote": "Her grey eyes went over the boats"}, {"section": 7, "quote": "looked hard at Mara's brown eyes"}],
     "why": "A careful reader will notice and stop trusting the details.", "fix": "Choose one colour and use it in both places.", "size": "medium"},
    {"id": "local-tide", "check": "science", "kind": "question", "what": "Check: a tide that turns every nine hours.",
     "where": [{"section": 4, "quote": "The tide on that coast turned every nine hours"}], "why": "Tides on Earth turn about every six hours.",
     "fix": "Keep it if this world is not ours, and say so somewhere.", "size": "small", "reason": "It may be a rule of this world."},
    {"id": "local-lost", "check": "prose", "what": "A sentence here leans on the same word twice.",
     "where": [{"section": 3, "quote": "words that are no longer in the book"}], "why": "The repeat dulls the moment.", "fix": "Try a different word for one of them.", "size": "small"},
]


def study_answer(user: str):
    """Study a book's stand-in editor: notes on two passages that quote real words of each, then the pointers in order."""
    if "Passage A" not in user:
        return [{"try": "Let a chapter close on a line of talk now and then.", "why": "It leaves the reader leaning into the next one.", "from": "ending"},
                {"try": "Give the place one long sentence before people speak.", "why": "The reader knows where they stand before the talk starts.", "from": "opening"}]
    a = user.split("Passage A, from the book she admires:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
    b = user.split("Passage B, from her manuscript:\n<<<\n", 1)[1].split("\n>>>", 1)[0]

    def few(text: str, skip: int = 0) -> str:
        return " ".join(text.split("\n\n")[0].split()[skip:skip + 6])          # exactly as written

    return {"theirs_does": "It lets long sentences carry the place and the weather, then drops in a short one.",
            "mine_does": "It moves in short lines, mostly talk, and the place comes in small pieces.",
            "pointers": [{"try": "Let one long sentence set the scene before the talk starts.", "why": "It gives the reader somewhere to stand.",
                          "their_quote": few(a), "my_quote": few(b)},
                         {"try": "Notice where their paragraphs end, and try ending one of yours on an image.", "why": "An image at the end of a paragraph lingers.",
                          "their_quote": few(a, 6), "my_quote": few(b, 6)}]}


def board_answer(user: str):
    """Panels for the stand-in storyboard artist: real sentences of the passage as quotes, so they are proved."""
    import re
    passage = user.split("<<<", 1)[-1].split(">>>", 1)[0]
    sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", " ".join(passage.split())) if 6 <= len(x.split()) <= 30]
    shots, moods = ("wide", "medium", "close"), ("quiet", "tense", "warm", "cold")
    out = []
    for i, sent in enumerate(sentences[::max(1, len(sentences) // 3)][:3]):
        out.append({"what": "A moment from this part of the story, as a picture.", "where": "where the scene is set", "who": [],
                    "mood": moods[i % 4], "shot": shots[i % 3], "frame": "The people in the scene, seen from a little way off.",
                    "quote": " ".join(sent.split()[:12])})
    return out


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def serve(server: ThreadingHTTPServer) -> None:
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()


def stand_in_core(made: Path) -> ThreadingHTTPServer:
    class Core(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _out(self, obj: dict) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._out({"made": str(made)})

        def do_POST(self):
            got = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            msgs = got.get("messages") or [{}]
            if "storyboard artist" in str(msgs[0].get("content")):
                return self._out({"text": json.dumps(board_answer(str(msgs[-1].get("content")))), "model": "stand-in"})
            if "learn from a book she admires" in str(msgs[0].get("content")):
                return self._out({"text": json.dumps(study_answer(str(msgs[-1].get("content")))), "model": "stand-in"})
            self._out({"text": ANSWER, "model": "stand-in"})

    return ThreadingHTTPServer(("127.0.0.1", 0), Core)


def token_proxy(port: int, inner: int, token: str) -> ThreadingHTTPServer:
    """What the PuffPAI Desktop does for the page: every call goes on to the app with the token added."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    class Proxy(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _pass(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else None
            head = {"Authorization": f"Bearer {token}"}
            if self.headers.get("Content-Type"):
                head["Content-Type"] = self.headers["Content-Type"]
            req = urllib.request.Request(f"http://127.0.0.1:{inner}{self.path}", data=body, method=self.command, headers=head)
            try:
                with opener.open(req, timeout=120) as r:
                    code, ctype, out = r.status, r.headers.get("Content-Type", ""), r.read()
            except urllib.error.HTTPError as exc:
                code, ctype, out = exc.code, exc.headers.get("Content-Type", ""), exc.read()
            self.send_response(code)
            if ctype:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(out)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(out)

        do_GET = do_POST = do_HEAD = _pass

    return ThreadingHTTPServer(("127.0.0.1", port), Proxy)


def make_copy(root: Path) -> Path:
    import make_book
    import study_book
    made = root / "My files" / "Made by bot"
    made.mkdir(parents=True)
    folder = make_book.write_book(root / "My files" / "Manuscripts")
    for name, old, new in MARKS:
        p = folder / "sections" / name
        p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8", newline="\n")
    idx = json.loads((folder / "manuscript.json").read_text(encoding="utf-8"))
    for key in ("sections", "chapters"):              # chapter five reads as the second part of chapter four: a seam to look at
        for e in idx[key]:
            if e["n"] == 5:
                e["heading"] = "Chapter Four, part 2"
    (folder / "manuscript.json").write_text(json.dumps(idx), encoding="utf-8")
    other = study_book.write_study_book(root / "My files" / "Manuscripts")    # a book she admires, added to study
    idx = json.loads((other / "manuscript.json").read_text(encoding="utf-8"))
    idx["study"] = True
    (other / "manuscript.json").write_text(json.dumps(idx), encoding="utf-8")
    return folder


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Editor's Desk with a fixture book, for a browser")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)
    root = Path(tempfile.mkdtemp(prefix="editors-desk-local-"))
    os.environ["PUFFPAI_DATA"] = str(root / "data")    # the app's own data folder, inside the temp folder
    import engine
    import server
    from brainline import Brain
    folder = make_copy(root)
    engine.save_findings(folder, [{**f, "state": "open", "note": "", "found_by": "model", "sha": {}} for f in FINDINGS])
    token = secrets.token_hex(16)
    core = stand_in_core(root / "My files" / "Made by bot")
    serve(core)
    api = f"http://127.0.0.1:{core.server_address[1]}"
    inner = free_port()
    app = server.make_server(inner, server.CoreFiles(api, token), token, Brain(api, token))
    serve(app)
    proxy = token_proxy(args.port, inner, token)
    print(f"Editor's Desk: http://127.0.0.1:{proxy.server_address[1]}/", flush=True)
    print(f"The book is a copy in {root}", flush=True)
    try:
        proxy.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        proxy.server_close()
        app.shutdown()
        core.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
