# Harvested from PuffPAIBuildBot@774e27a4:src/puffpai/office_docs.py (adapted)
"""Office files: make and change Word, Excel and PowerPoint files from chat, as a PuffPAI package.

One small server, bound to 127.0.0.1:
- ``/health``      "ok"
- ``/mcp``         the MCP server (JSON-RPC over HTTP)

Every route refuses a request without this package's PUFFPAI_TOKEN.

Where a file may go is the Core's one rule (``needs.owner_files``, ``GET /v1/owner-files``): a new file goes in
``My files\\Made by <bot name>``, and a file the bot made changes at once. The Core asks her Yes before this tool ever
hears about any other file in her data folder or a folder she linked, and refuses any file anywhere else. A file in a
linked folder has its original kept beside it first, as "<name> (before BuildBot).<ext>".

Nothing leaves the computer. Uses python-docx, openpyxl and python-pptx (requirements.txt), inside this package's venv.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

NAME = "office"
VERSION = "1.0.1"
MCP_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
MAX_BODY = 2 * 1024 * 1024
KINDS = {"word": ".docx", "excel": ".xlsx", "powerpoint": ".pptx"}
OPEN_LINE = "Open it in Office from your files on the Knowledge page"
CONTENT_MAX = 200_000
CHANGES_MAX = 50
BEFORE = " (before BuildBot)"
#: What the Core's gate puts in place of a private detail. With changing documents on, a local brain's words reach here
#: real (D140); a mark can still come when the vault is locked, or from an earlier turn the brain only has as a mark.
HANDLE = re.compile(r"\{\{vault:[^{}]{1,80}\}\}")


class Refused(Exception):
    """A plain line for the owner: nothing was changed."""


class CoreFiles:
    """The Core's one reading of her folders, asked each time: she may link a folder or rename the bot while this runs."""

    def __init__(self, api: str | None, token: str | None) -> None:
        self.api = (api or "").rstrip("/")
        self.token = token or ""

    def ask(self, path: str | None = None) -> dict:
        url = self.api + "/v1/owner-files" + ("?" + urllib.parse.urlencode({"path": path}) if path is not None else "")
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token}"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # the Core is on this computer
        try:
            with opener.open(req, timeout=10) as r:
                return json.loads(r.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError):
            raise Refused("I could not ask BuildBot where your files are, so nothing was changed.") from None

    def made_folder(self) -> str:
        return str(self.ask().get("made") or "")

    def where(self, path: str) -> dict:
        return self.ask(path)


def _safe(name: Any, fallback: str) -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(name or ""))
    text = " ".join(text.split()).strip(" .")[:80]
    return text or fallback


def _unique(folder: str, stem: str, ext: str) -> str:
    path = os.path.join(folder, stem + ext)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{stem} ({n}){ext}")
        n += 1
    return path


def keep_original(real: str) -> str:
    """Copy *real* to "<name> (before BuildBot)<ext>" beside it (a new name when one is there already)."""
    stem, ext = os.path.splitext(os.path.basename(real))
    copy = _unique(os.path.dirname(real), stem + BEFORE, ext)
    shutil.copy2(real, copy)
    return copy


def _lines(content: Any) -> list[str]:
    if isinstance(content, list):
        return [str(x) if not isinstance(x, list) else ", ".join(map(str, x)) for x in content]
    return str(content or "")[:CONTENT_MAX].splitlines()


def _cell(text: str) -> Any:
    try:
        return int(text) if re.fullmatch(r"-?\d{1,15}", text) else float(text) \
            if re.fullmatch(r"-?\d+\.\d+", text) else text
    except ValueError:
        return text


def _rows(content: Any) -> list[list[Any]]:
    """Rows for a sheet: a list of lists as given, else one row per line, split on tabs, then on commas."""
    if isinstance(content, list):
        return [list(r) if isinstance(r, (list, tuple)) else [r] for r in content]
    out = []
    for line in str(content or "")[:CONTENT_MAX].splitlines():
        if not line.strip():
            continue
        cells = line.split("\t") if "\t" in line else line.split(",")
        out.append([_cell(c.strip()) for c in cells])
    return out


