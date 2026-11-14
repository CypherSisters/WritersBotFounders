"""The storyboard through the server: her press, the panels, files other programs write, and the paint hand-over."""
import json
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "fixtures"))

import make_book  # noqa: E402
import storyboard  # noqa: E402
import test_server as ts  # noqa: E402
from test_server import TITLE, Box, call, q, tool  # noqa: E402


@pytest.fixture()
def desk(tmp_path, monkeypatch):
    asked: list[str] = []
    real = ts.answer

    def answer(system, user):
        if "storyboard artist" in system:
            asked.append(user)
            words = user.split("<<<\n", 1)[1].split("\n>>>", 1)[0].split()
            return [{"what": "A woman stands at a doorway looking out", "where": "a farmhouse", "who": [], "mood": "tense",
                     "shot": "wide", "frame": "seen from low down", "quote": " ".join(words[a:a + 8])} for a in (0, 20)]
        return real(system, user)

    monkeypatch.setattr(ts, "answer", answer)
    box = Box(tmp_path)
    make_book.write_book(box.shelf)
    call(box.port, "POST", "/api/open", {"title": TITLE})
    box.asked = asked
    box.folder = next(p for p in box.shelf.iterdir() if p.is_dir())
    yield box
    box.stop()


def view(box):
    return call(box.port, "GET", "/api/board?" + q(title=TITLE))


def drawn(box, sections="1,2,3"):
    code, _ = call(box.port, "POST", "/api/board", {"title": TITLE, "sections": sections})
    assert code == 200
    end = time.time() + 60
    while time.time() < end:
        _c, v = view(box)
        if not v["running"] and v["board"]:
            return v
        time.sleep(0.1)
    raise AssertionError("the storyboard did not finish")


def test_nothing_starts_without_her_yes_and_a_get_writes_nothing(desk):
    before = {str(p): p.stat().st_mtime_ns for p in desk.shelf.rglob("*") if p.is_file()}
    for path in ("/api/board?" + q(title=TITLE), "/api/board/plan?" + q(title=TITLE), "/api/board/picture?" + q(title=TITLE, id="s1-1")):
        call(desk.port, "GET", path)
    assert before == {str(p): p.stat().st_mtime_ns for p in desk.shelf.rglob("*") if p.is_file()}
    for said in ("yes", 1, "true", None):
        text, _err = tool(desk.port, "desk_storyboard", title=TITLE, confirm=said)
        assert "has not started" in text
    assert not desk.asked and not view(desk)[1]["running"]


def test_her_press_draws_panels_with_her_own_words_and_a_second_press_costs_nothing(desk):
    v = drawn(desk)
    panels = v["board"]["panels"]
    assert panels and len(desk.asked) == 3
    import book as bk
    b = bk.Book(desk.folder)
    for p in panels:
        assert p["quote"] in " ".join(b.section(p["section"]).text.split())
    assert v["sections"] and v["can_paint"] and v["changed"] == []
    n = len(desk.asked)
    drawn(desk)
    assert len(desk.asked) == n
    text, _err = tool(desk.port, "desk_storyboard_report", title=TITLE)
    assert "notes for pictures" in text and panels[0]["what"] in text


def test_a_changed_chapter_is_named(desk):
    drawn(desk)
    _c, sec = call(desk.port, "GET", "/api/section?" + q(title=TITLE, n=2))
    code, _ = call(desk.port, "POST", "/api/save", {"title": TITLE, "n": 2, "text": sec["text"] + "\n\nAnother line here.", "sha": sec["sha"]})
    assert code == 200
    assert view(desk)[1]["changed"] == [2]


@pytest.mark.parametrize("name, body", [
    ("pictures.json", '{"pictures": ["x", 1, null, {"panel": 5, "id": 7}, {"panel": "s1-1", "id": "../../secret", "state": "ready"}]}'),
    ("pictures.json", "[" * 100000 + "]" * 100000),
    ("pictures.json", "not json"),
    ("storyboard.json", '{"panels": 5, "asked_at": 3}'),
    ("storyboard.json", '{"panels": ["x", null], "asked_at": "never"}'),
    ("storyboard.json", "[" * 100000 + "]" * 100000),
], ids=["odd rows", "deep pictures", "not json", "wrong types", "odd panels", "deep storyboard"])
def test_a_bad_file_from_another_program_never_breaks_the_page(desk, name, body):
    drawn(desk)
    (desk.folder / name).write_text(body, encoding="utf-8")
    code, v = view(desk)
    assert code == 200 and v["board"]["panels"]
    assert call(desk.port, "GET", "/api/office?" + q(title=TITLE))[0] == 200
    assert call(desk.port, "GET", "/api/board/picture?" + q(title=TITLE, id="s1-1"))[0] == 400


