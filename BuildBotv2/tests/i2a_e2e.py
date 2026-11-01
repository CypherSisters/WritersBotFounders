"""Integration lane I2a: ADOPT end to end on REAL parts, through the wizard (pytest ``-m e2e`` and a live script).

A fresh bot folder; the Core on free ports (its own address, the pages address, the Bridge proxy) with
Toolsmith's REAL child process; the wizard driven through the Core API (``POST /v1/wizard/<step>``, the
browser renderer's own route) step by step: scan, brain, vault (PIN and recovery code), the Bridge
("search"), Toolsmith's model (this computer), the goal, the plan with a pasted GitHub project, the ONE Yes,
the build (its "Your turn" step is ticked here, and any Yes card for what was not on the plan is reported),
prove. Then the checks: the plan's candidate, Drawbridge's check of the download, HookGuard, the Bridge's
lines for the downloads, the manifest Toolsmith wrote (origin adopted, show new_tab), the install under the
plan's Yes (D60: the step she ticked is covered; a Drawbridge "warn" gets ONE card for just that, which she
answers), Steward running it from its manifest, Caretaker's health, and ``read_app`` summarized by the
brain from chat.

Real: GitHub (read-only, unauthenticated, the Core's generic User-Agent), the project's own downloads through
the Bridge proxy, this computer's local model for the brain (phrases, rank, the prove answer, chat).
Toolsmith's own model turns are the local model too, unless ``scripted=True``: then a fixed script drives
the same tools in the same order a developer would, each run as one program and its arguments with no shell
(D60, D61): download, look at its files with the Core's file tools (list_files, read_file), make its own
Python (``python -m venv .venv``, a Core step), install its requirements with that Python's pip, start it on
its port, ask her to make her notes account, wait for its page, done. Nothing else differs, so the scripted
run proves the plumbing and the live run proves what the local model can do.

``run(state, scripted=..., max_tries=...) -> [steps]``; each step is ``{"step", "ok", "said"}``.
Live: ``.venv\\Scripts\\python tests\\i2a_e2e.py --state <empty folder> [--scripted] [--max-tries N]``.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

import i1_e2e  # noqa: E402
from i1_e2e import ADDRESS, MODEL, PIN, _wait  # noqa: E402

GOAL = "a simple notes app I can open in my browser"
PROJECT = "https://github.com/techwithtim/Flask-Web-App-Tutorial"
FULL = "techwithtim/Flask-Web-App-Tutorial"


def scripted_turns() -> Callable[[str, list, list], dict]:
    """Toolsmith's model turns, fixed: the same tools a developer uses, in order (one try). Every program is
    ``{program, args, cwd}`` (no command lines, no shell); files are read with the Core's file tools."""

    def turn(system: str, messages: list[dict], tools: list[dict]) -> dict:
        first = messages[0]["content"][0]["text"]
        port = re.search(r"Your port: (\d+)", first).group(1)
        n = sum(1 for m in messages if m["role"] == "assistant") + 1
        steps = {1: ("download", {}),
                 2: ("list_files", {"path": ""}),
                 3: ("read_file", {"path": "requirements.txt"}),
                 4: ("run", {"program": "python", "args": ["-m", "venv", ".venv"], "cwd": ""}),
                 5: ("run", {"program": "python", "args": ["-m", "pip", "install", "-r", "requirements.txt"],
                             "cwd": ""}),
                 6: ("start_server", {"program": "python",
                                      "args": ["-m", "flask", "--app", "main", "run", "--host", "127.0.0.1",
                                               "--port", port], "cwd": ""}),
                 7: ("ask_owner", {"kind": "confirm", "title": "Make your notes account",
                                   "why": "The notes app shows your notes only after you sign up, with an email and "
                                          "a password you choose. Toolsmith goes on when you tick Done.",
                                   "text": f"Open http://127.0.0.1:{port}/sign-up in your browser and sign up."}),
                 8: ("wait_for_page", {"url": f"http://127.0.0.1:{port}/"}),
                 9: ("done", {"url": f"http://127.0.0.1:{port}/"})}
        if n not in steps:
            return {"content": [{"type": "text", "text": "Done."}], "stop": "end_turn", "usage": None}
        name, args = steps[n]
        return {"content": [{"type": "tool_use", "id": f"s{n}", "name": name, "input": args}], "stop": "tool_use",
                "usage": None}
    return turn


