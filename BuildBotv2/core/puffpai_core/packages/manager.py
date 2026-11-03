"""The Package Manager: the one install path (01 "Package Manager", 02 "Lifecycle").

``add(source, link=False)``: fetch -> validate -> Drawbridge check -> ONE Yes card.
It returns at the card (``{"consent_id", "id", "card"}``). When the owner
answers Yes, ``install`` runs: re-hash and re-check (the verdict is bound to
the staged tree's hash, X24) -> rename ``packages\\.staging\\<random>`` to
``packages\\<id>`` **before** setup -> ``.venv`` and setup in the final place ->
Registry row -> Steward starts it -> Caretaker probes it and reads its tools.

- **What runs is what was checked (X24).** Staging never keeps a ``.venv``,
  ``node_modules``, ``__pycache__`` or ``.git`` (a copy leaves them out; a git
  clone has them deleted) before the Drawbridge check and the hash, since
  setup makes them; Steward re-checks the hash right before setup.
- **After a restart** (``recover_after_restart``, at Core start): install and
  update cards still waiting from the last run expire with a plain line (their
  staged files are gone with the old process's memory), and
  ``packages\\.staging`` is emptied.
- A folder is copied (links and junctions left out); ``link=True`` runs the
  owner's own folder in place (``origin: local:<path>``), never copied, never
  deleted. A git URL is cloned into staging (``origin: github:<url>``; the
  Registry has no plain ``git:`` origin, so every git source is recorded so).
- **Drawbridge checks it, and its verdict is bound to the files** (X24, X26): lane L4's
  ``Drawbridge.check_package`` through ``seams.drawbridge_check`` (the Core's own Drawbridge,
  ``self.drawbridge``, with the audit log). ``block``, no Drawbridge, a crash or an odd answer is
  no. ``allow`` and ``warn`` go to the Yes card (a warn is said on it); ``hold`` goes to the Yes
  card with what is held on it, so her Yes is the Yes for exactly that. At install the folder is
  checked again and must be what was checked (``seams.drawbridge_bound``: L4's ``verify_tree``).
- After install, Drawbridge gets the package's everyday scope (09 section 3): the changing tools
  her install Yes listed under "changes" pass with no card; any other changing tool asks.
- Floor-level cards (D29): a provider that reads raw data or needs admin.
  A community provider that declares ``needs.admin`` never installs (D31(d)).
- ``remove(id, keep_data)``: stop, Registry row out, data kept or deleted
  (``data.keep_on_remove``; ``ask`` returns the question instead of guessing),
  package folder deleted junction-safely, a linked folder never.
- ``update(id)``: fetch again, re-check, show what changed (hosts, keys,
  changing tools, version), one Yes, swap the folder, data kept.
- **Her Yes is for one manifest file** (lane I2a part D): the sha256 of the manifest file her card
  showed (for an adopted app ``.buildbot/puffpai.json``) goes in the Registry row (``manifest_sha``)
  at install, and again only through an update's own Yes. Steward runs a package only while its file
  still has it (``Steward.manifest``).
- **The plan's Yes covers an adopted app** (03 3.3, ``plan_cover``): ``add(folder, plan=...)`` from
  Toolsmith's Core side names the build plan she said Yes to. The Package Manager reads that Yes
  from the consent store itself. Inside the plan, the app's card is made and answered at once
  "by plan:<consent id>" (no new card for her; the Bridge grants, ``yes_record`` and the audit stay
  the one path). Outside it, her card shows only what was not on the plan first. Never for a
  floor-level card.
- Providers: ``provider.installed`` / ``provider.changed`` on the Bus. No
  provider floor suite exists yet, so ``floor_suite`` is reported ``fail``
  (an unrun suite is not a pass).
- **Built-ins follow the code (lane I13).** A built-in's install is a copy (``packages\\<id>``) of the
  repo's ``packages\\<id>``, so a code update left it old. ``check_builtins`` (Core start, ``puffpai
  update``, ``POST /v1/packages/<id>/update``) compares each installed built-in with the repo's copy:
  the manifest version and the tree hash (``fsutil.tree_hash``: never ``.venv``, ``node_modules``,
  ``__pycache__``, ``.git`` or the Core's compose file; the package's data is in ``<bot>\\data\\<id>``,
  never in its folder). A built-in is an install that is not linked, whose Installs source and
  ``local:`` origin are exactly the repo's folder for that id. Anything else (an adopted app, a linked
  folder, a git clone, an outside folder) is never touched.
  - **System parts** (``SYSTEM_PARTS``: BuildBot's own code, like the Core) update with no card, with an
    audit line, only when Drawbridge says allow or warn with no reason the installed copy did not have,
    and the new manifest equals the installed one apart from ``_SILENT_FIELDS`` (version, description,
    name, icon). Any other manifest difference is the same Yes card as an app.
    A system part whose manifest changed since her Yes is left broken, never overwritten.
  - **Apps** get a Desktop notice and one Yes card, "<name> has an update"; her Yes applies it.
  - The swap is atomic for every update (``_swap``): stage and check, stop, move the old folder aside,
    move the new one in, set up and start. Any failure moves the old folder back, restores its row and
    starts it again if it ran. Data (``<bot>\\data\\<id>``) and settings are never touched.
- **From the store (D89).** ``add("store:<id>")``: the row comes from the official store first, then the owner's
  own sources in order (``store_index.Store``). A yanked (id, commit) is refused with its reason. Exactly the
  pinned commit is fetched into staging (``git init``, ``git fetch --depth 1 <repo> <commit>``, a fetch of its
  branches when the server will not send one commit, checkout, then HEAD must be that commit), and only the
  row's folder is moved out of the clone: a folder that leaves the clone or goes through a link is refused. The
  manifest's id and kind must be the row's. ``origin`` is ``store:verified`` only from the official source
  with tier verified; anything else is ``store:community``. The Registry row records ``store`` (source, repo,
  path, commit), so ``update(id)`` fetches the commit its source pins now, with its own Yes card.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import stat
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import contrast, seams
from ..bus import Bus, Source
from ..consent import ConsentError, ConsentStore, card_sha
from ..consent.shown import TUNNEL_SHORT
from ..paths import contracts_dir
from ..registry import Registry, RegistryError, row_from_manifest
from ..steward import procflags
from ..steward.steward import Steward, StewardError, run_block
from . import fsutil
from . import manifest as manifest_mod
from . import plan_cover
from . import store_index
from .installs import Installs

if TYPE_CHECKING:
    from ..caretaker import Caretaker

logger = logging.getLogger(__name__)
CORE = Source("core", "core:packages")
_GIT_URL = re.compile(r"^(https://|http://|ssh://|git@|file://)\S+$|^\S+\.git$")
_SYSTEM_DIRS = (("windows", "program files", "program files (x86)", "programdata") if os.name == "nt" else
                ("bin", "boot", "dev", "etc", "lib", "lib64", "proc", "sbin", "sys", "usr", "library", "system"))
LEFT_INSTALLING = "BuildBot stopped before it finished being added. Remove it and add it again"
EXPIRED_LINE = "This request expired when BuildBot restarted. Add it again."


class AddError(ValueError):
    """An add, remove or update that can't go ahead. ``problems`` holds plain sentences."""

    def __init__(self, problems: list[str] | str, status: int = 400) -> None:
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        self.status = status
        super().__init__("; ".join(self.problems))


@dataclass
class Pending:
    consent_id: str
    id: str
    folder: Path
    link: bool
    source: str
    origin: str
    manifest: dict
    tree_sha: str
    card: dict
    update_of: str | None = None
    verdict: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    manifest_sha: str = ""                # the sha256 of the manifest file her card showed (lane I2a part D)


#: What ``add`` gets as ``adopted`` for an app Toolsmith wrote itself (D121).
MADE = "made"
_ADOPTED_URL = re.compile(r"^https?://[^\s]{1,500}$")
_PACKAGE_ID = re.compile(r"^[a-z][a-z0-9-]{1,39}$")          # D55

#: The built-ins that are BuildBot itself (``wizard.terminal.BUILTINS``, the ones setup adds): updated at
#: Core start with no card, as the Core is (lane I13). Every other built-in is an app: a Yes card.
SYSTEM_PARTS = ("desktop", "wizard", "cloud-avatar")
#: The only manifest fields a system part's update may change with no card (lane I13): words and the
#: picture. Any other difference at all (kind, run, health, needs, ui, abilities, accepts, setup steps,
#: consent text, a field added or removed) gets her Yes card. Changed files under an unchanged manifest
#: stay silent: they are BuildBot's own code, checked by Drawbridge.
_SILENT_FIELDS = frozenset({"version", "description", "name", "icon"})
#: More fields one system part may change silently (lane I14): the wizard's own ``steps`` are BuildBot's
#: setup questions, not a new power (no host, key, tool or run block), so a code update that changes them
#: never holds setup behind a card.
_SILENT_FOR = {"wizard": frozenset({"steps"})}
#: Manifest fields in plain words, for an update card's "What changed" line (lane I14).
FIELD_WORDS = {
    "schema": "its manifest format", "id": "its id", "kind": "what kind of package it is", "name": "its name",
    "version": "its version", "description": "its description", "author": "its author", "license": "its license",
    "icon": "its picture", "consent": "its consent text", "run": "how it starts", "health": "its health check",
    "needs": "what it needs (internet, keys, hardware)", "ui": "its pages and tray", "abilities": "its tools",
    "accepts": "what it accepts", "setup_steps": "your setup steps for it", "steps": "the setup steps it asks",
    "provides": "the slots it fills", "reads": "what it may read", "includes": "what it includes",
}


