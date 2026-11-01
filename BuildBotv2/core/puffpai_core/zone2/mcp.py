# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/mcp_client.py (adapted)
"""The Core's MCP client: package tools for the brain (D5; 06 row "MCP client").

Servers come from Registry rows, not a saved list (06). A row says the
package and its port; the package's manifest says ``abilities.mcp``. This module never reads a manifest
file: the caller hands it the one from the Core's one reader (``Steward.read_manifest``, the file her
install Yes covered, checked by its sha256), and the folder the Core installed it in:
``{"transport": "http", "url": "http://127.0.0.1:{port}/mcp"}`` or
``{"transport": "stdio", "command": "python server.py"}``.

Every call sends the package's own ``PUFFPAI_TOKEN`` (02: the endpoint
refuses calls without it, so no other program can call a ``changes`` tool and
skip the Yes): ``Authorization: Bearer <token>`` over HTTP, and the
``PUFFPAI_TOKEN`` environment variable for a stdio server the Core starts.
With no token for the package the call is refused here ("no token for
<id>"), never sent without one. A token is a string with something in it
once spaces are taken off, written in printable characters with no space
inside (:func:`usable_token`); anything else (blank, a number, a list, a
line break) is no token at all (D50), so no ``Bearer <junk>`` is ever built.

**An outside tool server** (lane api-tools, D137): a web address the owner added herself, not a package.
Its :class:`Target` says ``outside=True``. It may have no token (its password, when it has one, is sent as
``Authorization: Bearer <password>``, or as typed when it starts with "bearer "). It may be off this computer
only through ``allowed()``, asked before EVERY connect and call: the Bridge's own answer for that exact
host and port (her Yes on the address card, D110). Anything but ``""`` is the plain line, and nothing is sent.

Each use opens a fresh session and closes it (v1's choice: small, and it
cannot leak a stuck session). The session runs on its own thread and event
loop with a timeout. Uses the official ``mcp`` SDK (2.x ``Client``).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

CONNECT_TIMEOUT = 30.0
STDIO_CONNECT_TIMEOUT = 120.0
CALL_TIMEOUT = 60.0
MAX_RESULT_CHARS = 6000
MAX_TOOLS_PER_SERVER = 40
_LOOPBACK = ("127.0.0.1", "localhost", "::1")
_TOKEN_CHARS = re.compile(r"[\x21-\x7e]{1,4096}")
_KEY_CHAR = re.compile(r"[A-Za-z0-9_\-+/=]")
_KEY_TAIL = re.compile(r"[A-Za-z0-9_\-+/=]+$")


def usable_token(value: Any) -> Optional[str]:
    """The token, stripped, when it is one: a ``str`` that is not blank after
    ``strip()``, in printable characters with no space inside. Else None."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if _TOKEN_CHARS.fullmatch(value) else None


class McpError(Exception):
    """A plain sentence for the owner, and a short code for the program."""

    def __init__(self, message: str, code: str = "failed") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class Target:
    """Where one package's MCP server is. ``server`` is for tests: an in-process MCPServer."""

    package: str
    kind: str                         # http | stdio | inproc
    url: str = ""
    command: str = ""
    args: list[str] = field(default_factory=list)
    cwd: str | None = None
    token: str | None = None
    server: Any = None
    outside: bool = False             # an outside tool server she added (D137), never a package
    allowed: Optional[Callable[[], str]] = None    # outside, off this computer: "" when the Bridge allows it now
    name: str = ""                    # the name in plain lines, for an outside server
    refused: list = field(default_factory=list, init=False, repr=False, compare=False)   # its 401/403 answers


