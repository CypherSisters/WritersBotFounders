# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/pipeline.py (rewritten-from)
"""Toolsmith's own process: plan a build, or run one (03 Part 1 steps 6-10, Part 3).

The Core starts this process (``python -I -m puffpai_core.zone3.toolsmith``)
for one build and one task, and gives it a Toolsmith token on stdin. Every
way out goes through the Core (``link``).

- :func:`plan`: FIND for each job (the owner's four questions, ``find``); no
  company model is asked before the Yes. The plan goes to the Core, which
  keeps it, writes the ONE Yes card from it and asks her.
- :func:`build`: runs only after her Yes (the Core checked it). Jobs run one
  at a time; a job waiting on a "Your turn" step or on the vault steps
  aside, and the next job goes on. Each job: reuse a package that fits;
  else, on a cloud build with no pasted project, one conversation works out what
  the job needs and goes to find it (``scout``, D121); else each candidate is
  tried (``tries``), installed through the
  Core (``adopt``), and proven (``prove``). The first one that works ends the
  job. A candidate Drawbridge couldn't fully check is set aside (the Core
  keeps what was held; nothing ran in it); only when no fully checked one
  worked does the job ask for ONE card listing those set aside, and tries
  just the ones she ticks (lane I4b).
- A job she ticked an offer for (an installed package one brain ask picked)
  reuses it: the Core put it on the plan at her Yes. A job with no tick
  searches as planned.
- The job's last line names every candidate tried, each with its plain
  reason, in the order tried (her pasted project first).
- **Nothing unexpected stops the build loop.** Any error in a job, of any
  kind, holds that job with a plain line, and its open try is ended by the
  Core (its programs stopped by job object, its folder removed junction-safe).
  If this whole process dies, the Core does the same for every job and try it
  left (``toolsmith_host.service``).

v1 sources in ``build/pipeline.py``: ``plan`` :269, ``run_job`` :323,
``run_after_yes`` :487 (rewritten: its own process, the Core link).
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import adopt, find, knowledge, prove, redact, scout, tries
from .errors import CloudError, JobHeld, NotBuilt, Refused

NOTHING_LINE = "Nothing found yet. Paste a GitHub address for this job."
UNEXPECTED_LINE = ("Toolsmith hit a problem it did not expect ({what}) while working on this job, so the job "
                   "is held and what it started was stopped. Check again to retry.")
VAULT_HELD_LINE = ("Your vault stayed locked while Toolsmith waited, so this job is held. Unlock your vault, then "
                   "check again to retry.")
#: The job states the Core holds when this process ends (``toolsmith_host.store.ACTIVE``): not a last state.
ACTIVE = ("planned", "building", "waiting_you", "waiting_vault")
#: The agent log line the Core reads when a job was left without its last state (``service._child_done``).
LEFT_MARK = "ended without its last status: "
#: How long a newly kept service waits for Caretaker to read its tools and check its health (D124).
SERVICE_WAIT_S = 120.0
#: The first agent log line of each build run: the log is appended to, so the Core reads only after the last one.
BUILD_STARTED = "build started"


def log(line: str) -> None:
    """One line in Toolsmith's agent log (this process's stderr, ``logs/toolsmith/agent-<build>.log``),
    scrubbed of keys and tokens."""
    try:
        print("Toolsmith: " + redact.scrub_text(" ".join(str(line).split()))[:600], file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 - a log line never stops a job
        pass


def log_error(where: str, exc: BaseException) -> None:
    """*where* went wrong: the error's class name and its message, scrubbed."""
    log(f"{where}: {type(exc).__name__}: {str(exc)[:300]}")


def why_of(name: str, why: str) -> str:
    """A reason without the app's own name in front ("superset: superset: You said No" was said twice)."""
    why = " ".join(str(why or "").split())
    low = name.lower() if name else ""
    for _ in range(3):
        if low and why.lower().startswith(f"{low}: "):
            why = why[len(name) + 2:]
        elif low and why.lower().startswith(f"{low} "):             # "X changed after ..." -> "it changed ..."
            why = "it " + why[len(name) + 1:]
    return why or "it did not work"


def tried_line(tried: list[dict]) -> str:
    """One plain line per candidate tried, with its reason, in the order tried."""
    return " ".join(f"{t['name']}: {why_of(t['name'], t['why']).rstrip('.')}." for t in tried)


