# For the writer: editing your book in WritersBot

This page is for the person writing the book. No terminal, no setup. It starts after someone has run the update
(`docs/FOR_THE_ORIN.md`, section 4) and you have pressed F5 in your WritersBot window and typed your PIN.

Your bot never writes your book. It reads, points and suggests. Every word in your book is yours.

## Where your book lives

When you added your Word file, the bot made its own working copy, split into chapters. That working copy is your
manuscript from now on. You edit it in the app.

- The Word file you uploaded is never changed. It stays as it was, as a safety copy.
- What you type in the app is saved by itself, a moment after you stop typing.
- When you want a Word file again (to send, print or back up), press **Save a Word copy**. It makes a fresh Word file
  of the book as it stands now.

So there is no need to edit the old Word file and add it again. Edit in the app, and take a Word copy out when you
want one.

## Edit your manuscript, step by step

1. Open your WritersBot window. In the menu on the left, press **Editor's Desk**.
2. At the top, pick your book.
3. Press **Bigger** at the top right of the page. The page takes the chat's room, so you have a wide page to write on.
   **Show chat** brings the chat back.
4. Press **Write**. Your whole book is there, every chapter in order, like a Word document.
5. Click anywhere and type. Enter starts a new paragraph.
   - Ctrl+Z undoes, Ctrl+Y redoes.
   - Ctrl+I makes italic, Ctrl+B makes bold.
   - Ctrl+F finds a word anywhere in the book, and can replace one place or all of them.
   - The chapters are listed on the left. Click one to go there.
6. You do not need to save. The line at the top says "Saved" and the time. To be sure a version is kept at a moment you care about, press Ctrl+S.
7. Changed your mind? Under each chapter heading, **Earlier versions** has what the chapter said before. The text from
   before you ever changed it is always kept there.
8. When you want a Word file: press **Save a Word copy**. It goes in My files, in the Editor's Desk folder, under your
   book's title. Your book in the app is not changed by this.

## Ask your editor about a passage

1. In **Write**, select some words with the mouse: a sentence, a paragraph, a scene.
2. Press **Ask your editor**, the button that appears by your selection.
3. Type your question. "Is this clear?" "Does this drag?"
4. Beside **Ask:**, pick who answers: Dewey, or one of her staff. Each reads through their own job.
5. Press **Ask**. The answer is shown beside your text. Your editor says what works, what does not, and why, and never
   writes sentences for you. Nothing in your book changes unless you type it.

## Your editor and her staff

Press **Your editor**. Dewey, your editor-in-chief, gives a short meeting: what changed since you were last here, what
each desk found, what is running, and what to do next. **Hear it** reads it aloud. The small list beside it picks the voice.

Her staff, one person for each kind of editing:

| Who | Job | Looks at |
|---|---|---|
| Margot | Story editor | The big shape: plot, structure, people, point of view, the opening and ending, the themes |
| Ines | Fact checker | That the book agrees with itself: who, where, when, and the rules of its world |
| Theo | Line editor | How it sounds: the voice, the dialogue, the habits in the sentences |
| Pearl | Copy editor | Grammar, spelling and style slips, the style sheet, and the last proofread |
| Sam | First reader | The finished book as a stranger would read it, and getting it out the door |
| Kit | Storyboard artist | Lays chapters out as picture panels |
| Wren | Illustrator | Paints the panels, if a picture program is set up |

Press a staff card to send that person over the book. It shows the plan and how long it will take, and starts only on
your Yes. **Have the whole staff edit this book** goes big to small: story first, commas last. A full edit of a long
book takes hours. You can keep writing while it runs.

What they find is on **Editor's desk**: each finding at its exact words, with why it matters and one way to fix it. Mark
each Fixed, Not a problem or Later.

## Storyboard

Press **Storyboard**, pick the chapters, and press **Draw the storyboard**. Kit lays them out as panels: what a picture would show,
where, who, the mood, and your own words under each. Every panel gets a sketch. The panels are notes for pictures.
They are never added to your book.

Painting the panels needs a picture program (`install-pictures.sh`, see `docs/FOR_THE_ORIN.md`) and your Yes on the
Bridge page. Without it, the sketches still work.

## Chat

The chat is still there, beside the page. Talk to Dewey the way you would talk to an editor: "what should I work on
today?", "have Theo look at chapter 3", "give me the meeting". She starts an edit only when you say yes.

## Good to know

- One thing at a time. The bot has one brain. If an edit is running, a question waits its turn.
- The pointers are a local model's reading. They are often right and sometimes wrong. You are the author; mark a wrong
  one Not a problem and move on.
- Nothing leaves your computer without your consent.
