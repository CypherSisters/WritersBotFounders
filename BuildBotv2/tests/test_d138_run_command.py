"""D138 part 1: chat runs one command after a Yes. The owner's chat, her Yes card, the real consent store and real
processes. The commands are small and unrelated: a recipe note, an invoice count, a bird list."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from conftest import wait_for
from zone2_doubles import FakeRuntime, GateDouble, call, make_brain
from test_term_chat import _chat, live  # noqa: F401 - a real Core on free ports
from puffpai_core import termchat, wiring
from puffpai_core.consent import card_sha, card_view
from puffpai_core.steward import jobs
from puffpai_core.zone2 import commands, loop
from puffpai_core.zone2.tools import NO_ASK_LINE, SAID_NO_LINE, ToolTable, call as call_tool, make

NODE = shutil.which("node")
SELF_ENDS_S = 20     # a test's own child and grandchild end by themselves after this, whatever happens


#: This test's own Python, by its full path (the machine guard refuses a bare ``python3`` while the Store stub is on PATH).
PY = '"' + sys.executable.replace(os.sep, "/") + '"'


def _write_and_print(name: str, text: str) -> str:
    return f"echo {text} > {name} && echo {text}"


def _script(path) -> str:
    return f"{PY} \"{str(path).replace(os.sep, '/')}\""


UNRELATED = [_write_and_print("recipes.txt", "soup"), _write_and_print("invoices.txt", "3 open"),
             _write_and_print("birds.txt", "wren")]


def _brain(state, freshtable, core, wait_s=30.0, script=None, gate=None):
    rt = FakeRuntime(*(script or []))
    brain, _items, _reg = make_brain(state, freshtable, rt, gate=gate, consent=wiring.core_question(core, wait_s=wait_s))
    return brain, rt


def _turn(brain):
    events, got = [], {}
    t = threading.Thread(target=lambda: got.update(brain.turn("please do it", emit=events.append)), daemon=True)
    t.start()
    return t, events, got


def _answer(core, answer):
    rec = wait_for(lambda: core.consent.list(status="pending"), 20)[0]
    core.consent.answer(rec["consent_id"], answer, "tap", None, by="test", card_sha=card_sha(rec["card"]))
    return rec


# -- Yes runs it, No doesn't, no answer doesn't -------------------------------------------------------------------
@pytest.mark.parametrize("command,made,says", [(UNRELATED[0], "recipes.txt", "soup"),
                                               (UNRELATED[1], "invoices.txt", "3 open"),
                                               (UNRELATED[2], "birds.txt", "wren")])
def test_a_yes_runs_it_once_and_the_model_gets_the_result(state, freshtable, core, command, made, says):
    brain, rt = _brain(state, freshtable, core, script=[call("run_command", command=command), {"text": "Done."}])
    t, events, got = _turn(brain)
    rec = _answer(core, "yes")
    t.join(60)
    assert got["text"] == "Done." and rec["scope"] == {"kind": "tool_call", "target": "concierge.run_command"}
    assert rec["floor"] is False                                       # one tap, an everyday Yes card
    folder = commands.workspace(state)
    assert Path(folder, made).read_text().strip() == says
    told = json.loads(rt.calls[1]["messages"][-1]["content"])
    assert told == {"exit_code": 0, "worked": True, "folder": folder, "output": says}
    ask = next(e for e in events if e["type"] == "ask")["question"]
    assert ask["consent_id"] == rec["consent_id"] and ask["card"]["title"] == commands.TITLE   # every surface draws it
    assert sum(e["type"] == "ask" for e in events) == 1
    assert {"type": "answered", "yes": True} in events


def test_a_no_runs_nothing_and_ends_the_turn(state, freshtable, core):
    brain, rt = _brain(state, freshtable, core, script=[call("run_command", command=UNRELATED[0]), {"text": "never"}])
    t, events, got = _turn(brain)
    _answer(core, "no")
    t.join(60)
    assert not (Path(commands.workspace(state)) / "recipes.txt").exists()
    assert got["text"] == SAID_NO_LINE and len(rt.calls) == 1          # the model never tries it another way
    assert {"type": "answered", "yes": False} in events


def test_no_answer_runs_nothing(state, freshtable, core):
    brain, rt = _brain(state, freshtable, core, wait_s=0.3,
                       script=[call("run_command", command=UNRELATED[1]), {"text": "never"}])
    t, _events, got = _turn(brain)
    t.join(60)
    assert not (Path(commands.workspace(state)) / "invoices.txt").exists()
    assert "No answer" in got["text"] and len(rt.calls) == 1
    assert [r["status"] for r in core.consent.list()] == ["expired"]


def test_a_changed_card_is_no_yes(state, core):
    """The same check as a start card: a Yes counts only for exactly the card that was raised."""
    ask = wiring.core_question(core, wait_s=20)
    q = commands.question(state, {"command": "echo soup"}, {"keep": str})
    got = {}
    t = threading.Thread(target=lambda: got.update(v=ask(q)), daemon=True)
    t.start()
    rec = wait_for(lambda: core.consent.list(status="pending"), 20)[0]
    with pytest.raises(Exception):
        core.consent.answer(rec["consent_id"], "yes", "tap", None, by="test", card_sha="not-this-card")
    assert core.consent.get(rec["consent_id"])["status"] == "pending"
    core.consent.answer(rec["consent_id"], "no", "tap", None, by="test", card_sha=rec["card_sha"])
    t.join(20)
    assert got["v"] is False


def test_no_way_to_ask_means_it_never_runs(state):
    tool = make(commands.TOOL, "Run.", commands.SCHEMA, lambda a, c: commands.run(state, a, c),
                question=lambda a, c: commands.question(state, a, c))
    got = call_tool(ToolTable([tool]), commands.TOOL, {"command": UNRELATED[2]}, {"keep": str})
    assert not got["ok"] and got["error"] == NO_ASK_LINE
    assert not (Path(commands.workspace(state)) / "birds.txt").exists()


def test_the_tool_route_outside_chat_runs_nothing_and_raises_no_card(state, freshtable, core):
    brain, _rt = _brain(state, freshtable, core)
    got = brain.call_tool("run_command", {"command": UNRELATED[2]})
    assert not got["ok"] and commands.ONLY_CHAT in got["error"]
    assert core.consent.list(status="pending") == []


@pytest.mark.parametrize("ctx", [{}, {"said_yes": False}, {"said_yes": "yes"}, {"said_yes": 1}])
def test_run_checks_her_yes_again(state, ctx):
    with pytest.raises(ValueError, match="not approved"):
        commands.run(state, {"command": UNRELATED[0]}, ctx)
    assert not (Path(commands.workspace(state)) / "recipes.txt").exists()


def test_a_yes_after_the_chat_closed_runs_nothing(state):
    gone = threading.Event()
    gone.set()
    with pytest.raises(ValueError, match="closed"):
        commands.run(state, {"command": UNRELATED[0]}, {"said_yes": True, "cancelled": gone})
    assert not (Path(commands.workspace(state)) / "recipes.txt").exists()


def test_only_a_command_question_gets_a_card_and_a_blank_one_does_not(core):
    ask = wiring.core_question(core, wait_s=0.2)
    assert ask({"kind": "command", "command": "  "}) is None and ask({"kind": "tool_call"}) is None
    assert core.consent.list() == []


def test_the_tool_is_always_there_for_the_model(state, freshtable, core):
    brain, rt = _brain(state, freshtable, core, script=[{"text": "hi"}])
    brain.turn("hello")
    spec = next(t["function"] for t in rt.calls[0]["tools"] if t["function"]["name"] == "run_command")
    assert spec["parameters"]["properties"].keys() == {"command"} and commands.shell_name() in spec["description"]
    assert "run_command" in loop.ACTING_CORE_TOOLS


# -- refusals, before she is asked --------------------------------------------------------------------------------
PHONE = "555-0134"
KEY = "sk-" + "a" * 30


def _refused(state, words, keep=None):
    gate = GateDouble()
    with pytest.raises(ValueError) as exc:
        commands.question(state, {"command": words}, {"keep": keep or gate.gate().keep})
    return str(exc.value)


@pytest.mark.parametrize("words,line", [
    ("", "no command"), ("   ", "no command"), (None, "no command"), (12, "no command"),
    ("x" * (commands.MAX_COMMAND + 1), "too long"), ("echo a\x00b", "too long"),
    ('{"tool": "list_installed", "args": {}}', "written as JSON"), ('["ls", "-la"]', "written as JSON"),
])
def test_what_is_not_a_command_is_refused(state, words, line):
    assert line in _refused(state, words)


def test_text_that_only_looks_like_json_is_not_refused_for_that(state):
    assert commands.check_command("[ -f recipes.txt ] && echo yes", state)
    assert commands.check_command("{ echo a; echo b; }", state)


@pytest.mark.parametrize("name", ["settings/anchor.key", "machine.key", "MACHINE.KEY", "settings/terminal.json", "tokens.db", "consent.db",
                                  "logs/audit.jsonl", "logs/outside.jsonl", "logs/vault.log", "pairing.json",
                                  "vault/keyring.json", "SETTINGS/TERMINAL.JSON"])
def test_a_protected_name_is_refused_wherever_it_is_said_to_be(state, name):
    assert _refused(state, f"cat /somewhere/{name}") == commands.NO_SECRETS_LINE
    assert _refused(state, f"cat somewhere\\{name}") == commands.NO_SECRETS_LINE


def test_a_protected_place_by_its_full_path_is_refused(state):
    for where in (state / "vault", state / "vault.inbox", state / "settings", state / "logs", state / "tokens.db"):
        assert _refused(state, f'dir "{where}"') == commands.NO_SECRETS_LINE
        assert _refused(state, f'ls "{str(where).replace(os.sep, "/")}/"') == commands.NO_SECRETS_LINE


@pytest.mark.parametrize("words", ["ls ..", "cat ../settings/bot.json", "cat ~/../logs/x", "ls ~/..", "cat ../vault/x",
                                   "ls ../vault", "cd .. && ls","type ..\\vault\\x"])
def test_a_path_that_climbs_to_the_core_is_refused(state, words):
    assert _refused(state, words) == commands.NO_SECRETS_LINE


@pytest.mark.parametrize("words", ["cat tokens.d?", "cat conse*.db", "cat anchor.k?y", "cat *inal.json"])
def test_a_wildcard_that_fits_a_protected_name_is_refused(state, words):
    assert _refused(state, words) == commands.NO_SECRETS_LINE


def test_an_everyday_wildcard_is_not_refused(state):
    for words in ("ls log*", "ls set*", "rm *.tmp", "cat notes?.txt"):
        assert commands.check_command(words, state) == words


def test_a_wildcard_that_finds_a_protected_place_is_refused(state):
    (state / "settings").mkdir(parents=True, exist_ok=True)
    assert _refused(state, "ls ../set*") == commands.NO_SECRETS_LINE


@pytest.mark.parametrize("words", ["ls ../*", "ls ../v*", "ls ../vault*/x", "ls .*/set*", "ls .?/set*",
                                   "ls */../../set*", "ls ./../set*", "cat ../set*/*", "cat .*/set*/*"])
def test_a_wildcard_that_climbs_is_refused_before_the_folder_is_made_and_after(state, words):
    """The first command runs in a folder not made yet, and on Linux nothing climbs out of what is not there."""
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "scratch-made-up.json").write_text("{}", encoding="utf-8")
    (state / "vault").mkdir(parents=True, exist_ok=True)
    (state / "vault" / "x").write_text("made up", encoding="utf-8")
    assert not os.path.exists(commands.workspace(state))
    assert _refused(state, words) == commands.NO_SECRETS_LINE
    commands.folder_for(state)
    os.makedirs(os.path.join(commands.workspace(state), "sub"), exist_ok=True)
    assert _refused(state, words) == commands.NO_SECRETS_LINE


def test_an_everyday_dot_wildcard_is_not_refused(state):
    for words in ("ls .git*", "cat .env*", "ls .[a-z]*", "grep '.*' README.md", "find . -name '.*'", "grep '.*/x' a.txt"):
        assert commands.check_command(words, state) == words


posix_only = pytest.mark.skipif(os.name == "nt", reason="Linux and Mac places; Windows has its own cases")


@posix_only
@pytest.mark.parametrize("words", ["ls /proc/self/cwd/..", "ls /proc/self/cwd/../set*", "cat /proc/thread-self/cwd/../vault/x",
                                   "ls /proc/1/cwd", "ls /proc/*/cwd/../set*", "ls /proc/[0-9]*/root", "ls /dev/fd/3/../..",
                                   "cat /pr*/self/cwd/../settings/x", "cat /[p]roc/self/cwd/../settings/x",
                                   "cat /./proc/*/cwd/../settings/x", "cat /proc/./*/cwd/../settings/x",
                                   "cat /pr*/[1-9][0-9]*/cwd/x", "ls ../../../../../../../../pr*/self/cwd"])
def test_a_path_through_a_running_program_is_refused(state, words):
    assert _refused(state, words) == commands.NO_SECRETS_LINE


@posix_only
def test_plain_proc_and_device_words_still_run(state):
    for words in ("cat /proc/cpuinfo", "cat /proc/c*", "cat /*/*.conf", "echo hi > /dev/null", "cat /dev/stdin",
                  "echo hi > /dev/stderr", "ls /dev/tty"):
        assert commands.check_command(words, state) == words


@posix_only
def test_two_slashes_and_a_link_are_the_place_they_open(state):
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "vault").mkdir(parents=True, exist_ok=True)
    tidy = str(state).lstrip("/")
    head, _, rest = tidy.partition("/")
    for words in (f"ls //{head}/./{rest}/settings", f"ls //{head}/./{rest}/set*", f"ls ///{head}/./{rest}/vault"):
        assert _refused(state, words) == commands.NO_SECRETS_LINE, words
    folder = commands.folder_for(state)
    os.symlink(state / "vault", os.path.join(folder, "plain"))
    assert _refused(state, "ls plain/") == commands.NO_SECRETS_LINE
    assert _refused(state, "ls ./pl*/") == commands.NO_SECRETS_LINE


@posix_only
@pytest.mark.parametrize("words", ["cat fl", "cat f*", "cd vl && cat y", "cat ./fl", "ls v?"])
def test_a_bare_word_that_is_a_link_out_is_the_place_it_opens(state, words):
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "vault").mkdir(parents=True, exist_ok=True)
    folder = commands.folder_for(state)
    os.symlink(state / "settings" / "bot.json", os.path.join(folder, "fl"))
    os.symlink(state / "vault", os.path.join(folder, "vl"))
    assert _refused(state, words) == commands.NO_SECRETS_LINE


def test_a_bare_word_that_is_its_own_stays_fine(state):
    folder = commands.folder_for(state)
    with open(os.path.join(folder, "notes.txt"), "w", encoding="utf-8") as fh:
        fh.write("soup")
    for words in ("cat notes.txt", "cat no*", "cat not-there-yet", "echo / 2"):
        assert commands.check_command(words, state) == words


@posix_only
def test_a_mac_disk_ignores_case_and_so_does_this_check(state, monkeypatch):
    """Not run on a real Mac: this turns on the same fold the zone uses."""
    from puffpai_core.zone3 import toolsmith_zone
    monkeypatch.setattr(toolsmith_zone, "_FOLD_CASE", True)
    (state / "vault").mkdir(parents=True, exist_ok=True)
    assert _refused(state, "ls ../VAULT") == commands.NO_SECRETS_LINE


@pytest.mark.skipif(os.name != "nt", reason="two slashes name a server only on Windows; on Linux they are one slash")
def test_a_wildcard_on_a_server_is_never_looked_up(state, monkeypatch):
    """Looking it up would reach that server, as her, before her Yes."""
    def nope(_p):
        raise AssertionError("looked up a server's folder")
    monkeypatch.setattr(commands.glob, "glob", nope)
    assert commands.check_command("cat //server/share/*.txt", state)
    assert commands.check_command("cat \\\\server\\share\\*.txt", state)


def test_a_vault_setting_that_cannot_be_read_refuses_everything(state):
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "vault.json").write_text("{not json", encoding="utf-8")
    assert _refused(state, "echo soup") == commands.NO_SECRETS_LINE


def test_the_vault_where_her_setting_puts_it_is_protected(state, tmp_path):
    elsewhere = tmp_path / "usb" / "myvault"
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "vault.json").write_text(json.dumps({"folder": str(elsewhere)}), encoding="utf-8")
    assert _refused(state, f'dir "{elsewhere}"') == commands.NO_SECRETS_LINE


@pytest.mark.parametrize("words", [f"echo {PHONE}", f"curl -H 'x: {KEY}' http://127.0.0.1:9", "echo {{vault:r1}}"])
def test_a_command_holding_a_private_detail_or_a_handle_is_refused_no_card_can_show_it(state, words):
    """X6: cards live in a plain store, and this card must show the command exactly."""
    assert _refused(state, words) == commands.HOLDS_PRIVATE


def test_a_gate_that_breaks_means_no_card(state):
    def broken(_t):
        raise RuntimeError("gate")
    with pytest.raises(RuntimeError):
        commands.question(state, {"command": "echo soup"}, {"keep": broken})


def test_curly_braces_that_are_no_handle_pass(state):
    q = commands.question(state, {"command": "docker ps --format '{{.Names}}'"}, {"keep": str})
    assert q["command"] == "docker ps --format '{{.Names}}'"


@pytest.mark.parametrize("words", ["bash -c ls", "sh x.sh", "wsl ls", "WSL.EXE ls", "bash.exe x", '"bash" x',
                                   "C:\\Windows\\System32\\wsl.exe ls"])
def test_windows_with_no_git_bash_refuses_what_would_start_linux(state, monkeypatch, words):
    monkeypatch.setattr(commands, "IS_WINDOWS", True)
    monkeypatch.setattr(commands, "git_bash", lambda: None)
    assert "no Git Bash" in _refused(state, words)
    monkeypatch.setattr(commands, "git_bash", lambda: "/x/Git/bin/bash.exe")
    assert commands.question(state, {"command": words}, {"keep": str})["command"] == words


def test_off_windows_bash_is_only_bash(state, monkeypatch):
    monkeypatch.setattr(commands, "IS_WINDOWS", False)
    assert commands.question(state, {"command": "bash -c ls"}, {"keep": str})


# -- the card, on every surface -----------------------------------------------------------------------------------
def _raised(core, state, words, ctx=None):
    q = commands.question(state, {"command": words}, ctx or {"keep": str})
    got: dict = {}
    t = threading.Thread(target=lambda: got.update(v=wiring.core_question(core, wait_s=0.2)(q)), daemon=True)
    t.start()
    rec = wait_for(lambda: core.consent.list(status="pending"), 20)[0]
    return q, rec, t


class _Term(io.StringIO):
    encoding = "utf-8"


def _term_card(rec) -> str:
    out = _Term()
    chat = termchat.Chat(types.SimpleNamespace(info={"port": 1}), look=termchat.Look(out, env={}), read_line=lambda p="": "")
    chat.draw(dict(rec, **card_view(rec["card"], bool(rec["floor"]))))      # what GET /v1/consent adds
    return out.getvalue()


def test_the_card_says_it_plainly_and_shows_the_exact_command_and_folder(core, state):
    words = "wren-count add_sighting.py --bird wren && echo \"seen today\" | tee -a birds.txt"
    _q, rec, t = _raised(core, state, words)
    t.join(10)
    seen = card_view(rec["card"], bool(rec["floor"]))
    assert seen["title"] == "May I run this command?" and seen["floor"] is False
    assert seen["lines"][0] == commands.CARD_WHAT.format(shell=commands.shell_name())
    assert "as you, outside the Bridge" in seen["lines"][0]
    assert f"Command: {words}" in seen["lines"]
    assert f"Folder: {commands.workspace(state)}" in seen["lines"]
    assert not any("always" in line.lower() for line in seen["lines"])
    assert termchat.card_ticks(rec) is None                            # nothing to tick, no "yes to all"
    drawn = _term_card(rec)
    assert "May I run this command?" in drawn and "wren" in drawn and "tee -a birds.txt" in drawn


def test_a_long_command_with_no_spaces_is_all_there_in_lines_a_card_can_hold(core, state):
    words = "echo" + "${IFS}part" * 150
    _q, rec, t = _raised(core, state, words)
    t.join(10)
    lines = card_view(rec["card"], False)["lines"]
    parts = lines[1:-1]
    assert "".join(parts)[len("Command: "):] == words
    assert all(len(p) <= 100 + len("Command: ") for p in parts)
    drawn = "".join(_term_card(rec).split())
    assert "part" * 0 + "${IFS}part" * 3 in drawn.replace("|", "").replace("\u2502", "")


def test_a_command_on_several_lines_shows_each_line(core, state, monkeypatch):
    monkeypatch.setattr(commands, "git_bash", lambda: "/x/Git/bin/bash.exe")
    _q, rec, t = _raised(core, state, "echo soup\necho stew")
    t.join(10)
    lines = card_view(rec["card"], False)["lines"]
    assert lines[1:3] == ["Command: echo soup", "echo stew"]


@pytest.mark.parametrize("trick,shown", [("\x1b[2K", "[U+001B][2K"), ("\r", "[U+000D]"), ("\u202e", "[U+202E]"),
                                         ("\u200b", "[U+200B]"), ("\x07", "[U+0007]"), ("\t", "[U+0009]"),
                                         ("\u2028", "[U+2028]")])
def test_what_would_not_show_is_written_out_so_nothing_hides_on_the_card(core, state, trick, shown):
    _q, rec, t = _raised(core, state, f"echo soup{trick} && del invoices.txt")
    t.join(10)
    line = next(x for x in card_view(rec["card"], False)["lines"] if x.startswith("Command:"))
    assert line == f"Command: echo soup{shown} && del invoices.txt" and trick not in line


def test_the_card_goes_through_the_core_view_the_window_and_the_desktop_read(core, state):
    """``GET /v1/consent`` gives ``title`` and ``lines`` from ``card_view``; both pages draw each line as text, and a
    chat ``ask`` with a ``consent_id`` is what draws the card in the chat on both (pages/chat.js, window.js)."""
    root = Path(__file__).resolve().parents[1] / "packages" / "desktop" / "web"
    logic = (root / "logic.js").read_text(encoding="utf-8")
    assert "id: q.consent_id || q.id || null" in logic and "lines: lines," in logic
    assert "c.lines.map((l) => el(\"p\", { text: l }))" in (root / "desktop.js").read_text(encoding="utf-8")
    assert "if (!a.id || drawn.has(a.id)) return;" in (root / "pages" / "chat.js").read_text(encoding="utf-8")
    assert "if (a.id) cardInto(m.asks, a.id" in (root / "window.js").read_text(encoding="utf-8")


def test_terminal_chat_shows_the_card_named_by_the_ask_and_offers_yes_and_no_only(core, state):
    _q, rec, t = _raised(core, state, "echo wren")
    prompts: list[str] = []
    out = _Term()
    look = termchat.Look(out, env={})

    def read(prompt=""):
        prompts.append(prompt)
        return "n"
    sent = []

    class Api:
        info = {"port": 1}

        def call(self, method, path, body=None):
            if method == "GET":
                return 200, [dict(rec, lines=card_view(rec["card"], False)["lines"], title=commands.TITLE)]
            sent.append((path, body))
            return 200, {"status": "declined"}
    chat = termchat.Chat(Api(), look=look, read_line=read)
    chat._events(iter([{"type": "ask", "question": {"consent_id": rec["consent_id"], "card": rec["card"]}},
                       {"type": "answered", "yes": False}, {"type": "done", "text": "ok"}]), set(), [])
    t.join(10)
    text = out.getvalue()
    assert "echo wren" in text and "always" not in text.lower() and "always" not in " ".join(prompts).lower()
    assert sent and sent[0][1]["answer"] == "no" and sent[0][1]["card_sha"]


# -- the time limit and the job -----------------------------------------------------------------------------------
PARENT = """
import os, subprocess, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
subprocess.Popen([sys.executable, sys.argv[3], sys.argv[2], "{limit}"])
print("started", flush=True)
time.sleep({stay})
"""
GRANDCHILD = """
import os, sys, time
open(sys.argv[1], "w").write(str(os.getpid()))
time.sleep(float(sys.argv[2]))
"""


def _pair(tmp_path, stay):
    parent, child = tmp_path / "parent.py", tmp_path / "grandchild.py"
    parent.write_text(PARENT.format(limit=SELF_ENDS_S, stay=stay), encoding="utf-8")
    child.write_text(GRANDCHILD, encoding="utf-8")
    pids = tmp_path / "parent.pid", tmp_path / "grandchild.pid"
    return [sys.executable, str(parent), str(pids[0]), str(pids[1]), str(child)], pids


def _wait_pids(pids, timeout_s=15.0):
    wait_for(lambda: all(p.exists() and p.read_text().strip() for p in pids), timeout_s, 0.05)
    return [int(p.read_text()) for p in pids]


def _both_gone(pids):
    return wait_for(lambda: not any(jobs.pid_alive(p) for p in pids), 5, 0.05)


def test_the_time_limit_ends_a_child_and_its_grandchild(tmp_path):
    argv, files = _pair(tmp_path, SELF_ENDS_S)
    box: dict = {}
    t = threading.Thread(target=lambda: box.update(commands.run_program(argv, tmp_path, dict(os.environ), seconds=6)),
                         daemon=True)
    t.start()
    pids = _wait_pids(files, 6)
    assert all(jobs.pid_alive(p) for p in pids)
    t.join(30)
    assert box["code"] is None and box["stopped"] == "time" and "started" in box["text"]
    assert _both_gone(pids)


def test_a_timed_out_command_tells_the_model_and_says_it_did_not_finish(state, tmp_path, monkeypatch):
    monkeypatch.setattr(commands, "COMMAND_SECONDS", 1)
    script = tmp_path / "slow.py"
    script.write_text(f"import time\nprint('soup', flush=True)\ntime.sleep({SELF_ENDS_S})\n", encoding="utf-8")
    got = commands.run(state, {"command": _script(script)}, {"said_yes": True})
    assert got["exit_code"] is None and got["worked"] is False and got["output"] == "soup"
    assert got["note"] == "It was stopped after 1 seconds, so it did not finish."


def test_what_it_left_running_ends_with_it(tmp_path):
    argv, files = _pair(tmp_path, 0)
    got = commands.run_program(argv, tmp_path, dict(os.environ), seconds=30)
    assert got["code"] == 0 and got["stopped"] == "" and "started" in got["text"]
    assert _both_gone([int(files[0].read_text())] + ([int(files[1].read_text())] if files[1].exists() else []))


def test_a_closed_chat_ends_it(tmp_path):
    argv, files = _pair(tmp_path, SELF_ENDS_S)
    closed = threading.Event()
    box: dict = {}
    t = threading.Thread(target=lambda: box.update(commands.run_program(argv, tmp_path, dict(os.environ), seconds=60,
                                                                        cancelled=closed)), daemon=True)
    t.start()
    pids = _wait_pids(files)
    closed.set()
    t.join(30)
    assert box["code"] is None and box["stopped"] == "closed"
    assert _both_gone(pids)


def test_stdin_is_empty_so_nothing_waits_for_a_keyboard(tmp_path):
    got = commands.run_program([sys.executable, "-c", "import sys; print(repr(sys.stdin.read()))"], tmp_path,
                               dict(os.environ), seconds=30)
    assert got["code"] == 0 and got["text"] == "''"


def test_a_program_that_cannot_be_held_is_ended_and_not_counted_as_run(tmp_path, monkeypatch):
    real = jobs.Job.spawn_piped

    def no_job(*a, **k):
        job = real(*a, **k)
        job.terminate(wait_s=5)
        job.close()
        monkeypatch.setattr(jobs.Job, "contained", property(lambda self: False))
        return job
    monkeypatch.setattr(jobs.Job, "spawn_piped", no_job)
    with pytest.raises(RuntimeError, match="job of its own"):
        commands.run_program([sys.executable, "-c", "print(1)"], tmp_path, dict(os.environ), seconds=30)


# -- the output ---------------------------------------------------------------------------------------------------
def test_the_output_is_the_tail_and_says_it_was_cut(state, tmp_path):
    script = tmp_path / "long.py"
    script.write_text("print('x' * 9000 + 'END')\n", encoding="utf-8")
    got = commands.run(state, {"command": _script(script)}, {"said_yes": True})
    assert got["output"].startswith("...(start cut)\n") and got["output"].endswith("END")
    assert len(got["output"]) == commands.MAX_OUTPUT + len("...(start cut)\n")


def test_secrets_are_hidden_in_the_whole_text_before_the_trim():
    token = "ppk_" + "a1B2" * 12
    jwt = "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTYifQ." + "abcdefghijklmnop"
    # A key that starts before the trim point and ends after it: never half of it.
    near = token + "y" * (commands.MAX_OUTPUT - 10)
    shown = commands.finish_output(near)
    assert "a1B2a1B2" not in shown and "ppk_" not in shown
    for secret in (token, jwt, "sk-" + "b" * 40, "API_KEY=" + "c1D2" * 8):
        text = f"before {secret} after"
        assert secret.split("=")[-1] not in commands.finish_output(text) and "before" in commands.finish_output(text)


def test_a_key_a_real_command_prints_never_reaches_the_model(state, freshtable, core):
    token = "ppt_" + "Zz9y" * 12
    Path(commands.folder_for(state), "printer.txt").write_text(f"wren {token}", encoding="utf-8")
    words = "cat printer.txt" if commands.shell_name() != "the Windows command prompt" else "type printer.txt"
    brain, rt = _brain(state, freshtable, core, script=[call("run_command", command=words), {"text": "Done."}])
    t, _events, _got = _turn(brain)
    _answer(core, "yes")
    t.join(60)
    seen = rt.seen(1)
    assert token not in seen and "wren" in seen and commands.HIDDEN in seen


def test_the_tail_kept_while_it_runs_is_bounded_and_drops_the_cut_line(tmp_path, monkeypatch):
    monkeypatch.setattr(commands, "KEEP_BYTES", 2000)
    script = "print('H' * 5000); print('tail line')"
    got = commands.run_program([sys.executable, "-c", script], tmp_path, dict(os.environ), seconds=30)
    assert got["text"] == "tail line"


def test_a_failure_gets_the_note_and_stderr_comes_back(state, tmp_path):
    script = tmp_path / "fails.py"
    script.write_text("import sys\nprint('out')\nprint('bad invoice', file=sys.stderr)\nsys.exit(3)\n", encoding="utf-8")
    got = commands.run(state, {"command": _script(script)}, {"said_yes": True})
    assert got["exit_code"] == 3 and got["worked"] is False and got["output"].split() == ["out", "bad", "invoice"]
    assert got["note"] == commands.FAILED_NOTE.format(code=3)


@pytest.mark.parametrize("output", ["bash: line 1: jq: command not found", "sh: 1: jq: not found",
                                    "'jq' is not recognized as an internal or external command,",
                                    "ModuleNotFoundError: No module named 'yaml'", "Error: Cannot find module 'left-pad'"])
def test_something_missing_gets_the_missing_note_and_no_install(state, monkeypatch, output):
    monkeypatch.setattr(commands, "run_program", lambda *a, **k: {"code": 1, "stopped": "", "text": output})
    got = commands.run(state, {"command": "wren-count"}, {"said_yes": True})
    assert got["note"] == commands.MISSING_NOTE.format(code=1) and "do not try to install" in got["note"]


def test_nothing_about_the_command_goes_to_the_log(state, freshtable, core, caplog):
    words = _write_and_print("recipes.txt", "soup-log-check")
    brain, _rt = _brain(state, freshtable, core, script=[call("run_command", command=words), {"text": "Done."}])
    with caplog.at_level("DEBUG"):
        t, _events, _got = _turn(brain)
        _answer(core, "yes")
        t.join(60)
    assert "soup-log-check" not in caplog.text


# -- the environment ----------------------------------------------------------------------------------------------
def test_the_environment_drops_tokens_and_secret_names(state, monkeypatch):
    token = "ppk_" + "Q7w8" * 12
    for name, value in (("PUFFPAI_TOKEN", "t1"), ("PUFFPAI_STATE_X", "t2"), ("MY_API_KEY", "t3"), ("BIRD_PASSWORD", "t4"),
                        ("INVOICE_SECRET", "t5"), ("GH_TOKEN", "t6"), ("A_PIN", "t7"), ("HOLDS_ONE", f"x {token} y")):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("RECIPE_FOLDER", "soups")
    env = commands.command_env(state, None)
    upper = {k.upper() for k in env}
    assert not upper & {"PUFFPAI_TOKEN", "PUFFPAI_STATE_X", "MY_API_KEY", "BIRD_PASSWORD", "INVOICE_SECRET", "GH_TOKEN",
                        "A_PIN", "HOLDS_ONE"}
    assert env["RECIPE_FOLDER"] == "soups" and env["HOME"] == commands.workspace(state)
    assert not any(token in v for v in env.values())


def test_a_real_command_does_not_see_them_and_finds_the_bots_python(state, monkeypatch):
    monkeypatch.setenv("BIRD_PASSWORD", "wren-value")
    monkeypatch.setenv("RECIPE_FOLDER", "soups")
    no_stubs = [d for d in os.environ["PATH"].split(os.pathsep) if "windowsapps" not in d.lower()]
    monkeypatch.setenv("PATH", os.pathsep.join([commands.shim_dir(state), *no_stubs]))
    words = ("python3 -c \"import os, sys; print(os.environ.get('BIRD_PASSWORD'), os.environ.get('RECIPE_FOLDER'), "
             "os.path.samefile(sys.executable, sys.executable))\"")
    got = commands.run(state, {"command": words}, {"said_yes": True})
    assert got["worked"], got
    assert got["output"] == "None soups True" and got["folder"] == commands.workspace(state)


@pytest.mark.parametrize("windows", [True, False])
def test_the_python_helpers_are_written_for_each_shell(state, monkeypatch, windows):
    monkeypatch.setattr(commands, "IS_WINDOWS", windows)
    folder = Path(commands.shim_dir(state))
    names = {p.name for p in folder.iterdir()}
    assert {"python", "python3"} <= names and "puffpai" not in names
    assert ("python3.cmd" in names) is windows


# -- the shell, each operating system with fakes (this ran on Windows only; never on Linux or Mac) ------------------
def _bash_at(root: Path, *parts) -> Path:
    path = root.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _windows(monkeypatch, tmp_path):
    monkeypatch.setattr(commands, "IS_WINDOWS", True)
    for name in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)", "PUFFPAI_BASH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nowhere"))


def test_windows_with_git_bash_uses_it_with_the_command_as_one_argument(monkeypatch, tmp_path):
    _windows(monkeypatch, tmp_path)
    bash = _bash_at(tmp_path / "pf", "Git", "bin", "bash.exe")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    assert commands.git_bash() == str(bash) and commands.shell_name() == "Git Bash"
    assert commands._shell_call('echo "a b"', commands.git_bash()) == \
        ([str(bash), "-c", 'echo "a b"'], f'"{bash}" -c "echo \\"a b\\""')
    assert "Commands run in Git Bash" in commands.description()


def test_windows_with_no_git_bash_uses_system32_cmd_and_never_path(monkeypatch, tmp_path):
    _windows(monkeypatch, tmp_path)
    on_path = _bash_at(tmp_path / "onpath", "bash.exe")
    monkeypatch.setenv("PATH", str(on_path.parent))
    monkeypatch.setattr(commands.devshell, "system_dir", lambda: str(tmp_path / "sys32"))
    assert commands.git_bash() is None and commands.shell_name() == "the Windows command prompt"
    argv, line = commands._shell_call('echo "a b"', None)
    exe = str(tmp_path / "sys32" / "cmd.exe")
    assert argv == [exe, "/D", "/S", "/C", 'echo "a b"'] and line == f'"{exe}" /D /S /C "echo "a b""'
    assert "Commands run in the Windows command prompt" in commands.description()


def test_puffpai_bash_names_a_program_but_never_the_one_that_starts_linux(monkeypatch, tmp_path):
    _windows(monkeypatch, tmp_path)
    mine = _bash_at(tmp_path, "tools", "bash.exe")
    monkeypatch.setenv("PUFFPAI_BASH", str(mine))
    assert commands.git_bash() == str(mine)
    # This check runs on Windows only, where normcase lowers a path: elsewhere the double gets the lowered spelling.
    linux = _bash_at(tmp_path, *(("Windows", "System32") if os.name == "nt" else ("windows", "system32")), "bash.exe")
    monkeypatch.setenv("PUFFPAI_BASH", str(linux))
    assert commands.git_bash() is None
    monkeypatch.setenv("PUFFPAI_BASH", str(tmp_path / "missing.exe"))
    assert commands.git_bash() is None


def test_linux_and_mac_use_bash_or_the_system_shell(monkeypatch, tmp_path):
    monkeypatch.setattr(commands, "IS_WINDOWS", False)
    bash = _bash_at(tmp_path, "bin", "bash")
    monkeypatch.setattr(commands, "_POSIX_BASH", (str(bash),))
    assert commands.git_bash() is None and commands.shell_name() == "bash"
    assert commands._shell_call("ls -la", commands.git_bash()) == ([str(bash), "-c", "ls -la"], None)
    assert "Commands run in bash" in commands.description()
    monkeypatch.setattr(commands, "_POSIX_BASH", (str(tmp_path / "none"),))
    assert commands.shell_name() == "the system shell"
    assert commands._shell_call("ls -la", None) == (["/bin/sh", "-c", "ls -la"], None)


def test_off_windows_a_command_line_is_never_passed_to_popen(monkeypatch, tmp_path):
    """On Linux and Mac the job starts the argv list; *line* is Windows' alone."""
    seen = {}

    class FakePopen:
        pid, _handle = 4242, 0

        def __init__(self, args, **kw):
            seen.update(args=args, kw=kw)
    monkeypatch.setattr(jobs, "IS_WINDOWS", False)
    monkeypatch.setattr(jobs, "_Leader", FakePopen)      # off Windows the job starts its own Popen, which is this
    job = jobs.Job.spawn_piped(["/bin/bash", "-c", "ls"], tmp_path, {}, stdin=subprocess.DEVNULL, line="ignored")
    assert seen["args"] == ["/bin/bash", "-c", "ls"] and seen["kw"]["start_new_session"] is True
    assert seen["kw"]["stdin"] == subprocess.DEVNULL and job.contained


