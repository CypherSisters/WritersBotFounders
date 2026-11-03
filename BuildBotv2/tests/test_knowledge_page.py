"""D161: the Knowledge page makes sense. Real Edge, a real Core, real clicks, on the dashboard and the window."""

from __future__ import annotations

import json

import pytest

from puffpai_core import owner_files
from test_i10a_desktop_clicks import EDGE, WHERE, _call, _desktop, _point, _text_point, _type

# WritersBot: the page asks GET /v1/knowledge/find first (can it look now), so that one answers "ok".
STUB_FIND = ("window.__sent = []; const real = window.fetch; window.fetch = (u, o) => { if (String(u).indexOf('/v1/knowledge/find') >= 0 && !(o && o.method === 'POST')) "
             "return Promise.resolve(new Response(JSON.stringify({can: 'ok', ask: [], after_yes: []}), {status: 200, headers: {'Content-Type': 'application/json'}})); "
             "if (String(u).indexOf('/v1/knowledge/find') >= 0) "
             "{ window.__sent.push(o.body); return Promise.resolve(new Response(JSON.stringify({error: 'not here'}), {status: 409, "
             "headers: {'Content-Type': 'application/json'}})); } return real(u, o); }")


def test_the_page_by_real_clicks(state, tmp_path, monkeypatch, drawbridge):
    if EDGE is None:
        pytest.skip("Microsoft Edge")
    mine = tmp_path / "Writing"
    mine.mkdir()
    (mine / "letter.txt").write_text("Dear aunt, the leeks are in.", encoding="utf-8")
    with _desktop(state, tmp_path, monkeypatch, complete=True) as b:
        made = owner_files.my_files(state)
        made.mkdir(parents=True, exist_ok=True)
        (made / "Plan.txt").write_text("a plan", encoding="utf-8")
        b.click(*_point(b, ".rail a.item[href='#knowledge']", 0))
        tile = "#view-knowledge [data-tile=folders]"
        assert b.wait(f"!!document.querySelector('{tile}')", timeout_s=30), b.js(WHERE)
        said = b.js(f"document.querySelector('{tile}').textContent")
        assert "Add a whole folder: every document in it, read where it is, never copied" in said

        # Find books is under Search, and a search with no result offers it with her words
        assert b.js("!!document.querySelector('#view-knowledge .tile [data-find-books]')")
        assert b.js("document.querySelector('#view-knowledge [data-find-books]').closest('[data-tile=folders], [data-tile=sources]') === null")
        _type(b, "#view-knowledge input[aria-label='Search']", "zzzqq turnips")
        b.click(*_text_point(b, "#view-knowledge button", "Search"))
        assert b.wait("!!document.querySelector('[data-find-these]')", timeout_s=30), b.js(WHERE)
        b.js(STUB_FIND)
        b.click(*_point(b, "[data-find-these]", 0))
        assert b.wait("window.__sent.length === 1", timeout_s=15), b.js(WHERE)
        assert json.loads(b.js("window.__sent[0]")) == {"words": "zzzqq turnips"}

        # a folder added elsewhere shows by itself, with its files, and no file-taking app is installed
        assert _call(b, "POST", "/v1/folders/link", {"path": str(mine)})["status"] == 200
        assert b.wait(f"!!document.querySelector('{tile} [data-documents=linked]')", timeout_s=40), b.js(WHERE)
        assert b.wait(f"document.querySelector('{tile} [data-documents=linked]').textContent.indexOf('Your bot can find 1 file here') >= 0", timeout_s=40), b.js(WHERE)
        assert b.js(f"!!document.querySelector('{tile} [data-folder-files]')")
        assert "See its files" in b.js(f"document.querySelector('{tile} [data-folder-files] summary').textContent")
        b.click(*_text_point(b, f"{tile} [data-documents=linked] button", "Remove"))
        assert b.wait(f"!document.querySelector('{tile} [data-documents=linked]')", timeout_s=30), b.js(WHERE)

        # Remove on what the bot made asks first, then the file is gone
        files = "#view-knowledge [data-tile=my-files]"
        assert b.wait(f"!!document.querySelector('{files} [data-remove]')", timeout_s=15), b.js(WHERE)
        b.click(*_text_point(b, f"{files} button", "Remove"))
        assert b.wait(f"document.querySelector('{files}').textContent.indexOf('Delete it from this computer?') >= 0", timeout_s=10)
        assert (made / "Plan.txt").exists()
        b.click(*_text_point(b, f"{files} button", "Delete it from this computer?"))
        assert b.wait(f"document.querySelector('{files}').textContent.indexOf('Plan.txt') < 0", timeout_s=30), b.js(WHERE)
        assert not (made / "Plan.txt").exists()

        # the window: the same page, and /books in its chat
        b.js("location.assign('/window/')")
        assert b.wait("!!document.querySelector('[data-rail=knowledge]')", timeout_s=30), b.js(WHERE)
        for _try in range(3):                            # the rail is drawn again as the window starts
            b.click(*_point(b, "[data-rail=knowledge]", 0))
            if b.wait("!!document.querySelector('[data-find-books]')", timeout_s=10):
                break
        assert b.js("!!document.querySelector('[data-find-books]')"), b.js(WHERE)
        b.js(STUB_FIND)
        _type(b, "#input", "/books gardening in pots")
        b.js("document.getElementById('composer').requestSubmit()")
        assert b.wait("window.__sent.length === 1", timeout_s=15), b.js(WHERE)
        assert json.loads(b.js("window.__sent[0]")) == {"words": "gardening in pots"}
