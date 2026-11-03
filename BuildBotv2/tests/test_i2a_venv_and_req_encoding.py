"""Lane I2a verifier fixes: requirement files are read the way pip reads them (or refused), and the try's own
.venv can't be written by Toolsmith's file tools, whatever the spelling.

1. pip decodes a requirement file with a byte-order mark first, then a coding line in its first two lines,
   then the locale's code page (``pip/_internal/utils/encoding.py`` ``auto_decode``). The checker reads a
   file only when it gets the same text: a UTF-8 mark (stripped), a UTF-8 coding line, or ASCII. Any other
   mark, any other coding line in the first two lines, and anything that is not UTF-8 is refused with
   ``REQ_ENCODING``, at any nesting level.
2. ``.venv`` is judged on the real path in one letter case (trailing dots and spaces, ``::$DATA``, 8.3 short
   names, junctions) for write, copy, move, mkdir and delete, and for copy and move targets. The try's
   Python (and its pip) never runs while its .venv holds a ``pip.ini`` or ``pip.conf``.

Test files are written as raw bytes by the test itself. Process rule: no test stops any process it did not
start itself (the World's own broker ends what it started, by its own job objects).
"""

import codecs
import ctypes
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_d61_allowlist import WIN, _checked, _never_ran, world  # noqa: F401 - shared fixture
from test_i2a_part_c import _decide, _fake_try
from test_i2a_part_e import _app
from toolsmith_fakes import make_venv, script
from puffpai_core.zone3.toolsmith_host import allowlist as A
from puffpai_core.zone3.toolsmith_host.trials import INSIDE_LINE, VENV_LINE

REFUSED = (VENV_LINE, INSIDE_LINE)          # into the .venv, or not a path of the app at all (both: never written)

pip_encoding = pytest.importorskip("pip._internal.utils.encoding")


def _pip_reads(raw: bytes) -> list[str]:
    """The lines pip itself reads from *raw* (its own decoder, then splitlines as its parser does)."""
    return pip_encoding.auto_decode(raw).splitlines()


def _refused_for_encoding(app: Path, args: list[str]) -> None:
    with pytest.raises(A.Refusal) as e:
        A._pip_args(args, app, app)
    assert e.value.said == A.REQ_ENCODING
    assert e.value.reply()["not_run"]["kind"] == A.DENIED


# -- 1. requirement files read as pip reads them ---------------------------------------------------------

# Each one is harmless comments when read as UTF-8, and an option line outside the try when pip reads it
# as the UTF-7 its coding line names ("+AAo-" is a newline in UTF-7).
UTF7_FILES = {
    "editable": b"# -*- coding: utf-7 -*-\n# flask +AAo--e ../outside\n",
    "index": b"# coding=utf-7\n# notes +AAo---index-url https://example.org/simple\n",
    "nested": b"flask\n#coding:utf-7\n# more +AAo--r ../outside/evil.txt\n",
}


@pytest.mark.parametrize("name", sorted(UTF7_FILES))
def test_a_utf7_requirement_file_pip_reads_as_option_lines_is_refused(tmp_path, name):
    raw = UTF7_FILES[name]
    want = {"editable": "-e ../outside", "index": "--index-url https://example.org/simple",
            "nested": "-r ../outside/evil.txt"}[name]
    assert want in _pip_reads(raw)                                   # what pip would really do with it
    assert want not in raw.decode("utf-8").splitlines()             # what a UTF-8 reader was shown
    app = _app(tmp_path)
    (app / "requirements.txt").write_bytes(raw)
    for args in (["install", "-r", "requirements.txt"], ["install", "-c", "requirements.txt", "flask"],
                 ["download", "-d", "w", "-r", "requirements.txt"]):
        _refused_for_encoding(app, args)


@pytest.mark.parametrize("depth", [1, 2])
def test_a_nested_file_in_another_encoding_is_refused_at_any_level(tmp_path, depth):
    app = _app(tmp_path)
    (app / "inner.txt").write_bytes(UTF7_FILES["editable"])
    (app / "middle.txt").write_text("click\n-r inner.txt\n", encoding="utf-8")
    top = "-r inner.txt\n" if depth == 1 else "-c middle.txt\n"
    (app / "requirements.txt").write_text("flask\n" + top, encoding="utf-8")
    _refused_for_encoding(app, ["install", "-r", "requirements.txt"])


