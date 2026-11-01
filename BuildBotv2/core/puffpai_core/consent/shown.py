"""What a Yes card shows, in plain lines: the one view for the CLI, ``GET /v1/consent`` and the Desktop.

``view(card, floor)`` -> ``{"title", "lines", "floor"}``. Every line comes from the stored card dict (and its
``puffpai_yes`` key, or the floor flag of an L4 card), which is exactly what ``card_sha`` covers, so what
she reads is what her Yes grants. Nothing here adds a fact that is not in the card.

A package card shows, in this order: what it is, its own words (description, consent sentence), the
internet hosts (run and install), the keys, the "Your turn" setup steps, the tools that change things
without asking (her approved scope) and that any other change asks, Drawbridge's line and notes, the
files Drawbridge could not check, that hold words aimed at an AI in its docs, or with a tunnel in their code, which
comes first (a "hold": her Yes adds them anyway, D105, D117), and for a floor-level card that the PIN is needed (D29). A ``tool_call`` card shows the app, the tool, the gated summary of its
arguments and why it asks; a chat command's shows the whole command and its folder (D138). A job's hold card (``try_holds``, lane I4b; one per job, after every app
Drawbridge could check fully was tried) lists each app set aside with the files Drawbridge could not check,
and what a Yes really does at that moment (Toolsmith may try it; nothing from it runs before that). The
older one-app form (``try_hold``, lane I3b) still reads.

An adopted app's card under a build plan (03 3.3) says so first: the lines that were NOT on the plan
she said Yes to (``outside_plan``), or that the plan's Yes covered it. A build plan's own card (lane
L6, the L4 form) shows the plan's sentence, its lines and the places it reaches while it installs.
"""

from __future__ import annotations

import textwrap
from typing import Any

PIN_LINE = "This one needs your PIN. A tap is not enough."
#: How much of a chat command one card line holds (D138).
COMMAND_LINE = 100
#: The note that only repeats what :func:`held_line` says about the held files (D95, D105).
_HELD_NOTES = ("some of its files could not be checked", "some of its docs hold words aimed at an AI",
               "its code can start a tunnel")
#: A tunnel in plain words (D117): the title's short form and the sentence. Always "can": the pattern can't tell whether
#: the tunnel is on by default. Drawbridge's own line says the same (a test holds them equal).
TUNNEL_SHORT = "Its code can start a tunnel that can open your computer to the internet"
TUNNEL_WORDS = ("Its code can start a tunnel, and a tunnel can open your computer to the internet, so people out there "
                "could reach what runs here")
TUNNEL_DOES = "A tunnel can open your computer to the internet, so people out there could reach what runs here."
#: Where a try of such an app runs (D117), in place of the plan's label: that label is not enforced, and a native command
#: can run after her Yes.
TUNNEL_WHERE = "It may run as a program on this computer, as you. There the Bridge may not stop a tunnel."
#: What a package's ``needs.addresses`` says on its Yes card (D110). The Yes for the package opens none of them.
ADDRESS_LINES = {"lan": "It may ask to reach an address on your home network. Each one needs its own Yes.",
                 "internet": "It may ask to reach an address on the internet. Each one needs its own Yes."}


def _names(items: Any) -> list[str]:
    return [str(x) for x in items or [] if isinstance(x, (str, int, float))] if isinstance(items, list) else []


def _list(label: str, items: Any, none: str) -> str:
    got = _names(items)
    return f"{label}: {', '.join(got)}" if got else none


def floor_of(card: dict, floor: Any = None) -> bool:
    if isinstance(floor, bool):
        return floor
    yes = card.get("puffpai_yes") if isinstance(card, dict) else None
    if isinstance(yes, dict) and isinstance(yes.get("floor"), bool):
        return yes["floor"]
    return bool(card.get("floor")) if isinstance(card, dict) else True


def _kind(card: dict) -> str:
    yes = card.get("puffpai_yes") if isinstance(card.get("puffpai_yes"), dict) else card
    scope = yes.get("scope") if isinstance(yes, dict) else None
    return str((scope or {}).get("kind") or "") if isinstance(scope, dict) else ""