# -- real shells on this computer -----------------------------------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows' own shells")
def test_a_real_cmd_runs_exactly_what_the_card_shows(state, monkeypatch):
    monkeypatch.setattr(commands, "git_bash", lambda: None)
    words = 'echo "a  b"&echo soup'
    got = commands.run(state, {"command": words}, {"said_yes": True})
    assert got["worked"] and got["output"].splitlines() == ['"a  b"', "soup"]


@pytest.mark.skipif(not commands.git_bash(), reason="no Git Bash here")
def test_a_real_git_bash_runs_exactly_what_the_card_shows(state):
    words = 'echo "a  b" | tr a x; printf "%s\\n" "$HOME"'
    got = commands.run(state, {"command": words}, {"said_yes": True})
    assert got["worked"] and got["output"].splitlines()[0] == "x  b"


def test_chat_keeps_the_stream_open_longer_than_a_card_and_a_command():
    assert termchat.STREAM_TIMEOUT_S > wiring.CARD_WAIT_S + commands.COMMAND_SECONDS


# -- the whole way: a real Core on free ports, the real chat stream, Terminal chat, the pages' own logic ---------------
@pytest.mark.parametrize("answer", ["y", "n"])
def test_terminal_chat_over_the_real_stream_shows_the_command_and_answers_it(live, answer):
    words = _write_and_print("sightings.txt", "wren")
    live.brain._runtime = FakeRuntime(call("run_command", command=words), {"text": "All done."})
    chat = _chat(live, keys=[answer])
    assert chat.turn("please note the wren") is True
    text = chat.out.getvalue()
    assert commands.TITLE in text and "echo wren" in text and "sightings.txt" in text
    assert "outside the Bridge" in text and "always" not in text.lower()
    made = Path(commands.workspace(live.state), "sightings.txt")
    assert made.exists() is (answer == "y")
    assert ("All done." in text) is (answer == "y") and ("You said no" in text) is (answer == "n")


