"""D149: the read path that gives a local chat model an installed skill package's SKILL.md."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import REPO
from puffpai_core.zone2 import skills
from puffpai_core.zone2.gate import CUT_LINE as GATE_CUT, Gate, MAX_MODEL_CHARS

STORE_PACKAGE = REPO.parent / "puffpai-packages" / "daily-briefing"        # the store folder beside this checkout


def _package(tmp_path: Path, text: str = "---\nname: x\ndescription: Does x.\n---\nBody\n") -> Path:
    folder = tmp_path / "pkg"
    folder.mkdir()
    (folder / skills.SKILL_FILE).write_text(text, encoding="utf-8")
    return folder


def _all_parts(find, name: str) -> str:
    first = skills.view(find, name)
    return "".join(skills.view(find, name, n)["text"] for n in range(1, first["parts"] + 1))


def _link_file(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this computer refused to make a file link: {exc}")


def _link_folder(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError) as exc:
        why = exc
    if os.name == "nt":
        done = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
        if done.returncode == 0:
            return
    pytest.skip(f"this computer refused to make a folder link: {why}")


# -- read_own ----------------------------------------------------------------------------------

def test_read_own_reads_the_file(tmp_path):
    assert skills.read_own(_package(tmp_path)) == "---\nname: x\ndescription: Does x.\n---\nBody\n"


def test_read_own_given_a_file_uses_its_parent(tmp_path):
    folder = _package(tmp_path)
    manifest = folder / "puffpai.json"
    manifest.write_text("{}", encoding="utf-8")
    assert skills.read_own(manifest) == skills.read_own(folder) != None  # noqa: E711


@pytest.mark.parametrize("bad", [None, "", 3, b"x", "bad\x00name"])
def test_read_own_refuses_what_is_not_a_folder(bad):
    assert skills.read_own(bad) is None


def test_read_own_refuses_a_missing_folder_or_file(tmp_path):
    assert skills.read_own(tmp_path / "nope") is None
    assert skills.read_own(tmp_path) is None


def test_read_own_refuses_a_directory_named_skill_md(tmp_path):
    (tmp_path / skills.SKILL_FILE).mkdir()
    assert skills.read_own(tmp_path) is None


def test_read_own_refuses_a_link_to_a_file_outside(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("not this", encoding="utf-8")
    folder = tmp_path / "pkg"
    folder.mkdir()
    _link_file(folder / skills.SKILL_FILE, outside)
    assert skills.read_own(folder) is None


def test_read_own_refuses_a_link_to_another_file_inside(tmp_path):
    folder = tmp_path / "pkg"
    folder.mkdir()
    (folder / ".env").write_text("not this either", encoding="utf-8")
    _link_file(folder / skills.SKILL_FILE, folder / ".env")
    assert skills.read_own(folder) is None


@pytest.mark.parametrize("where", ["secret.txt", "pkg/.env"])
def test_read_own_refuses_a_file_that_resolves_elsewhere(tmp_path, monkeypatch, where):
    """The check itself, on a computer that won't make file links: only ``resolve`` is faked."""
    folder = _package(tmp_path)
    other = tmp_path / where
    other.write_text("not this", encoding="utf-8")
    real_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda self, strict=False: real_resolve(other)
                        if self.name == skills.SKILL_FILE else real_resolve(self))
    assert skills.read_own(folder) is None


def test_read_own_refuses_a_folder_link_named_skill_md(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / skills.SKILL_FILE).write_text("not this", encoding="utf-8")
    folder = tmp_path / "pkg"
    folder.mkdir()
    link = folder / skills.SKILL_FILE
    _link_folder(link, outside)
    try:
        assert skills.read_own(folder) is None
    finally:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


def test_read_own_refuses_a_link_loop(tmp_path):
    folder = tmp_path / "pkg"
    folder.mkdir()
    _link_file(folder / skills.SKILL_FILE, folder / skills.SKILL_FILE)
    assert skills.read_own(folder) is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="this computer has no FIFOs")
