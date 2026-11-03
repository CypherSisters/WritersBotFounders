"""Where the lanes meet in a running Core (integration lane I1). One join per thing.

``wire(app)`` runs once per app, after Zone 3 is made:

- **Drawbridge** (lane L4) checks every install: the Package Manager gets the Core's own
  ``Zone3.drawbridge`` (with the audit log), through ``seams.drawbridge_check``.
- **Proxy credentials** (lane L4): Steward gets ``Zone3.package_env`` for every start and setup, and
  the Docker wall (``zone3.docker_wall.override``) gets the proxy's real port; no proxy, no Docker start.
- **The one PIN check** (lane L3, D29): ``seams.check_pin``. The consent store (a floor-level Yes)
  calls it; lane L5's ``app.extensions["puffpai.pin_check"]`` (run as administrator) is the same
  function unless the caller passed its own.
- **Every package tool call from chat** (09 section 3, X27): the Brain's ``review(call)`` is
  :func:`tool_review`, Drawbridge's ``review_action`` for each call. Whether a tool changes things is
  Drawbridge's one rule (``zone3.drawbridge.tool_changes``: the manifest, the tool's MCP annotations,
  its name rule; unknown counts as changing). Inside what the install Yes approved: it runs, no card,
  logged. Outside it: a ``tool_call`` Yes card for that one call, and the turn waits for her answer.
- **The wizard** (lane L5's engine): ``core.vault`` (lane L3's Archivist), ``core.bridge``
  (lane L4's posture), ``core.models`` (lane L2's fit, state and the one model setting), and
  ``core.toolsmith`` (lane L6's host, :class:`WizardToolsmith`, integration lane I2a): Toolsmith's
  model, the goal (on the local brain), the plan, the ONE Yes, the build and its "Your turn" steps,
  and the results.

``wire(app)`` runs in ``api.app.wire_core`` (Zone 3 made, then the joins); ``api.app.start_core``
(what ``serve`` calls) runs it first. ``create_app`` builds the app only: a bare app has no Zone 3
wiring, so installs there use a fresh Drawbridge with no audit log, and chat has no review (a
changing tool does not run without a Yes it can't get).

``start(core)`` and ``stop(core)`` run inside ``api.app.start_core`` / ``stop_core``: the Bridge
proxy (on ``core.proxy_port``, 0 = any free port) and the bypass watch (D26) start with the Core,
and Drawbridge gets the everyday scope of every installed package.

**Cards** (integration lane I2b): Caretaker's (``exposed``, ``listen_unknown``, ``safe_mode``) and Zone 3's
own (``Zone3.cards``: ``bridge_damaged``, ``ledger_damaged``, ``audit_damaged``, ``vault_mismatch``,
``proxy_down``, ``bypass``, ``watch_not_running``, ``watch_blind``, ``no_machine_key``,
``grant_refused``) go to lane L5's notices (the Desktop shows them) and are printed in the terminal that
runs ``puffpai serve`` (``puffpai notices`` lists them later).

**Safe mode** (01 "Core start", D28(18)): ``wire`` gives Caretaker its ``safe_steps`` (close the Bridge,
lock the vault, pause Toolsmith), so ``core.caretaker.safe_mode(reason)`` is the one call. The Bridge's
tamper cards (``bridge_damaged``, ``ledger_damaged``, ``audit_damaged``) call it. In safe mode, opening the
Bridge again is a floor-level action (orchestrator decision, I2b): ``bridge_guard`` is the Bridge's
``open_guard``, so ``/v1/bridge/open``, the Desktop and ``puffpai bridge open`` need the PIN (D29) and say
the safe-mode reason first; without it the Bridge stays closed (the wizard has no PIN field, so its
"search" posture is refused). Safe mode itself lasts until restart. DECIDED HERE:
``vault_mismatch`` does not, since it comes while she unlocks: the Bridge is already closed, and locking
the vault she just opened would leave her no way to check and open the Bridge again.

**Chat asks Toolsmith** (``ask_toolsmith``, 03 Part 5): :func:`chat_toolsmith` is the Brain's ``toolsmith``. It makes
a one-job plan with the same host calls as ``POST /v1/toolsmith/plan``, shows the plan's ONE Yes card in chat
(the turn waits for her answer), starts the build after her Yes (``/start``'s own check), and when the build
ends says the result in the same conversation and as a notice. A new ask while a build she started in the same
conversation is still open joins that build, unless she says the new one comes first (D77).

**A core tool's own question** (``read_app`` on a stopped app, ``run_command``, D138): ``core_question(core)`` is the Brain's
``consent``. It raises a ``tool_call`` Yes card for that one start ("May I start <app>?") and the turn
waits for her answer, like a package tool's card; with no card, the tool says so in plain words.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

from . import owner_files, seams
from .zone3.bridge import SEARCH_PLACES
from .zone3.policy import SEARCH_HOSTS

logger = logging.getLogger(__name__)


# -- the wizard's three parts ---------------------------------------------------------------------
class WizardVault:
    """Lane L3's Archivist for this bot folder, in the shape the wizard engine uses (``wizard.services.Vault``)."""

    def __init__(self, state: Path, bus: Any = None) -> None:
        self.state, self.bus = Path(state), bus

    def _a(self) -> Any:
        a = seams.archivist(self.state, self.bus)
        if a is None:
            raise ValueError("The vault is not here, so this can't be done.")
        return a

    @staticmethod
    def _plain(exc: Exception) -> ValueError:
        return ValueError(getattr(exc, "message", "") or str(exc))

    def status(self) -> str:
        v = self._a().vault
        if not v.is_set_up():
            return "missing"
        return "open" if v.is_open() else "locked"

    def create(self, where: str, folder: str | None, pin: str) -> str:
        if where not in ("here", None, ""):
            raise ValueError("A vault in a folder of your choice is not built yet. Pick This computer for now.")
        from .zone1.vault import VaultError
        try:
            return self._a().setup(pin)
        except VaultError as exc:
            raise self._plain(exc) from None

    @staticmethod
    def check_pin(pin: str) -> str:
        """The vault's own PIN rule, before she types it again: the plain reason, or "" when it is fine."""
        from .zone1.pin_rules import validate_vault_secret
        ok, why = validate_vault_secret(pin)
        return "" if ok else why

    def open_existing(self, folder: str | None, pin: str) -> None:
        from .zone1.vault import VaultError
        if folder and Path(folder).resolve() != self.state.resolve():
            raise ValueError("Opening a vault from another bot folder is not built yet.")
        try:
            self._a().unlock("pin", pin)
        except VaultError as exc:
            raise self._plain(exc) from None

    def confirm_open(self, folder: str | None, pin: str) -> None:
        from .zone1.vault import VaultError, WrongSecret
        if folder and Path(folder).resolve() != self.state.resolve():
            raise ValueError("Opening a vault from another bot folder is not built yet.")
        try:
            matched = self._a().vault.verify_pin(pin)
        except VaultError as exc:
            raise self._plain(exc) from None
        if not matched:
            raise ValueError(WrongSecret.__doc__)

    def test_recovery(self, code: str) -> bool:
        from .zone1.vault import VaultError
        try:
            return self._a().vault.test_recovery(code) is True
        except VaultError as exc:                  # too many tries
            raise self._plain(exc) from None

    def rotate_recovery(self, pin: str, may_open: bool = False) -> str:
        """The vault's own rotate (the same as ``POST /v1/vault/recovery-code/rotate``), for setup's typed-back step
        (D106). A locked vault is refused unless *may_open*, for the vault this setup is finishing: then her usual
        unlock runs first, with its throttle. Never the safe-mode repair the vault also allows: that unlock raises
        ``SafeMode`` here, so the rotate is not reached."""
        from .wizard import lines as L
        from .zone1.vault import SafeMode, VaultError, WrongSecret
        vault = self._a().vault
        was_open = vault.is_open()
        if not was_open and not may_open:
            raise ValueError(L.NEW_CODE_LOCKED)
        try:
            if not was_open:
                vault.unlock("pin", pin)
            return vault.rotate_recovery(pin)
        except WrongSecret:
            raise ValueError(L.NEW_CODE_WRONG_PIN) from None
        except SafeMode:
            raise ValueError(L.NEW_CODE_SAFE_MODE) from None
        except VaultError as exc:
            raise self._plain(exc) from None
        except OSError:                            # the keyring is replaced whole, so the old one still stands
            opened_now = not was_open and vault.is_open()
            raise ValueError(L.NEW_CODE_NOT_SAVED_OPENED if opened_now else L.NEW_CODE_NOT_SAVED) from None

    def new_code_problem(self) -> str:
        """Why setup can't make a new code even with the PIN, in her words, or "" (open, or simply locked)."""
        from .wizard import lines as L
        from .zone1.vault import StoreMissing
        vault = self._a().vault
        seen = vault.status()
        state = seen["state"]
        if state == "not_set_up":
            return L.NEW_CODE_NO_VAULT
        if state == "safe_mode":
            return L.NEW_CODE_UNREADABLE if seen.get("why") == "unreadable" else L.NEW_CODE_SAFE_MODE
        if state == "missing" and not vault.is_open():
            return StoreMissing.__doc__
        return ""

    def put_secret(self, name: str, value: str) -> None:
        from .zone1.vault import VaultError
        try:
            self._a().put_secret(name, value)
        except VaultError as exc:
            raise self._plain(exc) from None

    def deposit(self, name: str, value: str) -> None:
        self._a().inbox.seal(name, value)


def search_card() -> dict:
    """The search Yes in her words: every place by name (the card lists every host under them)."""
    return {"title": "Let Toolsmith search while it builds?",
            "sentence": f"While Toolsmith builds, it may search {SEARCH_PLACES} for apps and knowledge, and read "
                        "project pages there. It sends search words like \"world news map\", nothing else.",
            "lines": ["HACS, the Hermes skills index and GitHub's skill collections are read whole, with no words "
                      "from you.",
                      "Only while a build you said Yes to runs. The rest of the time, nothing goes out.",
                      "You can turn it off on the Developer page at any time."]}


