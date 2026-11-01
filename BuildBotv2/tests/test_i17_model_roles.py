"""Lane I17: local models chosen by the job they do (chat, code, vision, fast).

The catalog rows and their roles; the fit per role; the role routes (access, checks, chat always has a model);
the download Yes card and its order (no download before the Yes, the role set after it, the build after
that); chat and code asks each on their own model; and the plan's model offer for unrelated goals with the
brain faked. No real Ollama and no real download: the model server is a recorded fake.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from toolsmith_fakes import FakePorts, World, _Done
from zone2_doubles import FakeRuntime, make_brain
from puffpai_core import jsonfile
from puffpai_core.zone2 import catalog, models, roles
from puffpai_core.zone2 import settings as model_settings
from puffpai_core.zone3.toolsmith_host import store_phase as sp

NEW = {"qwen3-coder:30b", "qwen2.5-coder:14b", "qwen2.5-coder:7b", "qwen2.5vl:7b", "gemma3:4b"}


def census(ram, free=200.0):
    return {"hardware_class": "desktop", "memory": {"total_gb": ram}, "disk": {"free_gb": free}}


class FakeOllama:
    """The model server's calls the Core makes for roles: the list, a download (recorded, never real), states."""

    def __init__(self, names=("qwen3.5:9b",)):
        self.names = list(names)
        self.pulled: list[str] = []
        self.order: list[str] | None = None
        self.release = threading.Event()
        self.release.set()
        self.ok = True
        self.chats: list[dict] = []

    def models(self):
        return [{"name": n, "size_bytes": 5_000_000_000, "context": None, "params": "7B"} for n in self.names]

    cancelled = False

    def cancel(self, model):
        """The stream is closed: the download in progress ends, not done."""
        self.cancelled = True
        self.release.set()
        return True

    def pull(self, model, on_progress=None):
        self.release.wait(30)
        if self.cancelled:
            return False
        self.pulled.append(model)
        if self.order is not None:
            self.order.append("pull")
        if self.ok:
            self.names.append(model)
        return self.ok

    def state(self, model):
        return {"model": model, "state": "ready" if model in self.names else "not_downloaded", "reason": ""}

    remote: set = set()

    def show(self, model):
        """Ollama's /api/show: a hosted model carries remote_host and remote_model (mirrored here)."""
        if model not in self.names:
            raise OSError("model not found")
        return {"remote_host": "https://ollama.com:443", "remote_model": model} if model in self.remote else             {"details": {"format": "gguf"}}

    def chat(self, messages, *, model, tools=None, think=False, options=None, on_token=None):
        self.chats.append({"model": model, "think": think, "options": options})
        return {"text": "{}", "tool_calls": [], "done_reason": "stop", "usage": {}}


@pytest.fixture
def ollama(monkeypatch, state):
    fake = FakeOllama()
    monkeypatch.setattr(models, "runtime", lambda state_dir=None: fake)
    model_settings.save(state, address="http://127.0.0.1:9", model="qwen3.5:9b")
    jsonfile.write(state / "settings" / "hardware.json", census(16.0))
    return fake


# -- 1. the catalog rows -------------------------------------------------------------------------------------

def test_every_catalog_row_is_valid_and_has_its_roles():
    cat = catalog.load_catalog()
    ids = [m["id"] for m in cat["models"]]
    assert len(ids) == len(set(ids)) and NEW <= set(ids)
    for m in cat["models"]:
        assert m["roles"] and set(m["roles"]) <= set(roles.ROLES), m["id"]
        for key in ("params_b", "min_ram_gb", "rec_ram_gb", "disk_gb"):
            assert isinstance(m[key], (int, float)) and m[key] > 0, (m["id"], key)
        assert m["min_ram_gb"] <= m["rec_ram_gb"], m["id"]
        assert m["strength"].strip() and m["label"].strip(), m["id"]
        assert ":" in m["id"] and " " not in m["id"], m["id"]          # an Ollama tag, local only
        if m["id"] in NEW:
            assert m["benchmarks"] == {}, m["id"]                        # never a number not measured
    for role in roles.ROLES:
        assert any(role in m["roles"] for m in cat["models"]), role
    assert {m["id"] for m in cat["models"] if "code" in m["roles"]} == {"qwen3-coder:30b", "qwen2.5-coder:14b",
                                                                         "qwen2.5-coder:7b"}
    assert {m["id"] for m in cat["models"] if "vision" in m["roles"]} == {"qwen2.5vl:7b", "gemma3:4b"}
    profiles = catalog.load_profiles()["profiles"]
    assert all(m in profiles for m in NEW)
    assert profiles["qwen2.5vl:7b"]["vision"] and profiles["gemma3:4b"]["vision"]
    assert not any(profiles[m]["needs_thinking"] for m in NEW)            # think stays off on answer calls