@pytest.mark.skipif(not NODE, reason="no node here")
def test_the_real_card_row_reads_whole_in_the_pages_logic_with_yes_and_no_only(live):
    words = _write_and_print("invoices.txt", "3 open")
    live.brain._runtime = FakeRuntime(call("run_command", command=words), {"text": "All done."})
    gone = threading.Event()
    events: list = []
    t = threading.Thread(target=lambda: live.brain.turn("count them", emit=events.append, cancelled=gone), daemon=True)
    t.start()
    wait_for(lambda: live.core.consent.list(status="pending"), 20)
    st, rows = termchat.Api(live.info).call("GET", "/v1/consent?status=pending")
    assert st == 200
    row = rows[0]
    ask = next(e for e in events if e["type"] == "ask")
    script = ("const L = require(process.argv[1]);"
              "const d = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
              "const c = L.consentCard(d.row); const t = new L.ChatTurn(); t.add(d.ask);"
              "console.log(JSON.stringify({card: c, asks: t.asks}));")
    logic = Path(__file__).resolve().parents[1] / "packages" / "desktop" / "web" / "logic.js"
    done = subprocess.run([NODE, "-e", script, str(logic)], input=json.dumps({"row": row, "ask": ask}), text=True,
                          encoding="utf-8", capture_output=True, timeout=60, check=True)
    got = json.loads(done.stdout)
    assert got["asks"][0]["id"] == row["consent_id"] and got["asks"][0]["title"] == commands.TITLE
    assert got["card"]["title"] == commands.TITLE and f"Command: {words}" in got["card"]["lines"]
    assert got["card"]["needsPin"] is False and got["card"]["ticks"] is None
    assert [b["label"] for b in got["card"]["buttons"]] == ["Yes", "No"]
    live.core.consent.answer(row["consent_id"], "no", "tap", None, by="test", card_sha=row["card_sha"])
    t.join(30)


