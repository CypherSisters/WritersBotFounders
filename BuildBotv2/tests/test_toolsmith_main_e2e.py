"""Toolsmith on main's REAL Core: L1's Package Manager, consent store and Steward, L4's Zone 3, HookGuard and
toolsmith_zone, L3's Archivist. Only what needs the outside world or a model is recorded (the GitHub
fetch, the Bridge's own Yes for search, the local model, the page reader).

Since integration lane I1 (D57) the Package Manager calls L4's real ``Drawbridge.check_package`` through
``seams.drawbridge_check`` (the Core's own, with its audit log, joined by ``wire_core``), so a Toolsmith
build installs through the real Drawbridge. The second test keeps L6's earlier seam mapping (now unused
by the Package Manager) to show it changes nothing.
"""

import json
import re
import shutil
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from toolsmith_fakes import FIXTURES, InlineRunner, Script, tool
from puffpai_core.zone3.toolsmith_host import ToolsmithHost
from puffpai_core.zone3.toolsmith_host.ports import Ports

PY = Path(sys.executable).as_posix()
SEARCH = {"items": [{"full_name": "example/hello-page", "name": "hello-page", "description": "A tiny news headlines board",
                     "stargazers_count": 120, "language": "HTML", "topics": ["news"], "default_branch": "main",
                     "license": {"spdx_id": "MIT"}}]}
README = (FIXTURES / "toolsmith" / "hello-page" / "README.md").read_text()


class MainPorts(Ports):
    """The real adapter on main's Core; recorded only: GitHub, the Bridge's search Yes, the model, the reader."""

    def fetch_repo(self, url, dest):
        shutil.copytree(FIXTURES / "toolsmith" / "hello-page", dest, dirs_exist_ok=True)
        readme = Path(dest) / "README.md"            # without the AI-aimed line (real Drawbridge blocks it, rightly)
        kept = [ln for ln in readme.read_text().splitlines() if "ignore previous instructions" not in ln.lower()]
        readme.write_text("\n".join(kept), encoding="utf-8")
        return ""

    def bridge_decision(self, subject, host, phase="build"):
        if subject == "toolsmith:search":
            return "allow"                   # her wizard step 4 Yes, recorded
        return super().bridge_decision(subject, host, phase)

    def http_get(self, url, *, subject="toolsmith:search", accept="", max_bytes=2_000_000):
        if url.startswith("https://api.github.com/search/repositories"):
            return 200, {}, json.dumps(SEARCH).encode()
        if url == "https://raw.githubusercontent.com/example/hello-page/main/README.md":
            return 200, {}, README.encode()
        return 404, {}, b""

    def _outside_file(self) -> str:
        """A harmless settings file next to the bot folder: outside the try, and not one of the denied places."""
        shared = self.state.parent / "shared-headlines.json"
        if not shared.exists():
            shared.write_text('{"source": "test"}', encoding="utf-8")
        return shared.as_posix()

    def local_turn(self, system, messages, tools):
        port = re.search(r"Your port: (\d+)", messages[0]["content"][0]["text"]).group(1)
        n = sum(1 for m in messages if m["role"] == "assistant") + 1
        steps = {1: tool("download"),
                 # no shell and only the try's own Python (D60, D61): its .venv first, then the server
                 2: tool("run", program="python", args=["-m", "venv", "--without-pip", ".venv"]),
                 # a script of the try reading a file OUTSIDE it: the real zone check asks her first (python -m
                 # json.tool is Python's own module, which a try never runs, I2a part C)
                 3: tool("write_file", path="read_settings.py",
                         text="import sys\nprint(open(sys.argv[1], encoding='utf-8').read())\n"),
                 4: tool("run", program="python", args=["read_settings.py", self._outside_file()]),
                 5: tool("start_server", program="python", args=["-m", "http.server", port, "--bind", "127.0.0.1"]),
                 6: tool("wait_for_page", url=f"http://127.0.0.1:{port}/"),
                 7: tool("done", url=f"http://127.0.0.1:{port}/")}
        got = steps.get(n, {"type": "text", "text": "ok"})
        return {"content": [got], "stop": "tool_use" if got.get("type") == "tool_use" else "end_turn"}

    def brain_json(self, system, user):
        return None

    def read_page(self, url):
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5) as r:
                html = r.read().decode("utf-8", "replace")
        except OSError as exc:
            return {"ok": False, "said": str(exc)}
        title = (re.search(r"<title>(.*?)</title>", html, re.S | re.I) or [None, ""])[1]
        text = re.sub(r"<[^>]+>", " ", re.sub(r"<title>.*?</title>", " ", html, flags=re.S | re.I))
        return {"ok": True, "title": title.strip(), "text": " ".join(text.split())}

    def read_app(self, pkg_id, path=""):
        row = self.registry.get(pkg_id)
        return self.read_page(f"http://127.0.0.1:{row['port']}/")

    def brain_answer(self, question, context):
        return "It shows: " + " ".join(context.split()[:12])