def test_the_brain_step_still_lists_only_chat_models():
    menu = catalog.fit_menu(census(64.0, 900.0), [])
    ids = {o["id"] for o in menu["options"]}
    assert ids and not ids & NEW
    assert menu["recommended"]["id"] == "qwen3.5:9b"


# -- 2. fit per role ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("ram,code,vision", [
    (64.0, "qwen3-coder:30b", "qwen2.5vl:7b"),
    (32.0, "qwen3-coder:30b", "qwen2.5vl:7b"),
    (16.0, "qwen2.5-coder:7b", "qwen2.5vl:7b"),
    (12.0, "qwen2.5-coder:7b", "gemma3:4b"),           # no comfortable code model: the smallest tight one
    (8.0, "qwen2.5-coder:7b", "gemma3:4b"),
    (6.0, None, "gemma3:4b"),
    (4.0, None, None),
])
def test_the_best_fit_per_role(ram, code, vision):
    rows = roles.rows(census(ram), [])
    got_code, got_vision = roles.best("code", rows), roles.best("vision", rows)
    assert (got_code or {}).get("id") == code, [r["id"] for r in roles.for_role("code", rows)]
    assert (got_vision or {}).get("id") == vision


def test_installed_wins_and_a_full_disk_does_not_fit():
    rows = roles.rows(census(64.0), ["qwen2.5-coder:7b"])
    assert roles.best("code", rows)["id"] == "qwen2.5-coder:7b"            # already here: no download
    full = roles.rows(census(64.0, free=10.0), [])
    assert roles.best("code", full)["id"] == "qwen2.5-coder:7b"            # 19 GB and 9 GB do not fit 10 GB free
    assert not next(r for r in full if r["id"] == "qwen3-coder:30b")["fits"]
    row = next(r for r in rows if r["id"] == "qwen3-coder:30b")
    assert row["download"] == "19 GB download" and row["roles"] == ["code"]


def test_the_offer_words_and_none_fits(ollama, state):
    got = roles.offer(state, "code")
    assert got["line"] == "Add a coding model: Qwen2.5 Coder 7B, 5 GB download, fits your computer. Tick to add it."
    assert got["fits"] and got["model"] == "qwen2.5-coder:7b"
    jsonfile.write(state / "settings" / "hardware.json", census(4.0))
    none = roles.offer(state, "vision")
    assert none["fits"] is False and none["line"] == "A picture model would help this bot, but none we list fits this computer."
    roles.set_role(state, "code", "qwen3.5:9b", ollama.names)
    assert roles.offer(state, "code") is None                               # it has its own model already


# -- 3. the role routes ---------------------------------------------------------------------------------------

@pytest.fixture
def api(core, ollama):
    client = core.app.test_client()
    device = {"Authorization": "Bearer " + core.tokens.issue_device("laptop")}
    owner = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    return client, device, owner


