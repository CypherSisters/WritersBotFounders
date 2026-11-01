"""Lane L5 routes: checklist, events, wizard, settings, notify, theme, people, ui and pairing."""

import json
import shutil

import pytest

from conftest import REPO, record_install
from puffpai_core import pairing
from puffpai_core.api import create_app
from puffpai_core.bus import Envelope
from puffpai_core.registry import row_from_manifest


@pytest.fixture
def app(state):
    return create_app(state)


@pytest.fixture
def core(app):
    return app.extensions["puffpai"]


@pytest.fixture
def client(app):
    from desktop_kit import desktop_client
    return desktop_client(app)                 # D79: the Desktop's own address, http://puffpai.localhost:8420


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def owner(core):
    return bearer(core.tokens.issue_owner_key())


@pytest.fixture
def device(core):
    return bearer(core.tokens.issue_device("laptop"))


DOCKER = {"id": "docker", "kind": "run_command", "shell": "powershell", "admin": True,
          "title": "Install Docker Desktop", "why": "World Monitor runs in Docker.",
          "text": "winget install -e --id Docker.DockerDesktop"}


DESKTOP = {"Origin": "http://puffpai.localhost:8420", "X-PuffPAI": "1"}   # what desktop.js sends (D79)


def _events(client, headers, after=0):
    r = client.get(f"/v1/events?after={after}&timeout=0", headers=headers)
    assert r.status_code == 200 and r.mimetype == "text/event-stream"
    out = []
    for block in r.get_data(as_text=True).split("\n\n"):
        data = [line[6:] for line in block.split("\n") if line.startswith("data: ")]
        if data:
            out.append(json.loads(data[0]))
    return out


# -- checklist ----------------------------------------------------------------
def _ids(client, headers, all_=False):
    return [s["id"] for s in client.get("/v1/checklist" + ("?all=1" if all_ else ""), headers=headers).json]


def test_checklist_raise_done_skip(client, owner):
    step = {**DOCKER, "admin": False}
    r = client.post("/v1/checklist", json={**step, "package": "toolsmith", "blocks": ["worldmonitor"]}, headers=owner)
    assert r.status_code == 201
    s = r.json
    assert s["package"] == "toolsmith" and s["blocks"] == ["worldmonitor"] and s["state"] == "open"
    assert s["asked_id"] == "docker" and s["id"] != "docker" and s["id"].startswith("step_")   # random, not guessable
    assert s["raised_by"] == "owner"
    again = client.post("/v1/checklist", json={**step, "package": "toolsmith"}, headers=owner).json
    assert again["id"] == s["id"]                       # raised again while open: the same card
    assert _ids(client, owner) == [s["id"]]
    assert client.post(f"/v1/checklist/{s['id']}/done", headers=owner).json["state"] == "done"
    assert client.get("/v1/checklist", headers=owner).json == []
    assert client.get("/v1/checklist?all=1", headers=owner).json[0]["closed_by"] == "owner"
    mic = client.post("/v1/checklist", json={"id": "mic", "kind": "plug_in", "title": "Plug in your USB microphone"},
                      headers=owner).json["id"]
    assert client.post(f"/v1/checklist/{mic}/skip", headers=owner).json["state"] == "skipped"


def test_step_ids_are_random(app):
    cl = app.extensions["puffpai.checklist"]
    a = cl.add({"id": "docker", "kind": "confirm", "title": "A"}, "one", trust="core")
    b = cl.add({"id": "docker", "kind": "confirm", "title": "B"}, "two", trust="core")
    assert a["id"] != b["id"] and "docker" not in a["id"] and len(a["id"]) >= 20
    forged = cl.add({"id": "x", "kind": "confirm", "title": "C", "raised_by": "core", "state": "done"}, "p")
    assert forged["raised_by"] == "pkg:p" and forged["state"] == "open"     # Core fields never come from the raiser


def test_checklist_refuses_bad_steps(client, owner, core):
    r = client.post("/v1/checklist", json={"id": "x", "kind": "run_command", "title": "No text"}, headers=owner)
    assert r.status_code == 400 and "text" in r.json["error"]
    pkg = bearer(core.tokens.issue_package_token("freshtable"))
    r = client.post("/v1/checklist", json={**DOCKER, "admin": False, "check": {"command": ["docker", "version"]}},
                    headers=pkg)
    assert r.status_code == 400 and "not run a command" in r.json["error"]