@pytest.mark.parametrize("raw", [
    codecs.BOM_UTF16_LE + "flask\n-e ../outside\n".encode("utf-16-le"),
    codecs.BOM_UTF16_BE + "flask\n".encode("utf-16-be"),
    codecs.BOM_UTF32_LE + "flask\n".encode("utf-32-le"),
    codecs.BOM_UTF32_BE + "flask\n".encode("utf-32-be"),
], ids=["utf16le", "utf16be", "utf32le", "utf32be"])
def test_a_file_with_a_bom_other_than_utf8_is_refused(tmp_path, raw):
    app = _app(tmp_path)
    (app / "requirements.txt").write_bytes(raw)
    _refused_for_encoding(app, ["install", "-r", "requirements.txt"])


@pytest.mark.parametrize("raw", [
    b"flask\n# caf\xe9\n",                                  # Latin-1, not UTF-8
    b"flask\n\xff\xfe\n",
    codecs.BOM_UTF8 + b"flask\n\xc3(\n",                    # a UTF-8 mark and then not UTF-8
    b"# -*- coding: utf-8 -*-\nflask\n\x80\n",              # says UTF-8, is not
    b"flask  # caf\xc3\xa9\n",                              # UTF-8 with no mark: pip reads it in the code page
    b"# coding: latin-1\nflask\n",                          # any coding line but UTF-8's
    b"flask\n# vim: set fileencoding=cp1252 :\n",
    b"flask  # coding: utf-7\n",                            # stricter than pip: not only a line starting with #
    b"# coding: no-such-codec\nflask\n",
], ids=["latin1", "ff-fe-later", "bom-then-bad", "says-utf8-is-not", "utf8-no-bom-non-ascii", "latin1-line",
        "vim-line", "coding-after-requirement", "unknown-codec"])
def test_a_file_that_is_not_plain_utf8_is_refused(tmp_path, raw):
    app = _app(tmp_path)
    (app / "requirements.txt").write_bytes(raw)
    _refused_for_encoding(app, ["install", "-r", "requirements.txt"])


@pytest.mark.parametrize("raw", [
    codecs.BOM_UTF8 + b"flask==3.0.3\n",
    codecs.BOM_UTF8 + b"flask==3.0.3  # caf\xc3\xa9\n",
    b"# -*- coding: utf-8 -*-\nflask  # caf\xc3\xa9\n",
    b"flask==3.0.3\nclick\n",
    # Line 3: pip never looks there, so it reads this as ASCII; a UTF-7 line there is only a comment.
    b"flask\n# notes\n# -*- coding: utf-7 -*-\n# more +AAo--e ../outside\n",
], ids=["bom", "bom-non-ascii-comment", "utf8-coding-line", "ascii", "coding-on-line-3"])
def test_a_utf8_file_runs_and_the_checker_reads_what_pip_reads(tmp_path, raw):
    app = _app(tmp_path)
    (app / "requirements.txt").write_bytes(raw)
    assert A._req_text(raw) == pip_encoding.auto_decode(raw)          # the same text, line for line
    assert "-e ../outside" not in _pip_reads(raw)
    A._pip_args(["install", "-r", "requirements.txt"], app, app)
    (app / "top.txt").write_text("-r requirements.txt\n", encoding="utf-8")
    A._pip_args(["install", "-r", "top.txt"], app, app)


def test_an_encoding_refusal_is_one_not_run_record_through_decide(tmp_path):
    place = _fake_try(tmp_path)
    (place.folder / "requirements.txt").write_bytes(UTF7_FILES["editable"])
    with pytest.raises(A.Refusal) as e:
        _decide(place, ["-m", "pip", "install", "-r", "requirements.txt"])
    got = A.complete(e.value, A.Request("python", ["-m", "pip", "install", "-r", "requirements.txt"]),
                     place.folder).reply()
    assert got["said"] == A.REQ_ENCODING and got["not_run"]["kind"] == A.DENIED
    assert got["not_run"]["program"] == "python"


