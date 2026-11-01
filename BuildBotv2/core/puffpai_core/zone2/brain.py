"""The Brain: one turn through Concierge, the gate and the model slot (Zone 2).

    brain = Brain(registry, state)            # production parts by default
    brain.turn("put milk on the shopping list", emit=print)
    brain.sample_ask("freshtable.add_to_list", "put milk on the shopping list")   # puffpai check L2 item 12
    brain.package_chat(messages)              # POST /v1/brain/chat
    brain.toolsmith_turn(system, messages, tools)   # lane L6: one turn in the neutral shape (04 quick start 11)
    brain.ask_json(system, user)              # lane L6: a small JSON ask, or None
    brain.answer(question, context)           # lane L6: the brain's answer from a text it is given

Order of every turn:
1. X12: the runtime's address is this computer, or an exact home address the
   owner added (handles only there), else the turn is refused.
2. The gate (``zone2.gate``): no gate, no turn (fail closed).
3. Concierge picks the tools (budget from the model profile).
4. The loop runs; every tool result and error, whatever its shape, passes
   the gate before the model (``Gate.clean``, D50). The system prompt's
   Registry descriptions and every package tool's description and schema
   pass it too.
5. Right before the runtime, the floor key check runs once more over every
   message and tool spec (``gate.model_messages``): keys never reach it.
6. The conversation store keeps the locked form only. The reply goes back as
   the brain said it.

Parts other lanes fill in are passed in, or found when the turn runs:
``review(call) -> verdict`` (Drawbridge's review of EVERY package tool call,
``wiring.tool_review``: in scope it runs with no card; outside it, a Yes card
for that one call, and the turn waits for her answer), ``consent(question) ->
True | False | None`` (the Yes for a core tool's own question, and for package
tools when no review is wired; with none, a ``changes`` tool does not run),
Steward's ``token(id)`` and ``start(id)``, lane L5's ``checklist`` (``checklist_add``) and
``search(query, limit)`` (``search_knowledge``: lane L3's folders index, ``wiring.knowledge_search``).

A gate that is missing, raises or answers with something that is not a gate is
``BrainError("no_gate")``: the API answers 503 with a plain line (fail closed).
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from . import conversations, loop, persona
from . import settings as model_settings
from .concierge import Concierge
from .errors import BrainError
from .gate import NO_GATE, Gate, clean_value, model_messages, no_keys, production
from .tools import check_schema, make, shown_name

Consent = Callable[[dict], Optional[bool]]
Review = Callable[[dict], dict]

#: How much of one argument a Yes card shows.
SUMMARY_CHARS = 120
SUMMARY_ARGS = 10
_SECRET_ARG = re.compile(r"(?i)(pass(word|phrase)?|secret|token|api[_-]?key|^key$|_key$|credential|^pin$|cookie|auth)")


def arg_summary(args: dict, keep: Callable[[str], str]) -> list[str]:
    """The arguments of one call in plain lines for her Yes card: ``name: milk``. Every value is the gate's
    locked form (handles, never held values; keys held, D50); an argument named like a secret shows
    "(hidden)"; long values and file bytes are cut to a short note."""
    lines = []
    for k, v in list((args or {}).items())[:SUMMARY_ARGS]:
        name = keep(str(k))[:40]
        if _SECRET_ARG.search(str(k)):
            shown = "(hidden)"
        elif isinstance(v, str) and (str(k).endswith("_b64") or len(v) > 4 * SUMMARY_CHARS):
            shown = f"({len(v)} characters, not shown)"
        elif isinstance(v, (list, tuple)):
            parts = [keep(str(x))[:40] for x in list(v)[:5]]
            shown = ", ".join(parts) + (f" and {len(v) - 5} more" if len(v) > 5 else "")
        elif isinstance(v, dict):
            shown = f"({len(v)} fields)"
        else:
            shown = keep(str(v))
        shown = " ".join(shown.split())
        lines.append(f"{name}: {shown[:SUMMARY_CHARS] + (' ...' if len(shown) > SUMMARY_CHARS else '')}")
    if len(args or {}) > SUMMARY_ARGS:
        lines.append(f"and {len(args) - SUMMARY_ARGS} more")
    return lines or ["(no details)"]


def _guarded(prepare: Callable[[str, bool], tuple]) -> Callable[[str, bool], tuple]:
    """The gate's ``prepare``: any failure, or an answer that is not ``(text, handles)``, is no gate."""
    def run(text: str, vault_open: bool) -> tuple:
        try:
            got = prepare(text, vault_open)
        except BrainError:
            raise
        except Exception:  # noqa: BLE001 - a gate that breaks is no gate (fail closed)
            raise BrainError(NO_GATE, "no_gate") from None
        if not isinstance(got, tuple) or len(got) != 2 or not isinstance(got[0], str):
            raise BrainError(NO_GATE, "no_gate")
        return got
    return run


