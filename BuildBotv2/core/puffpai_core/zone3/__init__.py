"""Zone 3, the Bridge (lanes L4 and L6).

Built (L4):
- ``policy``: the one decision point. ``decide(request, policy, consents)``
  (rules only), grants only from the owner's Yes records, closed by default.
- ``bridge.Bridge``: ``request(...)`` / ``allowed(subject, host, labels)``;
  answers ``egress.request`` as Drawbridge, publishes ``egress.decision`` and
  ``bridge.posture`` (Core only), writes the ledger.
- ``puffpai_core.consent.ConsentStore`` (moved out of Zone 3: one store for the
  Core, lane L1's names): cards and Yes records; floor-level cards need the PIN
  through lane L3's ``verify_pin`` seam (D29).
- ``chain.ChainLog``, ``ledger.Ledger``: hash-chained outside log and audit log,
  each with a head sealed under the machine key (``puffpai_core.anchor``).
- ``proxy.Proxy``: the built-in proxy on 127.0.0.1:8421 (CONNECT and plain HTTP),
  identity by a per-package credential in ``Proxy-Authorization``.
- ``bypass.BypassWatch``: stop on bypass (D26), using Steward's ``running()``,
  ``pids(id)`` (the whole Job Object) and ``stop(id, reason)``.
- ``docker_wall.override(manifest, compose_path)``: the Docker wall (D30);
  ``docker_wall.probe(id)``: the Core's own "held" test (F11).
- ``drawbridge.Drawbridge``: ``check_package``, ``classify_tools``,
  ``review_action`` (rules only, under 50 ms).
- ``toolsmith_zone.check(path | command)``: Toolsmith's places, fail closed.
- ``service.Zone3``: all of the above for one Core.

Built (L6):
- ``toolsmith``: Toolsmith (find, adopt, run, prove; it lives here, D32), which
  runs in its OWN process (09 section 3) and reaches the Core only through its
  Toolsmith-token routes.
- ``toolsmith_host``: the Core's side of it: those routes, the exec broker,
  Toolsmith's process in a job object, and the ``zone3.toolsmith-model``
  sender (D35) with three data-only providers shipped with the Core
  (Anthropic, OpenAI, Google, D44).

Slot contracts: ``zone3.slots``.
"""
