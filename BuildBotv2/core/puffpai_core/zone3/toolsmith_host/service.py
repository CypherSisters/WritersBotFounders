r"""Toolsmith's host in the Core: the owner's Toolsmith routes, Toolsmith's process, and its token routes.

Toolsmith's loop (find, try, adopt, prove; its model calls) runs in its OWN
process (09 section 3, point 1; ``process.ProcessRunner``). This object is
the Core's side and runs in the Core process:

- **For the owner** (routes with access ``owner``): pick Toolsmith's model,
  plan (the Core starts Toolsmith's process to find apps and waits for the
  plan), the ONE Yes check, start (the Core starts Toolsmith's process for
  the build), the build and job views, "Try next app", and stop.
- **For Toolsmith's process** (routes with access ``toolsmith``, its token
  bound to one build, ``agent_*`` here): read its build, write its plan and
  its job status, read the Registry, read a page through ``toolsmith:search``,
  one model turn (local, or "lend and send": the Core screens, translates,
  screens the final body, checks the cap, adds the key and sends), the
  brain's JSON and answer, the try routes (download, Drawbridge check, run,
  start, log, write a file, end, add to the Package Manager), and Checklist
  steps. Nothing else: no route writes ``settings\``, the Bridge policy,
  consent records, providers or the audit log (X24).
- **Her ticks on the plan card** (lane I4b): an offer (an installed package one
  brain ask picked) is used for its job only when she ticked it on the plan
  card. :meth:`ToolsmithHost.use_offers` records that from her answer (the
  wizard's Yes step, or an owner route), before her Yes; :meth:`start` fixes it
  in the Yes record and the job reuses that package (its candidates go, so no
  try can be opened for it). No Toolsmith route writes it.
- **Her ticks** (D60): when she ticks Done on a step Toolsmith raised for a
  build, the Checklist tells this host (``Checklist.on_done``) and the Core
  writes ``ticked[<step id>]`` into that build's ``build.json`` with the step's
  fingerprint (``packages.plan_cover.step_sha``). Only that record lets the
  plan's Yes cover an adopted app's setup step; no Toolsmith route writes it.

- **Keys for the life of a build** (D68, lane I19a): her Yes (the plan card, a
  "Check again") borrows the build's keys from Archivist at once
  (:meth:`ToolsmithHost.take_keys`): the cloud model's key, and her GitHub key
  when one is saved and the vault is open. They are kept until the build's
  process ends, she locks the vault by hand, or 3 hours pass (X22), whichever
  is first; the idle lock does not end them (:meth:`ToolsmithHost.end_keys`).
  When the vault is locked at her Yes, the build view says ``keys.unlock`` and
  the Desktop asks for her PIN on the same card, then answers.

Everything Toolsmith sends is checked here (``store``, ``trials``), and a
missing part of another lane is the floor's answer (X26). When Toolsmith's
process ends, its token is revoked and what it left open is ended
(:meth:`ToolsmithHost._child_done`).
"""

from __future__ import annotations

import hashlib
import http.client
import json
import logging
import math
import os
import re
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from ...consent.store import CARD_TTL
from ...packages import plan_cover
from .. import policy as bridge_rules
from ..toolsmith import agent as agent_mod, find as find_mod, programs, scout, shelves, tries as agent_tries
from ..toolsmith.errors import CloudError, CloudHeld, CloudWaitVault, NotBuilt, Refused
from ..toolsmith.names import short_name
from . import (address, broker, knowledge as knowledge_mod, owner_apis, public_read, redact, sender,
               settings as settings_mod, store, store_phase, trials)
from .process import ProcessRunner
from .store_phase import FAILED as STORE_FAILED, FINAL as STORE_FINAL, NO_ANSWER as STORE_NO_ANSWER

logger = logging.getLogger(__name__)
DEFAULT_API = "http://127.0.0.1:8420"
STOPPED_LINE = "Toolsmith stopped before this job finished, so it is held. Check again to retry."
CRASHED_LINE = "Toolsmith stopped with an error before this job finished, so it is held. Check again to retry."
#: What stopped it, when Toolsmith's agent log says (lane I18a): the error's class name, never its message.
STOPPED_BY_LINE = "Toolsmith stopped before this job finished ({what}), so it is held. Check again to retry."
_CLASS = r"([A-Za-z_][A-Za-z0-9_]{0,59})"
_LEFT_RE = re.compile(r"^Toolsmith: job (\S{1,120}) " + re.escape(agent_mod.LEFT_MARK) + _CLASS + r"\s*$")
_CRASH_RE = re.compile(r"^Toolsmith stopped: " + _CLASS + r"\s*$")
_LOG_TAIL_BYTES = 256_000
NO_PLAN_LINE = "Toolsmith could not make a plan this time. Try again."
#: Find books, when it can't look: the real reason.
BOOKS_LOCKED_LINE = ("Your vault is locked, and your bot checks your words against the names kept in it before they go out, "
                     "so nothing was looked up. Unlock your vault on the Vault page and try again.")
BOOKS_CLOSED_LINE = "The Bridge is closed, so your bot can't reach any library, and nothing was looked up."
BOOKS_NO_WORDS_LINE = ("Nothing was looked up: those words can't be sent. A search takes plain letters, so no numbers, and no "
                       "name of someone your bot knows. Try the book's title in other words, or its subject.")
#: A Yes from before her plan card said a cloud build reads public pages and tries other apps (D121).
AGAIN_OLDER_YES_LINE = "Your last Yes did not say Toolsmith may read public pages and try other apps."
AGAIN_NO_READ_YES_LINE = "Your last Yes to check again did not name the places this plan reads knowledge from."
#: Her GitHub key in the vault, named like Toolsmith's cloud keys (``toolsmith.<provider>``): used only on
#: api.github.com, for search and her own project list (D135), through the Bridge, never logged (lane I15). None saved:
#: searches go unkeyed and the list is not read.
GITHUB_KEY_NAME = "toolsmith.github"
GITHUB_API_HOST = "api.github.com"
GITHUB_PURPOSE = "GitHub search"
#: The only GitHub API paths the owner's key goes to, and only on a GET Toolsmith's own search asks to key: the
#: repository search, one repository's facts, and her own project list in exactly the shape below (D135). Any other
#: path (``/user/emails``, a repository's contents) and every try's read go without it (lane I15 fix).
_KEYED_PATHS = (re.compile(r"/search/repositories"),
                re.compile(r"/repos/(?!\.{1,2}/)[A-Za-z0-9_.-]{1,100}/(?!\.{1,2}$)[A-Za-z0-9_.-]{1,100}"))


_OWN_LISTS = {"/user/repos": re.compile(r"affiliation=owner&sort=updated&per_page=[0-9]{1,3}&page=[0-9]{1,2}"),
              "/user/starred": re.compile(r"per_page=[0-9]{1,3}&page=[0-9]{1,2}")}


def _github_api(url: Any) -> urllib.parse.SplitResult | None:
    """*url*'s parts when it is ``https://api.github.com`` (no port, no user part, no fragment)."""
    try:
        parts = urllib.parse.urlsplit(str(url or ""))
    except ValueError:
        return None
    return parts if parts.scheme == "https" and parts.netloc.lower() == GITHUB_API_HOST and not parts.fragment else None


def own_list_url(url: Any) -> bool:
    """True only for a page of her own project list (D135, :data:`_OWN_LISTS`)."""
    parts = _github_api(url)
    want = _OWN_LISTS.get(parts.path) if parts else None
    return want is not None and want.fullmatch(parts.query) is not None


def github_keyed_url(url: Any, method: str = "GET") -> bool:
    """True only for a GET to ``https://api.github.com`` (no port, no user part) and one of :data:`_KEYED_PATHS`, or a
    page of her list; a ``/repos`` read with a query string goes without the key (lane I15 re-check)."""
    parts = _github_api(url) if method == "GET" else None
    if parts is None:
        return False
    search, repo = _KEYED_PATHS
    return bool(search.fullmatch(parts.path)) or (bool(repo.fullmatch(parts.path)) and not parts.query) or own_list_url(url)


#: /queries asks per job in one plan run (lane I15 fix): Toolsmith needs two (the brain's queries, then its
#: search queue); more would let it probe which names the screen holds.
QUERIES_PER_JOB = 3
SEARCH_HELD_LINE = ("That address held words BuildBot never sends (a name or a personal detail), so nothing was "
                    "sent.")
NOT_PLAIN_LINE = "That address is not in a plain form (an odd host, path or encoding), so nothing was sent."
#: /web: what a reader may ask for as its time limit (seconds), and the most it reads off a ``list`` row's file.
WEB_TIMEOUT_MIN_S, WEB_TIMEOUT_MAX_S = 1.0, 30.0
#: The only ``Accept`` values /web sends (what Toolsmith's own reads ask for): any other is dropped, never carried out.
WEB_ACCEPTS = frozenset({"application/json", "application/vnd.github+json"})
WEB_MAX_BYTES = 3_000_000
#: Her own web search keys (``sources.json`` rows ``web``, vault ``toolsmith.<id>``): a key's characters only, as
#: :data:`_GITHUB_KEY_SHAPE`. Six at least: some places give short keys. Lent per search while a plan is made, ended
#: at once, sent in the row's own header on its one host, never in an address.
WEB_KEY_NO_ROW_LINE = "That address can't use a search key, so nothing was sent."
WEB_KEY_NEEDED_LINE = "{name} needs your own key, and BuildBot can't use one right now, so nothing was sent."
WEB_KEY_REFUSED_LINE = "{name} did not accept your key, so it is off for this plan and nothing was sent."
WEB_KEY_NOT_NOW_LINE = ("A search key is used only while Toolsmith makes a plan, or builds on a cloud model after your "
                        "Yes, so nothing was sent.")
WEB_KEY_BRIDGE_LINE = "The Bridge is closed for {host}, so nothing was sent."
#: The APIs she added herself (``owner_apis``).
OWNER_API_NOT_NOW_LINE = ("An API you added is used only while Toolsmith makes a plan, or builds on a cloud model after "
                          "your Yes, so nothing was sent.")
OWNER_API_CANT_SEND_LINE = "BuildBot can't check that address before sending here, so nothing was sent."
OWNER_API_WORD_LINE = "Type a plain search word to test with, on one line."
OWNER_API_WORD_HELD_LINE = "That word holds a name or a personal detail BuildBot never sends, so nothing was sent."
OWNER_API_WORD_SHAPE_LINE = "That word can't go in this address, so nothing was sent. Try one plain word."
OWNER_API_NO_KEY_LINE = "{name} needs your key. Link it, and unlock your vault if it is locked, then press Test again."
OWNER_API_BAD_KEY_LINE = "That key has characters BuildBot won't send. Link it again."
OWNER_API_KEY_SAID_NO_LINE = "{name} did not accept your key ({status}). Check the key and where it goes."
OWNER_API_SAID_NO_LINE = "{name} answered {status}, so there are no results to read."
OWNER_API_NO_READ_LINE = "It answered, but Toolsmith can't read results from it. Name the fields, or remove it."
OWNER_API_READ_LINE = "It works. Toolsmith read {count} from {name}."
OWNER_API_TEST_WORDS = 80
WEB_KEY_NO_MORE = (401, 403, 422)                  # the place said no to the key: no second send on this plan
_WEB_KEY_SHAPE = re.compile(r"[A-Za-z0-9\-._~+/]{6,255}=*")
#: A GitHub key as the Core will put it in a header: RFC 6750 token characters only (letters, digits and
#: ``- . _ ~ + /``, then ``=`` padding), 20 to 255 of them, and nothing else anywhere: no space, no newline, no
#: control or non-ASCII character, not even at the ends (lane I15 fix: a newline made http.client raise with the
#: key in its message).
_GITHUB_KEY_SHAPE = re.compile(r"[A-Za-z0-9\-._~+/]{20,255}=*")


def github_key_ok(key: Any) -> bool:
    """True when *key* has a usable key's strict shape (:data:`_GITHUB_KEY_SHAPE`)."""
    return isinstance(key, str) and len(key) <= 255 and _GITHUB_KEY_SHAPE.fullmatch(key) is not None


def web_key_ok(key: Any) -> bool:
    """True when *key* has a usable web search key's strict shape (:data:`_WEB_KEY_SHAPE`)."""
    return isinstance(key, str) and len(key) <= 255 and _WEB_KEY_SHAPE.fullmatch(key) is not None


def _web_purpose(row: dict) -> str:
    return f"{row.get('short') or 'web'} search"


