"""A short novel with planted problems, as Manuscript would leave it on the shelf: sections, manuscript.json, bible.json,
cache entries and an old desk.json. For the tests, the preview and the model check.

Planted: a dropped thread (the silver key, sections 1 and 2 of 8), a dead man speaking (Harrow dies in 3, speaks in 6), dates
running backward (March 3, 1891 in 2, February 20, 1891 in 5), eye colour that changes (Mara's grey eyes in 1, brown in 7),
a wrong weekday, a doubled word, a misspelt name, two spellings, a stray quote mark.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

TITLE = "The Salt Road"
SECTIONS = [
    ("Chapter One", """Mara Quill came down to the harbor at first light with the silver key in her coat. Her grey eyes went over the boats, one by one, and stopped on the Petrel, which had not sailed since the storm.

Her father had pressed the key into her hand the night he died. "It opens what your grandfather buried," he had said. "Keep it from Harrow." She had not told anyone, not even Elias.

Captain Harrow stood on the Petrel's deck with his pipe gone cold. He was a big man, slow to speak, and the whole town owed him money.

"You want passage north," he said. "Everyone wants passage north. What are you paying with?"

"Work," she said. She would have to find the the money later. The road north was forty miles of salt flat, and nobody crossed it alone."""),
    ("Chapter Two", """On Monday, March 3, 1891, the Petrel took on water and rope, and Mara signed the ship's book. She felt the key against her ribs the whole time, and wondered what her grandfather could have buried that was worth a man's life.

Elias Wren, the doctor's son, found her on the dock. He was twenty-four, thin as a rail, and had read every book in the town. "You could stay," he said. "The harbour isn't so bad. There's work at the salt pans."

"Stay and do what? Count Harrow's money for him?"

He laughed, but he did not look at her. The silver key stayed in her coat, and she told him nothing about it."""),
    ("Chapter Three", """The storm came on the fourth day out. It took the mainmast first and then the boat, and by nightfall the crew were bailing with buckets and cooking pots.

Harrow went over the side trying to cut the wreck of the mast away. Mara saw the rope go tight and then go slack. Captain Harrow drowned within sight of the shore, and they found him on the rocks at dawn. They buried him above the tide line, with his pipe, and Elias said the words because nobody else would.

Nobody spoke of what they owed him now. Mara stood at the grave of Harrow a long time, wet through. She felt nothing at all, and that frightened her more than the storm."""),
    ("Chapter Four", """The tide on that coast turned every nine hours, and everyone who lived there set their days by it. They crossed the first of the salt flats at low water, walking in a line, with the Petrel's boy leading the mule.

Elias talked to fill the silence. He talked about his father, and about the plague ship that had come when he was small. "Twenty people died in the customs house," he said. "The doctor went in anyway. That's who you're walking with, the doctor's son. A coward by comparison."

"You came," she said.

"I came because you did." He said it to the mule."""),
    ("Chapter Five", """February 20, 1891 was the coldest day of that year. They reached the salt works at Grey Cross by evening and found it empty, the pans cracked and white. The people had gone north in a hurry, leaving plates on the tables.

In the manager's office Mara found a map of the road, and a ledger with her grandfather's name on the last page beside a sum that made Elias whistle. "Somebody was paid a lot of money to stop digging," he said.

They slept in the office. In the night the wind got up, and something moved in the yard."""),
    ("Chapter Six", """Morning showed them tracks, three men and a cart, going north ahead of them. Someone else wanted what was on the road.

"Hold the line, and keep the mule between you and the wind," Harrow said, as they set out across the white. Nobody answered him. Mara walked ahead with the map and tried to work out how long the water would last.

The tide here turned every six hours, and they had to run the last mile with it at their heels, the salt going dark behind them. Elias went down twice and got up twice. By dark they were on the far shore, and could not stop shaking."""),
    ("Chapter Seven", """The town of Kell stood where the road gave out. It was not on the map, and its people did not like questions. A woman at the inn looked hard at Mara's brown eyes and said she knew that face, she had seen it on a man who came through forty years ago.

