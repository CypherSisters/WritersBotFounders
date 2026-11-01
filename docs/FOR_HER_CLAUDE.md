# For the AI at the other end: how WritersBot is built, where it gets stuck, how to build on it

You are helping someone run WritersBot on a Jetson Orin, in a container, next to another PuffPAI bot, for a writer who
reaches it from a Windows computer. This page is what the AI that built it would tell you over coffee. It is honest
about what has run for real and what has not.

## The shape

- **BuildBot v2 is the base.** A Core (Flask, Python 3.12 in the container) with seven fixed agents: Archivist (vault and
  keys), Concierge (the chat, the tool loop), Steward (starts apps, each in its own process and venv), Caretaker
  (health), Majordomo (Your turn), Drawbridge (checks every change by fixed rules), Toolsmith (finds and builds apps).
  Apps are packages (`puffpai.json` + their own server, see `BuildBotv2/docs/02_PACKAGE_CONTRACT.md`). The brain is a
  local model through Ollama on the Orin host; the container reaches it through a socat relay on 127.0.0.1:11434.
- **The copy in `BuildBotv2/` is WritersBot's own branch** (`lane/writersbot`, off BuildBot main `ec66a625`; the commit
  is in `BuildBotv2/WRITERSBOT_LANE.txt`). It is frozen on purpose. Its changes from BuildBot main, all small, all in
  `core/puffpai_core`, `packages/desktop/web` and `install.ps1`:
  1. `zone2/apps.py` `puffpai_own`: a folder the owner adds herself counts as her own, so its page frames in the side
     pane (BuildBot's rule frames only built-ins and store apps).
  2. `zone2/data/model_profiles.json`: `qwen3:30b-a3b` gets `think_in_chat: true`, else it writes its reasoning into
     chat answers.
  3. `zone2/skills.py` + `persona.py` + `brain.py`: an installed skill's `SKILL.md` reaches the chat's system message
     when the owner's words match its name or its "Use when" phrases (BuildBot main never read skills at all).
  4. `api/groups/brain.py`, `zone2/brain.py`, `consent/shown.py`, `packages/manager.py`,
     `contracts/puffpai-package.schema.json`: `needs.brain.as_written`. An app that declares it (Manuscript does) may
     send `{"as_written": true}` on `POST /v1/brain/chat`; the privacy gate then passes the words to the local brain as
     written and returns them as written, no vault handles. Said on the app's Yes card. Keys are still stripped.
  5. `packages/desktop/web/window.js`: tools with no page get a real card in the side pane; a framed page may ask the
     window to open a sibling app with a query (Manuscript's "See in Maps"); F5 and Ctrl+R reload; a Reload item in
     the rail.
  6. `install.ps1 -WindowFor` writes a desktop shortcut straight to `puffpai.exe app --remote <address>`.
- **The bot's own apps** are in `packages/` and in `bot/apps/` (the same code, carried by the bundle):
  - **Manuscript** (`app`): upload a Word, text or Markdown file; split into sections (headings, scene breaks, parts of
    at most 2,200 words, short stories into parts of about 1,000); one brain call per section with a JSON-only ask,
    cached by the section's sha; deterministic merge; synthesis calls per character, threads, themes, structure,
    voice, the editor's desk, and a brief per tab. Nine tabs. Tools: list, read, status (with real counts), analyze
    (needs `confirm: true`, so the bot never starts a reading on its own), ask, show (returns at once, fills in later),
    point, notes read and append, correct, desk, remove. The manuscript text is never written by any code path.
    One brain queue per server: the owner's asks go ahead of the reading's next call. Settings: "Keep manuscripts out
    of the vault" (on by default) sets `as_written` on every brain call.
  - **Maps** (`app`): picture-first atlas from the story bible: cast portraits, a painted world map with pins and
    labels drawn by code, a family tree with faces, an illustrated story strip, a cover; Illustrate paints the whole
    book through the picture program with progress and a time estimate; Paint again with a note keeps the face
    (fixed seed per character); the proof under every picture (the evidence the prompt was built from, each with its
    section); the old code-drawn charts under a fold. Tools: list, draw, point, picture, illustrate. It reads only
    `bible.json`, `views.json`, `boards.json`, `corrections.json`, `pointer.json`; it writes `boards.json`,
    `pointer.json`, `pictures.json`, `pictures/`. No brain calls. Pictures go through ComfyUI at one address the
    owner types once and allows with a Yes on the Bridge page (`needs.addresses: lan`).
  - **Novel mode** (`skill`): `SKILL.md`, the playbook the brain follows: ask for the book, read only on the writer's
    yes, say what was found from the status tool's counts, walk the tabs, take corrections and notes, the editor's
    desk last, show and draw on request, never write the book.
- **The two apps share one folder per manuscript**: `My files/Manuscripts/<title>/` in the bot's data folder. That
  folder is the whole contract between them. See `packages/manuscript/CONTRACT.md`.

## Where it gets stuck, in the order it happened to us

1. **The vault locks after five minutes idle**, and anything that needs a key pauses: Toolsmith's cloud builds, and
   nothing else here. Setup's terminal says how to unlock; the window has a PIN box at the top. Nothing in the
   container can change the idle time today.
2. **One model, one request at a time.** A reading, a chat turn and a Manuscript Show all queue on Ollama. While a
   reading runs, chat answers take minutes. The status line says so. This is normal, not a hang. Look at
   `/sys/devices/platform/*gpu*/load` on the host: near 1000 means the chip is busy.
3. **The Core's brain call timeout.** `settings/model.json` in the bot folder has `timeout_s: 900`; the default 180
   made the long synthesis asks fail with "the brain could not answer for five minutes". Keep 900.
4. **After a Core restart, the page listeners for apps stay closed until a signed-in window loads** (the window asks
   for them with `GET /v1/network/pages`). Symptom: Show Manuscript frames a blank pane. Fix: reload the window (F5).
5. **`run.sh start` needs a terminal** (`docker exec -it`). From a script or a non-interactive shell, start the Core
   detached: `docker exec -d <container> bash -c "cd /data/bot/code && exec .venv/bin/puffpai serve >>/data/bot/puffpai/<bot>/logs/serve.out 2>&1"`.
6. **Yes cards from the command line**: `puffpai turn` lists them; `printf 'y\n' | puffpai yes <short id>` takes one.
7. **Updating an app from its folder**: copy the new files over `/data/bot/add/<id>` (the folder it was added from),
   then `puffpai update <id>` and take the card. It re-reads the folder and lists what changed. An update restarts
   the app, so do it when no reading runs. Updating the Core: copy changed files into `<DATA>/code/...`, then restart
   the Core (point 5). Never run `install.sh` again on an installed bot: it makes a second bot.
8. **A small model estimates when it has no tool to stand on.** Every number the chat says should come from a tool;
   the status tool carries the counts for that reason. If it invents a cast, the skill is not in play: check
   `zone2/skills.py` matched the words (the skill's name or its "Use when" phrases must be in the owner's message or
   her last six), and that the Registry row for `novel-mode` is `ready`.
9. **The bible carries `{{vault:...}}` handles** when a manuscript was read with "Keep manuscripts out of the vault"
   off, or before that switch existed: dates and names were filed as personal data. Turn the switch on and read again.
10. **Gutenberg texts** split at scene breaks because their chapter lines ("I.", "II.") are not recognised yet; the
    first section is the licence header. Harmless; roman-numeral headings are on the list.
11. **The picture program.** Maps accepts a home-network address only, so on the same Orin use the Orin's own address
    (`http://10.x.x.x:8188`), not 127.0.0.1. The Bridge card for that address must be taken once. ComfyUI's
    `/models/unet` route is a 404 on some builds; Maps falls back to `/object_info`. A Windows PC's firewall blocks
    the port until its Python is allowed; on Linux nothing blocks by default.
12. **Two bots on one Orin.** Each bot has its own folder and port set (Core 8420 + 10 per bot, its network listener at
    +3, page ports from 19100 + 100 per bot). The container publishes 8423 and 19100-19120; a second container needs
    the next set (`PUBLISH` in `run.sh`). They share the chip and, with the same model name, the model. If the other
    bot holds a different large model, expect reloads and slower turns; a smaller model for one of them helps.

## What has run for real, and what has not

Run on a real Orin, in the container, with the real model: setup, the window from a Windows PC, every app's Yes card,
readings of a short story and a 15,000-word novel under Manuscript 0.3.x, the nine tabs with briefs, corrections,
the editor's desk, Show, the pointer, See in Maps, Maps' painted atlas and Illustrate with the picture program on a
Windows PC on the home network, Paint again, the proof lines (with a 0.3.2 bible, so the fallback wording), Remove.

Never run for real: the bundle import on a fresh computer (`puffpai setup --from`, D141 in BuildBot's decisions),
`install-pictures.sh` on an Orin (the Jetson wheel index and the model files were checked by hand, the script was
not run), ComfyUI on an Orin at all, and Manuscript 0.3.3's evidence extraction on the real model (the looks, the
distances and the richer desk items were tested with a stand-in brain). The first fresh install is the test of all
four. Keep the exact words of anything that fails.

## How to build on it

- **An app**: follow `BuildBotv2/docs/02_PACKAGE_CONTRACT.md`. Copy Manuscript's `server.py` for the token check,
  `GET /v1/owner-files`, the static file routes and the MCP shape. Add it from a folder with `puffpai add <folder>`;
  the owner takes one Yes card. Its page frames in the side pane because of lane change 1 above.
- **A new tab or field in the bible**: add it to the extraction ask in `analysis.py`, merge it, render it in
  `blocks.py`, and extend `CONTRACT.md`. Maps reads the bible and ignores what it does not know.
- **A new board or picture kind in Maps**: `atlas.py` for what the atlas shows, `pictures.py` for prompts and proof,
  `draw.py` for anything code-drawn.
- **The skill**: `packages/novel-mode/SKILL.md`, plain text. Name real tools. A rule the small model must follow has to
  be enforced in the tool as well (as `confirm` on the reading is); words alone are not enough.
- **Tests**: each package has `tests/test_server.py`, run from its folder with a venv that has `pytest` and
  `python-docx`. They use a stand-in Core, brain and picture program, so they run anywhere. BuildBot's own tests run
  from `BuildBotv2` with `PYTHONPATH=core PUFFPAI_KEYRING=stub python -m pytest tests/<file> -q`; run the files you
  touched, not the whole suite.
- **Keep the two layers apart**: BuildBot's rules stay in `BuildBotv2/`; WritersBot's behaviour lives in `packages/`.
  A fix that is BuildBot's belongs upstream (CypherSisters' BuildBot), not here.

## How to make it faster or better

- **The model**: the bot was built on `qwen3:30b-a3b` with an 8,192-token context. A model with a larger usable
  context lets Manuscript send bigger sections and ask for more per call; set `budgets.num_ctx` in
  `zone2/data/model_profiles.json` for the model you pick, and keep `think_in_chat` on for any qwen3.
- **Reading speed**: about four minutes a section on an Orin while the chip is shared. The synthesis steps add a call
  per main character and one per tab. For a long novel, read overnight.
- **Pictures**: SDXL Turbo paints in under a minute on an Orin, FLUX schnell in one to two. Illustrate paints up to
  twelve scenes; cut that in `atlas.py` if time matters more than pictures.
- **The chat**: the skill text costs about 1,800 tokens of the 8,192; it is capped in `zone2/skills.py`
  (`MAX_CHARS`). Shorter skills, or a bigger context, give the model more room for history and tools.
- **Quality**: briefs and desk items are the local model's prose. A better local model is the biggest lever. A cloud
  model for the writing step only is possible in BuildBot's design (Toolsmith's provider) but is not wired for
  Manuscript, and it would send the writer's words off the computer; it needs the writer's informed yes.

## Things to not do

- Do not run `install.sh` or `run.sh install` a second time on an installed bot.
- Do not give the container `docker.sock`, `--privileged` or host networking; it does not need them.
- Do not put a cloud key in the image or an environment variable; keys live in the vault, pasted once on the
  Developer page.
- Do not read the writer's chats, manuscripts or vault to debug; the status files, logs and the Registry are enough.
- Do not "fix" the privacy gate globally. The as-written path is per app and per call, by design.

Cypher Sisters. puff puff pass.