# -- what the verifier found ------------------------------------------------------------------------------------------
TRICKY = ["echo';'whoami", "echo 'a;b' \"c  d\"", 'echo "q\\"r"', "echo a\\\\b", "printf '%s\\n' 'x y'", 'echo "trail\\\\"',
          "echo * && echo '*'", "echo $((1+1))", "echo '' \"\" x", "echo a\necho b", "echo 'a'\\''b'",
          'echo "%PATH%" | wc -l', "echo caf\u00e9", 'echo "a"b"c"', "echo \\\"x\\\""]


@pytest.mark.skipif(not commands.git_bash(), reason="no Git Bash here")
@pytest.mark.parametrize("words", TRICKY)
def test_git_bash_gets_exactly_the_cards_text_the_same_as_a_script_file(state, tmp_path, words):
    """The oracle: the same words in a script file, which nothing parses but bash."""
    (Path(commands.folder_for(state)) / "one.txt").write_text("", encoding="utf-8")
    bash = commands.git_bash()
    script = tmp_path / "same.sh"
    script.write_bytes(words.encode("utf-8") + b"\n")
    env = commands.command_env(state, bash)
    want = commands.run_program([bash, str(script)], commands.workspace(state), env, seconds=30)
    got = commands.run(state, {"command": words}, {"said_yes": True})
    assert got["output"].replace("line 1", "x").split()[-1:] == want["text"].replace("line 1", "x").split()[-1:], got
    assert got["exit_code"] == want["code"]


