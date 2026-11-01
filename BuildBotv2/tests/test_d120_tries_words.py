"""D120 (lane tries-words): no card promises Docker before a try. The plan's ``tries_in`` is only a label from the
README, and nothing holds a try to it. After the try the line says how it really ran."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from puffpai_core.consent import shown
from puffpai_core.zone3.toolsmith import adopt, find

PLAIN = "Tries as a program on this computer, as you. It could read your files and change PuffPAI."


@pytest.mark.parametrize("label", ["docker", "native", None])
def test_the_plan_line_is_the_same_whatever_the_label(label):
    assert find.tries_line({"tries_in": label}) == PLAIN


@pytest.mark.parametrize("label", ["docker", "native"])
def test_a_hold_card_promises_no_docker(label):
    card = {"name": "A job", "try_holds": [{"candidate": "c", "name": "aa", "url": "u", "tries_in": label,
                                            "drawbridge": {"held": ["a.bin"]}}]}
    line = shown._try_holds_lines(card)[1]
    assert "Docker" not in line and "(tries as a program on this computer, as you)" in line


def test_after_a_try_the_line_says_how_it_really_ran():
    assert adopt._ran_line(SimpleNamespace(compose_project="p1", cand={"tries_in": "native"})) == (
        "It runs in Docker behind the Bridge.")
    assert adopt._ran_line(SimpleNamespace(compose_project="", cand={"tries_in": "docker"})) == PLAIN
