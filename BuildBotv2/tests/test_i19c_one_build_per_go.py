"""Lane I19c: a Yes or No on a plan card never plans again, and one "Your plan" notice per build.

The bug: the plan's Yes card was drawn inside the Apps > Add form (and the GitHub block's form), and its
buttons had no type, so they were submit buttons. A Yes or No answered the card AND submitted the form again:
a second build for the same goal. Each Yes also added one more "Your plan" notice.

1. Static: the Desktop's one element maker gives every button a type ("button" unless it says "submit"), and
   no form in Apps > Add or the GitHub block holds the place where the plan card is drawn.
2. In a real headless Edge, by real clicks, against a FAKE Core API on a free port (never 8420-8422): three
   unrelated goals and one pasted GitHub address in Apps > Add, one address in the GitHub block. Each Go
   makes exactly one build, whether the answer is Yes or No; a second Yes for the same build keeps one notice.
"""

from __future__ import annotations

import http.server
import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "packages" / "desktop" / "web"


# -- 1. static -----------------------------------------------------------------------------------------------

def test_every_js_button_gets_a_type():
    src = (WEB / "desktop.js").read_text(encoding="utf-8")
    maker = src[src.index("function el(tag, attrs, kids)"):src.index("function clear(n)")]
    assert 'tag === "button"' in maker and 'setAttribute("type", "button")' in maker, maker
    # every page and the GitHub block build their elements with that one maker, never their own buttons
    for f in [WEB / "desktop.js", WEB / "github.js", WEB / "devzones.js", *sorted((WEB / "pages").glob("*.js"))]:
        text = f.read_text(encoding="utf-8")
        assert not re.search(r"createElement\(\s*[\"']button", text), f.name
        assert ".innerHTML" not in text, f.name


def _form_calls(src: str):
    """Each el("form", ...) call's text, up to its closing parenthesis."""
    for m in re.finditer(r'el\("form",', src):
        depth, i = 0, m.start() + 2
        while True:
            ch = src[i]
            depth += ch == "("
            depth -= ch == ")"
            if depth == 0:
                yield src[m.start():i + 1]
                break
            i += 1


def test_the_plan_card_is_drawn_outside_the_forms():
    for name in ("pages/apps.js", "github.js"):
        src = (WEB / name).read_text(encoding="utf-8")
        forms = list(_form_calls(src))
        assert forms, name
        for f in forms:
            assert not re.search(r"\bsaid\b", f), (name, f)


# -- 2. real clicks against a fake Core ------------------------------------------------------------------------