# -- 2. the try's .venv, whatever the spelling ------------------------------------------------------------

def _short_name(path: Path) -> str:
    """The 8.3 short name Windows gave *path*'s last part, or "" when it has none (8.3 names off)."""
    if not WIN:
        return ""
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf))
    short = Path(buf.value).name if n else ""
    return "" if not short or short.casefold() == path.name.casefold() else short


def _settings_in(venv: Path) -> list[str]:
    return [n for n in os.listdir(venv) if n.rstrip(" .").casefold() in ("pip.ini", "pip.conf")]


SPELLINGS = [".VENV", ".Venv", ".venv.", ".venv..", ".venv ", ".venv. ", ".venv"]


def test_toolsmith_cannot_write_into_the_venv_in_any_spelling(world):
    _b, link, try_id, rec = _checked(world)
    # Before the .venv exists: a .VENV folder written now would be the one python -m venv .venv fills.
    for top in SPELLINGS:
        got = link.try_write(try_id, f"{top}/pip.ini", "[global]\n")
        assert not got["ok"] and got["said"] == VENV_LINE, (top, got)
        got = link.try_fs(try_id, "mkdir", path=top)
        assert not got["ok"] and got["said"] == VENV_LINE, (top, got)
    assert not any(n.rstrip(" .").casefold() == ".venv" for n in os.listdir(rec.folder))
    make_venv(link, try_id)
    venv = rec.folder / ".venv"
    tops = SPELLINGS + [s for s in [_short_name(venv)] if s]
    assert link.try_write(try_id, "notes.txt", "x")["ok"] and link.try_fs(try_id, "mkdir", path="box")["ok"]
    for top in tops:
        for rel in (f"{top}/pip.ini", f"{top}/PIP.INI", f"{top}\\pip.conf", f"./{top}/Lib/x.pth", f"{top}/pyvenv.cfg"):
            got = link.try_write(try_id, rel, "[global]\nindex-url = https://example.org/simple\n")
            assert not got["ok"] and got["said"] in REFUSED, (rel, got)
        for op, fields in (("copy", {"from": "notes.txt", "to": f"{top}/pip.ini"}),
                           ("copy", {"from": "notes.txt", "to": top}),
                           ("move", {"from": "notes.txt", "to": f"{top}/pip.ini"}),
                           ("move", {"from": "notes.txt", "to": f"{top}/Scripts"}),
                           ("copy", {"from": "box", "to": f"{top}/Lib"}),
                           ("move", {"from": f"{top}/pyvenv.cfg", "to": "pyvenv.cfg"}),
                           ("copy", {"from": f"{top}/pyvenv.cfg", "to": "pyvenv.cfg"}),
                           ("mkdir", {"path": f"{top}/pip.ini"}),
                           ("mkdir", {"path": f"{top}/Lib/new"}),
                           ("delete", {"path": f"{top}/pyvenv.cfg"}),
                           ("delete", {"path": f"{top}/Scripts"})):
            got = link.try_fs(try_id, op, **fields)
            assert not got["ok"] and got["said"] in REFUSED, (op, fields, got)
    # ::$DATA and other stream names are never a path of the app's folder.
    for rel in (".venv/pip.ini::$DATA", ".VENV::$DATA", ".venv/pip.ini:x"):
        assert not link.try_write(try_id, rel, "[global]\n")["ok"], rel
        assert not link.try_fs(try_id, "copy", **{"from": "notes.txt", "to": rel})["ok"], rel
    assert _settings_in(venv) == [] and (rec.folder / "notes.txt").is_file()
    assert rec.broker.venv is not None
    # Only the whole of it may go, in any spelling, and the Core then forgets it.
    assert link.try_fs(try_id, "delete", path=".VENV." if os.path.exists(rec.folder / ".VENV") else ".venv")["ok"]
    assert not venv.exists() and rec.broker.venv is None


