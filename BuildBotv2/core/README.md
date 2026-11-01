# PuffPAI Core: the code

The first v2 code (D37). Plan and lanes: `docs/10_BUILD_PLAN.md`. The contracts in `contracts/` come first; this code follows them (`contracts/README.md`).

## Set up (Windows)

```powershell
# make the venv with your Python 3.11 or newer: <path to python.exe> -m venv .venv
# for example, with the per-user install:
%LOCALAPPDATA%\Python\pythoncore-3.11-64\python.exe -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
```

## Run the tests

```powershell
.venv\Scripts\python -m pytest -q            # everything
.venv\Scripts\python -m pytest -q tests\floor # the floor suite only
```

Tests use a throwaway bot folder (`PUFFPAI_STATE`), never `%LOCALAPPDATA%\PuffPAI`.

## Run the Core

```powershell
.venv\Scripts\puffpai serve          # Core API on http://127.0.0.1:8420, the Desktop at http://puffpai.localhost:8420 (D79; Ctrl+C stops it)
.venv\Scripts\puffpai list           # the Registry
.venv\Scripts\puffpai check <folder> # "Works on PuffPAI" Levels 1-3 on a real, throwaway run
.venv\Scripts\puffpai add <folder|git URL> [--link]   # the one Yes in the terminal
.venv\Scripts\puffpai start|stop|logs|remove <id>
```

The bot folder is `%LOCALAPPDATA%\PuffPAI\<bot-name>\` (`PUFFPAI_BOT`, default `buildbot`), or `PUFFPAI_STATE` if set.

## What is built (lane L0)

| Part | Where | Interface |
|---|---|---|
| Contracts | `contracts/` | `puffpai-package.schema.json`, `registry.schema.json`, `events.json`, `openapi.yaml` |
| Registry | `puffpai_core/registry` | `Registry().add/update/set_health/get/list/remove/validate`, `row_from_manifest` |
| Manifest checks | `puffpai_core/packages/manifest.py` | `load`, `validate`, `check_files`, `ManifestError` |
| Bus | `puffpai_core/bus` | `Bus().publish/ask/subscribe/answer/register/send`, `Bus.message`, `Source`, `Envelope` |
| Slot contracts | `puffpai_core/zone{1,2,3}/slots.py`, `puffpai_core/slots.py` | `typing.Protocol` per slot; `SLOTS` table |
| Core API | `puffpai_core/api` | `create_app`, `serve`; `routing.route/stub/current_core`; `auth.TokenStore` and access levels |
| CLI | `puffpai_core/cli.py` | `serve`, `list`, `check`; the rest name their lane |

## What is built (lane L1)

| Part | Where | Interface |
|---|---|---|
| Package Manager | `puffpai_core/packages/manager.py` | `PackageManager.add(source, link) -> {consent_id, id, card, floor}`, `wait(consent_id)`, `remove(id, keep_data)`, `update(id)`; install runs after the owner's Yes |
| Yes cards | `puffpai_core/consent` | `ConsentStore.request/answer/get/list/wait/on_answer` |
| Steward | `puffpai_core/steward` | `Steward.setup/start/stop/restart/alive/token/pids/stop_all/start_saved`; `jobs` (job objects), `ports` (18420-18999) |
| Caretaker | `puffpai_core/caretaker` | `Caretaker.probe/check/tick/start/stop/refresh_tools/mark_broken/selftest`, `safe_mode` |
| `puffpai check` | `puffpai_core/packages/check.py` | `check(folder) -> Report` (Levels 1-3) |
| Zone seams | `puffpai_core/seams.py` | the other lanes' functions L1 calls; a missing one is a no |
| Routes | `api/groups/packages.py`, `pages.py`, `providers.py`, `consent.py` | `contracts/openapi.yaml` |

Everything else is a package directory whose docstring says which lane fills it.

## What is built (lane L2, the Brain)

| Part | Where | Interface |
|---|---|---|
| Model settings (one file) | `zone2/settings.py` | `settings\model.json`: model, address, context, tool budget; `load`, `save`; X12 address check |
| Ollama adapter (`zone2.runtime`) | `zone2/ollama.py` | `OllamaRuntime.chat` (tools, streaming, `think: false`, `usage`), `state`, `warm`, `pull` |
| Recommender + profiles | `zone2/catalog.py`, `zone2/data/*.json` | `fit_menu` (D36(e)), `get_profile` (tool budget, thinking, vision) |
| For the wizard | `zone2/models.py` | `menu(census)`, `fit(census)`, `state(name)`, `census()` |
| Gate before Zone 2 | `zone2/gate.py` | consumes `zone1.gate.prepare(text, vault_open)` (L3); fails closed without it; `no_keys` is the one floor key check (D31(b), D50); `Gate.clean` gates a value of any shape (tool results, errors, package messages) |
| Brain | `zone2/brain.py` | `Brain.turn`, `sample_ask`, `package_chat`, `call_tool` |
| API | `api/groups/chat.py`, `brain.py`, `model.py` | `/v1/chat`, `/v1/chat/stream`, `/v1/chat/tool`, `/v1/brain/chat`, `/v1/model`, `/v1/model/menu` |

The live test runs only on request and only with a small tool model already installed: `.venv\Scripts\python -m pytest -q -m live tests\test_zone2_live.py`.

## What is wired (integration lane I1)

`puffpai_core/wiring.py` joins the lanes in a running Core (Drawbridge on the install path, one PIN check, the
Bridge proxy and bypass watch with the Core, proxy credentials for Steward, Drawbridge's everyday review for chat,
Caretaker cards to notices, the wizard's vault, Bridge and model parts). The end-to-end run:
`.venv\Scripts\python -m pytest -q -m e2e tests	est_i1_e2e.py` (needs the local model; see `tests/i1_e2e.py`).
