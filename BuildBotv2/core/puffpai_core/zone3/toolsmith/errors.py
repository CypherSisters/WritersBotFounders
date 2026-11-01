"""The errors Toolsmith's process and the Core's side of it share (lane L6).

Toolsmith runs in its own process (09 section 3, point 1). When a Core route
it calls can't do what was asked, the route answers 409 with
``{"error": <plain line>, "kind": <kind>, "code": <code>}`` (:func:`to_wire`)
and Toolsmith's link raises the same error again (:func:`from_wire`). Every
``str()`` here is a plain line for the owner.
"""

from __future__ import annotations

from typing import Any


class NotBuilt(RuntimeError):
    """An interface another lane builds is not here yet. ``str()`` is the plain line."""

    def __init__(self, what: str, lane: str) -> None:
        super().__init__(f"{what} is not built yet (lane {lane}).")
        self.what, self.lane = what, lane


class Refused(RuntimeError):
    """A check said no. ``str()`` is the plain reason."""


class CloudError(RuntimeError):
    """A cloud step that did not happen. ``message`` is plain words for her."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


class CloudOffline(CloudError):
    """The provider's host could not be reached: use this computer's model for this try."""


class CloudHeld(CloudError):
    """The build holds (``code``: cap | consent | key | model | provider): the owner decides."""


class CloudWaitVault(CloudError):
    """No key lease and the vault is locked: pause in place until she unlocks."""


class JobHeld(Exception):
    """The job can't go on until the owner decides (``reason`` is the plain line)."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code, self.reason = code, reason


class TryEnded(Refused):
    """The Core ended this try itself (a start-up file changed under it). The next candidate goes on."""


class Unreachable(Refused):
    """The Core sent it but got no answer (lane I15): ``str()`` is the plain line ("That address could not be
    reached: the connection timed out."), ``kind`` the error's class, for the build log only."""

    def __init__(self, said: str, kind: str = "") -> None:
        super().__init__(said)
        self.kind = kind


class LinkError(RuntimeError):
    """The Core did not answer Toolsmith the way its routes promise."""


class CoreUnreachable(LinkError):
    """No answer from the Core at all (its address refused or timed out): a status write tries again."""


_KINDS: dict[str, type] = {"cloud_offline": CloudOffline, "cloud_held": CloudHeld,
                           "cloud_wait_vault": CloudWaitVault, "cloud_error": CloudError}


def to_wire(exc: BaseException) -> dict:
    """The 409 body for one of the errors above."""
    if isinstance(exc, NotBuilt):
        return {"error": str(exc), "kind": "not_built", "code": exc.lane, "what": exc.what}
    if isinstance(exc, TryEnded):
        return {"error": str(exc), "kind": "try_ended", "code": "try_ended"}
    if isinstance(exc, Refused):
        return {"error": str(exc), "kind": "refused", "code": "refused"}
    if isinstance(exc, JobHeld):
        return {"error": exc.reason, "kind": "job_held", "code": exc.code}
    for kind, cls in _KINDS.items():
        if type(exc) is cls:
            return {"error": exc.message, "kind": kind, "code": exc.code}
    raise TypeError(f"not a Toolsmith error: {type(exc).__name__}")


def from_wire(body: Any) -> Exception:
    """The error a 409 body stands for (a body that is not one becomes a LinkError)."""
    body = body if isinstance(body, dict) else {}
    kind, code, said = str(body.get("kind") or ""), str(body.get("code") or ""), str(body.get("error") or "")
    if kind == "not_built":
        exc = NotBuilt(str(body.get("what") or "A part of the Core"), code or "?")
        exc.args = (said or str(exc),)
        return exc
    if kind == "try_ended":
        return TryEnded(said)
    if kind == "refused":
        return Refused(said)
    if kind == "job_held":
        return JobHeld(code, said)
    if kind in _KINDS:
        return _KINDS[kind](code, said)
    return LinkError(said or "The Core did not answer Toolsmith.")


ALL = (NotBuilt, Refused, CloudError, JobHeld)

__all__ = ["ALL", "CloudError", "CloudHeld", "CloudOffline", "CloudWaitVault", "CoreUnreachable", "JobHeld", "LinkError",
           "NotBuilt", "Refused", "TryEnded", "Unreachable", "from_wire", "to_wire"]