@pytest.mark.skipif(not commands.git_bash(), reason="no Git Bash here")
@pytest.mark.parametrize("words", ["';'whoami", '";"whoami', "a\\b", "a\\\\b", "C:\\Users\\x", "\\\\server\\share", "trail\\",
                                   "trail\\\\", '\\"', '\\\\"', '""', "''", "*", "?.txt", "[a-z]", "{a,b}", "~/x", "%PATH%",
                                   "$HOME $(whoami) `whoami`", "a  b   c", "caf\u00e9", "(x)", "!x", "^x", "<x> & | ;"])
def test_bash_sees_every_character_of_the_command(state, words):
    """Bash's own copy of its -c text, with the words in a comment so they do nothing."""
    command = f'printf %s "$BASH_EXECUTION_STRING" # {words}'
    assert commands.run(state, {"command": command}, {"said_yes": True})["output"] == command


@pytest.mark.skipif(not commands.git_bash(), reason="no Git Bash here")
def test_the_verifiers_repro_never_runs_the_second_command(state):
    got = commands.run(state, {"command": "echo';'whoami"}, {"said_yes": True})
    assert got["exit_code"] == 127 and got["worked"] is False and "echo;whoami" in got["output"]


@pytest.mark.parametrize("words", ["echo soup\necho stew", "echo soup\r\necho stew", "echo soup\recho stew"])
def test_cmd_refuses_a_command_with_a_line_break_and_says_how(state, monkeypatch, words):
    monkeypatch.setattr(commands, "IS_WINDOWS", True)
    monkeypatch.setattr(commands, "git_bash", lambda: None)
    assert _refused(state, words) == commands.CMD_ONE_LINE
    with pytest.raises(ValueError, match="first line"):
        commands.run(state, {"command": words}, {"said_yes": True})
    monkeypatch.setattr(commands, "git_bash", lambda: "/x/Git/bin/bash.exe")
    assert commands.question(state, {"command": words}, {"keep": str})


