r"""The check for a pinned document (D125): ``folders.file_problem``, and Toolsmith's zone keeping off pinned files.

Every test runs on a fake computer in its own tmp folder (the ``box`` fixture). Real junctions (``mklink /J``), removed
with ``rmdir`` before the tree goes. No test reaches a host: anything about a share either stops before touching it,
or has ``canon`` and ``os.stat`` stubbed.
"""

from __future__ import annotations

import builtins
import io
import json
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from puffpai_core.zone1 import folders
from puffpai_core.zone1 import knowledge_folder as kf
from puffpai_core.zone1.folders import PIN_SUFFIXES, file_problem
from puffpai_core.zone3 import toolsmith_zone as tz

windows = pytest.mark.skipif(os.name != "nt", reason="a Windows rule")


@pytest.fixture
def box(state, tmp_path, monkeypatch):
    """A fake computer, and a bot on it."""
    home = tmp_path / "home"
    disk = tmp_path / "disk"
    for p in (home, state / "settings", state / "data", disk):
        p.mkdir(parents=True, exist_ok=True)
    for name, value in (("USERPROFILE", home), ("HOME", home), ("LOCALAPPDATA", home / "AppData" / "Local"),
                        ("APPDATA", home / "AppData" / "Roaming")):
        monkeypatch.setenv(name, str(value))
    return SimpleNamespace(state=state, home=home, disk=disk, settings=state / "settings", data=state / "data")


@pytest.fixture
def junction():
    """``junction(link, target)``: a real junction, gone again before the tree is deleted."""
    made = []

    def make(link, target):
        got = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        assert got.returncode == 0, got.stdout + got.stderr
        made.append(link)
        return Path(link)

    yield make
    for link in reversed(made):
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


def put(path, text="plain words"):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value, str) else value, encoding="utf-8")


def symlink_or_skip(link, target):
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("this account may not make symlinks (Windows needs Developer Mode or an admin)")
    return Path(link)


# -- what is allowed -----------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["My Novel.txt", "notes.md", "My Novel.docx", "scan.pdf", "LOUD.TXT", "Mixed.DocX",
                                  "two.parts.pdf", "$100 and 50% off.txt"])
def test_a_good_file_of_each_kind(box, name):
    assert file_problem(box.state, put(box.disk / name)) == ""


def test_the_kinds_are_the_four_asked_for():
    assert PIN_SUFFIXES == (".txt", ".md", ".docx", ".pdf")


def test_a_variable_or_home_in_the_path_is_read(box, monkeypatch):
    put(box.disk / "a.txt")
    put(box.home / "b.txt")
    monkeypatch.setenv("MYBOOKS", str(box.disk))
    assert file_problem(box.state, os.path.join("%MYBOOKS%" if os.name == "nt" else "$MYBOOKS", "a.txt")) == ""
    assert file_problem(box.state, os.path.join("~", "b.txt")) == ""


def test_a_file_in_the_default_data_folder_is_allowed(box):
    assert file_problem(box.state, put(box.data / "book.docx")) == ""
    assert file_problem(box.state, put(box.data / "deep" / "er" / "book.pdf")) == ""


def test_a_file_in_the_data_folder_given_is_allowed(box):
    assert file_problem(box.state, put(box.state / "mydata" / "a.txt"), data=box.state / "mydata") == ""
    assert file_problem(box.state, put(box.disk / "mydata" / "a.txt"), data=box.disk / "mydata") == ""


def test_a_file_in_the_knowledge_store_is_allowed(box):
    assert file_problem(box.state, put(box.data / "knowledge" / "gutenberg" / "moby.txt")) == ""
    chosen = box.state / "books"                       # hand-edited, but it is where the store says it is
    write_json(box.settings / "folders.json", {"knowledge": str(chosen)})
    assert file_problem(box.state, put(chosen / "knowledge" / "a.txt")) == ""
    assert file_problem(box.state, put(chosen / "beside.txt")) == folders.PIN_OWN, "only the store, not what is round it"


