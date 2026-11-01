"""Test doubles for Toolsmith: fake ports (other lanes), fake GitHub, a scripted model, an in-process runner.

Nothing here reaches the internet. Each fake answers the way the interface
named in ``zone3/toolsmith_host/ports.py`` promises (L4's shapes as of
lane/l4-bridge ``a0764ba2``), so a lane that lands with a different shape
shows up as a failure there, in one place.

Toolsmith's own side always talks to the Core through the REAL Core API
routes and auth gate here (``flask_transport``): the test client stands in
for the HTTP socket, and ``InlineRunner`` runs Toolsmith's process body in a
thread instead of a child process. ``tests/test_toolsmith_process.py`` runs
the real child process once.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import threading
import urllib.request
from pathlib import Path
from typing import Any, Callable

from puffpai_core.zone3.toolsmith import agent
from puffpai_core.zone3.toolsmith.errors import NotBuilt, Refused
from puffpai_core.zone3.toolsmith.link import Link
from puffpai_core.zone3.toolsmith_host.ports import KeyUnavailable, ZoneAnswer

FIXTURES = Path(__file__).parent / "fixtures"


def start_held(argv: list, cwd) -> Any:
    """Start a program for a TEST inside a Job Object of its own (the Core's ``steward.jobs``): the program
    and everything it starts (a venv launcher's real python too) end with :func:`end_held`."""
    import os
    from puffpai_core.steward import jobs
    return jobs.Job.spawn(list(argv), cwd, dict(os.environ), None)


def end_held(job: Any) -> None:
    """End what :func:`start_held` started: only what is in its job, never a PID tree or a name."""
    job.terminate(wait_s=10)
    job.close()


class FakeChecklist:
    def __init__(self, answer: str = "done") -> None:
        self.steps: list[dict] = []
        self.answer = answer

    def add(self, step: dict, package: str, blocks: list[str] | None = None, trusted: bool = False,
            trust: str | None = None, raised_by: str | None = None) -> dict:      # L5 Checklist.add
        row = {**step, "package": package, "blocks": blocks, "state": "open", "trust": trust,
               "raised_by": raised_by}
        self.steps.append(row)
        return row

    def get(self, step_id: str) -> dict | None:
        for s in self.steps:
            if s["id"] == step_id:
                s["state"] = self.answer          # the owner ticks it at once
                return s
        return None


class FakeConsent:
    """L4 ``ConsentStore`` shape: ``request(kind, target, covers, floor=) -> card``; ``get(id)`` is a live
    record only after the owner said yes."""

    def __init__(self) -> None:
        self.cards: dict[str, dict] = {}
        self.records: dict[str, dict] = {}

    def request(self, kind: str, target: str, covers: dict | None = None, *, floor: bool = False,
                bound: dict | None = None, expires: str | None = None) -> dict:
        cid = f"c_{len(self.cards) + 1}"
        self.cards[cid] = {"consent_id": cid, "scope": {"kind": kind, "target": target}, "covers": covers or {},
                           "floor": floor, "bound": bound, "expires": expires}
        return dict(self.cards[cid])

    def get(self, cid: str | None) -> dict | None:
        rec = self.records.get(str(cid))
        return dict(rec) if rec else None

    def on_granted(self, fn) -> None:
        """L4's store calls each listener after a Yes (the Toolsmith host fixes a plan's ticks then)."""
        self.__dict__.setdefault("_granted", []).append(fn)

    def say_yes(self, cid: str) -> None:
        self.records[cid] = {**self.cards[cid], "factor": "tap", "by": "owner"}
        for fn in self.__dict__.get("_granted", []):
            fn(dict(self.records[cid]))


def tree_sha(folder: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(Path(folder).rglob("*")):
        if p.is_file() and not any(part in (".git", "node_modules", ".venv") for part in p.parts):
            h.update(p.relative_to(folder).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


class FakePorts:
    """Every Core-side port answers like a landed lane; flip a flag to make one missing."""

    def __init__(self, tmp: Path, *, web: dict[str, Any] | None = None, brain: Callable | None = None) -> None:
        self.tmp = Path(tmp)
        self.web = web or {}                 # url (or url prefix + "*") -> (status, body) or callable
        self.brain = brain                   # (system, user) -> obj
        self.allowed_hosts = {"api.github.com", "raw.githubusercontent.com", "github.com",
                              "api.anthropic.com", "api.openai.com", "generativelanguage.googleapis.com"}
        self.checklist = FakeChecklist()
        self.consent = FakeConsent()
        self.zone_refuse: list[str] = []      # substrings denied by the zone check
        self.zone_ask: list[str] = []         # substrings that wait for her Yes
        self.zone_calls: list[tuple[str, str]] = []
        self.zone_kw: list[dict] = []
        self.drawbridge = "allow"
        self.drawbridge_calls: list[dict] = []
        self.hide_missing = False
        self.docker_wall = False
        self.local_replies: Callable | None = None
        self.sent: list[dict] = []            # what reached the "network" (cloud)
        self.http_send_fn: Callable | None = None
        self.keys: dict[str, str] = {}
        self.leases: dict[str, dict] = {}
        self.vault_locked = False
        self.screen_missing = False
        self.fixture_repo: Path | None = None
        self.added: list[dict] = []
        self.add_fn: Callable | None = None   # (installed folder, origin, consent id): the "Steward start"
        self.pm: dict[str, dict] = {}         # the Package Manager's cards (L1 shape)
        self.pm_answer = "yes"                # how the owner answers each install card
        self.started: list[str] = []
        self.hook_core_sites: list[str] | None = []   # the Core's own Python: not scanned here (one test does)
        self.safe: list[str] = []
        self.read_page_fn: Callable | None = None
        self.answers: Callable | None = None
        self.registry = None

    # L1: PackageManager.add / .wait, Steward.start (main 3881f0d4 shapes)
    def packages_add(self, folder, plan=None, adopted=None):
        self.plans = getattr(self, "plans", []) + [plan]      # the build plan Yes the Core named (03 3.3)
        self.adopted = getattr(self, "adopted", []) + [adopted]
        folder = Path(folder)
        # An adopted app's manifest is the one the Core wrote (.buildbot), never the project's own top-level one.
        man_path = folder / ".buildbot" / "puffpai.json"
        man = json.loads(man_path.read_text(encoding="utf-8"))
        cid = f"pm_{len(self.pm) + 1}"
        staged = self.tmp / "pm-staging" / cid
        shutil.copytree(folder, staged, ignore=shutil.ignore_patterns(".venv", "node_modules", ".git"))
        self.pm[cid] = {"staged": staged, "final": folder, "manifest": man, "status": "pending"}
        return {"consent_id": cid, "id": man["id"], "card": {"title": f"Add {man['name']}?"}, "card_sha": "s",
                "floor": False}

    def packages_wait(self, cid, timeout_s=0.0):
        p = self.pm[cid]
        p["polls"] = p.get("polls", 0) + 1
        if p["status"] == "pending" and self.pm_answer and p["polls"] >= 2:      # she answers a moment later
            if self.pm_answer == "no":
                shutil.rmtree(p["staged"], ignore_errors=True)
                p["status"] = "declined"
            else:
                import os
                os.replace(p["staged"], p["final"])        # the staged copy IS packages\<id>
                man = p["manifest"]
                if self.add_fn:
                    got = self.add_fn(p["final"], man["origin"], cid)
                    p["status"] = "installed" if got.get("ok") else "failed"
                    p["problems"] = [got.get("said") or "no"]
                else:
                    self.added.append({"folder": str(p["final"]), "origin": man["origin"], "yes": cid, "manifest": man})
                    p["status"] = "installed"
        return {"status": p["status"], "id": p["manifest"]["id"], "problems": p.get("problems", [])}

    def steward_start(self, pkg_id):
        self.started.append(pkg_id)
        return {}

    def hook_guard(self, folder, *, stop_try, safe_mode, on_card):
        from puffpai_core.zone3.hook_guard import HookGuard
        return HookGuard(str(folder), core_sites=self.hook_core_sites, stop_try=stop_try, safe_mode=safe_mode,
                         on_card=on_card)

    def safe_mode(self, reason):
        self.safe.append(reason)
        return ["the Bridge is closed"]

    # consent (L4 shape)
    def consent_request(self, kind, target, covers, floor=False, bound=None):
        return self.consent.request(kind, target, covers, floor=floor, bound=bound)["consent_id"]

    def consent_record(self, cid):
        return self.consent.get(cid)

    # L3
    def lend_key(self, key_name, borrower, purpose, *, hosts=(), job=None):
        if self.vault_locked:
            raise KeyUnavailable("locked", "Unlock your vault to let Toolsmith go on.")
        if key_name not in self.keys:
            raise KeyUnavailable("missing", "not saved")
        handle = f"handle:{key_name}:{len(self.leases)}"
        self.leases[handle] = {"key": key_name, "borrower": borrower, "purpose": purpose,
                               "hosts": tuple(hosts), "job": job, "ended": False}
        return handle

    def key_value(self, handle, *, borrower, purpose, host=None):
        if self.vault_locked:
            raise KeyUnavailable("locked", "locked")
        lease = self.leases.get(handle)
        if lease is None or lease["ended"]:
            raise KeyUnavailable("ended", "ended")
        if (borrower, purpose) != (lease["borrower"], lease["purpose"]) or (lease["hosts"] and host not in lease["hosts"]):
            raise KeyUnavailable("refused", "not this lease")
        return self.keys[lease["key"]]

    def end_lease(self, handle):
        if handle in self.leases:
            self.leases[handle]["ended"] = True

    def screen_outbound(self, text):
        if self.screen_missing:
            raise NotBuilt("The personal-details screen", "L3")
        return re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[personal detail]", text)

    # L4 (a0764ba2 shapes)
    def zone_check(self, target, *, kind, folder, write=False, cwd="", logs="", scratch="", pids=(),
                   owner_step=False):
        self.zone_calls.append((kind, target))
        self.zone_kw.append({"pids": tuple(pids or ()), "owner_step": owner_step})
        for bad in self.zone_refuse:
            if bad in target:
                return ZoneAnswer("deny", f"That path is outside what Toolsmith may touch ({bad}).")
        for out in self.zone_ask:
            if out in target:
                return ZoneAnswer("ask", "", (out,))
        return ZoneAnswer("allow")

    def drawbridge_check(self, folder, manifest, *, origin="local"):
        self.drawbridge_calls.append({"folder": str(folder), "manifest": manifest, "origin": origin})
        if self.drawbridge != "allow":
            return {"verdict": "block", "reasons": ["hidden_loader"], "tree_sha": "",
                    "line": "This package: stopped because its page hides encoded code. It was not added."}
        return {"verdict": "allow", "reasons": [], "tree_sha": tree_sha(Path(folder)), "line": "passed"}

    def verify_tree(self, folder, verdict):
        return verdict.get("verdict") in ("allow", "warn") and verdict.get("tree_sha") == tree_sha(Path(folder))

    def hide_ai_lines(self, text):
        if self.hide_missing:
            raise NotBuilt("Drawbridge's README screen", "L4")
        return "\n".join(ln for ln in str(text).splitlines() if "ignore previous instructions" not in ln.lower())

    def screen_knowledge(self, text):
        """The real screen (Drawbridge's text rules): it is code, not another lane's running part."""
        from puffpai_core.zone3.toolsmith_host.ports import Ports
        return Ports.screen_knowledge(self, text)

    def bridge_decision(self, subject, host, phase="build"):
        return "allow" if host in self.allowed_hosts else "deny"

    def bridge_allowed(self, subject, host, phase="build"):
        return self.bridge_decision(subject, host, phase) == "allow"

    def bridge_peek(self, subject, host, phase="build"):
        return self.bridge_decision(subject, host, phase) == "allow"

    def search_places(self):
        from puffpai_core.zone3.policy import SEARCH_HOSTS
        return [h for h in SEARCH_HOSTS if h in self.allowed_hosts]

    def proxy_env(self, name, subject, phase="install"):
        return {}

    def docker_override(self, manifest, compose_path):
        if not self.docker_wall:
            raise NotBuilt("The Docker wall", "L4")
        out = Path(compose_path).parent / ".buildbot" / "compose.puffpai.yml"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"networks": {"puffpai": {"internal": True}}}), encoding="utf-8")
        return out

    # L2
    def local_turn(self, system, messages, tools):
        if self.local_replies is None:
            raise NotBuilt("Toolsmith's model on this computer", "L2")
        return self.local_replies(system, messages, tools)

    def brain_json(self, system, user):
        return self.brain(system, user) if self.brain else None

    def read_page(self, url):
        if self.read_page_fn:
            return self.read_page_fn(url)
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=5) as r:
                html = r.read().decode("utf-8", "replace")
        except OSError as exc:
            return {"ok": False, "said": str(exc)}
        title = (re.search(r"<title>(.*?)</title>", html, re.S | re.I) or [None, ""])[1]
        text = re.sub(r"<[^>]+>", " ", re.sub(r"<title>.*?</title>", " ", html, flags=re.S | re.I))
        return {"ok": True, "title": title.strip(), "text": " ".join(text.split())}

    def read_app(self, pkg_id, path=""):
        row = self.registry.get(pkg_id) if self.registry is not None else None
        if not row:
            return {"ok": False, "said": "no such app"}
        url = row["ui"]["pages"][0]["url"].replace("{port}", str(row.get("port")))
        return self.read_page(url)

    def brain_answer(self, question, context):
        if self.answers:
            return self.answers(question, context)
        return "It shows: " + " ".join(context.split()[:12])

    # L5
    def checklist_add(self, step, package, blocks):
        return self.checklist.add(step, package, blocks)

    def checklist_state(self, step_id):
        got = self.checklist.get(step_id)
        return got["state"] if got else "gone"

    # the network (the Core's own sends)
    def http_send(self, method, url, *, subject, headers=None, body=None, max_bytes=2_000_000,
                  timeout=60.0, phase="build"):
        host = re.match(r"https://([^/:]+)", url).group(1)
        if not self.bridge_allowed(subject, host, phase):
            raise Refused(f"The Bridge is closed for {host}, so nothing was sent.")
        if self.http_send_fn:
            self.sent.append({"url": url, "headers": dict(headers or {}), "body": body, "subject": subject})
            return self.http_send_fn(method, url, headers or {}, body)
        for key, value in self.web.items():
            if url == key or (key.endswith("*") and url.startswith(key[:-1])):
                got = value(url) if callable(value) else value
                status, data = got
                return status, {}, data.encode("utf-8") if isinstance(data, str) else data
        return 404, {}, b""

    def screen_queries(self, queries):
        """Toolsmith's ``Link.screen_queries`` when these fakes stand in for the link: the Core's screen
        (``knowledge.clean_topics`` with this fake's household names and privacy screen; lane I15)."""
        from puffpai_core.zone3.toolsmith_host import knowledge
        names = getattr(self, "household_names", None)
        try:
            household = list(names()) if callable(names) else []
        except Exception:  # noqa: BLE001 - can't read the household: none (fail closed, as the Core)
            return []
        return knowledge.clean_topics(list(queries)[:15], self.screen_outbound, household=household)

    def http_get(self, url, *, subject="toolsmith:search", accept="", max_bytes=2_000_000, headers=None,
                 keyed=False, timeout_s=None):
        return self.http_send("GET", url, subject=subject, max_bytes=max_bytes, headers=headers,
                              **({} if timeout_s is None else {"timeout": timeout_s}))

    def fetch_repo(self, url, dest):
        if self.fixture_repo is None:
            return "The download failed: no fixture."
        shutil.copytree(self.fixture_repo, dest, dirs_exist_ok=True)
        return ""


