# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/artifact_review.py (as-is: _FILLER.._MODEL_ORDER_RE, fold_for_orders, fold_label, _MODEL_ORDER_MORE_RE, model_orders)
# Harvested from PuffPAIBuildBot@63c8a037:src/puffpai/drawbridge.py (adapted: _ORDER_RE, instruction_shaped, readme_block, ai_line, hide_ai_lines, hide_ai_lines_in)
"""Drawbridge's text rules: words aimed at an AI, in an outside project's text.

Fixed patterns, no model. Two uses (09 section 3, point 4):
- :func:`hide_ai_lines` / :func:`hide_ai_lines_in`: lines in READMEs and tool
  answers that read like instructions to an AI are replaced by
  :data:`HIDDEN_LINE` before any model sees them. The project goes on.
- :func:`readme_block`: clearly hostile text (turning a model against its
  instructions, asking for keys to be sent away, words for an AI hidden in
  invisible characters or an HTML comment) stops the project. The bare words
  "system prompt" are hidden, with the lines under a heading of only those
  words, but stop nothing in plain text (D97): an AI app's docs have to talk
  about one. An order aimed at one still stops it. Drawbridge turns such a hit
  in a file under ``docs/`` into a hold under the owner's Normal setting, when
  :func:`hide_ai_lines` really takes it out (D105).
- :func:`instruction_shaped`: a field the owner reads (a manifest's name,
  description, consent or step text) must not hold orders at all.

Install lines (``git clone``, ``make``, ``sudo make install``, ``curl | sh``)
are ordinary README words and never count. Honest limit (v1 says the same):
words written in a way these patterns do not know (another language,
reworded) are not seen.
"""

from __future__ import annotations

import html
import re
import unicodedata
from functools import lru_cache
from typing import Any, Iterator

_FILLER = (r"(?:all|any|the|your|my|of|these|those|each|every|previous|prior|"
           r"above|earlier|system|safety|original|initial|given|current|other)")
_MODEL_ORDER_RE = re.compile(
    r"\b(?:ignore|disregard|forget)\s+"
    r"(?:(?:all|any|the|your|my|of|these|those|each|every)\s+){0,3}"
    r"(?:previous|prior|above|earlier|preceding|foregoing)\b|"
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+|any\s+)?(?:everything|anything|all)\s+"
    r"(?:above|before|else|previous|prior|you)\b|"
    # "ignore the instructions above", "ignore all the rules", "ignore your
    # system instructions": up to four small words, then the thing obeyed.
    r"\b(?:ignore|disregard|forget)\s+(?:" + _FILLER + r"\s+){0,4}"
    r"(?:instructions?|rules|guidelines|guardrails|directions|directives|"
    r"prompts?|programming)\b|"
    # "disregard what you were told", "forget all that came before".
    r"\b(?:ignore|disregard|forget)\s+(?:all\s+|everything\s+|anything\s+)?"
    r"(?:what|that)\s+(?:you\s+(?:were|have\s+been|'ve\s+been|are\s+being)\s+"
    r"(?:told|given|asked|instructed)|(?:came|comes|was\s+(?:said|written))\s+"
    r"(?:before|above|earlier))\b|"
    r"\bsystem\s+prompt\b|"
    r"\bsystem\s+(?:note|notice|message|instructions?|override|command|alert|update)"
    r"\s*[:!\-‐-―]|"
    r"\b(?:important|urgent|attention|note)\s*[:!-]?\s*(?:to\s+)?(?:the\s+)?"
    r"(?:ai|llm|assistant|model|agent|bot)s?"
    r"(?:\s+(?:agents?|assistants?|models?|bots?))?\s*[:!,]|"
    # Speaking to a model: "Hey AI: ...", "To any AI agent: ...".
    r"\b(?:hey|hi|hello|dear)\s+(?:(?:all|any|every|the|you)\s+)?"
    r"(?:ai|llm|language\s+model)s?"
    r"(?:\s+(?:agents?|assistants?|models?|bots?|systems?))?\s*[:!,]|"
    r"\bto\s+(?:(?:all|any|every|each|the)\s+)?(?:ai|llm|language\s+model)s?"
    r"(?:\s+(?:agents?|assistants?|models?|bots?|systems?))?\s*:|"
    r"\b(?:ai|llm|language model|assistant|agent)s?\s+(?:reading|parsing|processing)\s+this\b|"
    r"\bif you are an?\s+(?:ai|llm|language model|assistant|agent|bot)\b|"
    r"\byou are now (?:a|an|the|dan|jailbroken|unrestricted|unfiltered)\b|"
    r"\bfrom now on,? you\b|"
    r"\bnew instructions?\s*:|\bas an ai(?: language)? model\b|"
    r"\b(?:do not|don't|never) (?:tell|inform|warn|alert) (?:the )?(?:user|owner|human)\b|"
    r"^\s*(?:system|assistant)\s*:|<\|(?:im_start|im_end|system|assistant|user)\|>|"
    r"\[/?inst\]|<<\s*/?sys\s*>>|</?\s*system\s*>",
    re.IGNORECASE | re.MULTILINE)

