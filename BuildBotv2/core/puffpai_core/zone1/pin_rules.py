# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/security/vault.py (weak_pattern and helpers :166-510 as-is;
# validate_vault_secret :818 and normalize_recovery_code :804 adapted) and src/puffpai/zone_vault.py
# (generate_recovery_code :744, RECOVERY_ALPHABET :719, as-is)
"""PIN and recovery-code rules (Z1V F6 = X2, F10 = X3).

The vault's own floor for a PIN or passphrase: at least 8 characters, not one
character repeated, not a common password, no runs, steps or keypad shapes.
Spaces are allowed, so a passphrase is a fine choice.

The recovery code is 25 characters from a 25-symbol alphabet with no vowels
and no look-alike characters (about 116 bits), shown once and typed back.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import math
import secrets

# -- patterns a guessing program tries first --------------------------------
#
# Runs are read on rings (digits and letters wrap around: 7890123, xyzab) and
# along the keyboard's rows. "1234567890" passed the old check, because 9 -> 0
# is not a step of one; on the digit ring it is.
_RINGS = ("0123456789", "abcdefghijklmnopqrstuvwxyz")
_ROWS = ("1234567890-=", "qwertyuiop[]\\", "asdfghjkl;'", "zxcvbnm,./",
         "qwertyuiopasdfghjklzxcvbnm")


def _steps(a: str, b: str) -> set:
    """``(sequence, step)`` pairs under which *b* follows *a* by one."""
    found = set()
    for ring in _RINGS:
        i, j = ring.find(a), ring.find(b)
        if i >= 0 and j >= 0:
            if (i + 1) % len(ring) == j:
                found.add((ring, 1))
            if (i - 1) % len(ring) == j:
                found.add((ring, -1))
    for row in _ROWS:
        i, j = row.find(a), row.find(b)
        if i >= 0 and j >= 0 and abs(i - j) == 1:
            found.add((row, j - i))
    return found


def _run_segments(text: str) -> list[int]:
    """Lengths of the runs *text* splits into, greedily from the left."""
    out: list[int] = []
    i = 0
    while i < len(text):
        best = 1
        if i + 1 < len(text):
            for kind in _steps(text[i], text[i + 1]):
                j = i + 1
                while j + 1 < len(text) and kind in _steps(text[j], text[j + 1]):
                    j += 1
                best = max(best, j - i + 1)
        out.append(best)
        i += best
    return out


# Phone keypad and number-pad columns, top to bottom and bottom to top
# (1470258369 and 2580147369 are three column walks).
_PADS = ("147", "2580", "369", "741", "8520", "963")

# Keypad shapes as a class: the two diagonals (the same digits on
# both pads) and two loops, the diamond through the middle of each side (2, 6,
# 8, 4) and the four corners (1, 3, 9, 7), either way round from any key.
# With paths of neighbouring keys (:data:`_KING_RUN`) they count only when the
# WHOLE secret is cut into such pieces (:func:`_keypad_shape`): the X and the
# diamond drawn in another order (9517538426) are a shape; a random PIN with
# one of them inside it is not.
_KEYPAD_LINES = ("159", "357")
_LOOPS = ("2684", "1397")

# Shortest run that counts, by kind. A step of one (on a ring, along a row or
# down a keypad column) counts from three characters. Any other fixed step
# (24680, 97531, acegi, and 7777 as a step of nothing) counts from four: three
# characters counting by some step turn up by chance in one random place in
# ten, which would refuse honest random PINs (about 5% of random
# ten-digit PINs, mostly as "a pattern with a character or two added").
_UNIT_RUN = 3
_STEPPED_RUN = 4


def _cover_steps(a: str, b: str) -> set:
    """Every way *b* can follow *a* inside a run.

    :func:`_steps` (one up or down a ring, one along a row) and one along a
    keypad column, plus ``("step", ring, k)`` for any other fixed step on a
    ring, step 0 included. Only the ``"step"`` kinds need :data:`_STEPPED_RUN`
    characters.
    """
    found = set(_steps(a, b))
    for r, ring in enumerate(_RINGS):
        i, j = ring.find(a), ring.find(b)
        if i >= 0 and j >= 0:
            k = (j - i) % len(ring)
            if k not in (1, len(ring) - 1):
                found.add(("step", r, k))
    for pad in _PADS:
        i, j = pad.find(a), pad.find(b)
        if i >= 0 and j >= 0 and abs(i - j) == 1:
            found.add((pad, j - i))
    return found


def _keypad_steps(a: str, b: str) -> set:
    """The keypad kinds of :func:`_keypad_shape`: a diagonal, a loop (four
    long or more) or a path of neighbouring keys (:data:`_KING_RUN` long)."""
    found = set()
    for line in _KEYPAD_LINES:
        i, j = line.find(a), line.find(b)
        if i >= 0 and j >= 0 and abs(i - j) == 1:
            found.add((line, j - i))
    for loop in _LOOPS:
        i, j = loop.find(a), loop.find(b)
        if i >= 0 and j >= 0 and (j - i) % 4 in (1, 3):
            found.add(("loop", loop, (j - i) % 4))
    return found | _king_steps(a, b)


#: A path on a keypad: each move to a key right next to the last one, across,
#: up, down or corner to corner, turning any way. Seven keys or
#: more count as a run when the whole secret is cut into runs with nothing
#: left over; a path of the whole secret is :func:`_keypad_path`. Seven, not
#: six: the same shapes are caught and fewer random PINs are (measured
#: on 20,000 random PINs: ten digits 1.31% refused in all, twelve digits
#: 0.18%).
_KING_RUN = 7


def _king_steps(a: str, b: str) -> set:
    """``("king", pad)`` for every keypad on which *b* is right next to *a*."""
    found = set()
    for n, pad in enumerate(_KEYPADS):
        if a in pad and b in pad:
            (r1, c1), (r2, c2) = pad[a], pad[b]
            if (r1, c1) != (r2, c2) and max(abs(r1 - r2), abs(c1 - c2)) == 1:
                found.add(("king", n))
    return found


def _cover_and_king(a: str, b: str, king: bool) -> set:
    """:func:`_cover_steps`, plus the keypad kinds (:func:`_keypad_steps`)
    when *king*: only ever for a whole secret, never a piece of one."""
    found = _cover_steps(a, b)
    return found | _keypad_steps(a, b) if king else found


def _keypad_shape(text: str) -> bool:
    """Eight characters or more drawing keypad shapes from end to end: a
    path of neighbouring keys (:func:`_keypad_path`), or cut with nothing
    left over into runs, diagonals, loops and paths (9517538426: the X and
    the diamond, in another order)."""
    if len(text) < 8:
        return False
    return _keypad_path(text) or _run_cover(text, king=True)[0] == 0


def _keypad_path(text: str) -> bool:
    """Eight digits or more, every move to a key right next to the last one
    on one keypad (any turns): a path through neighbouring keys. About 0.2%
    of random ten-digit PINs are one (measured)."""
    if len(text) < 8 or not text.isdigit():
        return False
    kinds = None
    for a, b in zip(text, text[1:]):
        here = {k for k in _king_steps(a, b)}
        kinds = here if kinds is None else kinds & here
        if not kinds:
            return False
    return True


def _run_cover(text: str, king: bool = False) -> tuple[int, int]:
    """How few characters are left over when *text* is cut into runs.

    A run is characters each following the last by the same kind of step
    (:func:`_cover_steps`), at least :data:`_UNIT_RUN` long for a step of one
    and :data:`_STEPPED_RUN` long for any other step. Returns
    ``(left, left_with_long)``: the fewest characters not in any run, and the
    fewest when at least one run is one character longer than its shortest (a
    large number when there is no such cut). Exact, not greedy, so a run
    cannot hide by lending its first character to the run before it.
    """
    n = len(text)
    never = n + 1000
    left = [never] * (n + 1)
    left_long = [never] * (n + 1)
    left[0] = 0
    for i in range(n):
        if left[i] >= never:
            continue
        left[i + 1] = min(left[i + 1], left[i] + 1)
        left_long[i + 1] = min(left_long[i + 1], left_long[i] + 1)
        if i + 1 >= n:
            continue
        for kind in _cover_and_king(text[i], text[i + 1], king):
            shortest = (_KING_RUN if kind[0] == "king" else _STEPPED_RUN
                        if kind[0] in ("step", "loop") else _UNIT_RUN)
            j = i + 1
            while j + 1 < n and kind in _cover_and_king(text[j], text[j + 1], king):
                j += 1
                end = j + 1  # text[i:end] is a run of length end - i
                if end - i < shortest:
                    continue
                left[end] = min(left[end], left[i])
                left_long[end] = min(left_long[end], left_long[i])
                # A path of neighbouring keys is not "a run longer than its
                # shortest" (too many random PINs hold one with two over).
                if end - i > shortest and kind[0] != "king":
                    left_long[end] = min(left_long[end], left[i])
    return left[n], left_long[n]


# Where each digit sits on a phone keypad (123 on top, 0 under 8) and on a
# computer's number pad (789 on top, the wide 0 under 2 and under 1).
_KEYPADS = (
    {"1": (0, 0), "2": (0, 1), "3": (0, 2), "4": (1, 0), "5": (1, 1),
     "6": (1, 2), "7": (2, 0), "8": (2, 1), "9": (2, 2), "0": (3, 1)},
    {"7": (0, 0), "8": (0, 1), "9": (0, 2), "4": (1, 0), "5": (1, 1),
     "6": (1, 2), "1": (2, 0), "2": (2, 1), "3": (2, 2), "0": (3, 1)},
    {"7": (0, 0), "8": (0, 1), "9": (0, 2), "4": (1, 0), "5": (1, 1),
     "6": (1, 2), "1": (2, 0), "2": (2, 1), "3": (2, 2), "0": (3, 0)},
)


def _keypad_strokes(text: str, pad: dict) -> int:
    """How many straight strokes draw *text* on *pad*, or 0 when none do.

    Each move goes to a key in the same row, column or diagonal, at most two
    keys away (5 to 0 on a phone passes over 8). A stroke is moves in one
    direction; the corner key ends one stroke and starts the next.
    """
    directions = []
    for a, b in zip(text, text[1:]):
        (r1, c1), (r2, c2) = pad[a], pad[b]
        dr, dc = r2 - r1, c2 - c1
        if (dr, dc) == (0, 0) or max(abs(dr), abs(dc)) > 2:
            return 0
        if dr and dc and abs(dr) != abs(dc):
            return 0
        directions.append(((dr > 0) - (dr < 0), (dc > 0) - (dc < 0)))
    return 1 + sum(1 for x, y in zip(directions, directions[1:]) if x != y)


def _keypad_walk(text: str, part: bool = False) -> bool:
    """True when the digits draw a shape on a phone keypad or number pad.

    Snakes, spirals, the square around the edge, an X (1478963250,
    1236987410, 1593574862): every move to a key in line with the last, in
    at most ``len - 3`` strokes (so at least two moves carry straight on)
    and never more than seven. Six digits or more. About 0.2% of random
    ten-digit PINs draw such a shape and almost no longer ones (tested),
    against about 4% of ten-digit PINs if any number of turns were allowed.

    *part* (a PIN's middle, once a character or two is taken off): eight
    digits or more and one stroke fewer, so a shape with a character added
    (1478963250x) is still seen while random PINs almost never are (about
    0.03% more of them).
    """
    shortest, slack, most = (8, 4, 6) if part else (6, 3, 7)
    if len(text) < shortest or not text.isdigit():
        return False
    limit = min(len(text) - slack, most)
    for pad in _KEYPADS:
        strokes = _keypad_strokes(text, pad)
        if strokes and strokes <= limit:
            return True
    return False


#: The patterns the vault's own floor refuses (every path to a new secret
#: comes through it): one run, runs back to back, digits counting by a fixed
#: step, or a shape drawn on the keypad.
FLOOR_PATTERNS = frozenset({"run", "runs", "digit_step", "keypad"})


def weak_pattern(secret: str, strict: bool = False) -> str:
    """The pattern that makes *secret* guessable, or '' when there is none.

    ``run``: one run up or down a ring or a keyboard row (1234567890,
    0987654321, 7890123456, xyzabcde, qwertyuiop). ``runs``: the whole
    secret cut into runs, three or more long counting by one on a ring,
    along a keyboard row or down a keypad column, or four or more long
    counting by any other fixed step (abcd1234, 1234554321, 123abc456,
    123456123456123456, qwertqwertqwert, 1234512345123, 2468013579,
    8642097531, 1470258369, 2580147369, 7418529630, acegi13579), or with at
    most two characters left over when one run is longer than its shortest
    (abcdefgh1, 6210401234). ``repeat``: a block of one to three characters
    said again and again, a part-block at the end included (abcabcabc,
    121212, abcabcab). ``digit_step``: digits counting by any fixed step,
    wrapping (1357913579, 9630741852). ``keypad``: digits drawing a shape on
    a phone keypad or number pad (:func:`_keypad_walk`). Case does not
    matter. A passphrase with spaces is never one of these.

    *strict* is for a part of a PIN (the middle once a character or two is
    taken off, or every other character): there the whole part must be the
    pattern, with no characters left over and a keypad shape held to fewer
    turns, because a few random characters around something that is only
    nearly a pattern make no pattern at all.

    The vault's own floor refuses :data:`FLOOR_PATTERNS`; the wizard's PIN
    rule refuses every kind.
    """
    text = str(secret or "").lower()
    if len(text) < 3:
        return ""
    length = len(text)
    for block in range(1, min(3, length // 2) + 1):
        if all(text[k] == text[k % block] for k in range(length)):
            return "repeat"
    if text.isdigit() and length >= 6:
        step = (int(text[1]) - int(text[0])) % 10
        if all((int(text[k + 1]) - int(text[k])) % 10 == step
               for k in range(length - 1)):
            return "digit_step"
    segments = _run_segments(text)
    if len(segments) == 1:
        return "run"
    left, left_long = _run_cover(text)
    if left == 0 or (not strict and left_long <= 2):
        return "runs"
    if _keypad_walk(text, part=strict):
        return "keypad"
    if not strict:
        if _keypad_shape(text) or _keypad_with_marks(text):
            return "keypad"
        block = _said_twice_block(text)
        if block:
            kind = weak_pattern(block)
            if kind in FLOOR_PATTERNS:
                return kind
    return ""


def _keypad_with_marks(text: str) -> bool:
    """A keypad shape with one or two other characters put in anywhere.

    Without this, 1478963k250, 14789k63250,
    123698k7410 and 15935k74862 make real vaults. The digits are read on
    their own when there are at least six of them and at most two other
    characters, by the same shape rule as a PIN of digits only (so a random
    PIN is caught about as rarely as a random PIN of digits).
    """
    digits = "".join(ch for ch in text if ch.isdigit())
    others = len(text) - len(digits)
    if not 0 < others <= 2 or len(digits) < 6:
        return False
    # Any keypad shape, drawn in any order (9517538426q)
    return _keypad_walk(digits) or _keypad_shape(digits)


def _said_twice_block(text: str) -> str:
    """The block of four or more that *text* says again and again, or ''."""
    n = len(text)
    for block in range(4, n // 2 + 1):
        if all(text[k] == text[k % block] for k in range(n)):
            return text[:block]
    return ""



_MIN_SECRET_LENGTH = 8
_COMMON_SECRETS = frozenset({
    "password", "passw0rd", "password1", "12345678", "123456789", "1234567890",
    "87654321", "qwertyui", "iloveyou", "trustno1", "letmein1", "welcome1",
    "abcd1234", "11111111", "00000000", "aaaaaaaa", "changeme", "adminadmin",
    "1qaz2wsx", "1q2w3e4r", "1q2w3e4r5t", "zaq12wsx", "qazwsxedc",
    "qwertyuiop", "asdfghjkl", "zxcvbnm1",
})


def validate_vault_secret(secret: str) -> tuple[bool, str]:
    """The vault's floor for a PIN or passphrase. ``(ok, plain reason)``; the reason never repeats the secret."""
    if not isinstance(secret, str) or not secret:
        return False, "A PIN or passphrase is required."
    if len(secret) < _MIN_SECRET_LENGTH:
        return False, (f"Use at least {_MIN_SECRET_LENGTH} characters. "
                       "A short PIN can be worked through by a program that copied your vault.")
    if len(set(secret)) == 1:
        return False, "A PIN can't be one character repeated."
    if secret.lower() in _COMMON_SECRETS:
        return False, "That one is on every published list of common passwords."
    step = ord(secret[1]) - ord(secret[0])
    if step in (1, -1) and all(ord(secret[i + 1]) - ord(secret[i]) == step for i in range(len(secret) - 1)):
        return False, "A PIN can't be a straight run of characters."
    if weak_pattern(secret) in FLOOR_PATTERNS:
        return False, ("A PIN can't count up or down, by ones or by bigger steps, run along the "
                       "keyboard or keypad, even when it wraps around, or draw a shape on the keypad.")
    return True, ""


RECOVERY_ALPHABET = "23456789CDFHJKLMNPQRTVWXY"   # 25 symbols
RECOVERY_GROUPS = 5
RECOVERY_GROUP_LENGTH = 5
RECOVERY_CODE_ENTROPY_BITS = round(RECOVERY_GROUPS * RECOVERY_GROUP_LENGTH * math.log2(len(RECOVERY_ALPHABET)))


def generate_recovery_code() -> str:
    """``XXXXX-XXXXX-XXXXX-XXXXX-XXXXX``. A secret: shown once, never stored in the clear."""
    groups = ["".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_GROUP_LENGTH))
              for _ in range(RECOVERY_GROUPS)]
    return "-".join(groups)


def normalize_recovery_code(code: str) -> str:
    """Uppercase, letters and digits only, so dashes and spaces copied off a card don't matter."""
    if not isinstance(code, str):
        return ""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def same_recovery_code(shown: str, typed: str) -> bool:
    """Constant-time compare after normalising. Empty never matches."""
    a, b = normalize_recovery_code(shown), normalize_recovery_code(typed)
    return bool(a and b) and secrets.compare_digest(a, b)


__all__ = ["FLOOR_PATTERNS", "RECOVERY_CODE_ENTROPY_BITS", "generate_recovery_code",
           "normalize_recovery_code", "same_recovery_code", "validate_vault_secret", "weak_pattern"]
