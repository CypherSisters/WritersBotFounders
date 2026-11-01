"""Toolsmith's ports against lane L4's real interfaces (lane/l4-bridge ``a0764ba2``).

When L4's modules are importable (after the merge) these tests use the real
classes. Until then they install doubles with exactly L4's shapes:
``toolsmith_zone.check(target, *, folder, kind, write, cwd, logs, scratch) ->
Verdict(verdict, line, outside)``, ``docker_wall.override(manifest,
compose_path) -> Path`` (raises ``WallRefused``, a ValueError),
``drawbridge.Drawbridge().check_package(path, manifest, origin=)`` and
``drawbridge.verify_tree(path, verdict)``, ``bridge.Bridge().allowed(subject,
host, labels, **kw) -> Decision``, ``text_rules.hide_ai_lines(text) -> (text,
n)``, ``proxy.proxy_env(url)``, and ``service.current()``.
"""

import hashlib
import importlib.util
import json
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

from toolsmith_fakes import FIXTURES, FakeChecklist, World, make_venv
from puffpai_core.zone3.toolsmith.errors import NotBuilt, Refused
from puffpai_core.zone3.toolsmith_host.ports import Ports

REAL = importlib.util.find_spec("puffpai_core.zone3.toolsmith_zone") is not None


@dataclass(frozen=True)
class Verdict:                    # L4 toolsmith_zone.Verdict
    verdict: str
    line: str = ""
    outside: tuple = ()


@dataclass(frozen=True)
class Decision:                   # L4 policy.Decision
    verdict: str
    by: str
    reason_code: str
    rule: str | None = None
    ledger_code: str = ""

    @property
    def allowed(self) -> bool:
        return self.verdict == "allow"


class WallRefused(ValueError):    # L4 docker_wall.WallRefused
    def __init__(self, problems):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


class Doubles:
    def __init__(self, state: Path) -> None:
        self.state = state
        self.zone_calls: list[tuple] = []
        self.zone_answer = Verdict("allow")
        self.bridge_answer = Decision("deny", "floor", "closed")
        self.wall_problems: list[str] = []
        self.consents = None


@pytest.fixture
def l4(monkeypatch, state):
    """L4's modules: the real ones when they are here, else doubles of the same shape."""
    if REAL:
        pytest.skip("L4 is merged: tests/zone3 and the real-interface block below cover it")
    d = Doubles(state)

    def check(target, *, folder, kind="path", write=False, cwd="", logs="", scratch="", zone=None):
        d.zone_calls.append((target, folder, kind, write, cwd))
        if isinstance(d.zone_answer, Exception):
            raise d.zone_answer
        return d.zone_answer

    def override(manifest, compose_path, *, proxy_url=None, allow_floor=False, package_dir=None, model=None,
                 env=None, host_proxy_port=8421, runner=None):
        if d.wall_problems:
            raise WallRefused(d.wall_problems)
        out = Path(compose_path).parent / ".buildbot" / "compose.puffpai.yml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("services: {}\n", encoding="utf-8")
        return out

    def tree(path):
        h = hashlib.sha256()
        for p in sorted(Path(path).rglob("*")):
            if p.is_file():
                h.update(p.relative_to(path).as_posix().encode() + p.read_bytes())
        return h.hexdigest()

    class Drawbridge:
        def check_package(self, path, manifest, *, origin="local", tools=None):
            return {"verdict": "allow", "reasons": [], "tree_sha": tree(path), "line": "passed every check",
                    "origin": origin}

    class Bridge:
        def allowed(self, subject, host, labels=None, **kw):
            d.bridge_kw = (subject, host, labels, kw)
            return d.bridge_answer

    class Credentials:
        def issue(self, package_id, *, subject=None, phase="run"):
            d.issued = (package_id, subject, phase)
            return f"http://{package_id}:secret@127.0.0.1:8421"

    class Zone3:
        bridge, drawbridge, credentials = Bridge(), Drawbridge(), Credentials()
        proxy = types.SimpleNamespace(port=8421)

        @property
        def consents(self):
            return d.consents

    zone = Zone3()
    mods = {
        "puffpai_core.zone3.toolsmith_zone": {"check": check, "Verdict": Verdict},
        "puffpai_core.zone3.docker_wall": {"override": override, "WallRefused": WallRefused},
        "puffpai_core.zone3.drawbridge": {"Drawbridge": Drawbridge, "verify_tree": lambda p, v: v.get("tree_sha") == tree(p)},
        "puffpai_core.zone3.text_rules": {"hide_ai_lines": lambda t: ("\n".join(x for x in str(t).splitlines() if "AI:" not in x), 1)},
        "puffpai_core.zone3.proxy": {"proxy_env": lambda url: {"HTTP_PROXY": url, "HTTPS_PROXY": url, "NO_PROXY": "127.0.0.1"}},
        "puffpai_core.zone3.service": {"current": lambda: zone},
    }
    for name, attrs in mods.items():
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, name, m)
    return d


