"""What the Core imports comes with a real install, not only with the test tools (D157)."""

import ast
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

NAME_IN_PYPROJECT = {"webview": "pywebview", "yaml": "pyyaml"}
NOT_LISTED_ON_PURPOSE = {
    "anyio": "comes with mcp",
    "werkzeug": "comes with flask",
    "psutil": "a faster way when it's there, census.py works without it",
    "zstd": "the fallback when zstandard is missing, zim_reader.py",
}


def _core_imports() -> dict[str, str]:
    """``{top-level module: where}`` for each outside module any Core file imports, at any depth."""
    found = {}
    for path in sorted((REPO / "core" / "puffpai_core").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top != "puffpai_core" and top not in sys.stdlib_module_names:
                    found.setdefault(top, f"{path.relative_to(REPO)}:{node.lineno}")
    return found


def test_everything_the_core_imports_is_in_a_real_install():
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    listed = {re.match(r"[\w.-]+", d).group().lower() for d in project["dependencies"]}
    missing = {module: where for module, where in _core_imports().items()
               if module not in NOT_LISTED_ON_PURPOSE and NAME_IN_PYPROJECT.get(module, module) not in listed}
    assert not missing, f"Not in pyproject's dependencies, so a real install has no such module: {missing}"


def test_the_list_of_exceptions_does_not_go_stale():
    assert set(NOT_LISTED_ON_PURPOSE) <= set(_core_imports())
