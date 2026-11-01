"""Lane I18f: the Developer page shows and edits each zone, on v2's Core routes, with the wizard's own Yes.

- Zone 1: ``/v1/folders`` (the data folder and linked folders the Library and Toolsmith's zone read), and the
  wizard's vault step now writes ``settings\\folders.json`` too (before, only ``data.json``, which nothing read).
- Zone 3: Toolsmith's keys in the vault (never shown back), a lower cost cap at once and a higher one refused,
  the hosts Toolsmith may reach (read only), and Toolsmith's search: on is a Yes card, off is at once.
- The page's logic (tests/js/i18f.test.js, node) and one real-Edge run over Developer on free ports, every
  press a real click (``test_i10a_desktop_clicks``'s harness).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time

import pytest

from conftest import REPO
from puffpai_core.api.app import create_app

PIN = "Tq7#mz2Lp!"                     # a made-up test PIN (tests/z1kit.py), nobody's real one
FAKE_KEY = "not-a-real-key-" + "0" * 12   # never a real key; checked never to come back
NODE = shutil.which("node")


@pytest.fixture
def api(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    return app.test_client(), dev, owner, core


# -- Zone 1: folders ---------------------------------------------------------------------------------------

def test_folders_link_and_unlink_and_toolsmith_never_touches_them(api, state, tmp_path):
    client, dev, owner, core = api
    got = client.get("/v1/folders", headers=owner).json
    assert got["linked"] == [] and got["default"] is True and got["from_setup"] is None
    mine = tmp_path / "Recipes"
    mine.mkdir()
    (mine / "soup.txt").write_text("Leek soup: leeks, potatoes, stock.", encoding="utf-8")
    assert client.post("/v1/folders/link", json={"path": str(mine)}, headers=owner).status_code == 403   # a paired device only
    r = client.post("/v1/folders/link", json={"path": str(mine)}, headers=dev)
    assert r.status_code == 200 and [x["path"] for x in r.json["linked"]] == [str(mine.resolve())], r.json
    assert r.json["linked"][0]["there"] is True and r.headers["Cache-Control"] == "no-store"
    assert client.post("/v1/folders/link", json={"path": str(mine)}, headers=dev).status_code == 409
    for bad, word in ((str(state), "vault"), ("Recipes", "whole path"), (str(tmp_path / "nope"), "doesn't exist"),
                      (str(state / "data"), "own folder"), ("x" * 1001, "at most"), (7, "Send")):
        if bad == str(state / "data"):
            (state / "data").mkdir(parents=True, exist_ok=True)
        r = client.post("/v1/folders/link", json={"path": bad}, headers=dev)
        assert r.status_code == 400 and word in r.json["error"], (bad, r.json)
    # Toolsmith's zone reads the same file: a linked folder is a "never" place for it
    from puffpai_core.zone3 import toolsmith_zone as tz
    places = tz._internal(str(state))                                     # noqa: SLF001
    assert (str(mine.resolve()), tz.LINKED) in places
    # the Library reads it too
    from puffpai_core.zone1 import archivist
    from puffpai_core.zone1.folders import Library
    assert Library(archivist.for_state(core.state, core.bus), core.state).linked() == [mine.resolve()]
    r = client.post("/v1/folders/unlink", json={"path": str(mine)}, headers=dev)
    assert r.status_code == 200 and r.json["linked"] == []
    assert client.post("/v1/folders/unlink", json={"path": str(mine)}, headers=dev).status_code == 404
    assert (mine / "soup.txt").read_text(encoding="utf-8").startswith("Leek soup")          # never touched


def test_a_folder_holding_the_vault_is_never_linked(api, state, tmp_path):
    client, dev, _owner, _core = api
    vault_home = tmp_path / "VaultHere"
    (vault_home / "vault").mkdir(parents=True)
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "vault.json").write_text(json.dumps({"folder": str(vault_home / "vault")}), encoding="utf-8")
    for p in (vault_home, vault_home / "vault"):
        r = client.post("/v1/folders/link", json={"path": str(p)}, headers=dev)
        assert r.status_code == 400 and "vault" in r.json["error"], r.json
    data = tmp_path / "MyData"
    (data / "inside").mkdir(parents=True)
    (state / "settings" / "folders.json").write_text(json.dumps({"data": str(data), "linked": []}), encoding="utf-8")
    for p in (data, data / "inside"):
        r = client.post("/v1/folders/link", json={"path": str(p)}, headers=dev)
        assert r.status_code == 400 and "data folder" in r.json["error"], r.json


def test_folders_setup_saved_are_used_after_one_press(api, state, tmp_path):
    client, dev, owner, _core = api
    recipes = tmp_path / "Recipes"
    recipes.mkdir()
    data = tmp_path / "PuffPAI Data"
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "data.json").write_text(json.dumps({"data_folder": str(data), "linked": [str(recipes), str(tmp_path / "gone")]}),
                                                  encoding="utf-8")
    got = client.get("/v1/folders", headers=owner).json
    assert got["from_setup"] == {"data": str(data), "linked": [str(recipes), str(tmp_path / "gone")]}
    r = client.post("/v1/folders/from-setup", headers=dev)
    assert r.status_code == 200, r.json
    assert r.json["data"] == str(data) and [x["path"] for x in r.json["linked"]] == [str(recipes.resolve())]
    assert r.json["from_setup"] is None and any("gone" in line for line in r.json["lines"])
    assert data.is_dir()
    assert client.post("/v1/folders/from-setup", headers=dev).status_code == 409             # once only


def test_the_wizard_vault_step_writes_the_folders_the_library_reads(state, tmp_path):
    from wizard_kit import CODE, Kit
    recipes = tmp_path / "Recipes"
    recipes.mkdir()
    (state / "settings").mkdir(parents=True, exist_ok=True)
    before = tmp_path / "Before"
    before.mkdir()
    (state / "settings" / "folders.json").write_text(json.dumps({"linked": [str(before)]}), encoding="utf-8")
    eng = Kit(state, env={"USERPROFILE": str(tmp_path)}).engine()
    eng.submit("vault", {"where": "here", "pin": "482913", "pin_again": "482913"})
    eng.submit("vault", {"recovery_confirm": CODE})
    eng.submit("vault", {"data_folder": str(tmp_path / "Data"), "knowledge_folder": "", "linked": str(recipes)})
    got = json.loads((state / "settings" / "folders.json").read_text(encoding="utf-8"))
    assert got["data"] == str(tmp_path / "Data")
    assert got["linked"] == [str(before), str(recipes)]                 # hers from before stay linked
    from puffpai_core.zone3 import toolsmith_zone as tz
    places = tz._internal(str(state))                                   # noqa: SLF001
    assert (str(recipes), tz.LINKED) in places and (str(tmp_path / "Data"), tz.DATA) in places


# -- Zone 3: Toolsmith's keys, cap, hosts and search --------------------------------------------------------

def _provider(client, owner):
    got = client.get("/v1/toolsmith/providers", headers=owner).json
    assert got, "the Core ships Toolsmith's providers"
    return got[0]


def test_toolsmith_keys_go_into_the_vault_and_never_come_back(api):
    client, dev, owner, _core = api
    p = _provider(client, owner)
    r = client.put(f"/v1/toolsmith/keys/{p['id']}", json={"value": FAKE_KEY}, headers=dev)
    assert r.status_code == 409 and "Set up the vault first" in r.json["error"]
    assert client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev).status_code == 201
    assert client.put(f"/v1/toolsmith/keys/{p['id']}", json={"value": FAKE_KEY}, headers=owner).status_code == 403
    assert client.put("/v1/toolsmith/keys/nobody", json={"value": FAKE_KEY}, headers=dev).status_code == 404
    assert client.put(f"/v1/toolsmith/keys/{p['id']}", json={"value": "a\nb"}, headers=dev).status_code == 400
    r = client.put(f"/v1/toolsmith/keys/{p['id']}", json={"value": FAKE_KEY}, headers=dev)
    assert r.status_code == 200 and r.json["saved"] is True and FAKE_KEY not in r.get_data(as_text=True)
    keys = client.get("/v1/toolsmith/keys", headers=owner)
    row = next(k for k in keys.json if k["provider"] == p["id"])
    assert row["kept"] is True and row["vault_name"] == f"toolsmith.{p['id']}" and FAKE_KEY not in keys.get_data(as_text=True)
    assert client.delete(f"/v1/toolsmith/keys/{p['id']}", headers=dev).status_code == 200
    assert next(k for k in client.get("/v1/toolsmith/keys", headers=owner).json if k["provider"] == p["id"])["kept"] is False
    assert client.delete(f"/v1/toolsmith/keys/{p['id']}", headers=dev).status_code == 404
    client.post("/v1/vault/lock", headers=owner)
    r = client.put(f"/v1/toolsmith/keys/{p['id']}", json={"value": FAKE_KEY}, headers=dev)   # locked: sealed, as the wizard does
    assert r.status_code == 202 and r.json["saved"] is False and FAKE_KEY not in r.get_data(as_text=True)
    assert next(k for k in client.get("/v1/toolsmith/keys", headers=owner).json if k["provider"] == p["id"])["kept"] is None
    assert client.delete(f"/v1/toolsmith/keys/{p['id']}", headers=dev).status_code == 423


def test_a_lower_cap_is_at_once_and_a_higher_one_needs_her_yes(api):
    client, dev, owner, _core = api
    assert client.put("/v1/toolsmith/cap", json={"cost_cap_usd": 1}, headers=dev).status_code == 409      # local: no cost
    p = _provider(client, owner)
    model = p["default_model"] or p["models"][0]["id"]
    # her cloud choice, as PUT /v1/toolsmith/model keeps it (this test Core has no Bridge to record the Yes on)
    from puffpai_core.zone3.toolsmith_host import settings as ts_settings
    ts_settings.write(_core.state, {"where": "cloud", "provider": p["id"], "model": model, "cost_cap_usd": 5.0,
                                    "consent": {"sha": p["consent_sha"], "at": "2026-10-27T10:00:00+00:00", "by": "Test laptop"}})
    assert client.put("/v1/toolsmith/cap", json={"cost_cap_usd": 2}, headers=owner).status_code == 403
    r = client.put("/v1/toolsmith/cap", json={"cost_cap_usd": 2}, headers=dev)
    assert r.status_code == 200 and r.json == {"where": "cloud", "provider": p["id"], "model": model, "cost_cap_usd": 2.0}
    r = client.put("/v1/toolsmith/cap", json={"cost_cap_usd": 3}, headers=dev)
    assert r.status_code == 409 and r.json["code"] == "needs_yes"
    for bad in ("x", -1, True, None, float("inf")):
        assert client.put("/v1/toolsmith/cap", data=json.dumps({"cost_cap_usd": bad}) if bad != float("inf") else '{"cost_cap_usd": 1e999}',
                          content_type="application/json", headers=dev).status_code == 400, bad
    st = client.get("/v1/toolsmith", headers=owner).json
    assert st["cost_cap_usd"] == 2.0 and st["provider"] == p["id"]
    hosts = client.get("/v1/toolsmith/hosts", headers=owner).json
    assert hosts["cloud"]["on"] is True and set(hosts["cloud"]["hosts"]) <= set(p["hosts"]) and hosts["cloud"]["hosts"]


def test_toolsmith_search_on_is_a_yes_card_and_off_is_at_once(api):
    from puffpai_core.wiring import WizardBridge
    client, dev, owner, _core = api
    hosts = client.get("/v1/toolsmith/hosts", headers=owner).json
    assert hosts["search"] == {"hosts": list(WizardBridge.SEARCH_HOSTS), "on": False, "switch": False}
    assert hosts["knowledge"]["hosts"] and hosts["cloud"]["on"] is False
    assert client.post("/v1/bridge/search", json={"on": True}, headers=owner).status_code == 403
    assert client.post("/v1/bridge/search", json={"on": "yes"}, headers=dev).status_code == 400
    r = client.post("/v1/bridge/search", json={"on": True}, headers=dev)
    assert r.status_code == 202 and r.json["title"] == "Let Toolsmith search while it builds?", r.json
    assert any("api.github.com" in line for line in r.json["lines"]), r.json["lines"]
    bridge = client.get("/v1/bridge", headers=owner).json
    assert not bridge["switches"].get(WizardBridge.SEARCH)                # nothing changes before her Yes
    pending = [c for c in client.get("/v1/consent?status=pending", headers=owner).json if c["consent_id"] == r.json["consent_id"]]
    assert pending and pending[0]["title"] == "Let Toolsmith search while it builds?"
    a = client.post(f"/v1/consent/{r.json['consent_id']}/answer", json={"answer": "yes", "factor": "tap", "card_sha": r.json["card_sha"]},
                    headers=dev)
    assert a.status_code == 200, a.json
    bridge = client.get("/v1/bridge", headers=owner).json
    assert bridge["switches"].get(WizardBridge.SEARCH) is True and bridge["state"] == "closed"   # the Bridge stays as it was
    assert client.get("/v1/toolsmith/hosts", headers=owner).json["search"]["switch"] is True
    r = client.post("/v1/bridge/search", json={"on": False}, headers=dev)
    assert r.status_code == 200 and r.json["switches"].get(WizardBridge.SEARCH) is False and r.json["search_on"] is False


# -- the page's logic --------------------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_developer_page_logic():
    web = REPO / "packages" / "desktop" / "web"
    for f in (web / "devzones.js", web / "pages" / "developer.js"):
        got = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert got.returncode == 0, got.stderr
        src = f.read_text(encoding="utf-8")
        assert "innerHTML" not in src and "document.write" not in src and "eval(" not in src
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "i18f.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr
    html = (web / "index.html").read_text(encoding="utf-8")
    assert html.index("/ui/devzones.js") < html.index("/ui/pages/developer.js")


def test_the_page_keeps_her_words_and_never_says_doors():
    src = (REPO / "packages" / "desktop" / "web" / "pages" / "developer.js").read_text(encoding="utf-8") + \
        (REPO / "packages" / "desktop" / "web" / "devzones.js").read_text(encoding="utf-8")
    assert "Nothing leaves your computer without your consent." in src
    low = src.lower()
    assert "doors" not in low and " beta" not in low and chr(0x2014) not in src


# -- one real-Edge run over Developer ------------------------------------------------------------------------

def test_developer_zones_by_real_clicks(state, tmp_path, monkeypatch, drawbridge):
    """Every press a real click: the zone cards open their pages; Zone 1 links and unlinks a folder; Zone 3 turns
    Toolsmith's search on through its Yes card and off at once, refuses a cloud model without her tick, and
    says a key needs the vault first; the command line keeps showing what it prints after going away and back."""
    import socket
    from test_i10a_desktop_clicks import EDGE, WHERE, _desktop, _point, _text_point
    if os.name != "nt" or EDGE is None:
        pytest.skip("Windows Job Objects and Microsoft Edge")
    from test_i10a_desktop_clicks import _call, _type
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    closed_port = s.getsockname()[1]
    s.close()
    mine = tmp_path / "Recipes"
    mine.mkdir()

    def has(sel, text):
        return f"!!document.querySelector({json.dumps(sel)}) && document.querySelector({json.dumps(sel)}).textContent.indexOf({json.dumps(text)}) >= 0"

    # no model server is reached at all: Ollama's address is a closed port on this computer
    with _desktop(state, tmp_path, monkeypatch, complete=True, env={"OLLAMA_HOST": f"127.0.0.1:{closed_port}"}) as b:
        b.click(*_point(b, ".rail a.item[href='#developer']", 0))
        assert b.wait("!!document.querySelector('[data-zone-open=vault]')", timeout_s=30), b.js(WHERE)
        assert b.js(has("#view-developer [data-dev=zones]", "ZONE 1")) and b.js(has("#view-developer", "Add your own"))
        # the command line: start, run, go to Zone 1 and back, run again: its output still shows
        b.click(*_text_point(b, "#view-developer [data-tile=terminal] button", "New terminal"))
        assert b.wait("!document.querySelector('#view-developer input[aria-label=Command]').disabled", timeout_s=30)
        _type(b, "#view-developer input[aria-label=Command]", "echo first-i18f")
        b.click(*_text_point(b, "#view-developer .term-form button", "Run"))
        assert b.wait(has("#view-developer .term", "first-i18f\n") + " || /\\nfirst-i18f/.test(document.querySelector('#view-developer .term').textContent)", timeout_s=30)
        # Zone 1 by its card
        b.click(*_point(b, "#view-developer [data-zone-open=vault]", 0))
        assert b.wait("location.hash === '#developer/vault' && !!document.querySelector('[data-tile=folders]')", timeout_s=20), b.js(WHERE)
        assert b.js(has("#view-developer", "Zone 1: Vault")) and b.js(has("#view-developer [data-tile=vault-now]", "Not made yet"))
        _type(b, "#view-developer input[aria-label='A folder to link']", str(mine))
        b.click(*_text_point(b, "#view-developer [data-tile=folders] button", "Link this folder"))
        assert b.wait(f"!!document.querySelector('#view-developer [data-linked]') && {has('#view-developer [data-dev=linked]', str(mine.resolve()))}"), b.js(WHERE)
        folders = json.loads((state / "settings" / "folders.json").read_text(encoding="utf-8"))
        assert folders["linked"] == [str(mine.resolve())]
        b.click(*_text_point(b, "#view-developer [data-dev=linked] button", "Unlink"))
        assert b.wait(has("#view-developer [data-tile=folders]", "No folders are linked yet."))
        # back to the overview: the command line still follows its shell
        b.click(*_point(b, "#view-developer a[data-dev=back]", 0))
        assert b.wait("location.hash === '#developer' && !!document.querySelector('#view-developer [data-tile=terminal]')")
        assert b.wait("!document.querySelector('#view-developer input[aria-label=Command]').disabled", timeout_s=20)
        _type(b, "#view-developer input[aria-label=Command]", "echo second-i18f")
        b.click(*_text_point(b, "#view-developer .term-form button", "Run"))
        assert b.wait("/\\nsecond-i18f/.test(document.querySelector('#view-developer .term').textContent)", timeout_s=30), \
            b.js("document.querySelector('#view-developer .term').textContent")
        b.click(*_text_point(b, "#view-developer [data-tile=terminal] button", "Close"))
        # Zone 3 by its card
        b.click(*_point(b, "#view-developer [data-zone-open=bridge]", 0))
        assert b.wait("location.hash === '#developer/bridge' && !!document.querySelector('[data-tile=bridge-now] button')", timeout_s=20), b.js(WHERE)
        b.click(*_text_point(b, "#view-developer [data-tile=bridge-now] button", "Let Toolsmith search while it builds"))
        assert b.wait("!!document.querySelector('#view-developer [data-tile=bridge-now] .card.yes')"), b.js(WHERE)
        assert b.js(has("#view-developer .card.yes", "api.github.com"))
        b.click(*_text_point(b, "#view-developer .card.yes button", "Yes"))
        assert b.wait(has("#view-developer [data-said=bridge]", "Your Yes is kept.") + " && " +
                      has("#view-developer [data-tile=bridge-now]", "Turn off Toolsmith's search"), timeout_s=20), b.js(WHERE)
        assert _call(b, "GET", "/v1/bridge")["data"]["switches"]["toolsmith:search"] is True
        b.click(*_text_point(b, "#view-developer [data-tile=bridge-now] button", "Turn off Toolsmith's search"))
        assert b.wait(has("#view-developer [data-tile=bridge-now]", "Let Toolsmith search while it builds"), timeout_s=20)
        assert _call(b, "GET", "/v1/bridge")["data"]["switches"]["toolsmith:search"] is False
        # Toolsmith's model: a company without her tick is not saved; a key needs the vault first
        sel = "#view-developer [data-tile=toolsmith] select[aria-label='Where Toolsmith thinks']"
        pid = b.js(f"document.querySelector({json.dumps(sel)}).options[1].value")
        b.js(f"(() => {{ const s = document.querySelector({json.dumps(sel)}); s.value = {json.dumps(pid)}; s.dispatchEvent(new Event('change')); }})()")
        b.click(*_text_point(b, "#view-developer [data-tile=toolsmith] button", "Save"))
        assert b.wait(has("#view-developer [data-said=toolsmith]", "Tick Yes to what Toolsmith sends first.")), b.js(WHERE)
        assert _call(b, "GET", "/v1/toolsmith")["data"]["where"] == "local"
        _type(b, "#view-developer [data-tile=toolsmith] input[type=password]", FAKE_KEY)
        b.click(*_text_point(b, "#view-developer [data-tile=toolsmith] button", "Save the key"))
        assert b.wait(has("#view-developer [data-said=toolsmith-key]", "Set up the vault first")), b.js(WHERE)
        assert b.js("document.querySelector('#view-developer [data-tile=toolsmith] input[type=password]').value") == ""
        assert FAKE_KEY not in b.js("document.body.innerText")
        # Zone 2 by its card: the brain now, and the models section
        b.click(*_point(b, "#view-developer a[data-dev=back]", 0))
        assert b.wait("!!document.querySelector('[data-zone-open=brain]')")
        b.click(*_point(b, "#view-developer [data-zone-open=brain]", 0))
        assert b.wait("location.hash === '#developer/brain' && !!document.querySelector('[data-tile=brain-now]') && "
                      "!!document.querySelector('[data-tile=models]')", timeout_s=20), b.js(WHERE)
        assert b.js(has("#view-developer [data-tile=brain-now]", "Model in use"))
        time.sleep(0.5)
        assert not b.js("Array.from(document.querySelectorAll('#view-developer .fail')).map(n => n.textContent).join(' ')"), b.js(WHERE)


# -- the fix round: one folder check everywhere, and no old search card can turn the search back on ----------

def _junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def _drop_junction(link):
    subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)      # the junction only, never its target


def _short(p) -> str:
    import ctypes
    buf = ctypes.create_unicode_buffer(500)
    ctypes.windll.kernel32.GetShortPathNameW(str(p), buf, 500)
    return buf.value


def test_the_wizard_links_no_folder_the_check_refuses(state, tmp_path):
    """The verifier's repro: Setup's linked folders were written with no check at all."""
    from wizard_kit import CODE, Kit
    eng = Kit(state, env={"USERPROFILE": str(tmp_path)}).engine()
    eng.submit("vault", {"where": "here", "pin": "482913", "pin_again": "482913"})
    eng.submit("vault", {"recovery_confirm": CODE})
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "vault").mkdir(parents=True, exist_ok=True)
    ok = tmp_path / "Recipes"
    ok.mkdir()
    linked = ";".join([str(state), str(state / "vault"), str(tmp_path), str(state).upper() + "\\settings", str(ok)])
    screen = eng.submit("vault", {"data_folder": str(tmp_path / "Data"), "knowledge_folder": "", "linked": linked})
    got = json.loads((state / "settings" / "folders.json").read_text(encoding="utf-8"))
    assert got["linked"] == [str(ok)], got                       # only the folder the check allows
    said = " ".join(screen.get("lines") or [])
    assert said.count("was not linked") == 4, said               # each refused folder said plainly
    from puffpai_core.zone1 import archivist
    from puffpai_core.zone1.folders import Library
    lib = Library(archivist.for_state(state), state)
    assert not any(str(p).lower().startswith(str(state).lower()) for p in lib._files())      # noqa: SLF001


def test_the_wizard_never_makes_a_vault_its_data_folder(state, tmp_path):
    from wizard_kit import CODE, Kit
    eng = Kit(state, env={"USERPROFILE": str(tmp_path)}).engine()
    eng.submit("vault", {"where": "here", "pin": "482913", "pin_again": "482913"})
    eng.submit("vault", {"recovery_confirm": CODE})
    eng.submit("vault", {"data_folder": str(state / "vault"), "knowledge_folder": "", "linked": ""})
    got = json.loads((state / "settings" / "folders.json").read_text(encoding="utf-8"))
    assert got.get("data") in (None, str(state / "data")), got


def test_from_setup_checks_a_vault_kept_elsewhere_and_the_data_folder(api, state, tmp_path):
    """The verifier's repro: from-setup linked a folder holding a vault kept elsewhere, and set the data folder
    to that vault with no check."""
    client, dev, owner, _core = api
    vh = tmp_path / "VH"
    (vh / "vault").mkdir(parents=True)
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "vault.json").write_text(json.dumps({"folder": str(vh / "vault")}), encoding="utf-8")
    (state / "settings" / "data.json").write_text(json.dumps({"data_folder": str(vh / "vault"), "linked": [str(vh)]}),
                                                  encoding="utf-8")
    r = client.post("/v1/folders/from-setup", headers=dev)
    assert r.status_code == 200, r.json
    assert r.json["linked"] == [] and r.json["data"] == str(state / "data"), r.json
    assert sum("vault" in line for line in r.json["lines"]) == 2, r.json["lines"]
    # the link route and Library.link agree
    for p in (vh, vh / "vault"):
        assert client.post("/v1/folders/link", json={"path": str(p)}, headers=dev).status_code == 400


@pytest.mark.skipif(os.name != "nt", reason="junctions and 8.3 names are Windows")
def test_no_spelling_of_the_bot_folder_or_vault_gets_linked(api, state, tmp_path):
    client, dev, _owner, _core = api
    (state / "vault").mkdir(parents=True, exist_ok=True)
    (state / "settings").mkdir(parents=True, exist_ok=True)
    long_name = state / "settings" / "LongFolderNameHere"
    long_name.mkdir()
    j, j2 = tmp_path / "J", tmp_path / "J2"
    _junction(j, state)
    _junction(j2, state / "vault")
    try:
        cases = [str(state).upper(), str(state / "vault" / ".." / "vault"), str(long_name), str(j), str(j2),
                 str(j / "settings"), str(tmp_path), str(state).replace("\\", "/"), str(state) + "\\\\"]
        cases += [s for s in (_short(long_name), _short(state)) if s]
        for p in cases:
            r = client.post("/v1/folders/link", json={"path": p}, headers=dev)
            assert r.status_code == 400, (p, r.json)
    finally:
        for x in (j, j2):
            _drop_junction(x)
    assert json.loads((state / "settings" / "folders.json").read_text(encoding="utf-8")).get("linked", []) == [] \
        if (state / "settings" / "folders.json").exists() else True


def test_search_off_withdraws_old_cards_and_on_reuses_the_waiting_one(api):
    from puffpai_core.wiring import WizardBridge
    client, dev, owner, _core = api

    def yes(r):
        return client.post(f"/v1/consent/{r['consent_id']}/answer",
                           json={"answer": "yes", "factor": "tap", "card_sha": r["card_sha"]}, headers=dev)

    def switch():
        return client.get("/v1/bridge", headers=owner).json["switches"].get(WizardBridge.SEARCH)

    r1 = client.post("/v1/bridge/search", json={"on": True}, headers=dev).json
    r2 = client.post("/v1/bridge/search", json={"on": True}, headers=dev).json
    assert r2["consent_id"] == r1["consent_id"] and r2.get("again") is True        # one card, not two
    assert yes(r1).status_code == 200 and switch() is True
    r3 = client.post("/v1/bridge/search", json={"on": True}, headers=dev).json     # a new waiting card
    assert r3["consent_id"] != r1["consent_id"]
    assert client.post("/v1/bridge/search", json={"on": False}, headers=dev).status_code == 200 and not switch()
    assert yes(r3).status_code >= 400 and not switch()                            # the waiting card was withdrawn
    assert yes(r1).status_code >= 400 and not switch()                            # the old Yes was taken back
    live = [c for c in client.get("/v1/consent?status=granted", headers=owner).json
            if (c.get("scope") or {}).get("target") == WizardBridge.SEARCH]
    assert live == []