#: Cyrillic and Greek letters that look like Latin ones, read as Latin by the
#: order checks (so a Cyrillic "i" in "ignore" does not hide the word).
_LOOKALIKE = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c",
    "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s",
    "ԁ": "d", "ɡ": "g", "А": "A", "Е": "E", "О": "O",
    "Р": "P", "С": "C", "Х": "X", "І": "I", "Ј": "J",
    "Ѕ": "S", "Н": "H", "М": "M", "Т": "T", "В": "B",
    "К": "K", "ο": "o", "α": "a", "ε": "e", "ι": "i",
    "Ο": "O", "Α": "A", "Ε": "E", "Ι": "I", "Τ": "T",
    "Ν": "N", "Μ": "M", "Κ": "K", "Β": "B", "Ρ": "P",
    "ρ": "p", "τ": "t", "υ": "u", "ν": "v", "κ": "k", "Υ": "Y",
})
#: The same table, for the name checks of the search screen (``toolsmith_host.knowledge``).
LOOKALIKE = _LOOKALIKE


class _CharTable(dict):
    """For ``str.translate``: what *fn* says for each character, kept once worked out. A very long line costs
    little this way."""

    def __init__(self, fn: Any) -> None:
        super().__init__()
        self.fn = fn

    def __missing__(self, code: int) -> Any:
        got = self.fn(chr(code))
        self[code] = got
        return got


_NO_FORMAT = _CharTable(lambda ch: None if unicodedata.category(ch) == "Cf" else ord(ch))
_NO_MARKS = _CharTable(lambda ch: None if unicodedata.category(ch) == "Mn" else ord(ch))


def fold_for_orders(text: str) -> str:
    """*text* as the order checks read it: compatibility forms folded
    (full-width letters become plain ones), invisible format characters
    (zero-width spaces and joiners, direction marks, soft hyphens) dropped,
    and Cyrillic or Greek look-alike letters read as Latin."""
    folded = unicodedata.normalize("NFKC", text)
    return folded.translate(_NO_FORMAT).translate(_LOOKALIKE)


#: Characters that render as blank space but are not ``str.isspace`` and not control characters: the
#: Hangul fillers, the braille blank, the combining grapheme joiner. Read as a space by the label check;
#: hidden for :func:`hidden_char`. The ONE list (lane I14): the bot's name and the persona and You checks use it.
BLANK_CHARS = frozenset({0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800, 0x034F})
_BLANKS = BLANK_CHARS
#: Variation selectors (they change how the character before them looks) and tag characters.
VARIATION_SELECTORS = frozenset([*range(0xFE00, 0xFE10), *range(0xE0100, 0xE01F0)])
TAG_CHARS = frozenset(range(0xE0000, 0xE0080))
_ZWJ = chr(0x200D)


def hidden_char(ch: str, lines: bool = False) -> bool:
    """A character that hides: control, format, private use, surrogate, a blank (:data:`BLANK_CHARS`), a
    variation selector or a tag character; line and paragraph separators too unless *lines*. *lines*: tabs and
    line breaks are allowed."""
    if lines and ch in "\t\n\r":
        return False
    o = ord(ch)
    cat = unicodedata.category(ch)
    return (cat in ("Cc", "Cf", "Co", "Cs") or (not lines and cat in ("Zl", "Zp"))
            or o in BLANK_CHARS or o in VARIATION_SELECTORS or o in TAG_CHARS)


def _is_emoji(ch: str) -> bool:
    """A picture symbol, or a skin tone (an emoji's own modifier)."""
    return unicodedata.category(ch) == "So" or 0x1F3FB <= ord(ch) <= 0x1F3FF


def has_hidden(text: str, lines: bool = False, emoji: bool = False) -> bool:
    """True when *text* has a :func:`hidden_char`. *emoji*: a variation selector right after an emoji, and a
    zero width joiner between two emoji (a family, a heart on fire), are allowed."""
    for i, ch in enumerate(text):
        if emoji and ord(ch) in VARIATION_SELECTORS:
            if i and _is_emoji(text[i - 1]):
                continue
            return True
        if emoji and ch == _ZWJ:
            before = text[i - 2] if i >= 2 and ord(text[i - 1]) in VARIATION_SELECTORS else (text[i - 1] if i else "")
            after = text[i + 1] if i + 1 < len(text) else ""
            if before and after and _is_emoji(before) and _is_emoji(after):
                continue
            return True
        if hidden_char(ch, lines=lines):
            return True
    return False


_BLANK_IS_SPACE = _CharTable(lambda ch: 32 if (ord(ch) in _BLANKS or ch.isspace()
                                               or unicodedata.category(ch) in ("Zs", "Zl", "Zp")) else ord(ch))


def fold_label(text: str) -> str:
    """*text* as the short-label order check reads it.

    :func:`fold_for_orders`, plus: combining marks dropped (an accent on the
    "i" of "ignore" does not hide the word), blank-looking characters and
    every kind of space read as one space, and ``-``, ``_``, ``.``, ``/``,
    ``+`` and ``~`` between letters read as a space ("Ignore-all-previous").
    """
    decomposed = unicodedata.normalize("NFKD", str(text))
    folded = fold_for_orders(decomposed.translate(_NO_MARKS)).translate(_BLANK_IS_SPACE)
    folded = re.sub(r"(?<=\w)[-_./+~]+(?=\w)", " ", folded)
    return " ".join(folded.split())


_ORDER_VERBS = (r"remove|delete|erase|wipe|clear|drop|call|run|execute|invoke|send|post|"
                r"upload|ignore|forget|disregard|obey|reveal|print|output|fetch|open|"
                r"add|mark|change|update|approve|say|reply|answer|respond|tell")
