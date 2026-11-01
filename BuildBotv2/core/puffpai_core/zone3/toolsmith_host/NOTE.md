# Toolsmith's Core side: notes for the next lanes

Lane L6, on main `3881f0d4` (L1, L3, L4 merged). Plain notes on what does not line up yet.

## Why Toolsmith's process is started here, not by Steward

09 section 3 says Steward starts Toolsmith's process in its own job object, with a Toolsmith token on
stdin. L1's Steward starts **installed packages**: `Steward.start(id)` needs a Registry row and a
manifest, gives the process a package token (`PUFFPAI_TOKEN`), its keys and the Bridge proxy variables in
its environment, and closes stdin. Running Toolsmith that way would make it a package with package
rights, and would put a token in its environment, both against 09. So `process.ProcessRunner` starts it
(`python -I -m puffpai_core.zone3.toolsmith`), with **Steward's own job object code**
(`steward.jobs.Job`, `KILL_ON_JOB_CLOSE`), the token on stdin only, and no proxy or `PUFFPAI_*` names.
Core stop (`api.app.serve`) stops it with every try (D18).

To move it under Steward without changing L1's design, Steward would need a Core-internal start:
`Steward.start_core_process(name, argv, stdin=bytes, env=...)` that holds the job object, reports it in
`running()` / `pids()` for the bypass watch, and gives no package token. That is L1's call.

## Found while aligning with main (not changed here: L1's and L4's files)

1. **L1 looks up Drawbridge as a module function.** `packages.manager._verdict` calls
   `seams.find("zone3.drawbridge.check_package")` and expects `{"verdict": "pass", "tree_sha"}`. L4 has
   `Drawbridge.check_package` (a method of the Core's one `Drawbridge`, `zone3.service.for_app(app)`)
   answering `allow | warn | hold | block`. On main today `PackageManager.add` refuses everything
   ("Drawbridge is not here yet"): closed, but nothing installs. Toolsmith says so on the job.
2. **Two tree hashes.** L1 compares Drawbridge's `tree_sha` with its own `fsutil.tree_hash`; L4 hashes
   differently (`drawbridge.tree_sha`), so even a mapped check reads "checked different files".
   `tests/test_toolsmith_main_e2e.py` maps both for the test only and proves the rest of the chain.
3. **The plan's Yes and an adopted app (updated in lane I2a, 10-22).** Toolsmith's host hands the
   Package Manager the build's plan with `add(folder, adopted=<url>, plan=...)`; `packages.plan_cover`
   reads the plan's Yes from the consent store itself. Inside the plan the app's card is answered at once
   "by plan:<consent id>"; anything outside it gets ONE card that lists just the difference, and the job
   waits (`waiting_yes`). Toolsmith never answers a card. Outside the plan means any of: hosts or keys
   not on it, admin, a setup step she did not tick (or that changed since, D60), a step naming a changing
   tool (D57), a Drawbridge warn or hold, and (I2a part C) any command the Core itself starts from the
   manifest (`run.<os>.setup` and `start`, a health command, a stdio MCP server, `ui.open`) that is not
   exactly one the broker ran with success in this try (`Broker.record`, in the Core's memory, written by
   no route), or a run block `env` beyond `PORT={port}` and `HOST=127.0.0.1`. The manifest that counts is
   `.buildbot\puffpai.json`, which the Core writes itself from the manifest Toolsmith sends with add
   (`Trials.add`); a top-level `puffpai.json` from the download is never read for an adopted app, and
   Toolsmith's file tools can't write, move or delete either one. A floor-level card is never covered.
   (Part E) Every part of the Core that reads an installed package's manifest goes through one function,
   `packages.manifest.installed` (`read_installed`, `Steward.manifest`, `Steward.read_manifest`): the file
   the Registry row's origin says, in the folder `Installs` records, only while it has the row's
   `manifest_sha`; a changed file makes the row broken. The brain's tool table and MCP client, read_app,
   pages, `ui.open`, Caretaker, Drawbridge's scope, `puffpai check`, the Desktop, the wizard's steps and
   Toolsmith's provider list all use it. A folder in `packages\` with no Registry row (a try lives there)
   is never read as a package.
4. **Origin.** `PackageManager.add(folder)` records `origin: local:<path>`. There is no way to pass
   `adopted:<url>`, so the Registry row does not say where the app came from.
5. **Safe mode at runtime.** Caretaker has `safe_mode` / `safe_reasons` from its self-test, but no
   method to enter safe mode while running. On a change to the Core's own Python (L4's `HookGuard`) the
   host closes the Bridge, locks the vault, stops Toolsmith, sets Caretaker's flags (so the Package
   Manager adds nothing) and raises one card. A `Caretaker.enter_safe_mode(reason)` would be the one place.