def outside_auth(value: Any) -> Optional[str]:
    """The ``Authorization`` value for an outside server's password, or None when it can't be sent: one line,
    printable, no space inside, or "bearer " and then that (kept as typed, v1)."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if value[:7].lower() == "bearer ":
        return value if usable_token(value[7:]) else None
    token = usable_token(value)
    return f"Bearer {token}" if token else None


def _filled(word: str, row: dict, folder: Path) -> str:
    """A stdio command's placeholders, as Steward fills them for its other commands (D124): its own Python when it has
    one, its running port, its folder. A word with none stays as it is."""
    from ..steward import noshell
    from ..steward.steward import venv_python
    if "{" not in word:
        return word
    own = venv_python(folder)
    return (word.replace("{python}", str(own) if own.is_file() else sys.executable)
            .replace("{node}", noshell.node_exe() or "node").replace("{pkg}", str(folder))
            .replace("{port}", str(row.get("port") or "")))


def target_for(row: dict, manifest: Optional[dict], token: Optional[str], package_dir: Optional[Path] = None) -> Target:
    """The target for a Registry row. Raises :class:`McpError` with a plain reason."""
    mcp = ((manifest or {}).get("abilities") or {}).get("mcp")
    if not isinstance(mcp, dict):
        raise McpError(f"{row.get('name') or row['id']} has no MCP server.", "none")
    kind = str(mcp.get("transport") or "")
    if kind == "http":
        if not row.get("port"):
            raise McpError(f"{row.get('name') or row['id']} is not running.", "stopped")
        url = str(mcp.get("url") or "").replace("{port}", str(row["port"]))
        host = (urlsplit(url).hostname or "").lower()
        if host not in _LOOPBACK:
            raise McpError("A package's MCP server must be on this computer (127.0.0.1).", "address")
        return Target(package=row["id"], kind="http", url=url, token=usable_token(token))
    if kind == "stdio":
        words = mcp.get("command")
        words = shlex.split(words, posix=(os.name != "nt")) if isinstance(words, str) else list(words or [])
        words = [w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in "\"'" else w for w in words]
        if not words:
            raise McpError(f"{row.get('name') or row['id']} names no MCP command.", "command")
        if package_dir is None:                  # never in the Core's own folder
            raise McpError(f"{row.get('name') or row['id']}'s folder is not known, so its MCP server was not "
                           "started.", "command")
        words = [_filled(w, row, Path(package_dir)) for w in words]
        return Target(package=row["id"], kind="stdio", command=words[0], args=words[1:],
                      cwd=str(package_dir) if package_dir else None, token=usable_token(token))
    raise McpError(f"{row.get('name') or row['id']} names an MCP transport the Core does not know.", "transport")


# -- talking to a server ----------------------------------------------------

def _client(target: Target, stack: Any):
    from mcp import Client
    if target.kind == "inproc":
        return Client(target.server)            # tests only: an in-process server object
    if target.outside:
        return _outside_client(target, stack)
    token = usable_token(target.token)
    if token is None:
        raise McpError(f"no token for {target.package}", "token")   # fail closed: never call tokenless
    if target.kind == "stdio":
        from mcp.client.stdio import StdioServerParameters
        env = {"PUFFPAI_TOKEN": token}
        if target.cwd:                           # git never looks above the package folder (gitfence)
            from ..steward.gitfence import ceiling
            env["GIT_CEILING_DIRECTORIES"] = ceiling(target.cwd)
        return Client(StdioServerParameters(command=target.command, args=target.args, env=env, cwd=target.cwd))
    if target.kind != "http":
        raise McpError(f"{target.package} names an MCP transport the Core does not know.", "transport")
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    stack.append(http)
    return Client(streamable_http_client(target.url, http_client=http))


def _outside_client(target: Target, stack: Any):
    """An outside tool server (D137): web addresses only; off this computer only when ``allowed()`` says so now."""
    from mcp import Client
    who = target.name or target.package
    if target.kind != "http":
        raise McpError(f"{who} is not at a web address.", "transport")
    if (urlsplit(target.url).hostname or "").lower() not in _LOOPBACK:
        try:
            said = target.allowed() if callable(target.allowed) else None
        except Exception:  # noqa: BLE001 - unsure means no: nothing is sent
            said = None
        if said != "":
            raise McpError(str(said or f"{who} needs your Yes on the Bridge page first. Nothing was sent."), "bridge")
    headers = {}
    if target.token is not None:
        auth = outside_auth(target.token)
        if auth is None:
            raise McpError(f"The password kept for {who} can't be sent. Add {who} again with its password.", "token")
        headers["Authorization"] = auth
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client
    http = create_mcp_http_client(headers=headers)
    target.refused.clear()

    async def seen(response) -> None:              # the SDK keeps no status, so a refused password reads as one here
        if response.status_code in (401, 403):
            target.refused.append(response.status_code)
    http.event_hooks = {"response": [seen]}
    stack.append(http)
    return Client(streamable_http_client(target.url, http_client=http))


async def _with_client(target: Target, work: Callable[[Any], Any]) -> Any:
    owned: list = []
    client = _client(target, owned)
    try:
        if owned:
            async with owned[0]:
                async with client:
                    return await work(client)
        async with client:
            return await work(client)
    finally:
        owned.clear()


def _leaf(exc: BaseException) -> BaseException:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


def _plain(exc: BaseException, target: Target) -> McpError:
    exc = _leaf(exc)
    if isinstance(exc, McpError):
        return exc
    who = target.name or target.package
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if target.outside and target.refused:
        status = target.refused[-1]
    if status in (401, 403):
        if target.outside:
            return McpError(f"{who} did not accept its password." if target.token is not None else
                            f"{who} asks for a password. Add it on the Developer page, with its password.", "token")
        return McpError(f"{who} did not accept the Core's token.", "token")
    if status == 404:
        return McpError(f"{who} has no MCP server at that address.", "missing")
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return McpError(f"{who} did not answer in time.", "timeout")
    if isinstance(exc, FileNotFoundError):
        return McpError(f"This computer could not find the command {who} names.", "command")
    if isinstance(exc, OSError) or "connect" in type(exc).__name__.lower():
        return McpError(f"Could not reach {who}. It may not be running.", "unreachable")
    said = str(getattr(getattr(exc, "error", None), "message", "") or exc or "")
    return McpError(f"{who} did not work: {said[:160] or type(exc).__name__}.", "failed")


def _run(target: Target, work: Callable[[Any], Any], timeout: float) -> Any:
    box: dict[str, Any] = {}

    def session() -> None:
        try:
            box["value"] = asyncio.run(asyncio.wait_for(_with_client(target, work), timeout))
        except BaseException as exc:  # noqa: BLE001 - handed back below
            box["error"] = exc
    worker = threading.Thread(target=session, daemon=True, name=f"puffpai-mcp-{target.package}")
    worker.start()
    worker.join(timeout + 10)
    if worker.is_alive():
        raise McpError(f"{target.package} did not answer in time.", "timeout")
    if "error" in box:
        raise _plain(box["error"], target)
    return box.get("value")


def _tool_rows(listed: Any) -> list[dict]:
    rows = []
    for tool in getattr(listed, "tools", None) or []:
        name = getattr(tool, "name", "")
        if not isinstance(name, str) or not name:
            continue
        schema = getattr(tool, "input_schema", None)
        if schema is None:
            schema = getattr(tool, "inputSchema", None)
        rows.append({"name": name, "description": str(getattr(tool, "description", "") or "")[:1000],
                     "input_schema": schema if isinstance(schema, dict) else {},
                     "annotations": _hints(getattr(tool, "annotations", None))})
    return rows[:MAX_TOOLS_PER_SERVER]


def _hints(annotations: Any) -> dict:
    """The two MCP hints Drawbridge reads (X27), as plain booleans; anything else is dropped."""
    out = {}
    for key, snake in (("readOnlyHint", "read_only_hint"), ("destructiveHint", "destructive_hint")):
        for name in (key, snake):                 # the wire name, and mcp 2.x's attribute name
            value = annotations.get(name) if isinstance(annotations, dict) else getattr(annotations, name, None)
            if isinstance(value, bool):
                out[key] = value
                break
    return out


def list_tools(target: Target) -> list[dict]:
    """``[{name, description, input_schema, annotations}]`` from the server's ``list_tools``."""
    async def work(client):
        return _tool_rows(await client.list_tools())
    return _run(target, work, STDIO_CONNECT_TIMEOUT if target.kind == "stdio" else CONNECT_TIMEOUT)