def test_the_models_routes_are_for_her_paired_devices(api):
    client, device, owner = api
    assert client.get("/v1/models").status_code == 401
    assert client.get("/v1/models", headers=owner).status_code == 403
    assert client.put("/v1/models/roles", json={"code": "qwen3.5:9b"}, headers=owner).status_code == 403
    assert client.post("/v1/models/pull", json={"model": "qwen2.5-coder:7b"}, headers=owner).status_code == 403
    r = client.get("/v1/models", headers=device)
    assert r.status_code == 200, r.json
    d = r.json
    assert d["roles"] == {"chat": "qwen3.5:9b", "code": "", "vision": "", "fast": ""}
    assert d["installed"][0]["name"] == "qwen3.5:9b" and d["installed"][0]["used_for"] == ["chat"]
    row = next(x for x in d["catalog"] if x["id"] == "qwen2.5-coder:7b")
    assert row["roles"] == ["code"] and row["fits"] and row["download"] == "5 GB download"
    assert not next(x for x in d["catalog"] if x["id"] == "qwen3-coder:30b")["fits"]     # 16 GB here
    assert d["recommended"]["code"][0] == "qwen2.5-coder:7b" and d["recommended"]["chat"][0] == "qwen3.5:9b"
    assert d["pull_host"] == "registry.ollama.ai" and "around the Bridge" in d["bridge"]


def test_roles_take_only_installed_models_and_chat_always_has_one(api, state, ollama):
    client, device, _ = api
    bad = [({"code": "qwen2.5-coder:7b"}, "not on this computer"), ({"chat": ""}, "Chat always has a model"),
           ({"drawing": "qwen3.5:9b"}, "A role is"), ({"code": 3}, "A role is"), ({}, "Send")]
    for body, said in bad:
        r = client.put("/v1/models/roles", json=body, headers=device)
        assert r.status_code == 400 and said in r.json["error"], (body, r.json)
    assert roles.role_map(state)["chat"] == "qwen3.5:9b"
    ollama.names.append("qwen2.5-coder:7b")
    r = client.put("/v1/models/roles", json={"code": "qwen2.5-coder:7b", "fast": "qwen3.5:9b"}, headers=device)
    assert r.status_code == 200 and r.json["roles"]["code"] == "qwen2.5-coder:7b"
    assert json.loads((state / "settings" / "models.json").read_text(encoding="utf-8"))["roles"] == \
        {"code": "qwen2.5-coder:7b", "fast": "qwen3.5:9b"}
    # one change that fails keeps none of them
    r = client.put("/v1/models/roles", json={"fast": "", "vision": "gemma3:4b"}, headers=device)
    assert r.status_code == 400 and roles.role_map(state)["fast"] == "qwen3.5:9b"
    # chat is the brain in settings\model.json, so the old route reads the same model
    ollama.names.append("qwen2.5:7b-instruct")
    assert client.put("/v1/models/roles", json={"chat": "qwen2.5:7b-instruct"}, headers=device).status_code == 200
    owner = {"Authorization": "Bearer " + api[0].application.extensions["puffpai"].tokens.issue_owner_key()}
    assert client.get("/v1/model", headers=owner).json["settings"]["model"] == "qwen2.5:7b-instruct"
    assert client.put("/v1/models/roles", json={"code": ""}, headers=device).json["roles"]["code"] == ""
    assert roles.model_for(state, "code") == "qwen2.5:7b-instruct"          # no code model: chat's