6. **A tick hook on Caretaker.** While a try's server runs, its Python start-up files are checked again
   on every Caretaker tick (as well as at every Toolsmith step). Caretaker has no list of tick hooks, so
   the Toolsmith host wraps that one Caretaker's `tick` once. A `Caretaker.on_tick` list (like
   `Steward.on_start`) would be the clean place.

## Lane I4b (build flow, from the second fresh-install proof, 10-23)

7. **Offers.** When only one of the two reuse asks picks an installed package's tool, the plan carries an
   `offer`. The card shows it unticked. Her tick is recorded by the Core (`Builds.set_use`), from the
   wizard's Yes step or `PUT /v1/toolsmith/builds/<id>/use` (owner device), and fixed at `start`. The
   Desktop's chat card does not show a tick box yet, so a plan made from chat can't tick an offer there
   (it searches as planned). PROPOSED: the Desktop calls that route before it answers the card.
8. **A subject check on candidates.** A found project is kept only when its name, description, topics or
   README (all of it) name the job's own subject words (`find.subject_of`: its name, what it does and the
   brain's own search phrases for it, whole words by root, generic words left out). Each one left out gets
   the line "its name, description, topics and README never mention <every word looked for>". GitHub's own
   results are no longer filtered silently before that. Test proof that koala73/worldmonitor stays first:
   `tests/data/worldmonitor`, a fixture modeled on GitHub's answers (GitHub's search itself misses it for any phrase with "map"). This is a
   "fits the job" filter, which 03 3.2 rule 4 said not to have; the orchestrator asked for it after the
   proof (a blog engine for a shopping list). It needs a decision row in 00.
9. **Hold cards in one batch.** A held download is set aside (`Trials._defer_hold`: fingerprints kept,
   the folder removed before anything ran). Only when no fully checked candidate worked does Toolsmith
   ask for ONE card per job (`holds_ask`); her ticks go through `Trials.tick_holds` (the wizard's build
   step, or `PUT /v1/toolsmith/builds/<id>/holds/<consent id>`). Only ticked apps are tried: a Yes with no
   ticks recorded tries none (the Desktop and the wizard send her ticks first). The approved app is downloaded again and goes on only when Drawbridge holds it for the same
   files with the same fingerprints.
10. **Where a try really listens.** `Trials.listens` reads the listen table for the try's own job objects
   on every `try_log`. A server of the try on 0.0.0.0, `::` or a LAN address is stopped there. Toolsmith
   reads the page on the port its own server holds, and `done` on another port counts only when the
   try's own processes listen there. A docker compose try is not covered by this (its ports are
   Docker's).
11. **Fixed at her answer (verifier round on d2d79530).** Offer ticks are taken only while the plan card
   waits; when it is granted, the consent store's Yes listener has the Core copy them into `use_fixed`, and
   `start` reads only that copy. A job gets at most ONE hold card per build, and only after every fully
   checked candidate of the job has ended (one the cloud rank left out counts as ended); a held app she did
   not tick, or one refused after her Yes, stays skipped for the rest of the build. `done` counts a page
   only when one of the try's own processes listens on that port, its assigned port included.

## Lane I8a (the owner's live test, 10-25)

