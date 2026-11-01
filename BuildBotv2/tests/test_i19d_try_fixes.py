"""Lane I19d (D70): three reasons no real app got working in a Toolsmith build.

RC1: Toolsmith's zone refused every ``docker compose`` for an app whose compose file has a ``/tmp`` tmpfs
line, because ``/tmp`` is the try's scratch folder and that folder was never allowed. Now exactly the try's
own scratch folder is allowed (``logs\\toolsmith\\<id>\\scratch``); nothing else under ``logs`` is, and a
refused compose file says Docker is not the problem.
RC3: Drawbridge held whole apps for small plain text files of kinds its list did not name. Now a short named
list is read and scanned like other text, and must be strict UTF-8 with no NUL byte; anything else still holds.
RC4: the Bridge refused hosts during a try and the try never heard why. Now the reply names the hosts.

Three unrelated apps throughout: a recipe box, an invoices app and a birdwatching app. No network, no Docker,
no real keys; every folder is the test's own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from puffpai_core.zone3 import drawbridge as dmod
from puffpai_core.zone3 import toolsmith_zone as Z
from puffpai_core.zone3.chain import ChainLog
from puffpai_core.zone3.drawbridge import ALLOW, BLOCK, HOLD, WARN, Drawbridge
from puffpai_core.zone3.ledger import Ledger
from puffpai_core.zone3.toolsmith_host.ports import Ports
from puffpai_core.zone3.toolsmith_host.trials import TryRecord, bridge_line

WIN = sys.platform == "win32"
APPS = ("recipe-box", "invoices", "birdwatch")


@pytest.fixture(autouse=True)
def _no_keyring(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")


# -- RC1: the try's own scratch folder ----------------------------------------------------------------------

@pytest.fixture
def bot(monkeypatch, tmp_path, state):
    """A bot folder with three tries, each with its logs and scratch folders where Trials puts them."""
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    for k, v in (("USERPROFILE", home), ("HOME", home), ("APPDATA", home / "AppData" / "Roaming"),
                 ("LOCALAPPDATA", home / "AppData" / "Local")):
        monkeypatch.setenv(k, str(v))
    (state / "settings").mkdir(parents=True)
    (state / "vault").mkdir()
    tries = {}
    for app in APPS:
        folder = state / "packages" / app
        folder.mkdir(parents=True)
        logs = state / "logs" / "toolsmith" / app
        (logs / "scratch").mkdir(parents=True)
        tries[app] = {"folder": str(folder), "logs": str(logs), "scratch": str(logs / "scratch")}
    (state / "logs" / "audit.jsonl").write_text("", encoding="utf-8")
    return {"state": state, "tries": tries}


def _check(bot, app, target, kind, *, write=False, scratch=None):
    t = bot["tries"][app]
    return Z.check(target, folder=t["folder"], kind=kind, write=write, cwd=t["folder"], logs=t["logs"],
                   scratch=t["scratch"] if scratch is None else scratch)


RECIPE_COMPOSE = ("services:\n  recipes:\n    build: .\n    ports:\n      - \"127.0.0.1:8080:8080\"\n"
                  "    tmpfs:\n      - /tmp:size=64m,mode=1777\n")
INVOICE_COMPOSE = ("services:\n  invoices:\n    image: invoices:latest\n    volumes:\n      - /tmp:/cache\n"
                   "      - ./data:/app/data\n")


def test_a_compose_file_with_tmp_is_allowed_for_the_trys_own_scratch(bot):
    assert _check(bot, "recipe-box", RECIPE_COMPOSE, "compose").verdict == "allow"
    assert _check(bot, "invoices", INVOICE_COMPOSE, "compose").verdict == "allow"
    # A birdwatching app run with a command that names the try's temp folder.
    for cmd in ("python count_birds.py %TEMP%\\birds.csv", "python count_birds.py $TMP/birds.csv",
                "python count_birds.py /tmp/birds.csv"):
        got = _check(bot, "birdwatch", cmd, "command")
        assert got.verdict == "allow", (cmd, got)


def test_docker_compose_programs_pass_the_zone_for_each_app(bot):
    for app in APPS:
        line = (f"docker compose -p puffpai-{app} -f docker-compose.yml -f .buildbot/compose.puffpai.yml version")
        assert _check(bot, app, line, "command").verdict == "allow"


@pytest.mark.skipif(not WIN, reason="/tmp is the try's scratch folder on Windows only")
def test_without_its_own_scratch_the_tmp_line_is_still_refused(bot):
    """The same compose text, with a scratch argument that is not this try's own: refused, as before."""
    t = bot["tries"]
    state = bot["state"]
    for app, other in (("recipe-box", "invoices"), ("invoices", "birdwatch"), ("birdwatch", "recipe-box")):
        text = RECIPE_COMPOSE if app != "invoices" else INVOICE_COMPOSE
        got = _check(bot, app, text, "compose", scratch=t[other]["scratch"])          # another try's scratch
        assert got.verdict == "deny" and got.line == Z.refused_line(Z.STATE), (app, got)
        for wrong in (str(state / "logs"), t[app]["logs"], str(state / "logs" / "toolsmith"),
                      str(Path(t[app]["logs"]) / "scratch" / "inner")):
            got = _check(bot, app, text, "compose", scratch=wrong)
            assert got.verdict == "deny", (app, wrong, got)


