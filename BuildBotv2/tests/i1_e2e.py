"""Integration lane I1: the end-to-end run on REAL parts, step by step (pytest ``-m e2e`` and the live script).

A fresh bot folder; the Core on two free ports (its own address and the pages address) and the Bridge proxy
on a free port; the vault set up through the Core API (lane L3: PIN and recovery code); FreshTable added
from the PuffPAI store with ``puffpai add freshtable``, as she would (lane I10c: the wizard lists no apps;
Drawbridge, the Yes card, install, venv, Steward, Caretaker, MCP tools in the Registry);
its page and widget through the pages address; "put milk on the shopping list" through ``/v1/chat`` with
lane L3's gate and this computer's local model; then example-page and cloud-avatar dropped in. Everything
started here is stopped at the end (the packages by their job objects, the listeners by their servers).

``run(state, model=..., address=...) -> [steps]``; each step is ``{"step", "ok", "said"}``. A step that
fails stops the run (later steps need it), and the rest is reported as not run.
"""

from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parents[1]
PIN = "Tq7#mz2Lp!"                     # a made-up test PIN
MODEL = "qwen2.5:7b-instruct"
ADDRESS = "http://127.0.0.1:11435"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _open(url: str, *, method: str = "GET", body: Any = None, headers: dict | None = None,
          timeout: float = 30) -> tuple[int, dict, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json",
                                                                       **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _wait(fn: Callable[[], Any], timeout_s: float, every_s: float = 0.5) -> Any:
    end = time.monotonic() + timeout_s
    got = fn()
    while not got and time.monotonic() < end:
        time.sleep(every_s)
        got = fn()
    return got


class Run:
    def __init__(self, state: Path, model: str, address: str, log: Callable[[str], None]) -> None:
        self.state, self.model, self.address, self.log = Path(state), model, address, log
        self.steps: list[dict] = []
        self.port, self.pages, self.proxy = free_port(), free_port(), 0
        self.app = self.core = self.servers = self.thread = self.terminal = None

    # -- bookkeeping --------------------------------------------------------------------------
    def step(self, name: str, ok: bool, said: str = "") -> bool:
        self.steps.append({"step": name, "ok": bool(ok), "said": said})
        self.log(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {said}" if said else ""))
        return bool(ok)

    def api(self, method: str, path: str, body: Any = None, timeout: float = 30) -> tuple[int, Any]:
        status, _h, raw = _open(f"http://127.0.0.1:{self.port}{path}", method=method, body=body,
                                headers={"Authorization": f"Bearer {self.terminal}"}, timeout=timeout)
        try:
            return status, json.loads(raw.decode("utf-8"))
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    def page(self, package: str, path: str = "/", origin: str | None = None) -> tuple[int, dict, bytes]:
        headers = {"Host": f"{package}.localhost:{self.pages}"}
        if origin:
            headers["Origin"] = origin
        return _open(f"http://127.0.0.1:{self.pages}{path}", headers=headers)

    def row(self, id: str) -> dict:
        status, row = self.api("GET", f"/v1/registry/{id}")
        return row if status == 200 and isinstance(row, dict) else {}

    # -- the Core ----------------------------------------------------------------------------
    def start(self) -> bool:
        from puffpai_core.api.app import create_app, make_servers, run_servers, start_core
        os.environ["PUFFPAI_STATE"] = str(self.state)
        self.app = create_app(self.state, port=self.port, pages_port=self.pages)
        self.core = self.app.extensions["puffpai"]
        self.core.proxy_port = 0
        self.servers = make_servers(self.app, self.port, self.pages)
        self.terminal = start_core(self.core)
        self.thread = threading.Thread(target=run_servers, args=(self.servers,), name="i1-core", daemon=True)
        self.thread.start()
        up = _wait(lambda: self.api("GET", "/v1/health")[0] == 200, 20)
        z = self.core.zone3
        proxy = z.proxy.port if z is not None and z.proxy is not None else None
        return self.step("Core started (fresh state)", up and not self.core.caretaker.in_safe_mode,
                         f"core 127.0.0.1:{self.port}, pages {self.pages}, Bridge proxy {proxy}, "
                         f"safe mode {self.core.caretaker.in_safe_mode} {self.core.caretaker.safe_reasons}")

    def stop(self) -> None:
        from puffpai_core.api.app import stop_core, stop_servers
        if self.servers is not None:
            stop_servers(self.servers)
        if self.thread is not None:
            self.thread.join(15)
        if self.core is not None:
            running = list(self.core.steward.running())
            pids = [p for i in running for p in self.core.steward.pids(i)]
            stop_core(self.core, self.terminal)
            self.step("Everything stopped", not self.core.steward.running(),
                      f"stopped {running} (pids {pids}); listeners and proxy closed")

    # -- the steps ---------------------------------------------------------------------------
    def vault(self) -> bool:
        status, body = self.api("POST", "/v1/vault/setup", {"pin": PIN})
        code = body.get("recovery_code") if isinstance(body, dict) else None
        if not self.step("Vault set up (PIN + recovery code, lane L3)", status == 201 and bool(code),
                         f"HTTP {status}" + ("" if code else f" {body}")):
            return False
        status, body = self.api("POST", "/v1/vault/recovery-code/test", {"code": code})
        ok = status == 200 and body.get("ok") is True
        from puffpai_core import seams
        pin_ok = seams.check_pin(self.state, PIN, self.core.bus) is None
        return self.step("Recovery code checks; the one PIN check says yes", ok and pin_ok,
                         f"recovery test HTTP {status} {body}; check_pin ok={pin_ok}")

    def add(self, folder: Path | str) -> tuple[int, str]:
        from puffpai_core import cli
        out = io.StringIO()
        real_input = builtins.input
        builtins.input = lambda prompt="": "y"                   # she says Yes on the card
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(["add", str(folder)])
        finally:
            builtins.input = real_input
        return code, out.getvalue()

    def freshtable(self) -> bool:
        status, shelf = self.api("GET", "/v1/packages/available")       # the PuffPAI store's list
        listed = status == 200 and any(r.get("id") == "freshtable" for r in shelf or [])
        if not self.step("FreshTable is in the PuffPAI store (GET /v1/packages/available)", listed,
                         f"HTTP {status} {json.dumps(shelf)[:300]}"):
            return False
        code, said = self.add("freshtable")                             # puffpai add freshtable, Yes on its card
        row = self.row("freshtable")
        if not self.step("puffpai add freshtable from the store: its Yes card, installed (Drawbridge, Yes card, "
                         "install)", code == 0 and bool(row.get("id")),
                         f"exit {code}; " + " | ".join(x.strip() for x in said.splitlines() if x.strip())[:600]):
            return False
        audit = (self.state / "logs" / "audit.jsonl").read_text(encoding="utf-8") if \
            (self.state / "logs" / "audit.jsonl").is_file() else ""
        checked = '"action": "check_package"' in audit or '"action":"check_package"' in audit
        self.step("Drawbridge (lane L4) checked it, audit line written", checked,
                  "audit.jsonl has a check_package line" if checked else "no check_package line in audit.jsonl")
        venv = (self.state / "packages" / "freshtable" / ".venv").is_dir()
        self.step("Its venv is packages/freshtable/.venv (python: true)", venv, str(self.state / "packages" /
                                                                                  "freshtable" / ".venv"))
        row = _wait(lambda: (lambda r: r if r.get("status") == "running" and r.get("health", {}).get("ok") else None)(
            self.row("freshtable")), 90)
        if not self.step("Steward started it; Caretaker health is running", bool(row),
                         json.dumps({k: (row or self.row("freshtable")).get(k) for k in ("status", "health", "port")})):
            return False
        tools = _wait(lambda: [a["tool"] for a in self.row("freshtable").get("abilities") or []], 60)
        if not self.step("Its MCP tools are in the Registry", "freshtable.add_shopping" in (tools or []),
                         ", ".join(tools or [])):
            return False
        status, rows = self.api("GET", "/v1/registry")
        ft = next((r for r in rows if r["id"] == "freshtable"), {}) if status == 200 else {}
        ui = ft.get("ui") or {}
        self.step("The Desktop's /v1/registry shows its tray item and page",
                  bool(ui.get("tray")) and bool(ui.get("pages")), json.dumps({"tray": ui.get("tray"),
                                                                              "pages": ui.get("pages")}))
        status, headers, body = self.page("freshtable", "/")
        html = body.decode("utf-8", "replace")
        if not self.step(f"Its page loads at http://freshtable.localhost:{self.pages}/",
                         status == 200 and "<html" in html.lower(), f"HTTP {status}, {len(body)} bytes"):
            return False
        status, headers, body = self.page("freshtable", "/widget/list", origin=f"http://127.0.0.1:{self.port}")
        try:
            widget = json.loads(body)
        except ValueError:
            widget = None
        cors = headers.get("Access-Control-Allow-Origin")
        return self.step("Its widget JSON works (from the Desktop's origin)",
                         status == 200 and isinstance(widget, dict) and "items" in widget,
                         f"HTTP {status}, {body[:200]!r}, Access-Control-Allow-Origin {cors}")

    def chat(self) -> bool:
        from puffpai_core.zone2 import settings as model_settings
        model_settings.save(self.state, address=self.address, model=self.model)
        t0 = time.monotonic()
        status, body = self.api("POST", "/v1/chat", {"text": "put milk on the shopping list"}, timeout=300)
        took = time.monotonic() - t0
        said = json.dumps(body)[:500] if not isinstance(body, str) else body[:500]
        if not self.step(f"/v1/chat 'put milk on the shopping list' ({self.model}, real gate)", status == 200,
                         f"HTTP {status} in {took:.1f}s: {said}"):
            return False
        _s, _h, raw = self.page("freshtable", "/widget/list", origin=f"http://127.0.0.1:{self.port}")
        try:
            items = [str(i.get("text", "")).lower() for i in json.loads(raw).get("items") or []]
        except ValueError:
            items = []
        return self.step("FreshTable's list contains milk", any("milk" in i for i in items),
                         f"list: {items}; changed: {body.get('changed') if isinstance(body, dict) else None}")

    def drop_ins(self) -> bool:
        ok = True
        for pid, kind in (("example-page", "page"), ("cloud-avatar", "avatar")):
            code, said = self.add(REPO / "packages" / pid)
            row = self.row(pid)
            ok &= self.step(f"puffpai add packages/{pid} appears ({kind}, no Core edit)",
                            code == 0 and row.get("kind") == kind,
                            f"exit {code}, status {row.get('status')}; "
                            + " | ".join(x.strip() for x in said.splitlines() if x.strip())[:300])
        status, opened = self.api("POST", "/v1/packages/example-page/open/main")    # what the Desktop does
        url = opened.get("url", "") if isinstance(opened, dict) else ""
        path = "/" + url.split("/", 3)[3] if url.count("/") >= 3 else "/"
        status, _h, body = self.page("example-page", path)
        ok &= self.step(f"example-page loads at {url}", status == 200 and b"<html" in body[:500].lower(),
                        f"HTTP {status}, {len(body)} bytes")
        status, settings = self.api("GET", "/v1/settings")
        avatar = (settings.get("avatar") or {}) if isinstance(settings, dict) else {}
        ok &= self.step("The Desktop's settings name cloud-avatar as the avatar", avatar.get("id") == "cloud-avatar",
                        json.dumps(avatar))
        status, _h, body = self.page("cloud-avatar", "/preview.png")
        ok &= self.step("cloud-avatar's preview loads from its origin", status == 200 and body[:4] == bytes([0x89]) + b"PNG",
                        f"HTTP {status}, {len(body)} bytes")
        return ok


def run(state: Path, *, model: str = MODEL, address: str = ADDRESS,
        log: Callable[[str], None] = print) -> list[dict]:
    r = Run(state, model, address, log)
    try:
        if r.start() and r.vault() and r.freshtable():
            r.chat()
            r.drop_ins()
    finally:
        r.stop()
    return r.steps
