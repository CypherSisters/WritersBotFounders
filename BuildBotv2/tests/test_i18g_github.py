"""Lane I18g: link GitHub (the owner's key in the vault as ``toolsmith.github``) and add code from GitHub.

- ``/v1/github/key``: linked, not linked, or can't tell; saved when the vault is open, sealed when it is locked,
  refused before the vault is set up; a key without a token's shape refused; the key never in a reply, a log
  line, an event or a file in plain text.
- ``POST /v1/github/code``: a new app from one GitHub project (any common form), or that project for a job of an
  earlier build that is held or found nothing, always as a NEW plan with its own card (no earlier Yes reused).
- The block's logic (tests/js/i18g.test.js, node), and the block on both pages.

Fake keys only, no network, no real GitHub, no bot port.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

import pytest

from conftest import REPO
from puffpai_core.api.app import create_app

PIN = "Tq7#mz2Lp!"                                  # a made-up test PIN (tests/z1kit.py), nobody's real one
FAKE_KEY = "github_pat_" + "0Fake" * 8              # never a real key; checked never to come back
NODE = shutil.which("node")


@pytest.fixture
def api(state):
    app = create_app(state)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    events: list = []
    core.bus.subscribe("*", events.append, audience="core")
    return app.test_client(), dev, owner, core, events


def _no_key_anywhere(texts, events, caplog, state, key=FAKE_KEY):
    for t in texts:
        assert key not in t
    assert key not in repr(events)
    assert key not in caplog.text
    for p in state.rglob("*"):
        if p.is_file():
            assert key.encode() not in p.read_bytes(), p


# -- linking her GitHub key ------------------------------------------------------------------------------

def test_link_save_state_unlink_and_locked(api, state, caplog):
    caplog.set_level(logging.DEBUG)
    client, dev, owner, _core, events = api
    seen = []

    def keep(r):
        seen.append(r.get_data(as_text=True))
        return r

    got = keep(client.get("/v1/github/key", headers=owner))
    assert got.status_code == 200 and got.json["linked"] is None and got.json["vault"] == "not_set_up"
    assert "set it up first" in got.json["line"] and got.headers["Cache-Control"] == "no-store"
    r = keep(client.put("/v1/github/key", json={"value": FAKE_KEY}, headers=dev))
    assert r.status_code == 409 and "Set up the vault first" in r.json["error"]
    assert client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev).status_code == 201
    assert keep(client.put("/v1/github/key", json={"value": FAKE_KEY}, headers=owner)).status_code == 403   # a paired device only
    assert keep(client.get("/v1/github/key", headers=owner)).json["linked"] is False
    r = keep(client.put("/v1/github/key", json={"value": FAKE_KEY}, headers=dev))
    assert r.status_code == 200 and r.json["saved"] is True and r.headers["Cache-Control"] == "no-store"
    got = keep(client.get("/v1/github/key", headers=owner))
    assert got.json["linked"] is True and got.json["vault_name"] == "toolsmith.github" and "api.github.com" in got.json["line"]
    # Toolsmith's own GitHub search reads it under that name (lane I15's GITHUB_KEY_NAME)
    from puffpai_core.zone1 import archivist
    from puffpai_core.zone3.toolsmith_host.service import GITHUB_KEY_NAME
    assert archivist.for_state(state).vault.meta(GITHUB_KEY_NAME) is not None
    r = keep(client.delete("/v1/github/key", headers=dev))
    assert r.status_code == 200 and r.json["deleted"] is True
    assert keep(client.get("/v1/github/key", headers=owner)).json["linked"] is False
    assert keep(client.delete("/v1/github/key", headers=dev)).status_code == 404
    client.post("/v1/vault/lock", headers=owner)
    r = keep(client.put("/v1/github/key", json={"value": FAKE_KEY}, headers=dev))   # locked: sealed, as the wizard does
    assert r.status_code == 202 and r.json["saved"] is False
    got = keep(client.get("/v1/github/key", headers=owner))
    assert got.json["linked"] is None and got.json["vault"] == "locked" and "locked" in got.json["line"]
    assert keep(client.delete("/v1/github/key", headers=dev)).status_code == 423
    _no_key_anywhere(seen, events, caplog, state)


def test_a_key_without_a_token_shape_is_refused_and_never_echoed(api, state, caplog):
    caplog.set_level(logging.DEBUG)
    client, dev, _owner, _core, events = api
    assert client.post("/v1/vault/setup", json={"pin": PIN}, headers=dev).status_code == 201
    bad = ["short-0Fake", FAKE_KEY[:12] + " " + FAKE_KEY[12:], FAKE_KEY[:12] + "\n" + FAKE_KEY[12:],
           FAKE_KEY + "é", "A" * 256, "Bearer " + FAKE_KEY, "", None, 7, ["x"]]
    seen = []
    for value in bad:
        r = client.put("/v1/github/key", json={"value": value}, headers=dev)
        assert r.status_code == 400 and "does not look like a GitHub token" in r.json["error"], value
        seen.append(r.get_data(as_text=True))
    assert client.get("/v1/github/key", headers=dev).json["linked"] is False                    # nothing saved
    for value in bad[:4]:
        if isinstance(value, str) and len(value) > 12:
            _no_key_anywhere(seen, events, caplog, state, key=value)
    _no_key_anywhere(seen, events, caplog, state)


# -- adding code from GitHub -------------------------------------------------------------------------------

def _world(tmp_path, state):
    from test_i18e_check_again import _world as world
    return world(tmp_path, state)


def test_add_code_from_github_plans_a_new_app_from_any_common_form(tmp_path, state):
    w = _world(tmp_path, state)
    client = w.app.test_client()
    owner = {"Authorization": "Bearer " + w.core.tokens.issue_owner_key()}
    for bad in ("", "a recipe box", "https://gitlab.com/example/news-a", "https://github.com/settings/tokens",
                "x" * 501, 7, None):
        r = client.post("/v1/github/code", json={"address": bad}, headers=owner)
        assert r.status_code == 400 and "not a GitHub project address" in r.json["error"], bad
    assert w.runner.runs == []                                           # nothing planned for a bad address
    r = client.post("/v1/github/code", json={"address": "git@github.com:example/news-a.git"}, headers=owner)
    assert r.status_code == 201, r.json
    b = w.host.view(r.json["id"])
    assert b["goal"] == "Add news-a from GitHub"
    assert b["jobs_in"][0]["name"] == "news-a" and b["jobs_in"][0]["pasted"] == ["https://github.com/example/news-a"]
    assert w.runner.runs == [("plan", b["id"])] and not b.get("yes")


def test_add_code_for_a_held_job_is_a_new_plan_with_its_own_yes(tmp_path, state):
    from puffpai_core.zone3.toolsmith_host import store
    from test_i18e_check_again import _started
    w = _world(tmp_path, state)
    client = w.app.test_client()
    owner = {"Authorization": "Bearer " + w.core.tokens.issue_owner_key()}
    old, cid = _started(w, ("held", "working"))
    held, working = old["jobs"][0]["id"], old["jobs"][1]["id"]
    cards = len(w.ports.consent.cards)
    r = client.post("/v1/github/code", json={"address": "https://github.com/example/news-b/tree/main", "job_id": working},
                    headers=owner)
    assert r.status_code == 409 and "nothing to add code to" in r.json["error"]
    for job_id, code in (("20990101-000000-abcdef-0", 404), (old["id"] + "-9", 404), ("../x", 404), (7, 400), ("x" * 101, 400)):
        r = client.post("/v1/github/code", json={"address": "https://github.com/example/news-b", "job_id": job_id}, headers=owner)
        assert r.status_code == code, (job_id, r.json)
    assert w.runner.runs == []
    r = client.post("/v1/github/code", json={"address": "github.com/example/news-b", "job_id": held}, headers=owner)
    assert r.status_code == 201, r.json
    new = w.host.view(r.json["id"])
    assert new["id"] != old["id"] and r.json["for_job"] == {"build": old["id"], "job": held, "name": "Job 0"}
    assert [j["name"] for j in new["jobs_in"]] == ["Job 0"]
    assert new["jobs_in"][0]["pasted"] == ["https://github.com/example/news-b"] and new["jobs_in"][0]["does"] == "Shows the news"
    assert new["goal"] == "Job 0, from github.com/example/news-b"
    # her Yes on the old plan is not reused: the new plan has none, and nothing was granted for it
    assert not new.get("yes") and (new.get("consent") or {}).get("id") != cid
    assert len(w.ports.consent.cards) == cards
    assert w.runner.runs == [("plan", new["id"])]
    # the old build is left as it was
    again = w.host.view(old["id"])
    assert again["yes"]["consent_id"] == cid and store.job_status(again, again["jobs"][0]) == "held"


# -- the block, on both pages -----------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_github_block_logic():
    web = REPO / "packages" / "desktop" / "web"
    for f in (web / "github.js", web / "pages" / "developer.js", web / "pages" / "apps.js"):
        got = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert got.returncode == 0, got.stderr
    src = (web / "github.js").read_text(encoding="utf-8")
    for never in ("innerHTML", "document.write", "eval(", "localStorage.", "sessionStorage.", "console.", "document.cookie",
                  "location.hash =", "history."):
        assert never not in src, never
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "i18g.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr


def test_the_block_is_on_developer_and_apps_add_in_plain_words():
    web = REPO / "packages" / "desktop" / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    assert html.index("/ui/github.js") < html.index("/ui/pages/apps.js") and html.index("/ui/github.js") < html.index("/ui/pages/developer.js")
    apps = (web / "pages" / "apps.js").read_text(encoding="utf-8")
    dev = (web / "pages" / "developer.js").read_text(encoding="utf-8")
    assert 'PuffGitHub.block({ where: "apps" })' in apps and "P.planCard = planCard" in apps
    assert dev.count('PuffGitHub.block({ where: "developer" })') == 2 and 'goTo("#developer/github"' in dev
    src = (web / "github.js").read_text(encoding="utf-8") + (REPO / "core" / "puffpai_core" / "api" / "groups" / "github.py").read_text(encoding="utf-8")
    low = src.lower()
    assert "doors" not in low and "beta" not in low and chr(0x2014) not in src
    assert "Link your GitHub" in src and "Add code from GitHub" in src and "Use it for: " in src
