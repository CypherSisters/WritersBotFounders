"""One module per Core API route group (01 "Core API"); each has a ``bp`` Blueprint.

A lane edits only its own group files. The full route list is
``contracts/openapi.yaml``.
"""

from . import (addresses, audit, brain, bridge, chat, checklist, consent, dev, events, github, health, knowledge, model, models,
               network, notify, openai_api, owner_apis, packages, pages, people, programs, providers, registry, settings, shutdown, store, theme, tool_servers, toolsmith, ui, vault,
               wizard, you, zones)

ALL = [health, registry, packages, pages, providers, chat, brain, model, models, vault, bridge, consent, audit,
       checklist, events, wizard, settings, you, notify, theme, people, toolsmith, ui, dev, shutdown, knowledge, zones,
       github, owner_apis, addresses, store, tool_servers, openai_api, network, programs]
