"""Lane I20b (D78): Drawbridge reads any small plain text file as text, not only the kinds its lists name.

Before D78 a file of a kind no list named (``recipes.cook``, ``rates.tbl``) set the whole app aside behind
"Drawbridge couldn't check some of its files". Now such a file is read as text when it is under the read
limit, strict UTF-8 and plain text (no NUL or other control character but tab, line feed, form feed and
carriage return), and it then gets every check code gets: the tunnel check, the hidden-loader check whatever
its name, and the named-hosts note. Anything else is held, unscanned, exactly as before: a kind that is
never text (a program, library, archive, key file, or a kind Windows runs by itself, at any dotted part of the
name), a file over the read limit, UTF-16, a NUL byte, invalid UTF-8. A file with no extension was already
read as text; it is now held too when it is not plain text (a program with no ``.exe``), and still scanned.

Three unrelated apps (a recipe box, an invoices tool, a birdwatching app) and a no-extension notes file.
No network, no Docker, no real keys; every folder is the test's own.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from puffpai_core.zone3 import drawbridge as dmod
from puffpai_core.zone3.chain import ChainLog
from puffpai_core.zone3.drawbridge import ALLOW, BLOCK, HOLD, WARN, Drawbridge

#: app -> (its code, the unknown-kind file, its clean text)
APPS = {
    "recipe-box": ("index.js", "recipes.cook",
                   ">> servings: 4\nAdd @flour{200%g} and @eggs{2}. Bake for ~{25%minutes}.\n"),
    "invoices": ("app.py", "rates.tbl", "code\trate\tcurrency\nSTD\t19\tEUR\nRED\t7\tEUR\n"),
    "birdwatch": ("main.ts", "species.list", "Erithacus rubecula, robin\nTurdus merula, blackbird\n"),
    "notes-app": ("server.js", "LICENSE-NOTES", "Parts of this app are shared under the MIT licence.\n"),
}
NAMES = sorted(APPS)


@pytest.fixture
def db(state):
    return Drawbridge(ChainLog(state / "logs" / "audit.jsonl"))


def _app(root: Path, app: str, body: str | bytes | None = None, rel: str | None = None) -> tuple[Path, str]:
    code, unknown, clean = APPS[app]
    rel = rel or unknown
    root.mkdir(parents=True, exist_ok=True)
    (root / code).write_text("console.log('ok')\n" if code.endswith(("js", "ts")) else "print('ok')\n",
                             encoding="utf-8")
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    body = clean if body is None else body
    if isinstance(body, bytes):
        p.write_bytes(body)
    else:
        p.write_text(body, encoding="utf-8", newline="")
    return root, rel


def _man(app):
    return {"schema": "puffpai-package/1", "id": app, "kind": "app", "name": app}


def _held(v) -> list:
    return next((r["all"] for r in v["rows"] if r.get("code") == "cant_check"), [])


def _tunnels(v) -> list:
    """The files her card names for a tunnel (D117). The verdict is a hold: every check still found it."""
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"] and v["tunnels"] >= 1, v
    return next(r["tunnel"] for r in v["rows"] if r.get("code") == "cant_check")


def _loader() -> str:
    blob = base64.b64encode(os.urandom(6000)).decode()
    return f"var b='{blob}';eval(atob(b));\n"


# -- clean files pass ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("app", NAMES)
def test_a_clean_unknown_kind_is_checked_as_text_and_passes(db, tmp_path, app):
    root, _rel = _app(tmp_path / app, app)
    v = db.check_package(root, _man(app), origin="adopted:https://example.org/x")
    assert v["verdict"] == ALLOW and v["reasons"] == [], v


@pytest.mark.parametrize("app", NAMES)
def test_a_utf8_bom_and_zero_width_characters_are_still_text(db, tmp_path, app):
    body = "﻿" + APPS[app][2] + "tag​name‍\n"
    root, _rel = _app(tmp_path / app, app, body.encode("utf-8"))
    assert db.check_package(root, _man(app))["verdict"] == ALLOW


@pytest.mark.parametrize("app", NAMES)
def test_an_unknown_kind_at_exactly_the_read_limit_is_read(db, tmp_path, app, monkeypatch):
    monkeypatch.setattr(dmod, "MAX_TEXT_READ", 256)
    root, rel = _app(tmp_path / app, app, b"a" * 256)
    assert db.check_package(root, _man(app))["verdict"] == ALLOW
    (root / rel).write_bytes(b"a" * 257)
    v = db.check_package(root, _man(app))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"] and _held(v) == [rel], v


# -- every check code gets ----------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "require('ngrok'); ngrok.connect(1)\n",
    "import pyngrok\n",
    "#!/bin/sh\nngrok http 8080\n",
    "cloudflared tunnel run food\n",
    "ssh -R 80:localhost:8080 serveo.net\n",
    "﻿require('ngrok')​; ngrok.connect(1)\n",             # a BOM and a zero-width space around it
])
@pytest.mark.parametrize("app", NAMES)
def test_a_tunnel_in_an_unknown_kind_asks(db, tmp_path, app, bad):
    root, rel = _app(tmp_path / app, app, APPS[app][2] + bad)
    assert _tunnels(db.check_package(root, _man(app))) == [rel]


@pytest.mark.parametrize("app", NAMES)
def test_a_hidden_loader_in_an_unknown_kind_blocks_whatever_its_name(db, tmp_path, app):
    """A file with no extension gets it too: a script can run with no extension."""
    root, _rel = _app(tmp_path / app, app, _loader())
    v = db.check_package(root, _man(app))
    assert v["verdict"] == BLOCK and "hidden_loader" in v["reasons"], v


@pytest.mark.parametrize("app", ["recipe-box", "invoices", "birdwatch"])
def test_an_unknown_kind_gets_the_named_hosts_note_like_code(db, tmp_path, app):
    """A file with no extension is a text kind it always read (LICENSE, Makefile): no hosts note, as before."""
    root, _rel = _app(tmp_path / app, app, APPS[app][2] + "fetch('https://collector.recipes-tracker.test/x')\n")
    v = db.check_package(root, _man(app))
    assert v["verdict"] == WARN and v["reasons"] == ["hosts_not_listed"], v


@pytest.mark.parametrize("app", NAMES)
def test_a_tunnel_on_one_very_long_line_is_still_found(db, tmp_path, app):
    line = ("ab1Cd2" * 400_000) + " require('ngrok'); ngrok.connect(1)"      # about 2.4 MB, no line break
    root, rel = _app(tmp_path / app, app, line)
    assert _tunnels(db.check_package(root, _man(app))) == [rel]


@pytest.mark.parametrize("app", NAMES)
def test_a_key_shaped_value_gets_what_a_known_text_kind_gets(db, tmp_path, app):
    """Drawbridge's install check has no key-shaped value rule for any kind (keys are Zone 1's job). An
    unknown kind gets exactly what a ``.json`` file with the same words gets, never less."""
    body = APPS[app][2] + "api_key = sk-" + "Ab3" * 16 + "\nAKIA" + "IOSFODNN7" + "EXAMPLE\n"
    unknown_root, _rel = _app(tmp_path / "u" / app, app, body)
    known_root, _rel = _app(tmp_path / "k" / app, app, body, rel="settings.json")
    u = db.check_package(unknown_root, _man(app))
    k = db.check_package(known_root, _man(app))
    assert (u["verdict"], u["reasons"]) == (k["verdict"], k["reasons"])


@pytest.mark.parametrize("app", NAMES)
def test_an_executable_bit_changes_nothing(db, tmp_path, app):
    root, rel = _app(tmp_path / app, app, "#!/bin/sh\nngrok http 8080\n")
    os.chmod(root / rel, 0o755)
    assert _tunnels(db.check_package(root, _man(app))) == [rel]


# -- what is not plain text is held, as before --------------------------------------------------------------

@pytest.mark.parametrize("body", [
    b"servings: 4\x00\n",                                             # a NUL byte
    "café\n".encode("latin-1"),                                  # not UTF-8
    "rate\t19\n".encode("utf-16"),                                    # UTF-16 with its BOM
    "rate\t19\n".encode("utf-16-le"),                                 # UTF-16 without one
    b"\x7fELF\x02\x01\x01\x00\x00\x00",                               # a program
    b"MZ\x90\x00\x03\x00\x00\x00",
    b"plain words \x1b[31mred\x1b[0m\n",                              # an escape character
    b"plain words\x0bmore\n",                                         # a vertical tab
    b"plain words\x7f\n",                                             # DEL
    "plain words\u0085more\n".encode("utf-8"),                        # a C1 control, valid UTF-8
    b"\xef\xbb\xbf\xff\xfe",                                          # a BOM, then not UTF-8
])
@pytest.mark.parametrize("app", NAMES)
def test_an_unknown_kind_that_is_not_plain_text_holds(db, tmp_path, app, body):
    root, rel = _app(tmp_path / app, app, body)
    v = db.check_package(root, _man(app))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"] and _held(v) == [rel], v


@pytest.mark.parametrize("app", ["recipe-box", "invoices", "birdwatch"])
def test_an_unknown_kind_that_is_not_text_is_held_and_read_only_for_a_tunnel(db, tmp_path, app):
    """As before D78: a binary of an unknown kind is held for her Yes, never blocked for words in its bytes. It can't
    hide a tunnel from her card, though (D117)."""
    root, rel = _app(tmp_path / app, app, b"\x00\x01 require('ngrok'); ngrok.connect(1)\n")
    v = db.check_package(root, _man(app))
    assert _tunnels(v) == [rel] and _held(v) == [rel], v


@pytest.mark.parametrize("app", NAMES)
def test_an_unknown_kind_over_the_read_limit_holds(db, tmp_path, app, monkeypatch):
    monkeypatch.setattr(dmod, "MAX_TEXT_READ", 64)
    root, rel = _app(tmp_path / app, app, "plain words\n" * 20)
    v = db.check_package(root, _man(app))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"] and _held(v) == [rel], v


def test_a_no_extension_program_holds_and_is_still_scanned(db, tmp_path):
    """A file with no extension was always read as text. One that is not plain text is held now (D78),
    and a tunnel in it is still found (scanned, never skipped)."""
    root, rel = _app(tmp_path / "notes-app", "notes-app", b"\x7fELF\x02\x01\x01\x00 plain")
    v = db.check_package(root, _man("notes-app"))
    assert v["verdict"] == HOLD and _held(v) == [rel], v
    (root / rel).write_bytes(b"\x7fELF\x02\x01\x01\x00 ngrok http 8080\n")
    assert _tunnels(db.check_package(root, _man("notes-app"))) == [rel]
    (root / rel).write_text("#!/bin/sh\nngrok http 8080\n", encoding="utf-8")
    assert _tunnels(db.check_package(root, _man("notes-app"))) == [rel]


NEVER_TEXT = ("setup.exe", "lib/helper.dll", "lib/libz.so.1", "lib/libz.so", "SETUP.EXE.SAMPLE",
              "dist/bundle.zip", "dist/site.tar.gz", "app.jar", "certs/server.pem", "certs/server.key",
              "run.vbs", "open.url", "settings.reg", "page.hta", "photo.png.example", "model.gguf",
              "data.sqlite", "messages.mo", "tool.wasm", "addon.node", "backup.7z",
              # more kinds Windows runs or opens by itself
              "x.sct", "x.ws", "x.psd1", "x.ps1xml", "x.psc1", "x.shb", "x.shs", "x.ins", "x.isp", "x.mof",
              "x.theme", "x.search-ms", "x.website", "x.vb", "x.jnlp", "x.rdp",
              # 8.3 short names: X~1.VBS, EVIL~1.EXE, RUN~1.BAT
              "x.vbsx", "evil.exeabc", "run.batch", "setup.msiold")


@pytest.mark.parametrize("rel", NEVER_TEXT)
@pytest.mark.parametrize("app", ["recipe-box", "invoices", "birdwatch"])
def test_a_known_binary_or_archive_kind_with_text_inside_still_holds(db, tmp_path, app, rel):
    root, rel = _app(tmp_path / app, app, "whatever\n", rel=rel)
    v = db.check_package(root, _man(app))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"], v
    assert _held(v) == [rel], v


@pytest.mark.parametrize("app", ["recipe-box", "invoices", "birdwatch"])
def test_a_picture_that_is_really_text_still_holds(db, tmp_path, app):
    root, rel = _app(tmp_path / app, app, "require('ngrok')\n", rel="fake.png")
    v = db.check_package(root, _man(app))
    # Held as not the picture it says, and (D95) still read as the plain text it is, so its tunnel is caught, and
    # her card says both (D117).
    assert _tunnels(v) == [rel] and _held(v) == [rel], v
    (root / rel).write_text("just some words\n", encoding="utf-8")
    v = db.check_package(root, _man(app))
    assert v["verdict"] == HOLD and _held(v) == [rel], v


def test_never_text_names():
    yes = ("setup.exe", "libz.so.1", "tool.exe.", "tool.exe ", "a.exe.sample", "photo.png.example",
           "x.tar.gz", ".exe", "key.pem", "run.vbs", "go.url", "x.vbsx", "evil.exeabc", "run.batch",
           "x.v bsx", "x.vbsx.", "x.ps1xml", "x.website")
    no = ("recipes.cook", "rates.tbl", "species.list", "license-notes", ".gitignore", "makefile.am",
          "public_suffix_list.dat", "x.example", "notes.zzz", "v1.2.zzz", "x.vbsx.zzz", "x.webx", "x.mdx",
          "x.adoc")
    assert all(dmod._claims_never_text(n) for n in yes), [n for n in yes if not dmod._claims_never_text(n)]
    assert not any(dmod._claims_never_text(n) for n in no), [n for n in no if dmod._claims_never_text(n)]


def test_the_plain_text_rule():
    assert dmod._looks_text(b"") and dmod._looks_text(b"a\tb\r\n\x0cc") and dmod._looks_text("﻿café".encode())
    for bad in (b"a\x00", b"a\x1b", b"a\x0b", b"a\x7f", "a\u0085".encode(), b"\xff", "a".encode("utf-16")):
        assert not dmod._looks_text(bad), bad


def test_the_short_name_rule_never_touches_a_known_text_kind():
    known = dmod._TEXT_EXTS | dmod._PLAIN_TEXT_EXTS | dmod._PLAIN_COPY_EXTS
    assert not [e for e in known if len(e) > 4 and "." + e[1:4] in dmod._SHORT_NAME_RUNS]
    assert not dmod._NEVER_TEXT_EXTS & (dmod._TEXT_EXTS | dmod._PLAIN_TEXT_EXTS)


# -- a README is a doc only when it is a doc kind -----------------------------------------------------------

@pytest.mark.parametrize("app", ["recipe-box", "invoices", "birdwatch"])
def test_readme_command_is_held_by_its_short_name(db, tmp_path, app):
    """``README.command`` is ``README~1.COM`` as a short name, a kind Windows runs: held, and read only for a tunnel
    (D117)."""
    root, rel = _app(tmp_path / app, app, "#!/bin/sh\nngrok http 8080\n", rel="README.command")
    v = db.check_package(root, _man(app))
    assert _tunnels(v) == [rel] and _held(v) == [rel], v


@pytest.mark.parametrize("rel", ["readme.zzz", "readme.sh", "readme_run.ps1", "README.js", "README.fish",
                                 "docs/readme.yml"])
@pytest.mark.parametrize("app", ["recipe-box", "invoices", "birdwatch"])
def test_a_readme_of_a_kind_that_runs_gets_the_tunnel_check(db, tmp_path, app, rel):
    root, rel = _app(tmp_path / app, app, "#!/bin/sh\nngrok http 8080\n", rel=rel)
    assert _tunnels(db.check_package(root, _man(app))) == [rel]


@pytest.mark.parametrize("rel", ["README.md", "README", "readme.txt", "README.rst", "README.adoc",
                                 "docs/README.markdown"])
@pytest.mark.parametrize("app", ["recipe-box", "invoices", "birdwatch"])
def test_a_readme_doc_that_explains_a_tunnel_still_passes(db, tmp_path, app, rel):
    body = "# Share it\n\nTo share it, run `npm install ngrok` and then `ngrok http 8080`.\n"
    root, rel = _app(tmp_path / app, app, body, rel=rel)
    v = db.check_package(root, _man(app))
    assert v["verdict"] == ALLOW, v