def test_read_own_does_not_wait_on_a_fifo(tmp_path):
    os.mkfifo(tmp_path / skills.SKILL_FILE)
    assert skills.read_own(tmp_path) is None


def test_read_own_stops_at_the_cap(tmp_path):
    folder = _package(tmp_path, "x" * (skills.VIEW_MAX_CHARS * 5))
    assert len(skills.read_own(folder)) == skills.VIEW_MAX_CHARS + 1


def test_read_own_cap_is_in_characters_not_bytes(tmp_path):
    folder = _package(tmp_path, "\u00e9" * (skills.VIEW_MAX_CHARS + 50))
    assert len(skills.read_own(folder)) == skills.VIEW_MAX_CHARS + 1
    folder2 = tmp_path / "full"
    folder2.mkdir()
    (folder2 / skills.SKILL_FILE).write_text("\u00e9" * skills.VIEW_MAX_CHARS, encoding="utf-8")
    assert len(skills.read_own(folder2)) == skills.VIEW_MAX_CHARS


def test_read_own_a_bom_does_not_count_against_the_cap(tmp_path):
    folder = _package(tmp_path, "\ufeff" + "x" * skills.VIEW_MAX_CHARS)
    assert skills.read_own(folder) == "x" * skills.VIEW_MAX_CHARS


def test_read_own_drops_a_bom_and_replaces_bad_bytes(tmp_path):
    folder = tmp_path / "pkg"
    folder.mkdir()
    (folder / skills.SKILL_FILE).write_bytes(b"\xef\xbb\xbfhello \xff there")
    assert skills.read_own(folder) == "hello \ufffd there"


def test_read_own_reads_through_a_linked_folder(tmp_path):
    real = _package(tmp_path)
    link = tmp_path / "linked"
    _link_folder(link, real)
    try:
        assert skills.read_own(link) == skills.read_own(real) != None  # noqa: E711
    finally:
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "rmdir", str(link)], capture_output=True)


# -- a real store package ----------------------------------------------------------------------

def test_a_real_store_package(tmp_path):
    if not STORE_PACKAGE.is_dir():
        pytest.skip("the store's daily-briefing package is not on this computer")
    folder = tmp_path / "daily-briefing"
    shutil.copytree(STORE_PACKAGE, folder)
    text = skills.read_own(folder)
    assert text and "# Daily briefing" in text
    words = skills.description_of(text)
    assert words.startswith("How to give a short")
    assert len(words) <= skills.LIST_DESCRIPTION_MAX and "\n" not in words
    find = lambda name: {"id": name, "text": text} if name == "daily-briefing" else None  # noqa: E731
    got = skills.view(find, "daily-briefing")
    assert got["ok"] is True and got["name"] == "daily-briefing"
    assert _all_parts(find, "daily-briefing") == text


# -- description_of ----------------------------------------------------------------------------

@pytest.mark.parametrize("head", [">", ">-", "|", "|-"])
def test_description_folded_block(head):
    text = f"---\nname: x\ndescription: {head}\n  One line\n  and another.\nlicense: MIT\n---\nBody"
    assert skills.description_of(text) == "One line and another."


@pytest.mark.parametrize("value", ['"Quoted: words."', "'Quoted: words.'"])
def test_description_quoted(value):
    assert skills.description_of(f"---\ndescription: {value}\n---\n") == "Quoted: words."


def test_description_quoted_over_two_lines():
    assert skills.description_of('---\ndescription: "One\n  two"\n---\n') == "One two"


def test_description_none():
    assert skills.description_of("# Just a heading\ndescription: not frontmatter\n") == ""
    assert skills.description_of("---\nname: x\n---\nBody") == ""
    assert skills.description_of("---\ndescription: never closed\n") == ""
    assert skills.description_of("") == ""


