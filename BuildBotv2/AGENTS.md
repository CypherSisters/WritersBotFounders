# AGENTS.md: read this first

For any AI assistant (or person) working in this repo.

## What BuildBotv2 is

PuffPAI BuildBot is an AI OS: "the AI bot that builds itself". Every bot has the same small **Core** (three zones: Vault, Brain, Bridge; seven fixed agents: Concierge, Steward, Drawbridge, Archivist, Majordomo, Caretaker, Toolsmith; plus the Registry, Bus, Package Manager, Checklist and Core API). Everything else is a **package**: one folder with one `puffpai.json`, its abilities reaching the brain over MCP. A wizard scans the computer, helps the owner pick a local model, asks what the bot should do, and Toolsmith finds ready-made apps and knowledge, adopts them as packages, and proves they work. Like Linux: a bare base, and anyone can add apps, tools, UIs, avatars and agents, by code, by GUI or by chat. BuildBotv2 is the rebuild that replaces v1 (D1, D2).

## Current phase

**Built.** Version 2.0.0, the developer release. A change to a decision still goes in `docs/00_INTENT_AND_DECISIONS.md` first. v1's code is a read-only parts bin.

## The one rule

> **The Core never imports, edits or special-cases package code.** Packages talk to the Core only through the Core API, and to the brain only through MCP.

If adding one specific app needs a Core edit, the design is wrong. Stop and say so.

## Where things are

| File | What |
|---|---|
| `docs/00_INTENT_AND_DECISIONS.md` | Why. The owner's words, decisions (D1 onward, with dates), standing rules, glossary. **The source of truth.** |
| `docs/01_ARCHITECTURE.md` | The Core, part by part; how the brain sees everything; how the UI works |
| `docs/02_PACKAGE_CONTRACT.md` | `puffpai.json`, the kinds, the "Works on PuffPAI" levels, adopted and made packages |
| `docs/03_WIZARD_AND_TOOLSMITH.md` | The wizard steps; Toolsmith's four questions; find, adopt, run, prove |
| `docs/04_DEVELOPERS.md` | "Build for PuffPAI": quick starts, CLI, Core API, rules for AI assistants |
| `docs/05_FIVE_APPS_AS_PACKAGES.md` | The five existing apps rebuilt as packages |
| `docs/06_HARVEST_MAP.md` | Which v1 code each part is copied from |
| `docs/07_OPEN_QUESTIONS.md` | What only the owner can decide (what is still open; answered ones move into 00) |
| `docs/08_V1_LESSONS.md` | Today's BuildBot lessons (at `62253f94`), v2 status per lesson, proposals N1-N7, questions R1-R5 (now D14-D18 in 00) |
| `docs/09_AGENT_SECURITY.md` | The seven agents: zone, keys, reach and what needs a Yes (the full access table, part of 01); deny rules X21-X31; the checker and the checked; honest limits |
| `docs/zones/ZONES_TOGETHER.md` | The three zones together: slot names, events and checks, the label interface, the one floor list X1-X31 (X21-X31 are the agent deny rules from `docs/09_AGENT_SECURITY.md`). Wins over the other zone docs on these seams |
| `docs/zones/Z1_CLASSIFY_AND_LABEL.md` | Zone 1: detectors, labels, tiers P0-P4, label packs, retention |
| `docs/zones/Z1_VAULT_PROVIDERS.md` | Zone 1: the Core's key scheme; vault store, unlock and backup providers |
| `docs/zones/Z3_BRIDGE_PROVIDERS.md` | Zone 3: the Bridge decision; enforcers, network devices (watch first), monitors, feeds |
| `docs/10_BUILD_PLAN.md` | How the first build was split into lanes, and the rules lanes work by |
| `docs/11_TEST_STEPS.md` | The steps for a live test of a build |
| `docs/12_CLI_FIRST_STUDY.md` | The study behind the terminal chat and the window |
| `docs/recon/` | Evidence about today's code. Line numbers are pointers, not promises. |