def test_a_download_waits_for_its_own_yes_card(api, state, ollama, core):
    from puffpai_core.consent import card_view
    client, device, _ = api
    for body, code in (({"model": "nope:1b"}, 400), ({"model": "qwen3.5:9b"}, 409), ({"model": "qwen3-coder:30b"}, 400),
                       ({}, 400)):
        assert client.post("/v1/models/pull", json=body, headers=device).status_code == code, body
    ollama.release.clear()
    r = client.post("/v1/models/pull", json={"model": "qwen2.5-coder:7b"}, headers=device)
    assert r.status_code == 202, r.json
    cid = r.json["consent_id"]
    assert r.json["title"] == "Download Qwen2.5 Coder 7B?"
    text = " ".join(r.json["lines"])
    assert "about 5 GB, from registry.ollama.ai and Ollama's download sites." in text and "around the Bridge" in text
    assert "a local model for writing code." in text
    assert "Nothing leaves your computer without your consent." in text
    store = core.consent
    rec = store.get(cid)
    assert rec["scope"] == {"kind": "model_pull", "target": "model:qwen2.5-coder:7b"} and not rec["floor"]
    assert card_view(rec["card"], False)["lines"] == r.json["lines"]
    again = client.post("/v1/models/pull", json={"model": "qwen2.5-coder:7b"}, headers=device)
    assert again.status_code == 202 and again.json["consent_id"] == cid and again.json["again"] is True   # its card
    time.sleep(0.3)
    assert ollama.pulled == []                                             # nothing before her Yes
    assert roles.pulls(state)["qwen2.5-coder:7b"]["state"] == "asking"
    store.answer(cid, "yes", "tap", by="owner", card_sha=rec["card_sha"])
    ollama.release.set()
    end = time.monotonic() + 10
    while time.monotonic() < end and roles.pulls(state)["qwen2.5-coder:7b"].get("state") != "done":
        time.sleep(0.05)
    assert ollama.pulled == ["qwen2.5-coder:7b"]
    assert roles.pulls(state)["qwen2.5-coder:7b"]["state"] == "done"
    assert roles.role_map(state)["code"] == ""                             # added, not set: Use for sets it
    assert any(n["title"] == "Qwen2.5 Coder 7B is downloaded" for n in core.notices._load())
    # a No downloads nothing
    r = client.post("/v1/models/pull", json={"model": "gemma3:4b"}, headers=device)
    store.answer(r.json["consent_id"], "no", by="owner")
    end = time.monotonic() + 10
    while time.monotonic() < end and roles.pulls(state)["gemma3:4b"].get("state") == "asking":
        time.sleep(0.05)
    assert roles.pulls(state)["gemma3:4b"]["state"] == "declined" and ollama.pulled == ["qwen2.5-coder:7b"]


# -- 4. the role each ask uses ---------------------------------------------------------------------------------

def test_chat_uses_chat_and_toolsmith_turns_use_code(state, freshtable):
    rt = FakeRuntime({"text": "hello"}, {"text": "made it"}, {"text": "chat again"}, {"text": "toolsmith on chat"})
    brain, _items, _reg = make_brain(state, freshtable, rt, settings={"model": "qwen3.5:9b"})
    roles.set_role(state, "code", "qwen2.5-coder:7b", ["qwen2.5-coder:7b"], show=lambda m: {})
    brain.turn("hi")
    assert rt.calls[-1]["model"] == "qwen3.5:9b" and rt.calls[-1]["think"] is True        # its own think_in_chat
    brain.toolsmith_turn("Make the app.", [{"role": "user", "content": "go"}], [])
    assert rt.calls[-1]["model"] == "qwen2.5-coder:7b" and rt.calls[-1]["think"] is False
    assert rt.calls[-1]["options"]["num_ctx"] == 8192                       # the code model's own profile
    roles.set_role(state, "code", "", [])
    brain.turn("hi again")
    brain.toolsmith_turn("Make the app.", [{"role": "user", "content": "go"}], [])
    assert rt.calls[-1]["model"] == "qwen3.5:9b"                            # no code model: chat's


def test_pictures_go_to_the_picture_model(state, freshtable):
    rt = FakeRuntime({"text": "a cat"}, {"text": "words"})
    brain, _items, _reg = make_brain(state, freshtable, rt, settings={"model": "qwen2.5:7b-instruct"})
    assert brain.can_read_images() is False
    roles.set_role(state, "vision", "qwen2.5vl:7b", ["qwen2.5vl:7b"], show=lambda m: {})
    assert brain.can_read_images() is True
    brain.package_chat([{"role": "user", "content": "What is this?"}], ["aGVsbG8="])
    assert rt.calls[-1]["model"] == "qwen2.5vl:7b"
    brain.package_chat([{"role": "user", "content": "Say hi."}])
    assert rt.calls[-1]["model"] == "qwen2.5:7b-instruct"


# -- 5. the plan offers a model ----------------------------------------------------------------------------------

def fake_needs(system, user):
    """The faked brain's answer to the one small ask, from the goal's words (domain words live only here)."""
    assert system == roles.NEEDS_SYSTEM
    goal = json.loads(user)["goal"].lower()
    return {"code": "python" in goal or "code" in goal, "vision": "photo" in goal or "picture" in goal}


