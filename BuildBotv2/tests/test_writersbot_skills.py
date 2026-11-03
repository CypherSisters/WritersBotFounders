"""WritersBot's lane: an installed skill's SKILL.md reaches the chat's system message when the turn needs it.

Since the merge of main's D149 the skills come from the same checked list skill_view reads (``Concierge._skills``), so
only an installed skill in use, with its own ``SKILL.md``, counts; D149's own tests cover that list."""
import pytest

from conftest import REPO
from puffpai_core.zone2 import persona, skills
from test_d149_skills import _brain, _install, _skill

SKILL = """---
name: novel-mode
description: How to be the writer's editor-in-chief. Use when the owner says "novel mode", "read my novel", "read my book", or asks about the characters of a manuscript.
---

# Novel mode

You never write the book.
"""

NOVEL_MODE = REPO.parent / "WritersBot" / "packages" / "novel-mode" / "SKILL.md"      # the bot's own, beside this repo


def _found(text: str = SKILL) -> list[dict]:
    return [{"id": "novel-mode", "name": "Novel mode", "text": text}]


def test_a_skill_is_needed_by_its_name_or_its_use_when_phrases():
    assert "You never write the book." in skills.block(_found(), "start novel mode please")
    assert "You never write the book." in skills.block(_found(), "can you read my book now")
    assert "You never write the book." in skills.block(_found(), "tell me about the characters of a manuscript")
    assert skills.block(_found(), "what is the weather") == ""
    assert skills.block([], "novel mode") == ""


def test_a_skill_stays_on_for_recent_turns():
    assert "Novel mode" in skills.block(_found(), "and the plot?", ["start novel mode", "ok"])
    assert skills.block(_found(), "and the plot?", ["hello"] * 10 + ["start novel mode"] + ["x"] * 7) == ""


def test_the_text_is_capped_and_the_system_message_carries_it_before_the_rules():
    text = skills.block(_found(SKILL + ("word " * 5000)), "novel mode")
    assert len(text) <= skills.MAX_CHARS + len(skills.HEAD) + 200 and "cut to fit" in text
    msg = persona.system_text("Installed: Manuscript.", "THE RULES", skills=text)
    assert msg.index(skills.HEAD) < msg.index("Installed: Manuscript.") < msg.index("THE RULES")
    assert persona.system_text("Installed: Manuscript.", "THE RULES") is None


def test_the_real_novel_mode_phrases_are_read_whole():
    if not NOVEL_MODE.is_file():
        pytest.skip("the WritersBot repo is not beside this one")
    text = NOVEL_MODE.read_text(encoding="utf-8")
    meta, _body = skills.parse_frontmatter(text)
    said = skills.phrases(meta.get("description"))
    assert "start novel mode" in said and "some pdfs" in said and "editor's desk" in said
    assert "Rules that never bend" in skills.block([{"id": "novel-mode", "name": "", "text": text}], "check my book")


def test_an_installed_skill_is_in_the_system_message_on_a_real_turn(core, state, drawbridge, tmp_path):
    _install(core, _skill(tmp_path / "src", "seed-swap", 'How to run a seed swap. Use when the owner says "plan the swap"',
                          "Seed swap steps: list the seeds first."))
    brain, rt = _brain(core, state, *[{"text": "Done."}] * 6)
    brain.turn("can you plan the swap for Saturday?")
    assert "Seed swap steps: list the seeds first." in rt.calls[-1]["messages"][0]["content"]
    brain.turn("and who brings tables?")                                       # sticky: her last messages keep it on
    assert "Seed swap steps" in rt.calls[-1]["messages"][0]["content"]
    brain.turn("what is the weather", conversation="other")
    assert "Seed swap steps" not in rt.calls[-1]["messages"][0]["content"]