def test_package_can_never_raise_an_admin_step(client, owner, device, core, monkeypatch):
    """The L5 verifier's finding: a package token raised an admin step a paired device then ran elevated."""
    import subprocess
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr("sys.platform", "win32")
    pkg = bearer(core.tokens.issue_package_token("evil"))
    r = client.post("/v1/checklist", json={**DOCKER, "text": "whoami"}, headers=pkg)
    assert r.status_code == 400 and "administrator" in r.json["error"]
    assert client.get("/v1/checklist", headers=owner).json == []
    # Over HTTP no one raises an admin step or a command check, the owner's own key included.
    for headers in (owner, device):
        r = client.post("/v1/checklist", json=DOCKER, headers=headers)
        assert r.status_code == 400 and "administrator" in r.json["error"]
        r = client.post("/v1/checklist", json={"id": "c", "kind": "confirm", "title": "t",
                                                "check": {"command": ["whoami"]}}, headers=headers)
        assert r.status_code == 400
    # On the Bus, a package is still a package.
    r = core.bus.send(Envelope(intent="checklist.add", payload={"step": DOCKER}, requester="pkg:evil"), to="majordomo")
    assert not r["ok"] and "administrator" in r["error"]
    assert calls == []


def test_checklist_check_runs_on_done(client, owner, tmp_path):
    flag = tmp_path / "flag.txt"
    sid = client.post("/v1/checklist", json={"id": "f", "kind": "confirm", "title": "Make the file",
                                              "check": {"file": str(flag)}}, headers=owner).json["id"]
    r = client.post(f"/v1/checklist/{sid}/done", headers=owner)
    assert r.status_code == 409 and "is not there yet" in r.json["error"]
    assert client.get(f"/v1/checklist/{sid}", headers=owner).json["state"] == "open"
    flag.write_text("x")
    assert client.post(f"/v1/checklist/{sid}/done", headers=owner).json["state"] == "done"


def test_checklist_command_check(client, owner, app):
    import sys
    cl = app.extensions["puffpai.checklist"]               # command checks come from the Core in process
    sid = cl.add({"id": "py", "kind": "confirm", "title": "Python is here",
                  "check": {"command": [sys.executable, "-c", "raise SystemExit(3)"]}}, "toolsmith", trust="core")["id"]
    r = client.post(f"/v1/checklist/{sid}/done", headers=owner)
    assert r.status_code == 409 and "exit code 3" in r.json["error"]


def test_package_checks_reach_only_its_own_port_and_folders(client, owner, core, state, freshtable):
    core.registry.add(row_from_manifest(freshtable, "made", port=5123))
    ft = bearer(core.tokens.issue_package_token("freshtable"))
    bad = ["http://127.0.0.1:8420/v1/health", "http://127.0.0.1:5124/", "http://localhost:5123/",
           "http://192.168.1.1:5123/", "https://127.0.0.1:5123/", "http://user@127.0.0.1:5123/"]
    for url in bad:
        r = client.post("/v1/checklist", json={"id": "u", "kind": "confirm", "title": "u", "check": {"url": url}},
                        headers=ft)
        assert r.status_code == 400 and "own address" in r.json["error"], url
    ok = client.post("/v1/checklist", json={"id": "u", "kind": "confirm", "title": "u",
                                             "check": {"url": "http://127.0.0.1:5123/health"}}, headers=ft)
    assert ok.status_code == 201
    for path in (str(state / "registry.db"), str(state / "packages" / "other" / "x"),
                 str(state / "data" / "freshtable" / ".." / "other" / "x"), "C:/Windows/win.ini", "../../registry.db"):
        r = client.post("/v1/checklist", json={"id": "f", "kind": "confirm", "title": "f", "check": {"file": path}},
                        headers=ft)
        assert r.status_code == 400 and "own folders" in r.json["error"], path
    mine = state / "data" / "freshtable" / "ready.txt"
    r = client.post("/v1/checklist", json={"id": "f", "kind": "confirm", "title": "f", "check": {"file": str(mine)}},
                    headers=ft)
    assert r.status_code == 201
    assert client.post(f"/v1/checklist/{r.json['id']}/done", headers=ft).status_code == 409
    mine.parent.mkdir(parents=True)
    mine.write_text("x")
    assert client.post(f"/v1/checklist/{r.json['id']}/done", headers=ft).json["state"] == "done"
    # A package with no port has no address to check.
    other = bearer(core.tokens.issue_package_token("weather"))
    r = client.post("/v1/checklist", json={"id": "u", "kind": "confirm", "title": "u",
                                            "check": {"url": "http://127.0.0.1:5123/"}}, headers=other)
    assert r.status_code == 400 and "no address" in r.json["error"]
    # Its port changed after the step was raised: Done re-checks the scope before reaching anything.
    core.registry.update("freshtable", port=6000)
    r = client.post(f"/v1/checklist/{ok.json['id']}/done", headers=ft)
    assert r.status_code == 409 and "own address" in r.json["error"]