def _plan_lines(c: dict) -> list[str]:
    plan = c.get("plan") if isinstance(c.get("plan"), dict) else None
    if plan is None:
        return []
    outside = _names(c.get("outside_plan"))
    if outside:
        return (["Not on the plan you said Yes to:"] + [f"- {x}" for x in outside]
                + ["The rest was on that plan. Your Yes here covers the whole app."])
    return ["Covered by the plan you said Yes to, so it was added without a new card."]


def _package_lines(c: dict) -> list[str]:
    # D117: the tunnel comes first, ahead of the plan's lines and the app's own words, so its words can't speak first.
    tunnel = isinstance(c.get("drawbridge"), dict) and c["drawbridge"].get("held_tunnel") is True
    lines = ([f"{TUNNEL_WORDS}."] if tunnel else []) + _plan_lines(c) + [
        f"What it is: {c.get('name', '')} {c.get('version', '')} ({c.get('kind', '')}), "
        f"from {c.get('origin', '')}".replace("  ", " ")]
    for key in ("description", "consent"):
        if str(c.get(key) or "").strip():
            lines.append(str(c[key]).strip())
    store = c.get("store") if isinstance(c.get("store"), dict) else None
    if store is not None:                    # D89: where a store package comes from, and the commit pinned
        tier = "Verified" if store.get("tier") == "verified" else "Community (nobody at PuffPAI reviewed it)"
        was = f", replacing {str(store['was_commit'])[:12]}" if store.get("was_commit") else ""
        lines.append(f"From the store: {store.get('where', '')}, {tier}. {store.get('repo', '')}"
                     f"{'/' + str(store['path']) if store.get('path') else ''} at commit "
                     f"{str(store.get('commit') or '')[:12]}{was}.")
    if str(c.get("look_colours") or "").strip():      # D96: a look whose text may be hard to read
        lines.append(str(c["look_colours"]).strip())
    # D117: "no", or a list that reads as all of it, would be more than Drawbridge knows: that is only what it lists.
    places = _list("Reaches these places on the internet", c.get("hosts"),
                   "It lists no places on the internet, but its code can start a tunnel (see the top of this card)"
                   if tunnel else "Reaches the internet: no")
    lines.append(places + (". Its code can also start a tunnel (see the top of this card)"
                           if tunnel and _names(c.get("hosts")) else ""))
    if _names(c.get("install_hosts")):
        lines.append(_list("Reaches these places only while it installs", c.get("install_hosts"), ""))
    if c.get("addresses") in ADDRESS_LINES:           # D110: what it may ask for, never a grant
        lines.append(ADDRESS_LINES[c["addresses"]])
    lines.append(_list("Keys it uses", c.get("keys"), "Keys it uses: none"))
    lines.append(_list("Your turn (setup steps)", c.get("your_turn"), "Your turn (setup steps): none"))
    if c.get("owner_files") == "write":               # D136: the Core's own rule, whatever the package says
        lines.append("Your own files: it makes new ones in My files. Changing a file is off until you turn it on in "
                     "Settings. Then it changes a file it did not make only after your Yes, one file at a time, and only "
                     "in your data folder or a folder you linked.")
    elif c.get("owner_files"):
        lines.append(f"Your own files it may use: {c['owner_files']}")
    if c.get("brain_as_written") is True:              # WritersBot: the privacy gate steps aside for this app's words
        lines.append("It has a switch, on to start with, that sends its words to your bot's brain on this computer as "
                     "written, with nothing from them filed in your vault: dates, names and places in them are treated "
                     "as the work, not as your records. Turn the switch off in the app and they are filed as usual.")
    if _names(c.get("provides")):
        lines.append(_list("Fills these slots", c.get("provides"), ""))
    if c.get("reads") == "raw":
        lines.append("It sees your real private values (a raw reader).")
    lines.append(_list("Changes it makes without asking you", c.get("changes"),
                       "Changes it makes without asking you: none"))
    lines.append("Any other tool of it that changes something asks you each time.")
    if c.get("linked_folder"):
        lines.append(f"Runs in place from your folder {c['linked_folder']} (never deleted).")
    version = (c.get("what_changed") or {}).get("version") if isinstance(c.get("what_changed"), dict) else None
    if isinstance(version, list) and len(version) == 2 and any(version):
        lines.append(f"Version: {version[0]} -> {version[1]}")
    fields = (c.get("what_changed") or {}).get("fields") if isinstance(c.get("what_changed"), dict) else None
    if _names(fields):
        lines.append("What changed: " + ", ".join(_names(fields)) + ".")
    db = c.get("drawbridge") if isinstance(c.get("drawbridge"), dict) else None
    if db is None:
        lines.append("Drawbridge: no check is shown on this card.")
        return lines
    held = _names(db.get("held"))
    # Held only for its docs' words, or for a tunnel, the held line below says the same with the names (D105, D117):
    # said once.
    if str(db.get("line") or "").strip() and not (held and _names(db.get("held_said"))
                                                  and (tunnel or db.get("held_docs_only") is True)):
        lines.append(f"Drawbridge: {str(db['line']).strip()}")
    if held and tunnel:                               # D117: ahead of anything else Drawbridge has to say
        lines.append(held_line(_without_does(db)) + " Your Yes adds all of it anyway.")
    for note in _names(db.get("notes")):
        if held and note.startswith(_HELD_NOTES):
            continue                                  # said once, plainly, below
        lines.append(f"Drawbridge's note: {note}")
    if held and not tunnel:
        lines.append(held_line(db) + " Your Yes adds them anyway.")
    return lines


