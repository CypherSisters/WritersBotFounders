"""A small app made by Toolsmith (D155): a page, a health address and MCP tools, standard library only. What it keeps is
described by spec.json next to this file, and its rows are in its own data folder. It reaches nothing outside this
computer. It refuses every request that does not carry its own PUFFPAI_TOKEN (02 "PUFFPAI_TOKEN", D48d). The Core adds the
token when it forwards her page and when it calls a tool."""

import hmac
import http.server
import json
import os
import pathlib
import re
import threading
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
SPEC = json.loads((HERE / "spec.json").read_text(encoding="utf-8"))
DATA = pathlib.Path(os.environ["PUFFPAI_DATA"])
DATA.mkdir(parents=True, exist_ok=True)
ROWS = DATA / "rows.json"
TOKEN = os.environ.get("PUFFPAI_TOKEN", "")
FIELDS = {f["key"]: f for f in SPEC["fields"]}
TOOLS = SPEC["tools"]
LOCK = threading.Lock()
MAX_BODY = 1024 * 1024
MAX_SHOWN = 200
LIMITS = {"text": 500, "long": 20000, "link": 2000}
PAGES = {"/": ("page.html", "text/html; charset=utf-8"), "/page.js": ("page.js", "text/javascript; charset=utf-8"),
         "/page.css": ("page.css", "text/css; charset=utf-8")}
JSON_TYPES = {"number": {"type": "number"}, "check": {"type": "boolean"}}


class Wrong(ValueError):
    """A plain line for whoever sent this."""


def clean(given, whole):
    """The fields of a row, checked. *whole*: a new row, which needs its first field."""
    if not isinstance(given, dict):
        raise Wrong("Send the fields by name.")
    unknown = [k for k in given if k not in FIELDS]
    if unknown:
        raise Wrong(f"This app has no field called {unknown[0]}.")
    out = {}
    for key, value in given.items():
        kind = FIELDS[key]["kind"]
        if value is None or value == "":
            out[key] = False if kind == "check" else ""
        elif kind == "number":
            if isinstance(value, bool):
                raise Wrong(f"{FIELDS[key]['name']} is a number.")
            try:
                out[key] = float(value) if "." in str(value) else int(value)
            except (TypeError, ValueError):
                raise Wrong(f"{FIELDS[key]['name']} is a number.") from None
        elif kind == "check":
            out[key] = value is True or str(value).lower() in ("true", "yes", "1", "on")
        elif kind == "date":
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value)):
                raise Wrong(f"{FIELDS[key]['name']} is a date, like 2026-11-01.")
            out[key] = str(value)
        elif kind == "link":
            if not re.match(r"https?://[^\s]+$", str(value), re.I) or len(str(value)) > LIMITS["link"]:
                raise Wrong(f"{FIELDS[key]['name']} is a web address, starting with http.")
            out[key] = str(value)
        else:
            out[key] = str(value)[:LIMITS[kind]]
    if whole and not any(out.get(k) for k, f in FIELDS.items() if f["kind"] in ("text", "long")):
        raise Wrong("Say what to add: this needs some words.")
    return out


def load():
    try:
        rows = json.loads(ROWS.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, ValueError):
        return []


def save(rows):
    fresh = ROWS.with_name(f"rows.{os.getpid()}.tmp")
    fresh.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    os.replace(fresh, ROWS)


def add(given):
    row = {"id": uuid.uuid4().hex[:12], "added": int(time.time()), **clean(given, True)}
    with LOCK:
        save(load() + [row])
    return row


def listed(search=""):
    words = str(search or "").lower().split()
    with LOCK:
        rows = load()
    found = [r for r in rows if all(w in " ".join(str(v) for k, v in r.items() if k in FIELDS).lower() for w in words)]
    found.reverse()
    out = {"rows": found[:MAX_SHOWN], "count": len(found)}
    numbers = [k for k, f in FIELDS.items() if f["kind"] == "number"]
    if SPEC["shape"] == "tracker" and numbers:
        out["total"] = {k: sum(r[k] for r in found if isinstance(r.get(k), (int, float))) for k in numbers}
    return out


def change(id_, given):
    fields = clean(given, False)
    with LOCK:
        rows = load()
        for row in rows:
            if row.get("id") == id_:
                row.update(fields)
                save(rows)
                return row
    raise Wrong(f"There is no {SPEC['thing']} with that id. List them first.")


def remove(id_):
    with LOCK:
        rows = load()
        left = [r for r in rows if r.get("id") != id_]
        if len(left) == len(rows):
            raise Wrong(f"There is no {SPEC['thing']} with that id. List them first.")
        save(left)
    return {"removed": id_}


# -- the brain's tools -------------------------------------------------------------------------------------------------
def _props():
    return {k: {**JSON_TYPES.get(f["kind"], {"type": "string"}), "description": f["name"]} for k, f in FIELDS.items()}


