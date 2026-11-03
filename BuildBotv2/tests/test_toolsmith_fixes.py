"""Regression tests for the L6 verifier's findings (the cap, shadowed providers, the build loop on
unexpected errors, the adopted run line (D51), the final-body screen, hygiene and merge prep)."""

import copy
import fnmatch
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_toolsmith_build import make_world
from toolsmith_fakes import FakePorts
from puffpai_core.packages import manifest as manifest_mod
from puffpai_core.zone3.toolsmith import adopt, agent
from puffpai_core.zone3.toolsmith.errors import CloudError, CloudHeld, NotBuilt
from puffpai_core.zone3.toolsmith_host import sender, wire
from toolsmith_fakes import end_held

REPO = Path(__file__).resolve().parents[1]
CONV = [{"role": "user", "content": [{"type": "text", "text": "Install the app"}]}]


def reply(input_tokens=1000, output_tokens=16000):
    return json.dumps({"output": [{"type": "message", "content": [{"type": "output_text", "text": "hi"}]}],
                       "status": "completed", "usage": {"input_tokens": input_tokens,
                                                        "output_tokens": output_tokens}}).encode()


def openai_sender(tmp_path, spent=0.0, cap=5.0):
    ports = FakePorts(tmp_path)
    ports.keys["toolsmith.openai"] = "sk-" + "a" * 40
    ports.http_send_fn = lambda method, url, headers, body: (200, {}, reply())
    p = sender.load_providers()["openai"]
    led = sender.Ledger(tmp_path / "u.json")
    if spent:
        led.add("openai", "gpt-6-astra", {"in": 1, "out": 0}, spent, "earlier")
    s = sender.FrontierSender(ports, p, model="gpt-6-astra", build_id="b", cap_usd=cap, ledger=led,
                              consent_given=sender.consent_sha(p), sleep=lambda s: None)
    return s, ports, led


# -- 1. the cap counts the full maximum reply length (D43, D52) -------------------------

def test_a_call_that_could_cross_the_cap_is_never_sent(tmp_path):
    s, ports, led = openai_sender(tmp_path, spent=4.80)
    with pytest.raises(CloudHeld) as e:
        s.turn("sys", CONV, [])                       # 16000 out at $50 per million could be $0.80
    assert e.value.code == "cap" and "$5 limit" in e.value.message
    assert ports.sent == [] and led.read()["total_usd"] == pytest.approx(4.80)


def test_the_estimate_uses_the_requests_own_maximum_and_never_more_than_the_cores(tmp_path):
    s, ports, led = openai_sender(tmp_path, spent=4.80)
    ports.http_send_fn = lambda method, url, headers, body: (200, {}, reply(output_tokens=100))
    s.turn("sys", CONV, [], max_tokens=sender.FIND_MAX_TOKENS)       # 2048 out: at most about $0.10 more
    assert len(ports.sent) == 1
    s2, ports2, _ = openai_sender(tmp_path / "b")
    s2.turn("sys", CONV, [], max_tokens=10 ** 9)
    assert json.loads(ports2.sent[0]["body"])["max_output_tokens"] == sender.MAX_TOKENS
    assert sender.worst_case_usd({"in": 10, "out": 50}, 1000, 16000) == pytest.approx((1000 * 12.5 + 16000 * 50) / 1e6)


def test_a_reservation_holds_while_a_call_is_in_flight(tmp_path):
    led = sender.Ledger(tmp_path / "u.json")
    assert led.reserve(3.0, 5.0) and not led.reserve(3.0, 5.0)
    led.release(3.0)
    assert led.reserve(3.0, 5.0)


# -- 2. built-in provider ids can't be shadowed; known companies use the Core's prices (D52) ----

def _shadow(state, pid, **tm):
    p = copy.deepcopy(sender.load_providers()["anthropic"])
    p["id"] = pid
    p["toolsmith_model"].update(tm)
    folder = state / "packages" / pid
    folder.mkdir(parents=True)
    (folder / "puffpai.json").write_text(json.dumps(p), encoding="utf-8")
    return p