def test_nothing_else_under_logs_is_allowed(bot):
    t = bot["tries"]
    state = bot["state"]
    for app, other in (("recipe-box", "invoices"), ("invoices", "birdwatch"), ("birdwatch", "recipe-box")):
        mine = Path(t[app]["scratch"])
        assert _check(bot, app, str(mine / "out.csv"), "path", write=True).verdict == "allow"
        for denied, write in ((Path(t[other]["scratch"]) / "x.txt", True),        # another try's scratch
                              (Path(t[other]["scratch"]) / "x.txt", False),
                              (state / "logs" / "audit.jsonl", False),
                              (state / "logs" / "audit.jsonl", True),
                              (Path(t[app]["logs"]) / "server-0.log", True),        # its own server log
                              (state / "logs" / "outside.jsonl", False)):
            got = _check(bot, app, str(denied), "path", write=write)
            assert got.verdict == "deny" and got.line == Z.refused_line(Z.STATE), (app, denied, write, got)
        cmd = f"python tally.py --out {t[other]['scratch']}\\x.csv"
        assert _check(bot, app, cmd, "command").verdict == "deny"


def test_a_scratch_that_is_not_a_try_folders_own_carves_nothing(bot):
    """Only a try folder directly under packages gets its scratch; a folder of her code does not."""
    state = bot["state"]
    z = Z.zone_for(str(state / "packages" / ".staging"), scratch=str(state / "logs" / "toolsmith" / ".staging"
                                                                       / "scratch"))
    assert not any("logs" in c for c in z.carve)
    z = Z.zone_for(bot["tries"]["recipe-box"]["folder"], scratch=bot["tries"]["recipe-box"]["scratch"])
    assert Z.canon(bot["tries"]["recipe-box"]["scratch"]) in z.carve


@pytest.mark.skipif(not WIN, reason="a directory junction is a Windows link")
def test_a_scratch_that_is_a_link_is_not_allowed(bot, tmp_path):
    """A junction at the scratch folder's place (to the vault) must not turn the vault into an allowed place."""
    state = bot["state"]
    t = bot["tries"]["birdwatch"]
    scratch = Path(t["scratch"])
    scratch.rmdir()
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(scratch), str(state / "vault")],
                          capture_output=True, text=True)
    if made.returncode != 0:
        pytest.skip("could not make a junction here")
    try:
        assert _check(bot, "birdwatch", RECIPE_COMPOSE, "compose").verdict == "deny"
        got = _check(bot, "birdwatch", str(scratch / "x.txt"), "path", write=True)
        assert got.verdict == "deny" and got.line == Z.refused_line(Z.VAULT)
    finally:
        subprocess.run(["cmd", "/c", "rmdir", str(scratch)], capture_output=True)


def test_the_production_port_answers_allow_for_the_tmp_compose(bot):
    t = bot["tries"]["recipe-box"]
    got = Ports(bot["state"]).zone_check(RECIPE_COMPOSE, kind="compose", folder=t["folder"], cwd=t["folder"],
                                         logs=t["logs"], scratch=t["scratch"])
    assert got.verdict == "allow"


