r"""The model slot's one settings place: ``<state>\settings\model.json`` (01 "Zone 2").

Four owner choices live here: the model name, the model server's address,
the context size and the tool budget. The wizard (lane L5) writes them with
:func:`save`; the brain reads them with :func:`load`. v1 kept these in three
files and two ports; v2 has this one file.

Nothing is hard-coded. The wizard's scan asks this computer's Ollama for its
version (``OLLAMA_HOST``'s port, then 11434, then 11435, on 127.0.0.1 only,
:func:`find_local_ollama`) and saves the address that answered here; the Core
reads it from this file afterwards. A missing address comes from Ollama's own
setting (``OLLAMA_HOST``) and only then from Ollama's documented default. A missing
context size or tool budget comes from the model's profile (``catalog``).
A missing model name means no brain was picked yet; the brain says so.

The address check is floor item X12: the brain runtime is local. A runtime
on another computer in the home is allowed only by its exact address in
``lan_hosts`` (D28(19)), and it gets handles only, never real values (D25).
Anything else is refused.
"""

from __future__ import annotations

import ipaddress
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ..paths import state_dir
from . import catalog

#: Ollama's documented default, used only when neither the settings file nor
#: OLLAMA_HOST names an address.
OLLAMA_DEFAULT = "http://127.0.0.1:11434"
FILE = "model.json"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})

NOT_LOCAL = "This brain runs on the internet. Only Toolsmith may."


@dataclass
class ModelSettings:
    runtime: str = "ollama"
    address: str = ""
    model: str = ""
    context: int | None = None
    tool_budget: int | None = None
    timeout_s: float = 180.0
    lan_hosts: list[str] = field(default_factory=list)
    pinned: list[str] = field(default_factory=list)      # "<pkg>.<tool>" always shown

    # -- derived -------------------------------------------------------------
    def profile(self) -> dict:
        return catalog.get_profile(self.model)

    def context_size(self) -> int:
        budgets = self.profile().get("budgets") or {}
        cap = int(budgets.get("num_ctx") or 4096)
        return min(int(self.context), cap) if self.context else cap

    def tools_allowed(self) -> int:
        cap = int(self.profile().get("tool_budget") or 16)
        return max(1, min(int(self.tool_budget), cap)) if self.tool_budget else cap

    def think(self) -> bool:
        """``think`` for answer calls: off unless the profile says the model needs it."""
        return bool(self.profile().get("needs_thinking", False))

    def think_in_chat(self) -> bool:
        """``think`` for the brain's chat turns (tool loop): also on when the profile says ``think_in_chat``
        (a model whose reasoning spills into its chat answers with thinking off). The reasoning comes back
        in Ollama's own field, which the Core never shows or keeps."""
        return self.think() or bool(self.profile().get("think_in_chat", False))

    def where(self) -> str:
        """``local`` | ``lan`` | ``refused`` (X12)."""
        return address_kind(self.address, self.lan_hosts)

    def as_dict(self) -> dict:
        out = asdict(self)
        out["effective"] = {"context": self.context_size(), "tool_budget": self.tools_allowed(),
                            "think": self.think(), "think_in_chat": self.think_in_chat(), "where": self.where()}
        return out


def _from_ollama_host(value: str) -> str:
    """``OLLAMA_HOST`` (``0.0.0.0:11435``, ``host``, ``http://host:port``) as a URL to call."""
    value = value.strip()
    if not value:
        return ""
    if "://" not in value:
        value = "http://" + value
    parts = urlsplit(value)
    host = parts.hostname or "127.0.0.1"
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"          # "listen everywhere" is called on this computer
    port = parts.port or 11434
    host = f"[{host}]" if ":" in host else host
    return f"{parts.scheme or 'http'}://{host}:{port}"


def default_address() -> str:
    return _from_ollama_host(os.environ.get("OLLAMA_HOST", "")) or OLLAMA_DEFAULT


#: Ollama's documented port, then the second one some installs use; OLLAMA_HOST's port is asked first.
OLLAMA_PORTS = (11434, 11435)


def _ask_version(url: str, timeout_s: float = 1.5) -> bool:
    """True when an Ollama answers ``GET <url>/api/version`` (on this computer, no proxy)."""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/api/version", timeout=timeout_s) as resp:
            data = json.loads(resp.read(4096).decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and bool(data.get("version"))


def ollama_candidates(env: dict | None = None) -> list[str]:
    """The addresses to ask, in order: OLLAMA_HOST's port, then 11434, then 11435. Always 127.0.0.1."""
    env = os.environ if env is None else env
    ports: list[int] = []
    from_env = _from_ollama_host(str(env.get("OLLAMA_HOST") or ""))
    if from_env:
        port = urlsplit(from_env).port
        if port:
            ports.append(port)
    ports += [p for p in OLLAMA_PORTS if p not in ports]
    return [f"http://127.0.0.1:{p}" for p in ports]


def _has_model(url: str, model: str, timeout_s: float = 2.0) -> bool:
    """True when that Ollama's ``/api/tags`` lists *model* (``name`` or ``name:latest``)."""
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url + "/api/tags", timeout=timeout_s) as resp:
            data = json.loads(resp.read(2_000_000).decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return False
    names = {str(m.get("name") or m.get("model") or "") for m in (data.get("models") or [])
             if isinstance(m, dict)} if isinstance(data, dict) else set()
    return model in names or f"{model}:latest" in names


def find_local_ollama(env: dict | None = None, ask=None, model: str = "", has=None) -> str:
    """The address of the Ollama running on this computer, or "". Every candidate that answers with its
    version counts; when the owner already picked *model*, the one whose ``/api/tags`` lists it wins,
    else the first that answered."""
    ask = ask or _ask_version
    has = has or _has_model
    answered = [url for url in ollama_candidates(env) if ask(url)]
    if model:
        with_it = next((url for url in answered if has(url, model)), "")
        if with_it:
            return with_it
    return answered[0] if answered else ""


def address_kind(address: str, lan_hosts: list[str] | None = None) -> str:
    """X12: ``local`` for this computer, ``lan`` for an exact home address the
    owner added, else ``refused``."""
    try:
        host = (urlsplit(address).hostname or "").lower()
    except ValueError:
        return "refused"
    if host in LOOPBACK:
        return "local"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return "refused"            # a name could point anywhere; home brains are added by address
    if ip.is_loopback:
        return "local"
    if ip.is_private and host in {h.strip().lower() for h in (lan_hosts or [])}:
        return "lan"
    return "refused"


def path(state: Path | None = None) -> Path:
    return (Path(state) if state else state_dir()) / "settings" / FILE


def load(state: Path | None = None) -> ModelSettings:
    """The settings, with the address filled in. A broken file reads as no file."""
    data: dict = {}
    try:
        data = json.loads(path(state).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    known = {k: v for k, v in data.items() if k in ModelSettings.__dataclass_fields__}
    s = ModelSettings(**known)
    if not s.address:
        s.address = default_address()
    return s


def save(state: Path | None = None, **changes) -> ModelSettings:
    """Change some settings and write the file. Unknown names are refused."""
    unknown = [k for k in changes if k not in ModelSettings.__dataclass_fields__]
    if unknown:
        raise ValueError(f"unknown model setting {unknown[0]!r}")
    target = path(state)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data.update(changes)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return load(state)


__all__ = ["ModelSettings", "NOT_LOCAL", "OLLAMA_DEFAULT", "OLLAMA_PORTS", "address_kind", "default_address",
           "find_local_ollama", "load", "ollama_candidates", "path", "save"]