def result_text(result: Any) -> str:
    """A tool answer as text for the model (v1 ``_result_text``)."""
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        kind = getattr(block, "type", "")
        if kind == "text":
            parts.append(str(getattr(block, "text", "")))
        elif kind == "image":
            parts.append("[a picture]")
        elif kind == "audio":
            parts.append("[a sound]")
        elif kind == "resource":
            parts.append(str(getattr(getattr(block, "resource", None), "text", "") or "[a file]"))
        elif kind == "resource_link":
            parts.append(f"[a link: {getattr(block, 'uri', '')}]")
    structured = getattr(result, "structured_content", None)
    if structured is None:
        structured = getattr(result, "structuredContent", None)
    if not parts and structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    text = "\n".join(p for p in parts if p).strip() or "(the tool sent back nothing)"
    if getattr(result, "is_error", None) or getattr(result, "isError", None):
        text = "The tool said there was a problem: " + text
    if len(text) > MAX_RESULT_CHARS:
        head = text[:MAX_RESULT_CHARS]
        if _KEY_CHAR.match(text, MAX_RESULT_CHARS):
            head = _KEY_TAIL.sub("", head)          # no half of a key is left at the cut
        text = head + "\n[cut short]"
    return text


def call_tool(target: Target, tool: str, arguments: Optional[dict] = None, *, timeout: float = CALL_TIMEOUT) -> dict:
    """``{"ok", "text", "error"}``: one tool call. Never raises."""
    args = arguments if isinstance(arguments, dict) else {}

    async def work(client):
        res = await client.call_tool(tool, args)
        failed = bool(getattr(res, "is_error", None) or getattr(res, "isError", None))
        return {"ok": not failed, "text": result_text(res)}
    try:
        return _run(target, work, timeout)
    except McpError as exc:
        return {"ok": False, "text": exc.message, "error": exc.code}


__all__ = ["CALL_TIMEOUT", "McpError", "Target", "call_tool", "list_tools", "outside_auth", "result_text", "target_for",
           "usable_token"]