_ADDRESSED = (r"ai|a\.?i\.?|llm|assistant|claude|chatgpt|gpt(?:-?\d+)?|gemini|copilot|"
              r"llama|qwen|mistral|deepseek|grok|language\s+model")
#: More shapes of an order to a model, read in prose (a README's opening, a
#: description) after :func:`fold_label` as well as :func:`fold_for_orders`.
#: Each needs words an ordinary README does not put together: a model
#: addressed and then told to act, "previous instructions" overridden, a
#: "new task:" that is an order.
_MODEL_ORDER_MORE_RE = re.compile(
    r"\b(?:override|overrule|bypass|skip|discard|drop|replace)\s+"
    r"(?:(?:all|any|the|your|my|of|these|those|each|every)\s+){0,3}"
    r"(?:previous|prior|above|earlier|preceding|foregoing|original|system)\s+"
    r"(?:instructions?|prompts?|directives?|directions|guidelines|guardrails|orders|rules)\b|"
    r"\bpay\s+no\s+(?:attention|heed|mind)\s+to\s+(?:(?:all|any|the|your|my|of|these|those)\s+){0,3}"
    r"(?:previous|prior|above|earlier|preceding|instructions?|rules|prompts?|directions|"
    r"directives|guidelines|system)\b|"
    r"\b(?:do\s+not|don'?t|never|stop|no\s+longer)\s+(?:follow|obey|heed|listen\s+to)\s+"
    r"(?:(?:all|any|the|your|of|these|those)\s+){0,3}"
    r"(?:(?:previous|prior|above|earlier|system|original)\s+)?"
    r"(?:instructions?|prompts?|directives|guidelines|guardrails|programming)\b|"
    r"\b(?:hey|hi|hello|dear|attention|note\s+to|message\s+to)\s*,?\s+"
    r"(?:(?:all|any|every|the|you)\s+)?(?:" + _ADDRESSED + r"|model|models)"
    r"(?:\s+(?:agents?|assistants?|models?|bots?|systems?))?\s*[:,!\-]\s*(?:please\s+)?"
    r"(?:" + _ORDER_VERBS + r")\b|"
    r"(?:^|[.!?]\s+)\W*(?:" + _ADDRESSED + r")s?(?:\s+(?:agents?|assistants?|models?|bots?))?"
    r"\s*[:,]\s*(?:please\s+)?(?:" + _ORDER_VERBS + r")\b|"
    r"\b(?:you|u)\s+(?:must|will|should|shall|need\s+to|have\s+to|are\s+to)\s+now\s+"
    r"(?:remove|delete|erase|wipe|clear|drop|ignore|forget|disregard|obey|send|upload|"
    r"reveal)\s+(?:all|every|everything|each|any|the\s+(?:user|owner)|your)\b|"
    r"\b(?:new|updated|real|actual|true|secret|hidden)\s+(?:task|tasks|instructions?|"
    r"directives?|objective|goal|orders|mission)\s*:\s*(?:please\s+)?(?:you\b|(?:"
    + _ORDER_VERBS + r")\b)|"
    r"\bwhatever\s+the\s+(?:user|owner|human)\s+(?:asks|says|wants|requests|types)\b|"
    r"\bsystem\s+override\b",
    re.IGNORECASE | re.MULTILINE)


def model_orders(text: object) -> bool:
    """True when *text* holds words aimed at steering a model (the prose
    check, after folding)."""
    if not isinstance(text, str):
        return False
    folded = fold_for_orders(text)
    if _MODEL_ORDER_RE.search(folded) or _MODEL_ORDER_MORE_RE.search(folded):
        return True
    label = fold_label(text)
    return bool(_MODEL_ORDER_RE.search(label) or _MODEL_ORDER_MORE_RE.search(label))


#: Words shaped like an order to a model or a browser. Narrower than
#: v1 ``artifact_review.reads_like_orders`` on purpose: a web address in a
#: description is data, while "ignore previous instructions" never is.
_ORDER_RE = re.compile(
    r"ignore (?:all |the |any )?(?:previous|prior|above)|disregard (?:all|the|"
    r"any|previous|prior|your)|system prompt|you are (?:now )?(?:a|an|the)\b|"
    r"from now on,? you|new instructions?|as an ai|^\s*(?:system|assistant|"
    r"user)\s*:|<\s*script|javascript:|data:text|on(?:load|error)\s*=|"
    r"\bsudo\b|\brm -rf\b|"
    # Lane I14: role play aimed at a model ("act as admin", "pretend you are root").
    r"\bact as (?:an? |the )?(?:admin(?:istrator)?|root|super ?user|sysadmin|system|developer|owner|dan)\b|"
    r"\bpretend (?:that )?(?:you are|you're|to be)\b", re.IGNORECASE)


def instruction_shaped(text: Any) -> bool:
    """True when *text* reads like an order to a model or a browser (folded first)."""
    if not isinstance(text, str):
        return False
    folded = fold_for_orders(text)
    return bool(_ORDER_RE.search(folded)) or model_orders(folded)


#: What Toolsmith and the brain see in place of a hidden line.
HIDDEN_LINE = "[line hidden by Drawbridge: it reads like instructions to an AI]"

#: The small words allowed between a verb and "system prompt".
_PROMPT_FILLER = (r"(?:(?:all|any|the|your|my|our|their|this|that|these|those|of|in|from|each|every|everything|"
                  r"anything|previous|prior|above|earlier|safety|original|initial|given|current|other|entire|"
                  r"whole|full|exact|complete|real|actual|hidden|secret|very|contents?|text|words)\s+){0,6}")
