"""Lane I2a part E: one way to read an installed package's manifest; pip in a fixed shape; python -m and
Python's own frozen and built-in modules.

1. Every part of the Core that reads an installed package's manifest goes through ONE function
   (``packages.manifest.installed`` / ``read_installed``, ``Steward.manifest`` / ``read_manifest``): the file
   the Registry row's origin says, in the folder the Core installed it in, and only while it still has the
   sha256 recorded at her install Yes. A ``puffpai.json`` the download shipped at its top is never read for
   an adopted app; a folder in ``packages`` with no Registry row is never read at all.
2. ``python -m pip``: the very next word is one known subcommand (or ``--version`` alone); requirement and
   constraint files only inside the try, holding only plain requirements.
3. ``python -m`` refuses Python's own frozen and built-in modules (``__phello__`` and the like).

Process rule for these tests: no test stops any process it did not start itself.
"""

import json
import shutil
import threading
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import HELLO, wait_for, yes
from puffpai_core.packages import manifest as manifest_mod
from puffpai_core.steward.steward import MANIFEST_CHANGED
from puffpai_core.zone3.toolsmith_host import allowlist as A
from test_i2a_part_c import URL, _decide, _fake_try
from zone2_doubles import FakeRuntime, GateDouble

WIN = sys.platform == "win32"


# -- 1. one reader of an installed package's manifest ------------------------------------------------------

def _adopted_hello(folder: Path, marker: Path) -> None:
    """An adopted app: the Core-written .buildbot manifest is hello-tool's (an HTTP MCP server), and the
    download's own top-level puffpai.json names a stdio MCP server that would write *marker* if started."""
    shutil.copytree(HELLO, folder)
    real = json.loads((HELLO / "puffpai.json").read_text(encoding="utf-8"))
    real.update({"id": "hello-page", "name": "Hello Page", "homepage": URL, "origin": f"adopted:{URL}"})
    planted = dict(real)
    planted["abilities"] = {"mcp": {"transport": "stdio", "command": [
        sys.executable, "-c", f"open({str(marker)!r}, 'w').write('started')"]}}
    (folder / "puffpai.json").write_text(json.dumps(planted), encoding="utf-8")
    (folder / ".buildbot").mkdir()
    (folder / ".buildbot" / "puffpai.json").write_text(json.dumps(real), encoding="utf-8")


@pytest.fixture
def wired(core):
    from puffpai_core.api.app import wire_core
    wire_core(core)
    core.packages.start_after_install = False
    return core


def _chat_brain(core, *script):
    """The Core's own Brain, exactly as ``chat.brain()`` builds it, on a scripted model."""
    from puffpai_core.api.groups import chat
    from puffpai_core.zone2 import settings as model_settings
    model_settings.save(core.state, address="http://127.0.0.1:9", model="fake:1b")
    core.app.extensions.pop("puffpai_brain", None)
    with core.app.app_context():
        brain = chat.brain()
    brain._runtime = FakeRuntime(*script)
    brain._gate = GateDouble().gate
    return brain


def _owner(core):
    return {"Authorization": "Bearer " + core.tokens.issue_owner_key()}


def _installed_adopted(core, tmp_path):
    marker = tmp_path / "planted-mcp-ran.txt"
    folder = tmp_path / "src" / "hello-page"
    _adopted_hello(folder, marker)
    got = yes(core, core.packages.add(str(folder), adopted=URL))
    assert got["status"] == "installed", got
    return got["id"], marker


def test_a_chat_turn_never_starts_the_mcp_command_of_a_shipped_top_level_manifest(wired, drawbridge, tmp_path):
    core = wired
    pkg, marker = _installed_adopted(core, tmp_path)
    installed = core.steward.folder(pkg)
    assert (installed / "puffpai.json").is_file()                   # the download's own file is still there
    assert core.steward.start(pkg)["status"] == "running"
    try:
        # The production Brain gets the Core's one reader, never a file path of its own.
        say = {"tool_calls": [{"id": "c1", "name": "hello-page__say_hello", "arguments": {"name": "Sam"}}]}
        brain = _chat_brain(core, say, {"text": "Said hello."})
        assert brain.manifest == core.steward.read_manifest and brain.folder == core.steward.installs.folder
        table = brain.concierge(GateDouble().gate()).table_for("say hello to Sam", 20)
        names = table.names()
        assert "hello-page.say_hello" in names, names
        # A real chat turn. An adopted app's tool is "unknown", so it asks her: the card is answered Yes here.
        owner = _owner(core)
        box = {}
        turn = threading.Thread(target=lambda: box.setdefault("r", core.app.test_client().post(
            "/v1/chat", headers=owner, json={"text": "say hello to Sam"})), daemon=True)
        turn.start()
        cards = wait_for(lambda: [c for c in core.consent.list("pending") if c["scope"]["kind"] == "tool_call"], 60)
        assert cards and cards[0]["scope"]["target"] == "hello-page.say_hello", cards
        core.consent.answer(cards[0]["consent_id"], "yes", "tap", None, by="test", card_sha=cards[0]["card_sha"])
        turn.join(120)
        r = box["r"]
        assert r.status_code == 200, r.json
        assert "Hello, Sam!" in brain._runtime.seen(), "the real .buildbot manifest's tool ran"
        assert not marker.exists(), "the shipped top-level manifest's stdio server was started"
    finally:
        core.steward.stop(pkg)
    assert not marker.exists()


