"""A card in `puffpai watch` with no keyboard answers later, with no traceback (D132)."""

import io

from puffpai_core import termchat


def test_a_card_with_no_keyboard_answers_later():
    chat = termchat.Chat(None, out=io.StringIO())

    def no_input(prompt=""):
        raise EOFError
    chat.read_line = no_input
    assert chat.keys("Say Yes?", [("y", "yes")]) == ""