class WizardBridge:
    """Lane L4's Bridge posture (``wizard.services.Bridge``): ``search`` opens the Bridge with
    Toolsmith's search switch on and grants it exactly the search hosts; ``closed`` closes it and turns
    the switch off (off beats any grant).

    The grant (integration lane I2a): the switch alone lets nothing through (lane L4's ``decide`` needs a
    grant per host from a Yes record), so without it every search and README read of a real build was
    refused. Her choice on step 4 (its words name every place) is the Yes: one ``host`` record for
    ``toolsmith:search`` covering :data:`SEARCH_HOSTS` in the ``build`` phase, made once and answered
    ``by: owner (setup)``. Nothing else is covered: an app's own hosts are on its card.

    A bot's older search Yes named only GitHub's hosts. It stays good for those and is never widened: nothing
    makes the wider record but her own answer on step 4, or her Yes on the card :func:`search_card` words."""

    SEARCH = "toolsmith:search"
    #: GitHub (its search API, raw files and clones), then the other places. A fixed list: ``policy.SEARCH_HOSTS``.
    SEARCH_HOSTS = SEARCH_HOSTS

    def __init__(self, zone3: Any) -> None:
        self.zone3 = zone3

    def _grant_search(self) -> None:
        store = self.zone3.consents
        for rec in store.live("host"):
            if rec["scope"].get("target") == self.SEARCH and \
                    set((rec.get("covers") or {}).get("hosts") or []) >= set(self.SEARCH_HOSTS):
                return
        card = store.ask("host", self.SEARCH, {"hosts": list(self.SEARCH_HOSTS), "phase": "build",
                                               "card": search_card()})
        store.answer(card["consent_id"], "yes", "tap", by="owner (setup)", card_sha=card["card_sha"])

    def said(self) -> list[str]:
        """The Bridge as it is now, in her words (``zone3.bridge.plain``, the same as puffpai bridge status)."""
        registry = getattr(self.zone3, "registry", None)
        names = {r["id"]: r["name"] for r in registry.list()} if registry is not None else {}
        return list(self.zone3.bridge.view(names)["said"])

    def set_posture(self, posture: str) -> None:
        bridge = self.zone3.bridge
        if posture == "search":
            from .zone3.bridge import OpenRefused
            bridge.set_switch(self.SEARCH, True)
            try:
                got = bridge.open(by="owner")      # no PIN here: in safe mode this is refused
            except OpenRefused as exc:
                bridge.set_switch(self.SEARCH, False)
                raise ValueError(str(exc)) from None
            if got.get("state") != "open":
                raise ValueError("The Bridge could not open on this computer, so it stays closed.")
            self._grant_search()
        else:
            bridge.set_switch(self.SEARCH, False)
            bridge.close(by="owner")


class WizardModels:
    """Lane L2's model slot for this bot folder (``wizard.services.Models``); ``choose`` writes the one
    model setting (``zone2.settings``)."""

    def __init__(self, state: Path) -> None:
        self.dir = Path(state)          # not .state: that name is the method below

    def fit(self, census: dict) -> dict:
        from .zone2 import models
        return models.fit(census, state=self.dir)

    def menu(self, census: dict) -> list:
        return self.fit(census).get("options") or []

    def state(self, name: str) -> dict:
        from .zone2 import models
        return models.state(name, self.dir)

    def pull(self, name: str) -> None:
        from .zone2 import models
        rt = models.runtime(self.dir)
        threading.Thread(target=rt.pull, args=(name,), name=f"model-pull-{name}", daemon=True).start()

    def choose(self, name: str) -> None:
        from .zone2 import settings as model_settings
        model_settings.save(self.dir, model=name)

    def is_remote(self, name: str) -> bool:
        """Ollama's own ``/api/show`` says *name* is a hosted model (lane I17). A model not here yet can't be
        asked: False (its name was checked already)."""
        from .zone2 import models, roles
        try:
            return roles.is_remote(models.runtime(self.dir).show(name))
        except (OSError, ValueError):
            return False

    def find_runtime(self) -> str:
        """The scan: the Ollama running on this computer, kept as the model slot's address; "" when none answers."""
        from . import jsonfile
        from .zone2 import settings as model_settings
        picked = model_settings.load(self.dir).model or \
            str((jsonfile.read(model_settings.path(self.dir), {}) or {}).get("name") or "")
        found = model_settings.find_local_ollama(model=picked)     # the one that has her model, if she picked
        if found:
            model_settings.save(self.dir, address=found)
        return found


#: A pasted project that is not a GitHub project address (the wizard's plan step asks again with this).
NOT_GITHUB_LINE = ("That is not a GitHub project address. Paste one like https://github.com/owner/project, or "
                   "leave it empty to use the plan as it is.")


