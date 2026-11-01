"""Maps 2.1.0 against a stand-in Core and a stand-in picture program: the server runs as its own process, the Core here
answers ``/v1/owner-files`` and ``/v1/bridge/addresses/ask``, a small ComfyUI stand-in answers the picture calls, and a
manuscript folder holds the sample bible (and a second one the v2 sample bible, with importance and a minor
character). The real ComfyUI is never contacted. Run from the package folder:
``python -m pytest tests -q``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG))
import atlas  # noqa: E402
import draw  # noqa: E402
import pictures  # noqa: E402

TOKEN = "test-token-456"
SAMPLE = json.loads((PKG / "fixtures" / "bible.sample.json").read_text(encoding="utf-8"))
SAMPLE2 = json.loads((PKG / "fixtures" / "bible2.sample.json").read_text(encoding="utf-8"))
SUPPORTING = "show me all the supporting characters and whether any of them interact"


class Core:
    """Answers where My files is, and asks for an address: the first ask waits (202), later ones are allowed (200),
    and port 9 is refused. With ``waiting`` set, every ask waits."""

    def __init__(self, made: Path) -> None:
        self.made = made
        self.asks: list[str] = []
        self.waiting = False
        core = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                ok = self.headers.get("Authorization") == f"Bearer {TOKEN}" and self.path.startswith("/v1/owner-files")
                body = json.dumps({"made": str(core.made)} if ok else {"error": "no"}).encode()
                self.send_response(200 if ok else 401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.headers.get("Authorization") != f"Bearer {TOKEN}" or self.path != "/v1/bridge/addresses/ask":
                    code, out = 401, {"error": "no"}
                else:
                    address = json.loads(raw).get("address")
                    core.asks.append(address)
                    if address.endswith(":9"):
                        line = "That port is not one the Bridge allows."
                        code, out = 400, {"state": "refused", "error": line, "line": line}
                    elif core.waiting or len([a for a in core.asks if not a.endswith(":9")]) == 1:
                        code, out = 202, {"state": "waiting", "line": "Waiting for the owner's Yes. Nothing opens before that."}
                    else:
                        code, out = 200, {"state": "allowed", "line": "Allowed. The owner said Yes to this address and port."}
                body = json.dumps(out).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def tiny_png() -> bytes:
    """A valid 1 by 1 PNG."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xc8\x96\x64")) + chunk(b"IEND", b""))


PNG = tiny_png()


class Comfy:
    """A stand-in ComfyUI: the GGUF loader present or not, a prompt's history ready after ``ready_after`` empty polls
    (two), and with ``fail_prompt`` set a 404 for /prompt."""

    def __init__(self) -> None:
        self.gguf = True
        self.ready_after = 2
        self.fail_prompt = False
        self.prompts: list[dict] = []
        self.polls: dict[str, int] = {}
        comfy = self

        def info() -> dict:
            got = {"CheckpointLoaderSimple": {"input": {"required": {"ckpt_name": [["sd_turbo.safetensors"]]}}},
                   "DualCLIPLoader": {"input": {"required": {"clip_name1": [["clip_l.safetensors",
                                                                             "t5xxl_fp8_e4m3fn.safetensors"]]}}},
                   "VAELoader": {"input": {"required": {"vae_name": [["ae.safetensors"]]}}},
                   "KSampler": {"input": {"required": {}}}}
            if comfy.gguf:
                got["UnetLoaderGGUF"] = {"input": {"required": {"unet_name": [["flux1-schnell-Q4_K_S.gguf"]]}}}
            return got

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _out(self, code, obj=None, raw=None, ctype="application/json"):
                body = raw if raw is not None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path, _, query = self.path.partition("?")
                if path == "/system_stats":
                    return self._out(200, {"system": {"os": "nt", "comfyui_version": "0.3.60"}, "devices": []})
                if path == "/object_info":
                    return self._out(200, info())
                models = {"/models/checkpoints": ["sd_turbo.safetensors"], "/models/unet": [],
                          "/models/clip": ["clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors"],
                          "/models/vae": ["ae.safetensors"]}
                if path in models:
                    return self._out(200, models[path])
                if path.startswith("/history/"):
                    pid = path[len("/history/"):]
                    comfy.polls[pid] = comfy.polls.get(pid, 0) + 1
                    if comfy.polls[pid] <= comfy.ready_after:
                        return self._out(200, {})
                    return self._out(200, {pid: {"outputs": {"9": {"images": [
                        {"filename": f"puffpai-maps_{pid}.png", "subfolder": "", "type": "output"}]}},
                        "status": {"status_str": "success", "completed": True}}})
                if path == "/view":
                    got = urllib.parse.parse_qs(query)
                    if got.get("type") == ["output"] and got.get("filename", [""])[0].startswith("puffpai-maps_"):
                        return self._out(200, raw=PNG, ctype="image/png")
                return self._out(404, {"error": "not found"})

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if self.path == "/prompt" and not comfy.fail_prompt:
                    comfy.prompts.append(json.loads(raw))
                    return self._out(200, {"prompt_id": f"pid-{len(comfy.prompts)}", "number": 1})
                return self._out(404, {"error": "not found"})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.address = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(port, method, path, body=None, token=TOKEN):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers)
    try:
        with OPENER.open(req, timeout=60) as r:
            raw, code = r.read(), r.status
    except urllib.error.HTTPError as exc:
        raw, code = exc.read(), exc.code
    try:
        return code, json.loads(raw) if raw else None
    except ValueError:
        return code, raw


def q(**kw) -> str:
    return "?" + urllib.parse.urlencode(kw)


