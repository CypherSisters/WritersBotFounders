"""The storyboard, against stand-in artists only: no real model is ever called. Run from the package folder:
``python -m pytest tests/test_storyboard.py -q``.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PKG))
sys.path.insert(0, str(PKG / "fixtures"))

import storyboard  # noqa: E402
from book import Book  # noqa: E402
from brainline import BrainDown, BrainSlow  # noqa: E402
from engine import Stopped  # noqa: E402
from frame_book import write_frame_book  # noqa: E402
from make_book import write_book  # noqa: E402

ROOM = 5120                     # 5/8 of an 8,192-token context

ONE = [   # Chapter One, out of order, with a made-up quote, a name not in the book and odd shot words
    {"what": "A big man stands on a boat's deck with a cold pipe \u2014 waiting.", "where": "the Petrel's deck",
     "who": ["Captain Harrow", "Gandalf"], "mood": "heavy, watchful", "shot": "Close-up",
     "frame": "low angle from the dock", "quote": "Captain Harrow stood on the Petrel's deck with his pipe gone cold"},
    {"what": "A woman walks down to the harbor at dawn, scanning the boats.", "where": "the harbor",
     "who": ["Mara Quill", "Mara"], "mood": "cold, hopeful", "shot": "dutch angle",
     "frame": "the boats in a row, she is small at the left", "quote": "Mara Quill came down to the harbor at first light"},
    {"what": "The moon over the water.", "where": "the bay", "who": [], "mood": "calm", "shot": "wide",
     "frame": "high", "quote": "the moon was a silver coin over the bay"},
]
TWO = [
    {"what": "A thin young man finds her on the dock.", "where": "the dock", "who": ["Elias Wren", "the doctor's son"],
     "mood": "awkward", "shot": "medium", "frame": "two figures, the water behind",
     "quote": "Elias Wren, the doctor's son, found her on the dock"},
    {"what": "She signs the ship's book.", "where": "the Petrel", "who": ["Mara"], "mood": "resolved", "shot": "wide",
     "frame": "the book on a barrel", "quote": "the Petrel took on water and rope, and Mara signed the ship's book"},
]


def fenced(panels: list[dict]) -> str:
    return "<think>which moments?</think>\n```json\n" + json.dumps(panels) + "\n```"


class Artist:
    """A stand-in storyboard artist. ``answers`` maps a section heading to the answer text, or to an exception to
    raise. A heading with no answer gets an empty list."""

    def __init__(self, answers: dict | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[list[dict]] = []

    def __call__(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        head = re.search(r"The passage is (.+?)\.\n", messages[-1]["content"]).group(1)
        got = self.answers.get(head, "[]")
        if isinstance(got, BaseException):
            raise got
        return got

    def headings(self) -> list[str]:
        return [re.search(r"The passage is (.+?)\.\n", m[-1]["content"]).group(1) for m in self.calls]


@pytest.fixture()
def book(tmp_path):
    return Book(write_book(tmp_path))


def test_a_board_of_checked_panels(book):
    art = Artist({"Chapter One": fenced(ONE), "Chapter Two": json.dumps({"panels": TWO})})
    seen = []
    board = storyboard.run(book, [1, 2], art, ROOM, progress=lambda d, t, line: seen.append((d, t, line)))
    json.dumps(board)
    assert set(board) == {"made_at", "panels", "sections", "skipped"}
    assert len(art.calls) == storyboard.steps(book, [1, 2]) == 2
    assert [p["id"] for p in board["panels"]] == ["s1-1", "s1-2", "s2-1", "s2-2"]
    first, second = board["panels"][:2]
    for p in board["panels"]:
        assert set(p) == {"id", "section", "heading", "n", "what", "where", "who", "mood", "shot", "frame", "quote", "at"}
        s = book.section(p["section"])
        assert s.text[p["at"]:].startswith(p["quote"]), p          # every quote is hers, at its place
    assert first["quote"] == "Mara Quill came down to the harbor at first light" and first["n"] == 1
    assert first["shot"] == "medium" and first["who"] == ["Mara Quill", "Mara"]          # a bad shot word: medium
    assert second["quote"].startswith("Captain Harrow") and second["shot"] == "close"
    assert second["who"] == ["Captain Harrow"]                                          # Gandalf is not in the book
    assert "\u2014" not in second["what"] and second["heading"] == "Chapter One"
    assert "silver coin" not in json.dumps(board)                                     # the made-up quote is dropped
    two = board["panels"][2:]
    assert [p["quote"].split()[0] for p in two] == ["the", "Elias"]                    # ordered by where they sit
    assert two[1]["who"] == ["Elias Wren", "the doctor's son"]                         # in the section's own words
    assert board["sections"] == {"1": {"sha": book.section(1).sha, "panels": 2},
                                 "2": {"sha": book.section(2).sha, "panels": 2}}
    assert board["skipped"] == []
    assert seen[0] == (0, 2, "Drawing Chapter One") and seen[-1] == (2, 2, "Done")


def test_the_ask_says_what_a_storyboard_artist_does(book):
    art = Artist()
    storyboard.run(book, [1], art, ROOM)
    system, user = art.calls[0][0]["content"], art.calls[0][1]["content"]
    assert "storyboard artist" in system and "notes for pictures" in system and "never write or rewrite" in system
    assert "2 to 5 moments" in user and "JSON only" in user and "5 to 15 words copied exactly" in user
    assert all(k in user for k in ('"what"', '"where"', '"who"', '"mood"', '"shot"', '"frame"', '"quote"'))
    assert "Mara Quill came down to the harbor" in user and "Elias Wren" in user        # the passage, and the cast
    assert "\u2014" not in system + user


def test_a_note_that_copies_her_words_is_not_a_panel(book):
    copied = [{"what": "Her father had pressed the key into her hand the night he died, she remembers.",
               "where": "the harbor", "who": [], "mood": "grief", "shot": "close",
               "frame": "He was a big man, slow to speak, and the whole town owed him money",
               "quote": "Mara Quill came down to the harbor at first light"},
              {**ONE[1], "frame": "He was a big man, slow to speak, and the whole town owed him money"}]
    board = storyboard.run(book, [1], Artist({"Chapter One": json.dumps(copied)}), ROOM)
    assert len(board["panels"]) == 1 and board["panels"][0]["frame"] == ""
    assert "pressed the key" not in json.dumps(board)


def test_long_notes_are_cut(book):
    long = [{**ONE[1], "what": " ".join(["word"] * 40), "frame": " ".join(["wide"] * 30), "where": "x " * 30}]
    p = storyboard.run(book, [1], Artist({"Chapter One": json.dumps(long)}), ROOM)["panels"][0]
    assert len(p["what"].split()) == 24 and len(p["frame"].split()) == 24 and len(p["where"].split()) <= 16


def test_short_sections_and_the_ebook_wrapping_cost_nothing(tmp_path):
    frame = Book(write_frame_book(tmp_path))
    art = Artist()
    every = [s.n for s in frame.sections]
    board = storyboard.run(frame, every, art, ROOM)
    asked = art.headings()
    assert "Before the first chapter" not in asked and "Chapter II. The Fever" not in asked
    assert len(art.calls) == storyboard.steps(frame, every) == len(asked)
    why = {e["section"]: e["why"] for e in board["skipped"]}
    assert "wrapping" in why[1] and "fewer than 40 words" in why[3]
    assert board["sections"]["1"]["panels"] == 0 and board["sections"]["1"]["sha"] == frame.section(1).sha
    assert all("fewer than 40 words" in why[n] for n in (3, 5, 6, 7))
    assert all("no panel was kept" in why[n] for n in (2, 4, 8))                      # asked, and nothing came back
    for msgs in art.calls:
        assert "Gutenberg" not in msgs[-1]["content"] and "vault:" not in msgs[-1]["content"]


def test_a_section_with_no_panel_left_says_why(book):
    made_up = [{**ONE[2]}, {**ONE[2], "quote": "nothing like this is in her book"}]
    board = storyboard.run(book, [1, 99], Artist({"Chapter One": json.dumps(made_up)}), ROOM)
    assert board["panels"] == []
    assert [e["section"] for e in board["skipped"]] == [1, 99]
    assert "no panel was kept" in board["skipped"][0]["why"] and "no section 99" in board["skipped"][1]["why"]
    assert board["sections"]["1"]["panels"] == 0


def test_a_call_that_fails_skips_the_section_and_the_run_goes_on(book):
    art = Artist({"Chapter One": BrainSlow(), "Chapter Two": BrainDown("down"), "Chapter Three": "no json at all",
                  "Chapter Four": json.dumps([{"what": "Two walk across the salt.", "where": "the flats",
                                               "who": ["Elias"], "mood": "bleak", "shot": "wide", "frame": "far off",
                                               "quote": "They crossed the first of the salt flats at low water"}])})
    saved: dict = {}
    board = storyboard.run(book, [1, 2, 3, 4], art, ROOM, saved=saved)
    assert len(art.calls) == 4
    assert [p["id"] for p in board["panels"]] == ["s4-1"]
    why = {e["section"]: e["why"] for e in board["skipped"]}
    assert set(why) == {1, 2, 3}
    assert "ran out of time" in why[1] and "could not answer" in why[2] and "could not be read" in why[3]
    assert set(board["sections"]) == {"4"}
    assert len(saved) == 1                                        # only the answer that could be read is kept


def test_stop_raises_and_nothing_is_paid_twice(book):
    art = Artist({"Chapter One": json.dumps(ONE)})
    saved: dict = {}
    with pytest.raises(Stopped):
        storyboard.run(book, [1, 2, 3], art, ROOM, saved=saved, should_stop=lambda: len(art.calls) >= 1)
    assert len(art.calls) == 1 and len(saved) == 1
    again = Artist({"Chapter One": "this would be a different answer", "Chapter Two": json.dumps(TWO)})
    board = storyboard.run(book, [1, 2, 3], again, ROOM, saved=json.loads(json.dumps(saved)))
    assert again.headings() == ["Chapter Two", "Chapter Three"]          # Chapter One came from what was saved
    assert [p["id"] for p in board["panels"]] == ["s1-1", "s1-2", "s2-1", "s2-2"]


def test_an_ask_that_raises_stopped_stops_the_run(book):
    with pytest.raises(Stopped):
        storyboard.run(book, [1, 2], Artist({"Chapter One": Stopped()}), ROOM)


def test_the_old_board_keeps_what_was_not_drawn_again(book):
    first = storyboard.run(book, [1, 2], Artist({"Chapter One": json.dumps(ONE), "Chapter Two": json.dumps(TWO)}), ROOM)
    gone = {**first["panels"][0], "id": "s99-1", "section": 99}
    old = {**first, "panels": first["panels"] + [gone],
           "sections": {**first["sections"], "99": {"sha": "x", "panels": 1}},
           "skipped": [{"section": 99, "why": "x"}]}
    art = Artist({"Chapter Two": json.dumps(TWO[:1])})
    board = storyboard.run(book, [2], art, ROOM, old=old)
    assert art.headings() == ["Chapter Two"]
    assert [p["id"] for p in board["panels"]] == ["s1-1", "s1-2", "s2-1"]           # one kept, two replaced
    assert board["panels"][:2] == first["panels"][:2]
    assert board["panels"][2]["quote"].startswith("Elias Wren")
    assert set(board["sections"]) == {"1", "2"} and board["sections"]["2"]["panels"] == 1
    assert board["skipped"] == []                                                    # no section 99 in the book
    down = storyboard.run(book, [1], Artist({"Chapter One": BrainSlow()}), ROOM, old=board)
    assert [p["id"] for p in down["panels"]] == ["s1-1", "s1-2", "s2-1"]           # a failed run keeps what was there
    assert down["sections"] == board["sections"] and down["skipped"][0]["section"] == 1


def test_a_long_section_is_cut_at_paragraph_edges(book):
    art = Artist()
    room = 420
    storyboard.run(book, [1], art, room)
    user = art.calls[0][-1]["content"]
    passage = user.split("<<<\n", 1)[1].split("\n>>>", 1)[0]
    assert storyboard._ask_tokens(art.calls[0]) <= room
    paras = [" ".join(p.split()) for p in book.section(1).story.split("\n\n") if p.strip()]
    got = passage.split("\n\n")
    assert 0 < len(got) < len(paras) and got == paras[:len(got)]                      # whole paragraphs, from the start
    tiny = Artist()                                     # room for one sentence: the first paragraph is cut at a sentence
    small = storyboard._ask_tokens(storyboard._messages("Chapter One", storyboard._bible_names(book), "")) + 30
    storyboard.run(book, [1], tiny, small)
    p = tiny.calls[0][-1]["content"].split("<<<\n", 1)[1].split("\n>>>", 1)[0]
    assert storyboard._ask_tokens(tiny.calls[0]) <= small
    assert p == "Mara Quill came down to the harbor at first light with the silver key in her coat."
    none = Artist()
    board = storyboard.run(book, [1], none, 50)                                        # no room at all: no call
    assert none.calls == [] and "no room" in board["skipped"][0]["why"]


def test_prompt_for_uses_the_notes_only(book):
    board = storyboard.run(book, [1], Artist({"Chapter One": json.dumps(ONE)}), ROOM)
    p = board["panels"][0]
    prompt = storyboard.prompt_for(p)
    assert prompt == ("A woman walks down to the harbor at dawn, scanning the boats. the boats in a row, she is small at "
                      "the left. the harbor. Mood: cold, hopeful.")
    assert p["quote"] not in prompt
    long = storyboard.prompt_for({"what": "a " * 30, "frame": "b " * 30, "where": "c " * 30, "mood": "d"})
    assert len(long.split()) == 60 and long.endswith(".")
    assert storyboard.prompt_for({"what": 'She says "I will never go back there again" softly', "mood": ""}) == \
        "She says softly."
    assert storyboard.prompt_for({}) == ""


def test_lines_for_the_chat(book):
    board = storyboard.run(book, [1, 99], Artist({"Chapter One": json.dumps(ONE)}), ROOM)
    got = storyboard.lines(board)
    assert got[0] == ("Chapter One, panel 1: A woman walks down to the harbor at dawn, scanning the boats. Where: the "
                      "harbor. Who: Mara Quill, Mara. Mood: cold, hopeful. Her words: \"Mara Quill came down to the "
                      "harbor at first light\"")
    assert got[1].startswith("Chapter One, panel 2: A big man stands") and "Who: Captain Harrow." in got[1]
    assert got[2].startswith("Section 99: There is no section 99")
    assert storyboard.lines({}) == ["There is no storyboard for this book yet."]


def test_no_dash_or_banned_word_anywhere_the_module_can_emit():
    for path in (PKG / "storyboard.py", Path(__file__)):
        text = path.read_text(encoding="utf-8")
        assert "\u2014" not in text and "\u2013" not in text, path.name
        assert not re.search(r"\bbeta\b", text, re.I), path.name
    assert not re.search(r"\bthe model\b|\bAI\b", (PKG / "storyboard.py").read_text(encoding="utf-8"))