class WizardToolsmith:
    """Lane L6's Toolsmith host for this Core, in the shape the wizard engine uses (``wizard.services.Toolsmith``).

    - ``providers`` / ``choose``: step 5. Each installed ``zone3.toolsmith-model`` provider with its own
      consent sentence and its sha; ``choose`` is the host's own check (the sha she said yes to, a model
      of that company, a cost cap). Her key goes to the vault by the field's ``home`` (the engine).
    - ``interpret``: step 6, on the brain on THIS computer only (``toolsmith_host.interpret``); a draft
      build with her jobs.
    - ``plan``: step 7, the owner's four questions in Toolsmith's process (``ToolsmithHost.plan_build``), once
      per build; a pasted project makes a new draft with it first. The summary and lines are the plan
      card's own (what her Yes covers), plus the places it reaches while it installs.
    - ``yes``: step 8. The wizard showed exactly that card, so her Yes in the wizard answers it (with its
      ``card_sha``, ``by: owner``). The offers she ticked on it (lane I4b) are recorded by the Core first,
      from that same answer (``ToolsmithHost.use_offers``).
    - ``start`` / ``progress`` / ``answer_step`` / ``answer_card`` / ``results``: steps 9 and 10.
    """

    AGAIN = "Open this step again to let Toolsmith try again."

    def __init__(self, app: Any) -> None:
        self.app = app
        self._lock = threading.Lock()
        self._planning: dict[str, threading.Thread] = {}

    def host(self) -> Any:
        from .api.app import start_toolsmith
        got = start_toolsmith(self.app)
        if got is None:
            raise RuntimeError("Toolsmith is not part of this Core")
        return got

    @property
    def consent(self) -> Any:
        return self.app.extensions["puffpai"].consent

    # step 5
    def providers(self) -> list[dict]:
        from .zone3.toolsmith_host import sender
        return [sender.describe(p) for p in self.host().providers().values() if not sender.provider_problems(p)]

    def choose(self, choice: dict) -> dict:
        try:
            self.host().choose(dict(choice), by="owner")
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True}

    # step 6
    def interpret(self, goal: str, use_brain: bool) -> dict:
        from .zone3.toolsmith_host import interpret
        host = self.host()
        jobs, problem, left = interpret.jobs_for_goal(goal, host.ports.brain_json, use_brain)
        b = host.draft(goal, jobs, left_out=left)
        out: dict = {"build": b["id"], "jobs": [{"name": j["name"], "ask": j["sample_ask"]} for j in jobs]}
        if left:                                   # D84: what she asked for that is not a job, said plainly
            out.update(left_out=list(left), left_out_line=interpret.left_out_line(left))
        if problem:
            out.update(brain="error", reason=problem)
        return out

    # step 7
    def plan(self, build: str, project: str | None = None) -> dict:
        host = self.host()
        b = host.view(build)
        new = None
        if project:
            from .zone3.toolsmith.find import github_url
            project = github_url(project)
            if not project:                   # not a GitHub project address: nothing is planned again
                return {"summary": None, "items": [NOT_GITHUB_LINE]}
            jobs = [dict(j) for j in b.get("jobs_in") or []] or [{"name": b["goal"][:80], "does": b["goal"]}]
            jobs[0]["pasted"] = [project] + [u for u in jobs[0].get("pasted") or [] if u != project]
            new = host.draft(b["goal"], jobs, left_out=b.get("left_out"))["id"]
            host.replace(build, new)          # the old plan's card closes: a Yes there can't start a second build
            b = host.plan_build(new)
        elif not b.get("plan_sha") and not b.get("said") and not b.get("yes"):
            b = host.plan_build(build)
            self.write_persona(build)
        card = b.get("card") or {}
        if not card:
            said = b.get("said") or (b.get("consent") or {}).get("said") or "Toolsmith has no plan yet."
            out = {"summary": None, "items": [said, self.AGAIN] + self.persona_lines(b.get("goal"), "will be")}
            return dict(out, build=new) if new else out
        items = list(card.get("lines") or []) + self.persona_lines(b.get("goal"), "will be")
        hosts = (b.get("envelope") or {}).get("install_hosts") or []
        if hosts:
            items.append("While it installs, Toolsmith reaches only: " + ", ".join(hosts) + ".")
        reads = (b.get("knowledge_plan") or {}).get("hosts") or []
        if reads:
            items.append("While it builds, the Core reads knowledge only from: " + ", ".join(reads) + ".")
        if not (b.get("consent") or {}).get("id"):
            items.append((b.get("consent") or {}).get("said") or "This plan can't get its Yes yet.")
        out = {"summary": card.get("sentence"), "items": items}
        if new:
            out["build"] = new
        return out

    def plan_start(self, build: str) -> dict:
        """Plan in the background (the plan step shows what Toolsmith does now): ``{ready, now}``."""
        host = self.host()
        b = host.view(build)
        # A plan is kept a moment before its Yes card is made: the step waits for the card, or for why there is none.
        consent = b.get("consent") or {}
        if b.get("said") or b.get("yes") or (b.get("plan_sha") and (consent.get("id") or consent.get("said"))):
            return {"ready": True}
        with self._lock:
            th = self._planning.get(build)
            if th is None and b.get("plan_sha"):
                return {"ready": True}             # planned before this Core started: nothing here will add a card
            if th is not None and not th.is_alive():
                return {"ready": True}             # it ended: the plan step shows the plan or why not
            if th is None:
                th = self._planning[build] = threading.Thread(target=self._plan_and_persona, args=(build,),
                                                              name=f"plan-{build}", daemon=True)
                th.start()
        return {"ready": False, "now": str(b.get("now") or "")}

    def _plan_and_persona(self, build: str) -> None:
        """The plan step's background work: Toolsmith plans, then the brain writes who the bot is (lane I12)."""
        try:
            self.host().plan_build(build)
        finally:
            self.write_persona(build)

    # who the bot is (lane I12): written at the end of the plan step, shown on the plan card and at Done
    def write_persona(self, build: str, *, again: bool = False) -> dict:
        """Write the persona from this build's goal, unless one for the same goal is kept (``again``: always).
        Always on this computer's brain (``ToolsmithHost.persona_ask``; nothing is sent before her plan Yes, D66).
        Never raises: ``{"text": "", "said"}``."""
        from .zone2 import persona
        try:
            host = self.host()
            b = host.view(build)
            goal = str(b.get("goal") or "")
            if not goal.strip():
                return {"text": "", "said": "There is no goal yet. Answer the goal step first."}
            if not again and not persona.needs_writing(self.app.extensions["puffpai"].state, goal):
                return persona.load(self.app.extensions["puffpai"].state)
            # D84: written from the jobs as kept (name and what it does), never promising one left out.
            jobs = [": ".join(x for x in (str(j.get("name") or ""), str(j.get("does") or "")) if x)
                    for j in b.get("jobs_in") or [] if isinstance(j, dict)]
            return persona.write_from_goal(self.app.extensions["puffpai"].state, goal, build,
                                           lambda system, user: host.persona_ask(build, system, user), jobs,
                                           left_out=[str(x) for x in b.get("left_out") or []])
        except Exception as exc:  # noqa: BLE001 - never stops the plan: said plainly
            logger.info("persona not written for %s: %s", build, type(exc).__name__)
            return {"text": "", "said": persona.NOT_WRITTEN}

    def persona_lines(self, goal: Any = None, verb: str = "is") -> list[str]:
        """``Your bot will be: <role>.`` and what it knows, for the plan card and the Done step."""
        from .zone2 import persona
        state = self.app.extensions["puffpai"].state
        rec = persona.load(state)
        if not rec.get("text") or (goal is not None and rec.get("goal_sha") != persona.goal_sha(str(goal))):
            return [persona.NOT_WRITTEN] if goal and verb == "will be" else []
        seen = persona.view(state)
        if seen["in_use"] != "persona":
            return [f"Your bot {verb}: a plain helper. Its role was not used because {seen['flag']}. Change it "
                    "in Settings."]
        out = [f"Your bot {verb}: {seen['role']}." if seen["role"] else f"Your bot {verb} what Settings says."]
        if seen["knows"]:
            out.append("It can help with: " + "; ".join(seen["knows"]) + ".")
        if seen["first_question"]:
            out.append(f"It starts by asking: {seen['first_question']}")
        return out

    def done_lines(self) -> list[str]:
        """The Done step's lines on who the bot is (read or change it in Settings)."""
        lines = self.persona_lines()
        return lines + ["Read or change who your bot is in Settings."] if lines else []

    def again(self, build: str) -> str:
        """A plan that did not come: a new draft with the same goal and jobs, so the next open plans again."""
        b = self.host().view(build)
        return self.host().draft(b["goal"], b.get("jobs_in"), left_out=b.get("left_out"))["id"]

    # step 8
    def offers(self, build: str) -> list[dict]:
        """The plan card's offers she may tick: ``[{job, job_name, package, name, tool, line}]``."""
        b = self.host().view(build)
        card = b.get("card") or {}
        out = [dict(o) for o in card.get("offers") or [] if isinstance(o, dict)]
        # Lane I17: a coding or picture model she may tick, ``job`` "model:<role>" (what set_use takes).
        out += [{"job": m["value"], "job_name": "", "model": True, "name": m.get("label") or m.get("model"),
                 "line": m.get("line") or "", "package": "", "installed": bool(m.get("installed"))}
                for m in card.get("model_offers") or [] if isinstance(m, dict) and m.get("value")]
        return out

    def store_adds(self, build: str) -> list[dict]:
        """The PuffPAI store apps she ticked (fixed at her Yes) that are not installed: ``[{package, name}]``."""
        return list(self.host().store_adds(build))

    def yes(self, build: str, use: list | None = None) -> dict:
        from .consent import ConsentError
        host = self.host()
        b = host.view(build)
        cid = (b.get("consent") or {}).get("id")
        if not cid:
            return {"ok": False, "reason": (b.get("consent") or {}).get("said") or "There is no plan to say Yes to yet."}
        rec = self.consent.get(cid) or {}
        if rec.get("status") == "granted":
            return {"ok": True}
        if rec.get("status") != "pending":
            return {"ok": False, "reason": "That plan's Yes card is gone. Open the plan step again."}
        try:
            host.use_offers(build, cid, list(use or []), by="owner")       # her ticks, from her answer
        except (PermissionError, ValueError) as exc:
            return {"ok": False, "reason": str(exc)}
        try:
            self.consent.answer(cid, "yes", "tap", by="owner", card_sha=rec["card_sha"])
        except ConsentError as exc:
            try:
                host.use_offers(build, cid, [], by="owner")                # no Yes: no tick is kept
            except PermissionError:
                pass                                                   # not waiting any more: ticks never count
            return {"ok": False, "reason": str(exc)}
        return {"ok": True}

    # steps 9 and 10
    def start(self, build: str) -> dict:
        host = self.host()
        b = host.view(build)
        if b.get("yes"):
            return {"ok": True, "already": True}
        try:
            got = host.start(build, str((b.get("consent") or {}).get("id") or ""))
        except (PermissionError, KeyError) as exc:
            return {"ok": False, "reason": str(exc) or "The build did not start."}
        # Ticked store apps are added first; the Core starts the build when they are done (lane I16).
        return {"ok": True, "store": not (got or {}).get("yes")}

    def _cards(self, host: Any, build: str) -> list[dict]:
        from .consent import card_view
        out = []
        # A job's ONE hold card (apps Drawbridge couldn't fully check, lane I4b): she ticks which to try.
        holds = {h["consent_id"]: h["items"] for h in host.trials.hold_cards(build)}
        # An app's install card (not covered by the plan).
        ids = list(holds) + [rec.pending for rec in host.trials.of_build(build) if rec.pending and not rec.covered]
        # A PuffPAI store app ticked on the plan card: its own Yes card, raised by the Core before the build.
        shop = [str(a.get("card") or "") for a in (host.store_phase.record(build) or {}).get("apps") or []]
        shop += getattr(host, "store_cards", lambda _b: [])(build)        # D152: one the goal's conversation asked for after her Yes
        ids += [c for c in shop if c]
        for cid in [c for c in ids if c]:
            card = self.consent.get(cid) or {}
            if card.get("status") == "pending":
                seen = card_view(card.get("card") or {}, card.get("floor"))
                row = {"consent_id": cid, "title": seen["title"], "lines": seen["lines"], "floor": seen["floor"],
                       "kind": "hold" if cid in holds else "store" if cid in shop else "install"}   # lanes I4a, I16
                if cid in holds:
                    row["choices"] = [{"ref": i["ref"], "name": i["name"]} for i in holds[cid]]
                    row["yes_means"] = str((card.get("card") or {}).get("yes_means") or "")
                out.append(row)
        return out

    def progress(self, build: str) -> dict:
        """``{jobs: [{id, name, status, line, lines}], steps: [open steps], cards: [package cards], finished}``."""
        from .zone3.toolsmith_host.store import ACTIVE
        host = self.host()
        b = host.view(build)
        jobs = [{"id": j["id"], "name": j["name"], "status": j["status"], "line": j["run"].get("line") or "",
                 "lines": list(j["run"].get("lines") or []), "now": j["run"].get("now") or "",
                 "since": j["run"].get("since") or ""} for j in b["jobs"]]
        got = knowledge_view(b, host.running(build))
        if got is not None:
            jobs.append(got)                   # the knowledge fetch, shown as one more line of the build
        shop = host.store_phase.record(build)
        if shop:                               # the store apps ticked on the plan, added first (lane I16)
            jobs.insert(0, {"id": "store", "name": "PuffPAI store", "status": "building" if shop.get("state") in ("adding", "starting")
                            else "working", "line": "", "now": "",
                            "lines": [str(x) for x in shop.get("said") or []]})
        steps = [s for s in host.steps_of(build) if s.get("state") == "open"]
        busy = any(j["status"].split(":")[0] in ACTIVE + ("waiting_yes",) for j in jobs)
        stopped = bool(shop) and shop.get("state") == "done" and not b.get("yes")   # its start was refused
        return {"jobs": jobs, "steps": steps, "cards": self._cards(host, build),
                "finished": (bool(b.get("yes")) and not host.running(build) and not busy) or stopped}

    def answer_step(self, build: str, step_id: str, answer: str) -> str:
        return self.host().answer_step(build, step_id, answer)

    def answer_card(self, build: str, consent_id: str, yes: bool, picks: list | None = None) -> str:
        """Her answer to one of this build's cards. *picks*: on a hold card, the apps she ticked to try,
        recorded by the Core before her Yes. Only ticked apps are tried: a Yes with none ticked (or with no
        picks sent) tries none."""
        from .consent import ConsentError
        host = self.host()
        cards = {c["consent_id"]: c for c in self._cards(host, build)}
        if consent_id not in cards:
            return "That card is not one of this build's."
        ticked = False
        if yes and picks is not None and cards[consent_id].get("choices"):
            try:
                host.trials.tick_holds(build, consent_id, list(picks), by="owner")
                ticked = True
            except (PermissionError, ValueError) as exc:
                return str(exc)
        rec = self.consent.get(consent_id) or {}
        try:
            self.consent.answer(consent_id, "yes" if yes else "no", "tap", by="owner",
                                card_sha=rec.get("card_sha") if yes else None)
        except ConsentError as exc:
            if ticked:
                host.trials.clear_ticks(build, consent_id)    # no Yes went through: no tick is kept
            return str(exc)
        return ""

    def results(self, build: str) -> list[dict]:
        host = self.host()
        b = host.view(build)
        out = [job_result(j) for j in b["jobs"]]
        got = knowledge_view(b, host.running(build))
        if got is not None:
            # Not a job: its own line on the Prove and Done steps (``kind: knowledge``), never counted as one.
            out.append({"kind": "knowledge", "name": got["name"], "state": got["status"], "reason": got["line"],
                        "tried": []})
        return out