def test_the_check_reads_no_content_and_changes_nothing(box, monkeypatch):
    novel = put(box.disk / "novel.txt")
    before = novel.stat().st_mtime_ns
    real_open, real_io = builtins.open, io.open

    def guard(real):
        def opened(path, *a, **k):
            assert "novel.txt" not in str(path), "the check opened the file"
            return real(path, *a, **k)
        return opened

    with monkeypatch.context() as m:
        m.setattr(builtins, "open", guard(real_open))
        m.setattr(io, "open", guard(real_io))
        assert file_problem(box.state, novel) == ""
    assert novel.stat().st_mtime_ns == before and novel.read_text(encoding="utf-8") == "plain words"


# -- how it is written ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", None])
def test_blank_is_refused(box, bad):
    assert file_problem(box.state, bad) == "No file was given."


@pytest.mark.parametrize("bad", ["novel.txt", "..\\novel.txt", ".\\novel.txt", "books/novel.txt"])
def test_not_absolute_is_refused(box, bad):
    assert file_problem(box.state, bad) == "Use the file's whole path, like C:\\Users\\you\\Documents\\My Novel.docx."


@windows
@pytest.mark.parametrize("bad", ["C:novel.txt", "\\novel.txt", "/novel.txt", "%NO_SUCH_VAR_HERE%\\a.txt"])
def test_a_path_without_its_drive_is_refused(box, bad):
    assert file_problem(box.state, bad) == folders.PIN_WHOLE_PATH


@pytest.mark.parametrize("pad", [" ", " ", "\n", "\t"])
def test_a_path_with_spaces_round_it_is_checked_as_given(box, pad):
    """A strip here would check ``a.txt`` and leave ``a.txt<no-break space>`` to be opened. The route strips, not this."""
    good = str(put(box.disk / "a.txt"))
    assert file_problem(box.state, good + pad) != ""
    assert file_problem(box.state, pad + good) != ""


def test_a_real_file_with_a_no_break_space_after_its_kind_is_refused(box):
    assert file_problem(box.state, put(box.disk / "a.txt ")) == folders.PIN_KIND


@windows
def test_a_path_in_quotes_is_refused(box):
    """As Explorer's Copy as path gives it. The check takes no quotes off: what it passed is what gets opened."""
    assert file_problem(box.state, f'"{put(box.disk / "a.txt")}"') == folders.PIN_ODD_PATH


@windows
@pytest.mark.parametrize("tail", ["a.txt:secret", "a.txt::$DATA", "a.txt:secret:$DATA", "a.pdf:x.txt", "a:b\\c.txt"])
def test_a_hidden_stream_is_refused(box, tail):
    put(box.disk / "a.txt")
    want = "That path is written in a way PuffPAI can't check. Use the file's normal path."
    assert file_problem(box.state, f"{box.disk}\\{tail}") == want


@windows
def test_a_stream_held_in_a_variable_is_refused(box, monkeypatch):
    put(box.disk / "a.txt")
    monkeypatch.setenv("SNEAK", "a.txt:secret")
    assert file_problem(box.state, f"{box.disk}\\%SNEAK%") == folders.PIN_ODD_PATH


@windows
@pytest.mark.parametrize("prefix", ["\\\\?\\", "\\\\.\\", "//?/", "//./", "/\\?\\", "\\/./", "\\??\\", "/??/",
                                    "\\\\\\\\?\\"])
def test_a_device_path_to_a_good_file_is_refused(box, prefix):
    good = put(box.disk / "a.txt")
    assert file_problem(box.state, prefix + str(good)) == folders.PIN_ODD_PATH


@windows
@pytest.mark.parametrize("bad", ["\\Device\\HarddiskVolume1\\a.txt", "\\device\\mup\\a.txt",
                                 "\\\\?\\Volume{01234567-89ab-cdef-0123-456789abcdef}\\a.txt",
                                 "\\\\?\\UNC\\nas\\share\\a.txt", "\\\\.\\pipe\\a.txt", "\\\\\\nas\\share\\a.txt"])