def already_installed(name: str, id: str, source: str) -> str:
    """The "already installed" line with the exact commands to add it again: remove it with its data kept,
    then add it again. A built-in package is added again by its id (``puffpai update`` updates it in place)."""
    again = id if builtin_source(id) and Path(builtin_source(id) or "") == Path(str(source)) else str(source)
    again = f'"{again}"' if " " in again else again
    return (f"{name} ({id}) is already installed. To add it again, run: puffpai remove {id} --keep-data, "
            f"then: puffpai add {again}")


def builtin_source(source: str) -> str | None:
    """``puffpai add freshtable``: the folder of the Core's own built-in package with that id, or None.
    It is added like any folder, through the same checks and the same Yes card."""
    if not _PACKAGE_ID.fullmatch(source or ""):
        return None
    from ..desktop import builtin_dir
    folder = builtin_dir() / source
    return str(folder.resolve()) if (folder / "puffpai.json").is_file() and not is_real_link(folder) else None


def is_real_link(path: Path | str) -> bool:
    """True when *path* itself is a symlink or a junction, or can't be read. Only real links: a name-surrogate reparse
    tag (Windows' own test for "this points somewhere else"). A cloud placeholder (OneDrive, D108) is not one."""
    try:
        got = os.lstat(path)
    except (OSError, ValueError):
        return True
    return stat.S_ISLNK(got.st_mode) or bool(getattr(got, "st_reparse_tag", 0) & 0x20000000)


def is_builtin_path(id: str, stored: object) -> bool:
    """D108: *stored* (a Registry origin's or an install's path) is the repo's own folder for *id*, compared as plain
    text and resolving nothing in *stored*, so a source folder swapped for a junction later never counts. The repo
    itself may be reached through a junction or live under OneDrive: either spelling of its ``packages`` folder counts.
    The ``<id>`` folder itself may not be a link."""
    if builtin_source(id) is None or not isinstance(stored, str):
        return False
    from ..desktop import builtin_dir
    base = builtin_dir()
    want = {os.path.normcase(str(base / id)), os.path.normcase(os.path.join(os.path.realpath(base), id))}
    return os.path.normcase(stored) in want


def _origin(source: str, link: bool, git: bool) -> str:
    if git:
        return f"github:{source}"
    return f"local:{source}"


def _verdict(folder: Path, manifest: dict, origin: str, drawbridge: Any = None,
             before: dict | None = None) -> tuple[dict | None, list[str]]:
    """Lane L4's Drawbridge verdict on exactly this folder: ``(verdict, [])``, or ``(None, plain problems)``.

    No Drawbridge, a crash, ``block`` or any word but allow, warn or hold, or a verdict not bound to
    these files is a no (X24, X26). *before* is the verdict her card showed: at install a hold that
    was not on the card is a no."""
    try:
        verdict = seams.drawbridge_check(folder, manifest, origin=origin, drawbridge=drawbridge)
    except Exception as exc:
        logger.warning("drawbridge check crashed: %s", type(exc).__name__)
        return None, ["Drawbridge's check stopped with an error, so this was not added."]
    if verdict is None:
        return None, ["Drawbridge is not here, so nothing new can be added (a missing check is a no)."]
    word = verdict.get("verdict") if isinstance(verdict, dict) else None
    if word not in ("allow", "warn", "hold"):
        line = verdict.get("line") if isinstance(verdict, dict) and isinstance(verdict.get("line"), str) else ""
        codes = [str(c) for c in (verdict.get("reasons") or [])][:10] if isinstance(verdict, dict) else []
        return None, [line or f"Drawbridge said no ({', '.join(codes) or 'no answer'})."]
    if not seams.drawbridge_bound(folder, verdict, drawbridge):
        return None, ["Drawbridge checked different files than the ones staged, so this was not added."]
    if before is not None and verdict.get("files_mode") != before.get("files_mode"):
        # D95: her setting for files Drawbridge can't read changed after the card, so the card may not show
        # what is held now.
        return None, ["Your setting for files Drawbridge can't read changed after this card. Add it again."]
    if before is not None and word == "hold" and before.get("verdict") != "hold":
        return None, ["Something in the package now needs your Yes that the card did not show. Add it again."]
    return verdict, []


def shown_verdict(verdict: dict) -> dict:
    """What her Yes card says about Drawbridge's check (covered by the card's fingerprint). Toolsmith's
    host shows the same on a try's hold card."""
    from ..zone3.kinds import shown_name
    rows = [r for r in verdict.get("rows") or [] if isinstance(r, dict)]
    notes = [str(r.get("why") or "") for r in rows if r.get("result") in ("note", "fail") and r.get("why")]
    held: list[str] = []
    said: list[str] = []
    more = 0
    docs_only = tunnel = False
    for r in rows:
        if r.get("code") == "cant_check":
            docs_only = r.get("docs_only") is True
            tunnel = bool(r.get("tunnel"))
            held += [shown_name(f) for f in r.get("files") or [] if isinstance(f, str)]
            more += int(r.get("more") or 0) if isinstance(r.get("more"), int) else 0
            said += [str(x) for x in r.get("said") or [] if isinstance(x, str)]
    # ``held_said``: what the card says about the held files (D95), counts by kind and base names, never a path.
    # ``held``: their cleaned base names only; every held path stays in Drawbridge's own row (``all``).
    # ``held_docs_only``: every held file was read and holds words aimed at an AI (D105), so the card says that.
    # ``held_tunnel``: a held file has a tunnel tool in its code (D117), and the card leads with that.
    return {"verdict": verdict.get("verdict"), "line": str(verdict.get("line") or ""), "notes": notes[:20],
            "held": held[:20], "held_more": more, "held_said": said[:12], "tree_sha": verdict.get("tree_sha"),
            "files_mode": verdict.get("files_mode"), **({"held_docs_only": True} if docs_only else {}),
            **({"held_tunnel": True} if tunnel else {})}


def link_problem(folder: Path, state: Path) -> str | None:
    """Why the owner's folder can't be linked, or None (01 "The owner's own code folder")."""
    if not folder.is_absolute():
        return "Give the full path of the folder."
    if not folder.is_dir():
        return f"There is no folder {folder}."
    resolved = folder.resolve()
    if resolved.parent == resolved:
        return "A whole drive can't be a package. Pick the folder the code is in."
    if fsutil.is_link(folder):
        return "That folder is a link to somewhere else. Pick the real folder."
    own_code = contracts_dir().parent.resolve()
    for refused, words in ((state.resolve(), "the bot's own state folder"), (own_code, "BuildBot's own code")):
        if resolved == refused or refused in resolved.parents or resolved in refused.parents:
            return f"That folder holds {words}, so it can't be a package."
    parts = [p.lower() for p in resolved.parts[1:3]]
    if (parts and parts[0] in _SYSTEM_DIRS) or parts == ["private", "etc"]:     # a Mac's /etc is really /private/etc
        return "System folders can't be packages."
    return None


