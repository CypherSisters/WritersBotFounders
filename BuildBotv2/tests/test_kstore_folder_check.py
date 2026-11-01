r"""The one check for the folder she picks for books and downloads (D103): ``zone1.knowledge_folder``, and Toolsmith's
zone keeping out of the store.

Every test runs on a fake computer in its own tmp folder (the ``box`` fixture): home, AppData, Windows, Program Files
and the other bots are all fakes, so her real bots are never read. Real junctions (``mklink /J``), removed with
``rmdir`` before the tree goes. A test of a real ``C:\`` place only asks about it: nothing is written there.
"""

from __future__ import annotations

import json
import os
import shutil
import string
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from puffpai_core.zone1 import fetched
from puffpai_core.zone1 import knowledge_folder as kf
from puffpai_core.zone3 import toolsmith_zone as tz

windows = pytest.mark.skipif(os.name != "nt", reason="a Windows rule")


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fake computer, and a bot on it at the default place."""
    c = tmp_path / "c"
    users = c / "Users"
    home = users / "me"
    local = home / "AppData" / "Local"
    roaming = home / "AppData" / "Roaming"
    state = local / "PuffPAI" / "buildbot"
    disk = tmp_path / "disk"
    for p in (home / "Documents", local, roaming, state / "settings", state / "data", disk, users / "Public",
              users / "Default", users / "other", c / "Windows", c / "Program Files", c / "Program Files (x86)",
              c / "ProgramData"):
        p.mkdir(parents=True, exist_ok=True)
    env = {"USERPROFILE": home, "HOME": home, "LOCALAPPDATA": local, "APPDATA": roaming, "SystemDrive": c,
           "SystemRoot": c / "Windows", "windir": c / "Windows", "ProgramFiles": c / "Program Files",
           "ProgramW6432": c / "Program Files", "ProgramFiles(x86)": c / "Program Files (x86)",
           "ProgramData": c / "ProgramData", "PUBLIC": users / "Public", "PUFFPAI_STATE": state}
    for name, value in env.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setattr(kf, "_LITERAL_USERS", (str(users),))
    return SimpleNamespace(c=c, users=users, home=home, local=local, roaming=roaming, state=state, disk=disk,
                           settings=state / "settings", data=state / "data")


@pytest.fixture
def junction():
    """``junction(link, target)``: a real junction, gone again before the tree is deleted."""
    made = []

    def make(link, target):
        Path(link).parent.mkdir(parents=True, exist_ok=True)
        got = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        assert got.returncode == 0, got.stdout + got.stderr
        made.append(link)
        return Path(link)

    yield make
    for link in reversed(made):
        subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


@pytest.fixture
def free_letter():
    for letter in reversed(string.ascii_uppercase):
        if not os.path.exists(f"{letter}:\\"):
            return letter
    pytest.skip("no free drive letter")


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value, str) else value, encoding="utf-8")


def make_bot(parent, name="otherbot", **folders):
    bot = parent / name
    (bot / "settings").mkdir(parents=True)
    if folders:
        write_json(bot / "settings" / "folders.json", folders)
    return bot


def no_drive_check(monkeypatch):
    """Ask about a drive this computer doesn't have: every rule after the drive's own still runs."""
    monkeypatch.setattr(kf, "_drive_line", lambda _p: "")


# -- what is allowed -----------------------------------------------------------------------------------------

def test_plain_folders_are_allowed_and_need_not_exist(box):
    assert kf.problem(box.state, box.disk / "Books") == ""
    assert kf.problem(box.state, box.disk / "not" / "there" / "yet") == ""
    assert not (box.disk / "not").exists(), "the check writes nothing"


def test_her_documents_and_her_own_home_are_allowed(box):
    assert kf.problem(box.state, box.home / "Books") == ""
    assert kf.problem(box.state, box.home / "Documents") == ""
    assert kf.problem(box.state, box.home / "Documents" / "Books") == ""
    assert kf.problem(box.state, box.home) == "", "only because the store is <home>\\knowledge"


@windows
def test_a_drive_root_that_is_not_the_system_drive_is_allowed(box, monkeypatch, free_letter):
    no_drive_check(monkeypatch)
    assert kf.problem(box.state, f"{free_letter}:\\") == ""
    assert kf.problem(box.state, f"{free_letter}:\\Books") == ""


def test_the_data_folder_itself_is_allowed(box):
    assert kf.problem(box.state, box.data) == "", "the default layout, even in AppData and the bot's own folder"
    custom = box.disk / "mydata"
    write_json(box.settings / "folders.json", {"data": str(custom)})
    assert kf.problem(box.state, custom) == ""
    assert kf.problem(box.state, box.data, data=box.data) == ""


# -- how it is written ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", None])
def test_empty_is_refused(box, bad):
    assert kf.problem(box.state, bad) == kf.NO_FOLDER


@pytest.mark.parametrize("bad", ["Books", "..\\Books", ".\\x", "foo/bar"])
def test_not_absolute_is_refused(box, bad):
    assert kf.problem(box.state, bad) == kf.WHOLE_PATH


@windows
@pytest.mark.parametrize("bad", ["C:", "C:Books", "\\Books", "/Books", " C:\\Books", '"C:\\Books"'])
def test_a_path_without_its_drive_is_refused(box, bad):
    assert kf.problem(box.state, bad) == kf.WHOLE_PATH


def test_too_long_is_refused(box):
    assert kf.problem(box.state, str(box.disk) + os.sep + "x" * 1001) == kf.ODD_PATH


def test_long_only_after_expanding_is_refused(box, monkeypatch):
    monkeypatch.setenv("LONGONE", "x" * 1001)
    assert kf.problem(box.state, str(box.disk) + os.sep + "%LONGONE%" if os.name == "nt" else "$LONGONE")


@pytest.mark.parametrize("ch", ["\n", "\r", "\t", "\x00", "\x1b", "\x7f", "\x85"])
def test_control_characters_are_refused(box, ch):
    assert kf.problem(box.state, str(box.disk / f"bo{ch}oks")) == kf.ODD_PATH


@windows
@pytest.mark.parametrize("bad", ["\\\\?\\C:\\Books", "\\\\.\\C:\\Books", "\\??\\C:\\Books", "\\Device\\HarddiskVolume1\\x",
                                 "//?/C:/Books", "//./C:/Books", "/??/C:/Books", "\\\\?\\UNC\\nas\\share\\x",
                                 "\\\\\\\\?\\C:\\Books", "\\\\\\nas\\share\\x", "\\device\\mup\\x"])
def test_device_prefixes_are_refused(box, bad):
    assert kf.problem(box.state, bad) == kf.ODD_PATH


@windows
@pytest.mark.parametrize("tail", ["a.txt:hidden", "a::$DATA", "a:b\\c", "a:", "x\\y:z"])
def test_ntfs_streams_are_refused(box, tail):
    assert kf.problem(box.state, f"{box.disk}\\{tail}") == kf.ODD_PATH


@windows
@pytest.mark.parametrize("name", ["CON", "con", "NUL", "PRN", "AUX", "COM1", "COM9", "LPT1", "LPT9", "nul.txt",
                                  "CON.tar.gz", "com1.zip", "NUL .txt", "COM\u00b9"])
def test_dos_device_names_are_refused_as_any_part(box, name):
    assert kf.problem(box.state, f"{box.disk}\\{name}") == kf.ODD_PATH
    assert kf.problem(box.state, f"{box.disk}\\{name}\\Books") == kf.ODD_PATH


@windows
def test_names_that_only_look_like_devices_are_allowed(box):
    for name in ("CONSOLE", "COM10", "NULL", "console.txt", "AUXILIARY"):
        assert kf.problem(box.state, box.disk / name) == "", name


@windows
@pytest.mark.parametrize("name", ["Books.", "Books ", "Books..", "a. ", "..."])
def test_trailing_dot_or_space_is_refused_on_any_part(box, name):
    assert kf.problem(box.state, f"{box.disk}\\{name}") == kf.ODD_PATH
    assert kf.problem(box.state, f"{box.disk}\\{name}\\x") == kf.ODD_PATH


@windows
@pytest.mark.parametrize("ch", list('<>"|?*'))
def test_wildcards_are_refused(box, ch):
    assert kf.problem(box.state, f"{box.disk}\\a{ch}b") == kf.ODD_PATH


def test_a_variable_with_no_value_is_refused(box):
    assert kf.problem(box.state, f"{box.disk}{os.sep}%NO_SUCH_VAR_HERE%") == kf.WHOLE_PATH
    assert kf.problem(box.state, f"{box.disk}{os.sep}${{NO_SUCH_VAR_HERE}}") == kf.WHOLE_PATH
    assert kf.problem(box.state, f"{box.disk}{os.sep}$NO_SUCH_VAR_HERE") == kf.WHOLE_PATH


def test_a_variable_holding_a_variable_is_not_expanded_twice(box, monkeypatch):
    monkeypatch.setenv("TWICE", "%SystemRoot%")
    assert kf.problem(box.state, f"{box.disk}{os.sep}%TWICE%") == kf.WHOLE_PATH


@windows
def test_a_plain_unc_path_is_read_as_a_path_and_a_device_one_is_not():
    assert kf._written("\\\\nas\\share\\x") == ("", "\\\\nas\\share\\x")
    assert kf._written("//nas/share/x") == ("", "\\\\nas\\share\\x")
    assert kf._written("\\\\nas\\\\share\\\\x") == ("", "\\\\nas\\share\\x")
    assert kf._written("\\\\192.168.1.10\\books") == ("", "\\\\192.168.1.10\\books")
    assert kf._written("\\\\nas") == (kf.WHOLE_PATH, "")
    assert kf._written("\\\\nas\\..\\x") == (kf.WHOLE_PATH, "")


@windows
@pytest.mark.parametrize("bad", ["\\\\localhost\\books", "\\\\LOCALHOST\\books\\x", "\\\\127.0.0.1\\books", "\\\\127.1\\books",
                                 "\\\\2130706433\\books", "\\\\0.0.0.0\\books", "\\\\wsl$\\Ubuntu\\home",
                                 "\\\\wsl.localhost\\Ubuntu\\home", "\\\\localhost@SSL\\books",
                                 "\\\\0--1.ipv6-literal.net\\books"])
def test_a_share_on_this_computer_is_refused_without_touching_it(box, bad):
    assert kf.problem(box.state, bad) == kf.THIS_PC_SHARE


@windows
def test_a_share_named_for_this_computer_is_refused(box, monkeypatch):
    monkeypatch.setenv("COMPUTERNAME", "MYPC")
    assert kf.problem(box.state, "\\\\mypc\\books") == kf.THIS_PC_SHARE
    assert kf.problem(box.state, "\\\\mypc.local\\books") == kf.THIS_PC_SHARE


@windows
@pytest.mark.parametrize("bad", ["\\\\nas\\C$", "\\\\nas\\c$\\Windows", "\\\\nas\\ADMIN$\\x", "\\\\nas\\hidden$\\x"])
def test_a_hidden_admin_share_is_refused(box, bad):
    assert kf.problem(box.state, bad) == kf.ADMIN_SHARE


# -- the drive -----------------------------------------------------------------------------------------------

@windows
def test_a_drive_that_is_not_there_gets_the_exact_line(box, free_letter):
    want = f"Drive {free_letter}: is not there. Plug it in, or pick another folder."
    assert kf.problem(box.state, f"{free_letter}:\\Books") == want
    assert kf.problem(box.state, f"{free_letter.lower()}:\\Books") == want, "the real letter, in capitals"
    assert kf.problem(box.state, f"{free_letter}:\\") == want


@windows
def test_a_network_folder_that_is_not_there_is_refused_plainly(box, monkeypatch):
    monkeypatch.setattr(kf.os.path, "isdir", lambda _p: False)
    assert kf.problem(box.state, "\\\\nas\\share\\x") == kf.NET_GONE


# -- links ---------------------------------------------------------------------------------------------------

@windows
def test_is_link_junction_and_trailing_slash(box, junction):
    real = box.disk / "real"
    real.mkdir()
    link = junction(box.disk / "link", real)
    assert kf.is_link(link) is True
    assert kf.is_link(str(link) + "\\") is True
    assert kf.is_link(real) is False


def test_is_link_symlink(box):
    real = box.disk / "real"
    real.mkdir()
    try:
        os.symlink(real, box.disk / "dirlink", target_is_directory=True)
        os.symlink(real / "f", box.disk / "dangling")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not permitted here")
    assert kf.is_link(box.disk / "dirlink") is True
    assert kf.is_link(box.disk / "dangling") is True
    assert kf.problem(box.state, box.disk / "dirlink") == kf.LINK
    (box.disk / "books").mkdir()
    os.symlink(real, box.disk / "books" / "knowledge", target_is_directory=True)
    assert kf.problem(box.state, box.disk / "books") == kf.LINK


def test_is_link_is_false_for_a_plain_file_a_missing_path_and_junk(box):
    (box.disk / "f.txt").write_text("x", encoding="utf-8")
    assert kf.is_link(box.disk / "f.txt") is False
    assert kf.is_link(box.disk / "gone") is False
    assert kf.is_link("bad\x00name") is False


@windows
@pytest.mark.parametrize("tag, want", [(0xA0000003, True), (0xA000000C, True), (0xA000001D, True),
                                       (0x9000001A, False), (0x9000701A, False), (0x80000017, False), (0, False)])
def test_is_link_reads_the_name_surrogate_bit(monkeypatch, tag, want):
    """Mount point, symlink and WSL link are links. OneDrive's cloud tags, and other reparse points, are not."""
    fake = SimpleNamespace(st_file_attributes=0x400 | 0x10, st_reparse_tag=tag, st_mode=0o40777)
    with monkeypatch.context() as m:
        m.setattr(kf.os, "stat", lambda *_a, **_k: fake)
        assert kf.is_link("C:\\anything") is want


