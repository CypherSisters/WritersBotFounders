# NOTE from lane L5: what L1's /pkg/ forwarding must do on the pages address (D49)

For the merge chat, to reconcile lane L1 (pages forwarding) with lane L5.

## What L5 built

- `api/app.py`: one Flask app, two waitress listeners in the same Core process.
  `serve()` runs the Core's own address (127.0.0.1:8420) and the pages address
  (127.0.0.1:8422). Each listener stamps `environ["puffpai.listener"]` (`core` or
  `pages`) before the app runs. The stamp comes from the server, never from a header.
- On 8420: `/pkg/` answers 404 with the pages address in the reason. A
  cookie-signed POST, PUT, PATCH or DELETE needs `Origin` equal to the Core's own
  origin (or, with no Origin, `Sec-Fetch-Site: same-origin`) and `X-PuffPAI: 1`,
  else 403. Bearer callers are not affected. Every response gets
  `X-Frame-Options: DENY` and `frame-ancestors 'none'`.
- On 8422: the device cookie is never read; owner keys and device tokens are
  refused (403). Only `/pkg/` (the `pages` blueprint), `/v1/health`, and the
  `package_ok` / `own_row` routes are there, with a package token. Everything
  else is 404. Every response gets `frame-ancestors <Core origins>`.
- `api/groups/pages.py`: the two stubs changed from `owner` to `public` (there is
  no owner login on 8422; the forwarding adds the token). openapi matches.
- The Desktop gets the pages address from `GET /v1/settings` (`"pages"`). It
  frames pages from it with `sandbox="allow-scripts allow-forms allow-popups"`
  (no `allow-same-origin`), loads icons and avatar previews from it, and
  fetches JSON widgets from it with `credentials: "omit"`.

## What L1's forwarding must do

1. Keep the views in `api/groups/pages.py` (the `pages` blueprint), so the gate
   serves them on 8422 only. Never add a `/pkg/` route to another blueprint.
2. Find the package from the path `/pkg/<id>/`, never from a header, cookie or
   Referer. Forward to `127.0.0.1:{its port}` and add that package's own token
   (`Authorization: Bearer <PUFFPAI_TOKEN>`).
3. Strip from the forwarded request: `Cookie`, any incoming `Authorization` and
   `X-PuffPAI-Key`, and `X-PuffPAI`. Strip `Set-Cookie` from the reply.
4. Never forward to any port but that package's own, and never follow a redirect
   to another host or port. A `Location` pointing elsewhere goes back to the
   browser as is, or is refused.
5. Answer JSON widget fetches from the Desktop with
   `Access-Control-Allow-Origin: <the Core's origin>` (no credentials). Answer
   nothing else cross-origin. Sandboxed pages have an opaque origin
   (`Origin: null`). If a page's own fetches to its package need CORS, allow
   `null` only for that package's own `/pkg/<id>/` paths.
6. Keep the `frame-ancestors` header the gate adds. Don't replace it.

## Honest limits to decide on

- All package pages share one origin (127.0.0.1:8422). A sandboxed frame can't
  read another package's replies without CORS, but it can still send a simple
  request to `/pkg/<other>/`, and the Core adds the other package's token. A
  real fix needs one origin per package (for example, a port per package, or
  `<id>.localhost` hosts). PROPOSED, not built.
- Any program on this computer can call 8422 directly and get a package's
  routes with its token added. That is the D49 design; D48(d) now holds only
  against programs that skip the Core.

# NOTE from lane L4: lines for the docs (security verifier fixes, 2026-10-22)

For the docs lane. Each line is what the code now does; PROPOSED wording, the docs decide.

