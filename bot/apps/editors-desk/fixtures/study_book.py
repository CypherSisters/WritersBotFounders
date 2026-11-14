"""A second small book for Study a book, in a style far from The Salt Road's: first person, present tense, short
sentences, a lot of talk in curly quotes, scene breaks, one chapter in two parts, an italic word and a dash. Nothing is
planted; it is only there to be measured and compared.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

TITLE = "Night Ferry"
D = "\u2014"
SECTIONS = [
    ("Chapter One", f"""The ferry leaves at nine. I am late. I run.

\u201cTicket,\u201d the man at the gate says.

\u201cI have it. Somewhere.\u201d

\u201cSomewhere doesn\u2019t sail.\u201d

I find it in my boot. He looks at it. He looks at me. \u201cCabin twelve,\u201d he says. \u201cDon\u2019t lose that too.\u201d

\u201cI won\u2019t.\u201d

\u201cYou will,\u201d he says. He is grinning.

The deck is wet. The rail is cold. Gulls hang over the wake like paper. I count the lights on the shore. Six. Then five. Then none.

\u201cFirst crossing?\u201d A woman beside me. Red coat. Bare hands.

\u201cYes.\u201d

\u201cYou\u2019ll hate it,\u201d she says. \u201cEveryone does. Then they come back.\u201d

\u201cWhy?\u201d

She shrugs. \u201cAsk me in the morning.\u201d

* * *

Cabin twelve is a cupboard with a bunk. I sit. The engine hums through the floor, through my spine. I can\u2019t sleep. I don\u2019t try.

Someone knocks. Twice. Then once.

\u201cWho is it?\u201d

No answer. I open the door. Nobody. A folded note on the mat. *Deck. Midnight.* That\u2019s all it says."""),
    ("Chapter Two, part 1", f"""Midnight. I go up.

She is there. The red coat. She doesn\u2019t turn.

\u201cYou came,\u201d she says.

\u201cYou wrote.\u201d

\u201cI didn\u2019t write anything.\u201d Now she turns. \u201cShow me.\u201d

I hand her the note. She reads it. Her face goes still.

\u201cThat\u2019s my brother\u2019s hand,\u201d she says.

\u201cWhere is he?\u201d

\u201cDead. Two years.\u201d

The wind takes the note. We both watch it go. Neither of us moves.

\u201cSo who wrote it?\u201d I ask.

\u201cSomeone who wants us both up here,\u201d she says. \u201cAlone. In the dark.\u201d

\u201cShould we go down?\u201d

\u201cYes,\u201d she says. She doesn\u2019t move. Neither do I."""),
    ("Chapter Two, part 2", f"""A light swings at the stern. Somebody is there. Big. Slow. Carrying something.

\u201cHey!\u201d I shout.

The light goes out.

We run. My shoes slip. She is faster. At the stern there is nothing. A coil of rope. A smell of oil. The water churning white below.

\u201cGone,\u201d she says.

\u201cOverboard?\u201d

\u201cOr down the ladder.\u201d She points. A hatch. Shut.

I try it. Locked. Of course it is.

\u201cTomorrow,\u201d she says. \u201cIn the light.\u201d

\u201cTomorrow we dock.\u201d

\u201cThen tonight,\u201d she says, and smiles for the first time. It is not a nice smile."""),
    ("Chapter Three", f"""Breakfast is coffee and a hard roll. The red coat sits across from me. She has a name now. Ines.

\u201cI don\u2019t sleep on boats,\u201d Ines says.

\u201cMe neither.\u201d

\u201cLiar. You snored.\u201d

\u201cYou were listening?\u201d

\u201cThe walls are paper.\u201d She tears her roll in half. \u201cHere is what I know. My brother worked this ferry. Engine room. He fell. That was the story.\u201d

\u201cAnd you don\u2019t believe it.\u201d

\u201cI didn\u2019t. Then I did. Now{D}\u201d She holds up the note. Wet, but whole. Someone found it. Someone left it at her plate.

\u201cNow?\u201d

\u201cNow I don\u2019t,\u201d she says.

* * *

The engine room is loud. Hot. A boy in overalls blocks the stair.

\u201cCrew only.\u201d

\u201cWe\u2019re lost,\u201d I say.

\u201cYou\u2019re not,\u201d he says. \u201cNobody gets lost down here. Go up.\u201d

We go up. Halfway, Ines stops.

\u201cHe knew my brother,\u201d she says quietly.

\u201cHow do you know?\u201d

\u201cHe has his watch.\u201d"""),
    ("Chapter Four", f"""Land. Grey docks. Gulls. A crowd at the rail with bags.

I lose Ines in the crowd. Then I find her. She is at the foot of the gangway. Talking to the boy. His hand on her arm.

\u201cInes!\u201d

She looks up. She shakes her head. Small. Once. Stay back.

I stay back. I hate it.

The boy lets her go. He walks off the ferry. He doesn\u2019t look back.

\u201cWell?\u201d I say.

\u201cHe gave it back,\u201d she says. The watch. Gold. Stopped at ten past four. \u201cHe said he was sorry.\u201d

\u201cFor what?\u201d

\u201cHe didn\u2019t say.\u201d

We stand on the dock. The ferry blows once behind us. Going back.

\u201cAre you getting on?\u201d I ask.

\u201cYes,\u201d she says. \u201cAre you?\u201d

I look at the town. I look at the boat.

\u201cYes,\u201d I say."""),
]


def write_study_book(shelf: Path, title: str = TITLE) -> Path:
    """The folder Manuscript would have left on the shelf, without a bible: Study a book reads the text only."""
    folder = shelf / title
    (folder / "sections").mkdir(parents=True)
    index = []
    for i, (head, text) in enumerate(SECTIONS, 1):
        name = f"{i:02d} - {head.replace(',', '')}.md"
        raw = f"# {head}\n\n{text}\n".encode("utf-8")
        (folder / "sections" / name).write_bytes(raw)
        index.append({"n": i, "heading": head, "file": name, "words": len(text.split()),
                      "sha": hashlib.sha256(raw).hexdigest()})
    (folder / "manuscript.json").write_text(json.dumps({"title": title, "sections": index, "chapters": index}),
                                            encoding="utf-8")
    (folder / "status.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")
    return folder
