# My notes (example page)

An example of a `kind: page` package. By Cypher Sisters, Apache-2.0.

## PuffPAI

**What it does.** Adds a "My notes" page to the Desktop, a tray item that opens it, and a "Today" card (a `list` widget read from `widget.json`). The notes pad keeps its text in this browser, because a static page has no server of its own.

**What it needs.** Nothing. It runs nothing and uses no internet.

**Setup steps.** None.

**How to make your own.** Copy this folder, change the `id` and the files, then run `puffpai check <folder>`. Files only: the Core serves `index.html`, `widget.json` and the icon. Adding it needs no change to the Core.