KNOWLEDGE_NAME = "Knowledge"
KNOWLEDGE_WAITING = "Knowledge is read after your Yes, while Toolsmith builds."
KNOWLEDGE_STOPPED = "Toolsmith stopped before it read the knowledge on your plan."


def knowledge_view(b: dict, running: bool) -> dict | None:
    """The build's knowledge fetch as one progress row (``{id, name, status, line, lines, now}``), or None when
    the plan reads none. Its last line says what was read and why a source failed (lane I11)."""
    kp = b.get("knowledge_plan") or {}
    if not kp.get("topics"):
        return None
    run = b.get("knowledge_run") if isinstance(b.get("knowledge_run"), dict) else {}
    state = run.get("state")
    counts = run.get("counts") or {}
    if state == "done":
        status = "working" if any(int(v or 0) for v in counts.values()) and not run.get("retry") else "not_yet"
        line = str(run.get("line") or "")
    elif b.get("yes") and (state == "fetching" or running):
        status, line = "building", ""
    elif b.get("yes"):
        status, line = "held", KNOWLEDGE_STOPPED
    else:
        status, line = "planned", KNOWLEDGE_WAITING
    return {"id": "knowledge", "name": KNOWLEDGE_NAME, "status": status, "line": line,
            "lines": [str(x) for x in run.get("lines") or []], "now": str(run.get("now") or ""), "kind": "knowledge"}


def job_result(j: dict) -> dict:
    """One job's end for the Prove and Done steps and chat: its state, its line, and each app it tried."""
    tried = [{"name": str(t.get("name")), "why": str(t.get("why") or "")}
             for t in (j.get("run") or {}).get("tried") or [] if isinstance(t, dict) and t.get("name")]
    return {"name": j["name"], "state": j["status"], "reason": (j.get("run") or {}).get("line") or "", "tried": tried,
            **({"library": True} if j.get("library") else {})}     # D123: no app, only some reading


# -- every package tool call from chat (09 section 3, X27) ----------------------------------------
#: Why a tool_call card asks, in plain words, by Drawbridge's reason code.
ASK_WHY = {
    "needs_yes": "{tool} changes things and was not on {app}'s install card as a change it makes without "
                 "asking, so it asks each time.",
    "unclassified": "Drawbridge could not tell whether {tool} changes things, so it asks each time.",
    "outside_scope": "This goes outside what you approved for {app}.",
}
SAID_NO = "You said No, so {app}'s {tool} did not run. Nothing was changed."
EXPIRED = "The Yes card for {app}'s {tool} got no answer in time, so it did not run. Nothing was changed."
NO_CARD = "{app}'s {tool} needs your Yes, and no Yes card can be shown right now, so it did not run."
CARD_WAIT_S = 10 * 60 + 5                           # the store's card life (CARD_TTL), and a little more


def _plain_tool(tool: str) -> str:
    return tool.replace("_", " ")


def _manifest_of(core: Any, package: str) -> tuple[dict | None, str]:
    """The manifest and origin Drawbridge's scope reads: the Core's one reader (her Yes, by its sha256), or for an
    outside tool server she added (D137, ``outside-<id>``) the one its record makes. Never a file it names."""
    manifest = core.steward.read_manifest(package)
    if manifest is not None:
        return manifest, (core.registry.get(package) or {}).get("origin") or "local"
    from . import tool_servers
    return tool_servers.manifest_for(core.state, package), tool_servers.ORIGIN


def _see(core: Any, drawbridge: Any, package: str, row: dict, entry: dict | None) -> None:
    if entry is None:
        return
    manifest, origin = _manifest_of(core, package)
    if manifest is None:
        return
    try:
        drawbridge.see_tools(package, manifest, [entry], origin)
    except Exception as exc:  # noqa: BLE001 - not seen: the tool stays as classified (unknown: it asks)
        logger.info("live tools of %s not classified: %s", package, type(exc).__name__)


def tool_review(core: Any, drawbridge: Any, *, wait_s: float = CARD_WAIT_S):
    """The Brain's ``review(call) -> verdict``: Drawbridge's ``review_action`` for EVERY package tool call.

    *call* is ``{package, name, args (locked form), summary, entry, notify}`` (``zone2.brain``). The
    verdict is ``{run, changing, asked, line, end, review_id}``:
    - inside her approved scope (or a read-only tool): it runs, no card (Drawbridge logs it);
    - ``ask``: a ``tool_call`` Yes card for THIS one call (the package, the tool, the gated summary of
      its arguments, why; floor-level, so the PIN, when Drawbridge says so, D29). The call waits for her
      answer; a Yes runs it once, a No or an expired card ends the turn with a plain line;
    - ``deny`` or a broken check: it does not run, and the turn ends with Drawbridge's line.
    ``changing`` is Drawbridge's own classification, so the reply's ``changed`` lists every tool that
    changed something."""
    def review(call: dict) -> dict:
        package = str(call.get("package") or "")
        name = str(call.get("name") or "")
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        row = core.registry.get(package) or {}
        outside = None if row else _outside_record(core, package)      # a tool server she added (D137)
        app = str(row.get("name") or (outside or {}).get("name") or package)
        words = {"app": app, "tool": name}
        entry = call.get("entry") if isinstance(call.get("entry"), dict) else None
        _see(core, drawbridge, package, row, entry)
        labels = call.get("labels") or ()                 # a file she hands over (D127): what it holds, by kind
        verdict = drawbridge.review_action(package, name, args, labels=labels, caller="concierge")
        if verdict.get("reason_code") == "unclassified" or (outside and verdict.get("reason_code") == "not_installed"):
            refresh_scope(core, package)              # the tools were read after the install: once more
            _see(core, drawbridge, package, row, entry)
            verdict = drawbridge.review_action(package, name, args, labels=labels, caller="concierge")
        word = verdict.get("verdict")
        out = {"changing": verdict.get("changing") is not False, "review_id": verdict.get("review_id"),
               "asked": False}
        if word not in ("allow", "ask"):
            return dict(out, run=False, end=True,
                        line=str(verdict.get("line") or "Drawbridge said no, so nothing was changed."))
        # A package that changes her own files (02 needs.owner_files, D136): this only ever adds a refusal or a Yes.
        mine = _her_file(core, package, name, args)
        if mine and mine.get("line"):
            return dict(out, run=False, end=True, line=mine["line"])
        if word == "allow" and not mine:
            return dict(out, run=True)
        return dict(out, **_ask(core, package, name, app, args, call, verdict, words, wait_s, mine))
    return review


def _her_file(core: Any, package: str, name: str, args: dict) -> dict | None:
    if not owner_files.names_a_file(args):
        return None
    try:
        return owner_files.question(core.state, core.steward.read_manifest(package), name, args)
    except Exception as exc:  # noqa: BLE001 - not checked: it does not run
        logger.warning("owner file not checked: %s", type(exc).__name__)
        return {"line": owner_files.OUTSIDE}


def _outside_record(core: Any, package: str) -> dict | None:
    from . import tool_servers
    if not package.startswith(tool_servers.PREFIX):
        return None
    return tool_servers.rows(core.state).get(package[len(tool_servers.PREFIX):])


def _ask(core: Any, package: str, name: str, app: str, args: dict, call: dict, verdict: dict, words: dict,
         wait_s: float, mine: dict | None = None) -> dict:
    """One ``tool_call`` Yes card for this call, and her answer (the turn waits for it)."""
    from .anchor import sha
    from .consent import card_sha
    store = getattr(core, "consent", None)
    summary = [str(x) for x in call.get("summary") or [] if isinstance(x, str)][:12] or ["(no details)"]
    why = ASK_WHY.get(str(verdict.get("reason_code")), "") or str(verdict.get("line") or "")
    mine = mine or {}
    card = {"title": mine.get("title") or f"Let {app} {_plain_tool(name)}?",
            "what": mine.get("what") or "One change from chat, this time only.",
            "package": package, "package_name": app, "tool": name, "arguments": summary,
            "why": f"{app} asks before it changes this file." if mine else why.format(**words),
            "review_id": str(verdict.get("review_id") or ""),
            "args_sha": sha(args)}
    card = _said_by(card, call.get("asked_by"))
    floor = verdict.get("floor") is True
    try:
        if store is None:
            raise RuntimeError("no consent store")
        cid = store.request({"kind": "tool_call", "target": f"{package}.{name}"}, card, floor)
    except Exception as exc:  # noqa: BLE001 - no card means no Yes: it does not run
        logger.warning("tool_call card not raised: %s", type(exc).__name__)
        return {"run": False, "end": True, "line": NO_CARD.format(**words)}
    notify = call.get("notify")
    if callable(notify):
        try:
            notify({"consent_id": cid, "card": card, "card_sha": card_sha(card), "floor": floor})
        except Exception:  # noqa: BLE001 - the card is in the store; the Desktop shows it from there
            pass
    rec = store.wait(cid, timeout_s=wait_s)
    if rec is not None and rec.get("status") == "pending":
        store.expire(cid, EXPIRED.format(**words))
        rec = store.get(cid)
    status = (rec or {}).get("status")
    if status == "granted" and store.granted(cid) is not None and rec.get("card_sha") == card_sha(card) \
            and (rec.get("card") or {}).get("args_sha") == sha(args):
        return {"run": True, "asked": True}       # her Yes, for exactly this call, once
    if status == "declined":
        return {"run": False, "asked": True, "end": True, "line": SAID_NO.format(**words)}
    return {"run": False, "asked": True, "end": True, "line": EXPIRED.format(**words)}


