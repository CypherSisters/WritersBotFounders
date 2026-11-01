r"""Drawbridge's kind checks for files it can't read as text (D95).

The owner's words (10-30): "Yes drawbridge should let those through is they pass the security check. And not
all files just run code right?" One owner setting (``files_mode``): **Normal** (the default) or **Locked down**.

Under Normal, a file Drawbridge can't read as text passes with no card only when BOTH hold:

1. its kind can't run code: a picture, a sound, a video, a font, model weights that are pure data, a document
   with no scripts or macros, or an archive of those (:data:`PASS_KINDS`);
2. it really is that kind (:func:`check`): its first bytes are that kind's (and its layout, where that is
   cheap), it is under that kind's size cap (:data:`CAPS`), and it is not a program in disguise: no Windows,
   Linux or Mac program and no ``#!`` script at the start, and, for a kind that is not a zip itself, no zip
   record that looks real anywhere in it (a picture with a ``.jar`` stuck on the end).

A ``.bin`` passes only as raw data: no zip, tar, gzip or other packed data, no NumPy or HDF5 file, nothing that
could be a pickle. A PDF is read token by token and every stream that is not a picture is decoded; a script, an
attached file or an action anywhere asks, and so does a layout or filter it does not follow. Every part of an
Office or OpenDocument file must be XML with no macros, a picture or font that passes, or a Windows metafile.
If unsure, it asks.

Anything else keeps asking: kinds that can run code (``.jar``, Python pickles, ``.exe``, ``.wasm``, packed
``.data`` bundles, macro documents, every kind Windows runs or opens by itself) and kinds no list names.
Under Locked down every file Drawbridge can't read asks, as before D95; the pictures, fonts and sounds it
always passed by their first bytes get the same disguise check and caps in both modes, so Locked down is
never looser than Normal. A listed kind that fails its check but is plain text still gets every text check.

Archives are read in memory, member by member, and never unpacked to disk. Each member gets the same rules,
with caps on depth (:data:`MAX_DEPTH`), members (:data:`MAX_MEMBERS`), unpacked bytes (:data:`MAX_UNPACKED`)
and the packing ratio (:data:`MAX_RATIO`). A locked (encrypted) member, an archive over a cap, or any member
that would ask makes the whole archive ask. Small plain text members (``.txt``, ``.md``, ``.csv``, ``.json``
and the like, :data:`DATA_TEXT`) pass inside an archive; code does not.

Every rule here is a fixed rule, no model (X25). A check that can't finish asks, never passes (X26).
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import os
import pickletools
import re
import struct
import tarfile
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from typing import Any, Iterable

from . import text_rules

NORMAL, LOCKED = "normal", "locked"
MODES = (NORMAL, LOCKED)

KB, MB, GB = 1 << 10, 1 << 20, 1 << 30
HEAD = 1 * MB                 # the first bytes every check may look at
TAIL = 66 * KB                # a zip's end record is in its last 65,557 bytes
FULL_CAP = 64 * MB            # documents, archives inside archives and model metadata are read whole, in memory
TEXT_CAP = 4 * MB             # a plain text member of an archive
XML_CAP = 32 * MB             # one XML part of an Office or OpenDocument file
MAX_DEPTH = 2                 # an archive inside an archive is fine; one more level asks
MAX_MEMBERS = 10_000          # members across every level of one file
MAX_UNPACKED = 2 * GB         # unpacked bytes across every level of one file
CHECK_MEMBERS = 50_000        # members across every file of one Drawbridge check
CHECK_UNPACKED = 4 * GB       # unpacked bytes across every file of one Drawbridge check
MAX_RATIO = 100               # unpacked over packed, for anything over RATIO_FLOOR
RATIO_FLOOR = 1 * MB

PICTURES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".heic", ".heif", ".bmp", ".ico", ".tif",
                      ".tiff"})
SOUNDS = frozenset({".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac", ".opus"})
VIDEOS = frozenset({".mp4", ".webm", ".mov", ".mkv", ".avi"})
FONTS = frozenset({".ttf", ".otf", ".woff", ".woff2"})
MODELS = frozenset({".safetensors", ".gguf", ".onnx", ".tflite", ".bin"})
DOCUMENTS = frozenset({".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"})
ARCHIVES = frozenset({".zip", ".tar", ".tgz", ".gz"})       # ``x.tar.gz`` counts as ``.tgz``
PASS_KINDS = PICTURES | SOUNDS | VIDEOS | FONTS | MODELS | DOCUMENTS | ARCHIVES
#: Plain text an archive may hold (each member must really be plain text, under TEXT_CAP).
DATA_TEXT = frozenset({".txt", ".md", ".csv", ".tsv", ".json", ".srt", ".vtt"})
_OOXML = frozenset({".docx", ".xlsx", ".pptx"})
_ODF = frozenset({".odt", ".ods", ".odp"})
#: Kinds that are zips themselves (no zip-tail rule for them).
_ZIPS = frozenset({".zip"}) | _OOXML | _ODF
#: Kinds read whole (in memory, up to FULL_CAP) when they sit inside an archive.
_WHOLE = DOCUMENTS | ARCHIVES | frozenset({".tflite", ".safetensors"})

CAPS = {**{e: 64 * MB for e in PICTURES}, **{e: 512 * MB for e in SOUNDS}, **{e: 2 * GB for e in VIDEOS},
        **{e: 32 * MB for e in FONTS}, ".safetensors": 4 * GB, ".gguf": 4 * GB, ".bin": 4 * GB, ".onnx": 2 * GB,
        ".tflite": 1 * GB, **{e: FULL_CAP for e in DOCUMENTS}, **{e: 2 * GB for e in ARCHIVES}}

#: Kinds Windows runs or opens by itself, outside any app's run line (shared with Drawbridge's never-text list).
WINDOWS_OPENS = frozenset({
    ".lnk", ".url", ".scf", ".reg", ".inf", ".hta", ".vbs", ".vbe", ".jse", ".wsf", ".wsh", ".wsc", ".msc",
    ".application", ".appref-ms", ".gadget", ".settingcontent-ms", ".library-ms", ".searchconnector-ms",
    ".chm", ".hlp", ".sct", ".ws", ".vb", ".shb", ".shs", ".ins", ".isp", ".mof", ".msu", ".rdp", ".cnt",
    ".psd1", ".ps1xml", ".ps2", ".ps2xml", ".psc1", ".psc2", ".msh", ".msh1", ".msh2", ".mshxml",
    ".msh1xml", ".msh2xml", ".theme", ".themepack", ".deskthemepack", ".search-ms", ".website", ".xbap",
    ".jnlp", ".appinstaller", ".appcontent-ms", ".diagcab", ".diagpkg", ".diagcfg", ".xnk", ".vsmacros",
    ".vsw", ".mde", ".ade", ".adp", ".mda", ".mdz", ".prg", ".pcd", ".prf", ".plg", ".grp", ".ops", ".mcf",
})

#: Kinds that can run code, with the plain name for the card: (one, many, kinds).
_RUNS = (
    ("Java program", "Java programs", {".jar", ".class", ".war", ".ear", ".jnlp"}),
    ("Android app", "Android apps", {".apk", ".aab", ".aar", ".dex"}),
    ("Python pickle", "Python pickles", {".pkl", ".pickle", ".pt", ".pth", ".ckpt", ".joblib", ".npy", ".npz",
                                         ".doctree"}),
    ("compiled Python file", "compiled Python files", {".pyc", ".pyo", ".pyd", ".whl", ".egg"}),
    ("Windows program", "Windows programs", {".exe", ".dll", ".msi", ".msix", ".msixbundle", ".appx",
                                             ".appxbundle", ".msp", ".mst", ".sys", ".scr", ".com", ".cpl",
                                             ".ocx", ".drv", ".pif", ".efi"}),
    ("program or library", "programs or libraries", {".so", ".dylib", ".elf", ".o", ".obj", ".a", ".lib", ".ko",
                                                     ".node", ".beam", ".luac", ".out"}),
    ("WebAssembly program", "WebAssembly programs", {".wasm"}),
    ("packed bundle of files", "packed bundles of files", {".data"}),
    ("Office file that can hold macros", "Office files that can hold macros",
     {".docm", ".xlsm", ".pptm", ".xlam", ".xll", ".dotm", ".xltm", ".potm", ".ppsm", ".ppam", ".sldm", ".doc",
      ".xls", ".ppt", ".dot", ".xlt", ".pot", ".pps", ".mdb", ".accdb"}),
    ("add-on or package", "add-ons or packages", {".crx", ".xpi", ".vsix", ".nupkg", ".gem"}),
    ("installer or disk image", "installers or disk images", {".dmg", ".pkg", ".iso", ".img", ".vhd", ".vhdx",
                                                              ".vmdk", ".qcow2", ".snap", ".appimage", ".deb",
                                                              ".rpm", ".cab"}),
    ("script", "scripts", {".sh", ".bash", ".zsh", ".ps1", ".psm1", ".bat", ".cmd", ".js", ".mjs", ".cjs", ".py",
                           ".pyw", ".rb", ".pl", ".php", ".lua", ".html", ".htm", ".xhtml", ".svg"}),
    ("Windows shortcut or script", "Windows shortcuts or scripts", set(WINDOWS_OPENS)),
)
_RUN_LABEL = {ext: (one, many) for one, many, exts in _RUNS for ext in exts}
_PLURAL = {one: many for one, many, _exts in _RUNS}
_PLURAL.update({"program": "programs", "Python pickle or packed model": "Python pickles or packed models",
                "PDF with scripts, attachments or actions": "PDFs with scripts, attachments or actions",
                "document that holds a program or a part it can't read":
                    "documents that hold a program or a part it can't read",
                "Office file with macros or linked code": "Office files with macros or linked code",
                "document with macros": "documents with macros"})

_PROGRAM_HEADS = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe",
                  b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"#!")
_ZIP_ENDS = (b"PK\x05\x06", b"PK\x06\x06")
_ZIP_ANY = (b"PK\x03\x04", b"PK\x01\x02", b"PK\x05\x06", b"PK\x06\x06")
_ZIP_SIG = re.compile(rb"PK(?:\x03\x04|\x05\x06|\x06\x06)")
_ZIP_METHODS = frozenset({0, 1, 6, 8, 9, 12, 14, 18, 19, 93, 95, 96, 97, 98, 99})


def _zip_sig_real(buf: bytes, i: int) -> bool:
    """Does the zip signature at *i* start a record that looks real? Cut off by the end of *buf*: it counts."""
    sig = buf[i + 2:i + 4]
    if sig == b"\x03\x04":                        # a local file header
        h = buf[i:i + 30]
        if len(h) < 30:
            return True
        ver, _flags, method = struct.unpack("<HHH", h[4:10])
        nlen, xlen = struct.unpack("<HH", h[26:30])
        return ver <= 63 and method in _ZIP_METHODS and 0 < nlen <= 1024 and xlen <= 8192
    if sig == b"\x05\x06":                        # an end record
        h = buf[i:i + 22]
        if len(h) < 22:
            return True
        d1, d2, n1, n2 = struct.unpack("<HHHH", h[4:12])
        return d1 == d2 == 0 and n1 == n2
    h = buf[i:i + 12]                               # a zip64 end record
    return len(h) < 12 or 44 <= struct.unpack("<Q", h[4:12])[0] <= MB


def _zip_in(buf: bytes, final: bool, upto: int | None = None) -> bool:
    for m in _ZIP_SIG.finditer(buf):
        if upto is not None and m.start() >= upto:
            break
        if _zip_sig_real(buf, m.start()):
            return True
    return False


class _ZipScan:
    """:func:`_zip_in` over a stream, window by window, so a record across two windows is still seen."""

    KEEP = 64

    def __init__(self) -> None:
        self.carry = b""
        self.found = False

    def feed(self, chunk: bytes) -> None:
        if self.found:
            return
        buf = self.carry + chunk
        if _zip_in(buf, False, max(0, len(buf) - self.KEEP)):
            self.found = True
        self.carry = buf[-self.KEEP:]

    def end(self) -> bool:
        return self.found or _zip_in(self.carry, True)
_NOT_TEXT_CHAR = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")


@dataclass(frozen=True)
class Verdict:
    """``ok``: it passes. Else ``sort`` says why it asks (``runs``, ``not_kind``, ``too_big``, ``archive``,
    ``sealed``, ``unknown``), and ``label`` names the kind for the card (``Java program``)."""

    ok: bool
    sort: str = ""
    label: str = ""


OK = Verdict(True)


def _no(sort: str, label: str = "") -> Verdict:
    return Verdict(False, sort, label)


class _Over(Exception):
    """A cap was reached while reading: the file asks."""


@dataclass
class _Budget:
    members: int = MAX_MEMBERS
    unpacked: int = MAX_UNPACKED


def budget() -> _Budget:
    """What one whole Drawbridge check may unpack, across all its files (:func:`check_file`'s *shared*)."""
    return _Budget(CHECK_MEMBERS, CHECK_UNPACKED)


def kind_ext(name: str) -> str:
    """The kind of a lower-case file *name* for these checks: its last dotted part, ``.tgz`` for ``.tar.gz``."""
    name = name.lower()
    if name.endswith(".tar.gz"):
        return ".tgz"
    return os.path.splitext(name)[1]


def passable(name: str) -> bool:
    """True when a file named *name* is of a kind that may pass under Normal (it still needs :func:`check`)."""
    return kind_ext(name) in PASS_KINDS


def group_word(name: str) -> str:
    ext = kind_ext(name)
    for words, group in (("picture", PICTURES), ("sound", SOUNDS), ("video", VIDEOS), ("font", FONTS),
                         ("model", MODELS), ("document", DOCUMENTS), ("archive", ARCHIVES)):
        if ext in group:
            return words
    return "file"


def program_head(head: bytes) -> bool:
    """A Windows, Linux or Mac program, a Java class, or a ``#!`` script starts with these bytes."""
    return any(head.startswith(m) for m in _PROGRAM_HEADS)


def category(name: str, head: bytes = b"") -> Verdict:
    """Why a file of a kind with no pass check asks: ``runs`` with its plain name, else ``unknown``."""
    ext = kind_ext(name)
    if ext in _RUN_LABEL:
        return _no("runs", _RUN_LABEL[ext][0])
    if head and program_head(head):
        return _no("runs", "program")
    return _no("unknown")


# -- what is read --------------------------------------------------------------------------------------------

class Blob:
    """One file to check: on disk (*fh*, read where needed) or an archive member (*data* whole, or only its
    *head* and *tail* when it is too big to keep in memory)."""

    def __init__(self, name: str, size: int, *, fh: Any = None, data: bytes | None = None, head: bytes = b"",
                 tail: bytes = b"", zip_seen: bool | None = None) -> None:
        self.name = name.lower()
        self.size = int(size)
        self._fh, self._data, self._head, self._tail = fh, data, head, tail
        self._zip = zip_seen

    def zip_inside(self) -> bool:
        """A zip record (a local file header, an end record) that looks real anywhere in it, not only at its
        end: some tools find a zip by its first record. Read once, in windows, never kept whole."""
        if self._zip is None:
            if self._data is not None:
                self._zip = _zip_in(self._data, True)
            elif self._fh is not None:
                self._fh.seek(0)
                scan = _ZipScan()
                for chunk in iter(lambda: self._fh.read(4 * MB), b""):
                    scan.feed(chunk)
                self._zip = scan.end()
            else:
                self._zip = True                          # nothing kept to look at: it counts
        return self._zip

    def read_at(self, off: int, n: int) -> bytes | None:
        """*n* bytes from *off* (fewer at the end), or None when they were not kept."""
        if off < 0 or n < 0 or off > self.size:
            return None
        n = min(n, self.size - off)
        if self._data is not None:
            return self._data[off:off + n]
        if self._fh is not None:
            self._fh.seek(off)
            return self._fh.read(n)
        if off + n <= len(self._head):
            return self._head[off:off + n]
        start = self.size - len(self._tail)
        if off >= start:
            return self._tail[off - start:off - start + n]
        return None

    @property
    def head(self) -> bytes:
        return self.read_at(0, HEAD) or b""

    @property
    def tail(self) -> bytes:
        return self.read_at(max(0, self.size - TAIL), TAIL) or b""

    def stream(self) -> Any | None:
        """A seekable file to read it as an archive (never unpacked to disk), or None when not kept whole."""
        if self._data is not None:
            return io.BytesIO(self._data)
        if self._fh is not None:
            self._fh.seek(0)
            return self._fh
        return None

    def whole(self) -> bytes | None:
        if self._data is not None:
            return self._data
        if self._fh is not None and self.size <= FULL_CAP:
            self._fh.seek(0)
            return self._fh.read(self.size + 1)[:self.size]
        return None


def check_file(path: str, name: str, size: int, shared: _Budget | None = None) -> Verdict:
    """Check one file on disk as the kind its *name* says. Never raises: a check that can't finish asks.
    *shared* (:func:`budget`) caps what one whole Drawbridge check unpacks across all its files."""
    if shared is None:
        own = _Budget(MAX_MEMBERS, MAX_UNPACKED)
    else:
        own = _Budget(min(MAX_MEMBERS, shared.members), min(MAX_UNPACKED, shared.unpacked))
    start = (own.members, own.unpacked)
    try:
        with open(path, "rb") as fh:
            real = os.fstat(fh.fileno()).st_size
            return check(Blob(name, real if real >= 0 else size, fh=fh), 0, own)
    except OSError:
        return _no("unreadable")
    except Exception:  # noqa: BLE001 - a check that crashed is never a pass
        return _no("unknown")
    finally:
        if shared is not None:
            shared.members -= start[0] - own.members
            shared.unpacked -= start[1] - own.unpacked


def check(blob: Blob, depth: int = 0, budget: _Budget | None = None) -> Verdict:
    """Is *blob* really the kind its name says, under that kind's cap, and not a program in disguise?"""
    budget = budget if budget is not None else _Budget()
    ext = kind_ext(blob.name)
    if ext not in PASS_KINDS:
        return category(blob.name, blob.head[:8])
    if blob.size > CAPS[ext]:
        return _no("too_big")
    if blob.size == 0:
        return _no("not_kind")
    head = blob.head
    if program_head(head):
        return _no("not_kind")
    if ext not in _ZIPS and ext != ".tflite" and (any(m in blob.tail for m in _ZIP_ENDS) or blob.zip_inside()):
        return _no("runs", "Python pickle or packed model") if ext == ".bin" else _no("not_kind")
    try:
        return _CHECKS[ext](blob, ext, head, depth, budget)
    except _Over:
        return _no("too_big") if depth == 0 else _no("archive")
    except Exception:  # noqa: BLE001 - a layout it can't follow is not that kind
        return _no("not_kind") if depth == 0 else _no("archive")


def _member(blob: Blob, depth: int, budget: _Budget) -> Verdict:
    """One member of an archive: plain data text, or a kind that passes :func:`check`."""
    if kind_ext(blob.name) in DATA_TEXT:
        data = blob.whole()
        return OK if data is not None and blob.size <= TEXT_CAP and _plain_text(data) else _no("not_kind")
    return check(blob, depth, budget)


def _plain_text(raw: bytes) -> bool:
    try:
        text = raw.decode("utf-8-sig", "strict")
    except UnicodeDecodeError:
        return False
    return not _NOT_TEXT_CHAR.search(text)


# -- pictures, sounds, video, fonts -------------------------------------------------------------------------

def _starts(*magics: bytes, at: int = 0):
    def fn(blob, ext, head, depth, budget):
        return OK if any(head[at:at + len(m)] == m for m in magics) else _no("not_kind")
    return fn


def _riff(form: bytes):
    def fn(blob, ext, head, depth, budget):
        return OK if head[:4] == b"RIFF" and head[8:12] == form else _no("not_kind")
    return fn


def _brands(head: bytes) -> list[bytes] | None:
    """The brands of an ISO media file's first ``ftyp`` box, or None when it does not start with one."""
    if len(head) < 16 or head[4:8] != b"ftyp":
        return None
    size = struct.unpack(">I", head[:4])[0]
    if size < 16 or size > 4096 or size > len(head):
        return None
    return [head[8:12]] + [head[i:i + 4] for i in range(16, size - 3, 4)]


def _ftyp(wanted: set[bytes] | None):
    def fn(blob, ext, head, depth, budget):
        brands = _brands(head)
        if brands is None or (wanted is not None and not wanted & set(brands)):
            return _no("not_kind")
        return OK
    return fn


def _mp3(blob, ext, head, depth, budget):
    return OK if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") else _no("not_kind")


def _aac(blob, ext, head, depth, budget):
    adts = len(head) > 1 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0
    return OK if adts or head[:4] == b"ADIF" or head[:3] == b"ID3" else _no("not_kind")


def _mov(blob, ext, head, depth, budget):
    return OK if head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip", b"pnot") else _no("not_kind")


# -- model weights ------------------------------------------------------------------------------------------

_DTYPES = frozenset({"F64", "F32", "F16", "BF16", "I64", "I32", "I16", "I8", "U64", "U32", "U16", "U8", "BOOL",
                     "F8_E4M3", "F8_E5M2", "F8_E8M0", "F6_E2M3", "F6_E3M2", "F4", "C64"})


def _safetensors(blob, ext, head, depth, budget):
    """8 bytes of header length, then a JSON header of tensors with plain dtypes, shapes and offsets."""
    raw = blob.read_at(0, 8)
    if raw is None or len(raw) != 8:
        return _no("not_kind")
    n = struct.unpack("<Q", raw)[0]
    if n < 2 or n > 16 * MB or 8 + n > blob.size:
        return _no("not_kind")
    text = blob.read_at(8, n)
    if text is None:
        return _no("too_big")
    meta = json.loads(text.decode("utf-8", "strict"))
    if not isinstance(meta, dict):
        return _no("not_kind")
    room = blob.size - 8 - n
    for key, val in meta.items():
        if key == "__metadata__":
            if not isinstance(val, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in val.items()):
                return _no("not_kind")
            continue
        if not isinstance(val, dict) or set(val) != {"dtype", "shape", "data_offsets"}:
            return _no("not_kind")
        shape, offs = val["shape"], val["data_offsets"]
        if (val["dtype"] not in _DTYPES or not isinstance(shape, list) or len(shape) > 16
                or not all(type(d) is int and d >= 0 for d in shape)
                or not isinstance(offs, list) or len(offs) != 2 or not all(type(o) is int for o in offs)
                or not 0 <= offs[0] <= offs[1] <= room):
            return _no("not_kind")
    return OK


def _gguf(blob, ext, head, depth, budget):
    if head[:4] != b"GGUF" or len(head) < 8:
        return _no("not_kind")
    return OK if struct.unpack("<I", head[4:8])[0] in (1, 2, 3) else _no("not_kind")


#: The first field of an ONNX model is its IR version (field 1); the next is one of the model's own fields.
_ONNX_NEXT = frozenset({0x12, 0x1A, 0x22, 0x28, 0x32, 0x3A, 0x42, 0x72, 0xA2, 0xCA})


def _onnx(blob, ext, head, depth, budget):
    """Conservative: field 1 (the IR version, 1 to 15) first, then one of a model's own fields."""
    if len(head) < 3 or head[0] != 0x08 or not 1 <= head[1] <= 15:
        return _no("not_kind")
    return OK if head[2] in _ONNX_NEXT else _no("not_kind")


def _tflite(blob, ext, head, depth, budget):
    """``TFL3`` at offset 4. Model metadata may be a zip at its end (labels and the like): then every member
    of that zip is checked as an archive's are."""
    if head[4:8] != b"TFL3":
        return _no("not_kind")
    if not any(m in blob.tail for m in _ZIP_ENDS):
        return _no("not_kind") if blob.zip_inside() else OK      # a zip with no end record of its own: asks
    return _zip_members(blob, depth, budget, need_head=False)


_PICKLE_RUNS = frozenset({"GLOBAL", "STACK_GLOBAL", "INST", "OBJ", "REDUCE", "NEWOBJ", "NEWOBJ_EX", "BUILD", "EXT1",
                          "EXT2", "EXT4", "PERSID", "BINPERSID", "STOP"})
_PICKLE_OPS = frozenset(op.code.encode("latin-1")[0] for op in pickletools.opcodes)
_PICKLE_PROTO = re.compile(rb"\x80[\x02-\x05]")
#: Heads of containers and packed data: a ``.bin`` that starts with one is not raw weights.
_PACKED_HEADS = (b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00", b"7z\xbc\xaf\x27\x1c", b"\x28\xb5\x2f\xfd", b"Rar!",
                 b"\x04\x22\x4d\x18", b"\x93NUMPY", b"\x89HDF")


def _pickle_from(buf: bytes, start: int, window: int = 4096) -> bool:
    """Parsed from *start* (``pickletools`` only, nothing loaded), does it reach an op that can run code or end a
    pickle, or run off the window, before a byte that is no pickle op?"""
    part = buf[start:start + window]
    bio = io.BytesIO(part)
    try:
        for op, _arg, _pos in pickletools.genops(bio):
            if op.name in _PICKLE_RUNS:
                return True
    except Exception:  # noqa: BLE001 - not a pickle, unless it ran off the end
        return bio.tell() >= len(part)
    return True


def pickle_like(head: bytes) -> bool:
    """True when *head* could be a Python pickle, or hold one a loader would reach: its first byte is any pickle
    op (``pickle.load`` starts there), or anywhere in it a pickle start (protocol 2 to 5) parses to an op that can
    run code. Raw numbers often look like this by chance, and then they ask: if unsure, it asks."""
    if not head:
        return False
    if head[0] in _PICKLE_OPS:
        return True
    return any(_pickle_from(head, m.start()) for m in _PICKLE_PROTO.finditer(head))


def _tar_header(head: bytes) -> bool:
    """A tar header at the start: ``ustar`` at offset 257, or a header whose checksum adds up."""
    if head[257:262] == b"ustar":
        return True
    if len(head) < 512:
        return False
    h = head[:512]
    try:
        want = int(h[148:156].replace(b"\x00", b" ").strip() or b"x", 8)
    except ValueError:
        return False
    return want in (sum(h[:148]) + 256 + sum(h[156:512]),
                    sum(b - 256 if b > 127 else b for b in h[:148] + h[156:512]) + 256)


def _bin(blob, ext, head, depth, budget):
    """Raw weights only. Asks for a zip (a PyTorch ``.bin`` is a zip holding a pickle; any zip record in its first
    or last bytes, or one that looks real anywhere), a tar (legacy PyTorch), gzip and other packed data, NumPy
    and HDF5 files, and anything that could be a pickle (:func:`pickle_like`)."""
    if (_tar_header(head) or head.startswith(_PACKED_HEADS) or any(m in head for m in _ZIP_ANY)
            or any(m in blob.tail for m in _ZIP_ANY) or blob.zip_inside()):
        return _no("runs", "Python pickle or packed model")
    if pickle_like(head):
        return _no("runs", "Python pickle")
    if _plain_text(head if blob.size <= HEAD else head[:HEAD - 4]):
        return _no("not_kind")                    # plain text is not raw weights: read and checked as text
    return OK


# -- documents ----------------------------------------------------------------------------------------------

_PDF_DELIMS = rb"\x00\t\n\x0c\r ()<>\[\]{}/%"
_PDF_NAME = re.compile(rb"/([^" + _PDF_DELIMS + rb"]*)")
_PDF_HEX = re.compile(rb"#([0-9A-Fa-f]{2})")
#: Names that say a PDF runs something, holds a file, or acts by itself (an /AA dict always holds actions).
_PDF_RUNS = frozenset({b"JavaScript", b"JS", b"Launch", b"EmbeddedFile", b"EmbeddedFiles", b"EF", b"FileAttachment",
                       b"RichMedia", b"XFA", b"GoToE", b"ImportData", b"AA"})
_PDF_TOKEN = re.compile(rb"""
    (?P<ws>[\x00\t\n\x0c\r ]+)
  | (?P<comment>%[^\r\n]*)
  | (?P<dopen><<)
  | (?P<dclose>>>)
  | (?P<hex><[0-9A-Fa-f\x00\t\n\x0c\r ]*>)
  | (?P<name>/[^\x00\t\n\x0c\r ()<>\[\]{}/%]*)
  | (?P<lit>\()
  | (?P<aopen>\[)
  | (?P<aclose>\])
  | (?P<word>[^\x00\t\n\x0c\r ()<>\[\]{}/%]+)
""", re.X)
_PDF_LIT = re.compile(rb"[()\\]")
_PDF_END = re.compile(rb"(?:\r\n|\r|\n)?endstream")
#: Streams it decodes: the filters a non-picture stream may use. Any other filter on one asks.
_PDF_FILTERS = {b"FlateDecode": "flate", b"Fl": "flate", b"ASCIIHexDecode": "hex", b"AHx": "hex",
                b"ASCII85Decode": "a85", b"A85": "a85", b"RunLengthDecode": "rl", b"RL": "rl"}
PDF_PREDICT_CAP = 8 * MB


class _PdfOdd(Exception):
    """A PDF laid out in a way the check does not follow: it asks."""


def _pdf_name(raw: bytes) -> bytes:
    return _PDF_HEX.sub(lambda m: bytes([int(m.group(1), 16)]), raw)


def _pdf_names(data: bytes) -> set[bytes]:
    """Every name in *data*, ended by any PDF white space or delimiter (NUL too), ``#xx`` decoded."""
    return {_pdf_name(m.group(1)) for m in _PDF_NAME.finditer(data)}


def _pdf_lit_end(data: bytes, i: int) -> int:
    """The end of the literal string that starts at *i* (balanced parentheses, backslash escapes), or -1."""
    depth, j = 0, i
    while True:
        m = _PDF_LIT.search(data, j)
        if m is None:
            return -1
        c, k = m.group(), m.start()
        if c == b"\\":
            j = k + 2
            continue
        depth += 1 if c == b"(" else -1
        j = k + 1
        if depth == 0:
            return j


def _pdf_dict(items: list) -> dict:
    """``<< /Key value ... >>`` as ``{key: value}``; a value ``n g R`` is one reference. Odd: :class:`_PdfOdd`."""
    out: dict = {}
    i = 0
    while i < len(items):
        key = items[i]
        if key[0] != "n" or i + 1 >= len(items):
            raise _PdfOdd()
        val = items[i + 1]
        if (val[0] == "w" and i + 3 < len(items) and items[i + 2][0] == "w" and items[i + 3] == ("w", b"R")):
            val, i = ("r", None), i + 4
        else:
            i += 2
        out[key[1]] = val
    return out


def _pdf_parse(data: bytes, names: set, on_stream=None) -> None:
    """Walk *data* token by token: every name into *names*; each ``stream`` handed, with the dict just before
    it, to *on_stream* (which returns where to go on). Anything it does not follow: :class:`_PdfOdd`."""
    pos, n = 0, len(data)
    stack: list = []
    last = None
    while pos < n:
        m = _PDF_TOKEN.match(data, pos)
        if m is None:
            raise _PdfOdd()
        kind, pos = m.lastgroup, m.end()
        if kind in ("ws", "comment"):
            continue
        if kind == "lit":
            pos = _pdf_lit_end(data, m.start())
            if pos < 0:
                raise _PdfOdd()
            item = ("s", None)
        elif kind == "hex":
            item = ("s", None)
        elif kind == "name":
            item = ("n", _pdf_name(m.group()[1:]))
            names.add(item[1])
        elif kind in ("dopen", "aopen"):
            stack.append(("d" if kind == "dopen" else "a", []))
            if len(stack) > 64:
                raise _PdfOdd()
            continue
        elif kind in ("dclose", "aclose"):
            if not stack or stack[-1][0] != ("d" if kind == "dclose" else "a"):
                raise _PdfOdd()
            what, items = stack.pop()
            item = ("d", _pdf_dict(items)) if what == "d" else ("a", items)
        else:
            word = m.group()
            if word == b"stream":
                if stack or on_stream is None or last is None or last[0] != "d":
                    raise _PdfOdd()
                pos = on_stream(last[1], pos)
                last = None
                continue
            item = ("w", word)
        if stack:
            stack[-1][1].append(item)
        else:
            last = item
    if stack:
        raise _PdfOdd()


def _pdf_list(val) -> list:
    if val is None or val == ("w", b"null"):
        return []
    return list(val[1]) if val[0] == "a" else [val]


def _pdf_unpredict(raw: bytes, parms: dict) -> bytes:
    """Undo a PNG predictor (10 to 15). TIFF's (2) or any other: :class:`_PdfOdd`."""
    def num(key, default):
        v = parms.get(key)
        if v is None:
            return default
        if v[0] != "w" or not v[1].isdigit():
            raise _PdfOdd()
        return int(v[1])
    pred = num(b"Predictor", 1)
    if pred == 1:
        return raw
    if not 10 <= pred <= 15 or len(raw) > PDF_PREDICT_CAP:
        raise _PdfOdd()
    colors, bpc, cols = num(b"Colors", 1), num(b"BitsPerComponent", 8), num(b"Columns", 1)
    if not (1 <= colors <= 32 and bpc in (1, 2, 4, 8, 16) and 1 <= cols <= 1 << 20):
        raise _PdfOdd()
    bpp = max(1, colors * bpc // 8)
    width = (colors * bpc * cols + 7) // 8
    out = bytearray()
    prev = bytearray(width)
    for i in range(0, len(raw), width + 1):
        ft, row = raw[i], bytearray(raw[i + 1:i + 1 + width].ljust(width, b"\x00"))
        for x in range(width):
            a = row[x - bpp] if x >= bpp else 0
            b, c = prev[x], (prev[x - bpp] if x >= bpp else 0)
            if ft == 1:
                row[x] = (row[x] + a) & 0xFF
            elif ft == 2:
                row[x] = (row[x] + b) & 0xFF
            elif ft == 3:
                row[x] = (row[x] + (a + b) // 2) & 0xFF
            elif ft == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                row[x] = (row[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            elif ft != 0:
                raise _PdfOdd()
        out += row
        prev = row
    return bytes(out)


def _pdf_decode(body: bytes, d: dict, left: int) -> bytes:
    """A stream's bytes with every filter undone, up to *left* bytes. A filter it does not decode, bad data or
    a stream that does not end: :class:`_PdfOdd`; over *left*: :class:`_Over`."""
    filters = _pdf_list(d.get(b"Filter"))
    parms = _pdf_list(d.get(b"DecodeParms") or d.get(b"DP"))
    if any(f[0] != "n" for f in filters) or len(filters) > 4:
        raise _PdfOdd()
    data = body
    for k, f in enumerate(filters):
        how = _PDF_FILTERS.get(f[1])
        p = parms[k] if k < len(parms) else None
        p = p[1] if p is not None and p[0] == "d" else {}
        if how == "flate":
            z = zlib.decompressobj()
            try:
                data = z.decompress(data, left + 1)
            except zlib.error:
                raise _PdfOdd() from None
            if len(data) > left:
                raise _Over()
            if not z.eof:
                raise _PdfOdd()
            data = _pdf_unpredict(data, p)
        elif how == "hex":
            txt = re.sub(rb"[\x00\t\n\x0c\r ]", b"", data.split(b">", 1)[0])
            try:
                data = bytes.fromhex((txt + b"0" if len(txt) % 2 else txt).decode("ascii"))
            except ValueError:
                raise _PdfOdd() from None
        elif how == "a85":
            txt = data.strip()
            txt = txt[2:] if txt.startswith(b"<~") else txt
            try:
                data = base64.a85decode(txt.split(b"~>", 1)[0], ignorechars=b"\x00\t\n\x0c\r ")
            except ValueError:
                raise _PdfOdd() from None
        elif how == "rl":
            out, i = bytearray(), 0
            while i < len(data) and data[i] != 128:
                n = data[i]
                if n < 128:
                    out += data[i + 1:i + 2 + n]
                    i += n + 2
                else:
                    out += data[i + 1:i + 2] * (257 - n)
                    i += 2
                if len(out) > left:
                    raise _Over()
            data = bytes(out)
        else:
            raise _PdfOdd()
        if len(data) > left:
            raise _Over()
    return data


def _pdf(blob, ext, head, depth, budget):
    """No script, no attached or embedded file, no action that runs by itself (:data:`_PDF_RUNS`), wherever it is:
    the file's own syntax, read token by token, and every stream that is not a picture, decoded (object streams
    walked as PDF syntax too). A locked PDF, a filter it does not decode, data over the caps, or a layout it does
    not follow asks."""
    if not head.startswith(b"%PDF-"):
        return _no("not_kind")
    data = blob.whole()
    if data is None:
        return _no("too_big")
    names: set[bytes] = set()
    bodies: list[tuple[int, int]] = []             # stream bodies: scanned decoded, or pictures readers never parse
    left = [FULL_CAP]

    def on_stream(d: dict, pos: int) -> int:
        if data[pos:pos + 2] == b"\r\n":
            start = pos + 2
        elif data[pos:pos + 1] in (b"\n", b"\r"):
            start = pos + 1
        else:
            raise _PdfOdd()
        end = None
        length = d.get(b"Length")
        if length is not None and length[0] == "w" and length[1].isdigit():
            n = int(length[1])
            if _PDF_END.match(data, start + n):
                end = start + n
        if end is None:
            k = data.find(b"endstream", start)
            if k < 0:
                raise _PdfOdd()
            end = k
            while end > start and data[end - 1:end] in (b"\r", b"\n"):
                end -= 1
        after = _PDF_END.match(data, end)
        if after is None:
            raise _PdfOdd()
        picture = d.get(b"Subtype") == ("n", b"Image") and d.get(b"Type") in (None, ("n", b"XObject"))
        if not picture:
            got = _pdf_decode(data[start:end], d, left[0])
            left[0] -= len(got)
            if d.get(b"Type") == ("n", b"ObjStm"):
                _pdf_parse(got, names)              # objects in a stream: PDF syntax, and no stream inside
            else:
                names.update(_pdf_names(got))
        bodies.append((start, end))
        return after.end()

    try:
        _pdf_parse(data, names, on_stream)
    except _PdfOdd:
        return _no("not_kind")
    # Stricter than the tokens: every name-shaped run outside stream bodies, inside strings and comments too.
    pos = 0
    for start, end in bodies:
        names.update(_pdf_names(data[pos:start]))
        pos = end
    names.update(_pdf_names(data[pos:]))
    if b"Encrypt" in names:
        return _no("sealed")
    if names & _PDF_RUNS:
        return _no("runs", "PDF with scripts, attachments or actions")
    return OK


_EXTERNAL_RISK = re.compile(rb"/(?:attachedTemplate|oleObject|frame|subDocument|package|control)\"", re.I)
_REL = re.compile(rb"<Relationship\b[^>]*>", re.I)
_DDE_XL = re.compile(rb"<(?:ddeLink|oleLink)\b|<f\b[^>]*>[^<]*\|[^<]*![^<]*</f>", re.I)
_INSTR = re.compile(rb"<w:instrText[^>]*>([^<]*)</w:instrText>|w:instr=\"([^\"]*)\"")


#: Parts an Office or OpenDocument file may hold besides its XML: pictures and fonts (checked as their kind),
#: and Windows metafiles (checked by their first bytes).
_OFFICE_MEDIA = PICTURES | FONTS
_OFFICE_XML = (".xml", ".rels", ".vml", ".rdf")
_PRINTER = re.compile(r"^[a-z]{2,4}/printersettings/printersettings\d{1,4}\.bin$")
_PART = "document that holds a program or a part it can't read"


def _metafile(head: bytes, ext: str) -> bool:
    if ext == ".emf":
        return len(head) >= 44 and head[:4] == b"\x01\x00\x00\x00" and head[40:44] == b" EMF"
    return head[:4] == b"\xd7\xcd\xc6\x9a" or head[:6] in (b"\x01\x00\x09\x00\x00\x03", b"\x02\x00\x09\x00\x00\x03")


def _office(blob, ext, head, depth, budget):
    """A Word, Excel or PowerPoint file, or an OpenDocument file, where every part is one it knows: XML (with no
    macros, ActiveX, embedded objects, linked templates, DDE or script links), pictures and fonts that pass
    their own check, and Windows metafiles. Any other part asks: code, a program, a Java class or manifest,
    ``__main__.py``, a part with no extension or of a kind no list names. ``.docm`` and the other macro kinds,
    and old ``.doc`` and ``.xls``, always ask."""
    if not head.startswith(b"PK\x03\x04"):
        return _no("not_kind")
    if depth >= MAX_DEPTH:
        return _no("archive")
    s = blob.stream()
    if s is None:
        return _no("too_big")
    odf = ext in _ODF
    macros = _no("runs", "Office file with macros or linked code")
    part = _no("runs", _PART)
    seen: set[str] = set()
    with zipfile.ZipFile(s) as zf:
        infos = zf.infolist()
        lower = [i.filename.replace("\\", "/").lower() for i in infos]
        if odf:
            if not infos or infos[0].filename != "mimetype":
                return _no("not_kind")
            if not _read_capped(zf, infos[0], budget, 200).startswith(b"application/vnd.oasis.opendocument."):
                return _no("not_kind")
        elif "[content_types].xml" not in lower:
            return _no("not_kind")
        instr = b""
        for k, (info, low) in enumerate(zip(infos, lower)):
            bad = _info_problem(info, budget)
            if bad is not None:
                return bad
            if low.casefold() in seen:
                return _no("archive")
            seen.add(low.casefold())
            if info.is_dir() or (odf and k == 0):
                continue
            pext = kind_ext(low.rsplit("/", 1)[-1])
            if odf and low.startswith(("basic/", "scripts/")):
                return macros
            if not odf:
                if "/embeddings/" in "/" + low and pext in _OOXML:          # a chart's own workbook
                    with zf.open(info) as fh:
                        got = _member(_take(low.rsplit("/", 1)[-1], fh, info.file_size, budget), depth + 1, budget)
                    if not got.ok:
                        return macros
                    continue
                if _PRINTER.match(low) and info.file_size <= 64 * KB:      # the printer's own settings
                    continue
                if (low.endswith(".bin") or "vbaproject" in low or "vbadata" in low or "/embeddings/" in "/" + low
                        or "/activex/" in "/" + low):
                    return macros
            if low.endswith(_OFFICE_XML):
                body = _read_capped(zf, info, budget, XML_CAP)
                if odf:
                    if re.search(rb"vnd\.sun\.star\.script|macro:|script:event-listener", body, re.I):
                        return macros
                    continue
                if low == "[content_types].xml" and re.search(
                        rb"macroEnabled|vbaProject|activeX|oleObject|vbaData", body, re.I):
                    return macros
                if low.endswith(".rels"):
                    for rel in _REL.finditer(body):
                        if b"external" in rel.group(0).lower() and _EXTERNAL_RISK.search(rel.group(0)):
                            return macros
                if _DDE_XL.search(body):                                   # a DDE link or a DDE formula
                    return macros
                for m in _INSTR.finditer(body):
                    instr += (m.group(1) or m.group(2) or b"") + b" "
                continue
            if pext in _OFFICE_MEDIA or pext in (".emf", ".wmf"):
                with zf.open(info) as fh:
                    got = _take(low.rsplit("/", 1)[-1], fh, info.file_size, budget)
                if pext in (".emf", ".wmf"):
                    if not _metafile(got.head, pext) or program_head(got.head) or got.zip_inside():
                        return part
                elif not _member(got, depth + 1, budget).ok:
                    return part
                continue
            return part                                                    # any other part asks
        if re.search(rb"\bDDE(?:AUTO)?\b", re.sub(rb"\s+", b" ", instr).upper()):
            return macros
    return OK


# -- archives -----------------------------------------------------------------------------------------------

def _bad_name(name: str) -> bool:
    """A member path an app could unpack outside its own folder."""
    n = name.replace("\\", "/")
    return n.startswith("/") or (len(n) > 1 and n[1] == ":") or ".." in n.split("/")


def _info_problem(info: zipfile.ZipInfo, budget: _Budget) -> Verdict | None:
    """Caps and red flags from a zip member's own record, before it is read."""
    if info.flag_bits & 0x1:
        return _no("sealed")
    budget.members -= 1
    if budget.members < 0 or _bad_name(info.filename):
        return _no("archive")
    if ((info.external_attr >> 16) & 0o170000) == 0o120000:          # a link
        return _no("archive")
    if info.file_size > budget.unpacked:
        return _no("archive")
    if info.file_size > RATIO_FLOOR and info.file_size > MAX_RATIO * max(1, info.compress_size):
        return _no("archive")
    return None


def _read_capped(zf: zipfile.ZipFile, info: zipfile.ZipInfo, budget: _Budget, cap: int) -> bytes:
    if info.file_size > cap:
        raise _Over()
    with zf.open(info) as fh:
        body = fh.read(cap + 1)
    budget.unpacked -= len(body)
    if len(body) > cap or budget.unpacked < 0:
        raise _Over()
    return body


def _take(name: str, fh: Any, declared: int, budget: _Budget, ratio=None) -> Blob:
    """Read one member as a :class:`Blob`, in memory, never to disk: whole when its kind needs it and it is
    small enough, else only its first and last bytes. Over *declared* or the budget: :class:`_Over`."""
    ext = kind_ext(name)
    keep = (ext in _WHOLE and declared <= FULL_CAP) or (ext in DATA_TEXT and declared <= TEXT_CAP)
    head = bytearray()
    tail = b""
    parts: list[bytes] = []
    total = 0
    scan = _ZipScan()
    while True:
        chunk = fh.read(MB)
        if not chunk:
            break
        scan.feed(chunk)
        total += len(chunk)
        budget.unpacked -= len(chunk)
        if total > declared or budget.unpacked < 0:
            raise _Over()
        if ratio is not None:
            ratio(len(chunk))
        if keep:
            parts.append(chunk)
        if len(head) < HEAD:
            head += chunk[:HEAD - len(head)]
        tail = (tail + chunk)[-TAIL:]
    return Blob(name, total, data=b"".join(parts) if keep else None, head=bytes(head), tail=tail,
                zip_seen=scan.end())


def _zip(blob, ext, head, depth, budget):
    if not head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return _no("not_kind")
    return _zip_members(blob, depth, budget)


def _zip_members(blob: Blob, depth: int, budget: _Budget, need_head: bool = True) -> Verdict:
    if depth >= MAX_DEPTH:
        return _no("archive")
    s = blob.stream()
    if s is None:
        return _no("too_big") if depth == 0 else _no("archive")
    seen: set[str] = set()
    with zipfile.ZipFile(s) as zf:
        for info in zf.infolist():
            bad = _info_problem(info, budget)
            if bad is not None:
                return bad
            key = info.filename.replace("\\", "/").casefold()
            if key in seen:                                   # two members of one name: tools differ on which
                return _no("archive")
            seen.add(key)
            if info.is_dir():
                continue
            base = info.filename.replace("\\", "/").rsplit("/", 1)[-1]
            with zf.open(info) as fh:
                got = _member(_take(base, fh, info.file_size, budget), depth + 1, budget)
            if not got.ok:
                return got if got.sort == "sealed" else _no("archive")
    return OK


def _tar(blob, ext, head, depth, budget):
    """A ``.tar`` (``ustar``) or ``.tar.gz``: every member a plain file or folder, each checked."""
    if ext == ".tar" and head[257:262] != b"ustar":
        return _no("not_kind")
    if ext == ".tgz" and not head.startswith(b"\x1f\x8b"):
        return _no("not_kind")
    if depth >= MAX_DEPTH:
        return _no("archive")
    s = blob.stream()
    if s is None:
        return _no("too_big") if depth == 0 else _no("archive")
    packed = max(1, blob.size)
    seen = [0]

    def ratio(n: int) -> None:
        seen[0] += n
        if ext == ".tgz" and seen[0] > RATIO_FLOOR and seen[0] > MAX_RATIO * packed:
            raise _Over()

    with tarfile.open(fileobj=s, mode="r:gz" if ext == ".tgz" else "r:") as tf:
        for m in tf:
            budget.members -= 1
            if budget.members < 0 or _bad_name(m.name):
                return _no("archive")
            if m.isdir():
                continue
            if not m.isfile():                    # a link, a device, a pipe
                return _no("archive")
            if m.size > budget.unpacked:
                return _no("archive")
            fh = tf.extractfile(m)                # a reader over the archive itself, in memory
            if fh is None:
                return _no("archive")
            got = _member(_take(m.name.rsplit("/", 1)[-1], fh, m.size, budget, ratio), depth + 1, budget)
            if not got.ok:
                return got if got.sort == "sealed" else _no("archive")
    return OK


def _gz(blob, ext, head, depth, budget):
    """One packed file (``model.onnx.gz``): checked as the kind its name says without the ``.gz``."""
    if not head.startswith(b"\x1f\x8b"):
        return _no("not_kind")
    if depth >= MAX_DEPTH:
        return _no("archive")
    inner = blob.name[:-3]
    iext = kind_ext(inner)
    if iext not in PASS_KINDS and iext not in DATA_TEXT:
        return category(inner)
    s = blob.stream()
    if s is None:
        return _no("too_big") if depth == 0 else _no("archive")
    packed = max(1, blob.size)
    seen = [0]

    def ratio(n: int) -> None:
        seen[0] += n
        if seen[0] > RATIO_FLOOR and seen[0] > MAX_RATIO * packed:
            raise _Over()

    budget.members -= 1
    if budget.members < 0:
        return _no("archive")
    with gzip.GzipFile(fileobj=s) as g:
        got = _member(_take(inner, g, CAPS.get(iext, TEXT_CAP), budget, ratio), depth + 1, budget)
    return got if got.ok or got.sort in ("runs", "sealed") else _no("archive")


_CHECKS = {
    ".png": _starts(b"\x89PNG\r\n\x1a\n"), ".jpg": _starts(b"\xff\xd8\xff"), ".jpeg": _starts(b"\xff\xd8\xff"),
    ".gif": _starts(b"GIF87a", b"GIF89a"), ".webp": _riff(b"WEBP"), ".bmp": _starts(b"BM"),
    ".ico": _starts(b"\x00\x00\x01\x00"), ".tif": _starts(b"II*\x00", b"MM\x00*"),
    ".tiff": _starts(b"II*\x00", b"MM\x00*"), ".avif": _ftyp({b"avif", b"avis"}),
    ".heic": _ftyp({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}),
    ".heif": _ftyp({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}),
    ".mp3": _mp3, ".wav": _riff(b"WAVE"), ".ogg": _starts(b"OggS"), ".opus": _starts(b"OggS"),
    ".flac": _starts(b"fLaC"), ".m4a": _ftyp(None), ".aac": _aac,
    ".mp4": _ftyp(None), ".mov": _mov, ".webm": _starts(b"\x1a\x45\xdf\xa3"), ".mkv": _starts(b"\x1a\x45\xdf\xa3"),
    ".avi": _riff(b"AVI "),
    ".ttf": _starts(b"\x00\x01\x00\x00", b"true"), ".otf": _starts(b"OTTO"), ".woff": _starts(b"wOFF"),
    ".woff2": _starts(b"wOF2"),
    ".safetensors": _safetensors, ".gguf": _gguf, ".onnx": _onnx, ".tflite": _tflite, ".bin": _bin,
    ".pdf": _pdf, ".docx": _office, ".xlsx": _office, ".pptx": _office, ".odt": _office, ".ods": _office,
    ".odp": _office,
    ".zip": _zip, ".tar": _tar, ".tgz": _tar, ".gz": _gz,
}
assert set(_CHECKS) == PASS_KINDS


# -- the card's words ---------------------------------------------------------------------------------------

def shown_name(rel: str) -> str:
    """A held file's base name for a card: no folders, no control or direction characters, at most 60."""
    base = str(rel).replace("\\", "/").rsplit("/", 1)[-1]
    clean = "".join("?" if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp", "Co", "Cs") else c for c in base)
    return clean if len(clean) <= 60 else clean[:57] + "..."


#: One plain token: a file name can't speak on a card ("PuffPAI says this is safe.md") as a sentence can.
_PLAIN_NAME = re.compile(r"[A-Za-z0-9._-]+")


def card_name(rel: str) -> str:
    """A file's base name for a card when it is one plain token (letters, digits, ``.``, ``_``, ``-``) that reads like
    no order, link or markup; else "": it only counts (D105)."""
    shown = shown_name(rel)
    return shown if _PLAIN_NAME.fullmatch(shown) and not text_rules.owner_text_flag(shown) else ""


def card_names(rels: Iterable[str]) -> str:
    """At most three base names fit for a card, then how many more. "" when none is fit."""
    rels = list(rels)
    fit = [n for n in map(card_name, rels) if n][:3]
    more = len(rels) - len(fit)
    return ", ".join(fit) + (f" and {more} more" if fit and more > 0 else "")


def _names(rels: list[str]) -> str:
    return card_names(rels) or ("name not shown" if len(rels) == 1 else "names not shown")


def _n(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def held_words(sorts: dict[str, Iterable[str]]) -> list[str]:
    """Plain lines for the card from ``{rel: (sort, label)}``: counts by kind and at most three base names a
    line, never a path."""
    by: dict[str, list[tuple[str, str]]] = {}
    for rel, got in sorts.items():
        sort, label = (list(got) + ["", ""])[:2]
        by.setdefault(str(sort or "unknown"), []).append((str(rel), str(label or "")))
    out: list[str] = []
    runs = by.get("runs") or []
    if runs:
        kinds: dict[str, list[str]] = {}
        for rel, label in runs:
            ext = shown_name(os.path.splitext(shown_name(rel).lower())[1])[:12]
            exts = kinds.setdefault(label or "program", [])
            if ext and _PLAIN_NAME.fullmatch(ext) and ext not in exts:
                exts.append(ext)
        counts = {k: sum(1 for _r, lab in runs if (lab or "program") == k) for k in kinds}
        parts = [_n(counts[k], k, _PLURAL.get(k, k + "s")) + (f" ({', '.join(kinds[k][:3])})" if kinds[k] else "")
                 for k in kinds]
        out.append(f"{_n(len(runs), 'file', 'files')} can run code when opened: " + ", ".join(parts)
                   + ". For example: " + _names([r for r, _l in runs]) + ".")
    words = (
        ("not_kind", "{n} is not what its name says: {x}.", "{n} are not what their names say: {x}."),
        ("sealed", "{n} is locked with a password, so Drawbridge can't look inside: {x}.",
         "{n} are locked with a password, so Drawbridge can't look inside: {x}."),
        ("archive", "{n} holds files that ask, or is over a size cap: {x}.",
         "{n} hold files that ask, or are over a size cap: {x}."),
        ("unknown", "{n} of a kind Drawbridge can't read: {x}.", "{n} of a kind Drawbridge can't read: {x}."),
        ("too_big", "{n} is too big to check: {x}.", "{n} are too big to check: {x}."),
        ("locked", "{n} would pass under Normal, but your setting, Locked down, asks for every one: {x}.",
         "{n} would pass under Normal, but your setting, Locked down, asks for every one: {x}."),
        ("link", "{n} Drawbridge does not follow: {x}.", "{n} Drawbridge does not follow: {x}."),
        ("unreadable", "{n} could not be opened: {x}.", "{n} could not be opened: {x}."),
        ("cut", "", ""),
        ("ai_words", "", ""),          # said by Drawbridge's own line for words aimed at an AI (D105)
        ("tunnel", "", ""),            # said by Drawbridge's own line for a tunnel (D117)
    )
    nouns = {"archive": ("archive", "archives"), "link": ("link", "links")}
    for sort, one, many in words:
        rels = [r for r, _l in by.get(sort) or []]
        if not rels or not one:
            continue
        noun = nouns.get(sort, ("file", "files"))
        out.append((one if len(rels) == 1 else many).format(n=_n(len(rels), *noun), x=_names(rels)))
    known = {w[0] for w in words} | {"runs"}
    other = [r for s, items in by.items() if s not in known for r, _l in items]
    if other:
        out.append(f"{_n(len(other), 'file', 'files')} Drawbridge couldn't check: {_names(other)}.")
    return out


__all__ = ["ARCHIVES", "CAPS", "DOCUMENTS", "FONTS", "LOCKED", "MODELS", "MODES", "NORMAL", "PASS_KINDS", "PICTURES",
           "SOUNDS", "VIDEOS", "Verdict", "WINDOWS_OPENS", "budget", "card_name", "card_names", "category", "check", "check_file", "group_word",
           "held_words", "kind_ext", "passable", "pickle_like", "program_head", "shown_name"]
