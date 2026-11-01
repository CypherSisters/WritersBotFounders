# Novel mode

A skill for WritersBot: how your bot acts as the writer's editor-in-chief for a whole novel or short story, using the
Manuscript and Maps apps.

## PuffPAI

### What it does

It is text your bot reads. When the writer says "novel mode", "read my novel" or asks about the characters, plot,
timeline, world, science, themes, voice or editor's desk of a manuscript, your bot follows this playbook: it asks for
the book in Manuscript, has it read, says what it found, walks the tabs one at a time, opens them beside the chat and
draws them in Maps on request, takes corrections and notes, and reads the editor's desk last. It never writes the book
and never changes the manuscript text.

### What it needs

- The Manuscript app, 0.3 or newer, for the reading, the tabs, the desk and corrections.
- The Maps app, 1.1 or newer, for the drawings and pictures. Without Maps the skill still works; it just does not
  offer to draw.
- Nothing else. It adds no tools, runs nothing and uses no internet.

### Rough edges

- The quality of what your bot says depends on the model on this computer. A thin answer means a thin bible, and a
  re-reading after Manuscript improves helps.
- The skill asks before reading, drawing or opening anything, so a session has a few more questions than a human
  editor would ask. That is on purpose: nothing happens to the writer's work without a yes.
