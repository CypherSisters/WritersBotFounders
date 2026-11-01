# contracts/

**These files are the contract; code follows them.** When the code and a file here disagree, the code is wrong. When a file here and a design doc disagree, the doc wins (00, then 01, then 02, then the rest; `docs/zones/ZONES_TOGETHER.md` on the zone seams), and the file is fixed to match. Every file is plain JSON or YAML, so any person or AI can read it without running anything.

| File | What it is | Source | Who checks it |
|---|---|---|---|
| `puffpai-package.schema.json` | JSON Schema (2020-12) for `puffpai.json`: every kind, `run` blocks and placeholders, `health`, `abilities`, `ui`, `needs`, `accepts`, `setup_steps` (the one step shape, `$defs/step`), `data`, `includes`, `scope`, `avatar`, `sources`, wizard `steps`, and the provider fields (`provides`, `reads`, `floor`, `devices`, `detector`, `labels`, `store`, `unlock`, `backup`). Unknown top-level fields are refused. `x-plain` on a rule is the plain message shown when it fails. | 02 field reference, 03 1.1, zones | `puffpai check` Level 1 item 1; the Package Manager before any Yes |
| `registry.schema.json` | One Registry row: id, kind, origin, status, `health` (probed, never declared), abilities, ui, needs, port, `built_with`, provider fields, `taint`. | 01 "Registry" | The Registry on every write |
| `events.json` | Every Bus event and check: who may publish (Core-only namespaces `vault.*`, `egress.decision`, `consent.*`, `provider.*`), who may receive, the schema-locked payload, the fields never allowed (values), and each check's safe answer. | ZONES_TOGETHER section 2 | The Bus on every message |
| `openapi.yaml` | Every Core API route, grouped as 01 lists them, with who may call it (`x-access`) and the lane that builds it (`x-lane`). Auth: owner API key, paired device, or a package's own `PUFFPAI_TOKEN` (brain, notify, theme, its own Registry row, its own Checklist steps, its events). | 01 "Core API", 02, 04, Z1V 8 | A test holds the Core's routes to this list exactly |

Status: written by lane L0 on 2026-10-21. Bodies marked PROPOSED in `openapi.yaml` are a starting point for each route's lane; routes not built answer 501 with `{"error": "not built yet", "lane": "<L#>"}`.
