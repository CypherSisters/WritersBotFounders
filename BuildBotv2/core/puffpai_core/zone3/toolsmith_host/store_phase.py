r"""The PuffPAI store apps ticked on a plan card, added before the build starts (lane I16).

One Core path for every way a plan gets its Yes (the Desktop, the chat, the wizard), so none of them can
race the others and nothing is lost when a page reloads:

1. The plan card is granted (the consent store's Yes listener) or ``start`` is asked for. If the plan has
   ticked store apps that are not installed, the build gets a ``store_adds`` record and one thread for it.
2. For each app, in order: its Package Manager Yes card is raised (the same add, card and answer as
   ``puffpai add``); the thread waits until the card is answered, then, after a Yes, until the install
   reaches a final status (not ``installing``; ``broken`` counts as failed).
3. Only then does the build start. ``start`` asked for while this runs starts nothing (the thread starts
   the build when it is done), so starting twice stays safe.
4. A Core that stops mid-wait goes on at its next start (``resume``, from ``start_core``): every card and
   install is checked again, then the build starts once, with no ``start`` from any page or chat.

A row of the official store's index (D96) is added as ``store:<id>``; its Registry key is the bare id. Only that
store can make the suggestion true, so before its card the row is read again (gone, pulled, no longer Verified, or
no longer an app or tool means it is not added), and the add is pinned to that store: when it can't be read, the add
says so and nothing is added, even when another source lists the same id.

Every "not added" line is true: a No, a card that got no answer in time, a failed install, or a broken app.
A model she ticked on the plan card (lane I17, a coding or picture model) goes the same way, after the store
apps: its own download Yes card (the model, its size, the host Ollama downloads from), the download to its
end, then its role is set, and only then the build starts. Ollama downloads it itself, around the Bridge
(``zone2.roles``), so no Bridge grant is made for it.

The record lives in the build's own ``build.json``, so the Desktop, the chat and the wizard all read the
same progress from ``GET /v1/toolsmith/builds/<id>``.
"""

from __future__ import annotations

import logging
import secrets
import threading
import time
from typing import Any

from ...packages.store_index import SUGGESTED
from ..toolsmith.errors import NotBuilt
from .store import bare_id

logger = logging.getLogger(__name__)

LATER = "You can add it later from the PuffPAI store on your Desktop."
ASKING = "{name}: its own Yes card is waiting. Your Yes adds it to your bot before the build."
INSTALLING = "Adding {name} from the PuffPAI store."
ADDED = "Added {name} from the PuffPAI store."
SAID_NO = "{name} was not added, so its job has nothing to use. " + LATER
NO_ANSWER = "{name} was not added: its Yes card got no answer in time. " + LATER
FAILED = "{name} was not added: {why} " + LATER
NOT_LAUNCHED = "Toolsmith could not start the build: {why}."
BROKEN = "{name} was not added: it was installed but does not work ({why}). " + LATER
NO_PACKAGES = "{name} was not added: adding apps is not part of this Core. " + LATER
NOT_LISTED = "{name} was not added: the PuffPAI store no longer lists it."
PULLED = "{name} was not added: the PuffPAI store pulled it. {why}"
NOT_A_TOOL = ("{name} was not added: the PuffPAI store no longer lists it as an app or a tool, so it was not what the "
              "plan said. You can look at it in the PuffPAI store on your Desktop.")
NOT_VERIFIED = ("{name} was not added: the PuffPAI store no longer marks it Verified, so it was not what the plan "
                "said. You can look at it in the PuffPAI store on your Desktop.")
STARTED = "Toolsmith started the build."
MODEL_LATER = "You can add it later on the Developer page."
MODEL_ASKING = "{name}: its own Yes card is waiting. Your Yes downloads it before the build."
MODEL_PULLING = "Downloading {name} ({size} GB). The build starts when it is done."
MODEL_ADDED = "Added {name} as your bot's {words} model."
MODEL_SAID_NO = "{name} was not downloaded, so your bot uses its chat model for this. " + MODEL_LATER
MODEL_NO_ANSWER = "{name} was not downloaded: its Yes card got no answer in time. " + MODEL_LATER
MODEL_FAILED = "{name} was not added: {why} " + MODEL_LATER
MODEL_NO_PORTS = "{name} was not added: adding models is not part of this Core. " + MODEL_LATER
NOT_STARTED = "The build did not start: {why}"

