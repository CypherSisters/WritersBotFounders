"""The machine wrap off Windows (D132): one wrap key per user, in a file.

On Windows the anchors and the unattended tier use DPAPI and never reach this
module. Everywhere else they wrap with AES-256-GCM under the 32 random bytes in
``paths.data_root() / "machine.key"``. That folder is outside any one bot folder,
so a copy of a bot folder does not carry its own unwrap key.

Each caller passes its own ``purpose`` (its ``_ENTROPY``) as associated data, so
the anchor's wrap and Zone 1's wrap can't be swapped for each other.

The file is made once, and only when it is not there at all. Anything wrong with
one that is there means "not available", with a plain reason, fail closed. It is
never replaced, because that would orphan every wrapped key. Group or other bits
on it are taken off rather than refused, and so are those on the folder, which a
bot folder may have created first with the usual mode. The folder may be reached
through a link (a data folder moved to a bigger disk) but must belong to this user.
A second hard link to the file is not refused: a crash between linking and removing
the temp file leaves one, and only this user could make one.

HONEST LIMIT, the same as DPAPI CurrentUser: any program running as this user can
read the file and unwrap everything. This stops a stray edit, another user, and a
copy of one bot folder. It does not stop a program written to target PuffPAI. A
backup that copies the whole home folder carries the wrap key too, so whoever holds
that backup can unwrap what is in it. Deleting the file loses every wrap: anchored
files read as damaged and ticked keys wait for the PIN.

The keyring is deliberately not used: the secret service isn't there on a headless
box, and it prompts.

Tests: the suite's guard replaces :func:`_wrap_key`, so nothing here reads or makes
a real key unless a test turns it back on with its own HOME.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
from pathlib import Path

from . import paths

KEY_FILE = "machine.key"
_KEY_SIZE = 32
_NONCE_SIZE = 12
_TAG_SIZE = 16
_PREFIX = b"pmf1:"

_LEFT = "It was left as it is, because replacing it would lose every key wrapped with it."
_THE_FILE = f"The file {KEY_FILE} in your PuffPAI folder"


class MachineFileError(Exception):
    """The wrap key can't be used, or a blob was refused. The message is a plain reason, with no path and no value."""


def key_path() -> Path:
    return paths.data_root() / KEY_FILE


def _open_folder(folder: Path) -> int:
    try:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        raise MachineFileError(f"Your PuffPAI folder could not be opened ({exc.strerror}).") from None
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid():
            raise MachineFileError("Your PuffPAI folder belongs to another user, so its machine key was not used.")
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            os.fchmod(fd, mode & 0o700)
    except OSError as exc:
        os.close(fd)
        raise MachineFileError(f"Your PuffPAI folder could not be made private ({exc.strerror}).") from None
    except BaseException:
        os.close(fd)
        raise
    return fd


def _read_key(folder_fd: int) -> bytes | None:
    """The key, or None when there is no file at all (the only case that may make one). O_NONBLOCK: a pipe never hangs."""
    try:
        fd = os.open(KEY_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EMLINK):
            raise MachineFileError(f"{_THE_FILE} is a link, so it was not used. {_LEFT}") from None
        raise MachineFileError(f"{_THE_FILE} could not be read ({exc.strerror}). {_LEFT}") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise MachineFileError(f"{_THE_FILE} is not a regular file, so it was not used. {_LEFT}")
        if st.st_uid != os.geteuid():
            raise MachineFileError(f"{_THE_FILE} belongs to another user, so it was not used. {_LEFT}")
        if st.st_size != _KEY_SIZE:
            raise MachineFileError(f"{_THE_FILE} is the wrong size, so it was not used. {_LEFT}")
        mode = stat.S_IMODE(st.st_mode)
        if mode & 0o077:
            os.fchmod(fd, mode & 0o700)
            if stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:      # a disk that ignores modes: it can't be private here
                raise MachineFileError(f"{_THE_FILE} could not be made private on this disk. {_LEFT}")
        data = b""
        while len(data) <= _KEY_SIZE:
            part = os.read(fd, _KEY_SIZE + 1 - len(data))
            if not part:
                break
            data += part
    except OSError as exc:
        raise MachineFileError(f"{_THE_FILE} could not be read ({exc.strerror}). {_LEFT}") from None
    finally:
        os.close(fd)
    if len(data) != _KEY_SIZE:
        raise MachineFileError(f"{_THE_FILE} is the wrong size, so it was not used. {_LEFT}")
    return data