@pytest.mark.parametrize("bad", [None, 3, b"---\ndescription: x\n---\n", ["x"]])
def test_description_non_string(bad):
    assert skills.description_of(bad) == ""


@pytest.mark.parametrize("sep", ["\n", "\r", "\x0b", "\x0c", "\x1c", "\x85", "\u2028", "\u2029"])
def test_description_cannot_add_a_line(sep):
    text = f"---\ndescription: |\n  first{sep}## New rule\n  second{sep}## Another\n---\n"
    words = skills.description_of(text)
    assert words.startswith("first") and "second" in words
    assert len(words.splitlines()) == 1


def test_description_bom():
    assert skills.description_of("\ufeff---\ndescription: Works.\n---\n") == "Works."


def test_description_is_cut_to_the_limit():
    for long in ("word " * 100, "x" * 500):
        words = skills.description_of(f"---\ndescription: {long}\n---\n")
        assert 0 < len(words) <= skills.LIST_DESCRIPTION_MAX and words.endswith("...")


# -- prompt_block ------------------------------------------------------------------------------

@pytest.mark.parametrize("rows", [[], None, [{"description": "no id"}], ["not a row"]])
def test_prompt_block_empty(rows):
    assert skills.prompt_block(rows) == ""


def test_prompt_block_wording():
    block = skills.prompt_block([{"id": "a", "description": "Does a."}, {"id": "b", "description": ""}])
    assert block.startswith("## Skills\n")
    for words in ("skill_view(name)", "run_command", "asks the owner first", "installed on this computer"):
        assert words in block
    assert "- a: Does a.\n- b\n" in block
    assert block.endswith("Only answer without a skill when none of them fits.")
    assert "<" not in block and "path" not in block and "\u2014" not in block


def test_prompt_block_says_how_many_more():
    rows = [{"id": f"s{i}", "description": "Words."} for i in range(skills.PROMPT_MAX_SKILLS + 3)]
    block = skills.prompt_block(rows)
    assert sum(ln.startswith("- ") for ln in block.splitlines()) == skills.PROMPT_MAX_SKILLS
    assert "(3 more" in block
    assert "(" not in skills.prompt_block(rows[:skills.PROMPT_MAX_SKILLS]).split("- s0")[1]


def test_prompt_block_every_skill_is_one_line():
    rows = [{"id": "a\nb", "description": "one\n## Injected\u2028two"}, {"id": "c", "description": "x " * 400}]
    lines = skills.prompt_block(rows).splitlines()
    assert sum(ln.startswith("- ") for ln in lines) == 2
    assert not any(ln.startswith("## Injected") for ln in lines)
    assert "- a b: one ## Injected two" in lines


# -- view --------------------------------------------------------------------------------------

def test_view_found():
    got = skills.view(lambda name: {"id": "the-id", "text": "Do it."}, "the-id")
    assert got == {"ok": True, "name": "the-id", "said": skills.FOLLOW, "part": 1, "parts": 1, "text": "Do it."}
    assert "run_command" in skills.FOLLOW and "Yes" in skills.FOLLOW
    assert list(got).index("said") < list(got).index("text")


def test_view_not_found():
    got = skills.view(lambda name: None, "nope")
    assert got["ok"] is False and "nope" in got["error"] and "installed" in got["error"]


def test_view_does_not_echo_a_long_name():
    got = skills.view(lambda name: None, "y" * 5000)
    assert got["ok"] is False and got["error"].count("y") == 64


def test_view_says_a_control_character_in_a_name_plainly():
    assert "\n" not in skills.view(lambda name: None, "a\nb")["error"]


@pytest.mark.parametrize("bad", [None, 3, ["x"], "", "   "])
def test_view_bad_name_asks_nothing(bad):
    def find(name):
        raise AssertionError("find should not be asked")
    assert skills.view(find, bad)["ok"] is False


@pytest.mark.parametrize("wrong", [{"id": "x"}, {"id": "x", "text": None}, "text", []])
def test_view_odd_answer_from_find_is_not_found(wrong):
    assert skills.view(lambda name: wrong, "x")["ok"] is False