def test_a_package_cannot_shadow_a_built_in_provider(tmp_path, state):
    from toolsmith_fakes import World
    w = World(tmp_path, state)
    cheap = copy.deepcopy(sender.load_providers()["anthropic"]["toolsmith_model"]["models"])
    for m in cheap:
        m["prices"] = {"in": 0.0, "out": 0.0}
    shadow = _shadow(state, "anthropic", models=cheap,
                     consent=sender.load_providers()["anthropic"]["toolsmith_model"]["consent"] + " Changed.")
    got = w.host.providers()["anthropic"]
    assert got == sender.builtin_providers()["anthropic"] and got["toolsmith_model"]["models"][0]["prices"]["in"] > 0
    with pytest.raises(ValueError):                   # her Yes to the shadow's sentence is not a Yes to the built-in
        w.host.choose({"where": "cloud", "provider": "anthropic", "consent_sha": sender.consent_sha(shadow)})
    assert sender.SHADOW_LINE in sender.provider_problems(shadow)


def test_known_companies_count_with_the_cores_prices_and_adapters_only_higher(state):
    base = sender.load_providers()["anthropic"]["toolsmith_model"]
    core = sender.core_prices()["Anthropic"]["claude-opus-5-5"]
    free = copy.deepcopy(base["models"])
    for m in free:
        m["prices"] = {"in": 0.0, "out": 0.0, "cache_read": 0.0, "cache_write": 0.0}
    p = _shadow(state, "anthropic-cheap", models=free)
    assert sender.load_providers(state / "packages")["anthropic-cheap"]
    assert sender.effective_prices(p, "claude-opus-5-5") == core
    dear = copy.deepcopy(base["models"])
    dear[0]["prices"] = {"in": 100.0, "out": 500.0}
    p2 = _shadow(state, "anthropic-dear", models=dear)
    assert sender.effective_prices(p2, dear[0]["id"])["out"] == 500.0
    odd = copy.deepcopy(base["models"][:1])
    odd[0].update(id="claude-unknown", prices={"in": 0.0, "out": 0.0})
    p3 = _shadow(state, "anthropic-odd", models=odd, default_model="claude-unknown")
    dearest_out = max(row["out"] for row in sender.core_prices()["Anthropic"].values())
    assert sender.effective_prices(p3, "claude-unknown")["out"] == dearest_out
    shown = sender.describe(p)["models"][0]["prices"]
    assert shown == core                              # Settings shows what the cap counts with


# -- 3. nothing unexpected crashes the build loop -----------------------------------------

@pytest.fixture
def built(tmp_path, state):
    procs = []
    w = make_world(tmp_path, state, procs)
    yield w
    w.host.stop(timeout=10)
    for pr in procs:
        end_held(pr)


def _run(w):
    b = w.host.plan("daily news headlines", [{"name": "Daily headlines", "does": "Shows news headlines"}])
    w.ports.consent.say_yes(b["consent"]["id"])
    return w.host.start(b["id"], b["consent"]["id"])


def test_an_odd_error_in_a_core_part_holds_the_job_and_stops_the_try(built, state):
    started = []
    real_start = built.host.trials.start

    def spy(rec, command):
        got = real_start(rec, command)
        started.extend(rec.broker.started)
        return got
    built.host.trials.start = spy
    built.ports.read_page_fn = lambda url: (_ for _ in ()).throw(TypeError("reader changed shape"))
    done = _run(built)
    job = done["jobs"][0]
    assert job["status"] == "held" and "Check again" in job["run"]["line"]
    assert started and all(not s.job.alive() for s in started)          # its server stopped (job object)
    assert not (state / "packages" / "hello-page").exists()             # its folder removed
    assert built.host.trials.of_build(done["id"]) == []


def test_a_missing_package_manager_holds_the_job_with_its_line(built, state):
    def add(folder, plan=None, adopted=None):
        raise NotBuilt("The Package Manager", "L1")
    built.ports.packages_add = add
    job = _run(built)["jobs"][0]
    assert job["status"] == "held" and "Package Manager is not built yet" in job["run"]["line"]
    assert not (state / "packages" / "hello-page").exists()


