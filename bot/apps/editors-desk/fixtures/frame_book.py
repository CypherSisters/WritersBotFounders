"""A book shaped like the first real run's: a frame story told from papers found after a death, an e-book header with an
"updated" date, chapters out of order on purpose and chapters headed by their dates, a man who dies and is then only
mentioned, a story bible with handles the vault left before the vault switch, and an old desk with an empty item and a
template one. None of it is a problem; Editor's Desk must find nothing big here.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

TITLE = "The North Papers"
HANDLE = "{{vault:h83ecd42684294a39017a}}"
SECTIONS = [
    ("Before the first chapter", """The Project Gutenberg eBook of The North Papers

This ebook is for the use of anyone anywhere in the United States and most other parts of the world at no cost and with almost no restrictions whatsoever.

Title: The North Papers

Release date: May 1, 2010 [eBook #10001]
Most recently updated: October 18, 2024

Language: English

*** START OF THE PROJECT GUTENBERG EBOOK THE NORTH PAPERS ***

(Found among the papers of the late Edmund Pryce, of the harbour town.)"""),
    ("Chapter I. The Clay Tablet", """On March 1st, 1925, a young sculptor named Wilcox brought my great-uncle, Professor Silas Hale, a tablet of wet clay. It was carved with figures neither of them could name.

"Where did you find it?" Hale asked.

"I made it in my sleep," said Wilcox, and would not say more."""),
    ("Chapter II. The Fever", """On March 22, 1925, Wilcox fell ill with a fever, and in it he talked of a city under the sea. My great-uncle sat with him every night and wrote down what he said."""),
    ("Chapter III. The Inspector's Tale", """The tale goes back seventeen years, and I set it down as my great-uncle's papers tell it. On November 1st, 1907, Inspector Legrasse led his men into the swamp south of the city, and there they found the same figures carved in stone."""),
    ("March 28, 1925", """Wilcox woke clear-headed and remembered nothing. The fever had gone as it came."""),
    ("March 24, 1925", """The night before the fever broke, the sky over the harbour was green, and the dogs would not stop barking."""),
    ("Chapter IV. The Death", """I think Professor Hale died because he knew too much. He was struck down on the steep street by the docks, and died that night in his bed."""),
    ("Chapter V. The Madness from the Sea", """I had largely given over my inquiries into what Professor Hale called the cult. Hale's notes lay open on the desk beside me.

Hale had said that the stones were older than the sea.

In the diary Hale says, "The sea is rising."

I remembered how Hale smiled at the sea.

"Hale knew all of it," said Mrs. Corr, and shut the door.

Hale walked into my room in a dream, and I woke shaking.

Edmund Pryce died in the spring, and these papers were found among his things.

*** END OF THE PROJECT GUTENBERG EBOOK THE NORTH PAPERS ***

Updated editions will replace the previous one."""),
]

BIBLE = {
    "schema": "manuscript-bible/2", "title": TITLE, "built_with": "an older model", "sections_read": 8, "sections_total": 8,
    "characters": [
        {"id": "silas-hale", "name": "Professor Silas Hale", "aliases": ["Hale"], "role": "great-uncle", "importance": "main",
         "wants": "", "fears": "", "appearances": [2, 3, 7, 8], "arc": [{"section": 7, "beat": "Dies on the docks."}], "relationships": []},
        {"id": "wilcox", "name": "Wilcox", "role": "sculptor", "importance": "supporting", "wants": "", "appearances": [2, 3, 5]},
        {"id": "edmund-pryce", "name": "Edmund Pryce", "role": "narrator", "importance": "main", "wants": "to know the truth",
         "appearances": [1, 8], "arc": []},
        {"id": "h83ec", "name": HANDLE, "role": "unknown", "importance": "minor", "appearances": [4]},
    ],
    "places": [{"id": "harbour", "name": "the harbour town", "kind": "town", "appearances": [1, 6]}],
    "events": [
        {"id": "e1", "section": 2, "what": "Wilcox brings the tablet.", "who": ["wilcox", "silas-hale"], "where": "the harbour town", "when": "March 1st, 1925"},
        {"id": "e2", "section": 4, "what": f"The inspector finds the figures with {HANDLE}.", "who": [], "where": "the swamp", "when": "November 1st, 1907", "placement": "earlier"},
        {"id": "e3", "section": 7, "what": "Hale dies.", "who": ["silas-hale"], "where": "the docks", "when": "that night"},
    ],
    "threads": [{"id": "the-cult", "name": "The cult", "kind": "main plot", "sections": [2, 4, 8], "setups": [f"section 2: the tablet; {HANDLE}"],
                 "payoffs": ["section 8: the papers"], "open": False}],
    "summary": {"one_line": "A man pieces together his dead great-uncle's papers.",
                "paragraph": f"Edmund Pryce reads the papers of his great-uncle, and {HANDLE} is found.",
                "by_section": [{"section": n, "summary": s} for n, s in [(2, "The tablet."), (3, "The fever."), (4, "The inspector's tale."), (7, "Hale dies."), (8, "The papers.")]]},
    "continuity": [], "themes": [], "world_rules": [], "science": [],
}

DESK = {"items": [
    {"id": "d-empty00001", "kind": "character", "severity": "medium", "sections": [], "note": "", "fix": "", "state": "open", "writer_note": ""},
    {"id": "d-templ00002", "kind": "character", "severity": "medium", "sections": [3], "note": "Wilcox's want blank", "fix": "Clarify want", "state": "open", "writer_note": ""},
    {"id": "d-vault00003", "kind": "question", "severity": "medium", "sections": [4], "note": f"Remove the search artifact {HANDLE} from the manuscript", "fix": "", "state": "open", "writer_note": ""},
    {"id": "d-hers000004", "kind": "question", "severity": "low", "sections": [4], "note": "The inspector's tale is long and slows the middle.", "fix": "", "state": "kept", "writer_note": "I like it long"},
]}


def write_frame_book(shelf: Path) -> Path:
    folder = shelf / TITLE
    (folder / "sections").mkdir(parents=True)
    (folder / "cache").mkdir()
    index = []
    for i, (head, text) in enumerate(SECTIONS, 1):
        name = f"{i:02d} - {head.replace(':', '')}.md"
        raw = f"# {head}\n\n{text}\n".encode("utf-8")
        (folder / "sections" / name).write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        (folder / "cache" / f"{sha}.json").write_text(json.dumps({"extraction": {"summary": f"read with {HANDLE}"}}), encoding="utf-8")
        index.append({"n": i, "heading": head, "file": name, "words": len(text.split()), "sha": sha})
    (folder / "manuscript.json").write_text(json.dumps({"title": TITLE, "sections": index, "chapters": index}), encoding="utf-8")
    (folder / "bible.json").write_text(json.dumps(BIBLE), encoding="utf-8")
    (folder / "desk.json").write_text(json.dumps(DESK), encoding="utf-8")
    (folder / "status.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")
    return folder
