"""Lane i23a (D95): the owner's one setting for files Drawbridge can't read as text, Normal or Locked down.

In the owner's test6 every app found stopped behind "Drawbridge couldn't check some of its files" for pictures,
model weights, a zip of videos and Sphinx's build cache. Under Normal (the default) a picture, sound, video,
font, pure-data model, clean document, or an archive of those, now passes with no card when it really is that
kind (first bytes and layout, a size cap, no program or zip hidden in it). Files that can run code still ask,
and so does any kind no list names. Locked down asks for every file Drawbridge can't read, as before D95.
Sphinx ``.doctrees`` folders are removed from staging in both modes, and the tree hash is of what is left.

Fixture files are built here from their first bytes. Three unrelated app trees (an ML web app, a recipe app, a
Python app with a docs build). No network, no Docker, no real keys; every folder is the test's own.
"""

from __future__ import annotations

import io
import json
import pickle
import struct
import zipfile
from pathlib import Path

import pytest

from puffpai_core import seams
from puffpai_core.packages import fsutil
from puffpai_core.packages.manager import _verdict, shown_verdict
from puffpai_core.zone3 import files_mode, kinds
from puffpai_core.zone3.chain import ChainLog
from puffpai_core.zone3.drawbridge import ALLOW, BLOCK, HOLD, Drawbridge, tree_sha, verify_tree

NORMAL, LOCKED = kinds.NORMAL, kinds.LOCKED