def tool(port, name, **args):
    code, got = call(port, "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                            "params": {"name": name, "arguments": args}})
    assert code == 200, got
    return got["result"]["content"][0]["text"], bool(got["result"].get("isError"))


def told_out_of_order() -> dict:
    """The sample bible with a story order that differs from the telling: the last event happened first."""
    b = json.loads(json.dumps(SAMPLE))
    b["title"] = "Told Backwards"
    n = len(b["events"])
    for i, e in enumerate(b["events"]):
        e["story_order"] = (i + 1) % n + 1 if i < n - 1 else 1
    return b


def hashes(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob("*") if p.is_file() and p.name not in ("boards.json", "pointer.json", "pictures.json",
                                                                         "illustrate.json", "style.json")
            and p.parent.name != "pictures"}


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("data")
    made = root / "My files" / "Made by WritersBot"
    made.mkdir(parents=True)
    shelf = root / "My files" / "Manuscripts"
    book = shelf / "Salt Moon"
    (book / "sections").mkdir(parents=True)
    shutil.copy(PKG / "fixtures" / "bible.sample.json", book / "bible.json")
    (book / "sections" / "04 - Scene 4.md").write_text("# Scene 4\n\nMira was holding an old company badge.\n",
                                                       encoding="utf-8")
    (book / "manuscript.json").write_text(json.dumps({"title": "Salt Moon", "sections": []}), encoding="utf-8")
    (book / "views.json").write_text(json.dumps({"views": [{"id": "v-1", "tab": "characters", "title": "x"}]}),
                                     encoding="utf-8")
    (book / "corrections.json").write_text(json.dumps({"corrections": [{"what": "Tom", "correction": "cousin"}]}),
                                           encoding="utf-8")
    (book / "characters.md").write_text("My own notes.\n", encoding="utf-8")
    other = shelf / "Told Backwards"
    other.mkdir()
    (other / "bible.json").write_text(json.dumps(told_out_of_order()), encoding="utf-8")
    second = shelf / "Second Reading"
    second.mkdir()
    (second / "bible.json").write_text(json.dumps({**SAMPLE2, "title": "Second Reading"}), encoding="utf-8")
    (shelf / "Not Read Yet").mkdir()
    (shelf / "Not Read Yet" / "manuscript.json").write_text("{}", encoding="utf-8")
    before = {f.name: hashes(f) for f in shelf.iterdir()}
    core = Core(made)
    comfy = Comfy()
    data = root / "package data"
    port = free_port()
    e = {k: v for k, v in os.environ.items() if not k.upper().endswith("_PROXY")}
    e.update(PUFFPAI_API=f"http://127.0.0.1:{core.port}", PUFFPAI_TOKEN=TOKEN, PUFFPAI_PORT=str(port),
             PUFFPAI_DATA=str(data))
    proc = subprocess.Popen([sys.executable, "server.py", "--port", str(port)], cwd=str(PKG), env=e,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                if call(port, "GET", "/health")[0] == 200:
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("the server did not start")
        yield {"port": port, "shelf": shelf, "book": book, "before": before, "core": core, "comfy": comfy,
               "data": data, "second": second}
    finally:
        proc.terminate()                         # our own process, by its own handle
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        core.stop()
        comfy.stop()


ROUTES = [("GET", "/"), ("GET", "/index.html"), ("GET", "/app.js"), ("GET", "/boards.js"), ("GET", "/app.css"),
          ("GET", "/health"), ("GET", "/api/list"), ("GET", "/api/bible?title=Salt+Moon"),
          ("GET", "/api/boards?title=Salt+Moon"), ("GET", "/api/boards?title=Salt+Moon&id=std-family"),
          ("HEAD", "/"), ("POST", "/api/draw"), ("POST", "/api/point"), ("POST", "/api/boards/std-world/layout"),
          ("POST", "/mcp"), ("DELETE", "/api/boards?title=Salt+Moon&id=x"), ("PUT", "/api/boards"),
          ("GET", "/api/maker"), ("POST", "/api/maker"), ("POST", "/api/maker/test"), ("POST", "/api/picture"),
          ("GET", "/api/pictures?title=Salt+Moon"), ("DELETE", "/api/pictures?title=Salt+Moon&id=x"),
          ("GET", "/api/picture/p-20261101-000000-portrait.png?title=Salt+Moon"),
          ("GET", "/api/atlas?title=Salt+Moon"), ("GET", "/api/illustrate?title=Salt+Moon"), ("POST", "/api/illustrate"),
          ("POST", "/api/style")]


@pytest.mark.parametrize("token", [None, "wrong"])
def test_every_route_needs_the_token(env, token):
    for method, path in ROUTES:
        body = {"title": "Salt Moon", "section": 1, "quote": "x", "asked": "family tree"} if method == "POST" else None
        code, _ = call(env["port"], method, path, body, token=token)
        assert code == 401, (method, path, code)
    assert not (env["book"] / "pointer.json").exists() and not (env["book"] / "boards.json").exists()
    assert not (env["book"] / "style.json").exists() and not (env["book"] / "illustrate.json").exists()


def test_list_shows_read_manuscripts(env):
    code, got = call(env["port"], "GET", "/api/list")
    assert code == 200
    titles = [m["title"] for m in got["manuscripts"]]
    assert titles == ["Salt Moon", "Second Reading", "Told Backwards"]     # not the one with no bible
    salt = got["manuscripts"][0]
    assert salt["sections_total"] == 8 and salt["corrections"] == 1 and salt["boards"] == []
    assert [s["id"] for s in got["standard"]] == ["std-" + k for k in draw.KINDS]
    assert call(env["port"], "GET", "/api/bible" + q(title="salt moon"))[1]["title"] == "Salt Moon"
    text, err = tool(env["port"], "maps_list")
    assert not err and "Salt Moon" in text and "Told Backwards" in text


def positioned(spec: dict) -> int:
    items = spec["nodes"] + spec["pins"] + spec["rects"]
    for it in items:
        assert isinstance(it["x"], (int, float)) and isinstance(it["y"], (int, float)), it
        assert -1 <= it["x"] <= spec["width"] + 1 and -1 <= it["y"] <= spec["height"] + 1, (spec["kind"], it)
        assert it.get("point") is None or isinstance(it["point"]["section"], int)
    return len(items)


@pytest.mark.parametrize("kind", list(draw.KINDS))
def test_draw_each_kind(env, kind):
    text, err = tool(env["port"], "maps_draw", title="Salt Moon", kind=kind, asked=f"draw the {kind}")
    assert not err, text
    assert "saved in Maps" in text
    boards = json.loads((env["book"] / "boards.json").read_text(encoding="utf-8"))["boards"]
    b = boards[0]
    assert b["kind"] == kind and b["asked"] == f"draw the {kind}" and set(b) == {"id", "kind", "title", "asked",
                                                                                   "made_at", "spec"}
    assert b["spec"]["layout"] == "maps/1" and positioned(b["spec"]) > 0
    code, got = call(env["port"], "GET", "/api/boards" + q(title="Salt Moon", id="std-" + kind))
    assert code == 200 and got["board"]["standard"] and positioned(got["board"]["spec"]) > 0


def test_asks_pick_the_board(env):
    for asked, kind in (("draw the family tree", "family"), ("show me who interacts with whom", "relationships"),
                        ("draw the timeline", "timeline"), ("a relationship map please", "relationships"),
                        ("the world map", "world"), ("where are the threads", "threads"),
                        ("character touchpoints", "touchpoints"), ("Tamsin's arc", "arcs")):
        code, got = call(env["port"], "POST", "/api/draw", {"title": "Salt Moon", "kind": "", "asked": asked})
        assert code == 200 and got["board"]["kind"] == kind, (asked, got)
    code, got = call(env["port"], "POST", "/api/draw", {"title": "Salt Moon", "asked": "something lovely"})
    assert code == 400 and "family tree" in got["error"]
    arc = call(env["port"], "POST", "/api/draw", {"title": "Salt Moon", "asked": "the arc of Quill"})[1]["board"]
    assert {p["id"].split("-beat-")[0] for p in arc["spec"]["pins"]} == {"quill"}
    arc = call(env["port"], "POST", "/api/draw", {"title": "Salt Moon", "asked": "Tamsin's arc"})[1]["board"]
    assert {p["id"].split("-beat-")[0] for p in arc["spec"]["pins"]} == {"tamsin"} and "Tamsin" in arc["title"]


def test_supporting_characters_and_whether_they_interact(env):
    text, err = tool(env["port"], "maps_draw", title="Salt Moon", kind="", asked=SUPPORTING)
    assert not err and "Supporting characters" in text
    b = json.loads((env["book"] / "boards.json").read_text(encoding="utf-8"))["boards"][0]
    assert b["kind"] == "relationships" and b["asked"] == SUPPORTING
    roles = {c["id"]: c["role"] for c in SAMPLE["characters"]}
    ids = {n["id"] for n in b["spec"]["nodes"]}
    assert ids == {c for c, r in roles.items() if r not in ("protagonist", "antagonist")} == {"pell", "mira", "oren",
                                                                                               "quill"}
    apps = {c["id"]: set(c["appearances"]) for c in SAMPLE["characters"]}
    ties = {tuple(sorted((c["id"], r["to"]))) for c in SAMPLE["characters"] for r in c["relationships"]}
    assert b["spec"]["edges"]
    for e in b["spec"]["edges"]:
        assert e["from"] in ids and e["to"] in ids
        assert apps[e["from"]] & apps[e["to"]] or tuple(sorted((e["from"], e["to"]))) in ties, e


def test_timeline_two_rails_when_the_orders_differ(env):
    one = call(env["port"], "GET", "/api/boards" + q(title="Salt Moon", id="std-timeline"))[1]["board"]["spec"]
    assert [ln["cls"] for ln in one["lines"]].count("rail") == 1
    two = call(env["port"], "GET", "/api/boards" + q(title="Told Backwards", id="std-timeline"))[1]["board"]["spec"]
    assert [ln["cls"] for ln in two["lines"]].count("rail") == 2
    assert [ln["cls"] for ln in two["lines"]].count("join") == len(SAMPLE["events"])


def pointer(env) -> dict:
    return json.loads((env["book"] / "pointer.json").read_text(encoding="utf-8"))


def test_point_writes_what_manuscript_reads(env):
    text, err = tool(env["port"], "maps_point", title="Salt Moon", board="touchpoints", item="quill@3")
    assert not err and "section 3" in text
    p = pointer(env)
    assert set(p) == {"title", "section", "quote", "note", "set_at"}
    assert p["title"] == "Salt Moon" and p["section"] == 3 and p["quote"] == "Quill Asher" and p["set_at"]
    text, err = tool(env["port"], "maps_point", title="Salt Moon", board="timeline", item="The Lantern goes dark.")
    assert not err and pointer(env)["section"] == 4 and pointer(env)["quote"] == "The Lantern goes dark."
    text, err = tool(env["port"], "maps_point", title="Salt Moon", board="std-family", item="mira dray")
    assert not err and pointer(env)["quote"] == "Mira Dray" and pointer(env)["section"] == 1
    text, err = tool(env["port"], "maps_point", title="Salt Moon", board="family", item="nobody at all")
    assert err and "could not find" in text
    code, got = call(env["port"], "POST", "/api/point", {"title": "Salt Moon", "section": 5, "quote": "the files",
                                                         "note": "From Maps."})
    assert code == 200 and pointer(env)["section"] == 5 and pointer(env)["note"] == "From Maps."
    assert set(pointer(env)) == {"title", "section", "quote", "note", "set_at"}
    assert call(env["port"], "POST", "/api/point", {"title": "Salt Moon", "section": 99, "quote": "x"})[0] == 400


def test_layout_saved_and_removed(env):
    port = env["port"]
    code, got = call(port, "POST", "/api/boards/std-relationships/layout",
                     {"title": "Salt Moon", "positions": {"tamsin": [111, 222], "nobody": [1, 1]}})
    assert code == 200, got
    b = call(port, "GET", "/api/boards" + q(title="Salt Moon", id="std-relationships"))[1]["board"]
    assert b["saved_layout"]
    t = next(n for n in b["spec"]["nodes"] if n["id"] == "tamsin")
    assert (t["x"], t["y"]) == (111, 222)
    saved = call(port, "GET", "/api/boards" + q(title="Salt Moon"))[1]["boards"]
    assert all(not s["id"].startswith("std-") for s in saved)              # a saved layout is not an ask board
    ask = saved[0]
    node = ask["spec"]["nodes"][0] if ask["spec"]["nodes"] else None
    if node:
        code, _ = call(port, "POST", f"/api/boards/{ask['id']}/layout",
                       {"title": "Salt Moon", "positions": {node["id"]: [40, 50]}})
        assert code == 200
        again = call(port, "GET", "/api/boards" + q(title="Salt Moon", id=ask["id"]))[1]["board"]
        assert (again["spec"]["nodes"][0]["x"], again["spec"]["nodes"][0]["y"]) == (40, 50)
    assert call(port, "DELETE", "/api/boards" + q(title="Salt Moon", id="std-relationships"))[0] == 200
    b = call(port, "GET", "/api/boards" + q(title="Salt Moon", id="std-relationships"))[1]["board"]
    assert not b["saved_layout"]
    n = len(saved)
    assert call(port, "DELETE", "/api/boards" + q(title="Salt Moon", id=ask["id"]))[0] == 200
    assert len(call(port, "GET", "/api/boards" + q(title="Salt Moon"))[1]["boards"]) == n - 1
    assert call(port, "DELETE", "/api/boards" + q(title="Salt Moon", id=ask["id"]))[0] == 400


def test_mcp_tools(env):
    code, got = call(env["port"], "POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = [t["name"] for t in got["result"]["tools"]]
    assert names == ["maps_list", "maps_draw", "maps_point", "maps_picture", "maps_illustrate"]
    assert manifest_needs_lan()
    manifest = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))
    assert set(manifest["abilities"]["examples"]) <= set(names) and set(manifest["abilities"]["changes"]) <= set(names)
    assert manifest["abilities"]["examples"]["maps_draw"] == ["draw the family tree", "show me who interacts with whom",
                                                              "draw the timeline of Short story"]
    for t in got["result"]["tools"]:
        first = t["description"].split()[0].lower()
        assert first not in ("use", "call", "always", "never", "draw", "show", "list", "point", "paint",
                             "illustrate", "make"), t["description"]
    pic = next(t for t in got["result"]["tools"] if t["name"] == "maps_picture")["inputSchema"]["properties"]
    assert "scene" in pic["kind"]["enum"] and {"event", "note"} <= set(pic)


def test_page_files(env):
    page = call(env["port"], "GET", "/")[1]
    assert b"Maps <span class=\"ver\">2.1.0</span>" in page and b'id="atlas"' in page
    for name, ctype in (("app.js", "text/javascript"), ("boards.js", "text/javascript"), ("app.css", "text/css")):
        req = urllib.request.Request(f"http://127.0.0.1:{env['port']}/{name}",
                                     headers={"Authorization": f"Bearer {TOKEN}"})
        with OPENER.open(req, timeout=10) as r:
            assert r.status == 200 and r.headers["Content-Type"] == ctype
            assert r.read() == (PKG / "web" / name).read_bytes()
    assert call(env["port"], "GET", "/../server.py")[0] == 404
    assert call(env["port"], "GET", "/%2e%2e/draw.py")[0] == 404


def manifest_needs_lan() -> bool:
    m = json.loads((PKG / "puffpai.json").read_text(encoding="utf-8"))
    return m["version"] == "2.1.0" and m["needs"]["addresses"] == "lan" and "home network" in m["consent"]


SECTION_TEXT = "Mira was holding an old company badge."


def test_prompts_come_from_the_bible_only():
    tamsin = next(c for c in SAMPLE["characters"] if c["id"] == "tamsin")
    prompt, about = pictures.make_prompt(SAMPLE, "portrait", "Tamsin", "")
    assert about == {"who": "tamsin", "who_name": "Tamsin Dray", "section": 1}
    for t in tamsin["traits"]:
        assert t in prompt
    assert "the main character" in prompt and "moon" in prompt and "station" in prompt
    assert prompt.endswith(pictures.DEFAULT_STYLE)
    mira = pictures.make_prompt(SAMPLE, "portrait", "mira", "watercolor")[0]
    assert "mother" in mira and mira.endswith("watercolor")              # age words from her alias
    all_text = " ".join(pictures.make_prompt(SAMPLE, k, w, "")[0] for k, w in
                        (("portrait", "tamsin"), ("portrait", "mira"), ("place", "brine-station"), ("worldmap", ""),
                         ("cover", "")))
    assert SECTION_TEXT.lower() not in all_text.lower() and "badge" not in all_text
    for name in ("Tamsin", "Dray", "Drays", "Mira", "Quill", "Pell", "Halden"):
        assert name not in all_text.split() and name not in all_text, name
    for t in SAMPLE["themes"]:                                         # theme names travel, their quotes never
        assert t["name"] in all_text
        for ev in t["evidence"]:
            assert ev["quote"] not in all_text
    assert pictures.worldmap_prompt(SAMPLE).startswith("a hand-drawn fantasy map showing Brine Station (station)")
    with pytest.raises(pictures.PictureError, match="Tamsin Dray"):
        pictures.make_prompt(SAMPLE, "portrait", "nobody", "")


def test_workflow_choice_both_ways():
    models = {"unet": [], "clip": ["clip_l.safetensors", "t5xxl_fp8_e4m3fn.safetensors"], "vae": ["ae.safetensors"],
              "checkpoints": ["sd_turbo.safetensors"]}
    info = {"UnetLoaderGGUF": {"input": {"required": {"unet_name": [["flux1-schnell-Q4_K_S.gguf"]]}}}}
    flux = pictures.choose(info, models)
    assert flux == {"workflow": "flux-gguf", "unet": "flux1-schnell-Q4_K_S.gguf", "t5": "t5xxl_fp8_e4m3fn.safetensors",
                    "clip_l": "clip_l.safetensors", "vae": "ae.safetensors"}
    w = pictures.build_workflow(flux, "a lighthouse", "portrait", 7)
    assert [n["class_type"] for n in w.values()] == ["UnetLoaderGGUF", "DualCLIPLoader", "VAELoader", "CLIPTextEncode",
                                                     "CLIPTextEncode", "EmptySD3LatentImage", "KSampler", "VAEDecode",
                                                     "SaveImage"]
    assert (w["6"]["inputs"]["width"], w["6"]["inputs"]["height"]) == (640, 832)
    assert w["2"]["inputs"]["type"] == "flux" and w["5"]["inputs"]["text"] == ""
    assert {k: w["7"]["inputs"][k] for k in ("steps", "cfg", "sampler_name", "scheduler")} == \
        {"steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple"}
    assert pictures.size_for("worldmap", flux) == (1024, 640) and pictures.size_for("place", flux) == (768, 768)
    plain = pictures.choose({"CheckpointLoaderSimple": {}}, {**models, "checkpoints": ["v1-5.safetensors"]})
    assert plain == {"workflow": "checkpoint", "ckpt": "v1-5.safetensors"}             # no GGUF loader: workflow 2
    w = pictures.build_workflow(plain, "a lighthouse", "place", 7)
    assert w["7"]["inputs"]["steps"] == 20 and w["7"]["inputs"]["cfg"] == 6.0 and w["7"]["inputs"]["scheduler"] == "normal"
    assert (w["6"]["inputs"]["width"], w["6"]["inputs"]["height"]) == (768, 512)
    turbo = pictures.build_workflow({"workflow": "checkpoint", "ckpt": "sd_turbo.safetensors"}, "x", "cover", 1)
    assert turbo["7"]["inputs"]["steps"] == 4 and turbo["7"]["inputs"]["cfg"] == 1.0
    with pytest.raises(pictures.NoModel, match="no FLUX schnell GGUF model and no checkpoint"):
        pictures.choose({}, {"unet": [], "clip": [], "vae": [], "checkpoints": []})


def test_picture_maker_address_and_the_ask(env):
    port, core, comfy = env["port"], env["core"], env["comfy"]
    code, got = call(port, "GET", "/api/maker")
    assert code == 200 and got["address"] == "" and got["style"] == pictures.DEFAULT_STYLE
    code, got = call(port, "POST", "/api/picture", {"title": "Salt Moon", "kind": "cover"})
    assert code == 400 and "not set yet" in got["error"]
    code, got = call(port, "POST", "/api/maker", {"address": "http://8.8.8.8:8188"})
    assert code == 400 and "home network" in got["error"] and not core.asks
    code, got = call(port, "POST", "/api/maker", {"address": comfy.address, "style": ""})
    assert code == 200 and got["ask"]["state"] == "waiting", got
    assert "Waiting for your Yes on the Bridge page" in got["message"] and core.asks == [comfy.address]
    saved = json.loads((env["data"] / "maker.json").read_text(encoding="utf-8"))
    assert saved["address"] == comfy.address and saved["style"] == pictures.DEFAULT_STYLE
    assert not (env["book"] / "maker.json").exists()
    code, got = call(port, "POST", "/api/maker/test", {})                  # the Core now answers 200
    assert code == 200 and got["ask"]["line"] == "Allowed." and got["reached"] and got["workflow"] == "flux-gguf", got
    assert "FLUX schnell" in got["message"] and got["models"]["checkpoints"] == ["sd_turbo.safetensors"]
    comfy.gguf = False
    try:
        got = call(port, "POST", "/api/maker/test", {})[1]
        assert got["workflow"] == "checkpoint" and "sd_turbo.safetensors" in got["message"], got
    finally:
        comfy.gguf = True
    code, got = call(port, "POST", "/api/maker", {"address": "127.0.0.1:9"})
    assert code == 200 and got["ask"]["state"] == "refused" and got["ask"]["line"] == "That port is not one the Bridge allows."
    assert call(port, "POST", "/api/maker/test", {})[1]["reached"] is False
    code, got = call(port, "POST", "/api/maker", {"address": comfy.address, "style": "ink and wash"})
    assert code == 200 and got["ask"]["state"] == "allowed" and got["style"] == "ink and wash"
    assert call(port, "GET", "/api/maker")[1]["ask"]["state"] == "allowed"


def wait_for(env, pid: str, state: str = "ready", seconds: float = 30, title: str = "Salt Moon") -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = call(env["port"], "GET", "/api/pictures" + q(title=title))[1]
        p = next(x for x in got["pictures"] if x["id"] == pid)
        if p["state"] != "making":
            assert p["state"] == state, p
            return p
        time.sleep(0.3)
    raise AssertionError("the picture stayed making")


def ensure_maker(env) -> None:
    got = call(env["port"], "POST", "/api/maker", {"address": env["comfy"].address, "style": "ink and wash"})[1]
    assert got["ask"]["state"] == "allowed", got


def test_a_picture_goes_making_to_ready(env):
    port, comfy = env["port"], env["comfy"]
    ensure_maker(env)
    code, got = call(port, "POST", "/api/picture", {"title": "Salt Moon", "kind": "portrait", "who": "Tamsin Dray"})
    assert code == 200, got
    rec = got["picture"]
    assert rec["state"] == "making" and rec["kind"] == "portrait" and rec["who"] == "tamsin" and rec["section"] == 1
    assert "curious" in rec["prompt"] and rec["prompt"].endswith("ink and wash") and "Tamsin" not in rec["prompt"]
    saved = json.loads((env["book"] / "pictures.json").read_text(encoding="utf-8"))["pictures"]
    assert saved[0]["id"] == rec["id"]
    p = wait_for(env, rec["id"])
    assert p["workflow"] == "flux-gguf" and p["why"] == ""
    assert (env["book"] / "pictures" / f"{rec['id']}.png").read_bytes() == PNG
    sent = comfy.prompts[-1]
    assert sent["prompt"]["4"]["inputs"]["text"] == rec["prompt"] and sent["prompt"]["9"]["class_type"] == "SaveImage"
    assert comfy.polls[f"pid-{len(comfy.prompts)}"] == 3                 # two empty polls, then the picture
    code, raw = call(port, "GET", f"/api/picture/{rec['id']}.png" + q(title="Salt Moon"))
    assert code == 200 and raw == PNG
    assert call(port, "GET", f"/api/picture/{rec['id']}.png", token=None)[0] == 401
    assert call(port, "GET", f"/api/picture/{rec['id']}.png")[1] == PNG    # found without a title too
    assert call(port, "GET", "/api/picture/..%2Fbible.png")[0] == 404
    text, err = tool(port, "maps_picture", title="Salt Moon", kind="cover", who="")
    assert not err and "cover mock" in text, text
    cid = re.search(r"\(id (p-[\w-]+)\)", text).group(1)
    cover = wait_for(env, cid)
    assert "a debt you inherit" in cover["prompt"]
    assert call(port, "DELETE", "/api/pictures" + q(title="Salt Moon", id=cid))[0] == 200
    assert not (env["book"] / "pictures" / f"{cid}.png").exists()
    assert call(port, "DELETE", "/api/pictures" + q(title="Salt Moon", id=cid))[0] == 400


def test_a_picture_that_fails_says_why(env):
    port, comfy, core = env["port"], env["comfy"], env["core"]
    ensure_maker(env)
    assert call(port, "POST", "/api/picture", {"title": "Salt Moon", "kind": "portrait", "who": "nobody"})[0] == 400
    assert call(port, "POST", "/api/picture", {"title": "Salt Moon", "kind": "statue"})[0] == 400
    comfy.fail_prompt = True
    try:
        rec = call(port, "POST", "/api/picture", {"title": "Salt Moon", "kind": "place", "who": "the salt flats"})[1]
        p = wait_for(env, rec["picture"]["id"], "failed")
        assert "404" in p["why"] and "/prompt" in p["why"], p
    finally:
        comfy.fail_prompt = False
    core.waiting = True
    try:
        rec = call(port, "POST", "/api/picture", {"title": "Salt Moon", "kind": "worldmap"})[1]["picture"]
        p = wait_for(env, rec["id"], "failed")
        assert p["why"].startswith("Not allowed yet") and "Bridge page" in p["why"]
    finally:
        core.waiting = False
    again = call(port, "POST", "/api/picture", {"title": "Salt Moon", "retry": rec["id"]})[1]["picture"]
    assert again["id"] == rec["id"] and again["state"] == "making"
    assert wait_for(env, rec["id"])["kind"] == "worldmap"
    assert (env["book"] / "pictures" / f"{rec['id']}.png").read_bytes() == PNG


def test_a_scene_prompt_has_the_events_plain_words_and_no_names():
    prompt, about = pictures.make_prompt(SAMPLE, "scene", "e5", "")
    assert about == {"who": "e5", "who_name": "Tamsin finds Quill in the pump house and hides him.", "section": 3}
    assert "finds" in prompt and "in the pump house and hides him" in prompt
    assert "the curious person finds the charming person" in prompt          # who is there, by description
    assert "at a wilderness" in prompt and prompt.endswith(pictures.DEFAULT_STYLE)
    night = pictures.make_prompt(SAMPLE, "scene", "The Lantern goes dark", "")[0]
    assert "time: night" in night and "at a relay" in night                   # time-of-day words from "when"
    for e in SAMPLE["events"]:
        p = pictures.make_prompt(SAMPLE, "scene", e["id"], "")[0]
        for name in ("Tamsin", "Dray", "Mira", "Quill", "Pell", "Halden", "Oren", "Asher"):
            assert name not in p, (e["id"], p)
    assert "the guarded mother" in pictures.make_prompt(SAMPLE, "scene", "e8", "")[0]      # her alias
    with pytest.raises(pictures.PictureError, match="Say which event"):
        pictures.make_prompt(SAMPLE, "scene", "nothing like this", "")
    noted = pictures.make_prompt(SAMPLE, "portrait", "tamsin", "ink", "older, grey hair, not Quill")[0]
    assert noted.endswith("older, grey hair, not. ink") and "Quill" not in noted
    flux = {"workflow": "flux-gguf", "unet": "x.gguf", "t5": "t5", "clip_l": "c", "vae": "ae.safetensors"}
    assert pictures.size_for("scene", flux) == (1024, 640)
    assert pictures.size_for("scene", {"workflow": "checkpoint", "ckpt": "a"}) == (768, 512)


def test_key_events_spread_across_sections():
    assert [e["id"] for e in atlas.key_events(SAMPLE)] == [e["id"] for e in SAMPLE["events"]]     # twelve: all
    events = [{"id": f"e{i}", "section": 1 + i // 5, "order": i % 5, "what": f"Event {i}",
               "who": ["a"] * (i % 3)} for i in range(60)]
    got = atlas.key_events({"events": events})
    assert len(got) == 12 and len({e["section"] for e in got}) == 12                          # one per section
    assert all(len(e["who"]) == 2 for e in got)                                                # the fullest
    assert [e["id"] for e in got] == sorted((e["id"] for e in got), key=lambda x: int(x[1:]))   # in telling order


def test_a_bible_without_importance_still_has_a_cast():
    assert all("importance" not in c for c in SAMPLE["characters"])
    got = {c["id"]: atlas.importance(c) for c in SAMPLE["characters"]}
    assert got == {"tamsin": "main", "mira": "main", "quill": "main", "halden": "main", "pell": "supporting",
                   "oren": "supporting"}             # four or more sections, or a lead by role, is main
    assert [c["id"] for c in atlas.cast(SAMPLE)][0] == "tamsin"
    assert [k for k, _w, _l in atlas.illustrate_items(SAMPLE)].count("portrait") == 6
    assert atlas.importance({"importance": "minor", "appearances": [1, 2, 3, 4, 5]}) == "minor"


def wait_illustrated(env, title: str, seconds: float = 90) -> dict:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        got = call(env["port"], "GET", "/api/illustrate" + q(title=title))[1]["progress"]
        if got and got["state"] != "running":
            return got
        time.sleep(0.3)
    raise AssertionError("Illustrate did not finish")


def test_illustrate_paints_what_the_book_needs_and_skips_what_is_made(env):
    port, comfy = env["port"], env["comfy"]
    ensure_maker(env)
    comfy.ready_after = 0
    try:
        before = len(comfy.prompts)
        text, err = tool(port, "maps_illustrate", title="Second Reading")
        assert not err and text.startswith("Illustrating Second Reading: 23 pictures to paint, one at a time"), text
        assert "seconds each" in text and "\n" not in text           # timed by the pictures made before
        doc = json.loads((env["second"] / "illustrate.json").read_text(encoding="utf-8"))
        assert set(doc) >= {"state", "done", "total", "now", "started_at", "finished_at", "problems"}
        assert doc["total"] == 23 and doc["started_at"]
        slots = [i["slot"] for i in doc["items"]]
        assert len(slots) == len(set(slots)) == 23                                 # no duplicates
        kinds = [s.split(":")[0] for s in slots]
        assert {k: kinds.count(k) for k in set(kinds)} == {"portrait": 6, "place": 3, "worldmap": 1, "scene": 12,
                                                           "cover": 1}
        assert "portrait:wick" not in slots                                        # the minor character waits
        assert slots[:6] == ["portrait:tamsin", "portrait:quill", "portrait:halden", "portrait:mira",
                             "portrait:oren", "portrait:pell"]
        done = wait_illustrated(env, "Second Reading")
        assert done["state"] == "done" and done["done"] == done["total"] == 23 and not done["problems"], done
        assert done["finished_at"] and done["now"] == "" and done["line"] == "Painted 23 of 23."
        assert all(i["state"] == "ready" for i in done["items"])
        assert len(comfy.prompts) - before == 23
        pics = json.loads((env["second"] / "pictures.json").read_text(encoding="utf-8"))
        assert all(p["state"] == "ready" for p in pics["pictures"]) and len(pics["pictures"]) == 23
        assert set(pics["seeds"]) == {s for s in slots if s.startswith(("portrait:", "place:"))}
        scene = next(p for p in pics["pictures"] if p["kind"] == "scene" and p["who"] == "e5")
        sent = next(x for x in comfy.prompts if x["prompt"]["4"]["inputs"]["text"] == scene["prompt"])
        assert (sent["prompt"]["6"]["inputs"]["width"], sent["prompt"]["6"]["inputs"]["height"]) == (1024, 640)
        maker = json.loads((env["data"] / "maker.json").read_text(encoding="utf-8"))
        assert 1 <= len(maker["times"]) <= 10 and maker["avg_s"] > 0
        text, err = tool(port, "maps_illustrate", title="Second Reading")         # all made: nothing to paint
        assert not err and "nothing to paint" in text and len(comfy.prompts) - before == 23, text
        code, got = call(port, "POST", "/api/illustrate", {"title": "Second Reading", "minor": True})
        assert code == 200 and got["painting"] == 1 and got["progress"]["items"][0]["slot"] == "portrait:wick"
        assert wait_illustrated(env, "Second Reading")["state"] == "done" and len(comfy.prompts) - before == 24
        text, err = tool(port, "maps_list", title="Second Reading")
        assert "Pictures: 24, 24 of them ready." in text and "No Illustrate is running." in text, text
    finally:
        comfy.ready_after = 2


def test_illustrate_after_a_restart_paints_the_rest(env):
    """A record left making by an earlier run (Maps restarted) is painted again in place, not twice."""
    port, folder = env["port"], env["second"]
    doc = json.loads((folder / "pictures.json").read_text(encoding="utf-8"))
    cover = next(p for p in doc["pictures"] if p["kind"] == "cover")
    cover["state"] = "making"                                   # as if Maps stopped while painting it
    (folder / "pictures.json").write_text(json.dumps(doc), encoding="utf-8")
    prog = json.loads((folder / "illustrate.json").read_text(encoding="utf-8"))
    (folder / "illustrate.json").write_text(json.dumps({**prog, "state": "running", "done": 0}), encoding="utf-8")
    got = call(port, "GET", "/api/illustrate" + q(title="Second Reading"))[1]["progress"]
    assert got["state"] == "stopped" and "Illustrate again paints the rest" in got["line"]
    env["comfy"].ready_after = 0
    try:
        got = call(port, "POST", "/api/illustrate", {"title": "Second Reading"})[1]
        assert got["painting"] == 1 and got["progress"]["items"][0]["id"] == cover["id"]
        assert wait_illustrated(env, "Second Reading")["state"] == "done"
    finally:
        env["comfy"].ready_after = 2
    after = json.loads((folder / "pictures.json").read_text(encoding="utf-8"))["pictures"]
    assert len(after) == 24 and next(p for p in after if p["id"] == cover["id"])["state"] == "ready"


def test_paint_again_with_a_note_keeps_the_seed(env):
    port, comfy = env["port"], env["comfy"]
    ensure_maker(env)
    pics = call(port, "GET", "/api/pictures" + q(title="Salt Moon"))[1]["pictures"]
    first = next(p for p in pics if p["kind"] == "portrait" and p["who"] == "tamsin" and p["state"] == "ready")
    seeds = json.loads((env["book"] / "pictures.json").read_text(encoding="utf-8"))["seeds"]
    assert seeds["portrait:tamsin"] == first["seed"]
    code, got = call(port, "POST", "/api/picture", {"title": "Salt Moon", "again": first["id"],
                                                    "note": "older, grey hair"})
    assert code == 200, got
    rec = got["picture"]
    assert rec["id"] != first["id"] and rec["seed"] == first["seed"] and rec["note"] == "older, grey hair"
    assert "older, grey hair" in rec["prompt"] and rec["prompt"].endswith("ink and wash")
    assert rec["basis"] == first["basis"]                       # the same description underneath
    wait_for(env, rec["id"])
    assert comfy.prompts[-1]["prompt"]["7"]["inputs"]["seed"] == first["seed"]
    assert "older, grey hair" in comfy.prompts[-1]["prompt"]["4"]["inputs"]["text"]
    plain = call(port, "POST", "/api/picture", {"title": "Salt Moon", "again": rec["id"], "note": ""})[1]["picture"]
    assert plain["prompt"] == first["prompt"] and plain["seed"] != first["seed"]     # else the same picture again
    wait_for(env, plain["id"])
    seeds = json.loads((env["book"] / "pictures.json").read_text(encoding="utf-8"))["seeds"]
    assert seeds["portrait:tamsin"] == plain["seed"]                                   # the newest face is kept
    text, err = tool(port, "maps_picture", title="Salt Moon", kind="scene", event="The Lantern goes dark", note="rain")
    assert not err and "a scene: The Lantern goes dark." in text, text
    sid = re.search(r"\(id (p-[\w-]+)\)", text).group(1)
    scene = wait_for(env, sid)
    assert scene["who"] == "e6" and "rain. ink and wash" in scene["prompt"]


def test_the_atlas_route(env):
    port = env["port"]
    code, a = call(port, "GET", "/api/atlas" + q(title="Salt Moon"))
    assert code == 200 and a["title"] == "Salt Moon" and a["maker"] is True and a["style"] == "ink and wash"
    pics = call(port, "GET", "/api/pictures" + q(title="Salt Moon"))[1]["pictures"]

    def newest(kind, who=""):
        return next((p["id"] for p in pics if p["kind"] == kind and (p.get("who") or "") == who
                     and p["state"] == "ready"), None)
    assert [c["id"] for c in a["cast"]] == ["tamsin", "quill", "mira", "halden", "oren", "pell"]
    assert {c["id"]: c["importance"] for c in a["cast"]}["pell"] == "supporting"          # no importance: counted
    for c in a["cast"]:
        assert c["picture"] == newest("portrait", c["id"]), c["id"]
    assert a["cast"][0]["picture"] is not None and a["cast"][1]["picture"] is None
    tamsin = a["cast"][0]
    assert {t["id"] for t in tamsin["ties"]} >= {"mira", "oren", "quill"} and tamsin["point"]["section"] == 1
    assert [s["section"] for s in tamsin["sections"]] == tamsin["appearances"] and tamsin["sections"][0]["summary"]
    assert [p["id"] for p in a["world"]["pins"]] == ["brine-station", "salt-flats", "the-lantern"]
    assert all(p["picture"] is None for p in a["world"]["pins"])
    assert a["world"]["picture"] == newest("worldmap") is not None
    assert a["world"]["width"] == a["world"]["spec"]["width"] and a["world"]["spec"]["kind"] == "world"
    pin = a["world"]["pins"][0]
    node = next(n for n in a["world"]["spec"]["nodes"] if n["id"] == pin["id"])
    assert (pin["x"], pin["y"]) == (node["x"], node["y"])                       # from the saved world layout
    fam = a["family"]["spec"]
    assert {n["id"] for n in fam["nodes"]} == {"mira", "oren", "tamsin", "pell"} and fam["nodes"][0]["r"] == 30
    cards = a["story"]["cards"]
    assert [c["id"] for c in cards] == [e["id"] for e in SAMPLE["events"]]
    assert next(c for c in cards if c["id"] == "e6")["picture"] == newest("scene", "e6") is not None
    assert all(c["picture"] is None for c in cards if c["id"] != "e6")
    assert cards[0]["text"] == "The Brine Bell rings early." and cards[0]["section"] == 1
    assert a["cover"]["picture"] is None and a["cover"]["one_line"].startswith("A girl on a salt moon")
    assert a["progress"] is None and a["pictures"]["ready"] == len([p for p in pics if p["state"] == "ready"])
    code, b = call(port, "GET", "/api/atlas" + q(title="Second Reading"))
    assert code == 200 and all(c["picture"] for c in b["cast"]) and b["world"]["picture"] and b["cover"]["picture"]
    assert [c["importance"] for c in b["cast"]][-1] == "minor" and b["cast"][0]["write_up"]
    assert all(c["picture"] for c in b["story"]["cards"]) and b["progress"]["state"] == "done"
    assert b["setting"].startswith("A mining station on a salt moon")


def test_the_style_line_of_one_manuscript(env):
    port = env["port"]
    code, got = call(port, "POST", "/api/style", {"title": "Second Reading", "style": "woodcut, two colours"})
    assert code == 200 and got["style"] == "woodcut, two colours" and "Second Reading" in got["message"]
    assert json.loads((env["second"] / "style.json").read_text(encoding="utf-8"))["style"] == "woodcut, two colours"
    a = call(port, "GET", "/api/atlas" + q(title="Second Reading"))[1]
    assert a["style"] == "woodcut, two colours" and a["own_style"] is True
    rec = call(port, "POST", "/api/picture", {"title": "Second Reading", "kind": "place", "who": "the lantern"})[1]
    assert rec["picture"]["prompt"].endswith("woodcut, two colours")
    wait_for(env, rec["picture"]["id"], title="Second Reading")
    assert call(port, "GET", "/api/atlas" + q(title="Salt Moon"))[1]["style"] == "ink and wash"     # its own
    got = call(port, "POST", "/api/style", {"title": "Second Reading", "style": ""})[1]
    assert got["style"] == "ink and wash" and not (env["second"] / "style.json").exists()


def test_fixture_atlas_and_progress_match():
    """fixtures/atlas.sample.json is what the server gives for fixtures/bible2.sample.json with no pictures."""
    got = json.loads((PKG / "fixtures" / "atlas.sample.json").read_text(encoding="utf-8"))
    want = atlas.build(SAMPLE2, [], {"spec": draw.build(SAMPLE2, "world", ""), "saved_layout": False},
                       draw.portrait_family(SAMPLE2), pictures.DEFAULT_STYLE, None, False)
    assert got == json.loads(json.dumps({**want, "title": "Salt Moon", "own_style": False}))
    prog = json.loads((PKG / "fixtures" / "illustrate.sample.json").read_text(encoding="utf-8"))
    assert set(prog) >= {"state", "done", "total", "now", "started_at", "finished_at", "problems", "line"}
    assert prog["total"] == len(prog["items"]) == len(atlas.illustrate_items(SAMPLE2))
    assert prog["now"] in [c["name"] for c in got["cast"]]



# ---------------------------------------------------------------- 2.1: the proof under each picture

def by_id(bible: dict, key: str, ident: str) -> dict:
    return next(x for x in bible[key] if x["id"] == ident)


def test_a_portrait_prompt_starts_with_the_looks_and_keeps_its_proof():
    mira = by_id(SAMPLE2, "characters", "mira")
    prompt, about, proof = pictures.prompt_and_proof(SAMPLE2, "portrait", "mira", "")
    assert about == {"who": "mira", "who_name": "Mira Dray", "section": 1}
    looks = [x["detail"] for x in mira["looks"]]
    assert prompt.startswith(", ".join(looks) + "; " + mira["age_words"]["words"] + "; "), prompt
    assert "guarded, skilled" in prompt and "a supporting character" in prompt     # role and traits still follow
    assert proof == [{"owner": "Mira Dray", "detail": x["detail"], "section": x["section"], "quote": x["quote"]}
                     for x in mira["looks"]] + [{"owner": "Mira Dray", "detail": mira["age_words"]["words"],
                                                 "section": 4, "quote": mira["age_words"]["quote"]}]
    assert [e["section"] for e in proof] == [1, 4, 4]
    assert pictures.make_prompt(SAMPLE2, "portrait", "mira", "")[0] == prompt          # the 2.0 call is the same
    for k, w in (("portrait", "mira"), ("portrait", "quill"), ("place", "brine-station"), ("worldmap", ""),
                 ("scene", "e11"), ("cover", "")):
        p, _a, pf = pictures.prompt_and_proof(SAMPLE2, k, w, "")
        for name in ("Tamsin", "Dray", "Mira", "Quill", "Asher", "Pell", "Halden", "Oren"):
            assert name not in p, (k, w, p)
        for e in pf:
            assert not e["quote"] or e["quote"] in e["detail"] or e["quote"] not in p, (k, e)   # shown, never sent
    line = pictures.proof_line(proof)
    assert line == ("From the text: grey hair pulled back (section 1), a burn on her left hand (section 4), "
                    "older than the station's records (section 4).")


def test_a_place_and_a_scene_use_the_details_and_the_looks():
    prompt, _a, proof = pictures.prompt_and_proof(SAMPLE2, "place", "brine-station", "")
    details = [x["detail"] for x in by_id(SAMPLE2, "places", "brine-station")["details"]]
    assert prompt.startswith("a painting of a place, a station; " + ", ".join(details) + ";")
    assert [(e["owner"], e["detail"], e["section"]) for e in proof] == [("Brine Station", d["detail"], d["section"])
                                                                       for d in by_id(SAMPLE2, "places",
                                                                                      "brine-station")["details"]]
    prompt, _a, proof = pictures.prompt_and_proof(SAMPLE2, "scene", "e11", "")
    assert "a curious person (short dark hair cut with a knife, salt-white boots, a scar across her chin)" in prompt
    assert "a white light on a tall mast" in prompt
    assert [e["owner"] for e in proof] == ["Tamsin Dray"] * 3 + ["Mira Dray"] * 2 + ["Quill Asher"] * 2 + \
        ["The Lantern"] * 2


def test_the_world_map_prompt_holds_the_geography():
    prompt, _a, proof = pictures.prompt_and_proof(SAMPLE2, "worldmap", "", "")
    assert prompt.startswith("a hand-drawn fantasy map showing Brine Station (station)")
    assert "The salt flats is a day's walk on the tether line from Brine Station" in prompt
    assert "The Lantern is above Brine Station" in prompt
    assert "the look of the world: old mining machines, tin domes, tether lines" in prompt
    assert [(e["owner"], e["section"]) for e in proof[:2]] == [("world map", 2), ("world map", 1)]
    assert proof[0]["quote"] == "a day's walk out on the tether line, and a day back"
    assert [e["detail"] for e in proof[2:]] == ["old mining machines", "tin domes", "tether lines"]


def test_a_bible_without_evidence_gives_the_fallback_proof():
    prompt, _a, proof = pictures.prompt_and_proof(SAMPLE, "portrait", "tamsin", "")
    tamsin = by_id(SAMPLE, "characters", "tamsin")
    assert [e["detail"] for e in proof] == ["the main character"] + tamsin["traits"]
    assert all(e["section"] is None and e["owner"] == "Tamsin Dray" for e in proof)
    assert not pictures.has_sections(proof)
    assert pictures.proof_line(proof).startswith("From the story bible, without sections yet: the main character")
    assert prompt.startswith("a character portrait of a person; the main character")     # as in 2.0
    _p, _a, wproof = pictures.prompt_and_proof(SAMPLE, "worldmap", "", "")
    assert [e["detail"] for e in wproof] == ["Brine Station (station)", "The salt flats (wilderness)",
                                             "The Lantern (relay)"]
    assert pictures.prompt_and_proof(SAMPLE, "cover", "", "")[2] == []


def test_the_proof_lists_only_what_reached_a_long_prompt():
    long = json.loads(json.dumps(SAMPLE2))
    c = by_id(long, "characters", "pell")
    c["looks"] = [{"detail": f"a very long and particular detail number {i} " + "x" * 60, "section": i + 1,
                   "quote": f"quote {i}"} for i in range(12)]
    prompt, _a, proof = pictures.prompt_and_proof(long, "portrait", "pell", "")
    assert len(prompt) <= pictures.PROMPT_MAX and 0 < len(proof) < 12
    assert [e["section"] for e in proof] == list(range(1, len(proof) + 1))           # in order, from the start
    assert all(e["detail"] in prompt for e in proof)


def test_the_atlas_carries_the_proof_and_a_proof_link_points(env):
    port = env["port"]
    a = call(port, "GET", "/api/atlas" + q(title="Second Reading"))[1]
    slots = a["cast"] + a["world"]["pins"] + a["story"]["cards"] + [a["world"], a["cover"]]
    for s in slots:
        assert isinstance(s["proof"], list) and s["prompt"] and s["told_style"], s["slot"]
        for e in s["proof"]:
            assert set(e) == {"owner", "detail", "section", "quote"}
    pics = json.loads((env["second"] / "pictures.json").read_text(encoding="utf-8"))["pictures"]
    mira = next(c for c in a["cast"] if c["id"] == "mira")
    rec = next(p for p in pics if p["id"] == mira["picture"])
    assert mira["proof"] == rec["proof"] and mira["prompt"] == rec["prompt"]
    assert [e["section"] for e in mira["proof"]] == [1, 4, 4] and mira["told_style"] == rec["style"]
    assert [e["detail"] for e in a["world"]["proof"][:2]] == ["The salt flats is a day's walk on the tether line "
                                                              "from Brine Station", "The Lantern is above Brine Station"]
    assert [(r["from"], r["to"], r["note"]) for r in a["world"]["routes"]] == [
        ("salt-flats", "brine-station", "a day's walk on the tether line"), ("the-lantern", "brine-station", "above")]
    salt = call(port, "GET", "/api/atlas" + q(title="Salt Moon"))[1]
    tamsin = salt["cast"][0]
    assert tamsin["picture"] and tamsin["proof"] and all(e["section"] is None for e in tamsin["proof"])
    assert salt["world"]["routes"] == []
    e = mira["proof"][1]                                       # the page's "(section 4)" link writes this pointer
    code, got = call(port, "POST", "/api/point", {"title": "Second Reading", "section": e["section"],
                                                  "quote": e["quote"], "note": f"{e['owner']}: {e['detail']}."})
    assert code == 200
    p = json.loads((env["second"] / "pointer.json").read_text(encoding="utf-8"))
    assert p["section"] == 4 and p["quote"] == "the old burn on her left hand, shiny as brine glass"
    assert p["title"] == "Second Reading" and got["pointer"]["quote"] == p["quote"]


def test_the_tools_end_with_the_proof(env):
    port = env["port"]
    ensure_maker(env)
    text, err = tool(port, "maps_picture", title="Second Reading", kind="portrait", who="Quill Asher")
    assert not err and text.endswith("From the text: a long company coat with the badges torn off (section 3), "
                                     "taller than anyone on the station (section 5)."), text
    pid = re.search(r"\(id (p-[\w-]+)\)", text).group(1)
    rec = wait_for(env, pid, title="Second Reading")
    assert [e["section"] for e in rec["proof"]] == [3, 5] and rec["proof"][0]["owner"] == "Quill Asher"
    assert rec["prompt"].startswith("a long company coat with the badges torn off, taller than anyone")
    pics = call(port, "GET", "/api/pictures" + q(title="Second Reading"))[1]["pictures"]
    for p in pics:                                             # Tamsin's portrait gone: Illustrate paints only it
        if p["kind"] == "portrait" and p["who"] == "tamsin":
            assert call(port, "DELETE", "/api/pictures" + q(title="Second Reading", id=p["id"]))[0] == 200
    env["comfy"].ready_after = 0
    try:
        text, err = tool(port, "maps_illustrate", title="Second Reading")
        assert not err and "\n" not in text and text.startswith("Illustrating Second Reading: "), text
        assert text.endswith("From the text: short dark hair cut with a knife (section 1), salt-white boots "
                             "(section 2), a scar across her chin (section 3), a girl of fourteen (section 1)."), text
        assert wait_illustrated(env, "Second Reading")["state"] == "done"
    finally:
        env["comfy"].ready_after = 2


def big_bible(n_chars: int = 40, n_edges: int = 80, n_events: int = 60) -> dict:
    """A made-up bible as big as Maps promises to draw well: long two-line names, ties, a family, places, events
    told out of story order."""
    import random
    rnd = random.Random(11)
    first = ["Annabelle", "Bartholomew", "Cy", "Delphine", "Eustace", "Fen", "Gwendolyn", "Hal", "Isadora", "Jo"]
    last = ["Montgomery-Hale", "Dray", "Ashworth", "Li", "Vandermeer", "Okonkwo", "Rook", "St. Clair"]
    chars = [{"id": f"c{i}", "name": f"{first[i % 10]} {last[(i * 3) % 8]}" + (" Jr." if i % 7 == 0 else ""),
              "role": ["protagonist", "antagonist", "ally", "supporting"][i % 4],
              "appearances": sorted(rnd.sample(range(1, 61), rnd.randint(1, 25))), "relationships": [], "family": {}}
             for i in range(n_chars)]
    pairs: set = set()
    while len(pairs) < n_edges:
        pairs.add(tuple(sorted(rnd.sample(range(n_chars), 2))))
    for a, c in sorted(pairs):
        chars[a]["relationships"].append({"to": f"c{c}", "kind": rnd.choice(["sister", "rival", "owes money to"])})
    for i in range(1, 13):
        chars[i]["family"] = {"parents": [f"c{(i - 1) // 3}"]}
    chars[1]["family"]["partners"] = ["c13"]
    places = [{"id": f"p{i}", "name": f"The {last[i % 8]} {['Lighthouse', 'Old Mill', 'Inn'][i % 3]}",
               "appearances": sorted(rnd.sample(range(1, 61), rnd.randint(1, 10)))} for i in range(n_chars)]
    events = [{"id": f"e{i}", "section": 1 + i, "order": n_events - i,
               "what": rnd.choice(["The Lantern goes dark over the harbour", "Mira finds the badge", "A storm"])}
              for i in range(n_events)]
    return {"title": "Big", "sections_total": 60, "characters": chars, "places": places, "events": events,
            "threads": []}


def no_overlap(boxes: list) -> None:
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not (a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]), (a, b)