def test_other_device_forms_are_refused(box, bad):
    assert file_problem(box.state, bad) == folders.PIN_ODD_PATH


@windows
def test_a_device_path_held_in_a_variable_is_refused(box, monkeypatch):
    monkeypatch.setenv("SNEAK", "\\\\?\\" + str(put(box.disk / "a.txt")))
    assert file_problem(box.state, "%SNEAK%") == folders.PIN_ODD_PATH


@windows
def test_a_path_that_leads_to_a_device_form_is_refused(box, monkeypatch):
    monkeypatch.setattr(folders, "canon", lambda _p: "\\\\?\\volume{01234567}\\a.txt")
    assert file_problem(box.state, put(box.disk / "a.txt")) == folders.PIN_ODD_PATH


# -- shares --------------------------------------------------------------------------------------------------

def _stub_share(monkeypatch, *, mode=stat.S_IFREG | 0o666):
    """A share is never reached here: ``canon`` is text only, and the one path on a share stats as *mode*."""
    real_stat = os.stat
    monkeypatch.setattr(folders, "canon", lambda p: os.path.normcase(os.path.abspath(os.path.expandvars(str(p)))))

    def fake(path, *a, **k):
        if str(path).startswith("\\\\nas"):
            return SimpleNamespace(st_mode=mode)
        return real_stat(path, *a, **k)

    monkeypatch.setattr(folders.os, "stat", fake)


@windows
@pytest.mark.parametrize("path", ["\\\\nas\\share\\novel.txt", "//nas/share/novel.docx", "\\\\NAS\\share\\deep\\a.pdf"])
def test_a_network_share_she_typed_is_allowed_as_a_linked_folder_is(box, monkeypatch, path):
    _stub_share(monkeypatch)
    assert file_problem(box.state, path) == ""


@windows
def test_a_share_is_still_held_to_the_kinds(box, monkeypatch):
    _stub_share(monkeypatch)
    assert file_problem(box.state, "\\\\nas\\share\\keys.pem") == folders.PIN_KIND


@windows
@pytest.mark.parametrize("bad", ["\\\\localhost\\c$\\x\\a.txt", "\\\\LOCALHOST\\Docs\\a.txt", "\\\\127.1\\docs\\a.txt",
                                 "\\\\127.0.0.1\\c$\\a.txt", "//localhost/c$/a.txt", "\\\\wsl$\\Ubuntu\\a.txt"])
def test_a_share_on_this_computer_is_refused_without_touching_it(box, monkeypatch, bad):
    """``\\\\localhost\\c$\\<the vault>\\x.txt`` is the vault by a second name: the vault rule can't see it."""
    monkeypatch.setattr(folders, "canon", lambda _p: pytest.fail("a share on this computer must not be looked up"))
    assert file_problem(box.state, bad) == folders.PIN_ODD_PATH


@windows
def test_a_share_named_for_this_computer_is_refused(box, monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "MYPC")
    assert file_problem(box.state, "\\\\mypc\\docs\\a.txt") == folders.PIN_ODD_PATH


@windows
def test_a_mapped_drive_that_leads_back_to_this_computer_is_refused(box, monkeypatch):
    monkeypatch.setattr(folders, "canon", lambda _p: "\\\\localhost\\c$\\bot\\vault\\a.txt")
    assert file_problem(box.state, "Z:\\vault\\a.txt") == folders.PIN_ODD_PATH


# -- the vault -----------------------------------------------------------------------------------------------

LINE_VAULT = "That file is part of your vault, so it can't be pinned."


@pytest.mark.parametrize("place", ["vault", "vault.inbox"])
def test_the_vault_in_the_bot_folder_is_refused(box, place):
    assert file_problem(box.state, put(box.state / place / "secret.txt")) == LINE_VAULT
    assert file_problem(box.state, box.state / place / "not-there.txt") == LINE_VAULT, "there or not, it says the same"


