"""Lane I13 item 2: a built-in's store icon is a small picture inside its own folder, typed by its bytes.

``_icon_data`` (``api/groups/packages.py``) puts the icon into ``GET /v1/packages/available`` as a data: URL.
It never follows a link or junction out of the package folder, takes the type from the bytes (the name
must agree), refuses an SVG that could run or load anything, and reads at most the cap plus one byte.
"""

import base64
import os
import sys
from pathlib import Path

import pytest

from puffpai_core.api.groups.packages import _ICON_MAX, _icon_data

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 " + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 4 4"><rect width="4" height="4"/></svg>'


@pytest.fixture
def junction(tmp_path):
    """``junction(at, real)`` makes a Windows junction; each is removed as a link (os.rmdir) at the end."""
    if sys.platform != "win32":
        pytest.skip("junctions are a Windows thing")
    import _winapi
    made = []

    def make(at: Path, real: Path) -> Path:
        at.parent.mkdir(parents=True, exist_ok=True)
        _winapi.CreateJunction(str(real), str(at))
        made.append(at)
        return at
    yield make
    for at in reversed(made):
        if os.path.lexists(at):
            os.rmdir(at)                    # the junction itself, never what it points at


def _pkg(tmp_path):
    folder = tmp_path / "pkg"
    folder.mkdir()
    return folder


def test_each_picture_type_by_its_bytes(tmp_path):
    folder = _pkg(tmp_path)
    for name, data, kind in (("a.png", PNG, "image/png"), ("a.webp", WEBP, "image/webp"),
                             ("a.jpg", JPEG, "image/jpeg"), ("a.jpeg", JPEG, "image/jpeg"),
                             ("a.svg", SVG, "image/svg+xml")):
        (folder / name).write_bytes(data)
        assert _icon_data(folder, name) == f"data:{kind};base64,{base64.b64encode(data).decode()}", name
    (folder / "icons").mkdir()
    (folder / "icons" / "pot.svg").write_bytes(SVG)
    assert _icon_data(folder, "  icons/pot.svg  ").startswith("data:image/svg+xml;base64,")   # trimmed first