def _slides(title: str, content: Any) -> list[tuple[str, list[str]]]:
    """Slides from text: a line "---" or a "# heading" starts a new slide; a slide's first line is its title, the
    rest its points."""
    slides: list[tuple[str, list[str]]] = []
    cur: list[str] = []

    def flush() -> None:
        if cur:
            slides.append((cur[0].lstrip("# ").strip(), [c.lstrip("-* ").strip() for c in cur[1:]]))
    for line in _lines(content):
        s = line.strip()
        if s == "---" or (s.startswith("#") and cur):
            flush()
            cur = [s] if s != "---" else []
            continue
        if s:
            cur.append(s)
    flush()
    return slides or [(title, [])]


def _word_add(doc: Any, lines: list[str]) -> None:
    para: list[str] = []

    def flush() -> None:
        if para:
            doc.add_paragraph(" ".join(para))
            para.clear()
    for line in lines:
        s = line.strip()
        if not s:
            flush()
        elif s.startswith("#"):
            flush()
            level = min(len(s) - len(s.lstrip("#")), 3)
            doc.add_heading(s.lstrip("# ").strip(), level=level)
        elif s.startswith(("- ", "* ")):
            flush()
            doc.add_paragraph(s[2:].strip(), style="List Bullet")
        else:
            para.append(s)
    flush()


def _pptx_add(prs: Any, slides: list[tuple[str, list[str]]]) -> None:
    for head, points in slides:
        layout = prs.slide_layouts[1 if points else 0]
        slide = prs.slides.add_slide(layout)
        slide.shapes.title.text = head
        if points and len(slide.placeholders) > 1:
            body = slide.placeholders[1].text_frame
            body.text = points[0]
            for p in points[1:]:
                body.add_paragraph().text = p


def _all_paragraphs(doc: Any) -> list:
    out = list(doc.paragraphs)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                out += list(cell.paragraphs)
    return out


def _held_line(content: Any) -> str:
    n = len(HANDLE.findall(json.dumps(content, ensure_ascii=False))) if content else 0
    if not n:
        return ""
    what = "1 private detail" if n == 1 else f"{n} private details"
    return f" {what} from your vault went in as a {{{{vault:...}}}} mark, not the real text. Type the real one in yourself."


class Office:
    def __init__(self, files: Any) -> None:
        self.files = files

    def make_document(self, kind: str = "", title: str = "", content: Any = "") -> str:
        kind = str(kind or "").strip().lower()
        if kind not in KINDS:
            raise Refused("Say word, excel or powerpoint.")
        title = " ".join(str(title or "").split())[:120] or "Untitled"
        folder = self.files.made_folder()
        if not folder:
            raise Refused("I could not ask BuildBot where your files are, so nothing was made.")
        os.makedirs(folder, exist_ok=True)
        path = _unique(folder, _safe(title, "Untitled"), KINDS[kind])
        if kind == "word":
            import docx
            doc = docx.Document()
            doc.add_heading(title, level=0)
            _word_add(doc, _lines(content))
            doc.save(path)
        elif kind == "excel":
            import openpyxl
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = re.sub(r"[\[\]:*?/\\']", " ", _safe(title, "Sheet"))[:31].strip() or "Sheet"
            for row in _rows(content):
                ws.append(row)
            wb.save(path)
        else:
            import pptx
            prs = pptx.Presentation()
            slides = _slides(title, content)
            if slides[0][0] != title:
                slides = [(title, [])] + slides
            _pptx_add(prs, slides)
            prs.save(path)
        return f"Made {os.path.basename(path)} in your files: {path}. {OPEN_LINE}.{_held_line(content)}"

    def edit_document(self, path: str = "", changes: Any = None) -> str:
        """``changes``: a list of ``{"append": text}`` (paragraphs, or slides for PowerPoint), ``{"rows": [[...], ...]}``
        (Excel) or ``{"replace": old, "with": new}``. The Core has asked her Yes for any file the bot did not make."""
        got = self.files.where(str(path or ""))
        if got.get("line") or not got.get("path"):
            raise Refused(str(got.get("line") or "I could not find that file."))
        real, place = str(got["path"]), got.get("place")
        ext = os.path.splitext(real)[1].lower()
        if ext not in KINDS.values():
            raise Refused("I can change Word, Excel and PowerPoint files only.")
        todo = [c for c in (changes if isinstance(changes, list) else [changes]) if isinstance(c, dict)][:CHANGES_MAX]
        if not todo:
            raise Refused("Say what to change.")
        kept = ""
        if place == "linked":
            try:
                kept = keep_original(real)
            except OSError:
                raise Refused("I could not keep a copy of the original, so it was not changed.") from None
        try:
            done = self._change(real, ext, todo)
        except Exception:  # noqa: BLE001 - a file it cannot read: the plain line, never the file's words
            raise Refused("I could not open that file to change it.") from None
        if not done:
            return "Nothing in it matched; it is unchanged."
        line = f"Changed {os.path.basename(real)}."
        if kept:
            line += f" The original is kept as {os.path.basename(kept)}."
        return f"{line} {OPEN_LINE}.{_held_line(todo)}"

    def _change(self, real: str, ext: str, todo: list[dict]) -> int:
        done = 0
        if ext == ".docx":
            import docx
            doc = docx.Document(real)
            for c in todo:
                if c.get("replace"):
                    old, new = str(c["replace"]), str(c.get("with") or "")
                    for p in _all_paragraphs(doc):
                        if old in p.text:
                            p.text = p.text.replace(old, new)
                            done += 1
                if c.get("append"):
                    _word_add(doc, _lines(c["append"]))
                    done += 1
            doc.save(real)
        elif ext == ".xlsx":
            import openpyxl
            wb = openpyxl.load_workbook(real)
            ws = wb.active
            for c in todo:
                if c.get("replace"):
                    old, new = str(c["replace"]), str(c.get("with") or "")
                    for sheet in wb.worksheets:
                        for row in sheet.iter_rows():
                            for cell in row:
                                if isinstance(cell.value, str) and old in cell.value:
                                    cell.value = cell.value.replace(old, new)
                                    done += 1
                for key in ("rows", "append"):
                    if c.get(key):
                        for r in _rows(c[key]):
                            ws.append(r)
                            done += 1
            wb.save(real)
        else:
            import pptx
            prs = pptx.Presentation(real)
            for c in todo:
                if c.get("replace"):
                    old, new = str(c["replace"]), str(c.get("with") or "")
                    for slide in prs.slides:
                        for shape in slide.shapes:
                            if not shape.has_text_frame:
                                continue
                            for p in shape.text_frame.paragraphs:
                                for run in p.runs:
                                    if old in run.text:
                                        run.text = run.text.replace(old, new)
                                        done += 1
                if c.get("append"):
                    slides = _slides("", c["append"])
                    _pptx_add(prs, [s for s in slides if s[0] or s[1]])
                    done += 1
            prs.save(real)
        return done


