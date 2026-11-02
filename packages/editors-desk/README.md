# Editor's Desk

A publishing house's stages of editing for your book, big to small. Manuscript keeps the book, Maps pictures it, and Editor's Desk does the editing. Your editor works in the background with the model on this computer, and only when you say so. Your bot never writes your book.

## PuffPAI

### What it does

The page leads with your editor's letter: what the book is, what's working, the biggest problems in order, and what to do first. Under it is the next thing to do and why, then five stages, each a row you open:

1. **The story.** Plot and plot holes; Structure and pacing; Characters; Point of view; Opening and ending; Themes and stakes.
2. **The facts.** Continuity; Science and world rules; Real-world facts (it only flags what to look up).
3. **The prose.** Voice; Dialogue; Prose habits.
4. **The polish.** Grammar and syntax; Style sheet; Proofread.
5. **Getting it out.** Reader's eye; Sensitive content and permissions; Length and genre fit; Synopsis and query letter; Submission format.

At any time: **Future plans and changes**. Type "What if Elias survives?" and it traces what that touches and lists what you'd need to revisit. The **revision plan** in the letter keeps up as you mark things.

Each finding says what the problem is, where (its section and exact quote: click to go there), why it matters, and one way to fix it. Mark it Open, Fixed, Not a problem or Later, with a note of your own. A re-run doesn't bring back what you marked Not a problem, and says if something you marked Fixed still looks broken.

**Your text is on the same page.** Open a section, edit it, save. Every save keeps the earlier version, which you can see and bring back. Your uploaded original is never touched. **Use this** (proposed) puts a grammar or proofreading correction in, on your click only, after showing you the before and after.

A check is long. It shows what it is doing, how far along it is and about how long is left, from real timings, and it carries on through page closes and restarts. It waits while Manuscript reads. It never starts by itself: it starts when you press it, or when you say yes in chat.

### What it needs

Manuscript's folder of your books, and for most checks a story bible, so let Manuscript read the book first. Prose habits, Grammar, Style sheet, Proofread, Length and genre fit and Submission format run without one. No setup steps, no internet, and no outside packages.

### Adding a check

Add a text file to `checks/`. Its head says its name, its stage, what it does in plain words, what it reads and how far to trust it. Each `## block` is an ask: `thread`, `character`, `section`, `theme`, `claim`, `speakers`, `opening`, `ending`, `book` or `change` is asked once for each of those; `judge` weighs what code found (`find:` names the code); `write <name>` drafts a document.

### Tools for chat

`desk_checks`, `desk_run` (needs `confirm` true, after her yes), `desk_status`, `desk_findings`, `desk_mark`, `desk_letter`, `desk_ask`, `desk_documents`, `desk_stop`. No tool writes your text.

Cypher Sisters. puff puff pass.