def held_line(db: dict, lead: str = "Drawbridge couldn't check some of its files.") -> str:
    """What a card says about the files Drawbridge holds (D95): which can run code and why, which are not what
    their names say, and which are of a kind it can't read, by count with at most three base names each. A
    card from before D95 (no ``held_said``) lists its names as it always did. When every held file was read and
    only holds words aimed at an AI (D105), it never says Drawbridge couldn't check them; nor when a tunnel holds it
    (D117): its lines lead, and any file it couldn't read says so itself."""
    said = _names(db.get("held_said"))
    if said:
        plain = db.get("held_docs_only") is True or db.get("held_tunnel") is True
        return ("Drawbridge:" if plain else lead) + " " + " ".join(said)
    held = _names(db.get("held"))
    more = db.get("held_more") if isinstance(db.get("held_more"), int) else 0
    return ("Drawbridge couldn't check these files: " + ", ".join(held[:8])
            + (f" and {more + max(0, len(held) - 8)} more" if more or len(held) > 8 else "") + ".")


def _try_hold_lines(c: dict) -> list[str]:
    """A try's hold card (lane I3b): Drawbridge held a download Toolsmith wants to try for a build."""
    hold = c.get("try_hold") if isinstance(c.get("try_hold"), dict) else {}
    db = c.get("drawbridge") if isinstance(c.get("drawbridge"), dict) else {}
    lines = [f"What it is: {c.get('name', '')}, from {hold.get('url') or c.get('origin', '')}, an app on the "
             "plan you said Yes to.",
             "Drawbridge found a tunnel in its code, so this one waits for you."
             if db.get("held_tunnel") is True else
             "Drawbridge checked every file. Some of its docs hold words aimed at an AI, so this one waits for you."
             if db.get("held_docs_only") is True else
             "Drawbridge checked every file it can read. Some it can't (too big, a link, or a kind PuffPAI "
             "can't read), so this one waits for you."]
    if _names(db.get("held")):
        lines.append(held_line(db))
    if hold.get("cut") is True:
        lines.append("It has more files than Drawbridge walks, so some were not seen at all.")
    for note in _names(db.get("notes")):
        if not note.startswith(_HELD_NOTES):
            lines.append(f"Drawbridge's note: {note}")
    lines += [f"Yes: Toolsmith tries this app with these files. {TUNNEL_WHERE} If the try works, your Yes keeps it too." if db.get("held_tunnel") is True else
              "Yes: Toolsmith tries this app with these files. Its try runs as a program on this computer, as "
              "you, unless it runs in Docker behind the Bridge.",
              "No: Toolsmith moves on to the next app. Nothing of this one runs.",
              "Nothing runs and nothing is installed before you answer. This card closes after 10 minutes "
              "with no answer, like a No."]
    return lines