- **Z3 section 4 (proxy), honest limit:** "Inside a CONNECT tunnel the proxy sees only the name asked for (the CONNECT host and the SNI must match a grant). It cannot see the HTTP Host inside TLS or an Encrypted Client Hello's inner name, so domain fronting is not stopped: a package granted one name on a shared front (a CDN or cloud edge) can reach another site that front serves."
- **Z3 section 4 (proxy), plain HTTP:** "One request per upstream connection, parsed and policed whole (request line, header lines, one body framing); the reply says Connection: close and both sides close. Pipelined bytes are never forwarded."
- **Z3 section 2 (grants):** "A grant on port 443 allows CONNECT only, on port 80 plain HTTP only, any other port both." Sub-host grants are checked against the full Public Suffix List, bundled offline (`core/puffpai_core/zone3/data/`, version 2026-10-20).
- **09 section 3 (tamper anchors), as built:** the machine-key copy is an HMAC under one key at `settings\anchor.key` (DPAPI, own entropy). It covers the Bridge policy (checked at start and on every reload), each consent record (its MAC names the store's generation, which every revoke bumps), and the sealed heads of the outside log and the audit log (`<log>.head`). Without a machine key the Bridge stays closed with a card. The vault copy goes through lane L3's Archivist (see the D54 notes below).
- **09 section 3 (audit log), as built:** the first line of each log file is `n 1` after GENESIS; a rotated file starts with a `chain_rotated` line naming the old file's last `n` and `h`. A hard crash between a line and its seal reads as damaged at the next start (fail closed).
- **01 / Z1C (folders):** Toolsmith's zone reads lane L3's `settings\folders.json` (`data`, `linked`); a file there that can't be read refuses everything. `.pth`, `sitecustomize.py` and `usercustomize.py` in any site-packages are denied, and so is writing into the Python that runs the Core.
- **09 section 6 (what a scan promises):** Drawbridge scans and hashes every file at every depth (nothing skipped). What it can't check (over 4 MB, an unknown kind, a binary that is not what its name says, a link) is "hold": her Yes, never a pass.
- **01 (consent):** one consent store for the Core, `core/puffpai_core/consent/` (`<state>\consent.db`), with lane L1's names; pending cards survive a restart and expire after 10 minutes.

## L4 re-check (D54), for lane L3 and the docs

- **Lane L3's Archivist, as used (lane/l3-vault 936bcc5d):** the Bridge gets the Archivist from `zone1.archivist.for_state(state, bus)` (or the Core's own `core.archivist` when wired) and calls exactly `is_open()`, `anchor_put(name, value) -> bool`, `anchor_get(name) -> dict | None` and `on_unlock(callback) -> remove` (`ARCHIVIST_CONTRACT` in `core/puffpai_core/zone3/vault_anchor.py`; a floor test fails if the test double or the real Archivist drifts). Names and values: `bridge.policy` `{version, sha}`, `bridge.ledger` `{n, h}`, `core.audit` `{n, h}`, `consent.generation` `{generation}`; counts and hashes only. A put that returns False (locked) stays pending and is put again at the next unlock; a get that raises at the unlock check closes the Bridge with a card ("can't confirm").
- **09 section 3 / section 9, as built (D54):** at each unlock, a policy older than the vault's copy (or the same version with another sha), or a log line the vault remembers that is no longer in the log, closes the Bridge with one card. The policy's version only goes up (`policy.version`, MAC'd, beside the file). Still a stated risk: between unlocks, and a rollback to a state newer than the vault's last copy.
- **Consent, as built (D54):** a card's `card_sha` covers what was shown and everything the Yes grants (scope, floor, covers, bound, expiry; for lane L1's cards, a `puffpai_yes` key in the card holds scope, floor and covers). Each row is sealed with the machine-key MAC when it is created and checked before it is shown or answered; an edited row is `void` and can't be answered. Grants come only from the sealed row.
- **Toolsmith's zone, as built:** any write into any site-packages folder is denied; copy and move to a folder are checked as folder plus file name; NTFS streams (`a.pth::$DATA`, `a.txt:x`) are denied; long-path, device and UNC paths, unresolved variables and joined strings are denied for writes and wait for her Yes for reads.
- **09 section 6 (spec note):** a clean `.mts` (or `.cts`) file is now read as code like `.ts`, so a clean one passes ("allow"); only a kind the checks can't read is "hold".
- **Consent replay (re-check):** `consent.db` keeps a MAC'd `generation` that only goes up; every Yes's MAC names it, and every revoke bumps it and re-seals the Yeses still live, so a revoked row put back with its old status, sha and MAC stays dead. A whole old `consent.db` is caught at the next unlock (its generation is lower than the vault's). A Yes must carry the card's `card_sha` (400 `no_card_sha` without it); a No needs none. **For lane L1:** its CLI (`core.consent.answer(...)` in `cli.py`) and its route must pass `card_sha` for a Yes.
- **Toolsmith's commands (re-check):** besides the text check, every command runs inside `zone3.hook_guard.HookGuard` (lane L6's exec broker calls it): every `*.pth` under a site-packages folder and every `sitecustomize.py`/`usercustomize.py`, in the try folder and in the Core's own Python, is hashed before and after. A change in the try folder is undone and stops the try; a change in the Core's Python means safe mode, with a card. In a try's venv a new or changed `.pth` passes when a `*.dist-info/RECORD` written by the same command lists it with the same sha256 (pip's own record), so normal installs work; an unrecorded `.pth`, a removed one, and any `sitecustomize.py`/`usercustomize.py` still stop the try. Honest: a command that writes a fake dist-info record passes too.
- **D54 locked window (re-check):** a change made while the vault is locked (a revoke, a grant, log lines) keeps its anchor value as **pending**, in memory and in `settings\bridge\vault_pending.json` (MAC'd with the machine key, so it survives a restart). At unlock the files on disk must equal the newest expected value (pending, else the vault's copy); any difference closes the Bridge with a card, and the vault keeps the expected values until the owner opens the Bridge again (that accepts what is there now). At start the files are compared with the pending values on disk; a damaged pending file, or a missing one on a bot that has run before, is a card at start. A check that breaks closes the Bridge with a card. Still a stated risk: files and the pending file rolled back together while the Core is stopped, to a state newer than the vault's last copy.
- **Wiring for lane L6:** `zone3.hook_guard.HookGuard` has no caller in `core/` yet. L6's exec broker must wrap every Toolsmith command in it after the merge (`with HookGuard(try_folder, stop_try=..., safe_mode=..., on_card=...)`).

## Lane L1 after merging main 7b49ce29 (10-22)

L5's listener stamp, gate, CSP and Origin + `X-PuffPAI: 1` check are the base (the 8420 CSP is widened for D53)
(`api/app.py`, `auth.cookie_request_ok`, `auth.CSRF_HEADER`). L1 does not
duplicate them. L1's earlier separate pages app and page tickets were dropped.