def test_a_refused_compose_file_never_reads_as_docker_missing(tmp_path, state):
    """Trials' reply for a compose file the zone refuses: Docker is not the problem, and why."""
    from toolsmith_fakes import World
    w = World(tmp_path, state)
    try:
        w.ports.zone_refuse = ["/tmp"]
        for app, text in (("recipe-box", RECIPE_COMPOSE), ("invoices", INVOICE_COMPOSE),
                          ("birdwatch", RECIPE_COMPOSE.replace("recipes", "birds"))):
            folder = tmp_path / app
            folder.mkdir()
            (folder / "docker-compose.yml").write_text(text, encoding="utf-8")
            rec = TryRecord(id=f"t-{app}", build_id="b1", job_id="j1", cand={"name": app}, port=1,
                            folder=folder, pkg_id=app)
            argv, said = w.host.trials._compose(rec, ["up", "-d"])
            assert argv == []
            assert "Docker is on this computer and is not the problem" in said
            assert "docker-compose.yml" in said and "Installing Docker will not change this" in said
            assert "outside what Toolsmith may touch (/tmp)" in said              # the zone's own line
            assert "\u2014" not in said
    finally:
        w.host.stop(timeout=10)


# -- RC3: plain text kinds ----------------------------------------------------------------------------------

@pytest.fixture
def db(state):
    return Drawbridge(ChainLog(state / "logs" / "audit.jsonl"))


def _app(root: Path, files: dict) -> Path:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            p.write_bytes(body)
        else:
            p.write_text(body, encoding="utf-8")
    return root


def _man(app):
    return {"schema": "puffpai-package/1", "id": app, "kind": "app", "name": app}


PLAIN = {
    "recipe-box": {"index.js": "console.log('recipes')\n", ".env.example": "PORT=8080\nDB_PATH=./recipes.db\n",
                   "public/site.webmanifest": json.dumps({"name": "Recipes", "icons": []}),
                   "Caddyfile.example": "recipes.local {\n  reverse_proxy 127.0.0.1:8080\n}\n",
                   "config/settings.yml.dist": "units: metric\n"},
    "invoices": {"app.py": "print('invoices')\n", ".env.example": "CURRENCY=EUR\n",
                 "locale/de/LC_MESSAGES/messages.po": 'msgid "Invoice"\nmsgstr "Rechnung"\n',
                 "locale/messages.pot": 'msgid "Total"\nmsgstr ""\n',
                 "backend/Dockerfile.dev": "FROM python:3.12-slim\nCMD [\"python\", \"app.py\"]\n",
                 "backend/Dockerfile.test": "FROM python:3.12-slim\n", ".env.sample": "TAX=19\n"},
    "birdwatch": {"main.ts": "export const birds = 1\n", "frontend/tsconfig.tsbuildinfo": '{"program":{}}',
                  "alembic/script.py.mako": '"""${message}"""\nrevision = ${repr(up_revision)}\n',
                  "manifest.webmanifest": json.dumps({"name": "Birds"}), "Containerfile.prod": "FROM node:22\n",
                  "nginx.conf.template": "server { listen 8080; }\n"},
}


@pytest.mark.parametrize("app", APPS)
def test_named_plain_text_kinds_are_scanned_not_held(db, tmp_path, app):
    root = _app(tmp_path / app, PLAIN[app])
    v = db.check_package(root, _man(app), origin="adopted:https://example.org/x")
    assert v["verdict"] in (ALLOW, WARN), v
    assert "cant_check" not in v["reasons"]


@pytest.mark.parametrize("app,rel,bad", [
    ("recipe-box", ".env.example", "NGROK=1\nrequire('ngrok'); ngrok.connect(1)\n"),
    ("invoices", "locale/de/LC_MESSAGES/messages.po", 'msgid "x"\nmsgstr "ngrok http 8080"\n'),
    ("invoices", "backend/Dockerfile.dev", "FROM node:22\nRUN ngrok http 3000\n"),
    ("birdwatch", "alembic/script.py.mako", "import pyngrok\n"),
    ("birdwatch", "manifest.webmanifest", '{"start": "cloudflared tunnel run birds"}'),
])
def test_a_tunnel_in_a_plain_text_kind_is_found_and_asks(db, tmp_path, app, rel, bad):
    root = _app(tmp_path / app, dict(PLAIN[app], **{rel: bad}))
    v = db.check_package(root, _man(app))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"], v      # D117: her card
    assert next(r for r in v["rows"] if r.get("code") == "cant_check")["tunnel"] == [rel]


