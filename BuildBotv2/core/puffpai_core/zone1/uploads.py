"""One file sent from the device she is sitting at (D134, built, not yet tested by her). v1's Send to Puff, kept small.

It lands in one place, ``<data folder>/uploads/<its name>``, where the bot reads it as it reads the rest of her data
folder. Pin and Send to... then work on that copy as on any other file. No path from the client is ever used: only a
name, and one that can't leave the folder, checked here and then by the same check as a pinned file
(``folders.file_problem``), so the same endings hold and a vault or PuffPAI file can't be reached.

The limit holds while reading: a declared size over it is refused before the body is touched, and a body with no
declared size stops at the limit. A second upload of one name replaces the first, as v1 did, and the reply says so: that
is how she gets saved changes back in. There is no sync.

Each file saved is recorded (``Library.record_copy``), since in a data folder she chose an ``uploads`` folder may be her own.
There an upload never replaces a file that wasn't recorded, so it can't turn her file into a copy Remove would delete.
"""

from __future__ import annotations

import os
import re
import secrets
import unicodedata
from typing import Any, BinaryIO

from .folders import PIN_GONE, PIN_KIND, PIN_SUFFIXES, canon, file_problem

UPLOAD_MAX = 50 * 1024 * 1024            # v1's own limit
FOLDER = "uploads"
MAX_NAME_CHARS = 120
MAX_NAME_BYTES = 240                     # most file systems stop at 255
_CHUNK = 1 << 20
#: Windows gives these names to devices, with any ending. Refused everywhere: a data folder can move between computers.
_DEVICE_NAME = re.compile(r"(?i)^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)$")
_NOT_IN_A_NAME = set('/\\:*?"<>|')

BAD_NAME = "That file name can't be used. Rename the file and send it again."
LONG_NAME = "That file name is too long. Rename the file and send it again."
TOO_BIG = "That file is too big to send. The most is 50 MB."
EMPTY = "That file is empty, so nothing was saved."
NOT_SAVED = "That file could not be saved on your bot. It may be out of room."
LINKED_PLACE = "The folder your bot keeps these in goes through a link to somewhere else, so nothing was saved. Remove the link first."
HERS = "A file of yours with that name is already in that folder, so nothing was saved. Rename the file and send it again."


class UploadRefused(Exception):
    """A plain line for the owner: nothing was saved."""

    def __init__(self, line: str, code: str = "upload", status: int = 400) -> None:
        super().__init__(line)
        self.code, self.status = code, status


def clean_name(raw: Any) -> str:
    """A plain file name, or :class:`UploadRefused`. Refused rather than cut down, so a name is never quietly another one."""
    if not isinstance(raw, str) or not raw.strip():
        raise UploadRefused(BAD_NAME, "name")
    name = unicodedata.normalize("NFC", raw)
    if len(name) > MAX_NAME_CHARS:
        raise UploadRefused(LONG_NAME, "name")
    # Word's own lock file is no document: it could never get a row.
    if name != name.strip() or name.startswith((".", "~$")) or name.endswith(".") \
            or any(c in _NOT_IN_A_NAME or unicodedata.category(c) in ("Cc", "Cf", "Cs", "Zl", "Zp") for c in name):
        raise UploadRefused(BAD_NAME, "name")
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise UploadRefused(LONG_NAME, "name")
    if _DEVICE_NAME.match(name.split(".", 1)[0].rstrip()):
        raise UploadRefused(BAD_NAME, "name")
    if os.path.splitext(name)[1].lower() not in PIN_SUFFIXES:
        raise UploadRefused(PIN_KIND, "kind")
    return name


def save(library: Any, raw_name: Any, stream: BinaryIO, declared: int | None) -> dict:
    """Keep one upload. ``{name, path, size, replaced, pinned, line}``, or :class:`UploadRefused` with nothing changed."""
    name = clean_name(raw_name)
    if declared is not None and declared > UPLOAD_MAX:
        raise UploadRefused(TOO_BIG, "too_big", 413)
    data = library.data_folder
    folder, state = data / FOLDER, library.state
    dest = folder / name
    problem = file_problem(state, dest, data=data)   # before a byte is read. Not there yet is fine; anything else is not
    if problem and problem != PIN_GONE:
        raise UploadRefused(problem, "place")
    folder.mkdir(parents=True, exist_ok=True)
    if not library.uploads_place():                  # a link planted in its place, even one to a folder beside it
        raise UploadRefused(LINKED_PLACE, "place")
    replaced = os.path.lexists(dest)
    if replaced and not library.copy_place(canon(dest)):
        raise UploadRefused(HERS, "hers", 409)
    part = folder / f"{secrets.token_hex(8)}.part"   # the bot's walk reads no .part, so a half file is never read
    got = 0
    try:
        with open(part, "xb") as out:
            while True:
                chunk = stream.read(min(_CHUNK, UPLOAD_MAX + 1 - got))
                if not chunk:
                    break
                got += len(chunk)
                if got > UPLOAD_MAX:
                    raise UploadRefused(TOO_BIG, "too_big", 413)
                out.write(chunk)
        if not got:
            raise UploadRefused(EMPTY, "empty")
        os.replace(part, dest)                       # whole, or not at all
    finally:
        part.unlink(missing_ok=True)
    problem = file_problem(state, dest, data=data)   # again, on what is really there now
    if problem:
        dest.unlink(missing_ok=True)
        raise UploadRefused(problem, "place")
    try:
        library.record_copy(dest)
    except OSError:                                  # not said to be saved, so it isn't left unrecorded
        if not replaced:
            dest.unlink(missing_ok=True)
        raise
    pinned = canon(dest) in [canon(p) for p in library.pinned()]
    return {"name": name, "path": str(dest), "size": got, "replaced": replaced, "pinned": pinned,
            "line": f"{name} replaces your earlier copy on your bot." if replaced else f"{name} is saved on your bot."}


__all__ = ["FOLDER", "UPLOAD_MAX", "UploadRefused", "clean_name", "save"]