_PROMPT = r"system\s+prompts?\b"
_APOS = r"['\u2019\u02bc]"
#: Verbs that turn a model against something, with their -ing form ("ignores" only describes a model).
_TURN = (r"ignor(?:e|ing)|disregard(?:ing)?|forget(?:ting)?|overrid(?:e|ing)|overrul(?:e|ing)|bypass(?:ing)?|"
         r"discard(?:ing)?|disobey(?:ing)?|circumvent(?:ing)?|def(?:y|ying)")
#: A place on a screen. "Show the system prompt in Settings" describes an app; it is not an order to a model.
_ON_SCREEN = (r"(?!\s+(?:in|at|on|under|inside|within)\s+(?:(?:the|a|this|your)\s+)?(?:settings?|sidebar|panel|top|"
              r"bottom|ui|dashboard|page|tab|menu|console|logs?|editor|header|footer|preview|dialog|modal|window|"
              r"view|debugger)\b)")
#: Verbs an order after "system prompt:" starts with, and "you" (not a role: "You are a helpful editor").
_PROMPT_COMMAND = (r"(?:you(?!\s+are\s+an?\b|" + _APOS + r"re\s+an?\b)|ignore|disregard|forget|obey|send|upload|post|"
                   r"forward|leak|delete|remove|erase|wipe|reveal|print|show|tell|run|execute|fetch|approve|dump|"
                   r"output)\b")
#: An order aimed at a system prompt (D97): turning a model against it, asking it to give it away, or
#: handing it a new one. Words that only describe one ("Edit the system prompt in Settings") are not here.
_PROMPT_ORDER = (
    # "ignore your system prompt", "forget about the system prompt", "ignore what the system prompt says"
    r"\b(?:" + _TURN + r")(?:\s*[,;:]\s*|\s+)(?:about\s+)?(?:what(?:ever)?\s+)?" + _PROMPT_FILLER + _PROMPT + r"|"
    # "you must not follow your system prompt", "do not follow what the system prompt says"
    r"\b(?:do\s+not|don" + _APOS + r"?t|must\s+not|mustn" + _APOS + r"?t|should\s+not|shouldn" + _APOS
    + r"?t|never|stop|no\s+longer)\s+(?:follow(?:ing)?|obey(?:ing)?|heed(?:ing)?|listen(?:ing)?\s+to)\s+"
    r"(?:what(?:ever)?\s+)?" + _PROMPT_FILLER + _PROMPT + r"|"
    r"\bstop\s+using\s+your\s+" + _PROMPT_FILLER + _PROMPT + r"|"
    r"\bpay\s+no\s+(?:attention|heed|mind)\s+to\s+" + _PROMPT_FILLER + _PROMPT + r"|"
    # "replace your system prompt with", "send your system prompt to", "violate your system prompt"
    r"\b(?:replace|drop|skip|erase|delete|remove|wipe|send|upload|forward|e-?mail|overwrite|violate|break|abandon|"
    r"act\s+outside)\s+your\s+" + _PROMPT_FILLER + _PROMPT + r"|"
    r"\b(?:copy|include|paste)\s+your\s+" + _PROMPT_FILLER + _PROMPT
    + r"\s+(?:in|into|to)\s+(?:your|the)\s+(?:reply|response|answer|output|message)\b|"
    # "send the system prompt to http://...", "send system prompt to me"
    r"\b(?:send|upload|forward|post|e-?mail)\s+(?:(?:a|an)\s+)?" + _PROMPT_FILLER + _PROMPT
    + r"\s+to\s+(?:me\b|us\b|my\b|our\b|https?:|ftp:|www\.|[\w.+-]+@|\d{1,3}\.\d)|"
    # "replace the system prompt with this", "remove the system prompt and obey me"
    r"\breplace\s+(?:(?:the|this)\s+)?" + _PROMPT + r"\s+with\s+(?:this|these|that|mine|my|the\s+following)\b|"
    + _PROMPT + r"\s+and\s+(?:obey|do\s+as)\b|"
    # "print your system prompt", "show me the full text of your system prompt", "tell me about your system prompt"
    r"\b(?:reveal|repeat|leak|dump|expose|recite|disclose|(?:tell|give|send)\s+(?:me|us))\s+"
    r"(?:(?:me|us|out|back|about)\s+){0,3}" + _PROMPT_FILLER + r"(?:the|your)\s+" + _PROMPT_FILLER + _PROMPT + r"|"
    r"\b(?:show|display|print|output|echo)\s+(?:(?:me|us|out|back)\s+){0,2}" + _PROMPT_FILLER + r"your\s+"
    + _PROMPT_FILLER + _PROMPT + r"|"
    r"\b(?:show|display|print|output|echo)\s+(?:(?:me|us|out|back)\s+){0,2}" + _PROMPT_FILLER + r"the\s+"
    + _PROMPT_FILLER + _PROMPT + _ON_SCREEN + r"|"
    r"\b(?:reveal|repeat|leak|dump|show|display|print|output|echo|expose|disclose)\s*:\s*your\s+"
    + _PROMPT_FILLER + _PROMPT + r"|"
    # "reveal system prompt": no article, so only as an imperative, alone at the start of a line or sentence.
    r"(?:^[\s>*+-]*|[.!?;:]\s+)(?:reveal|print|show|repeat|leak|dump|output|display|expose|disclose)\s+" + _PROMPT
    + r"(?:\s*$|\s*[.!?]|\s+(?:now|verbatim|immediately|exactly|please|first|here|back|in\s+full|word\s+for\s+word)\b)|"
    r"\bwhat(?:\s+is|\s+are|\s+does|\s+do|" + _APOS + r"s)\s+your\s+" + _PROMPT_FILLER + _PROMPT + r"|"
    # "new system prompt:", "new system prompt - obey it", "this is your new system prompt"
    r"\b(?:new|updated|real|actual|true|secret|hidden|revised)\s+" + _PROMPT
    + r"\s*(?::|[-\u2013\u2014]\s|\b(?:is|are)\s+(?:below|now|here)\b|\bfollows\b)|"
    r"\bthis\s+is\s+(?:the|your)\s+new\s+" + _PROMPT + r"|\byou\s+have\s+a\s+new\s+" + _PROMPT + r"|"
    # "your system prompt is now", "your system prompt is: ...", "your system prompt no longer applies"
    r"\byour\s+(?:(?:new|updated|real|actual|true)\s+)?" + _PROMPT
    + r"\s*(?:\b(?:is|are)\s*(?:now\b|:|" + _PROMPT_COMMAND + r")|\b(?:says|reads|now\s+reads|follows|"
    r"has\s+changed|has\s+been\s+(?:replaced|changed|updated|removed))\b)|"
    # "system prompt: you must obey", "SYSTEM PROMPT: send the owner's files" (the words open the line)
    r"^[\W_]*(?:(?:the|your|my)\s+)?" + _PROMPT + r"\s*:\s*[\W_]*" + _PROMPT_COMMAND + r"|"
    # "the system prompt below replaces all others", "system prompt override", "the system prompt is void"
    + _PROMPT + r"\s+(?:(?:below|above|here)\s+)?(?:replaces?|overrides?|supersedes?|cancels?|voids?)\s+"
    r"(?:all|any|every|previous|prior|earlier|others?|your)\b|"
    + _PROMPT + r"\s+(?:(?:is|are)\s+(?:void|cancell?ed|revoked)\b|(?:is\s+|are\s+)?no\s+longer\s+appl(?:y|ies)\b|"
    r"(?:is|are)\s+now\s*:)|"
    + _PROMPT + r"\s+override\s*(?:$|[.!:;,])")