@pytest.mark.parametrize("shape", [lambda p: p, lambda p: [p], lambda p: {"path": p}])
def test_a_vault_folder_named_by_settings_is_refused(box, shape):
    place = box.disk / "keep"
    item = shape(str(place))
    write_json(box.settings / "vault.json", {"folder": [item] if isinstance(item, dict) else item})
    assert file_problem(box.state, put(place / "secret.txt")) == LINE_VAULT
    assert file_problem(box.state, put(place / "deep" / "secret.md")) == LINE_VAULT
    assert file_problem(box.state, put(box.disk / "keep.inbox" / "x.pdf")) == LINE_VAULT
    assert file_problem(box.state, put(box.disk / "keeper" / "x.pdf")) == "", "a name that only starts the same"


def test_a_vault_inside_the_data_folder_is_still_refused(box):
    write_json(box.settings / "vault.json", {"folder": str(box.data / "v")})
    assert file_problem(box.state, put(box.data / "v" / "secret.txt")) == LINE_VAULT
    assert file_problem(box.state, put(box.data / "book.txt")) == ""


@windows
def test_case_dots_and_slashes_cant_dodge_the_vault(box):
    place = box.disk / "keep"
    write_json(box.settings / "vault.json", {"folder": str(place)})
    good = put(place / "secret.txt")
    for spelled in (str(good).upper().replace(".TXT", ".txt"), str(good).replace("\\", "/"),
                    f"{box.disk}\\other\\..\\keep\\secret.txt", f"{box.disk}\\keep.\\secret.txt",
                    f"{box.disk}\\keep \\secret.txt"):
        assert file_problem(box.state, spelled) == LINE_VAULT, spelled


@pytest.mark.parametrize("junk", ["not json{", "[1, 2]", '{"folder": 5}', '{"folder": 0}', '{"folder": false}',
                                  '{"folder": ""}', '{"folder": [3]}', '{"folder": {"path": "x"}}',
                                  '{"folder": "relative\\\\path"}'])
def test_a_vault_setting_that_cant_be_read_refuses_every_file(box, junk):
    write_json(box.settings / "vault.json", junk)
    want = "PuffPAI can't read where your vault is kept, so no file can be pinned right now."
    assert file_problem(box.state, put(box.disk / "a.txt")) == want


@pytest.mark.parametrize("error", [PermissionError(5, "denied"), OSError(5, "denied"), ValueError("no"),
                                   RuntimeError("no")])
def test_a_vault_place_that_cant_be_read_refuses(box, monkeypatch, error):
    def boom(_state):
        raise error

    monkeypatch.setattr(kf, "_vaults", boom)
    assert file_problem(box.state, put(box.disk / "a.txt")) == folders.PIN_VAULT_UNREADABLE


# -- routes through links ------------------------------------------------------------------------------------

@windows
def test_a_junction_into_the_vault_is_refused(box, junction):
    place = box.disk / "keep"
    write_json(box.settings / "vault.json", {"folder": str(place)})
    put(place / "secret.txt")
    link = junction(box.disk / "plain", place)
    assert file_problem(box.state, link / "secret.txt") == LINE_VAULT


@windows
def test_a_junction_into_the_bot_folder_vault_is_refused(box, junction):
    put(box.state / "vault" / "secret.txt")
    assert file_problem(box.state, junction(box.disk / "plain", box.state / "vault") / "secret.txt") == LINE_VAULT


@windows
def test_a_junction_into_the_bot_folder_is_refused(box, junction):
    put(box.settings / "notes.md")
    assert file_problem(box.state, junction(box.disk / "plain", box.settings) / "notes.md") == folders.PIN_OWN


@windows
def test_a_junction_from_the_data_folder_into_the_bot_folder_is_refused(box, junction):
    """In the data folder by its name, in the settings folder by where it leads."""
    put(box.settings / "notes.md")
    assert file_problem(box.state, junction(box.data / "plain", box.settings) / "notes.md") == folders.PIN_OWN


