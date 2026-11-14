"""Study a book, against stand-in editors only: no real model is ever called. Run from the package folder:
``python -m pytest tests/test_study.py -q``.
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

import study  # noqa: E402
from book import Book  # noqa: E402
from brainline import BrainSlow, tokens  # noqa: E402
from frame_book import write_frame_book  # noqa: E402
from make_book import write_book  # noqa: E402
from study_book import write_study_book  # noqa: E402

ROOM = 5120                     # 5/8 of an 8,192-token context


@pytest.fixture()
def books(tmp_path):
    """(mine, theirs): her short, talky manuscript beside The Salt Road, which she admires."""
    return Book(write_study_book(tmp_path)), Book(write_book(tmp_path))


def _passages(messages: list[dict]) -> tuple[str, str]:
    user = messages[-1]["content"]
    a = user.split("Passage A, from the book she admires:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
    b = user.split("Passage B, from her manuscript:\n<<<\n", 1)[1].split("\n>>>", 1)[0]
    return a, b


def _few(text: str, n: int = 6) -> str:
    return " ".join(text.split("\n\n")[0].split()[:n])     # exactly as written


class Editor:
    """A stand-in editor. ``how`` picks what kind of answer it gives on each pair; the last ask gets a plain list."""

    def __init__(self, how: str = "good") -> None:
        self.how = how
        self.calls: list[list[dict]] = []

    def __call__(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        user = messages[-1]["content"]
        if "Passage A" not in user:
            return '<think>ordering</think>[{"try": "Let a scene end on a line of talk.", "why": "It leaves the reader leaning in.", "from": "ending"}, {"try": "Vary sentence length.", "why": "Rhythm.", "from": "nonsense"}]'
        if self.how == "slow":
            raise BrainSlow()
        theirs, mine = _passages(messages)
        tq, mq = _few(theirs), _few(mine)
        good = {"try": "Let one long sentence carry the setting before the talk starts.", "why": "It gives the reader a place to stand.",
                "their_quote": tq, "my_quote": mq}
        pointers = [good]
        if self.how == "invent":
            pointers = [{"try": "Slow the opening.", "why": "Room to breathe.", "their_quote": "the moon was a silver coin over the bay",
                         "my_quote": "nothing like this is in her book at all"},
                        {**good, "their_quote": "the moon was a silver coin over the bay"}]
        if self.how == "prose":
            pointers = [{"try": 'Open with something like "The harbour lay grey and still under a low sky that morning."',
                         "why": "Sets the scene.", "their_quote": tq, "my_quote": mq},
                        {"try": "Rewrite it as: She walked to the rail and looked out at the dark water.",
                         "why": "Flow.", "their_quote": tq, "my_quote": mq},
                        good]
        return "<think>hmm</think>" + json.dumps({"theirs_does": "Long sentences \u2014 then a short one.",
                                                  "mine_does": "Short lines, mostly talk.", "pointers": pointers})


# -- measure ---------------------------------------------------------------------------------------------------------

def test_measure_is_sane_and_tells_the_two_styles_apart(books):
    mine, theirs = books
    m, t = study.measure(mine), study.measure(theirs)
    for n in (m, t):
        assert 0 <= n["dialogue"]["share"] <= 1
        assert all(0 <= x <= 1 for x in n["dialogue"]["by_chapter"])
        assert n["words"] > 500 and n["sentences"]["count"] > 50
        assert len(n["sentences"]["by_chapter"]) == n["chapters"] == len(n["chapter_edges"])
        json.dumps(n)
    assert m["sentences"]["mean"] < t["sentences"]["mean"] / 2
    assert m["sentences"]["under_8"] > t["sentences"]["under_8"]
    assert m["dialogue"]["share"] > t["dialogue"]["share"]
    assert m["chapters"] == 4                                   # "Chapter Two, part 1" and "part 2" are one chapter
    assert m["scenes_per_chapter"] > 1 and t["scenes_per_chapter"] == 1
    assert (m["narration"]["person"], m["narration"]["tense"]) == ("first", "present")
    assert (t["narration"]["person"], t["narration"]["tense"]) == ("third", "past")
    assert m["per_1000"]["contractions"] > t["per_1000"]["contractions"]
    assert m["dialogue"]["tag_adverbs"] > 0                     # "she says quietly"
    assert "*" not in json.dumps(m["chapter_edges"])           # italic marks gone, the scene break line never a sentence
    assert t["chapter_edges"][0]["first"].startswith("Mara Quill came down")


def test_measure_leaves_out_the_ebook_wrapping(tmp_path):
    n = study.measure(Book(write_frame_book(tmp_path)))
    text = json.dumps(n).lower()
    assert "gutenberg" not in text and "updated" not in text
    assert n["chapter_edges"][0]["chapter"].startswith("Chapter I")
    assert n["narration"]["person"] == "first"


def test_measure_an_empty_book(tmp_path):
    folder = tmp_path / "Empty"
    (folder / "sections").mkdir(parents=True)
    (folder / "sections" / "01.md").write_text("# One\n\n\n", encoding="utf-8")
    (folder / "manuscript.json").write_text(json.dumps({"title": "Empty", "sections": [{"n": 1, "heading": "One", "file": "01.md"}]}), encoding="utf-8")
    empty = Book(folder)
    n = study.measure(empty)
    assert n["words"] == 0 and n["sentences"]["mean"] == 0 and n["dialogue"]["share"] == 0
    assert study.pairs(empty, empty, ROOM) == [] and study.steps(empty, empty, ROOM) == 0
    report = study.run(empty, empty, Editor(), ROOM)
    assert report["state"] == "done" and report["skipped"] and report["numbers"]["mine"]["words"] == 0


# -- differences -----------------------------------------------------------------------------------------------------

def test_differences_are_plain_lines_with_both_values(books):
    d = study.differences(study.measure(books[0]), study.measure(books[1]))
    assert 1 <= len(d["apart"]) <= 8 and len(d["alike"]) <= 4
    keys = [i["key"] for i in d["apart"]]
    assert "sentences.mean" in keys and "narration.tense" in keys
    for i in d["apart"] + d["alike"]:
        assert set(i) == {"key", "line", "mine", "theirs", "unit"}
        assert not re.search(r"wrong|token|model|heuristic|\bAI\b", i["line"], re.I)
    line = next(i["line"] for i in d["apart"] if i["key"] == "sentences.mean")
    assert line == "Your sentences run 3.5 words on average. Theirs run 12."


def test_a_book_beside_itself_has_nothing_apart(books):
    n = study.measure(books[1])
    d = study.differences(n, n)
    assert d["apart"] == [] and d["alike"]


# -- pairs -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("room", [ROOM, 1500, 1000])
def test_pairs_fit_the_room_and_are_the_same_each_time(books, room):
    mine, theirs = books
    ps = study.pairs(mine, theirs, room)
    assert ps == study.pairs(mine, theirs, room)
    assert {p["kind"] for p in ps} >= {"opening", "dialogue", "ending"}
    for p in ps:
        assert p["label"] and p["mine"]["text"] and p["theirs"]["text"]
        assert mine.section(p["mine"]["section"]) and theirs.section(p["theirs"]["section"])
        for side, book in (("mine", mine), ("theirs", theirs)):
            for para in p[side]["text"].split("\n\n"):              # cut at paragraphs, never mid-paragraph here
                assert para in study.strip(book.section(p[side]["section"]).story)
        msgs = study._pair_messages(p, p["theirs"]["text"], p["mine"]["text"], theirs.title, "x" * study.FOCUS_MAX,
                                    ["a line about the numbers " * 5] * 3, room)[0]
        assert study.ask_tokens(msgs) <= room
    assert next(p for p in ps if p["kind"] == "opening")["theirs"]["text"].startswith("Mara Quill came down")


def test_transition_pair_when_both_books_have_scene_breaks(tmp_path):
    a = Book(write_study_book(tmp_path / "a"))
    b = Book(write_study_book(tmp_path / "b", title="Night Ferry Again"))
    t = next(p for p in study.pairs(a, b, ROOM) if p["kind"] == "transition")
    assert t["mine"]["text"].startswith("Cabin twelve")


def test_pairs_skip_the_ebook_wrapping(tmp_path, books):
    frame = Book(write_frame_book(tmp_path / "f"))
    ps = study.pairs(books[0], frame, ROOM)
    assert ps and all("Gutenberg" not in p["theirs"]["text"] for p in ps)
    assert next(p for p in ps if p["kind"] == "opening")["theirs"]["text"].startswith("On March 1st, 1925")


# -- run -------------------------------------------------------------------------------------------------------------

def test_run_with_a_good_editor(books):
    mine, theirs = books
    ed = Editor()
    seen = []
    report = study.run(mine, theirs, ed, ROOM, progress=lambda d, t, line: seen.append((d, t, line)))
    json.dumps(report)
    assert set(report) == {"state", "mine", "theirs", "focus", "numbers", "apart", "alike", "pairs", "pointers",
                           "skipped", "made_at"}
    assert report["state"] == "done" and report["skipped"] == []
    assert (report["mine"], report["theirs"], report["focus"]) == ("Night Ferry", "The Salt Road", "narrative style and flow")
    assert len(ed.calls) == study.steps(mine, theirs, ROOM) == len(report["pairs"]) + 1
    for e in report["pairs"]:
        assert e["state"] == "done" and len(e["pointers"]) == 1
        ptr = e["pointers"][0]
        assert ptr["their_quote"] in e["theirs"]["text"] and ptr["my_quote"] in e["mine"]["text"]
        assert "\u2014" not in e["theirs_does"]                  # plain() turns a dash from the editor into a comma
    assert report["pointers"] == [{"try": "Let a scene end on a line of talk.", "why": "It leaves the reader leaning in.", "from": "ending"},
                                  {"try": "Vary sentence length.", "why": "Rhythm.", "from": ""}]
    assert seen[-1] == (len(ed.calls), len(ed.calls), "Done")
    for msgs in ed.calls:
        assert study.ask_tokens(msgs) <= ROOM
        assert msgs[0]["content"].startswith("You are a senior fiction editor")
    first = ed.calls[0][-1]["content"]
    assert "Her focus: narrative style and flow." in first and "Never rewrite her sentences" in first
    assert "Passage A" not in ed.calls[-1][-1]["content"] and "Mara Quill came down" not in ed.calls[-1][-1]["content"]


def test_every_ask_fits_a_small_room(books):
    ed = Editor()
    study.run(*books, ed, 1000, focus="how she moves between scenes " * 20)
    assert ed.calls
    assert all(study.ask_tokens(m) <= 1000 for m in ed.calls)
    assert all(sum(tokens(x["content"]) for x in m) <= 1000 for m in ed.calls)


def test_an_invented_quote_is_removed(books):
    report = study.run(*books, Editor("invent"), ROOM)
    for e in report["pairs"]:
        assert len(e["pointers"]) == 1                             # the pointer with no real quote at all is dropped
        assert e["pointers"][0]["their_quote"] == ""               # the invented quote is gone, the real one stays
        assert e["pointers"][0]["my_quote"]
    assert "silver coin" not in json.dumps(report)


def test_a_pointer_that_writes_her_prose_is_dropped(books):
    report = study.run(*books, Editor("prose"), ROOM)
    for e in report["pairs"]:
        assert [p["try"] for p in e["pointers"]] == ["Let one long sentence carry the setting before the talk starts."]
    text = json.dumps(report)
    assert "grey and still" not in text and "looked out at the dark water" not in text


def test_a_slow_editor_skips_the_step_and_the_report_still_comes(books):
    ed = Editor("slow")
    report = study.run(*books, ed, ROOM)
    n = len(report["pairs"])
    assert report["state"] == "done" and n
    assert len(ed.calls) == 2 * n                                    # each pair once more, shorter, then skipped
    for full, short in zip(ed.calls[::2], ed.calls[1::2]):
        assert study.ask_tokens(short) < study.ask_tokens(full)
    assert all(e["state"] == "skipped" for e in report["pairs"])
    assert [s["step"] for s in report["skipped"]] == [p["kind"] for p in report["pairs"]] + ["all"]
    assert report["pointers"] == [] and report["numbers"]["mine"]["words"] > 0 and report["apart"]


def test_resume_pays_nothing_twice(books):
    saved: dict = {}
    first = Editor()
    one = study.run(*books, first, ROOM, saved=saved)
    assert len(saved) == len(first.calls) > 0
    again = Editor()
    two = study.run(*books, again, ROOM, saved=json.loads(json.dumps(saved)))
    assert again.calls == []
    assert {k: v for k, v in two.items() if k != "made_at"} == {k: v for k, v in one.items() if k != "made_at"}


def test_should_stop_between_asks_and_go_on_later(books):
    saved: dict = {}
    ed = Editor()
    report = study.run(*books, ed, ROOM, saved=saved, should_stop=lambda: len(ed.calls) >= 2)
    assert report["state"] == "stopped" and len(ed.calls) == 2 and len(report["pairs"]) == 2
    rest = Editor()
    done = study.run(*books, rest, ROOM, saved=saved)
    assert done["state"] == "done"
    assert len(rest.calls) == study.steps(*books, ROOM) - 2


def test_no_em_dash_anywhere_the_module_can_emit():
    for path in (PKG / "study.py", Path(__file__), PKG / "fixtures" / "study_book.py"):
        assert "\u2014" not in path.read_text(encoding="utf-8"), path.name
    assert not re.search(r"\bthe model\b|\bAI\b", (PKG / "study.py").read_text(encoding="utf-8"))