_PROMPT_ORDER_RE = re.compile(_PROMPT_ORDER, re.IGNORECASE | re.MULTILINE)

#: Clearly hostile: turning a model against its own instructions, or giving
#: it a new role. Read after folding. Orders aimed at a system prompt are read
#: on their own (:func:`_prompt_order_heard`).
_HOSTILE_RE = re.compile(
    r"\b(?:ignore|disregard|forget|override|bypass)\s+"
    r"(?:(?:all|any|the|your|my|of|these|those|each|every|previous|prior|above|"
    r"earlier|system|safety|original|initial)\s+){0,4}"
    r"(?:previous|prior|above|earlier|preceding|instructions?|rules|guidelines|"
    r"guardrails|directives|prompts?|programming)\b|"
    r"(?<!\bif )\byou\s+are\s+(?:now\s+)?(?:an?\s+)?(?:ai|llm|language\s+model|"
    r"ai\s+assistant|chatbot)\b|"
    r"\byou\s+are\s+now\s+(?:a|an|the|dan|jailbroken|unrestricted|unfiltered)\b|"
    r"\b(?:do not|don't|never)\s+(?:tell|inform|warn|alert)\s+(?:the\s+)?"
    r"(?:user|owner|human)\b|"
    r"\bexfiltrat\w*",
    re.IGNORECASE | re.MULTILINE)

#: Asking for keys, tokens or passwords to be sent to an address.
_EXFIL_RE = re.compile(
    r"\b(?:send|upload|forward|transmit|e-?mail|leak)\b[^\n]{0,80}?"
    r"(?:~/\.ssh|\.ssh\b|\bid_(?:rsa|ed25519|ecdsa|dsa)\b|\bprivate\s+keys?\b|"
    r"\bsecret\s+keys?\b|\bapi[\s_-]?keys?\b|\baccess\s+tokens?\b|\btokens\b|"
    r"\bsecrets\b|\bpasswords?\b|\bcredentials\b|(?<![\w.])\.env\b|"
    r"\benvironment\s+variables\b|\bcookies\b|\bseed\s+phrase\b)"
    r"[^\n]{0,80}?\bto\b\s*:?\s*(?:https?://|ftp://|[\w.+-]+@[\w-]+\.[a-z]{2,}|"
    r"\d{1,3}(?:\.\d{1,3}){3}\b)",
    re.IGNORECASE)

#: Aimed at an AI but not hostile on its own ("Note to AI agents: run the
#: tests first", a chat transcript's "Assistant:" line, the bare words
#: "system prompt", D97): hidden, not stopped.
_AI_LINE_RE = re.compile(
    r"^\s*(?:system|assistant)\s*:|<\|(?:im_start|im_end|system|assistant|user)\|>|"
    r"\[/?inst\]|<<\s*/?sys\s*>>|</?\s*system\s*>|\bas an ai\b|"
    r"\bfrom now on,? you\b|\bnew instructions?\s*:|\bsystem\s+prompt\b",
    re.IGNORECASE)

