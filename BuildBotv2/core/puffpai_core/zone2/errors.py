"""The brain's named states and its one error (D36(d), 03 section 1.4).

Every "it failed" line says the real reason. ``timed_out`` is used only for a
real timeout; a model that is not downloaded is ``not_downloaded``; anything
else is ``error`` with a plain reason.
"""

from __future__ import annotations

NOT_DOWNLOADED = "not_downloaded"
LOADING = "loading"
READY = "ready"
TIMED_OUT = "timed_out"
ERROR = "error"
STATES = (NOT_DOWNLOADED, LOADING, READY, TIMED_OUT, ERROR)


class BrainError(RuntimeError):
    """The model did not answer. ``code`` is a state name, or ``no_tools`` /
    ``no_gate`` / ``not_local`` / ``no_model``; the message is plain words."""

    def __init__(self, message: str, code: str = ERROR) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


__all__ = ["BrainError", "ERROR", "LOADING", "NOT_DOWNLOADED", "READY", "STATES", "TIMED_OUT"]
