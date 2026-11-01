"""Yes cards (lanes L1 and L4): one ``ConsentStore`` keeps every card the Core raises and the owner's answer.

Interface: ``ConsentStore(path, bus)`` with lane L1's ``request(scope, card, floor) -> consent_id``,
``answer(consent_id, answer, factor, pin, by) -> record``, ``get``, ``list(status)``,
``wait(consent_id, timeout_s)``, ``on_answer(fn)``; and lane L4's ``ask(kind, target, covers, ...)``,
``granted(consent_id)``, ``live(kind)``, ``revoke``, ``on_granted(fn)``, ``find_send``, ``find_always``.
``ConsentError`` carries an HTTP status, a closed ``code`` and plain words.
``card_view(card, floor)`` is the one plain view of a card (CLI, ``GET /v1/consent``, the Desktop).
"""

from .shown import view as card_view
from .store import (CARD_TTL, FLOOR_KINDS, KINDS, STATUSES, VERIFY_PIN, ConsentError, ConsentRefused, ConsentStore, canon,
                    card_sha, sha)

__all__ = ["CARD_TTL", "ConsentError", "card_view", "ConsentRefused", "ConsentStore", "FLOOR_KINDS", "KINDS", "STATUSES", "VERIFY_PIN",
           "canon", "card_sha", "sha"]
