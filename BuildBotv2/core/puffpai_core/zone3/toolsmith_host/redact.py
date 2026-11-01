"""The key scrub (lane I15 fix), shared with Toolsmith's own process (lane I18a): it lives in
``toolsmith.redact`` so Toolsmith's log lines use the same helpers without importing the Core's side."""

from ..toolsmith.redact import REMOVED, pieces, scrub, scrub_text

__all__ = ["REMOVED", "pieces", "scrub", "scrub_text"]
