# Manuscript v2: the contract between the server and the page

Manuscript is a PuffPAI `app` package for WritersBot. Version 0.3.5 (0.2.1's routes, tools and files all still hold;
what 0.3 adds is marked **0.3**, and each later version has its section at the end). The owner's rules, in order:

1. **The manuscript text is never changed by this app.** No route and no tool writes a chapter or section file. The
   only files the app writes are its own: the story bible, views, notes, pointer, status, corrections, the desk, and
   its own boards in `boards.json`.
2. **The bot reads the whole book itself**, in sections small enough for the local model, and builds a *story bible*.
   The tabs are filled from the bible. The owner never has to ask for the basics.
3. **The chat drives the page.** "Show me the character arc of Jennifer" makes a *view* under Characters. "Show me
   where she first meets Tom" moves a *pointer* that scrolls and highlights the passage in the read-only text.
4. **Local model only.** Every model call goes through the Core's `POST /v1/brain/chat` with the package token. Nothing
   goes anywhere else. `needs.brain.text` is true in the manifest.
5. **Plain words, no em dashes**, in every line the owner can read. "Your bot", never "AI" or "LLM".

## Files, per manuscript

Under the owner's files, `My files/Manuscripts/<title>/`, as in 0.1.0:

```
original.<ext>          the upload, never touched again
manuscript.json         title, original, added, sections[]            (0.1.0 called them chapters; both keys are written)
sections/NN - <heading>.md
world.md characters.md plot.md themes.md science.md editing.md        the owner's notes, as in 0.1.0
timeline.md structure.md voice.md        0.3: notes for the new tabs, made on first save or append
                        (the Editor's desk shows editing.md as its notes; desk.md only when there is no editing.md)
bible.json              the story bible (below)
corrections.json        0.3: {"corrections": [{id, what, correction, made_at}]}, the writer's corrections, oldest first
desk.json               0.3: {"items": [{id, kind, severity, sections, note, fix, state, writer_note, changed_at}]},
                        the desk items she kept, dismissed or noted
boards.json             0.3: shared with Maps (below); Manuscript adds boards and keeps every other board and key
views.json              named views made by the chat or the owner, newest first
pointer.json            where the bot is pointing right now
status.json             the reading's progress
cache/<section sha>.json   one section's extraction, reused when the text is unchanged
```

### Sections

A section is what 0.1.0 called a chapter. Splitting, in this order:

- Word Heading 1 and Heading 2, Markdown `#` and `##`, and lines matching chapter or part headings (0.1.0's rule).
- With no headings (a short story): **scene breaks**. A line that is only `***`, `* * *`, `---`, `#`, `~`, or three or
  more blank lines in a row, splits a scene. Sections are named "Scene 1", "Scene 2", ...
- Still one block longer than 2,200 words: split at paragraph boundaries into parts of at most 2,200 words, named
  "<heading>, part 1", "part 2", ... so every section fits one model call with room for the ask and the answer.
- **0.3**: a manuscript with no headings (a short story, with or without scene breaks) splits into parts of at most
  1,000 words instead, so the model notices more.

`manuscript.json` keeps `sections: [{n, heading, file, words, sha}]` and, for 0.1.0 readers, the same list as `chapters`.

## The story bible: `bible.json`

```json
{
  "schema": "manuscript-bible/1",
  "title": "Storm Harbor",
  "built_at": "2026-10-31T23:40:00Z",
  "built_with": "qwen3:30b-a3b",
  "sections_read": 12, "sections_total": 12,
  "characters": [
    {"id": "jennifer", "name": "Jennifer Hale", "aliases": ["Jen"], "role": "protagonist",
     "traits": ["stubborn", "loyal"], "wants": "to leave the harbor", "fears": "ending up like her mother",
     "appearances": [1, 2, 5, 9], "first_seen": 1,
     "arc": [{"section": 1, "beat": "Refuses the job on the ferry."}, {"section": 9, "beat": "Takes the wheel in the storm."}],
     "relationships": [{"to": "tom", "kind": "brother", "note": "they stop speaking after section 4"}],
     "family": {"parents": ["maren"], "children": [], "partners": []}}
  ],
  "places": [{"id": "harbor", "name": "Storm Harbor", "kind": "town", "notes": "...", "appearances": [1, 2]}],
  "events": [{"id": "e1", "section": 1, "order": 1, "what": "The ferry sinks.", "who": ["jennifer"], "where": "harbor",
              "when": "the first night of autumn"}],
  "threads": [{"id": "ferry", "name": "The ferry", "kind": "main plot | subplot", "sections": [1, 4, 9],
               "setups": ["section 1: the cracked hull"], "payoffs": ["section 9: it fails in the storm"], "open": false}],
  "themes": [{"name": "leaving home", "evidence": [{"section": 2, "quote": "..."}]}],
  "world_rules": [{"rule": "The tide turns every nine hours.", "sections": [1, 7]}],
  "science": [{"claim": "A nine-hour tide", "section": 1, "note": "Earth's tides turn about every six hours; a moon this close would raise them far higher.", "plausible": "no | unclear | yes"}],
  "continuity": [{"kind": "mismatch | gap | question", "sections": [2, 8], "note": "Tom's eyes are grey in 2 and brown in 8."}],
  "editing": [{"section": 3, "kind": "pacing | point of view | clarity | dialogue | repetition", "note": "..."}],
  "summary": {"one_line": "...", "paragraph": "...", "by_section": [{"section": 1, "summary": "..."}]}
}
```

Every `id` is a lowercase slug the server makes; every list may be empty. Nothing in the bible is a quote longer than
200 characters.

**0.3: `manuscript-bible/2`.** Every 0.2 field above is kept with the same meaning, so a reader of
`manuscript-bible/1` (Maps 1.1) still reads it. Added:

```json
{
  "schema": "manuscript-bible/2",
  "sections": [{"n": 1, "heading": "Chapter One", "words": 2140}],
  "characters": [{"...": "as above", "importance": "main | supporting | minor",
                  "personality": "two or three sentences", "write_up": "one paragraph", "corrected": true}],
  "places": [{"...": "as above", "where": "the country, region or planet, when the text says"}],
  "events": [{"...": "as above", "story_order": 2, "placement": "now | earlier | later | (empty)"}],
  "scenes": [{"section": 1, "n": 1, "purpose": "", "pov": "", "tense": "past", "words": 900, "dialogue_share": 0.31}],
  "structure": {"acts": [{"name": "Act one", "from": 1, "to": 4}],
                "turning_points": [{"name": "", "section": 3, "note": ""}],
                "midpoint": {"section": 6, "note": ""}, "climax": {"section": 11, "note": ""}},
  "voice": {"by_section": [{"section": 1, "pov": "third person, Jennifer", "tense": "past", "slips": [""]}],
            "tics": [{"phrase": "grey", "count": 14, "sections": [1, 2, 5]}],
            "dialogue_habits": [{"character": "Jennifer Hale", "habit": "short answers"}]},
  "desk": [{"id": "d-3f2a9c01be", "kind": "plot_hole | timeline | science | character | continuity | question",
            "severity": "high | medium | low", "sections": [1, 9], "note": "", "fix": "",
            "state": "open | kept | dismissed", "writer_note": ""}],
  "setting": {"world": "", "place_in_world": "country, region, city or planet", "era": "", "technology": "",
              "one_paragraph": ""},
  "history": [{"when": "twenty years ago", "what": "", "sections": [1]}],
  "cast_summary": "one paragraph on the whole cast",
  "briefs": {"characters": "", "plot": "", "timeline": "", "structure": "", "world": "", "science": "", "themes": "",
             "voice": "", "desk": ""}
}
```

- `order` is still the event's place within its section; `story_order` is its place in the story's own time across
  the whole book (1 is earliest). Until the model has put them in order it equals the telling order.
- A scene's `words` and `dialogue_share` (the share of its words inside quotation marks) and a tic's `count` are
  counted by the server, not guessed by the model. A section's tense that differs from the book's usual tense is
  added to its `slips`.
- `importance`: main for a lead by role (protagonist, antagonist, narrator) or a character in at least three sections
  and two in five; supporting in two or more; minor otherwise. A minor character's `write_up` is one line made from
  the facts, with no model call.
- A desk item's `id` comes from its kind and note, so a re-read that finds the same thing keeps her choice on it.
- `briefs` are two to four paragraphs each, with section numbers in brackets like [3]. Em dashes the model writes
  become commas.
- `corrected: true` marks an entry (a character, a relationship, a place) a correction changed.

### How the bible is built (the server)

1. **Extract**, one model call per section, with a system message that asks for JSON only (characters, places, events,
   relationships, themes, world rules, science claims, continuity flags, editing notes, a two-sentence summary), the
   section text, and the names already known from earlier sections so the model reuses them. Strip `<think>...</think>`
   blocks before parsing. On bad JSON, one repair call ("Return only the JSON."), then skip that section with a note in
   `status.json`. Cache by the section's sha.
2. **Merge**, deterministic: the same name, or an alias, or a first name that matches exactly one known character,
   merges. Order events by section then by order.
3. **Synthesize**, a few calls: one per character with four or more appearances (the arc), one for threads and
   continuity across sections, one for themes. Each call gets the merged facts, never the whole text.
4. Write `bible.json` after every step, so the page can show a partial bible while reading goes on.

**0.3** adds to each step, and every ask stays under about 5,000 input tokens (more asks, not fatter ones):

1. Extract asks also for each event's `placement`, each place's `where`, setting clues (world, place, era,
   technology) and history mentions. A second ask per section, also cached by the sha, asks how the section is told:
   point of view, tense, its scenes (purpose and first words), slips, tics, dialogue habits. A 0.2 cache entry has the
   facts but not this, so only this second ask is made for it.
2. Merge also builds `sections`, `scenes`, `voice`, `history`, a first `setting` from the most common clues,
   `importance`, the minor characters' one-line write-ups, and the desk items the facts give without asking:
   continuity notes, science marked not plausible, and conflicts between story order and telling order.
3. Synthesize, after 0.2's calls, in this order: one call for the story order of the events; one for the structure;
   one for the setting and history from all sections' clues; one per main character for `personality` and
   `write_up`, and one per four supporting characters; one for the desk (it is given the items already found, open
   threads, the characters' wants and last beats, and adds plot holes, character holes and questions); then one per
   tab for its brief (and, with the Characters brief, `cast_summary`), from that tab's merged facts, never the text.
   The briefs come last. `bible.json` is written after each call.
4. Before every write the server puts the writer's corrections (`corrections.json`) and desk choices (`desk.json`)
   back on top, so a re-read never undoes them. `GET /api/bible` and every tab do the same when they read.

`status.json`: `{"state": "idle | reading | done | failed", "step": "Reading section 3 of 12", "done": 3, "total": 12,
"started_at", "finished_at", "problems": ["Section 7 gave no usable answer."]}`.

Reading starts on upload and can be started again with `POST /api/analyze` or the `manuscript_analyze` tool. One
reading at a time per manuscript. A reading is a thread in the server, and it survives a page reload because the page
only reads the files.

## Views: `views.json`

A view is what the chat or the owner asked to see, kept so it stays on its tab:

```json
{"views": [
  {"id": "v-20261031-2341-jennifer", "tab": "characters", "title": "Jennifer's arc", "asked": "show me the character arc of Jennifer",
   "made_at": "...", "made_by": "chat | page", "state": "making | ready | failed", "blocks": [ ... ]}
]}
```

Making a view is a job. `POST /api/show` and `manuscript_show` check the ask, save the view at once with
`"state": "making"` and no blocks, and answer "Making it. It shows under <Tab> when it is ready." (the route also sends
`{"view": ...}`). When the brain is done the record gets its blocks and `"state": "ready"`, or `"state": "failed"`
and a plain `"why"`. A view with no `state` is ready (0.2.0). A view still making when Manuscript stopped reads as
failed.

### One line for the brain

The brain answers one ask at a time, so the server keeps one queue of brain calls per process, run one at a time,
in order. Every call of a reading goes through it, one call per turn, and the owner's own asks (a view, a question)
go ahead of the reading's next call: a view asked during a reading is made after the section being read now, not
after the whole reading. `manuscript_ask` waits for the brain for at most 90 seconds (or on a 503), then answers
"Your bot is busy reading <title> right now (section N of M). Ask again in a few minutes, or ask me to show it under
a tab and it will appear when ready."

### Blocks, the one format for everything the tabs show

Both the bible's default tab content and every view are lists of blocks. The page renders each kind; the server never
sends HTML.

| `type` | fields | shows as |
|---|---|---|
| `markdown` | `text` | paragraphs, headings, bullet lists, bold and italic. No raw HTML, no links out. |
| `table` | `columns: [..]`, `rows: [[..]]` | a table; a cell may be a string or `{"text", "section": n}` to point at a section |
| `timeline` | `items: [{label, when, section, who: [..]}]` | a horizontal line of events, in order |
| `graph` | `nodes: [{id, label, kind}]`, `edges: [{from, to, label}]` | a relationship map, drawn with a small force layout |
| `tree` | `root: {label, children: [...]}` | a family tree or an outline, top down |
| `list` | `items: [{text, section}]` | a list where each item can jump to its section |
| `quote` | `text`, `section`, `note` | a short passage from the manuscript with a note under it |

Any `section` on a cell, item or quote is clickable in the page: it opens that section and highlights the first match
of the item's text, or the quote.

**0.3, the drawing twin.** When an ask for a view has a drawing in it (the words family, tree, timeline, touchpoint,
interact, relationship, arc, structure, world, map, thread or supporting), the server also adds a board record
`{id, kind, title, asked, made_at}` with no `spec` to the front of `boards.json`, with the view's own id, and the view
gets `"board": <id>`. Maps lays out a board with no `spec` when it is opened. Every other board and every other key in
`boards.json` is kept as it was. The answer line ends "..., and it is drawn in Maps." `kind` is the Maps board the
words name, tried in Maps' order (touchpoints, timeline, family, relationships, arcs, structure, threads, world).

### Default tab content

The server answers `GET /api/tab?title=..&tab=..` with `{"blocks": [...], "views": [...]}`: the blocks it builds from
the bible for that tab (below) and the saved views for that tab, newest first.

**0.3: nine tabs**, in this order, ids then titles: `characters` Characters, `plot` Plot and subplots, `timeline`
Timeline, `structure` Structure, `world` World, `science` Science, `themes` Themes, `voice` Voice, `desk` Editor's
desk. `editing` (0.2.1's tab) is taken everywhere as `desk`. Every tab opens with its brief as a `markdown` block
when the bible has one.

- **characters**: the brief; `cast_summary` as `markdown`; one `markdown` card per main character (name, role,
  personality, write-up, first seen); a table of the supporting and minor cast (name, importance, role, first seen,
  one-line write-up; a corrected one says "(you corrected this)"); one `graph` of relationships; a `tree` per family
  with more than two people. The answer also has `cards: [{id, name, role, importance, personality, write_up,
  first_seen, corrected, block}]`, where `block` is the index of that card's markdown block, so the page can draw it
  as a card.
- **plot**: the brief; a `timeline` of events, a table of threads (name, kind, sections, open or paid off), a `list` of
  continuity notes marked mismatch or gap.
- **timeline**: the brief; a `timeline` of events in story order; a table of conflicts (event, told in, happens
  before, whether the telling marks the jump), when there are any.
- **structure**: the brief; a table of acts (act, from, to); a `list` of turning points, then the midpoint and the
  climax; a table of scenes (section, scene, purpose, point of view, words).
- **world**: the brief; the setting paragraph as `markdown` (or the setting's fields as a short list); the history as
  a `timeline` when any item has a `when`, else a `list`; a `list` of world rules; a table of places (place, kind,
  where, notes, sections), "where" from the place's own `where`, else `setting.place_in_world`.
- **science**: the brief; a table (claim, section, plausible, note).
- **themes**: the brief; a `list` of themes, each with its strongest quote as a `quote` block.
- **voice**: the brief; a table (section, point of view, tense, slips); a `list` of tics with their counts; a table of
  dialogue habits (character, how they talk).
- **desk**: the brief (the editor's letter: what is strongest, then what needs work first); a table of the reading's
  editing notes (section, kind, note), as 0.2.1's Editing tab. The answer also has `desk: [...]`, the desk items, open
  and kept first by severity (high, medium, low) then section, dismissed last, for the page to draw with its buttons.

Every tab's answer also has `corrections: [{id, what, correction, made_at, applied}]`, the corrections whose subject
belongs on that tab: Characters when a character is named, World when a place is, else Timeline for words of time,
Science for words of science, else Characters.

## The pointer: `pointer.json`

`{"title", "section": n, "start": char offset, "end": char offset, "quote": "...", "note": "...", "set_at": "..."}`.
The page polls `GET /api/pointer` every two seconds while open; when `set_at` changes it opens the section, scrolls to
the range and highlights it, and shows `note` in a small bar above the text with a Clear button (`DELETE /api/pointer`).
The server finds the range by exact quote, else by the longest common run of five or more words, else by the first
paragraph that shares the most words with the quote.

## Routes (all need the package token; GET and DELETE change no manuscript text)

| route | does |
|---|---|
| `GET /api/list` | manuscripts with sections and each one's `status` |
| `GET /api/section?title&n` | one section's text |
| `POST /api/upload` | as 0.1.0 (`file_name`, `content_b64`, `title?`), and `.pdf` since 0.3.5; starts the reading |
| `GET /api/status?title` | `status.json` |
| `POST /api/analyze` | `{title}`; starts the reading again |
| `GET /api/bible?title` | the whole bible |
| `GET /api/tab?title&tab` | blocks and views for one tab |
| `POST /api/show` | `{title, tab, asked}`; starts a view from the page's own Ask box, same as the chat tool; answers at once with the making record |
| `GET /api/views?title&tab?` and `DELETE /api/views?title&id` | saved views |
| `GET /api/notes`, `POST /api/notes` | as 0.1.0, with the stale-save refusal |
| `GET /api/pointer?title`, `DELETE /api/pointer?title` | the pointer |
| `POST /api/correct` | **0.3** `{title, what, correction?}`; records the correction, applies what it can, answers `{message, correction: {id, what, correction, made_at, applied, tab, line}}` |
| `GET /api/desk?title` | **0.3** `{title, items, open}`: every desk item in the tab's order, with its state |
| `POST /api/desk` | **0.3** `{title, action: keep \| dismiss \| note \| list, id, note?}`; `id` is an item's id or its number among the open items; answers `{message, item}` |

**0.3, corrections.** `what` is a bible fact in her words ("Tom is Jennifer's cousin, not her brother");
`correction` is the right value on its own when she gave one. Applied by matching the names in her words to the
bible: a relationship's kind between two named characters (or one named character and the one relationship of the
negated kind, for "Tom is her cousin, not her brother"; the other side's kind follows, and family links follow a
parent, child or partner kind), an alias ("also called", "known as", "nicknamed", "goes by"), a character's role
(protagonist, antagonist, narrator, villain, hero, mentor, ...) or importance (main, supporting, minor), a place's
kind (town, village, island, ...). What it cannot apply stays a note on its tab, "You said: ...".

## MCP tools (the chat)

| tool | changes | does |
|---|---|---|
| `manuscript_list` | no | titles, sections, reading status |
| `manuscript_read` (title, section) | no | one section's text |
| `manuscript_status` (title) | no | the reading's progress, in one line |
| `manuscript_analyze` (title) | no (the app's own files) | start the reading again |
| `manuscript_ask` (title, question) | no | an answer from the bible plus the two sections that best match the question (word overlap), in one model call; cites sections by number |
| `manuscript_show` (title, tab, asked) | no (the app's own files) | makes a view: picks the bible facts that match `asked` (names, places, threads, themes), asks the model for the blocks as JSON in the block format above, validates them, saves the view. Answers at once "Making it. It shows under <Tab> when it is ready."; the view turns ready or failed later |
| `manuscript_point` (title, section, quote, note) | no | sets the pointer; returns where it landed |
| `manuscript_notes_read` / `manuscript_notes_append` (title, tab, text) | append is a change to the app's own notes | as 0.1.0 |
| `manuscript_correct` (title, what, correction?) | no (the app's own files) | **0.3** as `POST /api/correct`; answers "Changed it: ..." or "I could not change that in the story bible by myself, so I kept it as a note on the <Tab> tab: "You said: ..."" |
| `manuscript_add_file` (source, title?) | yes | **0.3.5** a file from My files as a new manuscript; no reading starts (below) |
| `manuscript_desk` (title, action: list \| keep \| dismiss \| note, id?, note?) | no (the app's own files) | **0.3** `list` gives the open items in severity order, one plain line each ("1. High, character, sections 1 and 3: ... Fix: ... [d-...]"); the others act on one item, by its number in that list or its id |

Tool descriptions are plain sentences; none reads like an instruction to a model. Examples in the manifest:
"show me the character arc of Jennifer", "show me where the ferry is first mentioned", "what does the story say about
the tide", "is the timeline in chapter four consistent", and for 0.3 "Tom is her cousin, not her brother", "what is
on the editor's desk", "dismiss the second desk item".

## The page

Built for the window's side pane first (400 to 600 pixels wide), then full width, then a phone. Three regions, top to
bottom in the pane, side by side when wider than 900 pixels:

1. **Manuscript**: picker of manuscripts and sections, then the read-only text with the highlight and the pointer bar.
   A small line under the picker: the reading's status ("Reading section 3 of 12", "Read by qwen3:30b-a3b on this
   computer", or "Not read yet. Start reading").
2. **Tabs**: World, Characters, Plot and subplots, Themes, Science, Editing (nine in 0.3, below). Each tab shows its default blocks, then
   its saved views (title, when, an x to remove), then the notes editor from 0.1.0. A view still making shows a
   small spinner and "Your bot is making this..."; a failed one shows its `why` and a Retry button. The page looks at
   the views every 10 seconds, and every 3 seconds while one is making. Every re-draw of a tab draws its default
   blocks and its saved views together, from one `GET /api/tab`, and keeps the scroll and any unsaved notes. An **Ask** box at the top of the
   tabs area: "Show me ..." sends `POST /api/show` for the open tab.
3. Everything renders from blocks. Renderers are the page's own code, no outside scripts: markdown (a small safe
   subset, text only), table, timeline (SVG), graph (SVG, a simple force layout, drag to move a node), tree (SVG,
   layered), list, quote. Light and dark through `prefers-color-scheme` and the Desktop's theme variables if present.

**0.3, the page.**

- Nine tabs that fit the pane: two rows of five from 400 to 900 pixels wide, one row of nine above 900.
- A **See in Maps** link under the tabs, to the tab's standard Maps board: characters to `std-relationships`, plot to
  `std-threads`, timeline to `std-timeline`, structure to `std-structure`, world to `std-world`, the others to the Maps
  list. The address is Maps' page as the Desktop serves a sibling package, `/pkg/maps/?title=<title>&board=<id>` on
  the page's own origin; the Desktop's proxy resolves it. The same link sits above every `graph`, `tree` and
  `timeline` block, and a view with a `board` shows it to that board. A character card's link adds
  `&focus=<character id>` on `std-relationships`.
- Characters: main characters as cards (name, role, an importance pill, personality, write-up, first seen as a
  section link, See in Maps).
- Characters and World: a **Correct a fact** box that sends `POST /api/correct`; every tab lists its corrections,
  "You corrected this: ..." or "You said: ...".
- Editor's desk: the items by severity, each with its sections (clickable), note, fix, her note, and Keep, Dismiss and
  Add a note; dismissed items fold under "Dismissed".
- Graphs keep every node and its name inside the card (names wrapped at about 14 characters on two lines, boxes
  pushed apart, edge labels on pills, slid along the edge when two would meet), at 260 pixels high or more and in a
  scroll box past 640; trees wrap names on up to three lines; timeline labels go above and below the rail in tiers so
  none overlap. The block format is unchanged.
- 0.2.1's making cards, polls and kept scroll stay as they were.

Fixture for building the page without the server: `fixtures/desk.sample.json` (0.3, the desk), and `fixtures/bible.sample.json`, `fixtures/tab.characters.json`,
`fixtures/views.sample.json`, `fixtures/pointer.sample.json`.

## 0.3.1: putting a manuscript away

`DELETE /api/manuscript?title` and the tool `manuscript_remove(title)` (a change) move the manuscript's folder to `Manuscripts/Removed/<folder> <stamp>/`. Nothing is deleted. Refused while the manuscript's status is `reading`. The page has a Remove button beside Upload with a confirm.

## 0.3.2: the bot never starts a reading on its own

`manuscript_analyze(title, confirm)`: without `confirm: true` nothing starts and the answer is the question for the writer, with the section count and a time estimate. The page's Start reading and Read again buttons use `POST /api/analyze`, the writer's own hand, and are unchanged. Picking another manuscript clears the say line under the picker.

## 0.3.3: evidence for pictures (the owner, 11-01)

Every picture in Maps shows its proof: the words from the manuscript that told the painter what to draw, each with its
section. The bible (`manuscript-bible/2`, additive) gains:

- `characters[].looks: [{detail, section, quote}]`: appearance and telling physical details ("light hair", "taller than
  the rest", "a burn on her left hand"), one per entry, `quote` at most 120 characters from the section, `section` an
  int. Up to 12 per character. Only what the text says; nothing inferred.
- `characters[].age_words`: the text's own words on age ("a girl of twelve", "the old man"), with `section`, or absent.
- `places[].details: [{detail, section, quote}]`: what the place looks like, is made of, sounds and smells like, its
  weather and light. Up to 10 per place.
- `geography: [{from, to, note, section, quote}]`: distances, travel times and directions between places ("five days
  by cart from A to B", "north of the harbour"), from the text only.
- `setting.look: [{detail, section}]`: the era's and technology's visible marks (lamps, carts, domes, uniforms).

The extraction ask per section asks for these; the merge keeps every entry with its section; nothing is dropped for
length until the caps above. `GET /api/bible` carries them. Maps 2.1 builds prompts from `looks`, `details`,
`geography` and `setting.look` first, and prints them under each picture as the proof.

Also in 0.3.3:

- `characters[].age_words` is `{words, section}`. A section's evidence is asked in the facts ask when the section fits
  the budget with it, else in a small ask of its own; the cache entry keeps it as `looks` (`{}` when the facts ask
  carried it). The World tab shows the distances as a `markdown` heading "Distances and directions" and a `table`
  (from, to, distance or direction, the quote); the places table gains "Details" (the first three, with sections).
- **The desk explains itself.** A desk item is `{id, kind, severity, sections, note, why, where: [{section, quote}],
  fix, state, writer_note}`: `note` one plain sentence naming the problem, `why` what a reader would feel or miss,
  `where` one to three quotes (at most 120 characters) from the facts the desk ask was given, each with the section of
  the fact line it came from, and `fix` written for the item. The desk ask writes up the items found from the facts
  (by their number; they keep their ids) and adds its own. A fix from the template, when the ask gave none, starts
  "A general suggestion: ". `manuscript_desk list` gives note, why and fix per item.
- **Keep manuscripts out of the vault.** The setting `vault_free` (default true) is kept in `settings.json` in the
  app's data folder (`PUFFPAI_DATA`, else `data/`); `GET /api/settings` gives `{settings}`, `POST /api/settings`
  `{vault_free}` saves it and answers `{settings, message}`. Every brain call sends `"as_written": <vault_free>`
  (the manifest declares `needs.brain.as_written: true`). `status.json` keeps the reading's `as_written`, and
  `GET /api/status` and each manuscript's `status` in `GET /api/list` add `vault`: "out of the vault" or "vault on".
- **Real counts.** `GET /api/list` gives each manuscript `counts: {characters, main, threads, places, events, desk,
  desk_high}` from `bible.json` (null before any reading). `manuscript_status` adds, after its progress line, "Found:
  N characters (M main), T plot threads, P places, E events, D items on the editor's desk (H high)."; `manuscript_list`
  gives the same line under each manuscript.

## 0.3.5: PDFs

`pdf_text.py`, the same file in Manuscript and in Document convert, turns PDF bytes into paragraphs with heading marks and
a report, with pypdf. `read_pdf(raw)` answers `Book(paragraphs, report)` or raises `Refused` with one plain line. It never
invents or rewords: its only changes are joining wrapped lines, taking out a hyphen when a word really was split, taking out
running heads, feet and page numbers (found by what repeats at one height), ligatures to letters, odd spaces to spaces,
stray spaces pypdf guessed inside a word, and a line of scene ornaments written `* * *`. A large gap between lines becomes
three blank paragraphs, which `split_scenes` reads as a scene break and which add no words; inside a chapter,
`chapter_text` drops them as it does for a Word file.

- **Upload.** `.pdf` is in `UPLOAD_TYPES`. A PDF's paragraphs go down the same path as a Word file's, keep `original.pdf`,
  and split by pdf_text's marks only: `split_sections(paras, by_words=False)`, so a contents line that reads like a
  chapter makes no section. The answer gains `report`, and `message` ends with its line. `manuscript.json` keeps it as
  `pdf_report`, and `manuscript_list` gives its line under the manuscript. The page shows `message` under the picker.
- **Report.** `{pages, words, pages_no_text, pages_pictures, pages_unread, running_heads_removed, page_numbers_removed,
  hyphens_joined, chapters_found, parts_found, scene_breaks, scene_marks_rewritten, lines_joined, ligatures, odd_spaces,
  stray_spaces_removed, contents_lines, pages_two_up, pages_tables, words_left_out_sideways, scanned_text, warnings}`.
  `pdf_text.report_line(report)` is the plain sentence.
- **`manuscript_add_file(source, title?)`.** `source` is a name or a path inside My files (`Converted/My Novel.docx`), of a
  .docx, .pdf, .txt or .md. A bare name is looked for in `Converted`, then in My files, leaving out `Manuscripts`.
  Refused: `..`, any path outside My files, any link on the way, a name that fits two files. It calls the same `_add` as
  the upload and starts no reading: the answer says the reading waits for the writer's yes and that `manuscript_analyze`
  with `confirm` true starts it. The argument is not called `path` or `file`: the Core reads those as a file being
  changed and hides such a tool while changing documents is off (`owner_files.py`, D140).

