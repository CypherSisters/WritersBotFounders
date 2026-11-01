"""Core API group ``addresses`` (lane lan-address, D110): one address and port for one package, with its own Yes.

The rules, the card and the limits: ``zone3/addresses.py``. Nothing here grants. A grant comes only from the owner's Yes on
the card (``POST /v1/consent/<id>/answer``, a paired device only), through the Bridge's own Yes listener.

- ``POST /v1/bridge/addresses/ask`` ``{address}``: a package's own token only, for itself only, and only for the kind its
  installed manifest declares (``needs.addresses``). Reply ``{state: allowed | waiting | not_needed | refused, line}``,
  its own state and nothing else: never a card's id or fingerprint. A refusal also says ``error``, the same words as ``line``.
- ``GET /v1/bridge/addresses``: the rows, and the installed packages her form may pick.
- ``POST /v1/bridge/addresses`` ``{package, address}``: her own form, any installed package, either kind, no manifest field
  needed. Replies with the card for the Desktop's inline Yes.
- ``DELETE /v1/bridge/addresses/<consent_id>``: removes it: the Yes and its grant, one action.
"""

from __future__ import annotations

from flask import Blueprint, current_app, g

from ... import anchor as _anchor
from ...consent import ConsentError, card_view
from ...packages import manifest as manifest_mod
from ...packages.manager import SYSTEM_PARTS
from ...zone3 import addresses
from ...zone3.bridge import BUILD_TARGET
from ...zone3.service import for_app
from ..auth import OWNER, OWNER_DEVICE, PACKAGE_OK
from ..routing import current_core, route
from .zones import _body, _no_store

bp = Blueprint("addresses", __name__)
LANE = "lan-address"
PACKAGES_ONLY_LINE = "This is for a package asking for itself. The owner uses the Bridge page."
NOT_INSTALLED_LINE = "That package is not installed."
NO_ROW_LINE = "There is no such address."
NO_CARD_LINE = "No card could be raised right now."
ANSWER_LINES = {"allowed": "Allowed. The owner said Yes to this address and port.",
                "waiting": "Waiting for the owner's Yes. Nothing opens before that."}


def built(method: str, rule: str, access: str):
    def deco(fn):
        fn.puffpai_built_by = LANE
        return route(bp, method, rule, access)(fn)
    return deco


def _may_hold(row: dict) -> bool:
    """BuildBot's own parts, a build's tries and a raw reader (no network, ever: D31(c), X13) never hold an address."""
    pid = str(row.get("id") or "")
    return pid not in SYSTEM_PARTS and not pid.startswith(BUILD_TARGET) and row.get("reads") != "raw"


def _view(z, names: dict) -> dict:
    """The Bridge view, or a closed one when the policy can't be read (no machine key, a file in use): never a 500."""
    try:
        return z.bridge.view(names)
    except (_anchor.AnchorUnavailable, OSError):
        return {"setting": "closed"}


def _closed(z) -> bool:
    return _view(z, {}).get("setting") != "open"


def _blocked(z, host: str) -> bool:
    try:
        return z.bridge.policy.blocked(host)
    except (_anchor.AnchorUnavailable, OSError):
        return True


def _refused(line: str, status: int = 400):
    return _no_store({"state": "refused", "error": line, "line": line}, status)


@built("POST", "/v1/bridge/addresses/ask", PACKAGE_OK)
def addresses_ask():
    caller = g.caller
    if caller.kind != "package" or not caller.package_id:
        return _refused(PACKAGES_ONLY_LINE, 403)
    core = current_core()
    package = caller.package_id                      # always itself: nothing in the body can name another package
    row = core.registry.get(package)
    if row is None or not _may_hold(row):
        return _refused(addresses.NOT_FOR_LINE, 403)
    manifest = manifest_mod.read_installed(core.registry, core.installs, package)     # the file her Yes covers, or None
    declared = ((manifest or {}).get("needs") or {}).get("addresses")
    if declared not in addresses.KINDS:
        return _refused(addresses.NOT_DECLARED_LINE, 403)
    try:
        address = addresses.parse(_body().get("address"))
    except addresses.NotNeeded as exc:
        return _no_store({"state": "not_needed", "line": exc.line})
    except addresses.Refused as exc:
        return _refused(exc.line)
    if address.where != declared:
        return _refused(addresses.WRONG_KIND[declared], 403)
    z = for_app(current_app)
    try:
        got = addresses.ask(z.consents, z.bridge.policy, package, row.get("name") or package, address,
                            closed=_closed(z), by_package=True)
    except addresses.Refused as exc:
        return _refused(exc.line, 429)
    except (ConsentError, _anchor.AnchorUnavailable):
        return _refused(NO_CARD_LINE, 503)
    return _no_store({"state": got["state"], "line": got.get("line") or ANSWER_LINES[got["state"]]},
                     200 if got["state"] == "allowed" else 202)


