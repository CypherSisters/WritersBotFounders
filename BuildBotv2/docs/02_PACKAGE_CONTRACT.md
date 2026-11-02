# The package contract: how to build anything that works on PuffPAI

Status: design draft for the owner's review (2026-10-14). **Everything here is PROPOSED; nothing is built yet**, including `puffpai check` and every route named. Architecture: `01_ARCHITECTURE.md`. Decisions: `00_INTENT_AND_DECISIONS.md` (D5 = Manifest + MCP; D19 to D23 = zone providers). Zone provider contracts: `zones/`.

This is the developer contract: the PuffPAI equivalent of "how to package software for Linux". A person or an AI that follows this page should end up with something that installs, runs, shows up in the tray and can be used by the brain, on any PuffPAI bot, with zero changes to the Core.

## A package is

**One folder (or git repo) with one `puffpai.json` at its root, plus whatever the package needs to run.**

- The manifest is data. The Core reads it and never runs it.
- The package's code runs as **its own process** (or is plain files, for skills, avatars and knowledge). The Core never imports it.
- The package's abilities reach the brain over **MCP**.
- The package's screens are web pages it serves itself.

## Kinds

| kind | What it is | Must contain | Runs a process? |
|---|---|---|---|
| `app` | Something with its own screen and data (a recipe box, a news dashboard) | `puffpai.json`, a `run` block, at least one `ui.page` or `ui.open` | Yes |
| `tool` | Abilities for the brain with no screen of their own (a converter, an API client) | `puffpai.json`, an MCP server | Yes, or stdio on demand |
| `skill` | Instructions the brain reads when needed (agentskills `SKILL.md` format) | `puffpai.json`, `SKILL.md` | No (scripts it names run only through a Yes) |
| `page` | Extra screens or widgets for the Desktop, with no backend logic | `puffpai.json`, static files | No (the Core serves the files) |
| `ui` | A whole replacement Desktop (kiosk UI, phone UI, a new look) | `puffpai.json`, a `run` block or static files, built only on the Core API | Yes or static |
| `avatar` | A face for the bot | `puffpai.json`, a `.glb`, an optional voice id and preview image | No |
| `look` | A look for the Desktop: colours, corners, fonts, spacing (D76) | `puffpai.json` with a `look` block of CSS variable values; no CSS file, no script | No |
| `agent` | An extra agent beside the fixed seven | `puffpai.json`, an MCP server whose tools are its intents, a declared `scope` | Yes |
| `knowledge` | Books, ZIMs, PDFs, datasets for the brain to search | `puffpai.json`, a `sources` list (URLs + hashes) or files | No (Archivist indexes it) |
| `bundle` | A named bot recipe: a list of packages, wizard steps, persona, default avatar and UI | `puffpai.json` with `includes` | No |
| `wizard` | The first-run flow; its steps are data, so bundles can add, remove or reorder them | `puffpai.json`, a `steps` list, step pages | Static |
| `provider` | Fills a zone slot: a vault store, an unlock factor, a detector, a label pack, a model runtime, a Bridge enforcer, a router or DNS-filter link, a block list, a frontier model for Toolsmith (D19, D35, `zones/ZONES_TOGETHER.md` section 1) | `puffpai.json` with `provides`, plus a process that answers its slot's operations, or a data pack (label packs, retention packs, `rules` detectors, feeds) | Yes, or No for data packs |

