"""Lane i23d (D101): Toolsmith's cloud model on the Developer Overview, as plain to find as GitHub.

The owner's words: "the cloud model add is also missing from the developer page. for toolsmith. i see where i can
add github, but not the cloud model API."

No route and no key behaviour changed. These tests hold the page to the routes as they are:

- The page on a small page model (tests/js/i23d.test.js, node): three companies; a cloud bot, a local bot, a locked
  vault and no vault; the order of the calls; the box cleared; the key in no text on the page.
- The same two routes for the three built-in companies: key, then model with the sentence's sha; owner key refused
  on the writes; a locked vault's inbox; the key in no reply and in no file outside the vault's sealed store.
- One real-Edge run on free ports, every press a real click (``test_i10a_desktop_clicks``'s harness).

Every key here is made up. Nothing is sent to any company: only saving a key and a choice is tested.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

import pytest

from conftest import REPO
from puffpai_core.api.app import create_app

PIN = "Tq7#mz2Lp!"                     # a made-up test PIN (tests/z1kit.py), nobody's real one
FAKE_KEY = "not-a-real-key-i23d-" + "0" * 12
NODE = shutil.which("node")
WEB = REPO / "packages" / "desktop" / "web"
TILE = "#view-settings [data-tile=toolsmith]"
COMPANIES = ("anthropic", "openai", "google")


@pytest.fixture
def yeses(monkeypatch):
    """No Bridge runs in a test client, so her Yes is recorded here (the real Edge run below uses the real one)."""
    from puffpai_core.zone3.toolsmith_host import ports
    seen = []
    monkeypatch.setattr(ports.Ports, "cloud_yes", lambda self, provider, hosts, sha, by="owner": seen.append((provider, hosts, sha)) or "c1")
    return seen


@pytest.fixture
def api(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    return app.test_client(), dev, owner


def _provider(client, owner, pid):
    return next(p for p in client.get("/v1/toolsmith/providers", headers=owner).json if p["id"] == pid)


def _files_holding(state, text: str) -> list[str]:
    needle = text.encode("utf-8")
    return [str(f) for f in state.rglob("*") if f.is_file() and needle in f.read_bytes()]


# -- the page ---------------------------------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_tile_on_a_small_page_model():
    for f in (WEB / "devzones.js", WEB / "pages" / "developer.js"):
        got = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert got.returncode == 0, got.stderr
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "i23d.test.js")], capture_output=True, text=True, timeout=120)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr


def test_the_tile_never_stores_or_prints_the_key():
    """Read as github.js is (tests/test_i18g_github.py): in the tile's own code, none of the ways a key could stay."""
    src = (WEB / "pages" / "developer.js").read_text(encoding="utf-8")
    tile = src[src.index("async function toolsmithTile("):src.index("async function hostsTile(")]
    for never in ("innerHTML", "document.write", "eval(", "localStorage", "sessionStorage", "console.", "document.cookie",
                  "location.", "history."):
        assert never not in tile, never
    assert re.search(r'keyBox = el\("input", \{ type: "password", autocomplete: "off"', tile)
    assert "let value = keyBox.value; keyBox.value = \"\";" in tile
    assert "if (liveKey) liveKey.value = \"\";" in tile
    assert '"noopener,noreferrer"' in tile and '"_blank", "noopener")' not in tile
    # the key goes only in a body, and only to its own route
    assert tile.count("{ value }") == 2 and tile.count('P.api("PUT", "/v1/toolsmith/keys/" + encodeURIComponent(') == 2
    assert "/v1/toolsmith/keys/\" + value" not in tile and "text: value" not in tile