@pytest.mark.parametrize("body", ['{"panels": ["x", 1, null, [1]]}', "[" * 100000 + "]" * 100000, '{"panels": 5}', "nope"],
                         ids=["odd panels", "deep", "wrong type", "not json"])
def test_a_bad_board_file_shows_as_no_storyboard(desk, body):
    home = next(desk.data.rglob("books")).iterdir().__next__() / "storyboard"
    home.mkdir(parents=True, exist_ok=True)
    (home / "board.json").write_text(body, encoding="utf-8")
    code, v = view(desk)
    assert code == 200 and not (v["board"] or {}).get("panels")
    assert call(desk.port, "GET", "/api/office?" + q(title=TITLE))[0] == 200


def test_a_picture_comes_only_from_the_books_own_pictures_folder(desk):
    v = drawn(desk)
    pid = v["board"]["panels"][0]["id"]
    pics = desk.folder / "pictures"
    pics.mkdir()
    (pics / "p-1.png").write_bytes(b"\x89PNG good")
    (desk.folder / "secret.png").write_bytes(b"secret")
    rows = [{"id": "p-1", "panel": pid, "state": "ready", "made_at": "2026-01-01T00:00:00Z"}]
    (desk.folder / "pictures.json").write_text(json.dumps({"pictures": rows}), encoding="utf-8")
    assert view(desk)[1]["pictures"][pid]["state"] == "ready"
    for bad in ("../secret", "..\\secret", "p-1\n", "p-1/../../secret", "C:\\secret", "x" * 5000):
        rows[0]["id"] = bad
        (desk.folder / "pictures.json").write_text(json.dumps({"pictures": rows}), encoding="utf-8")
        assert call(desk.port, "GET", "/api/board/picture?" + q(title=TITLE, id=pid))[0] == 400


def test_painting_writes_one_file_of_notes_and_never_her_words(desk):
    v = drawn(desk)
    code, got = call(desk.port, "POST", "/api/board/paint", {"title": TITLE, "panels": "all"})
    assert code == 200 and got["asked"] == len(v["board"]["panels"])
    asked = json.loads((desk.folder / "storyboard.json").read_text(encoding="utf-8"))
    assert len(asked["panels"]) <= 40 and asked["asked_at"]
    for x, p in zip(asked["panels"], v["board"]["panels"]):
        assert x["id"] == p["id"] and p["quote"] not in x["prompt"] and '"' not in x["prompt"]
    _c, after = view(desk)
    assert all(after["pictures"][p["id"]]["state"] == "making" for p in v["board"]["panels"])


def test_a_place_or_mood_that_repeats_her_words_is_left_out():
    class S:
        n, heading, sha = 2, "Chapter Two", "x"
        text = story = ("Elias Wren, the doctor's son, found her on the dock before the tide had turned that grey morning. "
                        "He was twenty-four, thin, and he had read every book in the town twice over by lamplight.")
    got = [{"what": "A young man meets a woman", "where": "Elias Wren, the doctor's son, found her on the dock before the tide",
            "mood": "he had read every book in the town twice", "shot": "wide", "frame": "from the water",
            "quote": "found her on the dock before the tide"}]
    panel = storyboard._panels(got, S, [])[0]
    assert panel["where"] == "" and panel["mood"] == ""
    assert storyboard.prompt_for({"what": 'A man says "You', "frame": "close", "where": "", "mood": "calm"}) == "A man says. close. Mood: calm."


def test_a_painting_that_failed_says_why_in_maps_own_words(desk):
    v = drawn(desk)
    pid = v["board"]["panels"][0]["id"]
    rows = [{"id": "p-1", "panel": pid, "state": "failed", "why": "Not allowed yet: waiting for your Yes on the Bridge page.", "made_at": "2026-01-01T00:00:00Z"}]
    (desk.folder / "pictures.json").write_text(json.dumps({"pictures": rows}), encoding="utf-8")
    _c, after = view(desk)
    assert after["pictures"][pid]["state"] == "failed" and "waiting for your Yes on the Bridge page" in after["paint_note"]
