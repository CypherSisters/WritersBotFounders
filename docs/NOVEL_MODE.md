# Novel mode: the design

WritersBot's first skill. The writer adds a novel, the bot reads it, every tab fills, Maps draws it, the editor's desk
lists what needs the writer's eye, and every line points back into the manuscript. The writer never has to know
which app does what. This page is the plan for three pieces that are built together: **Manuscript 0.3**, **Maps 1.0**
and the **Novel mode skill**. Nothing here is built yet.

The owner's rules, carried through: the manuscript text is never changed by the bot; the bot reads with the model
on this computer; plain words everywhere.

## 1. The tabs, final list

Six tabs today: World, Characters, Plot and subplots, Themes, Science, Editing. Three were missing for a novel, and
Editing was too thin for what the writer asked for. Nine tabs:

| Tab | What it holds | New? |
|---|---|---|
| **Characters** | every character: role, wants, fears, traits, arc beat by beat, relationships, family, first and last seen, how they change. Supporting cast grouped. | as now, deeper |
| **Plot and subplots** | threads with setups and payoffs, the main plot against each subplot, what is still open, where a subplot helps or steals | as now |
| **Timeline** | every event in story order and in telling order, with the section it comes from; story days and time jumps; the two orders side by side when the telling is out of order | **new** |
| **Structure** | the shape: acts, turning points, the midpoint, the climax; a scene list with purpose, length and point of view per scene; pacing by section (words per scene, dialogue share) | **new** |
| **World** | places, world rules, history, money, manners, technology; where each rule is stated and where it is used | as now |
| **Science** | each claim, where it is, plausible or not, the smallest change that fixes it, what research would settle it | as now |
| **Themes** | themes and motifs with their evidence, images that repeat, what the ending says about each theme | as now |
| **Voice** | point of view and tense per section and any slips; sentence rhythm; repeated words and tics; dialogue habits per character | **new** |
| **Editor's desk** | the consolidated list the writer asked for: plot holes, timeline mismatches, science that fails, character holes (a want that never pays off, a trait that disappears), continuity slips, open questions. Each with its severity, the sections involved, and one suggested fix. Replaces Editing. | **reworked** |

Every item on every tab carries its section numbers, and clicking one opens that section in the manuscript pane
with the passage highlighted. That is the contract's `section` field on blocks, kept everywhere.

## 2. Novel mode, the flow

A skill the brain reads (`packages/novel-mode/SKILL.md`), with a short manifest. It teaches the brain to run this
conversation, using Manuscript's and Maps' tools:

1. **Ask for the novel.** "Add the novel in Manuscript: Upload, or tell me its title if it is already there." The
   brain checks `manuscript_list`. A manuscript that is there but not read gets `manuscript_analyze`.
2. **Reading.** The brain says what is happening and how long it may take, then stays out of the way. The page shows
   "Reading section 3 of 42". When done, the brain says what it found in two lines: how many characters, threads,
   places, and how many items on the editor's desk.
3. **Walk the tabs.** One tab at a time, short: "Characters: eleven, four main. Here is the cast." For each tab the
   brain offers to open it in the pane (`manuscript_show`) and to draw it (`maps_draw`). The writer can stop,
   correct or add at any point: "No, Tom is her cousin, not her brother" becomes a note on the Characters tab
   (`manuscript_notes_append`) and a correction in the bible (`manuscript_correct`, below), and Maps redraws.
4. **The editor's desk last.** The brain reads the desk items in order of severity, one at a time, and for each asks
   whether to keep it, dismiss it, or add the writer's own note. Dismissed items stay listed but folded.
5. **Stay in Novel mode.** From then on, questions go to the book: "show me all the supporting characters and whether
   any of them interact" returns a list under Characters (`manuscript_show`) and a graphic in Maps (`maps_draw`),
   both from the same ask, in one turn. "Where does Ilsa first meet the lamplighter" sets the pointer.

The skill names the tools and the order, and the brain carries the conversation. Everything the skill says the brain
can do is a real tool, so a step is never words only.

## 3. Maps 1.0

A second `app` package, `packages/maps`. It reads what Manuscript writes, from the same files in the owner's data
folder, and draws it big. It never reads the manuscript text itself, only the bible, the views and the pointer.