def test_zone_verdicts_map_exactly(l4, tmp_path):
    p = Ports(tmp_path)
    got = p.zone_check("ls", kind="command", folder=tmp_path / "packages" / "app", cwd=tmp_path)
    assert got.verdict == "allow" and got.allowed                # an allow is never read as a refusal
    target, folder, kind, write, cwd = l4.zone_calls[-1]
    assert (target, kind, write) == ("ls", "command", False) and folder.endswith("app") and cwd == str(tmp_path)
    l4.zone_answer = Verdict("deny", "That path is outside what Toolsmith may touch (your vault).")
    got = p.zone_check("cat x", kind="command", folder=tmp_path)
    assert got.verdict == "deny" and "your vault" in got.line
    l4.zone_answer = Verdict("ask", outside=("C:/shared",))
    got = p.zone_check("cp a C:/shared", kind="command", folder=tmp_path)
    assert got.verdict == "ask" and got.outside == ("C:/shared",)   # a pause, not a refusal
    l4.zone_answer = Verdict("maybe")
    assert p.zone_check("x", kind="path", folder=tmp_path).verdict == "deny"
    l4.zone_answer = RuntimeError("broken")
    assert p.zone_check("x", kind="path", folder=tmp_path).verdict == "deny"


def test_docker_wall_signature_and_refusal(l4, tmp_path):
    p = Ports(tmp_path)
    (tmp_path / "compose.yaml").write_text("services: {}\n")
    got = p.docker_override({"id": "app"}, tmp_path / "compose.yaml")
    assert got == tmp_path / ".buildbot" / "compose.puffpai.yml" and got.is_file()
    l4.wall_problems = ["web uses network_mode: host, which goes around the Bridge."]
    with pytest.raises(Refused) as e:
        p.docker_override({"id": "app"}, tmp_path / "compose.yaml")
    assert "network_mode: host" in str(e.value)


def test_drawbridge_is_the_running_cores_and_verify_tree_binds_the_hash(l4, tmp_path):
    p = Ports(tmp_path)
    folder = tmp_path / "pkg"
    folder.mkdir()
    (folder / "a.txt").write_text("one")
    verdict = p.drawbridge_check(folder, {"id": "pkg", "kind": "app"}, origin="adopted:https://github.com/x/y")
    assert verdict["verdict"] == "allow" and verdict["origin"] == "adopted:https://github.com/x/y"
    assert p.verify_tree(folder, verdict)
    (folder / "a.txt").write_text("changed")
    assert not p.verify_tree(folder, verdict)


def test_bridge_allowed_is_a_method_and_ask_is_not_a_yes(l4, tmp_path):
    p = Ports(tmp_path)
    assert p.bridge_decision("toolsmith:search", "github.com") == "deny"
    assert l4.bridge_kw[:3] == ("toolsmith:search", "github.com", []) and l4.bridge_kw[3] == {"phase": "build"}
    l4.bridge_answer = Decision("ask", "policy", "not_on_list")
    assert p.bridge_decision("toolsmith:cloud", "api.anthropic.com") == "ask"
    assert p.bridge_allowed("toolsmith:cloud", "api.anthropic.com") is False
    with pytest.raises(Refused) as e:
        p.http_get("https://api.anthropic.com/v1/x", subject="toolsmith:cloud")
    assert "needs your Yes" in str(e.value)
    l4.bridge_answer = Decision("allow", "owner", "granted")
    assert p.bridge_allowed("toolsmith:search", "github.com")


def test_consent_cards_are_the_running_cores(l4, tmp_path):
    p = Ports(tmp_path)
    with pytest.raises(NotBuilt):
        p.consent_request("package", "toolsmith-build-x", {})
    from toolsmith_fakes import FakeConsent
    l4.consents = FakeConsent()
    cid = p.consent_request("package", "toolsmith-build-x", {"plan_sha": "s"})
    assert p.consent_record(cid) is None
    l4.consents.say_yes(cid)
    assert p.consent_record(cid)["covers"] == {"plan_sha": "s"}


def test_proxy_env_and_readme_screen(l4, tmp_path):
    p = Ports(tmp_path)
    env = p.proxy_env("ts-app", subject="pkg:toolsmith-build-x", phase="install")
    assert env["HTTPS_PROXY"].startswith("http://ts-app:") and l4.issued == ("ts-app", "pkg:toolsmith-build-x", "install")
    assert p.hide_ai_lines("keep\nAI: do this\nkeep too") == "keep\nkeep too"