def _plan_books(link: Any, got: dict, js: list[dict], now: Any) -> None:
    """Find more books (D136): her own words are the topic. No app looked for, no model asked."""
    picked = [j["name"] for j in js]
    fn = getattr(link, "knowledge_find", None)
    if callable(fn) and got.get("search_places"):
        now("looking for books and offline libraries")
        try:
            fn(picked)
        except Exception as exc:  # noqa: BLE001 - no downloads on the card, never no plan
            log(f"knowledge downloads: not looked for ({type(exc).__name__})")
    asked = {"jobs": len(js), "gave": len(picked), "no_answer": 0, "timed_out": 0}
    link.put_plan({"jobs": [], "knowledge": {"topics": picked, "asked": asked}})     # no job: no app to find or try


def plan(link: Any, cache: Path) -> None:
    """FIND for every job; the plan goes to the Core."""
    got = link.build()
    b = got["build"]
    now = getattr(link, "now", None) or (lambda _line: None)
    js = find.jobs_for(b["goal"], b.get("jobs_in"))
    if b.get("books_only"):
        _plan_books(link, got, js, now)
        return
    # D99: the plan's own limit leaves room to write the plan; no place or slow brain may lose it.
    wait = float(((got.get("settings") or {}).get("timing") or {}).get("plan_wait_s") or 0)
    share = float(find.sources()["budget"]["plan_share"])
    finder = find.Finder(link, Path(cache), now=now, ends_at=time.time() + wait * share if wait else None,
                         from_files=(got.get("settings") or {}).get("where") == "cloud",   # D116
                         places=got.get("search_places"), web_ready=got.get("web_ready"),
                         owner_rows=got.get("owner_apis"),
                         context=" ".join([b["goal"]] + [f"{j['name']} {j['does']}" for j in js]))
    finder.notes = []
    finder.say = finder.notes.append
    finder._now(f"asking your bot's brain for search words for {len(js)} job{'s' if len(js) != 1 else ''}")
    phrases = finder.search_words(js, b["goal"])
    finder.start_places(js, phrases)               # every other place, all at once, from here on (D99)
    reg = link.registry()
    finder.store_rows = [a for a in reg.get("store") or [] if isinstance(a, dict)]    # lane I10c
    out = []
    for i, j in enumerate(js, 1):
        finder.label = f"Job {i} of {len(js)}, {j['name']}"
        finder.jobs_left = len(js) - i + 1           # its share of the searches: what is left over the jobs to go
        finder._now("looking at what is already on your bot")
        finder.notes.clear()
        p = finder.plan_job(j, phrases.get(j["n"], []), reg.get("rows") or [], reg.get("proven") or {})
        p["notes"] = list(dict.fromkeys(list(p.get("notes") or []) + finder.notes))[:5]   # why nothing came
        out.append({**j, "plan": p})
    finder.label = ""
    finder._now("asking your bot's brain which knowledge topics to read for each job")
    asked: dict = {}
    picked = knowledge.topics(link, b["goal"], js, asked, stop=finder.over)
    fn = getattr(link, "knowledge_find", None)
    if picked and callable(fn) and got.get("search_places"):
        finder._now("looking for downloads about those topics")
        try:
            fn(picked)                             # the Core's own asks, within their own time (D99)
        except Exception as exc:  # noqa: BLE001 - no downloads on the card, never no plan
            log(f"knowledge downloads: not looked for ({type(exc).__name__})")
    # The build log (D84): how many topics the brain gave, for how many jobs, and asks with no answer. Counts
    # only, never the words. The Core adds how many it kept.
    log(f"knowledge topics: the brain gave {asked.get('gave', 0)} for {asked.get('jobs', 0)} jobs; "
        f"{asked.get('no_answer', 0)} asks had no answer, {asked.get('timed_out', 0)} of them timed out")
    finder.round.finish()
    finder._now("writing the plan")
    link.put_plan({"jobs": out, "knowledge": {"topics": picked, "asked": asked}, "places": finder.places_said()})