@windows
def test_a_junction_to_a_plain_folder_is_fine(box, junction):
    put(box.disk / "real" / "a.txt")
    assert file_problem(box.state, junction(box.disk / "plain", box.disk / "real") / "a.txt") == ""


def test_a_file_symlink_into_the_vault_is_refused(box):
    secret = put(box.state / "vault" / "secret.txt")
    assert file_problem(box.state, symlink_or_skip(box.disk / "novel.txt", secret)) == LINE_VAULT


def test_a_link_named_txt_that_leads_to_another_kind_is_refused(box):
    keys = put(box.disk / "keys.pem")
    assert file_problem(box.state, symlink_or_skip(box.disk / "novel.txt", keys)) == folders.PIN_KIND


def test_a_link_named_for_another_kind_that_leads_to_a_good_file_is_refused(box):
    novel = put(box.disk / "novel.txt")
    assert file_problem(box.state, symlink_or_skip(box.disk / "keys.pem", novel)) == folders.PIN_KIND


def _leads_to(monkeypatch, link, target):
    """How ``canon`` answers for a file link, for an account that may not make a real one: the tests above run the real
    thing where the platform lets them."""
    real = folders.canon
    monkeypatch.setattr(folders, "canon", lambda p: real(target) if str(p) == str(link) else real(p))


def test_the_kind_is_asked_of_where_a_link_leads(box, monkeypatch):
    novel, keys = put(box.disk / "novel.txt"), put(box.disk / "keys.pem")
    _leads_to(monkeypatch, box.disk / "shortcut.txt", keys)
    assert file_problem(box.state, box.disk / "shortcut.txt") == folders.PIN_KIND
    _leads_to(monkeypatch, box.disk / "shortcut.pem", novel)
    assert file_problem(box.state, box.disk / "shortcut.pem") == folders.PIN_KIND


def test_the_vault_is_asked_of_where_a_link_leads(box, monkeypatch):
    secret = put(box.state / "vault" / "secret.txt")
    _leads_to(monkeypatch, box.disk / "shortcut.txt", secret)
    assert file_problem(box.state, box.disk / "shortcut.txt") == LINE_VAULT


def test_a_link_that_leads_nowhere_is_refused(box):
    assert file_problem(box.state, symlink_or_skip(box.disk / "novel.txt", box.disk / "gone.txt")) == folders.PIN_GONE


# -- the bot's own folder ------------------------------------------------------------------------------------

LINE_OWN = "That file is one of PuffPAI's own, so it can't be pinned."


@pytest.mark.parametrize("rel", ["notes.txt", "settings/notes.md", "logs/toolsmith/x/run.txt", "index/a.pdf",
                                 "packages/x/README.md"])
def test_a_file_of_the_bot_itself_is_refused(box, rel):
    assert file_problem(box.state, put(box.state / rel)) == LINE_OWN


def test_dots_out_of_the_data_folder_are_refused(box):
    put(box.settings / "notes.md")
    assert file_problem(box.state, f"{box.data}{os.sep}..{os.sep}settings{os.sep}notes.md") == LINE_OWN


def test_a_folder_named_like_the_data_folder_is_refused(box):
    assert file_problem(box.state, put(box.state / "data2" / "a.txt")) == LINE_OWN


@windows
def test_case_cant_dodge_the_bot_folder(box):
    put(box.settings / "notes.md")
    assert file_problem(box.state, str(box.settings / "notes.md").upper().replace(".MD", ".md")) == LINE_OWN


def test_a_data_setting_naming_the_bot_folder_gives_no_pass(box):
    notes = put(box.settings / "notes.md")
    assert file_problem(box.state, notes, data=box.state) == LINE_OWN
    assert file_problem(box.state, notes, data=box.state.parent) == LINE_OWN, "one that holds it"
    write_json(box.settings / "folders.json", {"data": str(box.state)})
    assert file_problem(box.state, notes) == LINE_OWN
    write_json(box.settings / "folders.json", {"knowledge": str(box.state.parent)})
    assert file_problem(box.state, notes) == LINE_OWN