#: How long one card may wait for an answer (the consent store's card life is 10 minutes), and one install.
CARD_WAIT_S = 11 * 60
INSTALL_WAIT_S = 30 * 60
FINAL = ("running", "stopped", "needs_you")
#: The record's states while the phase runs: adding the apps, then starting the build.
RUNNING = ("adding", "starting")


class Stale(Exception):
    """This thread's owner token is no longer the record's: another owner took over, so it does nothing more."""


class StorePhase:
    """``host.store_phase``: the adds before a build, ONE owner per build.

    Every way in (the plan Yes listener, ``start`` from any page or chat, ``resume`` at Core start) goes through
    one claim (:meth:`_claim`), taken under this phase's lock and the build store's lock before any work: the
    record's ``owner`` token names the one thread that may act. A second caller while that thread lives
    returns without acting. A thread whose token is no longer the record's (a Core that restarted and took
    over) stops at once: :meth:`_guard` runs before EVERY side effect (a card, a wait on a card or an install,
    a card expired, a download, a role set, the start) and raises :class:`Stale` when the token changed. The
    download and the role set are also safe to ask twice (``zone2.roles``: one download per model at a time,
    and a role already set is not written again).

    The start: the owner sets ``starting`` under both locks (from then on ``start`` from anywhere does
    nothing), then calls ``start_now`` with no lock held (a runner may wait on its child, and the child calls
    back into the build store). The one ``_begin_build`` that writes the Yes launches the build and only then
    calls :meth:`launched`, which writes ``done``. So the build is launched once, and ``done`` is never seen
    before that launch.
    """

    def __init__(self, host: Any, poll_s: float = 0.5) -> None:
        self.host = host
        self.poll_s = poll_s
        self._lock = threading.RLock()           # always taken before the build store's lock
        self._threads: dict[str, threading.Thread] = {}
        self._mine = threading.local()           # the owner token of the thread running now

    # -- the record --------------------------------------------------------------------------------
    def record(self, build_id: str) -> dict | None:
        b = self.host.store.read(build_id) or {}
        got = b.get("store_adds")
        return got if isinstance(got, dict) else None

    def waiting(self, build_id: str, consent_id: str) -> bool:
        """True while this plan Yes's store apps are still being added (``start`` then starts nothing)."""
        rec = self.record(build_id)
        return bool(rec and rec.get("consent_id") == consent_id and rec.get("state") in RUNNING)

    def _set(self, build_id: str, n: int | None = None, **fields: Any) -> None:
        token = getattr(self._mine, "token", None)

        def fn(b: dict) -> None:
            rec = b.get("store_adds")
            if not isinstance(rec, dict):
                return
            if token is not None and rec.get("owner") != token:
                return                         # no longer the owner: a stale thread writes nothing
            if n is None:
                rec.update(fields)
            else:
                rec["apps"][n].update(fields)
            if fields.get("line"):             # every line said, in order (the wizard prints only new ones)
                rec.setdefault("said", []).append(fields["line"])
        self.host.store.change(build_id, fn)

    def _owns(self, build_id: str, token: str) -> bool:
        return (self.record(build_id) or {}).get("owner") == token

    def _guard(self, build_id: str) -> None:
        """Before every side effect: raise :class:`Stale` unless this thread still owns the build's phase."""
        token = getattr(self._mine, "token", None)
        if token is None or not self._owns(build_id, token):
            raise Stale(build_id)

    # -- the one claim -----------------------------------------------------------------------------
    def _claim(self, build_id: str, consent_id: str, new: dict | None = None) -> bool:
        """Take the build's store phase for one new thread, under this phase's lock and the store's lock. True
        when the phase is running (this call's thread, or one that already lives here); False when there is
        nothing to run (no record for this Yes, done, or the build started). *new*: the record to write when
        there is none for this Yes yet."""
        with self._lock:
            live = self._threads.get(build_id)
            claimed: dict = {}
            token = secrets.token_hex(8)

            def fn(b: dict) -> None:
                if b.get("yes"):
                    return
                rec = b.get("store_adds") if isinstance(b.get("store_adds"), dict) else None
                if rec is None or rec.get("consent_id") != consent_id:
                    if new is None:
                        return
                    rec = b["store_adds"] = dict(new)
                if rec.get("state") not in RUNNING + ("asking",):
                    return
                claimed["running"] = True
                if live is not None and live.is_alive() and rec.get("owner"):
                    return                     # it has its owner here: a second caller does nothing
                rec.update(state="starting" if rec.get("state") == "starting" else "adding", owner=token)
                claimed["token"] = token
            self.host.store.change(build_id, fn)
            if claimed.get("token"):
                th = threading.Thread(target=self._run, args=(build_id, consent_id, claimed["token"]),
                                      name=f"store-adds-{build_id}", daemon=True)
                self._threads[build_id] = th
                th.start()
            return bool(claimed.get("running"))

    # -- begin -------------------------------------------------------------------------------------
    def begin(self, build_id: str, consent_id: str) -> bool:
        """Start adding this plan Yes's ticked store apps, once. True when adding runs now (the build waits
        for it); False when there is nothing to add, or it is done."""
        with self._lock:
            b = self.host.store.read(build_id)
            if b is None or b.get("yes"):
                return False
            rec = b.get("store_adds") if isinstance(b.get("store_adds"), dict) else None
            if rec is not None and rec.get("consent_id") == consent_id:
                return self._claim(build_id, consent_id)     # a Core that restarted goes on where it was
            try:
                adds = self.host.store_adds(build_id)
                ticked = getattr(self.host, "model_adds", lambda _b: [])(build_id)     # lane I17
            except KeyError:
                return False
            if not adds and not ticked:
                return False
            apps = [{"package": a["package"], "name": a.get("name") or a["package"], "job_name": a.get("job_name") or "",
                     "state": "waiting", "card": None, "line": ""} for a in adds]
            apps += [{"kind": "model", "package": "", "role": m["role"], "model": m["model"],
                      "name": m.get("name") or m["model"], "disk_gb": m.get("disk_gb"), "job_name": "",
                      "state": "waiting", "card": None, "line": ""} for m in ticked]
            return self._claim(build_id, consent_id, new={"consent_id": consent_id, "state": "adding", "apps": apps,
                                                          "line": "", "said": []})

    def resume(self) -> list[str]:
        """On Core start: every build whose store adds were still running when the Core stopped (``adding``;
        ``asking`` too) goes on where it was, with no ``start`` from any page or chat: each card and install is
        checked again, then the store phase finishes and the build starts once. The ids resumed."""
        out = []
        for b in self.host.store.all():
            rec = b.get("store_adds")
            if not isinstance(rec, dict) or rec.get("state") not in RUNNING + ("asking",):
                continue
            if b.get("yes"):
                if rec.get("state") != "starting" or not self._starting_after_restart(b):
                    continue
            if self._claim(b["id"], str(rec.get("consent_id") or "")):
                out.append(b["id"])
        return out

    def _starting_after_restart(self, b: dict) -> bool:
        """A Core that stopped between the launch and :meth:`launched` left ``starting`` with a Yes. When the
        build ran (it is running, or a job moved on from ``planned``) the phase is done now; when it never
        started, its Yes is taken off so the phase starts it once. True when it must be started."""
        ran = self.host.running(b["id"]) or any((j.get("run") or {}).get("state") not in (None, "planned")
                                                for j in b.get("jobs") or [])
        if ran:
            self.launched(b["id"])
            return False

        def fn(x: dict) -> None:
            if (x.get("store_adds") or {}).get("state") == "starting" and x.get("yes"):
                x.pop("yes", None)
        self.host.store.change(b["id"], fn)
        return True

    # -- the adds ----------------------------------------------------------------------------------
    def _run(self, build_id: str, consent_id: str, token: str) -> None:
        self._mine.token = token
        try:
            rec = self.record(build_id) or {}
            for n, app in enumerate(rec.get("apps") or []):
                if not self._owns(build_id, token):
                    return                     # another owner took over: it does the rest
                if app.get("state") in ("added", "not_added"):
                    continue
                state, line = self._one(build_id, n, app)
                self._set(build_id, n, state=state, line=line)
        except Stale:
            return                             # another owner took over: it does the rest (no finish here)
        except Exception:  # noqa: BLE001 - never a silent stop: the build still starts, said plainly
            logger.exception("store adds for %s stopped", build_id)
        self._finish(build_id, consent_id, token)

    def _finish(self, build_id: str, consent_id: str, token: str) -> None:
        """The owner only: ``starting`` under both locks, then the start with no lock held. ``done`` is written
        by :meth:`launched` right after the launch, or here when the build could not start."""
        try:
            with self._lock, self.host.store.lock:
                if not self._owns(build_id, token):
                    return
                self._set(build_id, state="starting")
            try:
                self._guard(build_id)
                self.host.start_now(build_id, consent_id)
            except Stale:
                return
            except Exception as exc:  # noqa: BLE001 - safe mode, a replaced plan: the plain line
                self._set(build_id, state="done", line=NOT_STARTED.format(why=str(exc) or type(exc).__name__))
                end = getattr(self.host, "end_keys", None)
                if callable(end):
                    end(build_id)              # it did not start: the keys her Yes lent go back now (D68)
        finally:
            with self._lock:
                if self._threads.get(build_id) is threading.current_thread():
                    self._threads.pop(build_id, None)
            self._mine.token = None

    def launched(self, build_id: str) -> None:
        """The build was just launched (``ToolsmithHost._begin_build``, once per build): a phase that was
        starting it is done now, never before."""
        def fn(b: dict) -> None:
            rec = b.get("store_adds")
            if isinstance(rec, dict) and (rec.get("state") == "starting" or rec.pop("launch_failed", None)):
                rec.update(state="done", line=STARTED)
                rec.setdefault("said", []).append(STARTED)
        self.host.store.change(build_id, fn)

    def not_launched(self, build_id: str, why: str) -> None:
        """The launch failed (``ToolsmithHost._begin_build``): a plain failure line, never "started". The
        build's Yes was taken off, so asking to start it again starts it."""
        line = NOT_LAUNCHED.format(why=why)

        def fn(b: dict) -> None:
            rec = b.get("store_adds")
            if isinstance(rec, dict) and rec.get("state") == "starting":
                rec.update(state="done", line=line, launch_failed=True)   # a later start says started
                rec.setdefault("said", []).append(line)
        self.host.store.change(build_id, fn)

    def _model(self, build_id: str, n: int, app: dict) -> tuple[str, str]:
        """``(state, line)`` for one ticked model (lane I17): its download Yes card, the download to its end,
        then its role. ``added`` or ``not_added``, with the line that is true."""
        from ...zone2.roles import ROLE_WORDS
        name, model, role = app["name"], app["model"], app["role"]
        ports = self.host.ports
        need = [getattr(ports, f, None) for f in ("model_installed", "model_card", "model_pull", "model_set_role")]
        if not all(callable(f) for f in need):
            return "not_added", MODEL_NO_PORTS.format(name=name)
        here, raise_card, pull, set_role = need
        words = ROLE_WORDS.get(role, role)
        try:
            if not here(model):
                cid = app.get("card")
                if not cid:
                    self._guard(build_id)
                    cid = str(raise_card(model)["consent_id"])
                    self._set(build_id, n, card=cid, state="asking", line=MODEL_ASKING.format(name=name))
                status = self._answer(build_id, cid)
                if status == "declined":
                    return "not_added", MODEL_SAID_NO.format(name=name)
                shown = (ports.consent_state(cid) or {}).get("card") or {}
                if status != "granted" or shown.get("model") != model:
                    if status == "pending":
                        self._guard(build_id)
                        ports.consent_expire(cid, f"{name} was not downloaded: its Yes card got no answer in time.")
                    return "not_added", MODEL_NO_ANSWER.format(name=name)
                self._guard(build_id)
                if not here(model):            # downloaded meanwhile (another owner): never twice
                    self._set(build_id, n, state="installing",
                              line=MODEL_PULLING.format(name=name, size=app.get("disk_gb")))
                    ok, why = pull(model)
                    if not ok:
                        return "not_added", MODEL_FAILED.format(name=name, why=_sentence(why or "the download did not finish."))
            self._guard(build_id)
            set_role(role, model)
        except Stale:
            raise
        except Exception as exc:  # noqa: BLE001 - Refused, a model server that stopped: its plain words
            return "not_added", MODEL_FAILED.format(name=name, why=_sentence(str(exc) or type(exc).__name__))
        return "added", MODEL_ADDED.format(name=name, words=words)

    def _one(self, build_id: str, n: int, app: dict) -> tuple[str, str]:
        """``(state, line)`` for one store app: ``added`` or ``not_added``, with the line that is true."""
        if app.get("kind") == "model":
            return self._model(build_id, n, app)
        name, pkg = app["name"], app["package"]
        row_id = bare_id(pkg)                                   # the Registry's key; *pkg* is what adds it (D96)
        ports = self.host.ports
        if self.host.registry is not None and self._row(row_id) is not None:
            return self._final(build_id, name, row_id)          # added some other way meanwhile
        cid = app.get("card")
        if not cid:
            self._guard(build_id)
            gone = self._no_longer_offered(name, pkg)
            if gone:
                return "not_added", gone
            try:
                staged = ports.packages_add(pkg, official_only=True) if pkg.startswith("store:") else ports.packages_add(pkg)
            except NotBuilt:
                return "not_added", NO_PACKAGES.format(name=name)
            except Exception as exc:  # noqa: BLE001 - Refused and friends: their plain words
                return "not_added", FAILED.format(name=name, why=_sentence(str(exc) or "it could not be checked."))
            cid = str(staged["consent_id"])
            self._set(build_id, n, card=cid, state="asking", line=ASKING.format(name=name))
        status = self._answer(build_id, cid)
        if status == "declined":
            return "not_added", SAID_NO.format(name=name)
        if status != "granted":
            if status == "pending":
                self._guard(build_id)
                ports.consent_expire(cid, f"{name} was not added: its Yes card got no answer in time.")
            return "not_added", NO_ANSWER.format(name=name)
        self._set(build_id, n, state="installing", line=INSTALLING.format(name=name))
        self._guard(build_id)
        try:
            result = ports.packages_wait(cid, INSTALL_WAIT_S)
        except Exception as exc:  # noqa: BLE001
            return "not_added", FAILED.format(name=name, why=_sentence(str(exc)))
        self._guard(build_id)
        if (result or {}).get("status") != "installed":
            problems = " ".join(map(str, (result or {}).get("problems") or [])) or "the install did not finish."
            return "not_added", FAILED.format(name=name, why=_sentence(problems))
        return self._final(build_id, name, row_id)

    def _no_longer_offered(self, name: str, pkg: str) -> str:
        """The true line when a plan's ``store:<id>`` is not what the official store offers now (D96), else "".
        Can't tell (no network): "", and the add, pinned to the official store, says it could not reach it."""
        ask = getattr(self.host.ports, "store_row", None)
        if not pkg.startswith("store:") or not callable(ask):
            return ""
        try:
            row = ask(bare_id(pkg))
        except Exception:  # noqa: BLE001 - nothing to tell by: the pinned add says so, and adds nothing
            return ""
        if row is None:
            return NOT_LISTED.format(name=name)
        if row.get("yanked_why"):
            return PULLED.format(name=name, why=_sentence(row["yanked_why"]))
        if row.get("kind") not in SUGGESTED:
            return NOT_A_TOOL.format(name=name)
        return "" if row.get("tier") == "verified" else NOT_VERIFIED.format(name=name)

    def _answer(self, build_id: str, cid: str) -> str:
        """The card's status once it is answered (or the wait ends): granted, declined, expired, ... Stops
        (:class:`Stale`) the moment another owner takes over."""
        end = time.monotonic() + CARD_WAIT_S
        status = "pending"
        while time.monotonic() < end:
            self._guard(build_id)
            status = str((self.host.ports.consent_state(cid) or {}).get("status") or "gone")
            if status != "pending":
                return status
            time.sleep(self.poll_s)
        return status

    def _row(self, pkg: str) -> dict | None:
        row = self.host.registry.get(pkg) if self.host.registry is not None else None
        return row if isinstance(row, dict) and row.get("status") != "removed" else None

    def _final(self, build_id: str, name: str, pkg: str) -> tuple[str, str]:
        """Wait for the app's row to reach a final status (never ``installing``); ``broken`` is not added."""
        end = time.monotonic() + INSTALL_WAIT_S
        row = None
        while time.monotonic() < end:
            self._guard(build_id)
            row = self._row(pkg)
            if row is not None and row.get("status") != "installing":
                break
            time.sleep(self.poll_s)
        status = (row or {}).get("status")
        if status in FINAL:
            return "added", ADDED.format(name=name)
        if status == "broken":
            why = str(((row or {}).get("health") or {}).get("reason") or "it did not start").rstrip(".")
            return "not_added", BROKEN.format(name=name, why=why)
        return "not_added", FAILED.format(name=name, why="it did not finish installing.")


def _sentence(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if text.endswith((".", "!", "?")) else text + "."


__all__ = ["StorePhase"]