EDGE = next((p for p in (os.path.join(os.environ.get(v, ""), "Microsoft", "Edge", "Application", "msedge.exe")
                         for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")) if os.path.isfile(p)), None)

TYPES = {".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".webp": "image/webp", ".png": "image/png",
         ".svg": "image/svg+xml", ".woff2": "font/woff2", ".json": "application/json"}


class FakeCore:
    """The Desktop's files plus the few Core API routes a plan needs. Every POST that plans is counted."""

    def __init__(self):
        self.builds, self.cards, self.plans, self.lock = [], {}, [], threading.Lock()
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _send(self, status, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def _body(self):
                size = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(size) or b"{}")

            def do_GET(self):
                path, _, query = self.path.partition("?")
                if path == "/" or path.startswith("/ui/"):
                    rel = "index.html" if path == "/" else path[len("/ui/"):]
                    f = (WEB / rel).resolve()
                    if WEB.resolve() in f.parents and f.is_file():
                        return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
                    return self._send(404, {"error": "No such file"})
                with fake.lock:
                    if path == "/v1/consent" and "pending" in query:
                        return self._send(200, [c for c in fake.cards.values() if c["status"] == "pending"])
                    if path == "/v1/toolsmith":
                        return self._send(200, {"builds": [dict(b) for b in fake.builds]})
                    m = re.fullmatch(r"/v1/toolsmith/builds/([\w-]+)", path)
                    if m:
                        b = next((x for x in fake.builds if x["id"] == m.group(1)), None)
                        return self._send(200, b) if b else self._send(404, {"error": "No such build"})
                    if path == "/v1/github/key":
                        return self._send(200, {"linked": False, "vault": "open"})
                return self._send(404, {"error": "No such route"})

            def do_POST(self):
                path = self.path.partition("?")[0]
                body = self._body()
                with fake.lock:
                    if path == "/v1/toolsmith/plan":
                        return self._send(201, fake.plan(str(body.get("goal") or ""), "/v1/toolsmith/plan"))
                    if path == "/v1/github/code":
                        repo = str(body.get("address") or "").rstrip("/").rsplit("/", 1)[-1]
                        return self._send(201, fake.plan("Add " + repo + " from GitHub", "/v1/github/code"))
                    m = re.fullmatch(r"/v1/consent/([\w-]+)/answer", path)
                    if m and m.group(1) in fake.cards:
                        c = fake.cards[m.group(1)]
                        c["status"] = "answered"
                        c["answers"].append(body.get("answer"))
                        if body.get("answer") == "yes":
                            next(b for b in fake.builds if b["consent"]["id"] == c["id"])["yes"] = True
                        return self._send(200, {"status": "answered"})
                    if re.fullmatch(r"/v1/toolsmith/builds/[\w-]+/start", path):
                        return self._send(200, {"started": True})
                return self._send(404, {"error": "No such route"})

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        assert self.port not in (8420, 8421, 8422)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def plan(self, goal, route):
        n = len(self.builds) + 1
        bid, cid = f"20261028-1200{n:02d}-fake{n:02d}", f"card-{n}"
        lines = [f"Job 1 for {goal}: try a ready-made app."]
        self.cards[cid] = {"id": cid, "title": "Your plan: " + goal, "lines": lines, "status": "pending", "card_sha": "s" + str(n),
                           "card": {"covers": {"build": bid, "plan_sha": "p" + str(n)}}, "answers": []}
        b = {"id": bid, "goal": goal, "created_at": f"2026-10-28T12:00:{n:02d}", "card": {"lines": lines},
             "consent": {"id": cid}, "yes": False, "jobs": []}
        self.builds.append(b)
        self.plans.append((route, goal))
        return dict(b)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _click_until(b, point, happened, busy_js="false", wait_s=4.0, tries=3):
    """A real click on *point()*, again only if it did nothing. The page draws some parts late (the GitHub
    block's link state, notices), so a point taken just before the click can move: in the full suite a Go click
    landed beside the button and no plan was asked (lane I21c). A click that did something (*happened()*, or
    *busy_js* true while its request is out) is never repeated, so a second plan would still be caught. A target
    gone after the first click means that click was taken."""
    for n in range(tries):
        pt = point()
        if not pt and n:
            return
        assert pt
        b.click(*pt)
        end = time.monotonic() + wait_s
        while time.monotonic() < end:
            if happened() or b.js(busy_js):
                return
            time.sleep(0.1)
    raise AssertionError(f"the click did nothing, {tries} times")


@pytest.mark.skipif(os.name != "nt" or EDGE is None, reason="Windows Job Objects and Microsoft Edge")
def test_yes_or_no_on_a_plan_card_leaves_exactly_one_build(tmp_path):
    from test_i10a_desktop_clicks import Browser, _button
    core = FakeCore()
    b = None
    try:
        b = Browser(tmp_path / "edge")
        b.ws.call("Page.navigate", {"url": f"http://127.0.0.1:{core.port}/#apps/add"})
        add_form = "#view-apps form textarea"
        assert b.wait(f"!!document.querySelector({json.dumps(add_form)})", timeout_s=30)

        def go_and_answer(goal, answer, typed=None):
            before = len(core.builds)
            b.js(f"location.hash = '#apps/add'")
            assert b.wait(f"!!document.querySelector({json.dumps(add_form)})")
            b.js(f"document.querySelector({json.dumps(add_form)}).value = {json.dumps(typed or goal)}")
            go = "Array.from(document.querySelectorAll('#view-apps form button')).find(x => x.textContent.indexOf('Go') >= 0)"
            _click_until(b, lambda: b.js(f"""(() => {{ const g = {go}; g.scrollIntoView({{block: 'center'}});
              const r = g.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()"""),
                         lambda: len(core.builds) > before, busy_js=f"{go}.disabled")
            assert b.wait(f"Array.from(document.querySelectorAll('#view-apps .card.yes')).some(c => c.textContent.indexOf({json.dumps(goal)}) >= 0)",
                          timeout_s=20), b.js("document.getElementById('view-apps').innerText")
            assert len(core.builds) == before + 1
            label = "Yes" if answer == "yes" else "No"
            card = core.cards[core.builds[-1]["consent"]["id"]]
            _click_until(b, lambda: _button(b, "#view-apps .card.yes", goal, label), lambda: card["status"] != "pending")
            end = time.monotonic() + 10
            while time.monotonic() < end and card["status"] == "pending":
                time.sleep(0.1)
            time.sleep(1.5)                                   # time for any second POST to land
            assert card["answers"] == [answer]
            assert len(core.builds) == before + 1, core.plans
            return core.builds[-1]["id"]

        yes_ids = [go_and_answer("a recipe box for my family", "yes")]
        go_and_answer("invoices for a small shop", "no")
        yes_ids.append(go_and_answer("a birdwatching log", "yes"))
        yes_ids.append(go_and_answer("Add recipe-box from GitHub", "yes", typed="https://github.com/example/recipe-box"))

        # the GitHub block on the same page: one address, No
        before = len(core.builds)
        b.js("location.hash = '#apps/add'")
        field = "[data-gh=code] input"
        assert b.wait(f"!!document.querySelector({json.dumps(field)})", timeout_s=20)
        b.js(f"document.querySelector({json.dumps(field)}).value = 'https://github.com/example/bird-log'")
        add = "Array.from(document.querySelectorAll('[data-gh=code] button')).find(x => x.textContent.indexOf('Add as a new app') >= 0)"
        _click_until(b, lambda: b.js(f"""(() => {{ const g = {add}; g.scrollIntoView({{block: 'center'}});
          const r = g.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; }})()"""),
                     lambda: len(core.builds) > before, busy_js=f"{add}.disabled")
        assert b.wait("Array.from(document.querySelectorAll('[data-github] .card.yes')).some(c => c.textContent.indexOf('bird-log') >= 0)",
                      timeout_s=20), b.js("document.querySelector('[data-github]').innerText")
        gh_card = core.cards[core.builds[-1]["consent"]["id"]]
        _click_until(b, lambda: _button(b, "[data-github] .card.yes", "bird-log", "No"), lambda: gh_card["status"] != "pending")
        time.sleep(1.5)
        assert core.cards[core.builds[-1]["consent"]["id"]]["answers"] == ["no"]
        assert len(core.builds) == before + 1, core.plans

        # five Go presses, five builds, each goal once
        assert [g for _, g in core.plans] == ["a recipe box for my family", "invoices for a small shop", "a birdwatching log",
                                             "Add recipe-box from GitHub", "Add bird-log from GitHub"], core.plans
        # one "Your plan" notice per Yes, none for a No
        count = "(id) => document.querySelectorAll('#notices [data-plan-yes=\"' + id + '\"]').length"
        assert b.wait(f"[{','.join(json.dumps(i) for i in yes_ids)}].every(id => ({count})(id) === 1)", timeout_s=10), \
            b.js("document.getElementById('notices').innerText")
        assert b.js("document.querySelectorAll('#notices [data-plan-yes]').length") == 3

        # the same build's card said Yes to again (as from Building or the chat panel): still one notice
        first = core.builds[0]
        with core.lock:
            core.cards[first["consent"]["id"]]["status"] = "pending"
        b.js(f"""(() => {{ const box = document.createElement('div'); box.id = 'again-box';
          document.getElementById('main').prepend(box); Puff.consentInline(box, {json.dumps(first['consent']['id'])}); }})()""")
        assert b.wait("!!document.querySelector('#again-box .card.yes')")
        b.click(*_button(b, "#again-box .card.yes", first["goal"], "Yes"))
        assert b.wait("document.querySelector('#again-box').textContent.indexOf('You said Yes.') >= 0")
        time.sleep(1.5)
        assert b.js(f"({count})({json.dumps(first['id'])})") == 1
        assert b.js("document.querySelectorAll('#notices [data-plan-yes]').length") == 3
        assert len(core.builds) == 5, core.plans
    finally:
        if b is not None:
            b.close()                                       # our own browser job only
        core.close()
