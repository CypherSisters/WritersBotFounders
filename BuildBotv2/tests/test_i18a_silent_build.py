"""Lane I18a: her test3 build ended 25 s after her Yes, exit 0, with nothing tried for any of its 3 jobs.

Root cause: her vault had locked itself (idle) before her Yes. With a cloud model for Toolsmith, each job first
asks the company to rank its candidates; the key could not be lent (``CloudWaitVault``), and ``Builder._job``
turned that into ``JobHeld("vault")`` at once. ``_run_job`` wrote ``waiting_vault`` and the job's thread ended:
no wait, no try. The process ended when the knowledge fetch did, and ``_child_done`` turned every
``waiting_vault`` (an active state) into the generic stopped line. Nothing reached the agent log.

Here the build is her test3 build's shape (cloud Anthropic settings, 3 jobs with Docker candidates, knowledge
on), run through the real Core routes with fake ports: no network, no real key, no port of her bot."""

import contextlib
import json
import threading
from pathlib import Path

import pytest

from puffpai_core.zone3.toolsmith import agent
from puffpai_core.zone3.toolsmith.errors import CoreUnreachable
from puffpai_core.zone3.toolsmith.link import STATUS_RETRY_S, Link
from puffpai_core.zone3.toolsmith_host import sender
from puffpai_core.zone3.toolsmith_host.service import STOPPED_BY_LINE, STOPPED_LINE
from toolsmith_fakes import find_reply, FIXTURES, World, flask_transport

BUILD = FIXTURES / "toolsmith" / "i18a_test3_build.json"
KEY = "sk-ant-" + "F" * 40                       # made up: only the fake company sees it


def _reply(content, stop="end_turn"):
    return 200, {}, json.dumps({"content": content, "stop_reason": stop,
                                "usage": {"input_tokens": 10, "output_tokens": 5}}).encode()


class Company:
    """Anthropic's API, faked in the ports: each job's look tries its apps; a try gives up at once."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, method, url, headers, body):
        if "api.anthropic.com" not in url:
            return 404, {}, b""                  # the knowledge reads: nothing found
        req = json.loads(body or b"{}")
        look = find_reply(req)
        self.seen.append("look" if look else "try")
        if look:                                 # a job's look (D121): it tries the plan's apps, then finishes
            return _reply(look["content"], "tool_use")
        return _reply([{"type": "tool_use", "id": f"tu{len(self.seen)}", "name": "give_up",
                        "input": {"reason": "the fake company gives up"}}], "tool_use")


class LogRunner:
    """Toolsmith's process body in a thread, to its end, through the real routes; what it prints to stderr goes
    to ``logs/toolsmith/agent-<build>.log`` as the real process's does. *transport* can wrap the link."""

    def __init__(self, app, log_dir: Path, wrap=None) -> None:
        self.app, self.log_dir, self.wrap = app, Path(log_dir), wrap
        self.codes: list = []

    def launch(self, do, build_id, token, *, cache, on_exit):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        box = {}

        def go():
            tr = flask_transport(self.app, token)
            link = Link(self.wrap(tr) if self.wrap else tr, build_id, sleep=lambda s: None)
            with open(self.log_dir / f"agent-{build_id}.log", "w", encoding="utf-8") as fh, \
                    contextlib.redirect_stderr(fh):
                try:
                    box["code"] = agent.run({"do": do, "build": build_id, "cache": str(cache)}, link)
                except Exception as exc:  # noqa: BLE001 - as the real __main__
                    print(f"Toolsmith stopped: {type(exc).__name__}", file=fh)
                    box["code"] = 1
        th = threading.Thread(target=go)
        th.start()
        th.join(120)
        assert not th.is_alive(), "Toolsmith's build did not end"
        self.codes.append(box.get("code"))
        on_exit(box.get("code"))
        from toolsmith_fakes import _Done
        return _Done(box.get("code"))


def _world(tmp_path, state, *, wrap=None, vault_wait_s=60):
    w = World(tmp_path, state)
    w.runner = LogRunner(w.app, state / "logs" / "toolsmith", wrap)
    w.host.runner = w.runner
    prov = w.host.providers()["anthropic"]
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "toolsmith.json").write_text(json.dumps({
        "where": "cloud", "provider": "anthropic", "model": "claude-opus-5-5", "cost_cap_usd": 5.0,
        "consent": {"sha": sender.consent_sha(prov), "at": "2026-10-27T20:52:20+00:00", "by": "owner"},
        "timing": {"poll_s": 0, "vault_wait_s": vault_wait_s}}), encoding="utf-8")
    w.ports.keys = {"toolsmith.anthropic": KEY}
    w.ports.allowed_hosts |= {"en.wikipedia.org", "www.gutenberg.org"}
    w.company = Company()
    w.ports.http_send_fn = w.company
    b = json.loads(BUILD.read_text(encoding="utf-8"))
    d = state / "builds" / b["id"]
    d.mkdir(parents=True)
    (d / "build.json").write_text(json.dumps(b), encoding="utf-8")
    return w, b["id"]


def _log(state, build_id) -> str:
    return (state / "logs" / "toolsmith" / f"agent-{build_id}.log").read_text(encoding="utf-8")


def _unlock_after(ports, n: int) -> None:
    """Her vault is locked at her Yes and unlocked after *n* lend asks (the jobs' vault-wait polls)."""
    real = ports.lend_key
    asks = {"n": 0}

    def lend(*a, **kw):
        asks["n"] += 1
        ports.vault_locked = asks["n"] <= n
        return real(*a, **kw)
    ports.vault_locked = True
    ports.lend_key = lend