class Run(i1_e2e.Run):
    def __init__(self, state: Path, log: Callable[[str], None], scripted: bool, max_tries: int) -> None:
        super().__init__(state, MODEL, ADDRESS, log)
        self.scripted, self.max_tries = scripted, max_tries
        self.turns: list[dict] = []
        self.cards: list[dict] = []
        self.build = ""
        self.pkg = ""

    # -- the Core, with Toolsmith's model and the model download held to what this run may do ----------------
    def start(self) -> bool:
        from puffpai_core import wiring
        from puffpai_core.zone2 import settings as model_settings
        model_settings.save(self.state, address=self.address, model=self.model)
        wiring.WizardModels.pull = lambda self_, name: None          # never pull a model in this run
        if self.scripted:
            from puffpai_core.zone2 import brain
            brain.toolsmith_turn = scripted_turns()
        return super().start()

    # -- the wizard, through the Core API ---------------------------------------------------------------------
    def wizard(self, step: str, answers: dict, *, timeout_s: float = 3600) -> dict:
        status, screen = self.api("POST", f"/v1/wizard/{step}", {"answers": {}, "renderer": "browser"}, timeout=900)
        end = time.monotonic() + timeout_s
        lines: list[str] = []
        code = None
        while status == 200 and isinstance(screen, dict) and time.monotonic() < end:
            lines += [str(x) for x in screen.get("lines") or []]
            for ln in screen.get("lines") or []:
                self.log(f"    {step}: {ln}")
            if screen.get("show"):
                code = screen["show"]["value"]
            if screen["status"] == "working":
                self.watch_tries()
                time.sleep(float(screen.get("poll_s") or 2))
                status, screen = self.api("POST", f"/v1/wizard/{step}", {"answers": {}, "renderer": "browser"},
                                          timeout=900)
                continue
            field = screen.get("field") if screen["status"] in ("ask", "show") else None
            if not field:
                break
            fid = field["id"]
            if fid == "recovery_confirm":
                value = code
            elif fid == "turn":
                self.turns.append({"lines": list(screen.get("lines") or [])})
                value = "done"
            elif fid == "card":
                self.cards.append({"lines": list(screen.get("lines") or [])})
                value = True
            else:
                value = answers.get(fid, field.get("default", ""))
            status, screen = self.api("POST", f"/v1/wizard/{step}", {"answers": {fid: value}, "renderer": "browser"},
                                      timeout=900)
        if status != 200 or not isinstance(screen, dict):
            return {"status": "error", "lines": lines + [f"HTTP {status}: {screen}"]}
        return {**screen, "all_lines": lines}

    def watch_tries(self) -> None:
        """Hold a live run to *max_tries* candidates (each try may take 12 minutes)."""
        host = self.app.extensions.get("puffpai_toolsmith")
        if host is None or not self.build or not self.max_tries:
            return
        b = host.view(self.build)
        if any(len(j["run"].get("tried") or []) >= self.max_tries for j in b["jobs"]):
            self.log(f"    (stopping the build after {self.max_tries} candidate(s))")
            host.stop(timeout=30)

    def steps_through_wizard(self) -> bool:
        data = self.state / "PuffPAI Data"
        plan: dict = {}
        for step, answers in (("scan", {}), ("brain", {"model": "other", "other": self.model}),
                              ("vault", {"where": "here", "pin": PIN, "pin_again": PIN, "data_folder": str(data), "linked": ""}),
                              ("bridge", {"posture": "search"}), ("toolsmith", {"where": "local"}),
                              ("goal", {"goal": GOAL}), ("name", {}), ("look", {}),
                              ("plan", {"project": PROJECT}), ("yes", {"yes": True})):
            t0 = time.monotonic()
            got = self.wizard(step, answers)
            if step == "plan":
                plan = got
            said = f"{got['status']} in {time.monotonic() - t0:.0f}s; " + " | ".join(got.get("all_lines") or [])[:500]
            if not self.step(f"wizard step {step}", got["status"] == "done", said):
                return False
        prog = json.loads((self.state / "settings" / "wizard.json").read_text(encoding="utf-8"))
        self.build = prog["build"]
        host = self.app.extensions["puffpai_toolsmith"]
        b = host.view(self.build)
        cands = [c["full_name"] for j in b["jobs"] for c in j["plan"]["candidates"]]
        self.step("The plan: her pasted project first, the owner's four questions per job (find, needs, setup)",
                  bool(cands) and cands[0] == FULL,
                  f"jobs {[j['name'] for j in b['jobs']]}; candidates {cands}; setup {[s['id'] for s in b['setup']]}; "
                  f"install hosts {b['envelope']['install_hosts']}; summary {plan.get('lines')}")
        rec = self.core.consent.get(b["consent"]["id"]) or {}
        return self.step("ONE Yes for the plan (the wizard's Yes answered the plan card)",
                         rec.get("status") == "granted" and rec.get("by") == "owner",
                         f"consent {b['consent']['id']} {rec.get('status')} by {rec.get('by')}")

    def build_and_prove(self) -> bool:
        t0 = time.monotonic()
        got = self.wizard("build", {})
        took = time.monotonic() - t0
        host = self.app.extensions["puffpai_toolsmith"]
        b = host.view(self.build)
        job = b["jobs"][0]
        self.step(f"wizard step build ({'scripted' if self.scripted else 'local model'} Toolsmith turns)",
                  got["status"] == "done", f"{got['status']} in {took:.0f}s; job {job['status']}: {job['run']['line']}")
        self.log("    job lines: " + " | ".join(job["run"].get("lines") or [])[:3000])
        self.step("One \"Your turn\" step showed, she ticked Done, Toolsmith went on", len(self.turns) >= 1,
                  " || ".join(" | ".join(t["lines"])[-300:] for t in self.turns) or "no Your turn step was raised")
        tried = job["run"].get("tried") or []
        result = job["run"].get("result") or {}
        self.pkg = str(result.get("package") or "")
        if not self.step("Toolsmith adopted it and proved it", job["status"] == "working" and bool(self.pkg),
                         f"status {job['status']}; tried {tried}; line {job['run']['line']}"):
            return False
        audit = (self.state / "logs" / "audit.jsonl").read_text(encoding="utf-8")
        self.step("Drawbridge checked the download (before anything ran) and the install",
                  audit.count('"check_package"') >= 2 and FULL.split("/")[1].lower() in audit.lower(),
                  f"{audit.count(chr(34) + 'check_package' + chr(34))} check_package lines in audit.jsonl")
        hook_ended = any("start-up files" in str(x) for x in job["run"].get("lines") or [])
        self.step("HookGuard ran around every command; nothing changed Python's start-up files", not hook_ended,
                  "no try was stopped by the start-up file check" if not hook_ended else "a try was stopped")
        ledger = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                           for p in (self.state / "logs").glob("*.jsonl") if "bridge" in p.name or "outside" in p.name)
        hosts = sorted({h for h in ("github.com", "pypi.org", "files.pythonhosted.org") if h in ledger})
        self.step("The Bridge carried the downloads (its own log names the hosts)", "pypi.org" in hosts,
                  f"hosts in the Bridge log: {hosts}")
        # An adopted app's manifest is the one the Core wrote from Toolsmith's (never a top-level one).
        man_path = self.state / "packages" / self.pkg / ".buildbot" / "puffpai.json"
        man = json.loads(man_path.read_text(encoding="utf-8")) if man_path.is_file() else {}
        page = ((man.get("ui") or {}).get("pages") or [{}])[0]
        self.step("The manifest the Core wrote from Toolsmith's: origin adopted, show new_tab, a run block with no shell",
                  man.get("origin") == f"adopted:https://github.com/{FULL}" and page.get("show") == "new_tab",
                  json.dumps({k: man.get(k) for k in ("id", "origin", "run", "health", "needs")})[:900])
        cards = [r for r in self.core.consent.list() if r["scope"] == {"kind": "package", "target": self.pkg}]
        card = cards[0]["card"] if len(cards) == 1 else {}
        outside = [str(x) for x in card.get("outside_plan") or []]
        verdict = (card.get("drawbridge") or {}).get("verdict")
        seen = (f"{[(c['consent_id'], c['status'], c['by']) for c in cards]}; Drawbridge {verdict}; "
                f"outside the plan {outside}; her cards: "
                + " || ".join(" | ".join(c["lines"])[:400] for c in self.cards))
        self.step("The step she ticked Done is covered by the plan's Yes (D60: the Core's own record of her tick)",
                  len(cards) == 1 and not any("did not tick" in x or "not the ones you ticked" in x for x in outside),
                  seen)
        if verdict == "warn":
            # D60: a Drawbridge "warn" always gets a card. One card, showing only that difference first.
            self.step("Drawbridge's warn got ONE card for just that difference, and her Yes installed it",
                      len(self.cards) == 1 and bool(outside)
                      and all(x.startswith("Drawbridge found something to know first") for x in outside)
                      and cards[0]["status"] == "granted" and cards[0]["by"] == "owner"
                      and (card.get("plan") or {}).get("consent_id") == b["consent"]["id"], seen)
        else:
            self.step("No Yes card for anything outside the plan", not self.cards, seen)
            self.step("Installed under the plan's Yes (no new card for her)",
                      len(cards) == 1 and cards[0]["by"] == f"plan:{b['consent']['id']}", seen)
        alive = self.core.steward.alive(self.pkg)
        self.step("Steward runs it from its manifest", bool(alive),
                  f"alive {alive}; pids {self.core.steward.pids(self.pkg)}")
        self.core.caretaker.check(self.pkg) if hasattr(self.core.caretaker, "check") else None
        row = _wait(lambda: (lambda r: r if r.get("status") == "running" and (r.get("health") or {}).get("ok")
                             else None)(self.row(self.pkg)), 90)
        self.step("Caretaker: health running", bool(row),
                  json.dumps({k: (row or self.row(self.pkg)).get(k) for k in ("status", "health", "port", "built_with",
                                                                              "origin")}))
        self.step("The Registry says where it came from (origin adopted:<url>)",
                  (row or self.row(self.pkg)).get("origin") == f"adopted:https://github.com/{FULL}",
                  str((row or self.row(self.pkg)).get("origin")))
        proof = result.get("proof") or {}
        self.step("Toolsmith's prove: the brain read its page", proof.get("result") == "working",
                  json.dumps(proof)[:600])
        prove = self.wizard("prove", {})
        self.step("wizard step prove", prove["status"] == "done", " | ".join(prove.get("all_lines") or [])[:400])
        return True

    def read_app(self) -> bool:
        t0 = time.monotonic()
        status, body = self.api("POST", "/v1/chat", {"text": f"Use read_app on {self.pkg} and tell me in one "
                                                             "sentence what its page shows."}, timeout=600)
        took = time.monotonic() - t0
        said = (body.get("text") if isinstance(body, dict) else str(body)) or ""
        tools = body.get("tools") or body.get("used") if isinstance(body, dict) else None
        return self.step("read_app summarizes it (chat, the local model)", status == 200 and len(said) > 10,
                         f"HTTP {status} in {took:.0f}s: {said[:400]} (tools {tools})")