Package pages, D53 (every page on its own origin, `http://<id>.localhost:8422`):
- `api.app._page_hosts` wraps `app.wsgi_app`. On the pages listener only, `Host: <id>.localhost:<pages port>` with a valid id sets `environ["puffpai.page_package"]` and routes to `/pkg/<id>/<path>`. So a package origin never reaches a Core API route. Any other `*.localhost` host, a bad id or a wrong port gets 404. An unknown valid id gets 404 "Nothing installed".
- `/pkg/<id>/<path>` on `127.0.0.1:8422` is kept only as a 302 (GET) to `http://<id>.localhost:8422/<path>`. A POST there, or another Host, gets 404 or 403.
- Forwarding (`api/groups/pages.py`, `public`):
  - Only to that package's own port, with its own token. No redirects are followed.
  - Stripped from the request: `Cookie`, `Authorization`, `X-PuffPAI-Key`, `X-PuffPAI`.
  - Stripped from the reply: `Set-Cookie`, `Set-Cookie2`, `WWW-Authenticate`, `Authorization`, `Clear-Site-Data`, `Access-Control-*`.
- Writes (not GET, HEAD or OPTIONS) need `Origin` equal to the package's own origin, or, with no Origin, `Sec-Fetch-Site: same-origin`. `null`, other packages, the Core, 8422 and other websites get 403. GET /pkg/ and POST /pkg/ are the only methods with routes, so PUT and DELETE get 405.
- CORS: only a GET from a Core origin gets `Access-Control-Allow-Origin: <that origin>`, never with credentials.
- CSP:
  - On 8420: `img-src` / `connect-src` add `http://*.localhost:8422`, and `frame-src` is `http://*.localhost:8422` only.
  - On a package origin: `package_csp`, with `'self'` plus its own origin only, and `frame-ancestors` set to the Core origins.
  - On 8422 itself: L5's `pages_csp` is unchanged.
- Desktop:
  - `logic.js` has `pkgOrigin(id, base)`, derived from settings `pages` (scheme and port kept). Without a pages address, no package link is made.
  - `PAGE_SANDBOX` (and index.html) adds `allow-same-origin`. `WIDGET_SANDBOX` stays `allow-scripts`.
  - `ownPicture` accepts only its own origin's URLs or `/pkg/<own id>/` paths. `onPages` maps `/pkg/<id>/x` to `<id>`'s origin.
- `POST /v1/packages/<id>/open/<page>` answers `http://<id>.localhost:8422/<path>`.
- Residual, for the merge chat:
  - D55 done: package ids are `^[a-z][a-z0-9-]{1,39}$` in the package and registry schemas, events.json, `api.app._PACKAGE_ID` and `logic.js`. No browser test was run.
  - GETs are not origin-checked (frames and images are cross-site). D55 says a package's GET and HEAD routes never change anything. `puffpai check` warns about a declared GET route, or an undeclared MCP tool, whose name sounds like a change (`check.change_warnings`, `Report.warnings`, never a FAIL). It cannot see a route the manifest doesn't name.
  - `docs/02_PACKAGE_CONTRACT.md` in this tree still shows the old id pattern (docs follow main).

Other L1 changes the merge must keep:
- Steward, for L4's bypass watch:
  - `running() -> [id]`.
  - `pids(id) -> [int]`: every job-object process, the venv launcher child included (tested).
  - `stop(id, reason=None, *, status="stopped")`: `reason` is log text, never a status.
  - An uncontained start is refused on Windows.
- Vault seams (L3 merged, main 84dde4e8): `seams.verify_pin(state, pin, bus)`, `seams.lease_for_start(state, id, names, bus)` and `seams.end_holder(state, id, bus)` use `zone1.archivist.for_state(state, bus)` (the Core's own bot folder) and call `.verify_pin`, `.inject` and `.end_holder(PACKAGE_HOLDER + id)`. L3's module-level `verify_pin` / `lease_for_start` / `end_lease` are the fallback only; they resolve `state_dir()`, not the Core's folder.
- A floor card with no vault set up answers 503 "Set up your vault and its PIN first" (L3 raises `NotSetUp`).
- Test doubles now patch L3's real names: `Archivist.verify_pin(self, pin)` (conftest `archivist`), `Archivist.end_holder(self, borrower)`. The D41 test runs a real lease with z1kit's PIN and `FakeMachine`.
- ConsentStore (to be replaced by L4's):
  - Public names unchanged.
  - Added `expire(id, note)`, status `expired` and a `note` key. L4's store needs `expire` too.
  - `PackageManager._expire` falls back to a No if `expire` is missing.
  - `GET /v1/consent` and `/answer` are built by L1 here. The openapi says `x-lane` L1 and L4 respectively.
- `api.app.start_core(core)` / `stop_core(core, key)` run inside `serve()`:
  - `start_core`: self-test, expire last run's cards, empty staging, revoke earlier terminal keys, issue a new one, `start_saved`, Caretaker.
  - Terminal keys are labelled `this computer's terminal (run <hex>)`. The gate honours only this run's label.
- The listen watch reads `GetExtendedTcpTable` (`ports.listen_table`, under 1 ms; None = unknown) on every Caretaker tick. L4's `zone3/bypass.py` has a similar reader; dedupe later.
- Caretaker cards (`caretaker.cards`, `on_card`): `{"kind": "exposed" | "listen_unknown", "package", "line", "buttons"}`. They are not routed to L5's notices yet.
- 8422 is in `ports.RESERVED_PORTS`.
- card_sha (for L4's store, which refuses an answer without it):
  - Every L1 answer path sends the hash of the card the owner was shown: the CLI (online and offline), the Desktop (`logic.consentAnswer` adds `card_sha` from `GET /v1/consent`), `PackageManager._expire`'s No fallback, and the tests' `yes()`.
  - `POST /v1/packages`, `/update` and provider swaps answer `card_sha` too.
  - L1's store requires it on a Yes (400 without it, 409 for another card's) and checks it on a No when sent.
- **After merging lane L1 (for the merge chat):** L4's consent store now has `expire(cid, note)` (L1 expires waiting install cards at start; the note is the answer's message after that). Two small edits in L1's code and one in L1's test, each flagged here: (1) `steward._compose_up` calls lane L4's wall as `override(manifest, folder / block["file"], package_dir=folder)` (the wall reads the app's own compose file and writes the override; a refusal is a `StewardError`), and the seams table says so; (2) the store's PIN check goes through `seams.verify_pin(state, pin, bus)` (503 "Set up your vault" when there is no vault yet); (3) `tests/floor/test_l1_fixes.py` raised a card of kind `egress`, which is not in `contracts/events.json` `consent_scope`; it now uses `host`. **Still open:** L1's seam `zone3.drawbridge.check_package(path, manifest)` expects a module-level function returning `{verdict: pass|block, ...}`; L4's Drawbridge has `Drawbridge.check_package` (allow/warn/hold/block) only, so with both merged the real seam finds nothing and nothing is added (fail closed). An adapter needs a decision on how "hold" (her Yes) and the two tree hashes map.

