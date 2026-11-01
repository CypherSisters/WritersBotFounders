# Manuscript

Keep your novel and short stories in one place and let your bot read them for you. It reads the whole book, section by section, and builds a story bible, shown on nine tabs beside your own notes: Characters, Plot and subplots, Timeline, Structure, World, Science, Themes, Voice and the Editor's desk. Each tab opens with a short brief, a few paragraphs on what that tab adds up to. Your bot helps you think about the book. It never writes it.

## PuffPAI

### What it does

Upload a Word file (.docx), a text file (.txt) or a Markdown file (.md) on the Manuscript page. It is split into sections:

- at each Heading 1 or Heading 2, and at lines like "Chapter 3", "Part Two" or "12". Text before the first heading is section 0, "Before the first chapter";
- with no headings, as in a short story, at scene breaks: a line that is only `***`, `* * *`, `---`, `#` or `~`, or three blank lines in a row. These are "Scene 1", "Scene 2" and so on;
- a section longer than 2,200 words is cut at paragraph breaks into parts, "Chapter 3, part 1", "part 2", so each one fits what your bot can read at once. In a story with no headings the parts are about 1,000 words, so your bot reads it more closely.

Paragraph breaks are kept. As soon as the upload is in, your bot starts reading. The page shows how far it has got ("Reading section 3 of 12") and the tabs fill in as it goes, so you can look while it reads. When it is done the page says which model read it, on this computer.

What each tab holds:

- **Characters**: a paragraph on the whole cast, a card for each main character (role, what they are like, a write-up of who they are and how they change, where they first come in), the supporting and minor cast in a table, a relationship map and the family trees.
- **Plot and subplots**: the events, the threads with their setups and payoffs, and the continuity notes.
- **Timeline**: the events in the order they happen in the story, and the places where the telling runs out of that order.
- **Structure**: the acts, the turning points, the midpoint and the climax, and every scene with its purpose, point of view and length.
- **World**: the setting (the world, where in it, when, the technology), its history as the story tells it, its rules, and the places with where each one is.
- **Science**: each claim, where it is, and whether it holds.
- **Themes**: the themes and their strongest quotes.
- **Voice**: point of view and tense by section with any slips, the words the writing leans on, and how each character talks.
- **Editor's desk**: everything that needs your eye, most serious first: plot holes, timeline mismatches, science that fails, characters whose want never pays off, continuity slips and open questions. Each has its sections and a suggested fix, and three buttons: Keep, Dismiss and Add a note. Dismissed items fold away under "Dismissed". The brief here is a letter from your editor: what is strongest, then what needs work first.

Every section number on every tab can be clicked: it opens that section with the passage highlighted.

**The evidence for pictures.** While it reads, your bot also notes only what the text says about how things look: each character's looks and telling details (light hair, a burn on her left hand) and the book's own words on their age, what each place looks like, sounds and smells like, the distances and directions between places, and the visible marks of the era (gas lamps, carts, uniforms). Each one keeps the section it came from and a short quote. A main character's card shows a Looks line with the sections in brackets, the World tab's places table has a Details column, and a Distances and directions table lists how far apart places are and which way. Maps uses these to draw its pictures and prints them under each picture as the proof. A book read by an earlier version gets them on its next reading, without reading the facts again.

**The editor's desk explains itself.** Each item on the desk names the problem in one plain sentence, says why it matters to a reader, shows up to three passages with their sections, and gives a fix written for that item. When your bot wrote no fix for an item, the desk shows a general one and says so ("A general suggestion: ...").