def _timeout_s(value: Any) -> float | None:
    """A reader's time limit for one /web read, between the two limits; anything else: the Core's own."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return max(WEB_TIMEOUT_MIN_S, min(float(value), WEB_TIMEOUT_MAX_S))


MAX_REQUEST_BYTES = sender.MAX_BODY_BYTES
STEP_KINDS = ("run_command", "open_url", "enter_secret", "plug_in", "confirm")
PAUSED_LINE = "BuildBot is in safe mode: {why} Restart BuildBot to leave it."
CANT_TELL = "BuildBot could not tell whether it is in safe mode."
NO_CLOUD_YES_LINE = ("You picked {company} for Toolsmith, but BuildBot has no Yes from you on record for it, so "
                     "nothing was sent. Pick {company} again in Settings and say yes, or let Toolsmith use this "
                     "computer.")


#: "Check again" (lane I18e): its plain lines.
AGAIN_NO_YES_LINE = "This build has not started yet, so there is nothing to check again."
AGAIN_RUNNING_LINE = "Toolsmith is already working on this build. Check again once it stops."
AGAIN_NOTHING_LINE = "Every job of this build has ended, so there is nothing to check again."
AGAIN_NOTHING_NEW_LINE = ("Nothing new to try for this job. Paste a GitHub address, or ask Toolsmith to look again "
                          "with other words.")
AGAIN_VAULT_LINE = "Unlock your vault first. Toolsmith needs its cloud key from your vault to check again."
AGAIN_NOT_YOURS_LINE = "That Yes is not for checking this build again."
AGAIN_GONE_LINE = "Your Yes to check again is not there any more. Check again to ask once more."
AGAIN_NOT_STARTED_LINE = "Toolsmith could not start again ({why}), so this job is held. Check again to retry."
AGAIN_SAY = "You asked to check again: Toolsmith tries this job's apps once more."
AGAIN_TITLE = "Check again?"
AGAIN_TERMS_CHANGED_LINE = "Toolsmith's model or its cost cap changed after the last card, so this card asks again."


#: How long the plan's card waits for the model-needs ask (lane I17) before it goes on without a model offer.
MODEL_NEEDS_WAIT_S = 30.0


#: How long her Yes on a plan card keeps counting as live, for the start that follows it (D153). Without it, a Yes that
#: came past her idle setting could lock the vault just before the build borrows its keys.
CARD_YES_S = 60.0


def _process_alive(child: Any) -> bool:
    """Is this Toolsmith process itself still running (D153)? A runner with no ``ended`` says it by ``wait``."""
    ended = getattr(child, "ended", None)
    return not ended() if callable(ended) else not child.wait(0)


class Paused(Refused):
    """Toolsmith is paused (safe mode): every plan, start and agent route says so (409 ``refused``)."""


class ToolsmithHost:
    """The service the Core API's ``toolsmith`` group calls."""

    def __init__(self, state: Path | str, ports: Any, registry: Any = None, tokens: Any = None, *,
                 runner: Any = None, api: str | Callable[[], str] = DEFAULT_API,
                 provider_dirs: list[Path] | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.state = Path(state)
        self.ports, self.registry, self.tokens = ports, registry, tokens
        self.store = store.Builds(self.state)
        self.trials = trials.Trials(self)
        self.knowledge = knowledge_mod.Fetcher(self)
        self.store_phase = store_phase.StorePhase(self)     # ticked store apps, added before the build (lane I16)
        self.runner = runner or ProcessRunner(api, self.state / "logs" / "toolsmith")
        self.provider_dirs = provider_dirs
        self.sleep = sleep
        self._lock = threading.RLock()
        self._next: set[str] = set()
        self._store_cards: dict[str, dict[str, dict]] = {}   # the store apps the goal's conversation asked for (D152)
        self._stopping = False
        self._paused = ""                          # safe mode's reason, plain words ("" = not paused)
        self._children: dict[str, Any] = {}
        self._senders: dict[str, sender.FrontierSender] = {}
        self._opaque = sender.OpaqueRecord()        # the company items the Core received (09 section 4)
        self._ledgers: dict[str, sender.Ledger] = {}
        self._steps: dict[str, set[str]] = {}
        self._hidden: dict[str, str] = {}
        self._queries_asked: dict[str, int] = {}   # /queries asks per build in this plan run (lane I15 fix)
        self._needs: dict[str, threading.Thread] = {}   # the model-needs ask per build being planned (lane I17)
        self._launch_failed: dict[str, str] = {}         # why a build's last launch did not start (lane I17 fix)
        self._again_busy: set[str] = set()               # builds a "Check again" is starting now (lane I18e)
        self._gh_leases: dict[str, str] = {}             # build id -> its GitHub key lease, for the build (D68)
        self._cards_out: dict[str, float] = {}           # build id -> until when its plan card counts as live (D153)
        self._now: Callable[[], float] = time.monotonic
        self.cards: list[dict] = []                # what the Core tells her (safe mode, a stopped try)
        if hasattr(ports, "toolsmith"):
            ports.toolsmith = self                 # the ports' safe-mode fallback pauses this host too
        self._hook_caretaker()
        on_granted = getattr(getattr(ports, "consent", None), "on_granted", None)
        if callable(on_granted):
            on_granted(self._granted)              # her plan Yes fixes the offers she ticked (lane I4b)
        on_answer = getattr(getattr(ports, "consent", None), "on_answer", None)
        if callable(on_answer):
            on_answer(self._answered)              # her No on a plan card ends that build (lane I6b)
        hooks = getattr(getattr(ports, "checklist", None), "on_done", None)
        if isinstance(hooks, list):
            hooks.append(self._ticked)             # her Done on a build step, recorded by the Core (D60)

    # -- settings and providers -------------------------------------------------

    def settings(self) -> dict:
        return settings_mod.read(self.state)

    def providers(self) -> dict[str, dict]:
        if self.provider_dirs is not None:                  # tests: folders of provider manifests
            return sender.load_providers(*self.provider_dirs)
        # The running Core: installed provider packages only, through the Core's one manifest reader
        # (never a puffpai.json in packages\ itself: a try's download lives there too).
        read = getattr(getattr(self.ports, "steward", None), "read_manifest", None)
        if not callable(read):
            from ...packages import manifest as manifest_mod
            from ...packages.installs import Installs
            installs = Installs(self.state)
            read = lambda i: manifest_mod.read_installed(self.registry, installs, i)   # noqa: E731
        return sender.installed_providers(self.registry, read)

    @staticmethod
    def _cloud_for(b: dict, st: dict) -> bool:
        """Cloud only when she chose it now AND this build was planned (and said yes to) with that provider."""
        rec = b.get("model") or {}
        return (st.get("where") == "cloud" and rec.get("where") == "cloud"
                and rec.get("provider") == st.get("provider"))

    def _local_goal(self) -> dict:
        """``Ports.local_goal`` (D154). Ports with none (a test double): {}, so a local build walks its plan as it did."""
        fn = getattr(self.ports, "local_goal", None)
        try:
            got = fn() if callable(fn) else {}
        except Exception:  # noqa: BLE001 - can't tell: the walk down the plan
            return {}
        return got if isinstance(got, dict) else {}

    def looks_after_yes(self, b: dict, st: dict) -> bool:
        """After her Yes this build looks further than its plan: a cloud build (D121), or a local one (D154)."""
        return self._cloud_for(b, st) or (st.get("where") != "cloud" and bool(self._local_goal().get("makes")))

    def _company(self, st: dict) -> str:
        prov = self.providers().get(str(st.get("provider") or "")) or {}
        return str((prov.get("toolsmith_model") or {}).get("company") or st.get("provider") or "")

    def choose(self, body: dict, by: str = "owner") -> dict:
        """The owner's pick for Toolsmith's model (wizard step 5 or Settings).

        A company: her Yes to its consent sentence is recorded as the Bridge's ``cloud`` Yes (only its own model
        hosts, only while a build runs), before the setting says cloud; no record, no cloud. This computer: every
        cloud Yes is taken back and toolsmith:cloud closes."""
        if body.get("where") != "cloud":
            self._cloud_consent(None)
            return settings_mod.write(self.state, {"where": "local"})
        prov = self.providers().get(str(body.get("provider") or ""))
        if prov is None:
            raise ValueError("There is no such provider for Toolsmith.")
        problems = sender.provider_problems(prov)
        if problems:
            raise ValueError(problems[0])
        tm = prov["toolsmith_model"]
        model = str(body.get("model") or tm.get("default_model") or "")
        if model not in [m["id"] for m in tm["models"]]:
            raise ValueError(f"Pick one of {tm['company']}'s models.")
        if body.get("consent_sha") != sender.consent_sha(prov):
            raise ValueError(f"Read what Toolsmith sends to {tm['company']} and say yes to that sentence first.")
        try:
            cap = float(body.get("cost_cap_usd", tm["cost_cap"]["per_build_usd"]))
        except (TypeError, ValueError):
            raise ValueError("The cost cap is a number of dollars.") from None
        if not cap >= 0:
            raise ValueError("The cost cap is a number of dollars.")
        listed = sender.known_hosts().get(str(tm["company"]), [])
        self._cloud_consent(prov["id"], [h for h in tm["hosts"] if h in listed], body["consent_sha"], by,
                            company=str(tm["company"]))
        return settings_mod.write(self.state, {
            "where": "cloud", "provider": prov["id"], "model": model, "cost_cap_usd": cap,
            "consent": {"sha": body["consent_sha"], "at": settings_mod.now(), "by": by}})

    def _cloud_consent(self, provider: str | None, hosts: list[str] | None = None, consent_sha: str = "",
                       by: str = "owner", company: str = "") -> None:
        """Her cloud Yes on the Bridge (``Ports.cloud_yes``), or every cloud Yes taken back (*provider* None).
        Ports without these calls (a test double) record nothing. A Yes that can't be recorded refuses the
        choice, so the setting never says cloud without it; taking back that fails is logged (this computer
        is used either way)."""
        if provider is None:
            off = getattr(self.ports, "cloud_off", None)
            if callable(off):
                try:
                    off()
                except Exception:  # noqa: BLE001 - local is used whatever the Bridge says
                    logger.exception("cloud Yes not taken back")
            return
        yes = getattr(self.ports, "cloud_yes", None)
        if not callable(yes):
            return
        if not hosts:
            raise ValueError(f"{company or 'That company'} has no host on the Core's known list, so Toolsmith "
                             "can't use it.")
        try:
            yes(provider, list(hosts), consent_sha, by=by or "owner")
        except Exception as exc:  # noqa: BLE001 - no record means no cloud: said plainly
            why = str(getattr(exc, "message", "") or exc).strip().rstrip(".")
            self._cloud_consent(None)                  # what the line says: this computer, nothing half kept
            settings_mod.write(self.state, {"where": "local"})
            raise ValueError(f"BuildBot could not keep your Yes for {company or 'that company'}"
                             + (f" ({why})" if why else "") + ", so Toolsmith keeps using this computer.") from None

    def _consent_changed(self, st: dict) -> str:
        """Why her saved cloud Yes no longer covers what is sent (``sender.consent_change``). "" on a bot with no
        saved cloud Yes, or with a Yes for this very sentence."""
        prov = self.providers().get(str(st.get("provider") or "")) if st.get("where") == "cloud" else None
        return sender.consent_change(prov, (st.get("consent") or {}).get("sha")) if prov else ""

    def status(self) -> dict:
        st = self.settings()
        out = {"where": st["where"], "builds": [self._summary(b) for b in self.store.all()]}
        if st["where"] == "cloud":
            out.update(provider=st["provider"], model=st.get("model"), cost_cap_usd=st["cost_cap_usd"],
                       company=self._company(st))
            changed = self._consent_changed(st)
            if changed:
                out["consent_changed"] = changed       # Settings says why it asks again (D118); never on a new bot
        return out

    def _settled(self, b: dict) -> dict:
        """D160: a fetch whose process is gone (the Core was stopped hard, so ``_child_done`` never ran) ends now with its plain
        line, so it stops saying it is reading and Check again can read the rest."""
        run = b.get("knowledge_run")
        if not isinstance(run, dict) or run.get("state") != "fetching":
            return b
        with self._lock:
            busy = b["id"] in self._again_busy
        if busy or self.running(b["id"]):
            return b
        with self.knowledge._lock(b["id"]):
            self.store.change(b["id"], knowledge_mod.end_unfinished)
        return self.store.read(b["id"]) or b

    def _summary(self, b: dict) -> dict:
        b = self._settled(b)
        return {"id": b["id"], "goal": b["goal"], "created_at": b["created_at"],
                "jobs": [{"id": j["id"], "name": j["name"], "status": store.job_status(b, j),
                          **({"library": True} if store.library_job(j) else {})} for j in b["jobs"]],
                "library": knowledge_mod.progress(b),         # the knowledge fetch, for Home (lane I19b)
                # A books-only build has no jobs, so its rail row reads these.
                **({"books_only": True, "planning": bool(b.get("planning")), "yes": bool(b.get("yes"))} if b.get("books_only") else {})}

    # -- plan, Yes, start (the owner) ------------------------------------------------

    def plan(self, goal: str, jobs: Any = None) -> dict:
        """Toolsmith's process finds apps for every job; the Core keeps the plan and asks ONE Yes."""
        self._refuse_if_paused()
        return self.plan_build(self.draft(goal, jobs)["id"])

    def plan_books(self, words: str | list[str], *, goal: str = "", only: Any = None, wait: bool = True) -> dict:
        """Books only (D136, v1's Find more knowledge): the plan's card with no app looked for and no model asked, so
        it costs nothing. Her words, or a list's topics, are the topics, screened as every topic is. *only*: a reading
        list's sources, with no catalog asked (``knowledge.READING_SOURCES``). With *wait* False it answers at once, with
        ``planning`` true, and the page follows the build while it looks."""
        self._refuse_if_paused()
        topics = [words] if isinstance(words, str) else list(words)
        for old in self.store.all() if not only else []:   # a newer search takes the place of a card still waiting
            cid = str((old.get("consent") or {}).get("id") or "") if isinstance(old, dict) and old.get("books_only") and not old.get("knowledge_only") else ""
            if cid and not old.get("yes") and self._card_waiting(cid):
                self.ports.consent_expire(cid, "A newer search took its place.")
        b = self.draft(goal or topics[0], [{"name": t, "does": t} for t in topics])
        self.store.change(b["id"], lambda x: x.update(books_only=True, **({"knowledge_only": list(only)} if only else {})))
        if wait:
            return self.plan_build(b["id"])
        self.store.change(b["id"], lambda x: x.update(planning=True, said=""))
        threading.Thread(target=self._plan_books_now, args=(b["id"],), name=f"books-{b['id']}", daemon=True).start()
        return self.view(b["id"])

    def _plan_books_now(self, build_id: str) -> None:
        try:
            self.plan_build(build_id)
        except Paused:                                 # _launch said so on the build
            pass
        except Exception:  # noqa: BLE001 - the page follows this build, so it must end with a line
            logger.exception("the books plan of %s", build_id)
            self.store.change(build_id, lambda x: x.update(planning=False, said=NO_PLAN_LINE))

    def books_can(self, bridge: dict) -> dict:
        """Can Find books look right now, and where? ``{can, line, ask, after_yes}``. *can*: ``ok``; ``no_search`` (a plan
        can be made and read after her Yes, but the libraries asked while it is made are not on her search Yes);
        ``bridge_closed``, ``paused``, ``locked``, which stop it. *bridge*: ``Bridge.view``, since her search Yes opens its
        hosts only while a build runs, so ``search_places`` says nothing out here. Reads only: no line in the outside log."""
        why = self.paused_reason()
        if why:
            return {"can": "paused", "line": PAUSED_LINE.format(why=why if why.endswith(".") else why + "."), "ask": [], "after_yes": []}
        after = knowledge_mod.after_yes_names()
        if self._household() is None:
            return {"can": "locked", "line": BOOKS_LOCKED_LINE, "ask": [], "after_yes": after}
        if bridge.get("setting") != "open":
            return {"can": "bridge_closed", "line": BOOKS_CLOSED_LINE, "ask": [], "after_yes": after}
        ask = knowledge_mod.catalog_names(bridge.get("search_hosts") if bridge.get("search") else [])
        left = [n for n in knowledge_mod.catalog_names() if n not in ask]
        if left:
            return {"can": "no_search", "ask": ask, "after_yes": after,
                    "line": f"Toolsmith's search is off, so {knowledge_mod._and_list(left)} can't be asked. "
                            f"{knowledge_mod._and_list(after)} can still be read after your Yes."}
        return {"can": "ok", "line": "", "ask": ask, "after_yes": after}

    def reading_view(self, topics: list[str]) -> dict:
        """Where a moved bot's reading list stands (D148): ``{state: offer | asking | fetching | done, line, build,
        consent_id}``. It counts as read when any plan with every one of its topics read something, here or before it moved."""
        want = {t.lower() for t in topics}
        rows = sorted((b for b in self.store.all() if isinstance(b, dict)), key=lambda b: str(b.get("created_at") or ""), reverse=True)
        out = {"state": "offer", "line": "", "build": "", "consent_id": ""}
        for b in rows:
            kp = b.get("knowledge_plan") or {}
            have = {str(t).lower() for t in kp.get("topics") or []}
            if not b.get("knowledge_only") and not (want and want <= have):
                continue
            run = b.get("knowledge_run") if isinstance(b.get("knowledge_run"), dict) else {}
            lib = knowledge_mod.progress(b) or {}
            if b.get("planning"):
                return {**out, "state": "asking", "build": b["id"]}
            if lib.get("state") == "done":
                if lib.get("done"):
                    return {**out, "state": "done", "build": b["id"], "line": str(run.get("line") or "")}
                out["line"] = str(run.get("line") or "")       # the last try read nothing, and it says why
                continue
            if b.get("yes"):
                return {**out, "state": "fetching", "build": b["id"], "line": str(lib.get("now") or "")}
            cid = str((b.get("consent") or {}).get("id") or "")
            if cid and self._card_waiting(cid):
                return {**out, "state": "asking", "build": b["id"], "consent_id": cid}
        return out

    def reading_plan(self, topics: list[str]) -> dict:
        """The list's one plan card: only Wikipedia and Gutenberg, no catalog, no model, nothing before her Yes. Pressed
        twice, the card that waits is shown again."""
        now = self.reading_view(topics)
        if now["state"] != "offer":
            return self.view(now["build"])
        return self.plan_books(topics, goal=knowledge_mod.READING_GOAL, only=knowledge_mod.READING_SOURCES, wait=False)

    def draft(self, goal: str, jobs: Any = None, left_out: Any = None) -> dict:
        """A build with its goal and jobs (the wizard's goal step), not planned yet. *left_out*: what she asked
        for that is not a job, said on the plan card (D84)."""
        self._refuse_if_paused()
        b = self.store.create(goal, jobs, self.settings(), left_out=left_out)
        self.store.change(b["id"], lambda x: x.update(planning=False))
        return self.view(b["id"])

    def plan_build(self, build_id: str) -> dict:
        """Plan a drafted build (once): Toolsmith's process finds apps; the Core keeps the plan and asks ONE Yes."""
        self._refuse_if_paused()
        b = self.store.read(build_id)
        if b is None:
            raise KeyError(build_id)
        if b.get("plan_sha") or b.get("yes"):
            return self.view(build_id)
        self.store.change(build_id, lambda x: x.update(planning=True, said="",
                                                         model={"where": self.settings()["where"],
                                                                "provider": self.settings().get("provider"),
                                                                "model": self.settings().get("model")}))
        self._model_needs_begin(build_id)        # lane I17: alongside the plan, never in its way
        child = self._launch("plan", build_id)
        if child is not None and not child.wait(float(self.settings()["timing"]["plan_wait_s"])):
            child.stop()
            child.wait(30)
        b = self.store.read(build_id) or b
        if b.get("planning"):
            self.store.change(build_id, lambda x: x.update(planning=False, said=NO_PLAN_LINE))
        return self.view(build_id)

    def _model_needs_begin(self, build_id: str) -> None:
        """Ask :meth:`_model_needs` on a thread of its own, so a slow brain never holds up the plan; the plan's
        card waits for it a short while (:data:`MODEL_NEEDS_WAIT_S`), then goes on without a model offer."""
        if not callable(getattr(self.ports, "model_needs", None)) or (self.store.read(build_id) or {}).get("books_only"):
            return                                     # books only: no model is asked, and no card waits on one
        th = threading.Thread(target=self._model_needs, args=(build_id,), name=f"model-needs-{build_id}", daemon=True)
        with self._lock:
            self._needs[build_id] = th
        th.start()

    def _model_needs(self, build_id: str) -> None:
        """Lane I17: does this goal need a coding or picture model? One small JSON ask, ALWAYS on this
        computer's brain (``Ports.model_needs``), before the plan Yes (nothing is sent). Asked once per build,
        and only when a role it could offer has no model yet. Ports without it (a test double) ask nothing."""
        needs, offer = getattr(self.ports, "model_needs", None), getattr(self.ports, "model_offer", None)
        b = self.store.read(build_id) or {}
        if not callable(needs) or not callable(offer) or b.get("model_needs") is not None:
            return
        from ...zone2.roles import OFFERED
        try:
            open_roles = [r for r in OFFERED if offer(r) is not None]
            wanted = [r for r in (needs(b.get("goal") or "", b.get("jobs_in") or []) if open_roles else [])
                      if r in open_roles]
        except Exception:  # noqa: BLE001 - never a veto: the plan goes on without a model offer
            logger.exception("model needs of build %s not asked", build_id)
            wanted = []
        self.store.change(build_id, lambda x: x.update(model_needs=wanted))

    def _model_offers(self, build_id: str) -> list[dict]:
        """The plan card's model offers (lane I17): for each role the goal needs, worked out now."""
        offer = getattr(self.ports, "model_offer", None)
        with self._lock:
            th = self._needs.pop(build_id, None)
        if th is not None:
            th.join(MODEL_NEEDS_WAIT_S)            # still asking after that: no model offer on this card
        wanted = (self.store.read(build_id) or {}).get("model_needs") or []
        if not callable(offer) or not wanted:
            return []
        out = []
        for role in wanted:
            try:
                got = offer(role)
            except Exception:  # noqa: BLE001 - no offer for that role
                logger.exception("model offer for %s not worked out", role)
                got = None
            if isinstance(got, dict):
                out.append(got)
        return out

    def model_adds(self, build_id: str) -> list[dict]:
        """The model offers she ticked on this build's plan card (fixed at her Yes), lane I17:
        ``[{role, model, name, disk_gb, installed}]``. Each is downloaded (its own Yes card) and its role set
        before the build starts (``store_phase``)."""
        b = self.store.read(build_id)
        if b is None:
            raise KeyError(build_id)
        fixed = b.get("use_fixed") if isinstance(b.get("use_fixed"), dict) else {}
        ticked = fixed.get("models") if isinstance(fixed.get("models"), dict) else {}
        out = []
        for m in (b.get("card") or {}).get("model_offers") or []:
            if isinstance(m, dict) and m.get("model") and ticked.get(m.get("role")) == m["model"]:
                out.append({"role": m["role"], "model": m["model"], "name": m.get("label") or m["model"],
                            "disk_gb": m.get("disk_gb"), "installed": bool(m.get("installed"))})
        return out

    def persona_ask(self, build_id: str, system: str, user: str) -> tuple[Any, str]:
        """One small JSON ask about a build for the Core (who the bot is, lane I12), ALWAYS on this computer's
        brain (``Ports.brain_json``), even when she picked a cloud company for Toolsmith: it runs before her plan
        Yes, so nothing is sent and no key or Bridge grant is used (D66). ``(parsed JSON or None, "local")``;
        never a veto."""
        self._refuse_if_paused()
        self._build(build_id, after_yes=False)
        return self.ports.brain_json(system, user), "local"

    def holds_vault(self) -> bool:
        """D153: is a build live now, so the vault's idle timer is its 60 minutes? Only what a dead build can't leave
        true counts, and never a stored status: Toolsmith's own process (planning or building, and whatever it waits on
        with her), a store phase's thread, or a plan card this Core raised, not yet answered and not past its time. All
        of it is this process's memory, so a stop or a crash ends it. Paused or stopping: no.

        The vault asks with its own lock held, and the writers that seal take their locks before the vault's. So this
        takes no lock and never asks the consent store (its sweep runs listeners that reach those writers)."""
        if self.halted():
            return False
        now = self._now()
        return (any(_process_alive(child) for child in list(self._children.values()))
                or self.store_phase.alive()
                or any(now < end for end in list(self._cards_out.values())))

    def halted(self) -> bool:
        """Paused or stopping: nothing holds the vault open, whichever reason would (D153). No lock."""
        return self._stopping or bool(self.paused_reason())

    def _card_out(self, build_id: str) -> None:
        self._cards_out[build_id] = self._now() + CARD_TTL.total_seconds()

    def running(self, build_id: str) -> bool:
        """True while Toolsmith's process works on this build."""
        with self._lock:
            child = self._children.get(build_id)
        return child is not None and not child.wait(0)

    def steps_of(self, build_id: str) -> list[dict]:
        """The "Your turn" steps Toolsmith raised for this build, as the Checklist keeps them."""
        ids = set(self._steps.get(build_id, set())) | {sid for rec in self.trials.of_build(build_id)
                                                        for sid in rec.approvals.values()}
        checklist = getattr(self.ports, "checklist", None)
        out = []
        for sid in sorted(ids):
            row = checklist.get(sid) if checklist is not None and hasattr(checklist, "get") else None
            if isinstance(row, dict):
                out.append(row)
        return out

    def answer_step(self, build_id: str, step_id: str, answer: str, by: str = "owner") -> str:
        """Her Done or Skip on one of this build's steps (the wizard's build step). "" or a plain reason."""
        if step_id not in {s.get("id") for s in self.steps_of(build_id)}:
            return "That step is not one of this build's."
        checklist = getattr(self.ports, "checklist", None)
        if checklist is None:
            return "The Checklist is not here, so the step can't be ticked."
        if answer == "skip":
            checklist.skip(step_id, by=by)
            return ""
        ok, _row, said = checklist.done(step_id, by=by)
        return "" if ok else (said or "Not done yet.")

    def _ticked(self, step: dict) -> None:
        """Her Done on one of Toolsmith's build steps: the Core's record of that tick, in the build's own file.

        Only a step the Core raised for Toolsmith (``raised_by`` toolsmith, filed under a job of the build),
        closed by the owner, counts. The fingerprint is of the Checklist's own row (what she saw), never of
        what Toolsmith later writes in a manifest."""
        if not isinstance(step, dict) or step.get("state") != "done" or step.get("closed_by") != "owner"                 or step.get("raised_by") != "toolsmith" or step.get("package") != "toolsmith":
            return
        asked = str(step.get("asked_id") or "")
        if not asked:
            return
        mark = {"step": str(step.get("id") or ""), "sha": plan_cover.step_sha({**step, "id": asked}),
                "at": step.get("closed_at"), "by": "owner"}
        for block in step.get("blocks") or []:
            if not isinstance(block, str) or not block.startswith("job:"):
                continue
            job_id = block[len("job:"):]
            build_id = job_id.rsplit("-", 1)[0]

            def fn(b: dict, job_id: str = job_id) -> None:
                if any(j.get("id") == job_id for j in b.get("jobs") or []):
                    b.setdefault("ticked", {})[asked] = dict(mark)
            self.store.change(build_id, fn)

    def _yes_ok(self, b: dict, consent_id: str) -> str:
        """"" when the owner said yes to exactly this plan; else the plain reason."""
        if b.get("replaced_by"):
            return store.REPLACED_LINE
        if not consent_id or consent_id != (b.get("consent") or {}).get("id"):
            return "That Yes is not for this plan."
        try:
            rec = self.ports.consent_record(consent_id)
        except NotBuilt as exc:
            return str(exc)
        if not rec:
            return "The plan has no Yes from you yet."
        scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}
        covers = rec.get("covers") if isinstance(rec.get("covers"), dict) else {}
        bound = rec.get("bound") if isinstance(rec.get("bound"), dict) else {}
        if (scope.get("kind") != "package" or scope.get("target") != store.bridge_target(b["id"])
                or covers.get("plan_sha") != b.get("plan_sha") or covers.get("build") != b["id"]
                or bound.get("plan_sha", b.get("plan_sha")) != b.get("plan_sha")):
            return "That Yes was for a different plan."
        return ""

    def start(self, build_id: str, consent_id: str) -> dict:
        """Start a build after its plan Yes; safe to call more than once, from any path. When the plan has
        ticked PuffPAI store apps not on the bot yet, they are added first (``store_phase``, lane I16): this
        starts nothing then, and the Core starts the build itself once every one has a final answer."""
        try:
            return self._start(build_id, consent_id)
        except BaseException:
            self.settle_keys(build_id)                 # refused (safe mode, not her Yes): nothing stays lent
            raise

    def _start(self, build_id: str, consent_id: str) -> dict:
        self._refuse_if_paused()
        b = self.store.read(build_id)
        if b is None:
            raise KeyError(build_id)
        again = b.get("again") if isinstance(b.get("again"), dict) else {}
        if b.get("yes") and consent_id and consent_id == again.get("consent_id") \
                and consent_id != (b.get("yes") or {}).get("consent_id"):
            return self.again(build_id, consent_id)    # her Yes on a "Check again" card (lane I18e), from any path
        why = self._yes_ok(b, consent_id)
        if why:
            raise PermissionError(why)
        if not b.get("yes"):
            self.store.fix_use(build_id, consent_id)   # the ticks of this Yes, if its listener has not run yet
            if self.store_phase.begin(build_id, consent_id):
                return self.view(build_id)
        return self._begin_build(build_id, consent_id)

    def settle_after_restart(self) -> list[str]:
        """Core start: Toolsmith's process does not outlive a hard stop of the Core, and nothing is left to hold the jobs it
        was on (``_child_done`` runs only while the Core lives). They hold now, with its line, so Check again can run. A
        build whose store adds go on from here is left to ``resume_store_adds``. The ids settled."""
        out = []
        for b in self.store.all():
            jobs = b.get("jobs") or []
            if (not b.get("yes") or self.running(b["id"]) or not any((j.get("run") or {}).get("state") in store.ACTIVE for j in jobs)
                    or (self.store_phase.record(b["id"]) or {}).get("state") in store_phase.RUNNING + ("asking",)):
                continue

            def fn(x: dict) -> None:
                for j in x["jobs"]:
                    if j["run"].get("state") in store.ACTIVE:
                        j["run"].update(state="held", line=STOPPED_LINE, trying=None, since=settings_mod.now())
                knowledge_mod.end_unfinished(x)
            self.store.change(b["id"], fn)
            out.append(b["id"])
        return out

    def resume_store_adds(self) -> list[str]:
        """Core start: the builds whose store apps were being added go on (``store_phase.resume``, lane I16)."""
        return self.store_phase.resume()

    def start_now(self, build_id: str, consent_id: str) -> dict:
        """The build itself starts (once; a second call changes nothing), with its Yes checked again. The
        store phase calls it when its adds are done."""
        try:
            return self._start_now(build_id, consent_id)
        except BaseException:
            self.settle_keys(build_id)
            raise

    def _start_now(self, build_id: str, consent_id: str) -> dict:
        self._refuse_if_paused()
        b = self.store.read(build_id)
        if b is None:
            raise KeyError(build_id)
        why = self._yes_ok(b, consent_id)
        if why:
            raise PermissionError(why)
        return self._begin_build(build_id, consent_id)

    def _begin_build(self, build_id: str, consent_id: str) -> dict:
        """Write the Yes and launch the build, once; the Yes was checked by the caller."""
        started = {}
        refused: dict = {}

        def fn(x: dict) -> None:
            if x.get("yes"):
                return
            if x.get("replaced_by"):
                # Under the store's lock (lane I8a): a newer plan that replaced it after the check above wins,
                # and a start that wrote its Yes first makes replace leave it alone (end_replaced reads "yes").
                refused["why"] = store.REPLACED_LINE
                return
            # Only the copy the Core made when the card was granted counts (never a tick written after it).
            use = x.get("use_fixed") if isinstance(x.get("use_fixed"), dict) \
                and x["use_fixed"].get("consent_id") == consent_id else {}
            ticked = {jid: pkg for jid, pkg in (use.get("jobs") or {}).items() if isinstance(pkg, str)}
            x["yes"] = {"given": True, "at": settings_mod.now(), "consent_id": consent_id, "sha": x["plan_sha"],
                        "use": dict(ticked)}
            for j in x["jobs"]:
                j["run"]["state"] = "planned"
                offer = j["plan"].get("offer") or {}
                shop = j["plan"].get("store") or {}
                if ticked.get(j["id"]) and ticked[j["id"]] == shop.get("package"):
                    # Her tick on a PuffPAI store app (lane I10c): added with its own Yes card before the build;
                    # this job uses it and never searches.
                    # ``reuse`` is the Registry's id; a row of the store's index is added as ``store:<id>`` (D96).
                    pkg = store.bare_id(shop["package"])
                    j["plan"].update(reuse=pkg, reuse_name=shop.get("name") or pkg,
                                     picked=list(shop.get("picked") or []), reuse_by="store", candidates=[],
                                     **({"from_index": True} if pkg != shop["package"] else {}))
                    continue
                if ticked.get(j["id"]) and ticked[j["id"]] == offer.get("package"):
                    # Her tick: this job reuses the offered package; the build-time re-ask and the health
                    # check still decide "working". Its candidates go, so no try is opened for it.
                    j["plan"].update(reuse=offer["package"], reuse_name=offer.get("name") or offer["package"],
                                     picked=list(offer.get("picked") or []), reuse_by="tick", candidates=[])
            started["now"] = True
        self.store.change(build_id, fn)
        if refused:
            raise PermissionError(refused["why"])
        if started:
            self._refuse_if_paused()               # safe mode may have come while the Yes was written
            self._launch_failed.pop(build_id, None)
            self.take_keys(build_id)               # D68: kept for the life of the build (a no-op when taken)
            try:
                self._launch("build", build_id)
            except BaseException:
                self.end_keys(build_id)
                raise
            why = self._launch_failed.pop(build_id, None)    # _launch's own record of why it did not start
            if why:                                # it did not start: never "started", and it can start again
                self._not_launched(build_id)
                self.store_phase.not_launched(build_id, why)
            else:
                self.store_phase.launched(build_id)    # its store phase says done only now, after the launch
        return self.view(build_id)

    def _not_launched(self, build_id: str) -> None:
        """A build whose launch failed: its Yes is taken off and its jobs wait again, so ``start`` (or the store
        phase) can start it again. The Yes card itself stays granted."""
        self.end_keys(build_id)                        # it did not start: its keys go back now (D68)

        def fn(x: dict) -> None:
            x.pop("yes", None)
            for j in x.get("jobs") or []:
                if (j.get("run") or {}).get("state") in ("planned", "held"):
                    j["run"].update(state="planned", line="", trying=None)
        self.store.change(build_id, fn)

    # -- "Check again" (lane I18e) ----------------------------------------------------------

    def again(self, build_id: str, consent_id: str = "") -> dict:
        """"Check again": try this build's unfinished jobs once more (held, not working yet, nothing found), and
        read what its knowledge fetch did not (D160).

        Jobs that work already stay as they are; each job tried again starts its tries over, with the hold-card
        answers she already gave (``Trials.restore_answers``). Her first Yes is reused only while the consent
        rules allow it (:meth:`_reuse_why`); otherwise ONE new Yes card asks again, covering no more than the
        plan did: the same plan sha, only the jobs tried again and their install hosts, the plan's knowledge hosts
        when its fetch came up short, and the cost cap the card names, which counts what this build spent already
        (its ledger). *consent_id*: her Yes on that card. Refused plainly while Toolsmith works on the build, in
        safe mode, or (with a cloud model and a job to try) while the vault is locked."""
        self._refuse_if_paused()
        b = self.store.read(build_id)
        if b is None:
            raise KeyError(build_id)
        if b.get("replaced_by"):
            raise Refused(store.REPLACED_LINE)
        if not b.get("yes") or not b.get("plan_sha"):
            raise Refused(AGAIN_NO_YES_LINE)
        b = self._settled(b)
        with self._lock:
            if build_id in self._again_busy or self.running(build_id):
                raise Refused(AGAIN_RUNNING_LINE)
            self._again_busy.add(build_id)
        try:
            return self._again(b, consent_id)
        finally:
            with self._lock:
                self._again_busy.discard(build_id)

    def _again(self, b: dict, consent_id: str) -> dict:
        build_id = b["id"]
        if (self.store_phase.record(build_id) or {}).get("state") in store_phase.RUNNING:
            raise Refused(AGAIN_RUNNING_LINE)
        st = self.settings()
        cloud = self._cloud_for(b, st)
        short = bool(knowledge_mod.missing(b))              # D160: its fetch came up short, so this run reads the rest
        if not short and not any(store.job_status(b, j) in store.UNFINISHED for j in b["jobs"]):
            raise Refused(AGAIN_NOTHING_LINE)
        jobs = self._again_jobs(b, cloud, self.looks_after_yes(b, st))
        if not jobs and not short:
            raise Refused(AGAIN_NOTHING_NEW_LINE)
        if jobs:                                            # with none, the Core only reads: no cloud key to wait for
            self._again_vault(b, st)                        # D68: the key is borrowed now, for this run
        launched = {"ok": False}
        try:
            got = self._again_go(b, consent_id, jobs, st, launched)
        except BaseException:
            if not launched["ok"]:
                self.end_keys(build_id)                     # no run started: the key goes back now
            raise
        if not launched["ok"]:
            self.end_keys(build_id)
            got["keys"] = self.keys_view(b)                 # said after it went back, never "leased"
        return got

    @staticmethod
    def _again_jobs(b: dict, cloud: bool, looks: bool | None = None) -> list[str]:
        """The jobs a "Check again" tries. Lane I19e: a job with no app on its plan has nothing to try again (no new
        search runs without a new plan), so it is left out. D121, D154: a build that looks further after her Yes looks
        again, so it stays in. D160: a job left planned by a hard stop starts in any run, so it is named here, and the
        vault, the model and the cost are checked for it as for any job."""
        looks = cloud if looks is None else looks
        return [j["id"] for j in b["jobs"] if (store.job_status(b, j) in store.UNFINISHED or store.job_status(b, j) == "planned")
                and ((j.get("plan") or {}).get("reuse") or (j.get("plan") or {}).get("candidates")
                     or (looks and not j.get("pasted")))]

    def _again_go(self, b: dict, consent_id: str, jobs: list[str], st: dict, launched: dict) -> dict:
        build_id = b["id"]
        rec = b.get("again") if isinstance(b.get("again"), dict) else {}
        if consent_id == (b.get("yes") or {}).get("consent_id"):
            consent_id = ""                                 # her first Yes: the reuse rule decides
        if consent_id and consent_id != rec.get("consent_id"):
            raise PermissionError(AGAIN_NOT_YOURS_LINE)
        if not consent_id and rec.get("state") == "asking" and rec.get("consent_id"):
            status = self._consent_status(str(rec["consent_id"]))
            if status == "granted":
                consent_id = str(rec["consent_id"])         # answered somewhere else: it starts now
            elif status == "pending":
                return self.view(build_id)                  # its card still waits for her
        if consent_id:
            why = self._again_yes_ok(b, consent_id, rec)
            if why:
                raise PermissionError(why)
            terms = self._terms_of(consent_id)
            if terms != self._choice_now(b, st):
                # The model or cap changed after that card was raised: her Yes was not for these, so ask again.
                revoke = getattr(self.ports, "consent_revoke", None)
                if callable(revoke):
                    revoke(consent_id)
                return self._again_ask(b, jobs, st, AGAIN_TERMS_CHANGED_LINE)
            launched["ok"] = True                           # _again_launch ends the keys itself if it fails
            return self._again_launch(build_id, [j for j in jobs if j in (rec.get("jobs") or [])], consent_id,
                                      terms=terms)
        why = self._reuse_why(b, st, jobs)
        if not why:
            launched["ok"] = True
            return self._again_launch(build_id, jobs, str(b["yes"]["consent_id"]))
        return self._again_ask(b, jobs, st, why)

    def _terms_of(self, cid: str) -> dict | None:
        """The model terms a "Check again" card itself named (``covers.terms``, sealed with the card), or None."""
        try:
            got = self.ports.consent_record(cid) or {}
        except NotBuilt:
            return None
        terms = (got.get("covers") or {}).get("terms") if isinstance(got.get("covers"), dict) else None
        return dict(terms) if isinstance(terms, dict) else None

    def _consent_status(self, cid: str) -> str:
        """A card's status from the consent store: ``granted`` only for a live Yes (its seal, not past its end,
        not taken back). Ports without ``consent_state`` (a test double): a Yes record is granted."""
        state = getattr(self.ports, "consent_state", None)
        try:
            if callable(state):
                return str((state(cid) or {}).get("status") or "gone")
            return "granted" if self.ports.consent_record(cid) else "pending"
        except Exception:  # noqa: BLE001 - can't tell: no Yes
            return "gone"

    def _choice_now(self, b: dict, st: dict) -> dict:
        """The model this build would use now (``_cloud_for``), in the plan card's terms (``store.agreed_choice``)."""
        return store.agreed_choice(st) if self._cloud_for(b, st) else {"where": "local"}

    def _reuse_why(self, b: dict, st: dict, jobs: list[str]) -> str:
        """"" when the build's Yes still covers a "Check again" of *jobs*: the same plan sha, that Yes still live
        (not taken back, not past its end) and for exactly this plan (an earlier "Check again" Yes: for these
        jobs), naming the plan's knowledge hosts when its fetch came up short (D160), and the same Toolsmith model
        and cost cap as her card said when a job uses them. Else the plain reason a new card gives."""
        yes = b.get("yes") or {}
        cid = str(yes.get("consent_id") or "")
        if yes.get("sha") != b.get("plan_sha"):
            return "The plan changed since your Yes."
        if self._yes_ok(b, cid) or self._consent_status(cid) != "granted":
            return "Your first Yes for this plan is not there any more."
        holds = (self.ports.consent_record(cid) or {}).get("covers") or {}
        covered = holds.get("again")
        if isinstance(covered, list) and not set(jobs) <= set(covered):
            return "Your last Yes to check again was for other jobs."
        if knowledge_mod.missing(b) and not set((b.get("knowledge_plan") or {}).get("hosts") or []) \
                <= set(holds.get("knowledge_hosts") or []):
            return AGAIN_NO_READ_YES_LINE
        if not jobs:                                   # D160: the Core only reads, so no model or cost is in play
            return ""
        agreed = self._terms_of(cid) if isinstance(covered, list) else None   # a "Check again" Yes: its own terms
        if agreed is None and not isinstance(covered, list):
            agreed = b.get("agreed") if isinstance(b.get("agreed"), dict) else store.agreed_choice(
                {**(b.get("model") or {}), "cost_cap_usd": None})   # a build from before lane I18e: cap unknown
        if agreed != self._choice_now(b, st):
            return "Toolsmith's model or its cost cap changed since your Yes."
        if self._choice_now(b, st).get("where") == "cloud" and (
                (bridge_rules.READ_PUBLIC_PAGES and holds.get("reads_public") is not True)
                or (bridge_rules.TRY_FOUND_APPS and holds.get("tries_found") is not True)):
            return AGAIN_OLDER_YES_LINE
        return ""

    def _again_yes_ok(self, b: dict, cid: str, rec: dict) -> str:
        """"" when *cid* is her live Yes on this build's own "Check again" card, for this plan and these jobs."""
        try:
            got = self.ports.consent_record(cid)
        except NotBuilt as exc:
            return str(exc)
        if not got or self._consent_status(cid) != "granted":
            return AGAIN_GONE_LINE
        scope = got.get("scope") if isinstance(got.get("scope"), dict) else {}
        covers = got.get("covers") if isinstance(got.get("covers"), dict) else {}
        bound = got.get("bound") if isinstance(got.get("bound"), dict) else {}
        if (scope.get("kind") != "package" or scope.get("target") != store.bridge_target(b["id"])
                or covers.get("build") != b["id"] or covers.get("plan_sha") != b.get("plan_sha")
                or bound.get("plan_sha") != b.get("plan_sha") or covers.get("again") != rec.get("jobs")):
            return AGAIN_NOT_YOURS_LINE
        return ""

    def _again_vault(self, b: dict, st: dict) -> None:
        """With a cloud model, its key must be lendable now: a locked vault says "Unlock your vault first". The
        lease taken here is the run's own (D68): kept when the run starts, ended by the caller when none does."""
        if not self._cloud_for(b, st):
            return
        try:
            self._sender(b["id"]).probe()
        except CloudWaitVault:
            self.end_keys(b["id"])
            raise Refused(AGAIN_VAULT_LINE) from None
        except CloudError as exc:
            self.end_keys(b["id"])
            raise Refused(exc.message) from None

    def _again_ask(self, b: dict, jobs: list[str], st: dict, why: str) -> dict:
        """ONE new Yes card for a "Check again", raised the normal way (a plan card for this build, bound to its
        plan sha). It covers only the jobs tried again and their install hosts from the plan, and, when its fetch came
        up short, the plan's own knowledge hosts (D160). With no job to try it opens nothing else."""
        build_id = b["id"]
        terms = self._choice_now(b, st)                 # what this card names: checked again at her Yes
        # D124: a cloud build keeps reading and trying what it finds, but only a job does that.
        cloud = terms.get("where") == "cloud" and bool(jobs)
        covers = {"build": build_id, "plan_sha": b["plan_sha"], "card": self._again_card(b, jobs, st, why),
                  "install_hosts": store.again_hosts(b, jobs, cloud), "again": list(jobs), "terms": terms}
        if cloud and bridge_rules.READ_PUBLIC_PAGES:
            covers["reads_public"] = True
        if cloud and bridge_rules.TRY_FOUND_APPS:
            covers["tries_found"] = True
        hosts = (b.get("knowledge_plan") or {}).get("hosts") or []
        if knowledge_mod.missing(b) and hosts:          # Bridge.open_build grants these from this Yes, while it runs
            covers["knowledge_hosts"] = list(hosts)
        try:
            cid = self.ports.consent_request("package", store.bridge_target(build_id), covers, False,
                                             bound={"plan_sha": b["plan_sha"]})
        except NotBuilt as exc:
            raise Refused(f"{exc} So Toolsmith can't ask you to check again yet.") from None
        self._card_out(build_id)
        self.store.change(build_id, lambda x: x.update(again={"consent_id": cid, "jobs": list(jobs),
                                                             "state": "asking", "why": why, "terms": terms,
                                                             "at": settings_mod.now()}))
        return self.view(build_id)

    def _again_card(self, b: dict, jobs: list[str], st: dict, why: str) -> dict:
        """The "Check again" card, in the Core's own words."""
        mine = [j for j in b["jobs"] if j["id"] in jobs]
        choice = self._choice_now(b, st)
        who = "the model on this computer"
        lines = [why]
        cloud = choice["where"] == "cloud" and bool(mine)   # D160: with no job to try, no model and no key is used
        found = store.found_line(self._company(st)) if cloud else ""
        if cloud:
            who = (f"{self._company(st) or choice['provider']} ({choice['model']}), "
                   f"up to ${choice['cost_cap_usd']:g} for this build")
            spent = float(self._ledger(b["id"]).read().get("total_usd") or 0.0)
            lines.append(f"This build has spent ${spent:.2f} of that already.")
            lines.append(store.KEY_KEPT_LINE)
        n = len(mine)
        goal = str(b.get("goal") or "")[:120]
        short = knowledge_mod.missing(b)
        reads = "reads what is missing from your bot's library"
        sentence = f"Toolsmith tries {n} job{'s' if n != 1 else ''} of your plan for \"{goal}\" again, using {who}" \
            + (f", and {reads}." if short else ".") if mine else f"Toolsmith {reads} for your plan for \"{goal}\"."
        if short:
            lines.append(short)
        for j in mine:
            p = j.get("plan") or {}
            if p.get("reuse"):
                lines.append(f"{j['name']}: checks {p.get('reuse_name') or p['reuse']} again.")
            elif cloud and not j.get("pasted"):
                lines.append(f"{j['name']}: looks again with {self._company(st) or choice['provider']}'s model.")
            elif p.get("candidates"):
                names = ", ".join(c["name"] for c in p["candidates"])
                lines.append(f"{j['name']}: tries the apps the plan listed again: {names}.")
            else:
                lines.append(f"{j['name']}: looks again, with nothing new added.")
        lines += [found if cloud else "Nothing new is added: no new job, app or place on the internet.",
                  "Jobs that work already stay as they are. Your answers on held apps still count." if mine
                  else "Your jobs stay as they are."]
        return {"title": AGAIN_TITLE, "sentence": sentence, "lines": lines}

    def _again_launch(self, build_id: str, jobs: list[str], cid: str, terms: dict | None = None) -> dict:
        """The jobs tried again start their tries over (``planned``), the Yes that covers it is the build's Yes
        now, and Toolsmith's process starts for them. A launch that fails holds them again with its reason."""
        now = settings_mod.now()

        def fn(x: dict) -> None:
            yes = x["yes"]
            if cid != yes.get("consent_id"):           # her Yes on the "Check again" card covers the build now
                x["yes"] = {**yes, "consent_id": cid, "at": now,
                            "before": list(yes.get("before") or []) + [yes.get("consent_id")]}
                x["consent"]["id"] = cid
                if terms is not None:
                    x["agreed"] = dict(terms)          # that card's own model terms are what she agreed to now
            for j in x["jobs"]:
                run = j["run"]
                if j["id"] in jobs and run.get("state") in store.UNFINISHED:
                    run.update(state="planned", line="", trying=None, now="", tried=[], result=None, since=now, found=[],
                               lines=(list(run.get("lines") or []) + [AGAIN_SAY])[-60:])
            knowledge_mod.read_again(x)                # D160: what its fetch did not read goes back in its queue
            x["again"] = {"consent_id": cid, "jobs": list(jobs), "state": "started", "at": now}
        self.store.change(build_id, fn)
        self.trials.restore_answers(build_id, jobs)
        self._launch_failed.pop(build_id, None)
        if jobs:                                       # D160: a run that only reads borrows no key
            self.take_keys(build_id)                   # D68: this run keeps its keys until it ends
        try:
            self._launch("build", build_id)
        except Paused:
            self._again_held(build_id, jobs, "BuildBot is in safe mode")
            raise
        why = self._launch_failed.pop(build_id, None)
        if why:
            self._again_held(build_id, jobs, why)
        return self.view(build_id)

    def _again_held(self, build_id: str, jobs: list[str], why: str) -> None:
        """A "Check again" whose launch failed: the jobs it was for hold again, with why, and its fetch ends short."""
        self.end_keys(build_id)
        line = AGAIN_NOT_STARTED_LINE.format(why=why)

        def fn(x: dict) -> None:
            for j in x["jobs"]:
                if j["id"] in jobs and j["run"].get("state") in store.ACTIVE + ("held",):
                    j["run"].update(state="held", line=line, trying=None, now="")
            knowledge_mod.end_unfinished(x)            # D160: never "fetching" with no process
        self.store.change(build_id, fn)

    def replace(self, old_id: str, new_id: str) -> bool:
        """A newer plan for the same goal takes *old_id*'s place (the wizard's plan step, after she pasted an
        address). The old build ends with "Replaced by a newer plan.", its plan card closes with that line (a
        Yes on it, from the Desktop or anywhere, is refused), a Yes already given to it is taken back, and its
        Toolsmith process, token and build grants end. A build her Yes already started is left as it is.
        True when it ended something."""
        if not old_id or old_id == new_id:
            return False
        try:
            cid = self.store.end_replaced(old_id, new_id)
        except KeyError:
            return False
        if cid is None:
            return False
        self.end_keys(old_id)                             # replaced: its keys go back now (D68)
        if cid:
            expire = getattr(self.ports, "consent_expire", None)
            if callable(expire):
                expire(cid, store.REPLACED_LINE)          # a waiting card: closed with the reason
            revoke = getattr(self.ports, "consent_revoke", None)
            if callable(revoke):
                revoke(cid)                               # a Yes given but not started: taken back
        with self._lock:
            child = self._children.get(old_id)
        if child is not None and not child.wait(0):
            child.stop()                                  # its on_exit (_child_done) closes the rest
        else:
            if self.tokens is not None:
                self.tokens.revoke_toolsmith(old_id)
            self._bridge("bridge_build_end", old_id)      # only narrows: nothing of it stays open
        return True

    def use_offers(self, build_id: str, consent_id: str, use: Any, by: str = "owner") -> dict:
        """Her ticks on the plan card's offers (job numbers), from her own answer; only while the card waits."""
        return self.store.set_use(build_id, consent_id, use if isinstance(use, list) else [], by=by,
                                  pending=self._card_waiting)

    def _card_waiting(self, cid: str) -> bool:
        """The plan card still waits for her answer (unknown: no, fail closed)."""
        state = getattr(self.ports, "consent_state", None)
        try:
            if callable(state):
                return (state(cid) or {}).get("status") == "pending"
            return self.ports.consent_record(cid) is None and bool(cid)
        except Exception:  # noqa: BLE001 - can't tell: not waiting
            return False

    def _granted(self, rec: dict) -> None:
        """The consent store's Yes listener: a build's plan card was granted, so its ticks are fixed now."""
        covers = rec.get("covers") if isinstance(rec, dict) and isinstance(rec.get("covers"), dict) else {}
        build_id = str(covers.get("build") or "")
        if not build_id or not covers.get("plan_sha"):
            return
        if build_id in self._cards_out:
            self._cards_out[build_id] = self._now() + CARD_YES_S     # the start that follows her Yes takes over (D153)
        try:
            self.store.fix_use(build_id, str(rec.get("consent_id") or ""))
        except Exception:  # noqa: BLE001 - no copy means no tick counts (start reads only the copy)
            logger.exception("ticks of build %s not fixed", build_id)
            return
        # Lane I16: ticked store apps are added now, whichever path asked for the Yes; the build starts after.
        adding = False
        try:
            adding = bool(self.store_phase.begin(build_id, str(rec.get("consent_id") or "")))
        except Exception:  # noqa: BLE001 - start (from the wizard, chat or Desktop) begins it again
            logger.exception("store adds of build %s not begun", build_id)
        # D68: while store apps or a model are added first, her Yes lends the build its keys now, so an idle lock
        # meanwhile never stops it. Only for THIS build's own plan card (its consent id and plan sha, the same check
        # as the start); with nothing to add, the start that follows her Yes lends them. Never breaks her answer.
        try:
            b = self.store.read(build_id)
            cid = str(rec.get("consent_id") or "")
            if adding and b is not None and not b.get("yes") and not self._yes_ok(b, cid):
                self.take_keys(build_id)
        except Exception:  # noqa: BLE001 - the build borrows its key when it starts, as before
            logger.exception("keys of build %s not taken at the Yes", build_id)

    def _answered(self, rec: dict) -> None:
        """The consent store's answer listener: a build's plan card ended with no Yes (her No, or no answer in
        time). Its jobs end with one plain line, Toolsmith's process for it ends (if one still runs), its token
        goes, and its build grants on the Bridge close. A Yes is handled by ``start``, never here."""
        if not isinstance(rec, dict) or rec.get("status") not in ("declined", "expired"):
            return
        covers = rec.get("covers") if isinstance(rec.get("covers"), dict) else {}
        scope = rec.get("scope") if isinstance(rec.get("scope"), dict) else {}
        build_id = str(covers.get("build") or "")
        self._cards_out.pop(build_id, None)            # her No, or no answer in time: the card holds nothing now (D153)
        if not build_id or not covers.get("plan_sha") or scope.get("kind") != "package"                 or scope.get("target") != store.bridge_target(build_id):
            return
        try:
            if not self.store.end_without_yes(build_id, str(rec.get("consent_id") or ""), rec["status"]):
                return
        except Exception:  # noqa: BLE001 - never breaks her answer; the build stays as it was
            logger.exception("build %s did not end after its plan card", build_id)
            return
        self.end_keys(build_id)                        # no Yes: nothing of this build keeps a key (D68)
        with self._lock:
            child = self._children.get(build_id)
        if child is not None and not child.wait(0):
            child.stop()                               # its on_exit (_child_done) closes the rest
            return
        if self.tokens is not None:
            self.tokens.revoke_toolsmith(build_id)
        self._bridge("bridge_build_end", build_id)     # only narrows: nothing of this build stays open

    def _installed_ids(self) -> set[str]:
        """The installed apps and tools an offer may name (the Registry's own rows)."""
        from ..toolsmith import find
        rows = self.registry.list() if self.registry is not None else []
        return {str(r.get("id")) for r in rows if r.get("kind") in find.REUSE_KINDS
                and r.get("status") in find.REUSE_STATUS}

    def _hook_caretaker(self) -> None:
        """Re-scan running tries' start-up files on every Caretaker tick (L1's ``on_tick`` hook list)."""
        c = getattr(self.ports, "caretaker", None)
        hooks = getattr(c, "on_tick", None)
        if isinstance(hooks, list) and self.trials.rescan_all not in hooks:
            hooks.append(self.trials.rescan_all)

    def paused_reason(self) -> str:
        """Why Toolsmith is paused, in plain words; "" when it is not. Safe mode is read from Caretaker too,
        so a host made after safe mode began is paused as well. Can't tell = paused (fail closed)."""
        if self._paused:
            return self._paused
        c = getattr(self.ports, "caretaker", None)
        if c is None:
            return ""
        try:
            if getattr(c, "in_safe_mode", False) is True:
                return " ".join(str(r) for r in c.safe_reasons) or "A safety check failed."
        except Exception:  # noqa: BLE001
            return CANT_TELL
        return ""

    def _refuse_if_paused(self) -> None:
        why = self.paused_reason()
        if why:
            raise Paused(PAUSED_LINE.format(why=why if why.endswith(".") else why + "."))

    def pause(self, reason: str = "") -> str:
        """Safe mode: Toolsmith is paused until the Core restarts (01 "Core start"). Every plan, start and
        agent route refuses with the reason (:meth:`_refuse_if_paused`); Toolsmith's processes and every
        process its tries started are stopped now (each by its Job Object)."""
        why = " ".join(str(reason or "").split())[:300] or self.paused_reason() or "A safety check failed."
        with self._lock:
            if not self._paused:
                self._paused = why
            self._stopping = True
            children = list(self._children.values())
        for c in children:
            try:
                c.stop()                           # its Job Object: the process and all it started
            except Exception:  # noqa: BLE001 - the next one still stops
                pass
        for b in self.store.all():
            for rec in self.trials.of_build(b["id"]):
                try:
                    self.trials.end(rec)
                except Exception:  # noqa: BLE001
                    pass
            self.trials.forget_build(b["id"])
        self._end_all_keys()                       # safe mode: every key a Yes lent goes back now (D68)
        for c in children:
            try:
                c.wait(5)
            except Exception:  # noqa: BLE001
                pass
        return "Toolsmith is paused"

    def card(self, card: dict) -> None:
        """A card for her (from L4's HookGuard and safe mode): kept here and on Zone 3's own list."""
        self.cards.append(dict(card))
        z = self.ports.zone3() if hasattr(self.ports, "zone3") else None
        if z is not None and isinstance(getattr(z, "cards", None), list):
            z.cards.append(dict(card))

    def safe_mode(self, reason: str) -> None:
        """Safe mode (01 "Core start"): Toolsmith stops (every try ends), the Bridge closes, the vault
        locks, nothing new installs, one card. Called by L4's HookGuard on a change to the Core's own Python.
        Toolsmith pauses first, so it is paused even when the rest can't run."""
        from ...caretaker.caretaker import plain_reason
        self.pause(plain_reason(reason))
        done = self.ports.safe_mode(reason) if hasattr(self.ports, "safe_mode") else []
        self.card({"kind": "safe_mode", "reason": str(reason)[:80], "done": done,
                   "line": "PuffPAI went into safe mode: " + (", ".join(done) or "Toolsmith stopped") + "."})

    def next_app(self, job_id: str) -> None:
        with self._lock:
            self._next.add(job_id)

    def stop(self, timeout: float = 30.0, grace_s: float = 5.0) -> None:
        """Stop every build (D18): Toolsmith's processes end, and so does everything their tries started.
        *grace_s*: how long each process may take to end by itself first (0 at Core stop: Ctrl+C stops at once)."""
        self._stopping = True
        with self._lock:
            children = list(self._children.values())
        end = time.monotonic() + timeout
        for c in children:
            c.wait(max(0.0, min(grace_s, end - time.monotonic())))
            c.stop()
            c.wait(max(0.0, end - time.monotonic()))
        for b in self.store.all():
            for rec in self.trials.of_build(b["id"]):
                self.trials.end(rec)
        self._end_all_keys()                       # closing BuildBot ends every key a Yes lent (D68), even one
                                                   # taken while store apps were added, with no process yet

    def view(self, build_id: str) -> dict:
        b = self.store.read(build_id)
        if b is None:
            raise KeyError(build_id)
        b = self._settled(b)
        ledger = self._ledger(build_id).read()
        b["cost"] = {"usd": round(float(ledger.get("total_usd") or 0.0), 4), "calls": ledger.get("calls", 0)}
        for j in b["jobs"]:
            j["status"] = store.job_status(b, j)
            if store.library_job(j):
                j["library"] = True                # D123: every screen says no app yet
        b["keys"] = self.keys_view(b)
        b["knowledge_missing"] = knowledge_mod.missing(b)    # D160: what its fetch did not read, "" when nothing
        return b

    # -- keys for the life of a build (D68, lane I19a) ---------------------------------------

    def take_keys(self, build_id: str) -> str:
        """Her Yes: borrow this build's keys now, kept until the build ends (D68). The cloud model's key when
        the build uses one; her GitHub key when one is saved and the vault is open (never a reason to ask for
        her PIN: searches go unkeyed without it). Returns "" when done or nothing is needed, "locked" when
        the build needs its cloud key and the vault is locked (the build view says ``keys.unlock``). Never
        raises: the build still starts and waits for the vault as before (lane I18a)."""
        out = ""
        b = self.store.read(build_id)
        if b is None or not self.may_hold_keys(build_id, live=False):
            return out
        try:
            if self._cloud_for(b, self.settings()):
                self._sender(build_id).probe()
        except CloudWaitVault:
            out = "locked"
        except CloudError:
            pass                                       # no key saved, no Yes for that company: the job says so
        except Exception:  # noqa: BLE001
            logger.exception("the cloud key of build %s was not taken", build_id)
        self._github_lease(build_id)
        return out

    def _github_lease(self, build_id: str) -> None:
        lend = getattr(self.ports, "lend_key", None)
        if not callable(lend):
            return
        with self._lock:
            kept = self._gh_leases.get(build_id)
        if kept and self._lease_live(kept):
            return
        try:
            handle = lend(GITHUB_KEY_NAME, "toolsmith", GITHUB_PURPOSE, hosts=(GITHUB_API_HOST,), job=build_id)
        except Exception:  # noqa: BLE001 - none saved, the vault locked: searches go unkeyed, as before
            return
        with self._lock:
            old = self._gh_leases.get(build_id)
            self._gh_leases[build_id] = str(handle)
        if old:
            self._end_handle(old)

    def _lease_live(self, handle: str) -> bool:
        live = getattr(self.ports, "lease_live", None)
        if not callable(live):
            return True
        try:
            return bool(live(handle))
        except Exception:  # noqa: BLE001
            return False

    def _end_handle(self, handle: str) -> None:
        end = getattr(self.ports, "end_lease", None)
        if callable(end):
            try:
                end(handle)
            except Exception:  # noqa: BLE001 - Archivist also ends it at its 3 hour cap
                pass

    def may_hold_keys(self, build_id: str, *, live: bool = True) -> bool:
        """THE rule for this build's keys (D68): not in safe mode, not replaced, a Yes (the build's own, or its
        store phase adding apps for that Yes), and, with *live*, a run going on now (Toolsmith's process, or the
        store phase that starts it). Every path that may end a build asks this (:meth:`settle_keys`)."""
        if self.paused_reason():
            return False
        b = self.store.read(build_id)
        if b is None or b.get("replaced_by"):
            return False
        adding = (self.store_phase.record(build_id) or {}).get("state") in store_phase.RUNNING
        if not (b.get("yes") or adding):
            return False
        return not live or adding or self.running(build_id)

    def settle_keys(self, build_id: str) -> None:
        """Keys only while :meth:`may_hold_keys` says so: otherwise they go back now."""
        try:
            keep = self.may_hold_keys(build_id)
        except Exception:  # noqa: BLE001 - can't tell: give them back (fail closed)
            keep = False
        if not keep:
            self.end_keys(build_id)

    def _end_all_keys(self) -> None:
        with self._lock:
            lent = set(self._senders) | set(self._gh_leases)
        for build_id in lent:
            self.end_keys(build_id)

    def end_keys(self, build_id: str) -> None:
        """The build ended (its process ended, it did not start, a No): every key it borrowed goes back now, and a store
        card the goal's conversation raised and she never answered closes, so a later Yes installs nothing (D152)."""
        with self._lock:
            done = self._senders.pop(build_id, None)
            gh = self._gh_leases.pop(build_id, None)
            cards = self._store_cards.pop(build_id, None) or {}
        if done is not None:
            done.close()
        if gh:
            self._end_handle(gh)
        for cid, mine in cards.items():
            try:
                if (self.ports.consent_state(cid) or {}).get("status") == "pending":
                    self.ports.consent_expire(cid, f"{mine['name']} was not added: the build ended before your answer.")
            except Exception:  # noqa: BLE001 - it ends by itself in ten minutes
                logger.exception("store card %s not closed", cid)

    def keys_view(self, b: dict) -> dict:
        """For her Yes in one step (D68): ``cloud`` (this build uses a cloud model's key), ``leased`` (it holds
        a live lease for it now) and ``unlock`` (it needs that key, holds none, and the vault is locked now, so
        the Desktop asks for her PIN with the Yes). Reading this never unlocks and never counts as vault use."""
        try:
            cloud = self._cloud_for(b, self.settings())
        except Exception:  # noqa: BLE001
            cloud = False
        with self._lock:
            s = self._senders.get(b.get("id"))
        leased = bool(cloud and s is not None and s.leased())
        locked = False
        opened = getattr(self.ports, "vault_open", None)
        if cloud and not leased and callable(opened):
            try:
                locked = opened() is False
            except Exception:  # noqa: BLE001 - can't tell: not asked here; the build still waits plainly
                locked = False
        # D160: its next Check again only reads what its fetch missed, with no job going on: no key, so no PIN to ask.
        if locked and knowledge_mod.missing(b) and not self._again_jobs(b, cloud) \
                and not any((j.get("run") or {}).get("state") in store.ACTIVE for j in b.get("jobs") or []):
            locked = False
        return {"cloud": cloud, "leased": leased, "unlock": bool(cloud and not leased and locked)}

    def job(self, job_id: str) -> dict:
        b = self.view(str(job_id).rsplit("-", 1)[0])
        for j in b["jobs"]:
            if j["id"] == job_id:
                return j
        raise KeyError(job_id)

    # -- Toolsmith's process ---------------------------------------------------------

    def _launch(self, do: str, build_id: str) -> Any:
        why = self.paused_reason()
        if why:                                    # the last gate before a process: never in safe mode
            line = PAUSED_LINE.format(why=why if why.endswith(".") else why + ".")
            self.store.change(build_id, lambda x: x.update(planning=False, said=line))
            raise Paused(line)
        if self.tokens is None:
            self.store.change(build_id, lambda x: x.update(planning=False, said="Toolsmith can't start yet: "
                                                           "the Core has no token store."))
            self._launch_failed[build_id] = "the Core has no token store"
            return None
        token = self.tokens.issue_toolsmith(build_id)
        if do == "plan":
            with self._lock:
                self._queries_asked.pop(build_id, None)   # each plan run has its own /queries asks
            self.store.change(build_id, lambda x: [x.pop(k, None) for k in ("github_searches", "searches_by_place",
                                                                            "read_whole", "web_off", "held_names")])
        cache = self.state / "cache" / "toolsmith"
        # Its build grants open now and close in _child_done, whatever way the process ends (Z3 section 2).
        self._bridge("bridge_build_open", build_id, ((self.store.read(build_id) or {}).get("consent") or {}).get("id"))
        try:
            child = self.runner.launch(do, build_id, token, cache=cache,
                                       on_exit=lambda code: self._child_done(build_id, do, code))
        except Exception as exc:  # noqa: BLE001 - it did not start: say so, leave nothing open
            self._launch_failed[build_id] = type(exc).__name__
            self._child_done(build_id, do, None)
            return None
        finally:
            token = ""
        with self._lock:
            if not child.wait(0):              # still running (a finished one already cleaned up)
                self._children[build_id] = child
        if do == "build":
            self._cards_out.pop(build_id, None)     # the process counts as live from here (D153)
        return child

    def _child_done(self, build_id: str, do: str, code: int | None) -> None:
        """Toolsmith's process ended: its token goes, its open tries end, mid-way jobs hold, and its build
        grants on the Bridge close (done, not yet, gave up, an error, a stop: every way ends here)."""
        try:
            if self.tokens is not None:
                self.tokens.revoke_toolsmith(build_id)
            for rec in self.trials.of_build(build_id):
                try:
                    self.trials.end(rec)
                except Exception:  # noqa: BLE001 - the next one still ends
                    pass
            if do == "build":
                self.trials.forget_build(build_id)    # its hold cards still waiting close
        finally:
            try:
                self._bridge("bridge_build_end", build_id)
            finally:
                # Lane I11: its knowledge grants close here whatever failed above or in end_build (logged),
                # never left open until the next Core start.
                self._bridge("bridge_knowledge_end", build_id)
        line = STOPPED_LINE if code == 0 else CRASHED_LINE
        left, crashed = self._left_by(build_id) if do == "build" else ({}, "")

        def fn(b: dict) -> None:
            if do == "plan" and b.get("planning"):
                b.update(planning=False, said=NO_PLAN_LINE)
            if do == "build":
                for j in b["jobs"]:
                    if j["run"].get("state") in store.ACTIVE:
                        what = left.get(j["id"]) or crashed
                        j["run"].update(state="held", line=STOPPED_BY_LINE.format(what=what) if what else line,
                                        trying=None, since=settings_mod.now())
                knowledge_mod.end_unfinished(b)        # a fetch it did not finish ends with a plain line
        with self.knowledge._lock(build_id):           # never while a knowledge step still writes
            self.store.change(build_id, fn)
        with self._lock:
            self._children.pop(build_id, None)
        if do == "build":
            self.end_keys(build_id)                    # its key leases end with the build (D41, D68)

    def _left_by(self, build_id: str) -> tuple[dict[str, str], str]:
        """What Toolsmith's agent log says stopped its jobs (lane I18a): ``({job id: class name}, the class the
        whole process stopped on)``. Only a class name is read from a line, never anything else in it."""
        log_dir = getattr(self.runner, "log_dir", None)
        if not log_dir:
            return {}, ""
        try:
            with open(Path(log_dir) / f"agent-{build_id}.log", "rb") as fh:
                fh.seek(0, os.SEEK_END)
                fh.seek(max(0, fh.tell() - _LOG_TAIL_BYTES))
                text = fh.read().decode("utf-8", "replace")
        except OSError:
            return {}, ""
        lines = [x.strip() for x in text.splitlines()]
        start = "Toolsmith: " + agent_mod.BUILD_STARTED
        if start in lines:                             # the log is appended to: only this run's lines count
            lines = lines[len(lines) - 1 - lines[::-1].index(start):]
        left: dict[str, str] = {}
        crashed = ""
        for raw in lines:
            m = _LEFT_RE.match(raw)
            if m:
                left[m.group(1)] = m.group(2)
                continue
            m = _CRASH_RE.match(raw)
            if m:
                crashed = m.group(1)
        return left, crashed

    def _bridge(self, name: str, *args: Any) -> None:
        """The Bridge's build grants (``Ports.bridge_build_open`` / ``bridge_build_end``). A port that is not
        there opens nothing; one that breaks is logged (the Core's next start closes what was left)."""
        fn = getattr(self.ports, name, None)
        if not callable(fn):
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - never stops a build's own clean-up
            logger.exception("the Bridge's build grants (%s) for %s", name, args[0] if args else "")

    # -- the routes Toolsmith's process may call (its token is bound to *build_id*) ---

    def _build(self, build_id: str, *, after_yes: bool = True) -> dict:
        b = self.store.read(build_id)
        if b is None:
            raise KeyError(build_id)
        self.trials.rescan_all(build_id)               # every Toolsmith step: its running tries are re-checked
        if after_yes and not b.get("yes"):
            raise Refused("This build has no Yes yet.")
        return b

    def _job_of(self, b: dict, job_id: str) -> dict:
        job = next((j for j in b["jobs"] if j["id"] == job_id), None)
        if job is None:
            raise KeyError(job_id)
        return job

    def agent_build(self, build_id: str) -> dict:
        self._refuse_if_paused()
        b = self._build(build_id, after_yes=False)
        st = self.settings()
        cloud = self._cloud_for(b, st)
        return {"build": {"id": b["id"], "goal": b["goal"], "jobs_in": b["jobs_in"], "jobs": b["jobs"],
                          "planning": bool(b.get("planning")), "yes": bool(b.get("yes")),
                          "knowledge": bool((b.get("knowledge_plan") or {}).get("topics")),
                          "books_only": bool(b.get("books_only")), "no_catalogs": bool(b.get("knowledge_only"))},
                "settings": {"budgets": st["budgets"], "timing": st["timing"], "where": "cloud" if cloud else "local",
                             "provider": st.get("provider") if cloud else None,
                             "model": st.get("model") if cloud else None,
                             "company": self._company(st) if cloud else ""},
                "shell": "none", "os_name": "Windows" if os.name == "nt" else "macOS" if sys.platform == "darwin" else "Linux",
                "stop": self._stopping, "tries_found": not public_read.found_why_not(self, b), "local_goal": self._local_goal(),
                "bridge_closed": self._bridge_closed(), **self._places(b)}

    def _bridge_closed(self) -> bool:
        """Does the Bridge read closed right now (D151)? Words only. Ports that can't say: no."""
        read = getattr(self.ports, "bridge_closed", None)
        try:
            return callable(read) and read() is True
        except Exception:  # noqa: BLE001 - only words: can't tell is the usual words
            return False

    def _search_closed(self, body: dict) -> bool:
        """Is the closed Bridge really why this plan found so little (D151)? Toolsmith's count: it read something, every
        read was refused for a closed Bridge, none answered and none failed another way. The Core's own word: it still
        reads closed. Words only: it blocks nothing and opens nothing."""
        places = body.get("places") if isinstance(body.get("places"), dict) else {}
        reads = places.get("reads") if isinstance(places.get("reads"), dict) else {}
        closed, other = reads.get("closed"), reads.get("other")
        if type(closed) is not int or type(other) is not int or closed < 1 or other != 0:
            return False
        return self._bridge_closed()

    def _places(self, b: dict) -> dict:
        """``search_places``: the hosts Toolsmith may ask right now; ``web_ready``: the ``web`` rows that are on (her
        Yes for its key, the search on, the vault lends the key, and it was not refused on this plan), whose hosts
        are in ``search_places`` too. ``owner_apis``: the APIs she added that are on, the same way, plus a Test that read
        results; each is in ``web_ready`` too. Never a key value. Read without a line in the outside log. Can't tell:
        none (fail closed)."""
        ports = self.ports
        try:
            places = [str(h) for h in ports.search_places()]
        except Exception:  # noqa: BLE001 - no fake, no Zone 3, a broken read
            places = []
        ready: list[str] = []
        owner: list[dict] = []
        if b.get("planning") or self._keys_after_plan(b):
            for rid, row in address.key_rows().items():
                try:
                    if rid not in (b.get("web_off") or []) and ports.bridge_peek("toolsmith:search", row["host"]) \
                            and self._shipped_key_yes(row) and self._web_key_lends(b["id"], row):
                        ready.append(rid)
                        places.append(row["host"])
                except Exception:  # noqa: BLE001 - can't tell: that row stays off
                    continue
            for rid, row in self._owner_rows().items():
                try:
                    if row.get("_live") and rid not in (b.get("web_off") or []) \
                            and ports.bridge_peek("toolsmith:search", row["host"]) \
                            and (not row["key"] or self._web_key_lends(b["id"], row)):
                        ready.append(rid)
                        places.append(row["host"])
                        owner.append(owner_apis.public(row))
                except Exception:  # noqa: BLE001 - can't tell: that row stays off
                    continue
        return {"search_places": list(dict.fromkeys(places)), "web_ready": ready, "owner_apis": owner}

    def _keys_after_plan(self, b: dict) -> bool:
        """Her saved search keys after planning (D121): only while her cloud build runs under a live plan Yes that holds
        ``reads_public`` (:func:`.public_read.why_not`). A local plan, an ended build or an older Yes: no. Can't tell: no."""
        try:
            return not b.get("planning") and not public_read.why_not(self, b)[0]
        except Exception:  # noqa: BLE001 - no decision means no
            return False

    def _owner_rows(self) -> dict[str, dict]:
        """The APIs she added, as ``address.row_read`` takes them, each with her Yes checked right now. Can't read her
        Yes: none is live (fail closed)."""
        read = getattr(self.ports, "search_yeses", None)
        try:
            records = read() if callable(read) else None
        except Exception:  # noqa: BLE001
            records = None
        return owner_apis.for_reads(self.state, records)

    def _shipped_key_yes(self, row: dict) -> bool:
        """A shipped row's Bridge grant must come from its own Yes. An API she added on the same host opens that host for
        her own row only, so it never lends the shipped row's key, and the same goes the other way (``owner_apis.yes_of``)."""
        read = getattr(self.ports, "search_yeses", None)
        records = read() if callable(read) else None
        if records is None:
            return not any(r["host"] == row["host"] for r in owner_apis.rows(self.state).values())
        named = [r for r in records if (r.get("covers") or {}).get("hosts") == [row["host"]]]
        return not named or any(not (r.get("covers") or {}).get("owner_api") for r in named)

    def agent_plan(self, build_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        st = self.settings()
        # No key ever lands in the plan detail or the build log (lane I15 fix: a backstop under the key check).
        body = redact.scrub(dict(body)) if isinstance(body, dict) else {}
        raw = body.get("knowledge") if isinstance(body.get("knowledge"), dict) else {}
        # Topics are sent to Wikipedia and Gutenberg as search words: one the outbound privacy screen would
        # change (a key, a personal detail) is never kept; no screen here keeps none (fail closed).
        # A name from the household (the people list, her own name and what she is called) is never sent, nor a
        # capitalised word her goal never used where the model's style makes it a name. Can't read the
        # household: no topics at all (fail closed).
        body["knowledge"] = {"topics": self._screened(build_id, raw.get("topics"), held_as="topics")}
        # The build log (D84): how many topics the brain gave and how many the Core kept. Counts only.
        asked = raw.get("asked") if isinstance(raw.get("asked"), dict) else {}
        body["knowledge"]["asked"] = {**{k: _count(asked.get(k)) for k in ("jobs", "gave", "no_answer", "timed_out")},
                                      "kept": len(body["knowledge"]["topics"])}
        changed = self._consent_changed(st)            # said on her plan card, before her Yes (D118)
        search_closed = self._search_closed(body)      # said on her plan card too, so her Yes covers it (D151)
        local_looks = st.get("where") != "cloud" and bool(self._local_goal().get("makes"))
        b = self.store.set_plan(build_id, body, {**st, **({"consent_changed": changed} if changed else {}), **({"looks": True} if local_looks else {})},
                                self._company(st),
                                installed=self._installed_ids(),
                                rows=self.registry.list() if self.registry is not None else [],
                                shop=self._store_rows(), models=self._model_offers(build_id), search_closed=search_closed)
        kp = b.get("knowledge_plan") or {}
        # D121: a cloud job with no app still gets a Yes card, since after her Yes it looks further with the company's model.
        looks = self.looks_after_yes(b, st) and any(not j["plan"]["reuse"] and not j.get("pasted") for j in b["jobs"])
        if local_looks:
            self.store.change(build_id, lambda x: x.update(looks_after_yes=True))       # chat reads it (wiring)
        if not looks and not any(j["plan"]["reuse"] or j["plan"]["candidates"] or j["plan"].get("offer") or j["plan"].get("store")
                                 for j in b["jobs"]) and not kp.get("topics") and not (b.get("card") or {}).get("model_offers"):
            # Nothing to install: no Yes card at all (one that could be answered would cover nothing).
            def empty(x: dict) -> None:
                x["consent"]["said"] = f"{store.PLAN_BRIDGE_CLOSED_LINE} {store.EMPTY_PLAN_LINE}" if search_closed                     else store.EMPTY_PLAN_LINE
                if x.get("books_only"):
                    x["consent"]["said"] = BOOKS_LOCKED_LINE if self._household() is None else BOOKS_NO_WORDS_LINE
                for j in x["jobs"]:
                    j["run"].update(state="nothing_found", line=store.nothing_line(j, find_mod.pasted_any(x["jobs"])))
            self.store.change(build_id, empty)
            return {"ok": True, "consent": None}
        covers = {"build": build_id, "plan_sha": b["plan_sha"], "card": b["card"],
                  "install_hosts": b["envelope"]["install_hosts"]}
        if (st.get("where") == "cloud" or local_looks) and bridge_rules.READ_PUBLIC_PAGES:
            covers["reads_public"] = True              # her Yes for public reads while this build runs (D121)
        if (st.get("where") == "cloud" or local_looks) and bridge_rules.TRY_FOUND_APPS:
            covers["tries_found"] = True               # the card's "other apps" sentence, with its Yes (D121)
        if kp.get("hosts"):
            # The Core's own knowledge reads (lane I11): granted from this Yes only while this build runs.
            covers["knowledge_hosts"] = list(kp["hosts"])
        try:
            cid = self.ports.consent_request("package", store.bridge_target(build_id), covers, False,
                                             bound={"plan_sha": b["plan_sha"]})
        except NotBuilt as exc:
            self.store.change(build_id, lambda x: x["consent"].update(said=f"{exc} The plan can't get its Yes yet."))
            return {"ok": True, "consent": None}

        self._card_out(build_id)

        def fn(x: dict) -> None:
            x["consent"]["id"] = cid
            for j in x["jobs"]:
                j["run"]["state"] = "waiting_yes"
        self.store.change(build_id, fn)
        return {"ok": True, "consent": cid}

    def agent_fits(self, build_id: str, body: dict) -> dict:
        """Does an installed package fit a job? The brain gets the job's words and the Concierge's normal tools;
        it fits only when it picks one of that package's tools (``puffpai check`` item 12's test)."""
        self._refuse_if_paused()
        self._build(build_id, after_yes=False)
        body = body if isinstance(body, dict) else {}
        pkg, ask = body.get("package", ""), body.get("ask")
        if not isinstance(pkg, str) or not isinstance(ask, str) or not ask.strip():
            raise ValueError("Send {ask, package?}: the job's words, and a package id to check (or none: whose "
                             "tool does the brain pick?).")
        if body.get("store") and pkg:
            raise ValueError("A store ask names no package: it asks whose store app's tool the brain picks.")
        if pkg and (self.registry is None or self.registry.get(pkg) is None):
            raise KeyError(pkg)
        if body.get("store"):
            # Lane I10c: the same ask, with the PuffPAI store's apps' listed tools in the table; whose did it pick?
            shop = self._store_rows()
            if not shop:
                return {"ok": False, "asked": False, "called": [], "reason": "the PuffPAI store has no app to offer"}
            try:
                return self.ports.brain_picks("", " ".join(ask.split())[:300], store=shop)
            except Exception as exc:  # noqa: BLE001 - NotBuilt, or a brain that can't take the store: not asked
                return {"ok": False, "asked": False, "called": [], "reason": str(exc) or type(exc).__name__}
        try:
            return self.ports.brain_picks(pkg, " ".join(ask.split())[:300])
        except NotBuilt as exc:
            return {"ok": False, "asked": False, "called": [], "reason": str(exc)}

    def agent_now(self, build_id: str, body: dict) -> dict:
        """What Toolsmith does now while it plans: one plain line, kept on the build (never on the card)."""
        self._refuse_if_paused()
        self._build(build_id, after_yes=False)
        raw = (body or {}).get("line") if isinstance(body, dict) else None
        if not isinstance(raw, str):
            raise ValueError("line is one plain string.")
        line = redact.scrub_text(" ".join(raw.split()))[:300]
        self.store.change(build_id, lambda x: x.update(now=line, now_at=settings_mod.now()))
        return {"ok": True}

    def agent_job_status(self, build_id: str, job_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        b = self._build(build_id)
        job = self._job_of(b, job_id)
        body = redact.scrub(body) if isinstance(body, dict) else {}
        if body:
            self.store.job_update(build_id, job_id, body)
        result = body.get("result") if isinstance(body.get("result"), dict) else None
        if body.get("state") == "working" and result:
            pkg = str(result.get("package") or "")
            if pkg and (pkg in self.trials.installed_ids(build_id) or pkg == job["plan"]["reuse"]):
                self.store.remember_proven(job, pkg)
        with self._lock:
            nxt = job_id in self._next
            self._next.discard(job_id)
        return {"ok": True, "stop": self._stopping, "next": nxt}

    def agent_holds_ask(self, build_id: str, job_id: str) -> dict:
        """ONE hold card for the job (lane I4b): the candidates Drawbridge couldn't fully check, set aside. The
        Core writes the card; nothing here answers it."""
        self._refuse_if_paused()
        b = self._build(build_id)
        self._job_of(b, job_id)
        return self.trials.holds_ask(build_id, job_id)

    def agent_holds_state(self, build_id: str, job_id: str) -> dict:
        """Her answer to the job's hold card: which set-aside candidates Toolsmith may now try."""
        self._refuse_if_paused()
        b = self._build(build_id)
        self._job_of(b, job_id)
        return self.trials.holds_state(build_id, job_id)

    def agent_registry(self, build_id: str) -> dict:
        self._refuse_if_paused()
        self._build(build_id, after_yes=False)
        rows = self.registry.list() if self.registry is not None else []
        keep = ("id", "name", "kind", "origin", "status", "port", "ui", "health", "built_with", "description")
        out = []
        for r in rows:
            row = {k: r.get(k) for k in keep}
            # What it can do, so a job it already covers reuses it (03 Part 2, question 2).
            row["abilities"] = [{"tool": str(a.get("tool") or ""), "description": str(a.get("description") or "")}
                                for a in r.get("abilities") or [] if isinstance(a, dict)]
            row["examples"] = self._examples(str(r.get("id") or ""))
            out.append(row)
        return {"rows": out, "proven": self.store.proven(), "store": self._store_rows()}

    def _store_rows(self) -> list[dict]:
        """The PuffPAI store's apps (built in, kind app, not installed; ``GET /v1/packages/available``'s list), with
        what their tools do from their manifests (lane I10c), then the official store's verified rows from its
        saved copy, never the network (D96). [] when there is no Registry to tell."""
        if self.registry is None:
            return []
        from ...packages.store_apps import store_apps
        try:
            return store_apps(self.registry, getattr(getattr(self.ports, "packages", None), "store", None))
        except Exception:  # noqa: BLE001 - no store list: no store suggestion
            logger.exception("the PuffPAI store's list could not be read")
            return []

    def store_adds(self, build_id: str) -> list[dict]:
        """The store apps she ticked on this build's plan card (fixed at her Yes) that are not installed yet:
        ``[{package, name, job_name}]``, ``package`` as the add takes it (an id, or ``store:<id>``, D96). The wizard
        adds each with its own Yes card before the build starts."""
        b = self.store.read(build_id)
        if b is None:
            raise KeyError(build_id)
        fixed = b.get("use_fixed") if isinstance(b.get("use_fixed"), dict) else {}
        ticked = fixed.get("jobs") if isinstance(fixed.get("jobs"), dict) else {}
        out, seen = [], set()
        for j in b.get("jobs") or []:
            shop = j["plan"].get("store") or {}
            pkg = shop.get("package")
            if pkg and ticked.get(j["id"]) == pkg and pkg not in seen \
                    and (self.registry is None or self.registry.get(store.bare_id(pkg)) is None):
                seen.add(pkg)
                out.append({"package": pkg, "name": shop.get("name") or pkg, "job_name": j["name"]})
        return out

    def _examples(self, pkg_id: str) -> dict:
        """The sample asks in an installed package's manifest (the Core's one reader), or {}."""
        read = getattr(getattr(self.ports, "steward", None), "read_manifest", None)
        try:
            m = read(pkg_id) if callable(read) and pkg_id else None
        except Exception:  # noqa: BLE001 - no manifest: no examples
            m = None
        got = ((m or {}).get("abilities") or {}).get("examples") if isinstance(m, dict) else None
        if not isinstance(got, dict):
            return {}
        return {str(k): [str(x) for x in v][:5] for k, v in got.items() if isinstance(v, list)}

    def agent_read(self, build_id: str, body: dict) -> dict:
        """One public page for this cloud build (D121, :mod:`.public_read`). Plain words where it says no."""
        self._refuse_if_paused()
        return public_read.read(self, self._build(build_id, after_yes=False), body if isinstance(body, dict) else {})

    def agent_web(self, build_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        b = self._build(build_id, after_yes=False)
        # One plain form for every address, before any check, and only that form is sent (lane I15 re-check).
        url = address.canonical(body.get("url"))
        if url is None:
            raise Refused(NOT_PLAIN_LINE)
        # A search place takes only addresses shaped like its row, so every word is in the one place screened below.
        # An API she added counts only with her Yes checked right now (owner_apis).
        owner = self._owner_rows()
        read = address.row_read(url, owner=owner, prefer=body.get("web_key") if isinstance(body.get("web_key"), str) else "")
        if read is address.REFUSED:
            raise Refused(NOT_PLAIN_LINE)
        mine = owner.get(read.row) if read is not None else None
        if mine is not None and not b.get("planning") and not self._keys_after_plan(b):
            raise Refused(OWNER_API_NOT_NOW_LINE)
        most = read.max_bytes if read is not None and read.kind == "list" and read.max_bytes else WEB_MAX_BYTES
        most = min(most, int(mine["max_bytes"])) if mine is not None else most
        try:
            limit = max(1, min(int(body.get("max_bytes") or 2_000_000), most))
        except (TypeError, ValueError):
            limit = 2_000_000
        if self._address_held(build_id, url, owner):
            raise Refused(SEARCH_HELD_LINE)
        web_key = self._web_key_row(b, url, read, body.get("web_key"), owner)
        keyed = body.get("keyed") is True and github_keyed_url(url)
        if web_key is not None:
            extra, key_state = self._web_key(build_id, web_key)
            if not extra:
                raise Refused(WEB_KEY_NEEDED_LINE.format(name=web_key.get("name") or "That place"))
            secrets = self._key_forms(extra[web_key["header"]])
        else:
            extra, key_state = self._github_key(build_id) if keyed else ({}, "")
            secrets = [extra["Authorization"][len("Bearer "):]] if extra else []
            if not extra and own_list_url(url):        # D135: read with her key or not at all, so nothing is sent
                return {"status": 0, "text": "", **({"key": key_state} if key_state else {})}
        timeout_s = _timeout_s(body.get("timeout_s"))
        accept = body.get("accept") if isinstance(body.get("accept"), str) and body["accept"] in WEB_ACCEPTS else ""
        try:
            try:
                if mine is not None:
                    status, headers, data = self._owner_get(mine, url, extra, accept=accept, max_bytes=limit,
                                                            timeout_s=timeout_s)
                else:
                    status, headers, data = self.ports.http_get(url, accept=accept, max_bytes=limit,
                                                                **({"headers": extra} if extra else {}),
                                                                **({} if timeout_s is None else {"timeout_s": timeout_s}))
            except Refused as exc:
                # The Core's own line (the Bridge closed, a redirect): no header in it, scrubbed all the same.
                raise Refused(redact.scrub(str(exc), secrets)) from None
            except (OSError, http.client.HTTPException) as exc:
                # Sent, but no answer: the plain reason (fixed words, never the error's own text) and the error's
                # class go back for the build log (lane I15).
                why = find_mod.net_reason(exc)
                out = {"status": 0, "text": "", "error": type(exc).__name__,
                       "said": "That address could not be reached" + (f": {why}." if why else ".")}
            except Exception as exc:  # noqa: BLE001 - e.g. http.client's "Invalid header value b'...'": never echoed
                out = {"status": 0, "text": "", "error": type(exc).__name__,
                       "said": "That address could not be reached."}
            else:
                if web_key is not None and int(status) in WEB_KEY_NO_MORE:
                    key_state, data = "refused", b""       # that row is off for the plan: never a second send
                    self.store.change(build_id, lambda x: x.update(web_off=sorted({*(x.get("web_off") or []), read.row})))
                elif keyed and key_state == "sent" and int(status) == 200:
                    # A keyed answer can see the owner's private projects: none of them ever reaches Toolsmith.
                    path = urllib.parse.urlsplit(url).path
                    keep = find_mod.public_search if path == "/search/repositories" else \
                        find_mod.public_list if own_list_url(url) else find_mod.public_repo
                    data = keep(data)
                if mine is not None and secrets:
                    data = owner_apis.clean_reply(data, secrets[0])
                out = {"status": int(status), "text": data.decode("utf-8", errors="replace"),
                       "headers": find_mod.rate_headers(headers)}
        finally:
            extra.clear()
        if key_state:
            out["key"] = key_state                 # "sent", "unusable" or "refused": never the key itself
        self._note_searches(build_id, url, read)
        return redact.scrub(out, secrets, bearer=mine is None)

    @staticmethod
    def _key_forms(key: str) -> list[str]:
        """The forms the plain scrub cuts: the bare key, and as it goes in an address. Never a row's ``key_prefix``: the
        scrub also cuts a secret's long pieces, and that would take the word "Bearer" out of every answer. A key's
        strict shape holds no space, so the last word is the bare key, first in the list. The sent form goes whole in
        ``owner_apis.clean_reply``."""
        bare = key.rsplit(" ", 1)[-1]
        return list(dict.fromkeys([bare, urllib.parse.quote(bare, safe=""), urllib.parse.quote_plus(bare)]))

    def _owner_get(self, row: dict, url: str, extra: dict, *, accept: str = "", max_bytes: int = 1_000_000,
                   timeout_s: float | None = None, **more: Any) -> tuple[int, dict, bytes]:
        """One search on an API she added, its address checked and pinned (``Ports.owner_get``). Her key goes where the
        row says: on a query place it is added past this point, so Toolsmith's process never sees it. No such send here:
        nothing is sent."""
        send = getattr(self.ports, "owner_get", None)
        if not callable(send):
            raise Refused(OWNER_API_CANT_SEND_LINE)
        in_query = row["key_in"] == "query"
        return send(url, accept=accept, max_bytes=min(max_bytes, int(row["max_bytes"])),
                    timeout_s=float(row["timeout_s"]) if timeout_s is None else timeout_s,
                    **({"key_query": extra} if in_query and extra else {"headers": extra} if extra else {}), **more)

    def _web_key_row(self, b: dict, url: str, read: Any, asked: Any, owner: dict | None = None) -> dict | None:
        """The ``web`` row whose key this send may carry, or None when it carries none. A web row's host takes only
        its search with its key: no key asked, another row's key, a key off for this plan, a build past its plan or
        a closed Bridge is refused here, before the vault lends anything."""
        if read is not None and read.keyed and read.row != asked:
            raise Refused(WEB_KEY_NEEDED_LINE.format(name="That place"))
        if asked in (None, ""):
            return None
        if read is None or not read.keyed or not isinstance(asked, str):
            raise Refused(WEB_KEY_NO_ROW_LINE)
        mine = (owner or {}).get(asked)
        shipped = address.key_rows().get(asked)
        row = shipped if mine is None else mine
        if row is None or urllib.parse.urlsplit(url).hostname != row["host"]:
            raise Refused(WEB_KEY_NO_ROW_LINE)
        if not b.get("planning") and not self._keys_after_plan(b):
            raise Refused(WEB_KEY_NOT_NOW_LINE)
        if asked in (b.get("web_off") or []):
            raise Refused(WEB_KEY_REFUSED_LINE.format(name=row.get("name") or asked))
        peek = getattr(self.ports, "bridge_peek", None)
        try:
            open_now = callable(peek) and peek("toolsmith:search", row["host"]) is True \
                and (mine is not None or self._shipped_key_yes(row))
        except Exception:  # noqa: BLE001 - no decision means no
            open_now = False
        if not open_now:
            raise Refused(WEB_KEY_BRIDGE_LINE.format(host=row["host"]))
        return row

    def _web_key(self, build_id: str, row: dict) -> tuple[dict, str]:
        """``({<the row's header>: key}, "sent")`` when the vault lends her key for this row's one host and it has a
        key's strict shape; ``({}, "unusable")`` or ``({}, "")`` otherwise. Lent for this one search, ended at once;
        the value is never logged or returned. A row she added lends only on the host its key was saved for."""
        lend, use, end = (getattr(self.ports, n, None) for n in ("lend_key", "key_value", "end_lease"))
        if not (callable(lend) and callable(use)) or (row.get("_owner") and not owner_apis.key_bound(row)):
            return {}, ""
        purpose = _web_purpose(row)
        try:
            handle = lend(row["key"], "toolsmith", purpose, hosts=(row["host"],), job=build_id)
        except Exception:  # noqa: BLE001 - none saved, the vault locked: nothing is sent
            return {}, ""
        try:
            key = use(handle, borrower="toolsmith", purpose=purpose, host=row["host"])
        except Exception:  # noqa: BLE001
            key = ""
        finally:
            self._end_quietly(end, handle)
        if not isinstance(key, str) or not key:
            return {}, ""
        if not web_key_ok(key):                        # the bare key: its shape holds no space, with a prefix or without
            return {}, "unusable"
        # Only a row she added has a prefix, one of owner_apis.KEY_PREFIXES: a shipped row's data can't add one.
        prefix = row.get("key_prefix") if row.get("_owner") else ""
        return {row["header"]: f"{prefix} {key}" if prefix else key}, "sent"

    def _web_key_lends(self, build_id: str, row: dict, any_host: bool = False) -> bool:
        """Does the vault lend this row's key now? The lease ends at once and its bytes are never read. *any_host*: only
        whether a key is kept at all, wherever it was saved for, so she reads the right line. Nothing is sent on it."""
        lend, end = getattr(self.ports, "lend_key", None), getattr(self.ports, "end_lease", None)
        if not callable(lend) or (row.get("_owner") and not any_host and not owner_apis.key_bound(row)):
            return False
        try:
            handle = lend(row["key"], "toolsmith", _web_purpose(row), hosts=(row["host"],), job=build_id)
        except Exception:  # noqa: BLE001 - none saved, the vault locked: that row is off
            return False
        self._end_quietly(end, handle)
        return True

    @staticmethod
    def _end_quietly(end: Any, handle: Any) -> None:
        if callable(end):
            try:
                end(handle)
            except Exception:  # noqa: BLE001
                pass

    def _note_searches(self, build_id: str, url: str, read: Any = None) -> None:
        """A search the Core sent while this build plans: its words go on the plan card (lane I15), so the owner sees
        every search before the Yes. GitHub's in ``github_searches``; another place's in ``searches_by_place`` by its
        row's id; a ``list`` row read whole (no words at all) in ``read_whole``."""
        if read is not None and read.kind in ("search", "list"):
            def by_place(x: dict) -> None:
                if not x.get("planning"):
                    return
                if read.kind == "list":
                    if read.row not in x.setdefault("read_whole", []):
                        x["read_whole"].append(read.row)
                    return
                seen = x.setdefault("searches_by_place", {}).setdefault(read.row, [])
                for w in read.words:
                    if w not in seen and len(seen) < 40:
                        seen.append(w)
            self.store.change(build_id, by_place)
            return
        parts = urllib.parse.urlsplit(url)
        if parts.hostname not in address.SCREENED_HOSTS or not parts.path.startswith("/search"):
            return
        words = address.searches_of(url)
        if not words:
            return

        def fn(x: dict) -> None:
            if not x.get("planning"):
                return
            seen = x.setdefault("github_searches", [])
            for w in words:
                if w not in seen and len(seen) < 40:
                    seen.append(w)
        self.store.change(build_id, fn)

    def _household(self) -> list[str] | None:
        """The household's names (the people list, the owner's own name and what the owner is called), or None
        when they can't be read."""
        names = getattr(self.ports, "household_names", None)
        try:
            return list(names()) if callable(names) else []
        except Exception:  # noqa: BLE001
            return None

    def _address_held(self, build_id: str, url: str, owner: dict | None = None) -> bool:
        """True when canonical *url*, on a search place (a GitHub host or a row's), holds a search whose words the
        Core's screen would not send, whoever built it (Toolsmith's search or a try's docs read). Only the owner's
        own words are screened: a search's ``q`` (any case) or its row's words, made from the goal, the job names and
        the brain's queries. The rest of the address (a repository's owner and name, a README path) comes from the
        place's own answers, is public, and is never screened for names (lane I15 rebalance). A word sent with hyphens
        (a PyPI name, a one-word row) is screened with them as spaces too: "jane-at-example-dot-com" is an address."""
        github = urllib.parse.urlsplit(url).hostname in address.SCREENED_HOSTS
        if not github and address.row_read(url, owner=owner) is None:
            return False
        for q in address.searches_of(url, owner):
            forms = [q] + ([q.replace("-", " ")] if not github and "-" in q else [])
            if any(self._screened(build_id, [f], held_as="searches") != [f] for f in forms):
                return True
        return False

    def test_owner_api(self, api_id: str, word: Any, by: str = "owner") -> dict:
        """Her Test of one API she added: one search, with her own typed word, under her own act of pressing it. No plan and
        no search Yes: her press is her Yes for that one request (``Ports.one_send_yes``), and the Bridge still decides.
        ``{ok, line, rows}``, at most 5 rows, and ``answered: true`` only when it answered 200 and nothing read. KeyError:
        no such row. Only a Test that read results turns ``tested`` on."""
        self._refuse_if_paused()
        got = owner_apis.rows(self.state)[api_id]
        row = dict(got, header=got["key_name"], _owner=True, _live=True)
        name, sha = row["name"], owner_apis.row_sha(row)

        def said(line: str) -> dict:
            return {"ok": False, "line": line, "rows": []}
        text = " ".join(word.split()).strip(" .,:;'-") if isinstance(word, str) else ""
        if not text or len(text) > OWNER_API_TEST_WORDS:
            return said(OWNER_API_WORD_LINE)
        # The same screen as a plan's words, a word with hyphens as spaces too. Her own word is its own context.
        screen, household = getattr(self.ports, "screen_outbound", None), self._household()
        if not callable(screen) or household is None:
            return said(OWNER_API_WORD_HELD_LINE)
        url = address.canonical(shelves.search_url(row, text))
        read = address.row_read(url, owner={api_id: row}, prefer=api_id) if url else address.REFUSED
        if read is None or read is address.REFUSED or read.row != api_id:
            return said(OWNER_API_WORD_SHAPE_LINE)
        for q in read.words:
            for form in {q, q.replace("-", " ")}:
                if knowledge_mod.clean_topics([form], screen, household=household, context=form) != [form]:
                    return said(OWNER_API_WORD_HELD_LINE)
        extra: dict = {}
        secrets: list[str] = []
        if row["key"] and not owner_apis.key_bound(row):
            kept = row["key_host"] or self._web_key_lends("owner-test", row, any_host=True)
            return said(owner_apis.MOVED_KEY_LINE if kept else OWNER_API_NO_KEY_LINE.format(name=name))
        if row["key"]:
            extra, state = self._web_key("owner-test", row)
            if not extra:
                return said(OWNER_API_BAD_KEY_LINE if state == "unusable" else OWNER_API_NO_KEY_LINE.format(name=name))
            secrets = self._key_forms(extra[row["header"]])
        try:
            one_send = getattr(self.ports, "one_send_yes", None)
            if not callable(one_send):
                return said(OWNER_API_CANT_SEND_LINE)
            with one_send(row["host"], by) as subject:
                status, _headers, data = self._owner_get(row, url, extra, accept="application/json", subject=subject,
                                                         phase="run")
        except Refused as exc:
            return said(redact.scrub(str(exc), secrets))
        except (OSError, http.client.HTTPException) as exc:
            why = find_mod.net_reason(exc)              # fixed words, never the error's own text
            return said("That address could not be reached" + (f": {why}." if why else "."))
        except Exception:  # noqa: BLE001 - e.g. a consent store that can't write: never echoed
            return said("That address could not be reached.")
        finally:
            extra.clear()
        if int(status) in (401, 403) and row["key"]:
            return said(OWNER_API_KEY_SAID_NO_LINE.format(name=name, status=int(status)))
        if int(status) != 200:
            return said(OWNER_API_SAID_NO_LINE.format(name=name, status=int(status)))
        if secrets:
            data = owner_apis.clean_reply(data, secrets[0])    # before the reader cuts anything
        try:
            found = shelves.READERS["generic"](api_id, dict(row, _url=url), data)
        except Exception:  # noqa: BLE001 - an answer nobody can read reads as nothing
            found = []
        shown = owner_apis.shown_rows(found)                   # the card's own screen: nothing passes here it wouldn't
        owner_apis.set_tested(self.state, api_id, sha, bool(shown))
        if not shown:                                  # it answered 200: the one case naming the fields can help
            return {**said(OWNER_API_NO_READ_LINE), "answered": True}
        count = f"{len(shown)} result" + ("" if len(shown) == 1 else "s")
        return redact.scrub({"ok": True, "line": OWNER_API_READ_LINE.format(count=count, name=name), "rows": shown[:5]},
                            secrets, bearer=False)

    def _screened(self, build_id: str, words: Any, held_as: str = "") -> list[str]:
        """Words that are sent out as searches (knowledge topics, app search queries), as the screen keeps them:
        one the outbound privacy screen would change (a key, a personal detail) is never kept; no screen here
        keeps none (fail closed). A name from the household (the people list, her own name and what she is
        called) is never sent, nor a capitalised word her goal never used where the model's style makes it a
        name. Can't read the household: none at all (fail closed)."""
        screen = getattr(self.ports, "screen_outbound", None)
        household = self._household()
        if not callable(screen) or household is None:
            return []
        known = self.store.read(build_id) or {}
        context = " ".join([str(known.get("goal") or "")] + [f"{j.get('name') or ''} {j.get('does') or ''}"
                                                             for j in known.get("jobs_in") or []
                                                             if isinstance(j, dict)])
        held: list[str] = []
        kept = knowledge_mod.clean_topics(words, screen, household=household, context=context, held=held)
        if held_as and held:
            self._note_held(build_id, held_as, held)
        return kept

    def _note_held(self, build_id: str, kind: str, words: list[str]) -> None:
        """Searches or topics held because they used a household or You name, kept on the build (``held_names``)
        for the owner's own plan card only: never in a Core reply to Toolsmith after the screen (``agent_build``
        and ``/queries`` do not carry it; ``/brain/json`` is the local brain's own answer, from before the screen),
        never in an address, never in the Bridge's outside log, never to a cloud model (lane I15)."""
        def fn(x: dict) -> None:
            if not x.get("planning"):
                return
            got = x.setdefault("held_names", {}).setdefault(kind, [])
            for w in words:
                w = " ".join(str(w).split())[:80]
                if w and w.lower() not in {g.lower() for g in got} and len(got) < 20:
                    got.append(w)
        self.store.change(build_id, fn)

    def agent_queries(self, build_id: str, body: dict) -> dict:
        """The app search queries the brain gave, screened like the knowledge topics (lane I15)."""
        self._refuse_if_paused()
        b = self._build(build_id, after_yes=False)
        allowed = QUERIES_PER_JOB * len(find_mod.jobs_for(str(b.get("goal") or ""), b.get("jobs_in")))
        with self._lock:
            asked = self._queries_asked.get(build_id, 0) + 1
            self._queries_asked[build_id] = asked
        if asked > allowed and not self._cloud_for(b, self.settings()):    # D121: a cloud build has no such cap
            return {"queries": []}                 # over the cap: none kept (fail closed), nothing said why
        raw = body.get("queries") if isinstance(body, dict) else None
        return {"queries": self._screened(build_id, raw[:15] if isinstance(raw, list) else [], held_as="searches")}

    def _github_key(self, build_id: str) -> tuple[dict, str]:
        """``({"Authorization": ...}, "sent")`` when a GitHub key is saved, the vault lends it and it has a key's
        strict shape (:func:`github_key_ok`); ``({}, "unusable")`` when the saved one has not; ``({}, "")`` when
        none is saved or the vault is locked (searches go unkeyed). Lent for api.github.com only: after her Yes
        from the build's own lease (D68, kept for the build), before it (planning) per search and ended at once;
        the value is never logged or returned."""
        lend, use, end = (getattr(self.ports, n, None) for n in ("lend_key", "key_value", "end_lease"))
        if not (callable(lend) and callable(use)):
            return {}, ""
        key = ""
        with self._lock:
            kept = self._gh_leases.get(build_id)
        if kept:                                       # the build's own lease, from her Yes (D68)
            try:
                key = use(kept, borrower="toolsmith", purpose=GITHUB_PURPOSE, host=GITHUB_API_HOST)
            except Exception:  # noqa: BLE001 - ended (a hand lock, 3 hours): let it go, borrow per search
                with self._lock:
                    if self._gh_leases.get(build_id) == kept:
                        self._gh_leases.pop(build_id, None)
                self._end_handle(kept)
                key = ""
        if not key:
            try:
                handle = lend(GITHUB_KEY_NAME, "toolsmith", GITHUB_PURPOSE, hosts=(GITHUB_API_HOST,), job=build_id)
            except Exception:  # noqa: BLE001 - none saved, the vault locked: unkeyed
                return {}, ""
            try:
                key = use(handle, borrower="toolsmith", purpose=GITHUB_PURPOSE, host=GITHUB_API_HOST)
            except Exception:  # noqa: BLE001
                key = ""
            finally:
                if callable(end):
                    try:
                        end(handle)
                    except Exception:  # noqa: BLE001
                        pass
        key = key if isinstance(key, str) else ""
        if not key:
            return {}, ""
        if not github_key_ok(key):
            return {}, "unusable"                  # never sent, never shown: "the saved GitHub key is not usable"
        return {"Authorization": f"Bearer {key}"}, "sent"

    def agent_knowledge_find(self, build_id: str, body: Any) -> dict:
        """While planning: the Core looks for downloads about these topics (D99), each topic through the same screen
        as every other outbound word, under her search Yes, within its own time (``knowledge.Fetcher.find``). Only while
        planning and once per build; otherwise nothing is sent."""
        self._refuse_if_paused()
        self._build(build_id, after_yes=False)
        first: list[bool] = []

        def once(x: dict) -> None:
            if x.get("planning") and not x.get("knowledge_find_asked"):
                x["knowledge_find_asked"] = True
                first.append(True)
        self.store.change(build_id, once)
        if not first:
            return {"ok": True, "found": {}}
        raw = body.get("topics") if isinstance(body, dict) else None
        kept = self._screened(build_id, raw[:15] if isinstance(raw, list) else [], held_as="topics")
        return self.knowledge.find(build_id, kept) if kept else {"ok": True, "found": {}}

    def agent_knowledge(self, build_id: str) -> dict:
        """One step of the build's knowledge fetch (lane I11), only after her Yes: one search or one article or
        book, read by the Core itself through the Bridge (``knowledge.Fetcher``). ``{done, now, line, stop?}``."""
        self._refuse_if_paused()
        self._build(build_id)                          # no Yes: refused
        return self.knowledge.step(build_id)

    def agent_library(self, build_id: str, job_id: str) -> dict:
        """Lane I19e (D71, 03 Part 4 "Knowledge"): once the build's jobs and its knowledge fetch have ended, a job
        no app works for is proved by the library: the brain's own search, over this build's fetched items only,
        with the job's own words. Real passages: the Core marks the job working, and every screen says no app yet, your library has
        some reading on this (D123: :func:`store.library_job`). None: the
        job stays as it is. Never a job she pasted or ticked an app for; local only, nothing goes out."""
        self._refuse_if_paused()
        b = self._build(build_id)
        job = self._job_of(b, job_id)
        search = getattr(self.ports, "knowledge_search", None)
        if self._stopping or not callable(search) or not knowledge_mod.provable(job):
            return {"ok": True, "working": False}
        from ...zone1 import fetched
        run = b.get("knowledge_run") if isinstance(b.get("knowledge_run"), dict) else {}
        index = getattr(self.ports, "knowledge_index", None)
        for item in run.get("items") or []:
            if isinstance(item, dict) and item.get("indexed") is False and callable(index):
                try:                               # kept but not searchable yet: indexed now, if it can be
                    index(fetched.root(self.state) / str(item.get("source")) / str(item.get("file")))
                except Exception:  # noqa: BLE001 - it stays out of this search
                    pass
        try:
            got = knowledge_mod.library_proof(b, job, search, fetched.root(self.state))
        except Exception:  # noqa: BLE001 - no proof: the job keeps its own line
            logger.exception("the library check for job %s could not run", job_id)
            return {"ok": True, "working": False}
        if not got:
            return {"ok": True, "working": False}
        if not knowledge_mod.enough(got):
            # Too thin to call it working: the job keeps its state and its line, plus one honest line.
            thin = knowledge_mod.thin_line(got)
            old = str((job.get("run") or {}).get("line") or "")
            if thin not in old:
                self.store.job_update(build_id, job_id, {"state": job["run"]["state"],
                                                         "line": f"{old[:599 - len(thin)]} {thin}".strip()})
            return {"ok": True, "working": False, "line": thin}
        line = knowledge_mod.proof_line(job, got)
        tried = [str(t.get("name")) for t in (job.get("run") or {}).get("tried") or [] if isinstance(t, dict)]
        if tried:
            line += f" Apps tried first: {', '.join(dict.fromkeys(tried))}."
        result = {"by": "knowledge", "count": got["count"], "sources": got["sources"], "titles": got["titles"],
                  "proof": {"result": "working", "search": got["query"]}}
        self.store.job_update(build_id, job_id, {"state": "working", "line": line[:600], "trying": None,
                                                 "result": result})
        return {"ok": True, "working": True, "line": line[:600]}

    def _hide(self, text: Any) -> str:
        text = "" if text is None else str(text)
        if not text:
            return text
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        got = self._hidden.get(key)
        if got is None:
            got = self.ports.hide_ai_lines(text)
            if len(self._hidden) > 4000:
                self._hidden.clear()
            self._hidden[key] = got
        return got

    def _hide_messages(self, messages: Any) -> list[dict]:
        """Lines aimed at AIs hidden from every user-side text and tool result (Drawbridge, 09 section 3)."""
        out = []
        for m in messages if isinstance(messages, list) else []:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
                raise ValueError("A message is {role: user | assistant, content: [blocks]}.")
            blocks = [b for b in (m.get("content") or []) if isinstance(b, dict)]
            if m["role"] == "user":
                shown = []
                for b in blocks:
                    if b.get("type") == "text":
                        shown.append({**b, "text": self._hide(b.get("text"))})
                    elif b.get("type") == "tool_result":
                        c = b.get("content")
                        shown.append({**b, "content": self._hide(c if isinstance(c, str) else json.dumps(c))})
                    else:
                        shown.append(b)
                blocks = shown
            out.append({"role": m["role"], "content": blocks})
        return out

    def _ledger(self, build_id: str) -> sender.Ledger:
        with self._lock:
            if build_id not in self._ledgers:
                self._ledgers[build_id] = sender.Ledger(self.store.dir(build_id) / "usage.json")
            return self._ledgers[build_id]

    def _sender(self, build_id: str) -> sender.FrontierSender:
        """The build's sender, made again when her choice changes (a lower cap holds at once)."""
        with self._lock:
            st = self.settings()
            key = (st.get("provider"), st.get("model"), st.get("cost_cap_usd"), (st.get("consent") or {}).get("sha"))
            got = self._senders.get(build_id)
            if got is not None and getattr(got, "choice_key", None) == key:
                return got
            prov = self.providers().get(str(st.get("provider") or ""))
            if prov is None:
                raise CloudHeld("provider", "The provider you picked for Toolsmith is not installed.")
            live = getattr(self.ports, "cloud_live", None)
            if callable(live) and not live(prov["id"], (st.get("consent") or {}).get("sha")):
                # A choice from before lane I8a (or one taken back): no Yes on record, so nothing is sent.
                company = str((prov.get("toolsmith_model") or {}).get("company") or prov["id"])
                raise CloudHeld("consent", NO_CLOUD_YES_LINE.format(company=company))
            got = sender.FrontierSender(self.ports, prov, model=str(st.get("model") or ""), build_id=build_id,
                                        cap_usd=st["cost_cap_usd"], ledger=self._ledger(build_id),
                                        consent_given=st["consent"]["sha"], sleep=self.sleep,
                                        opaque=self._opaque)
            old = self._senders.get(build_id)
            if old is not None:
                old.close()
            got.choice_key = key
            self._senders[build_id] = got
            return got

    def agent_model(self, build_id: str, body: dict) -> dict:
        """One model turn for Toolsmith: this computer's model, or lend and send (09 section 4)."""
        self._refuse_if_paused()
        b = self._build(build_id)
        if len(json.dumps(body, ensure_ascii=False)) > MAX_REQUEST_BYTES:
            raise Refused("This app's notes grew too long for one try.")
        st = self.settings()
        cloud = self._cloud_for(b, st) and not body.get("local_only")
        if body.get("probe"):
            if not cloud:
                return {"ok": True}
            self._sender(build_id).probe()
            return {"ok": True}
        # The list decides the tools, since both have a read_page. A job's look (D121) and a try's done for a service
        # (D124) run only on the company's model.
        listed = body.get("tool_list")
        if (listed == "cloud_try" or (listed == "find" and not self._local_goal().get("talks"))) and not cloud:
            raise Refused("A job looks for what it needs only with a model that can use tools.")
        allowed = scout.TOOLS if listed == "find" else agent_tries.CLOUD_TOOLS if listed == "cloud_try" else agent_tries.TOOLS
        names = [str(t.get("name")) for t in body.get("tools") or [] if isinstance(t, dict)]
        if any(n not in {t["name"] for t in allowed} for n in names):
            raise Refused("A model may only get Toolsmith's own tools.")
        tools = [t for t in allowed if t["name"] in names]
        system = str(body.get("system") or "")[:20_000]
        messages = self._hide_messages(body.get("messages"))
        if cloud:
            s = self._sender(build_id)
            got = s.turn(system, messages, tools, max_tokens=body.get("max_tokens") or sender.MAX_TOKENS,
                         purpose=str(body.get("purpose") or "build"))
            return {**got, "built_with": f"{s.company}:{s.model}"}
        got = self.ports.local_turn(system, messages, tools)
        return {**(got if isinstance(got, dict) else {}), "built_with": "local"}

    def agent_brain_json(self, build_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        self._build(build_id, after_yes=False)
        try:
            user = self._hide(str(body.get("user") or "")[:50_000])
        except NotBuilt:
            return {"answer": None}             # never a veto: the caller's fallback
        return {"answer": self.ports.brain_json(str(body.get("system") or "")[:5000], user)}

    def agent_answer(self, build_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        self._build(build_id)
        question = self._hide(str(body.get("question") or "")[:1000])
        context = self._hide(str(body.get("context") or "")[:20_000])
        return {"answer": self.ports.brain_answer(question, context)}

    def agent_found(self, build_id: str, job_id: str, body: dict) -> dict:
        """D121: a project found after her Yes, or an app to build, kept under ``job.run.found``: never in the plan or the
        envelope, so ``plan_sha`` and ``plan_cover`` are as they were and keeping it asks on its own card."""
        self._refuse_if_paused()
        b = self._build(build_id)
        self._job_of(b, job_id)
        why = public_read.found_why_not(self, b)
        if why:
            raise Refused(why)
        body = body if isinstance(body, dict) else {}
        cand = self._made_cand(body) if body.get("made") else self._found_cand(body)
        got: dict = {}

        def fn(x: dict) -> None:
            run = next(j for j in x["jobs"] if j["id"] == job_id)["run"]
            kept = run.setdefault("found", [])
            got.update(next((c for c in kept if c["ref"] == cand["ref"]), None) or cand)
            if not any(c["ref"] == cand["ref"] for c in kept):
                kept.append(cand)
        self.store.change(build_id, fn)
        return {"ok": True, "cand": got}

    @staticmethod
    def _found_cand(body: dict) -> dict:
        """A found project as the Core keeps it: a plain project address, past the junk filters."""
        host, full = find_mod.project_of(body.get("url"))
        if not full:
            raise Refused("That is not a plain project address on GitHub or Codeberg.")
        item = {"name": str(body.get("name") or ""), "description": str(body.get("description") or ""),
                "language": str(body.get("language") or ""), "archived": body.get("archived") is True,
                "fork": body.get("fork") is True, "topics": [str(t) for t in body.get("topics") or []][:20],
                "listed": body.get("listed") is True or body.get("known") is not True}   # no facts: tried, as a pasted one
        why = find_mod.not_an_app(item)
        if why:
            raise Refused(f"That is {why}, so it is not tried." + (" Read it with read_page and follow its links."
                                                                   if why in find_mod.LIST_WHYS else ""))
        lang = item["language"]
        cand = store._candidate({**item, "full_name": full, "host": host, "stars": body.get("stars"),
                                 "branch": body.get("branch"), "source": "codeberg" if host == "codeberg.org" else "github",
                                 "runtime": find_mod.LANGUAGE_RUNTIMES.get(lang, "")})
        if cand is None:
            raise Refused("That is not a plain project address on GitHub or Codeberg.")
        return {**cand, "found": True}

    @staticmethod
    def _made_cand(body: dict) -> dict:
        """An app Toolsmith builds itself: nothing to download, and always its own card (``origin: made``)."""
        what = store._s(body.get("what"), 2000)
        name = short_name(what, 6, 60)
        if not name:
            raise Refused("Say in plain words what to build.")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "app"
        return {"ref": f"made:{slug}-{os.urandom(2).hex()}", "name": name, "full_name": f"toolsmith/{slug}", "url": "",
                "host": "", "stars": 0, "description": what, "language": "", "license": None, "branch": "", "readme_steps": [],
                "runtime": "", "readme_port": None, "docker_file": "", "tries_in": "native", "programs": [], "source": "made"}

    def agent_store_add(self, build_id: str, job_id: str, body: dict) -> dict:
        """D152: the goal's conversation asks for a store app. Only a row of the store list, through the same add and the
        same Yes card as a store app ticked on a plan (``store_phase``). Nothing more is covered by this: she answers."""
        self._refuse_if_paused()
        b = self._build(build_id)
        self._job_of(b, job_id)
        pid = str((body if isinstance(body, dict) else {}).get("id") or "")
        row = next((r for r in self._store_rows() if r.get("id") == pid), None)
        if row is None:
            raise Refused("That is not in the PuffPAI store.")
        pkg, name = str(row.get("package") or pid), str(row.get("name") or pid)
        gone = self.store_phase._no_longer_offered(name, pkg)
        if gone:
            raise Refused(gone)
        staged = self.ports.packages_add(pkg, official_only=True) if pkg.startswith("store:") else self.ports.packages_add(pkg)
        cid = str(staged["consent_id"])
        with self._lock:
            self._store_cards.setdefault(build_id, {})[cid] = {"name": name, "id": store.bare_id(pkg)}
        return {"ok": True, "consent_id": cid, "name": name}

    def store_cards(self, build_id: str) -> list[str]:
        """The Yes cards the goal's conversation raised for store apps, for the build's card list."""
        with self._lock:
            return list(self._store_cards.get(build_id) or {})

    def agent_store_state(self, build_id: str, consent_id: str) -> dict:
        """Her answer to that card and how its install went, in the store phase's own words."""
        self._refuse_if_paused()
        self._build(build_id)
        with self._lock:
            mine = (self._store_cards.get(build_id) or {}).get(consent_id)
        if mine is None:
            raise Refused("This build did not raise that card.")
        name = mine["name"]
        status = str((self.ports.consent_state(consent_id) or {}).get("status") or "gone")
        if status == "pending":
            return {"waiting": True}
        if status == "declined":
            return {"ok": False, "said": f"You said No. {name} was not added."}
        if status != "granted":
            return {"ok": False, "said": STORE_NO_ANSWER.format(name=name)}
        got = self.ports.packages_wait(consent_id, 0.0) or {}
        if got.get("status") == "pending":
            return {"waiting": True}
        if got.get("status") != "installed":
            problems = " ".join(map(str, got.get("problems") or [])) or "the install did not finish."
            return {"ok": False, "said": STORE_FAILED.format(name=name, why=problems)}
        row = self.registry.get(mine["id"]) if self.registry is not None else None
        status = (row or {}).get("status")
        if status == "installing":
            return {"waiting": True}
        if status not in STORE_FINAL:
            return {"ok": False, "said": STORE_FAILED.format(name=name, why=f"it is {status or 'not there'}.")}
        return {"ok": True, "said": f"You said Yes. {name} is added."}

    def agent_page(self, build_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        b = self._build(build_id)
        ours = self.trials.installed_ids(build_id) | {j["plan"]["reuse"] for j in b["jobs"] if j["plan"]["reuse"]}
        return self.trials.page(build_id, str(body.get("url") or ""), self.trials.app_ports(ours))

    def agent_read_app(self, build_id: str, pkg_id: str) -> dict:
        self._refuse_if_paused()
        b = self._build(build_id)
        ours = self.trials.installed_ids(build_id) | {j["plan"]["reuse"] for j in b["jobs"] if j["plan"]["reuse"]}
        if pkg_id not in ours:
            raise Refused("Toolsmith reads only the apps of its own build.")
        got = self.ports.read_app(pkg_id)
        return got if isinstance(got, dict) else {"ok": False, "said": "no answer"}

    # tries

    def agent_try_open(self, build_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        b = self._build(build_id)
        self._job_of(b, str(body.get("job_id") or ""))
        return self.trials.open(build_id, str(body["job_id"]), str(body.get("ref") or ""))

    def _try(self, build_id: str, try_id: str) -> trials.TryRecord:
        self._build(build_id)
        return self.trials.get(build_id, try_id)

    def agent_try_download(self, build_id: str, try_id: str) -> dict:
        self._refuse_if_paused()
        return self.trials.download(self._try(build_id, try_id))

    def agent_try_check(self, build_id: str, try_id: str) -> dict:
        self._refuse_if_paused()
        return self.trials.check(self._try(build_id, try_id))

    def agent_try_run(self, build_id: str, try_id: str, body: dict) -> dict:
        """``{program, args, cwd}`` (or one plain ``command`` line): no shell, the allowlist (D60, D61)."""
        self._refuse_if_paused()
        return self.trials.run(self._try(build_id, try_id), _program(body))

    def agent_try_start(self, build_id: str, try_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        return self.trials.start(self._try(build_id, try_id), _program(body))

    def agent_try_mcp(self, build_id: str, try_id: str, body: dict) -> dict:
        """D124: one program of the try as an MCP server over stdio: its tools and one real call. Only on a cloud build."""
        self._refuse_if_paused()
        rec = self._try(build_id, try_id)
        if not self._cloud_for(self.store.read(build_id) or {}, self.settings()):
            raise Refused("Toolsmith keeps a service with no page only on a cloud build.")
        body = body if isinstance(body, dict) else {}
        return self.trials.mcp(rec, _program(body), str(body.get("tool") or ""), body.get("tool_args"))

    def agent_try_fs(self, build_id: str, try_id: str, body: dict) -> dict:
        """The Core's file tools for the try folder only: read, list, copy, move, mkdir, delete (D60)."""
        self._refuse_if_paused()
        return self.trials.fs(self._try(build_id, try_id), body if isinstance(body, dict) else {})

    def agent_try_stop(self, build_id: str, try_id: str, body: dict) -> dict:
        self._refuse_if_paused()                # paused: every try was already ended by its jobs
        return self.trials.stop(self._try(build_id, try_id), body.get("pid"))

    def agent_try_log(self, build_id: str, try_id: str) -> dict:
        self._refuse_if_paused()
        return self.trials.log(self._try(build_id, try_id))

    def agent_try_write(self, build_id: str, try_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        return self.trials.write(self._try(build_id, try_id), str(body.get("path") or ""),
                                 str(body.get("text") or ""))

    def agent_try_end(self, build_id: str, try_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        return self.trials.end(self._try(build_id, try_id), remove=body.get("remove", True) is not False)

    def agent_try_add(self, build_id: str, try_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        return self.trials.add(self._try(build_id, try_id), str(body.get("built_with") or "local"),
                               body.get("manifest"))

    def agent_try_add_state(self, build_id: str, try_id: str) -> dict:
        self._refuse_if_paused()
        return self.trials.add_state(self._try(build_id, try_id))

    # Checklist steps

    def agent_step_add(self, build_id: str, body: dict) -> dict:
        self._refuse_if_paused()
        b = self._build(build_id)
        job = self._job_of(b, str(body.get("job_id") or ""))
        raw = body.get("step") if isinstance(body.get("step"), dict) else {}
        step = _step(raw)
        if step["kind"] == "run_command":
            rec = None
            if body.get("try_id"):
                try:
                    rec = self.trials.get(build_id, str(body["try_id"]))
                except KeyError:
                    rec = None
            folder = rec.folder if rec is not None and rec.folder is not None else self.state / "packages"
            # X23: her steps pass the check too. A change to the computer is allowed here (she runs it herself,
            # 03 3.5); stopping a process is not (Toolsmith stops its own servers with stop_server).
            answer = self.ports.zone_check(step.get("text", ""), kind="command", folder=folder, cwd=folder,
                                           owner_step=True)
            if answer.verdict == "deny":
                raise Refused(answer.line or "That is outside what Toolsmith may touch.")
        stored = self.ports.checklist_add(step, "toolsmith", [f"job:{job['id']}"])
        step_id = str((stored or {}).get("id") or step["id"])
        with self._lock:
            self._steps.setdefault(build_id, set()).add(step_id)
        return {"id": step_id}

    def agent_step_state(self, build_id: str, step_id: str) -> dict:
        self._refuse_if_paused()
        self._build(build_id)
        ours = self._steps.get(build_id, set()) | {sid for rec in self.trials.of_build(build_id)
                                                   for sid in rec.approvals.values()}
        if step_id not in ours:
            raise KeyError(step_id)
        return {"state": self.ports.checklist_state(step_id)}

    def fetch_repo(self, url: str, dest: Path) -> str:
        """The download, through the broker (a test may give its ports a recorded one)."""
        fetch = getattr(self.ports, "fetch_repo", None)
        return fetch(url, dest) if callable(fetch) else broker.fetch_repo(url, dest)


def _count(value: Any) -> int:
    """A small count from Toolsmith's process (0 when it is not one)."""
    return min(int(value), 999) if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _program(body: Any) -> dict:
    """The run request from a route body: only its program fields (never anything else a body carries)."""
    body = body if isinstance(body, dict) else {}
    if body.get("program") is not None:
        return {"program": body.get("program"), "args": body.get("args"), "cwd": body.get("cwd") or ""}
    return {"command": str(body.get("command") or ""), "cwd": body.get("cwd") or ""}


def _step(raw: dict) -> dict:
    """A "Your turn" step in the one step shape (02 "Setup steps"), its fields checked."""
    kind = raw.get("kind") if raw.get("kind") in STEP_KINDS else "confirm"
    step_id = "".join(ch for ch in str(raw.get("id") or "") if ch.isalnum() or ch in "-_")[:60] or "ts-step"
    step: dict = {"id": step_id, "kind": kind, "title": " ".join(str(raw.get("title") or "A step for you").split())[:120]}
    for k in ("why", "text", "key"):
        if raw.get(k):
            step[k] = str(raw[k])[:2000]
    if str(raw.get("get_it") or "").startswith("https://"):
        step["get_it"] = str(raw["get_it"])[:500]
    if isinstance(raw.get("admin"), bool):
        step["admin"] = raw["admin"]
    if kind == "run_command":
        step["shell"] = "powershell" if os.name == "nt" else "bash"
    # A check runs a program when she ticks Done, so only the Core's own fixed install steps keep one
    # (``programs.step_for``); Toolsmith's process never picks a program for the Core to run.
    known = programs.step_for(str(raw.get("id") or "").removeprefix("install-")) if kind == "run_command" else None
    if known is not None and known.get("text") == step.get("text") and isinstance(raw.get("check"), dict):
        step["check"] = known["check"]
    return step


__all__ = ["AGAIN_NOTHING_LINE", "AGAIN_NO_YES_LINE", "AGAIN_RUNNING_LINE", "AGAIN_VAULT_LINE", "CRASHED_LINE",
           "DEFAULT_API", "NO_PLAN_LINE", "PAUSED_LINE", "Paused", "STOPPED_BY_LINE", "STOPPED_LINE",
           "ToolsmithHost"]
