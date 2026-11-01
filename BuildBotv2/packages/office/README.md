# Office files

Make Word, Excel and PowerPoint files by asking your bot, and change them.

## PuffPAI

### What it does

| Tool | What it does | Changes |
|---|---|---|
| `make_document` | Makes a Word, Excel or PowerPoint file in `My files\Made by <bot name>` in your data folder. It never overwrites a file: a second one with that title is "<title> (2)". | Yes |
| `edit_document` | Adds paragraphs, rows or slides, or replaces text, in a Word, Excel or PowerPoint file. | Yes |

Things you might say: "make a packing list spreadsheet", "add a row for sunscreen to my packing list", "replace Bob with Ann in my letter".

The Knowledge page lists your newest files in My files, each with **Open**.

### Which files it may change

Changing a file is **off** until you turn on "Let your bot change your documents" in Settings. While it's off, `edit_document` isn't offered to your bot at all, and it shows you a fix instead, with the passage highlighted, for you to make. Making a new file stays on. With it on:

- A file your bot made: at once.
- Any other file in your data folder: only after your Yes, one file per question.
- A file in a folder you linked: only after your Yes to "This file is in a folder you linked. BuildBot normally never changes those. Change it anyway?". The original is kept beside it first, as "<name> (before BuildBot).<ext>".
- Anywhere else: never.

The Core checks this before the tool runs (`needs.owner_files`), so it holds for any package that declares it.

### What it needs

- About 100 MB, for python-docx, openpyxl and python-pptx, which its install fetches from PyPI into its own folder. After that it doesn't use the internet.
- A brain that can use tools. No keys. Office itself isn't needed to make a file, only to open one.

### Rough edges

With changing documents off, a private detail held in your vault reaches this tool only as a `{{vault:...}}` mark, so that mark goes in a new file, and the answer says so. With it on, the real text your bot has goes in, but a detail it only has as a mark from an earlier turn or a search stays a mark. Replacing text inside a Word paragraph drops that paragraph's bold or italics, as it did in v1. Excel and PowerPoint files aren't read into your bot's knowledge, so it can change them but can't quote them.

### Data

Your files stay in My files, where you can move or change them. On remove they stay. The package keeps nothing of its own.

### How it runs

A small Python server on 127.0.0.1, started by PuffPAI. It answers MCP at `/mcp` and refuses any call without its PuffPAI token.
