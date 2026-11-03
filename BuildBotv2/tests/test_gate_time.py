"""The floor's time grows with the text, not its square, and what it finds does not change.

``token_rules._WRAP`` tried a long run of token characters from every position in it. It now tries a run once, from
its start. The email detector does the same per ``@`` and the GPS pattern can't split spaces many ways (D156). These
tests show the matches are the same, the time is about linear, and the Core stays responsive.
"""

from __future__ import annotations

import base64
import random
import re
import threading
import time

import pytest

from puffpai_core.zone1 import classify, detectors, token_rules
from z1kit import canary_key

OLD_WRAP = re.compile(r"[" + token_rules._T + r"=]{6,}[ \t]*\\?\r?\n")
OLD_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
OLD_GPS = re.compile(r"(?<![\d.])(-?\d{1,2}\.\d{3,})\s*°?\s*[NS]?\s*[,;\s]\s*"
                     r"(-?\d{1,3}\.\d{3,})\s*°?\s*[EW]?(?![\d.])")


def b64(n: int, seed: int = 7) -> str:
    return base64.b64encode(random.Random(seed).randbytes(n * 3 // 4 + 3)).decode()[:n]


def b64url(n: int) -> str:
    return b64(n).replace("+", "-").replace("/", "_")


def wrapped(n: int) -> str:
    text = b64(n)
    return "\n".join(text[i:i + 76] for i in range(0, n, 76))


def prose(n: int) -> str:
    rng = random.Random(7)
    words = "the river ran low that summer and she kept the letters under a loose board".split()
    return " ".join(rng.choice(words) for _ in range(n // 4))[:n]


def repeat(unit: str, n: int) -> str:
    return (unit * (n // len(unit) + 1))[:n]


# -- the same matches ----------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "abcde\n", "abcdef\n", "abcdefg\n", "=====\n", "======\n", "=======\n", "abc===\n",
    "abcdef  \n", "abcdef\t \n", "abcdef\\\n", "abcdef \\\r\n", "abcdef\r\n", "abcdef\r", "abcdef \\ \n", "abcdef\\\\\n",
    "abcdef\nghijkl\n", "abcdef\n=\n======\n", "abcdef\n\nghijkl", "ab.abcdef\n", "abcde.abcdef\n", "abcdef.\n", "",
])
def test_wrap_same_matches_by_hand(text):
    assert [m.span() for m in token_rules._WRAP.finditer(text)] == [m.span() for m in OLD_WRAP.finditer(text)]


def test_wrap_same_matches_generated():
    rng = random.Random(148)
    letters = "aZ09_-+/="
    joins = ["\n", "\r\n", " \n", "\t\n", "\\\n", " \\\r\n", "  \t\\\n", "\r", "\\", " ", ".", "", "\n\n", "\\ \n", "\\\\\n"]
    matched = 0
    for _ in range(4000):
        parts = []
        for _ in range(rng.randint(1, 8)):
            size = rng.choice([0, 1, 4, 5, 6, 7, 8, 30])
            parts.append("=" * size if rng.random() < 0.15 else "".join(rng.choices(letters, k=size)))
            parts.append(rng.choice(joins))
        text = "".join(parts)
        want = [m.span() for m in OLD_WRAP.finditer(text)]
        assert [m.span() for m in token_rules._WRAP.finditer(text)] == want, repr(text)
        matched += len(want)
    assert matched > 1000                     # the text did reach the regex's matches


@pytest.mark.parametrize("text", [
    "a@b.cc-d@e.ff", "a@b.cc+d@e.ff", "a@b.cc.d@e.ff", "a@b.co@c.de", "a@@b.co", "@x.co", "a@b", "a@b.c", "-foo@x.co",
    ".a@b.co", "a.b@c.de", "_a@b.co", "é-foo@x.co", "x@a.b_c.de", "foo%bar+baz@ex-ample.org.", "river-low@ a@b.co", "",
    "a@b.co a@b.co", "a@b.cc-", "a-@b.co", "%@b.co", "%a@b.co", "a@b.co%c@d.ef",
])
def test_email_same_matches_by_hand(text):
    assert [m.span() for m in detectors._emails(text)] == [m.span() for m in OLD_EMAIL.finditer(text)]


def test_email_same_matches_generated():
    rng = random.Random(156)
    tokens = ["a", "bc", "Z9", "_", ".", "-", "+", "%", "@", "@", " ", "\n", "co", "b.cc", "x.org", "é", "/", ":", "<", ".."]
    matched = 0
    for _ in range(40_000):
        text = "".join(rng.choices(tokens, k=rng.randint(2, 16)))
        want = [m.span() for m in OLD_EMAIL.finditer(text)]
        assert [m.span() for m in detectors._emails(text)] == want, repr(text)
        matched += len(want)
    assert matched > 500


@pytest.mark.parametrize("text", [
    "12.345 67.890", "12.345,67.890", "12.345;\n67.890", "12.345N67.890", "12.345 N67.890", "12.345N 67.890",
    "12.345° 67.890", "12.345 °67.890", "12.345°N, 67.890°E", "12.345 N , 67.890", "12.345 S 67.890 W",
    "12.345  x", "12.345 , , 67.890", "12.345°° 67.890", "12.345 N S 67.890", "12.345 67.890 1", "12.345 67.890.1",
    "1.2.345 67.890", "-12.345 -67.890", "12.345 67.890", "12.34 67.890", "12.345 67.89",
])
def test_gps_same_matches_by_hand(text):
    new = detectors._GPS_PATTERN
    assert [(m.span(), m.groups()) for m in new.finditer(text)] == [(m.span(), m.groups()) for m in OLD_GPS.finditer(text)]


def test_gps_same_matches_generated():
    rng = random.Random(156)
    tokens = ["12.345", "-45.6789", "1.23", "123.456", "9.999", " ", "  ", "\t", "\n", "°", "N", "S", "E", "W", ",", ";", "x", ".",
              "7", "-"]
    matched = 0
    for _ in range(40_000):
        text = "".join(rng.choices(tokens, k=rng.randint(2, 12)))
        want = [(m.span(), m.groups()) for m in OLD_GPS.finditer(text)]
        assert [(m.span(), m.groups()) for m in detectors._GPS_PATTERN.finditer(text)] == want, repr(text)
        matched += len(want)
    assert matched > 500


# -- time against size ---------------------------------------------------------------------------

SHAPES = {
    "base64 one line": b64,
    "base64 wrapped": wrapped,
    "hex": lambda n: random.Random(7).randbytes(n // 2).hex(),
    "one letter": lambda n: "a" * n,
    "digits": lambda n: repeat("0123456789", n),
    "with equals": lambda n: repeat("ab=", n),
    "keyword and run": lambda n: "token: " + b64(n),
    "many keywords": lambda n: repeat("api key token ", n),
    "random runs on one line": lambda n: " ".join(b64(40, seed=i) for i in range(n // 41)),
    "code hits": lambda n: repeat("code 482913 ", n),
    "prose": prose,
    "long lines": lambda n: "\n".join(b64(n // 4, seed=i) for i in range(4)),
    "base64url": lambda n: b64url(n),
    "dash-joined": lambda n: repeat("river-low-summer-", n),
    "dotted words": lambda n: repeat("river.low.", n),
    "plus joined": lambda n: repeat("a+", n),
    "float and 64 spaces": lambda n: repeat("12.345" + " " * 64 + "x\n", n),
    "float and 400 spaces": lambda n: repeat("12.345" + " " * 400 + "x\n", n),
}


def clock(fn, text: str) -> float:
    a = time.perf_counter()
    fn(text)
    return time.perf_counter() - a


def grows_ok(fn, make, small: int = 25_000) -> tuple[bool, float, float]:
    # 4x the size under about 8x the time, with a floor so tiny times can't flake; one retry, this PC is in use.
    a, b = make(small), make(small * 4)
    for _ in range(2):
        t1, t4 = clock(fn, a), clock(fn, b)
        if t4 < 8 * max(t1, 0.02):
            return True, t1, t4
    return False, t1, t4


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_time_grows_with_the_size(shape):
    ok, t1, t4 = grows_ok(classify.classify_text, SHAPES[shape])
    assert ok, f"{shape}: 25 KB {t1:.3f}s, 100 KB {t4:.3f}s"


def test_many_spaces_after_one_float_take_no_time():
    """It grows with the spaces per number, not the size of the text, so growth with the size can't show it. The old pattern
    took 4.5 s at 256 spaces, about 19 times as long with each doubling."""
    text = "1.234" + " " * 2000 + "x"
    assert clock(lambda t: list(detectors._GPS_PATTERN.finditer(t)), text) < 0.5


def test_a_long_line_isnt_walked_for_each_run():
    """Many runs on one line: the label check reads a little way back, not back to the line's start."""
    text = "word " * 400_000 + "a1B2" * 10
    start = len(text) - 40
    # 300 reads: about 0.2 s with the old walk, 0.001 s now
    assert clock(lambda t: [token_rules._labelled_hash(t, start, len(t)) for _ in range(300)], text) < 0.05


@pytest.mark.parametrize("shape", ["base64 one line", "hex", "keyword and run"])
def test_400_kb_has_a_generous_bound(shape):
    text = SHAPES[shape](400_000)
    assert clock(classify.classify_text, text) < 20 or clock(classify.classify_text, text) < 20


# -- what is found -------------------------------------------------------------------------------

def held_whole(text: str, value: str) -> bool:
    at = text.index(value)
    return any(g.start <= at and at + len(value) <= g.end and g.secret for g in classify.classify_text(text).groups())


def test_a_key_in_400_kb_of_prose():
    key = canary_key()
    assert held_whole(prose(200_000) + " " + key + " " + prose(200_000), key)


def test_a_key_after_a_long_base64_line():
    key = canary_key()
    assert held_whole(b64(400_000) + "\n" + key + "\n", key)


def test_a_wrapped_key_after_a_long_base64_line_and_a_blank_line():
    key = canary_key()
    text = b64(400_000) + "\n\n" + key[:20] + "\n" + key[20:] + "\n"
    assert held_whole(text, key[:20]) and held_whole(text, key[20:])


def test_a_wrapped_key_right_under_a_long_base64_line_keeps_its_tail():
    key = canary_key()
    assert held_whole(b64(400_000) + "\n" + key[:20] + "\n" + key[20:] + "\n", key[20:])


@pytest.mark.xfail(strict=True, reason="An old limit, the same with the old regex: the line above joins the key into "
                                       "a run over 4,096 characters, so the first part of the key is not held.")
def test_a_wrapped_key_right_under_a_long_base64_line_whole():
    key = canary_key()
    assert held_whole(b64(6_000) + "\n" + key[:20] + "\n" + key[20:] + "\n", key[:20])


def test_run_lengths_stay_as_documented():
    assert any(kind == "random_token" for _, _, kind in token_rules.find(b64(40)))
    assert not any(kind == "random_token" for _, _, kind in token_rules.find(b64(5_000)))


def _mixed_texts() -> list[str]:
    rng = random.Random(148)
    hexes = lambda k: "".join(rng.choices("0123456789abcdef", k=k))  # noqa: E731
    pieces = [lambda: "sk-ant-api03-" + hexes(rng.randint(4, 40)), lambda: b64(rng.randint(5, 60), seed=rng.randint(0, 9999)),
              lambda: "sha256: " + hexes(64), lambda: prose(rng.randint(5, 120)), lambda: "password: ", lambda: "api key ",
              lambda: "=" * rng.randint(1, 9), lambda: "Bearer ", lambda: "TOKEN=", lambda: "code 482913 ",
              lambda: rng.choice(["\n", "\r\n", " \n", "\\\n", " ", "  "])]
    return ["".join(rng.choice(pieces)() for _ in range(rng.randint(5, 40))) for _ in range(200)]


def test_find_is_the_same_as_with_the_old_regex(monkeypatch):
    texts = _mixed_texts()
    new = [token_rules.find(t) for t in texts]
    assert sum(map(len, new)) > 100
    monkeypatch.setattr(token_rules, "_WRAP", OLD_WRAP)
    assert new == [token_rules.find(t) for t in texts]


def test_labelled_hash_reads_what_it_read():
    # The look back stops at a line end and at 40 characters, on a long line and a short one.
    run = "0123456789abcdef" * 4
    for text, labelled in (("sha256: " + run, True), ("sha256:\n" + run, False), ("sha256 " + "x" * 16 + " " + run, True),
                           ("sha256 " + "x" * 40 + " " + run, False), ("y " * 5000 + "sha256: " + run, True),
                           ("sha256: " + "y " * 5000 + run, False)):
        at = text.index(run)
        assert token_rules._labelled_hash(text, at, at + len(run)) is labelled, text[:30]


def test_one_time_codes_are_kept_once():
    text = "verification code 482913 and cvv code 4821 and code 777777"     # two patterns see the first two
    assert [(text[a:b], label) for a, b, label, _ in detectors.code_spans(text)] == [
        ("4821", "financial.card_cvv"), ("482913", "secret.one_time_code"), ("777777", "secret.one_time_code")]


# -- the Core stays responsive -------------------------------------------------------------------

def longest_gap(text: str) -> float:
    longest = [0.0]
    stop = threading.Event()

    def tick():
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(0.01)
            now = time.perf_counter()
            longest[0] = max(longest[0], now - last)
            last = now
    th = threading.Thread(target=tick, daemon=True)
    th.start()
    classify.classify_text(text)
    stop.set()
    th.join(10)
    return longest[0]


@pytest.mark.parametrize("make", [b64, b64url], ids=["base64", "base64url"])
def test_another_thread_keeps_running(make):
    text = make(400_000)
    assert longest_gap(text) < 2 or longest_gap(text) < 2