**Correct a fact.** On Characters and World, or in the chat ("Tom is her cousin, not her brother"), you can say what your bot got wrong. It changes what it can by itself (who is whose cousin, another name someone goes by, a character's role or importance, what kind of place a place is) and marks it "you corrected this". Anything else it keeps as your words on the tab, "You said: ...". Corrections stay when the book is read again.

**See in Maps.** Every tab has a See in Maps link to the same drawing, big, in Maps: Characters to the relationship map, Plot to the thread board, Timeline to the timeline, Structure to the story structure, World to the world map; the other tabs open the list of boards. Each drawing on a tab has the same link above it. When you ask to see something that is a drawing (a family tree, a timeline, who interacts with whom, an arc, the structure, a map), your bot also puts it on Maps' list, and says "and it is drawn in Maps".

Ask it to show you something and it makes a view on the right tab, which stays there until you remove it: "show me the character arc of Jennifer" makes one under Characters. Ask where something happens and the page opens that section, scrolls to the passage and highlights it.

| Tool | What it does | Changes |
|---|---|---|
| `manuscript_list` | Lists your manuscripts, each with its sections, word counts and how far your bot has read it. | No |
| `manuscript_read` | Gives your bot the text of one section, by its number or heading. | No |
| `manuscript_status` | Says how far the reading has got, in one line. | No |
| `manuscript_analyze` | Starts the reading again. Sections that haven't changed are not read twice. | Only its own files |
| `manuscript_ask` | Answers a question from the story bible and the two sections that match it best, naming the sections. | No |
| `manuscript_show` | Makes a view on one tab (a table, a timeline, a relationship map, a family tree, a list or quotes) and keeps it there; a drawing also goes on Maps' list. | Only its own files |
| `manuscript_point` | Points the page at a passage: by the exact words, else the closest run of five or more words, else the paragraph closest to them. | Only its own files |
| `manuscript_correct` | Corrects one fact in the story bible, in your words, and keeps the correction for every later reading. | Only its own files |
| `manuscript_desk` | Lists what is open on the editor's desk, most serious first, or keeps, dismisses or adds your note to one item. | Only its own files |
| `manuscript_notes_read` | Gives your bot your notes on one tab. | No |
| `manuscript_notes_append` | Adds a dated section to the end of one notes file. Your earlier notes stay as they are. | Yes |

Things you might say: "show me the character arc of Jennifer", "show me where the ferry is first mentioned", "what does the story say about the tide", "is the timeline in chapter four consistent", "Tom is her cousin, not her brother", "what is on the editor's desk", "dismiss the second desk item", "add to my science notes that the moon's orbit takes 40 days".

### Which files it may change

Your manuscript is never changed. No tool and no button writes section text: the upload makes the section files once, and after that they are only read. If you change your book, upload the new version; it comes in as a new manuscript beside the old one.

The files that change are its own, and your notes:

- your notes, by your own Save on the page or by `manuscript_notes_append`, which only adds to the end. If your bot adds a note while you have that tab open, Save tells you instead of writing over it; press Reload to see both;
- `bible.json`, what your bot has read; `views.json`, the views you or your bot made; `pointer.json`, where it is pointing; `status.json`, how far the reading has got; `cache\`, one file per section it has read;
- `corrections.json`, your corrections; `desk.json`, what you kept, dismissed or noted on the desk;
- `boards.json`, which it shares with Maps: it adds a board for each view that is a drawing and leaves every other board in the file as it was.

Everything is in `My files\Manuscripts\<title>\` in your data folder:

- the file you uploaded, as `original.docx` (or `.txt`, `.md`)
- `sections\NN - <heading>.md`, one file per section
- your notes: `characters.md`, `plot.md`, `world.md`, `science.md`, `themes.md`, and `editing.md`, which is the Editor's desk notes. `timeline.md`, `structure.md` and `voice.md` are made the first time you write notes on those tabs
- `manuscript.json`, the list of sections and their word counts

Manuscripts added with 0.1.0 keep their `chapters\` folder and open as before. A manuscript read by 0.2 fills the new tabs when you press Read again. Sections it already read keep the facts they had and are asked only how they are told, so their setting and history clues are not asked for again; the World tab then has less to go on until you upload the book again.

### What it needs

- The brain on this computer, to read. Every ask goes to your bot's own model through PuffPAI; nothing goes anywhere else. No keys.
- About 10 MB, for python-docx, which its install fetches from PyPI into its own folder. After that it doesn't use the internet.
- Maps, for the See in Maps links. Without it the links open a page that is not there.

**Keep manuscripts out of the vault.** A switch under Settings, above the tabs, on to start with. On: dates, names and places in your manuscripts are treated as the work and go to your bot's brain as written. Off: they are filed in your vault like anything else you tell your bot, for someone whose manuscripts hold real people's details. A manuscript already read keeps what it was read with until you read it again; the line under the picker says "(out of the vault)" or "(vault on)". The switch is kept in Manuscript's own data folder.

### Rough edges

Not yet proven on the owner's computer with the real model: the tests use a stand-in brain with set answers, so how good the story bible, the briefs and the desk are depends on the model, and has not been checked on a real book. The page has not been checked in a browser for this version. A small model will miss things and may call something a mismatch that isn't. A first name on its own is taken as the one known character with that first name, so a second Jennifer who first comes in as just "Jennifer" is joined to the first.

Reading is slow on a small model, a few minutes a section, and 0.3 asks more of it: two asks per section, then one each for the story order, the structure, the world, each main character, every four supporting characters, the desk and each of the nine briefs. Long books take hours. A reading stops if the brain can't answer for five minutes; start it again and it picks up the sections it already read. If Manuscript stops while reading, the page says so and you start it again.

A correction it cannot match by the names in your words stays a note; it does not guess. Desk items are matched across readings by what they say, so if your bot words an item differently on a later reading, your Keep or Dismiss does not carry over to it. Removing a view does not remove its board from Maps; remove that in Maps.

Bold, italics, footnotes, comments and tracked changes in a Word file don't come across; each section is plain text with its paragraphs. A line that starts "Chapter" or "Part" followed by a number, a roman numeral or a number word starts a new section, so a sentence like "Part two of the plan failed." on a line of its own would split there. In a story with no headings, a line that is only `---` or `#` is taken as a scene break. Uploading the same book again makes a second copy, "<title> (2)", rather than replacing the first.

### Data

Your manuscripts, notes and the story bible stay in My files, where you can open, move or back them up. On remove they stay. The package keeps nothing of its own.

### How it runs

A small Python server on 127.0.0.1, started by PuffPAI. It serves its page at `/`, answers MCP at `/mcp`, and refuses any call without its PuffPAI token. It asks PuffPAI where My files is each time, and reads with one thread per manuscript.

### Putting a manuscript away

The Remove button beside Upload, or the `manuscript_remove` tool, moves a manuscript's whole folder into `Removed` inside `My files/Manuscripts`: the original file, the story bible, views, notes and pictures go with it. Nothing is deleted; move the folder back out of `Removed` to have it again. It is refused while your bot is still reading that manuscript.
