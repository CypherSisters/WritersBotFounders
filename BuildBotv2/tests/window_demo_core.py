r"""A demo of the PuffPAI window (D81) on a real Core, to look at it, never for her real bot.

    .venv\Scripts\python.exe tests\window_demo_core.py            Core and seed, prints the sign-in link, Ctrl+C ends it
    .venv\Scripts\python.exe tests\window_demo_core.py --shots    the same, then headless Edge takes the screenshots

A real Core on free ports (never 8420-8422), its state in ``C:\tmp\window-ui\state``, wiped at each start, with the
guards of ``console_core`` (no keyring, fake DPAPI, no network).

Real: the Core, its routes, the Registry, Steward, the install Yes cards, the vault, Drawbridge's review, the chat loop and
its open event. Seeded through those routes: a bot name, a vault, FreshTable, Calendar, three looks and one small app of
its own whose row is then marked adopted (only its origin and its manifest file are set by hand), and two chat turns.

Stubbed: the brain, a fake Ollama on a free port that streams fixed markdown and asks for the same tools a real model
would. The plan card is made with the host's own ``agent_plan`` (console_core's store plan), since Toolsmith's planning
needs a real brain.
"""

from __future__ import annotations

import base64
import http.client
import http.server
import json
import os
import re
import shutil
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "core"), str(REPO / "tests")]
SCRATCH = Path(os.environ.get("PUFFPAI_DEMO_SCRATCH") or r"C:\tmp\window-ui")
STATE = SCRATCH / "state"
PIN = "demo-pin-48291"
os.environ["PUFFPAI_STATE"] = str(STATE)
os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"
os.environ.pop("PUFFPAI_BOT", None)

import console_core  # noqa: E402,F401 - its guards, on import
from puffpai_core import cli, pairing  # noqa: E402
from puffpai_core.api import app as app_mod  # noqa: E402

ANSWER = ("Here is what I can see.\n\n**FreshTable** has 4 items on the shopping list:\n\n- eggs\n- oat milk\n- coffee\n"
          "- lemons\n\nAsk me to add one, or say `show me freshtable`.")
OUTSIDE_PAGE = """<!doctype html><title>Pass Times</title><h1>Pass Times</h1>
<table><tr><th>Date</th><th>Rises</th><th>Visible</th></tr><tr><td>Oct 29</td><td>7:41 pm NW</td><td>5 min</td></tr>
<tr><td>Oct 30</td><td>6:52 pm WNW</td><td>6 min</td></tr></table>"""
OUTSIDE_SERVER = """import http.server, sys
PAGE = %r.encode()
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.send_header("Content-Length", str(len(PAGE))); self.end_headers(); self.wfile.write(PAGE)
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
""" % OUTSIDE_PAGE


def free_ports(n: int) -> list[int]:
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    assert not set(ports) & {8420, 8421, 8422, 11434, 11435}
    return ports


