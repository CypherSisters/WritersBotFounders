"""A headless check of the Desktop's JavaScript logic (packages/desktop/web/logic.js), with node.

No browser is needed. Skipped where node is not installed.
"""

import shutil
import subprocess

import pytest

from conftest import REPO

NODE = shutil.which("node")
WEB = REPO / "packages" / "desktop" / "web"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_desktop_logic():
    proc = subprocess.run([NODE, str(REPO / "tests" / "js" / "logic.test.js")], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "ok"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_desktop_js_parses():
    for name in ("logic.js", "desktop.js"):
        proc = subprocess.run([NODE, "--check", str(WEB / name)], capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stderr


def test_desktop_sets_text_never_html():
    """Text from packages and the Core is set as text: no innerHTML, no document.write, no eval."""
    for name in ("logic.js", "desktop.js"):
        src = (WEB / name).read_text(encoding="utf-8")
        assert "innerHTML" not in src and "document.write" not in src and "eval(" not in src