# -- MCP ------------------------------------------------------------------------------------------

TOOLS = {
    "make_document": ("Make a Word, Excel or PowerPoint file on this computer, in the owner's files. Word: plain text, "
                      "'# ' headings, '- ' points. Excel: one row per line, cells split by commas or tabs. PowerPoint: "
                      "'---' or '# ' starts a slide; a slide's first line is its title, the rest its points.",
                      {"kind": {"type": "string", "enum": list(KINDS)},
                       "title": {"type": "string", "description": "The file's title (its name too)."},
                       "content": {"type": "string", "description": "What goes in it."}},
                      ["kind", "title", "content"]),
    "edit_document": ("Change a Word, Excel or PowerPoint file in the owner's files or a folder she linked: add "
                      "paragraphs, rows or slides, or replace text.",
                      {"path": {"type": "string", "description": "The file's full path, or its name if the bot made it."},
                       "changes": {"type": "array", "items": {"type": "object", "properties": {
                           "append": {"type": "string", "description": "Text to add at the end."},
                           "rows": {"type": "array", "items": {"type": "array", "items": {}},
                                    "description": "Excel rows to add."},
                           "replace": {"type": "string", "description": "Text to find."},
                           "with": {"type": "string", "description": "What replaces it."}},
                           "additionalProperties": False}}},
                      ["path", "changes"]),
}


def tool_list() -> list[dict]:
    return [{"name": name, "description": desc, "annotations": {"readOnlyHint": False},
             "inputSchema": {"type": "object", "properties": props, "required": need, "additionalProperties": False}}
            for name, (desc, props, need) in TOOLS.items()]