def flask_transport(app: Any, token: str):
    """Toolsmith's link through the real Core API routes and auth gate (no socket)."""
    def call(method, path, body, timeout):
        r = app.test_client().open(path, method=method, json=body, headers={"Authorization": f"Bearer {token}"})
        return r.status_code, (r.get_json(silent=True) or {})
    return call


class _Done:
    def __init__(self, code):
        self.code = code

    def wait(self, timeout=None):
        return True

    def stop(self):
        pass


class InlineRunner:
    """Runs Toolsmith's process body in a thread, to its end, through the real routes."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.runs: list[tuple[str, str]] = []

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.runs.append((do, build_id))
        box = {}

        def go():
            link = Link(flask_transport(self.app, token), build_id)
            try:
                box["code"] = agent.run({"do": do, "build": build_id, "cache": str(cache)}, link)
            except Exception:  # noqa: BLE001 - as a crashed process
                box["code"] = 1
        th = threading.Thread(target=go)
        th.start()
        th.join()
        on_exit(box.get("code"))
        return _Done(box.get("code"))


def tool(name: str, call_id: str = "", **args) -> dict:
    return {"type": "tool_use", "id": call_id or f"t{name}", "name": name, "input": args}


class Script:
    """A scripted model: each turn returns the next reply; records what it was sent."""

    def __init__(self, turns: list) -> None:
        self.turns = list(turns)
        self.seen: list[list[dict]] = []

    def __call__(self, system, messages, tools):
        self.seen.append(json.loads(json.dumps(messages)))
        if not self.turns:
            return {"content": [{"type": "text", "text": "I am done."}], "stop": "end_turn", "usage": None}
        nxt = self.turns.pop(0)
        if callable(nxt):
            nxt = nxt(messages)
        if isinstance(nxt, dict) and "content" in nxt:
            return nxt
        blocks = nxt if isinstance(nxt, list) else [nxt]
        return {"content": blocks, "stop": "tool_use" if any(b.get("type") == "tool_use" for b in blocks)
                else "end_turn", "usage": None}


def find_reply(req: dict, usage: dict | None = None, skip: Any = ()) -> dict | None:
    """A company stub's reply to a cloud job's look (``scout``, D121) for a test about its tries, in the company's own
    wire shape: it states a need, tries the plan's apps in order (but those named in *skip*), then finishes. None when
    *req* is not a look."""
    if not any(t.get("name") == "state_needs" for t in req.get("tools") or []):
        return None
    turn = sum(1 for m in req["messages"] if m["role"] == "assistant")
    task = req["messages"][0]["content"][0]["text"]
    refs = [r for r in re.findall(r'"ref": "([^"]+)"', task.split("Places to look at", 1)[0]) if r.rsplit("/", 1)[-1] not in skip]
    if turn == 0:
        name, args = "state_needs", {"needs": ["an app for this"]}
    elif turn <= len(refs):
        name, args = "try_app", {"url_or_ref": refs[turn - 1]}
    else:
        name, args = "finish", {"reason": "No app fit."}
    return {"content": [{"type": "tool_use", "id": f"look{turn}", "name": name, "input": args}], "stop_reason": "tool_use",
            "usage": usage or {"input_tokens": 1, "output_tokens": 1, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}


def last_result(messages: list[dict]) -> str:
    """The text of the last tool result the model saw."""
    for m in reversed(messages):
        for b in reversed(m["content"]):
            if b.get("type") == "tool_result":
                return str(b.get("content"))
    return ""


class World:
    """A Core app with Toolsmith's host on fake ports, and Toolsmith's side linked to it by token."""

    def __init__(self, tmp: Path, state: Path, ports: FakePorts | None = None, registry: Any = None) -> None:
        from puffpai_core.api import create_app
        from puffpai_core.registry import Registry
        from puffpai_core.zone3.toolsmith_host import ToolsmithHost
        self.state = Path(state)
        self.registry = registry or Registry(self.state / "registry.db")
        self.app = create_app(self.state, registry=self.registry)
        self.core = self.app.extensions["puffpai"]
        self.ports = ports or FakePorts(tmp)
        self.ports.registry = self.registry
        self.runner = InlineRunner(self.app)
        self.host = ToolsmithHost(self.state, self.ports, self.registry, self.core.tokens, runner=self.runner,
                                  sleep=lambda s: None)
        self.app.extensions["puffpai_toolsmith"] = self.host

    def build_with_yes(self, cands: list[dict], job: dict | None = None) -> dict:
        """A build with one job whose plan holds *cands*, and the owner's Yes (no Toolsmith process)."""
        st = self.host.settings()
        job = job or {"name": "Daily headlines", "does": "Shows the news", "sample_ask": "What are the headlines?"}
        b = self.host.store.create("daily news", [job], st)
        self.host.store.set_plan(b["id"], {"jobs": [{**job, "plan": {"candidates": cands}}]}, st, "")

        def yes(x):
            x["yes"] = {"given": True, "consent_id": "c_test", "sha": x["plan_sha"]}
            for j in x["jobs"]:
                j["run"]["state"] = "planned"
        return self.host.store.change(b["id"], yes)

    def link(self, build_id: str) -> Link:
        return Link(flask_transport(self.app, self.core.tokens.issue_toolsmith(build_id)), build_id)


def make_venv(link: Link, try_id: str) -> dict:
    """The try's own Python, made by the Core (``python -m venv .venv``, D61); no pip, so it is quick."""
    got = link.try_run(try_id, {"program": "python", "args": ["-m", "venv", "--without-pip", ".venv"]})
    assert got.get("ok"), got
    return got


def script(link: Link, try_id: str, rel: str, code: str, *, start: bool = False, args: list | None = None) -> dict:
    """Write *code* to *rel* in the try (the Core's write tool), then run it (or start it) with the try's own
    python: the shell-free way a try runs code (D60: no ``python -c``)."""
    assert link.try_write(try_id, rel, code).get("ok")
    request = {"program": "python", "args": [rel, *(args or [])]}
    return link.try_start(try_id, request) if start else link.try_run(try_id, request)


__all__ = ["FIXTURES", "FakeChecklist", "FakeConsent", "FakePorts", "InlineRunner", "Script", "World",
           "find_reply", "flask_transport", "last_result", "make_venv", "script", "tool", "tree_sha"]