def test_an_odd_error_inside_toolsmith_holds_the_job(built, monkeypatch, state):
    monkeypatch.setattr(adopt, "manifest_for", lambda t: (_ for _ in ()).throw(KeyError("odd")))
    job = _run(built)["jobs"][0]
    assert job["status"] == "held" and job["run"]["line"] == agent.UNEXPECTED_LINE.format(what="KeyError")
    assert not (state / "packages" / "hello-page").exists()


# -- 5. the working start line is kept (D51) -------------------------------------------

PORT = 18555


@pytest.mark.parametrize("start,setup,want", [
    (f"PORT={PORT} HOST=127.0.0.1 node server.js", [],
     {"start": ["{node}", "server.js"], "env": {"PORT": "{port}", "HOST": "127.0.0.1"}, "node": True}),
    ("cd web && npm start", ["cd web && npm install"],
     {"start": ["npm", "start"], "cwd": "web", "setup": [["npm", "install"]]}),
    ("npm start", ["cp .env.example .env", "npm install"],
     {"start": ["npm", "start"], "setup": [{"copy": [".env.example", ".env"]}, ["npm", "install"]]}),
    (f".venv/Scripts/python -m app --port {PORT}", ["python -m venv .venv", ".venv/Scripts/pip install -r requirements.txt"],
     {"start": ["{python}", "-m", "app", "--port", "{port}"], "python": True,
      "setup": [{"venv": True}, ["{python}", "-m", "pip", "install", "-r", "requirements.txt"]]}),
    (f'"C:/Program Files/Python311/python.exe" -m http.server {PORT}', [],
     {"start": ["{python}", "-m", "http.server", "{port}"], "python": True}),
    (r'"C:\Program Files\nodejs\node.exe" server.js', [], {"start": ["{node}", "server.js"], "node": True}),
    ("set PORT=18555&& node s.js", [], {"start": ["{node}", "s.js"], "env": {"PORT": "{port}", "HOST": "127.0.0.1"}}),
    ('set "API_URL=http://127.0.0.1:18555/api" && npm start', [],
     {"start": ["npm", "start"], "env": {"PORT": "{port}", "HOST": "127.0.0.1", "API_URL": "http://127.0.0.1:{port}/api"}}),
    ("npm start", ["Copy-Item .env.example .env", "Copy-Item -Path conf/a.json -Destination conf/b.json -Force"],
     {"setup": [{"copy": [".env.example", ".env"]}, {"copy": ["conf/a.json", "conf/b.json"]}]}),
    ("npm start", ["copy .env.sample .env"], {"setup": [{"copy": [".env.sample", ".env"]}]}),
    ("cd web && cp .env.example .env && npm start", [],
     {"start": ["npm", "start"], "cwd": "web", "setup": [{"copy": ["web/.env.example", "web/.env"]}]}),
])
def test_the_working_start_line_is_kept(start, setup, want):
    block, notes, unsaved = adopt.run_block(start, setup, PORT)
    assert unsaved == []
    for k, v in want.items():
        assert block.get(k) == v, (k, block, notes)
    assert block["env"]["PORT"] == "{port}" and notes == []
    man = {"schema": "puffpai-package/1", "id": "app", "kind": "app", "name": "App", "version": "0.1.0",
           "description": "An app.", "author": "x", "license": "MIT", "origin": "adopted:https://github.com/x/app",
           "run": {"windows": block}, "health": {"url": "http://127.0.0.1:{port}/", "expect": 200, "every_s": 60},
           "ui": {"pages": [{"id": "main", "title": "App", "url": "http://127.0.0.1:{port}/", "show": "new_tab"}]},
           "consent": "App is an outside app."}
    assert manifest_mod.validate(man) == [], block


@pytest.mark.parametrize("start", ["npm start | tee log.txt", "cd ../elsewhere && npm start",
                                   "PUFFPAI_TOKEN=x node a.js", "$env:PORT=3; node a.js", ""])
def test_a_start_line_that_cant_be_saved_is_never_guessed(start):
    with pytest.raises(adopt.Unsaved) as e:
        adopt.run_block(start, [], PORT)
    assert e.value.reason


def test_setup_lines_that_need_a_shell_get_a_note_and_are_listed():
    block, notes, unsaved = adopt.run_block("npm start", ["npm install; npm run build", "Copy-Item -Recurse a b",
                                                          "cd other && npm install"], PORT)
    assert block["start"] == ["npm", "start"] and "setup" not in block
    assert unsaved == ["npm install; npm run build", "Copy-Item -Recurse a b", "cd other && npm install"]
    assert len(notes) == 3