def mcp_answer(app: Office, msg: dict) -> dict | None:
    method = msg.get("method") if isinstance(msg, dict) else None
    mid = msg.get("id") if isinstance(msg, dict) else None
    if mid is None and method:
        return None                          # a notification

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        return ok({"protocolVersion": asked if asked in MCP_VERSIONS else MCP_VERSIONS[0], "capabilities": {"tools": {}},
                   "serverInfo": {"name": NAME, "version": VERSION}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": tool_list()})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOLS or not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "No such tool."}}
        try:
            text = getattr(app, name)(**{k: v for k, v in args.items() if k in TOOLS[name][1]})
            return ok({"content": [{"type": "text", "text": text}]})
        except Refused as exc:
            return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except ImportError:
            return ok({"content": [{"type": "text", "text": "Office files is missing a part it needs. Remove it and "
                                                            "add it again from Apps."}], "isError": True})
        except OSError as exc:
            return ok({"content": [{"type": "text", "text": f"I could not write that file ({type(exc).__name__}). If it "
                                                            "is open in Office, close it and ask again."}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "No such method."}}


class Handler(BaseHTTPRequestHandler):
    server_version = "office/" + VERSION
    protocol_version = "HTTP/1.1"
    app: Office
    token = ""

    def log_message(self, fmt, *args):     # quiet; the Core keeps the logs it needs
        pass

    def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
        self.send_response(code)
        if body or code != 202:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _length(self) -> int:
        raw = self.headers.get("Content-Length") or "0"
        if not raw.isascii() or not raw.isdigit() or int(raw) > MAX_BODY:
            raise ValueError("Content-Length must be a whole number of bytes, at most 2 MB.")
        return int(raw)

    def _body(self):
        n = self._length()
        raw = self.rfile.read(n) if n else b""
        if len(raw) != n:
            raise ValueError("The request ended before its body did.")
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _token_ok(self) -> bool:
        if not self.token:
            return False                     # no token given at start: refuse every call
        got = self.headers.get("Authorization", "")
        got = got[7:] if got.lower().startswith("bearer ") else self.headers.get("X-PuffPAI-Key", "")
        return hmac.compare_digest(got.encode(), self.token.encode())

    def _refused(self) -> bool:
        """Every route needs the token, checked before anything else is read. A body with a sound Content-Length is
        read, so the refusal arrives whole; any other one closes the connection."""
        if self._token_ok():
            return False
        try:
            n = self._length()
            if n:
                self.rfile.read(n)
        except (ValueError, OSError):
            self.close_connection = True
        self._json(401, {"error": "Office files needs its PuffPAI token."})
        return True

    def _guarded(self, route) -> None:
        try:
            route()
        except Exception as exc:  # noqa: BLE001 - the answer says what kind of error it was
            self.close_connection = True
            try:
                self._json(500, {"error": f"Office files stopped on this request with an error ({type(exc).__name__})."})
            except Exception:  # noqa: BLE001 - the connection itself is gone
                pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        self._guarded(self._get)

    def _get(self):
        if self._refused():
            return
        path = self.path.split("?", 1)[0]
        if path == "/health":
            return self._send(200, b"ok", "text/plain")
        if path == "/mcp":
            return self._send(405, b"", "text/plain")      # no server-sent stream; POST only
        self._json(404, {"error": "Office files has no page here."})

    def do_POST(self):
        self._guarded(self._post)

    def _post(self):
        if self._refused():
            return
        if self.path.split("?", 1)[0] != "/mcp":
            return self._json(404, {"error": "Office files has no page here."})
        try:
            msg = self._body()
        except (ValueError, UnicodeDecodeError):
            self.close_connection = True
            return self._json(400, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Not JSON."}})
        answer = mcp_answer(self.app, msg)
        if answer is None:
            return self._send(202)
        self._json(200, answer)

    def do_PUT(self):
        if not self._refused():
            self._send(405, b"", "text/plain")

    def do_DELETE(self):
        if not self._refused():
            self._send(405, b"", "text/plain")


def make_server(port: int, files: Any, token: str | None) -> ThreadingHTTPServer:
    """The server, bound to 127.0.0.1 only. Call serve_forever(); stop with shutdown()."""
    handler = type("OfficeHandler", (Handler,), {"app": Office(files), "token": token or ""})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Office files for PuffPAI")
    p.add_argument("--port", type=int, default=int(os.environ.get("PUFFPAI_PORT") or 0))
    args = p.parse_args(argv)
    token = os.environ.get("PUFFPAI_TOKEN")
    if not args.port or not os.environ.get("PUFFPAI_API"):
        p.error("PuffPAI starts Office files with PUFFPAI_PORT (or --port) and PUFFPAI_API set.")
    server = make_server(args.port, CoreFiles(os.environ.get("PUFFPAI_API"), token), token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