"He dug up on the headland," the woman said. "Then the money came, and he stopped. Then he went away. They all went away." She would say no more, and Elias had to pay her double for the room.

Ellias went up to the headland alone that night, and came back with mud to the knee and an odd look. He did not say what he had seen."""),
    ("Chapter Eight", """They climbed the headland together in the morning, and at the top there was nothing but a cairn and the wind. Mara stood with her hand on the stones and understood at last that nobody was coming to tell her what it had all been for.

"We could go home," Elias said.

"We could."

She looked back down the road they had come, forty miles of salt, and north to the grey water. Then she took out her father's letter and read it one more time, and put it in the cairn, and they started down."""),
]

BIBLE = {
    "schema": "manuscript-bible/2", "title": TITLE, "built_with": "stand-in", "sections_read": 8, "sections_total": 8,
    "characters": [
        {"id": "mara-quill", "name": "Mara Quill", "aliases": ["Mara"], "role": "protagonist", "importance": "main", "traits": ["stubborn", "guarded"],
         "wants": "to find what her grandfather buried", "fears": "feeling nothing", "appearances": [1, 2, 3, 4, 5, 6, 7, 8], "first_seen": 1,
         "arc": [{"section": 1, "beat": "Takes the key north."}, {"section": 8, "beat": "Leaves the letter at the cairn."}],
         "relationships": [{"to": "elias-wren", "kind": "friend", "note": ""}]},
        {"id": "elias-wren", "name": "Elias Wren", "aliases": ["Elias"], "role": "friend", "importance": "main", "traits": ["bookish", "afraid"],
         "wants": "to be braver than his father", "fears": "being a coward", "appearances": [2, 3, 4, 5, 6, 7, 8], "first_seen": 2,
         "arc": [{"section": 4, "beat": "Admits why he came."}], "relationships": [{"to": "mara-quill", "kind": "friend", "note": ""}]},
        {"id": "captain-harrow", "name": "Captain Harrow", "aliases": ["Harrow"], "role": "ship's captain", "importance": "supporting", "traits": ["slow to speak"],
         "wants": "his money", "fears": "", "appearances": [1, 3, 6], "first_seen": 1, "arc": [{"section": 3, "beat": "Drowns in the storm."}], "relationships": []},
    ],
    "places": [{"id": "grey-cross", "name": "Grey Cross", "kind": "salt works", "notes": "abandoned", "appearances": [5]},
               {"id": "kell", "name": "Kell", "kind": "town", "notes": "not on the map", "appearances": [7, 8]}],
    "events": [
        {"id": "e1", "section": 1, "order": 1, "what": "Mara asks Harrow for passage north.", "who": ["mara-quill", "captain-harrow"], "where": "the harbor", "when": "first light"},
        {"id": "e2", "section": 2, "order": 1, "what": "The Petrel takes on stores.", "who": ["mara-quill"], "where": "the harbor", "when": "March 3, 1891"},
        {"id": "e3", "section": 3, "order": 1, "what": "Harrow drowns in the storm and is buried.", "who": ["captain-harrow", "elias-wren"], "where": "the shore", "when": "the fourth day out"},
        {"id": "e4", "section": 5, "order": 1, "what": "They find the empty salt works and a ledger.", "who": ["mara-quill", "elias-wren"], "where": "Grey Cross", "when": "February 20, 1891", "placement": "now"},
        {"id": "e5", "section": 7, "order": 1, "what": "The innkeeper tells of the digging on the headland.", "who": ["mara-quill"], "where": "Kell", "when": "evening"},
        {"id": "e6", "section": 8, "order": 1, "what": "Mara leaves the letter in the cairn.", "who": ["mara-quill", "elias-wren"], "where": "the headland", "when": "morning"},
    ],
    "threads": [
        {"id": "silver-key", "name": "The silver key", "kind": "main plot", "sections": [1, 2], "setups": ["section 1: her father gives her a silver key that opens what her grandfather buried"], "payoffs": [], "open": True},
        {"id": "the-digging", "name": "The digging on the headland", "kind": "main plot", "sections": [5, 7, 8], "setups": ["section 5: the ledger"], "payoffs": ["section 8: a cairn and nothing"], "open": False},
        {"id": "the-three-men", "name": "The three men and the cart", "kind": "subplot", "sections": [6], "setups": ["section 6: tracks ahead of them"], "payoffs": [], "open": True},
    ],
    "themes": [{"name": "what the dead leave us", "evidence": [{"section": 1, "quote": "It opens what your grandfather buried"}]}],
    "world_rules": [{"rule": "The tide turns every nine hours.", "sections": [4]}],
    "science": [{"claim": "A nine-hour tide", "section": 4, "note": "Tides on Earth turn about every six hours.", "plausible": "no"}],
    "continuity": [],
    "summary": {"one_line": "A woman carries a silver key north across the salt flats, to learn what her grandfather buried.",
                "paragraph": "Mara Quill takes her dead father's silver key north with the doctor's son Elias. A storm kills the captain. They cross the salt flats, find a ledger, and reach a headland where there is nothing but a cairn.",
                "by_section": [{"section": n + 1, "summary": h} for n, h in enumerate(["Mara asks Harrow for passage.", "Stores and Elias.", "The storm; Harrow drowns.", "The flats; Elias talks.", "Grey Cross and the ledger.", "Tracks; the tide.", "Kell and the innkeeper.", "The cairn."])]},
    "scenes": [], "structure": {"acts": [{"name": "Act one", "from": 1, "to": 3}], "turning_points": [{"name": "The storm", "section": 3, "note": "Harrow dies"}],
                                "midpoint": {"section": 5, "note": "The ledger"}, "climax": {"section": 8, "note": "The cairn"}},
    "voice": {"by_section": [{"section": 1, "pov": "third person, Mara", "tense": "past", "slips": []}], "tics": [{"phrase": "salt", "count": 9, "sections": [1, 4]}],
              "dialogue_habits": [{"character": "Elias Wren", "habit": "talks to fill silence"}]},
    "briefs": {"voice": "Plain, dry and close to Mara, in short declarative sentences, past tense."},
}