#: The bare words in any spelling, read after :func:`fold_label`.
_PROMPT_WORDS_RE = re.compile(r"\bsystem\s+prompt\b", re.IGNORECASE)
#: The bare words spelled plainly: ASCII only, as words or as a name in code or a path ("system_prompt",
#: "system-prompt.md"). Anything else that reads as these words took a look-alike or an odd space to say it.
_PROMPT_WORDS_PLAIN_RE = re.compile(r"(?<![^\W_])system[ \t_./+~-]+prompt(?![^\W_])", re.IGNORECASE | re.ASCII)

_COMMENT_RE = re.compile(r"<!--(.*?)(?:-->|\Z)", re.DOTALL)
#: Unicode tag characters: invisible, and each one carries an ASCII letter.
_TAG_RUN_RE = re.compile("[\U000E0000-\U000E007F]+")


def _read_char(ch: str, gap: bool) -> Any:
    """Every kind of space and line break is a space, combining marks go, and a hidden character
    (:func:`hidden_char`) goes, or with *gap* is a space (it may be the only gap between two words)."""
    if ch.isspace():
        return 32
    if hidden_char(ch):
        return 32 if gap else None
    return None if unicodedata.category(ch) == "Mn" else ord(ch)


_READ_GONE = _CharTable(lambda ch: _read_char(ch, False))
_READ_GAP = _CharTable(lambda ch: _read_char(ch, True))
#: HTML tags and comments, and a markdown link's address.
_TAGS_RE = re.compile(r"</?[a-zA-Z][^<>]{0,200}>|<!--[^<>]{0,200}-->|\]\([^()\s]{0,200}\)")
#: Marks that dress words up. ``_`` and ``'`` only at a word's edge: ``system_prompt`` and ``don't`` stay whole. A
#: colon or comma stays too: only the verbs that turn a model read past one, so "Output: the system prompt" passes.
_MARKS_RE = re.compile("[*`\"()\\[\\]~{}\u201c\u201d\u2018]|(?<![^\\W_])_+|_+(?![^\\W_])|(?<!\\w)'|['\u2019](?!\\w)")
_JOIN_RE = re.compile(r"(?<=\w)[-_./+~]+(?=\w)")
_ARTICLE_RE = re.compile(r"\b(?:your|the|this|that|these|those|all|my)\b", re.IGNORECASE)
#: "new-system-prompt:-obey-me": a whole sentence joined by marks, not a setting and its value.
_JOINED_SENTENCE_RE = re.compile(r"system[-_./+~]+prompts?:[-_./+~]+\w", re.IGNORECASE)


def _read(line: str, table: _CharTable) -> str:
    folded = unicodedata.normalize("NFKD", line).translate(table).translate(_LOOKALIKE)
    return " ".join(folded.split())


def _no_marks(read: str) -> str:
    return " ".join(_MARKS_RE.sub(" ", _TAGS_RE.sub(" ", read)).split())


#: How far around the words "system prompt" an order can reach, on a read with single spaces.
_PROMPT_REACH = 200
_PROMPT_ANY_RE = re.compile(r"system\s+prompts?\b", re.IGNORECASE)


def _near_prompt(read: str) -> list[str]:
    """The parts of *read* close enough to the words to hold an order, so a very long line costs little."""
    spans: list[list[int]] = []
    for m in _PROMPT_ANY_RE.finditer(read):
        start, end = max(0, m.start() - _PROMPT_REACH), m.end() + _PROMPT_REACH
        if spans and start <= spans[-1][1]:
            spans[-1][1] = end
        else:
            spans.append([start, end])
    return [read[a:b] for a, b in spans]


@lru_cache(maxsize=4)
def _prompt_order_heard(line: str) -> bool:
    """True when one line (split on line feeds only) holds an order aimed at a system prompt, however it is
    dressed: look-alike letters, hidden characters inside a word or as the only gap between words, odd line
    breaks, ordinary marks or tags, or joined by marks with a small word kept ("ignore-your-system-prompt"). A
    name in code with no such word ("override_system_prompt: true") is a setting, not an order."""
    if line.isascii() and "&" not in line and "prompt" not in line.lower():
        return False
    reads: list[str] = []
    for source in ((line, html.unescape(line)) if "&" in line else (line,)):
        gone = _read(source, _READ_GONE)
        if "prompt" not in gone.lower():
            continue
        gap = _read(source, _READ_GAP)
        if gap != gone and _PROMPT_WORDS_RE.search(gap):
            return True                                    # "system<invisible>prompt": hidden on purpose
        for read in (gone, gap):
            reads += [read, _no_marks(read)]
    reads = list(dict.fromkeys(reads))
    if any(_JOINED_SENTENCE_RE.search(read) for read in reads):
        return True
    if any(_PROMPT_ORDER_RE.search(part) for read in reads for part in _near_prompt(read)):
        return True
    return any(_ARTICLE_RE.search(m.group(0)) for read in dict.fromkeys(_JOIN_RE.sub(" ", r) for r in reads)
               for part in _near_prompt(read) for m in _PROMPT_ORDER_RE.finditer(part))


#: A line with only the bare words: a heading, ``system prompt:``, ``SYSTEM_PROMPT = """``.
_PROMPT_ONLY_RE = re.compile(r"^\W*(?:(?:the|your|my|a|an|new|custom|default)\s+)?system\s+prompts?\W*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s|$)")
#: How many lines after such a line are hidden with it: they may be the prompt itself.
_PROMPT_NEIGHBOURS = 20