class ModelPorts(FakePorts):
    """The model ports, with the real ``zone2.roles`` rules over a fake model server; the download card is a
    recorded fake, answered by the test."""

    def __init__(self, tmp, state, ollama):
        super().__init__(tmp)
        self.state_dir, self.ollama = state, ollama
        self.asks: list[str] = []
        self.cards: dict[str, str] = {}
        self.card_models: dict[str, str] = {}
        self.order: list[str] = []
        ollama.order = self.order
        self.expired_cards = []

    def model_needs(self, goal, jobs):
        self.asks.append(goal)
        return roles.needs(fake_needs, goal, jobs)

    def model_offer(self, role):
        return roles.offer(self.state_dir, role)

    def model_installed(self, model):
        return catalog.is_installed(model, self.ollama.names)

    def model_card(self, model):
        cid = f"mc_{len(self.cards) + 1}"
        self.cards[cid], self.card_models[cid] = "pending", model
        self.order.append("card")
        return {"consent_id": cid, "card_sha": "s"}

    def model_pull(self, model):
        return roles.pull_now(self.state_dir, model)

    def model_set_role(self, role, model):
        self.order.append("role")
        roles.set_role(self.state_dir, role, model, self.ollama.names)

    def consent_state(self, cid):
        if cid in self.cards:
            return {"status": self.cards[cid], "card": {"model": self.card_models[cid]}}
        return {"status": "granted" if self.consent.get(cid) else "pending"}

    def consent_expire(self, cid, note=""):
        self.expired_cards.append(cid)


class Launches:
    def __init__(self, order):
        self.order, self.runs = order, []

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.runs.append(do)
        self.order.append("launch")
        on_exit(0)
        return _Done(0)


@pytest.fixture
def world(tmp_path, state, ollama):
    w = World(tmp_path, state, ports=ModelPorts(tmp_path, state, ollama))
    w.host.store_phase.poll_s = 0.02
    w.launches = Launches(w.ports.order)
    w.host.runner = w.launches
    yield w
    ollama.release.set()
    w.host.stop(timeout=10)


JOB = {"name": "Main job", "does": "Does what the goal says.", "sample_ask": "Do it."}
CAND = {"name": "some-app", "full_name": "example/some-app", "runtime": "node", "source": "github"}


def _plan(w, goal):
    b = w.host.store.create(goal, [JOB], w.host.settings())
    w.host._model_needs(b["id"])
    w.host.agent_plan(b["id"], {"jobs": [{**JOB, "plan": {"candidates": [CAND]}}]})
    return w.host.view(b["id"])


def test_three_unrelated_goals_get_only_the_model_they_need(world):
    w = world
    coder = _plan(w, "a coder bot that helps me write Python scripts")
    assert coder["model_needs"] == ["code"]
    assert [m["value"] for m in coder["card"]["model_offers"]] == ["model:code"]
    assert "Add a coding model: Qwen2.5 Coder 7B, 5 GB download, fits your computer. Tick to add it." in coder["card"]["lines"]
    assert coder["envelope"]["models"] == [{"role": "code", "model": "qwen2.5-coder:7b"}]
    photos = _plan(w, "a bot that sorts my photos by what is in each picture")
    assert photos["model_needs"] == ["vision"] and photos["card"]["model_offers"][0]["model"] == "qwen2.5vl:7b"
    recipes = _plan(w, "a recipe bot")
    assert recipes["model_needs"] == [] and "model_offers" not in recipes["card"]
    assert not any("model" in ln for ln in recipes["card"]["lines"])
    assert w.ports.asks == ["a coder bot that helps me write Python scripts",
                            "a bot that sorts my photos by what is in each picture", "a recipe bot"]


def test_no_ask_when_the_roles_are_set_and_a_plain_line_when_nothing_fits(world, state, ollama):
    w = world
    jsonfile.write(state / "settings" / "hardware.json", census(4.0))
    small = _plan(w, "a coder bot for my code")
    assert "model_offers" not in small["card"]
    assert "A coding model would help this bot, but none we list fits this computer." in small["card"]["lines"]
    roles.set_role(state, "code", "qwen3.5:9b", ollama.names)
    roles.set_role(state, "vision", "qwen3.5:9b", ollama.names)
    asked = len(w.ports.asks)
    b = _plan(w, "a coder bot for my code")
    assert len(w.ports.asks) == asked and b["model_needs"] == [] and "model_offers" not in b["card"]