@windows
def test_a_cloud_placeholder_on_the_way_is_not_a_link(box, monkeypatch):
    """OneDrive folders are reparse points too: a folder there must still be allowed."""
    real_stat = os.stat

    def cloud(path, **kw):
        st = real_stat(path, **kw)
        if str(path).lower() == str(box.disk).lower():
            return SimpleNamespace(st_file_attributes=0x400 | 0x10, st_reparse_tag=0x9000001A, st_mode=st.st_mode)
        return st

    with monkeypatch.context() as m:
        m.setattr(kf.os, "stat", cloud)
        assert kf._way_problem(str(box.disk / "Books")) == ""


@windows
def test_the_chosen_folder_being_a_junction_is_refused(box, junction):
    real = box.disk / "real"
    real.mkdir()
    assert kf.problem(box.state, junction(box.disk / "link", real)) == kf.LINK


@windows
def test_knowledge_being_a_junction_is_refused(box, junction):
    real = box.disk / "real"
    real.mkdir()
    (box.disk / "books").mkdir()
    junction(box.disk / "books" / "knowledge", real)
    assert kf.problem(box.state, box.disk / "books") == kf.LINK


@windows
def test_a_junction_higher_up_is_refused(box, junction):
    real = box.disk / "real"
    (real / "deep").mkdir(parents=True)
    link = junction(box.disk / "up", real)
    assert kf.problem(box.state, link / "deep") == kf.LINK
    assert kf.problem(box.state, link / "new" / "folder") == kf.LINK