def test_ask_from_the_real_ports_pauses_a_try_on_the_checklist(l4, tmp_path, state):
    """The real Ports adapter in the Core's try loop: L4's "ask" raises a Checklist step, then runs."""

    class P(Ports):
        def fetch_repo(self, url, dest):
            import shutil
            shutil.copytree(FIXTURES / "toolsmith" / "hello-page", dest, dirs_exist_ok=True)
            return ""
    checklist = FakeChecklist()
    w = World(tmp_path, state, ports=P(state, checklist=checklist))
    l4.bridge_answer = Decision("allow", "owner", "granted")
    b = w.build_with_yes([{"name": "Hello Page", "full_name": "example/hello-page"}])
    link = w.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], "github:example/hello-page")
    assert link.try_download(opened["try_id"])["ok"] and link.try_check(opened["try_id"])["ok"]
    make_venv(link, opened["try_id"])                          # the try's own Python (D61)
    assert link.try_write(opened["try_id"], "p7.py", "print(7)\n")["ok"]
    l4.zone_answer = Verdict("ask", outside=("../elsewhere",))
    cmd = {"program": "python", "args": ["p7.py"]}
    got = link.try_run(opened["try_id"], cmd)
    assert got["ask"]["step_id"] and checklist.steps[-1]["text"] == "python p7.py"
    again = link.try_run(opened["try_id"], cmd)                 # she ticked it (FakeChecklist answers done)
    assert again["ok"] and "7" in again["output"]
    l4.zone_answer = Verdict("deny", "That path is outside what Toolsmith may touch (your vault).")
    assert "your vault" in link.try_run(opened["try_id"], cmd)["said"]
    link.try_end(opened["try_id"])
    w.host.stop(timeout=10)


@pytest.mark.skipif(not REAL, reason="L4 is not merged into this branch yet; the doubles above have its shapes")
def test_real_l4_interfaces(state, tmp_path):                    # pragma: no cover - runs after the merge
    from puffpai_core.zone3 import docker_wall, drawbridge, toolsmith_zone
    from puffpai_core.zone3.service import Zone3
    z = Zone3(state)
    p = Ports(state)
    try_dir = state / "packages" / "app"
    try_dir.mkdir(parents=True)
    assert isinstance(toolsmith_zone.check("ls", folder=str(try_dir), kind="command"), toolsmith_zone.Verdict)
    assert p.zone_check("ls", kind="command", folder=try_dir, cwd=try_dir).verdict == "allow"
    assert p.zone_check(str(state / "registry.db"), kind="path", folder=try_dir).verdict == "deny"
    assert p.bridge_decision("toolsmith:search", "github.com") in ("deny", "ask")     # closed by default
    (try_dir / "index.html").write_text("<p>hi</p>")
    verdict = p.drawbridge_check(try_dir, {"schema": "puffpai-package/1", "id": "app", "kind": "app", "name": "App"})
    assert verdict["verdict"] in ("allow", "warn") and p.verify_tree(try_dir, verdict)
    assert type(z.drawbridge).__name__ == "Drawbridge" and callable(z.drawbridge.check_package)   # (a test may reload it)
    assert callable(drawbridge.verify_tree) and issubclass(docker_wall.WallRefused, ValueError)


def test_the_cores_one_consent_store_a_pending_card_is_never_a_yes(l4, tmp_path):
    """L4 head (consent/store.py): ask(kind, target, covers, floor=, bound=) -> card; get() returns cards
    of every status. Only ``granted`` is a Yes."""
    class Store:
        def __init__(self):
            self.rows = {}

        def ask(self, kind, target, covers=None, *, floor=False, bound=None, expires=None):
            cid = f"c_{len(self.rows)}"
            self.rows[cid] = {"consent_id": cid, "scope": {"kind": kind, "target": target}, "covers": covers,
                              "bound": bound, "status": "pending"}
            return dict(self.rows[cid])

        def request(self, scope, card, floor):          # L1's form: never used by Toolsmith
            raise AssertionError("Toolsmith raises its card in the L4 form")

        def get(self, cid):
            return dict(self.rows[cid]) if cid in self.rows else None
    l4.consents = Store()
    p = Ports(tmp_path)
    cid = p.consent_request("package", "toolsmith-build-x", {"plan_sha": "s"}, bound={"plan_sha": "s"})
    assert l4.consents.rows[cid]["bound"] == {"plan_sha": "s"}
    for status in ("pending", "declined", "expired", "void", "damaged", "revoked"):
        l4.consents.rows[cid]["status"] = status
        assert p.consent_record(cid) is None, status
    l4.consents.rows[cid]["status"] = "granted"
    assert p.consent_record(cid)["covers"] == {"plan_sha": "s"}