def run(state: Path, *, scripted: bool = False, max_tries: int = 1,
        log: Callable[[str], None] = print) -> list[dict]:
    r = Run(Path(state), log, scripted, max_tries)
    try:
        if r.start() and r.steps_through_wizard() and r.build_and_prove():
            r.read_app()
    finally:
        r.stop()
    return r.steps


def main(argv: list[str] | None = None) -> int:
    import argparse
    import os
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--state", required=True, help="an EMPTY folder for this bot (never a real bot folder)")
    p.add_argument("--scripted", action="store_true", help="script Toolsmith's model turns")
    p.add_argument("--max-tries", type=int, default=1)
    args = p.parse_args(argv)
    state = Path(args.state)
    if state.exists() and any(state.iterdir()):
        print(f"{state} is not empty; give an empty folder.")
        return 2
    os.environ["PUFFPAI_STATE"] = str(state)
    os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    t0 = time.monotonic()
    steps = run(state, scripted=args.scripted, max_tries=args.max_tries,
                log=lambda line: print(line, flush=True))
    failed = [s for s in steps if not s["ok"]]
    print(f"\n{len(steps) - len(failed)} of {len(steps)} steps ok in {time.monotonic() - t0:.0f}s")
    (state.parent / f"{state.name}-steps.json").write_text(json.dumps(steps, indent=2), encoding="utf-8")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
