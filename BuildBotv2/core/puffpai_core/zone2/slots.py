"""Zone 2 slot contracts (contract 1), as the Core sees them.

The model slot's one adapter interface (01 "Zone 2"), shaped after v1
``inference/base.py`` ``LLMBackend`` (06: ``chat``, ``chat_stream``,
``is_available``) plus tool calls, which v2's brain loop needs. A runtime is a
raw reader while the vault is open (D25, D31(c)): no network, no token, no keys
(X13). It must be local (X12).
"""

from __future__ import annotations

from typing import Any, Iterator, Protocol, runtime_checkable

Message = dict[str, Any]   # {"role": "system"|"user"|"assistant"|"tool", "content": str, ...}


@runtime_checkable
class Runtime(Protocol):
    """``zone2.runtime``: the model server (Ollama, llama.cpp, OpenAI-compatible local). One active.

    ``chat`` returns ``{"text": str, "tool_calls": [{"name", "arguments"}], "done_reason": str}``.
    ``think=False`` is sent on answer calls unless the model profile says the
    model needs thinking. An empty reply is returned empty, never filled in.
    """

    def is_available(self) -> bool: ...
    def models(self) -> list[dict]: ...                 # [{name, size_bytes, context}]
    def chat(self, messages: list[Message], *, model: str, tools: list[dict] | None = None,
             think: bool = False, options: dict | None = None) -> dict: ...
    def chat_stream(self, messages: list[Message], *, model: str, think: bool = False,
                    options: dict | None = None) -> Iterator[str]: ...


@runtime_checkable
class Embedder(Protocol):
    """``zone2.embedder``: embeddings for the knowledge index. One active."""

    def is_available(self) -> bool: ...
    def info(self) -> dict: ...                         # {model, dims}
    def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class Reranker(Protocol):
    """``zone2.reranker``: reranks search hits. Zero or one."""

    def rerank(self, query: str, docs: list[str]) -> list[float]: ...


__all__ = ["Embedder", "Message", "Reranker", "Runtime"]
