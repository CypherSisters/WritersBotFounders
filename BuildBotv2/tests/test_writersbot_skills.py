"""WritersBot's lane: an installed skill's SKILL.md reaches the chat's system message when the turn needs it."""
from pathlib import Path

from puffpai_core.zone2 import persona, skills

SKILL = """---
name: novel-mode
description: How to be the writer's editor-in-chief. Use when the owner says "novel mode", "read my novel", "read my book", or asks about the characters of a manuscript.
---

# Novel mode

You never write the book.
"""


def _state(tmp_path: Path) -> Path:
    (tmp_path / "packages" / "novel-mode").mkdir(parents=True)
    (tmp_path / "packages" / "novel-mode" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    return tmp_path


ROWS = [{"id": "novel-mode", "kind": "skill", "status": "ready"}, {"id": "office", "kind": "tool", "status": "running"}]


def test_a_skill_is_needed_by_its_name_or_its_use_when_phrases(tmp_path):
    state = _state(tmp_path)
    assert "You never write the book." in skills.block(state, ROWS, "start novel mode please")
    assert "You never write the book." in skills.block(state, ROWS, "can you read my book now")
    assert "You never write the book." in skills.block(state, ROWS, "tell me about the characters of a manuscript")
    assert skills.block(state, ROWS, "what is the weather") == ""


def test_a_skill_stays_on_for_recent_turns_and_only_installed_skills_count(tmp_path):
    state = _state(tmp_path)
    assert "Novel mode" in skills.block(state, ROWS, "and the plot?", ["start novel mode", "ok"])
    assert skills.block(state, ROWS, "and the plot?", ["hello"] * 10 + ["start novel mode"] + ["x"] * 7) == ""
    assert skills.block(state, [{"id": "novel-mode", "kind": "skill", "status": "removed"}], "novel mode") == ""
    assert skills.block(state, [{"id": "../novel-mode", "kind": "skill", "status": "ready"}], "novel mode") == ""


def test_the_text_is_capped_and_the_system_message_carries_it_before_the_rules(tmp_path):
    state = _state(tmp_path)
    big = SKILL + ("word " * 5000)
    (state / "packages" / "novel-mode" / "SKILL.md").write_text(big, encoding="utf-8")
    text = skills.block(state, ROWS, "novel mode")
    assert len(text) <= skills.MAX_CHARS + len(skills.HEAD) + 200 and "cut to fit" in text
    msg = persona.system_text("Installed: Manuscript.", "THE RULES", skills=text)
    assert msg.index(skills.HEAD) < msg.index("Installed: Manuscript.") < msg.index("THE RULES")
    assert persona.system_text("Installed: Manuscript.", "THE RULES") is None
