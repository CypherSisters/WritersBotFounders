# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/brain_loop.py (adapted)
"""The tool loop: one loop for chat, voice and routines (01 "Zone 2", 06 row "Tool loop").

1. Send the chat so far and the turn's tools to the local model.
2. If it answers with tool calls, run each one (``tools.call``), pass each
   result through the gate before Zone 2 (X6), put it back as a ``tool``
   message, and go to 1. The tools are read again every step, so
   ``open_package`` mid-turn shows at once.
3. If it answers with words, that is the answer.
4. At most ``max_steps`` model calls with tools, then one last call with no
   tools (Hermes's "grace call").

What changed from v1, on purpose (08 lessons):
- **No filter rewrites the brain's sentences.** v1's ``_checked`` /
  ``FAILED_ANSWER``, ``plain_paths``, ``_plain_dashes`` and the "you said no"
  prefix are gone. What the bot changed is reported from the tool-call log
  (``changed``: every call that really ran and that Drawbridge or the
  Registry counts as changing), not guessed from the text. Her No, an expired
  Yes card or Drawbridge's no ends the turn with one plain line.
- **An empty reply is reported as empty** (``empty: true``, ``text: ""``);
  the Core says "The brain gave no answer." beside it, never in its place.
  One nudge is still sent when a model stops right after its tool calls
  (that asks the model again; it does not change what it said).
- **No claim without a tool** (third fresh-install proof, 10-23: "Toolsmith is
  building the world news dashboard app now" with no tool called). When no
  tool ran this turn and the answer says something was added, started,
  built, saved or scheduled (:func:`claims_work`), the model is asked once
  more with :data:`NO_TOOL_NOTE`, and that first answer is not kept. If it
  then calls a tool, the turn goes on as usual; if it still claims, the
  answer is :data:`NO_TOOL_LINE`. Only that narrow kind of sentence counts:
  questions, offers and sentences with "not" never do, and a dry run
  (``puffpai check``) is never asked again.
- Reasoning is never the answer: a ``<think>`` block is taken out of the
  words (``ollama.strip_thinking``; the Ollama adapter also does it while
  streaming, and never reads Ollama's ``thinking`` field).
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional

from . import tools as tl
from .ollama import strip_thinking

MAX_STEPS = 8
NO_ANSWER = "The brain gave no answer."

TOOL_NOTE = (
    "You can use tools. When a tool fits what the owner asks, call it, read what it gives back, and keep "
    "going until the job is done. Then answer in plain, short words. Say a thing was done only when a tool "
    "result says it was done. If a tool fails or the owner says no, say so plainly and do not try the same "
    "thing another way. If none of your tools can do what is asked, use list_installed or open_package to "
    "look for one, or ask_toolsmith to find one. When the owner asks to add or install an app, a tool or a "
    "GitHub address (\"add github.com/owner/repo\", \"add an app for X\"), call ask_toolsmith with the owner's words: "
    "only Toolsmith adds things, and only after the owner's Yes. Never say something was added, installed or put on a "
    "list or checklist unless a tool result says so. Each new ask to add something needs its own ask_toolsmith "
    "call, even when earlier messages in this chat talk about Toolsmith. To put an app that is already "
    "installed on the owner's Home page or dashboard, or to pin, hide, move or rename a tile there, call "
    "arrange_home, not ask_toolsmith. Values written like {{vault:...}} are "
    "kept private; use them as they are.")
AFTER_TOOLS_NOTE = ("You just used tools but gave no answer. Read the tool results above and answer the owner "
                    "now, in plain words, or carry on with the job.")
OUT_OF_STEPS_NOTE = ("You have used all your tool steps for this message. Answer the owner now with what you "
                     "found so far, and say plainly what is still not done.")
DRY_RUN_LINE = "This is a check, so the tool did not run."
NO_TOOL_NOTE = ("No tool ran for this message, so nothing was added, started, built, saved or scheduled. If the "
                "owner asked for that, call the tool that does it now (ask_toolsmith adds apps and tools). "
                "Otherwise answer again without saying it was done.")
NO_TOOL_LINE = ("Nothing was added, started or saved yet: no tool ran for this message. If you want an app or a "
                "tool added, say so and I will ask Toolsmith.")

# A claim that THIS bot did something for the owner (lane I6a). Only four shapes count, so facts about the
# world ("The Eiffel Tower was built in 1889", "Python was created by ...") are never claims:
# 1. first person: "I've added", "I'm building", "I made", "I went ahead", "I'll install";
# 2. Toolsmith as the subject: "Toolsmith is on it", "Toolsmith started the build";
# 3. the owner's things: "your note is saved", "the app was installed", "it's on your list now",
#    "Milk added to the list";
# 4. a done-word standing alone: "Done!", "Saved!", "All set, ...".
# A negation counts only when it negates the claim verb itself ("I didn't add", "I added nothing").
_DONE = (r"(?:added|built|made|created|installed|saved|scheduled|set up|started|queued|put|booked|planned"
         r"|asked toolsmith|told toolsmith|went ahead)")
_DOING = r"(?:adding|building|installing|saving|scheduling|setting up|making|creating|putting|starting|queuing)"
_NOUN = r"(?:app|apps|tool|tools|reminder|reminders|recipe|recipes|note|notes|item|items|build|meal|page)"
_CLAIMS = [re.compile(x, re.I) for x in (
    r"\bi(?:'ve| have| just| already| also)*\s+" + _DONE
    + r"\b(?!\s+(?:nothing|anything|an? (?:mistake|typo|error))\b)",
    r"\b(?:i'm|i am)\s+(?:now\s+|already\s+)?" + _DOING + r"\b",
    r"\b(?:i'll|i will)\s+(?:now\s+|go ahead and\s+)?(?:add|build|install|save|schedule|set up|create|start)\b",
    r"\b(?:i'll|i will|let me)\s+(?:now\s+)?(?:ask|have|get|tell|request)\s+toolsmith\b",
    r"\btoolsmith(?:'s|\s+is|\s+has)\s+(?:now\s+|already\s+|just\s+)?(?:on it|started|begun|building|adding"
    r"|installing|making|working on|built|added|installed|made)\b",
    r"\btoolsmith\s+(?:started|built|added|installed|made|created)\b",
    r"\btoolsmith\s+will\s+(?:now\s+)?(?:build|add|install|make|create|start)\b",
    r"\b(?:being|been|was)\s+(?:built|added|installed|made|set up)\s+by\s+toolsmith\b",
    r"\byour\s+(?:[\w'-]+\s+){0,3}?(?:has been|have been|is now|are now|was|were|is|are)\s+(?:now\s+)?"
    r"(?:added|built|made|created|installed|saved|scheduled|set up|started|ready|running|booked|planned)\b",
    r"\b(?:the|that|this|it|its)\s+(?:[\w'-]+\s+)?" + _NOUN + r"\s+(?:has been|was|is now|is)\s+(?:now\s+)?"
    r"(?:added|built|made|created|installed|saved|scheduled|set up|ready|running)\b"
    r"(?!\s+(?:with|by|in|from|using)\b)",                          # how its makers built it is a fact
    r"\b(?:has been|have been|was|were|is now|are now)\s+(?:added|put|saved|scheduled)\s+(?:to|on|in|for)\s+"
    r"(?:your|the)\s+(?:[\w'-]+\s+)?(?:list|checklist|plan|calendar|pantry|reminders)\b",
    r"\b(?:is|are|it's|it is)\s+now\s+on\s+(?:your|the)\b|\bon\s+(?:your|the)\s+(?:[\w'-]+\s+)?list\s+now\b",
    r"^(?:(?!(?:is|are|was|were|be|been)\b)[\w'-]+\s+){0,3}(?:added|saved|scheduled|installed)\s+(?:it\s+)?"
    r"(?:to|on|for|in)\s+(?:your|the)\b",
    r"^(?:added|saved|installed|scheduled|built|created)\b",
    r"^(?:done|saved|added|installed|scheduled|all set|all done)\b\s*(?:[!.,]|$)",
)]


def claims_work(text: str) -> bool:
    """Does *text* say this bot added, started, built, saved or scheduled something (or is doing it)?"""
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")):
        s = sentence.strip().strip("*_ ")
        if s and not s.endswith("?") and any(c.search(s) for c in _CLAIMS):
            return True
    return False


#: Core tools that can do what a claim says (add, ask Toolsmith, raise a reminder). A read-only tool that ran
#: (search_knowledge, list_installed) never excuses a claim; a package tool counts when it changes things.
ACTING_CORE_TOOLS = frozenset({"ask_toolsmith", "checklist_add", "open_in_program", "run_command", "send_document"})


AskModel = Callable[[list, Optional[list], Optional[Callable[[str], None]]], dict]
ToModel = Callable[[Any], str]

#: A tool result from earlier in this turn, cut to make room, keeps this much.
SHRUNK_RESULT_CHARS = 400
ROOM_CUT = " ...(cut here to keep room for the question)"


def _tokens(messages: list) -> int:
    return sum(len(str(m.get("content") or "")) for m in messages) // 4 + 8 * len(messages)


def fit_turn(chat: list, context: Optional[int], question_at: int) -> list:
    """*chat* within about *context* tokens, the question always kept (v1 ``fit_turn`` :529).

    This turn's older tool results are cut first, then the older chat turns
    before the question. System notes and the question stay.
    """
    room = int(context * 0.8) if context else 0
    if not room or question_at < 0 or _tokens(chat) <= room:
        return chat
    out = [dict(m) for m in chat]
    q = question_at
    for i in [i for i, m in enumerate(out) if i > q and m.get("role") == "tool"][:-1]:
        text = str(out[i].get("content") or "")
        if len(text) > SHRUNK_RESULT_CHARS + len(ROOM_CUT):
            out[i]["content"] = text[:SHRUNK_RESULT_CHARS] + ROOM_CUT
            if _tokens(out) <= room:
                return out
    while _tokens(out) > room:
        older = next((i for i, m in enumerate(out[:q]) if m.get("role") != "system"), None)
        if older is None:
            break
        del out[older]
        q -= 1
    return out


def run(messages: list, *, table: tl.ToolTable, ask_model: AskModel, to_model: ToModel,
        emit: Optional[Callable[[dict], None]] = None, ctx: Optional[dict] = None,
        max_steps: int = MAX_STEPS, context: Optional[int] = None, dry_run: bool = False) -> dict:
    """One turn. ``{"text", "empty", "steps", "used", "changed", "calls"}``.

    ``to_model(value)`` is the gate's text-for-model for one tool result or
    error, of any shape (``Gate.clean(value, for_model=True)``).
    ``dry_run``: tool calls are recorded, never run, and the turn ends after
    the first step that calls a tool (``puffpai check`` Level 2 item 12).
    Raises :class:`~.errors.BrainError` when the model never answered.
    """
    emit = emit or (lambda _e: None)
    ctx = dict(ctx or {})
    chat = [dict(m) for m in messages]
    question_at = max((i for i, m in enumerate(chat) if m.get("role") == "user"), default=-1)
    used: list[str] = []
    changed: list[dict] = []
    calls_log: list[dict] = []
    said: list[str] = []
    nudged = False
    rechecked = False
    acted = False                      # a tool that could do what a claim says really ran this turn
    steps = 0

    def on_token(text: str) -> None:
        emit({"type": "token", "text": text})

    def finish(text: str, n: int) -> dict:
        text = text if text.strip() else ""
        return {"text": text, "empty": not text, "steps": n, "used": used, "changed": changed, "calls": calls_log}

    while steps < max_steps:
        if ctx.get("cancelled") is not None and ctx["cancelled"].is_set():
            break
        steps += 1
        specs = table.specs() or None
        msg = ask_model(fit_turn(chat, context, question_at), specs, on_token)
        calls = list(msg.get("tool_calls") or []) if specs else []
        content = strip_thinking(str(msg.get("text") or ""))
        if not calls:
            if not content.strip() and not nudged and steps < max_steps and used:
                nudged = True
                chat.append({"role": "assistant", "content": ""})
                chat.append({"role": "user", "content": AFTER_TOOLS_NOTE})
                continue
            if not acted and not dry_run and claims_work(content):
                if rechecked or steps >= max_steps:
                    return finish(NO_TOOL_LINE, steps)
                # No tool ran, yet it says work was done: ask once more, and do not keep these words.
                rechecked = True
                chat.append({"role": "assistant", "content": content})
                chat.append({"role": "user", "content": NO_TOOL_NOTE})
                continue
            if content.strip():
                said.append(content)
            return finish("\n\n".join(said), steps)
        if content.strip():
            said.append(content)
        chat.append({"role": "assistant", "content": content,
                     "tool_calls": [{"function": {"name": c["name"], "arguments": c.get("arguments") or {}}}
                                    for c in calls]})
        for call in calls:
            name = str(call.get("name") or "")
            shown = tl.shown_name(name)
            tool = table.find(name)
            calls_log.append({"tool": shown, "known": tool is not None})
            if dry_run:
                got = {"ok": True, "result": DRY_RUN_LINE, "asked": False, "tool": shown}
            else:
                emit({"type": "progress", "tool": shown, "line": f"Using {shown} ..."})
                got = tl.call(table, name, call.get("arguments"), ctx)
            used.append(shown)
            result = got.get("result")
            if isinstance(result, dict) and result.get("open"):
                emit({"type": "open", "urls": list(result["open"])[:3], "tool": shown})
            if isinstance(result, dict) and result.get("show_passage"):      # D140: a fix, and the passage it means
                emit({"type": "passage", **{k: result.get(k) for k in ("document", "part", "of", "before", "passage", "after",
                                                                       "suggestion", "why")}})
            if got.get("ok") and got.get("changes"):
                changed.append({"tool": shown})
            if got.get("ok") and (got.get("changes") or shown in ACTING_CORE_TOOLS):
                acted = True
            if got.get("end_turn"):
                # Her No, an expired card or Drawbridge's no ends the turn with one plain line (09 section 3):
                # the model never tries the same thing another way.
                emit({"type": "tool_done", "tool": shown, "ok": False})
                return finish(str(got.get("error") or ""), steps)
            # The result or the error text, whatever its shape, through the gate (X6, D50).
            raw = result if got.get("ok") else {"error": got.get("error")}
            emit({"type": "tool_done", "tool": shown, "ok": bool(got.get("ok"))})
            chat.append({"role": "tool", "content": to_model(raw), "tool_name": name})
        if dry_run:
            return finish("\n\n".join(said), steps)
    chat.append({"role": "system", "content": OUT_OF_STEPS_NOTE})
    msg = ask_model(fit_turn(chat, context, question_at), None, on_token)
    if str(msg.get("text") or "").strip():
        said.append(str(msg["text"]))
    return finish("\n\n".join(said), steps + 1)


__all__ = ["ACTING_CORE_TOOLS", "AFTER_TOOLS_NOTE", "DRY_RUN_LINE", "MAX_STEPS", "NO_ANSWER", "NO_TOOL_LINE", "NO_TOOL_NOTE", "TOOL_NOTE",
           "claims_work", "fit_turn", "run"]