@pytest.mark.parametrize("bad", [
    {"env": {"PUFFPAI_TOKEN": "x"}}, {"env": {"HTTPS_PROXY": "http://x"}}, {"env": {"A": "{secret}"}},
    {"cwd": "../outside"}, {"cwd": "C:/Windows"}, {"setup": [{"copy": ["/etc/passwd", "x"]}]},
    {"setup": [{"venv": False}]}, {"setup": [{"copy": ["a"]}]}, {"setup": [{"run": ["x"]}]},
])
def test_the_schema_refuses_run_blocks_outside_d51(bad):
    block = {"type": "command", "start": ["{python}", "app.py"], **bad}
    man = {"schema": "puffpai-package/1", "id": "app", "kind": "app", "name": "App", "version": "0.1.0",
           "description": "An app.", "author": "x", "license": "MIT", "run": {"windows": block},
           "health": {"url": "http://127.0.0.1:{port}/", "expect": 200, "every_s": 60},
           "ui": {"pages": [{"id": "main", "title": "App", "url": "http://127.0.0.1:{port}/", "show": "new_tab"}]},
           "consent": "App is an outside app."}
    assert manifest_mod.validate(man) != []


def test_manifest_for_keeps_the_try_line(tmp_path):
    folder = tmp_path / "app"
    folder.mkdir()
    t = SimpleNamespace(cand={"name": "App", "full_name": "x/app", "url": "https://github.com/x/app", "runtime": "node"},
                        result={"port": PORT, "path": "/"}, pkg_id="app", compose_project="", compose_files=[],
                        started_lines=[f"cd web && PORT={PORT} node server.js"], port=PORT, folder=folder,
                        ran=[{"command": "cp .env.example .env", "ok": True, "build": False},
                             {"command": "cd web && npm install", "ok": True, "build": True},
                             {"command": "cat README.md", "ok": True, "build": False}],
                        human_steps=[], notes=[])
    man = adopt.manifest_for(t)
    run = man["run"][adopt.run_key()]
    assert run["cwd"] == "web" and run["start"] == ["{node}", "server.js"]
    assert run["setup"] == [{"copy": [".env.example", ".env"]}, ["npm", "install"]]
    assert manifest_mod.validate(man) == []


# -- 7. the outbound screen runs on the FINAL wire body ----------------------------------

def test_the_final_wire_body_is_screened_for_personal_details(tmp_path, monkeypatch):
    s, ports, _ = openai_sender(tmp_path)
    real = wire.build_request

    def leaky(provider, req):
        out = real(provider, req)
        out["body"]["metadata"] = {"note": "owner SSN 123-45-6789"}          # the mapping adds a detail back
        return out
    monkeypatch.setattr(wire, "build_request", leaky)
    with pytest.raises(CloudError) as e:
        s.turn("sys", CONV, [])
    assert e.value.code == "privacy_check" and ports.sent == []


def test_no_outbound_screen_on_the_final_body_means_nothing_is_sent(tmp_path, monkeypatch):
    s, ports, _ = openai_sender(tmp_path)
    real = wire.build_request

    def adds(provider, req):
        out = real(provider, req)
        out["body"]["extra"] = "a string the screen never saw"
        return out
    monkeypatch.setattr(wire, "build_request", adds)
    calls = {"n": 0}
    real_screen = ports.screen_outbound

    def flaky(text):
        calls["n"] += 1
        if "never saw" in text:
            raise NotBuilt("The personal-details screen", "L3")
        return real_screen(text)
    ports.screen_outbound = flaky
    with pytest.raises(CloudError) as e:
        s.turn("sys", CONV, [])
    assert e.value.code == "privacy_check" and ports.sent == []


# -- 8 and 9. hygiene and merge prep -------------------------------------------------------

PROVENANCE = re.compile(r"^# Harvested from PuffPAIBuildBot@[0-9a-f]{8}:src/puffpai/\S+ \((as-is|adapted|rewritten-from)\)$")