**Boards** (each a full-pane drawing, zoom and pan, with an export to a PNG made in the browser):

| Board | Drawn from |
|---|---|
| Family tree | `characters[].family` |
| Relationship map | `characters[].relationships`, nodes sized by appearances, edges labelled |
| Timeline | `events` in story order and telling order, two rails when they differ |
| Character touchpoints | a grid, characters by sections, a mark where each appears, lines where two share a scene |
| Arcs | one curve per main character across sections: beats as points, the want above, the fear below |
| Story structure | acts and turning points over the section axis, scene lengths as bars |
| World map | places as nodes placed by how often they are travelled between; a drawn schematic, not geography, unless the writer drags them into place and saves |
| Thread board | threads as lanes, setups and payoffs as pins, open ends marked |
| Ask boards | any drawing the chat asked for, saved with its ask |

**How the two apps link, both ways, with no new Core part:**

- Manuscript writes `boards.json` next to the bible: `{"boards": [{"id", "kind", "title", "asked", "made_at", "spec"}]}`.
  A view that has a drawing twin (a graph, tree, timeline or touchpoint ask) gets a board of the same id. Every
  Manuscript tab shows **See in Maps** at the top, and every view with a twin shows it on its card. The link opens
  Maps on that board.
- Maps reads the bible, the views and `boards.json`. Clicking a node, a pin or a mark in Maps writes `pointer.json`
  (the file Manuscript already polls), so the manuscript pane scrolls to the passage. That is the whole bridge: two
  apps, one folder, two small files.
- Maps' tools for the chat: `maps_draw(title, kind, asked)` makes a board from an ask (the server picks the bible
  facts, asks the model only when the ask needs a choice the data does not make, then lays it out), `maps_list`,
  `maps_point(title, board, item)`. `manuscript_show` for an ask with a drawing twin calls into the same code
  through the shared folder: it writes the board spec, and Maps draws it when opened. One ask, a list and a graphic.
- Opening Maps from the window frames it in the side pane like Manuscript; a board can also open in its own browser
  tab for a big screen, through the Open in browser link the window already has.

**Pictures, honestly.** Every board above is drawn by code, no picture model needed, and that is Maps 1.0.
Generated pictures (a portrait of a character, a painted map, a cover mock) need a diffusion model and a runtime for
it. Three facts shape that step:

- Ollama does not make pictures, so it is a separate program with its own model files.
- The WritersBot container has no graphics access. On your Orin a picture model would run on the host, outside the
  container, which is HomeBot's ground. On your sister's Orin, where WritersBot runs outside a container, it runs
  natively.
- The smallest useful models (SD-Turbo, around 2.5 GB in half precision; SDXL-Turbo, around 7 GB) fit a Jetson Orin
  beside the chat model, at a few seconds per picture, but the runtime install on Jetson (PyTorch with CUDA wheels)
  is the heavy part, and it must not pull the home internet through the bot without a Yes.

**Decided by the owner, 11-01:** pictures are part of the build that goes to her mom. On her sister's Orin, where
WritersBot runs natively and without the owner's own security requirements, Maps' setup must **recommend ComfyUI and
the picture models, and download them with the writer's Yes**: ComfyUI as a program on that Orin, started and watched
by the Steward like any app, and a model that fits beside the chat model (SD-Turbo or SDXL-Turbo first; FLUX schnell
only if the board has the memory and the patience). On the owner's own Orin, for testing now, Maps instead asks for
the address of ComfyUI on her Windows PC over the home network, one Yes card under the address rule, and the Orin
host stays untouched. Same Maps, two ways to reach a picture maker, chosen at setup. **Owner's note 11-01, after the first pictures:** the simplest path for her mom is ComfyUI and FLUX on her mom's own Windows laptop, reached over the home network exactly as this test does, with nothing added to the Orin. **Owner's decision later the same night:** her mom's laptop has no graphics card worth the name, so pictures run on her sister's Orin, natively, beside WritersBot. The steps are `FOR_THE_ORIN.md` in this repo (JetPack 6.2, a ComfyUI in its own venv with NVIDIA's Jetson PyTorch wheels, SDXL Turbo or FLUX schnell, a user service, Maps pointed at the Orin's own home-network address). The laptop path stays in `FOR_YOUR_WINDOWS_COMPUTER.md` Part 2 for anyone who has the card.