class PackageManager:
    def __init__(self, state: Path | str, registry: Registry, bus: Bus, consent: ConsentStore, steward: Steward,
                 installs: Installs, caretaker: "Caretaker | None" = None, start_after_install: bool = True) -> None:
        self.state = Path(state)
        self.registry = registry
        self.bus = bus
        self.consent = consent
        self.steward = steward
        self.installs = installs
        self.caretaker = caretaker
        self.start_after_install = start_after_install
        self.drawbridge: Any = None             # the Core's own Drawbridge (lane L4, zone3.service); else a fresh one
        self.on_install_end: Any = None         # fn(id): the Bridge closes its install grants (the Core's wiring)
        self.drop_address_grant: Any = None     # fn(consent_id, subject): the Bridge drops one Yes's grants (the wiring)
        self.packages = self.state / "packages"
        self.staging = self.packages / ".staging"
        self.staging.mkdir(parents=True, exist_ok=True)
        self._pending: dict[str, Pending] = {}
        self._results: dict[str, dict] = {}
        self._cond = threading.Condition()
        # Held from raising a card until it is in _pending (and the plan's own answer is in), and by
        # _answered: an answer that comes in that moment waits for it, so no Yes is ever missed.
        self._raising = threading.RLock()
        self.store = store_index.Store(self.state, registry)      # the store sources and their indexes (D89)
        consent.on_answer(self._answered)

    # -- Core start -------------------------------------------------------------
    def recover_after_restart(self) -> dict:
        """Expire install and update cards left from the last run, and empty staging.

        Their staged folder and plan lived in the old process, so a Yes now would install nothing. An install
        the last run did not finish has no one left to finish it (D122): its row says so, and never waits again.
        Returns ``{"expired": [consent ids], "staging_removed": n}``."""
        for row in self.registry.list():
            if row.get("status") == "installing":
                self._broken(row["id"], LEFT_INSTALLING)
        expired = []
        for record in self.consent.list(status="pending"):
            if record["scope"].get("kind") != "package" or "tree_sha" not in record["card"]:
                continue
            if record["consent_id"] in self._pending:
                continue
            _expire(self.consent, record["consent_id"], EXPIRED_LINE)
            expired.append(record["consent_id"])
        removed = 0
        mine = {p.folder.name for p in self._pending.values()}
        for entry in list(self.staging.iterdir()) if self.staging.is_dir() else []:
            if entry.name in mine:
                continue
            if fsutil.is_link(entry):
                fsutil._remove_link(str(entry))
                removed += 1
            elif entry.is_dir():
                removed += int(fsutil.remove_folder(entry, self.staging))
            else:
                entry.unlink(missing_ok=True)
                removed += 1
        return {"expired": expired, "staging_removed": removed}

    # -- add ----------------------------------------------------------------
    def add(self, source: str, link: bool = False, adopted: str | None = None, plan: dict | None = None,
            official_only: bool = False) -> dict:
        """Fetch, validate, check, and raise the one Yes card. ``{"consent_id", "id", "card"}``.

        *adopted* (in process only: Toolsmith's port, never a route) is the web address Toolsmith adopted
        the app from; the Registry then records ``origin: adopted:<url>`` (02 "Adopted packages"), which
        its ``puffpai.json`` must say too. Adopted apps open in a new tab, and their tools that don't say
        they are read-only count as changing (X27). The word ``made`` is an app Toolsmith wrote itself (D121):
        ``origin: made``, the same path, and it is never on the plan, so it always asks on its own card.

        *official_only* (in process only: a plan's store suggestion, D96, never a route): ``store:<id>`` comes from
        the official store as it reads now and from nowhere else. By hand, ``store:<id>`` also looks in her sources.

        *plan* (Toolsmith's adopted apps only): the build plan's Yes this app comes under. Inside the
        plan the card is answered by that Yes (``covered_by`` in the answer); outside it, the card
        shows the difference."""
        if adopted is not None and adopted != MADE and not _ADOPTED_URL.match(str(adopted)):
            raise AddError(["An adopted app's address must be a plain http(s) web address."])
        if self.caretaker is not None and self.caretaker.in_safe_mode:
            raise AddError(["The Core is in safe mode, so nothing new is added."] + self.caretaker.safe_reasons, 503)
        source = str(source).strip()
        if source.startswith(STORE_PREFIX):
            if link or adopted is not None or plan is not None:
                raise AddError("A store package is added on its own: not linked, not adopted, not part of a plan.")
            return self._add_store(source[len(STORE_PREFIX):].strip(), official_only)
        if official_only:
            raise AddError("Only a store package can be pinned to the official store.")
        git = bool(_GIT_URL.match(source)) and not Path(source).is_dir()
        if not git and not link and Path(source).is_dir():
            source = str(Path(source).resolve())
        elif not git and not link:
            source = builtin_source(source) or source   # "freshtable": the Core's own built-in package
        folder, staged = self._fetch(source, link, git)
        try:
            return self._stage(folder, staged, source, link, git, adopted=adopted, plan=plan)
        except BaseException:
            if staged:
                fsutil.remove_folder(folder, self.staging)
            raise

    def _fetch(self, source: str, link: bool, git: bool) -> tuple[Path, bool]:
        if git:
            if link:
                raise AddError("Only a folder on this computer can be linked, not a git URL.")
            return self._clone(source), True
        path = Path(os.path.expandvars(os.path.expanduser(source)))
        if link:
            problem = link_problem(path, self.state)
            if problem:
                raise AddError(problem)
            if manifest_mod.find(path) is None:      # D133: her own code with no manifest: the Core reads how it starts
                from .from_folder import write_manifest
                problem = write_manifest(path)
                if problem:
                    raise AddError(problem)
            return path, False
        if not path.is_dir():
            if source.startswith("adopt "):
                raise AddError("Adopting an outside app is Toolsmith's job; it calls add() with the folder it made.")
            raise AddError(f"{source} is not a folder on this computer, a git URL or the id of a built-in package. "
                           "For a store package, add store:<id>.")
        dst = self._new_staging()
        skipped = fsutil.copy_package(path, dst)
        if skipped:
            logger.info("add %s: left out links %s", source, skipped[:5])
        self._strip(dst)
        return dst, True

    def _new_staging(self) -> Path:
        """A short new folder name in staging, ``packages\\.staging\\<8 hex>`` (deep clones need the room)."""
        for _ in range(20):
            dst = self.staging / secrets.token_hex(4)
            if not os.path.lexists(dst):
                return dst
        raise AddError("Staging is full of leftovers; restart the Core to clear it.", 503)

    def _strip(self, staged: Path) -> None:
        """Delete what setup makes again (.venv, node_modules, ...) from staging, before the check."""
        try:
            gone = fsutil.strip_regenerated(staged)
        except OSError:
            fsutil.remove_folder(staged, self.staging)
            raise AddError("Its .venv, node_modules, .git or .doctrees folder could not be cleared, so it was not "
                           "added.") from None
        if gone:
            logger.info("staging: deleted %s (setup makes them again)", gone[:5])

    def _clone(self, url: str) -> Path:
        git = shutil.which("git.exe" if os.name == "nt" else "git")     # the program, never a git.cmd wrapper
        if not git:
            raise AddError("git is not installed on this computer, so a git URL can't be added.")
        dst = self._new_staging()
        try:
            got = subprocess.run([git, "-c", "core.longpaths=true", "clone", "--depth", "1", "--", url, str(dst)],
                                 capture_output=True,
                                 text=True, timeout=600, check=False, stdin=subprocess.DEVNULL, **procflags.hidden())
        except subprocess.TimeoutExpired:
            fsutil.remove_folder(dst, self.staging)
            raise AddError("The git clone ran out of time.") from None
        if got.returncode != 0:
            fsutil.remove_folder(dst, self.staging)
            last = (got.stderr or "").strip().splitlines()[-1:] or ["no message"]
            raise AddError(f"git could not fetch {url}: {last[0]}")
        self._strip(dst)
        return dst

    # -- the store (D89) ---------------------------------------------------------------
    def _store_row(self, id: str, key: str | None = None, official_only: bool = False) -> tuple[dict, dict, dict]:
        """``(row, index, record)`` for a store package: the row (from the official store first, then her
        sources; or from the one source *key* when it is an update), its index, and what the Registry keeps."""
        try:
            if official_only:
                row, ix = self.store.resolve_official(id)
            else:
                row, ix = self.store.resolve(id) if key is None else self.store.resolve_in(key, id)
        except store_index.StoreError as exc:
            raise AddError(exc.problems, exc.status) from None
        official = None if ix["official"] else self.store.index(self.store.sources()[0])
        why = self.store.yanked_why(ix, row["id"], row["commit"], official)
        if why is not None:
            raise AddError(f"The store pulled {row.get('name') or id} at commit {row['commit'][:12]}, so it is not "
                           f"added: {why}", 409)
        record = {"source": ix["key"], "repo": row["repo"], "path": row["path"], "commit": row["commit"]}
        return row, ix, record

    def _add_store(self, id: str, official_only: bool = False) -> dict:
        row, ix, record = self._store_row(id, official_only=official_only)
        folder = self._fetch_pinned(row)
        try:
            return self._stage(folder, True, STORE_PREFIX + id, False, False,
                               store={"row": row, "record": record,
                                      "origin": store_index.recorded_tier(ix["official"], row),
                                      "where": "the official PuffPAI store" if ix["official"] else ix["source"]})
        except BaseException:
            fsutil.remove_folder(folder, self.staging)
            raise

    def _git(self, git: str, where: Path, *args: str, timeout: int = 600) -> subprocess.CompletedProcess:
        """One git command in *where*, as ``_clone`` runs git: the program itself, hidden, no input, a time
        limit, long paths; never a prompt for a name or password (a private or missing repo just fails)."""
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="never")
        return subprocess.run([git, "-c", "core.longpaths=true", "-C", str(where), *args], capture_output=True,
                              text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL, env=env,
                              **procflags.hidden())

    def _fetch_pinned(self, row: dict) -> Path:
        """Exactly the row's commit, into staging, and only its folder: the staged package folder."""
        git = shutil.which("git.exe" if os.name == "nt" else "git")     # the program, never a git.cmd wrapper
        if not git:
            raise AddError("git is not installed on this computer, so a store package can't be added.")
        commit, url = row["commit"], row["repo"]
        if not store_index.SHA.fullmatch(commit) or not store_index.REPO.fullmatch(url) \
                or store_index.path_problem(row.get("path", "")):
            raise AddError("That store row is not one the Core can use.")   # checked when read; checked again here
        repo = store_index.REPO_MIRRORS.get(url, url)
        clone = self._new_staging()
        clone.mkdir()
        dst: Path | None = None

        def run(*args: str) -> subprocess.CompletedProcess:
            try:
                return self._git(git, clone, *args)
            except subprocess.TimeoutExpired:
                raise AddError(f"git ran out of time fetching {url}.") from None
        try:
            if run("init", "-q").returncode != 0:
                raise AddError("git could not make a folder to fetch into.")
            got = run("fetch", "-q", "--depth", "1", "--", repo, commit)
            if got.returncode != 0:
                # A server that won't send one commit by its id: fetch its branches and look for it there.
                got = run("fetch", "-q", "--", repo, "+refs/heads/*:refs/remotes/store/*")
                if got.returncode != 0:
                    last = (got.stderr or "").strip().splitlines()[-1:] or ["no message"]
                    raise AddError(f"git could not fetch {url}: {last[0][:300]}")
            got = run("-c", "advice.detachedHead=false", "checkout", "-q", "--detach", commit)
            if got.returncode != 0:
                raise AddError(f"{url} has no commit {commit[:12]} (the one the store pins), so it was not added.")
            head = run("rev-parse", "HEAD")
            if head.returncode != 0 or head.stdout.strip() != commit:
                raise AddError(f"git checked out a different commit than the store pins ({commit[:12]}), so it was "
                               "not added.")
            pkg = store_subfolder(clone, row.get("path", ""))
            if pkg == clone:
                dst = clone
            else:
                dst = self._new_staging()
                os.replace(pkg, dst)                  # only the row's folder leaves the clone
                fsutil.remove_folder(clone, self.staging)
        except BaseException:
            fsutil.remove_folder(clone, self.staging)
            if dst is not None and dst != clone:
                fsutil.remove_folder(dst, self.staging)
            raise
        self._strip(dst)
        return dst

    def _load(self, folder: Path, adopted: bool = False) -> tuple[dict, str]:
        """The manifest and the sha256 of its file; for an adopted app only the ``.buildbot/puffpai.json``
        the Core wrote (never a top-level one the downloaded project shipped, ``manifest.find``)."""
        try:
            data, _path, sha = manifest_mod.load_with_sha(folder, adopted=adopted)
        except manifest_mod.ManifestError as exc:
            raise AddError(exc.problems) from None
        missing = manifest_mod.check_files(folder, data)
        if missing:
            raise AddError(missing)
        return data, sha

    @staticmethod
    def _sha_in_place(folder: Path, origin: str, want: str) -> str:
        """The manifest sha in *folder* (its final place), which must be the one her card showed."""
        try:
            _data, _path, sha = manifest_mod.load_with_sha(folder, adopted=manifest_mod.adopted_origin(origin))
        except manifest_mod.ManifestError:
            sha = ""
        if not want or sha != want:
            raise AddError("Its settings file changed after its check, so it was not installed. Add it again.", 409)
        return sha

    def _stage(self, folder: Path, staged: bool, source: str, link: bool, git: bool,
               update_of: str | None = None, adopted: str | None = None, plan: dict | None = None,
               store: dict | None = None) -> dict:
        was_adopted = (adopted is not None and adopted != MADE) or (update_of is not None and manifest_mod.adopted_origin(
            (self.registry.get(update_of) or {}).get("origin")))
        data, manifest_sha = self._load(folder, adopted=was_adopted)
        said = MADE if adopted == MADE else f"adopted:{adopted}"
        if adopted is not None and data.get("origin") != said:
            raise AddError(["Its puffpai.json does not say it was adopted from " + str(adopted) + "."])
        if store is not None:
            store_manifest_check(folder, data, store["row"])
        id = data["id"]
        if update_of is None and self.registry.get(id) is not None:
            raise AddError(already_installed(data["name"], id, source), 409)
        if update_of is not None and id != update_of:
            raise AddError(f"The new version has a different id ({id}, not {update_of}).")
        origin = said if adopted is not None else (
            store["origin"] if store is not None else self._origin_for(source, link, git, update_of))
        needs = data.get("needs") or {}
        if data["kind"] == "provider" and needs.get("admin") and origin not in ("core", "store:verified"):
            raise AddError("Only Verified providers may need admin rights, so this one is not added.")
        tree = fsutil.tree_hash(folder)
        verdict, problems = _verdict(folder, data, origin, self.drawbridge)
        if verdict is None:
            raise AddError(problems, 403)
        card = self._card(data, origin, folder if link else None, tree, update_of)
        if store is not None:
            card["store"] = store_card(store)
        look_colours(card, data)
        card["drawbridge"] = shown_verdict(verdict)
        floor = data["kind"] == "provider" and (data.get("reads") == "raw" or bool(needs.get("admin")))
        plan_rec, outside = (None, [])
        # The plan's Yes is checked only for an app Toolsmith adopted (its address from the Core's own record).
        if plan is not None and update_of is None and not link and adopted is not None:
            plan_rec, outside = plan_cover.check(self.consent, plan, data, card["drawbridge"],
                                                 tools=verdict.get("tools") if isinstance(verdict, dict) else None,
                                                 folder=folder, checked=verdict)
            if plan_rec is not None:
                card["plan"] = {"consent_id": plan_rec["consent_id"], "build": plan_rec["covers"].get("build")}
                if outside:
                    card["outside_plan"] = outside
                    card["title"] = f"Add {data['name']}? Part of it was not on your plan"
        if adopted == MADE:                  # D121: said on its own card, whatever else the card says
            card["built_by"] = "Toolsmith"
            card["title"] = f"Add {data['name']}? Built by Toolsmith"
            card["outside_plan"] = ["Built by Toolsmith: it wrote this app itself for this job."] + list(card.get("outside_plan") or [])
        if verdict.get("verdict") == "hold" and verdict.get("tunnels"):      # D117: the title leads with it
            card["title"] = f"Add {data['name']}? {TUNNEL_SHORT}"
        with self._raising:
            cid = self.consent.request({"kind": "package", "target": id}, card, floor)
            self._pending[cid] = Pending(cid, id, folder, link, source, origin, data, tree, card, update_of,
                                         verdict=verdict, manifest_sha=manifest_sha,
                                         extra={"store": dict(store["record"])} if store is not None else {})
            out = {"consent_id": cid, "id": id, "card": card, "card_sha": card_sha(card), "floor": floor}
            if plan_rec is not None and not outside and not floor:
                # Inside the plan she said Yes to: recorded against that Yes; the install starts now.
                try:
                    self.consent.answer(cid, "yes", "tap", by=f"plan:{plan_rec['consent_id']}",
                                        card_sha=out["card_sha"])
                    out["covered_by"] = plan_rec["consent_id"]
                except ConsentError as exc:
                    # She answered this card herself in the moment it was raised: her answer stands (a Yes
                    # installs it, a No ends it, through _answered). Any other refusal is raised as before.
                    if exc.code != "answered":
                        raise
        return out

    def _origin_for(self, source: str, link: bool, git: bool, update_of: str | None) -> str:
        if update_of is not None:
            return (self.registry.get(update_of) or {}).get("origin") or _origin(source, link, git)
        return _origin(source, link, git)

    @staticmethod
    def _card(data: dict, origin: str, linked: Path | None, tree: str, update_of: str | None,
              changed: dict | None = None) -> dict:
        needs = data.get("needs") or {}
        abilities = data.get("abilities") or {}
        card = {
            "title": f"Update {data['name']}?" if update_of else f"Add {data['name']}?",
            "id": data["id"], "name": data["name"], "kind": data["kind"], "version": data.get("version", ""),
            "description": data.get("description", ""), "consent": data.get("consent", ""), "origin": origin,
            "hosts": list(needs.get("hosts", [])), "install_hosts": list(needs.get("install_hosts", [])),
            "keys": [k.get("name") for k in needs.get("keys", [])],
            "changes": list(abilities.get("changes", [])),
            "your_turn": [s.get("title", s.get("id")) for s in data.get("setup_steps", [])],
            "hardware": dict(needs.get("hardware", {})), "owner_files": needs.get("owner_files"),
            "linked_folder": str(linked) if linked else None, "tree_sha": tree,
        }
        if needs.get("addresses"):           # D110: a line on the card; it grants nothing (only hosts become covers)
            card["addresses"] = needs["addresses"]
        if isinstance(needs.get("brain"), dict) and needs["brain"].get("as_written") is True:
            card["brain_as_written"] = True  # WritersBot: said on the card, decided by the owner's Yes
        if data["kind"] == "provider":
            card["provides"] = [p["slot"] for p in data.get("provides", [])]
            card["reads"] = data.get("reads")
        if changed is not None:
            card["what_changed"] = changed
        return card

    # -- the answer -----------------------------------------------------------
    def _answered(self, record: dict) -> None:
        cid = record["consent_id"]
        with self._raising:                      # a card raised this moment is in _pending first
            pending = self._pending.get(cid)
        if pending is None:
            return
        if record["status"] != "granted":
            self._pending.pop(cid, None)
            if pending.folder.parent == self.staging:
                fsutil.remove_folder(pending.folder, self.staging)
            self._finish(cid, {"status": "declined", "id": pending.id})
            return
        threading.Thread(target=self._install_quietly, args=(cid,), name=f"install-{pending.id}", daemon=True).start()

    def _install_quietly(self, cid: str) -> None:
        pending = self._pending.get(cid)
        try:
            row = self.install(cid)
            self._finish(cid, {"status": "installed", "id": row["id"], "row": row})
        except (AddError, StewardError, RegistryError) as exc:
            problems = exc.problems if isinstance(exc, AddError) else [str(exc)]
            self._finish(cid, {"status": "failed", "problems": problems})
        except Exception as exc:  # never a silent install thread
            logger.exception("install %s failed", cid)
            self._finish(cid, {"status": "failed", "problems": [f"The install stopped with an error ({type(exc).__name__})."]})
        finally:
            if pending is not None:
                self._end_install(pending.id)

    def _end_install(self, id: str) -> None:
        """Install grants close when the install ends, however it ended (Z3 section 2, 02 field reference)."""
        if self.on_install_end is None:
            return
        try:
            self.on_install_end(id)
        except Exception:  # noqa: BLE001 - logged; the Core's next start closes every install grant left
            logger.exception("install grants of %s did not close", id)

    def _finish(self, cid: str, result: dict) -> None:
        with self._cond:
            self._results[cid] = result
            self._cond.notify_all()

    def wait(self, cid: str, timeout_s: float | None = None) -> dict:
        """The result of the card's install: ``status`` installed | declined | failed | pending."""
        with self._cond:
            self._cond.wait_for(lambda: cid in self._results, timeout=timeout_s)
            return dict(self._results.get(cid, {"status": "pending"}))

    # -- install --------------------------------------------------------------
    def install(self, cid: str) -> dict:
        """After the owner's Yes: re-check, install in place, set up, start."""
        p = self._pending.pop(cid, None)
        if p is None:
            raise AddError("There is nothing waiting to install for that card.", 404)
        record = self.consent.get(cid)
        if record is None or record["status"] != "granted" or record["card_sha"] != card_sha(p.card):
            raise AddError("That Yes is not for what is staged, so nothing was installed.", 403)
        try:
            if (fsutil.tree_hash(p.folder) != p.tree_sha
                    or not seams.drawbridge_bound(p.folder, p.verdict, self.drawbridge)):
                raise AddError("The package changed after its check, so it was not installed. Add it again.", 409)
            verdict, problems = _verdict(p.folder, p.manifest, p.origin, self.drawbridge, before=p.verdict)
            if verdict is None:
                raise AddError(problems, 403)
        except AddError:
            if p.folder.parent == self.staging:
                fsutil.remove_folder(p.folder, self.staging)
            raise
        if p.update_of:
            return self._swap(p)
        final = p.folder if p.link else self.packages / p.id
        if not p.link:
            if final.exists():
                fsutil.remove_folder(p.folder, self.staging)
                raise AddError(f"packages\\{p.id} is already there from an earlier install. Remove it first.", 409)
            os.replace(p.folder, final)          # the staged folder IS packages\<id>, before setup
        try:
            # Her Yes is for this manifest file: its sha goes in the row, and Steward runs only that file.
            manifest_sha = self._sha_in_place(final, p.origin, p.manifest_sha)
        except AddError:
            if not p.link:
                fsutil.remove_folder(final, self.packages)
            raise
        try:
            self.installs.set(p.id, final, p.link, p.source)
        except OSError as exc:                   # never a folder in packages with no record (lane I18c)
            if not p.link:
                fsutil.remove_folder(final, self.packages)
            raise AddError(f"{p.manifest['name']} was not installed: its record could not be written "
                           f"({_why(exc)}). Add it again.", 409) from None
        data_dir = self.state / "data" / p.id
        data_dir.mkdir(parents=True, exist_ok=True)
        self._forget_addresses(p.id)
        row = row_from_manifest(p.manifest, p.origin, status="installing", data_dir=str(data_dir), yes_record=cid,
                                manifest_sha=manifest_sha,
                                **({"store": dict(p.extra["store"])} if p.extra.get("store") else {}))
        if p.manifest["kind"] == "provider":
            row["floor"] = {"suite": "fail", "checked_at": None}
        self.registry.add(row)
        self.set_scope(p.id, p.manifest, p.origin)
        self._setup_and_start(p.id, expect_tree=p.tree_sha)
        if p.manifest["kind"] == "provider":
            for slot in p.manifest.get("provides", []):
                self.bus.publish(Bus.message("provider.installed", {
                    "slot": slot["slot"], "id": p.id, "version": p.manifest.get("version", "0"),
                    "origin": p.origin.split(":", 1)[0], "floor_suite": "fail"}, CORE))
        return self.registry.get(p.id)

    # -- Drawbridge's everyday scope (09 section 3) -------------------------------
    def set_scope(self, id: str, manifest: dict, origin: str) -> None:
        """What her install Yes approved for everyday use: the changing tools its card listed under
        "changes" pass Drawbridge with no card; a tool Drawbridge counts as changing that the card did
        not list (X27) asks. Called at install and again when the tools are read."""
        db = self.drawbridge
        if db is None or not callable(getattr(db, "set_scope", None)):
            return
        from ..zone3.drawbridge import Scope, classify_tools
        row = self.registry.get(id) or {}
        tools = [{"name": a["tool"].split(".", 1)[1]} for a in row.get("abilities") or []
                 if isinstance(a, dict) and "." in str(a.get("tool", ""))]
        kinds = classify_tools(manifest, tools, origin)
        listed = set((manifest.get("abilities") or {}).get("changes") or [])
        approved = {t for t, k in kinds.items() if k == "changing" and t in listed}
        needs = manifest.get("needs") or {}
        # An address she allowed, or may yet allow, is a place it can reach: a private detail handed to it asks first.
        hosts = bool(needs.get("hosts") or needs.get("addresses")) or self._has_address(id)
        db.set_scope(id, Scope(tools=kinds, approved=approved, has_hosts=hosts))

    def _has_address(self, id: str) -> bool:
        from ..zone3 import addresses
        try:
            return addresses.has_live(self.consent, id)
        except Exception:  # noqa: BLE001 - can't tell: the stricter answer
            logger.exception("could not read the addresses of %s", id)
            return True

    def _forget_addresses(self, id: str) -> None:
        """Every address Yes of this package goes, and its grants (D110): at remove, and again before a fresh install,
        so a reinstall always asks again even if a remove stopped half way. A revoked Yes stops the Bridge on its own, so
        this works with no Bridge wired too."""
        from ..zone3 import addresses
        try:
            addresses.take_back_package(self.consent, id, self.drop_address_grant)
        except Exception:  # noqa: BLE001 - logged; the next install of this id tries again
            logger.exception("the addresses of %s were not all taken back", id)

    def _setup_and_start(self, id: str, start: bool | None = None, expect_tree: str | None = None) -> None:
        try:
            self.steward.setup(id, expect_tree=expect_tree)
        except StewardError as exc:
            self._broken(id, str(exc))
            raise
        block = run_block(self.steward.manifest(id))
        if (self.start_after_install if start is None else start) and block is not None:
            # Straight from "installing" to the start's own status: no brief "stopped" in between
            # (the CLI and the Desktop read the first status that is not "installing" as the result).
            try:
                self.steward.start(id)
            except StewardError as exc:
                self._broken(id, str(exc))
                raise
            if (self.registry.get(id) or {}).get("status") == "installing":
                self.registry.update(id, status="stopped")
        else:
            self.registry.update(id, status="stopped")

    def _broken(self, id: str, reason: str) -> None:
        if self.caretaker is not None:
            self.caretaker.mark_broken(id, reason)
        elif self.registry.get(id) is not None:
            self.registry.update(id, status="broken")

    # -- remove ---------------------------------------------------------------
    def remove(self, id: str, keep_data: str | None = None) -> dict:
        """Stop it, take its row out, keep or delete its data. ``ask`` returns the question."""
        row = self.registry.get(id)
        if row is None:
            raise AddError(f"Nothing installed with id {id!r}.", 404)
        entry = self.installs.get(id) or {}
        try:
            data = self.steward.manifest(id)
        except Exception:
            data = {}
        choice = keep_data or (data.get("data") or {}).get("keep_on_remove", "ask")
        if choice not in ("ask", "keep", "delete"):
            raise AddError('keep_data is "keep" or "delete".')
        data_dir = self.state / "data" / id
        has_data = data_dir.is_dir() and any(data_dir.iterdir())
        if choice == "ask" and has_data:
            return {"removed": False, "question": "keep_data",
                    "text": f"Keep {row['name']}'s data on this computer? Answer keep or delete."}
        clean = self.steward.stop(id, status=None)
        self._forget_addresses(id)
        self.registry.remove(id)
        self.installs.remove(id)
        folder_gone = True
        if entry and not entry.get("link"):
            folder_gone = fsutil.remove_folder(entry["folder"], self.packages)
        data_kept = has_data and choice != "delete"
        if choice == "delete" or not has_data:
            fsutil.remove_folder(data_dir, self.state / "data")
        if row["kind"] == "provider":
            for slot in data.get("provides", []):
                self.bus.publish(Bus.message("provider.changed", {"slot": slot["slot"], "from": id, "to": None,
                                                                  "migration": "none"}, CORE))
        return {"removed": True, "id": id, "nothing_running": clean, "folder_removed": folder_gone,
                "linked_folder_kept": bool(entry.get("link")), "data_kept": data_kept}

    # -- update ---------------------------------------------------------------
    def update(self, id: str, title: str | None = None) -> dict:
        """Fetch again, re-check, show what changed, raise one Yes card. *title* replaces the card's title
        (a built-in app's "<name> has an update"). An update card already waiting for *id* is answered
        again rather than raising a second one."""
        row = self.registry.get(id)
        entry = self.installs.get(id)
        if row is None or entry is None:
            raise AddError(f"Nothing installed with id {id!r}.", 404)
        waiting = self._waiting_update(id)
        if waiting is not None:
            return waiting
        try:
            old = self.steward.manifest(id)
        except StewardError as exc:          # a changed manifest is never the "before" of an update card
            raise AddError(str(exc), 409) from None
        source, link = entry["source"], bool(entry["link"])
        git = row["origin"].startswith("github:")
        origin, store = row["origin"], None
        if str(origin).startswith(STORE_PREFIX) and isinstance(row.get("store"), dict):
            # D89(f): the commit its own source pins now, fetched as an add is; its tier as that row says now.
            have = row["store"]
            srow, ix, record = self._store_row(id, key=str(have.get("source") or ""))
            if srow["commit"] == have.get("commit"):
                raise AddError(f"{row['name']} is already at the commit its store pins.", 409)
            origin = store_index.recorded_tier(ix["official"], srow)
            store = {"row": srow, "record": record, "origin": origin,
                     "where": "the official PuffPAI store" if ix["official"] else ix["source"]}
            folder, staged = self._fetch_pinned(srow), True
        elif link:
            folder, staged = Path(entry["folder"]), False
        else:
            folder, staged = self._fetch(source, False, git)
        try:
            new, new_sha = self._load(folder, adopted=manifest_mod.adopted_origin(row.get("origin")))
            if new["id"] != id:
                raise AddError(f"The new version has a different id ({new['id']}, not {id}).")
            if store is not None:
                store_manifest_check(folder, new, store["row"])
            tree = fsutil.tree_hash(folder)
            verdict, problems = _verdict(folder, new, origin, self.drawbridge)
            if verdict is None:
                raise AddError(problems, 403)
            changed = what_changed(old, new)
            card = self._card(new, origin, folder if link else None, tree, id, changed)
            if store is not None:
                card["store"] = store_card(store, was=row["store"])
            look_colours(card, new)
            card["drawbridge"] = shown_verdict(verdict)
            if title:
                card["title"] = title
            if verdict.get("verdict") == "hold" and verdict.get("tunnels"):  # D117
                card["title"] = f"Update {new['name']}? {TUNNEL_SHORT}"
            needs = new.get("needs") or {}
            floor = new["kind"] == "provider" and (new.get("reads") == "raw" or bool(needs.get("admin")))
            extra = {"was_running": row["status"] == "running"}
            if store is not None:
                extra["store"] = dict(store["record"])
            with self._raising:
                cid = self.consent.request({"kind": "package", "target": id}, card, floor)
                self._pending[cid] = Pending(cid, id, folder, link, source, origin, new, tree, card,
                                             update_of=id, extra=extra, verdict=verdict, manifest_sha=new_sha)
        except BaseException:
            if staged:
                fsutil.remove_folder(folder, self.staging)
            raise
        return {"consent_id": cid, "id": id, "card": card, "card_sha": card_sha(card), "what_changed": changed,
                "floor": floor}

    def _waiting_update(self, id: str) -> dict | None:
        """The update card still waiting for *id* in this run, as ``update`` returns it, or None."""
        with self._raising:
            for cid, p in self._pending.items():
                record = self.consent.get(cid)
                if p.update_of == id and record is not None and record.get("status") == "pending":
                    return {"consent_id": cid, "id": id, "card": p.card, "card_sha": card_sha(p.card),
                            "what_changed": p.card.get("what_changed") or {}, "floor": bool(record.get("floor")),
                            "waiting": True}
        return None

    _ROW_FIELDS = ("name", "version", "description", "ui", "needs", "abilities", "yes_record", "manifest_sha",
                   "status", "port", "origin", "store")

    def _swap(self, p: Pending, yes_record: str | None = None) -> dict:
        """Swap the checked staged folder in for the installed one. Not linked: atomic, as the module says
        (any failure puts the old folder and its row back, and starts it again if it ran). *yes_record*
        is the Yes the row keeps (a system part's update keeps the old one); else this card's."""
        was_running = p.extra.get("was_running", False)
        if p.link:
            return self._swap_linked(p, was_running)
        # Checked in staging before anything is swapped: a failed check leaves the old version as it was.
        try:
            self._sha_in_place(p.folder, p.origin, p.manifest_sha)
        except AddError:
            fsutil.remove_folder(p.folder, self.staging)
            raise
        before = dict(self.registry.get(p.id) or {})
        name = before.get("name") or p.id
        final = self.packages / p.id
        old = self.staging / f"old-{secrets.token_hex(6)}"
        n = p.manifest
        step = "stop"                                  # how far the swap got: stop, aside, in, done
        try:
            self.steward.stop(p.id, status="installing")
            os.replace(final, old)
            step = "aside"
            os.replace(p.folder, final)
            step = "in"
            # The new sha is recorded only here, after the update's Yes (or a system part's own check).
            manifest_sha = self._sha_in_place(final, p.origin, p.manifest_sha)
            self.registry.update(p.id, name=n["name"], version=n.get("version", ""),
                                 description=n.get("description", ""),
                                 ui={k: n["ui"][k] for k in ("pages", "tray", "widgets", "open") if k in n.get("ui", {})},
                                 needs={k: v for k, v in (n.get("needs") or {}).items()
                                        if k in ("packages", "hosts", "install_hosts", "keys", "hardware", "brain",
                                                 "owner_files", "addresses", "admin")},
                                 abilities=[], yes_record=yes_record or p.consent_id, manifest_sha=manifest_sha,
                                 **({"origin": p.origin, "store": dict(p.extra["store"])} if p.extra.get("store")
                                    else {}))
            self.set_scope(p.id, n, p.origin)
            self._setup_and_start(p.id, start=was_running, expect_tree=p.tree_sha)
            step = "done"
        except Exception as exc:  # noqa: BLE001 - every failure puts the old version back, then is said plainly
            if not isinstance(exc, (AddError, StewardError, RegistryError, OSError)):
                logger.exception("update of %s stopped", p.id)
            self._recover(p, step, final, old, before, was_running)
            if step == "stop" and isinstance(exc, OSError):
                first = f"its folder could not be moved aside ({_why(exc)})"
            elif step == "aside" and isinstance(exc, OSError):
                first = f"the new version could not be moved in ({_why(exc)})"
            elif isinstance(exc, AddError):
                first = exc.problems[0]
            elif isinstance(exc, (StewardError, RegistryError)):
                first = str(exc)
            else:
                first = f"it stopped with an error ({type(exc).__name__})"
            raise AddError([f"The update of {name} did not work, so the old version is back: {first}"], 409) from None
        fsutil.remove_folder(old, self.staging)
        if n["kind"] == "provider":
            for slot in n.get("provides", []):
                self.bus.publish(Bus.message("provider.changed", {"slot": slot["slot"], "from": p.id, "to": p.id,
                                                                  "migration": "none"}, CORE))
        return self.registry.get(p.id)

    def _recover(self, p: Pending, step: str, final: Path, old: Path, before: dict, was_running: bool) -> None:
        """After a failed swap at *step*: the old folder in place, the staged or new copy gone, the row as
        it was (manifest sha, port, status, health), started again if it ran. Never raises."""
        try:
            if step == "stop":                         # nothing moved yet
                fsutil.remove_folder(p.folder, self.staging)
                self._put_back(p.id, before, was_running)
            elif step == "aside":                      # the old folder is aside, the new one not in
                back = _replace_quietly(old, final)
                fsutil.remove_folder(p.folder, self.staging)
                if back:
                    self._put_back(p.id, before, was_running)
                else:
                    self._broken(p.id, "An update could not put the old version back. Remove it and add it again.")
            else:
                self._roll_back(p, final, old, before, was_running)
        except Exception:  # noqa: BLE001 - logged; the row says what is left
            logger.exception("update of %s: putting the old version back", p.id)
            self._broken(p.id, "An update could not put the old version back. Remove it and add it again.")

    def _roll_back(self, p: Pending, final: Path, old: Path, before: dict, was_running: bool) -> None:
        """The new folder out, the old folder back in, its row as it was, started again if it ran."""
        try:
            self.steward.stop(p.id, status=None)
        except Exception:  # noqa: BLE001 - logged; the folder still goes back
            logger.exception("update of %s: stop before roll back", p.id)
        bad = self.staging / f"bad-{secrets.token_hex(6)}"
        back = _replace_quietly(final, bad) and _replace_quietly(old, final)
        fsutil.remove_folder(bad, self.staging)
        if not back:
            # Neither copy could be put in place: nothing is started from a half-swapped folder.
            self._broken(p.id, "An update could not put the old version back. Remove it and add it again.")
            return
        self._put_back(p.id, before, was_running)

    def _put_back(self, id: str, before: dict, was_running: bool) -> None:
        """The row as it was before the update, its scope again, and a start if it ran."""
        if not before or self.registry.get(id) is None:
            return
        try:
            fields = {k: before[k] for k in self._ROW_FIELDS if k in before}
            fields["status"] = "stopped" if before.get("status") in ("running", "installing") else before.get("status")
            self.registry.update(id, **fields)
            h = before.get("health") if isinstance(before.get("health"), dict) else None
            if h is not None:            # the failed update's "not ok" was about the new copy, not this one
                self.registry.set_health(id, bool(h.get("ok")), str(h.get("reason") or ""), h.get("checked_at"))
            old_manifest = self.steward.read_manifest(id)
            if old_manifest is not None:
                self.set_scope(id, old_manifest, before.get("origin") or "local")
            if was_running:
                self.steward.start(id)
        except Exception as exc:  # noqa: BLE001 - the old version is in place; it just did not start
            logger.warning("update of %s: the old version did not start again: %s", id, type(exc).__name__)
            self._broken(id, f"The old version is back but did not start: {exc}")

    def _swap_linked(self, p: Pending, was_running: bool) -> dict:
        """A linked folder is updated in place (it is the owner's own folder): nothing to move."""
        self.steward.stop(p.id, status="installing")
        final = p.folder
        try:
            manifest_sha = self._sha_in_place(final, p.origin, p.manifest_sha)
        except AddError as exc:
            self._broken(p.id, str(exc.problems[0]))
            raise
        n = p.manifest
        self.registry.update(p.id, name=n["name"], version=n.get("version", ""), description=n.get("description", ""),
                             ui={k: n["ui"][k] for k in ("pages", "tray", "widgets", "open") if k in n.get("ui", {})},
                             needs={k: v for k, v in (n.get("needs") or {}).items()
                                    if k in ("packages", "hosts", "install_hosts", "keys", "hardware", "brain",
                                             "owner_files", "addresses", "admin")},
                             abilities=[], yes_record=p.consent_id, manifest_sha=manifest_sha)
        self.set_scope(p.id, n, p.origin)
        self._setup_and_start(p.id, start=was_running, expect_tree=p.tree_sha)
        if n["kind"] == "provider":
            for slot in n.get("provides", []):
                self.bus.publish(Bus.message("provider.changed", {"slot": slot["slot"], "from": p.id, "to": p.id,
                                                                  "migration": "none"}, CORE))
        return self.registry.get(p.id)

    # -- built-ins follow the code (lane I13) ---------------------------------------
    def builtin_of(self, id: str) -> Path | None:
        """The repo's folder an installed built-in was copied from, or None when *id* is anything else
        (adopted, linked, a git clone, an outside folder, or a built-in folder that moved)."""
        return builtin_install(id, self.registry.get(id), self.installs.get(id), self.packages)

    def builtin_state(self, id: str) -> dict | None:
        """How an installed built-in compares with the repo's copy, or None when *id* is not one:
        ``{"id", "system", "current", "have", "repo"}`` (versions), plus ``problem`` when it can't be read."""
        src = self.builtin_of(id)
        if src is None:
            return None
        row = self.registry.get(id) or {}
        out = {"id": id, "name": row.get("name") or id, "system": id in SYSTEM_PARTS,
               "have": str(row.get("version") or ""), "repo": "", "current": False}
        try:
            repo_manifest = manifest_mod.load_with_sha(src)[0]
            out["repo"] = str(repo_manifest.get("version") or "")
            same = (out["repo"] == out["have"]
                    and fsutil.tree_hash(src) == fsutil.tree_hash(self.packages / id))
        except (manifest_mod.ManifestError, OSError) as exc:
            out["problem"] = exc.problems[0] if isinstance(exc, manifest_mod.ManifestError) else _why(exc)
            return out
        out["current"] = same
        return out

    def check_builtins(self, ids: list[str] | None = None, notices: Any = None) -> list[dict]:
        """Each installed built-in (or each of *ids*) against the repo's copy (Core start, ``puffpai update``).
        A system part is updated now; an app gets a notice and a Yes card. One result per built-in:
        ``status`` current | updated | card | not_updated, with ``line`` in plain words."""
        out = []
        for id in (ids if ids is not None else sorted(self.installs.all())):
            if self.builtin_of(id) is None:
                continue                                  # not a built-in: never touched here
            try:
                out.append(self.update_builtin(id, notices=notices))
            except AddError as exc:
                out.append({"id": id, "status": "not_updated", "line": " ".join(exc.problems)})
            except Exception as exc:  # noqa: BLE001 - one built-in failing never stops the Core start
                logger.exception("built-in update of %s", id)
                out.append({"id": id, "status": "not_updated",
                            "line": f"The update check stopped with an error ({type(exc).__name__})."})
        return out

    def update_builtin(self, id: str, notices: Any = None) -> dict:
        """One installed built-in brought up to the repo's copy: a system part now (no card, an audit line),
        an app through her Yes. Raises AddError when *id* is not an installed built-in."""
        state = self.builtin_state(id)
        if state is None:
            raise AddError(f"{id} is not one of the packages that ship with BuildBot.", 404)
        name = state["name"]
        if state.get("problem"):
            raise AddError(f"{name} was not updated: {state['problem']}", 409)
        if state["current"]:
            return {"id": id, "status": "current", "version": state["have"], "line": f"{name} is up to date."}
        if self.caretaker is not None and self.caretaker.in_safe_mode:
            raise AddError(f"{name} has an update, but BuildBot is in safe mode, so it waits until a restart.", 503)
        if state["system"]:
            return self._update_system_part(id, state, notices)
        return self._offer_update(id, state, notices)

    def _offer_update(self, id: str, state: dict, notices: Any, why: str = "") -> dict:
        """An app's update (or a system part's that asks for more): the Desktop notice and one Yes card."""
        title = f"{state['name']} has an update"
        staged = self.update(id, title=title)
        text = (f"Version {state['have'] or '?'} is installed; this copy of BuildBot has {state['repo'] or '?'}. "
                "Say Yes on its card to update it. Its data stays." + (f" {why}" if why else ""))
        _notice_once(notices, title, text)
        return {**staged, "status": "card", "line": title + ". Say Yes on its card to update it."}

    def _update_system_part(self, id: str, state: dict, notices: Any) -> dict:
        """A system part, with no card: staged from the repo, checked, swapped in, audited. Only when
        nothing in it needs her Yes; else the same card as an app."""
        row = self.registry.get(id) or {}
        name = state["name"]
        try:
            old = self.steward.manifest(id)
        except StewardError as exc:
            # Changed since her Yes: left as it is (broken, with its reason), never overwritten unseen.
            raise AddError(f"{name} was not updated: {exc}", 409) from None
        installed_verdict = self._quiet_verdict(self.packages / id, old, row.get("origin") or "local")
        folder, _staged = self._fetch(str(self.builtin_of(id)), False, False)
        try:
            new, new_sha = self._load(folder)
            if new["id"] != id or new["kind"] == "provider":
                raise AddError(f"{name} was not updated: the new copy is not the same kind of part.", 409)
            tree = fsutil.tree_hash(folder)
            verdict, problems = _verdict(folder, new, row["origin"], self.drawbridge)
            if verdict is None:
                raise AddError([f"{name} was not updated."] + problems, 403)
            wider = [k for k in manifest_difference(old, new) if k not in _SILENT_FOR.get(id, frozenset())]
            new_reasons = set(map(str, verdict.get("reasons") or [])) - set(map(str, installed_verdict))
            if wider or new_reasons or verdict.get("verdict") not in ("allow", "warn"):
                fsutil.remove_folder(folder, self.staging)
                folder = None
                why = "Its settings change more than its name and words (" + ", ".join(wider or ["its check"]) + "), so it needs your Yes."
                return self._offer_update(id, state, notices, why)
            if not self._audit({"actor": "core", "action": "builtin_update", "package": id,
                                "from": state["have"], "to": state["repo"], "tree_sha": tree, "how": "system_part"}):
                raise AddError(f"{name} was not updated: BuildBot could not write the audit line.", 503)
            p = Pending("", id, folder, False, str(self.builtin_of(id)), row["origin"], new, tree, {},
                        # At Core start nothing is alive yet: the row's "running" (start_saved's list) counts.
                        update_of=id, extra={"was_running": self.steward.alive(id) or row.get("status") == "running"},
                        verdict=verdict,
                        manifest_sha=new_sha)
            folder = None                                  # _swap owns it now (it removes it on any failure)
            try:
                got = self._swap(p, yes_record=row.get("yes_record"))
            except AddError as exc:
                self._audit({"actor": "core", "action": "builtin_update", "package": id, "result": "rolled_back",
                             "from": state["have"], "to": state["repo"]})
                raise AddError([f"{name} was not updated."] + exc.problems, exc.status) from None
        finally:
            if folder is not None:
                fsutil.remove_folder(folder, self.staging)
        line = f"{name} was updated to version {state['repo'] or '?'} (part of BuildBot)."
        logger.info(line)
        return {"id": id, "status": "updated", "from": state["have"], "to": state["repo"], "row": got, "line": line}

    def _quiet_verdict(self, folder: Path, manifest: dict, origin: str) -> list[str]:
        """Drawbridge's reasons for the copy installed now (what a system part's update is compared to)."""
        try:
            v = seams.drawbridge_check(folder, manifest, origin=origin, drawbridge=self.drawbridge)
        except Exception:  # noqa: BLE001 - no reasons known: any reason the new copy has needs her Yes
            return []
        return [str(r) for r in (v.get("reasons") or [])] if isinstance(v, dict) else []

    def _audit(self, line: dict) -> bool:
        """One line in the Core's audit log (Drawbridge's chain). False when there is none or it failed."""
        log = getattr(self.drawbridge, "audit", None)
        if log is None or not callable(getattr(log, "append", None)):
            return False
        try:
            log.append(line)
            return True
        except Exception:  # noqa: BLE001 - the caller decides; a system part is not updated unaudited
            logger.warning("audit line not written: %s", line.get("action"))
            return False