@pytest.mark.parametrize("app,rel,body", [
    ("invoices", "locale/de/LC_MESSAGES/messages.po", b'msgid "a"\x00msgstr "b"\n'),             # a NUL byte
    ("invoices", "locale/messages.pot", 'msgid "café"\n'.encode("latin-1")),                # not UTF-8
    ("recipe-box", ".env.example", "PORT=1\n".encode("utf-16")),                                 # UTF-16
    ("invoices", "backend/Dockerfile.dev", b"MZ\x90\x00\x03\x00\x00\x00"),                        # a program
    ("birdwatch", "frontend/tsconfig.tsbuildinfo", b"\x89PNG\r\n\x1a\n\x00\x00"),
])
def test_a_plain_text_kind_that_is_not_plain_text_still_holds(db, tmp_path, app, rel, body):
    root = _app(tmp_path / app, dict(PLAIN[app], **{rel: body}))
    v = db.check_package(root, _man(app))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"], v
    held = next(r for r in v["rows"] if r.get("code") == "cant_check")
    assert held["all"] == [rel]


def test_not_plain_text_is_still_scanned(db, tmp_path):
    """A .po with a NUL byte is held, and a tunnel in it is still found (scanned, never skipped)."""
    rel = "locale/de/LC_MESSAGES/messages.po"
    root = _app(tmp_path / "invoices", dict(PLAIN["invoices"], **{rel: b"\x00 require('ngrok'); ngrok.connect(1)\n"}))
    v = db.check_package(root, _man("invoices"))
    held = next(r for r in v["rows"] if r.get("code") == "cant_check")
    assert v["verdict"] == HOLD and held["tunnel"] == [rel] and held["all"] == [rel]     # both: held, and said (D117)
    assert any("not what" in x for x in held["said"]) and held["said"][0].startswith("1 file has code that can start")


def test_a_plain_text_kind_over_the_read_limit_holds(db, tmp_path, monkeypatch):
    monkeypatch.setattr(dmod, "MAX_TEXT_READ", 64)
    root = _app(tmp_path / "invoices", {"app.py": "1\n", "messages.po": 'msgid "x"\n' * 20})
    v = db.check_package(root, _man("invoices"))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"]


@pytest.mark.parametrize("rel,body", [
    ("tool.zzz", b"whatever\x00\n"),                                  # an unknown kind that is not text (D78)
    ("gradle/wrapper/gradle-wrapper.jar", b"PK\x03\x04\x14\x00\x08\x00"),
    ("notes.zzz.example", b"whatever \xc3\x28\n"),                    # .example of an unknown kind, not UTF-8
    ("setup.exe.sample", "whatever\n"),
    ("photo.png.example", "whatever\n"),
    ("Dockerfile.dev.zip", b"PK\x03\x04"),
    ("translations.mo", b"\xde\x12\x04\x95\x00\x00\x00\x00"),          # compiled translations: not on the list
])
@pytest.mark.parametrize("app", APPS)
def test_unknown_kinds_still_hold(db, tmp_path, app, rel, body):
    root = _app(tmp_path / app, dict(PLAIN[app], **{rel: body}))
    v = db.check_package(root, _man(app))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"], v
    held = next(r for r in v["rows"] if r.get("code") == "cant_check")
    assert held["all"] == [rel]


def _loader_page() -> str:
    import base64
    blob = base64.b64encode(os.urandom(6000)).decode()
    return f"<html><script>var b='{blob}';document.write(atob(b));</script></html>"


def _loader_js() -> str:
    import base64
    blob = base64.b64encode(os.urandom(6000)).decode()
    return f"var b='{blob}';eval(atob(b));\n"


