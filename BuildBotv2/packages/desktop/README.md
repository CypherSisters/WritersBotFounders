# PuffPAI Desktop (kind `ui`)

The default Desktop (D3). Plain HTML, CSS and JS in `web/`, no build step. The Core serves the active static `ui` package at `/` (its `index.html`) and its other files at `/ui/<path>`, so every file the Desktop loads names its `/ui/` path itself (no `<base>`: with one, every `#view` link would go to `/ui/#view`, a 404). It talks to the Core API only.

It has the look and the pages of today's BuildBot dashboard (at `774e27a4`), ported to plain JS on v2's routes (lane I8b): night by default, day from 07:00 to 19:00 (she can pick one in Looks or Settings), a left rail, a top bar, glass tiles over the cloud photo, and the footer bar. It works at phone width too (the rail becomes a drawer).

## Files

- `web/index.html`: the frame: rail, top bar, the page area, the chat side panel, the footer.
- `web/logic.js`: the rules, with no DOM or network, checked headless with node (`tests/js/logic.test.js`): tray items and widgets from Registry rows, Yes cards and "Your turn" cards, the rail, Toolsmith's builds and jobs in plain words, the chat stream, the Home layout order, the outside log in plain words, the seven agents, the vault's state.
- `web/theme.js`: day or night before the first paint, and the bot's theme (its CSS variables on `:root`, on top of day or night).
- `web/desktop.js`: the shell: the Core API calls (every one with `X-PuffPAI: 1`), the router, the rail, the top bar, the face, shared cards, live updates from `/v1/events`, and Setup (the wizard's browser renderer).
- `web/pages/*.js`: one file per page.
- `web/fonts/`: Fredoka, Nunito, JetBrains Mono (SIL OFL 1.1) and Material Symbols Rounded (Apache 2.0), each with its license; `fonts/LICENSE-fonts.txt` is the index.
- `web/img/`: v1's cloud photos, and Puff, the owner's own drawing, so the Desktop shows her even before the avatar package is installed (`img/HARVESTED.md`).

Text from packages and the Core is always set as text. Package pages, widgets and the face load from each package's own origin (`http://<id>.localhost:8422`) and never act as the owner (D49, D53).

## Pages and the routes they use

| Page | Routes |
|---|---|
| Home | the greeting with the bot's name; after setup, one question "Add apps from the PuffPAI store?" (`/v1/packages/available`; Not now hides the `store` tile in the layout file); `/v1/registry`, `/v1/toolsmith`, `/v1/toolsmith/builds/{id}`, `/v1/checklist`, `/v1/consent`, `/v1/bridge`, `/v1/model`, each widget on its package's origin; pin, hide, move and rename through `/v1/settings/layout` and `/v1/settings/layout/arrange` (kept in `data\desktop\layout.json`) |
| Chat (page and side panel) | `POST /v1/chat/stream`; Yes cards asked for in chat answer through `/v1/consent/{id}/answer` |
| Apps | `/v1/registry`, `/v1/packages` (add, remove with or without data), `/v1/packages/{id}/start`, `stop`, `update`, `logs`, `open`; an app whose update waits (lane I13's Yes card "<App> has an update", from `/v1/consent?status=pending`) shows Update, which follows that card (lane I16); the PuffPAI store tab: `/v1/packages/available` (the built-in apps and pages not installed, with their icons; other built-in parts below); Add: `POST /v1/toolsmith/plan` |
| Building | `/v1/toolsmith`, `/v1/toolsmith/builds/{id}`, `/v1/toolsmith/jobs/{id}/next` |
| Your turn | `/v1/consent?status=pending` with the tick routes (after a Yes on a plan card the Desktop asks `POST /v1/toolsmith/builds/{id}/start`, which is safe twice; a ticked PuffPAI store app is added by the Core first, with its own Yes card, and the Core starts the build once each has a final answer; the Desktop, Building and Your turn show the Core's `store_adds` progress, so a reload loses nothing; lane I16), `/v1/checklist` (Copy, Done, Skip, Run as administrator), `/v1/notify` |
| Developer | lane I8c's `/v1/dev/shell` routes, `/v1/model`, `/v1/bridge`, `/v1/bridge/ledger`, `/v1/vault/status`, `/v1/audit` |
| People | `GET` and `PUT /v1/people` |
| Settings, Looks | `/v1/model`, `/v1/model/menu`, `/v1/toolsmith/providers`, `PUT /v1/toolsmith/model`, `/v1/providers`, `/v1/bridge/open` and `close`, `/v1/settings`, `PUT /v1/settings/ui` and `/avatar`; Who your bot is: `GET` and `PUT /v1/settings/persona`, `POST /v1/settings/persona/redo`; You: `GET` and `PUT /v1/settings/you` (kept in the vault; locked says so, never an empty form); Looks: the bot's name and theme as swatches (night and day previews), `GET /v1/settings/themes` and `PUT /v1/settings/bot` (lane I10c; until they are there: the name Puff and the Desktop's own palettes); the wizard's look step draws the same swatches |
| Settings, Move this bot | `GET /v1/settings/move`, `POST /v1/settings/move/export`, `/check`, `/import` (D141) |
| Vault | lane L3's `/v1/vault/*` routes; no route returns a key |
| Knowledge | `POST /v1/chat/tool` with `search_knowledge`; Your documents: `GET` and `POST /v1/knowledge/documents`, `POST /v1/knowledge/documents/named` and `/remove`, `POST /v1/knowledge/upload` (V3-1), `POST /v1/knowledge/pinned/fiction` (D146), `POST /v1/folders/link` and `unlink` (D125, D126); `POST /v1/knowledge/pick` (`kind` `files` or `folder`), `GET /v1/packages/sendable`, `POST /v1/packages/<id>/send` (D127); Made by your bot: `GET /v1/knowledge/files`, `POST /v1/knowledge/files/open` (D136); changing her documents: `GET` and `PUT /v1/settings/edits` (D140); manage books: `POST /v1/knowledge/find`, `POST /v1/knowledge/books/remove`, `/look`, `GET /v1/knowledge/books/read`, `/articles`, `/article` (D136); what the builds read, by source, with title, link, license (Wikipedia's CC BY-SA credit), size and date: `GET /v1/knowledge/sources` (lane I16); where books and downloads are kept and moving them: `GET` and `POST /v1/knowledge/place` (D103) |
| Agent Team | the routes above, read only |
| What leaves your computer | `/v1/bridge/ledger`, `/v1/bridge` |

Where v2 has no route yet, the page shows "Coming in a later build" and names the missing part: changing the brain (`PUT /v1/model`), Toolsmith's key from Settings (the vault route takes only names like `ANTHROPIC_API_KEY`), paired devices, linked folders, `puffpai check` results, moving and backing up the vault.

The rail shows only the pages every bot has (lane I10a). Any other page comes with a package, as its own item under "Your apps". Setup is in the rail until it is finished, then on Settings.

Sign in: `puffpai pair` prints a one-time link; the Desktop trades the code for the paired-device cookie (`POST /v1/pair`).