STORE_PREFIX = "store:"


def store_subfolder(clone: Path, path: str) -> Path:
    """The row's folder inside a fresh *clone* (D89(c)): every part a real folder (no link or junction on the
    way), and where it resolves still inside the clone. Empty *path* is the clone itself."""
    if not path:
        return clone
    if store_index.path_problem(path):
        raise AddError("The store row's folder is not a plain folder inside the repo, so it was not added.")
    here = clone
    for part in path.split("/"):
        here = here / part
        if fsutil.is_link(here):
            raise AddError(f"The store row's folder {path!r} goes through a link, so it was not added.")
        if not here.is_dir():
            raise AddError(f"The repo has no folder {path!r} at that commit, so it was not added.")
    try:
        root, got = clone.resolve(strict=True), here.resolve(strict=True)
    except (OSError, RuntimeError):
        raise AddError(f"The store row's folder {path!r} could not be read, so it was not added.") from None
    if got == root or not got.is_relative_to(root):
        raise AddError(f"The store row's folder {path!r} leaves the repo, so it was not added.")
    return here


def store_manifest_check(folder: Path, data: dict, row: dict) -> None:
    """A store package's ``puffpai.json`` is the one at the top of its folder (the file the store checked), it
    is the package the row names (same id and kind), and it sets no ``origin`` (the Core writes that)."""
    if manifest_mod.find(folder) != folder / manifest_mod.MANIFEST:
        raise AddError("A store package's puffpai.json must be at the top of its folder, so it was not added.")
    if data.get("id") != row["id"] or data.get("kind") != row["kind"]:
        raise AddError(f"Its puffpai.json says {data.get('id')!r} ({data.get('kind')}), but the store row is for "
                       f"{row['id']!r} ({row['kind']}), so it was not added.")
    if "origin" in data:
        raise AddError("Its puffpai.json sets its own origin, which only the Core writes, so it was not added.")


