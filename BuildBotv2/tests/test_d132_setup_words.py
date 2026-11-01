"""What setup says about a locked vault and about signing a browser in (D132, from the owner's build on her Orin)."""

import io
import os
from types import SimpleNamespace

import pytest

from puffpai_core import termchat
from puffpai_core.api import network
from puffpai_core.wizard import engine as engine_mod, lines as L, terminal as term


def _launchers(tmp_path, monkeypatch):
    start = tmp_path / term.launcher_name("Start BuildBot.cmd")
    monkeypatch.setattr(term, "launcher", lambda: start)
    for name in ("Chat with BuildBot.cmd", "Sign in a browser.cmd"):
        (tmp_path / term.launcher_name(name)).write_text("x", encoding="utf-8")


def test_the_vault_line_names_the_real_ways_out_and_never_start(tmp_path, monkeypatch):
    _launchers(tmp_path, monkeypatch)
    line = term.vault_wait_line()
    assert term.how_to_run(term.chat_launcher()) in line and "/unlock" in line and "PIN" in line
    assert "Building" in line and term.how_to_run(term.signin_launcher()) in line
    assert term.launcher_name("Start BuildBot.cmd") not in line
    assert "—" not in line


def test_the_vault_line_from_source_names_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(term, "launcher", lambda: tmp_path / "no such file")
    line = term.vault_wait_line()
    assert " chat" in line and "pair --running" in line and "/unlock" in line


@pytest.mark.skipif(os.name == "nt", reason="a terminal runs a launcher only off Windows")
def test_in_a_container_the_line_goes_in_from_outside(tmp_path, monkeypatch):
    _launchers(tmp_path, monkeypatch)
    monkeypatch.setenv("PUFFPAI_HOST_EXEC", "docker exec -it box")
    assert f"run docker exec -it box {term.chat_launcher()}" in term.vault_wait_line()


def test_the_end_of_setup_gives_the_code_and_how_to_get_a_new_one(tmp_path, monkeypatch):
    _launchers(tmp_path, monkeypatch)
    monkeypatch.setattr(network, "saved_on", lambda state: True)
    monkeypatch.setattr(network, "own_addresses", lambda: {"10.87.1.41"})
    monkeypatch.setattr(network, "own_names", lambda: set())
    link = SimpleNamespace(url="http://puffpai.localhost:8420/#pair=ABCD-EFGH-JKLM", code="ABCD-EFGH-JKLM", line="")
    said = term.pair_lines(tmp_path, 8420, link)
    assert said[0] == f"To sign this browser in, open {link.url} {termchat.PAIR_ONCE}"
    assert "type ABCD-EFGH-JKLM" in said[1] and said[2] == "http://10.87.1.41:8423/"
    assert said[3].startswith("For a new code later, ") and term.how_to_run(term.signin_launcher()) in said[3]


def test_a_desktop_that_is_not_ours_gets_no_code(tmp_path):
    link = SimpleNamespace(url="http://puffpai.localhost:8420/", code="", line="Your Desktop is not one of PuffPAI's own.")
    assert term.pair_lines(tmp_path, 8420, link) == [link.line]


def test_a_plan_that_needs_the_cloud_key_waits_and_says_so_once(tmp_path, monkeypatch):
    _launchers(tmp_path, monkeypatch)
    started = []
    ts = SimpleNamespace(plan_start=lambda build: started.append(build) or {"ready": False, "now": "", "vault": True})
    eng = SimpleNamespace(services=SimpleNamespace(toolsmith=ts), build_id=lambda: "b1")
    step = engine_mod.PlanStep(eng)
    first = step.live({}, None, "terminal")
    assert first["status"] == "working" and first["lines"][0] == L.PLAN_NEEDS_KEY and "/unlock" in first["lines"][1]
    assert step.live({}, None, "terminal")["lines"] == []


@pytest.mark.parametrize("where, opened, waits", [("cloud", False, True), ("cloud", True, False), ("local", False, False)])
def test_only_a_cloud_choice_with_a_locked_vault_holds_the_plan(where, opened, waits):
    from puffpai_core.zone3.toolsmith_host.service import ToolsmithHost as Host
    me = SimpleNamespace(settings=lambda: {"where": where}, ports=SimpleNamespace(vault_open=lambda: opened))
    assert Host.plan_waits_for_key(me) is waits


def test_a_card_with_no_keyboard_answers_later_and_shows_no_traceback():
    chat = termchat.Chat(None, out=io.StringIO())

    def no_input(prompt=""):
        raise EOFError
    chat.read_line = no_input
    assert chat.keys("Say Yes?", [("y", "yes")]) == ""
