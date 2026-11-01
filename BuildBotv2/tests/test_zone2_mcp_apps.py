"""Lane L2: the MCP client over HTTP and stdio with the package token; read_app and open_app."""

import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from puffpai_core.zone2 import apps, mcp

FIXTURE = Path(__file__).parent / "fixtures" / "fake_mcp" / "server.py"
TOKEN = "ppt_test-token"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def http_mcp(tmp_path):
    """The fake package's MCP server over Streamable HTTP, refusing calls without its token."""
    import uvicorn
    sys.path.insert(0, str(FIXTURE.parent))
    try:
        import importlib
        server_mod = importlib.import_module("server")
    finally:
        sys.path.pop(0)
    server_mod.LIST = tmp_path / "list.txt"
    inner = server_mod.srv.streamable_http_app()

    async def app(scope, receive, send):
        if scope["type"] == "http":
            auth = dict(scope["headers"]).get(b"authorization", b"").decode()
            if auth != f"Bearer {TOKEN}":
                await send({"type": "http.response.start", "status": 401, "headers": []})
                await send({"type": "http.response.body", "body": b"no token"})
                return
        await inner(scope, receive, send)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield port, server_mod.LIST
    server.should_exit = True
    t.join(10)


def test_http_with_the_package_token(http_mcp):
    port, listfile = http_mcp
    row = {"id": "freshtable", "name": "FreshTable", "port": port}
    manifest = {"abilities": {"mcp": {"transport": "http", "url": "http://127.0.0.1:{port}/mcp"}}}
    good = mcp.target_for(row, manifest, TOKEN)
    assert {t["name"] for t in mcp.list_tools(good)} == {"add_to_list", "show_list"}
    assert mcp.call_tool(good, "add_to_list", {"item": "milk"})["ok"] is True
    assert listfile.read_text().strip() == "milk"
    bad = mcp.target_for(row, manifest, "ppt_wrong")
    got = mcp.call_tool(bad, "add_to_list", {"item": "eggs"})
    assert got["ok"] is False and listfile.read_text().strip() == "milk"


def test_stdio_gets_the_token_in_its_environment(tmp_path):
    listfile = tmp_path / "list.txt"
    row = {"id": "fakeshop", "name": "Fake shop"}
    manifest = {"abilities": {"mcp": {"transport": "stdio",
                                      "command": [sys.executable, str(FIXTURE), str(listfile), TOKEN]}}}
    got = mcp.call_tool(mcp.target_for(row, manifest, TOKEN, tmp_path), "add_to_list", {"item": "bread"})
    assert got["ok"] is True and listfile.read_text().strip() == "bread"
    refused = mcp.call_tool(mcp.target_for(row, manifest, None, tmp_path), "add_to_list", {"item": "jam"})
    assert refused["ok"] is False and "jam" not in listfile.read_text()


def test_targets_refuse_other_computers_and_stopped_packages():
    manifest = {"abilities": {"mcp": {"transport": "http", "url": "http://10.0.0.5:{port}/mcp"}}}
    with pytest.raises(mcp.McpError):
        mcp.target_for({"id": "x", "port": 1}, manifest, TOKEN)
    with pytest.raises(mcp.McpError) as err:
        mcp.target_for({"id": "x", "name": "X"}, {"abilities": {"mcp": {"transport": "http", "url": "u"}}}, TOKEN)
    assert err.value.code == "stopped"


class Page(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<html><head><title>World Monitor</title><script>x()</script></head><body>"
                         b"<h1>Headlines</h1><ul><li>Rain in Lisbon</li><li>Markets up</li></ul>"
                         b"<a href='/news'>More news</a></body></html>")


@pytest.fixture
def page():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_read_app_plain_read_and_stopped_app_asks_then_starts(page):
    rows = {"worldmonitor": {"id": "worldmonitor", "name": "World Monitor", "status": "stopped", "port": None,
                             "ui": {"pages": [{"id": "main", "path": "/", "show": "new_tab"}]}}}

    def find(key):
        r = rows.get(key) or next((r for r in rows.values() if r["name"].replace(" ", "").lower() == key.replace(" ", "").lower()), None)
        return (r, None) if r else None
    q = apps.read_question(find, {"id": "World Monitor"})
    assert q["kind"] == "start" and q["package"] == "worldmonitor"
    started = []

    def start(pid):
        started.append(pid)
        return {"ok": True, "port": page}
    got = apps.read_app(find, start, {"id": "worldmonitor"}, browser=False)
    assert started == ["worldmonitor"] and got["ok"] is True and got["title"] == "World Monitor"
    assert "Rain in Lisbon" in got["text"] and "x()" not in got["text"]
    rows["worldmonitor"].update(status="running", port=page)
    assert apps.read_question(find, {"id": "worldmonitor"}) is None
    opened = apps.open_app(find, {"id": "worldmonitor"})
    assert opened["open"] == [f"http://127.0.0.1:{page}/"]


def test_read_page_only_on_this_computer():
    assert apps.read_page("http://example.com/")["ok"] is False
    assert apps._plain_url("http://me:pw@127.0.0.1:5/x") == "http://127.0.0.1:5/x"
