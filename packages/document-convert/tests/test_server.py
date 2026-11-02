"""Document convert's own server (document-convert/server.py): the four tools, the missing
LibreOffice answer, the token on every route and a refused input. LibreOffice itself is
never run: the tests stand in for it. The server listens on a free port on 127.0.0.1 and
is stopped by the test that started it."""

import base64
import contextlib
import importlib.util
import io
import json
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
TOKEN = "test-token-" + "t" * 20

_spec = importlib.util.spec_from_file_location("document_convert_server", PKG / "server.py")
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)

_open = urllib.request.build_opener(urllib.request.ProxyHandler({})).open


@contextlib.contextmanager
def running(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.fixture
def data(tmp_path):
    return tmp_path / "data" / "document-convert"


@pytest.fixture
def base(data):
    with running(dc.make_server(0, data, TOKEN)) as url:
        yield url


def call(url, method="GET", body=None, token=TOKEN):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    raw = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=raw, method=method, headers=h)
    try:
        with _open(req, timeout=10) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text and "json" in r.headers.get("Content-Type", "") else text)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        return e.code, (json.loads(text) if text.startswith("{") else text)


def mcp(base, method, params=None, token=TOKEN):
    return call(base + "/mcp", "POST", {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}, token)


def tool(base, name, /, error=False, **args):
    code, out = mcp(base, "tools/call", {"name": name, "arguments": args})
    assert code == 200 and out["result"]["isError"] is error, out
    return out["result"]["content"][0]["text"]


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def docx(*paragraphs: str) -> bytes:
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def test_every_route_needs_the_token(base):
    assert call(base + "/health", token=None)[0] == 401
    assert call(base + "/health", token="wrong")[0] == 401
    assert mcp(base, "tools/list", token=None)[0] == 401
    assert call(base + "/health") == (200, "ok")
    code, out = mcp(base, "tools/list")
    assert code == 200 and [t["name"] for t in out["result"]["tools"]] == ["convert_to_pdf", "read_document_text", "pdf_to_word", "list_converted"]


def test_convert_runs_libreoffice_with_an_argument_list_and_keeps_the_pdf(base, data, monkeypatch):
    seen = []

    def fake_run(argv, timeout):
        seen.append((argv, timeout))
        out = Path(argv[argv.index("--outdir") + 1])
        (out / (Path(argv[-1]).stem + ".pdf")).write_bytes(b"%PDF-1.7 fake")
        return 0, ""

    monkeypatch.setattr(dc, "find_soffice", lambda: "soffice")
    monkeypatch.setattr(dc, "run_soffice", fake_run)
    said = tool(base, "convert_to_pdf", file_name="../Our Plan; rm -rf.docx", mime="", content_b64=b64(docx("hi")))
    argv, timeout = seen[0]
    assert timeout == 120 and argv[:2] == ["soffice", "--headless"] and "--convert-to" in argv
    assert Path(argv[-1]).name == "Our Plan_ rm -rf.docx" and ".." not in Path(argv[-1]).name
    made = data / "made" / "Our Plan_ rm -rf.pdf"
    assert made.read_bytes() == b"%PDF-1.7 fake" and str(made) in said
    assert list((data / "work").iterdir()) == []
    tool(base, "convert_to_pdf", file_name="Our Plan; rm -rf.docx", content_b64=b64(docx("hi")))
    listed = tool(base, "list_converted")
    assert listed.startswith("2 PDFs made") and "Our Plan_ rm -rf (2).pdf" in listed


def test_convert_without_libreoffice_says_so_plainly(base, monkeypatch):
    monkeypatch.setattr(dc, "find_soffice", lambda: None)
    assert tool(base, "convert_to_pdf", file_name="a.txt", text="hello") == dc.MISSING
    assert tool(base, "list_converted") == "No PDFs made yet."


def test_read_document_text_reads_office_files_without_libreoffice(base):
    said = tool(base, "read_document_text", file_name="notes.docx", content_b64=b64(docx("First line", "Second line")))
    assert "First line\nSecond line" in said
    assert "hello there" in tool(base, "read_document_text", file_name="a.md", text="# hello there")
    assert "without LibreOffice" in tool(base, "read_document_text", file_name="slides.odp", content_b64=b64(b"x"))