def _tunnel_lead(tunnels: int, apps: int, alone: bool) -> str:
    """The lead of a hold card with a tunnel on it (D117): true for the apps it names. On a card of several, only as
    many as have one."""
    if alone:
        return f"Drawbridge found a tunnel in its code. {TUNNEL_DOES} Let Toolsmith try it anyway?"
    if apps == 1:
        return f"No other app worked, so this one waits for you. Drawbridge found a tunnel in its code. {TUNNEL_DOES}"
    which = "each one" if tunnels == apps else f"{tunnels} of them, and each app's line says which"
    return f"No other app worked, so these wait for you. Drawbridge found a tunnel in the code of {which}. {TUNNEL_DOES}"


def _without_does(db: dict) -> dict:
    """*db* with the sentence on what a tunnel does left out, for an app's line when the card's lead says it."""
    return {**db, "held_said": [x for x in _names(db.get("held_said")) if x != TUNNEL_DOES]}


def _try_holds_lines(c: dict) -> list[str]:
    """The job's ONE hold card (lane I4b): the apps Drawbridge couldn't fully check, set aside while the
    others were tried. She ticks which ones Toolsmith may try; only those are tried (a Yes with nothing
    ticked tries none)."""
    items = [h for h in c.get("try_holds") or [] if isinstance(h, dict)]
    words_only = bool(items) and all(isinstance(h.get("drawbridge"), dict)
                                     and h["drawbridge"].get("held_docs_only") is True for h in items)
    tunnels = sum(1 for h in items if isinstance(h.get("drawbridge"), dict) and h["drawbridge"].get("held_tunnel") is True)
    lines = [f"For: {c.get('name') or 'this job'}. No other app worked, so these wait for you. Drawbridge checked "
             "every file. Some of their docs hold words aimed at an AI." if words_only else
             f"For: {c.get('name') or 'this job'}. No app Drawbridge could check fully worked, so these wait "
             "for you. Drawbridge checked every file it can read; some it can't (too big, a link, or a kind "
             "PuffPAI can't read)."]
    if c.get("alone") is True:                     # no other app was there to try first (D104)
        lines[0] = lines[0].replace("No app Drawbridge could check fully worked, so these wait for you. Drawbridge "
                                    "checked every", "Drawbridge couldn't check some of its files. Let Toolsmith try "
                                    "it anyway? It checked every")
    if tunnels:                                    # D117: said for the card here, and each app's own line says which
        lines[0] = f"For: {c.get('name') or 'this job'}. " + _tunnel_lead(tunnels, len(items), c.get("alone") is True)
    for n, h in enumerate(items, 1):
        db = h.get("drawbridge") if isinstance(h.get("drawbridge"), dict) else {}
        line = f"{n}. {h.get('name', '')}, from {h.get('url', '')} (tries as a program on this computer, as you)."
        if db.get("held_tunnel") is True:              # D117: never the plan's label for an app with a tunnel
            line = f"{n}. {h.get('name', '')}, from {h.get('url', '')}. {TUNNEL_WHERE}"
        if _names(db.get("held")):
            line += " " + held_line(_without_does(db) if tunnels else db, "Drawbridge:")
        if h.get("cut") is True:
            line += " It has more files than Drawbridge walks, so some were not seen at all."
        lines.append(line)
    lines += [str(c.get("yes_means") or "Your Yes lets Toolsmith try it. Nothing from it runs before that."),
              str(c.get("ticks") or "Only the apps you tick are tried. A Yes with nothing ticked tries none of them."),
              "No: Toolsmith tries none of them, and this job ends as not yet.",
              "Nothing runs and nothing is installed before you answer. This card closes after 10 minutes "
              "with no answer, like a No."]
    return lines


def _pieces(line: str) -> list[str]:
    return textwrap.wrap(line, COMMAND_LINE, break_on_hyphens=False, drop_whitespace=False, replace_whitespace=False,
                         expand_tabs=False) or [""]


def _command_lines(c: dict) -> list[str]:
    """A chat command's card (D138): the whole command, cut only into lines every surface can show, then its folder.
    The pieces put together are the command: nothing is left out, and a long word can't run off a card."""
    lines = [str(c.get("what") or "")]
    label = "Command: "
    for part in str(c.get("command") or "").split("\n"):
        for piece in _pieces(part):
            lines.append(label + piece)
            label = ""
    label = "It starts exactly: "
    for piece in _pieces(str(c.get("argv") or "")) if c.get("argv") else ():    # her own program, D16: no shell
        lines.append(label + piece)
        label = ""
    return lines + [f"Folder: {c.get('folder') or ''}"]