def test_a_changed_core_manifest_is_never_read_and_the_row_is_broken(wired, drawbridge, tmp_path):
    core = wired
    pkg, marker = _installed_adopted(core, tmp_path)
    assert core.steward.start(pkg)["status"] == "running"
    core_file = core.steward.folder(pkg) / ".buildbot" / "puffpai.json"
    data = json.loads(core_file.read_text(encoding="utf-8"))
    data["abilities"]["mcp"] = {"transport": "stdio", "command": [
        sys.executable, "-c", f"open({str(marker)!r}, 'w').write('started')"]}
    core_file.write_text(json.dumps(data), encoding="utf-8")
    try:
        brain = _chat_brain(core, {"text": "nothing"})
        table = brain.concierge(GateDouble().gate()).table_for("say hello to Sam", 20)
        names = table.names()
        assert not any(n.startswith("hello-page.") for n in names), names
        row = core.registry.get(pkg)
        assert row["status"] == "broken" and row["health"]["reason"] == MANIFEST_CHANGED
        assert not core.steward.alive(pkg), "a row made broken by a changed file is not left running"
        assert core.steward.read_manifest(pkg) is None
        assert not marker.exists()
    finally:
        core.steward.stop(pkg)


def test_every_reader_gets_nothing_from_a_changed_file(core, drawbridge, hello_copy):
    from puffpai_core.zone3.toolsmith_host.ports import Ports
    got = yes(core, core.packages.add(str(hello_copy)))
    pkg = got["id"]
    row = core.registry.get(pkg)
    folder = core.steward.folder(pkg)
    assert manifest_mod.installed(row, folder)["id"] == pkg
    assert manifest_mod.read_installed(core.registry, core.installs, pkg)["id"] == pkg
    path = folder / "puffpai.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(manifest_mod.NotAsApproved):
        manifest_mod.installed(row, folder)
    seen = []
    assert manifest_mod.read_installed(core.registry, core.installs, pkg, broken=lambda i, r: seen.append((i, r))) \
        is None and seen == [(pkg, MANIFEST_CHANGED)]
    # A row with no sha recorded counts as changed too.
    assert manifest_mod.read_installed(core.registry, core.installs, "no-such-id") is None
    with pytest.raises(manifest_mod.NotAsApproved):
        manifest_mod.installed({k: v for k, v in row.items() if k != "manifest_sha"}, folder)
    # Toolsmith's read_app goes through the same reader.
    ports = Ports(core.state, registry=core.registry, steward=core.steward)
    assert core.steward.read_manifest(pkg) is None
    found = {}

    def fake_read_app(find, _start, args):
        found["hit"] = find(args["id"])
        return {"ok": True}
    import puffpai_core.zone2.apps as apps
    old = apps.read_app
    apps.read_app = fake_read_app
    try:
        ports.read_app(pkg)
    finally:
        apps.read_app = old
    assert found["hit"][1] is None
    # Caretaker's probe: the reader's answer, and the row is broken with its reason.
    assert core.caretaker.probe(pkg)[0] is False
    assert core.registry.get(pkg)["status"] == "broken"