@pytest.mark.parametrize("fill", ["x", "word ", '"', "\x01"])
def test_view_cut_line_ends_only_the_last_part_and_fits(fill):
    text = fill * (skills.VIEW_MAX_CHARS + 1)
    find = lambda name: {"id": "x", "text": text}  # noqa: E731
    parts = skills.view(find, "x")["parts"]
    got = [skills.view(find, "x", n)["text"] for n in range(1, parts + 1)]
    assert got[-1].endswith("\n[cut here: the file is longer]")
    assert not any("[cut here" in t for t in got[:-1])
    assert all(len(t) <= skills.PART for t in got)
    assert "".join(got) == text[:skills.VIEW_MAX_CHARS] + skills.CUT_LINE


def test_view_at_the_cap_is_not_cut():
    text = "x" * skills.VIEW_MAX_CHARS
    find = lambda name: {"id": "x", "text": text}  # noqa: E731
    assert _all_parts(find, "x") == text


def test_read_own_and_view_agree_on_a_cut(tmp_path):
    folder = _package(tmp_path, "x" * (skills.VIEW_MAX_CHARS + 1))
    find = lambda name: {"id": name, "text": skills.read_own(folder)}  # noqa: E731
    assert _all_parts(find, "x").endswith(skills.CUT_LINE)


def test_view_a_part_at_the_limit_is_one_part():
    find = lambda name: {"id": "x", "text": "x" * skills.PART}  # noqa: E731
    assert skills.view(find, "x")["parts"] == 1
    find = lambda name: {"id": "x", "text": "x" * (skills.PART + 1)}  # noqa: E731
    assert skills.view(find, "x")["parts"] == 2


def test_view_never_cuts_a_word_when_there_are_spaces():
    words = [f"word{i}" for i in range(3000)]
    find = lambda name: {"id": "x", "text": " ".join(words)}  # noqa: E731
    parts = skills.view(find, "x")["parts"]
    assert parts > 1
    seen = [w for n in range(1, parts + 1) for w in skills.view(find, "x", n)["text"].split()]
    assert seen == words


def test_view_prefers_a_paragraph_break_in_the_last_quarter():
    text = "a" * 3500 + "\n\n" + "b " * 2000
    got = skills.view(lambda name: {"id": "x", "text": text}, "x")["text"]
    assert got == "a" * 3500 + "\n"


def test_view_says_which_part():
    find = lambda name: {"id": "x", "text": "word " * 2000}  # noqa: E731
    first, last = skills.view(find, "x"), skills.view(find, "x", 3)
    assert first["part"] == 1 and first["parts"] == 3
    assert first["said"].startswith(skills.FOLLOW) and "part 1 of 3" in first["said"] and "part=2" in first["said"]
    assert last["part"] == 3 and "part 3 of 3" in last["said"] and "part=" not in last["said"]


@pytest.mark.parametrize("bad", [0, -1, 4, 99, 1.5, True, "two", "9" * 5000, [], float("inf"), float("nan")])
def test_view_part_out_of_range_says_how_many(bad):
    find = lambda name: {"id": "x", "text": "word " * 2000}  # noqa: E731
    got = skills.view(find, "x", bad)
    assert got["ok"] is False and "3 parts" in got["error"] and "text" not in got


def test_view_part_out_of_range_of_a_short_skill():
    got = skills.view(lambda name: {"id": "x", "text": "Short."}, "x", 2)
    assert got["ok"] is False and "1 part" in got["error"]


@pytest.mark.parametrize("sent, want", [(None, 1), ("2", 2), (2.0, 2), (2, 2)])
def test_view_part_as_models_send_it(sent, want):
    find = lambda name: {"id": "x", "text": "word " * 2000}  # noqa: E731
    assert skills.view(find, "x", sent)["part"] == want