def test_a_bad_input_is_refused_plainly(base):
    assert "not a type this tool takes" in tool(base, "convert_to_pdf", error=True, file_name="run.exe", content_b64=b64(b"MZ"))
    assert "not base64" in tool(base, "convert_to_pdf", error=True, file_name="a.docx", content_b64="@@@")
    assert "not a real .docx" in tool(base, "read_document_text", error=True, file_name="a.docx", content_b64=b64(b"plain"))


# ---------------------------------------------------------------- 1.1.0: PDFs

sys.path.insert(0, str(PKG / "tests"))
import pdf_fixtures as fx  # noqa: E402


@contextlib.contextmanager
def stand_in_core(made: Path):
    """The Core's one answer this package asks for: where My files is, for this package's own token."""
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            ok = self.headers.get("Authorization") == f"Bearer {TOKEN}" and self.path == "/v1/owner-files"
            body = json.dumps({"made": str(made)} if ok else {"error": "no"}).encode()
            self.send_response(200 if ok else 401)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    server.daemon_threads = True
    with running(server) as url:
        yield url


@pytest.fixture
def my_files(tmp_path):
    return tmp_path / "data" / "My files"


@pytest.fixture
def pdf_base(data, my_files):
    with stand_in_core(my_files / "Made by WritersBot") as api:
        with running(dc.make_server(0, data, TOKEN, dc.CoreFiles(api, TOKEN))) as url:
            yield url


def need_pdf_tools():
    pytest.importorskip("reportlab")
    pytest.importorskip("pypdf")
    pytest.importorskip("docx")


def test_pdf_to_word_saves_real_headings_where_the_writer_can_reach_them(pdf_base, my_files, monkeypatch):
    need_pdf_tools()
    import docx
    monkeypatch.setattr(dc, "find_soffice", lambda: None)     # it needs no LibreOffice
    said = tool(pdf_base, "pdf_to_word", file_name="The Salt Ledger.pdf", mime="application/pdf", content_b64=b64(fx.novel()))
    made = my_files / "Converted" / "The Salt Ledger.docx"
    assert made.is_file()
    assert said.startswith("Made The Salt Ledger.docx, a Word file with real headings, in the Converted folder in My files. From the PDF: 5 pages, ")
    assert "3 chapters found, 7 running heads and page numbers removed, 3 hyphens joined." in said
    assert "Knowledge and documents, Your files" in said and str(made) in said
    assert "Converted/The Salt Ledger.docx" in said
    paras = docx.Document(str(made)).paragraphs
    heads = [(p.text, p.style.name) for p in paras if p.style.name.startswith("Heading")]
    assert heads == [("Chapter 1", "Heading 1"), ("Chapter 3", "Heading 1"), ("IV.", "Heading 1")]
    got = [p.text for p in paras]
    want = fx.PARAGRAPHS
    at = got.index("Chapter 1")
    assert got[at + 1:at + 7] == [want["ferry"], want["tonight"], want["cafe"], want["times"], want["wind"], want["short"]]
    assert got[at + 7:at + 13] == ["* * *", want["rain"], "", "", "", want["three"]]      # both kinds of scene break
    # a second one keeps the first
    said = tool(pdf_base, "pdf_to_word", file_name="The Salt Ledger.pdf", content_b64=b64(fx.novel()))
    assert "The Salt Ledger (2).docx" in said and (my_files / "Converted" / "The Salt Ledger (2).docx").is_file()
    listed = tool(pdf_base, "list_converted")
    assert listed.startswith("No PDFs made yet.") and "The Salt Ledger (2).docx" in listed


