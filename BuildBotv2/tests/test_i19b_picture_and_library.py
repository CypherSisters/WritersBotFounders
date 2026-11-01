"""Lane I19b: what the owner could not see on her test4 Desktop.

A. "I can't see that I used a small picture LLM to make a picture." The picture model (the ``vision`` role) only
   READS pictures an app sends to ``POST /v1/brain/chat``; nothing on her bot sent it one, and nothing recorded or
   showed what it did. Now each answered ask with pictures is counted for its model (counts only), the answer
   names the model, ``GET /v1/models/used`` says it, and Building shows the build's picture model plainly
   ("It reads pictures ... It does not make pictures", "Nothing has sent it a picture yet").
B. "Knowledge downloading was kind of hard to find, and is it actually downloading or am I on a real webpage?"
   The fetch's progress was only a closed "Knowledge for this build" list at the bottom of each build, and the
   Knowledge page's article titles were links to Wikipedia and Gutenberg. Now ``GET /v1/toolsmith`` carries each
   build's ``library`` progress, Home and Building show one "Library: fetching N of M from ..." line that opens
   the library, with the plain line that BuildBot downloads into her library and she is not on those websites;
   a source link is labeled as the source.

Goal-agnostic: a recipe bot with food photos, a birdwatching bot and an invoices bot.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from conftest import REPO
from puffpai_core.api import create_app
from puffpai_core.zone2 import roles
from puffpai_core.zone3.toolsmith_host import knowledge as kmod
from toolsmith_fakes import World
from zone2_doubles import FakeRuntime, GateDouble, make_brain

NODE = shutil.which("node")
WEB = REPO / "packages" / "desktop" / "web"

GOALS = {
    "recipes": ("a home recipe bot that reads my food photos", ["home cooking", "food photography"]),
    "birds": ("a birdwatching bot for my garden feeder", ["garden birds", "bird song"]),
    "invoices": ("a small business invoices bot", ["invoice", "bookkeeping", "sales tax"]),
}


# -- A. the picture model: what it read, for which app, and which model answered ----------------------------

@pytest.fixture
def brain_api(state, freshtable):
    def _make(*script):
        app = create_app(state)
        core = app.extensions["puffpai"]
        brain, _items, _ = make_brain(state, freshtable, FakeRuntime(*script), gate=GateDouble(vault_open=True),
                                      settings={"model": "qwen2.5:7b-instruct"})
        brain.registry = core.registry
        app.extensions["puffpai_brain"] = brain
        device = {"Authorization": "Bearer " + core.tokens.issue_device("laptop")}
        owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
        return app.test_client(), core, device, owner
    return _make


def test_nothing_used_the_picture_model_says_so(brain_api, state):
    client, _core, device, owner = brain_api()
    roles.set_role(state, "vision", "gemma3:4b", ["gemma3:4b"], show=lambda m: {})
    assert client.get("/v1/models/used").status_code == 401
    assert client.get("/v1/models/used", headers=owner).status_code == 403          # her paired devices only
    r = client.get("/v1/models/used", headers=device)
    assert r.status_code == 200
    assert r.json["vision"] == {"model": "gemma3:4b", "own": True, "count": 0, "last_at": "", "last_by": ""}
    assert r.json["code"]["model"] == "qwen2.5:7b-instruct" and r.json["code"]["own"] is False   # chat's


@pytest.mark.parametrize("app_id", ["freshtable", "bird-feeder-cam", "invoice-reader"])
def test_pictures_an_app_sends_are_counted_for_the_model_that_read_them(brain_api, state, app_id):
    client, core, device, _owner = brain_api({"text": "A bowl of soup."}, {"text": "Two words."}, {"text": "A robin."})
    roles.set_role(state, "vision", "qwen2.5vl:7b", ["qwen2.5vl:7b"], show=lambda m: {})
    tok = {"Authorization": "Bearer " + core.tokens.issue_package_token(app_id)}
    r = client.post("/v1/brain/chat", headers=tok, json={"messages": [{"role": "user", "content": "What is this?"}],
                                                          "images": ["aGk=", "aGk="]})
    assert r.status_code == 200 and r.json["model"] == "qwen2.5vl:7b"            # an app can say who read it
    r = client.post("/v1/brain/chat", headers=tok, json={"messages": [{"role": "user", "content": "Say hi."}]})
    assert r.status_code == 200 and r.json["model"] == "qwen2.5:7b-instruct"     # words only: not counted
    got = client.get("/v1/models/used", headers=device).json["vision"]
    assert got["model"] == "qwen2.5vl:7b" and got["count"] == 2 and got["last_by"] == app_id and got["last_at"]
    kept = json.loads((state / "settings" / roles.USE_FILE).read_text(encoding="utf-8"))
    assert set(kept) == {"vision"} and set(kept["vision"]["qwen2.5vl:7b"]) == {"count", "last_at", "last_by"}
    # a new picture model starts at nothing: the count is per model
    roles.set_role(state, "vision", "gemma3:4b", ["gemma3:4b"], show=lambda m: {})
    assert client.get("/v1/models/used", headers=device).json["vision"]["count"] == 0


# -- B. the library fetch on GET /v1/toolsmith ----------------------------------------------------------------

def _kp(topics):
    return {"topics": topics, "hosts": ["en.wikipedia.org", "www.gutenberg.org"], "offered": [],
            "fetch": [{"source": "wikipedia", "name": "Wikipedia", "host": "en.wikipedia.org", "kind": "articles",
                       "per_topic": 8, "max_items": 8 * len(topics)},
                      {"source": "gutenberg", "name": "Project Gutenberg", "host": "www.gutenberg.org", "kind": "books",
                       "per_topic": 2, "max_items": 2 * len(topics)}]}


@pytest.mark.parametrize("which", sorted(GOALS))
def test_library_progress_waiting_fetching_done(which):
    goal, topics = GOALS[which]
    kp = _kp(topics)
    cap = 10 * len(topics)
    assert kmod.progress({"goal": goal}) is None                                  # a plan that reads nothing
    wait = kmod.progress({"goal": goal, "knowledge_plan": kp})
    assert wait == {"state": "waiting", "done": 0, "of": cap, "up_to": True,
                    "sources": ["Wikipedia", "Project Gutenberg"], "now": "", "line": ""}
    run = kmod.start_run(kp)
    run.update(counts={"wikipedia": 3, "gutenberg": 0}, planned={"wikipedia": 8, "gutenberg": 0}, now="Reading one")
    got = kmod.progress({"knowledge_plan": kp, "knowledge_run": run})
    assert (got["state"], got["done"], got["of"], got["up_to"], got["now"]) == ("fetching", 3, cap, True, "Reading one")
    run["queue"] = [u for u in run["queue"] if u["do"] != "search"]               # every search ran: found is known
    run["planned"] = {"wikipedia": 8 * len(topics), "gutenberg": 1}
    got = kmod.progress({"knowledge_plan": kp, "knowledge_run": run})
    assert (got["done"], got["of"], got["up_to"]) == (3, 8 * len(topics) + 1, False)
    kmod.finish(run)
    got = kmod.progress({"knowledge_plan": kp, "knowledge_run": run})
    assert got["state"] == "done" and got["now"] == "" and got["line"].startswith("Read 3 Wikipedia articles")


def test_toolsmith_status_carries_each_builds_library(tmp_path, state):
    w = World(tmp_path, state)
    st = w.host.settings()
    ids = {}
    for which, (goal, topics) in GOALS.items():
        b = w.host.store.create(goal, [{"name": goal, "does": goal, "sample_ask": "?"}], st)
        ids[which] = b["id"]
        if which != "invoices":
            w.host.store.change(b["id"], lambda x, t=topics: x.update(knowledge_plan=_kp(t)))
    run = kmod.start_run(_kp(GOALS["birds"][1]))
    run["counts"]["wikipedia"] = 5
    w.host.store.change(ids["birds"], lambda x: x.update(knowledge_run=run))
    owner = {"Authorization": "Bearer " + w.core.tokens.issue_owner_key()}
    r = w.app.test_client().get("/v1/toolsmith", headers=owner)
    assert r.status_code == 200
    by = {b["id"]: b for b in r.json["builds"]}
    assert by[ids["invoices"]]["library"] is None
    assert by[ids["recipes"]]["library"]["state"] == "waiting"
    assert by[ids["birds"]]["library"]["state"] == "fetching" and by[ids["birds"]]["library"]["done"] == 5


# -- the Desktop: the words and where they show ---------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_desktop_logic_for_picture_and_library():
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "i19b.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0, got.stderr
    assert got.stdout.strip() == "ok"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_pages_parse():
    for name in ("logic.js", "pages/home.js", "pages/building.js", "pages/knowledge.js"):
        proc = subprocess.run([NODE, "--check", str(WEB / name)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, (name, proc.stderr)


def test_the_pages_show_the_library_and_the_picture_model():
    home = (WEB / "pages" / "home.js").read_text(encoding="utf-8")
    building = (WEB / "pages" / "building.js").read_text(encoding="utf-8")
    knowledge = (WEB / "pages" / "knowledge.js").read_text(encoding="utf-8")
    assert "L.libraryNow(tool.data)" in home and "Open your library" in home
    assert "L.libraryLine(b.library" in building and "Open your library" in building
    assert "L.pictureModel(full.data" in building and '"/v1/models/used"' in building
    assert "L.libraryNow(tool.data)" in knowledge
    # a source link is labeled as the source, opens a new tab of her browser, and is never the title itself
    assert '"Source: " + it.host' in knowledge and 'target: "_blank"' in knowledge and "noopener" in knowledge
    assert 'el("a", { href: it.url, target: "_blank", rel: "noopener noreferrer", text: it.title })' not in knowledge
    for src in (home, building, knowledge):
        assert "\\u2014".encode().decode("unicode_escape") not in src and "innerHTML" not in src
