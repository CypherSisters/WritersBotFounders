"""Zone 2, the Brain (lane L2).

| Part | Module | Interface |
|---|---|---|
| Model settings (one file) | ``settings`` | ``load(state)``, ``save(state, **changes)``, ``ModelSettings``, ``address_kind`` (X12) |
| Model slot: Ollama | ``ollama`` | ``OllamaRuntime`` (``zone2.runtime`` Protocol + ``state``, ``warm``, ``pull``) |
| Brain states (D36(d)) | ``errors`` | ``BrainError(message, code)``, state names |
| Catalog, profiles, recommender (D36(e)) | ``catalog`` | ``fit_menu``, ``get_profile`` |
| Hardware census | ``census`` | ``run_census`` |
| For the wizard (L5) | ``models`` | ``menu(census)``, ``fit(census)``, ``state(name)``, ``census()``, ``runtime()`` |
| The gate before Zone 2 | ``gate`` | ``Gate(prepare, vault_open)``, ``production()`` (needs ``zone1.gate``, L3) |
| Tool table | ``tools`` | ``Tool``, ``ToolTable``, ``call``; ``<id>.<tool>`` shown, ``<id>__<tool>`` sent |
| MCP client | ``mcp`` | ``target_for``, ``list_tools``, ``call_tool`` |
| Concierge | ``concierge`` | ``Concierge.table_for(text, budget)``, core tools |
| Tool loop | ``loop`` | ``run`` |
| ``read_app`` / ``open_app`` | ``apps``, ``page_reader`` | ``read_page``, ``read_app``, ``open_app`` |
| ``run_command`` (D138) | ``commands`` | ``question``, ``run``, ``run_program`` |
| Her own programs (D16, D138) | ``programs``, ``program_help`` | ``register``, ``match``, ``run_cli``, ``launch``, ``open_tool`` |
| Conversation store | ``conversations`` | ``ConversationStore`` |
| The Brain | ``brain`` | ``Brain.turn``, ``Brain.sample_ask``, ``Brain.package_chat``, ``Brain.call_tool`` |

The ``chat``, ``brain`` and ``model`` API groups use these. Slot Protocols: ``zone2.slots``.
"""