class _Hooks(tries.Hooks):
    def __init__(self, runner: "Builder", job_id: str) -> None:
        self.runner, self.job_id = runner, job_id
        self.vault_probe: Callable[[], bool] | None = None
        self._stop = False
        self._next = False
        self.label = ""

    def now(self, line: str) -> None:
        """What the try does right now: one plain line, replaced each time (the wizard and the Desktop)."""
        self._send(now=(f"{self.label}: {line}" if self.label else line)[:300])

    def _send(self, **fields: Any) -> None:
        try:
            got = self.runner.link.job_status(self.job_id, **fields)
        except Exception as exc:
            self.runner._write_failed(self.job_id, fields.get("state"), exc)
            raise
        self._stop = self._stop or bool(got.get("stop"))
        self._next = self._next or bool(got.get("next"))

    def say(self, line: str) -> None:
        self._send(say=line[:300])

    def status(self, state: str, line: str = "", **more: Any) -> None:
        self._send(state=state, line=line, **more)

    def stopped(self) -> bool:
        self._send()
        return self._stop

    def next_wanted(self) -> bool:
        if self._next:
            self._next = False
            return True
        return False

    def _wait(self, ready: Callable[[], Any], limit_s: float) -> Any:
        """Step aside (other jobs go on) until *ready* returns something."""
        self.runner.slot.release()
        try:
            end = time.monotonic() + limit_s
            while not self.stopped():
                got = ready()
                if got:
                    return got
                if time.monotonic() >= end:
                    return None
                self.runner.sleep(float(self.runner.settings["timing"]["poll_s"]))
            return None
        finally:
            self.runner.slot.acquire()

    def wait_step(self, step_id: str) -> str:
        def ready() -> str:
            state = self.runner.link.step_state(step_id)
            return state if state in ("done", "skipped", "gone") else ""
        return self._wait(ready, 7 * 24 * 3600) or "skipped"

    def wait_for(self, ready: Callable[[], Any], limit_s: float) -> Any:
        return self._wait(ready, limit_s)

    def at_share(self) -> bool:
        """This job spent its share of the cap (D119), so a running try stops too (D121)."""
        return bool(self.runner.share and self.models.spent >= self.runner.share)

    def wait_vault(self) -> bool:
        if self.vault_probe is None:
            return False
        return bool(self._wait(self.vault_probe, float(self.runner.settings["timing"]["vault_wait_s"])))


