"""Lane I10a: every link in the Desktop, clicked with a real mouse in a real browser, lands on the Desktop.

The bug it catches: ``<base href="/ui/">`` in index.html sent every ``href="#apps"`` link to ``/ui/#apps``,
which the Core answers with its JSON 404. A hash change in a script would not have shown it; a real click does.

A real Core runs ``serve`` on free ports (never 8420-8422) in a Job Object this test makes (``console_kit``),
with example-page installed so the rail has an app's tray item. Headless Edge runs in a Job Object of its own
(``steward.jobs``) and is driven over DevTools on 127.0.0.1: each rail link, top bar chip, the private card
and every link inside the page is clicked with ``Input.dispatchMouseEvent`` at its place on the screen.
Cleanup is each job's own TerminateJobObject, nothing else.

The second test, with setup finished: the rail fits the bot, the PuffPAI store on Home (Not now is kept)
and on Apps, the bot's name (Puff until named) and the Looks page, all by real clicks.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EDGE = next((p for p in (os.path.join(os.environ.get(v, ""), "Microsoft", "Edge", "Application", "msedge.exe")
                         for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")) if os.path.isfile(p)), None)
pytestmark = pytest.mark.skipif(os.name != "nt" or EDGE is None, reason="Windows Job Objects and Microsoft Edge")


def _free_ports(n):
    import socket
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    assert not set(ports) & {8420, 8421, 8422}
    return ports


def _env(**more):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST", "PUFFPAI_"))}
    env.update(PYTHONUNBUFFERED="1", PIP_REQUIRE_VIRTUALENV="1", PYTHON_KEYRING_BACKEND="keyring.backends.fail.Keyring",
               PYTHONPATH=os.pathsep.join([str(REPO / "core"), str(REPO / "tests")]), **more)
    return env


def _wait_ready(state: Path, job, timeout_s=90.0) -> dict:
    from puffpai_core.cli import _call
    key = state / "settings" / "terminal.json"
    end = time.monotonic() + timeout_s
    while time.monotonic() < end and job.proc.poll() is None:
        if key.is_file():
            try:
                info = json.loads(key.read_text(encoding="utf-8"))
                if _call(info, "GET", "/v1/health")[0] == 200:
                    return info
            except (OSError, ValueError):
                pass
        time.sleep(0.5)
    raise AssertionError("the Core did not get ready in time")


class Browser:
    """Headless Edge in its own job, one tab, driven over DevTools on 127.0.0.1."""

    def __init__(self, profile: Path):
        from puffpai_core.steward import jobs
        from puffpai_core.zone2.page_reader import _devtools_port
        profile.mkdir(parents=True, exist_ok=True)
        self.job = jobs.Job.spawn(
            [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--disable-extensions", "--disable-sync", "--disable-background-networking", "--disable-component-update",
             "--no-proxy-server", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0",
             f"--user-data-dir={profile}", "--window-size=1400,1000", "about:blank"],
            profile, dict(os.environ), None)
        if not self.job.contained:
            self.job.close()
            raise AssertionError("Windows could not hold the browser in a job, so the test did not run it.")
        self.ws = None
        port = _devtools_port(str(profile), self.job.proc)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{port}/json/new?about:blank", method="PUT"),
                         timeout=10) as resp:
            target = json.loads(resp.read(65536))
        from puffpai_core.zone2.page_reader import _Socket
        self.ws = _Socket(str(target["webSocketDebuggerUrl"]))
        self.ws.call("Emulation.setDeviceMetricsOverride", {"width": 1400, "height": 1000, "deviceScaleFactor": 1,
                                                            "mobile": False})

    def js(self, expression: str):
        res = self.ws.call("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
        if "exceptionDetails" in res:
            raise AssertionError(f"the page's script failed: {res['exceptionDetails']}")
        return (res.get("result") or {}).get("value")

    def wait(self, expression: str, timeout_s: float = 20.0):
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            try:
                got = self.js(expression)
            except AssertionError:
                got = None
            if got:
                return got
            time.sleep(0.2)
        return None

    def click(self, x: float, y: float) -> None:
        base = {"x": x, "y": y, "button": "left", "clickCount": 1}
        self.ws.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.ws.call("Input.dispatchMouseEvent", {"type": "mousePressed", **base})
        self.ws.call("Input.dispatchMouseEvent", {"type": "mouseReleased", **base})

    def close(self) -> None:
        if self.ws is not None:
            self.ws.close()
        self.job.terminate(wait_s=10)
        self.job.close()
        time.sleep(0.5)


#: Where the browser is, and what it shows. JSON text in the body means the Core's 404 page.
WHERE = """(() => ({path: location.pathname, hash: location.hash, desktop: !!document.getElementById('rail'),
  type: document.contentType, body: (document.body ? document.body.innerText : '').slice(0, 200),
  shown: Array.from(document.querySelectorAll('#main .view')).filter(v => !v.hidden).map(v => v.id)}))()"""

#: The links to click, as [selector, index, href]: the rail, the chips, the private card, and every
#: "#..." link inside the view on screen (the tiles' links).
LINKS = """(() => { const out = [];
  const add = (sel) => document.querySelectorAll(sel).forEach((a, i) => {
    const r = a.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && (a.getAttribute('href') || '').startsWith('#')) out.push([sel, i, a.getAttribute('href')]);
  });
  add('.rail a.item'); add('#chips a'); add('a#private');
  add('#main .view:not([hidden]) a');
  return out; })()"""


def _point(b: Browser, sel: str, i: int):
    return b.js(f"""(() => {{ const a = document.querySelectorAll({json.dumps(sel)})[{i}]; if (!a) return null;
      a.scrollIntoView({{block: 'center'}}); const r = a.getBoundingClientRect();
      return [r.left + Math.min(r.width / 2, 20), r.top + r.height / 2]; }})()""")


def _expected_view(href: str) -> str:
    h = href[1:]
    view = h.split("/", 1)[0] or "home"
    return "view-" + view


def _text_point(b: Browser, sel: str, text: str):
    """The place on screen of the first *sel* whose text has *text* in it, or None."""
    return b.js(f"""(() => {{ const a = Array.from(document.querySelectorAll({json.dumps(sel)}))
        .find(n => n.textContent.indexOf({json.dumps(text)}) >= 0 && n.getBoundingClientRect().width > 0);
      if (!a) return null; a.scrollIntoView({{block: 'center'}}); const r = a.getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")


@contextlib.contextmanager
def _desktop(state: Path, tmp_path: Path, monkeypatch, complete: bool = False, env: dict | None = None):
    """A real Core (example-page installed) and a signed-in headless Edge on its Desktop."""
    import console_kit
    from puffpai_core.cli import main
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    assert main(["add", str(REPO / "packages" / "example-page")]) == 0      # an app's tray item in the rail
    if complete:                                                             # setup finished
        (state / "settings").mkdir(parents=True, exist_ok=True)
        (state / "settings" / "wizard.json").write_text(json.dumps({"complete": True}), encoding="utf-8")
    env = _env(PUFFPAI_STATE=str(state), **(env or {}))
    folder = tmp_path / "core"
    folder.mkdir()
    core_port, pages_port, proxy_port = _free_ports(3)
    with open(folder / "serve.txt", "w", encoding="utf-8") as log:
        job = console_kit._held([sys.executable, str(REPO / "tests" / "console_core.py"), "serve", "--port", str(core_port),  # noqa: SLF001
                                 "--pages-port", str(pages_port), "--proxy-port", str(proxy_port)],
                                subprocess.CREATE_NEW_PROCESS_GROUP, env=env, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL)
    browser = None
    try:
        _wait_ready(state, job)
        pair = subprocess.run([sys.executable, str(REPO / "tests" / "console_core.py"), "pair", "--port", str(core_port)],
                              env=env, capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
        url = next(line.strip() for line in pair.stdout.splitlines() if "#pair=" in line)
        assert url.startswith(f"http://puffpai.localhost:{core_port}/#pair=")          # D79: the Desktop's own name
        browser = Browser(tmp_path / "edge")
        browser.ws.call("Page.navigate", {"url": url})
        assert browser.wait("document.querySelectorAll('.rail a.item').length > 5 && "
                            "!!document.getElementById('view-home') && !document.getElementById('view-home').hidden"), \
            browser.js(WHERE)
        assert browser.wait("document.querySelectorAll('#rail-apps a.item').length > 0"), "example-page has no rail item"
        yield browser
    finally:
        if browser is not None:
            browser.close()
        console_kit._end(job)                              # noqa: SLF001 - our own job only


def test_every_desktop_link_clicked_lands_on_the_desktop(state, tmp_path, monkeypatch, drawbridge):
    with _desktop(state, tmp_path, monkeypatch) as browser:
        clicked, seen = [], set()
        queue = [("home", None)]
        while queue:
            at, _ = queue.pop(0)
            browser.js(f"location.hash = '#{at}'")             # only to get back to where a link is; never the thing tested
            browser.wait(f"!!document.getElementById('view-{at.split('/')[0]}') && "
                         f"!document.getElementById('view-{at.split('/')[0]}').hidden")
            time.sleep(1.0)                                       # the view's tiles draw from the Core
            links = browser.js(LINKS)
            for sel, i, href in links:
                key = (at if sel.startswith("#main") else "", sel, href)
                if key in seen:
                    continue
                seen.add(key)
                browser.js(f"location.hash = '#{at}'")
                browser.wait(f"!document.getElementById('view-{at.split('/')[0]}').hidden")
                time.sleep(0.3)
                browser.js("document.querySelectorAll('#main details.act').forEach((d) => { d.open = true; })")   # a row's links
                pt = _point(browser, sel, i)
                if pt is None:
                    continue
                browser.click(*pt)
                want = _expected_view(href)
                ok = browser.wait(f"location.hash === {json.dumps(href)} && !!document.getElementById('rail')"
                                  f" && !!document.getElementById({json.dumps(want)})"
                                  f" && !document.getElementById({json.dumps(want)}).hidden", timeout_s=10)
                where = browser.js(WHERE)
                assert ok, (at, sel, href, where)
                assert where["path"] == "/" and where["type"] == "text/html", (href, where)
                assert "No such route" not in where["body"], (href, where)
                clicked.append((at, href))
                view = href[1:].split("/", 1)[0]
                if sel == ".rail a.item" and view not in ("page", "setup") and (view, None) not in queue:
                    queue.append((view, None))                    # then click every link on that page too
        rail = {h for _, h in clicked}
        for want in ("#home", "#chat", "#apps", "#building", "#yourturn", "#knowledge", "#vault", "#people", "#agents",
                     "#outside", "#looks", "#developer", "#settings", "#setup", "#page/example-page/main"):
            assert want in rail, (want, sorted(rail))
        assert not rail & {"#smarthome", "#talk", "#device"}, sorted(rail)
        assert any(at == "home" and h in ("#apps", "#settings", "#outside", "#building") for at, h in clicked), clicked
        assert len(clicked) >= 20, clicked


RAIL_HREFS = "Array.from(document.querySelectorAll('.rail a.item')).map(a => a.getAttribute('href'))"
NAME_BOX = "#view-looks input[aria-label=\"Your bot's name\"]"


def _hidden(state: Path) -> list:
    try:
        return json.loads((state / "data" / "desktop" / "layout.json").read_text(encoding="utf-8"))["tiles"]["hidden"]
    except (OSError, ValueError, KeyError):
        return []


def _has_text(sel: str, text: str) -> str:
    return f"!!document.querySelector({json.dumps(sel)}) && document.querySelector({json.dumps(sel)}).textContent.indexOf({json.dumps(text)}) >= 0"


def test_after_setup_the_store_question_the_bot_name_and_looks(state, tmp_path, monkeypatch, drawbridge):
    """Setup finished, no bot or themes routes in this Core yet: the name Puff, the Desktop's own palette,
    the PuffPAI store on Home and on Apps, Not now kept in the layout file. Every press is a real click."""
    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        rail = b.js(RAIL_HREFS)
        assert "#setup" not in rail and not set(rail) & {"#smarthome", "#talk", "#device"}, rail
        assert b.js("document.getElementById('botname').textContent") == "Puff"
        assert b.wait("!!document.querySelector('.hello h1') && /^Good (morning|afternoon|evening), I'm Puff[.]$/"
                      ".test(document.querySelector('.hello h1').textContent)"), b.js(WHERE)
        # Home asks once: the store apps, each with its icon and Add
        assert b.wait("!!document.querySelector('.store-card')"), b.js(WHERE)
        card = b.js("""(() => { const c = document.querySelector('.store-card');
          return {h: c.querySelector('h3').textContent, names: Array.from(c.querySelectorAll('.app-row .name')).map(n => n.textContent),
                  icons: Array.from(c.querySelectorAll('.app-row .icon img')).map(i => i.getAttribute('src').slice(0, 26)),
                  adds: c.querySelectorAll('.app-row button.primary').length}; })()""")
        assert card["h"] == "Add apps from the PuffPAI store?", card
        assert "FreshTable" in " ".join(card["names"]) and card["adds"] == len(card["names"]) <= 6, card
        assert "data:image/svg+xml;base64," in card["icons"], card
        b.click(*_text_point(b, ".store-card button", "Not now"))
        assert b.wait("!document.querySelector('.store-card')")
        end = time.monotonic() + 10
        while time.monotonic() < end and "store" not in _hidden(state):
            time.sleep(0.2)
        assert "store" in _hidden(state)                       # kept in the Desktop's layout file
        b.ws.call("Page.reload", {})
        assert b.wait("!!document.querySelector('.hello h1') && !document.getElementById('view-home').hidden")
        time.sleep(2.0)
        assert not b.js("!!document.querySelector('.store-card')")
        # Apps: the PuffPAI store tab
        b.click(*_point(b, ".rail a.item[href='#apps']", 0))
        assert b.wait("!!document.querySelector('#view-apps .tabs button')")
        b.click(*_text_point(b, "#view-apps .tabs button", "PuffPAI store"))
        assert b.wait("location.hash === '#apps/available' && "
                      + _has_text("#view-apps", "Anyone can build an app that works on BuildBot.")), b.js(WHERE)
        names = b.js("Array.from(document.querySelectorAll('#view-apps .tile')[0].querySelectorAll('.app-row .name'))"
                     ".map(n => n.textContent)")
        assert "FreshTable" in " ".join(names) and not any("notes" in n for n in names), names    # example-page is installed
        assert b.js("document.querySelector('#view-apps .app-row details summary').textContent") == "About"
        # Looks: the name and theme, the Core's looks (lane I10c), saved with PUT /v1/settings/bot
        from puffpai_core import bot
        b.click(*_point(b, ".rail a.item[href='#looks']", 0))
        assert b.wait(f"!!document.querySelector({json.dumps(NAME_BOX)})"), b.js(WHERE)
        assert b.js("document.querySelectorAll('#view-looks input[name=bot-theme]').length") == len(bot.THEMES)
        assert b.js(f"document.querySelector({json.dumps(NAME_BOX)}).value") == "Puff"    # the Core's default name
        b.js(f"document.querySelector({json.dumps(NAME_BOX)}).value = ''")
        b.click(*_point(b, NAME_BOX, 0))
        b.ws.call("Input.insertText", {"text": "Juniper"})
        assert b.wait(_has_text("#view-looks .looks-preview h3", "I'm Juniper."))
        b.click(*_text_point(b, "#view-looks button", "Save"))
        assert b.wait(_has_text("#view-looks", "Saved.")), b.js(WHERE)
        assert b.wait("document.getElementById('botname').textContent === 'Juniper'")
        # Lane I14: Puff (day and night) follows the day/night button: its night tokens, then its day tokens.
        # This bot finished setup with no look saved, so it keeps Puff (D90), and saving the name wrote it down.
        bg = "document.documentElement.style.getPropertyValue('--bg')"
        if b.js("document.documentElement.dataset.puffTheme") == "day":
            b.click(*_point(b, "#theme-btn", 0))
        assert b.wait(f"{bg} === {json.dumps(bot._PUFF_NIGHT['--bg'])}"), b.js(bg)
        b.click(*_point(b, "#theme-btn", 0))
        assert b.wait(f"document.documentElement.dataset.puffTheme === 'day' && {bg} === {json.dumps(bot._PUFF_DAY['--bg'])}"), b.js(bg)
        b.click(*_point(b, "#theme-btn", 0))
        assert b.wait(f"{bg} === {json.dumps(bot._PUFF_NIGHT['--bg'])}"), b.js(bg)
        # Settings: Setup tucked in, one list of rows
        b.click(*_point(b, ".rail a.item[href='#settings']", 0))
        assert b.wait(_has_text("#view-settings", "Open Setup"), timeout_s=30), b.js(WHERE)
        assert b.js("document.querySelectorAll('#view-settings .tile').length") == 1
        # A theme's tokens go on :root over day or night; a bad one is never set
        got = b.js("""(() => { PuffTheme.tokens({'--pink': '#123456', '--bg': 'url(https://example.org/x.png)'}, false);
          const st = getComputedStyle(document.documentElement);
          const out = [st.getPropertyValue('--pink').trim(), document.documentElement.style.getPropertyValue('--bg')];
          PuffTheme.tokens({}, false); out.push(getComputedStyle(document.documentElement).getPropertyValue('--pink').trim());
          return out; })()""")
        assert got[0] == "#123456" and got[1] == "" and got[2] in (bot._CLEAN_NIGHT["--pink"], bot._CLEAN_DAY["--pink"]), got


# -- lane I16: the pages for the Core's new parts, by real clicks -----------------------------------------
PIN = "Tq7#mz2Lp!"          # a made-up test PIN (tests/z1kit.py), nobody's real one

#: A faked update card (lane I16): an app's "<App> has an update" card in the pending list, and its update
#: route answering with it, so the Apps page's Update button can be clicked without a second copy of the app.
FAKE_UPDATE = """(() => {
  const UPDATE = {consent_id: 'c-fakeup', id: 'c-fakeup', card_sha: 'fake-up', status: 'pending', floor: false,
    title: 'My notes has an update', lines: ['Version 0.2.0 is ready.'], scope: {kind: 'package', target: 'example-page'},
    card: {title: 'My notes has an update', version: '0.2.0', what_changed: {version: ['0.1.0', '0.2.0']}}};
  const real = window.fetch.bind(window);
  const json = (obj, status) => new Response(JSON.stringify(obj), {status: status || 200, headers: {'Content-Type': 'application/json'}});
  window.fetch = async (url, init) => {
    const m = (init && init.method) || 'GET', u = String(url);
    if (u.startsWith('/v1/packages/example-page/update')) return json({consent_id: 'c-fakeup', id: 'example-page', card: UPDATE.card, status: 'card'}, 202);
    if (u.startsWith('/v1/consent') && m === 'GET') {
      const r = await real(url, init);
      const list = r.ok ? await r.json() : [];
      list.push(UPDATE);
      return json(list);
    }
    return real(url, init);
  };
  return true; })()"""


def _call(b: Browser, method: str, path: str, body=None):
    """The Core's own route from the signed-in page (setup for the test, never the thing it checks)."""
    sent = json.dumps(json.dumps(body)) if body is not None else "undefined"
    return b.js(f"""fetch({json.dumps(path)}, {{method: {json.dumps(method)}, credentials: 'same-origin',
      headers: window.Puff.authHeaders({{'Content-Type': 'application/json'}}), body: {sent}}})
      .then(async r => ({{status: r.status, data: await r.json().catch(() => null)}}))""")


def _type(b: Browser, sel: str, text: str) -> None:
    b.click(*_point(b, sel, 0))
    b.ws.call("Input.insertText", {"text": text})


def _rail(b: Browser, view: str) -> None:
    b.click(*_point(b, f".rail a.item[href='#{view}']", 0))
    assert b.wait(f"location.hash === '#{view}' && !document.getElementById('view-{view}').hidden"), b.js(WHERE)


def _button(b: Browser, card_sel: str, card_text: str, label: str):
    """The place of the button *label* inside the first *card_sel* whose text has *card_text*."""
    return b.js(f"""(() => {{ const c = Array.from(document.querySelectorAll({json.dumps(card_sel)}))
      .find(n => n.textContent.indexOf({json.dumps(card_text)}) >= 0); if (!c) return null;
      const y = Array.from(c.querySelectorAll('button')).find(x => x.textContent === {json.dumps(label)}); if (!y) return null;
      y.scrollIntoView({{block: 'center'}}); const r = y.getBoundingClientRect();
      return [r.left + r.width / 2, r.top + r.height / 2]; }})()""")


def test_i16_who_your_bot_is_you_looks_knowledge_store_ticks_and_updates(state, tmp_path, monkeypatch, drawbridge):
    """Lane I16, every press a real click: Settings > Who your bot is (empty, the owner's words, a 400 next to the box,
    Write it again), Settings > You saved in a test vault (then locked: never an empty form), Looks' swatches
    (and the wizard's look step), Knowledge (empty, then one item read), and an app's Update
    button following its (faked) update card."""
    from puffpai_core import bot
    from puffpai_core.zone1 import fetched
    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        # a vault for You, set up as lane I12's tests do (one PIN), with the Core's own route
        got = _call(b, "POST", "/v1/vault/setup", {"pin": PIN})
        assert got["status"] == 201, got
        # -- Settings > Who your bot is ------------------------------------------------------------
        _rail(b, "settings")
        tile = "#view-settings [data-tile=persona]"
        assert b.wait(_has_text(tile, "Nothing is written yet."), timeout_s=30), b.js(WHERE)
        assert b.js(f"document.querySelector({json.dumps(tile)}).open") is False       # a row opens when she opens it
        b.click(*_point(b, tile + " > summary", 0))
        b.click(*_text_point(b, tile + " summary", "Change it in your words"))
        _type(b, tile + " textarea", "Role: a helper for trying things out.\nHow it talks: It is plain and short.")
        b.click(*_text_point(b, tile + " button", "Save"))
        assert b.wait(_has_text(tile + " [data-said=persona]", "Saved.")), b.js(WHERE)
        assert b.js(f"document.querySelector({json.dumps(tile + ' .persona-role')}).textContent") == "a helper for trying things out"
        assert b.js(_has_text(tile, "It is plain and short.")) and b.js(_has_text(tile, "In your own words."))
        b.js(f"document.querySelector({json.dumps(tile + ' textarea')}).value = ''")     # an empty box: the Core says no
        b.click(*_text_point(b, tile + " button", "Save"))
        assert b.wait(f"document.querySelector({json.dumps(tile + ' [data-said=persona-field]')}).textContent.length > 0")
        b.click(*_text_point(b, tile + " button", "Write it again"))
        assert b.wait(f"""(() => {{ const s = document.querySelector({json.dumps(tile + ' [data-said=persona]')}).textContent;
          return s.length > 0 && s.indexOf('Writing it again') < 0; }})()""", timeout_s=60)
        said = b.js(f"document.querySelector({json.dumps(tile + ' [data-said=persona]')}).textContent")
        assert "goal" in said or "Toolsmith" in said, said                         # the Core's plain 409
        # -- Settings > You ------------------------------------------------------------------------
        you = "#view-settings [data-tile=you]"
        assert b.wait(f"!!document.querySelector({json.dumps(you + ' select')})")
        b.click(*_point(b, you + " > summary", 0))
        zone = b.js("Intl.DateTimeFormat().resolvedOptions().timeZone")
        assert b.js(f"document.querySelector({json.dumps(you + ' select')}).value") == zone     # the browser's own zone
        _type(b, you + " input[aria-label='Your name']", "Robin")
        _type(b, you + " input[aria-label='What your bot calls you']", "Rob")
        b.click(*_text_point(b, you + " button", "Save"))
        assert b.wait(_has_text(you + " [data-said=you]", "Saved")), b.js(WHERE)
        assert b.js(f"document.querySelector({json.dumps(you + ' [data-said=you]')}).textContent") == "Saved."
        kept = _call(b, "GET", "/v1/settings/you")
        assert kept["status"] == 200 and kept["data"]["name"] == "Robin" and kept["data"]["call_me"] == "Rob", kept
        assert kept["data"]["timezone"] == zone, kept
        assert _call(b, "POST", "/v1/vault/lock", {})["status"] == 200
        _rail(b, "looks")
        _rail(b, "settings")
        assert b.wait(_has_text(you, "Unlock your vault to see this."), timeout_s=30), b.js(WHERE)
        assert b.js(f"document.querySelectorAll({json.dumps(you + ' input, ' + you + ' textarea, ' + you + ' select')}).length") == 0
        # -- Looks: swatches, night and day for a "both" theme, and each theme's line -----------------
        _rail(b, "looks")
        assert b.wait("document.querySelectorAll('#view-looks .swatch input[name=bot-theme]').length === "
                      + str(len(bot.THEMES))), b.js(WHERE)
        for t in bot.THEMES:
            minis = b.js(f"document.querySelector('#view-looks .swatch input[value={json.dumps(t['id'])}]').closest('.swatch')"
                         ".querySelectorAll('.swatch-mini').length")
            assert minis == (2 if t["mode"] == "both" else 1), (t["id"], minis)
            assert b.js(_has_text("#view-looks .swatches", t["line"])), t["id"]
        assert b.js("document.querySelector('#view-looks .swatch input[value=\"puff\"]').closest('.swatch')"
                    ".querySelector('.swatch-mini').style.background"), "the night preview has no colors"
        aero = [t["id"] for t in bot.THEMES].index("aero")
        b.click(*_point(b, "#view-looks .swatch", aero))
        assert b.wait("document.querySelector('#view-looks input[value=aero]').checked && "
                      f"document.documentElement.style.getPropertyValue('--bg') === {json.dumps(bot.THEMES[aero]['tokens']['--bg'])}"), \
            b.js("document.documentElement.style.getPropertyValue('--bg')")
        # the wizard's look step draws the same swatches, never a text box
        b.js("location.hash = '#setup/look'")
        assert b.wait("document.querySelectorAll('#wiz .swatches input[name=\"q-theme\"]').length === "
                      + str(len(bot.THEMES)), timeout_s=60), b.js(WHERE)
        assert b.js("document.querySelectorAll('#wiz form.q input[type=text]').length") == 0
        # -- Knowledge: nothing read yet, then one Wikipedia article with its credit -------------------
        _rail(b, "knowledge")
        assert b.wait(_has_text("#view-knowledge [data-said=sources-empty]", "Nothing has been read yet."), timeout_s=30), b.js(WHERE)
        assert b.js(_has_text("#view-knowledge", "Linked folders"))
        fetched.save(state, "wikipedia", "soil-1", "Soil\n\nFrom Wikipedia.", {
            "title": "Soil", "url": "https://en.wikipedia.org/wiki/Soil", "license": "CC BY-SA 4.0",
            "fetched_at": "2026-10-26T09:00:00+00:00"})
        _rail(b, "home")
        _rail(b, "knowledge")
        link = "#view-knowledge [data-source=wikipedia] a"
        assert b.wait(f"!!document.querySelector({json.dumps(link)})", timeout_s=30), b.js(WHERE)
        assert b.js(f"document.querySelector({json.dumps(link)}).getAttribute('href')") == "https://en.wikipedia.org/wiki/Soil"
        assert b.js(_has_text("#view-knowledge", "Text from Wikipedia, by its authors, under CC BY-SA 4.0."))
        assert b.js(_has_text("#view-knowledge [data-source=wikipedia]", "2026-10-26"))
        # -- Apps: an update waiting shows Update on that app, and the button follows its card ----------
        assert b.js(FAKE_UPDATE) is True
        _rail(b, "apps")
        upd = "#view-apps button[data-act=update]"
        assert b.wait(f"Array.from(document.querySelectorAll({json.dumps(upd)})).some(x => x.textContent.endsWith('Update')"
                      " && x.classList.contains('primary'))", timeout_s=30), b.js(WHERE)
        assert b.js(_has_text("#view-apps", "My notes has an update: version 0.2.0."))
        b.click(*_text_point(b, upd + ".primary", "Update"))
        assert b.wait(_has_text("#view-apps .card.yes", "My notes has an update")), b.js(WHERE)


def test_i16_a_ticked_store_app_is_added_by_the_core_then_the_build_starts(state, tmp_path, monkeypatch, drawbridge):
    """Lane I16, the real Core's path: a plan card with the PuffPAI store's FreshTable (made through the host's
    own agent_plan, ``PUFFPAI_TEST_STORE_PLAN``), ticked and said Yes to by real clicks. The Core raises
    FreshTable's own Yes card; the page is reloaded while it waits; the card is answered Yes on Your turn; the
    install runs for real (FreshTable uses no internet); and the build starts only once FreshTable's row has a
    final status. Every press is a real click."""
    from puffpai_core.zone3.toolsmith_host import store_phase as sp
    with _desktop(state, tmp_path, monkeypatch, complete=True, env={"PUFFPAI_TEST_STORE_PLAN": "1"}) as b:
        builds = _call(b, "GET", "/v1/toolsmith")["data"]["builds"]
        assert len(builds) == 1, builds
        bid = builds[0]["id"]
        build = f"/v1/toolsmith/builds/{bid}"
        _rail(b, "yourturn")
        tick = "FreshTable is in the PuffPAI store"
        offer = "#view-yourturn .card.yes label.opt"
        assert b.wait(_has_text("#view-yourturn", tick), timeout_s=30), b.js(WHERE)
        b.click(*_text_point(b, offer, tick))
        assert b.wait(f"document.querySelector({json.dumps(offer + ' input')}).checked")
        b.click(*_button(b, "#view-yourturn .card.yes", tick, "Yes"))
        # the Core raises FreshTable's own card; the Desktop shows it from the Core's progress
        store_card = f"#notices [data-plan-yes={json.dumps(bid)}] .card.yes"
        assert b.wait(_has_text(store_card + " h3", "FreshTable"), timeout_s=90), (
            b.js(WHERE), _call(b, "GET", build)["data"].get("store_adds"))
        v = _call(b, "GET", build)["data"]
        assert not v.get("yes") and v["store_adds"]["state"] == "adding", v.get("store_adds")   # nothing started yet
        assert v["jobs"][0]["plan"].get("reuse") is None
        # a reload while it waits: the page forgets everything, the Core does not
        b.ws.call("Page.reload", {})
        assert b.wait("document.querySelectorAll('.rail a.item').length > 5 && !!document.getElementById('view-yourturn')"
                      " && !document.getElementById('view-yourturn').hidden", timeout_s=30), b.js(WHERE)
        assert not b.js("!!document.querySelector('#notices [data-plan-yes]')")      # the page's own progress is gone
        assert not _call(b, "GET", build)["data"].get("yes")
        assert b.wait(_has_text("#view-yourturn .card.yes h3", "FreshTable"), timeout_s=30), b.js(WHERE)
        b.click(*_button(b, "#view-yourturn .card.yes", "FreshTable", "Yes"))
        # the build starts only after FreshTable's install has a final status
        seen = []
        end = time.monotonic() + 180
        while time.monotonic() < end:
            row = _call(b, "GET", "/v1/registry/freshtable")
            v = _call(b, "GET", build)["data"]
            seen.append(((row["data"] or {}).get("status") if row["status"] == 200 else None, bool(v.get("yes"))))
            if v.get("yes") and (v.get("store_adds") or {}).get("state") == "done":
                break
            time.sleep(0.1)
        assert v.get("yes"), (seen[-5:], v.get("store_adds"))
        first_started = next(i for i, (_, started) in enumerate(seen) if started)
        assert all(status != "installing" for status, started in seen if started), seen
        assert seen[first_started][0] in ("running", "stopped", "needs_you"), seen
        assert v["store_adds"]["said"] == [sp.ASKING.format(name="FreshTable"), sp.INSTALLING.format(name="FreshTable"),
                                           sp.ADDED.format(name="FreshTable"), sp.STARTED], v["store_adds"]
        assert v["jobs"][0]["plan"]["reuse"] == "freshtable" and v["jobs"][0]["plan"]["reuse_by"] == "store"
        # Building shows the same progress from the Core
        _rail(b, "building")
        assert b.wait(_has_text(f"#view-building [data-store-adds={json.dumps(bid)}]", "Added FreshTable from the PuffPAI store."),
                      timeout_s=30), b.js(WHERE)


# -- lane I17: the Developer page's Models section, by real clicks, with a faked Ollama ----------------------

class FakeOllamaServer:
    """A model server on a free 127.0.0.1 port that answers like Ollama: its list, a download that streams a
    few progress lines and succeeds (nothing is downloaded), and the states. Never the real Ollama."""

    def __init__(self, names):
        import http.server
        import threading
        self.names, self.pulled = list(names), []
        fake = self

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
                    return self._json({"version": "0.0.0-test"})
                if self.path == "/api/tags":
                    return self._json({"models": [{"name": n, "model": n, "size": 4_700_000_000,
                                                   "details": {"parameter_size": "7B"}} for n in fake.names]})
                if self.path == "/api/ps":
                    return self._json({"models": []})
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                size = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(size) or b"{}")
                if self.path == "/api/pull":
                    lines = [{"status": "pulling", "total": 100, "completed": c} for c in (10, 60, 100)]
                    lines.append({"status": "success"})
                    data = b"".join(json.dumps(x).encode() + b"\n" for x in lines)
                    fake.pulled.append(body.get("model"))
                    fake.names.append(str(body.get("model")))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if self.path == "/api/chat":
                    return self._json({"message": {"role": "assistant", "content": "ok"}, "done": True})
                if self.path == "/api/show" and body.get("model") in fake.names:
                    return self._json({"details": {"format": "gguf"}})      # a local model (no remote_host)
                self.send_response(404)
                self.end_headers()

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.address = f"http://127.0.0.1:{self.server.server_address[1]}"
        assert self.server.server_address[1] not in (8420, 8421, 8422, 11434, 11435)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_i17_the_models_section_adds_a_coding_model_and_uses_it(state, tmp_path, monkeypatch, drawbridge):
    """Lane I17, every press a real click: Developer > Models lists what is on this (faked) Ollama and what each
    model is used for, and the coding models that fit a 16 GB computer. Add asks its own Yes card (the size and
    the host), the faked download runs only after the Yes, and Use for coding sets the role."""
    ollama = FakeOllamaServer(["qwen3.5:9b"])
    try:
        (state / "settings").mkdir(parents=True, exist_ok=True)
        (state / "settings" / "model.json").write_text(json.dumps({"address": ollama.address, "model": "qwen3.5:9b"}),
                                                       encoding="utf-8")
        (state / "settings" / "hardware.json").write_text(json.dumps(
            {"hardware_class": "desktop", "memory": {"total_gb": 16}, "disk": {"free_gb": 200}}), encoding="utf-8")
        with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
            _rail(b, "developer")
            # lane I18f: Models is on Zone 2 (Developer > Brain), as v1's Brain page had it
            assert b.wait("!!document.querySelector('#view-developer [data-zone-open=brain]')", timeout_s=30), b.js(WHERE)
            b.click(*_point(b, "#view-developer [data-zone-open=brain]", 0))
            assert b.wait("location.hash === '#developer/brain'"), b.js(WHERE)
            tile = "#view-developer [data-tile=models]"
            assert b.wait(_has_text(tile + " [data-models=installed]", "qwen3.5:9b"), timeout_s=30), b.js(WHERE)
            assert b.js(_has_text(tile + " [data-models=installed]", "Used for: Chat"))
            row = tile + " [data-role=code] [data-model='qwen2.5-coder:7b']"
            assert b.wait(f"!!document.querySelector({json.dumps(row)})"), b.js(WHERE)
            assert b.js(_has_text(row, "Needs 8 GB of RAM, best with 16 GB. 5 GB download."))
            assert not b.js(f"!!document.querySelector({json.dumps(tile + ' [data-role=code] [data-model=' + chr(39) + 'qwen3-coder:30b' + chr(39) + ']')})")
            assert b.js(_has_text(tile, "registry.ollama.ai"))
            b.click(*_button(b, row, "Qwen2.5 Coder 7B", "Add"))
            card = row + " .card.yes"
            assert b.wait(_has_text(card, "Download Qwen2.5 Coder 7B?"), timeout_s=30), b.js(WHERE)
            assert b.js(_has_text(card, "about 5 GB, from registry.ollama.ai"))
            assert b.js(_has_text(card, "goes around the Bridge"))
            time.sleep(0.5)
            assert ollama.pulled == []                                   # nothing before her Yes
            b.click(*_button(b, row, "Download Qwen2.5 Coder 7B?", "Yes"))
            use = "Use for coding"
            assert b.wait(f"""(() => {{ const r = document.querySelector({json.dumps(row)});
              return !!r && Array.from(r.querySelectorAll('button')).some(x => x.textContent === {json.dumps(use)}); }})()""",
                          timeout_s=60), b.js(WHERE)
            assert ollama.pulled == ["qwen2.5-coder:7b"]
            b.click(*_button(b, row, "Qwen2.5 Coder 7B", use))
            assert b.wait(_has_text(row, "IN USE"), timeout_s=30), b.js(WHERE)
            assert b.js(_has_text(tile + " [data-role=code]", "Now: qwen2.5-coder:7b"))
            got = _call(b, "GET", "/v1/models")
            assert got["status"] == 200 and got["data"]["roles"]["code"] == "qwen2.5-coder:7b", got
            assert b.js(_has_text(tile + " [data-models=installed]", "Used for: Coding"))
    finally:
        ollama.close()


def test_d96_the_store_panel_draws_store_rows_and_loads_no_outside_picture(state, tmp_path, monkeypatch, drawbridge):
    """D96, every press a real click: a source of hers lists rows; the official store can't be reached (private until
    the release) and that reads fine; a store row shows its tier, its source and its kind's glyph, never its https
    icon; a pulled row has no Add; Remove takes the source off. Nothing is added here: that would reach for the repo."""
    commit = "a" * 40
    icon = "https://raw.githubusercontent.com/example/repo/" + commit + "/icon.svg"
    base = {"repo": "https://github.com/example/repo", "commit": commit, "tier": "verified", "version": "1.2.3",
            "author": "A Maker", "license": "MIT", "icon": icon}
    rows = [{**base, "id": "gardenlog", "kind": "app", "path": "gardenlog", "name": "Garden Log", "description": "A garden log. More."},
            {**base, "id": "gardentool", "kind": "tool", "path": "gardentool", "name": "Garden Tool", "description": "A tool."},
            {**base, "id": "gardenskill", "kind": "skill", "path": "gardenskill", "name": "Garden Skill", "description": "A skill."},
            {**base, "id": "gardenagent", "kind": "agent", "path": "gardenagent", "name": "Garden Agent", "description": "An agent."},
            {**base, "id": "look-clean", "kind": "look", "path": "look-clean", "name": "Clean", "description": "A second Clean."},
            {**base, "id": "pulledapp", "kind": "app", "path": "pulledapp", "name": "Pulled App", "description": "Gone."}]
    source = tmp_path / "her store"
    source.mkdir()
    (source / "index.json").write_text(json.dumps({"schema": "puffpai-store-index/1", "name": "Hers", "packages": rows,
        "yanked": [{"id": "pulledapp", "commit": commit, "why": "It broke."}]}), encoding="utf-8")
    (state / "settings").mkdir(parents=True, exist_ok=True)
    odd = tmp_path / "odd store"                               # a row of the wrong type drops, and only that row
    odd.mkdir()
    (odd / "index.json").write_text(json.dumps({"schema": "puffpai-store-index/1", "packages": [
        {**base, "id": "oddrow", "kind": ["app"], "path": "oddrow"},
        {**base, "id": "goodrow", "kind": "app", "path": "goodrow", "name": "Good Row", "description": "Fine."}]}), encoding="utf-8")
    (state / "settings" / "store.json").write_text(json.dumps({"sources": [{"source": str(source)}, {"source": str(odd)}]}),
                                                   encoding="utf-8")
    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        b.click(*_point(b, ".rail a.item[href='#apps']", 0))
        assert b.wait("!!document.querySelector('#view-apps .tabs button')")
        b.click(*_text_point(b, "#view-apps .tabs button", "PuffPAI store"))
        assert b.wait("!!document.querySelector('#view-apps [data-store-row=gardenlog]')", timeout_s=30), b.js(WHERE)
        heads = b.js("Array.from(document.querySelectorAll('#view-apps h2.micro')).map(n => n.textContent)")
        for head in ("Apps", "Tools", "Skills", "Other parts", "Where the store reads from"):
            assert head in heads, heads
        assert heads.index("Apps") < heads.index("Tools") < heads.index("Skills") < heads.index("Other parts")
        row = b.js("document.querySelector('#view-apps [data-store-row=gardenlog]').textContent")
        assert "Garden Log" in row and "A garden log." in row and "By A Maker, version 1.2.3" in row
        assert "Community: PuffPAI has not read it." in row and "her store" in row     # her source says verified: never
        assert not b.js("!!document.querySelector('#view-apps [data-store-row=gardenlog] img')")
        assert b.js("document.querySelector('#view-apps [data-store-row=gardenlog] .icon .msr').textContent") == "widgets"
        assert not b.js("!!document.querySelector('#view-apps [data-store-row=look-clean]')")       # never two Cleans
        pulled = "document.querySelector('#view-apps [data-store-row=pulledapp]')"
        assert "The store pulled it: It broke." in b.js(pulled + ".textContent")
        assert not b.js(pulled + ".querySelector('button')")
        assert b.js("!!document.querySelector('#view-apps [data-store-row=gardenlog] button.primary')")
        line = b.js("document.querySelector('[data-store-line]').textContent")
        assert line.startswith("Could not reach the official PuffPAI store")
        assert "FreshTable" in b.js("document.getElementById('view-apps').textContent")   # the built-ins still list
        loaded = b.js("performance.getEntriesByType('resource').map(e => e.name).filter(n => n.indexOf('https:') === 0)")
        assert loaded == [] and not b.js("!!document.querySelector('img[src^=\"https:\"]')")
        b.click(*_text_point(b, "#view-apps .row button", "Remove"))
        assert b.wait("!document.querySelector('#view-apps [data-store-row=gardenlog]') && "
                      "!!document.querySelector('#view-apps [data-store-row=freshtable]')", timeout_s=30), b.js(WHERE)
        assert [s["source"] for s in json.loads((state / "settings" / "store.json").read_text(encoding="utf-8"))["sources"]] == [str(odd)]
        assert b.js("!!document.querySelector('#view-apps [data-store-row=goodrow]') && !document.querySelector('#view-apps [data-store-row=oddrow]')")
        # A listing that fails: the built-ins still list, and she can still remove a source
        b.js("""(() => { const real = window.fetch; window.fetch = (u, o) => String(u) === '/v1/store'
          ? Promise.resolve(new Response('{"error": "It failed."}', {status: 500, headers: {'Content-Type': 'application/json'}}))
          : real(u, o); return true; })()""")
        b.click(*_text_point(b, "#view-apps .tabs button", "Installed"))
        assert b.wait("location.hash === '#apps/installed' && !document.querySelector('#view-apps [data-store-row=freshtable]')"), b.js(WHERE)
        b.click(*_text_point(b, "#view-apps .tabs button", "PuffPAI store"))
        assert b.wait(_has_text("#view-apps", "The store could not be read:") + " && "
                      + _has_text("#view-apps", "odd store"), timeout_s=30), b.js(WHERE)
        assert b.js("!!document.querySelector('#view-apps [data-store-row=freshtable]') && !document.querySelector('#view-apps [data-store-row=goodrow]')")
        b.click(*_text_point(b, "#view-apps .row button", "Remove"))
        end = time.monotonic() + 30
        while time.monotonic() < end and json.loads((state / "settings" / "store.json").read_text(encoding="utf-8"))["sources"]:
            time.sleep(0.1)
        assert json.loads((state / "settings" / "store.json").read_text(encoding="utf-8"))["sources"] == []
