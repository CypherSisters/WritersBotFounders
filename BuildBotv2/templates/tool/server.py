"""__NAME__: an MCP server on 127.0.0.1:$PUFFPAI_PORT, plus GET /health. It refuses every request that does not carry
its own PUFFPAI_TOKEN, on every route (02 "PUFFPAI_TOKEN", D48d). The Core adds the token to its own calls."""

import hmac
import os
import sys

import uvicorn
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse

TOKEN = os.environ.get("PUFFPAI_TOKEN", "")
PORT = int(os.environ.get("PUFFPAI_PORT") or (sys.argv[1] if len(sys.argv) > 1 else "0"))

server = MCPServer("__ID__")


@server.tool()
def say_hello(name: str) -> str:
    """Say hello to someone by name."""
    return f"Hello, {name}!"


@server.custom_route("/health", methods=["GET"])
async def health(_request: Request):
    return JSONResponse({"ok": True})


@server.custom_route("/", methods=["GET"])
async def home(_request: Request):
    return PlainTextResponse("__NAME__ is running.")


inner = server.streamable_http_app(host="127.0.0.1")


async def app(scope, receive, send):
    """Every request needs this package's own token (D48d); nothing else gets in."""
    if scope["type"] == "http":
        given = dict(scope["headers"]).get(b"authorization", b"").decode("latin-1")
        if not TOKEN or not hmac.compare_digest(given, f"Bearer {TOKEN}"):
            await JSONResponse({"error": "This needs the package's own token."}, status_code=401)(scope, receive, send)
            return
    await inner(scope, receive, send)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning", lifespan="on")
