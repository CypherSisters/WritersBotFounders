"""D138 part 2 (D16): her own programs, Always allow, desktop apps. Small harmless programs made here, real processes for
command line ones, fakes for every desktop start: no real window, none of her real apps."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

from conftest import wait_for
from zone2_doubles import FakeRuntime, call, make_brain
from test_term_chat import _chat, live  # noqa: F401 - a real Core on free ports
from puffpai_core import wiring
from puffpai_core.consent import card_sha, card_view
from puffpai_core.steward import noshell
from puffpai_core.zone2 import commands, programs
from puffpai_core.zone2.concierge import CORE_TOOL_NAMES

NODE = shutil.which("node")
REPO = Path(__file__).resolve().parents[1]
TOOL = """
import os, sys, time
if "--help" in sys.argv:
    print("usage: {name} [-h] FILE")
    sys.exit(0)
if "slow" in sys.argv:
    print("started", flush=True)
    time.sleep(20)
open("ran-{name}.txt", "w").write(" ".join(sys.argv[1:]))
print("got", sys.argv[1:], os.environ.get("BIRD_PASSWORD"))
"""


def _tool(tmp_path, name="tally") -> Path:
    path = tmp_path / "tools" / f"{name}.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TOOL.replace("{name}", name), encoding="utf-8")
    return path


def _ran(state, name="tally") -> Path:
    return Path(commands.workspace(state)) / f"ran-{name}.txt"


def _added(state, tmp_path, name="tally", always=False) -> dict:
    row = programs.register(state, "cli", path=str(_tool(tmp_path, name)))
    if always:
        programs.set_always_allow(state, row["id"], True)
    return programs.get(state, row["id"])


@pytest.fixture(autouse=True)
def no_real_lists(monkeypatch):
    """Nothing here reads her real Start Menu or registry, or starts a real window."""
    monkeypatch.setattr(programs, "_start_menu", lambda: [])
    monkeypatch.setattr(programs, "_app_paths", lambda reg=None: [])
    monkeypatch.setattr(programs, "_mac", lambda: [])
    monkeypatch.setattr(programs, "_linux", lambda: [])


@pytest.fixture
def windows_started(monkeypatch):
    got = []
    monkeypatch.setattr(programs, "_start_window", lambda target, file="": got.append((target, file)))
    return got


def _brain(state, freshtable, core, script, wait_s=30.0):
    rt = FakeRuntime(*script)
    brain, _i, _r = make_brain(state, freshtable, rt, consent=wiring.core_question(core, wait_s=wait_s))
    records: list = []
    brain.audit = records.append
    return brain, rt, records


def _turn(brain):
    events, got = [], {}
    t = threading.Thread(target=lambda: got.update(brain.turn("do it", emit=events.append, personal=True)), daemon=True)
    t.start()
    return t, events, got


def _answer(core, answer):
    rec = wait_for(lambda: core.consent.list(status="pending"), 20)[0]
    core.consent.answer(rec["consent_id"], answer, "tap", None, by="test", card_sha=card_sha(rec["card"]))
    return rec


CTX = {"keep": str, "her_device": True}
HER = {"audit": lambda r: None, "her_device": True}


# -- adding -------------------------------------------------------------------------------------------------------
def test_add_by_path_reads_its_help_and_keeps_the_full_path(state, tmp_path):
    row = _added(state, tmp_path)
    assert row["kind"] == "cli" and row["command"] == "tally" and row["always_allow"] is False
    assert row["path"] == str(tmp_path / "tools" / "tally.py") and "usage: tally" in row["help"]
    assert programs.ID_RE.match(row["id"])
    assert json.loads((state / "programs.json").read_text(encoding="utf-8"))["programs"][0]["id"] == row["id"]


def test_add_by_a_name_on_path_finds_it_once(state, tmp_path, monkeypatch):
    folder = tmp_path / "onpath"
    folder.mkdir()
    name = "birdcount.cmd" if os.name == "nt" else "birdcount"
    path = folder / name
    path.write_text("", encoding="utf-8")
    path.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder))
    row = programs.register(state, "cli", command="birdcount")
    assert row["path"] == str(path) and row["command"] == "birdcount"


@pytest.mark.parametrize("kw,line", [({"path": "nowhere/x.exe"}, programs.NOT_FOUND),
                                     ({"command": "no-such-program-here"}, "could not find"), ({}, "full path")])
def test_what_is_not_there_is_refused(state, kw, line):
    with pytest.raises(programs.ProgramError, match=line):
        programs.register(state, "cli", **kw)


def test_a_wrong_kind_of_file_is_refused(state, tmp_path, monkeypatch):
    monkeypatch.setattr(programs, "_IS_WINDOWS", True)
    note = tmp_path / "notes.txt"
    note.write_text("x", encoding="utf-8")
    with pytest.raises(programs.ProgramError, match="not a program"):
        programs.register(state, "cli", path=str(note))


@pytest.mark.parametrize("name", ["bash", "python3", "powershell", "python3.12", "node"])
def test_a_shell_is_never_run_to_read_its_help(state, tmp_path, monkeypatch, name):
    def nope(*_a, **_k):
        raise AssertionError("ran a shell for its help")
    monkeypatch.setattr(programs, "read_help", nope)
    row = programs.register(state, "cli", path=str(_tool(tmp_path, name)))
    assert row["help"] == programs.SHELL_NOTE


def test_a_second_add_keeps_her_choices(state, tmp_path):
    row = _added(state, tmp_path, always=True)
    again = programs.register(state, "cli", path=row["path"])
    assert again["id"] == row["id"] and again["always_allow"] is True and len(programs.rows(state)) == 1


def test_subcommands_are_listed_and_a_changing_ones_help_is_never_run(state, monkeypatch):
    asked = []

    def fake(_state, prefix, flags=None):
        asked.append(prefix[-1])
        return "usage: x"
    monkeypatch.setattr(programs, "read_help", fake)
    text = "Commands:\n  list    List them\n  delete  Delete one\n  serve   Start a server\n"
    got = programs.specs_from_help(state, "/x/tally", "tally", text, ["/x/tally"])
    assert [c["command"] for c in got] == ["tally list", "tally delete", "tally serve"] and asked == ["list"]


def test_a_program_that_prints_a_key_in_its_help_keeps_none(state, tmp_path):
    secret = "ppk_" + "Ab1" * 14
    path = tmp_path / "tools" / "leaky.py"
    path.parent.mkdir(parents=True)
    path.write_text(f"print('usage: leaky {secret}')\n", encoding="utf-8")
    assert secret not in programs.register(state, "cli", path=str(path))["help"]


# -- Always allow -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,words", [("recipes", "add soup"), ("invoices", "count march"), ("birds", "note wren")])
def test_a_plain_call_of_always_allow_runs_with_no_card_and_is_logged(state, freshtable, core, tmp_path, name, words):
    row = _added(state, tmp_path, name, always=True)
    brain, rt, records = _brain(state, freshtable, core, [call("run_command", command=f"{name} {words}"), {"text": "Done."}])
    got = brain.turn("do it", personal=True)
    assert got["text"] == "Done." and core.consent.list() == []
    assert _ran(state, name).read_text() == words
    told = json.loads(rt.calls[1]["messages"][-1]["content"])
    assert told["worked"] is True and told["exit_code"] == 0 and words.split()[1] in told["output"]
    assert records == [{"actor": "concierge", "action": "own_program_run", "program": row["id"], "command": name,
                        "how": "always_allow", "arguments": 2}]
    assert words.split()[1] not in json.dumps(records)                       # X14: names and a count, never arguments


def test_by_its_full_path_too(state, tmp_path):
    row = _added(state, tmp_path, always=True)
    words = f'"{row["path"]}" one'
    assert programs.match(state, words)[1] == ["one"] and commands.question(state, {"command": words}, CTX) is None


@pytest.mark.parametrize("char", list(";|&$`<>(){}\n\r"))
def test_every_shell_character_still_asks(state, tmp_path, char):
    row = _added(state, tmp_path, always=True)
    words = f"tally one{char}two"
    assert programs.match(state, words) is None
    q = commands.question(state, {"command": words}, CTX)
    assert q["title"] == commands.TITLE and "argv" not in q
    with pytest.raises(ValueError, match="not approved"):
        commands.run(state, {"command": words}, HER)
    assert not _ran(state).exists() and row["always_allow"] is True


def test_off_means_it_asks_and_a_yes_shows_the_argv(state, freshtable, core, tmp_path):
    row = _added(state, tmp_path)
    brain, _rt, records = _brain(state, freshtable, core, [call("run_command", command="tally soup 'two words'"),
                                                           {"text": "Done."}])
    t, events, _got = _turn(brain)
    rec = wait_for(lambda: core.consent.list(status="pending"), 20)[0]
    lines = card_view(rec["card"], False)["lines"]
    joined = "".join(lines)
    assert rec["scope"] == {"kind": "tool_call", "target": "concierge.run_command"} and "It starts exactly: " in joined
    assert "tally soup 'two words'" in joined and '"two words"' in joined and "as you, outside the Bridge" in lines[0]
    assert sys.executable.replace("\\", "\\\\") in joined.replace("\n", "")
    core.consent.answer(rec["consent_id"], "yes", "tap", None, by="test", card_sha=card_sha(rec["card"]))
    t.join(60)
    assert _ran(state).read_text() == "soup two words"
    assert records[0]["how"] == "asked" and records[0]["program"] == row["id"]
    assert next(e for e in events if e["type"] == "ask")["question"]["consent_id"] == rec["consent_id"]


@pytest.mark.parametrize("answer", ["no", "none"])
def test_a_no_or_no_answer_runs_nothing(state, freshtable, core, tmp_path, answer):
    _added(state, tmp_path)
    brain, rt, records = _brain(state, freshtable, core, [call("run_command", command="tally soup"), {"text": "never"}],
                                wait_s=30.0 if answer == "no" else 0.3)
    t, _e, got = _turn(brain)
    if answer == "no":
        _answer(core, "no")
    t.join(60)
    assert not _ran(state).exists() and records == [] and len(rt.calls) == 1 and got["text"]


def test_no_audit_line_means_it_does_not_run(state, tmp_path):
    _added(state, tmp_path, always=True)

    def broken(_r):
        raise OSError("disk")
    for audit in (broken, None):
        with pytest.raises(programs.ProgramError, match="audit line"):
            commands.run(state, {"command": "tally soup"}, {"audit": audit, "her_device": True})
    assert not _ran(state).exists()


def test_any_owner_caller_but_her_own_device_gets_the_card(state, freshtable, core, tmp_path):
    """An owner API key can chat, but only her paired device says Yes, so Always allow skips nothing for it."""
    _added(state, tmp_path, always=True)
    brain, _rt, records = _brain(state, freshtable, core, [call("run_command", command="tally soup"), {"text": "never"}])
    events, got = [], {}
    t = threading.Thread(target=lambda: got.update(brain.turn("do it", emit=events.append, personal=False)), daemon=True)
    t.start()
    _answer(core, "no")
    t.join(60)
    assert not _ran(state).exists() and records == []
    with pytest.raises(ValueError, match="not approved"):
        commands.run(state, {"command": "tally soup"}, {"audit": lambda r: None})


def test_an_open_yes_skips_the_card_only_for_her_own_device(state, tmp_path, monkeypatch, windows_started):
    row = _desktop(state, tmp_path, monkeypatch)
    _doc(state)
    data = programs._load(state)
    data["open_yes"] = [row["id"]]
    programs._save(state, data)
    ask = {"program": "Letters", "file": "letter.docx"}
    assert programs.open_question(state, ask, {"keep": str})["title"] == "May I open files in Letters?"
    with pytest.raises(programs.ProgramError, match="not approved"):
        programs.open_tool(state, ask, {"audit": lambda r: None})
    assert windows_started == []


def test_always_allow_runs_only_from_chat(state, freshtable, core, tmp_path):
    _added(state, tmp_path, always=True)
    brain, _rt, _records = _brain(state, freshtable, core, [])
    got = brain.call_tool("run_command", {"command": "tally soup"})
    assert not got["ok"] and commands.ONLY_CHAT in got["error"] and not _ran(state).exists()


def test_safe_mode_closes_commands_even_always_allow(state, tmp_path):
    _added(state, tmp_path, always=True)
    for safe in (lambda: True, lambda: 1 / 0):
        ctx = {"keep": str, "audit": lambda r: None, "safe_mode": safe, "her_device": True}
        with pytest.raises(ValueError, match="safe mode"):
            commands.question(state, {"command": "tally soup"}, ctx)
        with pytest.raises(ValueError, match="safe mode"):
            commands.run(state, {"command": "tally soup"}, ctx)
    assert not _ran(state).exists()


def test_it_runs_with_the_clean_environment_and_in_a_job(state, tmp_path, monkeypatch):
    monkeypatch.setenv("BIRD_PASSWORD", "wren-value")
    _added(state, tmp_path, always=True)
    got = commands.run(state, {"command": "tally soup"}, HER)
    assert "wren-value" not in got["output"] and "None" in got["output"]
    monkeypatch.setattr(commands, "COMMAND_SECONDS", 1)
    slow = commands.run(state, {"command": "tally slow"}, HER)
    assert slow["exit_code"] is None and slow["worked"] is False and slow["output"] == "started"
    assert slow["note"].startswith("It was stopped after 1 seconds")


def test_a_wrong_exit_gets_the_failed_note(state, tmp_path):
    path = tmp_path / "tools" / "fails.py"
    path.parent.mkdir(parents=True)
    path.write_text("import sys\nsys.exit(4)\n", encoding="utf-8")
    row = programs.register(state, "cli", path=str(path), name="Fails")
    got = programs.run_cli(row, [], folder=str(tmp_path), asked=True, ctx=HER)
    assert got["exit_code"] == 4 and got["note"] == commands.FAILED_NOTE.format(code=4)


def test_a_program_that_is_gone_is_said_plainly(state, tmp_path):
    row = _added(state, tmp_path, always=True)
    os.remove(row["path"])
    assert programs.match(state, "tally soup") is None                       # then it is an ordinary command: it asks
    assert commands.question(state, {"command": "tally soup"}, CTX)["title"] == commands.TITLE


def test_windows_paths_in_a_plain_call_keep_their_backslashes(state, tmp_path, monkeypatch):
    _added(state, tmp_path)
    monkeypatch.setattr(programs, "_IS_WINDOWS", True)
    assert programs.match(state, 'TALLY C:\\a\\b.txt "two words"')[1] == ["C:\\a\\b.txt", "two words"]


def test_on_linux_and_mac_the_name_is_exact(state, tmp_path, monkeypatch):
    _added(state, tmp_path)
    monkeypatch.setattr(programs, "_IS_WINDOWS", False)
    assert programs.match(state, "TALLY soup") is None and programs.match(state, "tally soup")[1] == ["soup"]


@pytest.mark.parametrize("args", ["plain", "50%", "a^b", "wow!", '"quoted"'])
@pytest.mark.parametrize("ext", [".bat", ".cmd"])
def test_a_batch_file_is_never_a_plain_call_so_it_always_asks(state, tmp_path, monkeypatch, ext, args):
    """Windows runs one only through cmd.exe, a shell: D16 says never a shell, and steward.jobs never starts one."""
    monkeypatch.setattr(noshell, "IS_WINDOWS", True)
    monkeypatch.setattr(programs, "_IS_WINDOWS", True)
    path = tmp_path / f"stew{ext}"
    path.write_text("@echo off\n", encoding="utf-8")
    row = programs.register(state, "cli", path=str(path))
    assert row["help"] == ""                                                 # never run for its help either
    assert programs.match(state, f"stew {args}") is None
    with pytest.raises(programs.ProgramError, match="batch file"):
        programs.set_always_allow(state, row["id"], True)
    assert programs.get(state, row["id"])["always_allow"] is False
    assert str(path) in commands.description(state)                         # a shell finds it by its path


@pytest.mark.parametrize("name", ["cmd", "powershell", "bash", "python", "node", "git", "find"])
def test_always_allow_says_so_when_it_can_start_anything(state, tmp_path, name):
    row = programs.register(state, "cli", path=str(_tool(tmp_path, name)), read_help_now=False)
    got = programs.set_always_allow(state, row["id"], True)
    assert got["warning"] == programs.ALWAYS_ALLOW_WARNING and got["program"]["always_allow"] is True
    assert "warning" not in programs.set_always_allow(state, row["id"], False)


def test_no_warning_for_an_ordinary_program(state, tmp_path):
    assert "warning" not in programs.set_always_allow(state, _added(state, tmp_path)["id"], True)


def test_a_command_can_not_name_the_list(state):
    for words in ("cat programs.json", "type ..\\programs.json", "cat ../programs.jso?", "del PROGRAMS.JSON"):
        with pytest.raises(ValueError, match="vault or key files"):
            commands.question(state, {"command": words}, CTX)


# -- the seal (X10) -----------------------------------------------------------------------------------------------
def _edit(state, fn):
    path = state / "programs.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    fn(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_a_yes_written_into_the_file_by_anything_else_is_no_yes(state, tmp_path):
    row = _added(state, tmp_path)

    def on(data):
        data["programs"][0]["always_allow"] = True
        data["open_yes"] = ["default"]
    _edit(state, on)
    assert programs.get(state, row["id"])["always_allow"] is False
    assert commands.question(state, {"command": "tally soup"}, CTX) is not None
    assert programs._load(state)["open_yes"] == []


def test_a_sealed_program_pointed_somewhere_else_asks_again(state, tmp_path):
    row = _added(state, tmp_path, always=True)
    other = _tool(tmp_path, "other")
    _edit(state, lambda d: d["programs"][0].update(path=str(other)))
    assert programs.get(state, row["id"])["always_allow"] is False


def test_with_no_machine_key_always_allow_stays_off(state, tmp_path, monkeypatch):
    row = _added(state, tmp_path)

    class NoKey:
        def mac(self, *_a):
            raise programs.anchor_mod.AnchorUnavailable("no_machine_key")

        def check(self, *_a):
            return False
    monkeypatch.setattr(programs.anchor_mod, "for_state", lambda _s: NoKey())
    with pytest.raises(programs.ProgramError, match="no machine key"):
        programs.set_always_allow(state, row["id"], True)
    assert programs.get(state, row["id"])["always_allow"] is False
    assert programs.register(state, "cli", path=str(_tool(tmp_path, "second")))["kind"] == "cli"    # adding still works


# -- removing -----------------------------------------------------------------------------------------------------
def test_remove_takes_it_and_its_yes_away(state, tmp_path):
    row = _added(state, tmp_path, always=True)
    assert programs.remove(state, row["id"]) is True and programs.remove(state, row["id"]) is False
    assert programs.rows(state) == [] and programs.match(state, "tally soup") is None


# -- the brain sees them ------------------------------------------------------------------------------------------
def test_the_brain_sees_them_with_no_registry_row(state, freshtable, core, tmp_path, monkeypatch):
    row = _added(state, tmp_path)
    exe = tmp_path / "Letters.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(programs, "_ok_kind", lambda _p, _k: True)
    programs.register(state, "desktop", path=str(exe))
    brain, rt, _records = _brain(state, freshtable, core, [call("list_installed"), {"text": "Done."}])
    brain.turn("what is installed")
    prompt = rt.seen(0)
    assert f"program:{row['id']}: tally (program, ready)" in prompt and "Letters (desktop, ready)" in prompt
    tools = {t["function"]["name"]: t["function"]["description"] for t in rt.calls[0]["tools"]}
    assert "tally ... (your own program tally" in tools["run_command"] and "open_in_program" in tools
    listed = json.loads(rt.calls[1]["messages"][-1]["content"])
    assert {"program", "desktop"} <= {r["kind"] for r in listed}


def test_an_empty_list_changes_nothing_and_spends_no_tool(state, freshtable, core):
    brain, rt, _records = _brain(state, freshtable, core, [{"text": "hi"}])
    brain.turn("hello")
    names = [t["function"]["name"] for t in rt.calls[0]["tools"]]
    assert "open_in_program" not in names and "run_program" not in names and "Her own programs" not in str(rt.calls[0]["tools"])
    assert "run_program" not in CORE_TOOL_NAMES


# -- open_in_program ----------------------------------------------------------------------------------------------
def _desktop(state, tmp_path, monkeypatch, name="Letters"):
    exe = tmp_path / f"{name}.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(programs, "_ok_kind", lambda _p, _k: True)
    return programs.register(state, "desktop", path=str(exe))


def _doc(state, name="letter.docx") -> Path:
    path = state / "data" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("words", encoding="utf-8")
    return path


def test_open_asks_once_per_program(state, freshtable, core, tmp_path, monkeypatch, windows_started):
    row = _desktop(state, tmp_path, monkeypatch)
    doc, other = _doc(state), _doc(state, "stew.docx")
    ask = {"program": "Letters", "file": "letter.docx"}
    brain, rt, records = _brain(state, freshtable, core, [call("open_in_program", **ask), {"text": "Open."}])
    t, events, _got = _turn(brain)
    rec = wait_for(lambda: core.consent.list(status="pending"), 20)[0]
    assert rec["scope"] == {"kind": "tool_call", "target": "concierge.open_in_program"}
    seen = card_view(rec["card"], False)
    assert seen["title"] == "May I open files in Letters?" and f"File: {doc}" in seen["lines"]
    assert "Program: Letters" in seen["lines"] and "without asking again" in seen["lines"][0]
    core.consent.answer(rec["consent_id"], "yes", "tap", None, by="test", card_sha=card_sha(rec["card"]))
    t.join(60)
    assert windows_started == [(row["path"], str(doc))]
    assert programs._load(state)["open_yes"] == [row["id"]]
    assert records == [{"actor": "concierge", "action": "own_program_opened", "program": row["id"]}]
    # the second time, and another file: no card
    brain._runtime = FakeRuntime(call("open_in_program", program="Letters", file="stew.docx"), {"text": "Open."})
    brain.turn("again", personal=True)
    assert windows_started[1] == (row["path"], str(other)) and len(core.consent.list()) == 1


@pytest.mark.parametrize("answer", ["no", "none"])
def test_open_with_a_no_or_no_answer_opens_nothing(state, freshtable, core, tmp_path, monkeypatch, windows_started, answer):
    _desktop(state, tmp_path, monkeypatch)
    _doc(state)
    brain, rt, _r = _brain(state, freshtable, core, [call("open_in_program", program="Letters", file="letter.docx"),
                                                     {"text": "never"}], wait_s=30.0 if answer == "no" else 0.3)
    t, _e, _g = _turn(brain)
    if answer == "no":
        _answer(core, "no")
    t.join(60)
    assert windows_started == [] and programs._load(state)["open_yes"] == [] and len(rt.calls) == 1


def test_the_default_app_is_one_yes_shared_by_every_file(state, tmp_path, monkeypatch, windows_started):
    _desktop(state, tmp_path, monkeypatch)
    doc, other = _doc(state), _doc(state, "two.pdf")
    audit = {"audit": lambda r: None, "keep": str, "her_device": True}
    assert programs.open_question(state, {"file": "letter.docx"}, audit)["title"] == \
        "May I open files in its own app?"
    with pytest.raises(programs.ProgramError, match="not approved"):
        programs.open_tool(state, {"file": "letter.docx"}, audit)
    assert programs.open_tool(state, {"file": "letter.docx"}, dict(audit, said_yes=True))["in"] == "its own app"
    assert programs.open_question(state, {"file": "two.pdf"}, audit) is None
    programs.open_tool(state, {"file": "two.pdf"}, audit)
    assert windows_started == [("", str(doc)), ("", str(other))]


def test_each_program_has_its_own_yes(state, tmp_path, monkeypatch):
    _desktop(state, tmp_path, monkeypatch)
    _desktop(state, tmp_path, monkeypatch, "Sheets")
    _doc(state)
    data = programs._load(state)
    data["open_yes"] = [programs._desktop_named(state, "Letters")["id"]]
    programs._save(state, data)
    assert programs.open_question(state, {"program": "Letters", "file": "letter.docx"}, CTX) is None
    assert programs.open_question(state, {"program": "Sheets", "file": "letter.docx"}, CTX) is not None


@pytest.mark.parametrize("file,line", [("run.exe", "opening it would run it"), ("macro.js", "opening it would run it"),
                                       ("nope.docx", "no file")])
def test_a_program_file_never_opens_in_its_default_app(state, tmp_path, monkeypatch, file, line):
    _desktop(state, tmp_path, monkeypatch)
    if file != "nope.docx":
        _doc(state, file)
    with pytest.raises(programs.ProgramError, match=line):
        programs.open_question(state, {"file": file}, CTX)


def test_only_her_folders_open(state, tmp_path, monkeypatch):
    _desktop(state, tmp_path, monkeypatch)
    outside = tmp_path / "elsewhere.docx"
    outside.write_text("x", encoding="utf-8")
    inside_state = state / "settings" / "you.docx"
    inside_state.parent.mkdir(parents=True, exist_ok=True)
    inside_state.write_text("x", encoding="utf-8")
    vault = state / "vault" / "x.docx"
    vault.parent.mkdir(parents=True, exist_ok=True)
    vault.write_text("x", encoding="utf-8")
    climb = "..\\settings\\you.docx" if os.name == "nt" else "../settings/you.docx"
    (state / "data").mkdir(parents=True, exist_ok=True)
    for file in (str(outside), str(inside_state), str(vault), climb):
        with pytest.raises(programs.ProgramError, match="somewhere else"):
            programs.open_question(state, {"file": file}, CTX)


def test_a_linked_folder_and_a_pinned_file_open(state, tmp_path, monkeypatch):
    _desktop(state, tmp_path, monkeypatch)
    linked, pinned = tmp_path / "Recipes", tmp_path / "pinned" / "Novel.docx"
    linked.mkdir()
    pinned.parent.mkdir()
    (linked / "soup.docx").write_text("x", encoding="utf-8")
    pinned.write_text("x", encoding="utf-8")
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "folders.json").write_text(json.dumps({"linked": [str(linked)], "pinned": [str(pinned)]}), encoding="utf-8")
    assert programs.open_question(state, {"file": "soup.docx"}, CTX)["file"].endswith("soup.docx")
    assert programs.open_question(state, {"file": "Novel.docx"}, CTX)["file"].endswith("Novel.docx")


def test_an_added_file_opens_as_a_pinned_one_does_and_no_wider(state, tmp_path, monkeypatch):
    _desktop(state, tmp_path, monkeypatch)
    added, beside = tmp_path / "added" / "Novel.docx", tmp_path / "added" / "Beside.docx"
    added.parent.mkdir()
    added.write_text("x", encoding="utf-8")
    beside.write_text("x", encoding="utf-8")
    vault, own = state / "vault" / "kept.docx", state / "own.docx"
    vault.parent.mkdir(parents=True)
    vault.write_text("x", encoding="utf-8")
    own.write_text("x", encoding="utf-8")
    places = {"data": str(state / "data")}

    def refusals(key):
        (state / "settings").mkdir(parents=True, exist_ok=True)
        (state / "settings" / "folders.json").write_text(json.dumps({**places, key: [str(added), str(vault), str(own)]}), encoding="utf-8")
        return [programs.file_refusal(state, p) for p in (added, beside, vault, own)]
    pinned_got, added_got = refusals("pinned"), refusals("added")
    assert added_got == pinned_got == ["", programs.FILE_OUTSIDE, programs.FILE_OUTSIDE, programs.FILE_OUTSIDE]
    assert programs.resolve_file(state, "Novel.docx") == str(added)            # by its name too
    assert programs.open_question(state, {"file": "Novel.docx"}, CTX)["file"].endswith("Novel.docx")
    keys = tmp_path / "added" / "keys.pem"                                     # the walk and handable refuse it: so does this
    keys.write_text("x", encoding="utf-8")
    for key in ("pinned", "added"):
        (state / "settings" / "folders.json").write_text(json.dumps({**places, key: [str(keys)]}), encoding="utf-8")
        assert programs.file_refusal(state, keys) == programs.FILE_OUTSIDE


def test_open_in_a_program_that_is_not_hers_is_refused(state, tmp_path, monkeypatch):
    _desktop(state, tmp_path, monkeypatch)
    _doc(state)
    with pytest.raises(programs.ProgramError, match="no program by that name"):
        programs.open_question(state, {"program": "Word", "file": "letter.docx"}, CTX)


def test_open_needs_chat_and_closes_in_safe_mode(state, tmp_path, monkeypatch, windows_started):
    _desktop(state, tmp_path, monkeypatch)
    _doc(state)
    with pytest.raises(programs.ProgramError, match="own chat"):
        programs.open_question(state, {"file": "letter.docx"}, {})
    with pytest.raises(ValueError, match="safe mode"):
        programs.open_question(state, {"file": "letter.docx"}, dict(CTX, safe_mode=lambda: True))
    assert windows_started == []


# -- starting a desktop program on each computer, with fakes (never a real window; Mac and Linux did not run for real) --
class _Recorder:
    DEVNULL = subprocess.DEVNULL

    def __init__(self):
        self.calls = []

    def Popen(self, argv, **kw):                       # noqa: N802 - stands in for subprocess.Popen
        self.calls.append((argv, kw))
        return types.SimpleNamespace(pid=1)


def _on(monkeypatch, windows=False, mac=False):
    monkeypatch.setattr(programs, "_IS_WINDOWS", windows)
    monkeypatch.setattr(programs, "_IS_MAC", mac)
    rec = _Recorder()
    monkeypatch.setattr(programs, "subprocess", rec)
    return rec


def test_windows_starts_a_program_by_startfile(state, tmp_path, monkeypatch):
    _on(monkeypatch, windows=True)
    got = []
    monkeypatch.setattr(os, "startfile", lambda *a: got.append(a), raising=False)
    monkeypatch.setattr(programs, "subprocess", types.SimpleNamespace(list2cmdline=lambda a: " ".join(f'"{x}"' for x in a)))
    exe = tmp_path / "Letters.exe"
    exe.write_text("", encoding="utf-8")
    row = programs.register(state, "desktop", path=str(exe))
    doc = _doc(state, "my letter.docx")
    assert programs.launch(state, row["id"]) == {"ok": True}
    programs.launch(state, row["id"], "my letter.docx")
    programs._start_window("", str(doc))
    assert got == [(str(exe),), (str(exe), "open", f'"{doc}"'), (str(doc),)]


def test_a_mac_opens_an_app_with_open_a(state, tmp_path, monkeypatch):
    rec = _on(monkeypatch, mac=True)
    app = tmp_path / "Letters.app"
    app.mkdir()
    row = programs.register(state, "desktop", path=str(app))
    assert row["command"] == "" and row["name"] == "Letters"
    doc = _doc(state)
    programs.launch(state, row["id"], "letter.docx")
    programs._start_window("", str(doc))
    assert [c[0] for c in rec.calls] == [["/usr/bin/open", "-a", str(app), str(doc)], ["/usr/bin/open", str(doc)]]
    assert rec.calls[0][1]["start_new_session"] is True and "shell" not in rec.calls[0][1]


def test_linux_starts_a_desktop_files_own_exec(state, tmp_path, monkeypatch):
    rec = _on(monkeypatch)
    program = tmp_path / "bin" / "letters"
    program.parent.mkdir()
    program.write_text("", encoding="utf-8")
    entry = tmp_path / "letters.desktop"
    entry.write_text(f"[Desktop Entry]\nName=Letters\nType=Application\nExec={program.as_posix()} --new %F\n", encoding="utf-8")
    row = programs.register(state, "desktop", path=str(entry))
    doc = _doc(state)
    programs.launch(state, row["id"], "letter.docx")
    programs.launch(state, row["id"])
    programs._start_window("", str(doc))
    assert [c[0] for c in rec.calls] == [[program.as_posix(), "--new", str(doc)], [program.as_posix(), "--new"],
                                         ["/usr/bin/xdg-open", str(doc)]]


def test_a_desktop_file_that_does_not_say_how_is_refused(state, tmp_path, monkeypatch):
    _on(monkeypatch)
    entry = tmp_path / "broken.desktop"
    entry.write_text("[Desktop Entry]\nName=Broken\n", encoding="utf-8")
    row = programs.register(state, "desktop", path=str(entry))
    with pytest.raises(programs.ProgramError, match="does not say how"):
        programs.launch(state, row["id"])


def test_launch_only_a_desktop_program_of_hers(state, tmp_path, monkeypatch, windows_started):
    row = _added(state, tmp_path)
    with pytest.raises(programs.ProgramError, match="no program by that name"):
        programs.launch(state, row["id"])
    with pytest.raises(programs.ProgramError, match="no program by that name"):
        programs.launch(state, "p00000000")
    exe = tmp_path / "Gone.exe"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setattr(programs, "_ok_kind", lambda _p, _k: True)
    gone = programs.register(state, "desktop", path=str(exe))
    os.remove(exe)
    with pytest.raises(programs.ProgramError, match="any more"):
        programs.launch(state, gone["id"])
    assert windows_started == []


def test_finding_installed_programs_on_each_computer_with_fakes(tmp_path, monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(programs, "_linux_dirs", lambda: [str(tmp_path / "apps"), str(tmp_path / "none")])
    (tmp_path / "apps").mkdir()
    (tmp_path / "apps" / "a.desktop").write_text("[Desktop Entry]\nName=Alpha\nExec=alpha %f\n", encoding="utf-8")
    (tmp_path / "apps" / "b.desktop").write_text("[Desktop Entry]\nName=Hidden\nExec=b\nNoDisplay=true\n", encoding="utf-8")
    assert programs._linux() == [{"name": "Alpha", "path": str(tmp_path / "apps" / "a.desktop")}]
    monkeypatch.setattr(programs, "_mac_dirs", lambda: [str(tmp_path / "Applications")])
    (tmp_path / "Applications" / "Notes.app").mkdir(parents=True)
    assert programs._mac() == [{"name": "Notes", "path": str(tmp_path / "Applications" / "Notes.app")}]
    menu = tmp_path / "menu"
    (menu / "Sub").mkdir(parents=True)
    (menu / "Sub" / "Letters.lnk").write_bytes(b"not a real shortcut")
    (menu / "Uninstall Letters.lnk").write_bytes(b"x")
    monkeypatch.setattr(programs, "_start_menu_dirs", lambda: [str(menu)])
    assert programs._start_menu() == [{"name": "Letters", "path": str(menu / "Sub" / "Letters.lnk")}]
    exe = tmp_path / "WINWORD.EXE"
    exe.write_text("", encoding="utf-8")

    class Reg:
        HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER, REG_EXPAND_SZ = 1, 2, 3

        def OpenKey(self, hive, sub):                  # noqa: N802
            if hive == 2:
                raise OSError
            return self

        def EnumKey(self, _k, i):                      # noqa: N802
            if i:
                raise OSError
            return "WINWORD.EXE"

        def QueryValueEx(self, _k, _v):                # noqa: N802
            return str(exe), 1

        def CloseKey(self, _k):                        # noqa: N802
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False
    assert programs._app_paths(Reg()) == [{"name": "WINWORD", "path": str(exe)}]
    got = programs.installed_desktop(finders=[lambda: [{"name": "b", "path": "/x/b"}, {"name": "A", "path": "/x/a"}],
                                              lambda: 1 / 0, lambda: [{"name": "again", "path": "/x/a"}]])
    assert got == [{"name": "A", "path": "/x/a"}, {"name": "b", "path": "/x/b"}]


# -- the routes ---------------------------------------------------------------------------------------------------
def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(core):
    return core.app.test_client()


@pytest.fixture
def device(core):
    return _bearer(core.tokens.issue_device("Sam's laptop"))


def test_routes_refuse_everyone_but_a_paired_device(core, client, tmp_path):
    from puffpai_core.api import app as app_module
    row = _added(core.state, tmp_path)
    core.terminal_label = f"{app_module.TERMINAL_LABEL} (run d138)"
    who = [({}, 401), (_bearer(core.tokens.issue_owner_key()), 403),
           (_bearer(core.tokens.issue_package_token("freshtable")), 403),
           (_bearer(core.tokens.issue_device(core.terminal_label)), 403)]
    for headers, want in who:
        calls = [client.get("/v1/programs", headers=headers),
                 client.post("/v1/programs", json={"kind": "cli", "path": row["path"]}, headers=headers),
                 client.delete(f"/v1/programs/{row['id']}", headers=headers),
                 client.patch(f"/v1/programs/{row['id']}", json={"always_allow": True}, headers=headers),
                 client.post(f"/v1/programs/{row['id']}/launch", json={}, headers=headers)]
        assert [r.status_code for r in calls] == [want] * 5, headers
    assert programs.get(core.state, row["id"])["always_allow"] is False


def test_routes_add_list_switch_and_remove_with_audit_lines(core, client, device, tmp_path):
    tool = _tool(tmp_path, "python")
    r = client.post("/v1/programs", json={"kind": "cli", "path": str(tool)}, headers=device)
    assert r.status_code == 200 and r.json["program"]["help"] == programs.SHELL_NOTE
    pid = r.json["program"]["id"]
    listed = client.get("/v1/programs", headers=device).json
    assert [p["id"] for p in listed["programs"]] == [pid] and listed["installed_desktop"] == []
    on = client.patch(f"/v1/programs/{pid}", json={"always_allow": True}, headers=device)
    assert on.status_code == 200 and on.json["warning"] == programs.ALWAYS_ALLOW_WARNING
    assert on.json["program"]["always_allow"] is True
    assert client.patch(f"/v1/programs/{pid}", json={"always_allow": "yes"}, headers=device).status_code == 400
    off = client.patch(f"/v1/programs/{pid}", json={"always_allow": False}, headers=device)
    assert off.status_code == 200 and "warning" not in off.json
    assert client.post(f"/v1/programs/{pid}/launch", json={}, headers=device).status_code == 400   # not a desktop one
    assert client.delete(f"/v1/programs/{pid}", headers=device).status_code == 200
    assert client.delete(f"/v1/programs/{pid}", headers=device).status_code == 404
    for bad in ("nope", "p123", "../x"):
        assert client.patch(f"/v1/programs/{bad}", json={"always_allow": True}, headers=device).status_code == 404
    text = (core.state / "logs" / "audit.jsonl").read_text(encoding="utf-8")
    for action in ("program_added", "program_always_allow", "program_removed"):
        assert action in text
    assert str(tool) not in text and tool.name not in text


def test_a_refused_add_says_why(core, client, device):
    r = client.post("/v1/programs", json={"kind": "cli", "path": "nowhere/x.exe"}, headers=device)
    assert r.status_code == 400 and r.json["error"] == programs.NOT_FOUND


def test_a_route_open_starts_it_through_the_fake_and_is_logged(core, client, device, tmp_path, monkeypatch, windows_started):
    row = _desktop(core.state, tmp_path, monkeypatch)
    assert client.post(f"/v1/programs/{row['id']}/launch", json={}, headers=device).json == {"ok": True}
    assert windows_started == [(row["path"], "")]
    assert "own_program_opened" in (core.state / "logs" / "audit.jsonl").read_text(encoding="utf-8")


def test_no_audit_line_on_a_route_means_no_change(core, client, device, tmp_path, monkeypatch, windows_started):
    from puffpai_core.zone3.service import for_app
    row = _added(core.state, tmp_path)
    desk = _desktop(core.state, tmp_path, monkeypatch)

    def broken(_r):
        raise OSError("disk")
    monkeypatch.setattr(for_app(core.app).audit, "append", broken)
    assert client.patch(f"/v1/programs/{row['id']}", json={"always_allow": True}, headers=device).status_code == 503
    assert client.post(f"/v1/programs/{desk['id']}/launch", json={}, headers=device).status_code == 503
    assert programs.get(core.state, row["id"])["always_allow"] is False and windows_started == []
    assert client.delete(f"/v1/programs/{row['id']}", headers=device).status_code == 200       # narrowing still works


def test_safe_mode_closes_adding_on_and_opening_but_never_narrowing(core, client, device, tmp_path, monkeypatch, windows_started):
    row = _added(core.state, tmp_path, always=True)
    desk = _desktop(core.state, tmp_path, monkeypatch)
    monkeypatch.setattr(type(core.caretaker), "in_safe_mode", property(lambda self: True))
    assert client.post("/v1/programs", json={"kind": "cli", "path": row["path"]}, headers=device).status_code == 409
    assert client.patch(f"/v1/programs/{row['id']}", json={"always_allow": True}, headers=device).status_code == 409
    assert client.post(f"/v1/programs/{desk['id']}/launch", json={}, headers=device).status_code == 409
    assert client.get("/v1/programs", headers=device).status_code == 200
    assert client.patch(f"/v1/programs/{row['id']}", json={"always_allow": False}, headers=device).status_code == 200
    assert client.delete(f"/v1/programs/{row['id']}", headers=device).status_code == 200
    assert windows_started == []


# -- the whole way --------------------------------------------------------------------------------------------------
def test_real_chat_runs_always_allow_with_no_card_and_writes_the_real_audit_log(live, tmp_path):
    row = _added(live.state, tmp_path, "invoices", always=True)
    live.brain._runtime = FakeRuntime(call("run_command", command="invoices count"), {"text": "All done."})
    chat = _chat(live)
    assert chat.turn("count them") is True
    assert "All done." in chat.out.getvalue() and commands.TITLE not in chat.out.getvalue()
    assert _ran(live.state, "invoices").read_text() == "count" and live.core.consent.list() == []
    line = next(json.loads(x) for x in (live.state / "logs" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
                if "own_program_run" in x)
    assert line["program"] == row["id"] and line["how"] == "always_allow" and line["arguments"] == 1
    assert "count" not in json.dumps({k: v for k, v in line.items() if k not in ("arguments",)}).replace("arguments", "")


def test_real_chat_safe_mode_closes_it(live, tmp_path, monkeypatch):
    _added(live.state, tmp_path, "birds", always=True)
    monkeypatch.setattr(type(live.core.caretaker), "in_safe_mode", property(lambda self: True))
    live.brain._runtime = FakeRuntime(call("run_command", command="birds note"), {"text": "It did not run."})
    assert _chat(live).turn("note it") is True
    assert not _ran(live.state, "birds").exists()


@pytest.mark.skipif(not NODE, reason="no node here")
def test_the_tile_on_the_developer_page():
    html = (REPO / "packages" / "desktop" / "web" / "index.html").read_text(encoding="utf-8")
    assert html.index("/ui/ownprograms.js") < html.index("/ui/pages/developer.js")
    web = REPO / "packages" / "desktop" / "web" / "ownprograms.js"
    assert subprocess.run([NODE, "--check", str(web)], capture_output=True, timeout=60).returncode == 0
    got = subprocess.run([NODE, str(REPO / "tests" / "js" / "ownprograms.test.js")], capture_output=True, text=True,
                         timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr
    assert "innerHTML" not in web.read_text(encoding="utf-8")


# -- what the verifier found ------------------------------------------------------------------------------------------
BAD_LISTS = ['{"programs":["x"]}', '{"programs":5}', '[1]', 'not json', '{"programs":[{"id":"p12345678","kind":"cli","path":5,'
             '"commands":7,"name":null}]}', '{"open_yes":[1,{}],"programs":null}', '{"programs":[{"id":"p12345678","kind":"cli",'
             '"path":"/x","command":"x","commands":[{"argv":3}]}]}', '"text"', 'null', '']


@pytest.mark.parametrize("text", BAD_LISTS)
def test_a_bad_list_reads_as_no_programs_and_chat_goes_on(state, freshtable, core, text):
    state.mkdir(parents=True, exist_ok=True)
    (state / "programs.json").write_text(text, encoding="utf-8")
    brain, _rt, _records = _brain(state, freshtable, core, [{"text": "hi"}])
    assert brain.turn("hello", personal=True)["text"] == "hi"
    assert commands.question(state, {"command": "x soup"}, CTX)["title"] == commands.TITLE


def test_a_bad_row_beside_a_good_one_leaves_the_good_one(state, tmp_path):
    row = _added(state, tmp_path)

    def add_junk(data):
        data["programs"][:0] = ["x", 5, {"id": "nope"}]
    _edit(state, add_junk)
    assert [r["id"] for r in programs.rows(state)] == [row["id"]]


@pytest.mark.parametrize("name", ["forfiles", "wmic", "tcsh", "python3.11", "python3.12", "node18", "pypy3", "perl5.36"])
def test_these_read_as_a_shell_get_no_help_run_and_get_the_warning(state, tmp_path, monkeypatch, name):
    def nope(*_a, **_k):
        raise AssertionError("ran it for its help")
    monkeypatch.setattr(programs, "read_help", nope)
    row = programs.register(state, "cli", path=str(_tool(tmp_path, name)))
    assert programs.is_shell(row["command"]) and row["help"] == programs.SHELL_NOTE
    assert programs.set_always_allow(state, row["id"], True)["warning"] == programs.ALWAYS_ALLOW_WARNING


def test_an_open_yes_pointed_at_another_program_asks_again(state, tmp_path, monkeypatch):
    row = _desktop(state, tmp_path, monkeypatch)
    _doc(state)
    data = programs._load(state)
    data["open_yes"] = [row["id"]]
    programs._save(state, data)
    assert programs.open_question(state, {"program": "Letters", "file": "letter.docx"}, CTX) is None
    _edit(state, lambda d: d["programs"][0].update(path=str(_tool(tmp_path, "other"))))
    assert programs._load(state)["open_yes"] == []


def test_closing_the_chat_stream_ends_the_running_command(live, tmp_path, monkeypatch):
    from puffpai_core import termchat
    from puffpai_core.api.groups import chat as chat_group
    from puffpai_core.steward import jobs
    monkeypatch.setattr(chat_group, "KEEPALIVE_S", 0.2)
    pid_file = tmp_path / "sleeper.pid"
    tool = tmp_path / "tools" / "sleeper.py"
    tool.parent.mkdir(parents=True)
    tool.write_text(f"import os, time\nopen(r'{pid_file}', 'w').write(str(os.getpid()))\ntime.sleep(25)\n", encoding="utf-8")
    row = programs.register(live.state, "cli", path=str(tool), read_help_now=False)
    programs.set_always_allow(live.state, row["id"], True)
    live.brain._runtime = FakeRuntime(call("run_command", command="sleeper"), {"text": "never seen"})
    api = termchat.Api(live.info)
    resp = api._opener.open(api._request("POST", "/v1/chat/stream", {"text": "go"}), timeout=30)
    assert wait_for(lambda: pid_file.exists() and pid_file.read_text().strip(), 20, 0.05)
    pid = int(pid_file.read_text())
    assert jobs.pid_alive(pid)
    resp.close()
    assert wait_for(lambda: not jobs.pid_alive(pid), 15, 0.1), "the command outlived the closed chat"