def test_a_junction_to_the_venv_does_not_let_toolsmith_write_into_it(world):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    venv = rec.folder / ".venv"
    junction = rec.folder / "sneaky"
    if WIN:
        made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(venv)], capture_output=True,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if made.returncode != 0:
            pytest.skip("could not make a junction here")
    else:
        os.symlink(venv, junction)                               # Linux and Mac have the plain link instead
    try:
        assert link.try_write(try_id, "notes.txt", "x")["ok"]
        for got in (link.try_write(try_id, "sneaky/pip.ini", "[global]\n"),
                    link.try_fs(try_id, "copy", **{"from": "notes.txt", "to": "sneaky/pip.ini"}),
                    link.try_fs(try_id, "move", **{"from": "notes.txt", "to": "sneaky"}),
                    link.try_fs(try_id, "mkdir", path="sneaky/new"),
                    link.try_fs(try_id, "delete", path="sneaky/pyvenv.cfg")):
            assert not got["ok"], got
        assert _settings_in(venv) == [] and (venv / "pyvenv.cfg").is_file()
        # delete takes the junction itself away, never the .venv it points at
        assert link.try_fs(try_id, "delete", path="sneaky")["ok"]
        assert not os.path.lexists(junction) and (venv / "pyvenv.cfg").is_file() and rec.broker.venv
    finally:
        if os.path.lexists(junction):
            os.rmdir(junction) if WIN else os.unlink(junction)   # the link only, never what it points at


@pytest.mark.parametrize("name", ["pip.ini", "PIP.INI", "Pip.Conf", "pip.conf"])
def test_the_trys_python_is_not_run_with_a_pip_settings_file_in_its_venv(tmp_path, name):
    place = _fake_try(tmp_path)
    (A.venv_bin(place.folder) / ("pip.exe" if WIN else "pip")).write_bytes(b"MZ not a real pip")
    assert _decide(place, ["-m", "pip", "install", "flask"]).kind == "run"
    assert A.decide(A.Request("pip", ["install", "flask"]), place).kind == "run"
    (place.folder / ".venv" / name).write_text("[global]\nindex-url = https://example.org/simple\n")
    for req in [A.Request("python", args) for args in (["-m", "pip", "install", "flask"], ["myapp.py"],
                                                       ["-m", "myapp"])] + [A.Request("pip", ["install", "flask"])]:
        with pytest.raises(A.Refusal) as e:
            A.decide(req, place)
        assert e.value.said == A.VENV_PIP_SETTINGS.format(name=name)
        assert e.value.reply()["not_run"]["kind"] == A.DENIED


def test_a_folder_named_pip_ini_counts_too_and_no_venv_holds_none(tmp_path):
    place = _fake_try(tmp_path)
    (place.folder / ".venv" / "Pip.Ini").mkdir()
    assert A.venv_pip_settings(place.folder) == "Pip.Ini"
    assert A.venv_pip_settings(tmp_path / "no-such-app") == ""


def test_a_pip_ini_a_try_script_writes_stops_the_next_run_and_start(world):
    _b, link, try_id, rec = _checked(world)
    make_venv(link, try_id)
    plan = rec.broker.plan({"program": "python", "args": ["-V"]})       # decided before the file appears
    assert not isinstance(plan, dict), plan
    code = "import os, sys\nopen(os.path.join(sys.prefix, 'PIP.INI'), 'w').write('[global]\\n')\n"
    assert script(link, try_id, "mk.py", code)["ok"]                    # a try script may write it (runs as her)
    assert _settings_in(rec.folder / ".venv") == ["PIP.INI"]
    said = A.VENV_PIP_SETTINGS.format(name="PIP.INI")
    for got in (link.try_run(try_id, {"program": "python", "args": ["-V"]}),
                link.try_run(try_id, {"program": "python", "args": ["-m", "pip", "--version"]}),
                link.try_start(try_id, {"program": "python", "args": ["mk.py"]}),
                rec.broker.run(planned=plan),                            # the broker checks again at the start
                rec.broker.start(None, None, rec.scratch / "x.log", planned=plan)):
        _never_ran(got)
        assert got["said"] == said and got["not_run"]["kind"] == A.DENIED, got
    assert not rec.broker.alive()
    # The way back: delete the whole .venv and make it again.
    assert link.try_fs(try_id, "delete", path=".venv")["ok"]
    make_venv(link, try_id)
    assert link.try_run(try_id, {"program": "python", "args": ["-V"]})["ok"]
