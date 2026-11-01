# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/inference/ollama_backend.py (adapted)
"""The default ``zone2.runtime`` provider: an Ollama adapter (01 "Zone 2", 06 row "Model slot: Ollama").

Changes from v1 (06): no defaults in the class (address and model come from
``zone2.settings``); ``think`` is sent on every answer call (False unless the
profile says the model needs thinking; Ollama ignores False for a model that
does not think, v1 ``api_server._ollama_chat`` :1094); tool calls and
streaming in one call; the standard library instead of ``requests``; and the
named brain states of D36(d), read by :meth:`state`.

Native ``/api/chat`` only: the OpenAI-compatible endpoint ignores ``options``,
so ``num_ctx`` would never apply (v1 note). Calls never use a proxy: the model
server is on this computer (or an exact home address, X12).

Reasoning is never part of the answer (third fresh-install proof, 10-23): a
thinking model's reasoning comes in Ollama's own ``thinking`` field, which is
never read, streamed or returned; and a ``<think>...</think>`` block in the
words (a model or template that puts it there) is taken out, while streaming
too, so only the answer is shown or kept.
"""

from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterator, Optional

from .errors import ERROR, LOADING, NOT_DOWNLOADED, READY, TIMED_OUT, BrainError

KEEP_ALIVE = "30m"
SERVER_DOWN = "the model server is not running"


_TAG = re.compile(r"<\s*(/?)\s*think(?:ing)?\s*>", re.I)
#: The end of a piece that may be the start of a tag (``<``, ``</th``, ``< thinki``): held for the next piece.
_PART = re.compile(r"<\s*/?\s*(?:t(?:h(?:i(?:n(?:k(?:i(?:n(?:g)?)?)?)?)?)?)?)?\s*$", re.I)


class ThinkFilter:
    """Takes reasoning out of words that come in pieces (a tag may be split across pieces).

    ``<think>...</think>`` and ``<thinking>...</thinking>`` blocks go, in any case and with spaces inside
    the tag. A closing tag with no opening one means everything before it was reasoning (qwen3.5:9b, live
    10-23: "answer\n</think>\n\nanswer"): it is dropped, and only what follows is kept. :meth:`feed` gives
    the words to show as they come; :meth:`text` is the whole answer at the end (words already shown before
    a stray closing tag can't be taken back from a stream, but they are never in the answer or the history).
    """

    def __init__(self) -> None:
        self.inside = False
        self.seen = False                     # a block or a stray closing tag was taken out
        self.held = ""
        self.kept: list[str] = []

    def feed(self, piece: str) -> str:
        text, out = self.held + str(piece or ""), []
        self.held = ""
        while text:
            m = _TAG.search(text)
            if m is None:
                part = _PART.search(text)
                if part is not None:
                    self.held, text = text[part.start():], text[:part.start()]
                if not self.inside and text:
                    out.append(text)
                break
            before, closing = text[:m.start()], m.group(1) == "/"
            self.seen = True
            if self.inside:
                self.inside = not closing
            elif closing:                     # a stray closing tag: all before it was reasoning
                self.kept, out = [], []
            else:
                if before:
                    out.append(before)
                self.inside = True
            text = text[m.end():]
        shown = "".join(out)
        if shown:
            self.kept.append(shown)
        return shown

    def end(self) -> str:
        """What is left at the end: a half tag outside a block is words; an unclosed block is dropped."""
        rest, self.held = ("" if self.inside else self.held), ""
        if rest:
            self.kept.append(rest)
        return rest

    def text(self) -> str:
        """The whole answer so far, reasoning taken out."""
        out = "".join(self.kept)
        return out.lstrip() if self.seen else out


def strip_thinking(text: str) -> str:
    """*text* without reasoning blocks, or without everything before a stray closing tag."""
    f = ThinkFilter()
    f.feed(str(text or ""))
    f.end()
    return f.text()


#: One model only, and no downloads (D132): for a bot that shares its Ollama with something else, where loading another
#: model or starting a pull would get in that other thing's way. Set by whoever starts the Core; unset changes nothing.
ONLY_MODEL_ENV = "PUFFPAI_ONLY_MODEL"
ONLY_LINE = "This bot is set to use {only} only, so it does not use {model}."
NO_PULL_LINE = "This bot is set to use {only} only, and it downloads no models, so {model} was not downloaded."


