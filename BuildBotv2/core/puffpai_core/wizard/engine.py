r"""The wizard engine: runs step records, keeps progress, calls the other lanes (03 Part 1).

One function drives every step for both renderers::

    screen = engine.submit(step_id, answers, renderer="browser" | "terminal")

Send ``{}`` to start (or resume) a step, then one or more answers at a time.
The reply is one screen (``openapi.yaml`` ``POST /v1/wizard/{step}``):
``status`` is ``ask`` (``field`` is the next question), ``show`` (a
``show_once`` value to show, then ``field``), ``done``, ``waiting``,
``not_built``, or ``working`` (the step goes on by itself: send ``{}``
again after ``poll_s`` seconds; only new ``lines`` come each time). The
renderers only draw screens; every rule and line is here.

The build step is live (``StepHooks.live``): while Toolsmith builds it shows
what each job does, asks her each "Your turn" step (Done, Skip, Not yet) and
each Yes card for what was not on her plan, and is ``done`` when every job
has ended.

Rules kept here (D36):
- A field that ``show_when`` hides is never asked. A hidden answer that
  arrives anyway is dropped with a line, never silently.
- Path answers are expanded (``~``, ``%VAR%``, ``$VAR``, ``${VAR}``) and shown
  back; an unknown variable is a plain error.
- Examples are the ones for this platform only.
- A secret is used the moment it arrives (the PIN seals the vault; a key goes
  to the vault, or to the vault inbox while it is locked) and is never kept
  in progress. A secret with no home that can keep it is not asked.
- The recovery code is shown once; after the typed-back code matches, the
  screen says ``clear`` with the line for that renderer. ``new`` at that
  question makes a new code, with the PIN typed again; that PIN also opens
  the vault this setup is finishing if it has locked (D106).
- The brain's state is read by name before the goal step uses it.

Progress lives in ``settings\wizard.json``:
``{"steps": {id: {"state", "at", "by"}}, "partial": {id: {"answers", "settled"}}, "build", "complete"}``.
Only non-secret answers are written there.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import bot as bot_mod, jsonfile
from ..zone2 import ollama
from . import census as census_mod
from . import lines as L
from .fields import SECRET_TYPES, PathError, example, expand_path, synced_folder, this_platform, visible
from .services import Services
from .steps import load_steps

#: Field types answered by picking one of ``options`` (a ``theme`` is a choice of the bot's looks: the browser
#: draws swatches for it, the terminal a list of names).
CHOICE_TYPES = ("choice", "theme")
#: How long the name step waits for the brain's suggestions before it goes on with the default name.
IDEAS_WAIT_S = 90.0
IDEAS_SYSTEM = ("You help name a home AI bot. From the owner's goal, suggest 3 short, friendly names for the bot "
                "(one or two words each, at most 20 characters, letters and spaces only, no names of real people), "
                "and pick the one look below that fits the goal best.\nLooks: {looks}\n"
                'Answer JSON only: {{"names": ["...", "...", "..."], "theme": "<a look id>"}}')


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Ask(Exception):
    """An answer that can't be taken: ask the field again with this plain error. *again* names an earlier
    field of the same step to ask instead (the PIN, when the PIN typed again does not match)."""

    def __init__(self, message: str, again: str | None = None) -> None:
        super().__init__(message)
        self.again = again


class Task:
    """One slow part running in the background (``Engine.task``)."""

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.started = time.monotonic()
        self.done_event = threading.Event()
        self.result: Any = None
        self.error: BaseException | None = None
        self.told = 0                            # how many progress lines were said
        threading.Thread(target=self._run, args=(fn,), name="wizard-task", daemon=True).start()

    def _run(self, fn: Callable[[], Any]) -> None:
        try:
            self.result = fn()
        except BaseException as exc:  # noqa: BLE001 - said by the step that asked
            self.error = exc
        finally:
            self.done_event.set()

    @property
    def done(self) -> bool:
        return self.done_event.is_set()

    def progress(self, first: str, again: str, every_s: float = 30.0) -> list[str]:
        """The first line once, then one "still working" line every *every_s* seconds."""
        if self.told == 0:
            self.told = 1
            return [first]
        seconds = int(time.monotonic() - self.started)
        if seconds >= self.told * every_s:
            self.told += 1
            return [again.format(seconds=seconds)]
        return []


class Engine:
    def __init__(self, state: Path | str, services: Services, steps: list[dict] | None = None,
                 preset: dict | None = None, registry: Any = None, bus: Any = None,
                 platform: str | None = None, env: dict | None = None,
                 census_fn: Callable[[], dict] | None = None, brain_wait_s: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep, quick_s: float = 1.0) -> None:
        self.state = Path(state)
        self.quick_s = quick_s                 # a slow part goes on in the background after this long
        self._tasks: dict[str, Task] = {}
        self._tasks_lock = threading.Lock()
        self.services = services
        self.registry = registry
        self.bus = bus
        self.platform = platform or this_platform()
        self.env = env
        self.census_fn = census_fn or census_mod.run_census
        self.brain_wait_s = brain_wait_s
        self.sleep = sleep
        if steps is None:
            steps, more, _ = load_steps(self.state, registry)
            preset = {**more, **(preset or {})}
        self.steps = steps
        self.preset = dict(preset or {})
        self.hooks: dict[str, StepHooks] = {
            "scan": ScanStep(self), "brain": BrainStep(self), "vault": VaultStep(self),
            "bridge": BridgeStep(self), "toolsmith": ToolsmithStep(self), "goal": GoalStep(self),
            "name": NameStep(self), "look": LookStep(self), "plan": PlanStep(self), "yes": YesStep(self), "build": BuildStep(self),
            "prove": ProveStep(self), "done": DoneStep(self),
        }

    # -- slow parts -------------------------------------------------------------
    def task(self, key: str, fn: Callable[[], Any]) -> "Task":
        """*fn* once per *key*, in the background; waits up to ``quick_s`` for it first. A step whose part is
        slow (the brain's first answer) answers ``working`` meanwhile, so both renderers show a line."""
        with self._tasks_lock:
            t = self._tasks.get(key)
            if t is None:
                t = self._tasks[key] = Task(fn)
        t.done_event.wait(self.quick_s)
        return t

    # -- the brain's ideas for the bot's name and look (one small JSON ask) ----------
    def goal_text(self) -> str:
        return " ".join(str(self.answers_of("goal").get("goal") or self.preset.get("goal") or "").split())

    def ideas_task(self) -> "Task | None":
        """The one ask for names and a look, from her goal, in the background. None when there is no goal or no
        brain to ask (the name is then Puff, the look puff)."""
        goal, ask = self.goal_text(), getattr(self.services, "brain", None)
        if not goal or not callable(ask):
            return None
        return self.task("ideas:" + goal, lambda: ask_ideas(ask, goal))

    def ideas(self) -> dict:
        """``{"names": [up to 3], "theme": id or None, "asked": bool}``; empty when the brain was not asked, has
        not answered, or answered something that can't be used."""
        t = self.ideas_task()
        if t is None or not t.done or t.error is not None or not isinstance(t.result, dict):
            return {"names": [], "theme": None, "asked": False}
        return dict(t.result, asked=True)

    def drop_task(self, key: str) -> None:
        with self._tasks_lock:
            self._tasks.pop(key, None)

    # -- files ----------------------------------------------------------------
    @property
    def settings(self) -> Path:
        return self.state / "settings"

    def read_setting(self, name: str, default: Any = None) -> Any:
        return jsonfile.read(self.settings / name, default)

    def write_setting(self, name: str, data: Any) -> None:
        jsonfile.write(self.settings / name, data)

    def _progress(self) -> dict:
        data = jsonfile.read(self.settings / "wizard.json", {})
        data.setdefault("steps", {})
        data.setdefault("partial", {})
        data.setdefault("answers", {})
        data.setdefault("complete", False)
        return data

    def _save(self, prog: dict) -> None:
        jsonfile.write(self.settings / "wizard.json", prog)

    def build_id(self) -> str | None:
        return self._progress().get("build")

    def set_build(self, build: str) -> None:
        prog = self._progress()
        prog["build"] = build
        self._save(prog)

    def answers_of(self, step_id: str) -> dict:
        prog = self._progress()
        return {**prog["answers"].get(step_id, {}), **prog["partial"].get(step_id, {}).get("answers", {})}

    # -- the step list --------------------------------------------------------
    def step(self, step_id: str) -> dict:
        for s in self.steps:
            if s["id"] == step_id:
                return s
        raise KeyError(step_id)

    def overview(self) -> dict:
        prog = self._progress()
        rows = [{"id": s["id"], "title": s["title"], "required": bool(s.get("required")),
                 "state": prog["steps"].get(s["id"], {}).get("state", "todo")} for s in self.steps]
        current = next((r["id"] for r in rows if r["state"] not in ("done", "not_built")), None)
        return {"steps": rows, "current": current, "complete": bool(prog["complete"]), "platform": self.platform}

    def next_step(self, step_id: str) -> str | None:
        ids = [s["id"] for s in self.steps]
        i = ids.index(step_id)
        return ids[i + 1] if i + 1 < len(ids) else None

    def hook(self, step_id: str) -> "StepHooks":
        return self.hooks.get(step_id) or StepHooks(self)

    # -- one call for both renderers -----------------------------------------
    def submit(self, step_id: str, answers: dict | None = None, renderer: str = "browser") -> dict:
        step = self.step(step_id)
        renderer = renderer if renderer in ("browser", "terminal") else "browser"
        incoming = dict(answers or {})
        hook = self.hook(step_id)
        live = hook.live(step, incoming, renderer)
        if live is not None:
            return self._finish_screen(step, live, renderer)
        prog = self._progress()
        if not incoming and prog["steps"].get(step_id, {}).get("state") == "done":
            prog["partial"].pop(step_id, None)          # a done step opened again starts over
            self._save(prog)
            prog = self._progress()
        part = prog["partial"].setdefault(step_id, {"answers": {}, "settled": []})
        known: dict = dict(part["answers"])
        settled: list = list(part["settled"])
        lines: list[str] = []
        screen: dict[str, Any] = {"step": step_id, "title": step["title"], "lines": lines}

        stop = hook.before(step, known)
        if stop is not None:
            return self._finish_screen(step, stop, renderer)
        if not incoming:
            # What the step shows comes on its opening screen only: a later screen (after an answer) is added
            # under it on the page, so the same lines would show again (the plan summary twice, lane I4a).
            lines.extend(hook.view_lines(step, known))

        for field in step["fields"]:
            fid = field["id"]
            if not hook.wanted(field, known):       # nothing to ask this time (an offer field with no offers)
                incoming.pop(fid, None)
                known.pop(fid, None)
                if fid in settled:
                    settled.remove(fid)
                continue
            if not visible(field, known):
                if fid in incoming:
                    lines.append(self._not_used(step, field, incoming[fid], known))
                    incoming.pop(fid)
                known.pop(fid, None)
                if fid in settled:
                    settled.remove(fid)
                continue
            if fid in settled:
                continue
            if self._cannot_keep(step, field):
                lines.append(hook.cannot_keep_line(field, known))
                continue
            if fid not in incoming:
                if field["type"] == "show_once":         # made by an earlier answer (the PIN)
                    continue
                return self._ask(step, field, known, settled, lines, renderer, prog, screen)
            value = incoming.pop(fid)
            try:
                result = self._take(step, field, value, known, renderer, lines)
            except Ask as err:
                back = next((f for f in step["fields"] if f["id"] == err.again), None) if err.again else None
                if back is not None:                      # the questions after it hang on its answer
                    later = [f["id"] for f in step["fields"][step["fields"].index(back):]]
                    settled = [s for s in settled if s not in later]
                self._store(prog, step_id, known, settled)
                return self._ask(step, back or field, known, settled, lines, renderer, prog, screen, error=str(err))
            settled.append(fid)
            if result and result.get("show"):
                shown = result["show"]
                if field["type"] == "secret_confirm":   # a new code: it still has to be typed back
                    settled.remove(fid)
                if shown["field"] not in settled:
                    settled.append(shown["field"])
                self._store(prog, step_id, known, settled)
                screen["status"] = "show"
                line = L.SHOWN_ONCE_TERMINAL if renderer == "terminal" else L.SHOWN_ONCE
                screen["show"] = {"field": shown["field"], "value": shown["value"],
                                  "line": f"{L.OLD_CODE_GONE} {line}" if shown.get("replaces") else line}
                nxt = self._next_unsettled(step, known, settled)
                screen["answered"] = self._answered(step, known, settled)
                screen["field"] = self.resolve(step, nxt, known, renderer) if nxt else None
                return screen
            if result and result.get("clear"):
                screen["clear"] = True
                lines.append(L.CLEARED[renderer])

        for fid, value in incoming.items():           # answers for fields this step does not have
            lines.append(f"This step has no question {fid}, so that answer was not used.")
        self._store(prog, step_id, known, settled)
        result = hook.finish(step, known, renderer)
        result["lines"] = lines + result.get("lines", [])
        return self._finish_screen(step, result, renderer, answered=self._answered(step, known, settled),
                                   clear=screen.get("clear"))

    # -- pieces of submit -------------------------------------------------------
    def _store(self, prog: dict, step_id: str, known: dict, settled: list) -> None:
        prog["partial"][step_id] = {"answers": known, "settled": settled}
        self._save(prog)

    def _next_unsettled(self, step: dict, known: dict, settled: list) -> dict | None:
        for field in step["fields"]:
            if visible(field, known) and field["id"] not in settled and field["type"] != "show_once" \
                    and not self._cannot_keep(step, field) and self.hook(step["id"]).wanted(field, known):
                return field
        return None

    def _ask(self, step, field, known, settled, lines, renderer, prog, screen, error=None) -> dict:
        self._store(prog, step["id"], known, settled)
        screen.update(status="ask", field=self.resolve(step, field, known, renderer),
                      answered=self._answered(step, known, settled))
        if error:
            screen["error"] = error
        # The code was shown on an earlier screen. A hook's own label means this question asks something else now.
        if field["type"] == "secret_confirm" and known.get("_shown_before") \
                and not self.hook(step["id"]).label(field, known):
            screen["lines"].append(L.CODE_LOST)
        return screen

    def _finish_screen(self, step, result, renderer, answered=None, clear=None) -> dict:
        status = result.get("status", "done")
        sid = step["id"]
        prog = self._progress()
        if status in ("done", "not_built", "waiting"):
            prog["steps"][sid] = {"state": status, "at": _now(), "by": renderer}
            if status == "done":
                part = prog["partial"].pop(sid, None) or {}
                answers = {k: v for k, v in part.get("answers", {}).items() if not k.startswith("_")}
                prog["answers"][sid] = answers
            self._save(prog)
            self._publish(sid, status)
        out = {"status": status, "step": sid, "title": step["title"], "lines": result.get("lines", []),
               "answered": answered or [], "next": self.next_step(sid)}
        for key in ("lane", "jobs", "field", "poll_s", "asked", "error", "summary", "alive", "repeat"):
            if key in result:
                out[key] = result[key]
        if clear:
            out["clear"] = True
        return out

    def _publish(self, step_id: str, state: str) -> None:
        if self.bus is None:
            return
        from ..bus import BUS_SOURCE
        self.bus.publish(self.bus.message("wizard.progress", {"step": step_id, "state": state}, BUS_SOURCE))

    def _cannot_keep(self, step: dict, field: dict) -> bool:
        """A vault secret outside the vault step is asked only while the vault can keep it (D36(a))."""
        if field["type"] != "secret" or step["id"] == "vault":
            return False
        if not str(field.get("home", "")).startswith("vault:"):
            return False
        vault = self.services.vault
        return vault is None or vault.status() not in ("open", "locked")

    def _not_used(self, step: dict, field: dict, value: Any, known: dict) -> str:
        noun = field["id"].replace("_", " ")
        picked = []
        for other in (field.get("show_when") or {}):
            src = next((f for f in step["fields"] if f["id"] == other), None)
            label = self._option_label(step, src, known.get(other), known) if src else None
            picked.append(label or str(known.get(other)))
        why = f"You picked {' and '.join(picked)}, so" if picked else "So"
        if field["type"] in SECRET_TYPES:
            return f"{why} the {noun} you typed was not used, and BuildBot did not keep it."
        return f"{why} the {noun} you typed ({value}) was not used."

    def _option_label(self, step: dict, field: dict, value: Any, known: dict) -> str | None:
        for opt in self.options(step, field, known, saved=True) or []:
            if opt["value"] == value:
                return opt["label"]
        return None

    def options(self, step: dict, field: dict, known: dict, *, saved: bool = False) -> list[dict] | None:
        """What she may pick. An option marked ``hidden`` is never offered or taken (D109); *saved* adds it back, so
        an answer saved before it was hidden still has its words."""
        given = self.hook(step["id"]).options(field, known)
        if field["type"] not in CHOICE_TYPES:
            return given if field["type"] == "text" else None     # a text field's suggestions (the name)
        if given is None:
            given = [o if isinstance(o, dict) else {"value": o, "label": str(o)} for o in field.get("options", [])]
        return given if saved else [o for o in given if not o.get("hidden")]

    def resolve(self, step: dict, field: dict, known: dict, renderer: str) -> dict:
        hook = self.hook(step["id"])
        hints = L.HINT[renderer]
        out = {"id": field["id"], "type": field["type"],
               "label": hook.label(field, known) or field.get("label", field["id"]),
               "help": hook.help(field, known) or field.get("help", ""),
               "required": field.get("required", True) is not False,
               "hidden": field["type"] in SECRET_TYPES}
        board = None
        ex = example(field, self.platform, board)
        if ex:
            out["example"] = ex
            out["example_line"] = f"For example: {ex}"
        default = hook.default(field, known)
        opts = self.options(step, field, known)
        if opts is not None:
            out["options"] = opts
            if default is None and opts:
                default = next((o["value"] for o in opts if o.get("note", "").startswith("Recommended")),
                               opts[0]["value"])
        if default is not None:
            out["default"] = default
        if field["type"] in CHOICE_TYPES:
            label = next((o["label"] for o in opts or [] if o["value"] == default), None)
            out["hint"] = hints["choice"].format(default=label) if label else hints["choice_nodefault"]
        elif opts and default not in (None, ""):          # suggestions she may pick, or type her own
            out["hint"] = hints["suggest"].format(default=default)
        elif field["type"] == "confirm":
            out["hint"] = hints["confirm"]
        elif out["hidden"]:
            # An optional key (D93): hidden, and Enter (or an empty box) skips it.
            skip = "" if out["required"] else hints["skip"]
            out["hint"] = " ".join(h for h in (hints["hidden"], skip) if h)
        elif default not in (None, ""):
            out["hint"] = hints["use"].format(default=default)
            # The hint names the exact value Enter uses; a general example next to it would name another
            # folder (the data folder's example has no bot name in it, while Enter adds it).
            out.pop("example", None)
            out.pop("example_line", None)
        elif not out["required"]:
            out["hint"] = hints["skip"]
        else:
            out["hint"] = ""
        return out

    def _answered(self, step: dict, known: dict, settled: list) -> list[dict]:
        out = []
        for field in step["fields"]:
            fid = field["id"]
            if fid not in settled or not visible(field, known):
                continue
            if field["type"] in SECRET_TYPES:
                shown = L.SHOWN_SKIPPED if known.get(f"_{fid}_skipped") else L.SHOWN_SECRET
            elif field["type"] == "show_once":
                shown = L.SHOWN_ONCE_DONE
            elif field["type"] in CHOICE_TYPES:
                shown = self._option_label(step, field, known.get(fid), known) or str(known.get(fid, ""))
            elif field["type"] == "confirm":
                shown = "Yes" if known.get(fid) else "No"
            else:
                shown = "" if known.get(fid) in (None, "") else str(known.get(fid))
            out.append({"field": fid, "label": self.hook(step["id"]).label(field, known) or field.get("label", fid),
                        "shown": shown})
        return out

    def _take(self, step: dict, field: dict, value: Any, known: dict, renderer: str, lines: list) -> dict | None:
        """Check one answer and use it. Raises :class:`Ask` to ask again."""
        hook = self.hook(step["id"])
        kind = field["type"]
        required = field.get("required", True) is not False
        if kind in ("secret", "secret_confirm"):
            if not isinstance(value, str) or not value.strip():
                if kind == "secret" and not required:     # an optional key (D93): Enter skips, nothing is kept
                    known[f"_{field['id']}_skipped"] = True
                    known.pop(f"_{field['id']}_kept", None)
                    return None
                raise Ask(L.NEEDED)
            try:
                value.encode("utf-8")
            except UnicodeError:                          # a lone surrogate: wrong input, never a raw error
                raise Ask(L.CANT_READ) from None
            known.pop(f"_{field['id']}_skipped", None)
            if kind == "secret_confirm":
                return hook.confirm(field, value.strip(), known, renderer)
            handled = hook.on_secret(field, value.strip(), known, lines)
            return handled if handled is not None else self.keep_secret(field, value.strip(), known, lines)
        if kind == "show_once":
            return None
        if isinstance(value, str):
            value = value.strip()
        if value in (None, "") and kind != "confirm":
            default = hook.default(field, known)
            if default not in (None, ""):
                value = default
            elif kind in CHOICE_TYPES:
                opts = self.options(step, field, known) or []
                if not opts:
                    raise Ask(L.PICK_ONE)
                value = next((o["value"] for o in opts if o.get("note", "").startswith("Recommended")),
                             opts[0]["value"])
            elif required:
                raise Ask(L.NEEDED)
            else:
                known[field["id"]] = ""
                return None
        if kind in CHOICE_TYPES:
            opts = self.options(step, field, known) or []
            if value not in [o["value"] for o in opts]:
                raise Ask(L.PICK_ONE)
        elif kind == "confirm":
            if isinstance(value, bool):
                pass
            elif str(value).strip().lower() in ("yes", "y", "true", "1"):
                value = True
            elif str(value).strip().lower() in ("no", "n", "false", "0"):
                value = False
            else:
                raise Ask(L.YES_OR_NO)
        elif kind == "number":
            try:
                value = float(value)
            except (TypeError, ValueError):
                raise Ask(L.A_NUMBER) from None
            if value < 0:
                raise Ask(L.A_NUMBER)
        elif kind == "path":
            try:
                value = expand_path(value, self.platform, self.env)
            except PathError as exc:
                raise Ask(str(exc)) from None
            lines.append(f"Using {value}")
            service = synced_folder(value)
            if service:
                lines.append(f"{value} is inside {service}, which copies it to the internet. "
                             f"Pick a folder outside {service} to keep it on this computer.")
        hook.check(field, value, known)
        known[field["id"]] = value
        return None

    def keep_secret(self, field: dict, value: str, known: dict, lines: list) -> None:
        """File a secret at its home: the vault when open, the vault inbox while locked (D36(a))."""
        home = str(field.get("home", ""))
        home = re.sub(r"<([a-z]+)-id>", lambda m: str(known.get(m.group(1), m.group(0))), home)
        vault = self.services.vault
        if vault is None:
            raise Ask("BuildBot can't keep this yet (the vault is not built), so it was not taken. "
                      + L.NOT_BUILT)
        name = home.split(":", 1)[1] if home.startswith("vault:") else home
        status = vault.status()
        if status == "open":
            vault.put_secret(name, value)
            lines.append("Your key is saved in your vault.")
        elif status == "locked" or home.startswith("package:"):
            vault.deposit(name, value)
            lines.append("Your key is sealed in your vault. BuildBot files it the next time you unlock.")
        else:
            raise Ask("Set up the vault first, so this has a safe home.")
        known[f"_{field['id']}_kept"] = "vault" if status == "open" else "inbox"
        return None


class StepHooks:
    """What a step does beyond asking its fields. The default: keep the answers, done."""

    def __init__(self, engine: Engine) -> None:
        self.e = engine

    def before(self, step: dict, known: dict) -> dict | None:
        return None

    def live(self, step: dict, incoming: dict, renderer: str) -> dict | None:
        """A step that runs by itself (the build): the whole screen, or None for the usual fields."""
        return None

    def view_lines(self, step: dict, known: dict) -> list[str]:
        return []

    def options(self, field: dict, known: dict) -> list[dict] | None:
        return None

    def wanted(self, field: dict, known: dict) -> bool:
        """False leaves the field out this time (there is nothing to ask), the way ``show_when`` does."""
        return True

    def help(self, field: dict, known: dict) -> str | None:
        """The field's help said with what only this computer knows, or None for the package's own words."""
        return None

    def label(self, field: dict, known: dict) -> str | None:
        return None

    def default(self, field: dict, known: dict) -> Any:
        return None

    def check(self, field: dict, value: Any, known: dict) -> None:
        return None

    def cannot_keep_line(self, field: dict, known: dict) -> str:
        return f"{field.get('label', field['id'])}: set up the vault first, so it has a safe home. This was not asked."

    def on_secret(self, field: dict, value: str, known: dict, lines: list) -> dict | None:
        return None

    def confirm(self, field: dict, value: str, known: dict, renderer: str) -> dict | None:
        return None

    def finish(self, step: dict, known: dict, renderer: str) -> dict:
        return {"status": "done", "lines": []}

    def not_built(self, lane: str, line: str | None = None) -> dict:
        return {"status": "not_built", "lane": lane, "lines": [line or L.NOT_BUILT]}


class ScanStep(StepHooks):
    def finish(self, step, known, renderer):
        census = self.e.census_fn()
        found = self._runtime()
        if found is not None:
            census.setdefault("programs", {})["ollama_address"] = found
        self.e.write_setting("hardware.json", census)
        out = [L.FIRST_LOOK] + census_mod.census_summary_lines(census) + list(census.get("notes") or [])
        if found:
            out.append(L.OLLAMA_FOUND.format(address=found))
        elif found is not None:
            out.append(L.OLLAMA_NOT_FOUND)
        return {"status": "done", "lines": out}

    def _runtime(self) -> str | None:
        """The model slot's own look for a running Ollama (it keeps the address); None when it can't look."""
        find = getattr(self.e.services.models, "find_runtime", None)
        if not callable(find):
            return None
        try:
            return str(find() or "")
        except Exception:  # noqa: BLE001 - not found is said plainly
            return ""


class BrainStep(StepHooks):
    def _census(self) -> dict:
        census = self.e.read_setting("hardware.json")
        if not census:
            census = self.e.census_fn()
            self.e.write_setting("hardware.json", census)
        return census

    def _fit(self) -> dict:
        """L2's ``fit(census)``: ``{options, recommended, already_here, too_big, message}``."""
        out = self.e.services.models.fit(self._census())
        return out if isinstance(out, dict) else {}

    def _menu(self) -> list[dict]:
        """The fit's options, in the words this step shows: name, title, size_gb, installed, reason."""
        rows = []
        for o in self._fit().get("options") or []:
            if not isinstance(o, dict) or not (o.get("id") or o.get("name")):
                continue
            rows.append({"name": str(o.get("id") or o.get("name")),
                         "title": o.get("label") or o.get("title") or str(o.get("id") or o.get("name")),
                         "size_gb": o.get("disk_gb") if o.get("disk_gb") is not None else o.get("size_gb"),
                         "installed": bool(o.get("installed")), "recommended": bool(o.get("recommended")),
                         "reason": o.get("reason") or "", "warning": o.get("warning") or ""})
        return rows

    def _here(self) -> dict | None:
        """The model the Enter (or Next) default is: the Recommended one when it is already downloaded, else
        the strongest model already here that fits (the menu is strongest first). None when none is here.
        A download is never the default while a model that fits is already on this computer."""
        menu = self._menu()
        rec = next((m for m in menu if m.get("recommended")), None)
        if rec is not None and rec.get("installed"):
            return rec
        return next((m for m in menu if m.get("installed")), None)

    def view_lines(self, step, known):
        if self.e.services.models is None:
            return []
        out = []
        message = self._fit().get("message")
        if message:
            out.append(message)
        here = self._here()
        if here is not None:
            line = f"{here['title']} is already downloaded, so it is the default and needs no download."
            rec = next((m for m in self._menu() if m.get("recommended")), None)
            if rec is not None and rec["name"] != here["name"] and rec.get("size_gb"):
                line += f" {rec['title']} is Recommended, a {rec['size_gb']:g} GB download if you pick it."
            out.append(line)
        return out

    def default(self, field, known):
        if field["id"] != "model":
            return None
        here = self._here()
        return here["name"] if here is not None else None

    def before(self, step, known):
        if self.e.services.models is None:
            return self.not_built("L2", "Picking the brain is not built yet, so this step waits for it.")
        return None

    @staticmethod
    def _reason(title: str, reason: str) -> str:
        """The recommender's reason without the model's name again: "Recommended: Qwen3.5 9B (7 GB
        download). It fits ..." becomes "Recommended. 7 GB download. It fits ..." (the name is the label)."""
        head = f"Recommended: {title} ("
        if reason.startswith(head) and ")." in reason:
            size, rest = reason[len(head):].split(").", 1)
            return f"Recommended. {size}. {rest.strip()}".strip()
        return reason

    def options(self, field, known):
        if field["id"] != "model":
            return None
        out = []
        for m in self._menu():
            note = []
            reason = self._reason(str(m.get("title") or m["name"]), str(m.get("reason") or ""))
            if m.get("recommended") and not reason.startswith("Recommended"):
                note.append("Recommended.")
            if reason:
                note.append(reason)
            if m.get("installed"):
                if "Already" not in reason:
                    note.append("Already downloaded.")
            elif m.get("size_gb") and "GB download" not in reason:
                note.append(f"{m['size_gb']:g} GB download.")
            if m.get("warning"):
                note.append(m["warning"])
            out.append({"value": m["name"], "label": m.get("title") or m["name"], "note": " ".join(note)})
        out.append({"value": "other", "label": "Use another model",
                    "note": "Any model name on this computer's Ollama. A hosted (cloud) model is not used here."})
        return out

    def check(self, field, value, known):
        """Lane I17: the brain runs on this computer. A name that is not a local Ollama model (a cloud tag, a
        slash, an address) is refused, and so is one Ollama itself says is hosted."""
        if field["id"] != "other":
            return
        from ..zone2 import roles
        said = roles.name_problem(str(value or ""))
        if said:
            raise Ask(said)
        if ollama.only_model() and str(value).strip() != ollama.only_model():
            raise Ask(ollama.ONLY_LINE.format(only=ollama.only_model(), model=str(value).strip()))
        remote = getattr(self.e.services.models, "is_remote", None)
        if callable(remote) and remote(str(value).strip()):
            raise Ask(roles.NOT_LOCAL.format(model=str(value).strip()))

    def finish(self, step, known, renderer):
        name = known.get("other") if known.get("model") == "other" else known.get("model")
        row = next((m for m in self._menu() if m["name"] == name), {"name": name, "title": name})
        # settings\model.json is lane L2's one model setting: keep its fields, add the choice.
        record = self.e.read_setting("model.json", {})
        record = dict(record) if isinstance(record, dict) else {}
        record.update({"name": name, "chosen_at": _now(), "by": "wizard"})
        if known.get("model") == "other":
            record["other"] = name                # the one model outside the catalog a role may use (lane I17)
        self.e.write_setting("model.json", record)
        choose = getattr(self.e.services.models, "choose", None)
        if callable(choose):
            choose(name)                          # lane L2: zone2.settings.save(model=name)
        title = row.get("title") or name
        out = [f"Your bot's brain: {title}."]
        if row.get("installed"):
            out.append("It is already downloaded, so it starts at once.")
        elif ollama.only_model():
            out.append(ollama.NO_PULL_LINE.format(only=ollama.only_model(), model=name))
        elif callable(getattr(self.e.services.models, "pull", None)):
            self.e.services.models.pull(name)
            size = f" ({row['size_gb']:g} GB)" if row.get("size_gb") else ""
            out.append(f"Downloading {title}{size} in the background. You can go on; the goal step checks on it.")
        else:
            out.append(f"Starting the download from here is not built yet. For now, run: ollama pull {name}")
        return {"status": "done", "lines": out}


class VaultStep(StepHooks):
    def label(self, field, known):
        if field["id"] == "pin" and known.get("where") == "existing":
            return "Type your vault's PIN"         # opening a vault: no new PIN is picked here
        if field["id"] == "recovery_confirm" and self._new_code_pin:
            return "Type your vault's PIN"         # she typed new: this answer is the PIN, not the code
        return None

    def options(self, field, known):
        """A bot with a vault comes back through this step by "A vault I already have", since a second vault is
        refused, so it keeps that choice. A new bot has nothing to open (D111)."""
        vault = self.e.services.vault
        if field["id"] != "where" or vault is None or vault.status() == "missing":
            return None
        return [{**o, "hidden": False} if o["value"] == "existing" else o for o in field.get("options", [])]

    def before(self, step, known):
        if self.e.services.vault is None:
            return self.not_built("L3", "The vault is not built yet, so the PIN and recovery code can't be set "
                                        "up yet. This step waits for it.")
        return None

    def default(self, field, known):
        if field["id"] == "data_folder":
            base = "%USERPROFILE%\\PuffPAI Data" if self.e.platform == "windows" else "~/PuffPAI Data"
            sep = "\\" if self.e.platform == "windows" else "/"
            # install.sh --data-dir, or a container: with no env given it is this process's own, as expand_path reads it.
            moved = str((os.environ if self.e.env is None else self.e.env).get("PUFFPAI_HOME") or "").strip()
            if self.e.platform != "windows" and moved.startswith("/"):
                base = moved.rstrip("/").rsplit("/", 1)[0] + "/PuffPAI Data"
            try:
                return expand_path(base + sep + self.e.state.name, self.e.platform, self.e.env)
            except PathError:
                return None
        if field["id"] == "knowledge_folder":             # Enter keeps where they are: with the data folder at first
            from ..zone1 import fetched
            return fetched.chosen(self.e.state) or known.get("data_folder") or self.default({"id": "data_folder"}, known)
        return None

    def help(self, field, known):
        if field["id"] != "knowledge_folder":
            return None
        from ..zone1 import fetched
        where = self.default(field, known)
        free = fetched.free_bytes(where) if where else None
        room = "" if free is None else f" Press Enter to keep them in {where}: {fetched.drive_of(where)} has " \
                                       f"{fetched.size_said(free)} free."
        return f"{field.get('help', '')}{room}"

    def check(self, field, value, known):
        if field["id"] != "knowledge_folder":
            return
        from ..zone1 import fetched, knowledge_folder
        from ..zone1.folders import canon
        data = known.get("data_folder") or self.default({"id": "data_folder"}, known)
        if value and data and canon(value) == canon(data):
            return                                        # the default place: books and downloads go with the data
        problem = knowledge_folder.problem(self.e.state, value, data=data)
        if problem:
            raise Ask(problem)

    def _knowledge_place(self, known, data_now, kept, out) -> dict:
        """Where books and downloads go: ``kept`` with its ``knowledge`` key as it should be. Files already kept
        are never left behind here: that is a move, with its own progress (``puffpai knowledge move``)."""
        from ..zone1 import fetched, knowledge_folder
        from ..zone1.folders import canon
        want = str(known.get("knowledge_folder") or "").strip()
        was = kept.get("knowledge") if isinstance(kept.get("knowledge"), str) else ""
        new = "" if not want or canon(want) == canon(data_now) else want
        if new and canon(new) != canon(was or data_now):
            problem = knowledge_folder.problem(self.e.state, new, data=data_now)
            if problem:
                out.append(f"{new} is not used for books and downloads: {problem}")
                new = was
        moved = (new or "") != (was or "") and (bool(was) or fetched.has_files(self.e.state))
        if moved:
            out.append(f"Your books and downloads stay where they are for now. To move them to {new or data_now}: "
                       f"puffpai knowledge move {new or 'default'}")
            new = was
        kept = {k: v for k, v in kept.items() if k != "knowledge"}
        if new:
            try:
                Path(new, "knowledge").mkdir(parents=True, exist_ok=True)
                knowledge_folder.write_marker(self.e.state, Path(new) / "knowledge")
            except OSError:
                out.append(f"{new} could not be used, so books and downloads go with your data folder.")
                new = ""
        if new:
            kept["knowledge"] = new
        where = new or data_now
        free = fetched.free_bytes(where)
        room = "" if free is None else f" ({fetched.size_said(free)} free)"
        out.append(f"Your books and downloads: {where}{room}")
        return kept

    def cannot_keep_line(self, field, known):
        return "The vault can't keep this yet, so it was not asked."

    def __init__(self, engine: "Engine") -> None:
        super().__init__(engine)
        # A new PIN waits here, in memory only (never in progress), until it is typed again; then it seals
        # the vault and is dropped. A step list with no "pin_again" field seals at the first PIN.
        self._pin: str | None = None
        # She typed new, so the next answer to the same hidden question is her PIN (D106). Only this yes or no is
        # kept, in memory: the PIN itself is used once and dropped, and a restart goes back to the code question.
        self._new_code_pin = False

    def on_secret(self, field, value, known, lines):
        if field["id"] not in ("pin", "pin_again"):
            return None
        vault = self.e.services.vault
        if field["id"] == "pin" and known.get("where") == "existing":
            # On a finished bot no wizard path opens a vault (D106): this route takes an owner API key, her unlock
            # does not. Settings says "Run any step again", so an open vault still goes on: PIN checked, nothing opened.
            finished = bool(self.e._progress().get("complete"))
            if finished and vault.status() != "open":
                raise Ask(L.SETUP_DONE_VAULT)
            try:
                (vault.confirm_open if finished else vault.open_existing)(known.get("folder"), value)
            except ValueError as exc:                   # no vault here (saved before D111): she can pick again
                raise Ask(str(exc), again="where" if vault.status() == "missing" else None) from None
            lines.append("Your vault is open.")
            return {}
        step = self.e.step("vault")
        confirm = any(f["id"] == "pin_again" and visible(f, known) for f in step["fields"])
        if field["id"] == "pin" and confirm:
            check = getattr(vault, "check_pin", None)
            problem = check(value) if callable(check) else ""
            if problem:
                raise Ask(problem)
            self._pin = value
            return {}
        if field["id"] == "pin_again":
            pin, self._pin = self._pin, None
            if pin is None:
                raise Ask(L.PIN_AGAIN_LOST, again="pin")
            if value != pin:
                raise Ask(L.PIN_MISMATCH, again="pin")
            value = pin
        try:
            code = vault.create(known.get("where", "here"), known.get("folder"), value)
        except ValueError as exc:
            if known.get("where") == "folder":          # saved before D109 hid it, and refused: she can pick again
                raise Ask(str(exc), again="where") from None
            raise Ask(str(exc), again="pin" if field["id"] == "pin_again" else None) from None
        known["_shown_before"] = True
        return {"show": {"field": "recovery", "value": code}}

    def confirm(self, field, value, known, renderer):
        vault = self.e.services.vault
        said_new = value.strip().lower() == "new"       # a code is 25 characters and a PIN at least 8: never "new"
        # The vault this setup made and has not had typed back yet: create sets this and a match clears it.
        finishing = bool(known.get("_shown_before")) and not self.e._progress().get("complete")
        if said_new or self._new_code_pin:
            problem = vault.new_code_problem()          # gone, unreadable or safe mode: not simply locked
            if problem:
                self._new_code_pin = False
                raise Ask(problem)
        if self._new_code_pin:
            if said_new:
                raise Ask(L.NEW_CODE_ASK_PIN if vault.status() == "open" else L.NEW_CODE_ASK_PIN_LOCKED)
            self._new_code_pin = False                  # one try: a wrong PIN goes back to the code question
            try:
                code = vault.rotate_recovery(value, may_open=finishing)
            except ValueError as exc:
                raise Ask(str(exc)) from None
            return {"show": {"field": "recovery", "value": code, "replaces": True}}
        if said_new:
            is_open = vault.status() == "open"
            if not is_open and not finishing:
                raise Ask(L.NEW_CODE_LOCKED)
            self._new_code_pin = True
            raise Ask(L.NEW_CODE_ASK_PIN if is_open else L.NEW_CODE_ASK_PIN_LOCKED)
        try:
            matched = vault.test_recovery(value)
        except ValueError as exc:                       # too many tries: the vault's own wait, in plain words
            raise Ask(str(exc)) from None
        if not matched:
            raise Ask(L.CODE_MISMATCH)
        known.pop("_shown_before", None)
        return {"clear": True}

    def finish(self, step, known, renderer):
        # Every folder here goes through the one folder check (zone1.folders.folder_problem, lane I18f), the same
        # one Library.link and the Developer page use: never PuffPAI's own folder or a vault, nor one inside or
        # holding one. A refused folder is left out with a plain line; the step still finishes.
        from ..zone1.folders import canon, folder_problem
        out = []
        kept = jsonfile.read(self.e.settings / "folders.json", {})
        kept = kept if isinstance(kept, dict) else {}
        folder = known.get("data_folder") or self.default({"id": "data_folder"}, known)
        refused = folder_problem(self.e.state, folder, as_data=True) if folder else ""
        if refused:
            out.append(f"{folder} is not used as your data folder: {refused}")
            folder = None
        elif folder:
            Path(folder).mkdir(parents=True, exist_ok=True)
        data_now = folder or kept.get("data") or str(self.e.state / "data")
        before = [p for p in kept.get("linked") or [] if isinstance(p, str) and p.strip()]
        seen = {canon(p) for p in before}
        linked, missing = [], []
        for part in re.split(r"[;\n]", str(known.get("linked") or "")):
            if not part.strip():
                continue
            try:
                path = expand_path(part, self.e.platform, self.e.env)
            except PathError as exc:
                out.append(str(exc))
                continue
            if not Path(path).is_dir():
                missing.append(path)
                continue
            problem = folder_problem(self.e.state, path, data=data_now)
            if problem:
                out.append(f"{path} was not linked: {problem}")
                continue
            if canon(path) not in seen:
                seen.add(canon(path))
                linked.append(path)
        for path in missing:
            out.append(f"{path} is not there, so it was not linked.")
        for path in linked:
            out.append(f"Linked {path}. It is read where it is, never copied or changed.")
        self.e.write_setting("data.json", {"data_folder": folder, "linked": linked})
        # The Library and Toolsmith's zone read settings/folders.json (lane L3), never data.json: the same
        # answer goes there too (lane I18f). Folders linked before stay linked (and stay "never" for Toolsmith).
        kept = dict(kept, data=folder or kept.get("data"), linked=before + linked)
        out.append(f"Your data folder: {data_now}")
        self.e.write_setting("folders.json", self._knowledge_place(known, data_now, kept, out))
        return {"status": "done", "lines": out}


class BridgeStep(StepHooks):
    def finish(self, step, known, renderer):
        posture = known.get("posture")
        bridge = self.e.services.bridge
        if bridge is None:
            return self.not_built("L4", "The Bridge is not built yet. Setup keeps your answer, but nothing "
                                        "uses it until the Bridge is built.")
        bridge.set_posture(posture)
        # What the Bridge really is now, in the words puffpai bridge status and the Desktop use
        # (zone3.bridge.plain over the saved posture), never what she asked for.
        said = getattr(bridge, "said", None)
        return {"status": "done", "lines": list(said()) if callable(said) else []}


#: The toolsmith step's GitHub key field (D93); its home is ``vault:toolsmith.github`` (``GITHUB_KEY_NAME``).
GITHUB_FIELD = "github"


class ToolsmithStep(StepHooks):
    def _providers(self) -> list[dict]:
        ts = self.e.services.toolsmith
        if ts is None:
            return []
        return list(ts.providers() or [])

    def _provider(self, known) -> dict | None:
        return next((p for p in self._providers() if p["id"] == known.get("provider")), None)

    def view_lines(self, step, known):
        if not self._providers():
            return ["No cloud model for Toolsmith is installed, so Toolsmith uses this computer."]
        return []

    def options(self, field, known):
        if field["id"] == "where":
            opts = [dict(o) for o in field.get("options", [])]
            return opts if self._providers() else [o for o in opts if o["value"] != "cloud"]
        if field["id"] == "provider":
            out = []
            for p in self._providers():
                model = next((m for m in p.get("models", []) if m["id"] == p.get("default_model")),
                             (p.get("models") or [{}])[0])
                prices = model.get("prices") or {}
                label = f"{p['company']}: {model.get('name', model.get('id', ''))}"
                if "in" in prices and "out" in prices:
                    label += (f", ${prices['in']:g} per million tokens sent, "
                              f"${prices['out']:g} per million received")
                out.append({"value": p["id"], "label": label})
            return out
        return None

    def label(self, field, known):
        if field["id"] == "consent":
            p = self._provider(known)
            return p.get("consent") if p else None
        return None

    def default(self, field, known):
        if field["id"] == "cost_cap":
            p = self._provider(known)
            return (p or {}).get("cost_cap_usd", 5)          # D43
        return None

    def check(self, field, value, known):
        if field["id"] == "consent" and value is not True:
            company = (self._provider(known) or {}).get("company", "this company")
            raise Ask(f"Toolsmith can use {company} only after you agree to this. "
                      f"To keep everything here, go back and pick This computer.")

    def cannot_keep_line(self, field, known):
        if field["id"] == GITHUB_FIELD:
            return ("There is no vault yet, so BuildBot can't keep a GitHub key. Toolsmith searches GitHub as a "
                    "visitor. You can add a key later on the Developer page.")
        return ("Set up the vault first, so your key has a safe home. Your choice is kept, and Toolsmith waits "
                "for the key before its first run.")

    def on_secret(self, field, value, known, lines):
        """Her GitHub key (D93) has to have a key's strict shape first, the same check as the Desktop's GitHub
        block (``PUT /v1/github/key``) and Toolsmith before it ever sends one. Then the engine files it at its
        home, ``vault:toolsmith.github`` (the vault, or its inbox while locked). It is never said back."""
        if field["id"] == GITHUB_FIELD:
            from ..zone3.toolsmith_host.service import github_key_ok
            if not github_key_ok(value):
                from ..api.groups.github import BAD_SHAPE_LINE
                raise Ask(BAD_SHAPE_LINE + " Press Enter to skip it for now.")
        return None

    def finish(self, step, known, renderer):
        result = self._finish(step, known, renderer)
        if any(f.get("id") == GITHUB_FIELD for f in step.get("fields") or []):
            result["lines"] = list(result.get("lines") or []) + self._github_lines(known)
        return result

    @staticmethod
    def _github_lines(known) -> list[str]:
        if known.get(f"_{GITHUB_FIELD}_kept"):
            return ["Toolsmith adds your GitHub key only to its own GitHub searches and to reading your project list, on api.github.com."]
        if known.get(f"_{GITHUB_FIELD}_skipped"):
            return ["No GitHub key was added now. Without one, Toolsmith searches GitHub as a visitor (10 searches "
                    "a minute). You can add one later on the Developer page."]
        return []

    def _finish(self, step, known, renderer):
        choose = getattr(self.e.services.toolsmith, "choose", None)
        if known.get("where") != "cloud":
            if callable(choose):
                choose({"where": "local"})                 # lane L6's own setting (settings\toolsmith.json)
            else:
                self.e.write_setting("toolsmith.json", {"where": "local"})
            if self.e.services.models is None:
                return self.not_built("L2", "Your choice is saved: Toolsmith will use the brain on this computer. "
                                            "Picking the brain is not built yet, so this step waits for it.")
            if self.e._progress()["steps"].get("brain", {}).get("state") != "done":
                return {"status": "waiting", "lines": ["Your choice is saved: Toolsmith will use the brain on "
                                                       "this computer, once you pick one in the brain step."]}
            return {"status": "done", "lines": ["Toolsmith uses the brain on this computer."]}
        p = self._provider(known) or {}
        consent = p.get("consent", "")
        record = {"where": "cloud", "provider": p.get("id"), "model": p.get("default_model"),
                  "cost_cap_usd": known.get("cost_cap", p.get("cost_cap_usd", 5)),
                  "consent": {"sha": p.get("consent_sha") or hashlib.sha256(consent.encode("utf-8")).hexdigest(),
                              "at": _now(), "by": "owner"}}
        if callable(choose):                           # lane L6 checks the sha she said yes to, the model, the cap
            got = choose({"where": "cloud", "provider": record["provider"], "model": record["model"],
                          "consent_sha": record["consent"]["sha"], "cost_cap_usd": record["cost_cap_usd"]}) or {}
            if not got.get("ok"):
                return {"status": "waiting", "lines": [got.get("reason") or "Toolsmith did not take that choice."]}
        else:
            self.e.write_setting("toolsmith.json", record)
        out = [f"Toolsmith uses {p.get('company', 'the cloud model')} for builds, up to "
               f"${record['cost_cap_usd']:g} per build."]
        if not known.get("_key_kept"):
            out.append("Toolsmith will wait for your key before its first run. Open Settings to add it, "
                       "or let Toolsmith use this computer.")
        return {"status": "done", "lines": out}


def ask_ideas(ask: Callable[[str, str], Any], goal: str) -> dict:
    """One small JSON ask to the local brain: up to 3 names and the look that fits *goal*. Only names that pass
    the name rules (``bot.check_name``, at most 24 characters) and a known look id are kept."""
    looks = "; ".join(f"{t['id']} ({t['line']})" for t in bot_mod.THEMES)
    got = ask(IDEAS_SYSTEM.format(looks=looks), f"The owner's goal: {goal[:600]}")
    raw = got.get("names") if isinstance(got, dict) else got if isinstance(got, list) else []
    names: list[str] = []
    for n in raw if isinstance(raw, list) else []:
        try:
            name = bot_mod.check_name(n)
        except ValueError:
            continue
        if len(name) <= 24 and name.lower() not in [x.lower() for x in names]:
            names.append(name)
    theme = got.get("theme") if isinstance(got, dict) else None
    return {"names": names[:3], "theme": theme if theme in bot_mod.theme_ids() else None}


class _IdeasStep(StepHooks):
    """The name and look steps: both use the brain's one ask for ideas (``Engine.ideas``), and wait for it a
    while (``working``) the first time; past :data:`IDEAS_WAIT_S` they go on with the defaults."""

    def before(self, step, known):
        task = self.e.ideas_task()
        if task is None or task.done or time.monotonic() - task.started > IDEAS_WAIT_S:
            return None
        return {"status": "working", "poll_s": 2.0, "lines": task.progress(L.IDEAS_WORKING, L.IDEAS_STILL)}


class NameStep(_IdeasStep):
    def _names(self) -> list[str]:
        return list(self.e.ideas()["names"]) or [bot_mod.DEFAULT_NAME]

    def view_lines(self, step, known):
        return [L.NAMES_FROM_GOAL] if self.e.ideas()["names"] else [L.NAMES_DEFAULT]

    def options(self, field, known):
        if field["id"] != "name":
            return None
        return [{"value": n, "label": n} for n in self._names()]

    def default(self, field, known):
        return self._names()[0] if field["id"] == "name" else None

    def check(self, field, value, known):
        if field["id"] == "name":
            try:
                bot_mod.check_name(value)
            except ValueError as exc:
                raise Ask(str(exc)) from None

    def finish(self, step, known, renderer):
        try:
            got = bot_mod.save(self.e.state, name=known.get("name"), by="wizard")
        except ValueError as exc:
            return {"status": "waiting", "lines": [str(exc)]}
        return {"status": "done", "lines": [f"Your bot is called {got['name']}."]}


class LookStep(_IdeasStep):
    def _picked(self) -> str | None:
        return self.e.ideas()["theme"]

    def view_lines(self, step, known):
        picked = self._picked()
        if not picked or picked == bot_mod.DEFAULT_THEME:
            return []
        return [L.LOOK_PICKED.format(name=bot_mod.theme_name(picked), default=bot_mod.theme_name(bot_mod.DEFAULT_THEME))]

    def options(self, field, known):
        if field["id"] != "theme":
            return None
        return [{"value": t["id"], "label": t["name"], "note": t["line"], "mode": t["mode"]}
                for t in bot_mod.THEMES]

    def default(self, field, known):
        # D90: Clean is the default for a new bot; the brain's pick is only a suggestion (view_lines).
        return bot_mod.DEFAULT_THEME if field["id"] == "theme" else None

    def finish(self, step, known, renderer):
        try:
            got = bot_mod.save(self.e.state, theme=known.get("theme"), by="wizard")
        except ValueError as exc:
            return {"status": "waiting", "lines": [str(exc)]}
        return {"status": "done", "lines": [L.LOOK_SAVED.format(name=bot_mod.theme_name(got["theme"]))]}


def brain_line(state: dict) -> str | None:
    """The 03 1.4 line for a brain state, or None when it is ready."""
    name = state.get("state")
    if name == "ready":
        return None
    if name == "not_downloaded":
        if state.get("done_gb") is not None and state.get("size_gb"):
            return L.BRAIN["not_downloaded"].format(done=f"{state['done_gb']:g}", size=f"{state['size_gb']:g}")
        return L.BRAIN["not_downloaded_plain"]
    if name == "loading":
        return L.BRAIN["loading"]
    if name == "timed_out":
        return L.BRAIN["timed_out"]
    return L.BRAIN["error"].format(reason=state.get("reason") or "the reason was not given")


class GoalStep(StepHooks):
    def _state(self) -> dict:
        models = self.e.services.models
        name = (self.e.read_setting("model.json") or {}).get("name")
        if models is None or not name:
            return {"state": "error", "reason": L.NO_BRAIN_SET}
        try:
            return dict(models.state(name) or {"state": "error", "reason": "the model slot gave no state"})
        except Exception as exc:                       # the real reason, never a guess
            return {"state": "error", "reason": str(exc) or exc.__class__.__name__}

    def view_lines(self, step, known):
        line = brain_line(self._state())
        return [line] if line else []

    def default(self, field, known):
        return self.e.preset.get("goal") if field["id"] == "goal" else None

    def _interpret(self, goal: str) -> tuple[dict, dict]:
        state = self._state()
        waited = 0.0
        while state.get("state") == "loading" and waited < self.e.brain_wait_s:
            self.e.sleep(2.0)
            waited += 2.0
            state = self._state()
        return state, self.e.services.toolsmith.interpret(goal, state.get("state") == "ready") or {}

    def finish(self, step, known, renderer):
        ts = self.e.services.toolsmith
        if ts is None:
            return self.not_built("L6", "Toolsmith is not part of this Core, so your goal can't be turned into "
                                        "jobs yet. Your goal is kept.")
        key = "goal:" + str(known["goal"])
        task = self.e.task(key, lambda: self._interpret(known["goal"]))
        if not task.done:                          # the brain's first answer can take minutes: say so
            return {"status": "working", "lines": task.progress(L.GOAL_WORKING, L.GOAL_STILL), "poll_s": 2.0}
        self.e.drop_task(key)
        if task.error is not None:
            raise task.error
        state, result = task.result
        if result.get("build"):
            self.e.set_build(result["build"])
        out = []
        line = brain_line({"state": result["brain"], **result}) if result.get("brain") else brain_line(state)
        if line:
            out.append(line)
        jobs = result.get("jobs") or []
        for i, job in enumerate(jobs, 1):
            ask = f" (try: \"{job['ask']}\")" if job.get("ask") else ""
            out.append(f"Job {i}: {job['name']}{ask}")
        if jobs and result.get("left_out_line"):  # D84: what is not a job yet, and how to add it
            out.append(str(result["left_out_line"]))
        if not jobs:
            out.append("No jobs yet. Say a little more about what the bot should do.")
            return {"status": "waiting", "lines": out}
        return {"status": "done", "lines": out, "jobs": jobs}


class _BuildStep(StepHooks):
    def _build(self) -> str | None:
        build = self.e.build_id()
        ts = self.e.services.toolsmith
        if build is None and ts is not None and self.e.preset.get("goal"):
            result = ts.interpret(self.e.preset["goal"], False) or {}
            build = result.get("build")
            if build:
                self.e.set_build(build)
        return build

    def before(self, step, known):
        if self.e.services.toolsmith is None:
            return self.not_built("L6", "Toolsmith is not part of this Core, so this step waits for it.")
        if self._build() is None:
            return {"status": "waiting", "lines": ["There is no goal yet. Answer the goal step first."]}
        return None


class PlanStep(_BuildStep):
    def __init__(self, engine: "Engine") -> None:
        super().__init__(engine)
        self.shown: dict[str, str] = {}
        self.vault_told: set[str] = set()

    def live(self, step, incoming, renderer):
        """While Toolsmith plans (minutes: the search places, READMEs, the brain's order), show what it does now."""
        ts = self.e.services.toolsmith
        start = getattr(ts, "plan_start", None)
        if incoming or not callable(start):
            return None
        build = self.e.build_id()
        if build is None:
            return None
        got = start(build) or {}
        if got.get("ready", True):
            return None
        if got.get("vault"):                           # D132: a plan made with no cloud key costs the whole build
            first = build not in self.vault_told
            self.vault_told.add(build)
            from .terminal import vault_wait_line
            return {"status": "working", "lines": [L.PLAN_NEEDS_KEY, vault_wait_line()] if first else [], "poll_s": 2.0}
        now = str(got.get("now") or "")
        lines = []
        if build not in self.shown:
            lines.append(L.PLAN_WORKING)
        if now and now != self.shown.get(build):
            lines.append(now)
        self.shown[build] = now
        return {"status": "working", "lines": lines, "poll_s": 2.0}

    def view_lines(self, step, known):
        plan = self.e.services.toolsmith.plan(self._build()) or {}
        if plan.get("build"):
            self.e.set_build(plan["build"])
        out = [plan["summary"]] if plan.get("summary") else []
        line = brain_line(GoalStep(self.e)._state())
        if line:
            out.append(line)
        return out + list(plan.get("items") or [])

    def check(self, field, value, known):
        """A pasted project must be a GitHub project address (any common form); else it is asked again."""
        if field["id"] == "project" and str(value or "").strip():
            from ..zone3.toolsmith.find import github_of
            if not github_of(value):
                from ..wiring import NOT_GITHUB_LINE
                raise Ask(NOT_GITHUB_LINE)

    def finish(self, step, known, renderer):
        ts = self.e.services.toolsmith
        if known.get("project"):
            from ..zone3.toolsmith.find import github_of
            full = github_of(known["project"])
            if not full:                               # never reached past check(); said plainly all the same
                from ..wiring import NOT_GITHUB_LINE
                return {"status": "waiting", "lines": [NOT_GITHUB_LINE]}
            before = ts.plan(self._build()) or {}
            shown = set([before.get("summary")] + list(before.get("items") or []))   # on the opening screen
            plan = ts.plan(self._build(), f"https://github.com/{full}") or {}
            if plan.get("build"):
                self.e.set_build(plan["build"])
            if not plan.get("summary"):
                return {"status": "waiting", "lines": list(plan.get("items") or [])}
            new = [x for x in [plan["summary"]] + list(plan.get("items") or []) if x not in shown]
            return {"status": "done", "lines": [f"Toolsmith tries github.com/{full} only."] + new}
        plan = ts.plan(self._build()) or {}
        if not plan.get("summary"):
            again = getattr(ts, "again", None)
            if callable(again):
                self.e.set_build(again(self._build()))
            return {"status": "waiting", "lines": []}
        return {"status": "done", "lines": []}


class YesStep(_BuildStep):
    """Step 8: the ONE Yes. When the plan has offers (an installed app one brain ask picked, lane I4b), she
    may tick them first (``use``); nothing is ticked unless she picks it."""

    def _offers(self) -> list[dict]:
        fn = getattr(self.e.services.toolsmith, "offers", None)
        build = self.e.build_id()
        try:
            return list(fn(build) or []) if callable(fn) and build else []
        except Exception:  # noqa: BLE001 - no offers to show: nothing is ticked
            return []

    def wanted(self, field, known):
        return field["id"] != "use" or bool(self._offers())

    def options(self, field, known):
        if field["id"] != "use":
            return None
        offers = self._offers()
        opts = [{"value": "none", "label": "Tick nothing: Toolsmith searches for every job",
                 "note": "Recommended: nothing is used unless you pick it"}]
        for o in offers:
            if o.get("model"):                         # a coding or picture model (lane I17)
                said = str(o.get("line") or o.get("name") or "")
                opts.append({"value": str(o["job"]), "label": said.rsplit(" Tick to ", 1)[0], "note": L.MODEL_TICK_NOTE})
                continue
            does = str(o.get("does") or "")            # what its tool does, never the tool's name (lane I8a)
            job = o.get("job_name") or "its job"
            if o.get("store"):                         # a PuffPAI store app, not on her bot yet (lane I10c)
                opts.append({"value": str(o["job"]), "label": f"Add {o['name']} from the PuffPAI store for {job}",
                             "note": (f"it can {does}. " if does else "") + L.STORE_TICK_NOTE})
                continue
            opts.append({"value": str(o["job"]), "label": f"Use {o['name']} for {job}",
                         "note": f"it would {does} instead of searching" if does else "instead of searching"})
        if len(offers) > 1:
            opts.append({"value": "all", "label": "Use all of them", "note": "each instead of searching"})
        return opts

    def default(self, field, known):
        return "none" if field["id"] == "use" else None

    def view_lines(self, step, known):
        # The plan's one sentence and its list are lines, not the question: a window that already shows them
        # under "The plan" (the terminal) does not print them again; the question is the step's own label.
        plan = self.e.services.toolsmith.plan(self._build()) or {}
        return ([plan["summary"]] if plan.get("summary") else []) + list(plan.get("items") or [])

    def finish(self, step, known, renderer):
        if not known.get("yes"):
            return {"status": "waiting", "lines": ["Nothing is built until you say Yes. Change the goal or the plan, "
                                                   "or come back to this step later."]}
        picked = str(known.get("use") or "none")
        offers = self._offers()
        use = ([o["job"] for o in offers] if picked == "all" else
               [o["job"] for o in offers if str(o["job"]) == picked])
        ts = self.e.services.toolsmith
        result = (ts.yes(self._build(), use) if use else ts.yes(self._build())) or {}
        if not result.get("ok"):
            return {"status": "waiting", "lines": [result.get("reason") or "The Yes was not taken."]}
        said = ["Yes given. Toolsmith starts the build."]
        for o in offers:
            if o["job"] in use and o.get("model") and o.get("installed"):
                said.append(f"{o['name']} is set for that job first, as you ticked.")
            elif o["job"] in use and o.get("model"):
                said.append(f"{o['name']} is downloaded first, as you ticked. Its own Yes card comes next.")
            elif o["job"] in use and o.get("store"):
                said.append(f"{o.get('job_name') or 'That job'}: {o['name']} is added from the PuffPAI store first, "
                            "as you ticked. Its own Yes card comes next.")
            elif o["job"] in use:
                said.append(f"{o.get('job_name') or 'That job'}: Toolsmith uses {o['name']}, as you ticked.")
        return {"status": "done", "lines": said}


TURN_OPTIONS = [{"value": "done", "label": "Done", "note": "I did it"},
                {"value": "skip", "label": "Skip it", "note": "Toolsmith tries the next app"},
                {"value": "later", "label": "Not yet", "note": "Ask me again in a moment"}]


def pick_answer(raw, choices: list[dict], yes_ticks_all: bool = False) -> tuple[bool | None, list[str]]:
    """Her answer to a hold card: ``(yes, refs)``. Numbers like "1 3": those; "all": every one; "none", no or
    nothing: No. A bare yes ticks nothing, so it tries none (``(True, [])``), unless the card has one app and
    its Yes field names it (*yes_ticks_all*). ``(None, [])`` when it can't be read (asked again)."""
    every = [c["ref"] for c in choices]
    if raw is True:
        return True, every if yes_ticks_all else []
    if raw is False:
        return False, []
    text = str(raw or "").strip().lower()
    if text in ("", "none", "no", "n", "0", "false"):
        return False, []
    if text == "all":
        return True, every
    if text in ("yes", "y", "true"):
        return True, every if yes_ticks_all else []
    picked = []
    for word in text.replace(",", " ").split():
        if not word.isdigit() or not 1 <= int(word) <= len(choices):
            return None, []
        ref = choices[int(word) - 1]["ref"]
        if ref not in picked:
            picked.append(ref)
    return (True, picked) if picked else (False, [])


class BuildStep(_BuildStep):
    """Step 9, live: start the build once, then show each job's new lines, her "Your turn" steps and any
    Yes card for what was not on her plan, until every job has ended."""

    POLL_S = 2.0
    STILL_EVERY_S = 300.0                                 # a quiet job's "Still working" line in the terminal (D113)

    def __init__(self, engine: "Engine") -> None:
        super().__init__(engine)
        self.started: set[str] = set()
        # Per renderer and job (D113): the terminal and the Setup page share this engine, and each is owed every line.
        self.seen: dict[tuple[str, str], int] = {}
        self.said: dict[str, str] = {}
        self.now_seen: dict[tuple[str, str], str] = {}
        self.quiet_from: dict[str, float] = {}            # the terminal: when a job last had something to say
        self.vault_told = 0.0
        self.vault_said = False                           # the terminal was told about this wait for the vault
        self.clock = time.monotonic
        self.choices: dict[str, list[dict]] = {}          # a hold card's apps, by card id (lane I4b)

    def options(self, field, known):
        return [dict(o) for o in TURN_OPTIONS] if field["id"] == "turn" else None

    def live(self, step, incoming, renderer):
        stop = self.before(step, {})
        if stop is not None:
            return stop
        ts = self.e.services.toolsmith
        build = self._build()
        if not callable(getattr(ts, "progress", None)):
            return None                                # a Toolsmith with no live view: the plain finish
        lines: list[str] = []
        if build not in self.started:
            # The store apps ticked on the plan are the Core's to add (lane I16): their Yes cards come below as
            # this build's cards, and the Core starts the build once each has a final answer.
            result = ts.start(build) or {}
            if not result.get("ok"):
                return {"status": "waiting", "lines": lines + [result.get("reason") or "The build did not start."]}
            self.started.add(build)
            if result.get("store"):
                lines.append(L.STORE_FIRST)
            elif not result.get("already"):
                lines.append("Toolsmith is building. Each job says what it does below, and on the Desktop.")
        error = None
        if "turn" in incoming and incoming["turn"] in ("done", "skip"):
            said = ts.answer_step(build, str(self.said.get("turn") or ""), incoming["turn"])
            if said:
                error = said
        if "card" in incoming:
            choices = self.choices.get(str(self.said.get("card") or ""))
            if choices:
                one = len(choices) == 1                   # its own Yes field names that one app: Yes ticks it
                answer, picks = pick_answer(incoming["card"], choices, yes_ticks_all=one)
                said = ts.answer_card(build, str(self.said.get("card") or ""), answer, picks) if answer is not None \
                    else "Type the numbers of the apps to try (like 1 2), all, or none."
                if answer and not picks and not said:
                    lines.append("You said Yes but ticked none, so Toolsmith tries none of them.")
            else:
                answer = str(incoming["card"]).strip().lower() in ("yes", "y", "true", "1") or incoming["card"] is True
                said = ts.answer_card(build, str(self.said.get("card") or ""), answer)
            if said:
                error = said
        got = ts.progress(build) or {}
        for job in got.get("jobs") or []:
            key = (renderer, job["id"])
            new = (job.get("lines") or [])[self.seen.get(key, 0):]
            self.seen[key] = len(job.get("lines") or [])
            lines += [f"{job['name']}: {x}" for x in new]
            now = str(job.get("now") or "")
            changed = bool(now and now != self.now_seen.get(key))
            if changed:                                           # what it does right now, when it changed
                lines.append(f"{job['name']}: {now}")
            self.now_seen[key] = now
            if renderer == "terminal":
                lines += self._still(job, bool(new) or changed)
        extra = self._alive(got, renderer, lines)
        card = next((c for c in got.get("cards") or [] if not c.get("floor")), None)
        if card is not None:
            self.said["card"] = card["consent_id"]
            lines += [card["title"]] + list(card.get("lines") or [])
            choices = list(card.get("choices") or [])
            yes_means = str(card.get("yes_means") or "")
            if len(choices) > 1:
                self.choices[card["consent_id"]] = choices
                field = {"id": "card", "type": "text", "label": "Which ones may Toolsmith try?",
                         "help": f"{yes_means} Type their numbers (like 1 2), or all. Only the ones you type are "
                                 "tried; yes or nothing typed tries none."}
            else:
                if choices:
                    self.choices[card["consent_id"]] = choices
                help_ = (f"Yes tries {choices[0]['name']}. {yes_means}".strip() if choices else
                         yes_means or L.CARD_HELP.get(str(card.get("kind") or ""), L.CARD_HELP[""]))
                field = {"id": "card", "type": "confirm", "label": "Say Yes to this?", "help": help_}
            # poll_s and asked (D122): the same card can be answered on the Desktop, so the terminal keeps looking.
            out = {"status": "ask", "lines": lines, "field": self.e.resolve(step, field, {}, renderer),
                   "poll_s": self.POLL_S, "asked": card["consent_id"], **extra}
            return dict(out, error=error) if error else out
        turn = next(iter(got.get("steps") or []), None)
        if turn is not None and incoming.get("turn") != "later":
            self.said["turn"] = turn["id"]
            lines.append(f"Your turn: {turn.get('title')}")
            if turn.get("why"):
                lines.append(str(turn["why"]))
            if turn.get("text"):
                lines.append(f"Run this: {turn['text']}" if turn.get("kind") == "run_command" else str(turn["text"]))
            if turn.get("get_it"):
                lines.append(f"Get it here: {turn['get_it']}")
            field = {"id": "turn", "type": "choice", "label": "Did you do it?",
                     "help": "The same step is on your Checklist in the Desktop, with Copy."}
            out = {"status": "ask", "lines": lines, "field": self.e.resolve(step, field, {}, renderer),
                   "poll_s": self.POLL_S, "asked": turn["id"], **extra}
            return dict(out, error=error) if error else out
        if not got.get("finished"):
            out = {"status": "working", "lines": lines, "poll_s": self.POLL_S, **extra}
            return dict(out, error=error) if error else out
        lines.append(L.BUILD_ENDED)                     # each job's end is the Prove step's, said once there
        return {"status": "done", "lines": lines}

    def _still(self, job: dict, spoke: bool) -> list[str]:
        """The terminal only adds lines, so a job in a long step says it is alive every few minutes (D113)."""
        now = self.clock()
        if spoke or not str(job.get("status") or "").startswith("building") or job["id"] not in self.quiet_from:
            self.quiet_from[job["id"]] = now
            return []
        if now - self.quiet_from[job["id"]] < self.STILL_EVERY_S:
            return []
        self.quiet_from[job["id"]] = now
        return [L.still_line(job)]

    def _alive(self, got: dict, renderer: str, lines: list[str]) -> dict:
        """D113. The Setup page gets the state on every screen, and draws it in one place (``setupAlive`` in the
        Desktop's logic.js), so a reload still shows it. The terminal gets one line per wait for the vault, and it
        prints that line again after a new lock, though it never prints a line twice otherwise."""
        jobs = got.get("jobs") or []
        waiting = any(j.get("status") == "waiting_vault" for j in jobs)
        if renderer != "terminal":
            return {"alive": {"vault": waiting, "jobs": [{k: j.get(k) for k in ("name", "status", "now", "since")}
                                                         for j in jobs]}}
        said, self.vault_said = self.vault_said, waiting
        now = self.clock()
        if not waiting or (said and now - self.vault_told < self.STILL_EVERY_S):    # silence read as a hang (D132)
            return {}
        self.vault_told = now
        from .terminal import vault_wait_line
        line = vault_wait_line()
        lines.append(line)
        return {"repeat": [line]}

    def finish(self, step, known, renderer):
        result = self.e.services.toolsmith.start(self._build()) or {}
        if not result.get("ok"):
            return {"status": "waiting", "lines": [result.get("reason") or "The build did not start."]}
        return {"status": "done", "lines": ["Toolsmith is building. Each job shows what it is doing on the Desktop, "
                                            "and \"Your turn\" cards show in the Checklist."]}


class ProveStep(_BuildStep):
    def finish(self, step, known, renderer):
        out = L.prove_lines(self.e.services.toolsmith.results(self._build()) or [])
        return {"status": "done", "lines": out or ["No job has finished yet. The Desktop shows each one as it does."]}


class DoneStep(StepHooks):
    def finish(self, step, known, renderer):
        prog = self.e._progress()
        missing = [s["title"] for s in self.e.steps
                   if s.get("required") and prog["steps"].get(s["id"], {}).get("state") != "done"]
        if missing:
            return {"status": "waiting", "lines": [f"Not done yet: {', '.join(missing)}. The Desktop opens anyway, "
                                                   f"and Setup stays in its tray.", L.STORE_MORE]}
        prog["complete"] = True
        self.e._save(prog)
        # A summary: every job is listed again here, even where the Prove step's line read the same. The store
        # line takes the place of lane I8d's Apps step (lane I10c); who the bot is comes last (lane I12).
        who = getattr(self.e.services.toolsmith, "done_lines", None)
        from .terminal import chat_line            # D86: the chat in a terminal, one line, both renderers
        return {"status": "done", "lines": self._said() + [L.STORE_MORE, L.MODELS_LATER, chat_line()]
                + (list(who() or []) if callable(who) else []), "summary": True}

    def _said(self) -> list[str]:
        """What really happened, job by job (D36(c): never claim more than is true)."""
        build, ts = self.e.build_id(), self.e.services.toolsmith
        results: list = []
        if build and callable(getattr(ts, "results", None)):
            try:
                results = list(ts.results(build) or [])
            except Exception:  # noqa: BLE001 - never "set up" when the jobs could not be read
                return [L.DONE_UNREAD]
        if not results:
            return [L.DONE_PLAIN]
        jobs = [r for r in results if r.get("kind") != "knowledge"]     # the knowledge line is not a job
        reading = sum(1 for r in jobs if r.get("state") == "working" and r.get("library"))   # D123: no app yet
        working = sum(1 for r in jobs if r.get("state") == "working") - reading
        head = (L.DONE_ALL if working == len(jobs) else L.DONE_SOME if working
                else L.DONE_READING if reading == len(jobs) else L.DONE_NONE)
        return [head] + L.job_result_lines(results) + ([] if working == len(jobs) else [L.DONE_NEXT])