def look_colours(card: dict, data: dict) -> None:
    """A look with text that may be hard to read says so on its Yes card (D96). It warns and never refuses: the
    owner chooses. The looks that ship pass (``tests/test_contrast.py``), so only an outside look ever shows this."""
    if data.get("kind") == "look":
        got = contrast.check(data)
        if got["state"] != "ok":
            card["look_colours"] = got["line"]


def store_card(store: dict, was: dict | None = None) -> dict:
    """What the Yes card says about where a store package comes from (covered by the card's fingerprint)."""
    rec = store["record"]
    out = {"where": store["where"], "tier": store["origin"].split(":", 1)[1], "repo": rec["repo"],
           "path": rec["path"], "commit": rec["commit"]}
    if was is not None:
        out["was_commit"] = str(was.get("commit") or "")
    return out


def manifest_difference(old: dict, new: dict) -> list[str]:
    """The top-level manifest fields that differ between *old* and *new*, other than :data:`_SILENT_FIELDS`
    (sorted). Empty means only words or the picture changed. Compared as parsed JSON, so formatting and
    key order never count, and a field added or removed counts as a difference."""
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    keys = (set(old) | set(new)) - _SILENT_FIELDS
    missing = object()
    return sorted(k for k in keys if old.get(k, missing) != new.get(k, missing))


