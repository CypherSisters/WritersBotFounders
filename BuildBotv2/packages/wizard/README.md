# BuildBot setup (kind `wizard`)

The default setup steps as data (03 1.1 and 1.2): `puffpai.json` `steps`, each with `fields` (`show_when`, per-platform `examples`, `home` for secrets). The Core's engine (`core/puffpai_core/wizard/`) runs them; the Desktop's Setup page and `puffpai setup` draw the same records. No step has its own `page` yet: every default step is drawn from its fields, so both renderers show exactly the same thing. A bundle may add a step with a `page` (it is served at `/pkg/<bundle>/...`).

Some wording is kept from v1 `src/puffpai/wizard/wording.py` at `PuffPAIBuildBot@63c8a037` (the goal help and sample goal); the rest is from 03.