def refresh_scope(core: Any, package_id: str) -> None:
    manifest, origin = _manifest_of(core, package_id)
    if manifest is None:
        return                                    # no scope: every change asks (Drawbridge's rule)
    try:
        core.packages.set_scope(package_id, manifest, origin)
    except Exception as exc:  # noqa: BLE001 - no scope: every change asks (Drawbridge's rule)
        logger.info("scope for %s not set: %s", package_id, type(exc).__name__)


# -- create_app ---------------------------------------------------------------------------------
NO_PROXY_URL = "http://127.0.0.1:9"           # nothing listens there: no proxy means no way out


def proxy_env(zone3: Any, package_id: str, phase: str = "run") -> dict[str, str]:
    """A fresh proxy credential for this package when the Core's proxy runs; else an address where
    nothing listens, so a credential never goes to another program on 8421 and nothing gets out."""
    if zone3.proxy is None:
        from .zone3.proxy import proxy_env as env_of
        return env_of(NO_PROXY_URL)
    return zone3.package_env(package_id, phase=phase)



def wire(app: Any) -> None:
    core = app.extensions["puffpai"]
    zone3 = app.extensions.get("puffpai.zone3")
    core.zone3 = zone3
    core.caretaker.on_card.append(lambda card: route_card(core, card))
    core.caretaker.safe_steps.extend(safe_steps(core, app))
    app.extensions["puffpai.core_question"] = core_question(core)
    app.extensions["puffpai.ask_toolsmith"] = chat_toolsmith(core, app)
    if app.extensions.get("puffpai.pin_check") is None:
        app.extensions["puffpai.pin_check"] = seams.pin_check(core.state, core.bus)
    core.vault = WizardVault(core.state, core.bus)
    core.models = WizardModels(core.state)
    core.toolsmith = WizardToolsmith(app)
    from .zone1.archivist import set_hold
    set_hold(core.state, vault_hold(core, app))
    if zone3 is None:
        return                                        # no Zone 3: nothing installs, nothing leaves (X26)
    if getattr(zone3, "bridge", None) is not None:
        zone3.bridge.open_guard = bridge_guard(core, app)   # safe mode: opening the Bridge needs the PIN
    core.packages.drawbridge = zone3.drawbridge
    if getattr(zone3, "bridge", None) is not None:
        core.packages.on_install_end = zone3.bridge.end_install   # install grants close when the install ends
        core.packages.drop_address_grant = zone3.bridge.drop_consent   # a removed package's address grants go (D110)
    core.steward.proxy_env = lambda id, phase="run": proxy_env(zone3, id, phase)
    core.steward.docker_wall_kw = lambda id: docker_wall_kw(zone3)
    core.bridge = WizardBridge(zone3)
    app.extensions["puffpai.tool_review"] = tool_review(core, zone3.drawbridge)


#: How often the vault's hold reads ``wizard.json`` at most. On Windows a reader with the file open makes the wizard's
#: ``os.replace`` fail, so the file is read as little as can be (D153).
SETUP_READ_S = 5.0


def vault_hold(core: Any, app: Any, now: Any = time.monotonic):
    """What the vault asks when it decides its idle timer (D153): a live build or unfinished setup. Never in safe mode
    or on a paused or stopping host. It runs with the vault's lock held, so it waits on no lock
    (:meth:`ToolsmithHost.holds_vault`), and a failure in it holds nothing (``Vault._held``).

    Setup counts while ``wizard.json`` says plainly ``complete: false``: no file, or one with anything else in it, holds
    nothing, and a finished bot running a step again stays complete. The file is read only when no live build already
    answers, at most once per :data:`SETUP_READ_S`, and never again in this Core's run once it says complete, since
    nothing sets that back. A read that fails (a Windows sharing clash) keeps the last answer."""
    from . import jsonfile
    progress = Path(core.state) / "settings" / "wizard.json"
    seen: dict[str, Any] = {"complete": False, "unfinished": False, "at": None}

    def setup_unfinished() -> bool:
        if seen["complete"]:
            return False
        at = now()
        if seen["at"] is not None and at - seen["at"] < SETUP_READ_S:
            return seen["unfinished"]
        seen["at"] = at
        try:
            wizard = jsonfile.read(progress, {})
        except OSError:
            return seen["unfinished"]
        complete = wizard.get("complete") if isinstance(wizard, dict) else None
        seen["complete"], seen["unfinished"] = complete is True, complete is False
        return seen["unfinished"]

    def hold() -> bool:
        if core.caretaker.in_safe_mode:
            return False
        host = app.extensions.get("puffpai_toolsmith")
        if host is not None:
            if host.halted():
                return False
            if host.holds_vault() is True:
                return True
        return setup_unfinished()
    return hold


def docker_wall_kw(zone3: Any) -> dict:
    """The Docker wall's containers reach the Core's own proxy port (lane L4's ``host_proxy_port``). With no
    proxy running, a Docker app does not start: its traffic would have no way out but a stranger's port.
    Called at each Docker app's start: on plain Linux Docker it also brings up the proxy's second listener."""
    if zone3.proxy is None:
        raise RuntimeError("the Bridge proxy is not running")
    from .zone3.docker_wall import wall_kw
    return wall_kw(zone3.proxy)


# -- cards: to the Desktop notices and the terminal ------------------------------------------------
CARD_TITLES = {
    "exposed": "An app was stopped", "listen_unknown": "An app could not be checked",
    "safe_mode": "BuildBot is in safe mode",
    "proxy_down": "The Bridge proxy is not running", "bridge_damaged": "Your Bridge settings were changed",
    "ledger_damaged": "The record of what left was changed", "audit_damaged": "The audit log was changed",
    "vault_mismatch": "The Bridge does not match your vault", "bypass": "An app went around the Bridge",
    "watch_not_running": "The Bridge watch is not running", "watch_blind": "BuildBot can't watch an app",
    "no_machine_key": "The Bridge has no machine key", "grant_refused": "A Yes was not used",
    "tool_words": "An app's tools are described in words aimed at an AI",
}
#: The Bridge's tamper cards: each puts the Core in safe mode (see the module docstring).
TAMPER_CARDS = ("bridge_damaged", "ledger_damaged", "audit_damaged")
#: Zone 3 cards another card already says: Caretaker raises the safe-mode card itself, and a change to
#: the Core's own Python (HookGuard) calls safe mode, whose card says it.
SAID_ELSEWHERE = ("safe_mode", "core_hook_changed")


def _say(line: str) -> None:
    """The terminal that runs ``puffpai serve`` (the CLI) shows every card too."""
    try:
        print(line, flush=True)
    except (OSError, ValueError):  # no console (a service): the notice is still there
        pass


def route_card(core: Any, card: dict) -> None:
    """One card to lane L5's notices (the Desktop) and to the terminal."""
    kind = str(card.get("kind") or "")
    title = CARD_TITLES.get(kind, "BuildBot needs you")
    line = str(card.get("line") or "")
    _say(f"{title}: {line}")
    notices = getattr(core, "notices", None)
    if notices is None:
        return
    try:
        notices.post("core", {"title": title, "text": line[:600], "urgency": "high"})
    except Exception as exc:  # noqa: BLE001 - the card is still on its list
        logger.warning("card not posted as a notice: %s", type(exc).__name__)


def route_zone3_card(core: Any, card: dict) -> None:
    kind = str(card.get("kind") or "")
    if kind in SAID_ELSEWHERE:
        return
    route_card(core, card)
    if kind in TAMPER_CARDS:
        core.caretaker.safe_mode(str(card.get("line") or kind))


# -- safe mode: what entering it does ---------------------------------------------------------------
def safe_steps(core: Any, app: Any) -> list:
    """Caretaker's ``safe_steps``: close the Bridge, lock the vault, pause Toolsmith. Each says what it did.
    The steps are Caretaker's shared ``safe_mode_steps`` (the one fallback in Toolsmith's ports runs the same)."""
    from .caretaker.caretaker import safe_mode_steps
    ct = core.caretaker
    return safe_mode_steps(zone3=lambda: getattr(core, "zone3", None), state=core.state, bus=core.bus,
                           toolsmith=lambda: app.extensions.get("puffpai_toolsmith"),
                           reason=lambda: " ".join(ct.safe_reasons))


#: Opening the Bridge in safe mode (orchestrator decision, I2b): a floor-level action.
SAFE_OPEN_LINE = ("BuildBot is in safe mode: {why} Opening the Bridge now needs your PIN. "
                  "Safe mode itself lasts until you restart BuildBot.")


def bridge_guard(core: Any, app: Any):
    """The Bridge's ``open_guard(pin) -> None | (status, line, reasons)`` (lane L4's Bridge calls it first).

    Not in safe mode: None (opening is the owner's everyday choice). In safe mode: the PIN (D29, the one
    PIN check, ``app.extensions["puffpai.pin_check"]``) or a refusal that says the safe-mode reason first.
    A guard that can't tell, or no PIN check, is a refusal (fail closed)."""
    def guard(pin: Any) -> tuple[int, str, list[str]] | None:
        try:
            reasons = list(core.caretaker.safe_reasons)
        except Exception:  # noqa: BLE001 - can't tell: treat it as safe mode
            reasons = ["BuildBot could not tell whether it is in safe mode."]
        if not reasons:
            return None
        why = " ".join(r if r.endswith(".") else r + "." for r in reasons)
        line = SAFE_OPEN_LINE.format(why=why)
        if not isinstance(pin, str) or not pin:
            return 403, line, reasons
        check = app.extensions.get("puffpai.pin_check")
        if check is None:
            return 503, line + " The PIN check is not here, so the Bridge stays closed.", reasons
        try:
            refused = check(pin)
        except Exception:  # noqa: BLE001 - a crashing check is a no
            refused = (403, "The PIN check failed.")
        if refused:
            return int(refused[0]), f"{refused[1]} {line}", reasons
        return None
    return guard


# -- a core tool's own question (read_app on a stopped app) ----------------------------------------
START_WHAT = "Start {app} so I can read its page. It runs on this computer, bound to 127.0.0.1."