When docs disagree: 00 wins, then 01, then 02, then the rest (among the zone docs, `ZONES_TOGETHER.md` wins on the seams).

## Zone providers (PROPOSED)

To add or replace part of a zone (a vault store, an unlock factor, a detector or label pack, a model runtime, a Bridge enforcer, a router or DNS-filter link, a block list), build a package with `"kind": "provider"` and `"provides": [{"slot": "zone<N>.<slot>", "contract": 1}]`. Slot names: `docs/zones/ZONES_TOGETHER.md` section 1. Start with `puffpai new provider <slot> <id>`; quick start in `docs/04_DEVELOPERS.md` ("A zone provider"). You never go below the floor (X1-X31, `docs/zones/ZONES_TOGETHER.md` section 4). Rules: answer only your slot's checks, by their deadlines; never publish `vault.*`, `egress.decision`, `consent.*` or `provider.*`; no values in events; only add labels or blocks, never loosen; a `reads: raw` provider gets no network, token or keys; a device provider starts watch-only and changes a device only with a `consent_id` for that one change. Done means `puffpai check --floor` passes, then Level 2 Fits. If the floor seems to block a good idea, stop and say so; do not work around it.

## Standing rules (from 00, still in force)

- **Toolsmith is the only built-in agent that may use a cloud model**, by the owner's explicit choice, with plain consent text on what is sent (the job name, READMEs, other text files from an app's own download, commands and their output, what its pages show). A try runs as a program on her computer, as her, so it can read her files, and what it shows goes to the company: her choice with awareness (D118). Keys and personal details it spots are taken out first, but it can miss some. The key lives in the vault. Everything else runs locally.
- **Outside AIs connecting to a running bot** (Claude Desktop, Claude Code, through the Core MCP server): off by default, the owner's explicit choice with consent text, D11 in `docs/00_INTENT_AND_DECISIONS.md`. Logs are excluded unless allowed per request.
- **App-first.** Find an existing app and run it as built, with its own UI. Make something only when no app exists.
- **Outside apps open in the owner's real browser**, in a new tab.
- **One Yes.** One plain plan, one Yes. Bigger steps later need a new Yes.
- **Choice with awareness.** Anything is allowed if the owner explicitly chooses it. Private by default.
- **Works over hardening.** It must just work. No new security layers. Docker and dependencies install automatically.
- **BuildBot is the factory.** Never hand-build one specific bot. Judge every feature with wildcard goals.
- **Delete layers, don't add.**
- **The LLM sees everything installed, automatically.**
- **One data folder.** Linked folders are indexed in place, never copied. Personal data and keys found go to the vault.
- **Linux, Mac, Jetson and Windows are all required for the v2 release** (D128, replaces D7 and D38). No Windows-only code.

## Writing rules (all docs, UI text and code comments)

- Plain words, short sentences, dense. No marketing fluff.
- No em dashes. Use commas, colons or parentheses.
- "Rough edges", never "warts". Never "beta".
- The Bridge is "open or closed". Never "doors" in owner-facing text.
- Privacy line: "Nothing leaves your computer without your consent."
- The owner is "the owner" (she/her). Credit: Cypher Sisters.
- Never remove or reword the "puff puff pass" signature.
- Mark anything not built yet as PROPOSED. Never claim untested work works. Cite `path:line` for claims about today's code, or say "not verified".

## How to add to the design

1. A new or changed decision goes in `docs/00_INTENT_AND_DECISIONS.md` **first**, as a new row with the date (D10, D11, ...). Never rewrite an old row silently; add a row that replaces it.
2. Then update the docs that follow from it (01 to 06) and cite the decision number.
3. Found a contradiction between docs? Don't paper over it. Write it down and ask the owner.
4. Commit on your own branch with explicit paths. Do not push unless the owner asks.

## Never touch

- **The owner's own HomeBot** (D8): its machine and its code. Never ssh anywhere.
- **v1 checkouts and other worktrees:** read only. Other sessions edit them live. No edits, no checkout, no stash, no servers.
- **The owner's test installs** and her running services and ports.
- Anything outside this repo, unless the owner asks.