def test_pdf_to_word_marks_parts_above_chapters(pdf_base, my_files):
    need_pdf_tools()
    import docx
    trees = iter("oak ash elm yew fir bay".split())

    def lines():
        return [f"She walked the long way round the harbour wall, past the {next(trees)}, and did not look." for _ in range(3)]
    raw = fx.text_pages([["Part One", "", ""] + lines(), ["Chapter 1", "", ""] + lines()])
    tool(pdf_base, "pdf_to_word", file_name="parts.pdf", content_b64=b64(raw))
    paras = docx.Document(str(my_files / "Converted" / "parts.docx")).paragraphs
    assert [(p.text, p.style.name) for p in paras if p.style.name.startswith("Heading")] == [("Part One", "Heading 1"), ("Chapter 1", "Heading 2")]


@pytest.mark.parametrize("make,line", [
    (lambda: fx.scanned(), "This PDF is pictures of pages, so there is no text to read. Reading scans is not built."),
    (lambda: fx.locked(), "This PDF is locked with a password, so I cannot read it. Save a copy with no password and try again."),
    (lambda: fx.empty(), "This PDF has no text and no pictures. Its pages are empty."),
    (lambda: b"a letter, renamed", "That file is not a real PDF, so it will not open as one."),
])
def test_a_pdf_with_nothing_to_read_is_one_plain_line_and_makes_nothing(pdf_base, my_files, make, line):
    need_pdf_tools()
    assert tool(pdf_base, "pdf_to_word", error=True, file_name="bad.pdf", content_b64=b64(make())) == line
    assert tool(pdf_base, "read_document_text", error=True, file_name="bad.pdf", content_b64=b64(make())) == line
    assert not (my_files / "Converted").exists() or not list((my_files / "Converted").iterdir())


def test_pdf_to_word_takes_only_a_pdf_and_says_so_when_the_core_is_away(base, pdf_base):
    need_pdf_tools()
    assert "not a type this tool takes" in tool(pdf_base, "pdf_to_word", error=True, file_name="a.docx", content_b64=b64(b"x"))
    assert tool(base, "pdf_to_word", error=True, file_name="a.pdf", content_b64=b64(fx.novel())) == (
        "I could not ask BuildBot where your files are, so no Word file was made.")


def test_read_document_text_reads_a_pdf_with_its_report_and_its_limit(pdf_base):
    need_pdf_tools()
    said = tool(pdf_base, "read_document_text", file_name="n.pdf", content_b64=b64(fx.novel()))
    assert said.startswith("From the PDF: 5 pages, ") and "\n\nText of n.pdf:\n\n" in said
    assert fx.PARAGRAPHS["times"] in said and "THE SALT LEDGER" not in said
    pages = [[f"She walked the long way round the harbour wall, page {p} line {i}, and did not look." for i in range(30)] for p in range(30)]
    said = tool(pdf_base, "read_document_text", file_name="long.pdf", content_b64=b64(fx.text_pages(pages)))
    head, body = said.split("Text of long.pdf (the first part only):\n\n")
    assert len(body) == dc.MAX_TEXT


def test_the_manifest_says_what_the_server_does():
    manifest = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))
    names = [t[0] for t in dc.TOOLS]
    ab = manifest["abilities"]
    assert manifest["version"] == dc.VERSION == "1.1.0"
    assert set(ab["tools"]) == set(names) and set(ab["examples"]) == set(names) and set(ab["changes"]) == set(dc.CHANGES)
    assert {"convert_to_pdf", "pdf_to_word"} == set(ab["changes"])
    tools = {a["tool"]: a["types"] for a in manifest["accepts"]}
    assert tools["pdf_to_word"] == [".pdf"] and ".pdf" not in tools["convert_to_pdf"] and set(tools) <= set(names)
    assert "or read PDFs" not in manifest["description"]
    assert manifest["run"]["default"]["setup"] == ["{python} -m pip install -r requirements.txt"]
    # "write" with a listed change that has file_name is hidden and refused by the Core while edits are off (D140)
    assert manifest["needs"]["owner_files"] == "read"
    assert (PKG / "requirements.txt").read_text(encoding="utf-8").split() == ["pypdf==6.21.4", "cryptography==50.0.2", "python-docx==1.2.0"]