@pytest.mark.parametrize("blank", ["", ".", "relative"])
def test_a_data_setting_with_no_whole_path_gives_no_pass(box, monkeypatch, blank):
    """Read as written, it would be relative to wherever this runs."""
    monkeypatch.chdir(box.settings)
    assert file_problem(box.state, put(box.settings / "notes.md"), data=blank) == LINE_OWN


# -- one regular file ----------------------------------------------------------------------------------------

def test_a_file_that_is_not_there_is_refused(box):
    assert file_problem(box.state, box.disk / "gone.txt") == "That file isn't there."
    assert file_problem(box.state, box.disk / "no" / "such" / "gone.pdf") == folders.PIN_GONE


def test_a_path_through_a_file_is_refused(box):
    put(box.disk / "a.txt")
    assert file_problem(box.state, box.disk / "a.txt" / "b.txt") == folders.PIN_GONE


def test_a_folder_is_refused(box):
    assert file_problem(box.state, box.disk) == "That is a folder. Pin one file."
    (box.disk / "looks-like.txt").mkdir()
    assert file_problem(box.state, box.disk / "looks-like.txt") == folders.PIN_FOLDER
    assert file_problem(box.state, box.data) == folders.PIN_FOLDER, "the data folder itself"


@pytest.mark.parametrize("name", ["keys.pem", "run.exe", "noname", "macro.docm", "novel.txt.pem", ".txt", "novel.txtx",
                                  "book.txt.bak", "book.pdf.lnk"])
def test_any_other_kind_is_refused(box, name):
    assert file_problem(box.state, put(box.disk / name)) == "PuffPAI can pin .txt, .md, .docx and .pdf files."


@windows
def test_a_trailing_dot_or_slash_is_refused_by_its_written_name(box):
    good = put(box.disk / "a.txt")
    assert file_problem(box.state, f"{good}.") == folders.PIN_KIND
    assert file_problem(box.state, f"{good}\\") == folders.PIN_KIND


@windows
def test_a_short_name_cant_dodge_the_kind(box):
    import ctypes
    long_file = put(box.disk / "averylongname.txtfile")
    buf = ctypes.create_unicode_buffer(500)
    ctypes.windll.kernel32.GetShortPathNameW(str(long_file), buf, 500)
    if not buf.value or not buf.value.lower().endswith(".txt"):
        pytest.skip("this drive has no 8.3 names")
    assert file_problem(box.state, buf.value) == folders.PIN_KIND


def test_something_that_is_neither_a_file_nor_a_folder_is_refused(box, monkeypatch):
    novel = put(box.disk / "a.txt")
    real_stat = os.stat

    def device(path, *a, **k):
        st = real_stat(path, *a, **k)
        return SimpleNamespace(st_mode=stat.S_IFCHR | 0o666) if str(path).lower().endswith("a.txt") else st

    with monkeypatch.context() as m:
        m.setattr(folders.os, "stat", device)
        assert file_problem(box.state, novel) == folders.PIN_ODD_PATH


# -- a check that can't finish refuses -----------------------------------------------------------------------

