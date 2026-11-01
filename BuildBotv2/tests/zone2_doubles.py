"""Test doubles for lane L2: a fake model runtime, a gate double, a consent double, a fake package."""

from __future__ import annotations

import re
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from puffpai_core.registry import Registry, row_from_manifest
from puffpai_core.zone2.brain import Brain
from puffpai_core.zone2.gate import Gate
from puffpai_core.zone2.mcp import Target

PHONE = re.compile(r"\b\d{3}-\d{4}\b")
KEY = re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")


class GateDouble:
    """Phones are held values (P2); ``sk-...`` keys are secrets. Same value, same ref."""

    def __init__(self, vault_open: bool = False) -> None:
        self.open = vault_open
        self.refs: dict[str, str] = {}
        self.calls: list[tuple[str, bool]] = []

    def _ref(self, value: str) -> str:
        return self.refs.setdefault(value, f"r{len(self.refs) + 1}")

    def prepare(self, text: str, vault_open: bool) -> tuple[str, list]:
        self.calls.append((text, vault_open))
        handles = []

        def key(m):
            handles.append(self._ref(m.group(0)))
            return "{{vault:%s}} (an API key)" % self.refs[m.group(0)]

        def phone(m):
            handles.append(self._ref(m.group(0)))
            return m.group(0) if vault_open else "{{vault:%s}} (a phone number)" % self.refs[m.group(0)]
        text = KEY.sub(key, text)
        text = PHONE.sub(phone, text)
        return text, handles

    def gate(self) -> Gate:
        return Gate(prepare=self.prepare, vault_open=lambda: self.open)


class FakeRuntime:
    """Answers from a script. Each item is a dict reply or ``fn(messages, tools) -> dict``."""

    def __init__(self, *script: Any) -> None:
        self.script = list(script)
        self.calls: list[dict] = []

    def is_available(self) -> bool:
        return True

    def models(self) -> list[dict]:
        return [{"name": "fake:1b", "size_bytes": 1, "context": None}]

    def chat(self, messages, *, model, tools=None, think=False, options=None, on_token=None) -> dict:
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "think": think,
                           "model": model, "options": options})
        item = self.script.pop(0) if self.script else {"text": ""}
        reply = item(messages, tools) if callable(item) else dict(item)
        reply.setdefault("text", "")
        reply.setdefault("tool_calls", [])
        if on_token and reply["text"]:
            on_token(reply["text"])
        return reply

    def chat_stream(self, messages, *, model, think=False, options=None):
        yield self.chat(messages, model=model, think=think, options=options)["text"]

    def seen(self, n: int = -1) -> str:
        """Everything the model was sent in call *n*, as one string."""
        return "\n".join(str(m.get("content") or "") for m in self.calls[n]["messages"])


def call(name: str, **args) -> dict:
    return {"tool_calls": [{"id": "c1", "name": name, "arguments": args}]}


def fake_shop() -> tuple[MCPServer, list[str]]:
    """A package MCP server like FreshTable's: a shopping list."""
    items: list[str] = []
    srv = MCPServer("freshtable")

    @srv.tool()
    def add_to_list(item: str) -> str:
        """Put an item on the shopping list."""
        items.append(item)
        return f"Added {item} to the shopping list."

    @srv.tool(annotations=ToolAnnotations(readOnlyHint=True))      # a made package says read-only the MCP way (X27)
    def show_list() -> str:
        """Show what is on the shopping list."""
        return ", ".join(items) or "The list is empty."

    return srv, items


class ConsentDouble:
    def __init__(self, answer: Any = True) -> None:
        self.answer = answer
        self.questions: list[dict] = []

    def __call__(self, question: dict):
        self.questions.append(question)
        return self.answer


def make_brain(state, freshtable: dict, runtime: FakeRuntime, gate: GateDouble | None = None,
               consent: Callable | None = None, extra_rows: list[dict] | None = None,
               settings: dict | None = None, search: Callable | None = None,
               checklist=None) -> tuple[Brain, list[str], Registry]:
    from puffpai_core.zone2 import settings as model_settings
    reg = Registry(state / "registry.db")
    reg.add(row_from_manifest(freshtable, "made", status="running", port=18500))
    for row in extra_rows or []:
        reg.add(row)
    srv, items = fake_shop()
    manifests = {"freshtable": freshtable}
    model_settings.save(state, **{"address": "http://127.0.0.1:9", "model": "fake:1b", **(settings or {})})
    gate = gate or GateDouble()
    brain = Brain(reg, state, runtime=runtime, gate=gate.gate, consent=consent,
                  manifest=lambda pid: manifests.get(pid),
                  targets=lambda pid: Target(package=pid, kind="inproc", server=srv) if pid == "freshtable" else None,
                  browser=False, search=search, checklist=checklist)
    return brain, items, reg
