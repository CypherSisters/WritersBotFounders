"""Core API group ``store`` (lane store, D89): the PuffPAI Store's indexes and the owner's own store sources.

- ``GET /v1/store``: every source's rows merged (the official store first, then hers, in order). Each row has
  ``tier`` and ``origin`` as the Core will record them (Verified only from the official store), ``listed_tier``
  (what the index says), its ``source``, ``used`` (the row ``store:<id>`` installs) and ``yanked``. ``sources``
  says per source whether it was reached, or that an older saved copy is shown (``stale``). ``?fresh=1`` asks
  every source again now. ``?saved=1`` reads the saved copies only and never the network (D96): what the Desktop
  asks when she did not ask, as for Home's question.
- ``GET /v1/store/sources``; ``POST /v1/store/sources`` ``{source}`` (an https index address, or a folder or
  file on this computer); ``DELETE /v1/store/sources`` ``{source}`` (or ``?source=``). The official store is
  always first and can't be removed. Adding or removing a source is for the owner at a paired device.
- ``GET /v1/store/updates``: installed store packages whose source now pins a different commit. Apply one with
  ``POST /v1/packages/<id>/update`` (its Yes card).

Install with ``POST /v1/packages {"source": "store:<id>"}``: the same path and Yes card as any package.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ...packages.store_index import StoreError
from ..auth import OWNER, OWNER_DEVICE
from ..routing import current_core, route

bp = Blueprint("store", __name__)
LANE = "store"


def built(method: str, rule: str, access: str):
    def deco(fn):
        fn.puffpai_built_by = LANE
        return route(bp, method, rule, access)(fn)
    return deco


def _store():
    return current_core().packages.store


def _fresh() -> bool:
    return str(request.args.get("fresh") or "").lower() in ("1", "true", "yes")


def _problem(exc: StoreError):
    return jsonify(error=exc.problems[0], problems=exc.problems), exc.status


def _source_arg():
    body = request.get_json(silent=True) or {}
    got = body.get("source") if isinstance(body, dict) else None
    return got if isinstance(got, str) else request.args.get("source")


@built("GET", "/v1/store", OWNER)
def store_list():
    if str(request.args.get("saved") or "").lower() in ("1", "true", "yes"):
        return jsonify(_store().saved_listing())
    return jsonify(_store().listing(fresh=_fresh(), notices=getattr(current_core(), "notices", None)))


@built("GET", "/v1/store/sources", OWNER)
def store_sources():
    return jsonify(sources=_store().sources())


@built("POST", "/v1/store/sources", OWNER_DEVICE)
def store_sources_add():
    try:
        return jsonify(_store().add_source(_source_arg())), 201
    except StoreError as exc:
        return _problem(exc)


@built("DELETE", "/v1/store/sources", OWNER_DEVICE)
def store_sources_remove():
    try:
        return jsonify(_store().remove_source(_source_arg()))
    except StoreError as exc:
        return _problem(exc)


@built("GET", "/v1/store/updates", OWNER)
def store_updates():
    return jsonify(_store().updates(fresh=_fresh(), notices=getattr(current_core(), "notices", None)))