class StubBrain:
    """Answers like Ollama. A words-only answer streams; "show me <word>" and "add <x>" ask for the real tools."""

    def __init__(self) -> None:
        stub = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def _json(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/api/version":
                    return self._json({"version": "0.0.0-demo"})
                if self.path == "/api/tags":
                    return self._json({"models": [{"name": "qwen3:8b", "model": "qwen3:8b", "size": 4_700_000_000,
                                                   "details": {"parameter_size": "8B"}}]})
                if self.path == "/api/ps":
                    return self._json({"models": [{"name": "qwen3:8b"}]})
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path == "/api/show":
                    return self._json({"details": {"format": "gguf"}})
                if self.path != "/api/chat":
                    self.send_response(404)
                    self.end_headers()
                    return None
                lines = stub.reply(body)
                if not body.get("stream"):
                    text = "".join(x["message"].get("content", "") for x in lines)
                    calls = [c for x in lines for c in x["message"].get("tool_calls", [])]
                    return self._json({"message": {"role": "assistant", "content": text, "tool_calls": calls}, "done": True})
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for x in lines:
                    self.wfile.write(json.dumps(x).encode() + b"\n")
                    self.wfile.flush()
                    time.sleep(0.01)
                return None

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @staticmethod
    def reply(body: dict) -> list[dict]:
        msgs = body.get("messages") or []
        users = [m for m in msgs if m.get("role") == "user"]
        said = str(users[-1].get("content") or "").lower() if users else ""
        after_tool = bool(msgs) and msgs[-1].get("role") == "tool"
        last_tool = str(msgs[-1].get("tool_name") or "") if after_tool else ""
        names = [str((t.get("function") or {}).get("name") or "") for t in body.get("tools") or []]
        def call(name, args):
            return [{"message": {"content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}, "done": True}]
        m = re.search(r"show me (?:my |the )?([a-z0-9-]+)", said)
        if m and not after_tool and "open_app" in names:
            return call("open_app", {"id": m.group(1)})
        m = re.search(r"add (.+?) to the shopping list", said)
        tool = next((n for n in names if n.endswith("add_shopping")), None)
        if m and not last_tool.endswith("add_shopping"):
            if tool:
                return call(tool, {"name": m.group(1)})
            if last_tool != "open_package" and "open_package" in names:
                return call("open_package", {"id": "freshtable"})
        text = "Done. FreshTable has it now." if "shopping list" in said and after_tool else "Here it is." if after_tool else ANSWER
        pieces = re.findall(r"\S+\s*|\s+", text)
        return [{"message": {"content": p}, "done": False} for p in pieces] + [{"message": {"content": ""}, "done": True}]


class Owner:
    """A paired browser, through the Core's real routes, the way the page calls them."""

    def __init__(self, port: int) -> None:
        self.port, self.cookie, self.key = port, "", ""
        got = self.call("POST", "/v1/pair", {"code": pairing.new_code(STATE)})
        assert got[0] == 200, got
        self.key = got[1]["device_key"]

    def call(self, method: str, path: str, body=None, timeout: float = 120.0):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        host = f"puffpai.localhost:{self.port}"
        headers = {"Host": host, "Origin": f"http://{host}", "X-PuffPAI": "1"}
        if self.key:
            headers["X-PuffPAI-Device"] = self.key
        if self.cookie:
            headers["Cookie"] = self.cookie
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        c.request(method, path, data, headers)
        r = c.getresponse()
        raw = r.read()
        for k, v in r.getheaders():
            if k.lower() == "set-cookie":
                self.cookie = v.split(";", 1)[0]
        c.close()
        try:
            return r.status, json.loads(raw) if raw else None
        except ValueError:
            return r.status, raw.decode("utf-8", "replace")

    def add(self, source: str) -> str:
        st, got = self.call("POST", "/v1/packages", {"source": source})
        assert st == 202, (source, st, got)
        cid = got["consent_id"]
        _st, cards = self.call("GET", "/v1/consent?status=pending")
        card = next(c for c in cards if (c.get("consent_id") or c.get("id")) == cid)
        st, ans = self.call("POST", f"/v1/consent/{cid}/answer", {"answer": "yes", "factor": "tap", "card_sha": card.get("card_sha")})
        assert st < 300, (source, st, ans)
        end = time.monotonic() + 300
        while time.monotonic() < end:
            st, row = self.call("GET", f"/v1/registry/{got['id']}")
            if st == 200 and row.get("status") not in ("installing", None):
                return got["id"]
            time.sleep(0.5)
        raise AssertionError(f"{source} did not finish installing")

    def turn(self, text: str) -> None:
        self.call("POST", "/v1/chat/stream", {"text": text, "conversation": "main"}, timeout=60)


DEMO: dict = {}


def live_jobs() -> None:
    """The Building view's states for a look at them: Toolsmith's own status route, by the host, with made-up lines."""
    host, build = DEMO["host"], DEMO["build"]
    jobs = host._build(build)["jobs"]  # noqa: SLF001
    host.agent_job_status(build, jobs[0]["id"], {"state": "working", "line": "Proved: it answers its sample ask."})
    host.agent_job_status(build, jobs[1]["id"], {"state": "building", "trying": "an-example-app", "now": "Its page answers. Proving it now."})


def make_outside_app() -> Path:
    folder = SCRATCH / "outside-app"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    (folder / "server.py").write_text(OUTSIDE_SERVER, encoding="utf-8")
    (folder / "puffpai.json").write_text(json.dumps({
        "schema": "puffpai-package/1", "id": "pass-times", "kind": "app", "name": "Pass Times", "version": "1.0.0",
        "description": "A small page for the demo: an outside app's row.", "author": "Cypher Sisters", "license": "Apache-2.0",
        "run": {"default": {"type": "command", "python": True, "start": ["{python}", "server.py", "{port}"]}},
        "health": {"url": "http://127.0.0.1:{port}/", "expect": 200, "every_s": 60},
        "ui": {"pages": [{"id": "main", "title": "Pass Times", "url": "http://127.0.0.1:{port}/", "show": "new_tab"}]},
        "consent": "A demo page on this computer. Nothing leaves your computer without your consent.",
    }, indent=1), encoding="utf-8")
    return folder


def seed(app, port: int, ready: threading.Event, failed: list) -> None:
    """Everything through the real routes, then one plan card the host's own way."""
    try:
        o = Owner(port)
        st, got = o.call("POST", "/v1/vault/setup", {"pin": PIN})
        assert st == 201, (st, got)
        st, got = o.call("PUT", "/v1/settings/bot", {"name": "SpaceBot", "theme": "clean"})
        assert st == 200, (st, got)
        for source in ("freshtable", "calendar", "look-slate", "look-paper", "look-contrast", str(REPO / "tests" / "fixtures" / "hello-tool"), str(make_outside_app())):
            o.add(source)
        # An adopted app's row, with its one hand-set field: its origin. Everything else about it really runs.
        core = app.extensions["puffpai"]
        installed = STATE / "packages" / "pass-times"
        origin = "adopted:https://github.com/example/pass-times"
        (installed / ".buildbot").mkdir(exist_ok=True)
        manifest = json.loads((installed / "puffpai.json").read_text(encoding="utf-8"))
        (installed / ".buildbot" / "puffpai.json").write_text(json.dumps({**manifest, "origin": origin}, indent=1), encoding="utf-8")
        from puffpai_core.packages.manifest import load_with_sha
        core.registry.update("pass-times", origin=origin, manifest_sha=load_with_sha(installed, adopted=True)[2])
        end = time.monotonic() + 60
        while time.monotonic() < end:            # running, whatever Caretaker did with it meanwhile
            st, row = o.call("GET", "/v1/registry/pass-times")
            if st == 200 and row.get("status") == "running":
                break
            o.call("POST", "/v1/packages/pass-times/start", {})
            time.sleep(1)
        o.turn("what's on the shopping list?")
        o.turn("add basil to the shopping list")
        host = app_mod.start_toolsmith(app)
        job = {"name": "Reminders", "does": "remind me when it is time", "sample_ask": "Remind me at 7 pm"}
        job2 = {"name": "A page for it", "does": "show what is coming up", "sample_ask": "What is coming up?"}
        b = host.store.create("remind me when it is time", [job, job2], host.settings())
        offer = {"package": "alarmflow", "name": "AlarmFlow", "tool": "alarmflow.create_alarm",
                 "picked": [{"ask": job["sample_ask"], "tool": "alarmflow.create_alarm"}]}
        host.agent_plan(b["id"], {"jobs": [{**job, "plan": {"candidates": [], "store": offer}}, {**job2, "plan": {"candidates": []}}]})
        DEMO.update(host=host, build=b["id"])
        ready.set()
    except Exception as exc:  # noqa: BLE001 - said by the main thread
        failed.append(repr(exc))
        ready.set()


def _wait_core(port: int) -> None:
    end = time.monotonic() + 90
    while time.monotonic() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError("the Core did not answer")


# -- the screenshots: headless Edge in its own job, driven over DevTools ---------------------------------------------
class Browser:
    def __init__(self, width=1200, height=850) -> None:
        from puffpai_core.steward import jobs
        from puffpai_core.zone2.page_reader import _devtools_port, _Socket
        edge = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        profile = SCRATCH / "edge"
        shutil.rmtree(profile, ignore_errors=True)
        profile.mkdir(parents=True)
        self.job = jobs.Job.spawn([edge, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                   "--disable-extensions", "--disable-sync", "--disable-background-networking", "--disable-component-update",
                                   "--no-proxy-server", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
                                   f"--user-data-dir={profile}", f"--window-size={width},{height}", "about:blank"],
                                  profile, dict(os.environ), None)
        dev = _devtools_port(str(profile), self.job.proc)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{dev}/json/new?about:blank", method="PUT"), timeout=10) as resp:
            target = json.loads(resp.read(65536))
        self.ws = _Socket(str(target["webSocketDebuggerUrl"]))
        self.size(width, height)
        self.ws.call("Runtime.enable", {})

    def size(self, width, height) -> None:
        self.ws.call("Emulation.setDeviceMetricsOverride", {"width": width, "height": height, "deviceScaleFactor": 1, "mobile": False})

    def js(self, expression: str):
        res = self.ws.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in res:
            raise AssertionError(res["exceptionDetails"])
        return (res.get("result") or {}).get("value")

    def wait(self, expression: str, timeout_s: float = 30.0):
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            try:
                got = self.js(expression)
            except AssertionError:
                got = None
            if got:
                return got
            time.sleep(0.2)
        raise AssertionError("never true: " + expression + "\nPage errors: " + str(self.js("window.__errors")) + "\n"
                             + str(self.js("document.getElementById('view').innerText.slice(0, 800)")))

    def shot(self, name: str) -> None:
        time.sleep(0.6)
        got = self.ws.call("Page.captureScreenshot", {"format": "png"})
        (SCRATCH / name).write_bytes(base64.b64decode(got["data"]))
        print("  " + str(SCRATCH / name), flush=True)

    def press(self, selector: str, text: str = "") -> None:
        self.wait(f"""(() => {{ const n = Array.from(document.querySelectorAll({json.dumps(selector)})).find(x => x.textContent.indexOf({json.dumps(text)}) >= 0);
          if (!n) return false; n.scrollIntoView({{block: 'nearest'}}); n.click(); return true; }})()""")

    def type(self, text: str) -> None:
        self.js(f"""(() => {{ const i = document.getElementById('input'); i.focus(); i.value = {json.dumps(text)}; i.dispatchEvent(new Event('input', {{bubbles: true}})); }})()""")

    def send(self, text: str) -> None:
        self.type(text)
        self.js("document.getElementById('composer').requestSubmit()")

    def close(self) -> None:
        self.ws.close()
        self.job.terminate(wait_s=10)
        self.job.close()


def shots(url: str, pages_port: int) -> None:
    b = Browser()
    base = url.split("/window/")[0]
    try:
        b.ws.call("Page.enable", {})
        b.ws.call("Page.addScriptToEvaluateOnNewDocument", {"source": "window.__errors = []; "
                  "addEventListener('error', (e) => __errors.push(String(e.message))); "
                  "addEventListener('unhandledrejection', (e) => __errors.push(String(e.reason && e.reason.stack || e.reason)));"})
        b.ws.call("Page.navigate", {"url": url})
        b.wait("!document.getElementById('win').hidden && document.querySelectorAll('[data-rail-app]').length >= 3")
        assert "pair=" not in b.js("location.href")
        assert b.js("localStorage.getItem('puffpai.device_key').length > 10")
        b.wait("(document.querySelector('[data-tabview=\"page:home\"]') || {innerText: ''}).innerText.indexOf('SpaceBot') >= 0")
        b.wait("!!document.querySelector('[data-card]') && document.querySelector('.w-log:not([hidden])').innerText.indexOf('Changed: freshtable.add_shopping') >= 0")
        b.js("PuffTheme.set('day')")
        b.shot("clean_day_plan_and_app.png")

        # "show me": the open event opens a side pane tab for a built page, and a browser tab and a card for an outside app
        b.send("show me calendar")
        b.wait("!!document.querySelector('[data-tab=\"app:calendar\"].on .w-tabname')")
        b.wait("document.querySelectorAll('[data-tabview=\"app:calendar\"] iframe').length === 1")
        assert b.js("document.querySelector('[data-tabview=\"app:calendar\"] iframe').src").startswith(f"http://calendar.localhost:{pages_port}/")
        b.shot("clean_day_show_me.png")
        b.js("window.open = (u) => { window.__opened = (window.__opened || []).concat([u]); return null; }")
        b.press("[data-rail-app='pass-times']")
        b.wait("(document.querySelector('[data-tabview=\"app:pass-times\"]') || {innerText: ''}).innerText.indexOf('read its page') >= 0", 60)
        assert not b.js("document.querySelectorAll('[data-tabview=\"app:pass-times\"] iframe').length"), "an outside app was framed"
        assert b.js("(window.__opened || []).length") == 1, "the outside app did not open in the browser"
        b.shot("clean_day_outside_card.png")
        b.press("[data-rail-app='hello-tool']")
        b.wait("!!document.querySelector('[data-plain-card=\"hello-tool\"]')")
        assert b.js("(window.__opened || []).length") == 1, "a tool with no page opened a browser tab"
        b.press("[data-open-dash=rail]")
        assert b.js("window.__opened[window.__opened.length - 1]") == "/", "the dashboard did not open in a new tab"
        b.shot("clean_day_tool_card.png")
        b.js("delete window.open")

        # inside the shell: every way out goes through its api, and window.open is never used
        b.js("window.__shell = []; window.pywebview = {api: {open_browser: (u) => __shell.push(u), open_dashboard: () => Promise.resolve(__shell.push('dashboard') && {ok: true, line: ''})}}")
        b.press("[data-rail-app='pass-times']")
        b.wait("window.__shell.length === 1 && window.__shell[0].indexOf('http://127.0.0.1:') === 0")
        b.press("[data-open-dash=tabs]")
        assert b.js("window.__shell[1]") == "dashboard"
        b.js("delete window.pywebview")

        # the / list, searchable by any word
        b.type("/password")
        b.wait("!document.getElementById('palette').hidden && document.getElementById('palette').innerText.indexOf('/vault') >= 0")
        b.shot("clean_day_palette.png")
        b.type("")
        b.js("document.getElementById('palette').hidden = true")

        # the switch (D115): Window is where she is, Terminal asks the Core for the chat she is in (no console opens here),
        # Dashboard opens the same way as Open full dashboard
        assert b.js("document.querySelector('[data-switch=window]').classList.contains('on')")
        asked = []
        cli.open_terminal = lambda st, chat: asked.append(chat) or (True, cli.TERMINAL_OPENING)
        b.press("[data-switch=terminal]")
        b.wait("document.getElementById('toast').textContent === " + json.dumps(cli.TERMINAL_OPENING))
        assert asked == ["main"], asked
        b.shot("clean_day_switch.png")
        b.js("window.open = (u) => { window.__opened = [u]; return null; }")
        b.press("[data-switch=dashboard]")
        assert b.js("window.__opened[0]") == "/", "the switch did not open the dashboard"
        b.js("delete window.open")

        # the store (D115), in the side pane: its shelves, the one Yes card, and what it adds shows in the pane
        b.press("[data-rail=store]")
        b.wait("!!document.querySelector('[data-tab=store].on') && !!document.querySelector('[data-store-row]')")
        b.wait("document.querySelector('[data-store]').innerText.indexOf('Reading the store') < 0")
        kinds = b.js("Array.from(document.querySelectorAll('[data-store-section]')).map((n) => n.textContent)")
        assert "Apps" in kinds and "Avatars" in kinds, kinds
        b.shot("clean_day_store.png")
        b.press("[data-store-add=trivia]")
        b.wait("!!document.querySelector('[data-tabview=store] [data-store-row=trivia] [data-card]')")
        assert not b.js("document.querySelector('[data-chat].on') && document.querySelector('.w-log:not([hidden]) [data-card*=\"' + document.querySelector('[data-tabview=store] [data-store-row=trivia] [data-card]').getAttribute('data-card') + '\"]')"), "its card is in the chat too"
        b.shot("clean_day_store_yes_card.png")
        b.press("[data-tabview=store] [data-store-row=trivia] [data-card] .w-btn.primary")
        b.wait("document.querySelector('[data-tabview=store] [data-store-row=trivia]').innerText.indexOf('is installed') >= 0", 180)
        assert not b.js("document.querySelector('[data-store-add=trivia]')"), "Add stayed after it was added"
        b.press("[data-tabview=store] [data-store-row=trivia] .w-chipbtn")
        b.wait("!!document.querySelector('[data-tabview=\"app:trivia\"] iframe')", 60)
        assert b.js("document.querySelector('[data-tabview=\"app:trivia\"] iframe').src").startswith(f"http://trivia.localhost:{pages_port}/"), "a store app did not open in the pane"
        b.shot("clean_day_store_app_in_pane.png")
        b.press("[data-rail=store]")
        b.press("[data-rail=settings]")
        b.press("[data-add-look]")
        b.wait("!!document.querySelector('[data-tab=store].on')")

        # every dashboard page draws in the side pane (D115), from the same page files
        for view, word in (("home", "SpaceBot"), ("knowledge", "Your library"), ("apps", "Trivia"), ("people", "Who lives here"), ("agents", "Toolsmith"),
                           ("outside", "Bridge"), ("developer", "A folder of my code")):
            b.press(f"[data-rail={view}]")
            b.wait(f"(document.querySelector('[data-tabview=\"page:{view}\"]') || {{innerText: ''}}).innerText.indexOf({json.dumps(word)}) >= 0", 40)
            b.wait(f"document.querySelector('[data-tab=\"page:{view}\"].on') !== null")
            assert "could not be drawn" not in b.js(f"document.querySelector('[data-tabview=\"page:{view}\"]').innerText"), view
            time.sleep(1.0)
            b.shot(f"page_{view}.png")
        b.press("[data-status=vault]")
        b.wait("(document.querySelector('[data-tabview=\"page:vault\"]') || {innerText: ''}).innerText.indexOf('Your vault') >= 0")
        b.shot("page_vault.png")
        b.press("[data-status=brain]")
        b.wait("(document.querySelector('[data-tabview=\"page:settings\"]') || {innerText: ''}).innerText.indexOf('brain') >= 0", 40)
        b.shot("page_settings.png")
        b.press("[data-rail=settings]")
        b.press("button", "Looks and avatars")
        b.wait("!!document.querySelector('[data-tabview=\"page:looks\"] .swatches')", 40)
        b.shot("page_looks.png")
        b.press("[data-tabview=\"page:knowledge\"] a", "") if False else None
        assert not b.js("window.__errors.length"), b.js("window.__errors")
        for _ in range(12):
            if b.js("!document.querySelector('[data-tab^=\"page:\"] .w-x')"):
                break
            b.js("document.querySelector('[data-tab^=\"page:\"] .w-x').click()")

        # D96: a Store tab is not brought back, so the window reads no store at start
        b.ws.call("Page.reload", {})
        b.wait("!document.getElementById('win').hidden && !!document.querySelector('[data-rail=store]') && !!document.querySelector('.w-tablist')")
        time.sleep(1.5)
        assert not b.js("!!document.querySelector('[data-tabview=store]')"), "a Store tab came back at start"
        assert not b.js("performance.getEntriesByType('resource').filter((e) => e.name.indexOf('/v1/store') >= 0 || e.name.indexOf('/v1/packages/available') >= 0).length"), "the store was read at start"
        assert not b.js("window.__errors.length"), b.js("window.__errors")

        # Settings, with Toolsmith's cloud model; picking a company shows its consent, model, key and cap
        b.press("[data-rail=settings]")
        b.wait("!!document.querySelector('[data-cloud=company]') && !!document.querySelector('[data-look].on')")
        b.shot("clean_day_settings.png")
        b.js("(() => { const c = document.querySelector('[data-cloud=company]'); c.value = c.options[1].value; c.dispatchEvent(new Event('change')); document.getElementById('set-cloud').scrollIntoView(); })()")
        b.wait("!!document.querySelector('[data-cloud=consent]')")
        b.shot("clean_day_settings_cloud_model.png")

        # a key: typed into a password field, cleared when sent, and shown back only as "a key is saved"
        secret = "demo-key-not-real-0123456789"
        b.js(f"(() => {{ document.querySelector('[data-cloud=key]').value = {json.dumps(secret)}; document.querySelector('[data-cloud=agree]').checked = true; document.querySelector('[data-cloud=save]').click(); }})()")
        b.wait("(document.querySelector('[data-cloud=key-state]') || {textContent: ''}).textContent.indexOf('A key is saved') === 0")
        assert secret not in b.js("document.documentElement.outerHTML") and not b.js("document.querySelector('[data-cloud=key]').value")
        assert secret not in b.js("JSON.stringify(localStorage)")
        b.shot("clean_day_settings_key_saved.png")
        b.js("(() => { const c = document.querySelector('[data-cloud=company]'); c.value = ''; c.dispatchEvent(new Event('change')); document.querySelector('[data-cloud=save]').click(); })()")
        b.wait("document.querySelector('[data-said=cloud]').textContent.indexOf('this computer') >= 0")

        # a dark look, the same page: one click on its card
        b.press("[data-look=look-slate]")
        b.wait("!!document.querySelector('[data-look=look-slate].on')")
        b.press("[data-daynight=night]")
        b.js("document.querySelector('[data-tabview=settings]').scrollTop = 0")
        b.shot("slate_night_settings.png")
        b.press("[data-rail-app=freshtable]")
        b.wait("!!document.querySelector('[data-tabview=\"app:freshtable\"] iframe')")
        b.shot("slate_night_plan_and_app.png")

        # every other look reads through its own tokens, with nothing window-specific
        b.press("[data-rail=settings]")
        for look in ("look-paper", "look-contrast", "puff"):
            b.press(f"[data-look={look}]")
            b.wait(f"!!document.querySelector('[data-look={look}].on')")
            b.shot(f"{look}_night_settings.png")
        b.press("[data-look=clean]")
        b.wait("!!document.querySelector('[data-look=clean].on')")
        b.press("[data-daynight=night]")
        b.shot("clean_night_settings.png")

        # the Your turn pane, then the pane closed: the chat takes the room
        b.press("[data-rail=turn]")
        b.wait("document.querySelector('[data-tabview=turn]').innerText.indexOf('Waiting for your Yes') >= 0")
        b.shot("clean_night_your_turn.png")
        for _ in range(8):
            if b.js("!document.querySelector('.w-x')"):
                break
            b.js("document.querySelector('.w-x').click()")
        assert b.js("document.getElementById('win').classList.contains('w-nopane')")
        b.shot("clean_night_pane_closed.png")

        # Build this plan: the real Yes, in the chat, then the Building pane
        b.press("[data-card] .w-btn.primary", "Build this plan")
        b.wait("!!document.querySelector('[data-tabview=building]')")
        for _ in range(60):                # Toolsmith's own process may say held first (it has no real brain here)
            live_jobs()
            if b.js("document.querySelector('[data-tabview=building]').innerText.indexOf('an-example-app') >= 0"):
                break
            time.sleep(0.5)
        b.shot("clean_night_building.png")

        # a new chat, its own conversation; a reload brings back the words from the Core, not from this browser
        b.press("#new-chat")
        b.wait("document.querySelector('[data-chat].on').textContent === 'New chat'")
        b.send("hello there")
        b.wait("document.querySelector('[data-chat].on').textContent === 'hello there'")
        b.press("[data-chat=main]")
        b.ws.call("Page.reload", {})
        b.wait("!document.getElementById('win').hidden && document.querySelector('.w-log:not([hidden])').innerText.indexOf('basil') >= 0")
        store = b.js("JSON.stringify(localStorage)")
        assert "oat milk" not in store and "basil" not in store, "her words were kept in the browser"
        b.shot("clean_night_after_reload.png")
        assert not b.js("window.__errors.length"), b.js("window.__errors")

        # sign-in. Signed in already: a code in the address leaves it, and is not used up
        code = pairing.new_code(STATE)
        b.ws.call("Page.navigate", {"url": base + "/window/#pair=" + code})
        b.wait("!document.getElementById('win').hidden")
        b.wait("location.hash === ''")
        assert pairing.redeem(STATE, code), "a signed-in page used up the code in its address"
        # Signed out: the one plain screen, and a used code says so
        b.js("localStorage.removeItem('puffpai.device_key')")
        b.ws.call("Page.navigate", {"url": base + "/window/#pair=" + code})
        b.wait("!document.getElementById('signin').hidden && document.getElementById('signin').innerText.indexOf('already used') >= 0")
        assert b.js("location.hash") == "" and b.js("document.getElementById('win').hidden")
        b.shot("sign_in.png")
        print("  driver: every step passed", flush=True)
    finally:
        b.close()


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    take_shots = "--shots" in sys.argv
    SCRATCH.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(STATE, ignore_errors=True)
    (STATE / "settings").mkdir(parents=True)
    brain = StubBrain()
    (STATE / "settings" / "model.json").write_text(json.dumps({"address": brain.address, "model": "qwen3:8b"}), encoding="utf-8")
    (STATE / "settings" / "hardware.json").write_text(json.dumps({"hardware_class": "desktop", "memory": {"total_gb": 16}, "disk": {"free_gb": 200}}), encoding="utf-8")
    (STATE / "settings" / "wizard.json").write_text(json.dumps({"complete": True}), encoding="utf-8")
    port, pages_port, proxy_port = free_ports(3)
    ready, failed = threading.Event(), []
    real = app_mod.start_core

    def start_core(app):
        got = real(app)
        threading.Thread(target=seed, args=(app, port, ready, failed), daemon=True).start()
        return got
    app_mod.start_core = start_core

    def after():
        ready.wait()
        if failed:
            print("The seed failed: " + failed[0], flush=True)
            os._exit(1)
        url = f"http://puffpai.localhost:{port}/window/#pair={pairing.new_code(STATE)}"
        print(f"\nThe window (the link works once, for 10 minutes):\n  {url}\nPID {os.getpid()}. Ctrl+C ends it.\n", flush=True)
        if take_shots:
            code = 0
            try:
                shots(url, pages_port)
            except BaseException as exc:  # noqa: BLE001 - said, then the demo ends
                print("The driver stopped: " + str(exc)[:1800], flush=True)
                code = 1
            os._exit(code)
    threading.Thread(target=after, daemon=True).start()
    from puffpai_core.cli import main as cli_main
    return cli_main(["serve", "--port", str(port), "--pages-port", str(pages_port), "--proxy-port", str(proxy_port)])


if __name__ == "__main__":
    sys.exit(main())
