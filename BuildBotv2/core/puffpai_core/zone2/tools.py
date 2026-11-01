# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/brain_tools.py (adapted)
"""The tools of one turn (06 row "Tool table").

v1 kept one global table that offered every tool to every turn. v2 builds a
:class:`ToolTable` per turn: Concierge fills it within the model's tool
budget, and ``open_package`` can add a package's tools mid-turn.

Names: people see ``<id>.<tool>``; models get ``<id>__<tool>`` because many
model APIs reject dots (02 field reference). Core tools have plain names.

Every package tool call goes through ``ctx["review"](call) -> verdict`` when
the Core wired one (Drawbridge's review, ``wiring.tool_review``): the verdict
says whether it runs, whether the tool changes things (Drawbridge's own
classification), and whether her Yes was asked. A broken review runs nothing.
Without a review, a tool with ``changes`` (Drawbridge's one rule plus the
Registry, never the caller, X27) asks the owner first through
``ask(question) -> True | False | None``. With no way to ask, it does not run.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

#: What one tool result may hand back to the model, in characters.
MAX_RESULT_CHARS = 6000
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")

NO_ASK_LINE = "This needs your Yes first, and there is no Yes card here. Nothing was run."
SAID_NO_LINE = "You said no, so it did not run."
NO_ANSWER_LINE = "No answer came, so it did not run."
UNKNOWN_TOOL_LINE = "There is no tool called {name} right now."
FAILED_LINE = "The tool {name} did not work: {why}"
REVIEW_DOWN_LINE = "Drawbridge isn't answering, so I can't change things for you right now. Nothing was run."
NOT_RUN = (NO_ASK_LINE, SAID_NO_LINE, NO_ANSWER_LINE, REVIEW_DOWN_LINE)


class BadArgs(ValueError):
    """A tool's own refusal of its arguments (plain words). ``call`` reports it like a schema miss."""


def wire_name(shown: str) -> str:
    """``freshtable.add_to_list`` -> ``freshtable__add_to_list``."""
    return shown.replace(".", "__", 1) if "." in shown else shown


def shown_name(wire: str) -> str:
    """``freshtable__add_to_list`` -> ``freshtable.add_to_list``."""
    return wire.replace("__", ".", 1) if "__" in wire else wire


@dataclass
class Tool:
    """One tool the model may call. ``fn(args, ctx)`` returns anything JSON can carry."""

    name: str                       # shown name: "<pkg>.<tool>" or a core tool name
    description: str
    schema: dict
    fn: Callable[[dict, dict], Any]
    changes: bool = False
    package: Optional[str] = None
    question: Optional[Callable[[dict, dict], Optional[dict]]] = None   # an extra Yes (read_app on a stopped app)
    extra: dict = field(default_factory=dict)

    @property
    def wire(self) -> str:
        return wire_name(self.name)

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.wire, "description": self.description,
                                                 "parameters": self.schema}}


def check_schema(schema: Any) -> dict:
    """An input schema every model accepts (v1 mcp_client._schema): an object with properties."""
    schema = dict(schema) if isinstance(schema, dict) else {}
    schema.setdefault("type", "object")
    if schema.get("type") == "object":
        props = schema.get("properties")
        schema["properties"] = props if isinstance(props, dict) else {}
        req = schema.get("required")
        schema["required"] = [r for r in req if isinstance(r, str) and r in schema["properties"]] \
            if isinstance(req, list) else []
    return schema


def make(name: str, description: str, schema: dict, fn: Callable[[dict, dict], Any], **kw: Any) -> Tool:
    if not _NAME_RE.match(wire_name(name)):
        raise ValueError(f"{name!r} is not a usable tool name")
    if not str(description or "").strip():
        raise ValueError("a tool needs a plain description")
    return Tool(name=name, description=str(description).strip(), schema=check_schema(schema), fn=fn, **kw)