DESK = {"items": [
    {"id": "d-kept000001", "kind": "plot_hole", "severity": "high", "sections": [7], "note": "What Elias saw on the headland is never told.", "fix": "Tell it, or cut the trip.", "state": "kept", "writer_note": "I know, working on this", "changed_at": "2026-11-01T10:00:00Z"},
    {"id": "d-dism000002", "kind": "question", "severity": "low", "sections": [4], "note": "Why a mule?", "fix": "", "state": "dismissed", "writer_note": "", "changed_at": "2026-11-01T10:01:00Z"},
]}


def write_book(shelf: Path, title: str = TITLE, bible: dict | None = BIBLE, desk: bool = True, cache: bool = True) -> Path:
    """The folder Manuscript would have left on the shelf."""
    folder = shelf / title
    (folder / "sections").mkdir(parents=True)
    (folder / "original.txt").write_text("the upload, never touched", encoding="utf-8")
    index = []
    for i, (head, text) in enumerate(SECTIONS, 1):
        name = f"{i:02d} - {head}.md"
        content = f"# {head}\n\n{text}\n"
        raw = content.encode("utf-8")
        (folder / "sections" / name).write_bytes(raw)
        sha = hashlib.sha256(raw).hexdigest()
        index.append({"n": i, "heading": head, "file": name, "words": len(text.split()), "sha": sha})
        if cache:
            (folder / "cache").mkdir(exist_ok=True)
            (folder / "cache" / f"{sha}.json").write_text("{}", encoding="utf-8")
    (folder / "manuscript.json").write_text(json.dumps({"title": title, "original": "book.txt", "original_file": "original.txt", "sections": index, "chapters": index}), encoding="utf-8")
    if bible:
        (folder / "bible.json").write_text(json.dumps(bible), encoding="utf-8")
    (folder / "status.json").write_text(json.dumps({"state": "done", "step": "Read", "done": 8, "total": 8}), encoding="utf-8")
    if desk:
        (folder / "desk.json").write_text(json.dumps(DESK), encoding="utf-8")
    return folder