@windows
def test_a_junction_to_the_vault_or_windows_cant_dodge(box, junction):
    place = box.disk / "keep"
    place.mkdir()
    write_json(box.settings / "vault.json", {"folder": str(place)})
    assert kf.problem(box.state, junction(box.disk / "a", place)) == kf.LINK
    assert kf.problem(box.state, junction(box.disk / "b", box.c / "Windows")) == kf.LINK


@windows
def test_a_dotdot_through_a_junction_is_read_as_windows_reads_it(box, junction):
    real = box.disk / "real"
    real.mkdir()
    link = junction(box.disk / "link", real)
    assert kf.problem(box.state, f"{link}\\..\\Books") == "", "Windows drops the .. before it opens: disk\\Books"


@windows
def test_a_subst_drive_resolves_elsewhere_so_it_is_refused(box, free_letter):
    (box.disk / "x").mkdir()
    got = subprocess.run(["subst", f"{free_letter}:", str(box.disk)], capture_output=True, text=True)
    if got.returncode != 0:
        pytest.skip("subst is not available here")
    try:
        assert kf.problem(box.state, f"{free_letter}:\\x") == kf.LINK
    finally:
        subprocess.run(["subst", f"{free_letter}:", "/d"], capture_output=True)


def test_a_path_through_a_file_is_refused(box):
    (box.disk / "f.txt").write_text("x", encoding="utf-8")
    assert kf.problem(box.state, box.disk / "f.txt") == kf.NOT_FOLDER
    assert kf.problem(box.state, box.disk / "f.txt" / "sub") == kf.NOT_FOLDER
    (box.disk / "books").mkdir()
    (box.disk / "books" / "knowledge").write_text("x", encoding="utf-8")
    assert kf.problem(box.state, box.disk / "books") == kf.NOT_FOLDER


# -- spellings can't dodge -----------------------------------------------------------------------------------

@windows
def test_case_slashes_repeats_dotdot_and_variables_cant_dodge_a_rule(box, monkeypatch):
    windir = box.c / "Windows"
    text = str(windir)
    monkeypatch.setenv("MYWIN", text)
    for spelled in (text.upper(), text.lower(), text.replace("\\", "/"), text.replace("\\", "\\\\"),
                    f"{box.disk}\\..\\c\\Windows", f"{windir}\\sub\\..", "%MYWIN%", "%SystemRoot%\\sub"):
        assert kf.problem(box.state, spelled) == kf.SYSTEM, spelled


@windows
def test_real_short_names_cant_dodge(box):
    if not os.path.isdir("C:\\PROGRA~1"):
        pytest.skip("this drive has no 8.3 names")
    assert kf.problem(box.state, "C:\\PROGRA~1") == kf.SYSTEM
    assert kf.problem(box.state, "C:\\PROGRA~1\\Books") == kf.SYSTEM
    assert kf.problem(box.state, "c:/progra~1/not/there") == kf.SYSTEM


def test_canon_of_a_path_not_there_yet_keeps_the_real_parent(box):
    assert kf._canon_new(box.disk / "a" / "b") == os.path.normcase(os.path.join(os.path.realpath(box.disk), "a", "b"))


@windows
def test_canon_of_a_path_not_there_yet_makes_short_names_long():
    if not os.path.isdir("C:\\PROGRA~1"):
        pytest.skip("this drive has no 8.3 names")
    assert kf._canon_new("C:\\PROGRA~1\\no\\such") == "c:\\program files\\no\\such"


# -- this bot's own places -----------------------------------------------------------------------------------

def test_the_vault_in_its_own_folder_is_refused(box):
    (box.state / "vault").mkdir()
    assert kf.problem(box.state, box.state / "vault") == kf.VAULT
    assert kf.problem(box.state, box.state / "vault" / "x") == kf.VAULT
    assert kf.problem(box.state, box.state / "vault.inbox") == kf.VAULT


def test_the_vault_in_any_place_is_refused_both_ways(box):
    place = box.disk / "keep" / "myvault"
    write_json(box.settings / "vault.json", {"folder": [str(place), {"path": str(box.disk / "two")}]})
    assert kf.problem(box.state, place) == kf.VAULT, "the same folder"
    assert kf.problem(box.state, place / "inside") == kf.VAULT, "inside it"
    assert kf.problem(box.state, box.disk / "two") == kf.VAULT, "the second place"
    write_json(box.settings / "vault.json", {"folder": str(box.disk / "holder" / "knowledge" / "v")})
    assert kf.problem(box.state, box.disk / "holder") == kf.VAULT, "the store holds it"
    write_json(box.settings / "vault.json", {"folder": str(box.disk / "holder" / "v")})
    assert kf.problem(box.state, box.disk / "holder") == "", "beside the store, not in it: the store never walks it"
    write_json(box.settings / "vault.json", {"folder": str(box.disk / "s")})
    assert kf.problem(box.state, box.disk / "s.inbox") == kf.VAULT, "its inbox"


@pytest.mark.parametrize("junk", ["not json{", "[1, 2]", '{"folder": 5}', '{"folder": 0}', '{"folder": false}',
                                  '{"folder": ""}', '{"folder": [3]}',
                                  '{"folder": {"path": "x"}}', '{"folder": "relative\\\\path"}'])
def test_a_vault_setting_that_cant_be_read_refuses_every_folder(box, junk):
    write_json(box.settings / "vault.json", junk)
    assert kf.problem(box.state, box.disk / "Books") == kf.VAULT_UNREADABLE
    assert kf.still_safe(box.state, box.disk / "Books" / "knowledge") == kf.VAULT_UNREADABLE


def test_the_vault_code_and_its_settings_are_not_written(box):
    before = sorted(p.name for p in box.settings.iterdir())
    kf.problem(box.state, box.disk / "Books")
    assert sorted(p.name for p in box.settings.iterdir()) == before


def test_this_bots_own_folder_is_refused(box):
    assert kf.problem(box.state, box.state) == kf.OWN
    assert kf.problem(box.state, box.state / "index") == kf.OWN
    write_json(box.settings / "folders.json", {"data": str(box.disk / "mydata")})
    assert kf.problem(box.state, box.state / "data") == kf.OWN, "once the data folder moved, it is only bot folder"