def test_package_sees_only_its_own_steps(client, owner, core):
    ft = bearer(core.tokens.issue_package_token("freshtable"))
    other = bearer(core.tokens.issue_package_token("weather"))
    sid = client.post("/v1/checklist", json={"id": "k", "kind": "enter_secret", "title": "Paste your key",
                                              "key": "WEATHER_KEY", "package": "spoofed"}, headers=ft).json["id"]
    assert client.get(f"/v1/checklist/{sid}", headers=ft).json["package"] == "freshtable"
    assert client.get("/v1/checklist", headers=other).json == []
    assert client.get(f"/v1/checklist/{sid}", headers=other).status_code == 404
    assert client.post(f"/v1/checklist/{sid}/done", headers=other).status_code == 404
    assert len(client.get("/v1/checklist", headers=owner).json) == 1


def test_majordomo_on_the_bus(core):
    agent = core.bus.get("majordomo")
    assert agent is not None
    r = core.bus.send(Envelope(intent="checklist.add", payload={"step": DOCKER}, requester="toolsmith",
                               origin_agent="toolsmith"), to="majordomo")
    assert r["ok"] and r["step"]["package"] == "toolsmith" and r["step"]["admin"] is True
    assert r["step"]["raised_by"] == "toolsmith"
    r = core.bus.send(Envelope(intent="checklist.add", payload={"step": {**DOCKER, "id": "d2", "admin": False,
                               "check": {"command": ["docker"]}}}, requester="pkg:freshtable"), to="majordomo")
    assert not r["ok"] and "not run a command" in r["error"]


def test_wait_closed(app):
    import threading
    cl = app.extensions["puffpai.checklist"]
    sid = cl.add({"id": "w", "kind": "confirm", "title": "Restart"}, "toolsmith", trusted=True)["id"]
    threading.Timer(0.1, lambda: cl.done(sid)).start()
    assert cl.wait_closed(sid, timeout=5) == "done"


class _Pin:
    """A stand-in for lane L3's PIN check: ``check(pin) -> None | (status, reason)``."""

    def __init__(self, good="482913"):
        self.good, self.seen = good, []

    def __call__(self, pin):
        self.seen.append(pin)
        return None if pin == self.good else (403, "That PIN is not right.")


def test_run_as_administrator_needs_the_pin(client, owner, device, app, freshtable, monkeypatch):
    import subprocess
    from puffpai_core.api.app import wire_core
    wire_core(app)                                   # the one PIN check is joined by the wiring
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append(argv))   # never a real UAC prompt
    monkeypatch.setattr("sys.platform", "win32")
    cl = app.extensions["puffpai.checklist"]
    manifest = {**freshtable, "setup_steps": [DOCKER]}
    (sid,) = [s["id"] for s in cl.add_setup_steps("freshtable", manifest)]   # from the manifest she said Yes to
    step = client.get(f"/v1/checklist/{sid}", headers=owner).json
    assert step["admin"] is True and step["raised_by"] == "setup_steps:freshtable"
    with pytest.raises(Exception):
        cl.add_setup_steps("other", manifest)                    # not that package's manifest
    url = f"/v1/checklist/{sid}/run-admin"
    pin = {"factor": "pin", "pin": "482913"}
    assert client.post(url, json=pin, headers=owner).status_code == 403           # a paired device only
    r = client.post(url, json=pin, headers=device)
    assert r.status_code == 503 and "Set up your vault" in r.json["error"]   # the one PIN check, no vault yet
    check = _Pin()
    app.extensions["puffpai.pin_check"] = check
    assert client.post(url, headers=device).status_code == 403                       # no PIN in the request
    assert client.post(url, json={"factor": "pin", "pin": "000000"}, headers=device).status_code == 403
    assert client.post(url, json={"factor": "hello"}, headers=device).status_code == 501
    assert calls == []
    assert client.post(url, json=pin, headers=device).status_code == 200
    assert calls and "-Verb RunAs" in calls[0][-1] and "-EncodedCommand" in calls[0][-1]
    assert "winget" not in calls[0][-1]                    # the command travels encoded, never quoted
    import base64
    encoded = calls[0][-1].split("'-EncodedCommand','")[1].split("'")[0]
    assert base64.b64decode(encoded).decode("utf-16-le") == DOCKER["text"]        # the stored, approved text
    plain = cl.add({"id": "plain", "kind": "run_command", "text": "dir", "title": "t"}, "core", trust="core")["id"]
    assert client.post(f"/v1/checklist/{plain}/run-admin", json=pin, headers=device).status_code == 400


