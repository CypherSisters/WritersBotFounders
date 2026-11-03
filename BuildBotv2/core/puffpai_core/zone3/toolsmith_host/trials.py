# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/build/toolsmith_run.py (adapted)
r"""The Core's side of one try: what Toolsmith's process may ask for, and what the Core does (09 section 3).

A try belongs to one build and one job, and to one candidate on that job's
plan (the ONE Yes covered it). Toolsmith names the candidate by its ``ref``;
the Core takes the address from the plan it kept, so a try can't fetch
anything the plan did not list, or, on a cloud build, what the Core kept under the job's
``run.found`` (D121). A made app (``source: made``) has an empty folder and no fetch. Then:
1. **download** (:meth:`Trials.download`): the Core fetches the project into
   ``packages\.staging\<random>`` itself (``broker.fetch_repo``, its own job
   object), after the Bridge's yes for ``toolsmith:search`` to the candidate's own host.
2. **check** (:meth:`Trials.check`): Drawbridge checks the downloaded folder
   in the Core (``check_package``). Nothing runs in a folder before it passes.
   A pass renames it to ``packages\<id>`` before any setup (02 "Install in
   place"); a block removes it (never a card). A hold (files Drawbridge
   couldn't check, or a tunnel in its code, D117) is SET ASIDE (lane I4b): its fingerprints are kept, the
   download is removed before anything ran in it, and the next candidate
   starts. Only when no fully checked candidate worked for the job does
   Toolsmith ask the Core for ONE card for that job (:meth:`Trials.holds_ask`)
   listing every candidate set aside; she ticks which ones to try, and only the
   ticked ones are tried (a Yes with nothing ticked tries none). Nothing runs
   before her answer. A Yes lets Toolsmith download a ticked one again, and it
   goes on only when Drawbridge holds it for exactly the same files, byte for
   byte (:meth:`Trials.check`); No or no answer in time skips them all. Her
   ticks are recorded by the Core from her answer (:meth:`Trials.tick_holds`),
   never by Toolsmith. The renamed folder gets a git repository of
   its own (``steward.gitfence.init_repo``, lane I2a part D), so nothing in
   the try finds a repository above it; Toolsmith can't write its ``.git``.
3. **run / start / write / files** (:meth:`Trials.run`, :meth:`Trials.start`,
   :meth:`Trials.write`, :meth:`Trials.fs`): every program is one program and
   its arguments, never a shell, from the allowlist (D60, D61, ``allowlist``:
   the try's own .venv Python, node/npm/npx, docker compose, programs from the
   checked download, matched by the resolved file); copy, move, make folder,
   delete, read and list are Core tools for the try folder only (no junction
   is followed). Toolsmith's zone check (L4 ``toolsmith_zone.check``) runs
   on every program line, path and compose file. ``deny`` refuses with its
   plain line; ``ask`` (a place outside the app's folder that is not denied)
   is a PAUSE: the Core raises a Checklist step naming exactly that command,
   the try waits in place, and only after she ticks Done does that exact line
   run (09 access table: "exactly as written on her list"). Every ``docker
   compose`` program gets the Core's project name and the Docker wall's
   override (L4 ``docker_wall.override``), or does not run. Every command and server start runs inside L4's ``HookGuard``: Python
   start-up files are hashed before and compared after; a change in the try
   folder is undone and ends the try, a change in the Core's own Python puts
   the Core into safe mode (09 section 3, X23). No guard, no command.
4. **end** (:meth:`Trials.end`): what the try started is stopped by its job
   objects (``docker compose down`` for its own project), and its folder is
   removed: only a folder directly under ``packages``, never through a
   junction or link (v1 ``remove_try_folder`` :406 with its guard :365-404).
5. **add** (:meth:`Trials.add`, :meth:`Trials.add_state`): the try is stopped, and
   the Core itself writes the manifest Toolsmith sends as ``.buildbot\puffpai.json``
   (checked: valid, this app's id, ``origin: adopted:<the plan's address>``). That
   file is the only manifest an adopted app runs from (``packages.manifest.find``);
   a ``puffpai.json`` the project shipped at its top is its own file, and Toolsmith
   can't write either one (:meth:`Trials.write`, :meth:`Trials.fs`). The plan handed
   on carries the broker's record of what ran in the try (``Broker.record``): the
   plan's Yes covers a command the Core starts later only when it ran there
   (``packages.plan_cover``). Then lane L1's Package Manager takes the folder
   (``add``: a copy into staging without ``.venv``/``node_modules``/``.git``,
   Drawbridge bound to that staged tree hash, X24) with the build's plan Yes
   (03 3.3): inside the plan, that Yes covers the app and it installs with no
   new card; outside it, ONE Yes card shows her the difference, and the job
   waits for her answer (Toolsmith never answers it). Before that, what the run
   made that is not the app is dropped (files that were not in the checked
   download and that Toolsmith did not write: a database the app made, build
   output; setup is replayed from the manifest), and the try folder is moved
   aside into ``packages\.staging``, so ``packages\<id>`` is free for the
   install's rename (integration lane I2a: with the plan's Yes the install starts
   at once). Steward then starts it from the manifest (the restart test).

If Toolsmith's process ends or crashes, every try it left open is ended the
same way (``service.ToolsmithHost``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import socket
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ...consent.shown import TUNNEL_DOES, TUNNEL_SHORT, TUNNEL_WORDS
from ...steward import gitfence
from ..toolsmith_zone import mark_began
from ..toolsmith import adopt
from ..toolsmith.errors import NotBuilt, Refused, TryEnded
from . import allowlist, public_read
from . import broker as broker_mod

logger = logging.getLogger(__name__)
_REPARSE_POINT = 0x400
_LOCAL = re.compile(r"^http://(?:127\.0\.0\.1|localhost):(\d{2,5})(/[^\s]*)?$")
COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
#: A host port in Docker's own list of a container's ports: ``127.0.0.1:8080->80/tcp``, ``127.0.0.1:8000-8002->8000-8002/tcp``.
_PUBLISHED = re.compile(r":(\d{1,5})(?:-(\d{1,5}))?->")
DOCKER_PS_S = 15.0
MAX_FILE_BYTES = 1_000_000
CHECK_FIRST = "Drawbridge checks the download before anything runs in it. Download it first."
TRY_HOOK_LINE = ("Something in this try changed Python's start-up files, so the try was stopped and the change "
                 "was undone.")
CORE_HOOK_LINE = "Something changed Python's start-up files in PuffPAI itself, so PuffPAI went into safe mode."
RESCAN_EVERY_S = 1.0
SETTLE_S = 60.0                         # how long an add waits for checks and commands in flight (D112)
INSIDE_LINE = "Give a path inside the app's folder."
VENV_LINE = ("The app's own Python (.venv) is the Core's: change it with python -m pip, or delete the whole "
             ".venv and make it again with python -m venv .venv.")
READ_BYTES = 60_000
LIST_MAX = 400
ASK_LINE = ("That reaches a place outside the app's folder, so it waits for the owner's Yes. "
            "It runs once the owner ticks it on the Checklist.")
CORE_FILE_LINE = ("puffpai.json at the top of the app's folder, the .buildbot folder and the try's own .git are the "
                  "Core's: Toolsmith sends the app's manifest with add, and the Core writes it.")
NO_MANIFEST = "Send the app's puffpai.json with add (the Core writes it), so it was not installed."


def free_port(taken: set[int] | None = None) -> int:
    """A port in 18420-18999 that is free on 127.0.0.1 (10_BUILD_PLAN "Ports").

    In random order, and never one the listen table shows in use on any address: always taking the lowest
    free port made two builds on one computer (two test runs, or a try and a package start) pick 18420 at
    the same moment, and a server that sets SO_REUSEADDR (Python's http.server does) can then share it,
    so the page read was not always the try's own (lane I6a, the flaky main e2e)."""
    import random
    from ...steward.ports import listening_ports
    taken = set(taken or set()) | listening_ports()
    ports = list(range(18420, 19000))
    random.shuffle(ports)
    for port in ports:
        if port in taken:
            continue
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError("No free port in 18420-18999.")


def _is_link(path: Path) -> bool:
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def remove_try_folder(folder: Path | None, root: Path) -> bool:
    """Remove a try's own folder: only under *root*, never through a junction or link."""
    if folder is None:
        return False
    folder, root = Path(folder), Path(root)
    try:
        folder.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    if folder.resolve() == root.resolve() or not folder.exists() or _is_link(folder):
        return False

    def walk(p: Path) -> None:
        for child in list(p.iterdir()):
            if _is_link(child):
                try:
                    child.rmdir() if child.is_dir() else child.unlink()
                except OSError:
                    os.unlink(child)
            elif child.is_dir():
                walk(child)
            else:
                try:
                    child.chmod(stat.S_IWRITE)
                except OSError:
                    pass
                child.unlink()
        p.rmdir()
    try:
        walk(folder)
    except OSError:
        pass
    return not folder.exists()


#: Folders setup makes again (the Package Manager leaves them out of its copy): never counted as files.
_REGENERATED = frozenset({".venv", "venv", "node_modules", "__pycache__", ".git"})


def _has_link(folder: Path) -> bool:
    """Does anything under *folder* (not following links) be a junction or link?"""
    for root, dirs, files in os.walk(folder, followlinks=False):
        for name in dirs + files:
            if _is_link(Path(root) / name):
                return True
    return False


def _files(folder: Path) -> list[str]:
    """Every file under *folder* (``a/b.txt`` form), not through a link, not in regenerated folders."""
    out: list[str] = []

    def walk(p: Path, rel: str) -> None:
        try:
            children = list(p.iterdir())
        except OSError:
            return
        for child in children:
            r = f"{rel}{child.name}"
            if _is_link(child):
                continue
            if child.is_dir():
                if child.name not in _REGENERATED:
                    walk(child, r + "/")
            else:
                out.append(r)
    walk(Path(folder), "")
    return out


def _folders(folder: Path) -> list[str]:
    """Every real folder under *folder* (``a/b`` form), not through a link, not regenerated ones (as :func:`_files`)."""
    out: list[str] = []

    def walk(p: Path, rel: str) -> None:
        try:
            children = list(p.iterdir())
        except OSError:
            return
        for child in children:
            if not _is_link(child) and child.is_dir() and child.name not in _REGENERATED:
                out.append(f"{rel}{child.name}")
                walk(child, f"{rel}{child.name}/")
    walk(Path(folder), "")
    return out


def holds_files_only(verdict: dict) -> bool:
    """Drawbridge holds the download only for its files (ones it couldn't check, or under ``docs/`` with words
    aimed at an AI, D105; both are ``cant_check``), never a block, never another hold."""
    return [str(c) for c in verdict.get("reasons") or []] == ["cant_check"]


def _held_why(words_only: bool, tunnel: bool = False) -> str:
    """Why an app is set aside, as a sentence's opening. ``words_only``: it read every file, and only words aimed
    at an AI in its docs hold it (D105). ``tunnel``: its code can start a tunnel, which leads whatever else holds it,
    and it read those files, so never "couldn't check" (D117)."""
    if tunnel:
        return TUNNEL_WORDS
    return "Some of its docs hold words aimed at an AI" if words_only else "Drawbridge couldn't check some of its files"


def held_files_line(shown: dict) -> str:
    """The held files in plain words (D95: counts by kind, at most three base names a line, never a path)."""
    said = [str(x) for x in shown.get("held_said") or [] if isinstance(x, str)]
    if said:
        return " ".join(said)
    held = [str(f).rsplit("/", 1)[-1] for f in shown.get("held") or []][:3]
    more = int(shown.get("held_more") or 0) + max(0, len(shown.get("held") or []) - 3)
    return ", ".join(held) + (f" and {more} more" if more else "")


def hold_line(name: str, shown: dict, alone: bool = False) -> str:
    """The job's line when a download is set aside: what could not be checked, in plain words. *alone*: the job
    has no other app to try first, so the line says only what is asked (D104). What a tunnel does comes first, ahead
    of any name, since the job's line is cut at 300 characters; the rest of the held files come after what is asked
    (D117)."""
    if shown.get("held_tunnel") is True:
        said = [str(x) for x in shown.get("held_said") or [] if isinstance(x, str) and x != TUNNEL_DOES]
        ask = ("Let Toolsmith try it anyway? Nothing from it runs unless you say Yes." if alone else
               "Toolsmith first tries the other apps. Nothing from it runs unless you say Yes later.")
        return " ".join([TUNNEL_DOES, f"{name}: set aside for now.", *said[:1], ask, *said[1:]])
    if alone and shown.get("held_docs_only") is not True:
        return (f"{name}: set aside for now. Drawbridge couldn't check some of its files. Let Toolsmith try it anyway? "
                "Nothing from it runs unless you say Yes." + (f" {held_files_line(shown)}" if held_files_line(shown) else ""))
    files = held_files_line(shown)
    first = ("so Toolsmith first tries the other apps" if shown.get("held_docs_only") is True
             else "so Toolsmith first tries the apps it could check fully")
    return (f"{name}: set aside for now. {_held_why(shown.get('held_docs_only') is True)}, {first}. Nothing from it "
            "runs unless you say Yes later." + (f" {files}" if files else ""))


#: What a Yes on a hold card really does at that moment (never "got this app working").
YES_MEANS_ONE = "Your Yes lets Toolsmith try it. Nothing from it runs before that."
YES_MEANS_MANY = ("Your Yes lets Toolsmith try the ones you tick, one at a time. Nothing from them runs "
                  "before that.")
#: With a tunnel on the card, one Yes covers the try and keeping the app (D117). The card says so, since a tunnel stays.
TUNNEL_YES_ONE = YES_MEANS_ONE + " If the try works, your Yes keeps it too. Another card comes only if something new turns up."
TUNNEL_YES_MANY = (YES_MEANS_MANY + " If the try of an app with a tunnel works, your Yes keeps it too. Another card comes "
                   "only if something new turns up.")
#: Only what she ticked is tried: a Yes that came with no ticks tries none (the Desktop and the wizard send them).
TICKS_LINE = "Only the apps you tick are tried. A Yes with nothing ticked tries none of them."
NONE_TICKED = "you said Yes but ticked none, so Toolsmith tries none of them"


#: Why the Bridge said no, in plain words, by its closed ledger code (D70).
_BRIDGE_WHY = {"not_on_list": "not on the plan the owner said Yes to",
               "needs_yes": "not on the plan the owner said Yes to",
               "bridge_closed": "the Bridge is closed", "switched_off": "the Bridge is closed",
               "drawbridge_down": "the Bridge is closed", "policy_damaged": "the Bridge is closed"}
_BRIDGE_SHOWN = 6


def bridge_line(rows: list[dict]) -> str:
    """One plain line for the hosts the Bridge refused during a try (host names only)."""
    groups: dict[str, list[str]] = {}
    for r in rows:
        why = _BRIDGE_WHY.get(str(r.get("code") or ""), "the Bridge's safety rules")
        hosts = groups.setdefault(why, [])
        if str(r["host"]) not in hosts:
            hosts.append(str(r["host"]))
    parts = []
    for why, hosts in groups.items():
        shown = ", ".join(hosts[:_BRIDGE_SHOWN]) + (f" and {len(hosts) - _BRIDGE_SHOWN} more"
                                                    if len(hosts) > _BRIDGE_SHOWN else "")
        parts.append(f"{shown} ({why})")
    return ("The Bridge refused " + "; ".join(parts) + ", so nothing came from there. Trying again will not "
            "help: use a way that needs only the hosts on the plan, or tell the owner what it needs.")


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", str(text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:32].strip("-")
    return s if re.match(r"^[a-z][a-z0-9_-]{1,39}$", s or "") else f"app-{s or secrets.token_hex(3)}"[:39]


@dataclass
class TryRecord:
    id: str
    build_id: str
    job_id: str
    cand: dict
    port: int
    staging: Path | None = None
    folder: Path | None = None
    pkg_id: str = ""
    logs: Path | None = None
    scratch: Path | None = None
    broker: Any = None
    compose: dict | None = None
    approvals: dict = field(default_factory=dict)
    installed: bool = False
    ended: bool = False
    pending: str = ""                     # the Package Manager's card for this app, while she decides
    covered: str = ""                     # the plan Yes that covers this app (no card for her)
    base: dict | None = None              # the start-up files as last judged clean (moves only after a clean check)
    scanned_at: float = 0.0
    manifest: dict | None = None
    built_with: str = "local"
    files: set = field(default_factory=set)   # the checked download's files, and what Toolsmith wrote
    dirs: set = field(default_factory=set)    # the checked download's folders, and what Toolsmith made
    temp: Path | None = None              # the try's own short TEMP (broker.make_temp), the Core's
    git: str = ""                         # "" = it has its own .git (Trials.check), else why not (no git here)
    # After her Yes on the job's hold card (the Core's record, never a route's): ``{consent_id, files}``, what
    # the plan hands the Package Manager, so the same files are not asked about again at install.
    held_yes: dict | None = None
    exposed: list = field(default_factory=list)  # servers of this try the Core stopped for listening beyond 127.0.0.1
    subject: str = ""                     # its Bridge subject (``pkg:<build's bridge name>``), set at its check
    bridge_seen: int = 0                  # the Bridge ledger's refusal mark already told to Toolsmith (D70)
    # An add waits for every check and command in flight to reach its verdict before it touches the folder (D112).
    busy: Any = field(default_factory=threading.Condition, repr=False, compare=False)
    in_flight: int = 0                    # checks and commands of this try that have not reached their verdict
    add_step: str = ""                    # "stopping": no new command starts. "moving": no new check starts either.

    def checked_drop(self, rel: str) -> None:
        """A program from the checked download that was written over or deleted is not trusted any more."""
        if self.broker is not None:
            for k in [k for k in self.broker.checked if k == rel or k.startswith(rel + "/")]:
                self.broker.checked.pop(k, None)

    def checked_move(self, a: str, b: str) -> None:
        """A checked program moved inside the folder keeps its check (its hash is compared at every run)."""
        if self.broker is not None:
            for k in [k for k in self.broker.checked if k == a or k.startswith(a + "/")]:
                self.broker.checked[b + k[len(a):]] = self.broker.checked.pop(k)


class Trials:
    """Every open try of every build, in the Core. ``host`` is the ``ToolsmithHost``."""

    def __init__(self, host: Any) -> None:
        self.host = host
        self._tries: dict[str, TryRecord] = {}
        self._ended: dict[str, str] = {}                 # try id -> why the Core ended it itself
        self._installed: dict[str, set[str]] = {}       # build id -> package ids its tries installed
        # Lane I4b. Downloads Drawbridge held, set aside per job: (build, job) -> {ref: what was held}.
        self._deferred: dict[tuple[str, str], dict[str, dict]] = {}
        # The job's ONE hold card: (build, job) -> {consent_id, card_sha, items, answer}.
        self._hold_cards: dict[tuple[str, str], dict] = {}
        # Her Yes for one set-aside candidate: (build, job, ref) -> {consent_id, tree_sha, files}.
        self._approved: dict[tuple[str, str, str], dict] = {}
        # Candidates whose try ended, per job: (build, job) -> refs (the hold card waits for all of them).
        self._ended_refs: dict[tuple[str, str], set[str]] = {}
        self._lock = threading.RLock()

    @property
    def ports(self) -> Any:
        return self.host.ports

    @property
    def packages(self) -> Path:
        return self.host.state / "packages"

    # -- the records ----------------------------------------------------------

    def open(self, build_id: str, job_id: str, ref: str) -> dict:
        b = self.host.store.read(build_id)
        if b is None or not b.get("yes"):
            raise Refused("This build has no Yes, so nothing is tried.")
        job = next((j for j in b["jobs"] if j["id"] == job_id), None)
        if job is None:
            raise KeyError(job_id)
        cand = next((c for c in job["plan"]["candidates"] if c["ref"] == ref), None)
        if cand is None:                               # D121: found after her Yes, kept on the job, never on the plan
            cand = next((c for c in (job.get("run") or {}).get("found") or [] if c.get("ref") == ref), None)
            why = public_read.found_why_not(self.host, b) if cand is not None else ""
            if why:
                raise Refused(why)
        if cand is None:
            raise Refused("That app is not on this job's plan, so it is not tried.")
        with self._lock:
            taken = {t.port for t in self._tries.values()}
            rec = TryRecord(id="t" + secrets.token_hex(6), build_id=build_id, job_id=job_id, cand=dict(cand),
                            port=free_port(taken))
            self._tries[rec.id] = rec
        return {"try_id": rec.id, "port": rec.port, "name": cand["name"], "url": cand["url"]}

    def get(self, build_id: str, try_id: str) -> TryRecord:
        rec = self._tries.get(str(try_id or ""))
        if rec is None or rec.build_id != build_id or rec.ended:
            why = self._ended.get(str(try_id or ""))
            if why and why.split("|", 1)[0] == build_id:
                raise TryEnded(why.split("|", 1)[1])
            raise KeyError(try_id)
        return rec

    def of_build(self, build_id: str) -> list[TryRecord]:
        with self._lock:
            return [t for t in self._tries.values() if t.build_id == build_id and not t.ended]

    def installed_ids(self, build_id: str) -> set[str]:
        with self._lock:
            return set(self._installed.get(build_id, set()))

    # -- 1 and 2: download, then Drawbridge -----------------------------------------

    def download(self, rec: TryRecord) -> dict:
        if rec.folder is not None or rec.staging is not None:
            return {"ok": True, "said": "It is downloaded already."}
        made = adopt.made(rec.cand)                      # D121: an app Toolsmith writes: an empty folder, nothing fetched
        host = adopt.project_host(rec.cand)
        decision = "allow" if made else self.ports.bridge_decision("toolsmith:search", host)
        if decision != "allow":
            return {"ok": False, "said": f"The Bridge is closed for {host}, so the project was not downloaded."}
        staging = self.packages / ".staging" / secrets.token_hex(6)
        staging.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir()                                  # empty: git clones into it; any start-up file it brings is new
        try:
            guard = self._new_guard(staging)
            guard.before = guard._snapshot()
        except NotBuilt as exc:
            return {"ok": False, "said": f"{exc} So nothing is downloaded."}
        except OSError:
            return {"ok": False, "said": "The start-up check could not run, so nothing is downloaded."}
        why = "" if made else self.host.fetch_repo(rec.cand["url"], staging)
        result = guard.finish()
        if not result.get("ok"):
            remove_try_folder(staging, self.packages)
            return {"ok": False, "said": CORE_HOOK_LINE if result.get("core") else
                    "It ships Python start-up files (a .pth or sitecustomize.py), so it was not downloaded."}
        if why:
            remove_try_folder(staging, self.packages)
            return {"ok": False, "said": why}
        # The clone's own history is not the app: Drawbridge would hold its pack files (it can't read
        # them), and the Package Manager strips .git before its own check anyway (X24).
        if (staging / ".git").is_dir() and not remove_try_folder(staging / ".git", staging):
            remove_try_folder(staging, self.packages)
            return {"ok": False, "said": "Its download could not be cleaned of git's own files, so it was not used."}
        # D95: folders an app never needs to run (Sphinx's .doctrees, a cache of Python pickles) are removed
        # before Drawbridge's check, as the Package Manager does; its tree hash is of what is left (X24).
        try:
            from ...packages import fsutil
            fsutil.strip_regenerated(staging, only_unused=True)
        except OSError:
            remove_try_folder(staging, self.packages)
            return {"ok": False, "said": "Its download could not be cleaned of Sphinx's .doctrees folder, so it "
                                         "was not used."}
        rec.staging = staging
        return {"ok": True, "said": f"Downloaded {rec.cand['name']}."}

    def check(self, rec: TryRecord) -> dict:
        if rec.folder is not None:
            return {"ok": True, "said": "Drawbridge checked it already.", "pkg_id": rec.pkg_id,
                    "folder": str(rec.folder), "port": rec.port}
        if rec.staging is None:
            return {"ok": False, "said": CHECK_FIRST}
        base = _slug(rec.cand.get("name") or rec.cand["full_name"].split("/")[-1])
        verdict = self.ports.drawbridge_check(rec.staging, {"schema": "puffpai-package/1", "id": base, "kind": "app",
                                                            "name": rec.cand["name"]},
                                              origin=adopt.origin_of(rec.cand))
        if verdict.get("verdict") == "hold" and holds_files_only(verdict):
            with self._lock:
                approved = self._approved.get((rec.build_id, rec.job_id, rec.cand["ref"]))
            if approved is None:
                with self._lock:
                    carded = (rec.build_id, rec.job_id) in self._hold_cards
                if carded:                                  # the job had its one card: not asked again
                    row = self._held_names(verdict)[1]
                    return self._hold_refused(rec, f"{_held_why(row.get('docs_only') is True, bool(row.get('tunnel')))}"
                                                   ", and this job had its one card for them already, so it is not "
                                                   "tried again.")
                return self._defer_hold(rec, verdict)      # set aside: no card now, nothing ran in it
            # Her Yes must still be live, as restore_answers asks: one she took back lets nothing run.
            if self.host._consent_status(str(approved.get("consent_id") or "")) != "granted":
                with self._lock:
                    self._approved.pop((rec.build_id, rec.job_id, rec.cand["ref"]), None)
                return self._hold_refused(rec, "Your Yes for it is no longer in force, so it was not used.")
            # Her Yes: the download must be exactly what her card showed, held for the same files, byte for byte.
            if not self._same_as_approved(rec, verdict, approved):
                with self._lock:                            # refused once: skipped for the rest of the build
                    self._approved.pop((rec.build_id, rec.job_id, rec.cand["ref"]), None)
                return self._hold_refused(rec, "It changed after Drawbridge's check, so it was not used.")
            rec.held_yes = {"consent_id": approved["consent_id"], "files": dict(approved["files"])}
        elif verdict.get("verdict") not in ("allow", "warn"):
            remove_try_folder(rec.staging, self.packages)
            rec.staging = None
            said = str(verdict.get("line") or ", ".join(verdict.get("reasons") or []) or "blocked")
            return {"ok": False, "said": "Drawbridge did not pass it: " + said}
        with self._lock:
            taken = {t.pkg_id for t in self._tries.values() if t.pkg_id and not t.ended}
            reg = self.host.registry
            pkg_id, n = base, 2
            from ...packages.manifest import RESERVED_IDS     # D79: never the Desktop's own name
            while (pkg_id in taken or pkg_id in RESERVED_IDS or (self.packages / pkg_id).exists()
                   or (reg is not None and reg.get(pkg_id) is not None)):
                pkg_id, n = f"{base[:36]}-{n}", n + 1
            target = self.packages / pkg_id
            os.replace(rec.staging, target)             # renamed before any setup runs (02 "Install in place")
            rec.staging, rec.folder, rec.pkg_id = None, target, pkg_id
            rec.files, rec.dirs = set(_files(target)), set(_folders(target))
        # Its own git repository (lane I2a part D): a tool in the try that walks up looking for .git stops
        # here, never at a repository the owner's home folder may be. No git here: nothing is made, and
        # rec.git says why (the broker's GIT_CEILING_DIRECTORIES still keeps git itself inside).
        rec.git = gitfence.init_repo(target, broker_mod.base_env())
        if rec.git:
            logger.info("try %s: %s", rec.id, rec.git)        # git's own words stay in the Core's log
        if rec.git and rec.git != gitfence.NO_GIT:
            self.end(rec)
            raise Refused("The try's own git folder could not be made, so nothing runs.")
        logs = self.host.state / "logs" / "toolsmith" / pkg_id
        rec.logs, rec.scratch = logs, logs / "scratch"
        mark_began(rec.scratch)                            # before anything of this try runs
        try:
            from .store import bridge_target
            rec.subject = f"pkg:{bridge_target(rec.build_id)}"
            proxy = self.ports.proxy_env(f"ts-{pkg_id}", subject=rec.subject, phase="install")
        except NotBuilt:
            self.end(rec)
            raise
        rec.bridge_seen = self._bridge_refused(rec)[1]     # only refusals from now on are this try's news
        try:
            start = self._new_guard(target)
            rec.base = start._snapshot()                # the baseline: fixed now, before any command
        except (NotBuilt, OSError) as exc:
            self.end(rec)
            raise NotBuilt("The Python start-up check", "L4") if isinstance(exc, NotBuilt) else \
                Refused("The try folder could not be read for the start-up check, so nothing runs.") from None
        # The native programs in the checked download, by hash: the only programs of its own a try may run (D61).
        checked = allowlist.checked_programs(target, sorted(rec.files))
        try:
            rec.temp = broker_mod.make_temp(self.host.state)
        except OSError:
            self.end(rec)
            raise Refused("The try's own TEMP folder could not be made, so nothing runs.") from None
        rec.broker = broker_mod.Broker(target, rec.scratch, self.host.settings()["timing"], proxy_env=proxy,
                                       checked=checked, temp=rec.temp, port=rec.port)
        return {"ok": True, "said": "Drawbridge checked it.", "pkg_id": pkg_id, "folder": str(target),
                "port": rec.port}

    # -- 2b: Drawbridge holds the download: set aside; ONE card per job, later (lane I4b) -----------

    @staticmethod
    def _held_names(verdict: dict) -> tuple[list[str], dict]:
        row = next((r for r in verdict.get("rows") or [] if isinstance(r, dict) and r.get("code") == "cant_check"),
                   {})
        return [str(n) for n in (row.get("all") or row.get("files") or []) if isinstance(n, str)], row

    def _defer_hold(self, rec: TryRecord, verdict: dict) -> dict:
        """Drawbridge couldn't check some of the download's files, or found a tunnel (a "hold": her Yes, never a pass). Its
        fingerprints are kept for the job's one hold card, the download is removed (nothing ran in it), and
        Toolsmith moves on to the next candidate."""
        from ...packages.manager import shown_verdict
        names, row = self._held_names(verdict)
        name = str(rec.cand["name"])
        try:
            files = self.ports.held_fingerprints(rec.staging, names)
            shown = shown_verdict(verdict)
        except (NotBuilt, ValueError, OSError) as exc:
            remove_try_folder(rec.staging, self.packages)
            rec.staging = None
            why = str(exc) if isinstance(exc, NotBuilt) else "its files could not be fingerprinted"
            return {"ok": False, "said": f"{name}: {_held_why(row.get('docs_only') is True, bool(row.get('tunnel')))}, "
                                         f"and {why}, so it was not used."}
        item = {"ref": rec.cand["ref"], "name": name, "url": str(rec.cand["url"]),
                "tree_sha": str(verdict.get("tree_sha") or ""), "files": files, "shown": shown,
                "cut": row.get("cut") is not False, "tries_in": rec.cand.get("tries_in") or "native"}
        with self._lock:
            self._deferred.setdefault((rec.build_id, rec.job_id), {})[rec.cand["ref"]] = item
        remove_try_folder(rec.staging, self.packages)
        rec.staging = None
        logger.info("try %s: set aside for the job's hold card (%d file(s))", rec.id, len(files))
        return {"ok": False, "held": True, "said": hold_line(name, shown, self._alone(rec.build_id, rec.job_id, rec.cand["ref"]))}

    def _alone(self, build_id: str, job_id: str, ref: str) -> bool:
        """The job's plan has no app to try but *ref* (one the company's rank dropped is not tried either)."""
        b = self.host.store.read(build_id) or {}
        job = next((j for j in b.get("jobs") or [] if j.get("id") == job_id), {})
        plan = job.get("plan") or {}
        dropped = {str(n.get("ref")) for n in plan.get("not_used") or [] if isinstance(n, dict)}
        return not [c for c in plan.get("candidates") or [] if c["ref"] != ref and c["ref"] not in dropped]

    def _same_as_approved(self, rec: TryRecord, verdict: dict, approved: dict) -> bool:
        """The new download is byte for byte what her card showed: the same tree, held for the same files,
        each with the same fingerprint."""
        if not approved.get("tree_sha") or verdict.get("tree_sha") != approved["tree_sha"]:
            return False
        names, _row = self._held_names(verdict)
        if sorted(names) != sorted(approved["files"]):
            return False
        try:
            now = self.ports.held_fingerprints(rec.staging, names)
        except (NotBuilt, ValueError, OSError):
            return False
        return now == approved["files"]

    def set_aside(self, build_id: str, job_id: str) -> list[dict]:
        with self._lock:
            return [dict(v) for v in (self._deferred.get((build_id, job_id)) or {}).values()]

    def holds_ask(self, build_id: str, job_id: str) -> dict:
        """ONE Yes card for the job, listing every candidate set aside (Toolsmith asks only when no fully
        checked candidate worked). Nothing is answered here; the same card is given back while it waits."""
        from ...packages.plan_cover import held_sha
        from .store import bridge_target
        key = (build_id, job_id)
        with self._lock:
            items = list((self._deferred.get(key) or {}).values())
            have = self._hold_cards.get(key)
            ended = set(self._ended_refs.get(key) or set())
            # An open try still counts unless it is one set aside (nothing is left to do in it).
            trying = any(t.job_id == job_id and t.build_id == build_id and not t.ended
                         and t.cand.get("ref") not in {i["ref"] for i in items} for t in self._tries.values())
        if have is not None:                               # at most ONE hold card per job per build, ever
            if have.get("answer") is None and self._card_state(have["consent_id"]) == "pending":
                return {"consent_id": have["consent_id"], "title": have["title"], "names": have["names"],
                        "alone": bool(have.get("alone")), "tunnel": str(have.get("tunnel") or "")}
            return {"consent_id": "", "said": "This job had its one card for the apps set aside already."}
        if not items:
            return {"consent_id": "", "said": "Nothing was set aside for this job."}
        b = self.host.store.read(build_id) or {}
        job = next((j for j in b.get("jobs") or [] if j.get("id") == job_id), {})
        # Only after every fully checked candidate of the job has ended (a candidate the cloud rank left out,
        # listed under "Not used", counts as ended): the card is the last thing a job asks.
        plan = job.get("plan") or {}
        dropped = {str(n.get("ref")) for n in plan.get("not_used") or [] if isinstance(n, dict)}
        left = [c["ref"] for c in plan.get("candidates") or []
                if c["ref"] not in ended and c["ref"] not in dropped
                and c["ref"] not in {i["ref"] for i in items}]
        if trying or left:
            return {"consent_id": "", "not_yet": True,
                    "said": "Toolsmith asks about the apps set aside only after it tried every app Drawbridge "
                            "could check fully."}
        names = [i["name"] for i in items]
        words_only = all(i["shown"].get("held_docs_only") is True for i in items)   # D105: it read every file
        tunnels = [i["name"] for i in items if i["shown"].get("held_tunnel") is True]   # D117: leads, true for those
        title = (f"Let Toolsmith try {names[0]}? {_held_why(words_only)}" if len(items) == 1
                 else f"Let Toolsmith try these {len(items)} apps for {job.get('name') or 'this job'}? "
                      + ("Some of their docs hold words aimed at an AI" if words_only
                         else "Drawbridge couldn't check some of their files"))[:160]
        if tunnels:
            # The name and the job are cut, never the tunnel's words.
            title = (f"Let Toolsmith try {names[0][:60]}? {TUNNEL_SHORT}" if len(items) == 1 else
                     f"Let Toolsmith try these {len(items)} apps for {str(job.get('name') or 'this job')[:40]}? "
                     + ("Each one's code can start a tunnel that can open your computer to the internet"
                        if len(tunnels) == len(items) else
                        f"{len(tunnels)} of them can start a tunnel that can open your computer to the internet"))
        card = {"title": title, "name": str(job.get("name") or ""), "build": build_id, "job": job_id,
                "yes_means": ((TUNNEL_YES_ONE if len(items) == 1 else TUNNEL_YES_MANY) if tunnels
                              else YES_MEANS_ONE if len(items) == 1 else YES_MEANS_MANY),
                "ticks": f"Yes tries {names[0]}. {TICKS_LINE}" if len(items) == 1 else TICKS_LINE,
                "try_holds": [{"candidate": i["ref"], "name": i["name"], "url": i["url"],
                               "held_sha": held_sha(i["files"]), "count": len(i["files"]), "cut": i["cut"],
                               "tries_in": i["tries_in"], "drawbridge": i["shown"]} for i in items]}
        alone = len(items) == 1 and self._alone(build_id, job_id, items[0]["ref"])
        if alone:
            card["alone"] = True                   # no other app was there to try first (D104)
        # The card is raised and recorded as this job's under one lock, so an answer that comes the moment the
        # card is in the store (the Desktop, the wizard) always finds it as one of this build's.
        with self._lock:
            try:
                got = self.ports.consent_card({"kind": "package",
                                               "target": f"{bridge_target(build_id)}.holds-{job_id.rsplit('-', 1)[-1]}"},
                                              card, False)
            except (NotBuilt, ValueError, OSError) as exc:
                self._deferred.pop(key, None)
                why = str(exc) if isinstance(exc, NotBuilt) else "no card could be shown"
                what = (f"The code of {', '.join(tunnels)} can start a tunnel, and a tunnel can open your computer to "
                        "the internet, so people out there could reach what runs here" if tunnels
                        else f"Some docs of {', '.join(names)} hold words aimed at an AI" if words_only
                        else f"Drawbridge couldn't check some files of {', '.join(names)}")
                return {"consent_id": "", "said": f"{what}, and {why}, so they were not used."}
            # D117: the job's line says it too, in the Core's words, first and with no names, since that line is cut at
            # 300 characters. Empty when no app on the card has a tunnel.
            said = "" if not tunnels else f"{TUNNEL_DOES} Drawbridge found one in " + (
                "this app's code." if len(items) == 1 else
                "the code of each of these apps." if len(tunnels) == len(items) else
                f"the code of {len(tunnels)} of these apps.")
            rec = {"consent_id": str(got["consent_id"]), "card_sha": str(got["card_sha"]), "title": title,
                   "names": names, "items": {i["ref"]: i for i in items}, "answer": None, "alone": alone, "tunnel": said}
            self._hold_cards[key] = rec
        logger.info("job %s: one hold card %s for %d app(s)", job_id, rec["consent_id"], len(items))
        return {"consent_id": rec["consent_id"], "title": title, "names": names, "alone": alone, "tunnel": said}

    def _card_state(self, cid: str) -> str:
        try:
            return str(self.ports.consent_state(cid).get("status") or "gone")
        except NotBuilt:
            return "gone"

    def holds_state(self, build_id: str, job_id: str) -> dict:
        """``{state: none | pending | answered, try: [refs], skipped: [{name, why}]}``. After her Yes, only the
        refs she ticked (recorded by the Core from her answer) are approved, for exactly the files the card
        showed. A Yes with no ticks recorded tries none (fail closed)."""
        key = (build_id, job_id)
        with self._lock:
            have = self._hold_cards.get(key)
        if have is None:
            return {"state": "none", "try": [], "skipped": []}
        if have.get("answer") is not None:
            return dict(have["answer"])
        try:
            st = self.ports.consent_state(have["consent_id"])
        except NotBuilt:
            st = {"status": "gone"}
        status = st.get("status")
        if status == "pending":
            return {"state": "pending", "try": [], "skipped": []}
        items = have["items"]
        if status == "granted" and st.get("card_sha") == have["card_sha"]:
            ticks = ((self.host.store.read(build_id) or {}).get("hold_ticks") or {}).get(have["consent_id"])
            ticks = ticks if isinstance(ticks, list) else []
            refs = [r for r in items if r in ticks]
            why = "you did not tick it" if ticks else NONE_TICKED
            skipped = [{"name": items[r]["name"], "ref": r, "why": why} for r in items if r not in refs]
            with self._lock:
                for r in refs:
                    self._approved[(build_id, job_id, r)] = {"consent_id": have["consent_id"],
                                                             "tree_sha": items[r]["tree_sha"],
                                                             "files": dict(items[r]["files"])}
        else:
            why = {"declined": "you said No to trying it",
                   "expired": "its card got no answer in time"}.get(str(status), "its card can't be used")
            refs, skipped = [], [{"name": i["name"], "ref": r, "why": why} for r, i in items.items()]
        answer = {"state": "answered", "try": refs, "skipped": skipped}
        with self._lock:
            have["answer"] = answer
            self._deferred.pop(key, None)
        approved = {r: {"tree_sha": items[r]["tree_sha"], "files": dict(items[r]["files"])} for r in refs}

        def keep(b: dict) -> None:
            # Lane I18e: her answer is kept in the build's own file, so "Check again" honours it (restore_answers).
            b.setdefault("holds_answered", {})[job_id] = {
                "consent_id": have["consent_id"], "card_sha": str(have.get("card_sha") or ""),
                "title": have["title"], "names": list(have["names"]),
                "answer": json.loads(json.dumps(answer)), "approved": approved}
        self.host.store.change(build_id, keep)
        return dict(answer)

    def _hold_card_of(self, build_id: str, job_id: str, cid: str, card_sha: str) -> bool:
        """True only when *cid* is this job's own hold card (its holds target) with the card_sha that was kept."""
        from .store import bridge_target
        try:
            got = self.ports.consent_record(cid)
            if not isinstance(got, dict):              # her No: not a Yes record, so read the card itself
                state = getattr(self.ports, "consent_state", None)
                got = state(cid) if callable(state) else None
        except Exception:  # noqa: BLE001 - can't tell: not hers
            return False
        if not isinstance(got, dict) or not card_sha or got.get("card_sha") != card_sha:
            return False
        scope = got.get("scope") if isinstance(got.get("scope"), dict) else {}
        return scope.get("kind") == "package" \
            and scope.get("target") == f"{bridge_target(build_id)}.holds-{job_id.rsplit('-', 1)[-1]}"

    def restore_answers(self, build_id: str, job_ids: list[str]) -> None:
        """"Check again" (lane I18e): the hold-card answers she already gave for these jobs count again, from the
        build's own file. Her No stands (no second card for that job). Her Yes counts only while that card's Yes
        is still live, and only for the same files, byte for byte (:meth:`check`); a Yes taken back is dropped,
        so the job may ask one new card the normal way."""
        kept = (self.host.store.read(build_id) or {}).get("holds_answered")
        kept = kept if isinstance(kept, dict) else {}
        for jid in job_ids:
            rec = kept.get(jid)
            if not isinstance(rec, dict) or not isinstance(rec.get("answer"), dict) or not rec.get("consent_id"):
                continue
            approved = rec.get("approved") if isinstance(rec.get("approved"), dict) else {}
            if not self._hold_card_of(build_id, jid, str(rec["consent_id"]), str(rec.get("card_sha") or "")):
                continue                                   # not that job's own hold card: nothing counts
            if approved and self.host._consent_status(str(rec["consent_id"])) != "granted":
                continue
            with self._lock:
                self._hold_cards[(build_id, jid)] = {
                    "consent_id": str(rec["consent_id"]), "card_sha": "", "title": str(rec.get("title") or ""),
                    "names": list(rec.get("names") or []), "items": {}, "answer": dict(rec["answer"])}
                for ref, a in approved.items():
                    if isinstance(a, dict) and isinstance(a.get("files"), dict):
                        self._approved[(build_id, jid, ref)] = {"consent_id": str(rec["consent_id"]),
                                                                "tree_sha": str(a.get("tree_sha") or ""),
                                                                "files": dict(a["files"])}

    def hold_cards(self, build_id: str) -> list[dict]:
        """This build's hold cards still waiting for her: ``[{consent_id, items}]`` (the wizard shows them)."""
        with self._lock:
            return [{"consent_id": v["consent_id"], "items": list(v["items"].values())}
                    for (b, _j), v in self._hold_cards.items() if b == build_id and v.get("answer") is None]

    def tick_holds(self, build_id: str, consent_id: str, refs: Any, by: str = "owner") -> list[str]:
        """Her ticks on a hold card (which listed apps to try), from her own answer, before she says Yes.
        Only refs the card lists; recorded in the build's own file by the Core. Toolsmith never calls it."""
        with self._lock:
            have = next((v for (b, _j), v in self._hold_cards.items()
                         if b == build_id and v["consent_id"] == consent_id and v.get("answer") is None), None)
        if have is None:
            raise PermissionError("That is not one of this build's hold cards.")
        try:
            waiting = self.ports.consent_state(consent_id).get("status") == "pending"
        except NotBuilt:
            waiting = False
        if not waiting:                                  # her answer is in: what it covers can't change now
            raise PermissionError("That card was already answered, so what Toolsmith tries can't change.")
        picked = [r for r in (refs or []) if isinstance(r, str)]
        if any(r not in have["items"] for r in picked):
            raise ValueError("Only an app on the card can be ticked.")

        def fn(b: dict) -> None:
            b.setdefault("hold_ticks", {})[consent_id] = list(dict.fromkeys(picked))
            b.setdefault("hold_ticked_by", {})[consent_id] = str(by or "owner")[:64]
        self.host.store.change(build_id, fn)
        return picked

    def clear_ticks(self, build_id: str, consent_id: str) -> None:
        """Her answer did not go through: the ticks recorded for it go too (they count only with her Yes)."""
        def fn(b: dict) -> None:
            for k in ("hold_ticks", "hold_ticked_by"):
                if isinstance(b.get(k), dict):
                    b[k].pop(consent_id, None)
        self.host.store.change(build_id, fn)

    def forget_build(self, build_id: str) -> None:
        """The build ended: its hold cards still waiting close (nothing would try them), and what was set
        aside or approved goes."""
        with self._lock:
            waiting = [v["consent_id"] for (b, _j), v in self._hold_cards.items()
                       if b == build_id and v.get("answer") is None]
            for k in [k for k in self._deferred if k[0] == build_id]:
                self._deferred.pop(k, None)
            for k in [k for k in self._hold_cards if k[0] == build_id]:
                self._hold_cards.pop(k, None)
            for k in [k for k in self._approved if k[0] == build_id]:
                self._approved.pop(k, None)
            for k in [k for k in self._ended_refs if k[0] == build_id]:
                self._ended_refs.pop(k, None)
        for cid in waiting:
            try:
                self.ports.consent_expire(cid, "Toolsmith stopped this build, so this card is closed.")
            except Exception:  # noqa: BLE001 - the card expires on its own after 10 minutes
                pass

    def _hold_refused(self, rec: TryRecord, said: str) -> dict:
        remove_try_folder(rec.staging, self.packages)
        rec.staging = None
        return {"ok": False, "said": said}

    # -- 3: commands, servers and files, each zone checked ---------------------------

    def _zone(self, rec: TryRecord, target: str, kind: str, write: bool = False, cwd: Path | None = None) -> Any:
        pids = tuple(sorted(rec.broker.own_pids())) if kind == "command" and rec.broker is not None else ()
        return self.ports.zone_check(target, kind=kind, folder=rec.folder, write=write, cwd=cwd or rec.folder,
                                     logs=rec.logs or "", scratch=rec.scratch or "", pids=pids)

    def _gate(self, rec: TryRecord, target: str, kind: str, write: bool = False, cwd: Path | None = None) -> dict | None:
        """None when *target* may go ahead; else the reply (a refusal, or a pause for her Yes)."""
        if rec.folder is None or rec.broker is None:
            return {"ok": False, "said": CHECK_FIRST}
        answer = self._zone(rec, target, kind, write, cwd)
        if answer.verdict == "allow":
            return None
        if answer.verdict != "ask":
            said = answer.line or "That is outside what Toolsmith may touch."
            if kind == "command":                         # the same one not_run record as the allowlist's
                words = allowlist.parse_words(target)
                req = allowlist.Request(program=words[0] if words else "", args=words[1:])
                return allowlist.complete(allowlist.Refusal(said), req, rec.folder).reply()
            return {"ok": False, "said": said}
        key = f"{kind}:{target}"
        step_id = rec.approvals.get(key)
        if step_id:
            state = self.ports.checklist_state(step_id)
            if state == "done":
                return None
            if state in ("skipped", "gone"):
                return {"ok": False, "skipped": True, "said": "Skipped by you"}
            return {"ok": False, "ask": {"step_id": step_id}, "said": ASK_LINE}
        name = rec.cand["name"]
        step = {"id": f"ts-outside-{secrets.token_hex(4)}", "kind": "confirm",
                "title": f"Let Toolsmith reach outside {name}'s folder"[:120],
                "why": (f"To get {name} working, Toolsmith wants to "
                        + ("run this command. It reaches " if kind == "command" else "write a file that reaches ")
                        + f"{max(1, len(answer.outside))} place(s) outside the app's folder. It runs as you."),
                "text": target[:2000]}
        stored = self.ports.checklist_add(step, "toolsmith", [f"job:{rec.job_id}"])
        step_id = str((stored or {}).get("id") or step["id"])
        rec.approvals[key] = step_id
        return {"ok": False, "ask": {"step_id": step_id, "title": step["title"]}, "said": ASK_LINE}

    def _compose(self, rec: TryRecord, rest: list[str]) -> tuple[list[str], str]:
        """``docker compose`` behind the Docker wall (D30): ``(argv, refusal)``. The Core sets the project and
        the files (theirs, then the wall's override) on every compose program, never the model."""
        theirs = next((n for n in COMPOSE_NAMES if (rec.folder / n).is_file()), "")
        if not theirs:
            return [], "There is no compose file at the top of its folder."
        try:
            text = (rec.folder / theirs).read_text(encoding="utf-8", errors="replace")[:500_000]
        except OSError:
            return [], "Its compose file could not be read."
        answer = self._zone(rec, text, "compose")
        if answer.verdict != "allow":
            # Docker was found (the allowlist plans compose only then) and never ran: say so, so the refusal
            # is not read as "Docker is missing" (D70).
            why = answer.line or "That is outside what Toolsmith may touch."
            return [], (f"Not run. Docker is on this computer and is not the problem: this app's compose file "
                        f"({theirs}) names a place Toolsmith may not touch, so the Core refused it before Docker "
                        f"started. {why} Installing Docker will not change this.")
        try:
            rest = allowlist.compose_rest(rest)
        except allowlist.Refusal as exc:
            return [], exc.said
        wall = self.ports.docker_override({"id": rec.pkg_id, "run": {"default": {"type": "docker_compose",
                                                                                "file": theirs}}},
                                          rec.folder / theirs)
        try:
            rel = Path(wall).resolve().relative_to(rec.folder.resolve()).as_posix()
        except ValueError:
            return [], "The Docker wall's file is not inside the app's folder, so it does not start."
        project = f"puffpai-{rec.pkg_id}"
        rec.compose = {"project": project, "files": [theirs, rel]}
        docker = rec.broker.docker or "docker"
        return [docker, "compose", "-p", project, "-f", theirs, "-f", rel, *rest], ""

    def _new_guard(self, folder: Path) -> Any:
        """L4's HookGuard for *folder*, keeping the snapshot it judged (``last``) so a clean check can
        become the next baseline exactly, never a later scan."""
        guard = self.ports.hook_guard(folder, stop_try=lambda reason: None,
                                      safe_mode=self.host.safe_mode, on_card=self.host.card)
        real = guard._snapshot

        def snapshot() -> dict:
            got = real()
            guard.last = got
            return got
        guard._snapshot = snapshot
        return guard

    @contextlib.contextmanager
    def _counted(self, rec: TryRecord):
        """One check or command in flight; gives the add's step as it read when this one was counted. Being
        counted and reading the step are one action, so once an add has read "none in flight" and set "moving",
        nothing new can start on the folder (D112)."""
        with rec.busy:
            rec.in_flight += 1
            step = rec.add_step
        try:
            yield step
        finally:
            with rec.busy:
                rec.in_flight -= 1
                rec.busy.notify_all()

    def _settle(self, rec: TryRecord) -> bool:
        """Wait until every check and command in flight has its verdict, then let none start ("moving"). False:
        one was still going after :data:`SETTLE_S`, and nothing was changed. No lock is held while they run."""
        until = time.monotonic() + SETTLE_S
        with rec.busy:
            while rec.in_flight:
                left = until - time.monotonic()
                if left <= 0:
                    return False
                rec.busy.wait(min(left, 0.25))
            rec.add_step = "moving"
        return True

    def _mark_ended(self, rec: TryRecord, line: str) -> dict:
        """This try is over: from here it is never handed over. The caller stops it next, after it
        is no longer counted, because that can be slow (``docker compose down``) and no one should wait for it."""
        self._ended[rec.id] = f"{rec.build_id}|{line}"
        rec.base = None                                  # judged once; ending it does not judge again
        return {"ok": False, "ended": True, "said": line}

    def _judge(self, rec: TryRecord, guard: Any) -> dict:
        """Compare against the try's baseline. Clean (or pip-recorded): the baseline moves to exactly what was
        judged. Not clean: L4's guard undid the try's change (or called safe mode), and the try is marked as
        ended. The caller ends it."""
        result = guard.finish()
        rec.scanned_at = time.monotonic()
        if result.get("ok"):
            if rec.id not in self._ended:                # a check that ended the try keeps it ended
                rec.base = getattr(guard, "last", None) or rec.base
            return {}
        return self._mark_ended(rec, CORE_HOOK_LINE if result.get("core") else TRY_HOOK_LINE)

    def _guarded(self, rec: TryRecord, go) -> dict:
        """Run *go* inside L4's HookGuard, judged against the try's own baseline (09 section 3, X23)."""
        bad: dict = {}
        try:
            with self._counted(rec) as step:
                if step:
                    return {"ok": False, "said": "This app is being added, so nothing more runs in it."}
                if rec.base is None:
                    return {"ok": False, "said": "This try has no start-up baseline, so nothing runs."}
                try:
                    guard = self._new_guard(rec.folder)
                except NotBuilt as exc:
                    return {"ok": False, "said": f"{exc} So no command runs."}
                guard.before = rec.base                  # never a fresh "before": that would take a change as normal
                try:
                    got = go()
                finally:
                    try:
                        bad = self._judge(rec, guard)
                    except OSError:
                        bad = self._mark_ended(rec, TRY_HOOK_LINE)
        finally:
            if bad:
                self.end(rec)
        return bad or got

    def rescan(self, rec: TryRecord, *, force: bool = False) -> dict:
        """While the try's servers run, the same check with no command (Caretaker's tick, each Toolsmith step)."""
        bad: dict = {}
        try:
            with self._counted(rec) as step:
                if step == "moving":                     # the add has its folder: nothing of the try runs now
                    return {}
                if rec.ended or rec.base is None or rec.folder is None or rec.broker is None:
                    return {}
                if not rec.broker.alive() and not rec.compose:
                    return {}
                if not force and time.monotonic() - rec.scanned_at < RESCAN_EVERY_S:
                    return {}
                try:
                    guard = self._new_guard(rec.folder)
                except NotBuilt:
                    return {}
                guard.before = rec.base
                try:
                    bad = self._judge(rec, guard)
                except OSError:
                    bad = self._mark_ended(rec, TRY_HOOK_LINE)
        finally:
            if bad:
                self.end(rec)
        return bad

    def rescan_all(self, build_id: str | None = None) -> None:
        for rec in list(self._tries.values()):
            if build_id is None or rec.build_id == build_id:
                try:
                    self.rescan(rec)
                except Exception:  # noqa: BLE001 - the next tick tries again
                    pass

    def _plan(self, rec: TryRecord, request: Any, port: int | None = None) -> Any:
        """The broker's plan for *request* (one program and its arguments, no shell, D60/D61), with a compose
        program put behind the Docker wall; or the reply that it is not run. *port*: a server's own port."""
        if rec.folder is None or rec.broker is None:
            return {"ok": False, "said": CHECK_FIRST}
        planned = rec.broker.plan(request, port)
        if isinstance(planned, dict) or planned.kind != "compose":
            return planned
        try:
            argv, refusal = self._compose(rec, planned.rest)
        except NotBuilt as exc:
            return {"ok": False, "said": f"{exc} Docker apps start only behind it (D30), so this one "
                                         "can't start in Docker yet."}
        except Refused as exc:
            return {"ok": False, "said": str(exc)}
        if refusal:
            return rec.broker.refused(refusal, request, planned)
        planned.argv = argv
        planned.line = allowlist.join(["docker", *argv[1:]])
        return planned

    def run(self, rec: TryRecord, request: Any) -> dict:
        """Run one program to its end, zone checked, inside L4's HookGuard."""
        planned = self._plan(rec, request)
        if isinstance(planned, dict):
            return planned
        gate = self._gate(rec, planned.line, "command", cwd=planned.cwd)
        if gate is not None:
            return gate
        got = self._guarded(rec, lambda: rec.broker.run(planned=planned))
        if planned.kind == "compose" and rec.compose and not got.get("ended"):
            got["compose"] = dict(rec.compose)
            self._gate_note(rec, planned.rest, got)
        return self._bridge_note(rec, got, "said")

    def _gate_note(self, rec: TryRecord, rest: list[str], got: dict) -> None:
        """After a try's ``compose up`` on plain Linux Docker: when the wall did not hold or its gate can't reach the
        Bridge proxy (a firewall), one plain line, and the try's containers come down, as at Steward's start. Toolsmith
        does not take it for the app's own fault. Anywhere else: nothing."""
        ask = getattr(self.ports, "docker_gate_problem", None)
        if ask is None or got.get("exit") != 0 or "up" not in rest:
            return
        files = [w for f in rec.compose["files"] for w in ("-f", f)]
        base = [rec.broker.docker or "docker", "compose", "-p", rec.compose["project"], *files]

        def run(args: list[str]) -> int:
            answer = rec.broker.core_run(base + args, rec.folder, allowlist.join(["docker", *base[1:], *args]), limit_s=30)
            code = answer.get("exit") if isinstance(answer, dict) else None
            return code if isinstance(code, int) else 1
        problem = ask(run, rec.pkg_id)
        if problem:
            down = [*base, "down", "-t", "1"]
            self._guarded(rec, lambda: rec.broker.core_run(down, rec.folder, allowlist.join(["docker", *down[1:]])))
            got["ok"] = False
            got["said"] = (str(got.get("said") or "") + "\n\n" + problem + " This app was stopped.").strip()

    def mcp(self, rec: TryRecord, request: Any, tool: str = "", arguments: Any = None) -> dict:
        """D124: ask this program, as an MCP server over stdio, for its tools and one real call: zone checked, inside
        L4's HookGuard, like any command."""
        planned = self._plan(rec, request)
        if isinstance(planned, dict):
            return planned
        gate = self._gate(rec, planned.line, "command", cwd=planned.cwd)
        if gate is not None:
            return gate
        return self._guarded(rec, lambda: rec.broker.ask_mcp(planned, tool, arguments))

    def start(self, rec: TryRecord, request: Any) -> dict:
        planned = self._plan(rec, request, rec.port)
        if isinstance(planned, dict):
            return planned
        gate = self._gate(rec, planned.line, "command", cwd=planned.cwd)
        if gate is not None:
            return gate
        log = rec.logs / f"server-{len(rec.broker.started)}.log"
        got = self._guarded(rec, lambda: rec.broker.start(None, rec.port, log, planned=planned))
        return self._bridge_note(rec, got, "said")

    # -- the Bridge's refusals for this try, in its replies (D70) -------------------------

    def _bridge_refused(self, rec: TryRecord) -> tuple[list[dict], int]:
        """The hosts the Bridge refused for this try's subject since its last mark, and the new mark. Read
        only; no port or an odd answer: none."""
        read = getattr(self.ports, "bridge_refused", None)
        if read is None or not rec.subject:
            return [], rec.bridge_seen
        try:
            rows, mark = read(rec.subject, rec.bridge_seen)
        except Exception:  # noqa: BLE001 - words for Toolsmith only: none is safe
            return [], rec.bridge_seen
        if not isinstance(rows, list) or not isinstance(mark, int):
            return [], rec.bridge_seen
        return [r for r in rows if isinstance(r, dict) and isinstance(r.get("host"), str)], max(mark, rec.bridge_seen)

    def _bridge_note(self, rec: TryRecord, got: Any, key: str) -> Any:
        """*got* with one plain line in *got[key]* naming the hosts the Bridge refused for this try since
        the last reply (host names only, never a path or a value), so Toolsmith stops retrying and can tell
        the owner. It widens nothing: the Bridge already said no."""
        if not isinstance(got, dict):
            return got
        rows, mark = self._bridge_refused(rec)
        rec.bridge_seen = mark
        if not rows:
            return got
        got["bridge_refused"] = [str(r["host"]) for r in rows]
        line = bridge_line(rows)
        got[key] = (str(got.get(key) or "") + "\n\n" + line).strip()
        return got

    def stop(self, rec: TryRecord, pid: Any = None) -> dict:
        """Stop a server this try started, by its job object (Toolsmith's ``stop_server``; never a kill)."""
        if rec.broker is None:
            return {"ok": False, "said": CHECK_FIRST}
        if pid is not None:
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                return {"ok": False, "said": "Give the process number start_server gave you, or none to stop all."}
        return rec.broker.stop(pid)

    def log(self, rec: TryRecord) -> dict:
        if rec.broker is None:
            return {"log": "", "alive": 0}
        listens = self.listens(rec)
        got = {"log": rec.broker.log_tail(), "alive": len(rec.broker.alive()), "listens": listens}
        return self._bridge_note(rec, got, "log")

    def listens(self, rec: TryRecord) -> dict:
        """Where this try's own servers really listen (lane I4b), read from the computer's listen table for the
        processes in the try's own job objects, never from what a log says alone:
        ``{port, own: [[address, port]] | None, app_port, exposed, taken, said}``.

        - ``app_port``: its own servers listen on another 127.0.0.1 port and not on the try's port (an app
          with its own fixed port, like a Flask app on 5000): Toolsmith reads that page instead.
        - ``exposed``: a server of the try listened beyond this computer (0.0.0.0, ``::`` or a LAN address),
          so the Core stopped that server (03 run note 7).
        - ``taken``: its log names a 127.0.0.1 port its own processes do not hold, and another program does.
        ``own`` is None when the listen table can't be read (unknown, never "nowhere")."""
        from ...steward import ports as sp
        from ..toolsmith import runbook
        out: dict[str, Any] = {"port": rec.port, "own": None, "app_port": None, "exposed": [], "taken": [],
                               "said": ""}
        if rec.broker is None:
            return out
        named = runbook.log_listens(rec.broker.log_tail())
        own: list[tuple[str, int]] = []
        known = True
        for srv in rec.broker.alive():
            try:
                pids = set(srv.job.pids()) if srv.job is not None and srv.job.contained else set()
            except Exception:  # noqa: BLE001 - a job that can't be read: unknown
                pids = set()
            got = sp.job_listeners(pids) if pids else None
            if got is None:
                known = False
                continue
            wide = [(a, p) for a, p in got if sp.network_reachable([a])]
            if wide:
                rec.broker.stop(srv.pid)                 # never left listening beyond this computer
                rec.exposed += [[a, p] for a, p in wide if [a, p] not in rec.exposed]
                continue
            own += [x for x in got if x not in own]
        name = rec.cand.get("name") or "It"
        lines = []
        out["exposed"] = [list(x) for x in rec.exposed]       # said on every later read too
        if out["exposed"]:
            where = ", ".join(f"{a}:{p}" for a, p in out["exposed"][:3])
            lines.append(f"{name} listened on all networks ({where}), not only on this computer, so the Core "
                         "stopped that server. Start it bound to 127.0.0.1 (it gets HOST=127.0.0.1; or its own "
                         "--host 127.0.0.1 setting).")
        if known:
            out["own"] = [[a, p] for a, p in own]
        ports = {p for _a, p in own}
        if known and ports and rec.port not in ports:
            said_first = [p for h, p in named if runbook.is_loopback(h) and p in ports]
            out["app_port"] = (said_first or sorted(ports))[0]
            lines.append(f"{name} listens on http://127.0.0.1:{out['app_port']}, not on its port {rec.port}.")
        if known:
            # A port its log says it listens on (127.0.0.1) or could not take ("already in use"), held by none
            # of the try's own processes, and held by another program right now.
            said_ports = [p for h, p in named if h and runbook.is_loopback(h) and p != rec.port]
            said_ports += runbook.log_in_use(rec.broker.log_tail())
            for p in said_ports:
                if p not in ports and p not in out["taken"]:
                    try:
                        busy = sp.port_in_use(p)
                    except OSError:
                        busy = False
                    if busy:
                        out["taken"].append(p)
            if out["taken"]:
                lines.append(f"Its log says it uses port {', '.join(map(str, out['taken']))}, but another program "
                             f"on this computer already holds {'that port' if len(out['taken']) == 1 else 'those'}"
                             f", so {name} could not use it. Start it on its port {rec.port} instead.")
        out["said"] = " ".join(lines)
        if rec.compose:
            out["published"] = self._published(rec)
        return out

    def _published(self, rec: TryRecord) -> list[int] | None:
        """The host ports this try's own compose project publishes, in Docker's own word, so a port its file only names
        while another program holds it never counts. None when Docker can't say (unknown, never "nowhere")."""
        argv = [rec.broker.docker or "docker", "ps", "--filter", f"label=com.docker.compose.project={rec.compose['project']}",
                "--format", "{{.Ports}}"]
        try:
            got = rec.broker.core_run(argv, rec.folder, "docker ps", limit_s=DOCKER_PS_S)
        except Exception:  # noqa: BLE001 - can't tell: not counted
            return None
        if not got.get("ok"):
            return None
        ports: set[int] = set()
        for first, last in _PUBLISHED.findall(str(got.get("output") or "")):
            ports.update(range(int(first), int(last or first) + 1))
        return sorted(ports)

    def _path(self, rec: TryRecord, rel: Any, *, link_itself: bool = False) -> tuple[Path | None, str]:
        """``(path, rel)`` for a path inside the app's folder, else ``(None, "")``. The folder itself is ``""``.
        Resolved through every junction or link; *link_itself* (delete): a link is judged by its own folder."""
        rel = str(rel or "").replace("\\", "/").strip()
        rel = rel[2:] if rel.startswith("./") else rel
        if os.name != "nt" and rel.startswith("/"):
            return None, ""                       # Linux and Mac: a whole path, as C:/... is on Windows, never a name here
        rel = rel.lstrip("/") if not re.match(r"^[A-Za-z]:", rel) else rel
        if (rec.folder is None or ".." in rel.split("/") or re.match(r"^[A-Za-z]:", rel) or rel.startswith("~")
                or ":" in rel or "\x00" in rel):
            return None, ""
        rel = "/".join(p for p in rel.split("/") if p and p != ".")
        path = rec.folder / rel if rel else rec.folder
        try:
            real = Path(os.path.realpath(path))
            if link_itself and rel and _is_link(path):
                real = Path(os.path.realpath(path.parent)) / path.name
            real.relative_to(Path(os.path.realpath(rec.folder)))
        except (ValueError, OSError):
            return None, ""
        return path, rel

    @staticmethod
    def _real_parts(rec: TryRecord, path: Path, link_itself: bool = False) -> list[str] | None:
        """The parts of *path* under the app's folder as Windows reads them: the real path (through every
        junction or link, a short 8.3 name made long), each part in one letter case with trailing dots and
        spaces dropped. None when it can't be judged (callers count that as the Core's, fail closed)."""
        try:
            root = Path(os.path.realpath(rec.folder))
            path = Path(path)
            # link_itself (delete): a link is judged where it is, its own folder and name, never its target.
            if link_itself and _is_link(path):
                real = Path(os.path.realpath(path.parent)) / path.name
            else:
                real = Path(os.path.realpath(path))
            rel = real.relative_to(root)
        except (ValueError, OSError, TypeError):
            return None
        return [x.rstrip(" .").casefold() for x in rel.parts]

    @classmethod
    def _venv_part(cls, rec: TryRecord, path: Path, link_itself: bool = False) -> str:
        """Where *path* is against the try's own ``.venv`` (the Core's, D51), judged like :meth:`_core_file`:
        ``"top"`` (the .venv folder itself), ``"inside"`` (anything in it, or a path that can't be judged)
        or "" (not in it)."""
        parts = cls._real_parts(rec, path, link_itself)
        if parts is None:
            return "inside"
        if not parts or parts[0] != ".venv":
            return ""
        return "top" if len(parts) == 1 else "inside"

    @classmethod
    def _core_file(cls, rec: TryRecord, path: Path, link_itself: bool = False) -> bool:
        """``puffpai.json`` at the top of the app's folder, or anything in ``.buildbot``: the Core's, never
        written, moved or deleted by Toolsmith. Judged on the real path (any letter case, a short 8.3 name or
        trailing dots that Windows reads as the same file); a path that can't be judged counts as one."""
        parts = cls._real_parts(rec, path, link_itself)
        if parts is None:
            return True
        # .git at the top is the try's own git boundary (Trials.check): a .git file Toolsmith wrote there
        # ("gitdir: <another repository>") would point every git command in the try somewhere else.
        return bool(parts) and (parts[0] in (".buildbot", ".git") or (len(parts) == 1 and parts[0] == "puffpai.json"))

    def write(self, rec: TryRecord, rel: str, text: str) -> dict:
        path, rel = self._path(rec, rel)
        if path is None or not rel:
            return {"ok": False, "said": INSIDE_LINE}
        if self._venv_part(rec, path):
            return {"ok": False, "said": VENV_LINE}
        if self._core_file(rec, path):
            return {"ok": False, "said": CORE_FILE_LINE}
        data = str(text or "")
        if len(data.encode("utf-8")) > MAX_FILE_BYTES:
            return {"ok": False, "said": "That file is too big for Toolsmith to write."}
        gate = self._gate(rec, str(path), "path", write=True)
        if gate is not None:
            return gate
        if _is_link(path) or path.is_dir():
            return {"ok": False, "said": "That is a folder or a link, so it was not written."}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
        rec.files.add(rel)
        rec.checked_drop(rel)
        return {"ok": True, "said": f"Wrote {rel}."}

    def fs(self, rec: TryRecord, body: dict) -> dict:
        """The Core's file tools for the try folder only (D60): read, list, copy, move, mkdir, delete. Each path
        is inside the app's folder and zone checked; nothing goes through a junction or link; ``.venv`` is the
        Core's (only a delete of the whole of it)."""
        if rec.folder is None or rec.broker is None:
            return {"ok": False, "said": CHECK_FIRST}
        op = str(body.get("op") or "")
        if op in ("read", "list"):
            path, rel = self._path(rec, body.get("path"))
            if path is None:
                return {"ok": False, "said": INSIDE_LINE}
            gate = self._gate(rec, str(path), "path")
            if gate is not None:
                return gate
            return self._read(path, rel) if op == "read" else self._list(path, rel)
        if op in ("copy", "move"):
            src, a = self._path(rec, body.get("from"))
            dst, b = self._path(rec, body.get("to"))
            if src is None or dst is None or not a or not b:
                return {"ok": False, "said": INSIDE_LINE}
            if self._venv_part(rec, src) or self._venv_part(rec, dst):
                return {"ok": False, "said": VENV_LINE}
            if self._core_file(rec, dst) or (op == "move" and self._core_file(rec, src)):
                return {"ok": False, "said": CORE_FILE_LINE}
            for target, write in ((src, op == "move"), (dst, True)):
                gate = self._gate(rec, str(target), "path", write=write)
                if gate is not None:
                    return gate
            if _is_link(src) or not src.exists():
                return {"ok": False, "said": f"{a} is not a file or folder in the app's folder."}
            if dst.is_dir() and not _is_link(dst):
                dst, b = dst / src.name, f"{b}/{src.name}"
                if self._venv_part(rec, dst):
                    return {"ok": False, "said": VENV_LINE}
                if self._core_file(rec, dst):
                    return {"ok": False, "said": CORE_FILE_LINE}
                gate = self._gate(rec, str(dst), "path", write=True)
                if gate is not None:
                    return gate
            if _is_link(dst) or (dst.exists() and (src.is_dir() or dst.is_dir())):
                return {"ok": False, "said": f"{b} is there already."}
            if src.is_dir() and _has_link(src):
                return {"ok": False, "said": f"{a} holds a link to somewhere else, so it was not copied or moved."}
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                if op == "copy":
                    shutil.copytree(src, dst) if src.is_dir() else shutil.copyfile(src, dst)
                else:
                    os.replace(src, dst)
            except OSError as exc:
                return {"ok": False, "said": f"It could not be done ({type(exc).__name__})."}
            if op == "move":
                rec.files = {(b + f[len(a):]) if f == a or f.startswith(a + "/") else f for f in rec.files}
                rec.dirs = {(b + f[len(a):]) if f == a or f.startswith(a + "/") else f for f in rec.dirs}
                rec.checked_move(a, b)
            return {"ok": True, "said": f"{'Copied' if op == 'copy' else 'Moved'} {a} to {b}."}
        if op == "mkdir":
            path, rel = self._path(rec, body.get("path"))
            if path is None or not rel:
                return {"ok": False, "said": INSIDE_LINE}
            if self._venv_part(rec, path):
                return {"ok": False, "said": VENV_LINE}
            if self._core_file(rec, path):
                return {"ok": False, "said": CORE_FILE_LINE}
            gate = self._gate(rec, str(path), "path", write=True)
            if gate is not None:
                return gate
            if path.exists() and not path.is_dir():
                return {"ok": False, "said": f"{rel} is a file."}
            path.mkdir(parents=True, exist_ok=True)
            rec.dirs.add(rel)
            return {"ok": True, "said": f"Made the folder {rel}."}
        if op == "delete":
            path, rel = self._path(rec, body.get("path"), link_itself=True)
            if path is None or not rel:
                return {"ok": False, "said": INSIDE_LINE}
            venv = self._venv_part(rec, path, link_itself=True)
            if venv == "inside":                     # only the whole .venv goes, never a part of it
                return {"ok": False, "said": VENV_LINE}
            if self._core_file(rec, path, link_itself=True):
                return {"ok": False, "said": CORE_FILE_LINE}
            gate = self._gate(rec, str(path), "path", write=True)
            if gate is not None:
                return gate
            if not path.exists() and not _is_link(path):
                return {"ok": True, "said": f"{rel} is not there."}
            if _is_link(path):                       # the link itself goes, never what it points at
                try:
                    path.rmdir() if path.is_dir() else path.unlink()
                except OSError:
                    os.unlink(path)
            elif path.is_dir():
                remove_try_folder(path, rec.folder)
            else:
                try:
                    path.chmod(stat.S_IWRITE)
                except OSError:
                    pass
                path.unlink()
            if path.exists() or _is_link(path):
                return {"ok": False, "said": f"{rel} could not be deleted (in use?)."}
            rec.files = {f for f in rec.files if f != rel and not f.startswith(rel + "/")}
            rec.dirs = {f for f in rec.dirs if f != rel and not f.startswith(rel + "/")}
            rec.checked_drop(rel)
            if venv == "top":
                rec.broker.venv = None
            return {"ok": True, "said": f"Deleted {rel}."}
        return {"ok": False, "said": "The file tools are read, list, copy, move, mkdir and delete."}

    @staticmethod
    def _read(path: Path, rel: str) -> dict:
        if not path.is_file():
            return {"ok": False, "said": f"{rel or 'That'} is not a file."}
        try:
            with open(path, "rb") as fh:
                raw = fh.read(READ_BYTES + 1)
        except OSError as exc:
            return {"ok": False, "said": f"It could not be read ({type(exc).__name__})."}
        if b"\x00" in raw[:4096]:
            return {"ok": False, "said": f"{rel} is not a text file."}
        text = raw[:READ_BYTES].decode("utf-8", errors="replace")
        more = "\n(cut: the file is longer)" if len(raw) > READ_BYTES else ""
        return {"ok": True, "text": text + more, "said": text + more}

    @staticmethod
    def _list(path: Path, rel: str) -> dict:
        if not path.is_dir() or _is_link(path):
            return {"ok": False, "said": f"{rel or 'That'} is not a folder."}
        rows = []
        try:
            for child in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:LIST_MAX]:
                if _is_link(child):
                    rows.append(f"{child.name} (link)")
                elif child.is_dir():
                    rows.append(child.name + "/")
                else:
                    try:
                        rows.append(f"{child.name} ({child.stat().st_size} bytes)")
                    except OSError:
                        rows.append(child.name)
        except OSError as exc:
            return {"ok": False, "said": f"It could not be listed ({type(exc).__name__})."}
        said = "\n".join(rows) or "(empty)"
        return {"ok": True, "entries": rows, "said": said}

    # -- 4 and 5: end, or install ---------------------------------------------------

    def stop_started(self, rec: TryRecord) -> dict | None:
        """Stop what the try started. Gives the verdict of the check around ``docker compose down``, if there was
        one: :meth:`end` is ending the try anyway, and :meth:`add` must not hand over a try that changed."""
        if rec.broker is None:
            return None
        found = None
        compose, rec.compose = rec.compose, None
        if compose and rec.folder is not None:
            files = [w for f in compose["files"] for w in ("-f", f)]
            argv = [rec.broker.docker or "docker", "compose", "-p", compose["project"], *files, "down"]
            line = allowlist.join(["docker", *argv[1:]])
            try:
                if rec.base is not None and not rec.ended:
                    guard = self._new_guard(rec.folder)
                    guard.before = rec.base
                    try:
                        rec.broker.core_run(argv, rec.folder, line)
                    finally:
                        found = {"ok": False}           # a check that can't finish counts as a change
                        found = guard.finish()          # undoes a try change / calls safe mode
                else:
                    rec.broker.core_run(argv, rec.folder, line)
            except Exception:  # noqa: BLE001 - best effort; the project name is its own
                pass
        rec.broker.stop_all()
        return found

    def _stop_for_add(self, rec: TryRecord) -> None:
        """Stop what the try started; a change found on the way ends the try, as any other check's would."""
        found = self.stop_started(rec)
        if found is not None and not found.get("ok"):
            self._mark_ended(rec, CORE_HOOK_LINE if found.get("core") else TRY_HOOK_LINE)
            self.end(rec)

    def end(self, rec: TryRecord, remove: bool = True) -> dict:
        """Stop what the try started; remove its folder (never an installed one) and its own TEMP."""
        with self._lock:
            self._ended_refs.setdefault((rec.build_id, rec.job_id), set()).add(str(rec.cand.get("ref") or ""))
        self.stop_started(rec)
        removed = False
        if remove and not rec.installed:
            removed = remove_try_folder(rec.folder or rec.staging, self.packages)
            if rec.logs is not None and rec.scratch is not None:
                remove_try_folder(rec.scratch, rec.logs)    # junction-safe, like the try folder
        if remove and rec.temp is not None:
            if broker_mod.remove_temp(rec.temp, self.host.state):
                rec.temp = None
        if remove:
            rec.ended = True
            with self._lock:
                self._tries.pop(rec.id, None)
        return {"ok": True, "removed": removed}

    def add(self, rec: TryRecord, built_with: str = "local", manifest: Any = None) -> dict:
        r"""Hand the working try to the Package Manager; she answers its one card (never Toolsmith).

        *manifest* is the app's ``puffpai.json`` as Toolsmith made it from what worked; the Core checks it
        and writes it itself as ``.buildbot\puffpai.json``, the only manifest an adopted app runs from."""
        if rec.pending:
            return self.add_state(rec)
        if rec.folder is None:
            return {"ok": False, "said": CHECK_FIRST}
        with rec.busy:
            if rec.add_step:
                return {"ok": False, "said": "This app is being added already."}
            rec.add_step = "stopping"                    # set before the stop, so no command starts after it
        try:
            return self._add(rec, built_with, manifest)
        finally:
            with rec.busy:
                rec.add_step = ""

    def _add(self, rec: TryRecord, built_with: str, manifest: Any) -> dict:
        from ...packages import manifest as manifest_mod
        self._stop_for_add(rec)
        if self._try_is_over(rec):
            return self._over_reply(rec)
        if not isinstance(manifest, dict):
            return {"ok": False, "said": NO_MANIFEST}
        problems = manifest_mod.validate(manifest)
        if problems:
            return {"ok": False, "said": "Its puffpai.json is not valid: " + problems[0]}
        origin = adopt.origin_of(rec.cand)
        if manifest.get("id") != rec.pkg_id or manifest.get("origin") != origin:
            return {"ok": False, "said": "Its puffpai.json does not name this app, so it was not installed."}
        # Every check and command already going reaches its verdict first (a real change ends the try here, and
        # a command's was stopped above, so it is quick). Then none starts, so the Core's own move is never taken
        # for a change by the try, and no verdict can land once the folder has moved (D112).
        if not self._settle(rec):
            return {"ok": False, "said": "A check or command of this try was still going after its programs "
                                         "stopped, so it was not installed."}
        self._stop_for_add(rec)                          # anything that started just before the stop
        if self._try_is_over(rec):
            return self._over_reply(rec)
        said = self._write_manifest(rec, manifest)
        if said:
            return {"ok": False, "said": said}
        try:
            man, _path = manifest_mod.load(rec.folder, adopted=not adopt.made(rec.cand))
        except manifest_mod.ManifestError as exc:
            return {"ok": False, "said": "Its puffpai.json is not valid: " + (exc.problems[0] if exc.problems else "")}
        if man != manifest:
            return {"ok": False, "said": "Its puffpai.json changed while it was written, so it was not installed."}
        self._drop_run_files(rec)
        aside = self._move_aside(rec)
        if aside is None:
            return {"ok": False, "said": "Its folder was still in use after its programs stopped, so it was not "
                                         "installed."}
        rec.folder = aside
        try:
            got = self.ports.packages_add(rec.folder, plan=self._plan_for(rec),   # NotBuilt holds the job
                                          adopted=adopt.adopted_word(rec.cand))
        except Refused as exc:                          # it said no to THIS app: the next candidate goes on
            self._mark_ended(rec, str(exc))             # its folder is aside now, so no later check could be fair
            self.end(rec)
            return {"ok": False, "ended": True, "said": str(exc)}
        except Exception:
            self.end(rec)                               # the same: this try can't go on from the moved folder
            raise
        rec.pending, rec.manifest, rec.built_with = str(got["consent_id"]), man, str(built_with or "local")[:80]
        rec.covered = str(got.get("covered_by") or "")
        # The Package Manager holds a checked copy; the install renames it to packages\<id>, so the try's
        # own folder (moved aside above) goes now, junction-safe.
        remove_try_folder(rec.folder, self.packages)
        if rec.scratch is not None and rec.logs is not None:
            remove_try_folder(rec.scratch, rec.logs)
        rec.folder = rec.broker = None          # packages\<id> is the Package Manager's from here on: never removed by a try
        if rec.temp is not None and broker_mod.remove_temp(rec.temp, self.host.state):
            rec.temp = None
        return self.add_state(rec)

    def _try_is_over(self, rec: TryRecord) -> bool:
        return rec.ended or rec.id in self._ended

    def _over_reply(self, rec: TryRecord) -> dict:
        why = self._ended.get(rec.id, "").split("|", 1)[-1] or "This try has ended, so it was not installed."
        return {"ok": False, "ended": True, "said": why}

    def _write_manifest(self, rec: TryRecord, manifest: dict) -> str:
        r"""The Core writes ``.buildbot\puffpai.json`` in the try folder ("" = done, else why not). A
        ``.buildbot`` the download shipped as a file or a link is taken away first (only the link itself)."""
        folder = rec.folder
        bb = folder / ".buildbot"
        try:
            if _is_link(bb):
                try:
                    os.rmdir(bb)
                except OSError:
                    os.unlink(bb)
            elif bb.exists() and not bb.is_dir():
                bb.chmod(stat.S_IWRITE)
                bb.unlink()
            bb.mkdir(exist_ok=True)
            Path(os.path.realpath(bb)).relative_to(Path(os.path.realpath(folder)))
            target = bb / "puffpai.json"
            if _is_link(target) or target.is_dir():
                return "Its .buildbot folder holds a link or a folder named puffpai.json, so it was not installed."
            if target.exists():
                target.chmod(stat.S_IWRITE)
            target.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            return "Its puffpai.json could not be written, so it was not installed."
        rec.files.add(".buildbot/puffpai.json")
        return ""

    def _drop_run_files(self, rec: TryRecord) -> None:
        """Files the run made that are not the app (not in the checked download, not written by Toolsmith):
        a database the app made, logs, build output. The manifest's setup makes what it needs again.
        The folders the run made go too: an empty ``mysql`` folder tells a database it is already set up.
        A folder with anything left in it (a link, a locked file, a regenerated folder) stays."""
        for rel in sorted(set(_files(rec.folder)) - rec.files, reverse=True):
            path = rec.folder / rel
            if _is_link(path):
                continue
            try:
                path.unlink()
            except OSError:
                pass
        kept = set(rec.dirs)
        for rel in rec.files | rec.dirs:
            kept.update(p.as_posix() for p in PurePosixPath(rel).parents)
        for rel in sorted(set(_folders(rec.folder)) - kept, reverse=True):     # a folder's own folders come first
            try:
                os.rmdir(rec.folder / rel)                                     # only ever an empty folder
            except OSError:
                pass

    def _move_aside(self, rec: TryRecord) -> Path | None:
        r"""The try folder, moved to ``packages\.staging\try-<hex>`` (so ``packages\<id>`` is free). On
        Windows a folder whose programs just stopped can stay locked for a moment, so it is tried again."""
        if _is_link(rec.folder):
            return None
        dest = self.packages / ".staging" / f"try-{secrets.token_hex(4)}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(40):
            try:
                os.replace(rec.folder, dest)
                return dest
            except OSError:
                time.sleep(0.25)
        return None

    def _plan_for(self, rec: TryRecord) -> dict | None:
        """The build's plan Yes for the Package Manager to check this app against (03 3.3)."""
        b = self.host.store.read(rec.build_id) or {}
        cid = (b.get("consent") or {}).get("id")
        if not cid or not b.get("yes") or not isinstance(b.get("envelope"), dict):
            return None
        job = next((j for j in b.get("jobs") or [] if j.get("id") == rec.job_id), {})
        # The Core's own record of the steps she ticked Done during this build (D60), never Toolsmith's.
        ticked = b.get("ticked") if isinstance(b.get("ticked"), dict) else {}
        # The broker's record of what ran in this try (the Core's memory, never a route's): the plan's Yes
        # covers a command the Core starts later only when it is on it (plan_cover.commands_outside).
        broker = getattr(rec, "broker", None)
        ran = broker.record() if broker is not None else {}
        out = {"consent_id": cid, "envelope": b["envelope"], "candidate": rec.cand["ref"],
               "job": job.get("name") or "", "ticked": json.loads(json.dumps(ticked)),
               "ran": json.loads(json.dumps(ran))}
        held_yes = getattr(rec, "held_yes", None)
        if held_yes:
            # Her Yes on this try's hold card (the Core's record): those exact files are not asked about again.
            out["held"] = json.loads(json.dumps(held_yes))
        return out

    def add_state(self, rec: TryRecord) -> dict:
        """``{ok, waiting}`` while she decides; then installed (and running) or her No, in plain words."""
        if not rec.pending:
            return {"ok": False, "said": "Nothing of this try waits to be installed."}
        got = self.ports.packages_wait(rec.pending, 0.0)
        status = got.get("status")
        name = str((rec.manifest or {}).get("name") or rec.pkg_id)
        if status == "pending":
            if rec.covered:
                return {"ok": True, "waiting": True, "covered": True, "consent_id": rec.pending,
                        "said": f"{name} is being installed under the plan you said Yes to."}
            return {"ok": True, "waiting": True, "consent_id": rec.pending,
                    "said": f"{name} waits for your Yes on its card."}
        if status == "declined":
            self.end(rec)
            return {"ok": False, "answer": "no", "said": f"You said no to adding {name}."}
        if status != "installed":
            self.end(rec)
            problems = got.get("problems") or ["the Package Manager said no"]
            return {"ok": False, "said": "It did not start again from its puffpai.json: " + " ".join(map(str, problems))}
        rec.installed = True
        pkg_id = str(got.get("id") or rec.pkg_id)
        with self._lock:
            self._installed.setdefault(rec.build_id, set()).add(pkg_id)
        reg = self.host.registry
        row = reg.get(pkg_id) if reg is not None else None
        if row is not None:
            try:
                reg.update(pkg_id, built_with=getattr(rec, "built_with", "local"))
            except Exception:  # noqa: BLE001 - a missing field is not a failed build
                pass
            if row.get("status") != "running":
                try:
                    self.ports.steward_start(pkg_id)
                except (Refused, NotBuilt) as exc:
                    self.end(rec)
                    return {"ok": False, "said": f"It did not start again from its puffpai.json: {exc}"}
        self.end(rec, remove=True)
        return {"ok": True, "id": pkg_id, "manifest": json.loads(json.dumps(rec.manifest or {})),
                "said": f"Installed {name}."}

    def _own_ports(self, build_id: str) -> set[int]:
        """Where this build's running tries really listen, and what their compose projects publish."""
        out: set[int] = set()
        for rec in self.of_build(build_id):
            rep = self.listens(rec)
            out |= {int(x[1]) for x in rep.get("own") or []} | set(rep.get("published") or [])
        return out

    def app_ports(self, ids: set[str]) -> set[int]:
        """The ports of these Registry apps: their own port and the addresses of their pages, for the proof after a try."""
        reg, out = self.host.registry, set()
        for pkg_id in ids:
            row = reg.get(pkg_id) if reg is not None else None
            if not isinstance(row, dict):
                continue
            if isinstance(row.get("port"), int):
                out.add(row["port"])
            for page in (row.get("ui") or {}).get("pages") or []:
                m = _LOCAL.match(str(page.get("url") or "").replace("{port}", str(row.get("port")))) \
                    if isinstance(page, dict) else None
                if m:
                    out.add(int(m.group(1)))
        return out

    def page(self, build_id: str, url: str, apps: set[int] | None = None) -> dict:
        """A GET only to a port of this build's own: a running try's, or one of *apps*. Any other port gets none, whoever
        asks: only whether something holds it (a bind test, no connection), so nothing it shows comes back."""
        m = _LOCAL.match(str(url or ""))
        if not m:
            return {"ok": False, "said": "Give an address on 127.0.0.1."}
        port = int(m.group(1))
        if port not in self._own_ports(build_id) | (apps or set()):
            from ...steward import ports as sp
            try:
                answers = sp.port_in_use(port)
            except (OSError, OverflowError, ValueError):
                answers = False
            return {"ok": False, "not_own": True, "answers": bool(answers),
                    "said": f"No program of this try listens on port {port}, so the Core did not read that page."}
        got = self.ports.read_page(url)
        return {"ok": bool(got.get("ok")), "title": str(got.get("title") or "")[:300],
                "text": str(got.get("text") or "")[:20_000], "said": str(got.get("said") or "")[:500]}


__all__ = ["COMPOSE_NAMES", "TryRecord", "Trials", "free_port", "remove_try_folder"]