def _zip(members: dict[str, bytes], method=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 17
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 40 + b"\xff\xd9"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24
AVIF = struct.pack(">I", 28) + b"ftypavif" + b"\x00\x00\x00\x00" + b"avifmif1miaf" + b"\x00" * 40
MP4 = struct.pack(">I", 24) + b"ftypisom" + b"\x00\x00\x02\x00" + b"isomiso2" + b"\x00" * 64
TFLITE = b"\x1c\x00\x00\x00TFL3" + b"\x00" * 64
GGUF = b"GGUF" + struct.pack("<I", 3) + b"\x00" * 32
_HDR = json.dumps({"w": {"dtype": "F32", "shape": [2, 2], "data_offsets": [0, 16]},
                   "__metadata__": {"format": "pt"}}).encode()
SAFETENSORS = struct.pack("<Q", len(_HDR)) + _HDR + b"\x00" * 16
WEIGHTS_BIN = struct.pack("<8f", 0.5, -1.25, 3.0, 0.0, 2.5, -0.75, 1.0, 4.0)       # TF.js-style raw floats
WASM = b"\x00asm\x01\x00\x00\x00" + b"\x00" * 16
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 56
PDF_OK = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
PDF_JS = (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>\n"
          b"endobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")
PDF_HIDDEN_JS = PDF_JS.replace(b"/JavaScript", b"/J#61vaScript").replace(b"/JS ", b"/J#53 ")
CT = (b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      b'<Default Extension="xml" ContentType="application/xml"/></Types>')
DOCX_OK = _zip({"[Content_Types].xml": CT, "word/document.xml": b"<w:document><w:body/></w:document>"})
DOCX_MACRO = _zip({"[Content_Types].xml": CT, "word/document.xml": b"<w:document/>",
                   "word/vbaProject.bin": b"\xd0\xcf\x11\xe0" + b"\x00" * 60})
PICKLE = pickle.dumps({"weights": [1, 2, 3]})
TORCH_BIN = _zip({"archive/data.pkl": PICKLE, "archive/data/0": b"\x00" * 64})


def _nested(depth: int) -> bytes:
    data = _zip({"pic.png": PNG})
    for n in range(depth - 1):
        data = _zip({f"level{n}.zip": data})
    return data


#: name -> bytes, each passes under Normal
PASS = {
    "cover.avif": AVIF, "sticker.webp": WEBP, "logo.png": PNG, "pose.tflite": TFLITE,
    "model.safetensors": SAFETENSORS, "tiny.gguf": GGUF, "pictures.zip": _zip({"a.png": PNG, "b/c.png": PNG}),
    "weights.bin": WEIGHTS_BIN, "clip.mp4": MP4, "menu.pdf": PDF_OK, "notes.docx": DOCX_OK,
}
#: name -> (bytes, the card's sort), each still asks under Normal
ASK = {
    "photo.png": (EXE, "not_kind"),                                    # an .exe renamed .png
    "beach.jpg": (JPEG + _zip({"Main.class": b"\xca\xfe\xba\xbe" + b"\x00" * 8}), "not_kind"),   # a polyglot
    "gradle-wrapper.jar": (_zip({"META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\n"}), "runs"),
    "model.pkl": (PICKLE, "runs"),
    "index.doctree": (PICKLE, "runs"),                                 # outside a .doctrees folder
    "pytorch_model.bin": (TORCH_BIN, "runs"),
    "tools.zip": (_zip({"lib/helper.jar": b"PK\x05\x06" + b"\x00" * 18}), "archive"),
    "deep.zip": (_nested(3), "archive"),
    "bomb.zip": (_zip({"zeros.txt": b"0" * (8 * 1024 * 1024)}), "archive"),
    "budget.docx": (DOCX_MACRO, "runs"),
    "flyer.pdf": (PDF_JS, "runs"),
    "sneaky.pdf": (PDF_HIDDEN_JS, "runs"),
    "broken.safetensors": (struct.pack("<Q", 40) + b"{not json at all" + b" " * 40, "not_kind"),
    "loader.wasm": (WASM, "runs"),
    "assets.data": (b"\x00" * 64, "runs"),
}
#: Before D95 these passed by their first bytes alone (pictures and documents with first bytes Drawbridge knew).
BEFORE_D95_PASSED = {"logo.png", "sticker.webp", "menu.pdf", "beach.jpg", "flyer.pdf", "sneaky.pdf"}
#: ... and these two now ask in both modes: Locked down is never looser than Normal (D95).
TIGHTER_IN_BOTH = {"beach.jpg", "flyer.pdf", "sneaky.pdf"}


@pytest.fixture
def setting(state):
    return {"mode": NORMAL}


@pytest.fixture
def db(state, setting):
    return Drawbridge(ChainLog(state / "logs" / "audit.jsonl"), files_mode=lambda: setting["mode"])


def _man(app):
    return {"schema": "puffpai-package/1", "id": app, "kind": "app", "name": app}


def _row(v):
    return next((r for r in v["rows"] if r.get("code") == "cant_check"), {})


def _one(tmp_path: Path, name: str, data: bytes) -> Path:
    root = tmp_path / ("app-" + name.replace(".", "-"))
    root.mkdir()
    (root / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (root / name).write_bytes(data)
    return root


# -- the kind checks, file by file ------------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(PASS))
def test_normal_passes_files_that_are_really_their_kind(db, setting, tmp_path, name):
    root = _one(tmp_path, name, PASS[name])
    v = db.check_package(root, _man("app"))
    assert v["verdict"] == ALLOW, (name, _row(v))
    assert v["files_mode"] == NORMAL
    assert v["eased"] == (0 if name in BEFORE_D95_PASSED else 1)


@pytest.mark.parametrize("name", sorted(ASK))
def test_normal_still_asks_for_code_disguises_and_caps(db, setting, tmp_path, name):
    data, sort = ASK[name]
    root = _one(tmp_path, name, data)
    v = db.check_package(root, _man("app"))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"], (name, v["rows"])
    row = _row(v)
    assert row["all"] == [name]
    got = kinds.check_file(str(root / name), name, len(data))
    assert not got.ok and got.sort == sort, (name, got)


def test_oversize_asks(db, tmp_path, monkeypatch):
    monkeypatch.setitem(kinds.CAPS, ".avif", 64)
    root = _one(tmp_path, "huge.avif", AVIF)
    v = db.check_package(root, _man("app"))
    assert v["verdict"] == HOLD and kinds.check_file(str(root / "huge.avif"), "huge.avif", len(AVIF)).sort == "too_big"
    assert _row(v)["said"] == ["1 file is too big to check: huge.avif."]


@pytest.mark.parametrize("name", sorted(PASS) + sorted(ASK))
def test_locked_down_gives_the_verdict_from_before_d95(db, setting, tmp_path, name):
    """Locked down: every file Drawbridge can't read asks, as before D95. Only pictures and documents whose
    first bytes it knew passed before, and they still pass, unless they hide a program or scripts (then they ask
    in both modes: Locked down is never looser than Normal)."""
    setting["mode"] = LOCKED
    data = PASS[name] if name in PASS else ASK[name][0]
    v = db.check_package(_one(tmp_path, name, data), _man("app"))
    before = ALLOW if name in BEFORE_D95_PASSED else HOLD
    want = HOLD if name in TIGHTER_IN_BOTH else before
    assert v["verdict"] == want, (name, v["rows"])
    assert v["files_mode"] == LOCKED and v["eased"] == 0
    if name in PASS and name not in BEFORE_D95_PASSED:
        assert _row(v)["said"][-1].endswith(f"would pass under Normal, but your setting, Locked down, asks for "
                                            f"every one: {name}.")


def test_a_bare_drawbridge_and_a_broken_setting_are_locked_down(state, tmp_path):
    root = _one(tmp_path, "cover.avif", AVIF)
    assert Drawbridge().check_package(root, _man("app"))["verdict"] == HOLD          # no setting given
    assert files_mode.read(state) == NORMAL                                           # no file: the default
    (state / "settings").mkdir(parents=True, exist_ok=True)
    (state / "settings" / "drawbridge.json").write_text("{broken", encoding="utf-8")
    assert files_mode.read(state) == LOCKED
    (state / "settings" / "drawbridge.json").write_text('{"files": "open"}', encoding="utf-8")
    assert files_mode.read(state) == LOCKED
    db = Drawbridge(files_mode=lambda: files_mode.read(state))
    assert db.check_package(root, _man("app"))["verdict"] == HOLD
    files_mode.save(state, NORMAL)
    assert db.check_package(root, _man("app"))["verdict"] == ALLOW
    with pytest.raises(ValueError):
        files_mode.save(state, "open")


def test_archives_are_never_unpacked_to_disk(db, tmp_path):
    root = _one(tmp_path, "videos.zip", _zip({"warmup.mp4": MP4, "stretch.mp4": MP4, "list.txt": b"warm up\n"}))
    before = sorted(p.name for p in root.rglob("*"))
    assert db.check_package(root, _man("app"))["verdict"] == ALLOW
    assert sorted(p.name for p in root.rglob("*")) == before


def test_an_archive_inside_an_archive_passes_one_more_level_asks(db, tmp_path):
    assert db.check_package(_one(tmp_path, "two.zip", _nested(2)), _man("a"))["verdict"] == ALLOW
    assert db.check_package(_one(tmp_path, "three.zip", _nested(3)), _man("b"))["verdict"] == HOLD


def test_one_check_unpacks_at_most_its_budget_across_all_archives(db, tmp_path, monkeypatch):
    monkeypatch.setattr(kinds, "CHECK_UNPACKED", 3000)
    root = tmp_path / "many-zips"
    root.mkdir()
    for n in range(3):
        (root / f"part{n}.zip").write_bytes(_zip({"a.txt": b"x" * 1200}, zipfile.ZIP_STORED))
    v = db.check_package(root, _man("many"))
    assert v["verdict"] == HOLD and _row(v)["all"] == ["part2.zip"]       # the first two fit, the third asks


def test_a_locked_zip_member_asks(db, tmp_path):
    data = bytearray(_zip({"a.png": PNG}, zipfile.ZIP_STORED))
    data[6] |= 0x01                                          # the local record's "encrypted" flag
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 8] |= 0x01                                     # and the central record's
    root = _one(tmp_path, "locked.zip", bytes(data))
    assert db.check_package(root, _man("app"))["verdict"] == HOLD
    assert kinds.check_file(str(root / "locked.zip"), "locked.zip", len(data)).sort == "sealed"


def test_a_tflite_with_label_metadata_passes_and_one_hiding_code_asks(db, tmp_path):
    ok = TFLITE + _zip({"labels.txt": b"squat\nlunge\n"})
    bad = TFLITE + _zip({"labels.txt": b"squat\n", "run.jar": b"PK\x05\x06" + b"\x00" * 18})
    assert db.check_package(_one(tmp_path, "pose.tflite", ok), _man("a"))["verdict"] == ALLOW
    assert db.check_package(_one(tmp_path, "pose2.tflite", bad), _man("b"))["verdict"] == HOLD


def test_a_pickle_shaped_bin_asks_and_raw_floats_pass():
    assert kinds.pickle_like(PICKLE) and kinds.pickle_like(pickle.dumps([1], protocol=0))
    assert kinds.pickle_like(b"cos\nsystem\n(S'echo hi'\ntR")                          # protocol 0, no STOP
    assert not kinds.pickle_like(WEIGHTS_BIN)
    assert kinds.pickle_like(b"S'" + b"x" * 5000)                                      # runs off the end: asks


# -- the card's words -------------------------------------------------------------------------------------------

def test_the_card_says_which_files_run_code_by_kind_with_three_names_at_most(db, tmp_path):
    root = tmp_path / "card-app"
    (root / "android" / "gradle" / "wrapper").mkdir(parents=True)
    (root / "android" / "gradle" / "wrapper" / "gradle-wrapper.jar").write_bytes(_zip({"x": b"y"}))
    (root / "ml").mkdir()
    (root / "ml" / "model.pkl").write_bytes(PICKLE)
    (root / "ml" / "extra.pkl").write_bytes(PICKLE)
    (root / "img").mkdir()
    (root / "img" / "photo.png").write_bytes(EXE)
    for name in ("a.bson", "b.ai", "c.ai", "d.ai"):
        (root / "dump" / name).parent.mkdir(exist_ok=True)
        (root / "dump" / name).write_bytes(b"\x00\x01\x02 binary")
    v = db.check_package(root, _man("card-app"))
    row = _row(v)
    assert sorted(row["all"]) == sorted(["android/gradle/wrapper/gradle-wrapper.jar", "ml/model.pkl", "ml/extra.pkl",
                                         "img/photo.png", "dump/a.bson", "dump/b.ai", "dump/c.ai", "dump/d.ai"])
    said = row["said"]
    assert said[0] == ("3 files can run code when opened: 1 Java program (.jar), 2 Python pickles (.pkl). "
                       "For example: gradle-wrapper.jar, extra.pkl, model.pkl."), said
    assert "1 file is not what its name says: photo.png." in said
    assert "4 files of a kind Drawbridge can't read: a.bson, b.ai, c.ai and 1 more." in said
    for line in said:
        assert "/" not in line and "\\" not in line
        names = line.rsplit(": ", 1)[-1]
        assert len(names.split(", ")) <= 3
    shown = shown_verdict(v)
    assert shown["held_said"] == said and shown["held"]                 # the internal list is kept as it was
    from puffpai_core.consent.shown import view
    lines = view({"kind": "package", "puffpai_yes": True, "drawbridge": shown})["lines"]
    joined = "\n".join(lines)
    assert "can run code when opened" in joined and "android/gradle" not in joined


def test_a_card_name_with_direction_characters_is_made_plain():
    assert kinds.shown_name("dir/report\u202egnp.exe") == "report?gnp.exe"


# -- .doctrees: removed from staging, the hash is of what is left ---------------------------------------------

def test_doctrees_folders_are_removed_from_staging_and_the_hash_still_binds(db, tmp_path):
    root = tmp_path / "docs-app"
    for rel in ("docsrc/gettext/.doctrees/index.doctree", "docsrc/gettext/.doctrees/environment.pickle",
                "docs/.doctrees/api.doctree"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(PICKLE)
    (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes" / "loose.doctree").write_bytes(PICKLE)               # outside a .doctrees folder
    gone = fsutil.strip_regenerated(root, only_unused=True)
    assert gone == ["docs/.doctrees", "docsrc/gettext/.doctrees"]
    v = db.check_package(root, _man("docs-app"))
    assert v["verdict"] == HOLD and _row(v)["all"] == ["notes/loose.doctree"]
    (root / "notes" / "loose.doctree").unlink()
    v = db.check_package(root, _man("docs-app"))
    assert v["verdict"] == ALLOW and v["tree_sha"] == tree_sha(root) and db.verify(root, v)
    (root / "docs" / ".doctrees").mkdir()                                # put back after the check: not bound
    (root / "docs" / ".doctrees" / "x.doctree").write_bytes(PICKLE)
    assert not db.verify(root, v)
    assert fsutil.is_skip(".Doctrees") and ".doctrees" in fsutil.UNUSED_NAMES


def test_the_package_manager_strips_doctrees_in_both_modes(state, tmp_path):
    from puffpai_core.api import create_app
    app = create_app(state, port=18419)
    core = app.extensions["puffpai"]
    src = tmp_path / "src" / "docs-tool"
    (src / "build" / ".doctrees").mkdir(parents=True)
    (src / "build" / ".doctrees" / "environment.pickle").write_bytes(PICKLE)
    (src / "server.py").write_text("print('ok')\n", encoding="utf-8")
    (src / "puffpai.json").write_text(json.dumps({"schema": "puffpai-package/1", "id": "docs-tool", "kind": "tool",
                                                  "name": "Docs Tool", "version": "1.0.0",
                                                  "description": "Keeps docs.", "run": {"command": "python server.py"}}),
                                      encoding="utf-8")
    staged = tmp_path / "staged"
    fsutil.copy_package(src, staged)
    assert not (staged / "build" / ".doctrees").exists()                 # a copy leaves it out
    core.packages._strip(src)                                            # noqa: SLF001 - a clone has it removed
    assert not (src / "build" / ".doctrees").exists() and (src / "server.py").is_file()


# -- the mode is bound to the verdict -------------------------------------------------------------------------

def test_a_switch_to_locked_down_blocks_a_stale_normal_verdict(db, setting, tmp_path, state):
    root = _one(tmp_path, "cover.avif", AVIF)
    v = db.check_package(root, _man("recipes"))
    assert v["verdict"] == ALLOW and v["eased"] == 1 and db.verify(root, v)
    assert seams.drawbridge_bound(root, v, db)
    assert not verify_tree(root, v)                                     # a caller that can't say the setting
    setting["mode"] = LOCKED
    assert not db.verify(root, v) and not seams.drawbridge_bound(root, v, db)
    got, problems = _verdict(root, _man("recipes"), "local", db, before=v)
    assert got is None and "setting" in problems[0]
    setting["mode"] = NORMAL
    assert db.verify(root, v)
    # A verdict that eased nothing does not depend on the setting.
    plain = _one(tmp_path, "logo.png", PNG)
    v2 = db.check_package(plain, _man("plain"))
    setting["mode"] = LOCKED
    assert v2["eased"] == 0 and db.verify(plain, v2) and verify_tree(plain, v2)


def test_a_hold_card_from_normal_does_not_cover_more_files_after_the_switch(db, setting, tmp_path):
    root = _one(tmp_path, "cover.avif", AVIF)
    (root / "gradle-wrapper.jar").write_bytes(_zip({"x": b"y"}))
    v = db.check_package(root, _man("app"))
    assert v["verdict"] == HOLD and _row(v)["all"] == ["gradle-wrapper.jar"] and v["eased"] == 1
    assert seams.drawbridge_bound(root, v, db)
    setting["mode"] = LOCKED
    assert not seams.drawbridge_bound(root, v, db)
    got, problems = _verdict(root, _man("app"), "local", db, before=v)
    assert got is None and problems


# -- the owner-only route -------------------------------------------------------------------------------------

def test_only_the_owner_at_a_paired_device_changes_it_and_each_change_is_audited(state):
    from puffpai_core.api import create_app
    app = create_app(state, port=18419)
    core = app.extensions["puffpai"]
    client = app.test_client()
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}
    owner_key = {"Authorization": "Bearer " + core.tokens.issue_owner_key()}
    terminal = {"Authorization": "Bearer " + core.tokens.issue_device("this computer's terminal")}
    package = {"Authorization": "Bearer " + core.tokens.issue_package_token("recipes")}
    toolsmith = {"Authorization": "Bearer " + core.tokens.issue_toolsmith("b_test")}
    got = client.get("/v1/settings/drawbridge", headers=dev).json
    assert got["files"] == NORMAL and got["title"] == "Files Drawbridge can't read"
    assert [(c["id"], c["name"], c["on"]) for c in got["choices"]] == [(NORMAL, "Normal", True),
                                                                        (LOCKED, "Locked down", False)]
    assert got["choices"][1]["line"] == "Every file Drawbridge can't read asks you first."
    assert "Files that can run code still ask you." in got["choices"][0]["line"]
    for who in (owner_key, terminal, package, toolsmith, {}):
        r = client.put("/v1/settings/drawbridge", json={"files": LOCKED}, headers=who)
        assert r.status_code in (401, 403), (who, r.status_code)
    assert files_mode.read(state) == NORMAL
    assert client.put("/v1/settings/drawbridge", json={"files": "open"}, headers=dev).status_code == 400
    assert client.put("/v1/settings/drawbridge", json={"files": LOCKED, "x": 1}, headers=dev).status_code == 400
    r = client.put("/v1/settings/drawbridge", json={"files": LOCKED}, headers=dev)
    assert r.status_code == 200 and r.json["files"] == LOCKED and files_mode.read(state) == LOCKED
    assert client.put("/v1/settings/drawbridge", json={"files": LOCKED}, headers=dev).status_code == 200   # no change
    assert client.put("/v1/settings/drawbridge", json={"files": NORMAL}, headers=dev).json["files"] == NORMAL
    lines = [json.loads(ln) for ln in (state / "logs" / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    mine = [ln for ln in lines if ln.get("action") == "drawbridge_files"]
    assert [(ln["actor"], ln["from"], ln["to"]) for ln in mine] == [("owner", NORMAL, LOCKED), ("owner", LOCKED, NORMAL)]
    # The Core's own Drawbridge reads it at every check.
    from puffpai_core.zone3.service import for_app
    assert for_app(app).drawbridge.mode() == NORMAL
    files_mode.save(state, LOCKED)
    assert for_app(app).drawbridge.mode() == LOCKED


def test_no_audit_line_means_no_change(state, monkeypatch):
    from puffpai_core.api import create_app
    from puffpai_core.zone3.service import for_app
    app = create_app(state, port=18419)
    core = app.extensions["puffpai"]
    dev = {"Authorization": "Bearer " + core.tokens.issue_device("Test laptop")}

    def boom(_record):
        raise OSError("disk full")
    monkeypatch.setattr(for_app(app).audit, "append", boom)
    r = app.test_client().put("/v1/settings/drawbridge", json={"files": LOCKED}, headers=dev)
    assert r.status_code == 503 and files_mode.read(state) == NORMAL


# -- three unrelated apps, under each mode --------------------------------------------------------------------

def _ml_web_app(root: Path) -> None:
    """An ML web app: a pose model, its WebAssembly runtime, the runtime's packed .data, pictures."""
    for rel, data in {"public/models/pose.tflite": TFLITE, "public/wasm/vision.wasm": WASM,
                      "public/wasm/vision.data": b"\x00" * 32, "public/img/hero.webp": WEBP,
                      "public/img/logo.png": PNG, "src/app.js": b"console.log('ok')\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)


def _recipe_app(root: Path) -> None:
    """A recipe app: a cover picture and a zip of cooking videos."""
    for rel, data in {"static/cover.avif": AVIF, "static/videos.zip": _zip({"knife.mp4": MP4, "dough.mp4": MP4}),
                      "app.py": b"print('ok')\n"}.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)


def _python_docs_app(root: Path) -> None:
    """A Python app with a Sphinx docs build and an Android companion's gradle wrapper."""
    for rel, data in {"docs/_build/.doctrees/index.doctree": PICKLE, "docs/_build/html/index.html": b"<p>hi</p>\n",
                      "android/gradle/wrapper/gradle-wrapper.jar": _zip({"x": b"y"}), "server.py": b"print('ok')\n"
                      }.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)


APP_TREES = {
    "ml-web": (_ml_web_app, {NORMAL: ["public/wasm/vision.data", "public/wasm/vision.wasm"],
                             LOCKED: ["public/models/pose.tflite", "public/wasm/vision.data",
                                      "public/wasm/vision.wasm"]}),
    "recipes": (_recipe_app, {NORMAL: [], LOCKED: ["static/cover.avif", "static/videos.zip"]}),
    "py-docs": (_python_docs_app, {NORMAL: ["android/gradle/wrapper/gradle-wrapper.jar"],
                                   LOCKED: ["android/gradle/wrapper/gradle-wrapper.jar"]}),
}


@pytest.mark.parametrize("app", sorted(APP_TREES))
@pytest.mark.parametrize("mode", [NORMAL, LOCKED])
def test_three_unrelated_apps_under_each_mode(db, setting, tmp_path, app, mode):
    build, asks = APP_TREES[app]
    root = tmp_path / app
    build(root)
    fsutil.strip_regenerated(root, only_unused=True)                    # as the try's download does
    setting["mode"] = mode
    v = db.check_package(root, _man(app))
    held = sorted(_row(v).get("all") or [])
    assert held == asks[mode], (app, mode, _row(v).get("said"))
    assert v["verdict"] == (HOLD if held else ALLOW)


# -- the Developer page ---------------------------------------------------------------------------------------

def test_the_developer_page_logic_for_the_setting():
    import shutil
    import subprocess
    from conftest import REPO
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    web = REPO / "packages" / "desktop" / "web"
    for f in (web / "devzones.js", web / "pages" / "developer.js", web / "logic.js"):
        got = subprocess.run([node, "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert got.returncode == 0, got.stderr
    got = subprocess.run([node, str(REPO / "tests" / "js" / "i23a.test.js")], capture_output=True, text=True, timeout=60)
    assert got.returncode == 0 and got.stdout.strip() == "ok", got.stderr
    src = (web / "pages" / "developer.js").read_text(encoding="utf-8")
    assert '"/v1/settings/drawbridge"' in src and 'class: "opt"' in src and "innerHTML" not in src


# -- the verifier's repros (fix round): each must ask, in both modes ------------------------------------------

#: A protocol 2 pickle that would call os.system("echo pwned") if loaded, written op by op (PROTO, GLOBAL, a
#: string, TUPLE1, REDUCE, STOP). It is never loaded here: only its bytes are checked.
EVIL_PICKLE = b"\x80\x02cos\nsystem\nX\x0a\x00\x00\x00echo pwned\x85R."


def _tar(members: dict[str, bytes], mode: str = "w") -> bytes:
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode, format=tarfile.PAX_FORMAT) as tf:
        for name, data in members.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _repros() -> dict[str, bytes]:
    import gzip
    import warnings
    import zlib
    inner = b"3 0 << /S /JavaScript /JS (app.alert(1)) >>"
    comp = zlib.compress(inner)
    zl = str(len(comp)).encode()
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    main_zip = _zip({"__main__.py": b"print('x')\n"})
    dup = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(dup, "w") as z:
            z.writestr("a.txt", "hi")
            z.writestr("a.txt", "there")
    hdr = b"{'descr': '|O', 'fortran_order': False, 'shape': (1,), }"
    hdr = hdr + b" " * (64 - 10 - len(hdr) - 1) + bytes([10])
    ct = b'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>'
    return {
        "A_legacy_torch_tar.bin": _tar({"sys_info": pickle.dumps({"little_endian": True}, protocol=2),
                                        "pickle": EVIL_PICKLE, "storages": b"\x80\x02K\x00.",
                                        "tensors": b"\x80\x02K\x00."}),
        "B_jar_as.docx": _zip({"[Content_Types].xml": ct,
                               "META-INF/MANIFEST.MF": b"Manifest-Version: 1.0\nMain-Class: Evil\n",
                               "Evil.class": b"\xca\xfe\xba\xbe\x00\x00\x00\x34" + b"\x00" * 40,
                               "__main__.py": b"import os; os.system('calc')\n", "payload.exe": b"MZ" + b"\x00" * 100}),
        "B2_pyzip_as.odt": _zip({"mimetype": b"application/vnd.oasis.opendocument.text",
                                 "__main__.py": b"import os; os.system('calc')\n",
                                 "Evil.class": b"\xca\xfe\xba\xbe" + b"\x00" * 40}),
        "B3_report.docx": _zip({"[Content_Types].xml": b"<Types/>", "__main__.py": b"print('CODE RAN')\n"}),
        "D_pdf_nul.pdf": (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /OpenAction 2 0 R >>\nendobj\n2 0 obj\n"
                          b"<< /S /JavaScript\x00/JS\x00(app.alert(1)) >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"),
        "E_pdf_objstm.pdf": (b"%PDF-1.5\n1 0 obj\n<< /Type /Catalog /OpenAction 3 0 R >>\nendobj\n4 0 obj\n"
                             b"<< /Type /ObjStm /N 1 /First 4 /Filter /FlateDecode /Note (obj) /Length " + zl
                             + b" >>\nstream\n" + comp + b"\nendstream\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"),
        "E2_pdf_objstm_pad.pdf": (b"%PDF-1.5\n4 0 obj\n<< /Type /ObjStm /N 1 /First 4 /Filter /FlateDecode /Pad ("
                                  + b"A" * 5000 + b") /Length " + zl + b" >>\nstream\n" + comp
                                  + b"\nendstream\nendobj\n%%EOF\n"),
        "F_pdf_attach.pdf": (b"%PDF-1.4\n1 0 obj\n<< /Type /Annot /Subtype /FileAttachment /FS << /Type /Filespec "
                             b"/F (x.exe) /EF << /F 2 0 R >> >> >>\nendobj\n2 0 obj\n<< /Length 102 >>\nstream\nMZ"
                             + b"\x00" * 100 + b"\nendstream\nendobj\n%%EOF\n"),
        "H_png_zip_pad.png": png + main_zip + b"\x00" * 70000,
        "H2_png_zip.png": png + main_zip,
        "I_dup.zip": dup.getvalue(),
        "J_pkl.tar.gz": _tar({"m.pkl": EVIL_PICKLE}, "w:gz"),
        "K_npy.bin": bytes([0x93]) + b"NUMPY" + bytes([1, 0]) + struct.pack("<H", len(hdr)) + hdr + EVIL_PICKLE,
        "L_huge.safetensors": struct.pack("<Q", 1 << 62) + b"{}",
        "M_p0.bin": b"(" + b"\x00" * 4,
        "N_meta.tflite": b"\x00\x00\x00\x00TFL3" + b"\x00" * 100 + main_zip,
        "P_docx_media.docx": _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>",
                                   "word/media/run.py": b"import os\n",
                                   "word/media/x.hta": b"<script>alert(1)</script>"}),
        "Q_gzip.bin": gzip.compress(EVIL_PICKLE),
        "R_pickle_later.bin": WEIGHTS_BIN * 8 + EVIL_PICKLE,
    }


REPROS = _repros()


@pytest.mark.parametrize("mode", [NORMAL, LOCKED])
@pytest.mark.parametrize("name", sorted(REPROS))
def test_every_repro_asks_in_both_modes(db, setting, tmp_path, name, mode):
    setting["mode"] = mode
    root = _one(tmp_path, name, REPROS[name])
    v = db.check_package(root, _man("app"))
    assert v["verdict"] == HOLD and v["eased"] == 0, (name, mode, v["rows"])
    assert not kinds.check_file(str(root / name), name.lower(), len(REPROS[name])).ok


def test_the_repro_verdicts_say_why(tmp_path):
    want = {"A_legacy_torch_tar.bin": "runs", "B_jar_as.docx": "runs", "B2_pyzip_as.odt": "runs",
            "B3_report.docx": "runs", "D_pdf_nul.pdf": "runs", "E_pdf_objstm.pdf": "runs",
            "E2_pdf_objstm_pad.pdf": "runs", "F_pdf_attach.pdf": "runs", "H_png_zip_pad.png": "not_kind",
            "H2_png_zip.png": "not_kind", "I_dup.zip": "archive", "K_npy.bin": "runs", "M_p0.bin": "runs",
            "N_meta.tflite": "archive", "P_docx_media.docx": "runs", "Q_gzip.bin": "runs",
            "R_pickle_later.bin": "runs"}
    for name, sort in want.items():
        path = tmp_path / name
        path.write_bytes(REPROS[name])
        assert kinds.check_file(str(path), name.lower(), path.stat().st_size).sort == sort, name


# -- what still passes after the fix round -----------------------------------------------------------------------

def _pdf_with_objstm_and_xref() -> bytes:
    """A clean PDF 1.5: its objects in a Flate object stream, its cross references in a stream with the PNG Up
    predictor, a Flate content stream and a picture with a filter the check does not decode (DCT)."""
    import zlib
    objs = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >> << /Type /Page /Parent 2 0 R /Contents 5 0 R >>"
    objstm = zlib.compress(b"2 0 3 44 " + objs)
    rows = [bytes([1, 0, 0, 15]), bytes([1, 0, 0, 90]), bytes([2, 0, 4, 0])]
    predicted, prev = b"", bytes(4)
    for r in rows:
        predicted += b"\x02" + bytes((a - b) & 0xFF for a, b in zip(r, prev))
        prev = r
    xref = zlib.compress(predicted)
    content = zlib.compress(b"BT /F1 12 Tf 72 712 Td (Hello) Tj ET")
    return (b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"4 0 obj\n<< /Type /ObjStm /N 2 /First 9 /Filter /FlateDecode /Length " + str(len(objstm)).encode()
            + b" >>\nstream\n" + objstm + b"\nendstream\nendobj\n"
            b"5 0 obj\n<< /Filter /FlateDecode /Length " + str(len(content)).encode() + b" >>\nstream\n" + content
            + b"\nendstream\nendobj\n"
            b"6 0 obj\n<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /Filter /DCTDecode /Length "
            + str(len(JPEG)).encode() + b" >>\nstream\n" + JPEG + b"\nendstream\nendobj\n"
            b"7 0 obj\n<< /Type /XRef /Size 8 /W [1 2 1] /Root 1 0 R /Filter /FlateDecode "
            b"/DecodeParms << /Predictor 12 /Columns 4 >> /Length " + str(len(xref)).encode() + b" >>\nstream\n"
            + xref + b"\nendstream\nendobj\nstartxref\n0\n%%EOF\n")


EMF = b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 40
STILL_PASS = {
    "report.pdf": _pdf_with_objstm_and_xref(),
    "letter.docx": _zip({"[Content_Types].xml": CT, "_rels/.rels": b"<Relationships/>",
                         "word/document.xml": b"<w:document/>", "word/media/image1.png": PNG,
                         "word/media/image2.emf": EMF, "docProps/thumbnail.jpeg": JPEG}),
    "sheet.xlsx": _zip({"[Content_Types].xml": CT, "xl/workbook.xml": b"<workbook/>",
                        "xl/printerSettings/printerSettings1.bin": b"\x00" * 200}),
    "notes.odt": _zip({"mimetype": b"application/vnd.oasis.opendocument.text", "content.xml": b"<office:document/>",
                       "META-INF/manifest.xml": b"<manifest/>", "Thumbnails/thumbnail.png": PNG}),
    "raw.bin": WEIGHTS_BIN,
}


@pytest.mark.parametrize("name", sorted(STILL_PASS))
def test_clean_documents_and_raw_weights_still_pass_under_normal(db, tmp_path, name):
    v = db.check_package(_one(tmp_path, name, STILL_PASS[name]), _man("app"))
    assert v["verdict"] == ALLOW and v["eased"] == (0 if name.endswith(".pdf") else 1), (name, v["rows"])


def test_a_pdf_with_a_filter_it_cant_decode_or_a_layout_it_cant_follow_asks(tmp_path):
    lzw = b"%PDF-1.4\n1 0 obj\n<< /Filter /LZWDecode /Length 4 >>\nstream\nabcd\nendstream\nendobj\n%%EOF\n"
    odd = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R\nendobj\n%%EOF\n"            # a dict never closed
    unended = b"%PDF-1.4\n1 0 obj\n<< /Length 4 >>\nstream\nabcd\n%%EOF\n"
    for n, data in (("lzw.pdf", lzw), ("odd.pdf", odd), ("unended.pdf", unended)):
        (tmp_path / n).write_bytes(data)
        assert not kinds.check_file(str(tmp_path / n), n, len(data)).ok, n


# -- never looser than before D95: a "picture" that is a script still gets the text checks ---------------------

@pytest.mark.parametrize("mode", [NORMAL, LOCKED])
@pytest.mark.parametrize("ext", sorted(kinds.PASS_KINDS))
def test_a_tunnel_in_plain_text_under_any_listed_kind_is_found_in_both_modes(db, setting, tmp_path, ext, mode):
    """Under every kind D95 lists, in both modes, it is read as the text it is and the tunnel is found, so it never
    passes under Normal as that kind. Her card names it, and her Yes binds its bytes (D117)."""
    setting["mode"] = mode
    name = "t_tunnel" + (".tar.gz" if ext == ".tgz" else ext)
    v = db.check_package(_one(tmp_path, name, b"ngrok http 8080\n"), _man("app"))
    assert v["verdict"] == HOLD and v["reasons"] == ["cant_check"] and v["tunnels"] == 1, (name, mode, v["reasons"])
    assert next(r for r in v["rows"] if r.get("code") == "cant_check")["tunnel"] == [name]


def test_direction_characters_never_reach_the_card_by_name_or_kind(db, tmp_path):
    root = tmp_path / "bidi"
    root.mkdir()
    (root / "x.\u202egnp").write_bytes(EXE)
    (root / "private" / "tax_2025").mkdir(parents=True)
    (root / "private" / "tax_2025" / "runme").write_bytes(EXE)
    v = db.check_package(root, _man("bidi"))
    shown = shown_verdict(v)
    text = " ".join(shown["held_said"] + shown["held"])
    assert "\u202e" not in text and "private/" not in text and "tax_2025/" not in text, text
    assert ".?gnp" in text
    assert sorted(_row(v)["all"]) == ["private/tax_2025/runme", "x.\u202egnp"]       # kept whole, inside


def test_pdf_names_in_picture_bytes_do_not_ask_but_names_in_strings_do(tmp_path):
    pic = JPEG[:-2] + b" /JS /AA " + JPEG[-2:]                      # by chance, inside a photo's own bytes
    ok = (b"%PDF-1.4\n1 0 obj\n<< /Type /XObject /Subtype /Image /Width 1 /Height 1 /Filter /DCTDecode /Length "
          + str(len(pic)).encode() + b" >>\nstream\n" + pic + b"\nendstream\nendobj\n%%EOF\n")
    in_string = b"%PDF-1.4\n1 0 obj\n<< /Title (see /JavaScript here) >>\nendobj\n%%EOF\n"
    (tmp_path / "photo.pdf").write_bytes(ok)
    (tmp_path / "title.pdf").write_bytes(in_string)
    assert kinds.check_file(str(tmp_path / "photo.pdf"), "photo.pdf", len(ok)).ok
    assert kinds.check_file(str(tmp_path / "title.pdf"), "title.pdf", len(in_string)).sort == "runs"