@built("GET", "/v1/bridge/addresses", OWNER)
def addresses_get():
    core = current_core()
    z = for_app(current_app)
    listed = core.registry.list()
    names = {r["id"]: r.get("name") or r["id"] for r in listed}
    view = _view(z, names)
    return _no_store({"addresses": addresses.rows(z.consents, view, names, lambda host: _blocked(z, host)),
                      "setting": view.get("setting"), "note": "" if z.anchor.available else _anchor.NO_KEY_LINE,
                      "packages": [{"id": r["id"], "name": addresses.safe_name(names[r["id"]])} for r in listed if _may_hold(r)]})


@built("POST", "/v1/bridge/addresses", OWNER_DEVICE)
def addresses_post():
    body = _body()
    package = body.get("package")
    core = current_core()
    row = core.registry.get(package) if isinstance(package, str) and package else None
    if row is None:
        return _no_store({"state": "refused", "error": NOT_INSTALLED_LINE, "line": NOT_INSTALLED_LINE}, 404)
    if not _may_hold(row):
        return _no_store({"state": "refused", "error": addresses.NOT_FOR_LINE, "line": addresses.NOT_FOR_LINE}, 400)
    try:
        address = addresses.parse(body.get("address"))
    except addresses.NotNeeded as exc:
        return _no_store({"state": "not_needed", "line": exc.line})
    except addresses.Refused as exc:
        return _no_store({"state": "refused", "error": exc.line, "line": exc.line}, 400)
    z = for_app(current_app)
    try:
        got = addresses.ask(z.consents, z.bridge.policy, package, row.get("name") or package, address,
                            closed=_closed(z), by_package=False)
    except ConsentError as exc:
        said = str(getattr(exc, "message", "") or exc)
        return _no_store({"state": "refused", "error": said, "line": said}, getattr(exc, "status", 503))
    except _anchor.AnchorUnavailable:
        return _no_store({"state": "refused", "error": NO_CARD_LINE, "line": NO_CARD_LINE}, 503)
    if got["state"] == "allowed":
        return _no_store({"state": "allowed", "line": "You already allowed this address and port for this package."})
    rec = got.get("record")
    if rec is None:                                  # her Yes stands, and its grant can't be read now: no second card
        return _no_store({"state": "waiting", "line": got.get("line") or NO_CARD_LINE})
    seen = card_view(rec.get("card") if isinstance(rec.get("card"), dict) else {}, bool(rec.get("floor")))
    return _no_store({"state": "waiting", "consent_id": rec["consent_id"], "card_sha": rec.get("card_sha"),
                      "title": seen["title"], "lines": seen["lines"], "line": "Its Yes card is below, and on Your turn."}, 202)


@built("DELETE", "/v1/bridge/addresses/<consent_id>", OWNER_DEVICE)
def addresses_delete(consent_id: str):
    z = for_app(current_app)
    rec = z.consents.get(consent_id)
    if rec is None or addresses.package_of(rec) is None or rec.get("status") not in ("pending", "granted"):
        return _no_store({"error": NO_ROW_LINE}, 404)
    done = addresses.take_back(z.consents, rec, z.bridge.drop_consent, "You removed this address.")
    if not done:
        return _no_store({"error": "That Yes could not be taken back. Check your Yes cards."}, 409)
    return _no_store({"deleted": True, "line": "Removed. This package can't reach that address any more."})