def tool_list():
    id_prop = {"id": {"type": "string", "description": f"The {SPEC['thing']}'s id, from {TOOLS['list']}"}}
    first = next((k for k, f in FIELDS.items() if f["kind"] in ("text", "long")), "")
    return [
        {"name": TOOLS["add"], "description": f"Add a new {SPEC['thing']} to {SPEC['title']}.",
         "inputSchema": {"type": "object", "properties": _props(), "required": [first]}},
        {"name": TOOLS["list"], "description": f"Show the {SPEC['things']} kept in {SPEC['title']}, newest first. Search by any words.",
         "inputSchema": {"type": "object", "properties": {"search": {"type": "string", "description": "Words to look for"}}}},
        {"name": TOOLS["change"], "description": f"Change one {SPEC['thing']} in {SPEC['title']}. Send its id and only the fields to change.",
         "inputSchema": {"type": "object", "properties": {**id_prop, **_props()}, "required": ["id"]}},
        {"name": TOOLS["remove"], "description": f"Remove one {SPEC['thing']} from {SPEC['title']}.",
         "inputSchema": {"type": "object", "properties": id_prop, "required": ["id"]}},
    ]


def call_tool(name, args):
    args = args if isinstance(args, dict) else {}
    if name == TOOLS["add"]:
        return add(args)
    if name == TOOLS["list"]:
        return listed(args.get("search"))
    if name == TOOLS["change"]:
        rest = {k: v for k, v in args.items() if k != "id"}
        return change(args.get("id"), rest)
    if name == TOOLS["remove"]:
        return remove(args.get("id"))
    raise Wrong(f"This app has no tool called {name}.")


def mcp(message):
    """One JSON-RPC message of MCP over HTTP, answered as plain JSON. None for a notification."""
    method, id_ = message.get("method"), message.get("id")
    if id_ is None:
        return None
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": SPEC["title"], "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        try:
            said, bad = call_tool(params.get("name"), params.get("arguments")), False
        except Wrong as exc:
            said, bad = str(exc), True
        result = {"content": [{"type": "text", "text": said if bad else json.dumps(said)}], "isError": bad}
    else:
        return {"jsonrpc": "2.0", "id": id_, "error": {"code": -32601, "message": "No such method."}}
    return {"jsonrpc": "2.0", "id": id_, "result": result}


# -- the address -------------------------------------------------------------------------------------------------------
class Page(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, body=b"", kind="text/plain; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status, value):
        self._send(status, json.dumps(value).encode("utf-8"), "application/json")

    def _allowed(self):
        if TOKEN and hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {TOKEN}"):
            return True
        self._read()
        self._send(401)
        return False

    def _read(self):
        try:
            size = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            size = 0
        return self.rfile.read(size) if 0 < size <= MAX_BODY else b""

    def _body(self):
        """A write from her page or the Core. Plain JSON only: another site's page can't send that without asking first."""
        raw = self._read()
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            raise Wrong("Send JSON.")
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            raise Wrong("Send JSON.") from None

    def _path(self):
        return self.path.split("?", 1)[0]

    def do_GET(self):
        if not self._allowed():
            return
        path = self._path()
        if path == "/health":
            self._send(200, b"ok")
        elif path in PAGES:
            name, kind = PAGES[path]
            self._send(200, (HERE / name).read_bytes(), kind)
        elif path == "/api/spec":
            self._json(200, {k: SPEC[k] for k in ("title", "about", "shape", "view", "thing", "things", "fields")})
        elif path == "/api/rows":
            from urllib.parse import parse_qs, urlsplit
            self._json(200, listed((parse_qs(urlsplit(self.path).query).get("search") or [""])[0]))
        else:
            self._send(405 if path == "/mcp" else 404)

    def do_POST(self):
        if not self._allowed():
            return
        path = self._path()
        try:
            body = self._body()
            if path == "/mcp":
                said = mcp(body) if isinstance(body, dict) else None
                if said is None:
                    self._send(202)
                else:
                    self._json(200, said)
            elif path == "/api/rows":
                self._json(200, add(body))
            else:
                self._send(404)
        except Wrong as exc:
            self._json(400, {"error": str(exc)})

    def _one(self):
        found = re.fullmatch(r"/api/rows/([0-9a-f]{12})", self._path())
        return found.group(1) if found else ""

    def do_PUT(self):
        if not self._allowed():
            return
        try:
            body = self._body()
            self._json(200, change(self._one(), body))
        except Wrong as exc:
            self._json(400, {"error": str(exc)})

    def do_DELETE(self):
        if not self._allowed():
            return
        self._read()
        try:
            if self._path() == "/mcp":
                self._send(405)
                return
            if self.headers.get("X-PuffPAI-App") != "1":        # another site's page can't send this without asking first
                raise Wrong("Remove this from the app's own page.")
            self._json(200, remove(self._one()))
        except Wrong as exc:
            self._json(400, {"error": str(exc)})

    def log_message(self, *args):
        pass


http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["PUFFPAI_PORT"])), Page).serve_forever()