def test_run_admin_refuses_a_package_step_even_if_stored(client, device, app, monkeypatch):
    """Defense in depth: even a step that got into the store marked admin never runs if a package raised it."""
    import subprocess
    from puffpai_core import jsonfile
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append(argv))
    monkeypatch.setattr("sys.platform", "win32")
    app.extensions["puffpai.pin_check"] = _Pin()
    cl = app.extensions["puffpai.checklist"]
    sid = cl.add({"id": "x", "kind": "run_command", "text": "dir", "title": "t"}, "evil")["id"]
    steps = jsonfile.read(cl.path, {})["steps"]
    steps[0]["admin"] = True
    jsonfile.write(cl.path, {"steps": steps})
    r = client.post(f"/v1/checklist/{sid}/run-admin", json={"factor": "pin", "pin": "482913"}, headers=device)
    assert r.status_code == 400 and calls == []


# -- events -------------------------------------------------------------------
def test_events_stream_and_filter(client, owner, core):
    ft = bearer(core.tokens.issue_package_token("freshtable"))
    other = bearer(core.tokens.issue_package_token("weather"))
    client.post("/v1/checklist", json={"id": "a", "kind": "confirm", "title": "A"}, headers=ft)
    client.post("/v1/notify", json={"title": "Milk is on the list"}, headers=ft)
    seen = [e["type"] for e in _events(client, owner)]
    assert "checklist.changed" in seen and "notify.posted" in seen
    assert [e["type"] for e in _events(client, ft)] == ["checklist.changed"]   # notify.posted is not for packages
    assert _events(client, other) == []
    first = client.get("/v1/events?after=0&timeout=0", headers=owner).get_data(as_text=True)
    last_id = max(int(line[4:]) for line in first.split("\n") if line.startswith("id: "))
    assert _events(client, owner, after=last_id) == []


def test_events_never_carry_text(client, owner, core):
    ft = bearer(core.tokens.issue_package_token("freshtable"))
    client.post("/v1/notify", json={"title": "Secret recipe", "text": "the words"}, headers=ft)
    body = json.dumps(_events(client, owner))
    assert "Secret recipe" not in body and "the words" not in body


# -- notify -------------------------------------------------------------------
def test_notify(client, owner, core):
    ft = bearer(core.tokens.issue_package_token("freshtable"))
    r = client.post("/v1/notify", json={"title": "Dinner plan ready", "text": "Tacos on Tuesday.", "urgency": "high"},
                    headers=ft)
    assert r.status_code == 201
    assert client.get("/v1/notify", headers=ft).status_code == 403          # reading is the owner's
    items = client.get("/v1/notify", headers=owner).json
    assert items[0]["package"] == "freshtable" and items[0]["urgency"] == "high"
    assert client.delete(f"/v1/notify/{items[0]['id']}", headers=owner).json["ok"]
    assert client.get("/v1/notify", headers=owner).json == []
    assert client.post("/v1/notify", json={"text": "no title"}, headers=ft).status_code == 400
    assert client.post("/v1/notify", json={"title": "x", "urgency": "panic"}, headers=ft).status_code == 400


