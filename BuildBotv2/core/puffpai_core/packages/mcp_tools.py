"""Read a package's tool list from its own MCP server (02: ``list_tools`` is the source of truth).

The Core calls the package's MCP endpoint with the package's own
``PUFFPAI_TOKEN`` (``Authorization: Bearer``); the endpoint must refuse calls
without it (``refuses_without_token`` checks that, for ``puffpai check`` item 4
and the floor suite). Stdio servers are started on demand for the call and
end with it. Uses the official ``mcp`` SDK (v2 ``Client``).

The brain's own MCP client (tool calls, lane L2) is separate; this module only lists.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..steward.steward import fill, split_command


class McpError(RuntimeError):
    pass


def endpoint(manifest: dict, values: dict[str, str]) -> tuple[str, Any] | None:
    """``("http", url)`` or ``("stdio", argv)`` from ``abilities.mcp``, placeholders filled; None if none."""
    mcp = (manifest.get("abilities") or {}).get("mcp")
    if not mcp:
        return None
    if mcp["transport"] == "http":
        return "http", fill(mcp["url"], values)
    command = mcp["command"]
    argv = split_command(command) if isinstance(command, str) else list(command)
    return "stdio", [fill(a, values) for a in argv]


async def _list(target: Any, token: str | None, env: dict | None, cwd: str | None, timeout_s: float) -> list[dict]:
    import anyio
    from mcp import Client, StdioServerParameters
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    with anyio.fail_after(timeout_s):
        if isinstance(target, str):
            headers = {"Authorization": f"Bearer {token}"} if token else None
            async with create_mcp_http_client(headers=headers) as http:
                async with Client(streamable_http_client(target, http_client=http)) as client:
                    result = await client.list_tools()
        else:
            if cwd:                                  # git never looks above the package folder (gitfence)
                from ..steward.gitfence import ceiling
                env = {**(env or {}), "GIT_CEILING_DIRECTORIES": ceiling(cwd)}
            params = StdioServerParameters(command=target[0], args=list(target[1:]), env=env, cwd=cwd)
            async with Client(params) as client:
                result = await client.list_tools()
    out = []
    for tool in result.tools:
        notes = tool.annotations
        out.append({
            "name": tool.name,
            "description": (tool.description or "").strip(),
            "input_schema": tool.input_schema or {},
            "read_only": getattr(notes, "read_only_hint", None) if notes else None,
            "destructive": getattr(notes, "destructive_hint", None) if notes else None,
        })
    return out


def list_tools(manifest: dict, values: dict[str, str], token: str | None, *, env: dict | None = None,
               cwd: Path | str | None = None, timeout_s: float = 20) -> list[dict]:
    """``[{name, description, input_schema, read_only, destructive}]``. Raises :class:`McpError`."""
    ep = endpoint(manifest, values)
    if ep is None:
        raise McpError("It has no MCP server (abilities.mcp).")
    kind, target = ep
    try:
        return asyncio.run(_list(target, token, env if kind == "stdio" else None,
                                 str(cwd) if cwd else None, timeout_s))
    except BaseException as exc:  # the SDK raises exception groups; any failure is "no tools"
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise McpError(f"Its MCP server did not list its tools ({_first(exc)}).") from None


def _first(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return type(exc).__name__ + (f": {exc}" if str(exc) else "")


def refuses_without_token(url: str, timeout_s: float = 10) -> bool:
    """True when an MCP ``initialize`` with no credential is refused (HTTP 401 or 403)."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "puffpai-check", "version": "1"}}}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            return False if resp.status < 400 else resp.status in (401, 403)
    except urllib.error.HTTPError as exc:
        return exc.code in (401, 403)
    except OSError:
        return False


__all__ = ["McpError", "endpoint", "list_tools", "refuses_without_token"]