@pytest.mark.parametrize("rel", ["zone3/toolsmith/adopt.py", "zone3/toolsmith/agent.py", "zone3/toolsmith_host/ports.py",
                                 "zone3/toolsmith/prove.py", "zone3/toolsmith_host/store.py",
                                 "zone3/toolsmith_host/trials.py", "zone3/toolsmith_host/sender.py"])
def test_provenance_lines(rel):
    first = (REPO / "core" / "puffpai_core" / rel).read_text(encoding="utf-8").splitlines()[0]
    assert PROVENANCE.match(first), first


def test_package_data_ships_every_toolsmith_data_file():
    import tomllib
    data = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    globs = data["tool"]["setuptools"]["package-data"]["puffpai_core"]
    root = REPO / "core" / "puffpai_core"
    files = [p.relative_to(root).as_posix() for d in ("zone3/toolsmith/data", "zone3/toolsmith_host")
             for p in (root / d).rglob("*.json")]
    assert files and all(any(fnmatch.fnmatch(f, g) for g in globs) for f in files), files
    assert "zone2/data/*.json" in globs
    markers = data["tool"]["pytest"]["ini_options"]["markers"]          # other lanes may add their own
    assert "live: uses this computer's real model server (Ollama); skipped when no small tool model is installed" in markers
    assert len({m.split(":")[0] for m in markers}) == len(markers)      # one definition per marker


def test_built_in_providers_load_without_a_file_system_path(tmp_path):
    """From a zip (as a wheel or zipapp may be): importlib.resources, never a repo-relative path."""
    z = tmp_path / "core.zip"
    root = REPO / "core"
    with zipfile.ZipFile(z, "w") as zf:
        for p in (root / "puffpai_core").rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts:
                zf.write(p, p.relative_to(root).as_posix())
    code = (f"import sys; sys.path.insert(0, {str(z)!r})\n"
            "from puffpai_core.zone3.toolsmith_host import sender\n"
            "assert '.zip' in sender.__file__\n"
            "print(sorted(sender.builtin_providers()), sender.known_hosts()['Anthropic'])")
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "['anthropic', 'google', 'openai'] ['api.anthropic.com']"


# -- found in review: a build's model choice, a lower cap, odd repo names ----------------

def test_a_build_planned_for_this_computer_never_sends_to_a_company(tmp_path, state):
    from toolsmith_fakes import World, tool
    w = World(tmp_path, state)
    b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])   # planned with "local"
    p = sender.load_providers()["anthropic"]
    w.host.choose({"where": "cloud", "provider": "anthropic", "consent_sha": sender.consent_sha(p)})
    w.ports.keys["toolsmith.anthropic"] = "sk-ant-" + "a" * 30
    w.ports.local_replies = lambda system, messages, tools: {"content": [tool("give_up", reason="x")], "stop": "tool_use"}
    link = w.link(b["id"])
    assert link.build()["settings"]["where"] == "local"
    got = link.model_turn("sys", CONV, [], local_only=False)
    assert got["built_with"] == "local" and w.ports.sent == []


def test_a_lower_cap_holds_at_once(tmp_path, state):
    from toolsmith_fakes import World
    w = World(tmp_path, state)
    p = sender.load_providers()["openai"]
    w.host.choose({"where": "cloud", "provider": "openai", "consent_sha": sender.consent_sha(p), "cost_cap_usd": 5})
    st = w.host.settings()
    b = w.host.store.create("x", [{"name": "News"}], st)
    s1 = w.host._sender(b["id"])
    assert s1.cap == 5.0 and w.host._sender(b["id"]) is s1
    w.host.choose({"where": "cloud", "provider": "openai", "consent_sha": sender.consent_sha(p), "cost_cap_usd": 1})
    assert w.host._sender(b["id"]).cap == 1.0


@pytest.mark.parametrize("full", ["../..", "../x", "x/..", "x/...", "a/b/c", "a", "https://evil.example/x"])
def test_a_plan_keeps_only_real_github_projects(full):
    from puffpai_core.zone3.toolsmith_host import store
    assert store._candidate({"full_name": full, "name": "x"}) is None
    assert store._candidate({"full_name": "owner.name/repo-1.x", "name": "x"})["url"] == "https://github.com/owner.name/repo-1.x"
