# WritersBotFounders

A PuffPAI bot for a novelist and short-story writer, built on BuildBot v2 and shaped for a Jetson Orin. The bot reads
the writer's manuscripts on its own computer, keeps a story bible on eight tabs, draws and paints the book, edits it
the way a publishing house does, big to small, and never writes the book itself. The writer talks to it from a window on
a Windows computer on the same home network, or from a browser.

Nothing leaves the computer without the writer's consent.

## What is in this repository

| Folder or file | What it is |
|---|---|
| `bot/` | The bot bundle made by `puffpai export`: its name and look, its role, what opens first, its Home layout, and its own apps. No manuscript, key, chat or model file is in it. |
| `packages/` | The source of the bot's own apps: **Manuscript** (the reading and the eight tabs; it keeps track of everything), **Editor's Desk** (all the editing: the checks, the findings, the editor's letter, her own edits with every earlier version kept), **Maps** (the painted atlas and the picture maker), **Novel mode** (the skill that runs the whole flow from chat). |
| `BuildBotv2/` | The BuildBot code this bot runs on, WritersBot's own version (its changes are listed in `docs/FOR_HER_CLAUDE.md`). |
| `install-writersbot.sh` | One script for the Orin: BuildBot, the bot from the bundle, setup. |
| `install-pictures.sh` | The picture program (ComfyUI) on the same Orin, for Maps. |
| `docs/FOR_THE_ORIN.md` | Step by step for the person at the Orin, including the pictures by hand if the script is not wanted. |
| `docs/FOR_YOUR_WINDOWS_COMPUTER.md` | Step by step for the writer's Windows computer: the window, and a shortcut on the desktop. |
| `docs/FOR_HER_CLAUDE.md` | For the AI helping at the other end: how it is built, where it gets stuck, how to build on it, how to make it faster. |
| `docs/NOVEL_MODE.md` | The design: the tabs, the flow, Maps, pictures, and where editing went. |
| `docs/ROLE.txt` | A fuller role for the bot, ready to paste on Settings, Who your bot is. |

## Quick start, on the Orin

```bash
git clone https://github.com/CypherSisters/WritersBotFounders.git ~/WritersBotFounders
cd ~/WritersBotFounders
./install-writersbot.sh --pictures
```

Setup asks about the model (pick the one it recommends; the bot was built with `qwen3:30b-a3b`), the vault and its PIN,
the Bridge, and Toolsmith's model. When it says BuildBot is running, it prints the home-network address, like
`http://10.0.0.25:8423`. Give that to the writer for the window. Then `docs/FOR_YOUR_WINDOWS_COMPUTER.md`, Part 1.

No sudo on that command, and do not run `BuildBotv2/install.sh`: the script above is the way in. Already have another
PuffPAI bot in a container on this Orin? Ports 8423 and 19100-19120 must be free, or use the other set in
`docs/FOR_THE_ORIN.md`, section 6. The two share the chip and, if you choose the same model, the model.

## The first ten minutes with it

1. Open the window from the desktop shortcut, say "start novel mode".
2. In Manuscript, Upload a Word, PDF, text or Markdown file. The bot reads it; a short story takes about fifteen minutes on
   an Orin, a novel an hour or two. The tabs fill as it goes.
3. Walk the tabs. Correct a fact. Press Open Editor's Desk, and run the first check.
4. In Maps, press Illustrate. Portraits, places, the painted world map, scenes and a cover, with the proof under each.

## Rough edges

- Readings and pictures share one chip with the chat, so chat is slower while either runs, and the status line says so.
- The quality of the briefs and the editing is the local model's. A thin brief means a thin reading; a longer model helps.
- Pictures on an Orin take a minute or two each with FLUX schnell, under a minute with SDXL Turbo.
- The picture program's first run on a real Orin is the test; `install-pictures.sh` has not run on one yet.

Cypher Sisters. puff puff pass.
