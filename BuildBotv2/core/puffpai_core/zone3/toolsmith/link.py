"""Toolsmith's one way out of its own process: the Core API's Toolsmith-token routes (09 section 3).

Toolsmith runs commands, writes files, reads pages, searches, talks to a
model, asks Drawbridge, installs and raises Checklist steps only by asking
the Core here. Its token came on stdin (``__main__``) and is bound to one
build; it opens nothing but these routes. The transport sends to the Core on
127.0.0.1 only, never through a proxy.

A route that could not do what was asked answers 409 with an error from
``errors`` (raised again here); anything else that is not a 2xx is a
``LinkError``, which holds the job with a plain line.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .errors import CoreUnreachable, LinkError, Unreachable, from_wire

Transport = Callable[[str, str, Any, float], tuple[int, Any]]
BASE = "/v1/toolsmith/agent"
LONG = 3600.0
#: A status write the Core did not answer at all is sent again after these waits (seconds), then given up (lane I18a).
STATUS_RETRY_S = (0.5, 1.0, 2.0, 4.0)
#: Not a header any place sends: the Core's word on the owner's key for this send ("sent", "unusable", "refused").
KEY_HEADER = "x-puffpai-key"


def http_transport(api: str, token: str) -> Transport:
    """Calls to the Core API at *api* (``http://127.0.0.1:<port>``) with the Toolsmith token."""
    parts = urllib.parse.urlsplit(api)
    if parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "localhost"):
        raise LinkError("Toolsmith talks to the Core on this computer only.")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    auth = {"Authorization": f"Bearer {token}"}
    root = api.rstrip("/")

    def call(method: str, path: str, body: Any, timeout: float) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(root + path, data=data, method=method,
                                     headers={**auth, "Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=timeout) as reply:
                raw = reply.read()
                status = reply.status
        except urllib.error.HTTPError as exc:
            raw, status = exc.read(), exc.code
        except OSError as exc:
            raise CoreUnreachable(f"Toolsmith could not reach the Core ({type(exc).__name__}).") from None
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            return status, {}
    return call


class Link:
    """The Toolsmith routes, as methods. ``transport`` does the HTTP (or a test client)."""

    def __init__(self, transport: Transport, build_id: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.transport, self.build_id, self.sleep = transport, build_id, sleep

    def _call(self, method: str, path: str, body: Any = None, timeout: float = 120.0) -> Any:
        status, data = self.transport(method, BASE + path, body, timeout)
        if 200 <= status < 300:
            return data
        if status == 409 and isinstance(data, dict) and data.get("kind"):
            raise from_wire(data)
        said = data.get("error") if isinstance(data, dict) else ""
        raise LinkError(f"The Core did not do what Toolsmith asked ({status}). {said or ''}".strip())

    # the build
    def build(self) -> dict:
        return self._call("GET", "/build")

    def put_plan(self, plan: dict) -> dict:
        return self._call("PUT", "/plan", plan)

    def fits(self, package: str, ask: str) -> dict:
        """Does the brain pick one of *package*'s tools for *ask* (a job's words)? With *package* "": whose
        tool does it pick? ``{ok, asked, package, called, reason}``."""
        return self._call("POST", "/fits", {"package": package, "ask": ask}, timeout=300)

    def fits_store(self, ask: str) -> dict:
        """Lane I10c: the same ask with the PuffPAI store's apps' tools in the brain's table: whose did it pick?"""
        return self._call("POST", "/fits", {"package": "", "ask": ask, "store": True}, timeout=300)

    def now(self, line: str) -> dict:
        """What Toolsmith does now while it plans (one plain progress line for the wizard and the Desktop)."""
        return self._call("PUT", "/now", {"line": line})

    def job_status(self, job_id: str, **fields: Any) -> dict:
        """One status write. The Core not answering at all is tried again a few times (lane I18a): a job's last
        line is never lost to one dropped connection."""
        path = f"/jobs/{urllib.parse.quote(job_id)}/status"
        for wait in STATUS_RETRY_S:
            try:
                return self._call("POST", path, fields)
            except CoreUnreachable:
                self.sleep(wait)
        return self._call("POST", path, fields)

    def holds_ask(self, job_id: str) -> dict:
        """Ask the Core for the job's ONE hold card (the candidates set aside). ``{consent_id, title, names}``."""
        return self._call("POST", f"/jobs/{urllib.parse.quote(job_id)}/holds", {})

    def holds_state(self, job_id: str) -> dict:
        """Her answer: ``{state: none | pending | answered, try: [refs], skipped: [{name, ref, why}]}``."""
        return self._call("GET", f"/jobs/{urllib.parse.quote(job_id)}/holds")

    def registry(self) -> dict:
        return self._call("GET", "/registry")

    # the network and the brain
    def http_get(self, url: str, *, accept: str = "", max_bytes: int = 2_000_000, keyed: bool = False,
                 timeout_s: float | None = None, web_key: str = "") -> tuple[int, dict, bytes]:
        """One GET the Core sends. *keyed*: Toolsmith's own GitHub search or repository read asks for the owner's GitHub
        key (lane I15); a try's reads never do. The Core decides; it adds the key on its few API paths only. *timeout_s*:
        this read's limit in all (the Core keeps it within 1 to 30 seconds). *web_key*: a ``web`` row's id, for its own
        search with the owner's own key; the Core decides, and a key it refuses comes back as the word "refused"."""
        ask = {"url": url, "accept": accept, "max_bytes": max_bytes, **({"keyed": True} if keyed else {}),
               **({} if timeout_s is None else {"timeout_s": timeout_s}), **({"web_key": web_key} if web_key else {})}
        got = self._call("POST", "/web", ask)
        if got.get("error"):                       # sent, but no answer: the Core's plain reason and the class
            raise Unreachable(str(got.get("said") or "That address could not be reached."), str(got["error"])[:60])
        headers = dict(got.get("headers")) if isinstance(got.get("headers"), dict) else {}
        if got.get("key") in ("sent", "unusable", "refused"):
            headers[KEY_HEADER] = got["key"]       # the Core's word on the key, never the key (lane I15)
        return int(got.get("status") or 0), headers, str(got.get("text") or "").encode("utf-8")

    def read_public(self, url: str, job_id: str = "", start: int = 0) -> dict:
        """One public page the Core reads for this cloud build, under her plan Yes (D121): ``{ok: True, status, url,
        title, text, links: [{text, url}], cut, total}`` (*start*: where in the page's text this window begins; ``cut``:
        there is more after it; ``links``: on the first window only), or ``{ok: False, said}``."""
        return self._call("POST", "/read", {"url": url, "job_id": job_id, "start": start}, 180.0)

    def screen_queries(self, queries: list[str]) -> list[str]:
        """The search queries the Core's screen keeps (no names, numbers or personal detail; lane I15)."""
        got = self._call("POST", "/queries", {"queries": list(queries)[:15]})
        return [q for q in got.get("queries") or [] if isinstance(q, str)]

    def knowledge_find(self, topics: list[str]) -> dict:
        """While planning: the Core looks for downloads about these topics, within its own time (D99)."""
        return self._call("POST", "/knowledge/find", {"topics": list(topics)[:15]}, 300.0)

    def knowledge_step(self) -> dict:
        """One step of the Core's knowledge fetch for this build (after her Yes): ``{done, now, line}``."""
        return self._call("POST", "/knowledge", {}, 300.0)

    def library_check(self, job_id: str) -> dict:
        """Lane I19e (D71): the Core proves a job no app works for by this build's own library, or leaves it.
        ``{working, line?}``."""
        return self._call("POST", f"/jobs/{urllib.parse.quote(job_id)}/library", {}, 300.0)

    def brain_json(self, system: str, user: str) -> Any:
        try:
            return self._call("POST", "/brain/json", {"system": system, "user": user}).get("answer")
        except Exception:  # noqa: BLE001 - never a veto: the caller's fallback
            return None

    def brain_answer(self, question: str, context: str) -> str:
        return str(self._call("POST", "/brain/answer", {"question": question, "context": context},
                              LONG).get("answer") or "")

    def model_turn(self, system: str, messages: list[dict], tools: list[dict], *, local_only: bool = False,
                   max_tokens: int | None = None, purpose: str = "build", tool_list: str = "") -> dict:
        """One model turn. The tools go by name; *tool_list* "find" says they are a job's look's own (``scout``)."""
        body = {"system": system, "messages": messages, "tools": [{"name": t["name"]} for t in tools],
                "local_only": local_only, "purpose": purpose}
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        if tool_list:
            body["tool_list"] = tool_list
        return self._call("POST", "/model", body, LONG)

    def model_probe(self) -> bool:
        return bool(self._call("POST", "/model", {"probe": True}).get("ok"))

    def read_page(self, url: str) -> dict:
        return self._call("POST", "/page", {"url": url}, 300.0)

    def read_app(self, pkg_id: str, path: str = "") -> dict:
        return self._call("GET", f"/apps/{urllib.parse.quote(pkg_id)}/read", None, 300.0)

    def found_add(self, job_id: str, facts: dict) -> dict:
        """D121: a project found after her Yes, or an app to build (``made``), kept on the job, never on the plan.
        ``{cand}``, or a refusal in plain words."""
        return self._call("POST", f"/jobs/{urllib.parse.quote(job_id)}/found", facts)

    # a try
    def try_open(self, job_id: str, ref: str) -> dict:
        return self._call("POST", "/tries", {"job_id": job_id, "ref": ref})

    def _t(self, try_id: str, what: str) -> str:
        return f"/tries/{urllib.parse.quote(try_id)}/{what}"

    def try_download(self, try_id: str) -> dict:
        return self._call("POST", self._t(try_id, "download"), {}, LONG)

    def try_check(self, try_id: str) -> dict:
        return self._call("POST", self._t(try_id, "check"), {}, LONG)

    @staticmethod
    def _program(request: Any) -> dict:
        """``{program, args, cwd}`` as given, or one plain line as ``{"command": line}`` (the Core reads it)."""
        if isinstance(request, dict):
            return {k: request[k] for k in ("program", "args", "cwd", "command") if k in request}
        return {"command": str(request or "")}

    def try_run(self, try_id: str, request: Any) -> dict:
        return self._call("POST", self._t(try_id, "run"), self._program(request), LONG)

    def try_start(self, try_id: str, request: Any) -> dict:
        return self._call("POST", self._t(try_id, "start"), self._program(request))

    def try_mcp(self, try_id: str, request: Any, tool: str = "", args: Any = None) -> dict:
        """D124: the Core starts this program as an MCP server over stdio, asks for its tools and makes one real call,
        then stops it. ``{ok, tools, answer, said}``, or ``not_run`` as any program."""
        return self._call("POST", self._t(try_id, "mcp"), {**self._program(request), "tool": tool, "tool_args": args or {}}, LONG)

    def try_fs(self, try_id: str, op: str, **fields: Any) -> dict:
        """The Core's file tools for the try folder: read, list, copy, move, mkdir, delete."""
        return self._call("POST", self._t(try_id, "fs"), {"op": op, **fields})

    def try_stop(self, try_id: str, pid: int | None = None) -> dict:
        return self._call("POST", self._t(try_id, "stop"), {} if pid is None else {"pid": int(pid)})

    def try_log(self, try_id: str) -> dict:
        return self._call("GET", self._t(try_id, "log"))

    def try_write(self, try_id: str, path: str, text: str) -> dict:
        return self._call("POST", self._t(try_id, "files"), {"path": path, "text": text})

    def try_end(self, try_id: str, remove: bool = True) -> dict:
        return self._call("POST", self._t(try_id, "end"), {"remove": remove}, 300.0)

    def try_add(self, try_id: str, built_with: str, manifest: dict | None = None) -> dict:
        """Hand the try over with its manifest: the Core writes it as ``.buildbot/puffpai.json`` itself."""
        return self._call("POST", self._t(try_id, "add"), {"built_with": built_with, "manifest": manifest}, LONG)

    def try_add_state(self, try_id: str) -> dict:
        return self._call("GET", self._t(try_id, "add"), None, LONG)

    # Checklist steps
    def step_add(self, job_id: str, step: dict, try_id: str = "") -> dict:
        return self._call("POST", "/steps", {"job_id": job_id, "step": step, "try_id": try_id})

    def step_state(self, step_id: str) -> str:
        return str(self._call("GET", f"/steps/{urllib.parse.quote(step_id)}").get("state") or "gone")


__all__ = ["BASE", "Link", "STATUS_RETRY_S", "Transport", "http_transport"]