def only_model() -> str:
    return os.environ.get(ONLY_MODEL_ENV, "").strip()


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _plain_error(text: str) -> tuple[str, str]:
    """``(code, plain reason)`` from Ollama's own words."""
    low = text.lower()
    if "not found" in low or "try pulling" in low:
        return NOT_DOWNLOADED, "the model is not downloaded yet"
    if "does not support tools" in low:
        return "no_tools", "this model cannot use tools"
    if "memory" in low:
        return ERROR, "the model server ran out of memory"
    return ERROR, f"the model server refused the request ({text[:200]})"


class OllamaRuntime:
    """One Ollama server. ``address`` like ``http://127.0.0.1:11435``."""

    def __init__(self, address: str, *, timeout_s: float = 180.0) -> None:
        if not address:
            raise ValueError("the Ollama address comes from the model settings")
        self.address = address.rstrip("/")
        self.timeout_s = float(timeout_s)
        self._lock = threading.Lock()
        self._last: dict[str, tuple[str, str]] = {}        # model -> (state, reason) of its last call
        self._loading: set[str] = set()
        self._pulls: dict[str, dict] = {}                 # model -> {done_bytes, total_bytes}
        self._streams: dict[str, Any] = {}                # model -> the open download stream (cancel closes it)
        self._cancelled: set[str] = set()

    # -- HTTP ---------------------------------------------------------------
    def _request(self, method: str, path: str, body: Optional[dict] = None, timeout: Optional[float] = None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.address + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        return _opener().open(req, timeout=timeout or self.timeout_s)

    def _get_json(self, path: str, timeout: float = 5.0) -> dict:
        with self._request("GET", path, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")

    # -- Runtime protocol ---------------------------------------------------
    def is_available(self) -> bool:
        try:
            self._get_json("/api/version", timeout=3.0)
            return True
        except (OSError, ValueError):
            return False

    def models(self) -> list[dict]:
        """``[{name, size_bytes, context, params}]`` of the models on this server."""
        data = self._get_json("/api/tags")
        return [{"name": m.get("name") or m.get("model"), "size_bytes": m.get("size"),
                 "context": None, "params": (m.get("details") or {}).get("parameter_size")}
                for m in data.get("models") or [] if isinstance(m, dict)]

    def show(self, model: str) -> dict:
        """Ollama's own facts about an installed model (``POST /api/show``). A hosted model carries
        ``remote_host`` / ``remote_model`` there (``zone2.roles.is_remote``)."""
        with self._request("POST", "/api/show", {"model": model}, timeout=10.0) as resp:
            got = json.loads(resp.read() or b"{}")
        return got if isinstance(got, dict) else {}

    def cancel(self, model: str) -> bool:
        """Stop a download of *model* that is running here: its stream is closed (Ollama stops the pull when
        the asker goes). True when one was running."""
        with self._lock:
            resp = self._streams.get(model)
            self._cancelled.add(model)
        if resp is None:
            return False
        try:
            resp.close()
        except OSError:
            pass
        return True

    def chat(self, messages: list[dict], *, model: str, tools: list[dict] | None = None,
             think: bool = False, options: dict | None = None,
             on_token: Optional[Callable[[str], None]] = None) -> dict:
        """One call. ``{"text", "tool_calls": [{"id", "name", "arguments"}], "done_reason", "usage"}``.

        Words stream to ``on_token`` as they come when it is given. An empty
        reply is returned empty. Raises :class:`BrainError` with a state code.
        """
        if not model:
            raise BrainError("No brain is picked yet. Pick one in the wizard's brain step.", "no_model")
        if only_model() and model != only_model():
            raise BrainError(ONLY_LINE.format(only=only_model(), model=model), "only_model")
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": on_token is not None,
                                   "keep_alive": KEEP_ALIVE, "think": bool(think)}
        if options:
            payload["options"] = dict(options)
        if tools:
            payload["tools"] = tools
        calls: list[dict] = []
        words = ThinkFilter()                 # only the answer: no reasoning (think blocks, a stray closing tag)
        done_reason = ""
        usage = {"prompt_tokens": 0, "completion_tokens": 0}
        try:
            with self._request("POST", "/api/chat", payload) as resp:
                if on_token is None:
                    lines = [resp.read()]
                else:
                    lines = iter(resp.readline, b"")
                for line in lines:
                    if not line or not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise urllib.error.HTTPError(self.address, 500, str(chunk["error"]), {}, None)
                    msg = chunk.get("message") or {}
                    # msg["thinking"] (a thinking model's reasoning) is never read: it is not the answer.
                    token = words.feed(msg.get("content") or "")
                    if token and on_token is not None:
                        on_token(token)
                    for call in msg.get("tool_calls") or []:
                        fn = call.get("function") if isinstance(call.get("function"), dict) else call
                        args = fn.get("arguments")
                        if isinstance(args, str):
                            try:
                                args = json.loads(args) if args.strip() else {}
                            except ValueError:
                                args = {"_unreadable": args[:200]}
                        calls.append({"id": str(call.get("id") or f"call_{len(calls)}"),
                                      "name": str(fn.get("name") or ""),
                                      "arguments": args if isinstance(args, dict) else {}})
                    if chunk.get("done"):
                        done_reason = str(chunk.get("done_reason") or "")
                        usage = {"prompt_tokens": int(chunk.get("prompt_eval_count") or 0),
                                 "completion_tokens": int(chunk.get("eval_count") or 0)}
                        break
        except urllib.error.HTTPError as exc:
            said = exc.msg or ""
            try:
                said = json.loads(exc.read() or b"{}").get("error") or said
            except (OSError, ValueError, AttributeError, TypeError):
                pass
            code, reason = _plain_error(str(said))
            self._note(model, code if code in (NOT_DOWNLOADED,) else ERROR, reason)
            raise BrainError(f"Your bot's brain could not answer ({reason}).", code) from None
        except (socket.timeout, TimeoutError) as exc:
            self._timed_out(model, exc)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                self._timed_out(model, exc)
            self._note(model, ERROR, SERVER_DOWN)
            raise BrainError(f"Your bot's brain could not answer ({SERVER_DOWN}).", ERROR) from exc
        except (OSError, ValueError) as exc:
            self._note(model, ERROR, f"the model server sent something unreadable ({type(exc).__name__})")
            raise BrainError("Your bot's brain could not answer (the model server sent something unreadable).",
                             ERROR) from exc
        rest = words.end()
        if rest and on_token is not None:
            on_token(rest)
        self._note(model, READY, "")
        return {"text": words.text(), "tool_calls": calls, "done_reason": done_reason, "usage": usage}

    def chat_stream(self, messages: list[dict], *, model: str, think: bool = False,
                    options: dict | None = None) -> Iterator[str]:
        """Words as they come (no tools). Runs the call on a thread so the caller can iterate."""
        import queue
        q: "queue.Queue[tuple[str, Any]]" = queue.Queue()

        def work() -> None:
            try:
                self.chat(messages, model=model, think=think, options=options, on_token=lambda t: q.put(("t", t)))
                q.put(("end", None))
            except BrainError as exc:
                q.put(("err", exc))
        threading.Thread(target=work, daemon=True, name="ollama-stream").start()
        while True:
            kind, value = q.get()
            if kind == "t":
                yield value
            elif kind == "err":
                raise value
            else:
                return

    def _timed_out(self, model: str, exc: BaseException) -> None:
        """A timeout is ``timed_out`` only when the server is up; on Windows a refused
        connection can take longer than a short timeout, and that is ``error``."""
        if not self.is_available():
            self._note(model, ERROR, SERVER_DOWN)
            raise BrainError(f"Your bot's brain could not answer ({SERVER_DOWN}).", ERROR) from exc
        self._note(model, TIMED_OUT, "it did not answer in time")
        raise BrainError("Your bot's brain did not answer in time.", TIMED_OUT) from exc

    # -- states (D36(d)) ----------------------------------------------------
    def _note(self, model: str, state: str, reason: str) -> None:
        with self._lock:
            self._last[model] = (state, reason)

    def state(self, model: str) -> dict:
        """``{state, model, reason, done_gb?, size_gb?, in_memory?}``. Never raises."""
        out: dict[str, Any] = {"model": model, "reason": ""}
        if not model:
            return {**out, "state": ERROR, "reason": "no brain is picked yet"}
        try:
            names = [m["name"] for m in self.models()]
        except (OSError, ValueError):
            return {**out, "state": ERROR, "reason": SERVER_DOWN}
        from .catalog import is_installed
        if not is_installed(model, names):
            with self._lock:
                pull = dict(self._pulls.get(model) or {})
            if pull.get("total_bytes"):
                out["done_gb"] = round(pull.get("done_bytes", 0) / 1e9, 1)
                out["size_gb"] = round(pull["total_bytes"] / 1e9, 1)
            if pull.get("error"):
                out["reason"] = pull["error"]
            return {**out, "state": NOT_DOWNLOADED}
        with self._lock:
            loading = model in self._loading
            last_state, last_reason = self._last.get(model, (READY, ""))
        if loading:
            return {**out, "state": LOADING}
        if last_state in (TIMED_OUT, ERROR):
            return {**out, "state": last_state, "reason": last_reason}
        try:
            loaded = {str(m.get("name") or m.get("model")) for m in self._get_json("/api/ps").get("models") or []}
            out["in_memory"] = is_installed(model, list(loaded))
        except (OSError, ValueError):
            pass
        return {**out, "state": READY}

    def warm(self, model: str) -> None:
        """Load the model in the background (state ``loading`` until it is in memory)."""
        if only_model() and model != only_model():
            self._note(model, ERROR, ONLY_LINE.format(only=only_model(), model=model))
            return
        with self._lock:
            if model in self._loading:
                return
            self._loading.add(model)

        def work() -> None:
            try:
                with self._request("POST", "/api/chat", {"model": model, "messages": [], "keep_alive": KEEP_ALIVE}):
                    pass
                self._note(model, READY, "")
            except (socket.timeout, TimeoutError):
                self._note(model, TIMED_OUT, "it did not load in time")
            except urllib.error.HTTPError as exc:
                self._note(model, ERROR, _plain_error(exc.msg or "")[1])
            except OSError:
                self._note(model, ERROR, SERVER_DOWN)
            finally:
                with self._lock:
                    self._loading.discard(model)
        threading.Thread(target=work, daemon=True, name="ollama-warm").start()

    def pull(self, model: str, on_progress: Optional[Callable[[int, int], None]] = None) -> bool:
        """Download a model (the wizard's brain step starts this; the owner's pick is its Yes).

        Blocking; progress shows in :meth:`state` as ``done_gb`` of ``size_gb``.
        Tests never call this against a real server.
        """
        if only_model():
            with self._lock:
                self._pulls[model] = {"error": NO_PULL_LINE.format(only=only_model(), model=model)}
            return False
        with self._lock:
            self._pulls[model] = {"done_bytes": 0, "total_bytes": 0}
            self._cancelled.discard(model)
        try:
            with self._request("POST", "/api/pull", {"model": model, "stream": True}, timeout=3600) as resp:
                with self._lock:
                    self._streams[model] = resp
                for line in iter(resp.readline, b""):
                    if model in self._cancelled:
                        raise ValueError("you cancelled it")
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise ValueError(str(chunk["error"]))
                    if chunk.get("total"):
                        with self._lock:
                            self._pulls[model] = {"done_bytes": int(chunk.get("completed") or 0),
                                                  "total_bytes": int(chunk["total"])}
                        if on_progress:
                            on_progress(int(chunk.get("completed") or 0), int(chunk["total"]))
                    if chunk.get("status") == "success":
                        with self._lock:
                            self._pulls.pop(model, None)
                        return True
        except (OSError, ValueError, AttributeError) as exc:     # a closed stream (cancel) reads as an error
            why = "you cancelled it" if model in self._cancelled else f"the download stopped ({exc})"[:200]
            with self._lock:
                self._pulls[model] = {**self._pulls.get(model, {}), "error": why}
            return False
        finally:
            with self._lock:
                self._streams.pop(model, None)
        if model in self._cancelled:
            with self._lock:
                self._pulls[model] = {**self._pulls.get(model, {}), "error": "you cancelled it"}
        return False


def wait_until_answering(runtime: OllamaRuntime, model: str, seconds: float = 30.0) -> dict:
    """Wait up to *seconds* while the state is ``loading`` (03 1.4: a short wait, then a call)."""
    end = time.monotonic() + seconds
    st = runtime.state(model)
    while st["state"] == LOADING and time.monotonic() < end:
        time.sleep(0.5)
        st = runtime.state(model)
    return st


__all__ = ["OllamaRuntime", "SERVER_DOWN", "ThinkFilter", "strip_thinking", "wait_until_answering"]