def test_a_locked_vault_at_her_yes_waits_then_every_job_tries_its_apps(tmp_path, state):
    w, bid = _world(tmp_path, state)
    _unlock_after(w.ports, 4)
    w.host._launch("build", bid)
    assert w.runner.codes == [0]
    b = w.host.view(bid)
    assert w.company.seen.count("look") >= 3 and "try" in w.company.seen     # each job's look went out, then the tries
    for j in b["jobs"]:
        run = j["run"]
        assert run["line"] != STOPPED_LINE and run["state"] == "not_yet", run
        assert run["tried"], run                                  # each job tried its app
    assert "ended without its last status" not in _log(state, bid)


def test_a_vault_that_stays_locked_holds_each_job_with_its_own_line(tmp_path, state):
    w, bid = _world(tmp_path, state, vault_wait_s=0)
    w.ports.vault_locked = True
    w.host._launch("build", bid)
    b = w.host.view(bid)
    for j in b["jobs"]:
        assert (j["run"]["state"], j["run"]["line"]) == ("held", agent.VAULT_HELD_LINE)
    assert w.company.seen.count("look") == 0                      # nothing was sent without her key


def test_status_writes_the_core_never_answers_are_logged_and_named_on_the_job(tmp_path, state):
    """Every status write fails as if the Core could not be reached: each is tried again, then the agent log
    names the class (scrubbed message), and the Core's end-of-process line says what stopped the job."""
    sent = {"status": 0}

    def wrap(tr):
        def call(method, path, body, timeout):
            if path.endswith("/status"):
                sent["status"] += 1
                raise CoreUnreachable("Toolsmith could not reach the Core (ConnectionResetError). "
                                      "Bearer abc123secret ghp_" + "Z" * 20)
            return tr(method, path, body, timeout)
        return call
    w, bid = _world(tmp_path, state, wrap=wrap)
    w.host._launch("build", bid)
    log = _log(state, bid)
    assert "CoreUnreachable" in log and "did not reach the Core" in log
    assert "abc123secret" not in log and "ghp_" not in log and "[removed]" in log
    assert sent["status"] % (len(STATUS_RETRY_S) + 1) == 0       # every write was tried again before giving up
    b = w.host.view(bid)
    for j in b["jobs"]:
        assert f"job {j['id']} ended without its last status: CoreUnreachable" in log
        assert j["run"]["state"] == "held"
        assert j["run"]["line"] == STOPPED_BY_LINE.format(what="CoreUnreachable")


def test_an_error_that_ends_a_job_is_in_the_log_with_its_class(tmp_path, state, monkeypatch):
    def boom(*a, **kw):
        raise ZeroDivisionError("token Bearer s3cr3tvalue")
    monkeypatch.setattr(agent.scout.Search, "run", boom)
    w, bid = _world(tmp_path, state)
    w.host._launch("build", bid)
    log = _log(state, bid)
    assert log.count("ended on an error: ZeroDivisionError") == 3 and "s3cr3tvalue" not in log
    for j in w.host.view(bid)["jobs"]:
        assert j["run"]["line"] == agent.UNEXPECTED_LINE.format(what="ZeroDivisionError")


def test_a_status_write_is_tried_again_when_the_core_does_not_answer():
    calls = []

    def flaky(method, path, body, timeout):
        calls.append(path)
        if len(calls) < 3:
            raise CoreUnreachable("Toolsmith could not reach the Core (ConnectionRefusedError).")
        return 200, {"ok": True}
    waits = []
    link = Link(flaky, "b1", sleep=waits.append)
    assert link.job_status("b1-0", state="building") == {"ok": True}
    assert len(calls) == 3 and waits == list(STATUS_RETRY_S[:2])

    def dead(method, path, body, timeout):
        raise CoreUnreachable("no")
    with pytest.raises(CoreUnreachable):
        Link(dead, "b1", sleep=lambda s: None).job_status("b1-0", state="building")


def test_a_refusal_from_the_core_is_not_sent_again():
    calls = []

    def refuse(method, path, body, timeout):
        calls.append(path)
        return 400, {"error": "That is not a job state."}
    from puffpai_core.zone3.toolsmith.errors import LinkError
    with pytest.raises(LinkError):
        Link(refuse, "b1", sleep=lambda s: None).job_status("b1-0", state="nope")
    assert len(calls) == 1


def test_the_process_crash_class_is_named_on_jobs_it_left(tmp_path, state):
    w, bid = _world(tmp_path, state)
    log_dir = state / "logs" / "toolsmith"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"agent-{bid}.log").write_text("Toolsmith stopped: MemoryError\n", encoding="utf-8")
    w.host._child_done(bid, "build", 1)
    for j in w.host.view(bid)["jobs"]:
        assert j["run"]["line"] == STOPPED_BY_LINE.format(what="MemoryError")


def test_only_the_last_build_runs_log_lines_count(tmp_path, state):
    """The agent log is appended to: a job named by an earlier run is not named again after a clean stop."""
    w, bid = _world(tmp_path, state)
    log_dir = state / "logs" / "toolsmith"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"agent-{bid}.log").write_text(
        "Toolsmith: build started\n"
        f"Toolsmith: job {bid}-0 ended without its last status: CoreUnreachable\n"
        "Toolsmith: build started\n", encoding="utf-8")
    w.host._child_done(bid, "build", 0)
    assert {j["run"]["line"] for j in w.host.view(bid)["jobs"]} == {STOPPED_LINE}
