"""D146's switch on the Pinned row: real Edge, real Core, real clicks, on the dashboard's page and the window's."""

from __future__ import annotations

import json
import os

import pytest

from test_i10a_desktop_clicks import EDGE, WHERE, _call, _desktop, _point, _rail


def _saved(state):
    return json.loads((state / "settings" / "folders.json").read_text(encoding="utf-8")).get("fiction") or []


def _flip(b, box):
    """A real click on the switch, then wait for the tile the Core's answer drew: the box itself ticks before that."""
    b.js(f"document.querySelector('{box}').setAttribute('data-was', '1')")
    b.click(*_point(b, box, 0))
    assert b.wait(f"!!document.querySelector('{box}') && !document.querySelector('{box}').hasAttribute('data-was')", timeout_s=30), b.js(WHERE)


def test_the_switch_on_the_pinned_row_by_real_clicks(state, tmp_path, monkeypatch, drawbridge):
    if os.name != "nt" or EDGE is None:
        pytest.skip("Windows Job Objects and Microsoft Edge")
    novel = tmp_path / "My Novel.txt"
    novel.write_text("It was the summer the river ran low.", encoding="utf-8")
    box = "[data-fiction]"

    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        # the dashboard: off when named in every chat, then on, and it stays on when drawn again
        b.click(*_point(b, ".rail a.item[href='#knowledge']", 0))
        tile = "#view-knowledge [data-tile=folders]"
        assert b.wait(f"!!document.querySelector('{tile}')", timeout_s=30), b.js(WHERE)
        assert _call(b, "POST", "/v1/knowledge/documents", {"path": str(novel)})["status"] == 200     # setup: its dialog can't be clicked
        _rail(b, "home")
        _rail(b, "knowledge")
        assert b.wait(f"!!document.querySelector('{tile} [data-named]')", timeout_s=30), b.js(WHERE)
        assert b.js(f"!document.querySelector('{tile} {box}')")   # the switch only shows on a document named in every chat
        b.click(*_point(b, f"{tile} [data-named]", 0))            # a real tick on its own switch
        assert b.wait(f"!!document.querySelector('{tile} {box}')", timeout_s=30), b.js(WHERE)
        assert b.js(f"document.querySelector('{tile} {box}').checked") is False and _saved(state) == []
        said = b.js(f"document.querySelector('{tile}').textContent")
        assert "No vault for this document" in said and "it is an ordinary file" in said
        _flip(b, f"{tile} {box}")
        assert b.js(f"document.querySelector('{tile} {box}').checked") is True and len(_saved(state)) == 1

        # the window draws the same page: it shows on there, and it turns off there
        b.js("location.assign('/window/')")
        assert b.wait("!!document.querySelector('[data-rail=knowledge]')", timeout_s=30), b.js(WHERE)
        for _try in range(3):                            # the rail is drawn again as the window starts: a click can land on the old one
            b.click(*_point(b, "[data-rail=knowledge]", 0))
            if b.wait(f"!!document.querySelector('{box}')", timeout_s=10):
                break
        assert b.js(f"!!document.querySelector('{box}')"), b.js(WHERE)
        assert b.js(f"document.querySelector('{box}').checked") is True
        _flip(b, box)
        assert b.js(f"document.querySelector('{box}').checked") is False and _saved(state) == []

        # on again, then Remove takes it away, and its mark with it
        _flip(b, box)
        b.click(*_point(b, "[data-tile=folders] [data-remove-document]", 0))
        assert b.wait(f"!document.querySelector('{box}')", timeout_s=30), b.js(WHERE)
        assert _saved(state) == [] and novel.exists()
