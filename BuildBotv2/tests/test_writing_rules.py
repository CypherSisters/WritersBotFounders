"""AGENTS.md writing rules, checked over everything this repo's code lanes write."""

from conftest import REPO

ROOTS = ["contracts", "core", "tests", "packages", "README.md", "pyproject.toml"]
SUFFIXES = {".py", ".json", ".yaml", ".md", ".toml", ".txt", ".svg", ".html", ".css", ".js"}


def _files():
    for root in ROOTS:
        path = REPO / root
        if path.is_file():
            yield path
        else:
            yield from (p for p in path.rglob("*") if p.is_file() and p.suffix in SUFFIXES
                        and "__pycache__" not in p.parts and ".egg-info" not in str(p))


def test_no_em_dashes():
    bad = []
    for path in _files():
        text = path.read_text(encoding="utf-8")
        if "\u2014" in text:
            bad.append(f"{path}: em dash")
    assert bad == []