class ToolTable:
    """The tools of one turn. Thread-safe; ``add`` during a turn shows on the next model call."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._lock = threading.Lock()
        self._tools: dict[str, Tool] = {}
        for t in tools or []:
            self.add(t)

    def add(self, tool: Tool) -> None:
        with self._lock:
            self._tools.setdefault(tool.name, tool)

    def names(self) -> list[str]:
        with self._lock:
            return list(self._tools)

    def find(self, name: str) -> Optional[Tool]:
        with self._lock:
            return self._tools.get(name) or self._tools.get(shown_name(name))

    def specs(self) -> list[dict]:
        with self._lock:
            return [t.spec() for t in self._tools.values()]

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)


def _type_ok(value: Any, kind: Any) -> bool:
    checks = {"string": lambda v: isinstance(v, str), "boolean": lambda v: isinstance(v, bool),
              "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
              "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
              "array": lambda v: isinstance(v, list), "object": lambda v: isinstance(v, dict)}
    return checks.get(kind, lambda v: True)(value)


def check_args(tool: Tool, args: Any) -> dict:
    """The arguments, checked just enough for a model to correct itself. Raises ValueError."""
    if args is None:
        args = {}
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            raise ValueError("the arguments were not JSON") from None
    if not isinstance(args, dict):
        raise ValueError("the arguments must be an object")
    props = tool.schema.get("properties") or {}
    if tool.schema.get("additionalProperties") is False:
        unknown = [k for k in args if k not in props]
        if unknown:
            raise ValueError(f"unknown argument {unknown[0]!r}")
    missing = [r for r in tool.schema.get("required") or [] if r not in args]
    if missing:
        raise ValueError(f"missing argument {missing[0]!r}")
    for key, value in args.items():
        spec = props.get(key)
        if isinstance(spec, dict) and not _type_ok(value, spec.get("type")):
            raise ValueError(f"{key!r} should be a {spec.get('type')}")
    return args


def question_for(tool: Tool, args: dict, ctx: dict) -> Optional[dict]:
    """The Yes question for this call, or None (v1 ``question_for`` :246)."""
    q = tool.question(args, ctx) if tool.question is not None else None
    if q is None and not tool.changes:
        return None
    q = dict(q or {})
    shown = json.dumps(args, ensure_ascii=False) if args else ""
    q.setdefault("title", f"May I use {tool.name}?")
    q.setdefault("detail", shown)
    q.setdefault("tool", tool.name)
    if tool.package:
        q.setdefault("package", tool.package)
    q.setdefault("kind", "tool_call")
    return q


def result_text(result: Any) -> str:
    """A tool result as the text the model reads, cut to a size it can hold."""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(result)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + " ...(cut short)"
    return text


def _wrong_args(tool: Tool, exc: Exception, asked: bool) -> dict:
    props = list((tool.schema.get("properties") or {}).keys())
    takes = f" It takes: {', '.join(props)}." if props else " It takes no arguments."
    return {"ok": False, "error": f"Wrong arguments for {tool.name}: {exc}.{takes}", "asked": asked,
            "tool": tool.name, "bad_args": True}


def call(table: ToolTable, name: str, args: Any, ctx: dict) -> dict:
    """Run one call: ``{"ok", "result" | "error", "asked", "tool"}``. Never raises.

    Arguments the tool refuses (its schema, or :class:`BadArgs` from the tool)
    add ``"bad_args": true``: the Core API answers 400 for those.
    """
    tool = table.find(str(name or ""))
    if tool is None:
        return {"ok": False, "error": UNKNOWN_TOOL_LINE.format(name=shown_name(str(name))), "asked": False,
                "tool": shown_name(str(name))}
    try:
        clean = check_args(tool, args)
    except ValueError as exc:
        return _wrong_args(tool, exc, False)
    review = ctx.get("review")
    if tool.package and callable(review):
        return _reviewed(tool, clean, ctx, review)
    asked = False
    try:
        question = question_for(tool, clean, ctx)
    except Exception as exc:  # noqa: BLE001 - a call we cannot describe does not run
        return {"ok": False, "error": FAILED_LINE.format(name=tool.name, why=exc), "asked": False, "tool": tool.name}
    if question is not None:
        ask = ctx.get("ask")
        if not callable(ask):
            return {"ok": False, "error": str(question.get("no_card") or NO_ASK_LINE), "asked": False,
                    "tool": tool.name}
        asked = True
        try:
            answer = ask(question)
        except Exception:  # noqa: BLE001 - a broken ask runs nothing
            answer = None
        if answer is None:
            return {"ok": False, "error": str(question.get("no_card") or NO_ANSWER_LINE), "asked": True,
                    "tool": tool.name, "end_turn": question.get("end_turn") is True}
        if answer is not True:
            return {"ok": False, "error": str(question.get("said_no") or SAID_NO_LINE), "asked": True,
                    "tool": tool.name, "end_turn": question.get("end_turn") is True}
        ctx = dict(ctx, said_yes=True)
    try:
        result = tool.fn(clean, ctx)
    except BadArgs as exc:
        return _wrong_args(tool, exc, asked)
    except Exception as exc:  # noqa: BLE001 - the model hears it, chat goes on
        return {"ok": False, "error": FAILED_LINE.format(name=tool.name, why=str(exc) or type(exc).__name__),
                "asked": asked, "tool": tool.name}
    return {"ok": True, "result": result, "asked": asked, "tool": tool.name, "changes": tool.changes}


def _reviewed(tool: Tool, clean: dict, ctx: dict, review: Callable[[dict], Any]) -> dict:
    """A package tool call through Drawbridge's review (every call, read-only ones too)."""
    call_ = {"package": tool.package, "tool": tool.name, "name": tool.name.split(".", 1)[-1], "args": clean,
             "entry": tool.extra.get("entry"), "changes": tool.changes}
    try:
        verdict = review(call_)
    except Exception:  # noqa: BLE001 - a broken review runs nothing (X26)
        verdict = None
    if not isinstance(verdict, dict):
        return {"ok": False, "error": REVIEW_DOWN_LINE, "asked": False, "tool": tool.name, "end_turn": True,
                "changes": True}
    changing = verdict.get("changing") is not False or tool.changes     # either one says changing: it is
    asked = verdict.get("asked") is True
    if verdict.get("run") is not True:
        return {"ok": False, "error": str(verdict.get("line") or SAID_NO_LINE), "asked": asked, "tool": tool.name,
                "end_turn": verdict.get("end") is not False, "changes": changing}
    ctx = dict(ctx, said_yes=asked, review_id=verdict.get("review_id"))
    try:
        result = tool.fn(clean, ctx)
    except BadArgs as exc:
        return _wrong_args(tool, exc, asked)
    except Exception as exc:  # noqa: BLE001 - the model hears it, chat goes on
        return {"ok": False, "error": FAILED_LINE.format(name=tool.name, why=str(exc) or type(exc).__name__),
                "asked": asked, "tool": tool.name, "changes": changing}
    return {"ok": True, "result": result, "asked": asked, "tool": tool.name, "changes": changing,
            "review_id": verdict.get("review_id")}


__all__ = ["BadArgs", "MAX_RESULT_CHARS", "NOT_RUN", "REVIEW_DOWN_LINE", "Tool", "ToolTable", "call", "check_args", "check_schema", "make",
           "question_for", "result_text", "shown_name", "wire_name"]