So: Maps 2.0 gets pictures, as a Toolsmith job first ("a local picture maker for Maps"), per the app-first rule,
built or adopted on your sister's Orin where it belongs, with the writer's Yes for the download. Maps 1.0 ships
without it and loses nothing the writer asked for in this page, because trees, timelines, touchpoints, arcs and maps
are drawings, not paintings. One line in Maps says "Pictures come later" so nobody hunts for them.

## 4. Manuscript 0.3

- The nine tabs above. Extraction asks grow to cover timeline order, scene purpose and point of view, voice tics,
  and the desk's categories. Short stories split into sections of about a thousand words so the model notices more.
- `manuscript_correct(title, what, correction)`: the writer's fix to a bible fact, kept in `corrections.json` and
  applied on top of every reading so a re-read never undoes it. Shown on the tab as "You corrected this".
- The editor's desk: `desk.json` with items `{id, kind, severity, sections, note, fix, state: open | kept | dismissed,
  writer_note}`, and `manuscript_desk(title, action, id, note)` for the chat and the page.
- `manuscript_show` asks with a drawing twin also write a board spec, and the answer says "and it is drawn in Maps".
- Every tab gets **See in Maps**.

## 5. Order of building, and what each step proves

1. **Manuscript 0.3** (tabs, desk, corrections, board specs). Proof: a reading of the short story fills nine tabs
   and the desk on the Orin; a click on a desk item lands in the text.
2. **Maps 1.0** (the boards, the pointer bridge). Proof: the family tree and timeline of the short story drawn from
   the same bible; a click in Maps scrolls Manuscript.
3. **Novel mode skill.** Proof: a fresh chat, "start novel mode", and the brain walks the flow end to end with a
   real novel, on your Orin, with no help from us.
4. **Maps 2.0, pictures**: a Toolsmith job on your sister's Orin.

Each step is a Yes card on Dewey, and each is a commit in this repo first.

## 6. Decisions needed from you

1. The nine tabs: keep, cut or add.
2. Pictures as Maps 2.0 on your sister's Orin, with Maps 1.0 all drawn: yes or no.
3. The word "desk": Editor's desk, or another name your mom would use.

## 7. Maps 2.0: picture-first (the owner, 11-01 afternoon)

"The picture maker should be the main tool used for the Maps app. These images are bunk and confusing." So Maps turns
around: the picture maker makes the pictures, and code puts the words and the lines on top, because a picture model
cannot be trusted with text or structure.

What Maps shows, in order, for a manuscript:

1. **Cast**: a gallery of portraits, name, role and importance under each; a portrait opens the character's page: the
   portrait large, the write-up, who they are tied to (with their portraits), where they appear, See in Manuscript.
2. **World**: one painted map of the story's places as the backdrop, with the places pinned on it and labelled by
   code; pins start where the old schematic put them and the writer drags them where they belong; Save layout. Each
   place has its own painting on its page.
3. **Family**: the tree drawn by code, with portraits in the boxes.
4. **Story**: the timeline as an illustrated strip, one painted scene per key event with its caption and section;
   events without a picture show as caption cards until painted.
5. **Cover**: a cover mock from the one-line summary and themes.
6. **Charts**: the old boards (relationship map, touchpoints, arcs, structure, thread board), kept under one fold for
   whoever wants them. Not the front.

**Illustrate**: one button per manuscript, and the chat tool `maps_illustrate(title)`. It queues the pictures the
manuscript needs: every main and supporting character, every place, the world map, up to twelve key events, the cover;
minor characters on request. One at a time through the picture maker, with a progress line ("Painting 7 of 23:
Odrey Varrow"), resumable, cached by the prompt so a re-run paints only what changed. Each character keeps a fixed
seed so a repaint keeps the same face. **Paint again** on any picture, with an optional note from the writer ("older,
grey hair") that joins the prompt. All of it stays in the manuscript's folder, in `pictures/`.

**Style**: one style line per manuscript, editable in the Picture maker box, default "painterly, soft light, book
illustration", applied to every picture so the cast and the world look like one book.

**Without a picture maker**: Maps still opens; every picture slot says "No picture maker yet" with the Picture maker
box one click away, and the code-drawn charts remain. On a slow maker (an Orin alone) the progress line says the time.