def _tool_lines(c: dict) -> list[str]:
    if "command" in c:
        return _command_lines(c)
    if "open_file" in c:                      # open_in_program (D138)
        return [str(c.get("what") or ""), f"Program: {c.get('program') or ''}", f"File: {c.get('open_file') or ''}"]
    lines = [str(c.get("what") or "One change from chat, this time only."),
             f"App: {c.get('package_name') or c.get('package')} ({c.get('package')})",
             f"Tool: {c.get('tool')}"]
    args = _names(c.get("arguments"))
    lines.append("With: " + "; ".join(args) if args else "With: (no details)")
    if str(c.get("why") or "").strip():
        lines.append(str(c["why"]).strip())
    return lines


def _l4_lines(c: dict) -> list[str]:
    scope = c.get("scope") if isinstance(c.get("scope"), dict) else {}
    covers = c.get("covers") if isinstance(c.get("covers"), dict) else {}
    plan = covers.get("card") if isinstance(covers.get("card"), dict) else None
    if plan is not None:                      # a Toolsmith build plan (lane L6): its own sentence and lines
        lines = [str(plan.get("sentence") or "")] + _names(plan.get("lines"))
    else:
        lines = [f"What it is: {scope.get('kind')} for {scope.get('target')}"]
    for key, label in (("hosts", "Places on the internet"), ("install_hosts", "Places while it installs"),
                       ("knowledge_hosts", "Places it reads knowledge from, only while this build runs"),
                       ("subhosts", "Every name under"), ("lan", "Places on your home network")):
        if _names(covers.get(key)):
            lines.append(_list(label, covers.get(key), ""))
    bound = c.get("bound") if isinstance(c.get("bound"), dict) else {}
    if _names(bound.get("labels")):
        lines.append(_list("Kinds of private data", bound.get("labels"), ""))
    if c.get("expires"):
        lines.append(f"Until: {c['expires']}")
    return lines


def view(card: Any, floor: Any = None) -> dict:
    """``{"title", "lines", "floor"}`` for one card (a stored card dict)."""
    c = card if isinstance(card, dict) else {}
    is_floor = floor_of(c, floor)
    kind = _kind(c)
    if kind == "tool_call":
        title, lines = str(c.get("title") or "May I use this tool?"), _tool_lines(c)
    elif kind == "package" and isinstance(c.get("try_holds"), list):
        title, lines = str(c.get("title") or "Let Toolsmith try these apps?"), _try_holds_lines(c)
    elif kind == "package" and isinstance(c.get("try_hold"), dict):
        title, lines = str(c.get("title") or "Let Toolsmith try this app?"), _try_hold_lines(c)
    elif (kind == "package" and "puffpai_yes" in c) or "drawbridge" in c:
        title, lines = str(c.get("title") or "Add this?"), _package_lines(c)
    elif kind == "model_pull":                # a local model download (lane I17): the Core's own lines
        title, lines = str(c.get("title") or "Download this model?"), _names(c.get("lines"))
    elif kind == "provider_swap":
        title = str(c.get("title") or "Use this provider?")
        lines = [f"What it is: {c.get('name')} ({c.get('id')}) for {c.get('slot')}",
                 f"Instead of: {c.get('from') or 'nothing'}"]
    elif "puffpai_yes" in c:
        title = str(c.get("title") or "Your Yes is needed")
        lines = [f"{k}: {v}" for k, v in c.items() if k not in ("title", "puffpai_yes") and isinstance(v, (str, int))]
    else:
        covers = c.get("covers") if isinstance(c.get("covers"), dict) else {}
        plan = covers.get("card") if isinstance(covers.get("card"), dict) else {}
        title, lines = str(plan.get("title") or "Your Yes is needed"), _l4_lines(c)
    if is_floor:
        lines.append(PIN_LINE)
    return {"title": title, "lines": [ln for ln in lines if ln], "floor": is_floor}


__all__ = ["ADDRESS_LINES", "PIN_LINE", "TUNNEL_DOES", "TUNNEL_SHORT", "TUNNEL_WHERE", "TUNNEL_WORDS", "floor_of", "view"]