def test_the_same_tile_is_on_each_page_and_github_stays_as_it_was():
    src = (WEB / "pages" / "developer.js").read_text(encoding="utf-8")
    assert src.count("async function toolsmithTile(") == 1 and "P.toolsmithTile = toolsmithHere;" in src     # D162: Settings' row
    assert "await P.toolsmithTile()" in (WEB / "pages" / "settings.js").read_text(encoding="utf-8")
    over = src[src.index("async function overview("):src.index("/* ---- Zone 1: Vault")]
    own = over[over.index('"data-dev": "add-your-own"'):]
    own = own[:own.index("]);")]
    assert 'window.PuffGitHub.block({ where: "developer" }) : null' in own and "smith" not in own
    assert src.count('goTo("#settings/toolsmith"') == 2 and 'goTo("#developer/github"' in src
    assert 'P.go("settings", "toolsmith")' in src                  # an old address still lands on it
    assert 'toolsmith: toolsmithPage' in src and 'github: githubPage' in src


def test_no_new_route_for_the_tile():
    """Only the routes the tile has always used: read here from its own code."""
    src = (WEB / "pages" / "developer.js").read_text(encoding="utf-8")
    tile = src[src.index("async function toolsmithTile("):src.index("async function hostsTile(")]
    used = set(re.findall(r'P\.api\("([A-Z]+)", "(/v1/[a-z/]+)"', tile))
    assert used == {("GET", "/v1/toolsmith"), ("GET", "/v1/toolsmith/providers"), ("GET", "/v1/toolsmith/keys"), ("GET", "/v1/vault/status"),
                    ("PUT", "/v1/toolsmith/keys/"), ("DELETE", "/v1/toolsmith/keys/"), ("PUT", "/v1/toolsmith/model"), ("PUT", "/v1/toolsmith/cap")}


def test_the_words_follow_the_writing_rules():
    src = (WEB / "pages" / "developer.js").read_text(encoding="utf-8") + (WEB / "devzones.js").read_text(encoding="utf-8")
    low = src.lower()
    assert "doors" not in low and " beta" not in low and chr(0x2014) not in src
    assert "Pick a company first" in src and "Nothing leaves your computer without your consent." in src


# -- the routes, for the three companies ---------------------------------------------------------------------------

@pytest.mark.parametrize("pid", COMPANIES)
def test_key_then_model_with_the_sentences_sha(api, state, yeses, pid):
    client, dev, owner = api
    assert client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev).status_code == 201
    p = _provider(client, owner, pid)
    assert p["company"] in p["consent"] and p["consent_sha"]
    body = {"where": "cloud", "provider": pid, "model": p["default_model"], "cost_cap_usd": p["cost_cap_usd"]}

    # the writes stay paired-device only
    assert client.put(f"/v1/toolsmith/keys/{pid}", json={"value": FAKE_KEY}, headers=owner).status_code == 403
    assert client.put("/v1/toolsmith/model", json={**body, "consent_sha": p["consent_sha"]}, headers=owner).status_code == 403

    # a key alone does not move Toolsmith to the company
    r = client.put(f"/v1/toolsmith/keys/{pid}", json={"value": FAKE_KEY}, headers=dev)
    assert r.status_code == 200 and r.json == {"saved": True, "line": "Saved in your vault."}
    assert r.headers["Cache-Control"] == "no-store"
    assert client.get("/v1/toolsmith", headers=owner).json["where"] == "local"

    # the Core checks the sha: a wrong or missing one changes nothing
    for bad in ({**body, "consent_sha": "0" * 64}, body):
        r = client.put("/v1/toolsmith/model", json=bad, headers=dev)
        assert r.status_code == 400 and "say yes" in r.json["error"], r.json
    assert client.get("/v1/toolsmith", headers=owner).json["where"] == "local"

    r = client.put("/v1/toolsmith/model", json={**body, "consent_sha": p["consent_sha"]}, headers=dev)
    assert r.status_code == 200 and r.json["provider"] == pid and r.json["model"] == p["default_model"], r.json
    got = client.get("/v1/toolsmith", headers=owner).json
    assert got["where"] == "cloud" and got["company"] == p["company"]
    assert [(y[0], y[2]) for y in yeses] == [(pid, p["consent_sha"])]      # only that one Yes, with its sha

    keys = client.get("/v1/toolsmith/keys", headers=owner)
    assert keys.headers["Cache-Control"] == "no-store"
    mine = next(k for k in keys.json if k["provider"] == pid)
    assert mine["kept"] is True and mine["vault_name"] == f"toolsmith.{pid}"
    assert [k["kept"] for k in keys.json if k["provider"] != pid] == [False, False]
    for r in (keys, client.get("/v1/toolsmith", headers=owner), client.get("/v1/toolsmith/providers", headers=owner),
              client.get("/v1/audit?limit=200", headers=owner)):
        assert FAKE_KEY not in r.get_data(as_text=True)
    assert _files_holding(state, FAKE_KEY) == []

    # changing the key: the same route, and it is still kept
    assert client.put(f"/v1/toolsmith/keys/{pid}", json={"value": FAKE_KEY + "1"}, headers=dev).status_code == 200
    assert next(k for k in client.get("/v1/toolsmith/keys", headers=owner).json if k["provider"] == pid)["kept"] is True