12. **Her cloud model Yes is a record the Bridge reads.** Before this lane the wizard's Toolsmith-model step and
   `PUT /v1/toolsmith/model` kept her choice only in `settings\toolsmith.json`. No `cloud` Yes record was ever
   made outside tests, so the Bridge never turned `toolsmith:cloud` on and `open_build` had nothing to grant
   from: every call was refused (`switched_off`). Now `ToolsmithHost.choose` records her Yes to the company's
   own consent sentence through `Ports.cloud_yes` (kind `cloud`, target `toolsmith:cloud`, covers the company's
   hosts that are on the Core's known list, `phase: build`, its `provider` and `consent_sha`) before the setting
   says cloud; a choice that can't be recorded falls back to this computer. Each pick takes the one before back;
   picking this computer takes every cloud Yes back and drops the switch (`Ports.cloud_off`). The sender holds
   a job with a plain line when no live Yes matches the company and sentence (a bot set up before this lane:
   she picks the company again in Settings). PROPOSED decision row: "Her Yes to a company's consent sentence is
   the Bridge's cloud record; only that company's known hosts, only while a build runs."
13. **A newer plan replaces the old one.** When the wizard plans again with a pasted address, the old build ends
   (`Builds.end_replaced`, state `replaced`, "Replaced by a newer plan."), its plan card expires with that line
   (a Yes on it from the Desktop is refused with it), a Yes already given to it is taken back, and `start`
   refuses it. A build her Yes already started is never replaced.
14. **The brain's yes/no check** (`find.Finder.brain_says_no`): one ask per candidate that passed the word
   check, "Is <name>: <description> an app for <what the job does>?". It never drops: a plain no only puts the
   candidate after the others, said in the plan's `detail` (the build log), never on the card. (Verifier round:
   her qwen3.5:9b said no to good budget apps, with thinking on or off.) The word check leaves out only filler
   words (pronouns, place words, "across", "limit"); a job's domain words (vendor, budget, theme, color,
   design, event) always count. A floor of "keep a job's words when fewer than two are left" was asked for and
   not added: it makes "planner" count again for "Dinner Planner" (the 10-23 trip planner for dinners,
   `test_subject_words_leave_out_generic_ones`); taking the domain words back already keeps Vendor Tracker at
   {vendor, wedding}. With item 8 this needs the decision row item 8 asks for.
15. **A pasted project always gets its try** (D63(c)), even when its README gives no run steps or it has no
   README: the try is told to work out how to start it from its files, and GitHub's language for it (one quiet
   read of `api.github.com/repos/<owner>/<repo>`) says how it runs, so its install hosts are on the plan card.
   Only when GitHub could not be read at all is that the reason instead.
16. **Every GitHub form** (`find.github_of` / `github_url`): with or without https:// or www., a clone address,
   .git, and any page after the name (/tree/<ref>/..., /blob/..., #readme, ?tab=...) give owner/repo; GitHub's
   own pages (topics, orgs, ...) and other hosts give nothing. A pasted address is kept as
   https://github.com/owner/repo. The wizard asks again, in plain words, for one that is not a GitHub project
   address, and says "Toolsmith tries github.com/owner/repo only." only for one that parsed.
17. **GitHub could not be reached** is the reason (the Bridge closed, or the network error), never "no README".
18. **Tools are said by what they do** (`find.tool_does`: the tool's own description, first sentence), never by
   name, on the card, the offer line, the wizard's offer note and the reuse job lines; names stay in `detail`
   or the result's proof.
19. **Tool words are screened** (last verifier round). A package's live MCP tool description goes through
   `text_rules.owner_text_flag` before any of it is shown on her card, the offer line (the Desktop's tick label)
   or a reuse job line: words aimed at an AI or at her answers (`instruction_shaped`, plus talk of her Yes, cards,
   PIN or keys), a link or a scheme, markup, hidden or control characters. Flagged: no words at all ("it has a
   tool for this job"), and the build log says why. When Caretaker reads the tools, Drawbridge's
   `screen_tool_words` drops a description aimed at an AI (or with hidden characters) from the Registry and marks
   the package `needs_you` with one `tool_words` card; a link or markup only keeps it off her cards. No screen:
   every description is dropped. `needs_you` clears when she stops it and starts it again, and the tools are
   screened again then (no review screen of its own yet).
20. GitHub addresses are ASCII only, `:443` is dropped and any other port refused, and GitHub's own first
   segments are never an owner. A brain "no" left out by the five-app limit gets a true "Not used" line. HTTP
   403, 429 and 5xx from GitHub read "GitHub could not be reached (it answered <n>)", and a closed Bridge is one
   line for every GitHub host.
