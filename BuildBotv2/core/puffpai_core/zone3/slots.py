"""Zone 3 slot contracts (contract 1), as the Core sees them.

Source: ``docs/zones/Z3_BRIDGE_PROVIDERS.md`` section 4, names from
``docs/zones/ZONES_TOGETHER.md`` section 1; ``zone3.toolsmith-model`` from D35.
Providers enforce, watch or feed blocks; they never decide or open anything
(the decision is the Core's, Drawbridge ``decide()``). Every call has a
timeout (default 20 s); a timeout or junk answer is ``failed``, never "allowed".
Common error codes: ``unsupported``, ``needs_admin``, ``unreachable``,
``auth_failed``, ``busy``, ``stale_base``, ``failed``.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

Result = dict[str, Any]


@runtime_checkable
class BridgeEnforcer(Protocol):
    """``zone3.bridge-enforcer``: makes the decision real on this computer.

    Never self-certifies: the Core's own probe decides "held" (D31(e)).
    """

    def describe(self) -> Result: ...       # {strength: cooperative|os|container, covers, platforms, needs_admin}
    def apply(self, snapshot: dict) -> Result: ...   # {applied_version, covered, not_covered}; idempotent
    def verify(self) -> Result: ...         # {version, ok, drift: [{what, expected, found}]}
    def events(self, since: str | None) -> list[dict]: ...   # [{t, subject, host, port, via, outcome, reason_code, bytes_in, bytes_out}]
    def remove_all(self) -> Result: ...     # {removed, leftovers}


@runtime_checkable
class NetworkDevice(Protocol):
    """``zone3.network-device``: routers, firewalls, DNS filters. Watch first (D22).

    ``plan_change`` has no side effects. ``apply_change`` needs a ``consent_id``
    for that one change; main-router DNS and DHCP are never changed (D31(f)).
    """

    def describe(self) -> Result: ...       # {device_kinds, models, can_watch, can_change}
    def read_state(self, device: str) -> Result: ...           # {snapshot, base_hash, taken_at}
    def report(self, device: str, version: str) -> Result: ...  # {findings: [{kind, summary_code, counts, would_change}]}
    def plan_change(self, device: str, intent: dict) -> Result: ...  # ChangePlan
    def apply_change(self, plan_id: str, consent_id: str, credential_handle: str) -> Result: ...  # {applied, new_hash} | stale_base | partial
    def undo(self, plan_id: str, credential_handle: str) -> Result: ...   # {restored, hash}
    def alive(self, device: str) -> Result: ...                 # {mgmt_ok, dns_ok, internet_ok}


@runtime_checkable
class NetMonitor(Protocol):
    """``zone3.net-monitor``: device watchers and IDS. Report only; no change tools at all."""

    def describe(self) -> Result: ...
    def snapshot(self) -> list[dict]: ...   # [{address, name, vendor, first_seen}]
    def alerts(self, since: str | None) -> list[dict]: ...   # [{t, severity, device, kind, summary_code, suggest}]


@runtime_checkable
class PolicyFeed(Protocol):
    """``zone3.policy-feed``: block lists and threat feeds. Blocks only; the Core parses entries itself."""

    def describe(self) -> Result: ...       # {feed_id, kind: blocklist|threat|telemetry, license, every_h}
    def fetch(self) -> Result: ...          # {version, sha256, entries: [{pattern, kind: host|suffix, category}]}


@runtime_checkable
class ToolsmithModel(Protocol):
    """``zone3.toolsmith-model`` (D35, D42): a frontier model for Toolsmith only.

    The two operations of 04 quick start 11 (contract 1). The adapter only
    translates; it never sees the key (it writes ``{key}`` in the declared
    header) and never sends: the Core adds the key, sends only to the
    provider's declared hosts through the Bridge, and counts the cost. A
    data-only provider has no process: it names a ``wire`` mapping the Core
    runs (``zone3.toolsmith_host.wire``).
    """

    def build_request(self, req: dict) -> Result: ...
    # req: {model, system, tools: [{name, description, input_schema}], messages: [{role, content: [blocks]}],
    #       max_tokens, purpose: find|build} -> {url, headers, body}
    def read_reply(self, reply: dict) -> Result: ...
    # reply: {status, headers, body} -> {content: [blocks], stop: tool_use|end_turn|max_tokens|refusal,
    #       usage: {in, out, cache_read, cache_write}, error: None|key|busy|refused|too_big|odd_reply, retry_after_s}


__all__ = ["BridgeEnforcer", "NetMonitor", "NetworkDevice", "PolicyFeed", "ToolsmithModel"]