class Brain:
    def __init__(self, registry: Any, state: Path, *, runtime: Any = None,
                 gate: Optional[Callable[[], Gate]] = None, consent: Optional[Consent] = None,
                 review: Optional[Review] = None,
                 token: Optional[Callable[[str], Optional[str]]] = None,
                 start: Optional[Callable[[str], dict]] = None,
                 manifest: Optional[Callable[[str], Optional[dict]]] = None,
                 folder: Optional[Callable[[str], Optional[Path]]] = None,
                 targets: Optional[Callable[[str], Any]] = None, browser: bool = True,
                 checklist: Any = None, search: Optional[Callable[[str, int], Any]] = None,
                 toolsmith: Optional[Callable[[str, dict], Any]] = None, pinned: Any = None, outside: Any = None,
                 reaches: Optional[Callable[[str], bool]] = None,
                 audit: Optional[Callable[[dict], None]] = None, safe_mode: Optional[Callable[[], bool]] = None) -> None:
        self.registry = registry
        self.reaches = reaches                  # has she allowed this package an address? (D110; D140's exception)
        self.audit = audit                      # her own programs: every run is logged (D16)
        self.safe_mode = safe_mode              # chat's commands close in safe mode, like the Developer shell
        self.checklist, self.search = checklist, search
        self.pinned = pinned                    # read_pinned (``wiring.pinned_documents``, D125)
        self.outside = outside                  # the tool servers she added (``tool_servers.ToolServers``, D137)
        self.toolsmith = toolsmith              # ask_toolsmith in chat (``wiring.chat_toolsmith``)
        self.state = Path(state)
        self._runtime = runtime
        self._gate = gate or production
        self.consent = consent
        self.review = review
        self.token, self.start, self.manifest, self.targets, self.browser = token, start, manifest, targets, browser
        self.folder = folder                     # where the Core installed a package (its stdio MCP runs there)
        self.store = conversations.ConversationStore(self.state / "chat")
        self._lock = threading.Lock()

    # -- parts ---------------------------------------------------------------
    def settings(self, role: str = "chat") -> model_settings.ModelSettings:
        """The model settings for *role* (lane I17): chat's brain, or the model set for code or pictures (chat's
        when none is set), each with its own profile (think, context, JSON asks)."""
        if role == "chat":
            return model_settings.load(self.state)
        from . import roles
        return roles.settings_for(self.state, role)

    def runtime(self, s: Optional[model_settings.ModelSettings] = None) -> Any:
        if self._runtime is not None:
            return self._runtime
        from . import models
        return models.runtime(self.state)       # one adapter per address, so its states are shared

    def _checked(self, role: str = "chat") -> tuple[model_settings.ModelSettings, Gate, bool]:
        s = self.settings(role)
        where = s.where()
        if where == "refused":
            raise BrainError(model_settings.NOT_LOCAL, "not_local")
        try:
            gate = self._gate()
        except BrainError:
            raise
        except Exception:  # noqa: BLE001 - a gate that can't be made is no gate (fail closed)
            raise BrainError(NO_GATE, "no_gate") from None
        if gate is None or not callable(getattr(gate, "prepare", None)):
            raise BrainError(NO_GATE, "no_gate")
        gate = Gate(prepare=_guarded(gate.prepare), vault_open=getattr(gate, "vault_open", None) or (lambda: False))
        return s, gate, where == "local"

    def concierge(self, gate: Gate) -> Concierge:
        return Concierge(self.registry, self.state, keep=gate.keep, manifest=self.manifest, folder=self.folder,
                         token=self.token,
                         start=self.start, targets=self.targets, browser=self.browser,
                         checklist=self.checklist, search=self.search, toolsmith=self.toolsmith, pinned=self.pinned,
                         outside=self.outside, real_args=gate.with_real_values, reaches=self.reaches)

    def _ask_model(self, s: model_settings.ModelSettings):
        rt = self.runtime(s)
        options = {"num_ctx": s.context_size()}

        def ask(messages: list, specs: Optional[list], on_token: Optional[Callable[[str], None]]) -> dict:
            messages, specs = model_messages(messages, specs)      # the last floor check (X6, D50)
            return rt.chat(messages, model=s.model, tools=specs, think=s.think_in_chat(), options=options,
                           on_token=on_token)
        return ask

    def _system(self, concierge: Concierge, gate: Gate) -> dict:
        # Registry descriptions are a package's words: the locked form, keys held (D50).
        return {"role": "system", "content": loop.TOOL_NOTE + "\n\n" + gate.keep(concierge.prompt_block())}

    def _chat_system(self, concierge: Concierge, gate: Gate, real: bool, opening: str,
                     personal: bool = False, skills_text: str = "") -> dict:
        """The chat's system message (lane I12): who the bot is and her own notes on top, then the installed
        list, then the tool and safety rules, last, and they win. Each of her texts passes the gate first (the
        real values only for this computer's brain while the vault is open, D25). With no persona and no notes
        it is :meth:`_system`, unchanged."""
        from .. import you
        mine = you.for_brain(self.state, real) if personal else {}
        role = persona.in_use(self.state)

        def gated(text: str) -> str:
            return gate.for_model(text, real_values=real)[0] if text else ""
        content = persona.system_text(gate.keep(concierge.prompt_block()), loop.TOOL_NOTE, role=gated(role),
                                      call_me=gated(mine.get("call_me", "")), about=gated(mine.get("about", "")),
                                      opening=gated(opening), skills=skills_text)
        return {"role": "system", "content": content} if content else self._system(concierge, gate)

    # -- one chat turn ---------------------------------------------------------
    def turn(self, text: str, *, conversation: str = conversations.DEFAULT,
             emit: Optional[Callable[[dict], None]] = None, cancelled: Optional[threading.Event] = None,
             personal: bool = False) -> dict:
        """``{"text", "empty", "notice", "changed", "used", "steps"}``. Raises BrainError.

        ``personal``: the caller is her paired owner device, so her "You" notes may go in (lane I12). Any
        other caller (an owner API key, a routine) gets none."""
        emit = emit or (lambda _e: None)
        s, gate, real = self._checked()
        model_text, kept_text, _handles = gate.for_model(text, real_values=real)
        concierge = self.concierge(gate)
        history = [{"role": m["role"], "content": m["content"]}
                   for m in self.store.load(conversation) if m.get("role") in ("user", "assistant")]
        # The first chat since who the bot is was written opens with its first question (lane I12).
        opening = persona.first_question_due(self.state)
        # WritersBot: an installed skill the turn needs goes in the system message (zone2.skills); the owner's
        # recent messages keep it on. The locked history is what the model sees, so it is matched on too.
        from . import skills as _skills
        try:
            recent = [m["content"] for m in history if m.get("role") == "user"]
            skills_text = _skills.block(self.state, concierge.rows(), model_text, recent)
        except Exception:  # noqa: BLE001 - a skill that cannot be read is no reason to lose the turn
            skills_text = ""
        messages = [self._chat_system(concierge, gate, real, opening, personal, skills_text), *history,
                    {"role": "user", "content": model_text}]
        table = concierge.table_for(kept_text, s.tools_allowed(), s.pinned)

        def ask(question: dict) -> Optional[bool]:
            if "notify" not in question:           # with it, the ask comes once the card has its id (run_command)
                emit({"type": "ask", "question": question})
            answer = self.consent(question)
            emit({"type": "answered", "yes": answer is True})
            return answer

        ctx = {"ask": ask if self.consent is not None else None, "cancelled": cancelled,
               "review": self._review_for(gate, emit), "conversation": conversation, "real": real,
               "keep": gate.keep,      # run_command: what its Yes card may show (X6)
               "audit": self.audit, "safe_mode": self.safe_mode,
               "her_device": personal is True,   # Always allow skips her Yes only for her own paired device
               # ask_toolsmith: its plan card shown in this chat, and its result said here later.
               "notify": lambda card: emit({"type": "ask", "question": card}),
               "say_later": lambda words: self.store.append(conversation, "assistant", gate.keep(str(words)))}
        got = loop.run(messages, table=table, ask_model=self._ask_model(s),
                       to_model=lambda v: gate.clean(v, for_model=True, real_values=real),
                       emit=emit, ctx=ctx, context=s.context_size())
        if opening:
            # Asked whatever the model did with the note: added after its words, never in place of them.
            if persona.plain_said(opening) not in persona.plain_said(got["text"]):
                got["text"] = (got["text"] + "\n\n" + opening) if got["text"] else opening
                got["empty"] = False
            persona.mark_opened(self.state)
        self.store.append(conversation, "user", kept_text)
        self.store.append(conversation, "assistant", gate.keep(got["text"]),
                          changed=[c["tool"] for c in got["changed"]])
        out = {"text": got["text"], "empty": got["empty"], "notice": loop.NO_ANSWER if got["empty"] else None,
               "changed": got["changed"], "used": got["used"], "steps": got["steps"]}
        if opening:
            out["opening"] = opening
        emit({"type": "done", **out})
        return out

    def _review_for(self, gate: Gate, emit: Callable[[dict], None]) -> Optional[Callable[[dict], dict]]:
        """Drawbridge's review for this turn: the call's arguments in the locked form, its plain summary
        for a Yes card, and the ``ask`` / ``answered`` events when her Yes was needed."""
        if self.review is None:
            return None

        def review(call: dict) -> dict:
            locked = clean_value(call.get("args") or {}, gate.keep)
            call = dict(call, args=locked, summary=arg_summary(locked, gate.keep),
                        notify=lambda card: emit({"type": "ask", "question": card}))
            got = self.review(call)
            if isinstance(got, dict) and got.get("asked"):
                emit({"type": "answered", "yes": got.get("run") is True})
            return got
        return review

    # -- puffpai check Level 2 item 12 -------------------------------------------
    def sample_ask(self, tool: str, ask: str) -> dict:
        """Ask the local model *ask* with *tool*'s package loaded; did it call *tool*?

        Nothing runs: tool calls are recorded only (a dry run), so a ``changes``
        tool changes nothing during a check. Nothing is saved.
        ``{"ok", "tool", "called": [shown names], "text", "reason"}``.
        """
        tool = shown_name(tool)
        s, gate, real = self._checked()
        model_text, kept_text, _h = gate.for_model(ask, real_values=real)
        concierge = self.concierge(gate)
        table = concierge.table_for(kept_text, s.tools_allowed(), [tool])
        if "." in tool and table.find(tool) is None:
            try:
                for t in concierge.package_tools(tool.split(".", 1)[0]):
                    table.add(t)
            except Exception as exc:  # noqa: BLE001 - the reason is the answer
                return {"ok": False, "tool": tool, "called": [], "text": "",
                        "reason": getattr(exc, "message", str(exc))}
        if table.find(tool) is None:
            return {"ok": False, "tool": tool, "called": [], "text": "", "reason": f"{tool} is not a tool here."}
        messages = [self._system(concierge, gate), {"role": "user", "content": model_text}]
        got = loop.run(messages, table=table, ask_model=self._ask_model(s),
                       to_model=lambda v: gate.clean(v, for_model=True, real_values=real), ctx={}, max_steps=1,
                       context=s.context_size(), dry_run=True)
        called = [c["tool"] for c in got["calls"]]
        ok = tool in called
        reason = "" if ok else (f"The brain called {', '.join(called)} instead." if called
                                else "The brain answered in words and called no tool.")
        return {"ok": ok, "tool": tool, "called": called, "text": got["text"], "reason": reason}

    # -- does an installed package fit a job? (Toolsmith's reuse, 03 Part 2 question 2) -------
    def picks(self, package: str, ask: str, store: list[dict] | None = None) -> dict:
        """The same test as ``puffpai check`` item 12, with the Concierge's normal tool picking: *ask* (a job's
        own words) goes to the local model with the tools a chat turn would get. With *package*: does it call
        one of that package's tools? With ``""``: whose tool does it call? A dry run: nothing runs, nothing
        is saved. ``{"ok", "package", "called": [shown names], "reason"}``. Raises BrainError when it can't be
        asked.

        *store* (lane I10c): the PuffPAI store's apps, not installed (``packages.store_apps.store_apps``: the built-in
        ones and the official store's verified rows, never another row's outside text, D96). Their
        tools join the table as listed in their manifests or the row (name and description through the gate, no schema,
        never callable), and the answer is whose store app's tool the brain called."""
        s, gate, real = self._checked()
        model_text, kept_text, _h = gate.for_model(ask, real_values=real)
        concierge = self.concierge(gate)
        table = concierge.table_for(kept_text, s.tools_allowed())
        shelf: set[str] = set()
        for app in store or []:
            for a in app.get("abilities") or []:
                tool, desc = str(a.get("tool") or ""), str(a.get("description") or "")
                try:
                    table.add(make(tool, gate.keep(desc or f"{tool.split('.', 1)[-1]} from {app.get('name')}"),
                                   {"type": "object", "properties": {}}, _not_installed, package=str(app.get("id"))))
                except ValueError:
                    continue
                shelf.add(str(app.get("id")))
        messages = [self._system(concierge, gate), {"role": "user", "content": model_text}]
        got = loop.run(messages, table=table, ask_model=self._ask_model(s),
                       to_model=lambda v: gate.clean(v, for_model=True, real_values=real), ctx={}, max_steps=1,
                       context=s.context_size(), dry_run=True)
        called = [c["tool"] for c in got["calls"] if c.get("known")]
        owners = [c.split(".", 1)[0] for c in called if "." in c]      # package tools are <id>.<tool>
        if store is not None:
            owners = [o for o in owners if o in shelf]
        chosen = (package if package in owners else "") if package else (owners[0] if owners else "")
        whose = "its tools" if package else ("a store app's tools" if store is not None else "an installed app's tools")
        reason = "" if chosen else (f"the brain called {', '.join(called)} instead" if called
                                    else f"the brain called none of {whose}")
        return {"ok": bool(chosen), "package": chosen or package, "called": called, "reason": reason}

    # -- POST /v1/brain/chat (packages) -------------------------------------------
    def can_read_images(self) -> bool:
        """The picture model (lane I17: the ``vision`` role, else chat's brain) reads pictures."""
        return bool(self.settings("vision").profile().get("vision", False))

    def package_as_written(self, package_id: str | None) -> bool:
        """WritersBot: True when the calling package's installed manifest says ``needs.brain.as_written``. The owner
        saw that line on its Yes card. Fail closed: no id, no manifest, no flag means the gate runs as ever."""
        if not package_id or self.manifest is None:
            return False
        try:
            got = self.manifest(str(package_id))
        except Exception:  # noqa: BLE001 - a reader that breaks means no manifest
            return False
        needs = got.get("needs") if isinstance(got, dict) else None
        brain = needs.get("brain") if isinstance(needs, dict) else None
        return isinstance(brain, dict) and brain.get("as_written") is True

    def package_chat(self, messages: list[dict], images: list[str] | None = None, *,
                     as_written: bool = False) -> dict:
        """A package asks the local model: ``{"text", "usage": {prompt_tokens, completion_tokens}, "model"}``.

        The gate runs with the vault treated as locked: a package never gets a
        held value. ``images`` (base64) go with the last user message, only for
        a model whose profile says ``vision`` (the caller checks first).
        ``as_written`` (WritersBot, ``needs.brain.as_written``): the words go to the brain on this computer as they
        are and come back as they are; nothing is held or filed. Keys are still stripped either way.
        """
        s, gate, _real = self._checked("vision" if images else "chat")    # pictures go to the picture model
        if as_written and s.where() != "local":
            as_written = False                       # as written is for the brain on this computer only
        safe = []
        for m in messages:
            role = m.get("role") if m.get("role") in ("system", "user", "assistant") else "user"
            content = m.get("content")
            text = "" if content is None else content
            if as_written:
                safe.append({"role": role, "content": no_keys(str(text))})
            else:
                safe.append({"role": role, "content": gate.clean(text, for_model=True, real_values=False, limit=None)})
        if images:
            last = next((m for m in reversed(safe) if m["role"] == "user"), None)
            if last is None:
                safe.append(last := {"role": "user", "content": ""})
            last["images"] = list(images)
        safe, _none = model_messages(safe)                      # the last floor check (X6, D50)
        got = self.runtime(s).chat(safe, model=s.model, think=s.think(), options={"num_ctx": s.context_size()})
        usage = got.get("usage") or {"prompt_tokens": 0, "completion_tokens": 0}
        text = no_keys(str(got["text"])) if as_written else gate.keep(got["text"])
        return {"text": text, "usage": usage, "model": s.model}     # which local model answered (I19b)

    # -- /v1/chat/tool (pages and UIs) ---------------------------------------------
    def call_tool(self, tool: str, args: dict) -> dict:
        """One tool by name, with the same Yes as in chat. The whole answer (result
        of any shape, error text, names) is the locked form, in JSON types (D50)."""
        from .tools import call as call_one
        _s, gate, _real = self._checked()
        concierge = self.concierge(gate)
        table = concierge.table_for("", 0)
        tool = shown_name(tool)
        if "." in tool:
            for t in concierge.package_tools(tool.split(".", 1)[0]):
                table.add(t)
        ctx = {"ask": self.consent, "review": self._review_for(gate, lambda _e: None)}
        got = call_one(table, tool, args, ctx)
        return gate.clean(got, for_model=False)


    # -- for lane L6 (Toolsmith's model on this computer) --------------------------------
    # Toolsmith's words come from outside apps (READMEs, logs, pages): the gate runs with the vault
    # treated as locked (handles only, never a held value), keys never reach the model (D50), and the
    # answer goes back in the locked form. X12 holds: the runtime is this computer's.

    def _local(self, role: str = "chat") -> tuple[model_settings.ModelSettings, Gate]:
        s, gate, _real = self._checked(role)
        return s, gate

    @staticmethod
    def _in(gate: Gate, value: Any) -> str:
        return gate.clean("" if value is None else value, for_model=True, real_values=False, limit=None)

    def _ask_once(self, s: model_settings.ModelSettings, messages: list[dict], specs: Optional[list],
                  think: Optional[bool] = None) -> dict:
        messages, specs = model_messages(messages, specs)          # the last floor check (X6, D50)
        return self.runtime(s).chat(messages, model=s.model, tools=specs, think=s.think() if think is None else think,
                                    options={"num_ctx": s.context_size()})

    def toolsmith_turn(self, system: str, messages: list[dict], tools: list[dict]) -> dict:
        """One turn in the neutral shape (04 quick start 11, ``read_reply``'s output).

        In: ``system``; ``messages`` ``[{role, content: [blocks]}]`` with ``text``, ``tool_use {id, name,
        input}``, ``tool_result {tool_use_id, content, is_error}`` and ``opaque`` blocks (dropped here: only
        a cloud company needs them back); ``tools`` ``[{name, description, input_schema}]``.
        Out: ``{content: [blocks], stop: tool_use | end_turn | max_tokens, usage: {in, out, cache_read,
        cache_write}, error: None, retry_after_s: None}``. Raises BrainError when the brain can't answer.
        Toolsmith's turns write commands, files and code, so they use the ``code`` model when one is set
        (lane I17), else chat's."""
        s, gate = self._local("code")
        chat: list[dict] = [{"role": "system", "content": self._in(gate, system)}]
        names: dict[str, str] = {}
        for m in messages or []:
            if not isinstance(m, dict):
                continue
            role = "assistant" if m.get("role") == "assistant" else "user"
            blocks = m.get("content")
            blocks = [{"type": "text", "text": blocks}] if isinstance(blocks, str) else \
                [b for b in blocks or [] if isinstance(b, dict)]
            words = [str(b.get("text") or "") for b in blocks if b.get("type") == "text"]
            calls = [b for b in blocks if b.get("type") == "tool_use"]
            results = [b for b in blocks if b.get("type") == "tool_result"]
            for c in calls:
                names[str(c.get("id") or "")] = str(c.get("name") or "")
            if role == "assistant":
                msg: dict = {"role": "assistant", "content": self._in(gate, "\n".join(words))}
                if calls:
                    msg["tool_calls"] = [{"function": {"name": str(c.get("name") or ""),
                                                       "arguments": clean_value(c.get("input") or {}, gate.keep)}}
                                         for c in calls]
                chat.append(msg)
                continue
            for r in results:
                text = self._in(gate, r.get("content"))
                chat.append({"role": "tool", "content": ("Error: " + text) if r.get("is_error") else text,
                             "tool_name": names.get(str(r.get("tool_use_id") or ""), "")})
            if words or not results:
                chat.append({"role": "user", "content": self._in(gate, "\n".join(words))})
        specs = [{"type": "function", "function": {
            "name": str(t.get("name") or ""), "description": gate.keep(str(t.get("description") or "")),
            "parameters": check_schema(clean_value(t.get("input_schema") or {}, gate.keep))}}
            for t in tools or [] if isinstance(t, dict) and t.get("name")] or None
        got = self._ask_once(s, chat, specs)
        content: list[dict] = []
        if str(got.get("text") or "").strip():
            content.append({"type": "text", "text": gate.keep(got["text"])})
        for c in got.get("tool_calls") or []:
            content.append({"type": "tool_use", "id": str(c.get("id") or ""), "name": str(c.get("name") or ""),
                            "input": c.get("arguments") if isinstance(c.get("arguments"), dict) else {}})
        stop = "tool_use" if got.get("tool_calls") else ("max_tokens" if got.get("done_reason") == "length"
                                                          else "end_turn")
        usage = got.get("usage") or {}
        return {"content": content, "stop": stop,
                "usage": {"in": int(usage.get("prompt_tokens") or 0), "out": int(usage.get("completion_tokens") or 0),
                          "cache_read": 0, "cache_write": 0},
                "error": None, "retry_after_s": None}

    def ask_json(self, system: str, user: str, think: Optional[bool] = None) -> Any:
        """A small JSON ask: the parsed JSON the brain answered, or None (never a veto; the caller falls back).
        *think* False turns thinking off for this ask (the wizard's name ideas); None follows the model profile."""
        s, gate = self._local()
        got = self._ask_once(s, [{"role": "system", "content": self._in(gate, system) + JSON_NOTE},
                                 {"role": "user", "content": self._in(gate, user)}], None, think=think)
        value = read_json(str(got.get("text") or ""))
        return None if value is None else clean_value(value, gate.keep)

    def answer(self, question: str, context: str) -> str:
        """The brain's answer to *question* from *context* only (``prove``: "what does this app show?")."""
        s, gate = self._local()
        got = self._ask_once(s, [{"role": "system", "content": ANSWER_NOTE},
                                 {"role": "user", "content": self._in(gate, f"{context}\n\nQuestion: {question}")}],
                             None)
        return gate.keep(str(got.get("text") or "")).strip()