@pytest.mark.parametrize("pid", COMPANIES)
def test_a_locked_vault_goes_to_the_inbox_as_the_route_says(api, state, pid):
    client, dev, owner = api
    assert client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev).status_code == 201
    assert client.post("/v1/vault/lock", headers=owner).status_code == 200
    r = client.put(f"/v1/toolsmith/keys/{pid}", json={"value": FAKE_KEY}, headers=dev)
    assert r.status_code == 202 and "inbox" in r.json["line"]
    assert next(k for k in client.get("/v1/toolsmith/keys", headers=owner).json if k["provider"] == pid)["kept"] is None
    assert client.get("/v1/vault/status", headers=owner).json["state"] == "locked"
    assert client.delete(f"/v1/toolsmith/keys/{pid}", headers=dev).status_code == 423
    assert _files_holding(state, FAKE_KEY) == []


def test_no_vault_yet_says_so_and_keeps_nothing(api, state):
    client, dev, owner = api
    r = client.put("/v1/toolsmith/keys/openai", json={"value": FAKE_KEY}, headers=dev)
    assert r.status_code == 409 and "Set up the vault first" in r.json["error"]
    assert client.get("/v1/vault/status", headers=owner).json["state"] == "not_set_up"
    assert _files_holding(state, FAKE_KEY) == []


# -- one real-Edge run on free ports ---------------------------------------------------------------------------------