class Builder:
    """One build's run, in Toolsmith's process."""

    tried_text = staticmethod(tried_line)

    def __init__(self, link: Any, *, sleep: Callable[[float], None] = time.sleep, cache: Path | None = None) -> None:
        self.link, self.sleep, self.cache = link, sleep, cache
        info = self.info = link.build()
        self.b = info["build"]
        self.settings = info["settings"]
        self.shell, self.os_name = str(info.get("shell") or "PowerShell"), str(info.get("os_name") or "Windows")
        self.slot = threading.Semaphore(1)
        st = self.settings
        self.models = tries.Models(link, {"company": st.get("company"), "model": st.get("model")}
                                   if st.get("where") == "cloud" else None)
        # A job's share of the cap (D119): only the jobs that can spend. One job: the sender's cap decides. On a cloud
        # build every job that does not reuse can spend, since it looks for what it needs even with no app (D121).
        cloud = self.models.cloud is not None
        spending = sum(1 for j in self.b.get("jobs") or [] if (j.get("plan") or {}).get("candidates")
                       or (cloud and not (j.get("plan") or {}).get("reuse")))
        self.cap = float(st.get("cost_cap_usd") or 0.0) if self.models.cloud is not None else 0.0
        self.share = self.cap / spending if self.cap and spending > 1 else 0.0

    def run(self) -> None:
        if not self.b.get("yes"):
            return
        workers = []
        if self.b.get("knowledge"):
            # The knowledge fetch runs beside the jobs (it is the Core's own reading, no try, no slot).
            th = threading.Thread(target=self._knowledge, daemon=True)
            workers.append(th)
            th.start()
        for j in self.b["jobs"]:
            if ((j.get("run") or {}).get("state") or "planned") != "planned":
                continue                       # "Check again" (lane I18e): a job that has its answer stays as it is
            th = threading.Thread(target=self._run_job, args=(j,), daemon=True)
            workers.append(th)
            th.start()
        for th in workers:
            th.join()
        if self.b.get("knowledge"):
            self._library()

    def _library(self) -> None:
        """The jobs and the knowledge fetch have ended: a job no app works for may be proved by this build's
        library (D71, 03 Part 4 "Knowledge"). The Core decides and writes the job; an app that worked still wins."""
        for j in self.b["jobs"]:
            try:
                got = self.link.library_check(j["id"])
            except Exception as exc:  # noqa: BLE001 - the job keeps its own line
                log_error(f"job {j['id']}: the library check did not run", exc)
                continue
            if got.get("working"):
                log(f"job {j['id']} works from your library")

    def _knowledge(self) -> None:
        """Step the Core's knowledge fetch to its end. Any error ends it; the Core says how far it got."""
        try:
            knowledge.fetch(self.link)
        except Exception as exc:  # noqa: BLE001 - the Core ends an unfinished fetch with a plain line
            log_error("the knowledge fetch ended", exc)

    def _ends(self) -> tuple[set, dict]:
        """(the jobs whose last state reached the Core, {job: the class of its last failed status write})."""
        return self.__dict__.setdefault("_final", set()), self.__dict__.setdefault("_missed", {})

    def _write_failed(self, job_id: str, state: Any, exc: BaseException) -> None:
        self._ends()[1][job_id] = type(exc).__name__
        log_error(f"job {job_id}: its status write{f' ({state})' if state else ''} did not reach the Core", exc)

    def _status(self, job_id: str, state: str, line: str = "", **more: Any) -> None:
        try:
            self.link.job_status(job_id, state=state, line=line, **more)
        except Exception as exc:  # noqa: BLE001 - logged; the Core holds a job it never heard about at the end
            self._write_failed(job_id, state, exc)
            return
        if state not in ACTIVE:
            self._ends()[0].add(job_id)

    def _run_job(self, job: dict) -> None:
        self.slot.acquire()
        current: dict = {"try": None}
        jid = job["id"]
        what = ""                                # the class of the error that ended the job, if one did
        try:
            self._job(job, current)
        except JobHeld as exc:
            # Nothing waits on this job once its thread ends, so its last state is held (lane I18a: a waiting
            # state left here became the Core's generic stopped line when the process ended).
            log_error(f"job {jid} is held ({exc.code})", exc)
            self._status(jid, "held", VAULT_HELD_LINE if exc.code == "vault" else exc.reason, trying=None)
        except (NotBuilt, Refused) as exc:      # their words are plain lines already
            log_error(f"job {jid} is held", exc)
            self._status(jid, "held", f"{exc} The job is held. Check again to retry.", trying=None)
        except Exception as exc:  # noqa: BLE001 - nothing unexpected stops the build: the job holds
            what = type(exc).__name__
            log_error(f"job {jid} ended on an error", exc)
            self._status(jid, "held", UNEXPECTED_LINE.format(what=what), trying=None)
        finally:
            try:
                t = current.get("try")
                if t is not None and not t.closed:
                    t.close()
            except Exception as exc:  # noqa: BLE001 - the Core ends a try this process left open
                log_error(f"job {jid}: its try could not be closed", exc)
            finally:
                self.slot.release()
            final, missed = self._ends()
            if jid not in final and (what or jid in missed):
                log(f"job {jid} {LEFT_MARK}{what or missed[jid]}")

    def _at_share(self, job: dict, hooks: "_Hooks", left: list[dict], tried: list[dict]) -> bool:
        """This job spent its share of the cap (D119), so it starts no new try: its last line says so, and True."""
        if not self.share or hooks.models.spent < self.share:
            return False
        line = find.share_line(hooks.models.spent, self.share, self.cap, [c["name"] for c in left])
        log(f"job {job['id']} stopped at its share of the cap")
        self._status(job["id"], "not_yet", f"{line} {tried_line(tried)}".strip()[:600], trying=None)
        return True

    def _job(self, job: dict, current: dict) -> None:
        jid = job["id"]
        plan_ = job["plan"]
        if plan_["reuse"]:
            rows = self.link.registry().get("rows") or []
            row = next((r for r in rows if r.get("id") == plan_["reuse"]), None)
            if row is None and plan_.get("reuse_by") == "store":
                # She ticked a PuffPAI store app, but it was not added (her No on its card, or it failed).
                name = plan_.get("reuse_name") or plan_["reuse"]
                # A row of the store's index may be gone by now (D96), so this does not promise it can be added.
                later = "" if plan_.get("from_index") else " You can add it from the PuffPAI store on your Desktop."
                self._status(jid, "nothing_found", f"{name} from the PuffPAI store was not added, so this job "
                                                   f"has nothing to use.{later}")
                return
            if row is not None and row.get("abilities"):
                self._prove_tools(job, row)        # a package with tools: its own health, then the chat
            else:
                self._prove(job, plan_["reuse"], None)
            return
        cands = list(plan_["candidates"])
        hooks = _Hooks(self, jid)
        hooks.models = self.models.one_job()
        if self.models.cloud is not None:
            def probe() -> bool:
                try:
                    return self.link.model_probe()
                except CloudError:
                    return False
            hooks.vault_probe = probe
        tried: list[dict] = []
        set_aside: list[dict] = []
        if self.models.cloud is not None and not job.get("pasted"):
            # D121: one conversation instead of a walk down the plan. Her pasted project stays that one only.
            search = scout.Search(self, job, hooks, current, tried, set_aside)
            if search.run():
                return
            if hooks.stopped():
                return
            hooks.status("building", scout.LOOKING, trying=None)     # the model can't be used: the plan's apps, in order
            cands = [c for c in cands if c["ref"] not in search.refs]
        if not cands and not tried and not set_aside:
            line = find.nothing_line(job, find.pasted_any(self.b.get("jobs")))
            offer = plan_.get("offer") or {}
            if offer.get("package"):
                line = f"You did not tick {offer.get('name') or offer['package']}, so Toolsmith searched. {line}"
            self._status(jid, "nothing_found", line[:600])
            return
        for i, cand in enumerate(cands, 1):
            if hooks.stopped() or self._at_share(job, hooks, cands[i - 1:] + set_aside, tried):
                return
            got = self._try_one(job, cand, f"{cand['name']} (app {i} of {len(cands)})", hooks, current, tried)
            if got == "done":
                return
            if got == "held":
                set_aside.append(cand)
        if set_aside and not hooks.stopped():
            if self._at_share(job, hooks, set_aside, tried):
                return
            picked = self._ask_holds(job, set_aside, hooks, tried)
            for i, cand in enumerate(picked, 1):
                if hooks.stopped() or self._at_share(job, hooks, picked[i - 1:], tried):
                    return
                if self._try_one(job, cand, f"{cand['name']} (set aside, {i} of {len(picked)})", hooks, current,
                                 tried) == "done":
                    return
        if self._at_share(job, hooks, [], tried):      # a cloud try can stop there, even the last one (D121)
            return
        self._status(jid, "not_yet", f"No app worked yet. {tried_line(tried)}"[:600], trying=None)

    def _try_one(self, job: dict, cand: dict, label: str, hooks: "_Hooks", current: dict,
                 tried: list[dict]) -> str:
        """One candidate: ``done`` (it works and is proven), ``held`` (set aside: Drawbridge couldn't check
        some files, or found a tunnel) or ``failed`` (its reason is on the job)."""
        jid = job["id"]
        hooks.label = label
        self._status(jid, "building", cand["name"], trying=cand["name"])
        t = tries.Try(job, cand, link=self.link, settings=self.settings, hooks=hooks,
                      shell=self.shell, os_name=self.os_name)
        current["try"] = t
        tries.drive(t, hooks.models)
        if getattr(t, "held", False):
            t.close()
            return "held"
        if not t.result:
            why = why_of(cand["name"], t.ended or "it did not work")
            mine = cand.get("source") == "pasted" or self.models.cloud is not None     # a local plan as it was (D116)
            if mine and not cand.get("readme_steps") and cand.get("tries_in") != "docker" and not adopt.made(cand):
                # The project's README gave no steps (D63(c), D116): say that, then what stopped the try.
                why = f"its README does not say how to start it, and Toolsmith's try from its files ended: {why}"
            tried.append({"name": cand["name"], "why": why})
            self.link.job_status(jid, tried={"name": cand["name"], "ref": cand["ref"], "why": why})
            t.close()
            return "failed"
        got = adopt.install(t, self.link)
        if not got["ok"]:
            why = why_of(cand["name"], got["said"])
            tried.append({"name": cand["name"], "why": why})
            self.link.job_status(jid, tried={"name": cand["name"], "ref": cand["ref"], "why": why})
            t.close()
            return "failed"
        current["try"] = None
        self.__dict__.setdefault("_built", {})[got["id"]] = cand
        if ((got["manifest"] or {}).get("abilities") or {}).get("mcp"):
            self._prove_service(job, got["id"], got["manifest"], hooks)
            return "done"
        self._prove(job, got["id"], got["manifest"], notes=t.notes)
        return "done"

    def _ask_holds(self, job: dict, set_aside: list[dict], hooks: "_Hooks", tried: list[dict]) -> list[dict]:
        """No fully checked candidate worked: ONE card for the job lists those set aside, and the job waits
        for her answer (stepping aside like a "Your turn" step). The candidates she ticked, in plan order."""
        jid = job["id"]
        holds_ask = getattr(self.link, "holds_ask", None)
        got = holds_ask(jid) if callable(holds_ask) else {}
        if not got.get("consent_id"):
            for c in set_aside:
                why = got.get("said") or "Drawbridge set it aside, and no card could be shown"
                tried.append({"name": c["name"], "why": why_of(c["name"], why)})
            return []
        names = ", ".join(c["name"] for c in set_aside)
        hooks.label = ""
        hooks.status("waiting_you", str(got.get("title") or "Your Yes for apps Drawbridge set aside")[:120],
                     trying=None)
        tunnel = str(got.get("tunnel") or "")          # D117: the Core's words, only when an app on the card has one
        if tunnel:                                     # first, so a long list of names is what the 300 characters cut
            hooks.say(f"{tunnel} One card asks you about {names}. Nothing from it runs before you answer." if got.get("alone")
                      else f"{tunnel} No other app worked, so one card asks you about the ones set aside: {names}. "
                           "Nothing from them runs before you answer.")
        else:
            hooks.say(f"Drawbridge couldn't check some of the files of {names}, so one card asks you. Nothing from it "
                      "runs before you answer."[:300] if got.get("alone") else
                      f"No other app worked, so one card asks you about the ones set aside: "
                      f"{names}. Nothing from them runs before you answer."[:300])

        def answered() -> dict | None:
            st = self.link.holds_state(jid)
            return st if st.get("state") != "pending" else None
        st = hooks.wait_for(answered, 7 * 24 * 3600) or {"try": [], "skipped": [
            {"name": c["name"], "ref": c["ref"], "why": "stopped before your answer"} for c in set_aside]}
        for x in st.get("skipped") or []:
            name = str(x.get("name") or x.get("ref") or "")
            why = str(x.get("why") or "skipped")
            tried.append({"name": name, "why": why})
            self.link.job_status(jid, tried={"name": name, "ref": str(x.get("ref") or ""), "why": why})
        refs = set(st.get("try") or [])
        return [c for c in set_aside if c["ref"] in refs]

    def _prove_service(self, job: dict, pkg_id: str, manifest: dict, hooks: "_Hooks") -> None:
        """D124: a service with no page works only when the brain can reach it. Caretaker reads its tools and checks its
        health just after it starts, so this waits for that, then asks the brain as for any package with tools."""
        def seen() -> dict | None:
            row = next((r for r in self.link.registry().get("rows") or [] if r.get("id") == pkg_id), None)
            ready = row is not None and row.get("abilities") and (row.get("health") or {}).get("checked_at")
            return row if ready or (row is not None and row.get("status") not in ("running", "installing")) else None
        got = hooks.wait_for(seen, SERVICE_WAIT_S)
        name = str(manifest.get("name") or pkg_id)
        if not got or not got.get("abilities"):
            self._status(job["id"], "not_yet", f"{name} was added, but your bot's brain can't reach its tools yet, so it is "
                                               "not counted as working.", trying=None)
            return
        self._prove_tools(job, got, new=True)

    def _prove_tools(self, job: dict, row: dict, new: bool = False) -> None:
        """A package already on the bot: it works for this job only when the brain, given the job's own words,
        picks one of its tools (``puffpai check`` item 12's test) AND Caretaker's health check passes."""
        name = str(row.get("name") or row["id"])
        health = row.get("health") or {}
        result = {"package": row["id"], "url": "", "proof": {"result": "", "health": health}, "notes": []}
        got = find.picks(self.link, row["id"], find.job_ask(job))
        result["proof"]["ask"] = {"ask": find.job_ask(job), "called": list(got.get("called") or [])[:5]}
        if not got.get("ok"):
            # Her line in plain words; the brain's own reason (tool names) stays in the result's detail (lane I8a).
            how = "could not be asked" if got.get("asked") is False else "did not pick it"
            why = f"{name} is on your bot, but your bot's brain {how} for this job, so it is not counted as working."
            result["proof"]["detail"] = str(got.get("reason") or "no reason given")[:300]
            result["proof"]["result"] = "not_yet"
            self._status(job["id"], "not_yet", why, trying=None, result=result)
            return
        if row.get("status") == "running" and health.get("ok"):
            asks = {tool: str(v[0]) for tool, v in (row.get("examples") or {}).items() if isinstance(v, list) and v}
            # The sample ask of the tool the brain picked for this job ("Weekly meal planner" -> list_meal_plan's),
            # else the one most about this job's words; none of them is about it: no example.
            picked = [c.split(".", 1)[1] for c in got.get("called") or [] if c.startswith(f"{row['id']}.")]
            want = find.stems(f"{job.get('name') or ''} {job.get('does') or ''}")
            near = max(asks.items(), key=lambda a: len(want & find.stems(f"{a[0]} {a[1]}")), default=("", ""))
            example = next((asks[t] for t in picked if t in asks), "") or (
                near[1] if want & find.stems(f"{near[0]} {near[1]}") else "")
            by = (job.get("plan") or {}).get("reuse_by")
            ticked = by == "tick"
            # What its tool does, in its own words, never the tool's name (lane I8a); the name is in proof.ask.
            mine = [c for c in got.get("called") or [] if c.startswith(f"{row['id']}.")]
            does = find.tools_said(mine, [row], "can")
            flags = find.tool_flags(mine, [row])
            if flags:
                result["proof"]["detail"] = "; ".join(flags)[:300]     # kept off her line: said in the log
            line = (f"{name} is already on your bot" + (", you ticked it for this job" if ticked else "")
                    + f": {does}, and its health check passes.")
            if new:                                # D124
                line = f"{name} runs as a service your bot can use, with no page: {does}, and its health check passes."
            if by == "store":                      # her tick added it with this plan: the add-card's words (D104)
                line = f"You said Yes. {name} is added: {does}, and its health check passes."
            line += f" Ask the chat, for example: \"{example}\"." if example else " Ask the chat to use it."
            result["proof"]["result"] = "working"
            self._status(job["id"], "working", line, trying=None, result=result)
            return
        if row.get("status") != "running":
            why = f"{name} is on your bot but is {row.get('status') or 'not running'}. Start it from the Desktop."
        else:
            why = (f"{name} is on your bot, but its health check did not pass: "
                   f"{health.get('reason') or 'not checked yet'}.")
        result["proof"]["result"] = "not_yet"
        self._status(job["id"], "not_yet", why, trying=None, result=result)

    def _prove(self, job: dict, pkg_id: str, manifest: dict | None, notes: list[str] | None = None) -> None:
        rows = self.link.registry().get("rows") or []
        row = next((r for r in rows if r.get("id") == pkg_id), None)
        name = str((row or {}).get("name") or (manifest or {}).get("name") or pkg_id)
        url = ""
        pages = ((manifest or {}).get("ui") or (row or {}).get("ui") or {}).get("pages") or []
        if pages:
            url = str(pages[0].get("url") or "")
        port = (row or {}).get("port")
        if "{port}" in url:
            url = url.replace("{port}", str(port)) if port else ""
        if not url:
            self._status(job["id"], "not_yet", f"{name} has no page address Toolsmith can check.", trying=None)
            return
        got = prove.prove(self.link, row, pkg_id, name, job, url)
        result = {"package": pkg_id, "url": url, "proof": got, "notes": (notes or [])[:20]}
        if got["result"] == "working":
            # The Prove step and chat say "<job>: works." first, so this line never says "works" again.
            line = f"The brain read {name}: {got['answer'][:200]}"
            if adopt.made(getattr(self, "_built", {}).get(pkg_id)):
                line = f"Toolsmith built {name} itself for this job. {line}"
            self._status(job["id"], "working", line, trying=None, result=result)
        else:
            self._status(job["id"], "not_yet", got["why"], trying=None, result=result)


def build(link: Any, cache: Path | None = None) -> None:
    Builder(link, cache=cache).run()


def run(msg: dict, link: Any) -> int:
    """The process's one task. 0 when it ran to its end (jobs may still be held)."""
    do = str(msg.get("do") or "")
    if do == "plan":
        plan(link, Path(str(msg.get("cache") or ".")))
        return 0
    if do == "build":
        log(BUILD_STARTED)
        build(link, Path(str(msg["cache"])) if msg.get("cache") else None)
        return 0
    return 2


__all__ = ["ACTIVE", "BUILD_STARTED", "Builder", "LEFT_MARK", "NOTHING_LINE", "UNEXPECTED_LINE", "VAULT_HELD_LINE",
           "build", "log", "log_error", "plan", "run"]
