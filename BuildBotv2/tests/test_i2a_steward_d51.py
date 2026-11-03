"""Integration lane I2a: Steward runs D51 run blocks (what Toolsmith writes for an adopted app), and pip never
installs into the owner's own Python (found by the live adopt run)."""

import json
import os
import urllib.request

import pytest

from conftest import wait_for, yes
from puffpai_core.steward import StewardError
from puffpai_core.steward.steward import block_env

SERVER = r'''
import os, http.server
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = (os.environ.get("GREETING", "") + "|" + open("../settings.txt").read() + "|"
                + open("made.txt").read() + "|" + os.getcwd()).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer((os.environ["HOST"], int(os.environ["PORT"])), H).serve_forever()
'''


@pytest.fixture
def d51_folder(tmp_path):
    folder = tmp_path / "src" / "d51-app"
    (folder / "web").mkdir(parents=True)
    (folder / "web" / "server.py").write_text(SERVER, encoding="utf-8")
    (folder / "settings.example").write_text("from-example", encoding="utf-8")
    manifest = {
        "schema": "puffpai-package/1", "id": "d51-app", "kind": "app", "name": "D51 App", "version": "1.0.0",
        "description": "A test app with a D51 run block.", "author": "Cypher Sisters", "license": "MIT",
        "run": {"default": {"type": "command",
                            "setup": [{"copy": ["settings.example", "settings.txt"]},
                                      ["{python}", "-c", "open('made.txt', 'w').write('made')"]],
                            "start": ["{python}", "server.py"],
                            "env": {"PORT": "{port}", "HOST": "127.0.0.1", "GREETING": "hi {port}"},
                            "cwd": "web"}},
        "health": {"url": "http://127.0.0.1:{port}/", "expect": 200, "every_s": 60},
        "ui": {"pages": [{"id": "main", "title": "D51", "url": "http://127.0.0.1:{port}/", "show": "new_tab"}]},
        "needs": {"hosts": []},
        "consent": "A test app. It does not use the internet.",
    }
    (folder / "puffpai.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def test_a_d51_run_block_sets_up_and_starts_with_its_env_and_folder(core, drawbridge, d51_folder, state):
    core.packages.start_after_install = False
    assert yes(core, core.packages.add(str(d51_folder)))["status"] == "installed"
    pkg = state / "packages" / "d51-app"
    assert (pkg / "settings.txt").read_text(encoding="utf-8") == "from-example"        # {"copy": [...]}
    assert (pkg / "web" / "made.txt").read_text(encoding="utf-8") == "made"            # argument list, in cwd
    row = core.steward.start("d51-app")
    try:
        def page():
            try:
                with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        f"http://127.0.0.1:{row['port']}/", timeout=2) as r:
                    return r.read().decode()
            except OSError:
                return None
        got = wait_for(page, 20)
        greeting, settings, made, cwd = got.split("|")
        assert greeting == f"hi {row['port']}" and settings == "from-example" and made == "made"
        assert cwd.lower().endswith(os.path.join("d51-app", "web"))
    finally:
        core.steward.stop("d51-app")


def test_a_copy_never_replaces_her_file_and_never_leaves_the_folder(core, drawbridge, d51_folder, state):
    core.packages.start_after_install = False
    (d51_folder / "settings.txt").write_text("hers", encoding="utf-8")
    assert yes(core, core.packages.add(str(d51_folder)))["status"] == "installed"
    assert (state / "packages" / "d51-app" / "settings.txt").read_text(encoding="utf-8") == "hers"
    folder = state / "packages" / "d51-app"            # the schema refuses this too; Steward checks again itself
    with pytest.raises(StewardError, match="outside its own folder"):
        core.steward._file_step("d51-app", folder, {}, {"copy": ["settings.example", "../../outside.txt"]}, 1, {},
                                core.steward.log_path("d51-app"))
    assert not (state / "outside.txt").exists()


def test_block_env_never_sets_core_bridge_or_system_names():
    got = block_env({"env": {"PORT": "{port}", "PATH": "x", "PUFFPAI_TOKEN": "t", "https_proxy": "p",
                             "PIP_REQUIRE_VIRTUALENV": "0", "NODE_OPTIONS": "--require x", "OK_NAME": "1"}},
                    {"port": "18500"})
    assert got == {"PORT": "18500", "OK_NAME": "1"}


def test_pip_installs_only_into_a_virtual_environment(core, tmp_path):
    from puffpai_core.zone3.toolsmith_host.broker import Broker
    env = Broker(tmp_path, tmp_path / "scratch", {}).env(18500)
    assert env["PIP_REQUIRE_VIRTUALENV"] == "1"
    assert core.steward.env("x", {"kind": "app"}, 18500, "t", {})["PIP_REQUIRE_VIRTUALENV"] == "1"