def test_a_junction_out_of_the_folder_is_refused(tmp_path, junction):
    folder = _pkg(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(PNG)
    (outside / "secret.svg").write_bytes(SVG)
    junction(folder / "icons", outside)
    assert (folder / "icons" / "secret.png").is_file()            # the junction does lead out
    assert _icon_data(folder, "icons/secret.png") is None
    assert _icon_data(folder, "icons/secret.svg") is None
    # A junction that points back inside the folder is refused too: no link anywhere on the path.
    (folder / "real").mkdir()
    (folder / "real" / "a.png").write_bytes(PNG)
    junction(folder / "inside", folder / "real")
    assert _icon_data(folder, "inside/a.png") is None
    assert _icon_data(folder, "real/a.png").startswith("data:image/png;base64,")
    # The package folder itself as a junction: refused.
    junction(tmp_path / "linked-pkg", folder)
    assert _icon_data(tmp_path / "linked-pkg", "real/a.png") is None


def test_a_name_that_disagrees_with_the_bytes_is_refused(tmp_path):
    folder = _pkg(tmp_path)
    (folder / "fake.png").write_bytes(SVG)                         # an SVG named .png
    (folder / "fake.svg").write_bytes(PNG)                         # a PNG named .svg
    (folder / "page.svg").write_bytes(b"<html><body>hi</body></html>")
    (folder / "text.jpg").write_bytes(b"just words")
    (folder / "webp.png").write_bytes(WEBP)
    for name in ("fake.png", "fake.svg", "page.svg", "text.jpg", "webp.png"):
        assert _icon_data(folder, name) is None, name


@pytest.mark.parametrize("svg", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="x()" width="1" height="1"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><p>x</p></foreignObject></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
    b'<image xlink:href="https://evil.example/x.png"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><use href="file:///C:/Windows/win.ini#x"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(https://evil.example/p)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(https://evil.example/s.css);</style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a><set attributeName="href" to="javascript:x()"/></a></svg>',
    b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
    b'<?xml version="1.0"?><?xml-stylesheet href="https://evil.example/s.css"?><svg/>',
    b'<html xmlns="http://www.w3.org/1999/xhtml"><svg/></html>',
    # Verifier round 1: CSS escapes and loaders that slip past url( and @import.
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:\\75rl(https://evil.example/p)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:\\0075rl(https://evil.example/p)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:\\75 rl(https://evil.example/p)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:u\\rl(https://evil.example/p)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style>@\\69mport "https://evil.example/s.css";</style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style>rect{fill:\\75rl(https://evil.example/p)}</style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style><![CDATA[@\\69mport "x.css";]]></style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style>rect{fill:&#x5c;75rl(https://evil.example/p)}</style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="background:image-set(\'https://evil.example/p.png\' 1x)"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style>rect{fill:-webkit-image-set("x.png" 1x)}</style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="\\75rl(https://evil.example/p)"/></svg>',
    b'<?xml-stylesheet href="https://evil.example/s.css"?><svg xmlns="http://www.w3.org/2000/svg"/>',
    b'<?xml-stylesheet type="text/css" href="s.css"?>\n<svg/>',
    b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><?php echo 1; ?></svg>',
    b'<?xml version="1.0" foo="bar"?><svg/>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect',
])
def test_a_hostile_svg_is_refused(tmp_path, svg):
    folder = _pkg(tmp_path)
    (folder / "a.svg").write_bytes(svg)
    assert _icon_data(folder, "a.svg") is None


def test_a_plain_svg_with_inside_links_is_kept(tmp_path):
    folder = _pkg(tmp_path)
    (folder / "a.svg").write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink"><defs><linearGradient id="g"/></defs>'
        b'<rect fill="url(#g)" width="1" height="1"/><use xlink:href="#g"/><!-- a note --></svg>')
    assert _icon_data(folder, "a.svg").startswith("data:image/svg+xml;base64,")


@pytest.mark.parametrize("decl", [
    b'<?xml version="1.0"?>', b"<?xml version='1.0' encoding='utf-8'?>",
    b'<?xml version="1.0" encoding="UTF-8" standalone="no"?>', b"\xef\xbb\xbf<?xml version=\"1.0\"?>\n",
])
def test_only_a_plain_xml_declaration_is_kept(tmp_path, decl):
    folder = _pkg(tmp_path)
    (folder / "a.svg").write_bytes(decl + SVG)
    assert _icon_data(folder, "a.svg").startswith("data:image/svg+xml;base64,")


def test_the_cap_reads_at_most_one_byte_more(tmp_path, monkeypatch):
    folder = _pkg(tmp_path)
    (folder / "exact.png").write_bytes(PNG + b"\x00" * (_ICON_MAX - len(PNG)))
    (folder / "over.png").write_bytes(PNG + b"\x00" * (_ICON_MAX - len(PNG) + 1))
    assert _icon_data(folder, "exact.png").startswith("data:image/png;base64,")
    assert _icon_data(folder, "over.png") is None
    asked = []
    real_open = open

    class Spy:
        def __init__(self, fh):
            self.fh = fh

        def read(self, n=-1):
            asked.append(n)
            return self.fh.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.fh.close()

    import builtins
    monkeypatch.setattr(builtins, "open", lambda p, mode="r", *a, **k: Spy(real_open(p, mode, *a, **k))
                        if str(p).endswith("over.png") else real_open(p, mode, *a, **k))
    assert _icon_data(folder, "over.png") is None
    assert asked == [_ICON_MAX + 1]                                 # never the whole file


def test_the_shipped_icons_still_show():
    from puffpai_core.desktop import builtin_dir
    base = builtin_dir()
    assert _icon_data(base / "freshtable", "icons/pot.svg").startswith("data:image/svg+xml;base64,")
    assert _icon_data(base / "example-page", "icons/notes.svg").startswith("data:image/svg+xml;base64,")