def test_a_ticked_model_has_its_own_yes_then_downloads_then_its_role_then_the_build(world, state, ollama):
    w = world
    b = _plan(w, "a coder bot that helps me write Python scripts")
    cid = b["consent"]["id"]
    with pytest.raises(ValueError):
        w.host.use_offers(b["id"], cid, ["model:vision"])                  # only what the card offers
    w.host.use_offers(b["id"], cid, ["model:code"])
    ollama.release.clear()
    w.ports.consent.say_yes(cid)                                           # the plan Yes: the Core begins
    rec = lambda: (w.host.view(b["id"]).get("store_adds") or {})           # noqa: E731
    app = lambda: (rec().get("apps") or [{}])[0]                           # noqa: E731
    end = time.monotonic() + 10
    while time.monotonic() < end and app().get("state") != "asking":
        time.sleep(0.02)
    assert app()["kind"] == "model" and app()["line"] == sp.MODEL_ASKING.format(name="Qwen2.5 Coder 7B")
    assert w.host.start(b["id"], cid).get("yes") is None and w.launches.runs == []
    time.sleep(0.2)
    assert ollama.pulled == [] and w.ports.order == ["card"]               # nothing before its own Yes
    w.ports.cards[app()["card"]] = "granted"
    ollama.release.set()
    end = time.monotonic() + 10
    while time.monotonic() < end and rec().get("state") != "done":
        time.sleep(0.02)
    assert w.ports.order == ["card", "pull", "role", "launch"]
    assert roles.role_map(state)["code"] == "qwen2.5-coder:7b"
    assert rec()["said"] == [sp.MODEL_ASKING.format(name="Qwen2.5 Coder 7B"),
                             sp.MODEL_PULLING.format(name="Qwen2.5 Coder 7B", size=5),
                             sp.MODEL_ADDED.format(name="Qwen2.5 Coder 7B", words="coding"), sp.STARTED]
    assert w.host.view(b["id"])["yes"]["consent_id"] == cid


def test_a_no_on_the_model_card_downloads_nothing_and_the_build_still_starts(world, state, ollama):
    w = world
    b = _plan(w, "a coder bot that helps me write Python scripts")
    cid = b["consent"]["id"]
    w.host.use_offers(b["id"], cid, ["model:code"])
    w.ports.consent.say_yes(cid)
    end = time.monotonic() + 10
    while time.monotonic() < end and not w.ports.cards:
        time.sleep(0.02)
    w.ports.cards[next(iter(w.ports.cards))] = "declined"
    while time.monotonic() < end and (w.host.view(b["id"]).get("store_adds") or {}).get("state") != "done":
        time.sleep(0.02)
    said = w.host.view(b["id"])["store_adds"]["said"]
    assert said == [sp.MODEL_ASKING.format(name="Qwen2.5 Coder 7B"), sp.MODEL_SAID_NO.format(name="Qwen2.5 Coder 7B"),
                    sp.STARTED]
    assert ollama.pulled == [] and roles.role_map(state)["code"] == "" and w.launches.runs == ["build"]


def test_an_unticked_model_offer_adds_nothing(world, ollama):
    w = world
    b = _plan(w, "a coder bot that helps me write Python scripts")
    w.ports.consent.say_yes(b["consent"]["id"])
    assert w.host.start(b["id"], b["consent"]["id"])["yes"]                # nothing to add: it starts at once
    assert w.launches.runs == ["build"] and w.ports.cards == {} and ollama.pulled == []


def test_the_wizard_yes_step_offers_the_model_tick(world):
    from puffpai_core import wiring
    w = world
    b = _plan(w, "a coder bot that helps me write Python scripts")
    ts = wiring.WizardToolsmith(w.app)
    ts.host = lambda: w.host
    got = ts.offers(b["id"])
    assert got == [{"job": "model:code", "job_name": "", "model": True, "name": "Qwen2.5 Coder 7B",
                    "line": "Add a coding model: Qwen2.5 Coder 7B, 5 GB download, fits your computer. Tick to add it.",
                    "package": "", "installed": False}]