def test_a_store_holding_this_bots_folder_is_refused(box):
    state = box.disk / "knowledge" / "mybot"
    state.mkdir(parents=True)
    assert kf.problem(state, box.disk) == kf.VAULT, "the vault is in the bot's folder, and its rule comes first"


def test_inside_the_data_folder_or_holding_it_is_refused(box):
    custom = box.disk / "mydata"
    write_json(box.settings / "folders.json", {"data": str(custom)})
    assert kf.problem(box.state, custom / "sub") == kf.DATA
    write_json(box.settings / "folders.json", {"data": str(box.disk / "up" / "knowledge" / "d")})
    assert kf.problem(box.state, box.disk / "up") == kf.DATA
    write_json(box.settings / "folders.json", {"data": str(box.disk / "up" / "knowledge")})
    assert kf.problem(box.state, box.disk / "up") == kf.DATA, "her store would be her data folder itself"


def test_inside_the_default_data_folder_is_refused(box):
    assert kf.problem(box.state, box.data / "sub") != ""
    assert kf.problem(box.state, box.data / "sub", data=box.disk) == kf.OWN


def test_the_data_folder_being_the_bot_folder_gets_no_pass(box):
    write_json(box.settings / "folders.json", {"data": str(box.state)})
    assert kf.problem(box.state, box.state) == kf.OWN


def test_linked_folders_are_refused_by_overlap_with_the_store(box):
    linked = box.disk / "recipes"
    write_json(box.settings / "folders.json", {"linked": [str(linked)]})
    assert kf.problem(box.state, linked) == kf.LINKED
    assert kf.problem(box.state, linked / "sub") == kf.LINKED
    assert kf.problem(box.state, box.disk / "x", linked=[str(box.disk / "x" / "knowledge" / "y")]) == kf.LINKED
    assert kf.problem(box.state, box.disk) == "", "the store sits beside it: it is never walked"


@pytest.mark.parametrize("junk", ["not json{", "[]", '{"linked": 5}', '{"data": 7}', '{"knowledge": 7}'])
def test_this_bots_folder_settings_that_cant_be_read_refuse(box, junk):
    write_json(box.settings / "folders.json", junk)
    assert kf.problem(box.state, box.disk / "Books") == kf.SETTINGS_UNREADABLE


def test_buildbots_own_code_folder_is_refused(box):
    code = Path(kf.__file__).resolve()
    for p in (code.parents[0], code.parents[1], code.parents[2], code.parents[3], code.parents[3] / "docs"):
        assert kf.problem(box.state, p) == kf.CODE, p


# -- other bots ----------------------------------------------------------------------------------------------

def test_a_sibling_bots_own_folder_is_refused(box):
    other = make_bot(box.local / "PuffPAI")
    assert kf.problem(box.state, other) == kf.OTHER_BOT
    assert kf.problem(box.state, other / "sub" / "x") == kf.OTHER_BOT


def test_a_sibling_beside_a_moved_state_folder_is_found(box):
    state = box.disk / "bots" / "mine"
    state.mkdir(parents=True)
    other = make_bot(box.disk / "bots")
    assert kf.problem(state, other) == kf.OTHER_BOT


def test_a_siblings_vault_places_are_refused(box):
    other = make_bot(box.local / "PuffPAI")
    place = box.disk / "theirvault"
    write_json(other / "settings" / "vault.json", {"folder": str(place)})
    assert kf.problem(box.state, place) == kf.OTHER_BOT
    assert kf.problem(box.state, box.disk / "theirvault.inbox") == kf.OTHER_BOT


def test_a_folder_with_only_a_vault_counts_as_a_sibling(box):
    other = box.local / "PuffPAI" / "vaultonly"
    (other / "vault").mkdir(parents=True)
    assert kf.problem(box.state, other) == kf.OTHER_BOT


def test_a_folder_with_only_an_index_counts_as_a_sibling(box):
    other = box.local / "PuffPAI" / "indexonly"
    (other / "index").mkdir(parents=True)
    (other / "index" / "knowledge.db").write_bytes(b"")
    assert kf.problem(box.state, other) == kf.OTHER_BOT


def test_a_siblings_data_folder_and_store_are_refused(box):
    make_bot(box.local / "PuffPAI", data=str(box.disk / "theirdata"), knowledge=str(box.disk / "theirs"))
    assert kf.problem(box.state, box.disk / "theirdata") == kf.OTHER_BOT
    assert kf.problem(box.state, box.disk / "theirs") == kf.OTHER_BOT, "the same store for two bots"
    assert kf.problem(box.state, box.disk / "theirs" / "knowledge" / "x") == kf.OTHER_BOT
    assert kf.problem(box.state, box.disk / "mine") == ""


@pytest.mark.parametrize("name, junk", [("folders.json", "not json{"), ("folders.json", "[]"),
                                        ("folders.json", '{"data": 3}'), ("folders.json", '{"knowledge": 3}'),
                                        ("folders.json", '{"knowledge": "relative"}'),
                                        ("folders.json", '{"data": "relative"}'), ("vault.json", "not json{"),
                                        ("vault.json", '{"folder": 3}')])
def test_a_sibling_whose_settings_cant_be_read_refuses_every_folder(box, name, junk):
    other = make_bot(box.local / "PuffPAI")
    write_json(other / "settings" / name, junk)
    assert kf.problem(box.state, box.disk / "Books") == kf.OTHER_UNREADABLE


def test_a_folder_of_bots_that_cant_be_listed_refuses(box, monkeypatch):
    real = kf._subfolders

    def denied(parent):
        if Path(parent) == box.state.parent:
            raise PermissionError(5, "denied")
        return real(parent)

    monkeypatch.setattr(kf, "_subfolders", denied)
    assert kf.problem(box.state, box.disk / "Books") == kf.OTHER_UNREADABLE


def test_another_bots_default_data_folder_is_refused(box):
    other = box.home / "PuffPAI Data" / "otherbot"
    other.mkdir(parents=True)
    assert kf.problem(box.state, other) == kf.OTHER_BOT
    assert kf.problem(box.state, other / "sub") == kf.OTHER_BOT


def test_this_bots_own_default_data_folder_is_not_another_bot(box):
    mine = box.home / "PuffPAI Data" / box.state.name
    mine.mkdir(parents=True)
    assert kf.problem(box.state, mine) == ""
    assert kf.problem(box.state, box.home / "PuffPAI Data") == "", "the store sits beside each bot's folder"


@pytest.mark.parametrize("build", ["settings", "vault", "index"])
def test_shape_the_chosen_folder_looks_like_a_bot(box, build):
    here = box.disk / "found"
    {"settings": lambda: write_json(here / "settings" / "folders.json", {}),
     "vault": lambda: ((here / "vault").mkdir(parents=True), (here / "settings").mkdir()),
     "index": lambda: write_json(here / "index" / "knowledge.db", "x")}[build]()
    assert kf.problem(box.state, here) == kf.OTHER_BOT


def test_shape_an_ancestor_looks_like_a_bot(box):
    write_json(box.disk / "found" / "settings" / "folders.json", {})
    assert kf.problem(box.state, box.disk / "found" / "deep" / "er") == kf.OTHER_BOT


def test_shape_a_child_looks_like_a_bot(box):
    write_json(box.disk / "holder" / "bot" / "index" / "knowledge.db", "x")
    assert kf.problem(box.state, box.disk / "holder") == kf.OTHER_BOT