# -- settings, theme, people ---------------------------------------------------
def _install_avatar(state, core):
    folder = state / "packages" / "cloud-avatar"
    folder.mkdir(parents=True)
    manifest = {"schema": "puffpai-package/1", "id": "cloud-avatar", "kind": "avatar", "name": "Cloud",
                "version": "1.0.0", "description": "A cloud.", "author": "Cypher Sisters", "license": "MIT",
                "consent": "Pictures only.", "avatar": {"model": "cloud.glb", "preview": "cloud.png", "voice": None}}
    (folder / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
    record_install(core, folder, manifest, "core")


def _avatar_shape(av):
    """An avatar view is None or {id, name, preview, model}; preview and model are /pkg/ paths or None."""
    if av is None:
        return True
    return set(av) == {"id", "name", "preview", "model"} and isinstance(av["id"], str) and av["name"]         and all(av[k] is None or av[k].startswith(f"/pkg/{av['id']}/") for k in ("preview", "model"))


def test_settings(client, owner, core, state):
    """Robust to avatar packages that ship with PuffPAI (main ships the cloud avatar, lane L7)."""
    s = client.get("/v1/settings", headers=owner).json
    # The Bridge's own policy, the one source GET /v1/bridge reads too (lane I3b): it ships closed.
    assert s["ui"] == "desktop" and s["bridge"] == "closed" and not s["wizard_complete"]
    assert s["bridge"] == client.get("/v1/bridge", headers=owner).json["state"] and s["bridge_search"] is False
    assert s["pages"] == "http://127.0.0.1:8422"                         # package pages live there (D49)
    assert _avatar_shape(s["avatar"])
    built_in = s["avatar"]["id"] if s["avatar"] else None                # whatever ships, picked by kind
    assert client.put("/v1/settings/ui", json={"id": "nope"}, headers=owner).status_code == 400
    assert client.put("/v1/settings/ui", json={"id": "desktop"}, headers=owner).json["ui"] == "desktop"
    _install_avatar(state, core)
    assert client.put("/v1/settings/avatar", json={"id": "cloud-avatar"}, headers=owner).status_code == 200
    s = client.get("/v1/settings", headers=owner).json
    assert _avatar_shape(s["avatar"]) and s["avatar"]["id"] == "cloud-avatar"   # the active choice
    assert s["avatar"]["preview"] == "/pkg/cloud-avatar/cloud.png"       # dropped in, no Core edit
    none = client.put("/v1/settings/avatar", json={"id": None}, headers=owner)
    assert none.status_code == 200 and _avatar_shape(none.json["avatar"])
    if built_in is None:                                                 # with none chosen, the first installed shows
        assert none.json["avatar"]["id"] == "cloud-avatar"
    assert client.put("/v1/settings/avatar", json={"id": "ghost"}, headers=owner).status_code == 400
    assert client.put("/v1/settings/avatar", json={"id": "cloud-avatar"}, headers=owner).json["avatar"]["name"] == "Cloud"


def test_theme(client, core, state):
    ft = bearer(core.tokens.issue_package_token("freshtable"))
    t = client.get("/v1/theme", headers=ft).json
    assert t["colors"]["paper"] == "#f5efe3" and "--pp-coral" in t["css"]
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "theme.json").write_text('{"colors": {"coral": "#ff0000"}}', encoding="utf-8")
    assert client.get("/v1/theme", headers=ft).json["colors"]["coral"] == "#ff0000"


def test_people(client, owner, core):
    assert client.get("/v1/people", headers=owner).json == []
    people = [{"id": "sam", "name": "Sam", "emoji": "\U0001F338", "role": "adult"},
              {"id": "kid", "name": "Sam", "role": "child"}]
    stored = client.put("/v1/people", json=people, headers=owner).json
    assert stored[1] == {"id": "kid", "name": "Sam", "emoji": "", "role": "child"}
    assert client.get("/v1/people", headers=owner).json == stored
    assert client.put("/v1/people", json=[people[0], people[0]], headers=owner).status_code == 400
    assert client.put("/v1/people", json=[{"id": "x", "name": "X", "role": "boss"}], headers=owner).status_code == 400
    # A package token reads the shared list (02, D48); it never changes it.
    ft = bearer(core.tokens.issue_package_token("freshtable"))
    assert client.get("/v1/people", headers=ft).json == stored
    assert client.put("/v1/people", json=[], headers=ft).status_code == 403
    assert client.get("/v1/people", headers=owner).json == stored