# -- 6. setup stays simple -------------------------------------------------------------------------------------

def test_the_done_step_says_where_other_models_are_added():
    from puffpai_core.wizard import lines as L
    assert L.MODELS_LATER == "You can add a coding or picture model later on the Developer page."
    import inspect
    from puffpai_core.wizard import engine
    assert "L.MODELS_LATER" in inspect.getsource(engine.DoneStep)


def test_the_wizard_yes_step_lists_and_sends_the_model_tick():
    import types
    from puffpai_core.wizard import engine, lines as L
    offer = {"job": "model:code", "job_name": "", "model": True, "name": "Qwen2.5 Coder 7B", "package": "",
             "line": "Add a coding model: Qwen2.5 Coder 7B, 5 GB download, fits your computer. Tick to add it.",
             "installed": False}
    sent = []
    ts = types.SimpleNamespace(offers=lambda build: [offer], yes=lambda build, use=None: sent.append(use) or {"ok": True})
    e = types.SimpleNamespace(services=types.SimpleNamespace(toolsmith=ts), build_id=lambda: "b1")
    step = engine.YesStep(e)
    step._build = lambda: "b1"
    opts = step.options({"id": "use"}, {})
    assert opts[1] == {"value": "model:code", "note": L.MODEL_TICK_NOTE,
                       "label": "Add a coding model: Qwen2.5 Coder 7B, 5 GB download, fits your computer."}
    got = step.finish({}, {"yes": True, "use": "model:code"}, "terminal")
    assert sent == [["model:code"]] and got["status"] == "done"
    assert "Qwen2.5 Coder 7B is downloaded first, as you ticked. Its own Yes card comes next." in got["lines"]


def test_the_core_ports_raise_the_real_download_card(core, ollama, state):
    from puffpai_core.zone3.toolsmith.errors import Refused
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    ports = Ports(state, consent=core.consent)
    with pytest.raises(Refused):
        ports.model_card("qwen3-coder:30b")                                # does not fit 16 GB
    got = ports.model_card("qwen2.5-coder:7b")
    st = ports.consent_state(got["consent_id"])
    assert st["status"] == "pending" and st["card"]["model"] == "qwen2.5-coder:7b"
    assert st["scope"] == {"kind": "model_pull", "target": "model:qwen2.5-coder:7b"}
    core.consent.answer(got["consent_id"], "yes", "tap", by="owner", card_sha=st["card_sha"])
    assert ports.consent_state(got["consent_id"])["status"] == "granted"
    assert ports.model_installed("qwen2.5-coder:7b") is False
    assert ports.model_pull("qwen2.5-coder:7b") == (True, "") and ports.model_installed("qwen2.5-coder:7b")
    ports.model_set_role("code", "qwen2.5-coder:7b")
    assert roles.role_map(state)["code"] == "qwen2.5-coder:7b"
    assert ports.model_offer("code") is None
    assert ports.model_needs("a recipe bot", []) == []                      # no brain here: never a veto


def test_a_slow_brain_never_holds_up_the_plan(world, monkeypatch):
    from puffpai_core.zone3.toolsmith_host import service
    w = world
    hold = threading.Event()
    real = w.ports.model_needs
    w.ports.model_needs = lambda goal, jobs: (hold.wait(10), real(goal, jobs))[1]
    monkeypatch.setattr(service, "MODEL_NEEDS_WAIT_S", 0.2)
    b = w.host.store.create("a coder bot for my code", [JOB], w.host.settings())
    t = time.monotonic()
    w.host._model_needs_begin(b["id"])                                     # what plan_build runs first
    assert time.monotonic() - t < 1.0
    w.host.agent_plan(b["id"], {"jobs": [{**JOB, "plan": {"candidates": [CAND]}}]})
    assert "model_offers" not in w.host.view(b["id"])["card"]              # it waited a short while, then went on
    hold.set()