def test_a_big_cast_is_laid_out_inside_the_box_with_no_names_overlapping():
    big = big_bible()
    spec = draw.build(big, "relationships", "")
    assert len(spec["nodes"]) == 40 and len(spec["edges"]) == 80
    for kind in ("relationships", "world", "family"):
        spec = spec if kind == "relationships" else draw.build(big, kind, "")
        boxes = [draw.label_box(n) for n in spec["nodes"]]
        assert boxes and positioned(spec)
        for (x0, y0, x1, y1), n in zip(boxes, spec["nodes"]):
            assert 0 <= x0 and x1 <= spec["width"] and 0 <= y0 and y1 <= spec["height"], (kind, n["id"])
            assert 1 <= len(n["lines"]) <= 2 and all(len(s) <= 22 for s in n["lines"]), n["lines"]
            assert n["label"] in n["title"]
        no_overlap(boxes)
        if kind != "family":                       # least centre distance: the taller name box plus 10
            for i, a in enumerate(spec["nodes"]):
                for b, bb in zip(spec["nodes"][i + 1:], boxes[i + 1:]):
                    tall = max(boxes[i][3] - boxes[i][1], bb[3] - bb[1])
                    assert ((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2) ** 0.5 >= tall + 10 - 0.5, (a, b)
    assert next(n for n in spec["nodes"] if n["id"] == "c0")["title"].startswith("Annabelle Montgomery-Hale Jr.")


def test_a_long_timeline_spaces_its_events_and_labels():
    spec = draw.build(big_bible(), "timeline", "")
    rails = sorted({p["y"] for p in spec["pins"]})
    assert len(rails) == 2 and positioned(spec)
    for y in rails:
        xs = sorted(p["x"] for p in spec["pins"] if p["y"] == y)
        assert len(xs) == 60 and min(b - a for a, b in zip(xs, xs[1:])) >= 90
    first = [i for i, ln in enumerate(spec["lines"]) if ln["cls"] != "join"][0]
    assert all(ln["cls"] == "join" for ln in spec["lines"][:first]) and first == 60     # joins drawn first, behind

    def box(t: dict) -> tuple:
        px, asc = (draw.NAME_PX, 13) if t["cls"] in ("label", "head") else (draw.SMALL_PX, 11)
        w = max(len(s) for s in t["lines"]) * px
        x0 = t["x"] - w / 2 if t["anchor"] == "middle" else t["x"]
        return x0, t["y"] - asc, x0 + w, t["y"] + (len(t["lines"]) - 1) * 16.25 + 3
    boxes = [box(t) for t in spec["texts"]]
    no_overlap(boxes)
    assert all(0 <= b[0] and b[2] <= spec["width"] and 0 <= b[1] and b[3] <= spec["height"] for b in boxes)


def test_touchpoints_turn_their_headers_past_twelve_sections():
    spec = draw.build(big_bible(), "touchpoints", "")
    heads = [t for t in spec["texts"] if t.get("rotate")]
    assert len(heads) == 60 and all(t["rotate"] == -45 for t in heads)
    cols = sorted({p["x"] for p in spec["pins"]})
    assert min(b - a for a, b in zip(cols, cols[1:])) >= 22
    names = [t for t in spec["texts"] if t["cls"] == "label"]
    assert len({t["x"] for t in names}) == 1 and all(t["anchor"] == "end" for t in names)
    assert min(cols) - names[0]["x"] >= 12
    small = draw.build(SAMPLE, "touchpoints", "")
    assert not any(t.get("rotate") for t in small["texts"])


def test_fixture_boards_match_the_sample_bible():
    """fixtures/boards.sample.json is what the server draws from fixtures/bible.sample.json."""
    got = json.loads((PKG / "fixtures" / "boards.sample.json").read_text(encoding="utf-8"))["boards"]
    for b in got:
        if b["id"].startswith("std-"):
            assert b["spec"] == draw.build(SAMPLE, b["kind"], ""), b["id"]
        else:
            assert b["spec"] == draw.build(SAMPLE, b["kind"], b["asked"]), b["id"]
    listed = json.loads((PKG / "fixtures" / "list.sample.json").read_text(encoding="utf-8"))
    assert {b["id"] for b in listed["manuscripts"][0]["boards"]} <= {b["id"] for b in got}


def test_zz_other_files_never_changed(env):
    after = {f.name: hashes(f) for f in env["shelf"].iterdir()}
    assert after == env["before"]
    assert not (env["shelf"] / "Not Read Yet" / "boards.json").exists()
    assert sorted(p.name for p in env["book"].iterdir()) == sorted(["bible.json", "boards.json", "characters.md",
                                                                    "corrections.json", "manuscript.json",
                                                                    "pictures", "pictures.json", "pointer.json",
                                                                    "sections", "views.json"])
    assert sorted(p.name for p in env["data"].iterdir()) == ["maker.json"]      # the package's own folder