def _not_installed(_args: dict, _ctx: dict) -> Any:
    """A store app's tool, listed for a dry run only: it is not installed, so it never runs."""
    raise BrainError("That app is in the PuffPAI store, not on this bot yet.", "not_installed")


JSON_NOTE = "\n\nAnswer with JSON only: no words before or after it, no code fence."
ANSWER_NOTE = ("Answer the question from the text the user gives you, and nothing else. Plain words, one or two "
               "sentences. If the text does not say, answer: The page does not say.")
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


def read_json(text: str) -> Any:
    """The JSON in a model's answer (a code fence or words around it are allowed), or None."""
    text = _FENCE.sub("", text.strip()).strip()
    try:
        return json.loads(text)
    except ValueError:
        pass
    for open_, close in (("{", "}"), ("[", "]")):
        a, b = text.find(open_), text.rfind(close)
        if 0 <= a < b:
            try:
                return json.loads(text[a:b + 1])
            except ValueError:
                continue
    return None


_running: Optional[Brain] = None


def register(brain: Brain) -> None:
    """The running Core's Brain, for callers that reach it by name (L1 ``seams``)."""
    global _running
    _running = brain


def sample_ask(tool: str, ask: str, *, brain: Optional[Brain] = None) -> Any:
    """L1's seam ``zone2.brain.sample_ask(tool, ask) -> bool`` (``puffpai check`` Level 2 item 12). A dry run.

    With *brain* (``puffpai check`` passes the throwaway bot's own): that Brain's whole answer,
    ``{"ok", "tool", "called", "text", "reason"}``. Without: the running Core's Brain, or one on this bot's
    folder, and True or False."""
    if brain is not None:
        return brain.sample_ask(tool, ask)
    return bool(_brain().sample_ask(tool, ask).get("ok"))