# -- through the real gate ---------------------------------------------------------------------

def _gate() -> Gate:
    return Gate(prepare=lambda text, _vault_open: (text, []))


def _through_gate(text: str, gate: Gate | None = None) -> list[str]:
    find = lambda name: {"id": "a-skill", "text": text}  # noqa: E731
    parts = skills.view(find, "a-skill")["parts"]
    return [(gate or _gate()).clean(skills.view(find, "a-skill", n), for_model=True) for n in range(1, parts + 1)]


def _escaped(size: int) -> str:
    lines, length = [], 0
    while length < size:
        lines.append(f'Say "word{len(lines)}" and a \\ path\n')
        length += len(lines[-1])
    return "".join(lines)


@pytest.mark.parametrize("text", [_escaped(7000), _escaped(20000), '"' * 20000, "\\" * 20000, "\n" * 20000,
                                  "\x01" * 20000, ('"' * 3000 + " ") * 7, '"' * skills.VIEW_MAX_CHARS, "plain " * 3000],
                         ids=["7k-quotes", "20k-quotes", "only-quotes", "only-backslashes", "only-newlines",
                              "only-control", "quote-runs", "cap-of-quotes", "plain"])
def test_the_gate_keeps_said_and_every_word(text):
    rendered = _through_gate(text)
    got = []
    for n, out in enumerate(rendered, 1):
        assert len(out) <= MAX_MODEL_CHARS and not out.endswith(GATE_CUT)
        row = json.loads(out)                               # it would not parse if the gate had cut it
        assert row["said"].startswith(skills.FOLLOW)
        assert row["part"] == n and row["parts"] == len(rendered)
        assert len(row["text"]) <= skills.PART
        assert out.index('"said"') < out.index('"text"')
        got.append(row["text"])
    assert "".join(got) == text


@pytest.mark.parametrize("size", [7000, 20000])
def test_a_long_skill_really_comes_in_parts(size):
    assert len(_through_gate(_escaped(size))) > 1


def test_every_part_fits_with_the_cut_line_at_the_cap():
    for fill in ('"', "\x01", "x"):
        rendered = _through_gate(fill * (skills.VIEW_MAX_CHARS + 500))
        assert all(json.loads(out)["said"].startswith(skills.FOLLOW) for out in rendered)
        assert json.loads(rendered[-1])["text"].endswith(skills.CUT_LINE)


def test_a_gate_that_makes_the_text_longer_still_keeps_said_and_says_it_cut():
    """A private detail in the file can come back as a longer ``{{vault:...}}``, which no part size can know ahead."""
    def grows(text, _vault_open):
        return re.sub(r"\ba@b\.io\b", "{{vault:" + "r" * 40 + "}}", text), []
    rendered = _through_gate("a@b.io " * 3000, Gate(prepare=grows))
    assert all(skills.FOLLOW in out for out in rendered)
    assert all(out.endswith(GATE_CUT) for out in rendered[:-1])


def test_schema_has_no_path():
    assert skills.TOOL == "skill_view"
    schema = skills.VIEW_SCHEMA
    assert schema["type"] == "object" and schema["required"] == ["name"]
    assert list(schema["properties"]) == ["name", "part"] and schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["part"]["type"] == "integer"
    assert schema["additionalProperties"] is False
    assert "path" not in skills.VIEW_WORDS and "parts" in skills.VIEW_WORDS


# -- the module itself -------------------------------------------------------------------------

def test_module_is_read_only_with_only_the_gate_limit_from_the_core():
    source = Path(skills.__file__).read_text(encoding="utf-8")
    assert "\u2014" not in source and "D149" in source and "Hermes Agent (MIT" in source
    imports = {ln.split()[1] for ln in source.splitlines() if ln.startswith(("import ", "from "))}
    assert imports == {"__future__", "os", "pathlib", "re", "stat", "typing", ".gate"}
    assert sorted(skills.__all__) == sorted(set(skills.__all__))