def test_overview_by_real_clicks(state, tmp_path, monkeypatch, drawbridge):
    """She sees the tile by GitHub on Overview, picks a company, pastes a key, ticks Yes and presses Save; then the
    locked vault; then the card from Bridge to the tile's own page. The Core is real; no company is ever reached."""
    import socket
    from test_i10a_desktop_clicks import EDGE, WHERE, _call, _desktop, _point, _text_point, _type
    if os.name != "nt" or EDGE is None:
        pytest.skip("Windows Job Objects and Microsoft Edge")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()

    def has(sel, text):
        return f"!!document.querySelector({json.dumps(sel)}) && document.querySelector({json.dumps(sel)}).textContent.indexOf({json.dumps(text)}) >= 0"

    def pick(b, pid):
        sel = TILE + " select[aria-label='Where Toolsmith thinks']"
        b.js(f"(() => {{ const s = document.querySelector({json.dumps(sel)}); s.value = {json.dumps(pid)}; s.dispatchEvent(new Event('change')); }})()")

    no_key = f"""(() => {{ const k = {json.dumps(FAKE_KEY)};
      const boxes = Array.from(document.querySelectorAll('input')).map(i => i.value).join(' ');
      return [document.documentElement.outerHTML, document.body.innerText, boxes, location.href,
              JSON.stringify(localStorage), JSON.stringify(sessionStorage), document.cookie].every(t => t.indexOf(k) < 0); }})()"""

    with _desktop(state, tmp_path, monkeypatch, complete=True, env={"OLLAMA_HOST": f"127.0.0.1:{closed_port}"}) as b:
        assert _call(b, "POST", "/v1/vault/setup", {"pin": PIN})["status"] == 201
        b.click(*_point(b, ".rail a.item[href='#developer']", 0))
        assert b.wait("!!document.querySelector('[data-tile=github]')", timeout_s=30), b.js(WHERE)

        # D162: Developer points to it, and it is on Settings, on a local bot
        own = b.js("Array.from(document.querySelector('[data-dev=add-your-own]').children).map(n => n.dataset.tile)")
        assert own[:2] == ["own-folder", "github"] and "toolsmith" not in own
        assert b.js("document.querySelector('[data-tile=github]').dataset.github") == "developer"
        b.click(*_point(b, "#view-developer [data-goto='#settings/toolsmith']", 0))
        assert b.wait(f"location.hash === '#settings/toolsmith' && !!document.querySelector({json.dumps(TILE + ' [data-toolsmith=now]')})", timeout_s=30), b.js(WHERE)
        assert b.js(has(TILE + " [data-toolsmith=now]", "This computer"))
        assert b.js(has(TILE + " [data-toolsmith=stat]", "NO KEY")) and b.js(has(TILE, "Pick a company first"))
        assert b.js(f"document.querySelector({json.dumps(TILE + ' input[type=password]')}).disabled") is True

        # Each company shows its own sentence and key box
        got = b.js("Array.from(document.querySelector(" + json.dumps(TILE + " select") + ").options).map(o => o.value)")
        assert set(COMPANIES) <= set(got), got
        for pid in COMPANIES:
            pick(b, pid)
            sentence = _call(b, "GET", "/v1/toolsmith/providers")["data"]
            sentence = next(p for p in sentence if p["id"] == pid)
            assert b.wait(has(TILE + " [data-toolsmith=consent]", sentence["consent"]))
            assert b.js(f"document.querySelector({json.dumps(TILE + ' input[type=password]')}).ariaLabel || document.querySelector({json.dumps(TILE + ' input[type=password]')}).getAttribute('aria-label')") == sentence["company"] + " key"

        # One thing at a time, in a real browser: a held key write closes everything, and Save's real click does nothing
        pick(b, "google")
        _type(b, TILE + " input[type=password]", FAKE_KEY)
        b.js("""window.__fetch = window.fetch; window.fetch = (u, i) => (i && i.method === 'PUT' && String(u).indexOf('/v1/toolsmith/keys/') >= 0)
          ? new Promise(res => { window.__go = () => res(window.__fetch(u, i)); }) : window.__fetch(u, i); 0""")
        b.click(*_text_point(b, TILE + " button", "Save the key"))
        assert b.wait(has(TILE + " [data-said=toolsmith-key]", "Saving...")), b.js(WHERE)
        closed = ("Array.from(document.querySelectorAll(" + json.dumps(TILE + " select, " + TILE + " input, " + TILE + " button") + "))"
                  ".filter(n => n.textContent.indexOf('Get a key') < 0).every(n => n.disabled)")
        assert b.js(closed) is True
        b.click(*_text_point(b, TILE + " button", "Save"))
        assert b.js("window.__go(); window.fetch = window.__fetch; 0") == 0
        assert b.wait(has(TILE + " [data-said=toolsmith-key]", "Saved in your vault."), timeout_s=20), b.js(WHERE)
        assert b.js(f"document.querySelector({json.dumps(TILE + ' select')}).disabled") is False
        assert b.js(f"document.querySelector({json.dumps(TILE + ' input[type=password]')}).disabled") is False
        assert _call(b, "GET", "/v1/toolsmith")["data"]["where"] == "local"       # that click on Save moved nothing

        # A marker on the command line's tile, to see that Save leaves the page around it alone
        b.js("window.__shell = document.querySelector('[data-tile=terminal]'); 0")

        # Pick, paste, tick, Save: every press a real click
        pick(b, "openai")
        _type(b, TILE + " input[type=password]", FAKE_KEY)
        b.click(*_text_point(b, TILE + " button", "Save"))
        assert b.wait(has(TILE + " [data-said=toolsmith]", "Tick Yes to what Toolsmith sends first.")), b.js(WHERE)
        assert _call(b, "GET", "/v1/toolsmith")["data"]["where"] == "local"
        b.click(*_point(b, TILE + " input[aria-label='Yes to what Toolsmith sends']", 0))
        b.click(*_text_point(b, TILE + " button", "Save"))
        assert b.wait(has(TILE + " [data-said=toolsmith]", "Saved: OpenAI"), timeout_s=20), b.js(WHERE)
        assert b.js(has(TILE + " [data-said=toolsmith]", "Saved in your vault."))
        assert b.js(has(TILE + " [data-toolsmith=stat]", "KEY KEPT")) and b.js(has(TILE + " [data-toolsmith=now]", "OpenAI, "))
        assert b.js(f"document.querySelector({json.dumps(TILE + ' input[type=password]')}).value") == ""
        assert b.js(has(TILE + " [data-toolsmith=key]", "Change the key"))
        assert b.js(no_key) is True
        assert b.js("window.__shell.isConnected") is True
        st = _call(b, "GET", "/v1/toolsmith")["data"]
        assert st["where"] == "cloud" and st["provider"] == "openai"
        assert next(k for k in _call(b, "GET", "/v1/toolsmith/keys")["data"] if k["provider"] == "openai")["kept"] is True
        assert FAKE_KEY not in json.dumps(_call(b, "GET", "/v1/audit?limit=200"))

        # Coming back: straight to its key box, with no click on the list
        b.js("location.reload(); 0")
        assert b.wait(has(TILE + " [data-toolsmith=stat]", "KEY KEPT"), timeout_s=30), b.js(WHERE)
        assert b.js(f"document.querySelector({json.dumps(TILE + ' input[type=password]')}).disabled") is False
        assert b.js(f"document.querySelector({json.dumps(TILE + ' select')}).value") == "openai"

        # A locked vault: can't tell, and a new key goes to the inbox
        assert _call(b, "POST", "/v1/vault/lock", {})["status"] == 200
        b.js("location.reload(); 0")
        assert b.wait(has(TILE + " [data-toolsmith=stat]", "VAULT LOCKED"), timeout_s=30), b.js(WHERE)
        assert b.js(has(TILE + " [data-toolsmith=now]", "Open your vault"))
        _type(b, TILE + " input[type=password]", FAKE_KEY)
        b.click(*_text_point(b, TILE + " button", "Save the key"))
        assert b.wait(has(TILE + " [data-said=toolsmith-key]", "inbox"), timeout_s=20), b.js(WHERE)
        assert b.js(f"document.querySelector({json.dumps(TILE + ' input[type=password]')}).value") == ""
        assert b.js(no_key) is True

        # From Zone 3 Bridge: the card to it, next to GitHub's; an old address lands on it too
        b.js("location.hash = '#developer/bridge'")
        assert b.wait("!!document.querySelector(\"#view-developer [data-goto='#settings/toolsmith']\")", timeout_s=20), b.js(WHERE)
        assert b.js("Array.from(document.querySelector('#view-developer [data-dev=goto]').children).map(n => n.dataset.goto)") == ["#outside", "#developer/github", "#settings/toolsmith"]
        assert not b.js("!!document.querySelector('#view-developer [data-tile=toolsmith]')")
        assert not b.js("Array.from(document.querySelectorAll('#view-developer .fail')).map(n => n.textContent).join(' ')"), b.js(WHERE)
        b.js("location.hash = '#developer/toolsmith'")
        assert b.wait(f"location.hash === '#settings/toolsmith' && !!document.querySelector({json.dumps(TILE + ' [data-toolsmith=now]')})", timeout_s=20), b.js(WHERE)
    assert _files_holding(state, FAKE_KEY) == []
    assert FAKE_KEY not in (tmp_path / "core" / "serve.txt").read_text(encoding="utf-8", errors="replace")