def _brain() -> Brain:
    """The running Core's Brain, or one on this bot's folder."""
    brain = _running
    if brain is None:
        from ..paths import state_dir
        from ..registry import Registry
        from ..packages import manifest as manifest_mod
        from ..packages.installs import Installs
        state = state_dir()
        registry, installs = Registry(state / "registry.db"), Installs(state)
        # The Core's one reader of an installed package's manifest (no Steward here: a changed file is
        # simply not read).
        brain = Brain(registry, state, manifest=lambda i: manifest_mod.read_installed(registry, installs, i),
                      folder=installs.folder)
    return brain


def picks(package: str, ask: str) -> dict:
    """Lane L6's ``brain_picks`` port: does the brain pick one of *package*'s tools for *ask*? (``Brain.picks``)"""
    return _brain().picks(package, ask)


def toolsmith_turn(system: str, messages: list[dict], tools: list[dict]) -> dict:
    """Lane L6's ``local_turn`` port: one turn on this computer's model, in the neutral shape."""
    return _brain().toolsmith_turn(system, messages, tools)


def ask_json(system: str, user: str) -> Any:
    """Lane L6's ``brain_json`` port: parsed JSON, or None."""
    return _brain().ask_json(system, user)


def answer(question: str, context: str) -> str:
    """Lane L6's ``brain_answer`` port."""
    return _brain().answer(question, context)


__all__ = ["Brain", "answer", "arg_summary", "ask_json", "picks", "read_json", "register", "sample_ask",
           "toolsmith_turn"]
