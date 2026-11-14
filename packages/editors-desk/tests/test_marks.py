"""marks.py: italics and bold kept in a section file as stars, read back the same, and gone for anything that reads."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG))
import marks  # noqa: E402

LINES = [
    "She said *no* and meant it.",
    "It was **never** hers.",
    "***Run,*** he said.",
    "*The Times* had it first, then **The Post**, then ***all of them***.",
    "Plain words, nothing marked.",
    "*a*",
    "Two *quick* small *words* here.",
    "Dashes stay\u2014*like this*\u2014as they are.",
]
AS_IS = ["* * *", "***", "  *  *  *  ", "*", "a * b", "2 * 3 * 4", "5*", "x ** y", "", "   "]


@pytest.mark.parametrize("line", LINES + AS_IS)
def test_round_trip(line):
    assert marks.wrap(marks.runs(line)) == line


@pytest.mark.parametrize("line", AS_IS)
def test_scene_breaks_and_lone_stars_are_words(line):
    got = marks.runs(line)
    assert all(not i and not b for _w, i, b in got)
    assert marks.strip(line) == line


def test_runs_read_the_marks():
    assert marks.runs("She said *no* and **meant** it, ***really***.") == [
        ("She said ", False, False), ("no", True, False), (" and ", False, False), ("meant", False, True),
        (" it, ", False, False), ("really", True, True), (".", False, False)]


def test_strip_keeps_every_line_break_and_scene_break():
    text = "# One\n\nShe said *no*.\n\n* * *\n\n**Later** it rained.\nA second *line*.\n"
    assert marks.strip(text) == "# One\n\nShe said no.\n\n* * *\n\nLater it rained.\nA second line.\n"
    assert marks.strip("2 * 3 * 4") == "2 * 3 * 4"


def test_spaces_at_the_edge_move_outside_the_stars():
    assert marks.wrap([("She said", False, False), (" no ", True, False), ("again.", False, False)]) == "She said *no* again."
    assert marks.wrap([("   ", True, False)]) == "   "
    line = marks.wrap([("a", False, False), (" bold words  ", False, True), ("b", False, False)])
    assert line == "a **bold words**  b"
    assert marks.strip(line) == "a bold words  b"


def test_neighbours_with_the_same_marks_join_and_a_star_inside_stays_plain():
    assert marks.wrap([("one ", True, False), ("two", True, False)]) == "*one two*"
    assert marks.wrap([("2 * 3", True, False)]) == "2 * 3"
    assert marks.wrap([("", True, True), ("x", False, False)]) == "x"


def test_the_two_copies_are_the_same_file():
    other = PKG.parent / "manuscript" / "marks.py"
    assert other.read_bytes() == (PKG / "marks.py").read_bytes()