@pytest.mark.parametrize("error", [PermissionError(5, "denied"), OSError(1, "bad"), ValueError("no")])
def test_a_stat_that_fails_refuses(box, monkeypatch, error):
    novel = put(box.disk / "a.txt")
    real_stat = os.stat

    def denied(path, *a, **k):
        if str(path).lower().endswith("a.txt"):
            raise error
        return real_stat(path, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(folders.os, "stat", denied)
        assert file_problem(box.state, novel) == "PuffPAI can't check that file, so it can't be pinned."


@pytest.mark.parametrize("error", [OSError(1, "bad"), ValueError("no"), RuntimeError("no")])
def test_a_canon_that_fails_refuses(box, monkeypatch, error):
    def boom(_p):
        raise error

    monkeypatch.setattr(folders, "canon", boom)
    assert file_problem(box.state, put(box.disk / "a.txt")) == folders.PIN_CANT_CHECK


@pytest.mark.parametrize("name", ["a\x00.txt", "a.pem\x00.txt"])
def test_a_nul_in_the_name_refuses(box, name):
    put(box.disk / "a.txt")
    assert file_problem(box.state, f"{box.disk}{os.sep}{name}") != ""


def test_a_store_lookup_that_fails_refuses_inside_the_bot_folder(box, monkeypatch):
    from puffpai_core.zone1 import fetched

    def boom(_state):
        raise OSError(5, "denied")

    monkeypatch.setattr(fetched, "root", boom)
    assert file_problem(box.state, put(box.data / "a.txt")) == folders.PIN_CANT_CHECK


# -- Toolsmith's zone ----------------------------------------------------------------------------------------

def _zone(box, **settings):
    write_json(box.settings / "folders.json", settings)
    return tz.zone_for("", state=str(box.state))


def test_toolsmith_may_not_touch_a_pinned_file(box):
    novel = put(box.disk / "books" / "novel.docx")
    zone = _zone(box, pinned=[str(novel)])
    assert not zone.broken
    assert zone.classify(str(novel)) == (tz.DENY, tz.PINNED)
    assert zone.classify(str(novel), write=True) == (tz.DENY, tz.PINNED)
    assert zone.refusal(str(novel)) == "That path is outside what Toolsmith may touch (a document you pinned)."
    show = f'type "{novel}"' if os.name == "nt" else f'cat "{novel}"'
    assert tz.command_check(show, zone, str(box.disk))[0] == tz.refused_line(tz.PINNED)
    copy = f'copy other.txt "{novel}"' if os.name == "nt" else f'cp other.txt "{novel}"'
    assert tz.command_check(copy, zone, str(box.disk))[0] == tz.refused_line(tz.PINNED)
    out = f'copy "{novel}" out.docx' if os.name == "nt" else f'cp "{novel}" out.docx'
    assert tz.command_check(out, zone, str(box.disk))[0] == tz.refused_line(tz.PINNED)


@windows
def test_another_spelling_of_a_pinned_file_is_refused(box, junction):
    novel = put(box.disk / "books" / "novel.docx")
    zone = _zone(box, pinned=[str(novel)])
    link = junction(box.disk / "plain", box.disk / "books")
    for spelled in (str(novel).upper(), str(novel).replace("\\", "/"), str(link / "novel.docx"),
                    f"{box.disk}\\x\\..\\books\\novel.docx"):
        assert zone.classify(spelled) == (tz.DENY, tz.PINNED), spelled
    assert zone.classify(f"{novel}:hidden") == (tz.DENY, tz.STREAM)
    assert zone.classify("\\\\?\\" + str(novel), write=True)[0] == tz.DENY
    assert zone.classify("\\\\?\\" + str(novel)) == (tz.DENY, tz.PINNED)


def test_a_link_to_a_file_is_refused_at_the_file_and_the_link(box):
    novel = put(box.disk / "novel.docx")
    link = symlink_or_skip(box.disk / "shortcut.docx", novel)
    zone = _zone(box, pinned=[str(link)])
    assert zone.classify(str(novel)) == (tz.DENY, tz.PINNED)
    assert zone.classify(str(link)) == (tz.DENY, tz.PINNED)


def test_a_folder_holding_a_pinned_file_is_treated_as_one_holding_a_data_place(box):
    """Exactly what a data place gets: the holder is refused (``find``, ``-v D:\\books:/x``, a rename), the rest of
    it is not."""
    books = box.disk / "books"
    novel = put(books / "novel.docx")
    beside = put(books / "beside.txt")
    (books / "sub").mkdir()
    pinned, as_data = _zone(box, pinned=[str(novel)]), _zone(box, data=str(books / "dataplace"))
    for how in ({"write": False}, {"write": True}):
        assert pinned.classify(str(books), **how) == (tz.DENY, tz.PINNED)
        assert as_data.classify(str(books), **how) == (tz.DENY, tz.DATA)
        for near in (beside, books / "sub", books / "sub" / "x.txt"):
            assert pinned.classify(str(near), **how) == as_data.classify(str(near), **how) == (tz.OUTSIDE, ""), near
    assert tz.folder_problem(str(books), pinned) == tz.refused_line(tz.PINNED)
    assert tz.folder_problem(str(books), as_data) == tz.refused_line(tz.DATA)
    assert tz.folder_problem(str(books / "sub"), pinned) == tz.folder_problem(str(books / "sub"), as_data) == ""


def test_a_pinned_file_is_not_a_folder_toolsmith_may_work_in(box):
    books = box.disk / "books"
    novel = put(books / "novel.docx")
    _zone(box, pinned=[str(novel)])
    assert tz.PINNED not in tz._INSIDE_OK
    assert tz.folder_problem(str(novel), tz.zone_for("", state=str(box.state))) == tz.refused_line(tz.PINNED)
    for folder in (novel, books):
        assert tz.zone_for(str(folder), state=str(box.state)).carve == [], folder


def test_a_pinned_file_in_the_data_folder_says_it_is_pinned(box):
    novel = put(box.data / "novel.docx")
    assert _zone(box, pinned=[str(novel)]).classify(str(novel)) == (tz.DENY, tz.PINNED)
    assert _zone(box).classify(str(box.data / "other.txt")) == (tz.DENY, tz.DATA)


def test_a_pinned_file_gone_from_the_disk_stays_refused(box):
    gone = box.disk / "books" / "gone.docx"
    zone = _zone(box, pinned=[str(gone)])
    assert not zone.broken and zone.classify(str(gone)) == (tz.DENY, tz.PINNED)


@pytest.mark.parametrize("none", [{}, {"pinned": None}, {"pinned": []}])
def test_no_pins_adds_no_place(box, none):
    write_json(box.settings / "folders.json", none)
    assert not any(cat == tz.PINNED for _p, cat in tz._internal(str(box.state)))
    assert not tz.zone_for("", state=str(box.state)).broken


def test_each_pin_is_a_place_and_the_root_stays_first(box):
    one, two = box.disk / "a.txt", box.disk / "b.pdf"
    write_json(box.settings / "folders.json", {"pinned": [str(one), str(two)]})
    places = tz._internal(str(box.state))
    assert (str(one), tz.PINNED) in places and (str(two), tz.PINNED) in places
    assert places[0] == (str(box.state), tz.STATE)


@pytest.mark.parametrize("junk", [5, True, False, 0, "", "   ", "a whole string, not a list", {"path": "x"}, [5], [None],
                                  [""], ["   "], [["x"]], [{"path": "x"}], ["relative.txt"], ["..\\up.txt"],
                                  ["~\\novel.txt"], ["%USERPROFILE%\\novel.txt"]])
def test_junk_pinned_makes_the_zone_refuse_everything(box, junk):
    zone = _zone(box, pinned=junk)
    assert zone.broken
    assert zone.classify(str(box.disk / "anything")) == (tz.DENY, tz.UNREADABLE)
    with pytest.raises(tz.ZoneUnreadable):
        tz._internal(str(box.state))


def test_one_junk_pin_among_good_ones_still_refuses_everything(box):
    assert _zone(box, pinned=[str(box.disk / "a.txt"), "relative.txt"]).broken


def test_a_path_the_check_allows_is_a_path_the_zone_takes(box):
    """The pin route writes what the check passed: the two must agree on what is a whole path."""
    novel = put(box.disk / "novel.docx")
    assert file_problem(box.state, novel) == ""
    assert not _zone(box, pinned=[str(novel)]).broken