@pytest.mark.parametrize("app,rel,make", [
    ("recipe-box", "public/index.html.template", _loader_page),
    ("invoices", "index.html.example", _loader_page),
    ("birdwatch", "src/app.js.example", _loader_js),
    ("invoices", "web/page.htm.sample", _loader_page),
    ("birdwatch", "lib/boot.mjs.dist", _loader_js),
])
def test_a_copy_of_a_web_file_gets_the_hidden_loader_check(db, tmp_path, app, rel, make):
    """A .example/.template copy is checked as the kind it copies: a hidden loader never passes (D70)."""
    body = make()
    plain_name = rel.rsplit(".", 1)[0]
    same = db.check_package(_app(tmp_path / "a" / app, dict(PLAIN[app], **{plain_name: body})), _man(app))
    assert same["verdict"] == BLOCK and "hidden_loader" in same["reasons"], same
    copy = db.check_package(_app(tmp_path / "b" / app, dict(PLAIN[app], **{rel: body})), _man(app))
    assert copy["verdict"] in (BLOCK, HOLD), copy
    assert copy["verdict"] == BLOCK and "hidden_loader" in copy["reasons"], copy


@pytest.mark.parametrize("app,rel,body", [
    ("recipe-box", "public/index.html.template", "<html><body><h1>${TITLE}</h1></body></html>\n"),
    ("invoices", "index.html.example", "<html><script src='app.js'></script></html>\n"),
    ("birdwatch", "src/app.js.example", "export const apiBase = '/api'\n"),
])
def test_a_clean_copy_of_a_web_file_passes(db, tmp_path, app, rel, body):
    v = db.check_package(_app(tmp_path / app, dict(PLAIN[app], **{rel: body})), _man(app))
    assert v["verdict"] == ALLOW, v


def test_a_copy_is_named_for_the_kind_it_copies():
    assert (dmod._copied_name("app.js.example"), dmod._copied_name("index.html.template")) == ("app.js", "index.html")
    assert dmod._copied_name("site.webmanifest") == "site.webmanifest"
    assert dmod._copied_name("x.zzz.example") == "x.zzz.example"          # not a known copy: unchanged


def test_a_key_id_in_a_refused_host_never_reaches_the_reply(tmp_path):
    """An AWS-shaped key ID hidden in a host name: lower case alone would hide its shape, so the host
    as written is checked too, and it is dropped from the ledger and from any try's reply."""
    from puffpai_core.zone3.ledger import safe_host
    led = Ledger(tmp_path / "outside.jsonl")
    subject = "pkg:toolsmith-build-k1k2k3"
    for host in ("AKIAIOSFODNN7EXAMPLE.recipes.example", "AKIAIOSFODNN7EXAMPLE.invoices.example",
                 "AKIAIOSFODNN7EXAMPLE.birds.example"):
        assert safe_host(host) == ""
        led.record(subject, host=host, port=443, outcome="refused", code="not_on_list")
    led.record(subject, host="Registry.NPMJS.org", port=443, outcome="refused", code="not_on_list")
    rows, _mark = led.refused_since(subject)
    assert rows == [{"host": "registry.npmjs.org", "code": "not_on_list"}]
    assert "akia" not in led.path.read_text(encoding="utf-8").lower()


def test_plain_text_kind_names():
    yes = (".env.example", "caddyfile.example", "config.yml.dist", "app.json.sample", "nginx.conf.template",
           "site.webmanifest", "messages.po", "messages.pot", "tsconfig.tsbuildinfo", "script.py.mako",
           "dockerfile.dev", "containerfile.test", "dockerfile.dev.example", "messages.po.sample")
    no = ("tool.zzz", "x.zzz.example", "a.exe.sample", "gradle-wrapper.jar", ".example.zzz", "dockerfile.",
          "dockerfile.dev.zip", "x.example.example", "x.mo")
    assert all(dmod._plain_text_kind(n) for n in yes), [n for n in yes if not dmod._plain_text_kind(n)]
    assert not any(dmod._plain_text_kind(n) for n in no), [n for n in no if dmod._plain_text_kind(n)]


# -- RC4: the Bridge's refusals, named in the try's reply ---------------------------------------------------

REFUSED = {"recipe-box": ("registry.npmjs.org", "not_on_list"), "invoices": ("pypi.org", "not_on_list"),
           "birdwatch": ("tile.openstreetmap.org", "bridge_closed")}