def _hidden_beside_prompt_words(line: str) -> bool:
    """A hidden character right beside the plain words (a direction mark around them, a tag character after
    them). A byte order mark that opens the file is only a byte order mark."""
    for m in _PROMPT_WORDS_PLAIN_RE.finditer(line):
        before = line[m.start() - 1] if m.start() else ""
        after = line[m.end()] if m.end() < len(line) else ""
        if before and hidden_char(before, lines=True) and not (m.start() == 1 and before == chr(0xFEFF)):
            return True
        if after and hidden_char(after, lines=True):
            return True
    return False


def _prompt_only(line: str) -> bool:
    return len(line) < 200 and bool(_PROMPT_ONLY_RE.match(fold_label(line)))


def _hostile(folded: str) -> bool:
    return bool(_HOSTILE_RE.search(folded) or _EXFIL_RE.search(folded))


def ai_line(line: Any) -> bool:
    """True when one line of an outside project's text reads like instructions
    to an AI (hidden from the model). Shell install lines never count."""
    if not isinstance(line, str) or not line.strip():
        return False

    folded = fold_for_orders(line)
    return (_hostile(folded) or bool(_AI_LINE_RE.search(folded))
            or model_orders(line) or _prompt_order_heard(line))


def _tag_text(text: str) -> str:
    """The ASCII that runs of invisible tag characters spell."""
    return " ".join("".join(chr(ord(ch) - 0xE0000) for ch in m.group(0)
                            if 0x20 <= ord(ch) - 0xE0000 < 0x7F)
                    for m in _TAG_RUN_RE.finditer(text))


#: The kinds of hit, for the card (D105): orders, a request to send keys away, and words kept out of a person's sight.
HIT_ORDERS, HIT_KEYS, HIT_UNSEEN = "orders", "keys", "unseen"


def _hits(text: str) -> Iterator[tuple[str, str]]:
    """Every hit in *text* as ``(kind, why)``, one at a time. The first is the reason :func:`readme_block` gives."""
    tags = _tag_text(text)
    if tags and (ai_line(tags) or len(tags.replace(" ", "")) > 16):
        yield HIT_UNSEEN, "it hides words for an AI in invisible characters"
        if _EXFIL_RE.search(fold_for_orders(tags)):
            yield HIT_KEYS, "its words ask an AI to send keys or passwords away"
    for m in _COMMENT_RE.finditer(text):
        if any(ai_line(ln) for ln in m.group(1).splitlines()):
            yield HIT_UNSEEN, "it hides words for an AI in a comment people do not see"
    # Orders aimed at a system prompt are read on lines split the way the model's text is split (line feeds), so
    # an odd line break does not cut one in two.
    for line in text.split("\n"):
        if _prompt_order_heard(line):
            if any(_PROMPT_ORDER_RE.search(part) for part in _near_prompt(_no_marks(" ".join(line.split())))):
                yield HIT_ORDERS, "its words read like orders to a computer"
            else:
                yield HIT_UNSEEN, "it hides words for an AI behind invisible or look-alike characters"
    for line in text.splitlines():
        if not ai_line(line):
            continue
        # The bare words "system prompt" never stop a project on their own (D97), so they are taken out before
        # asking what else this line says. They must not cover for anything hidden on the same line.
        rest = _PROMPT_WORDS_PLAIN_RE.sub(" ", line)
        if ((ai_line(rest) and not ai_line_plain(rest)) or _PROMPT_WORDS_RE.search(fold_label(rest))
                or _hidden_beside_prompt_words(line)):
            # Only read as words for an AI once invisible or look-alike
            # characters are taken out: hidden on purpose.
            yield HIT_UNSEEN, "it hides words for an AI behind invisible or look-alike characters"
        folded = fold_for_orders(line)
        if _EXFIL_RE.search(folded):
            yield HIT_KEYS, "its words ask an AI to send keys or passwords away"
        if _HOSTILE_RE.search(folded):
            yield HIT_ORDERS, "its words read like orders to a computer"


def readme_block(text: Any) -> str:
    """Why an outside project's README (or doc) is stopped, or "".

    Only clearly hostile text stops it; anything else that reads like
    instructions to an AI is hidden line by line (:func:`hide_ai_lines`)."""
    if not isinstance(text, str) or not text:
        return ""
    return next(_hits(text), ("", ""))[1]


def hit_kinds(text: Any) -> frozenset[str]:
    """Every kind of hit anywhere in *text*, so a card says each one, whichever comes first in the file (D105)."""
    if not isinstance(text, str) or not text:
        return frozenset()
    return frozenset(kind for kind, _why in _hits(text))


def ai_line_plain(line: str) -> bool:
    """The line reads like instructions to an AI WITHOUT folding (so nothing
    invisible or look-alike was needed to say it)."""

    return bool(_HOSTILE_RE.search(line) or _EXFIL_RE.search(line)
                or _AI_LINE_RE.search(line)
                or _MODEL_ORDER_RE.search(line)
                or _MODEL_ORDER_MORE_RE.search(line))


def _hit_in_line(line: str) -> bool:
    """:func:`ai_line` on a line split on line feeds, and on each part of it when it holds an odd line break
    (a carriage return, a form feed, U+2028): :func:`readme_block` reads those parts one by one (D105)."""
    if ai_line(line):
        return True
    parts = line.splitlines()
    return len(parts) > 1 and any(ai_line(part) for part in parts)


