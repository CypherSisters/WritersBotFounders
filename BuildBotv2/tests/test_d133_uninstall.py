"""D133: ``puffpai uninstall`` removes this one bot and nothing else. Every path here is a scratch folder."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from puffpai_core import cli, corelock, uninstall, window


@pytest.fixture
def bot(tmp_path, monkeypatch):
    folder = tmp_path / "bots" / "mom"
    for sub, name, text in (("settings", "bot.json", "{}"), ("vault", "keyring.json", "sealed"), ("data", "notes.txt", "hers"), ("packages/app", "app.py", "x"), ("chat", "main.jsonl", "{}")):
        (folder / sub).mkdir(parents=True, exist_ok=True)
        (folder / sub / name).write_text(text, encoding="utf-8")
    (folder / uninstall.MARK).write_text("x", encoding="utf-8")
    monkeypatch.setenv("PUFFPAI_STATE", str(folder))
    monkeypatch.setattr(cli, "_terminal", lambda: None)
    monkeypatch.setattr(corelock, "holder", lambda s: None)
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.chdir(tmp_path)
    return folder


def _args(yes=False, keep=False):
    return argparse.Namespace(yes=yes, keep_vault=keep)


def _run(args, *answers):
    said, keys = [], iter(answers)

    def ask(prompt):
        said.append(prompt)
        try:
            return next(keys)
        except StopIteration:
            raise EOFError from None
    return uninstall.run(args, ask=ask, say=said.append), "\n".join(said)


def test_it_says_everything_first_and_asks_twice_when_a_vault_goes(bot):
    code, text = _run(_args(), "", "yes")
    assert code == 0 and not bot.exists()
    assert text.index(uninstall.VAULT_LINE.strip()) < text.index(uninstall.VAULT_ASK) < text.index(uninstall.CONFIRM) < text.index("This bot is removed.")
    assert str(bot) in text and "leaves the code folder" in text


@pytest.mark.parametrize("answers", [("", "no"), ("", ""), ("k", "n"), ()])
def test_anything_but_yes_removes_nothing(bot, answers):
    code, text = _run(_args(), *answers)
    assert code == 1 and uninstall.KEPT in text
    assert (bot / "vault" / "keyring.json").read_text(encoding="utf-8") == "sealed" and (bot / "data" / "notes.txt").exists()
    assert not list((Path.home() / "Documents").iterdir())


def test_k_keeps_a_copy_of_the_vault_before_anything_goes(bot):
    code, text = _run(_args(), "k", "yes")
    assert code == 0 and not bot.exists()
    copy = next((Path.home() / "Documents").iterdir())
    assert "PuffPAI vault" in copy.name and (copy / "keyring.json").read_text(encoding="utf-8") == "sealed"
    assert str(copy) in text


def test_yes_asks_nothing_and_takes_the_vault_unless_told_not_to(bot):
    assert _run(_args(yes=True))[0] == 0 and not bot.exists()


def test_yes_with_keep_vault_keeps_it(bot):
    code, _text = _run(_args(yes=True, keep=True))
    assert code == 0 and next((Path.home() / "Documents").iterdir()).joinpath("keyring.json").exists()


def test_a_vault_that_cant_be_copied_stops_everything(bot, monkeypatch):
    def fails(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(uninstall.shutil, "copytree", fails)
    code, text = _run(_args(), "k", "yes")
    assert code == 1 and "nothing was removed" in text and (bot / "vault" / "keyring.json").exists()


def test_the_vault_goes_last(bot, monkeypatch):
    order = []
    real = uninstall.fsutil._remove_tree
    monkeypatch.setattr(uninstall.fsutil, "_remove_tree", lambda p: (order.append(os.path.basename(p)), real(p))[1])
    assert uninstall.remove_all(bot) == []
    assert order[-1] == "vault" and {"settings", "packages", "chat", "data"} <= set(order)


def test_another_bot_and_a_chosen_data_folder_stay(bot, tmp_path):
    sister = tmp_path / "bots" / "sister"
    (sister / "settings").mkdir(parents=True)
    (sister / "settings" / "ports.json").write_text("{}", encoding="utf-8")
    chosen = tmp_path / "My Data"
    chosen.mkdir()
    (chosen / "photo.txt").write_text("hers", encoding="utf-8")
    (bot / "settings" / "data.json").write_text(json.dumps({"data_folder": str(chosen)}), encoding="utf-8")
    code, text = _run(_args(yes=True))
    assert code == 0 and not bot.exists() and (sister / "settings" / "ports.json").exists() and (chosen / "photo.txt").exists()
    assert str(chosen) in text


def test_a_linked_folder_inside_is_removed_as_a_link_and_not_followed(bot, tmp_path):
    outside = tmp_path / "her code"
    outside.mkdir()
    (outside / "keep.txt").write_text("hers", encoding="utf-8")
    link = bot / "packages" / "linked"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError:
        pytest.skip("this account can't make a link")
    assert _run(_args(yes=True))[0] == 0
    assert (outside / "keep.txt").exists() and not bot.exists()


@pytest.mark.parametrize("make", ["no-settings", "settings-only", "home", "documents", "code", "parent-of-bots", "extended", "share"])
def test_it_refuses_what_is_not_a_bots_folder(bot, tmp_path, monkeypatch, make):
    def marked(f):
        (f / "settings").mkdir(parents=True, exist_ok=True)
        (f / uninstall.MARK).write_text("x", encoding="utf-8")
        return f
    if make == "no-settings":
        folder = tmp_path / "just a folder"
        folder.mkdir()
    elif make == "settings-only":
        folder = tmp_path / "has settings"
        (folder / "settings").mkdir(parents=True)
    elif make == "home":
        folder = marked(Path.home())
    elif make == "documents":
        folder = marked(Path.home() / "Documents")
    elif make == "parent-of-bots":
        folder = marked(bot.parent)                        # it holds another bot
    elif make == "extended":
        folder = "\\\\?\\" + str(bot)
    elif make == "share":
        folder = "\\\\localhost\\C$\\" + str(bot)[3:]
    else:
        folder = Path(uninstall.__file__).resolve().parents[2]
    monkeypatch.setenv("PUFFPAI_STATE", str(folder))
    code, text = _run(_args(yes=True))
    assert code == 2 and "not a bot's folder" in text and bot.exists() and (make in ("extended", "share") or Path(folder).exists())


def test_an_open_window_stops_it(bot, monkeypatch):
    monkeypatch.setattr(corelock, "holder", lambda s: {"pid": 1} if Path(s).name == window.FOLDER else None)
    code, text = _run(_args(yes=True))
    assert code == 1 and uninstall.WINDOW_OPEN in text and bot.exists()


def test_a_bot_that_does_not_stop_stops_it(bot, monkeypatch):
    monkeypatch.setattr(corelock, "holder", lambda s: None if Path(s).name == window.FOLDER else {"pid": 1, "kind": corelock.CORE})
    code, text = _run(_args(yes=True))
    assert code == 1 and uninstall.BUSY in text and bot.exists()


def test_a_running_bot_is_stopped_first(bot, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_terminal", lambda: {"port": 1, "token": "x"})
    monkeypatch.setattr(cli, "_call", lambda info, method, path, body=None: calls.append((method, path)) or (202, {}))
    code, _text = _run(_args(yes=True))
    assert code == 0 and calls == [("POST", "/v1/shutdown")]


def test_only_this_bots_launchers_go(tmp_path, monkeypatch):
    fake = tmp_path / "repo" / "core" / "puffpai_core" / "uninstall.py"
    fake.parent.mkdir(parents=True)
    monkeypatch.setattr(uninstall, "__file__", str(fake))
    monkeypatch.delenv("PUFFPAI_STATE", raising=False)
    repo = tmp_path / "repo"
    (repo / "Start BuildBot.cmd").write_text('set "PUFFPAI_BOT=sister"\n', encoding="ascii")
    (repo / "bots" / "mom").mkdir(parents=True)
    (repo / "bots" / "mom" / "Start BuildBot.cmd").write_text('set "PUFFPAI_BOT=mom"\n', encoding="ascii")
    assert uninstall.own_launchers(tmp_path, "mom") == [repo / "bots" / "mom" / "Start BuildBot.cmd"]
    assert uninstall.own_launchers(tmp_path, "sister") == [repo / "Start BuildBot.cmd"]


def test_the_parser_takes_it_and_never_defaults_to_yes():
    a = cli.build_parser().parse_args(["uninstall"])
    assert a.yes is False and a.keep_vault is False


@pytest.mark.skipif(os.name != "nt", reason="a junction is Windows")
def test_a_junction_inside_is_removed_as_a_link_and_not_followed(bot, tmp_path):
    import subprocess
    outside = tmp_path / "her code"
    outside.mkdir()
    (outside / "keep.txt").write_text("hers", encoding="utf-8")
    link = bot / "packages" / "linked"
    got = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)], capture_output=True, stdin=subprocess.DEVNULL, creationflags=0x08000000)
    assert got.returncode == 0, got.stderr
    assert _run(_args(yes=True))[0] == 0
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "hers" and not bot.exists()


def test_a_relative_state_path_means_the_folder_it_was_checked_as(bot, tmp_path, monkeypatch):
    other = bot.parent.parent / "mom"                       # what ..\\mom would mean after a chdir to the parent
    other.mkdir()
    (other / "hers.txt").write_text("hers", encoding="utf-8")
    monkeypatch.chdir(bot)
    monkeypatch.setenv("PUFFPAI_STATE", os.path.join("..", "mom"))
    code, _text = _run(_args(yes=True))
    assert code == 0 and not bot.exists() and (other / "hers.txt").exists()


def test_a_window_opened_while_it_asked_stops_it(bot, monkeypatch):
    calls = {"n": 0}

    def holder(s):
        if Path(s).name != window.FOLDER:
            return None
        calls["n"] += 1
        return None if calls["n"] == 1 else {"pid": 1}
    monkeypatch.setattr(corelock, "holder", holder)
    code, text = _run(_args(), "", "yes")
    assert code == 1 and uninstall.WINDOW_OPEN in text and (bot / "vault" / "keyring.json").exists()


def test_the_vault_stays_if_anything_else_could_not_go(bot, monkeypatch):
    real = uninstall.fsutil._remove_tree

    def held(path):
        if os.path.basename(path) == "chat":
            raise OSError("held")
        real(path)
    monkeypatch.setattr(uninstall.fsutil, "_remove_tree", held)
    left = uninstall.remove_all(bot)
    assert left == [str(bot / "chat")] and (bot / "vault" / "keyring.json").exists()


def test_a_data_folder_set_to_the_bots_own_folder_is_not_said_to_stay(bot):
    (bot / "settings" / "data.json").write_text(json.dumps({"data_folder": str(bot)}), encoding="utf-8")
    _code, text = _run(_args(), "", "no")
    assert "It leaves your data folder" not in text and "its data folder" in text


def test_a_failed_copy_leaves_no_half_a_vault(bot, monkeypatch):
    real = uninstall.shutil.copytree

    def half(src, dest, **k):
        Path(dest).mkdir(parents=True)
        (Path(dest) / "part").write_text("x", encoding="utf-8")
        raise OSError("disk full")
    monkeypatch.setattr(uninstall.shutil, "copytree", half)
    code, _text = _run(_args(), "k", "yes")
    assert code == 1 and list((Path.home() / "Documents").iterdir()) == [] and (bot / "vault" / "keyring.json").exists()


@pytest.mark.parametrize("words", [["uninstall", "--y"], ["uninstall", "--ye"], ["uninstall", "--yess"]])
def test_only_the_whole_word_yes_means_yes(bot, words):
    with pytest.raises(SystemExit):
        cli.main(words)
    assert bot.exists()


def test_this_bots_linux_and_mac_launchers_go_and_a_like_named_bots_stay(tmp_path, monkeypatch):
    fake = tmp_path / "repo" / "core" / "puffpai_core" / "uninstall.py"
    fake.parent.mkdir(parents=True)
    monkeypatch.setattr(uninstall, "__file__", str(fake))
    monkeypatch.delenv("PUFFPAI_STATE", raising=False)
    repo = tmp_path / "repo"
    (repo / "start-buildbot.sh").write_text('export PUFFPAI_BOT="mom"\n', encoding="ascii")
    (repo / "stop-buildbot.sh").write_text("export PUFFPAI_BOT=mom-2\n", encoding="ascii")
    assert uninstall.own_launchers(tmp_path, "mom") == [repo / "start-buildbot.sh"]