def test_the_ledger_keeps_refused_hosts_per_subject_names_only(tmp_path):
    led = Ledger(tmp_path / "outside.jsonl")
    subjects = {app: f"pkg:toolsmith-build-{i}a{i}b{i}c" for i, app in enumerate(APPS)}
    marks = {s: led.refused_since(s)[1] for s in subjects.values()}
    for app, (host, code) in REFUSED.items():
        for _ in range(3):
            led.record(subjects[app], host=host, port=443, outcome="refused", code=code, phase="install")
        led.record(subjects[app], host="github.com", port=443, outcome="done", phase="install")
        # A host with a path or a value never becomes a name here (the file's own rule).
        led.record(subjects[app], host=f"{host}/secret/path?token=abc", outcome="refused", code=code)
    for app, (host, code) in REFUSED.items():
        rows, mark = led.refused_since(subjects[app], marks[subjects[app]])
        assert rows == [{"host": host, "code": code}], rows
        assert led.refused_since(subjects[app], mark) == ([], mark)


def test_the_port_reads_the_running_bridges_ledger(tmp_path, state):
    led = Ledger(tmp_path / "outside.jsonl")

    class _Bridge:
        ledger = led

    class _Zone3:
        bridge = _Bridge()

    ports = Ports(state, zone=_Zone3())
    led.record("pkg:toolsmith-build-abc123", host="registry.npmjs.org", outcome="refused", code="not_on_list")
    rows, mark = ports.bridge_refused("pkg:toolsmith-build-abc123", 0)
    assert rows == [{"host": "registry.npmjs.org", "code": "not_on_list"}] and mark == 1
    assert ports.bridge_refused("pkg:toolsmith-build-abc123", mark) == ([], mark)
    assert Ports(state, zone=lambda: None).bridge_refused("pkg:x1", 5) == ([], 5)        # not running: none


def test_the_line_names_hosts_only_and_says_not_to_retry():
    line = bridge_line([{"host": "registry.npmjs.org", "code": "not_on_list"}, {"host": "pypi.org", "code": "not_on_list"},
                        {"host": "tile.openstreetmap.org", "code": "bridge_closed"}])
    assert "registry.npmjs.org, pypi.org (not on the plan the owner said Yes to)" in line
    assert "tile.openstreetmap.org (the Bridge is closed)" in line
    assert "Trying again will not help" in line and "\u2014" not in line and "://" not in line


@pytest.fixture
def world(tmp_path, state):
    from toolsmith_fakes import FIXTURES, World
    w = World(tmp_path, state)
    w.ports.fixture_repo = FIXTURES / "toolsmith" / "hello-page"
    led = Ledger(tmp_path / "outside-test.jsonl")

    class _Bridge:
        ledger = led

    class _Zone3:
        bridge = _Bridge()

    w.ports.bridge_refused = Ports(state, zone=_Zone3()).bridge_refused
    w.ledger = led
    yield w
    w.host.stop(timeout=10)


@pytest.mark.parametrize("app", APPS)
def test_a_try_command_reply_names_the_hosts_the_bridge_refused(world, app):
    from toolsmith_fakes import make_venv
    w = world
    b = w.build_with_yes([{"name": app, "full_name": f"example/{app}"}],
                         job={"name": f"Run {app}", "does": f"Runs {app}", "sample_ask": "What does it show?"})
    link = w.link(b["id"])
    opened = link.try_open(b["jobs"][0]["id"], f"github:example/{app}")
    assert link.try_download(opened["try_id"])["ok"]
    assert link.try_check(opened["try_id"])["ok"]
    rec = w.host.trials.get(b["id"], opened["try_id"])
    assert rec.subject.startswith("pkg:toolsmith-build-")
    # Refused before the try opened, or for another build: never this try's news.
    w.ledger.record("pkg:toolsmith-build-other1", host="evil.example", outcome="refused", code="not_on_list")
    host, code = REFUSED[app]
    for _ in range(40):                                       # a native install asking again and again
        w.ledger.record(rec.subject, host=host, port=443, outcome="refused", code=code, phase="install")
    got = make_venv(link, opened["try_id"])
    assert got["bridge_refused"] == [host]
    assert got["said"].count(host) == 1 and "evil.example" not in got["said"]
    assert "Trying again will not help" in got["said"]
    again = make_venv(link, opened["try_id"])                 # nothing new refused: no line
    assert "bridge_refused" not in again and "The Bridge refused" not in str(again.get("said"))
    w.ledger.record(rec.subject, host="cdn.example.net", port=443, outcome="refused", code="not_on_list")
    log = link.try_log(opened["try_id"])
    assert "cdn.example.net" in log["log"] and host not in log["log"]