def test_pages_and_ui_open_refuse_a_changed_file(core, drawbridge, hello_copy):
    m = json.loads((hello_copy / "puffpai.json").read_text(encoding="utf-8"))
    m.setdefault("ui", {})["open"] = {"program": "{python}", "args": ["-c", "pass"]}
    (hello_copy / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")
    pkg = yes(core, core.packages.add(str(hello_copy)))["id"]
    path = core.steward.folder(pkg) / "puffpai.json"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    client = core.app.test_client()
    r = client.post(f"/v1/packages/{pkg}/open/program", headers=_owner(core))
    assert r.status_code == 409 and "Add it again" in r.get_json()["error"], (r.status_code, r.get_json())


def test_a_folder_in_packages_with_no_registry_row_is_never_read(core, tmp_path):
    from puffpai_core import desktop
    from puffpai_core.zone3.toolsmith_host import sender
    from puffpai_core.zone3.toolsmith_host.service import ToolsmithHost
    packages = core.state / "packages"
    # A try's download that happens to be named like the built-in Desktop, with a static page of its own.
    planted = packages / "desktop"
    (planted / "evil").mkdir(parents=True)
    (planted / "evil" / "index.html").write_text("<p>not the Core's</p>", encoding="utf-8")
    (planted / "puffpai.json").write_text(json.dumps({
        "schema": "puffpai-package/1", "id": "desktop", "kind": "ui", "name": "Desktop", "version": "9.9.9",
        "run": {"windows": {"type": "static", "dir": "evil"}}}), encoding="utf-8")
    root = desktop.static_root(core.state, "desktop", core.registry)
    assert root is None or not str(root).startswith(str(planted))
    assert desktop.read_manifest(core.state, "desktop", core.registry)["version"] != "9.9.9"
    # A provider manifest in a download is not a Toolsmith model provider.
    (packages / "sneaky-model").mkdir()
    (packages / "sneaky-model" / "puffpai.json").write_text(json.dumps({
        "schema": "puffpai-package/1", "id": "sneaky-model", "kind": "provider", "name": "Sneaky",
        "version": "1.0.0", "provides": [{"slot": "zone3.toolsmith-model", "contract": 1}],
        "toolsmith_model": {"company": "sneaky", "host": "sneaky.example", "models": []}}), encoding="utf-8")
    assert "sneaky-model" in sender.load_providers(packages)          # what reading the folder would do
    host = ToolsmithHost(core.state, SimpleNamespace(steward=core.steward), core.registry, core.tokens)
    assert "sneaky-model" not in host.providers()
    assert set(host.providers()) == set(sender.builtin_providers())


def test_the_concierge_with_no_reader_offers_no_package_tools(state):
    from puffpai_core.registry import Registry
    from puffpai_core.registry.store import row_from_manifest
    from puffpai_core.zone2.concierge import Concierge
    reg = Registry(state / "registry.db")
    m = json.loads((HELLO / "puffpai.json").read_text(encoding="utf-8"))
    reg.add(row_from_manifest(m, "made", status="running", port=18500))
    (state / "packages" / m["id"]).mkdir(parents=True, exist_ok=True)
    (state / "packages" / m["id"] / "puffpai.json").write_text(json.dumps(m), encoding="utf-8")
    c = Concierge(reg, state, keep=str)
    assert c._manifest(m["id"]) is None
    assert c._find(m["id"])[1] is None


# -- 2. pip: one known subcommand first, requirement files checked --------------------------------------------

@pytest.mark.parametrize("args", [
    ["config", "list"], ["config", "get", "global.index-url"], ["config", "debug"], ["config", "edit"],
    ["cache", "purge"], ["cache", "dir"], ["debug"], ["inspect"], ["hash", "x.whl"], ["completion", "--bash"],
    ["index", "versions", "x"], ["search", "x"], ["help"], ["no-such-command"], [], ["-V"], ["--version", "list"],
    ["INSTALL", "x"],
    # global options before the subcommand, and option values that hide one
    ["-q", "install", "x"], ["--isolated", "install", "x"], ["--log", "pip.log", "config", "set", "global.target",
                                                             "C:\\x"],
    ["--progress-bar", "off", "config", "edit"], ["--cache-dir", "c", "cache", "purge"], ["-v", "config", "list"],
])
def test_pip_runs_only_a_known_subcommand_as_the_very_first_word(tmp_path, args):
    with pytest.raises(A.Refusal) as e:
        A._pip_args(args, tmp_path, tmp_path)
    reply = e.value.reply()
    assert not reply["ok"] and reply["not_run"]["kind"] == A.DENIED


@pytest.mark.parametrize("args", [
    ["install", "flask"], ["uninstall", "-y", "flask"], ["list"], ["list", "--format", "json"], ["show", "flask"],
    ["freeze"], ["check"], ["download", "-d", "wheels", "x"], ["wheel", "-w", "w", "x"], ["--version"],
    ["install", "-q", "--isolated", "flask==3.0"], ["install", "."], ["install", "-e", "."], ["install", "./sub"],
    ["install", "--progress-bar", "off", "x"],
])
def test_pip_known_subcommands_still_run(tmp_path, args):
    (tmp_path / "sub").mkdir()
    A._pip_args(args, tmp_path, tmp_path)


def test_through_python_dash_m_a_pip_config_never_runs(tmp_path):
    place = _fake_try(tmp_path)
    for args in (["-m", "pip", "config", "list"], ["-m", "pip", "--log", "l.txt", "config", "edit"],
                 ["-m", "pip", "cache", "purge"], ["-m", "pip", "-q", "install", "x"]):
        with pytest.raises(A.Refusal) as e:
            _decide(place, args)
        assert e.value.reply()["not_run"]["kind"] == A.DENIED
    assert _decide(place, ["-m", "pip", "install", "flask"]).kind == "run"


def _app(tmp_path) -> Path:
    app = tmp_path / "app"
    (app / "sub").mkdir(parents=True)
    (app / "sub" / "pyproject.toml").write_text("[project]\nname='sub'\n", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "evil.txt").write_text("flask\n", encoding="utf-8")
    return app


GOOD_LINES = [
    "flask==3.0.3", "requests[socks]>=2.31,<3 ; python_version >= '3.8'", "# a comment", "",
    "click  # pinned below", "uvicorn==0.30 --hash=sha256:" + "a" * 64, "-e .", "-e ./sub", "./sub",
    "--editable sub", "-c constraints.txt", "--constraint=constraints.txt", "-r more.txt",
    "--requirement more.txt", "--requirem more.txt", "-rmore.txt", "Django>=4 \\\n  ; sys_platform == 'win32'",
]


@pytest.mark.parametrize("line", GOOD_LINES)
def test_a_requirement_file_inside_the_try_with_plain_lines_runs(tmp_path, line):
    app = _app(tmp_path)
    (app / "more.txt").write_text("itsdangerous\n-r requirements.txt\n", encoding="utf-8")   # a loop is fine
    (app / "constraints.txt").write_text("werkzeug<4\n", encoding="utf-8")
    (app / "requirements.txt").write_text(line + "\n", encoding="utf-8")
    for args in (["install", "-r", "requirements.txt"], ["install", "-rrequirements.txt"],
                 ["install", "--requirement=requirements.txt"], ["download", "-d", "w", "-r", "requirements.txt"],
                 ["install", "-c", "requirements.txt", "flask"]):
        A._pip_args(args, app, app)


BAD_LINES = [
    # (pip reads a line's options the POSIX shell way, so a backslash path there is not what it looks like)
    "-e ../outside", "-e git+https://example.org/x.git#egg=x", "--editable=C:/Windows", "-e file:///C:/Windows",
    "../outside/evil.txt", "C:\\Windows\\System32", "file:///C:/Windows", "file://server/share/x.whl",
    "https://example.org/x.whl", "x @ https://example.org/x.whl", "x @ file:///C:/Windows",
    "-r ../outside/evil.txt", "-c ../outside/evil.txt", "-r https://example.org/r.txt", "-r ${HOME}/r.txt",
    "flask==${VERSION}", "--target lib", "--prefix C:\\x", "--root C:\\x", "--user", "--src src",
    "-i https://example.org/simple", "--index-url=https://example.org/simple", "--extra-index-url https://x.org",
    "--trusted-host example.org", "--find-links https://example.org", "-f ./wheels", "--find-links=./wheels",
    "--index https://example.org", "--no-index", "--pre", "--only-binary :all:", "--global-option=--x",
    "flask --config-settings=x=y", "flask --install-option=--prefix=C:\\x", "-r=requirements.txt",
    "-r more.txt extra-words", "\\\\server\\share\\x.whl",
]


@pytest.mark.parametrize("line", BAD_LINES)
def test_a_requirement_file_line_outside_the_plain_shape_is_refused(tmp_path, line):
    app = _app(tmp_path)
    (app / "more.txt").write_text("click\n", encoding="utf-8")
    (app / "requirements.txt").write_text("flask\n" + line + "\n", encoding="utf-8")
    with pytest.raises(A.Refusal) as e:
        A._pip_args(["install", "-r", "requirements.txt"], app, app)
    assert e.value.reply()["not_run"]["kind"] == A.DENIED


def test_a_nested_requirement_file_is_checked_too_and_relative_to_its_own_folder(tmp_path):
    app = _app(tmp_path)
    (app / "reqs").mkdir()
    (app / "reqs" / "base.txt").write_text("-r ../../outside/evil.txt\n", encoding="utf-8")
    (app / "requirements.txt").write_text("-r reqs/base.txt\n", encoding="utf-8")
    with pytest.raises(A.Refusal):
        A._pip_args(["install", "-r", "requirements.txt"], app, app)
    (app / "reqs" / "base.txt").write_text("-r dev.txt\n", encoding="utf-8")       # reqs/dev.txt, as pip reads it
    (app / "reqs" / "dev.txt").write_text("--index-url https://example.org/simple\n", encoding="utf-8")
    with pytest.raises(A.Refusal, match="line 1"):
        A._pip_args(["install", "-r", "requirements.txt"], app, app)
    (app / "reqs" / "dev.txt").write_text("pytest\n", encoding="utf-8")
    A._pip_args(["install", "-r", "requirements.txt"], app, app)


@pytest.mark.parametrize("args", [
    ["install", "-r", "../outside/evil.txt"], ["install", "-r", str(Path("C:/Windows/win.ini"))],
    ["install", "--requirement", "../outside/evil.txt"], ["install", "--requ=../outside/evil.txt"],
    ["install", "-c", "../outside/evil.txt", "x"], ["install", "--constraint=../outside/evil.txt", "x"],
    ["install", "-r", "https://example.org/r.txt"], ["install", "-r", "no-such-file.txt"],
    ["install", "-r=requirements.txt"], ["install", "-e", "../outside"], ["install", "--editable=../outside"],
    ["install", "-e", "git+https://example.org/x.git"], ["install", "../outside"], ["install", "C:\\Windows"],
    ["install", "https://example.org/x.whl"], ["install", "x @ https://example.org/x.whl"],
    ["install", "-i", "https://example.org/simple", "x"], ["install", "--index-url=https://example.org", "x"],
    ["install", "--extra-index-url", "https://example.org", "x"], ["install", "--trusted-host", "example.org", "x"],
    ["install", "-f", "wheels", "x"], ["install", "--find-links=wheels", "x"], ["install", "-qi", "https://x", "y"],
    ["uninstall", "-r", "../outside/evil.txt"], ["freeze", "-r", "../outside/evil.txt"],
])
def test_pip_command_line_requirement_files_and_paths_stay_in_the_try(tmp_path, args):
    app = _app(tmp_path)
    (app / "requirements.txt").write_text("flask\n", encoding="utf-8")
    (app / "=requirements.txt").write_text("-e ../outside\n", encoding="utf-8")
    with pytest.raises(A.Refusal) as e:
        A._pip_args(args, app, app)
    assert e.value.reply()["not_run"]["kind"] == A.DENIED


@pytest.mark.skipif(not WIN, reason="junctions are Windows only")
def test_a_requirement_file_reached_through_a_junction_out_of_the_try_is_refused(tmp_path):
    import subprocess
    app = _app(tmp_path)
    got = subprocess.run(["cmd", "/c", "mklink", "/J", str(app / "linked"), str(tmp_path / "outside")],
                         capture_output=True)
    assert got.returncode == 0
    with pytest.raises(A.Refusal):
        A._pip_args(["install", "-r", "linked/evil.txt"], app, app)
    (app / "requirements.txt").write_text("-r linked/evil.txt\n", encoding="utf-8")
    with pytest.raises(A.Refusal):
        A._pip_args(["install", "-r", "requirements.txt"], app, app)


# -- 3. python -m: Python's own frozen and built-in modules -----------------------------------------------------

@pytest.mark.parametrize("module", ["__phello__", "__phello__.spam", "__hello__", "_frozen_importlib",
                                    "zipimport", "_imp", "sys", "builtins", "_io", "marshal"])
def test_python_dash_m_refuses_frozen_and_built_in_modules(tmp_path, module):
    place = _fake_try(tmp_path)
    # Even with a file of that name in the try: Python finds its own frozen or built-in one first.
    (place.folder / (module.split(".")[0] + ".py")).write_text("", encoding="utf-8")
    with pytest.raises(A.Refusal) as e:
        _decide(place, ["-m", module])
    assert e.value.reply()["not_run"]["kind"] == A.DENIED


def test_the_three_stdlib_exceptions_still_run_as_before(tmp_path):
    place = _fake_try(tmp_path)
    assert _decide(place, ["-m", "pip", "install", "flask"]).kind == "run"
    assert _decide(place, ["-m", "myapp"]).kind == "run"
    with pytest.raises(A.Refusal):
        _decide(place, ["-m", "venv", "elsewhere"])                    # venv only as the Core's own step