#: A Core tool in chat that changes the bot itself (D137: add_tool_server, remove_tool_server) asks with its own card.
CORE_CHANGE = "core_change"
CHANGE_EXPIRED = "This Yes card got no answer in time, so nothing was changed."


def _core_change_card(question: dict) -> tuple[dict, str]:
    tool = str(question.get("tool") or "core")
    said = [str(x)[:200] for x in question.get("arguments") or [] if isinstance(x, str)][:6] or ["(no details)"]
    return ({"title": str(question.get("title") or f"May I {tool.replace('_', ' ')}?")[:200],
             "what": str(question.get("what") or "One change to your bot, from chat, this time only.")[:300],
             "package": "core", "package_name": "BuildBot", "tool": tool, "arguments": said,
             "why": str(question.get("why") or "")[:300]}, f"core.{tool}")


COMMAND_EXPIRED = "The Yes card for that command got no answer in time, so it did not run."
OPEN_EXPIRED = "The Yes card to open that file got no answer in time, so it was not opened."


#: One plain line on a Yes card a program key's turn raises (D147), by the label she gave the key.
ASKED_BY_KEY = "A program asked for this, using your key \"{label}\". It did not come from your own chat."


def _said_by(card: dict, label: Any) -> dict:
    """*card* with the line that says which key asked for it, part of what her Yes covers. No label: *card* as it is."""
    if not label:
        return card
    from .zone2.commands import visible
    return {**card, "what": f"{card.get('what') or ''} {ASKED_BY_KEY.format(label=visible(str(label)))}".strip()}


def _one_card(store: Any, target: str, card: dict, expired: str, wait_s: float, notify: Any = None) -> bool | None:
    """One ``tool_call`` Yes card, and her answer (the turn waits). True only for a Yes on exactly this card."""
    from .consent import card_sha
    try:
        cid = store.request({"kind": "tool_call", "target": target}, card, False)
    except Exception as exc:  # noqa: BLE001 - no card, no Yes
        logger.warning("core tool card not raised: %s", type(exc).__name__)
        return None
    if callable(notify):
        try:
            notify({"consent_id": cid, "card": card, "card_sha": card_sha(card), "floor": False})
        except Exception:  # noqa: BLE001 - the card is in the store; Your turn shows it from there
            pass
    rec = store.wait(cid, timeout_s=wait_s)
    if rec is not None and rec.get("status") == "pending":
        store.expire(cid, expired)
        rec = store.get(cid)
    status = (rec or {}).get("status")
    if status == "granted" and store.granted(cid) is not None and rec.get("card_sha") == card_sha(card):
        return True
    if status == "declined":
        return False
    return None


def core_question(core: Any, *, wait_s: float = CARD_WAIT_S):
    """The Brain's ``consent(question) -> True | False | None`` for a core tool's own question: ``read_app``
    starting a stopped app, and ``run_command`` (D138). One ``tool_call`` Yes card for that one start or that one
    command; the turn waits for her answer. None when no card can be raised or it got no answer (the tool says so
    plainly)."""
    def ask(question: dict) -> bool | None:
        store = getattr(core, "consent", None)
        if store is None:
            return None
        by = question.get("asked_by")
        if question.get("kind") == "command":
            from .zone2 import commands
            if not str(question.get("command") or "").strip():
                return None
            return _one_card(store, f"concierge.{commands.TOOL}", _said_by(commands.card_for(question), by), COMMAND_EXPIRED,
                             wait_s, question.get("notify"))
        if question.get("kind") == "open_program":
            from .zone2 import programs
            return _one_card(store, f"concierge.{programs.OPEN_TOOL}", _said_by(programs.open_card(question), by), OPEN_EXPIRED,
                             wait_s, question.get("notify"))
        package = str(question.get("package") or "")
        if question.get("kind") == CORE_CHANGE:
            card, target = _core_change_card(question)
            return _one_card(store, target, _said_by(card, by), CHANGE_EXPIRED, wait_s)
        if not package or question.get("kind") != "start":
            return None                               # only these ask here; anything else gets no Yes
        row = core.registry.get(package) or {}
        app = str(row.get("name") or package)
        card = {"title": str(question.get("title") or f"May I start {app}?"),
                "what": START_WHAT.format(app=app), "package": package, "package_name": app,
                "tool": str(question.get("tool") or "read_app"), "arguments": [f"start: {app}"],
                "why": "It is stopped, and reading its page needs it running."}
        return _one_card(store, f"{package}.start", _said_by(card, by),
                         f"The Yes card to start {app} got no answer in time, so it was not started.", wait_s)
    return ask


# -- ask_toolsmith from chat (03 Part 5 "Chat") ------------------------------------------------------
_GITHUB_IN = re.compile(r"(?i)\b(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]{1,100})/([A-Za-z0-9_.-]{1,100})",
                        re.ASCII)              # look-alike letters never read as github.com (lane I8a)
_ASKED_FOR = re.compile(r"(?i)^\s*(?:please\s+)?(?:can you\s+)?(?:add|install|get|find|make)\s+(?:me\s+)?"
                        r"(?:an?\s+|the\s+)?(?:(?:app|tool|program)\b)?\s*(?:(?:for|to|that)\b)?\s*")
NO_TOOLSMITH_LINE = "Toolsmith is not part of this Core, so nothing was looked for."
CHAT_SAID_NO = "You said No, so Toolsmith added nothing."
CHAT_EXPIRED = "The plan's Yes card got no answer in time, so Toolsmith added nothing. Ask again when you are ready."
CHAT_NOTHING = "Toolsmith found nothing it can install for that, so there is nothing to say Yes to."
CHAT_BUILDING = ("Toolsmith is building it now: {plan} When it is done, the result is said here in this chat "
                 "and shown as a notice.")
# D77: a new ask while a build she started in this chat is still open joins that build.
CHAT_ADDED = ("Added to the build that is running: {name} ({now}). Toolsmith finishes it first, then plans what "
              "you added. If that needs anything new, it gets its own Yes card; if the build already covers it, "
              "I say so here.")
CHAT_ADDED_TO_PLAN = "Added to the plan that was waiting for your Yes: {name}. It is one plan with one Yes now."
CHAT_FIRST = "Started a new build first, as you asked. The other build stays as it is: {name}."
CHAT_REPLACED = "This plan was replaced by a newer one with what you added, so say Yes on that card instead."
CHAT_NEXT = "Next, Toolsmith plans what you added while it ran: {words}"
CHAT_FOLLOW_CARD = "Toolsmith made a plan for what you added. Say Yes or No on its card in Your turn."
CHAT_ADDS_KEPT = 5                                 # her added asks kept on one build, at most
_NOW_WORDS = {"planned": "about to start", "waiting_yes": "waiting for your Yes", "building": "building",
              "waiting_you": "waiting for you", "waiting_vault": "waiting for your vault"}
WATCH_EVERY_S = 5.0
WATCH_MAX_S = 6 * 3600.0


def chat_job(text: str) -> dict:
    """One job from her words in chat: a GitHub address goes first as her pasted project (03 3.2 rule 6)."""
    text = " ".join(str(text or "").split())
    m = _GITHUB_IN.search(text)
    if m:
        repo = re.sub(r"\.git$", "", m.group(2).rstrip(".,;:!?)"))
        return {"name": repo[:80], "does": text[:300], "pasted": [f"https://github.com/{m.group(1)}/{repo}"]}
    words = _ASKED_FOR.sub("", text).strip(" .!?") or text
    from .zone3.toolsmith.names import subject_name
    # D85: the name ends where the subject ends ("that", "which", "for", "with"), never mid-phrase; what it
    # does is her words without "add a" (so a fits ask reads "an app for recipe box that ...").
    name = subject_name(words) or "My app"
    return {"name": name[:1].upper() + name[1:], "does": words[:300]}