# -- ui, recovery, pairing -----------------------------------------------------
def test_root_serves_the_desktop(client):
    r = client.get("/")
    assert r.status_code == 200 and b"PuffPAI Desktop" in r.data
    assert b"puff puff pass" in r.data and b"Nothing leaves your computer without your consent." in r.data
    assert client.get("/ui/logic.js").status_code == 200
    assert client.get("/ui/../puffpai.json").status_code == 404
    assert client.get("/ui/nothing.js").status_code == 404


def test_a_dropped_in_ui_package_is_served(client, owner, state, core):
    folder = state / "packages" / "kiosk-ui"
    (folder / "web").mkdir(parents=True)
    manifest = {"schema": "puffpai-package/1", "id": "kiosk-ui", "kind": "ui", "name": "Kiosk", "version": "0.1.0",
                "description": "Big buttons.", "author": "you", "license": "MIT", "consent": "Files only.",
                "run": {"default": {"type": "static", "dir": "web"}}}
    (folder / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
    (folder / "web" / "index.html").write_text("<title>Kiosk</title>", encoding="utf-8")
    record_install(core, folder, manifest, "local:" + str(folder))
    assert client.put("/v1/settings/ui", json={"id": "kiosk-ui"}, headers=owner).status_code == 200
    assert b"Kiosk" in client.get("/").data
    assert client.get("/v1/recover").status_code == 200 and b"kiosk-ui" in client.get("/v1/recover").data


def test_pairing(client, state, core):
    assert client.get("/v1/registry").status_code == 401
    assert client.post("/v1/pair", json={"code": "NOPE-NOPE-NOPE"}).status_code == 401
    code = pairing.new_code(state)
    r = client.post("/v1/pair", json={"code": code.lower()})
    assert r.status_code == 200
    cookie = r.headers["Set-Cookie"]
    assert "puffpai_device=ppd_" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert client.get("/v1/registry").status_code == 401             # D79: the cookie alone is no login
    client.device_key = r.json["device_key"]                         # what desktop.js keeps in localStorage
    assert client.get("/v1/registry").status_code == 200             # both halves sign the Desktop in
    assert client.post("/v1/pair", json={"code": code}, headers=DESKTOP).status_code == 401   # once only
    assert code.encode() not in (state / "pairing.json").read_bytes()


def test_pairing_code_expires(state):
    code = pairing.new_code(state, now=1000.0)
    assert not pairing.redeem(state, code, now=1000.0 + pairing.LIFETIME_S + 1)


def test_cli_pair_prints_a_link(capsys, state):
    from puffpai_core.cli import main
    assert main(["pair"]) == 0
    out = capsys.readouterr().out
    assert "http://puffpai.localhost:8420/#pair=" in out          # D79: the Desktop's own name


# -- wizard over HTTP ------------------------------------------------------------
def test_wizard_routes(client, owner, app, state):
    from wizard_kit import Kit
    app.extensions["puffpai.wizard"] = Kit(state).engine()
    w = client.get("/v1/wizard", headers=owner).json
    assert [s["id"] for s in w["steps"]][:3] == ["scan", "brain", "vault"] and w["current"] == "scan"
    assert client.post("/v1/wizard/nope", json={}, headers=owner).status_code == 404
    assert client.post("/v1/wizard/scan", json={"answers": []}, headers=owner).status_code == 400
    done = client.post("/v1/wizard/scan", json={}, headers=owner).json
    assert done["status"] == "done" and done["next"] == "brain"
    assert client.get("/v1/wizard", headers=owner).json["steps"][0]["state"] == "done"


def test_wizard_with_the_merged_lanes_nothing_is_not_built(client, owner, app):
    """No doubles: the Core's own vault (L3), models (L2), Bridge (L4) and, since lane I2a, Toolsmith (L6)."""
    from puffpai_core.api.app import wire_core
    wire_core(app)                                   # start_core's joins (a bare create_app has none)
    r = client.post("/v1/wizard/vault", json={}, headers=owner).json
    assert r["status"] == "ask" and r.get("lane") is None
    assert client.post("/v1/wizard/brain", json={}, headers=owner).json["status"] in ("not_built", "ask")
    plan = client.post("/v1/wizard/plan", json={}, headers=owner).json
    assert plan["status"] == "waiting" and plan.get("lane") is None      # no goal yet; Toolsmith is wired