@pytest.fixture
def main_core(state):
    from puffpai_core.api import create_app
    from puffpai_core.api.app import wire_core
    app = create_app(state)
    core = wire_core(app)                               # Zone 3 and the joins (start_core's first part, D57)
    ports = MainPorts(state, registry=core.registry, checklist=app.extensions.get("puffpai.checklist"),
                      consent=core.consent, bus=core.bus, zone=lambda: app.extensions.get("puffpai.zone3"),
                      packages=core.packages, steward=core.steward, caretaker=core.caretaker)
    host = ToolsmithHost(state, ports, core.registry, core.tokens, runner=InlineRunner(app), sleep=lambda s: None)
    app.extensions["puffpai_toolsmith"] = host
    try:
        yield app, core, host, ports
    finally:
        host.stop(timeout=10)
        core.steward.stop_all(remember=False)          # every package Steward started, by its job object
        z = app.extensions.get("puffpai.zone3")
        if z is not None:
            z.stop()


def _waits_for_her(rec) -> bool:
    """A pending card she answers. One inside the plan she said Yes to (``plan`` on the card, nothing
    ``outside_plan``, not floor-level) is the Core's to answer: the Package Manager records it against
    the plan's Yes in the same step that raised it, so she never taps it (lane I2a race fix)."""
    card = rec.get("card") or {}
    return not (card.get("plan") and not card.get("outside_plan") and not rec.get("floor"))


def _answer_cards(core, stop, kinds=("package",), checklist=None, ticked=None):
    """Her answers, from another thread: Yes on the cards that wait for her (tap, with the card_sha the
    store requires), and Done on Toolsmith's Checklist steps (each one is recorded in *ticked*)."""
    def go():
        while not stop.is_set():
            for rec in core.consent.list(status="pending"):
                if rec["scope"]["kind"] in kinds and _waits_for_her(rec):
                    core.consent.answer(rec["consent_id"], "yes", card_sha=rec["card_sha"], by="owner")
            for step in (checklist.list("toolsmith") if checklist is not None else []):
                checklist.done(step["id"], by="owner")
                if ticked is not None:
                    ticked.append(step)
            time.sleep(0.05)
    th = threading.Thread(target=go, daemon=True)
    th.start()
    return th


def _build(host, core, checklist=None, ticked=None):
    b = host.plan("daily news headlines", [{"name": "Daily headlines", "does": "Shows news headlines",
                                           "sample_ask": "What are the headlines today?"}])
    assert b["consent"]["id"], b
    stop = threading.Event()
    th = _answer_cards(core, stop, checklist=checklist, ticked=ticked)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and core.consent.get(b["consent"]["id"])["status"] != "granted":
            time.sleep(0.05)
        done = host.start(b["id"], b["consent"]["id"])
    finally:
        stop.set()
        th.join(timeout=5)
    return done


def test_on_main_a_build_installs_through_the_real_drawbridge(main_core, state):
    app, core, host, ports = main_core
    ticked = []
    done = _build(host, core, app.extensions["puffpai.checklist"], ticked)
    job = done["jobs"][0]
    assert job["status"] == "working", job["run"]
    assert ticked and "outside" in ticked[0]["title"]       # the real zone check asked her first (an outside program)
    row = core.registry.get("hello-page")
    assert row["status"] == "running" and row["built_with"] == "local" and core.steward.alive("hello-page")
    assert core.packages.drawbridge is core.zone3.drawbridge          # the Core's own Drawbridge, not a stand-in
    audit = (state / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    assert audit.count('"check_package"') >= 2 and '"hello-page"' in audit   # at its card and again at install
    cards = [r for r in core.consent.list() if r["scope"] == {"kind": "package", "target": "hello-page"}]
    assert len(cards) == 1 and cards[0]["status"] == "granted"
    assert cards[0]["card"]["drawbridge"]["verdict"] in ("allow", "warn", "hold")   # her card showed the check


def test_the_whole_chain_on_main_with_the_drawbridge_seam_mapped(main_core, state, monkeypatch):
    app, core, host, ports = main_core
    import importlib
    import puffpai_core.zone3 as zone3_pkg
    drawbridge = importlib.import_module("puffpai_core.zone3.drawbridge")    # what L1's seams.find gets

    def check_package(folder, manifest):           # TEST ONLY: L1's seam shape onto L4's class
        v = app.extensions["puffpai.zone3"].drawbridge.check_package(folder, manifest)
        # L4 hashes the tree its own way; L1 re-hashes its staged copy itself and checks again at install.
        return {**v, "verdict": "pass" if v["verdict"] in ("allow", "warn") else "block",
                "reason_code": ",".join(v.get("reasons") or []), "tree_sha": None}
    monkeypatch.setattr(drawbridge, "check_package", check_package, raising=False)
    monkeypatch.setattr(getattr(zone3_pkg, "drawbridge", drawbridge), "check_package", check_package, raising=False)
    done = _build(host, core, app.extensions["puffpai.checklist"], [])
    job = done["jobs"][0]
    assert job["status"] == "working", job["run"]
    row = core.registry.get("hello-page")
    assert row["status"] == "running" and row["built_with"] == "local"
    assert core.steward.alive("hello-page")                         # Steward started it from its manifest
    cards = [r for r in core.consent.list() if r["scope"] == {"kind": "package", "target": "hello-page"}]
    # Inside the plan she said Yes to (03 3.3): recorded against that Yes, no new card for her (lane I2a).
    assert len(cards) == 1 and cards[0]["status"] == "granted" and cards[0]["by"] == f"plan:{done['consent']['id']}"
    assert any("installed under the plan you said Yes to" in ln for ln in job["run"]["lines"])