def test_a_folder_named_vault_alone_is_hers_not_a_bots(box):
    (box.home / "vault").mkdir()
    assert kf.problem(box.state, box.home / "Books") == ""
    assert kf.problem(box.state, box.disk) == ""
    (box.disk / "x" / "vault").mkdir(parents=True)
    (box.disk / "x" / "settings").mkdir()
    assert kf.problem(box.state, box.disk / "x" / "Books") == kf.OTHER_BOT


@windows
def test_a_mapped_drive_is_its_share_under_the_same_rules(box, monkeypatch):
    """No real mapped drive here: this is how Windows answers for one, ``Z:\\x`` really being ``\\\\nas\\share\\x``."""
    real = kf._as_written
    monkeypatch.setattr(kf, "_as_written", lambda p: real(p))
    assert kf._resolves_elsewhere("\\\\nas\\share\\books", "Z:\\books") == ""
    assert kf._resolves_elsewhere("\\\\nas\\share\\books\\knowledge", "Z:\\books\\knowledge") == ""
    # back at this computer, or at a hidden share: the rules for a typed share answer
    assert kf._resolves_elsewhere("\\\\localhost\\c$\\books", "Z:\\books") == kf.THIS_PC_SHARE
    assert kf._resolves_elsewhere("\\\\nas\\c$\\books", "Z:\\books") == kf.ADMIN_SHARE
    # other names after the share: a link or a subst, not a mapped drive
    assert kf._resolves_elsewhere("\\\\nas\\share\\other", "Z:\\books") == kf.LINK
    assert kf._resolves_elsewhere("c:\\windows\\books", "Z:\\books") == kf.LINK


def test_shape_goes_only_one_level_down(box):
    write_json(box.disk / "holder" / "a" / "bot" / "index" / "knowledge.db", "x")
    assert kf.problem(box.state, box.disk / "holder") == ""


def test_shape_never_counts_this_bot(box, monkeypatch):
    state = box.disk / "mine"
    write_json(state / "settings" / "folders.json", {})
    assert kf.problem(state, box.disk) == "", "her drive holds this bot: the store is beside it"