def chat_toolsmith(core: Any, app: Any, *, wait_s: float = CARD_WAIT_S, every_s: float = WATCH_EVERY_S):
    """The Brain's ``toolsmith(job, ctx)`` for ``ask_toolsmith``: a one-job plan, its ONE Yes card in chat, the
    build after her Yes, and the result in the same conversation (03 Part 5 "Chat").

    The same host calls as the owner's routes: ``plan`` (``POST /v1/toolsmith/plan``) and ``start`` with the
    plan's consent id (``POST /v1/toolsmith/builds/{id}/start``, which checks her Yes is for exactly this plan).
    The card is the plan's own card in the consent store, so the Desktop shows and answers it as any other; the
    chat turn waits for her answer like a ``tool_call`` card.

    D77: while a build she started in this conversation is still open, a new ask joins it instead of starting a
    second build. Waiting for her Yes: one new plan with its jobs and the new one takes its place (``replace``),
    so there is still one card. Running: her words are kept on the build, and when it ends they are planned
    (with its goal) on a plan of their own, whose Yes card shows only if something new is needed. ``ctx["first"]``
    (the brain's ``first``, only when she says this comes first) starts a new build as before; the other stays."""
    def ask(job_text: str, ctx: dict) -> dict:
        from .api.app import start_toolsmith
        host = start_toolsmith(app)
        if host is None:
            return {"ok": False, "error": NO_TOOLSMITH_LINE}
        ctx = ctx if isinstance(ctx, dict) else {}
        conv = str(ctx.get("conversation") or "main")
        try:
            open_b = _chat_open(host, conv)
        except Exception:  # noqa: BLE001 - can't tell: a new build, as before
            logger.exception("open chat builds not read")
            open_b = None
        goal, jobs, replaces, lead = job_text, [chat_job(job_text)], None, ""
        if open_b is not None:
            name = _build_name(open_b)
            if ctx.get("first") is True:
                lead = CHAT_FIRST.format(name=name)
            elif open_b.get("yes") or open_b.get("store_adds"):
                if _add_to_running(host, open_b["id"], job_text):
                    return {"ok": True, "build": open_b["id"], "joined": True,
                            "said": CHAT_ADDED.format(name=name, now=_now_of(open_b))}
            else:
                replaces, lead = open_b, CHAT_ADDED_TO_PLAN.format(name=name)
                goal = f"{str(open_b.get('goal') or '').rstrip(' .')}. {job_text}"
                jobs = [dict(j) for j in open_b.get("jobs_in") or [] if isinstance(j, dict)] + jobs
        return go(host, goal, jobs, ctx, conv, replaces, lead)

    def follow(host: Any, done: dict, adds: list[str], ctx: dict, conv: str) -> None:
        """D77: what she added while a chat build ran, planned once it ended (her Yes only if something new)."""
        words = " ".join(" ".join(str(a).split()) for a in adds)[:300]
        goal = f"{str(done.get('goal') or '').rstrip(' .')}. {words}"
        job = chat_job(words)
        job["does"] = goal[:300]
        say = ctx.get("say_later")
        notices = getattr(core, "notices", None)

        def tell(text: str) -> None:
            if callable(say):
                try:
                    say(text)
                except Exception:  # noqa: BLE001 - the notice still says it
                    pass
            if notices is not None:
                try:
                    notices.post("toolsmith", {"title": "Toolsmith", "text": text[:600]})
                except Exception:  # noqa: BLE001
                    pass

        tell(CHAT_NEXT.format(words=words))
        got = go(host, goal, [job], {"conversation": conv, "say_later": say,
                                     "notify": lambda _card: tell(CHAT_FOLLOW_CARD)}, conv)
        tell(got.get("said") if got.get("ok") else str(got.get("error") or ""))

    def go(host: Any, goal: str, jobs: list[dict], ctx: dict, conv: str, replaces: dict | None = None,
           lead: str = "") -> dict:
        from .consent import card_sha
        try:
            b = host.draft(goal, jobs)
            host.store.change(b["id"], lambda x: x.update(chat={"conversation": conv, "adds": [], "closed": False}))
            b = host.plan_build(b["id"])
        except Exception as exc:  # noqa: BLE001 - safe mode and friends carry a plain line
            return {"ok": False, "error": str(exc) or "Toolsmith could not make a plan this time."}
        if replaces is not None and b.get("plan_sha"):
            host.replace(replaces["id"], b["id"])         # one plan, one card: the waiting one closes (D77)
        jobs = b.get("jobs") or []
        # Knowledge topics (lane I11) and a PuffPAI store app (lane I10c) are covered by the plan's Yes too, so
        # their card stays open, as on the wizard's path (lane I14). Only an installed app's offer is closed:
        # chat has no tick for it, so the line says how to use it instead.
        topics = ((b.get("knowledge_plan") or {}).get("topics") or [])
        # D121, D143, D154: a job with no app on its plan still looks further after her Yes, so its card stays open.
        looks = ((b.get("model") or {}).get("where") == "cloud" or b.get("looks_after_yes")) and any(not j["plan"].get("reuse") and not j.get("pasted") for j in jobs)
        if jobs and not topics and not looks and not any(j["plan"].get("reuse") or j["plan"].get("candidates")
                                                         or j["plan"].get("store") for j in jobs):
            from .zone3.toolsmith.find import nothing_line, pasted_any
            left = str((b.get("consent") or {}).get("id") or "")
            if left:                                   # only an offer: chat has no tick, so no card is left open
                core.consent.expire(left, "Toolsmith found nothing to install, so this card is closed.")
            offers = [o for o in (b.get("card") or {}).get("offers") or [] if isinstance(o, dict)]
            hint = " ".join(f"{o.get('name')} may already do this: ask me to use it, for example "
                            f"\"{(j['plan'].get('offer') or {}).get('ask') or ''}\"."
                            for o in offers for j in jobs if j.get("n") == o.get("job"))
            return {"ok": False, "error": " ".join(x for x in (CHAT_NOTHING, " ".join(nothing_line(j, pasted_any(jobs)) for j in jobs),
                                                               hint) if x)}
        cid = str((b.get("consent") or {}).get("id") or "")
        if not cid:
            return {"ok": False, "error": b.get("said") or (b.get("consent") or {}).get("said")
                    or "Toolsmith could not make a plan this time."}
        store = core.consent
        rec = store.get(cid) or {}
        notify = ctx.get("notify") if isinstance(ctx, dict) else None
        if callable(notify):
            try:
                notify({"consent_id": cid, "card": rec.get("card"), "card_sha": rec.get("card_sha") or
                        card_sha(rec.get("card") or {}), "floor": bool(rec.get("floor"))})
            except Exception:  # noqa: BLE001 - the card is in the store; the Desktop shows it from there
                pass
        rec = store.wait(cid, timeout_s=wait_s)
        if rec is not None and rec.get("status") == "pending":
            store.expire(cid, CHAT_EXPIRED)
            rec = store.get(cid)
        status = (rec or {}).get("status")
        if (host.store.read(b["id"]) or {}).get("replaced_by"):
            return {"ok": False, "error": CHAT_REPLACED}   # she added to it meanwhile (D77)
        if status == "declined":
            return {"ok": False, "error": CHAT_SAID_NO}
        if status != "granted":
            return {"ok": False, "error": CHAT_EXPIRED}
        try:
            host.start(b["id"], cid)
        except Exception as exc:  # noqa: BLE001 - a Yes for another plan, safe mode: the plain line
            return {"ok": False, "error": str(exc) or "The build did not start."}
        _watch_build(core, host, b["id"], ctx.get("say_later"), every_s,
                     then=lambda done, adds: follow(host, done, adds, ctx, conv))
        plan = " ".join(str(x) for x in (b.get("card") or {}).get("lines") or [])
        said = CHAT_BUILDING.format(plan=plan)
        return {"ok": True, "build": b["id"], "said": f"{lead} {said}" if lead else said}
    return ask


def _chat_open(host: Any, conversation: str) -> dict | None:
    """D77: the newest build she started in this chat conversation that is still open (planning, waiting for
    her Yes, its store apps being added, or building), else None. A build from the wizard or Apps > Add has no
    ``chat`` mark, so it is never joined; a build whose chat watch ended (``closed``) is never joined either."""
    from .zone3.toolsmith_host.store import ACTIVE
    for raw in reversed(host.store.all()):
        chat = raw.get("chat") if isinstance(raw.get("chat"), dict) else None
        if chat is None or chat.get("conversation") != conversation or chat.get("closed") or raw.get("replaced_by"):
            continue
        try:
            b = host.view(raw["id"])
        except KeyError:
            continue
        adding = (b.get("store_adds") or {}).get("state") in ("adding", "starting")
        if b.get("planning") or adding or host.running(b["id"]) or any(
                str(j.get("status") or "").split(":")[0] in ACTIVE + ("waiting_yes",) for j in b.get("jobs") or []):
            return b
    return None


def _add_to_running(host: Any, build_id: str, words: str) -> bool:
    """Keep her added words on a running chat build (D77), unless its chat watch ended meanwhile."""
    kept: dict = {}

    def fn(x: dict) -> None:
        chat = x.get("chat") if isinstance(x.get("chat"), dict) else None
        if chat is None or chat.get("closed"):
            return
        chat["adds"] = (list(chat.get("adds") or []) + [" ".join(str(words).split())[:300]])[-CHAT_ADDS_KEPT:]
        kept["ok"] = True
    host.store.change(build_id, fn)
    return bool(kept)


def _build_name(b: dict) -> str:
    names = [str(j.get("name") or "") for j in b.get("jobs") or b.get("jobs_in") or [] if isinstance(j, dict)]
    return ", ".join(n for n in names if n) or str(b.get("goal") or "")[:80]


def _now_of(b: dict) -> str:
    """What an open build does right now, in a few plain words."""
    if (b.get("store_adds") or {}).get("state") in ("adding", "starting"):
        return "adding store apps first"
    if b.get("planning"):
        return "planning"
    states = [str(j.get("status") or "") for j in b.get("jobs") or []]
    for s in states:
        if s.startswith("building: "):
            return "trying " + s.split(": ", 1)[1]
    for word in ("building", "waiting_you", "waiting_vault", "waiting_yes", "planned"):
        if word in states:
            return _NOW_WORDS[word]
    return "building"


def chat_result_lines(jobs: list[dict]) -> list[str]:
    """What a chat build did, job by job. A job that did not work gets one plain line per candidate tried,
    each with its reason, in the order tried (her pasted project first), never a name said twice."""
    from .wizard.lines import prove_lines
    from .zone3.toolsmith.agent import why_of
    out: list[str] = []
    for j in jobs:
        tried = [t for t in (j.get("run") or {}).get("tried") or [] if isinstance(t, dict) and t.get("name")]
        state = str(j.get("status") or "")
        if state.split(":")[0] in ("working", "free_site") or not tried:
            # The whole reason, in the Prove step's words (one chat message): never a raw state word.
            out += prove_lines([{"name": j["name"], "state": state, "reason": (j.get("run") or {}).get("line") or "",
                                  "library": j.get("library")}])
            continue
        out.append(f"{j['name']}: not working yet. Toolsmith tried {len(tried)} app{'s' if len(tried) != 1 else ''}:")
        out += [f"{t['name']}: {why_of(t['name'], t.get('why') or '').rstrip('.')}." for t in tried]
    return out