Registry rows of kind `program` and `desktop` (programs already on the owner's computer) are not packages and have no `puffpai.json`; they are a Core feature (PROPOSED, 01 "The owner's own programs").

## `puffpai.json`: full example (an app)

```json
{
  "schema": "puffpai-package/1",
  "id": "freshtable",
  "kind": "app",
  "name": "FreshTable",
  "version": "2.0.0",
  "description": "Recipes, a shopping list and a meal plan for the week.",
  "author": "Cypher Sisters",
  "license": "MIT",
  "homepage": "https://github.com/example/freshtable",

  "run": {
    "windows": { "type": "command", "python": true,
                 "setup": ["{python} -m pip install -r requirements.txt"],
                 "start": ["{python}", "-m", "freshtable", "--port", "{port}"] },
    "default": { "type": "docker_compose", "file": "compose.yaml" }
  },
  "health": { "url": "http://127.0.0.1:{port}/health", "expect": 200, "every_s": 60 },

  "abilities": {
    "mcp": { "transport": "http", "url": "http://127.0.0.1:{port}/mcp" },
    "changes": ["add_recipe", "add_to_list", "clear_list"],
    "examples": { "add_to_list": ["put milk on the shopping list"] }
  },

  "ui": {
    "pages": [ { "id": "main", "title": "FreshTable", "path": "/", "show": "in_desktop" } ],
    "tray":  [ { "label": "FreshTable", "icon": "icons/pot.svg", "opens": "main" } ],
    "widgets": [ { "id": "list", "type": "list", "title": "Shopping list", "path": "/widget/list" } ]
  },

  "needs": {
    "packages": [],
    "hosts": [],
    "keys": [],
    "hardware": { "ram_gb": 1 },
    "brain": { "text": true, "images": false }
  },

  "accepts": [ { "types": [".pdf", ".txt", "image/*"], "tool": "import_recipes" } ],
  "setup_steps": [],
  "data": { "keep_on_remove": "ask" },
  "consent": "FreshTable stores your recipes and lists in its own data folder on this computer. It does not use the internet."
}
```

## Field reference

| Field | Required | Rules |
|---|---|---|
| `schema` | yes | `puffpai-package/1`. The Core refuses schemas it doesn't know, with a plain reason. |
| `id` | yes | `^[a-z][a-z0-9-]{1,39}$` (no `_`, D55), unique in the bot. Tools are shown to people as `<id>.<tool>` and sent to models as `<id>__<tool>`, because many model APIs reject dots in tool names. |
| `kind` | yes | One of the kinds above. |
| `icon` | Level 3 | An SVG or PNG inside the package. Used in the Installed list, the store, and as the default tray icon. |
| `name`, `description`, `version`, `author`, `license` | yes | Plain text. `version` is semver. `description` is what the brain and the store show, so write it for both. |
| `run` | app, tool, ui, agent, provider with a process (page: static only) | Per-OS blocks (`windows`, `mac`, `linux`, `default`). `type`: `command` \| `docker_compose` \| `static`. `command`: `setup` is a list of commands, `start` is one command as an argument list; see "How the Core runs a package". A package whose only process is an MCP server over stdio may have `setup` and no `start` (D124): the Core starts it on demand, and it has a `health` of its own. `static` = `{ "type": "static", "dir": "web" }`, served by the Core. A `command` block may also carry `env` (values may use placeholders) and `cwd` (a folder inside the package); `setup` items may be `{ "copy": [from, to] }` or `{ "venv": true }` (D51). |
| `health` | if it runs a process, or has `ui.open` | `{ "url": "http://127.0.0.1:{port}/health", "expect": 200 }`, `{ "command": ["{python}", "check.py"], "expect_exit": 0 }` or `{ "file": "<program path>" }` (a desktop app counts as installed while its program file exists), plus `every_s`. Caretaker runs it. Without it, the Registry row can never say `running`. |
| `origin` | written by the Core | `core` \| `store:verified` \| `store:community` \| `github:<url>` \| `local:<path>` \| `adopted:<url>` \| `made`. Authors don't set it. |
| `abilities.mcp` | tool, agent; app optional | `transport`: `http` \| `stdio` (`{ "transport": "stdio", "command": "python server.py" }`). Stdio servers are started on demand by Steward; their health check is a successful MCP `list_tools`. MCP tool descriptions are what the brain reads, so write them plainly. **The MCP server's `list_tools` is the source of truth for the tool list**; the manifest doesn't repeat it. The MCP endpoint must refuse calls without the package's `PUFFPAI_TOKEN`. |
| `abilities.changes` | if any tool changes things | Tool names (without the `<id>.` prefix) that change data, spend money, send messages or touch devices. Drawbridge reviews them, and the owner may require a Yes. Undeclared tools are treated as read-only, with two exceptions (PROPOSED, X27): a tool whose own MCP annotations say `destructiveHint: true` or `readOnlyHint: false` counts as changing anyway; and for manifests Toolsmith writes (adopted and made packages), Drawbridge classifies every tool itself from its annotations and rules, a tool it can't classify counts as changing until the owner marks it read-only, and Toolsmith's list can only add to that, never remove. Calls inside what the owner approved pass Drawbridge's rules check with no card (`09_AGENT_SECURITY.md` section 3). Lying here fails `puffpai check` and Verified review. |
| `abilities.examples` | optional | `{ "<tool>": ["a sample ask", ...] }`. Used by Level 2 item 12; if missing, the check writes a sample ask from the tool's description. |
| `ui.pages` | app, page, ui | `{ id, title, path \| file, show }`. `path` is served by the package's own process; `file` is a static file in the package. `show`: `in_desktop` (packages built for PuffPAI, i.e. not `adopted`; D13) \| `new_tab` (always the case for adopted outside apps, which use `url` with their own address). |
| `ui.open` | app with no web page (a desktop program) | `{ "program": "<full path or {pkg} path>", "args": [...] }`. Open launches the program in its own window (the one on-purpose visible window, see "No console windows"); the tray item says Open, "Opens in its own window". Pair it with `health: { "file": "<program path>" }`. Toolsmith writes it when a try ends with `done({"program": path})` (03). |
| `ui.tray` | optional | Label, icon (SVG or PNG in the package), what it opens. |
| `ui.widgets` | optional | `{ id, type, title, path \| file }` (`file` for static packages). The `path` or `file` returns JSON by type: `list` `{ "items": [{ "text" }] }`, `number` `{ "value", "unit", "label" }`, `image` `{ "src", "caption" }`, `feed` `{ "items": [{ "title", "url", "time" }] }`; `html` returns a page shown in a sandboxed frame. |
| `needs.packages` | optional | `[ { "id": "x", "version": "^1.2" } ]` (npm-style semver ranges). |
| `needs.hosts` | if it uses the internet | Each host it talks to while running. Shown on the Yes card and allowed for this package only. |
| `needs.install_hosts` | optional | Hosts needed only during install (package registries, Docker Hub, GitHub). Allowed during install, closed after. |
| `needs.keys` | if any | `{ "name": "NASA_API_KEY", "why": "...", "get_it": "https://..." }`. Stored in the vault and injected at start as an environment variable. |
| `needs.hardware` | optional | `ram_gb`, `gpu`, `disk_gb`, `docker: true`. The wizard uses this to warn before install. |
| `needs.owner_files` | optional (`write` built, D136) | `"read"` \| `"write"`. For a package that works on the owner's own files (an Office tool, an app that edits her documents). Shown on the Yes card. `read` lets it read files the owner hands it. `write` lets it write in `<data folder>\My files\Made by <bot>\`; any other file in her data folder changes only after a Yes per file, and a file in a linked folder only after a plain extra line, with the original kept as `<name> (before BuildBot).<ext>`, which the package does. Changing a file is off until she turns it on (D140); with it on, a package with no hosts or addresses gets her real text in its arguments, from a local brain with the vault open. The Core enforces this before the call, for a tool listed under `abilities.changes` that has a `path` argument. A package with this field and its own token may ask `GET /v1/owner-files` where it makes files (`{made}`) and where one file is (`?path=`, `{path, place: made \| mine \| linked}` or `{line}`). First user: `packages/office`, kind `tool`, with `make_document` and `edit_document` for Word, Excel and PowerPoint files, a normal, removable built-in (D17, D136). A folder the package keeps its own bookkeeping in holds a file named `.app-files` (below). |
| `needs.addresses` | optional | `"lan"` \| `"internet"`, nothing else. The package may ask, with its own token, to reach one exact address and port of that kind: `"lan"` a private address or a name ending in `.local` or `.lan`, `"internet"` a public DNS name, never a public address given as numbers and never its sub-hosts. It may not ask for the other kind. Shown as a line on the Yes card. It grants nothing: each address needs the owner's own Yes on its own card (`POST /v1/bridge/addresses/ask`, D110). The owner's form on the Bridge page needs no field and may allow either kind, for any installed package. |
| `needs.brain` | optional | `{ "text": true, "images": false }` if the package asks the bot's local model for help, through the Core API `brain` route, never directly. `images: true` warns on Yes when the chosen model can't read images. |
| `accepts` | optional | `[ { "types": [".pdf", "text/*"], "tool": "<tool that imports it>" } ]`. The Desktop and chat offer "send this file to <app>", which calls that tool with the file (built, D127; `POST /v1/packages/<id>/send`). The tool gets `{ "file_name", "mime", "text" }` for text files or `{ "file_name", "mime", "content_b64" }` otherwise (D48c). |
| `setup_steps` | optional | Checklist steps (see below) the owner must do. |
| `data.keep_on_remove` | optional | `ask` (default) \| `keep` \| `delete`. |
| `consent` | yes | One or two plain sentences shown on the Yes card. |
| `includes` | bundle | Package ids or git URLs, wizard step changes, `persona`, `avatar`, `ui`. |
| `scope` | agent | The intents it handles. It may never ask for vault keys or a cloud model. |
| `avatar` | avatar | `{ "preview": "face.svg", "model": "face.glb", "voice": "<voice id or null>" }`. `preview` (SVG or PNG) is required; `model` (a .glb) is optional (D48a). |
| `look` | look | `{ "mode": "night" \| "day" \| "both", "line": "<one sentence>", "tokens": { "--bg": "#0F1115", ... }, "day_tokens": { ... }, "preview": "preview.svg" }` (D76). `tokens` is the night palette (or the only one), `day_tokens` the day one and required for `both`. Names are CSS variables the Desktop reads (`packages/desktop/web/desktop.css`: its palettes and `:root`, including `--round`, `--glass`, `--shine`, `--gap`, `--tile-pad`, `--edge`, `--display`, `--body`, `--text-size`, `--on-primary`). Values: only letters, digits, spaces and `# % . , ( ) " ' + * / - _`, calling only `rgb`, `rgba`, `hsl`, `hsla`, `color-mix`, `linear-gradient`, `radial-gradient`, `calc`, `var`, `min`, `max`, `clamp`. No `url()`, no `@`, nothing that loads anything. A look has no `run`, `abilities` or `ui`. An SVG `preview` (here or on an avatar) is a plain drawing: no script, no `on...=` handler, no outside link. |
| `sources` | knowledge (a `.zim` she drops in becomes one by itself, `kn-own-<name>`, D136) | `[ { "url", "sha256", "size_mb", "license", "title" } ]` or `files: [paths]` inside the package |
| `abilities.accepts_labels` | optional (PROPOSED) | `{ "<tool>": ["person.phone", ...] }`. By default a tool gets handles (`{{vault:<ref>}}`), never held values. A tool listed here may receive those label kinds as values, and only under the label's `share` rule (D31(a)): by default a Yes per send (`ask_each`, bound to that one payload); "always for this app and this kind of data" if the owner chooses it, with a warning and the PIN or Windows Hello (D29; bound to package + kind, never a payload; never for P4); never for secrets (keys, tokens, passwords). The Core records what it delivered as the package's `taint` (01 Registry, `zones/ZONES_TOGETHER.md` X19). |
| `data.labels` | optional (PROPOSED) | `[ { "path": "meds[]", "label": "health.medication" } ]`: which fields of its own data hold which label (`zones/Z1_CLASSIFY_AND_LABEL.md` section 8). Archivist labels by field, not by guessing. |

**Provider fields** (kind `provider`, PROPOSED, D19 to D23; full rules in `zones/`):

| Field | Required | Rules |
|---|---|---|
| `provides` | yes | `[ { "slot": "zone<N>.<slot>", "contract": 1 } ]`. Slot names only from the table in `zones/ZONES_TOGETHER.md` section 1. A list: one package may fill two slots. |
| `reads` | if it runs a process | `raw` \| `gated` \| `ciphertext` \| `none`. Must be true. Every provider that sees real values is `raw` (D31(c)): `zone1.detector` (process form), `zone2.runtime`, `zone2.speech-in`, `zone2.speech-out`, `zone2.vision`. `raw` readers get no network, no `PUFFPAI_TOKEN`, no keys (X13), Verified and owner-made ones too; community ones stay off until the owner turns each one on (D28(4)). Zone 1 vault providers must be `ciphertext` or `none`. |
| `abilities.mcp.audience` | providers with a process | `"core"`: the slot operations are called only by the Core and are never shown to the brain. |
| `floor.attests` | optional | X items the author claims (`zones/ZONES_TOGETHER.md` section 4). `puffpai check` runs them; a mismatch fails Level 1F. |
| `abilities.watch` | `zone3.network-device` | Read-only operations (`read_state`, `report`, `alive`). `abilities.changes` holds `apply_change`, `undo`; empty = watch only. A `zone3.net-monitor` or `zone3.policy-feed` with any `changes` fails (`zones/Z3_BRIDGE_PROVIDERS.md` section 7). |
| `devices` | `zone3.network-device` | `[ { id, kind: router \| firewall \| dns_filter, ask, lan_only, ports } ]`. The owner types the address at setup; it becomes an exact LAN grant at the Yes. |
| `needs.keys[].hand` | optional | `"per_action"`: the key is not injected at start; the Core hands a short-lived credential per action, for one `device` (X5, X11). |
| `needs.admin` | optional | `true` for an enforcer that needs one-time admin. **Verified providers only** (D31(d)): a community provider that declares it does not install. Shown on the Yes card (floor-level: PIN or Windows Hello, D29), raised as a "Your turn" step. |
| `detector` | `zone1.detector` | `{ form: rules \| process, emits, paths, langs, budget_ms }` (`zones/Z1_CLASSIFY_AND_LABEL.md` section 8). |
| `labels` | `zone1.label-pack`, `zone1.retention`, optional on `zone1.detector` | Path to the pack JSON. |
| `store` / `unlock` / `backup` | `zone1.vault-store` / `-unlock` / `-backup` | Slot facts the Core shows on the Yes card (`remote`, `location_text`, ...; `zones/Z1_VAULT_PROVIDERS.md` section 7). |
| `toolsmith_model` | `zone3.toolsmith-model` (D35) | `{ company, hosts, key: { name, header, format, shape, get_it }, models: [ { id, name, context_tokens, prices: { in, out, cache_read, cache_write } } ], default_model, prices_checked, cost_cap: { per_build_usd }, consent }`. `company` is the name the owner sees. `hosts`: exact HTTPS hosts, the only ones the Core sends to for this provider. `key.format` holds `{key}`, which only the Core fills. Prices are US dollars per million tokens. `consent` names the company and lists every item Toolsmith sends (03 step 5 notes); its hash is what the owner said yes to. The adapter process gets no network, no `PUFFPAI_TOKEN`, no keys and no proxy variables, whatever its `reads` value; it speaks only the slot's two operations (04 quick start 11). |

## A package's own files in My files (`.app-files`, WB2)

A package that keeps its own working files in My files puts a file named `.app-files` in that folder. Its content does not matter. That folder and everything below it then belong to the app:

- The Knowledge page's Your files and `GET /v1/knowledge/files` leave them out, and so does the report on a linked folder, which is also what `send_document` can name.
- The knowledge index does not walk them: not read, not sent through the privacy check, not searchable by the brain. What the index held from there from before goes at its next pass. A document the owner pinned in such a folder is still read, as she chose it.
- Nothing moves and no path changes. A file there can still be opened or changed by its full path, under the same rules, and moving a bot takes it along.

The package reaches those files itself and shows them on its own page. Only a folder of bookkeeping gets the mark: what the owner made for herself, like `Made by <bot>`, stays listed. The Core knows the file name and nothing about the package. First user: Manuscript, on `My files/Manuscripts`.

## How the Core runs a package

- **No shell.** Steward runs each `setup` command and the `start` command directly as a program with arguments, with the working folder set to `packages/<id>`. So `&&`, pipes and PowerShell-only syntax don't apply. Several steps go in the `setup` list.
- **Placeholders** the Core fills in: `{port}` (a free port it assigns, see "Ports"), `{python}` (the package's own Python, see below), `{node}`, `{data}` (its data folder), `{pkg}` (its package folder).
- **Python and Node stay inside the package.** `"python": true` makes the Core create `packages/<id>/.venv` before `setup`, and `{python}` points at it, so `pip install` never touches the computer's own Python. `npm install` already installs into the package folder.
- **Environment** given to every process: `PUFFPAI_PORT`, `PUFFPAI_DATA`, `PUFFPAI_API` (Core API address), `PUFFPAI_TOKEN`, the declared keys, and `HTTP_PROXY` / `HTTPS_PROXY` pointing at the Bridge proxy. Exception (PROPOSED): a provider with `reads: raw` (detector process form, `zone2.runtime`, `zone2.speech-in`, `zone2.speech-out`, `zone2.vision`; D31(c)) gets none of `PUFFPAI_TOKEN`, the keys or the proxy variables, only stdin and stdout or its Core-only MCP endpoint (X13). A runtime that downloads models gets the proxy variables only during a model-download window the owner starts (`phase: install`). A provider's token grants are per slot: it sees only its slot's inputs and events (`zones/ZONES_TOGETHER.md` section 2).
- **`PUFFPAI_TOKEN`** is this package's own key. It lets the package call only these Core API routes: `brain`, `notify`, `theme`, `people` (read only, `GET /v1/people`, so apps use the Core's shared people list, D12), its own Registry row, its own Checklist steps, `POST /v1/bridge/addresses/ask` (asking for one address for itself when it declares `needs.addresses`; this never grants, D110), and `GET /v1/owner-files` when it declares `needs.owner_files` (D136). The Core uses the same token to call the package's MCP endpoint, which must refuse calls without it. That way no other program on the computer can call a `changes` tool and skip Drawbridge. **Every HTTP route the package serves must refuse a request without this token** (D48d); its GET and HEAD routes never change anything (D55), not only `/mcp`; the Desktop reaches its pages through `/pkg/<id>/`, where the Core adds it.
- **Pages go through the Core, on their own address (D49).** The Desktop loads a package page at `http://<id>.localhost:8422/<path>`: the pages listener, with its own origin per package (D53), never the Core's own address. The Core forwards it to `127.0.0.1:{port}` and adds the package's token. On the pages address there is no owner login: a page's JavaScript can call its own package and the Core API routes its token allows, and nothing else. Adopted outside apps are the exception: they open in a new tab at their own address and keep their own login.
- **Install in place.** `add` fetches into `packages/.staging/<random>`. That folder holds no running code and nothing starts from it. After the Yes, it is renamed to `packages/<id>` **before** setup runs, so venvs and compose paths are created in their final place and never moved. `puffpai add <folder>` copies the folder; `puffpai add <folder> --link` (the Add page's "A folder of my code" does the same, PROPOSED, 01 "The owner's own code folder") runs a folder in place (`origin: local:<path>`). For a linked package, "its own folder" below means that folder: its writes go there (venvs, `node_modules`) and to `data/<id>`. A missing manifest is written to `<folder>\.buildbot\puffpai.json`, never to her folder's root. Remove never deletes a linked folder.
- **Ports.** `{port}` is free on both 127.0.0.1 and ::1, is not a port any running program already holds, and is not on the owner's reserved list (`settings\ports.json`, seeded with the known service ports already on this computer). A package never takes the port of something already on the computer, including a port an app hard-codes: that is a run note for Toolsmith to change, not a reason to stop the other program. (v1 `ffa72638`, `b6dcad41`; harvest `reserved_ports`, `taken_ports`, `_free_port`.)
- **After start, Steward checks what really got published.** For Docker it reads the ports Docker published, for every compose file and `docker run` in the start, not just the first; for native servers it reads their listen addresses. Anything on all networks (0.0.0.0 or `::`) is stopped at once, and the Registry row goes `broken` with a plain reason. (v1 `6a30ab66`, `88556fe6`, `4617bfe5`; a verifier saw MongoDB, Redis and Elasticsearch on 0.0.0.0. Harvest `Run._stop_exposed`, `Run._compose_published`.)
- **Docker packages run behind the Docker wall** (D30, first release; PROPOSED). Steward never edits the app's compose file; it adds an override that puts every service on a private `internal: true` network for that package, with one small Core gate container as the only way out, forwarding to that package's own Bridge proxy port, and carrying the app's page in on 127.0.0.1. `network_mode: host`, other networks, `privileged` and the engine socket are refused. Some apps that expect open internet need extra allow steps (one Yes per host). Details and honest limits: `zones/Z3_BRIDGE_PROVIDERS.md` section 6.1.
- **No console windows on Windows.** Every program start uses hidden-window flags. The one on-purpose exception: opening her browser or a desktop program (`ui.open`). (Harvest `procflags.hidden` `:53`.)

Things a package may NOT do (each is a `puffpai check` failure):
- Write outside `packages/<id>` (or its linked folder) and `data/<id>`. Owner files only as `needs.owner_files` allows.
- Bind to anything other than 127.0.0.1 (the owner can open it to the home network separately).
- Mount the Docker engine socket, or run `--privileged` or with `pid: host`, without an owner Yes for exactly that. Each of these can get around the Docker wall (D30), so that Yes is floor-level (PIN or Windows Hello, D29) and the package shows "not held" on the Bridge page.
- Talk to hosts not listed in `needs.hosts` / `needs.install_hosts`. (Honest note, D10 as amended by D26 and D30: on Windows the Bridge holds the Core and agents fully. Docker packages are held by the Docker wall from the first release (D30). Native packages go through a local proxy that logs and filters; one that deliberately skips the proxy is not prevented at the OS level, but **when the Core sees it connect around the proxy it stops the package at once**, with a card offering Start again or Remove (D26). Short connections between checks can slip.)
- Call the model server directly instead of through the Core API.
- Put instructions to AIs in its README or descriptions (Drawbridge hides those lines).
- Require admin rights at runtime. One-time admin steps go in `setup_steps`, as the owner's own action.
- Publish `vault.*`, `egress.decision`, `consent.*` or `provider.*` events (Core only), or put a value in any event (ids, kinds, counts and hashes only). (PROPOSED)
- As a provider: lower or remove a label, open a host, or answer for a slot it doesn't declare; reach the network as a `reads: raw` provider; change a device without a `consent_id` for that one change (`zones/ZONES_TOGETHER.md` section 4). (PROPOSED)

Honest note for providers (PROPOSED): the floor holds by construction where the Core keeps the part: keys and crypto (X1, except the unattended tier, which any program running as the owner can read, D27), and the decision (X4). For consent (X10) it holds by construction **only on the Bus path**: no provider or package can publish `consent.*`. A program running as the owner's Windows user can still drive the owner's UI and fake a one-tap Yes; that is why floor-level cards need the PIN or Windows Hello (D29), which such a program can't supply (a keylogger could still record a typed PIN; Hello is stronger). Where a provider runs as the owner's Windows user, Windows-first can't fully hold: a native package or provider can skip the proxy until caught (X4, X5; Docker ones are walled, D30), keep a device credential once handed and reach undeclared LAN addresses directly (X11), and a raw reader is network-walled only if the AppContainer spike passes (X13). `zones/ZONES_TOGETHER.md` section 4 names each gap and its smallest fix.

Honest note, like D10: a package's native processes, and the scripts Toolsmith runs while trying one, run as the owner's own Windows user. They could read her files. `puffpai check` and Toolsmith's zone (01) catch writes and known paths; they do not sandbox the process. A separate limited Windows account is a later question (`07`).

## Setup steps (the human Checklist)

When a step is easier or safer for a person, the package declares it, and the owner sees a "Your turn" card:

```json
"setup_steps": [
  { "id": "docker", "kind": "run_command", "shell": "powershell", "admin": false,
    "title": "Install Docker Desktop",
    "why": "This app runs in Docker.",
    "text": "winget install -e --id Docker.DockerDesktop",
    "check": { "command": ["docker", "version"] } },
  { "id": "key", "kind": "enter_secret", "title": "Paste your NASA API key",
    "why": "NASA needs a free key for its pictures.", "get_it": "https://api.nasa.gov", "key": "NASA_API_KEY" }
]
```
**The one step shape** (01 and 03 use the same): `{ id, package, title, why, kind, shell, text, admin, get_it, key, check, blocks }`. `package` and `blocks` (what waits on this step) are filled in by the Core. `admin: true` adds a "Run as administrator" button (Windows UAC); otherwise the buttons are Copy, Done and Skip. `check` is `{ "command": ["program", "arg"] }` (an argument list, like `start`), `{ "url": "..." }` or `{ "file": "..." }`.
Kinds: `run_command`, `open_url`, `enter_secret` (goes straight into the vault), `plug_in` (hardware), `confirm`. Toolsmith raises the same cards when it hits something it can't or shouldn't do itself, such as an admin password or a sign-up page.

## Lifecycle

`add` -> validate -> Drawbridge check -> **one Yes** -> install + setup (+ "Your turn" steps) -> Steward starts it -> Caretaker health-checks it -> Registry row -> the brain and the tray see it.
`remove` -> stop -> Registry row removed (tools and tray go with it) -> data kept or deleted per the owner's choice. A linked folder (the owner's own code) is never deleted.
`update` -> fetch -> re-check -> show what changed (new hosts, keys, changing tools) -> Yes -> swap -> data kept.
`move` (PROPOSED, a provider slot that holds data and allows one at a time, today `zone1.vault-store`): copy, read back, commit once, keep the old one read-only until the owner removes it, roll back on any failure (`zones/Z1_VAULT_PROVIDERS.md` 4.1, X15). A provider install or update reruns its floor suite (Caretaker).

## "Works on PuffPAI": the criteria

These are the criteria the owner asked for. `puffpai check <folder>` runs all of them locally, so a developer or an AI gets a pass/fail list before publishing. There are three levels, and each needs the one before it.

**Level 1: Runs** (required to install)
1. `puffpai.json` is valid for its `schema` and `kind`, and the files its kind must contain are there and load (the `.glb` opens, `SKILL.md` parses, `sources` hashes match).
2. If it runs a process: it starts on Windows from its `run` block, using only the Core's placeholders, bound to 127.0.0.1. The after-start check finds nothing published on all networks.
3. If it runs a process: its `health` check passes within 2 minutes of starting. The `health` URL may be a plain `/health` route (D48b). The first `ui.pages` entry is the app's main page, never a metrics, health-only or API-only page (multi-service apps: 03 run notes).
4. If it runs a process: it stops cleanly on Stop, leaves no running process behind, and its MCP endpoint refuses calls without its token.
5. It writes only inside `packages/<id>` and `data/<id>`.
6. Every outside host it contacts is listed in `needs.hosts`.
7. Drawbridge finds nothing on its block list (hidden loaders, hostile README text, or hostile docs text under Locked down, over the disk bound; 01 "The seven agents"), no hidden AI instructions and no tunnel tool in code. A tunnel asks the owner at install, and she may say Yes, but a package with one does not pass this item, and `puffpai check` does not run it (D117).

**Level 2: Talks** (the brain can use it; applies to kinds with abilities: `app`, `tool`, `agent`; a `skill` passes if its `SKILL.md` has a plain description)
8. It exposes an MCP server, or it is an app the Core can read with `read_app`.
9. Every tool has a plain description the brain can act on, and a schema.
10. Every tool that changes something is listed in `abilities.changes`.
11. `validate`: every tool named in `abilities.changes`, `abilities.examples` and `accepts` exists in the MCP server's `list_tools`.
12. A sample ask works: for each tool, `puffpai check` asks the bot's local model a test question in its description's words and confirms the right tool gets called.

**Level 3: Belongs** (fits the Desktop and the store)
13. It has an icon. Kinds with a screen (`app`, `page`, `ui`) also have a tray item or page. (Tools, skills, agents and knowledge show in the Desktop's Installed list instead.)
14. It has a `consent` sentence and an honest `description`.
15. Removing it leaves nothing behind except the data the owner chose to keep.
16. Its README has a "PuffPAI" section: what it does, what it needs, any `setup_steps`.

**Providers (PROPOSED, D20, D21)** add one level and change one:
- **Level 1F: Floor** (required to install, after Level 1): the slot's floor suite passes (the X items for that slot, `zones/ZONES_TOGETHER.md` section 4), including a hostile neighbour and canary tests. `puffpai check --floor` runs it alone; `--zone1`, `--zone3` run one zone's tests.
- **Level 2 for providers is "Fits"**: answers every operation in the slot contract within its deadline (a late answer counts as the safe answer), publishes only allowed message types, and declares `reads` truthfully.
- Verified review adds a human read of every network call and every place data is kept; detectors publish a sample set with hit counts; `zone3.network-device` gets a read-back and undo test on a real device; `zone1.vault-store` / `zone1.vault-unlock` get a move and a recovery test both ways.

**Verified tier (D6)** = all three levels pass, plus a PuffPAI review of `abilities.changes`, `needs.hosts` and the code. Community packages need Level 1 to install (providers: Level 1 and 1F), and the store shows which levels they pass.

## Where your app opens (D100)

The owner, 10-30: "the apps from GitHub shoud work as they are intended to work, as seprate windows opened into whatever thier UI is, but accessibale by the LLM." The Core decides by where the package came from (its Registry origin, which only the Core writes), never per app and never from what the manifest claims. `show` can only ask to stay outside; it can't get a package inside.

| Where it came from | Where it opens | The brain |
|---|---|---|
| Ships with PuffPAI, or the official PuffPAI store, either tier | Inside: the window's side pane or the dashboard, on its own address (D53), sandboxed | MCP tools, and `read_app` |
| Written by her own bot (`made`; PROPOSED, the make path is not built) | Inside, the same way | The same |
| Anything else: adopted from GitHub, a git address, another store, your own folder with `puffpai add` | Its own window: a new tab of her real browser, its own UI as built. It is never framed, and its pages refuse to be framed | `read_app` reads its page; "show me" opens it (`open_app`) |

**To integrate your own app so it opens inside PuffPAI:** follow "Getting into the official store" below. While you develop from your own folder, it opens in your browser. That is on purpose: `puffpai check` passes on your computer, but only the official store listing is what the Core trusts. Publishing your project on GitHub with a `puffpai.json` doesn't change this: a GitHub app stays in its own window, and works as its author built it.

## Getting into the official store (D100)

The owner, 10-30: "I review each app before it goes in the official PuffPAI store." Every app, tool, look or other package in the official store has had her review, on both tiers. Anyone can still host their own store (D6), and a package from another store installs and is read by the brain as usual, but opens in its own window.

1. **Build it as a package.** Start from a quick start in `04_DEVELOPERS.md` (`puffpai new` is PROPOSED, not built). Give any page `show: in_desktop`.
2. **Run `puffpai check .` until Level 1, 2 and 3 pass**, and keep its output. That includes the README's "PuffPAI" section and an honest `description`, `consent`, `abilities.changes` and `needs.hosts`.
3. **Put it in a public GitHub repo** and note the exact commit. The store pins one commit, never a branch.
4. **Ask for review** (PROPOSED: the way to ask is a pull request to the official store's repo, `CypherSisters/puffpai-store`, adding one row to `index.json` with `tier: community`, plus the check output. Not yet set up or tested). An AI may prepare all of this, but a human author sends it and answers for it.
5. **The owner reviews.** She installs it from that commit, runs `puffpai check`, reads `abilities.changes`, `needs.hosts` and the code, and tries it. Then she says no with her reason, lists it as `community`, or lists it as `verified`. Only she merges the row. Nothing is listed by passing the check alone, and no AI approves a package.
6. **Every new version is a new review.** The row pins a commit, so a new version changes the row and comes back to her (`verified_commit` must equal `commit`). She can pull a listed package with the index's `yanked` list.

## Adopted packages (outside apps)

Most useful software was never built for PuffPAI. Toolsmith's main job is to **adopt** it:

1. Find it (GitHub by stars, awesome-selfhosted, the store).
2. Read its README, compose file and package files.
3. **Write a `puffpai.json` for it**: `origin: adopted:<url>`, a `run` block from the README's Quick Start, a `health` URL, `ui.pages` with `show: new_tab`, `needs.hosts` from what it saw, and `setup_steps` for anything a human must do. A desktop program gets `ui.open` and a `health` `file` instead of a page. **The `health` and `ui.pages` URL is the main page**: the address the README says to open first, then a front service (`frontend`, `web`, `ui`, `app`, `proxy`, `gateway`, `nginx`, `caddy`, `traefik`); never a `/metrics`, health, pure JSON, exporter or Whitelabel error page. (v1 `4b7cd85d`: opentelemetry-demo was marked working at "Prometheus Java Client".)
4. Install it through the same Package Manager (Drawbridge, one Yes, and so on).
5. The brain gets `read_app` and `open_app` for it right away. If the app has an API, Toolsmith may write a small MCP adapter as a second, separate package (`<id>-mcp`, kind `tool`), or later the Core may read the app's own OpenAPI file (03 "Later option"). A service with no web page, on a cloud try (D124), is kept as one package of `kind: tool`: its own `run` block, no `ui.pages`, and in `abilities.mcp` over stdio the small MCP server its try wrote, with `{port}` for the service's port (a stdio command may use `{python}`, `{node}`, `{pkg}` and `{port}`). An MCP server project is kept the same way, with only `setup` in its run block.

So everything in the Registry is a package, however it got there. A good adopted manifest can be shared to the store, which means the next person's bot installs that app without Toolsmith having to work it out again. This is how the store fills up.

## Made packages

When no app exists, Toolsmith **makes** one from found code (libraries, CLIs, APIs, the owner's own repos). It produces an ordinary package folder with a `puffpai.json`, its own small server, a page and an MCP server, and checks it with `puffpai check`. `origin: made`. This is v1's MAKE path, minus the old assemble/scaffold layers.

## Store (D6, built by lane store, D89; not yet tested by the owner)

- **A store is one `index.json`** (`"schema": "puffpai-store-index/1"`): `packages`, rows of `id`, `kind`, `name`, `description`, `version`, `author`, `license`, `repo` (`https://github.com/<owner>/<repo>`), `path` (the package's folder in that repo, or empty for its root), `commit` (40 hex), `tier` (`community` \| `verified`), and optionally `icon`, `verified_by`, `levels`, `abilities` (`{tools: {name: description}, examples: {name: [ask, ...]}}`, what Toolsmith reads to suggest a package that is not installed yet, D96); and `yanked`, entries of `id`, `commit`, `why`. The official store builds it from one row file per package and refuses a verified row whose `verified_commit` is not its `commit`.
- **Sources** (`packages/store_index.py`): the official store is a constant in the Core and is always first; no setting removes or replaces it. The owner adds her own (an https `index.json` address, or a folder or file on this computer) like a Linux package source; they are kept in `settings\store.json`, in order. A source that can't be reached lists its last good copy (`settings\store-cache`), marked out of date. A row that breaks the rules is left out with its reason; the rest still list.
- **Install from the store = `add("store:<id>")`**, the same Package Manager path (`puffpai add store:<id>`, `POST /v1/packages {"source": "store:<id>"}`). The row comes from the official store first, then her sources in order. Exactly the pinned commit is fetched into staging, only the row's folder is kept (never one that leaves the repo or goes through a link), and the manifest's `id` and `kind` must be the row's. A yanked (id, commit) is refused with its reason. Nothing else about a package changes because it came from the store: Drawbridge, the Yes card (which says where it is from, its tier and the commit), the Registry.
- **`origin`** is `store:verified` only for a verified row from the official store. Everything else, a verified row from another source included, is `store:community`. The Registry row also keeps `store`: `{source, repo, path, commit}` (`source` is `official` or her source).
- **Updates**: `GET /v1/store/updates` / `puffpai store updates` list installed store packages whose source pins a different commit now; `puffpai update <id>` fetches it behind its own Yes card, with the origin that row gives now. An installed package that a store pulled gets one notice offering removal; it is never removed without her.
- **The Desktop and Toolsmith read it (D96, PROPOSED, built and tested in code).** Apps > PuffPAI store merges the built-in packages and the store's rows, one row per id, with the built-in row winning. The index is fetched only when the owner opens that panel, refreshes it, runs `puffpai store`, or adds a package. Home's question and Toolsmith's suggestions read the saved copy only. Toolsmith suggests only the official store's verified rows of kinds app and tool with `abilities`; its plan names them `store:<id>`, and that add installs only from the official store as it reads then. A row's `icon` is never loaded by the Desktop. The wizard lists no apps (lane I10c), so it gets store rows only through the plan card. The store and packages repos are private until the v2 release, so a real Core can't fetch them yet.
- **An outside look's colours (D96).** At install, and in `puffpai check`, a `look` is checked for WCAG AA (4.5:1) with `core/puffpai_core/contrast.py`, the same check `tests/test_contrast.py` runs on the looks in this repo. One that fails, or has values the check can't read (only hex, `rgb()`, `rgba()`, `transparent` and `var()` of those), says so in one line on its Yes card. It is never refused for this, and no level reads it.