@pytest.mark.parametrize("words", ["dir", "dir /s /b", "cmd /c dir", "git status", "python --version", "ls", "ls -la", "npm -v",
                                   "ipconfig /all", "findstr /i soup recipes.txt", "where /q git", "tasklist /v",
                                   "cd invoices && dir", "type birds.txt", "echo a/b", "node -v", "pip list",
                                   "git log --oneline -3", "cat notes/2024/march.txt", "dir C:\\Windows", "ls /usr/bin"])
@pytest.mark.parametrize("windows", [True, False])
def test_everyday_commands_are_not_refused(state, monkeypatch, words, windows):
    from puffpai_core.zone2.gate import no_keys
    monkeypatch.setattr(commands, "IS_WINDOWS", windows)
    monkeypatch.setattr(commands, "git_bash", lambda: "/x/Git/bin/bash.exe")
    assert commands.question(state, {"command": words}, {"keep": no_keys})["command"] == words


def test_git_bashs_name_for_a_drive_still_counts_as_a_path(state, monkeypatch):
    monkeypatch.setattr(commands, "IS_WINDOWS", True)
    drive = commands.workspace(state)[0].lower()
    if os.name == "nt":
        assert _refused(state, f"ls /{drive}/") == commands.NO_SECRETS_LINE


@pytest.mark.parametrize("name", ["GH_PAT", "AZURE_DEVOPS_PAT", "pat", "MY_pat"])
def test_a_personal_access_token_name_is_dropped(name):
    assert name not in commands.clean_env({name: "x"})


@pytest.mark.parametrize("name", ["PATH", "PATHEXT", "SPATIAL", "PATTERN", "HOMEPATH"])
def test_a_name_that_only_holds_pat_stays(name):
    assert commands.clean_env({name: "x"}) == {name: "x"}


@pytest.mark.parametrize("value", ["ghp_" + "a1B2" * 9, "sk-" + "a" * 30, "AKIA" + "ABCDEFGHIJKLMNOP"])
def test_a_value_shaped_like_a_key_is_dropped_whatever_its_name(value):
    assert commands.clean_env({"RECIPE_THING": value, "RECIPE_FOLDER": "soups"}) == {"RECIPE_FOLDER": "soups"}


def test_this_computers_path_is_never_taken_for_a_key():
    assert "PATH" in {k.upper() for k in commands.clean_env(os.environ)}


def test_a_closed_chat_stream_says_a_blank_line_every_client_skips():
    root = Path(__file__).resolve().parents[1]
    assert "if (!p.trim()) return;" in (root / "packages" / "desktop" / "web" / "logic.js").read_text(encoding="utf-8")
    assert "if not text:" in (root / "core" / "puffpai_core" / "termchat.py").read_text(encoding="utf-8")