def _make_key(folder_fd: int) -> None:
    """Write a temp in the same folder, then link it to the name. A link never overwrites, so of two Cores
    starting at once one wins, the other reads the winner's file, and neither sees a half-written one."""
    tmp = f"{KEY_FILE}.{secrets.token_hex(8)}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder_fd)
        try:
            data = memoryview(secrets.token_bytes(_KEY_SIZE))
            while data:
                data = data[os.write(fd, data):]
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.link(tmp, KEY_FILE, src_dir_fd=folder_fd, dst_dir_fd=folder_fd)
        except FileExistsError:
            pass                                                # another Core got there first: its key is the key
        finally:
            os.unlink(tmp, dir_fd=folder_fd)
        os.fsync(folder_fd)
    except OSError as exc:
        raise MachineFileError(f"The machine key could not be made in your PuffPAI folder ({exc.strerror}).") from None


def _wrap_key() -> bytes:
    """The 32-byte wrap key, made if there is none. Raises :class:`MachineFileError` with the plain reason."""
    if os.name != "posix":
        raise MachineFileError("The machine key file is not used on this computer.")
    try:
        path = key_path()
    except RuntimeError:                                        # no home folder to be found
        raise MachineFileError("This computer's home folder could not be found, so there is no machine key.") from None
    folder_fd = _open_folder(path.parent)
    try:
        key = _read_key(folder_fd)
        if key is None:
            _make_key(folder_fd)
            key = _read_key(folder_fd)
        if key is None:
            raise MachineFileError(f"{_THE_FILE} went away as soon as it was made.")
        return key
    finally:
        os.close(folder_fd)


def available() -> bool:
    return why_not() == ""


def why_not() -> str:
    """Empty when the wrap key can be used (making it if there is none), else the plain reason."""
    try:
        _wrap_key()
    except MachineFileError as exc:
        return str(exc)
    return ""


def protect(data: bytes, purpose: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _wrap_key()
    nonce = secrets.token_bytes(_NONCE_SIZE)
    return _PREFIX + nonce + AESGCM(key).encrypt(nonce, bytes(data), purpose)


def unprotect(blob: bytes, purpose: bytes) -> bytes:
    """The wrapped bytes. A wrong key, a wrong purpose or a damaged blob raises, as DPAPI's refusal does."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    blob = bytes(blob)
    if not blob.startswith(_PREFIX) or len(blob) < len(_PREFIX) + _NONCE_SIZE + _TAG_SIZE:
        raise MachineFileError("This wrapped key is damaged, or was not made here.")
    key = _wrap_key()
    start = len(_PREFIX) + _NONCE_SIZE
    try:
        return AESGCM(key).decrypt(blob[len(_PREFIX):start], blob[start:], purpose)
    except InvalidTag:
        raise MachineFileError("This wrapped key doesn't open with this computer's machine key.") from None


class FileWrap:
    """The file wrap for one purpose, with the ``available``/``protect``/``unprotect`` of a machine wrap.

    ``error`` is what its caller already catches for a DPAPI refusal. ``reason`` is the last plain reason, or ""."""

    def __init__(self, purpose: bytes, error: type[Exception] = MachineFileError) -> None:
        self.purpose = purpose
        self.error = error
        self.reason = ""

    def available(self) -> bool:
        self.reason = why_not()
        return not self.reason

    def _do(self, fn, data: bytes) -> bytes:
        try:
            return fn(data, self.purpose)
        except MachineFileError as exc:
            self.reason = str(exc)
            raise self.error(self.reason) from None

    def protect(self, data: bytes) -> bytes:
        return self._do(protect, data)

    def unprotect(self, data: bytes) -> bytes:
        return self._do(unprotect, data)


__all__ = ["FileWrap", "KEY_FILE", "MachineFileError", "available", "key_path", "protect", "unprotect", "why_not"]