def test_an_unreadable_child_does_not_stop_a_whole_drive(box, monkeypatch):
    """Every real drive has System Volume Information, which nobody may read."""
    (box.disk / "System Volume Information").mkdir()
    real_stat = os.stat

    def denied(path, *a, **k):
        if "System Volume Information" in str(path) and str(path) != str(box.disk / "System Volume Information"):
            raise PermissionError(5, "denied")
        return real_stat(path, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(os, "stat", denied)
        assert kf._bot_by_shape("x", str(box.disk), near=True) == ""


def test_a_marker_naming_another_bot_is_refused(box):
    write_json(box.disk / "used" / "knowledge" / fetched.MARKER, {"bot": "0123456789abcdef"})
    assert kf.problem(box.state, box.disk / "used") == kf.OTHER_BOT


def test_this_bots_own_marker_is_fine(box):
    store = box.disk / "used" / "knowledge"
    kf.write_marker(box.state, store)
    assert kf.problem(box.state, box.disk / "used") == ""


def test_a_marker_that_cant_be_read_is_refused(box):
    write_json(box.disk / "used" / "knowledge" / fetched.MARKER, "not json{")
    assert kf.problem(box.state, box.disk / "used") == kf.MARKER_JUNK


# -- system folders ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["SystemRoot", "windir", "ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "ProgramData"])
@windows
def test_folders_the_environment_names_are_refused(box, monkeypatch, name):
    place = box.disk / ("sys-" + "".join(c for c in name if c.isalnum()))
    monkeypatch.setenv(name, str(place))
    assert kf.problem(box.state, place) == kf.SYSTEM
    assert kf.problem(box.state, place / "sub") == kf.SYSTEM


@windows
def test_a_store_holding_a_system_folder_is_refused(box, monkeypatch):
    monkeypatch.setenv("ProgramData", str(box.disk / "up" / "knowledge" / "pd"))
    assert kf.problem(box.state, box.disk / "up") == kf.SYSTEM


@windows
@pytest.mark.parametrize("tail", ["$Recycle.Bin", "$RECYCLE.BIN\\S-1-5", "System Volume Information", "Recovery", "recovery\\x"])
def test_folders_every_drive_has_are_refused(box, monkeypatch, free_letter, tail):
    no_drive_check(monkeypatch)
    assert kf.problem(box.state, f"{free_letter}:\\{tail}") == kf.SYSTEM


@windows
def test_those_names_deeper_down_are_fine(box):
    assert kf.problem(box.state, box.disk / "Recovery") == "", "only at the top of a drive"


@windows
def test_real_system_drive_places_are_refused_with_the_environment_gone(box, monkeypatch):
    """The literal defaults stand in for variables that are missing."""
    for name in ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "ProgramData", "SystemDrive",
                 "PUBLIC"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(kf, "_LITERAL_USERS", ("C:\\Users",))
    for path in ("C:\\", "C:\\Windows", "C:\\Windows\\Books", "C:\\Program Files", "C:\\Program Files (x86)\\x",
                 "C:\\ProgramData\\x", "C:\\Users", "C:\\Users\\Public", "C:\\Users\\Public\\Books", "C:\\Users\\Default\\x",
                 "C:\\Users\\someone-else\\Books"):
        assert kf.problem(box.state, path) == kf.SYSTEM, path


@windows
def test_real_drive_folders_are_refused(box):
    for path in ("C:\\$Recycle.Bin\\x", "C:\\Recovery", "C:\\System Volume Information\\x"):
        assert kf.problem(box.state, path) != "", path


@windows
def test_c_users_with_no_home_at_all_refuses_every_profile(box, monkeypatch):
    for name in ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "HOME", "LOCALAPPDATA", "APPDATA", "PUBLIC", "SystemDrive"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(kf, "_LITERAL_USERS", ("C:\\Users",))
    assert kf.problem(box.state, "C:\\Users\\anyone\\Books") == kf.SYSTEM


def test_the_users_folder_and_other_profiles_are_refused(box):
    if os.name != "nt":
        pytest.skip("a Windows rule")
    for path in (box.users, box.users / "Public", box.users / "Public" / "Books", box.users / "Default",
                 box.users / "other", box.users / "other" / "Books", box.users / "new-person" / "x"):
        assert kf.problem(box.state, path) == kf.SYSTEM, path


def test_the_system_drive_root_is_refused_by_the_environment_too(box):
    if os.name != "nt":
        pytest.skip("a Windows rule")
    assert kf.problem(box.state, box.c) == kf.SYSTEM


def test_appdata_is_refused(box):
    if os.name != "nt":
        pytest.skip("a Windows rule")
    for path in (box.home / "AppData", box.home / "AppData" / "LocalLow", box.roaming / "x", box.local / "x"):
        assert kf.problem(box.state, path) == kf.SYSTEM, path


@pytest.mark.parametrize("rel", [".ssh", ".aws", ".gnupg", ".azure", ".claude", ".docker", os.path.join(".config", "gh"),
                                 os.path.join(".config", "google-chrome"), ".mozilla", os.path.join(".config", "autostart")])
def test_keys_browsers_and_startup_are_refused(box, rel):
    assert kf.problem(box.state, box.home / rel) == kf.SECRETS
    assert kf.problem(box.state, box.home / rel / "x") == kf.SECRETS


def test_a_store_holding_keys_is_refused(box, monkeypatch):
    keys = box.disk / "up" / "knowledge" / "keys"
    monkeypatch.setattr(tz, "_sensitive", lambda _home: [(str(keys), tz.KEYS)])
    assert kf.problem(box.state, box.disk / "up") == kf.SECRETS


def test_a_folder_beside_her_keys_is_allowed(box):
    assert kf.problem(box.state, box.home / ".config") == "", "the store sits beside .config\\gh, not over it"


def test_personal_places_in_the_sensitive_list_stay_allowed(box, monkeypatch):
    monkeypatch.setattr(tz, "_sensitive", lambda _home: [(str(box.disk / "p"), tz.PERSONAL)])
    assert kf.problem(box.state, box.disk / "p") == ""


@pytest.mark.parametrize("rel", ["Documents", "Downloads", "Pictures", "Music", "Videos", "Desktop"])
def test_personal_folders_are_allowed(box, rel):
    assert kf.problem(box.state, box.home / rel) == ""


# -- the check before every write ----------------------------------------------------------------------------

def test_still_safe_is_fine_for_a_good_store(box):
    store = box.disk / "Books" / "knowledge"
    assert kf.still_safe(box.state, store) == "", "not made yet"
    kf.write_marker(box.state, store)
    assert kf.still_safe(box.state, store) == ""


@windows
def test_still_safe_sees_a_junction_swapped_in(box, junction):
    store = box.disk / "Books" / "knowledge"
    kf.write_marker(box.state, store)
    (store / fetched.MARKER).unlink()
    store.rmdir()
    target = box.disk / "elsewhere"
    target.mkdir()
    junction(store, target)
    assert kf.still_safe(box.state, store) == kf.LINK


@windows
def test_still_safe_sees_a_junction_higher_up(box, junction):
    target = box.disk / "elsewhere"
    target.mkdir()
    link = junction(box.disk / "up", target)
    assert kf.still_safe(box.state, link / "x" / "knowledge") == kf.LINK


def test_still_safe_sees_another_bots_marker(box):
    store = box.disk / "Books" / "knowledge"
    write_json(store / fetched.MARKER, {"bot": "0123456789abcdef"})
    assert kf.still_safe(box.state, store) == kf.OTHER_BOT


def test_still_safe_sees_a_marker_it_cant_read(box):
    store = box.disk / "Books" / "knowledge"
    write_json(store / fetched.MARKER, "junk")
    assert kf.still_safe(box.state, store) == kf.MARKER_JUNK


def test_still_safe_sees_a_vault_moved_onto_the_store(box):
    store = box.disk / "Books" / "knowledge"
    write_json(box.settings / "vault.json", {"folder": str(store / "v")})
    assert kf.still_safe(box.state, store) == kf.VAULT
    write_json(box.settings / "vault.json", {"folder": str(box.disk / "Books")})
    assert kf.still_safe(box.state, store) == kf.VAULT


@windows
def test_still_safe_says_when_the_drive_is_gone(box, free_letter):
    assert kf.still_safe(box.state, f"{free_letter}:\\knowledge") == (
        f"Drive {free_letter}: is not there. Plug it in, or pick another folder.")


def test_still_safe_after_its_first_time_lists_no_folder_and_reads_no_other_bot(box, monkeypatch):
    store = box.disk / "Books" / "knowledge"
    assert kf.still_safe(box.state, store) == ""       # the first time for this folder: the whole check
    make_bot(box.local / "PuffPAI")
    write_json(box.local / "PuffPAI" / "otherbot" / "settings" / "folders.json", "not json{")
    monkeypatch.setattr(kf.os, "scandir", lambda *_a: pytest.fail("still_safe must not list a folder"))
    monkeypatch.setattr(kf.os, "walk", lambda *_a: pytest.fail("still_safe must not walk a folder"))
    assert kf.still_safe(box.state, store) == ""


def test_a_check_that_blows_up_refuses(box, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("no")

    monkeypatch.setattr(kf, "_vaults", boom)
    assert kf.problem(box.state, box.disk / "Books") == kf.CANT_CHECK
    assert kf.still_safe(box.state, box.disk / "Books" / "knowledge") == kf.CANT_CHECK


def test_a_part_that_cant_be_read_refuses(box, monkeypatch):
    real_stat = os.stat

    def denied(path, *a, **k):
        if str(path) == str(box.disk):
            raise PermissionError(5, "denied")
        return real_stat(path, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(kf.os, "stat", denied)
        assert kf._way_problem(str(box.disk / "Books")) == kf.CANT_CHECK


# -- the marker ----------------------------------------------------------------------------------------------

def test_bot_key_is_sixteen_hex_and_holds_no_name(box):
    key = kf.bot_key(box.state)
    assert len(key) == 16 and all(c in "0123456789abcdef" for c in key)
    assert key == kf.bot_key(str(box.state).upper() if os.name == "nt" else box.state), "one spelling, one key"
    assert key != kf.bot_key(box.disk)


def test_write_marker_writes_only_the_key_atomically(box):
    store = box.disk / "Books" / "knowledge"
    kf.write_marker(box.state, store)
    assert json.loads((store / fetched.MARKER).read_text(encoding="utf-8")) == {"bot": kf.bot_key(box.state)}
    assert [p.name for p in store.iterdir()] == [fetched.MARKER], "no temp file left"
    assert kf.marker_bot(store) == kf.bot_key(box.state)
    assert str(box.state) not in (store / fetched.MARKER).read_text(encoding="utf-8")


def test_write_marker_leaves_its_own_marker_alone(box):
    store = box.disk / "Books" / "knowledge"
    kf.write_marker(box.state, store)
    before = (store / fetched.MARKER).stat().st_mtime_ns
    kf.write_marker(box.state, store)
    assert (store / fetched.MARKER).stat().st_mtime_ns == before


def test_write_marker_never_overwrites_another_bots_or_a_junk_one(box):
    store = box.disk / "Books" / "knowledge"
    write_json(store / fetched.MARKER, {"bot": "0123456789abcdef"})
    with pytest.raises(PermissionError) as err:
        kf.write_marker(box.state, store)
    assert str(err.value.args[0]) == kf.OTHER_BOT
    assert kf.marker_bot(store) == "0123456789abcdef"
    write_json(store / fetched.MARKER, "junk")
    with pytest.raises(OSError):
        kf.write_marker(box.state, store)
    assert (store / fetched.MARKER).read_text(encoding="utf-8") == "junk"


@windows
def test_write_marker_refuses_a_junction(box, junction):
    target = box.disk / "elsewhere"
    target.mkdir()
    link = junction(box.disk / "up", target)
    with pytest.raises(PermissionError):
        kf.write_marker(box.state, link / "knowledge")
    assert not (target / "knowledge").exists()


@pytest.mark.parametrize("content", ["not json{", "[]", "5", '{"bot": 5}', '{"other": "x"}', ""])
def test_marker_bot_is_empty_for_junk(box, content):
    store = box.disk / "s"
    write_json(store / fetched.MARKER, content)
    assert kf.marker_bot(store) == ""


def test_marker_bot_is_empty_when_absent_or_a_link(box):
    store = box.disk / "s"
    store.mkdir()
    assert kf.marker_bot(store) == ""
    try:
        (box.disk / "real.json").write_text('{"bot": "0123456789abcdef"}', encoding="utf-8")
        os.symlink(box.disk / "real.json", store / fetched.MARKER)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not permitted here")
    assert kf.marker_bot(store) == ""


# -- Toolsmith's zone ----------------------------------------------------------------------------------------

def _zone(box, **settings):
    write_json(box.settings / "folders.json", settings)
    return tz.zone_for("", state=str(box.state))


def test_toolsmith_never_reaches_the_store(box):
    chosen = box.disk / "Books"
    store = chosen / "knowledge"
    zone = _zone(box, knowledge=str(chosen))
    assert not zone.broken
    for path, write in ((store, False), (store, True), (store / "gutenberg" / "a.txt", False),
                        (store / "gutenberg" / "a.txt", True)):
        assert zone.classify(str(path), write=write) == (tz.DENY, tz.DATA), path
    why, _outside = tz.command_check(f'type "{store}\\a.txt"' if os.name == "nt" else f'cat "{store}/a.txt"', zone,
                                     str(box.disk))
    assert why == tz.refused_line(tz.DATA)
    why, _outside = tz.command_check(f'copy a.txt "{store}\\b.txt"' if os.name == "nt" else f'cp a.txt "{store}/b.txt"',
                                     zone, str(box.disk))
    assert why == tz.refused_line(tz.DATA)


def test_toolsmith_may_not_adopt_the_store_or_the_folder_that_holds_it(box):
    chosen = box.disk / "Books"
    zone = _zone(box, knowledge=str(chosen))
    assert tz.folder_problem(str(chosen / "knowledge"), zone) == tz.refused_line(tz.DATA)
    assert tz.folder_problem(str(chosen), zone) == tz.refused_line(tz.DATA)
    assert tz.zone_for(str(chosen / "knowledge"), state=str(box.state)).carve == []


def test_a_subfolder_of_the_store_is_treated_as_a_subfolder_of_the_data_folder(box):
    """Today's rule for the data folder (a folder of her code may sit inside it, she picks it): the same here."""
    chosen, custom = box.disk / "Books", box.disk / "mydata"
    zone = _zone(box, knowledge=str(chosen), data=str(custom))
    assert tz.folder_problem(str(chosen / "knowledge" / "x"), zone) == tz.folder_problem(str(custom / "x"), zone) == ""


def test_only_the_store_is_a_data_place_not_her_files_beside_it(box):
    chosen = box.disk / "Books"
    zone = _zone(box, knowledge=str(chosen))
    assert zone.classify(str(chosen / "her-other-files"))[0] == tz.OUTSIDE


def test_no_knowledge_key_changes_nothing(box):
    write_json(box.settings / "folders.json", {"linked": [str(box.disk / "r")]})
    with_file = tz._internal(str(box.state))
    (box.settings / "folders.json").unlink()
    without = tz._internal(str(box.state))
    assert [p for p in with_file if p[1] != tz.LINKED] == without
    assert not any(p[0].endswith("knowledge") for p in with_file)


@pytest.mark.parametrize("junk", [5, "", "   ", [], ["E:\\x"], {"path": "E:\\x"}, "relative\\folder", True])
def test_a_junk_knowledge_setting_makes_the_zone_refuse_everything(box, junk):
    zone = _zone(box, knowledge=junk)
    assert zone.broken
    assert zone.classify(str(box.disk / "anything")) == (tz.DENY, tz.UNREADABLE)
    with pytest.raises(tz.ZoneUnreadable):
        tz._internal(str(box.state))


def test_a_folders_file_that_cant_be_read_still_makes_the_zone_refuse(box):
    write_json(box.settings / "folders.json", "not json{")
    assert tz.zone_for("", state=str(box.state)).broken


def test_parents_may_all_be_missing_down_from_the_drive(box):
    shutil.rmtree(box.disk)
    assert kf.problem(box.state, box.disk / "Books") == ""
    assert kf.still_safe(box.state, box.disk / "Books" / "knowledge") == ""


# -- the fix round: each of these reproduces what a verifier refuted with real junctions ---------------------------

@windows
def test_source_problem_refuses_a_link_a_way_through_one_and_the_vault(box, junction):
    assert kf.source_problem(box.state, box.data / "knowledge") == ""
    vault = box.disk / "keep"
    vault.mkdir()
    (box.data).mkdir(exist_ok=True)
    link = junction(box.data / "knowledge", vault)
    assert kf.source_problem(box.state, link) == kf.SOURCE_LINK
    assert kf.source_problem(box.state, link / "below") == kf.SOURCE_LINK
    write_json(box.settings / "vault.json", {"folder": str(box.disk / "v" / "knowledge")})
    assert kf.source_problem(box.state, box.disk / "v" / "knowledge") == kf.SOURCE_VAULT
    assert kf.source_problem(box.state, box.disk / "v" / "knowledge" / "x") == kf.SOURCE_VAULT
    write_json(box.settings / "vault.json", "not json{")
    assert kf.source_problem(box.state, box.disk / "other") == kf.VAULT_UNREADABLE


@windows
def test_source_problem_is_fine_with_a_drive_that_is_gone(box, free_letter):
    assert kf.source_problem(box.state, f"{free_letter}:\\Books\\knowledge") == ""


@windows
def test_inside_problem_sees_a_link_at_any_part_below_the_store(box, junction):
    store = box.disk / "s"
    (store / "real" / "sub").mkdir(parents=True)
    target = box.disk / "elsewhere"
    target.mkdir()
    junction(store / "real" / "jump", target)
    assert kf.inside_problem(store, "real/sub/a.txt") == ""
    assert kf.inside_problem(store, "not/there/yet") == ""
    assert kf.inside_problem(store, "real/jump") == kf.LINK, "the last part"
    assert kf.inside_problem(store, "real/jump/a/b.txt") == kf.LINK, "a part on the way"
    assert kf.inside_problem(store, "real\\jump\\a.txt") == kf.LINK, "either slash"
    assert kf.inside_problem(store, "../out.txt") == kf.CANT_CHECK


@pytest.mark.parametrize("where", ["other bot's vault", ".ssh", "AppData", "Windows"])
def test_a_setting_edited_by_hand_to_a_bad_place_writes_nothing(box, where):
    """The refutation: still_safe trusted the setting. Each of these passed it, and the save wrote there."""
    other = make_bot(box.local / "PuffPAI")
    theirs = box.disk / "theirvault"
    write_json(other / "settings" / "vault.json", {"folder": str(theirs)})
    bad = {"other bot's vault": theirs, ".ssh": box.home / ".ssh", "AppData": box.roaming / "x",
           "Windows": box.c / "Windows"}[where]
    bad.mkdir(parents=True, exist_ok=True)
    write_json(box.settings / "folders.json", {"knowledge": str(bad)})
    assert kf.still_safe(box.state, bad / "knowledge") != ""
    with pytest.raises(fetched.StoreAway):
        fetched.save(box.state, "gutenberg", "moby", "Plain words.", {"source": "gutenberg"})
    assert not (bad / "knowledge").exists()


def test_the_whole_check_runs_once_per_folder_not_on_every_write(box, monkeypatch):
    real, calls = kf._problem, []
    monkeypatch.setattr(kf, "_problem", lambda *a: (calls.append(a), real(*a))[1])
    one, two = box.disk / "one" / "knowledge", box.disk / "two" / "knowledge"
    for _ in range(3):
        assert kf.still_safe(box.state, one) == ""
    assert len(calls) == 1
    assert kf.still_safe(box.state, two) == "" and len(calls) == 2, "a new folder gets its own"


def test_a_refusal_is_never_remembered_as_a_pass(box):
    bad = box.home / ".ssh" / "knowledge"
    assert kf.still_safe(box.state, bad) == kf.SECRETS
    assert kf.still_safe(box.state, bad) == kf.SECRETS


def test_the_cheap_checks_still_run_after_the_whole_check_passed(box):
    store = box.disk / "Books" / "knowledge"
    assert kf.still_safe(box.state, store) == ""
    write_json(store / fetched.MARKER, {"bot": "0123456789abcdef"})
    assert kf.still_safe(box.state, store) == kf.OTHER_BOT


@pytest.mark.parametrize("junk", ["not json{", "[]", "5", '{"knowledge": 7}', '{"knowledge": "relative"}', '{"knowledge": ""}',
                                  '{"data": 7}', '{"data": "relative"}', '{"linked": 5}'])
def test_junk_folder_settings_give_a_line_and_write_nothing_anywhere(box, junk):
    """The refutation: junk fell to the default place, and a save wrote there."""
    write_json(box.settings / "folders.json", junk)
    with pytest.raises(fetched.StoreAway):
        fetched.save(box.state, "gutenberg", "moby", "Plain words.", {"source": "gutenberg"})
    assert not (box.data / "knowledge").exists()
    with pytest.raises(fetched.StoreAway) as got:
        fetched.folder_for(box.state, "gutenberg")
    assert str(got.value) == fetched.away_line(box.state) == fetched.SETTINGS_JUNK
    assert fetched.root(box.state) == box.data / "knowledge", "reading keeps its default"


def test_no_folder_settings_at_all_is_not_junk(box):
    assert fetched.settings_line(box.state) == ""
    write_json(box.settings / "folders.json", {"data": None, "linked": None})
    assert fetched.settings_line(box.state) == ""


def test_shape_a_bot_one_level_inside_the_store(box):
    write_json(box.disk / "holder" / "knowledge" / "bot" / "index" / "knowledge.db", "x")
    assert kf.problem(box.state, box.disk / "holder") == kf.OTHER_BOT


@windows
def test_a_share_at_this_computers_own_address_is_refused(box, monkeypatch):
    monkeypatch.setattr(kf.socket, "getaddrinfo", lambda *_a, **_k: [(2, 1, 6, "", ("192.0.2.7", 0))])
    assert kf.problem(box.state, "\\\\192.0.2.7\\books") == kf.THIS_PC_SHARE
    assert kf.problem(box.state, "\\\\3221225991\\books") == kf.THIS_PC_SHARE, "the same address as one number"
    assert kf._written("\\\\192.0.2.8\\books") == ("", "\\\\192.0.2.8\\books")


@windows
def test_a_failed_lookup_of_its_own_name_is_just_no_extra_addresses(monkeypatch):
    def fails(*_a, **_k):
        raise kf.socket.gaierror(11001, "no such name")

    monkeypatch.setattr(kf.socket, "getaddrinfo", fails)
    assert kf._written("\\\\192.0.2.7\\books") == ("", "\\\\192.0.2.7\\books")


@windows
def test_a_share_by_name_asks_no_lookup(monkeypatch):
    monkeypatch.setattr(kf.socket, "getaddrinfo", lambda *_a, **_k: pytest.fail("a name is never looked up"))
    assert kf._written("\\\\nas\\books")[0] == ""


@windows
@pytest.mark.parametrize("path", ["C:\\Windows.old", "C:\\Windows.old\\x", "C:\\inetpub", "C:\\inetpub\\wwwroot"])
def test_windows_old_and_inetpub_are_system_folders(box, path):
    assert kf.problem(box.state, path) == kf.SYSTEM


# -- Toolsmith's zone: a move's places, and a store on a share ------------------------------------------------------

def _journal(box, **fields):
    write_json(box.state / "index" / fetched.MOVE, fields)


@pytest.mark.parametrize("phase", ["copying", "stopped", "done"])
def test_toolsmith_never_reaches_either_place_of_a_move(box, phase):
    frm, to = box.disk / "old" / "knowledge", box.disk / "new" / "knowledge"
    _journal(box, phase=phase, from_root=str(frm), to_root=str(to))
    zone = tz.zone_for("", state=str(box.state))
    assert not zone.broken
    for place in (frm, to):
        assert zone.classify(str(place / "a.txt")) == (tz.DENY, tz.DATA), place
        assert zone.classify(str(place / "a.txt"), write=True) == (tz.DENY, tz.DATA), place
    assert zone.classify(str(box.disk / "old" / "beside"))[0] == tz.OUTSIDE


@pytest.mark.parametrize("junk", ["not json{", "[]", '{"to_root": 5}', '{"from_root": ""}', '{"to_root": "relative"}'])
def test_a_move_record_that_cant_be_read_makes_the_zone_refuse_everything(box, junk):
    write_json(box.state / "index" / fetched.MOVE, junk)
    assert tz.zone_for("", state=str(box.state)).broken


def test_no_move_record_adds_no_place(box):
    before = tz._internal(str(box.state))
    _journal(box, phase="done")
    assert tz._internal(str(box.state)) == before


def _share_zone(box, monkeypatch):
    """A store on a share. Text only, so no test here reaches for a host."""
    monkeypatch.setattr(tz, "canon", lambda p: os.path.normcase(os.path.abspath(p)))
    write_json(box.settings / "folders.json", {"knowledge": "\\\\nas\\share\\books"})
    return tz.zone_for("", state=str(box.state))


@windows
@pytest.mark.parametrize("path", ["\\\\nas\\share\\books\\knowledge", "\\\\nas\\share\\books\\knowledge\\gutenberg\\a.txt",
                                  "//NAS/share/books/knowledge/a.txt", "\\\\nas\\share\\books\\x\\..\\knowledge\\a.txt",
                                  "\\\\?\\UNC\\nas\\share\\books\\knowledge\\a.txt", '"\\\\nas\\share\\books\\knowledge\\a.txt"',
                                  "\\\\nas\\share\\books", "\\\\nas\\share"])
def test_a_store_on_a_share_is_denied_for_reading_as_well_as_writing(box, monkeypatch, path):
    zone = _share_zone(box, monkeypatch)
    assert not zone.broken
    assert zone.classify(path) == (tz.DENY, tz.DATA)
    assert zone.classify(path, write=True)[0] == tz.DENY


@windows
@pytest.mark.parametrize("path", ["\\\\nas\\share\\books\\other.txt", "\\\\nas\\other\\knowledge\\a.txt", "\\\\host2\\x\\y"])
def test_every_other_share_read_stays_as_it_was(box, monkeypatch, path):
    zone = _share_zone(box, monkeypatch)
    assert zone.classify(path) == (tz.OUTSIDE, "")
    assert zone.classify(path, write=True) == (tz.DENY, tz.UNCHECKABLE)
