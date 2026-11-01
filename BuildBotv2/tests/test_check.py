"""``puffpai check``: Levels 1-3 on a real run of the package."""

import json

from conftest import HELLO
from puffpai_core.cli import main
from puffpai_core.packages.check import FAIL, NA, PASS, check


def _items(report):
    return {(lv, item): status for lv, item, status, _w in report.items}


def test_hello_tool_passes_level_3(drawbridge, no_brain):
    report = check(HELLO)
    items = _items(report)
    assert report.passed() == 3, report.lines()
    assert items[(2, 12)] == NA                                          # brain not built yet
    for key in [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (2, 8), (2, 9), (2, 10), (2, 11),
                (3, 13), (3, 14), (3, 15), (3, 16)]:
        assert items[key] == PASS, (key, report.lines())
    assert ("Result: every item that ran passes up to Level 3, but 1 item(s) could not be checked (marked ----), "
            "so Level 3 is not fully proven yet.") in report.lines()


def test_without_drawbridge_nothing_passes(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "puffpai_core.zone3.drawbridge", None)      # lane L4's code not here
    report = check(HELLO)
    assert _items(report)[(1, 7)] == FAIL and report.passed() == 0


def test_sample_ask_uses_the_brain(drawbridge, monkeypatch):
    import puffpai_core.packages.check as check_mod
    import puffpai_core.zone2.brain as brain_mod
    asked = []
    monkeypatch.setattr(check_mod, "_check_brain", lambda *a: "the check's own brain")
    monkeypatch.setattr(brain_mod, "sample_ask", lambda tool, ask, brain=None: asked.append((tool, ask, brain))
                        or tool.endswith("say_hello"))
    report = check(HELLO)
    assert _items(report)[(2, 12)] == FAIL
    assert ("hello-tool.say_hello", "say hello to Sam", "the check's own brain") in asked   # while it ran
    assert any("write_note" in w for _l, i, _s, w in report.items if i == 12)


def test_sample_ask_says_why_a_tool_was_not_picked(drawbridge, monkeypatch):
    import puffpai_core.packages.check as check_mod
    import puffpai_core.zone2.brain as brain_mod
    monkeypatch.setattr(check_mod, "_check_brain", lambda *a: object())
    monkeypatch.setattr(brain_mod, "sample_ask", lambda tool, ask, brain=None: {"ok": tool.endswith("say_hello"),
                        "reason": "The brain answered in words and called no tool."})
    words = next(w for _l, i, _s, w in check(HELLO).items if i == 12)
    # write_note has no sample ask, so its description was asked; the line says how to fix that (lane I6a).
    assert ("write_note (The brain answered in words and called no tool. It has no sample ask in "
            "abilities.examples") in words


def test_a_model_that_cant_be_asked_is_said_plainly_not_failed(drawbridge, monkeypatch):
    """Standalone check with no privacy gate or no model: item 12 says why, and is not blamed on the model."""
    import puffpai_core.packages.check as check_mod
    from puffpai_core.zone2.errors import BrainError

    def no_brain(*_a):
        raise BrainError("The privacy gate is not running, so nothing goes to the brain.", "no_gate")
    monkeypatch.setattr(check_mod, "_check_brain", no_brain)
    report = check(HELLO)
    status, words = _items(report)[(2, 12)], next(w for _l, i, _s, w in report.items if i == 12)
    assert status == NA and "could not be asked" in words and "privacy gate" in words
    assert "did not pick" not in words and report.passed() == 3
    assert "Level 3 is not fully proven yet" in "\n".join(report.lines())
    assert "Result: passes Level 3." not in report.lines()


def test_no_model_picked_is_said_plainly(drawbridge, state):
    """The real seam with no model chosen on this bot: the check says so (nothing is sent anywhere)."""
    report = check(HELLO)
    words = next(w for _l, i, _s, w in report.items if i == 12)
    assert _items(report)[(2, 12)] == NA and "no brain is picked yet" in words.lower()


def test_lying_about_tools_and_missing_readme(drawbridge, hello_copy):
    data = json.loads((hello_copy / "puffpai.json").read_text())
    data["abilities"]["changes"] = ["write_note", "delete_everything"]
    (hello_copy / "puffpai.json").write_text(json.dumps(data))
    (hello_copy / "README.md").write_text("# Hello\n", encoding="utf-8")
    items = _items(check(hello_copy))
    assert items[(2, 11)] == FAIL and items[(3, 16)] == FAIL and items[(1, 4)] == PASS


def test_cli_check(drawbridge, no_brain, capsys):
    assert main(["check", str(HELLO)]) == 0
    out = capsys.readouterr().out
    assert "hello-tool 1.0.0 (tool)" in out and "Level 3 Belongs" in out and "skipped: brain not built yet" in out


def test_d55_names_that_sound_like_a_change_warn():
    from puffpai_core.packages.check import change_warnings, suggests_change
    for name in ("clear_list", "/api/add", "deleteItem", "check_off", "/items/check-off", "set_price", "importCsv",
                 "/v1/update?x=1", "removeAll"):
        assert suggests_change(name), name
    for name in ("say_hello", "/", "/widget/list", "/settings", "list_items", "get_offset", "/health", "/news",
                 "read_note", "checklist"):
        assert not suggests_change(name), name
    data = {"ui": {"pages": [{"id": "m", "path": "/items/delete"}], "widgets": [{"id": "w", "path": "/w/list"}]},
            "health": {"url": "http://127.0.0.1:{port}/health"}, "abilities": {"changes": ["clear_list"]}}
    tools = [{"name": "clear_list"}, {"name": "add_item"}, {"name": "list_items"}]
    got = change_warnings(data, tools)
    assert len(got) == 2
    assert "GET route /items/delete" in got[0] and "(D55)" in got[0]
    assert "add_item" in got[1] and "abilities.changes" in got[1]


def test_d55_warning_shows_in_check_but_never_fails(drawbridge, no_brain, hello_copy):
    data = json.loads((hello_copy / "puffpai.json").read_text())
    data.setdefault("ui", {})["widgets"] = [{"id": "w", "type": "list", "title": "Clear", "path": "/api/clear"}]
    (hello_copy / "puffpai.json").write_text(json.dumps(data))
    report = check(hello_copy)
    assert any("GET route /api/clear" in w for w in report.warnings)
    assert report.passed() == 3, report.lines()
    assert any(line.startswith("Warning: its widget GET route /api/clear") for line in report.lines())