def _watch_build(core: Any, host: Any, build_id: str, say: Any, every_s: float, then: Any = None) -> None:
    """When the chat's build ends, its result goes into the same conversation and to the Desktop's notices.
    D77: then its chat watch closes (no new ask joins it any more), and *then(build, adds)* plans what she
    added while it ran."""
    def close() -> list[str]:
        taken: list[str] = []

        def fn(x: dict) -> None:
            chat = x.get("chat") if isinstance(x.get("chat"), dict) else None
            if chat is not None and not chat.get("closed"):
                chat["closed"] = True
                taken.extend(str(a) for a in chat.get("adds") or [])
        try:
            host.store.change(build_id, fn)
        except Exception as exc:  # noqa: BLE001 - nothing more joins it either way
            logger.warning("chat build not closed: %s", type(exc).__name__)
        return taken

    def run() -> None:
        import time
        from .zone3.toolsmith_host.store import ACTIVE
        end = time.monotonic() + WATCH_MAX_S
        b = None
        while time.monotonic() < end:
            time.sleep(every_s)
            try:
                b = host.view(build_id)
            except KeyError:
                return
            adding = (b.get("store_adds") or {}).get("state") in ("adding", "starting")   # store apps first (I16)
            if not adding and not host.running(build_id) and not any(j["status"].split(":")[0] in ACTIVE for j in b["jobs"]):
                break
        else:
            close()
            return
        adds = close()
        lines = chat_result_lines((b or {}).get("jobs") or [])
        known = knowledge_view(b or {}, False)
        if known is not None and known.get("line"):
            lines.append(known["line"])                # what it read into her bot's knowledge (lane I11)
        text = "Toolsmith finished. " + "\n".join(lines)
        if callable(say):
            try:
                say(text)
            except Exception as exc:  # noqa: BLE001 - the notice still says it
                logger.warning("chat result not kept: %s", type(exc).__name__)
        notices = getattr(core, "notices", None)
        if notices is not None:
            try:
                notices.post("toolsmith", {"title": "Toolsmith finished", "text": text[:600]})
            except Exception as exc:  # noqa: BLE001
                logger.warning("chat result not posted: %s", type(exc).__name__)
        if adds and callable(then):
            try:
                then(b or {}, adds)
            except Exception:  # noqa: BLE001 - her words stay on the build file
                logger.exception("what she added to build %s was not planned", build_id)
    threading.Thread(target=run, name=f"chat-build-{build_id}", daemon=True).start()


# -- search_knowledge: lane L3's folders index -------------------------------------------------------
SCAN_EVERY_S = 60.0


def knowledge_search(core: Any):
    """The Brain's ``search(query, limit)``: lane L3's ``zone1.folders.Library`` for this bot folder (the
    data folder and her linked folders). FTS over the index only, which holds the gated text: every held
    value is its handle there, never the value (the loop's gate gives the local brain the real value
    only while the vault is open, D25). Before a search the Library's scan runs (files that did not
    change are skipped), at most once a minute; the Archivist stays the index's one writer."""
    last: dict = {"at": None}

    def search(query: str, limit: int) -> list[dict]:
        import time
        from .zone1.folders import SCAN_LOCK, Library
        a = seams.archivist(core.state, core.bus)
        if a is None:
            raise RuntimeError("the vault is not here, so the index can't be read")
        library = Library(a, core.state)
        now = time.monotonic()
        # A scan running now (a folder she just linked, D126) goes on by itself: search what is read so far.
        if (last["at"] is None or now - last["at"] >= SCAN_EVERY_S) and SCAN_LOCK.acquire(blocking=False):
            try:
                library.scan()
                last["at"] = now
            finally:
                SCAN_LOCK.release()
        from .zone1.fetched import meta_for
        from .zone1.gate import OwnWords
        out = []
        for h in library.search(query, limit):
            snippet = OwnWords(h["snippet"]) if library.is_fiction(h["path"]) else h["snippet"]   # D146
            hit = {"file": Path(h["path"]).name, "path": h["path"], "snippet": snippet}
            credit = meta_for(core.state, h["path"])
            if credit:
                hit["from"] = credit           # fetched knowledge: its source, title, address and license
            out.append(hit)
        tell_knowledge_away(core)
        return out

    def away() -> str:
        from .zone1.fetched import away_line
        return away_line(core.state)
    search.away = away
    return search


class PinnedDocuments:
    """The Brain's ``read_pinned`` (D125): the documents she pinned to this bot, by name, read in parts through
    ``zone1.pinned``. ``names()`` runs every turn and reads one settings file."""

    def __init__(self, core: Any, review: Any = None) -> None:
        self.core = core
        self.review = review

    def names(self) -> list[str]:
        from .zone1 import pinned
        from .zone1.folders import pinned_paths
        return list(pinned.names(pinned_paths(self.core.state)))

    def show(self, name: str, passage: str, suggestion: str, why: str, part: int, real: bool) -> dict:
        """``show_passage`` (D140): reads only."""
        from .zone1 import pinned
        from .zone1.folders import Library
        a = seams.archivist(self.core.state, self.core.bus)
        if a is None:
            return {"ok": False, "error": "The vault is not here, so a document can't be read."}
        return pinned.show(Library(a, self.core.state), name, passage, suggestion, why, part, real=real)

    def apps_take_files(self) -> bool:
        """Is there anything for ``send_document`` to do: a package that takes files (D127)?"""
        from .send_file import sendable
        return bool(sendable(self.core))

    def send(self, name: str, app: str) -> dict:
        """``send_document`` (D127): a file on her list or one she linked, by its name, never a path, to a package by its id."""
        from .send_file import SendRefused, hand, sendable
        from .zone1 import pinned
        from .zone1.folders import Library
        a = seams.archivist(self.core.state, self.core.bus)
        if a is None:
            return {"ok": False, "error": "The vault is not here, so a document can't be read."}
        lib = Library(a, self.core.state)
        known = lib.known_documents()
        path = pinned.named(known, name)
        if path is None or lib.handable(path) is None:
            return {"ok": False, "error": "No one file on her list or in a folder she linked is called that. She adds "
                                          "documents on the Knowledge page, with the Add documents button."}
        want = str(app or "").strip().lower()
        fit = [t for t in sendable(self.core) if want in (t["id"].lower(), t["name"].lower())]
        if not fit:
            return {"ok": False, "error": "No app that takes files is called that. list_installed shows the apps."}
        try:
            return {"ok": True, "said": hand(self.core, self.review, fit[0]["id"], path.name, path.read_bytes())}
        except SendRefused as exc:
            return {"ok": False, "error": str(exc)}
        except OSError:
            return {"ok": False, "error": f"{path.name} can't be read right now."}

    def read(self, name: str, part: int, find: str, real: bool) -> Any:
        from .zone1 import pinned
        from .zone1.folders import Library
        a = seams.archivist(self.core.state, self.core.bus)
        if a is None:
            return {"ok": False, "error": "The vault is not here, so a pinned document can't be read."}
        return pinned.read(Library(a, self.core.state), name, part, find, real=real)


def tell_knowledge_away(core: Any) -> None:
    """The one plain line when the place for her books and downloads is gone or full (D103): a notice, once per
    problem. Everything else keeps working, so it is a line and never a stop."""
    from .zone1.fetched import away_line
    try:
        line = away_line(core.state)
    except Exception:  # noqa: BLE001 - a line that can't be made is no reason to stop anything
        logger.exception("the knowledge place could not be checked")
        return
    if line == getattr(core, "_knowledge_away", ""):
        return
    core._knowledge_away = line
    notices = getattr(core, "notices", None)
    if line and notices is not None:
        notices.post("core", {"title": "Your books and downloads", "text": line[:600], "urgency": "high"})


# -- start_core / stop_core ---------------------------------------------------------------------
def start(core: Any, notices: Any = None) -> None:
    """With the Core: Zone 3's cards routed, the Bridge proxy, the bypass watch, every package's scope."""
    if notices is not None:
        core.notices = notices
    # A slow or gone network drive never holds the start up (D103).
    threading.Thread(target=tell_knowledge_away, args=(core,), name="knowledge-place", daemon=True).start()
    zone3 = getattr(core, "zone3", None)
    if zone3 is None:
        return
    listeners = getattr(zone3.cards, "listeners", None)
    if isinstance(listeners, list) and getattr(core, "_zone3_listener", None) not in listeners:
        # Once per Core: a stop and a start again in the same process never routes a card twice.
        listen = core._zone3_listener = lambda card: route_zone3_card(core, card)
        listeners.append(listen)
        for card in list(zone3.cards):                # raised while Zone 3 was made (a damaged Bridge at start)
            route_zone3_card(core, card)
    close_stale_grants(zone3, "start")                 # a crash or a hard kill left build or install grants
    want = getattr(core, "proxy_port", 8421)
    try:
        port = zone3.start_proxy(want)
        logger.info("Bridge proxy on 127.0.0.1:%s", port)
    except (OSError, RuntimeError) as exc:
        zone3.proxy = None
        why = getattr(exc, "strerror", None) or str(exc)
        line = (f"The Bridge proxy could not start on port {want} ({why}), so no app can "
                "reach the internet. Stop what uses that port, or start the Core with another --proxy-port.")
        logger.warning(line)
        zone3.cards.append({"kind": "proxy_down", "line": line})   # routed to the notices (above)
    for row in core.registry.list():
        refresh_scope(core, row["id"])
    from . import tool_servers
    for rec in tool_servers.rows(core.state).values():      # the tool servers she added (D137)
        refresh_scope(core, tool_servers.PREFIX + rec["id"])
    zone3.start_watch(core.steward)


def stop(core: Any) -> None:
    zone3 = getattr(core, "zone3", None)
    if zone3 is not None:
        close_stale_grants(zone3, "stop")              # no build and no install goes on after a stop
        zone3.stop()


def close_stale_grants(zone3: Any, when: str) -> int:
    """No build and no install runs at Core start or after its stop, so the Bridge's build and install
    grants close (Z3 section 2). Her Yes records stay; the next build opens its grants from them again."""
    bridge = getattr(zone3, "bridge", None)
    fn = getattr(bridge, "close_stale", None)
    if not callable(fn):
        return 0
    try:
        n = int(fn() or 0)
    except Exception:  # noqa: BLE001 - logged; a policy that can't be saved reads closed anyway
        logger.exception("stale Bridge grants not closed at %s", when)
        return 0
    if n:
        logger.info("closed %d build or install grant(s) left on the Bridge at %s", n, when)
    return n


__all__ = ["CARD_TITLES", "NO_PROXY_URL", "SAFE_OPEN_LINE", "TAMPER_CARDS", "bridge_guard", "WizardBridge", "WizardModels",
           "WizardToolsmith", "WizardVault", "chat_job", "chat_result_lines", "chat_toolsmith", "close_stale_grants", "core_question",
           "docker_wall_kw", "knowledge_search", "proxy_env",
           "refresh_scope", "route_card", "route_zone3_card", "safe_steps", "start", "stop", "tool_review", "wire"]
