# PuffPAI BuildBotv2

**Build it once, own it forever.**

BuildBotv2 is the clean rebuild of PuffPAI BuildBot as an AI OS: a small Core that every bot shares (three zones, seven agents, one Registry), with everything else added as packages. It's the same idea as Linux: a bare base that anyone can build anything on.

**Status: design phase (started 2026-10-14).** No code yet. The owner reviews the design before any code is written. Today's BuildBot keeps running and getting fixed until v2 passes her live test.

## Read in this order

| Doc | What it answers |
|---|---|
| [docs/00_INTENT_AND_DECISIONS.md](docs/00_INTENT_AND_DECISIONS.md) | What the owner asked for, every decision with its date, standing rules, glossary |
| [docs/01_ARCHITECTURE.md](docs/01_ARCHITECTURE.md) | The bare metal: the Core, the 7 agents, Registry, Package Manager, Checklist, Core API, how the brain sees everything |
| [docs/02_PACKAGE_CONTRACT.md](docs/02_PACKAGE_CONTRACT.md) | How to build anything that works on PuffPAI: `puffpai.json`, package kinds, the "Works on PuffPAI" criteria |
| [docs/03_WIZARD_AND_TOOLSMITH.md](docs/03_WIZARD_AND_TOOLSMITH.md) | The wizard steps, and how Toolsmith finds, adopts, makes and proves |
| [docs/04_DEVELOPERS.md](docs/04_DEVELOPERS.md) | Build for PuffPAI: GUI, CLI and chat; quick starts for every package kind; notes for AI coding assistants |
| [docs/05_FIVE_APPS_AS_PACKAGES.md](docs/05_FIVE_APPS_AS_PACKAGES.md) | What the five existing apps need today, and what each becomes as a v2 package |
| [docs/06_HARVEST_MAP.md](docs/06_HARVEST_MAP.md) | Which existing code each v2 part is pulled from, and what gets left behind |
| [docs/07_OPEN_QUESTIONS.md](docs/07_OPEN_QUESTIONS.md) | What still needs the owner's call, and what is settled by default |
| [docs/08_V1_LESSONS.md](docs/08_V1_LESSONS.md) | What today's BuildBot learned, what v2 already covers, and what was folded into 01 to 07 |
| [docs/09_AGENT_SECURITY.md](docs/09_AGENT_SECURITY.md) | The seven agents: where each lives, what it may touch, what needs your Yes, and what the safety checks do and do not promise |
| [docs/zones/ZONES_TOGETHER.md](docs/zones/ZONES_TOGETHER.md) | The three zones together: provider slot names, events and checks, the label interface, the one floor list (X1 to X31) |
| [docs/zones/Z1_CLASSIFY_AND_LABEL.md](docs/zones/Z1_CLASSIFY_AND_LABEL.md) | Zone 1: detectors, labels and tiers P0 to P4, label packs, retention |
| [docs/zones/Z1_VAULT_PROVIDERS.md](docs/zones/Z1_VAULT_PROVIDERS.md) | Zone 1: the Core's key scheme, vault store, unlock and backup providers |
| [docs/zones/Z3_BRIDGE_PROVIDERS.md](docs/zones/Z3_BRIDGE_PROVIDERS.md) | Zone 3: the Bridge decision, enforcers, routers and DNS filters (watch first), monitors, block lists |
| [docs/recon/](docs/recon/) | Evidence: read-only recon of today's BuildBot and HomeBot |

AI assistants: start with [AGENTS.md](AGENTS.md).

## Install

Linux or Mac, from a clone (D132; `--dry-run` shows every step and changes nothing):

```bash
./install.sh
```

Windows: double-click `install.cmd`.

## The code

The first build (D37) is under way: [contracts/](contracts/README.md) (the contract files) and [core/](core/README.md) (how to set up, run the tests and run `puffpai serve`).

Cypher Sisters. puff puff pass.