# NOTE from integration lane I1 (10-22): the seams, wired

`core/puffpai_core/wiring.py` is the one place the lanes are joined (`wire(app)` in `create_app`, `start`/`stop` in `start_core`/`stop_core`).
- **Drawbridge (L1 to L4), was "Still open" above:** `seams.drawbridge_check(folder, manifest, origin=, drawbridge=)` calls L4's `Drawbridge.check_package` (the Core's own, with the audit log); `seams.drawbridge_bound(folder, verdict)` is L4's `verify_tree` (allow, warn) or its `tree_sha` (hold). The module-level `check_package` seam is gone. Mapping (PROPOSED, needs a yes): allow and warn go to the Yes card; **hold goes to the Yes card with what is held listed on it, and her Yes on that card is the Yes** (it is never floor-level by itself); block, a crash, an odd word or a tree that is not the checked one is a no. At install the folder is re-hashed (L1's hash and L4's), and re-checked; a hold that was not on the card is a no.
- **One PIN check:** `seams.check_pin(state, pin, bus)` (L3's `Archivist.verify_pin`). The consent store's floor-level Yes and L5's `pin_check` (run as administrator) both call it. No vault yet: 503 "Set up your vault and its PIN first".
- **Zone 3 starts with the Core** (`api.app.start_zone3`, the same function lane L6 adds): the proxy (`--proxy-port`, 0 = any free port) and the bypass watch start in `start_core`. Steward gives each start and setup a fresh proxy credential (`Zone3.package_env`); with no proxy running, the proxy address is `127.0.0.1:9` (nothing listens), so a credential never reaches another program on 8421. A busy proxy port is a `proxy_down` card and a notice, not a crash. The Docker wall gets the proxy's real port; no proxy, no Docker start.
- **Everyday changes from chat (09 section 3):** the Brain's `consent` is Drawbridge's `review_action`. Scope (PROPOSED, needs a yes): every tool the install card listed under "changes" is approved ("doesn't ask"); a tool Drawbridge counts as changing that the card did not list asks. An `ask` verdict is no answer (the tool does not run): a chat-time Yes card needs a consent kind for one tool call in `contracts/events.json`, which there is not.
- **Caretaker's cards** (`exposed`, `listen_unknown`) go to L5's notices. Zone 3's own cards (`Zone3.cards`) are not routed yet.
- **Static packages** (`page`, `avatar`) now serve the pictures the Desktop loads (`icon`, tray icons, `avatar.preview`, `avatar.model`), so the avatar preview shows.
- **The wizard:** `core.vault` (L3 Archivist), `core.bridge` (L4: `search` opens the Bridge with the `toolsmith:search` switch on), `core.models` (L2 fit and state; the pick is saved with `zone2.settings.save(model=)`, and `settings\model.json` keeps L2's fields). Toolsmith's steps say "not built yet (lane L6)".
- **For L6:** `zone2.brain.toolsmith_turn`, `ask_json`, `answer` are built (gate with the vault treated as locked, X12, D50).

## I1 verifier fixes (10-22): every tool call through Drawbridge, tool_call cards, cards that show their grants

- **One rule for "changing" (X27):** `zone3.drawbridge.tool_changes(name, manifest, entry, origin, marked)`. Changing when the manifest lists it, its MCP annotations say `destructiveHint: true` or `readOnlyHint: false` (both the wire names and mcp 2.x's `read_only_hint` / `destructive_hint` are read), its name reads like a change, or Toolsmith wrote the manifest (`adopted:`, `made`) and it does not say `readOnlyHint: true`. `classify_tools`, the Concierge and the review all use it. A tool Drawbridge has no classification for counts as changing. DECIDED HERE (restrictive within X27): for a hand-written package, a tool with a plain read name and no hints is read-only, as X27 says; "unknown" means not classified at all.
- **Every package tool call is reviewed:** the Brain's `review(call)` is `wiring.tool_review` (set by `wire_core`). `Drawbridge.see_tools` adds the live tool list (with hints) to the scope; it only adds reviews, never lowers one and never widens her approved scope. Inside her scope or read-only: runs, no card (in-scope calls get an audit line). `ask`: a `tool_call` card (new kind in `contracts/events.json` and the store) for that one call; the turn waits (up to the card's 10 minutes). Her Yes runs it once (checked: granted, same `card_sha`, same `args_sha`); her No, an expired card, Drawbridge's no or a broken review ends the turn with one plain line. `changed` lists every call that ran and that Drawbridge or the Registry counts as changing.
- **The tool_call card:** title "Let <app> <tool in words>?", "One change from chat, this time only.", the app, the tool, "With:" the gate's locked summary of the arguments (an argument named like a secret shows "(hidden)", file bytes and long values are cut), why it asks, and for a floor-level call (Drawbridge's `floor`) the PIN line.
- **Cards show what they grant:** `consent.card_view(card, floor)` is the one plain view; `GET /v1/consent` adds `title` and `lines`, the CLI prints the same lines, the Desktop shows them. Every line comes from the stored card (what `card_sha` covers). A package card lists hosts, install hosts, keys, setup steps, the changes it makes without asking, that any other change asks, Drawbridge's line and notes, "Drawbridge couldn't check these files: ... Your Yes adds them anyway." (from the `cant_check` row's new `files` list), and the PIN line on a floor card. A Desktop card that came with no lines offers only No. The Desktop now asks `?status=pending` (it asked `?pending=1` and filtered on a `state` key the API never sent, so answered cards showed too).
- **Gate failures:** a gate that raises, is None, or whose `prepare` raises or answers badly is `BrainError("no_gate")`: 503 `{"error", "state": "no_gate"}`.
- **After merging main 756baf37 (L6, D57):** `start_core(app)` is the one start, called by `serve` only: `wire_core` (L6's `start_zone3` + the wiring), L1's start, the proxy and bypass watch, Caretaker, and L6's Toolsmith host (`start_toolsmith`, the same `groups.toolsmith.service()`). It is idempotent (a second call returns the same terminal key and starts nothing). `stop_core` stops Toolsmith first (D18). L6's `test_on_main_as_it_is_nothing_installs` is now `test_on_main_a_build_installs_through_the_real_drawbridge`. L6's ports reach `zone2.brain.toolsmith_turn`, `ask_json` and `answer` (tested).
- **Start (decided, matches L6):** `create_app` builds the app only. `api.app.wire_core(app)` = `start_zone3` + `wiring.wire`, once. `start_core(app)` (what `serve` calls; a Core with `.app` also works) runs `wire_core`, then L1's start and `wiring.start` (proxy, bypass watch, scopes). The offline CLI (`add`, `remove`) and `puffpai setup` call `wire_core` only. Merge with L6: its `serve` calls `start_zone3(app)` then `start_core(core)`; keep this `start_core(app)` (it calls `start_zone3`, which is idempotent).
- **Install status:** the Package Manager no longer writes `stopped` before the first start; a package goes from `installing` to the start's own status (so `puffpai add` no longer prints a brief "stopped").
- **Still open:** a core tool's own question (`read_app` starting a stopped app) has no card yet (it does not run). A waiting chat turn holds one of the Core's 8 request threads for up to 10 minutes. The CLI's online `add` still polls the Registry for up to 30 minutes when an install fails before its row exists.

# NOTE from integration lane I2b (10-22): the last seams

- **Safe mode, one call:** `core.caretaker.safe_mode(reason)` (Caretaker). The first time for a reason it runs `caretaker.safe_steps` (the wiring adds: close the Bridge, lock the vault, pause Toolsmith, each returning what it did) and raises one `safe_mode` card (Desktop notice "BuildBot is in safe mode", printed by `puffpai serve`). The same reason again does nothing. `in_safe_mode` / `safe_reasons` replace the old `safe_mode` bool. Lane L4's HookGuard reaches it through L6's host (`ToolsmithHost.safe_mode` -> `Ports.safe_mode` -> Caretaker); the Bridge's tamper cards (`bridge_damaged`, `ledger_damaged`, `audit_damaged`) reach it through the wiring. A failed self-test at start enters it the same way. It lasts until the Core starts again.
- **DECIDED HERE (needs a yes):** `vault_mismatch` (D54, at unlock) is routed as a card but does not enter safe mode: it comes while she unlocks, the Bridge is already closed, and locking the vault she just opened would leave no way to check and open the Bridge.
- **Tick hooks:** `Caretaker.on_tick` (a list). L6's Toolsmith host adds `trials.rescan_all` there; the wrapped `tick` is gone.
- **Zone 3's cards:** `Zone3.cards` is a list that calls `cards.listeners`; `wiring.start` routes every card (and the ones raised while Zone 3 was made) to the notices and the terminal. `puffpai notices` lists the notices.
- **The brain's core tools:** `search_knowledge` = `wiring.knowledge_search(core)`: lane L3's `zone1.folders.Library` (scan at most once a minute, then FTS over the gated index: held values stay handles; the loop's gate gives real values only while the vault is open, D25). `checklist_add` = lane L5's Checklist, filed under `core:concierge` with `trust="package"` (never Core trust).
- **A core tool's own question:** `wiring.core_question(core)` is the Brain's `consent` in the running Core: `read_app` on a stopped app raises a `tool_call` card (target `<id>.start`, "May I start <app>?") and waits; with no card, no answer or a No, the tool answers a plain line (`start_question`'s `no_card` / `said_no`).
- **Adopted origin:** `PackageManager.add(source, adopted=<url>)` (in process only; the manifest must say `origin: adopted:<url>`). L6's `Ports.packages_add` passes it from the try's checked manifest. The Desktop tray says "Open in a new tab" for a new-tab target, and the Installed list has an "Open in a new tab" button for adopted apps.
- **`puffpai check` item 6:** a Zone 3 of the throwaway bot folder (proxy on a free port, bypass watch, Bridge closed so nothing leaves); hosts in the outside log under `pkg:<id>` that are not in `needs.hosts` (`install_hosts` too while installing) FAIL, and so does a bypass card. Honest: only what it does during the check.
- **Steward + Docker wall fix:** `_compose_up` now gives the wall this start's proxy credential (`proxy_url`) and its env, so `${PUFFPAI_PORT}` in the app's compose file is read with the port Steward picked (before, the published page port came out empty).

## I2b verifier fixes (10-22)

- **Toolsmith paused in safe mode:** `ToolsmithHost.pause(reason)` keeps the reason; `plan`, `start`, `_launch` and every `agent_*` route refuse first with "BuildBot is in safe mode: <reason>. Restart BuildBot to leave it." (`Paused`, a `Refused`: 409 `refused`; the owner routes add `safe_mode: true`). Pausing stops Toolsmith's processes and ends every try (its servers by their Job Object). A host made after safe mode began reads Caretaker's `in_safe_mode` too; a Caretaker that can't say counts as paused.
- **`puffpai check` item 6:** FAILs "couldn't watch it, so this can't be checked" when the bypass watch was blind to the package (`watch_blind`) or not running (`watch_not_running`, or not running at the end) at any point; the check runs one watch pass while the package still runs.
- **DECIDED (orchestrator, needs the owner's row in 00):** in safe mode, opening the Bridge is floor-level: `Bridge.open(by, pin)` runs `open_guard` first (`wiring.bridge_guard`: the one PIN check, D29). `/v1/bridge/open` takes `{"pin"}` and answers 403 `safe_mode_pin` with the reason first; `GET /v1/bridge` has `safe_mode: {on, reasons}`; the Desktop header has Open / Close the Bridge (in safe mode a card: reason, then the PIN); `puffpai bridge [status|open|close]` shows the reason, then asks for the PIN (hidden). The wizard has no PIN field, so its "search" posture is refused in safe mode (the search switch is turned back off). No vault = no PIN = the Bridge stays closed until restart. Safe mode itself lasts until restart.
- **Desktop:** "Open in a new tab" only for adopted apps (`origin adopted:`); other new-tab pages keep their own label.
- **Zone 3 card listener:** registered once per Core (`core._zone3_listener`); a stop and start in the same process routes each card once.
- **One safe-mode effect:** `caretaker.enter_safe_mode` over `caretaker.safe_mode_steps` is what Caretaker runs; the ONLY other path is `Ports.safe_mode`'s last-resort fallback (no Caretaker, or its call raised), which runs the same two functions and gives the card to Caretaker's listeners (same notice). Its line leaves out "nothing new is added" (the Package Manager reads Caretaker).
- **Docker live test:** also tries a bare IP (1.1.1.1 on 80 and 443, no proxy, no DNS) and expects it to fail (marked `docker`, not run here).

# NOTE from integration lane I2a (10-22): installer, the wizard's Toolsmith steps, the plan Yes, ADOPT

- **install.ps1** (repo root): Python 3.11+ from `-Python`, the registry (PEP 514) or a real python.exe on the
  PATH (never the `py` launcher or the Store / install-manager stubs: they can install a Python unasked; a
  missing Python is a printed "Your turn" step with `winget install -e --id Python.Python.3.11`), `.venv`,
  `pip install -e .`, the bot folder `%LOCALAPPDATA%\PuffPAI\<name>` (taken: `<name>-2`, the old one never
  touched), `Start BuildBot.cmd`, then `puffpai setup`. Log in `install-log.txt` (stopped before setup: setup
  shows the recovery code). Rerunning it makes a NEW bot (`-2`), as asked; there is no "update" mode.
- **puffpai setup** (`wizard/terminal.py`): starts the Core in-process (or uses a running one through
  `/v1/wizard`), offers the five built-ins through the Package Manager with their cards, then the wizard.
  Keeps serving until Ctrl+C (`--exit` stops). Ports `--port/--pages-port/--proxy-port`.
- **The wizard's Toolsmith steps**: `wiring.WizardToolsmith` (`core.toolsmith`). Goal -> jobs on the local
  brain only (`toolsmith_host/interpret.py`, harvested from v1 `build/interpret.py`). The plan step runs L6's
  plan once per build (`ToolsmithHost.draft` / `plan_build`); a pasted project is a new draft with it first.
  The wizard's One Yes answers the plan card it showed (`by: owner`). The build step is live (status
  `working`, polled by both renderers; desktop.js polls): job lines, each "Your turn" step (Done / Skip / Not
  yet), and any card for what was not on the plan.
- **The plan Yes covers adoptions** (`packages/plan_cover.py`, `PackageManager.add(plan=...)`): the PM reads
  the plan Yes itself (granted, `plan_sha` = sha of the envelope, the candidate on the plan, origin = that
  candidate). Inside: the app's card is made and answered `by: plan:<consent id>` (no card for her; Bridge
  grants and `yes_record` stay the one path). Outside: the card lists only what was not on the plan first.
  DECIDED HERE: plain (non-admin) setup steps she did during the build count as inside; keys, admin steps,
  hosts not in the plan's install hosts, changing tools and Drawbridge holds are outside.
- **Fixed in other lanes' files (found by the live adopt run)**: `WizardBridge` now records her step-4
  "search" choice as one `host` Yes for `toolsmith:search` (api.github.com, raw.githubusercontent.com,
  github.com, phase build); before, the switch alone let nothing through. `WizardModels.state` was shadowed
  by an attribute. A build's Bridge subject is `toolsmith-build-<6 hex>` (`store.bridge_target`): the Bus's
  D50 check read the build id's digits as a secret and dropped every proxied request of a try. Trials strips
  the clone's `.git`, drops files the run made (not checked, not written by Toolsmith) and moves the try aside
  before the PM takes it. Steward runs D51 blocks (`env`, `cwd`, argument-list, `copy`, `venv` steps).
  `PIP_REQUIRE_VIRTUALENV=1` for tries and packages (a try's bare `pip` installed into the owner's Python).
- **For a decision (security lane)**: the local model's try ran `taskkill /F /PID <n>` and the zone check let
  it; a try can end any process by number. The Registry origin of an adopted app is still `local:<staging
  path>` (lane I2b owns the adopted origin; the path is now `packages\.staging\try-<hex>`).
- **Part D (git boundaries, the manifest she said Yes to, TEMP leftovers)**: `steward/gitfence.py`. A try
  folder gets its own `.git` at `Trials.check` (`git init -q --template=`, the real `git.exe`; no git: nothing
  made, `TryRecord.git` says why; a failed init ends the try). Every try program (`Broker.env`), every package
  program (`Steward.env`) and every stdio MCP server gets `GIT_CEILING_DIRECTORIES` = its folder's parent, and
  none of the Core's `GIT_DIR`-style settings. Toolsmith's file tools can't write the try's top `.git`. The
  Registry row has `manifest_sha` (sha256 of the manifest file her card showed; `.buildbot/puffpai.json` for
  an adopted app), set at install and again only by an update's own Yes; `Steward.manifest` refuses a file
  without it, `start` marks the row `broken` ("Its settings file changed since you said Yes. Add it again to
  see what changed."). A row with no `manifest_sha` (made before part D) is refused the same way: such a bot
  needs its packages added again. `start_core` removes leftover `<bot>\t\<6 hex>` folders (real folders with
  the broker's exact name only, never through a link). Tests: `GIT_CEILING_DIRECTORIES` for the session,
  `conftest.git_init` fails a test at once.

# NOTE from integration lane I3b (10-22): hold cards in a try, build and install grants, one Bridge posture

From the live fresh-install proof (`C:\tmp\bb2proof`): every held candidate ended with "Drawbridge did not pass it: X: waiting for your Yes" and no card, and after the build the Bridge kept `toolsmith:search` (phase build) and `pkg:toolsmith-build-*` (phase install) grants while `GET /v1/settings` read a `settings\bridge.json` nobody writes (`not_set`).

- **A hold in a try is ONE card for her** (`Trials.check`, `_raise_hold`): Drawbridge's `hold` on a download, for files it couldn't check only (reasons exactly `cant_check`), raises one `package` card (target `toolsmith-build-<6 hex>.<slug>`, card key `try_hold`) through the store's L1 form (`Ports.consent_card`), naming the files (`consent.shown`: "Drawbridge couldn't check these files: ..."; Yes goes on, No moves on, it closes after 10 minutes like a No). The download stays in staging, so no try tool reaches it and nothing runs or installs before her answer. Toolsmith's side (`tries.Try._held`) sets the job to `waiting_you` and steps aside (other jobs go on), polling `try_check`. Her Yes: Drawbridge checks again, and the tree must be the one her card showed, held for the same files, else the try ends. Her No, an expired card or a void one: the download is removed and the next candidate starts. A try that ends while its card waits expires the card. The wizard's build step lists the card (`WizardToolsmith._cards`). A `block` still never has a card. A hold for any other reason (a raw-reader provider) still ends the try.
- **The install does not ask again for the same files** (`plan_cover.held_record`, `held_covered`): `Trials._plan_for` hands the Package Manager `held` (the Core's own record: her card's consent id and a sha256 per held file, `zone3.drawbridge.held_fingerprints`). The Package Manager reads her Yes from the store itself (live, a `try_hold` card for this candidate, `held_sha` of these files). A hold is inside the plan only when every file Drawbridge holds at install is one she said Yes to, byte for byte (a link by its target), the walk was not cut, and nothing else holds it. Drawbridge's other notes under a hold (hosts it saw, hidden lines) are still outside, as a warn is (D60), so such an app can still get an install card. Drawbridge's `cant_check` row now carries `all` (every held path) and `cut`.
- **Build grants live only while Toolsmith works** (Z3 section 2), in the Bridge (`Bridge.open_build`, `end_build`, `close_stale`): a Yes record whose grants belong to a build (`cloud`, a `host` Yes in phase `build` such as her wizard search choice, a build's plan Yes `toolsmith-build-*`) turns its switch on as before, but its grants are made only while a build runs. `ToolsmithHost._launch` opens them from her live records (`Ports.bridge_build_open`, only for a subject whose switch is on: this never turns a switch on) and `_child_done` closes them (`bridge_build_end`): done, not yet, gave up, a crash, a launch that failed, a stop. A build's own `pkg:toolsmith-build-*` grants and switch go when it ends; Toolsmith's shared build-phase grants go when no build runs. Her Yes records stay, so the next plan or build opens again.
- **Install grants close when the install ends** (`PackageManager.on_install_end`, wired to `Bridge.end_install`), installed or failed.
- **At Core start and stop** (`wiring.close_stale_grants`, in `wiring.start` and `wiring.stop`): every build grant and every install grant left (a crash, a hard kill) closes; run grants stay.
- **One posture source**: `GET /v1/settings` now reads the Bridge's own policy (`bridge`: `open` | `closed`, `bridge_search`: the step-4 switch; `not_set` only with no Zone 3), the same as `GET /v1/bridge` and `puffpai bridge status` (which now also says whether Toolsmith may search and what is let through now). `settings\bridge.json` is not read or written (03 step 4 still names it: for the docs lane).
- **DECIDED HERE (restrictive, needs a yes):** the hold card uses the existing `package` consent kind (no new kind in `contracts/events.json`); it expires after the store's 10 minutes like every card, and that counts as a No (the build does not wait 7 days as on a Checklist step). A warn under a hold still gets an install card (D60), so "one card per candidate" holds for holds without other notes.
- **Not proven:** a real GitHub clone with real held files on this computer (the tests use the fixture repo with a `.bin` file and the real Drawbridge, consent store, Package Manager and Steward); the Desktop's card rendering (no browser test).

# NOTE from lane I8b (10-25): the Desktop ported from today's BuildBot dashboard (774e27a4)

For the merge chat and the docs lane. PROPOSED wording; the docs decide.

- **New Core routes (x-lane I8b in openapi.yaml):** `GET` and `PUT /v1/settings/layout`, `POST /v1/settings/layout/arrange` (Home's pin, hide, move, rename; `core/puffpai_core/layout.py`, harvested from v1 `dashboard_widgets.py` `get_layout` :754, `save_layout` :776, `arrange` :842; kept in `data\desktop\layout.json`), and `GET /v1/packages/available` (the built-in packages not installed yet). Merge note: lane I8c adds the `/v1/dev/shell` routes to openapi.yaml too; the Desktop's terminal tile shows "Coming in a later build" until they are in.
- **Avatar face (01 "How the UI works", 02 avatar):** an avatar package may declare a page with the id `face`; the Desktop frames it on the package's own origin (`sandbox="allow-scripts allow-same-origin"`; its origin is its own, never the Core's) and posts only `{"type": "puffpai.avatar", "talking", "pulse"}`, to that origin only, while the bot answers. `cloud-avatar` 1.1.0 does (`face.html`, ported from v1 `PuffCloudAvatar.tsx`).
- **Core gaps the Desktop names as "Coming in a later build":** `PUT /v1/model` (the brain; Setup's brain step changes it today); Toolsmith's key from Settings (`/v1/vault/secrets/<name>` takes `^[A-Z][A-Z0-9_]{0,63}$`, but Toolsmith reads `toolsmith.<provider id>`, so only Setup's Toolsmith step can save it, though that step tells her "Open Settings to add it"); paired devices (list, remove); linked folders (list, link); `puffpai check` results; vault `store/move` and `backup/*` (501). The chat has no route to read back the conversation, so the page starts empty on reload.
- **Not proven:** the mouth moving from a real chat stream (no brain was set on the scratch bot; the message path was proven in a browser); the terminal tile against lane I8c's real route; Apps > Add through a real Toolsmith plan (no network, no paid calls).

## Lane I8b round 2 (10-25): The owner's own Puff, the shell route details, the verifier's notes

- **Puff is the owner's own drawing** (`packages/cloud-avatar/puff.webp`, 2000 x 2000, transparent around the cloud, kept byte-identical; `preview.png` is its lossless full-size PNG, because `avatar.preview` takes SVG or PNG only). v1's drawn mascot `preview.svg` is gone. The Desktop keeps its own copy (`packages/desktop/web/img/puff.webp`) for the rail, the tab icon and the face before `cloud-avatar` is installed. The face frame is now `sandbox="allow-scripts allow-same-origin"` (an opaque origin could not be messaged by origin) and the Desktop posts only to the face's own origin.
- **DECIDED HERE (needs a yes from the docs lane):** the Bridge offers Close whenever its own `setting` is open, also when nothing is let through and it reads closed (lane I6b had no button there).
- **Lane I8c's shell route, as the Desktop uses it:** one line per input with no line break (the Desktop no longer sends a trailing newline), plain words for 409 safe_mode, 429 too_many, 404 and 503, and Ctrl+C's stopped, idle and restarted. Proven only against the contract read from `lane/i8c-dev-shell` at `d7bf207c`, not a merged route.
