# Third-Party Notices

BuildBot v2 is by Cypher Sisters, under the Apache License 2.0 (`LICENSE`). This file lists what in this repository, or what it installs, comes from someone else.

## Copied or adapted code

### Hermes Agent (MIT)

BuildBot v1 copied patterns and short pieces of code from Hermes Agent by Nous Research (https://github.com/NousResearch/hermes-agent, read at commit `ea81748579ee1732d214ccb75f91d22208ed623d`). v2 carries them on, by way of v1. The files, each of which names its v1 source or the idea in its first lines:

- `core/puffpai_core/zone2/tools.py`, `zone2/loop.py` and `zone2/commands.py`: the shape of Hermes's tool registry and agent loop, the "grace call", and its ask-before-each-command approval.
- `core/puffpai_core/zone2/mcp.py` and `zone2/tools.py`: v1's MCP client code, which took its tool naming and input-schema repair from Hermes.
- `core/puffpai_core/zone2/skills.py`: parts of the `SKILL.md` frontmatter reading and the prompt wording.
- `core/puffpai_core/zone3/toolsmith/scout.py`: the pattern of one agent loop with tools, as its first lines say.

Hermes Agent's license:

```
MIT License

Copyright (c) 2025 Nous Research

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### OpenClaw (idea)

`core/puffpai_core/zone2/skills.py` follows the way OpenClaw lists skills to a model by name and one line, as v1 did. No OpenClaw code is in this repository.

## Bundled files

### Fonts

In `packages/desktop/web/fonts/`, each with its license beside it. `LICENSE-fonts.txt` there gives each file's source.

| Font | License | Copyright |
|---|---|---|
| Fredoka | SIL Open Font License 1.1 | 2016 The Fredoka Project Authors |
| Nunito | SIL Open Font License 1.1 | 2014 The Nunito Project Authors |
| JetBrains Mono | SIL Open Font License 1.1 | 2020 The JetBrains Mono Project Authors |
| Material Symbols Rounded | Apache-2.0 | Google |

### Public Suffix List

`core/puffpai_core/zone3/data/public_suffix_list.dat`, from https://publicsuffix.org/list/public_suffix_list.dat, under the Mozilla Public License 2.0, as the header in the file says. It is shipped unchanged. `zone3/data/README.md` says how to refresh it.

## What it installs

These are not stored in this repository. pip installs them from PyPI. Licenses are as each package's own metadata says, read on a Windows install of this commit, and versions are what that install resolved.

### The Core (`pyproject.toml`)

| Package | Version | License | Note |
|---|---|---|---|
| annotated-types | 0.8.0 | MIT |  |
| anyio | 4.15.1 | MIT |  |
| attrs | 26.1.0 | MIT |  |
| blinker | 1.9.0 | MIT License |  |
| bottle | 0.13.4 | MIT License | Windows only |
| cffi | 2.1.1 | MIT-0 |  |
| click | 8.5.0 | BSD-3-Clause |  |
| clr_loader | 0.3.1 | MIT (its LICENSE file) | Windows only |
| colorama | 0.4.6 | BSD License | Windows only |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |  |
| Flask | 3.1.3 | BSD-3-Clause |  |
| h11 | 0.16.0 | MIT License |  |
| httpcore2 | 2.16.0 | BSD-3-Clause |  |
| httpx2 | 2.16.0 | BSD-3-Clause |  |
| idna | 3.20 | BSD-3-Clause |  |
| iniconfig | 2.3.1 | MIT | tests only |
| itsdangerous | 2.2.0 | BSD License |  |
| Jinja2 | 3.1.6 | BSD License |  |
| jsonschema | 4.26.0 | MIT |  |
| jsonschema-specifications | 2025.9.1 | MIT |  |
| MarkupSafe | 3.0.4 | BSD-3-Clause |  |
| mcp | 2.3.0 | MIT License |  |
| mcp-types | 2.3.0 | MIT License |  |
| opentelemetry-api | 1.45.2 | Apache-2.0 |  |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | tests only |
| pluggy | 1.7.0 | MIT License | tests only |
| proxy_tools | 0.1.0 | MIT License | Windows only |
| pycparser | 3.12 | BSD-3-Clause |  |
| pydantic | 2.14.2 | MIT |  |
| pydantic_core | 2.50.2 | MIT |  |
| Pygments | 2.21.0 | BSD-2-Clause | tests only |
| PyJWT | 2.15.1 | MIT |  |
| pypdf | 6.21.4 | BSD-3-Clause |  |
| pytest | 9.2.0 | MIT | tests only |
| python-multipart | 0.0.33 | Apache-2.0 |  |
| pythonnet | 3.2.1 | MIT | Windows only |
| pywebview | 6.2.1 | BSD License | Windows only |
| pywin32 | 312 | Python Software Foundation License | Windows only |
| PyYAML | 6.0.3 | MIT License |  |
| referencing | 0.37.0 | MIT |  |
| rpds-py | 2026.9.1 | MIT |  |
| sse-starlette | 3.6.0 | BSD-3-Clause |  |
| starlette | 1.10.0 | BSD-3-Clause |  |
| truststore | 0.10.5 | MIT |  |
| typing-inspection | 0.4.4 | MIT |  |
| typing_extensions | 4.16.0 | PSF-2.0 |  |
| uvicorn | 0.54.0 | BSD-3-Clause |  |
| waitress | 3.0.2 | Zope Public License |  |
| Werkzeug | 3.1.9 | BSD-3-Clause |  |
| zstandard | 0.25.0 | BSD-3-Clause |  |

### The Office package (`packages/office/requirements.txt`)

Installed in that package's own folder when the owner adds it.

| Package | Version | License |
|---|---|---|
| python-docx | 1.2.0 | MIT License |
| openpyxl | 3.1.5 | MIT License |
| python-pptx | 1.0.2 | MIT License |
| lxml | 6.1.3 | BSD-3-Clause |
| et_xmlfile | 2.0.0 | MIT License |
| pillow | 13.0.0 | MIT-CMU |
| typing_extensions | 4.16.0 | PSF-2.0 |
| xlsxwriter | 3.3.1 | BSD-2-Clause |

### What the installers download

If this computer doesn't have them, and the owner says yes, `install.sh` and `install.ps1` download these from their makers. They are not stored in this repository, and each has its own license: Python (python-build-standalone, https://github.com/astral-sh/python-build-standalone), Node.js (https://nodejs.org) and Ollama (https://github.com/ollama/ollama). `scripts/container/Dockerfile` builds on `python:3.12-slim` and packages from Debian.

## What a build adds

Toolsmith finds and adds outside projects on the owner's computer, as they are, each under its own license. None is part of this repository.

## Made for this project

Puff, the Desktop's pictures, the five apps, Trivia's questions, FitFam's exercises, the looks and the avatars are by Cypher Sisters, under this repository's license. `templates/` starts a new package marked MIT, for its author to change. The test fixtures in `tests/data/` and `tests/fixtures/` are hand-made or recorded from BuildBot's own runs, and may keep project names and their one-line GitHub descriptions as facts. `tests/data/*/SOURCE.md` says what those keep.

PuffPAI by Cypher Sisters. puff puff pass