def builtin_install(id: str, row: dict | None, entry: dict | None, packages: Path) -> Path | None:
    """The repo's built-in folder that installed package *id* is a copy of, or None. Only an install that
    is not linked, in ``packages\\<id>`` (a real folder), whose Installs source and ``local:`` origin are
    both exactly the repo's folder for *id*. An adopted app never is (its origin is ``adopted:``)."""
    if row is None or not entry or entry.get("link"):
        return None
    origin = str(row.get("origin") or "")
    src = builtin_source(id)
    if src is None or not origin.startswith("local:") or manifest_mod.adopted_origin(origin):
        return None
    if not is_builtin_path(id, origin[len("local:"):]) or not is_builtin_path(id, entry.get("source")):
        return None                               # D108: as written, so a swapped source folder is not the repo's
    try:
        repo = Path(src).resolve()
        same = Path(str(entry.get("folder") or "")).resolve() == (Path(packages) / id).resolve()
    except (OSError, ValueError, RuntimeError):
        return None
    return repo if same and not fsutil.is_link(Path(packages) / id) else None


def _why(exc: OSError) -> str:
    return str(exc.strerror or exc)


def _replace_quietly(src: Path, dst: Path) -> bool:
    """``os.replace`` a folder, a few tries (a process just stopped can hold a file a moment). True if done."""
    import time
    for attempt in range(3):
        try:
            os.replace(src, dst)
            return True
        except OSError:
            if attempt < 2:
                time.sleep(0.5)
    return False