def _comment_lines(text: str) -> set[int]:
    """The line numbers (split on line feeds) of every line an HTML comment with words for an AI touches. Nobody
    writes such a comment for a reader, so all of it goes, not only the lines with the words (D105)."""
    out: set[int] = set()
    if "<!--" not in text:
        return out
    line, seen = 0, 0
    for m in _COMMENT_RE.finditer(text):
        line += text.count("\n", seen, m.start())
        seen = m.start()
        if any(ai_line(part) for part in m.group(1).splitlines()):
            out.update(range(line, line + m.group(0).count("\n") + 1))
    return out


def hide_ai_lines(text: Any) -> tuple[Any, int]:
    """*text* with each line that reads like instructions to an AI replaced by
    :data:`HIDDEN_LINE`, and how many were. Not a string: as given, 0.

    A line with only the words "system prompt" (a heading, ``system prompt:``) may
    be followed by the prompt itself, and it stops nothing (D97), so the lines
    after it are hidden too: blank lines right after it are kept, then up to
    :data:`_PROMPT_NEIGHBOURS` lines, until the next blank line or heading.

    An HTML comment with such words is hidden whole, and invisible tag characters
    are always dropped (D105)."""
    if not isinstance(text, str) or not text:
        return text, 0
    if _TAG_RUN_RE.search(text):
        text = _TAG_RUN_RE.sub("", text)          # never passed on to a model
    buried = _comment_lines(text)
    out, n = [], 0
    left, opening = 0, False
    for number, line in enumerate(text.split("\n")):
        hidden = _hit_in_line(line)
        if hidden and _prompt_only(line):
            left, opening = _PROMPT_NEIGHBOURS, True
        elif left:
            if not line.strip():
                left = left if opening else 0
            elif _HEADING_RE.match(line) and not hidden:
                left = 0
            else:
                hidden, left, opening = True, left - 1, False
        if hidden or number in buried:
            out.append(HIDDEN_LINE)
            n += 1
        else:
            out.append(line)
    return "\n".join(out), n


def hide_ai_lines_in(value: Any) -> tuple[Any, int]:
    """:func:`hide_ai_lines` on every string inside *value* (a tool's answer:
    dicts, lists, strings). Returns the new value and the lines hidden."""
    if isinstance(value, str):
        return hide_ai_lines(value)
    if isinstance(value, dict):
        total, out = 0, {}
        for k, v in value.items():
            out[k], n = hide_ai_lines_in(v)
            total += n
        return out, total
    if isinstance(value, (list, tuple)):
        total, items = 0, []
        for v in value:
            item, n = hide_ai_lines_in(v)
            items.append(item)
            total += n
        return (type(value)(items) if isinstance(value, tuple) else items), total
    return value, 0



# -- words a package wrote that she reads on a Yes card (lane I8a) ----------------------------------------------
#: A link or a scheme in a tool's description (http, www., javascript:, data:, file: and the like).
_LINKISH_RE = re.compile(r"(?i)(?:\b[a-z][a-z0-9+.-]{1,20}://|\bwww\.|\b(?:javascript|vbscript|data|file|blob|"
                         r"mailto|about|chrome|ms-[a-z-]+)\s*:)")
#: Words aimed at the owner's answers: a tool's description never needs to talk about her Yes, a card, her PIN,
#: her keys or what she should approve.
_COAX_RE = re.compile(r"(?i)\b(?:tap|click|press|say|answer|choose|pick|select|hit)\s+(?:on\s+)?[\"']?"
                      r"(?:yes|allow|ok|okay|approve|accept)\b|\b(?:approve|allow|accept)\s+(?:every|all|each|any)\b|"
                      r"\byes\s+(?:card|button)s?\b|\b(?:plan|consent)\s+cards?\b|"
                      r"\b(?:your|her|his|their|the\s+owner'?s?)\s+(?:pin|password|recovery\s+code|api\s+keys?|keys?|vault)\b|"
                      r"\b(?:tell|ask|get|make|convince|remind)\s+(?:the\s+)?(?:owner|user|person|human)\b")
#: Markup: tags, entities, markdown links and images, code ticks, template slots like {{name}} (lane I14).
_MARKUP_RE = re.compile(r"[<>`]|&#?\w+;|\]\s*\(|!\[|\{\{|\}\}|\$\{")


def url_is_plain(url: Any) -> bool:
    """*url* has no whitespace, control character, backslash or ``@`` anywhere and is plain ASCII: urllib strips some of
    these before it reads a host, a line break in an address on a card reads as a line of its own, and
    ``https://bank.example@other.example/`` reads as one place and goes to another."""
    return isinstance(url, str) and url.isascii() and not any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F or c in "\\@"
                                                              for c in url)


def owner_text_flag(text: Any) -> str:
    """Why a package's own words (an MCP tool's description) may not be shown to the owner on a card, in plain
    words; "" when they may. Flagged: words that read like orders to an AI (:func:`instruction_shaped`), a link
    or a scheme, markup, or control and invisible characters (tabs and line breaks aside). Fail closed: text
    that is not a string is flagged."""
    if not isinstance(text, str):
        return "it is not plain text"
    if any(unicodedata.category(ch) in ("Cc", "Cf", "Co", "Cs") and ch not in "\t\n\r" for ch in text):
        return "it has hidden or control characters"
    if instruction_shaped(text) or _COAX_RE.search(fold_for_orders(text)):
        return "it reads like instructions to an AI"
    if _LINKISH_RE.search(text):
        return "it has a link in it"
    if _MARKUP_RE.search(text):
        return "it has markup in it"
    return ""