def _notice_once(notices: Any, title: str, text: str) -> None:
    """A Desktop notice from the Core, unless the same one is still showing (no pile at every start)."""
    if notices is None:
        return
    try:
        if any(n.get("package") == "core" and n.get("title") == title for n in notices.list()):
            return
        notices.post("core", {"title": title, "text": text[:600], "urgency": "normal"})
    except Exception as exc:  # noqa: BLE001 - the card is still waiting on its own
        logger.warning("update notice not posted: %s", type(exc).__name__)


def _expire(consent: ConsentStore, cid: str, line: str) -> None:
    """Expire one waiting card: the store's ``expire`` when it has one, else a No (a No only narrows)."""
    expire = getattr(consent, "expire", None)
    if callable(expire):
        expire(cid, line)
    else:
        record = consent.get(cid) or {}
        consent.answer(cid, "no", "tap", by=line, card_sha=record.get("card_sha"))


def field_words(key: str) -> str:
    return FIELD_WORDS.get(key) or "its " + str(key).replace("_", " ")


def what_changed(old: dict, new: dict) -> dict:
    """What an update changes, for its Yes card: version, hosts, keys, changing tools, and ``fields``: every
    top-level manifest field that differs, in plain words (lane I14), so a card never says nothing changed.
    The same manifest: ``fields`` says only its files changed."""
    def names(m: dict, path: tuple) -> set:
        node: Any = m
        for key in path:
            node = (node or {}).get(key, {}) if isinstance(node, dict) else {}
        items = node if isinstance(node, list) else []
        return {i.get("name") if isinstance(i, dict) else i for i in items}
    out: dict[str, Any] = {"version": [old.get("version", ""), new.get("version", "")]}
    for label, path in (("hosts", ("needs", "hosts")), ("install_hosts", ("needs", "install_hosts")),
                        ("keys", ("needs", "keys")), ("changes", ("abilities", "changes"))):
        a, b = names(old, path), names(new, path)
        out[label] = {"added": sorted(b - a), "removed": sorted(a - b)}
    old = old if isinstance(old, dict) else {}
    new = new if isinstance(new, dict) else {}
    missing = object()
    keys = sorted(k for k in set(old) | set(new) if old.get(k, missing) != new.get(k, missing))
    out["fields"] = [field_words(k) for k in keys] or ["only its files (the manifest is the same)"]
    return out


__all__ = ["STORE_PREFIX", "SYSTEM_PARTS", "AddError", "PackageManager", "Pending", "builtin_install", "link_problem", "field_words", "manifest_difference", "what_changed"]
