r"""The active Desktop (a ``ui`` package), the active avatar, and the theme (D3).

Owner choices live in ``settings\desktop.json``: ``{"ui": "<id>", "avatar": "<id>" | null}``.
A package is found among the installed ones first (a Registry row: its folder and its manifest from the
Core's one reader, ``packages.manifest.read_installed``, so only the file her install Yes covered, by its
sha256), then among the packages that ship with PuffPAI (the repo's ``packages\`` folder, or
``PUFFPAI_BUILTIN``). A folder in ``packages\<id>\`` with no Registry row (a try's download, say) is
never read. The default Desktop is the built-in ``desktop`` package.

Nothing here knows any one package: a ``ui`` or ``avatar`` package is found by
its kind, in the Registry or among the built-in packages.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, NamedTuple

from . import jsonfile
from .packages import manifest as manifest_mod
from .packages.installs import Installs

DEFAULT_UI = "desktop"

THEME = {
    "name": "PuffPAI paper",
    "colors": {
        "paper": "#f5efe3", "paper_2": "#ece4d3", "ink": "#1f1c1a", "ink_2": "#5a534c",
        "rule": "#d6ccb8", "coral": "#e0684f", "pink": "#f2b8ae", "ok": "#3f7a52", "warn": "#b5651d",
    },
    "fonts": {
        "body": "system-ui, -apple-system, 'Segoe UI', sans-serif",
        "display": "'Iowan Old Style', 'Palatino Linotype', Georgia, serif",
        "mono": "ui-monospace, 'Cascadia Mono', Consolas, monospace",
    },
    "radius": "3px",
}


def builtin_dir() -> Path:
    override = os.environ.get("PUFFPAI_BUILTIN", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "packages"


def _broken(registry: Any):
    """A changed manifest file: the row is broken, with its reason (as Caretaker's ``mark_broken``)."""
    def mark(id: str, reason: str) -> None:
        row = registry.get(id)
        if row is not None and row.get("status") != "broken":
            registry.set_health(id, False, reason)
            registry.update(id, status="broken")
    return mark


def _installed(state: Path, registry: Any, package_id: str) -> tuple[Path, dict] | None:
    """An installed package's folder and manifest, from the Core's one reader; None if not usable."""
    if registry is None or not isinstance(package_id, str) or registry.get(package_id) is None:
        return None
    installs = Installs(state)
    data = manifest_mod.read_installed(registry, installs, package_id, broken=_broken(registry))
    folder = installs.folder(package_id)
    return (folder, data) if data is not None and folder is not None else None


def _builtin(package_id: str) -> Path | None:
    if not isinstance(package_id, str) or not package_id or Path(package_id).name != package_id:
        return None
    folder = builtin_dir() / package_id
    return folder if manifest_mod.find(folder) is not None else None


def package_dir(state: Path, package_id: str, registry: Any = None) -> Path | None:
    """The folder of *package_id*: installed (by its Registry row), else built-in; or None."""
    got = _installed(state, registry, package_id)
    return got[0] if got is not None else _builtin(package_id)


def read_manifest(state: Path, package_id: str, registry: Any = None) -> dict | None:
    got = _installed(state, registry, package_id)
    if got is not None:
        return got[1]
    folder = _builtin(package_id)
    if folder is None:
        return None
    try:
        return json.loads(manifest_mod.find(folder).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def has_process(state: Path, package_id: str, registry: Any = None) -> bool:
    """Whether a package runs a process of its own: a run block for this computer that is not ``static``.
    Read through :func:`read_manifest` (the Core's one reader). A manifest that can't be read counts as a
    process, so nothing is called ready by mistake."""
    from .steward.steward import run_block
    if registry is not None and registry.get(package_id) is not None:
        got = _installed(state, registry, package_id)   # installed: only the file her Yes covered (by sha)
        manifest = got[1] if got is not None else None   # a failed check is never the built-in's file
    else:
        manifest = read_manifest(state, package_id, registry)
    if not manifest:
        return True
    block = run_block(manifest)
    return block is not None and block.get("type") != "static"


def builtin_of_kind(kind: str) -> list[str]:
    base = builtin_dir()
    out = []
    if base.is_dir():
        for folder in sorted(base.iterdir()):
            path = manifest_mod.find(folder)
            if path is None:
                continue
            try:
                if json.loads(path.read_text(encoding="utf-8-sig")).get("kind") == kind:
                    out.append(folder.name)
            except (OSError, ValueError):
                continue
    return out


def _settings_path(state: Path) -> Path:
    return Path(state) / "settings" / "desktop.json"


def choices(state: Path, registry: Any) -> dict:
    return jsonfile.read(_settings_path(state), {"ui": DEFAULT_UI, "avatar": None})


def ui_ids(registry: Any) -> list[str]:
    return sorted({r["id"] for r in registry.list(kind="ui")} | set(builtin_of_kind("ui")))


def avatar_ids(registry: Any) -> list[str]:
    return sorted({r["id"] for r in registry.list(kind="avatar")} | set(builtin_of_kind("avatar")))


def set_ui(state: Path, registry: Any, ui_id: Any) -> dict:
    if not isinstance(ui_id, str) or ui_id not in ui_ids(registry):
        raise ValueError(f"There is no Desktop package with the id {ui_id!r}. Installed: {', '.join(ui_ids(registry))}.")
    data = choices(state, registry)
    data["ui"] = ui_id
    jsonfile.write(_settings_path(state), data)
    return data


def set_avatar(state: Path, registry: Any, avatar_id: Any) -> dict:
    if avatar_id is not None and (not isinstance(avatar_id, str) or avatar_id not in avatar_ids(registry)):
        raise ValueError(f"There is no avatar package with the id {avatar_id!r}.")
    data = choices(state, registry)
    data["avatar"] = avatar_id
    jsonfile.write(_settings_path(state), data)
    return data


def active_ui(state: Path, registry: Any) -> str:
    chosen = choices(state, registry).get("ui") or DEFAULT_UI
    return chosen if package_dir(state, chosen, registry) is not None else DEFAULT_UI


def static_root(state: Path, ui_id: str, registry: Any = None) -> Path | None:
    """The folder a static ``ui`` package serves from, or None (its own process, or not found)."""
    folder = package_dir(state, ui_id, registry)
    data = read_manifest(state, ui_id, registry)
    if folder is None or not data:
        return None
    from .steward.steward import run_block
    block = run_block(data) or next(iter((data.get("run") or {}).values()), None)
    if not block or block.get("type") != "static":
        return None
    return (folder / block.get("dir", ".")).resolve()


class DashboardLink(NamedTuple):
    url: str
    code: str      # "" when no code was made
    line: str      # what to say instead of the link, or ""


def own_desktop_is_active(state: Path | str) -> bool:
    """True when the active ui package is PuffPAI's own (``zone2.apps.puffpai_own``) and its pages are on the Core's own
    address. A ui with a process of its own is on the pages address, where no owner login reaches (D49). A check that
    fails says no."""
    from .registry import Registry
    from .zone2.apps import puffpai_own
    try:
        state = Path(state)
        registry = Registry(state / "registry.db")
        ui_id = active_ui(state, registry)
        row = registry.get(ui_id) or {"id": ui_id, "origin": f"local:{builtin_dir() / ui_id}"}    # a built-in, no row yet
        return puffpai_own(row) and static_root(state, ui_id, registry) is not None
    except Exception:  # noqa: BLE001 - fail closed
        return False


def dashboard_link(state: Path | str, port: int) -> DashboardLink:
    """Every dashboard link made for her with no explicit ask for a code (D108): the window's button, ``/dashboard``, the
    end of setup, ``puffpai start``. A one-time code in the link reaches the active Desktop's own script, so only
    PuffPAI's own gets one. Any other gets the plain address and one plain line. ``puffpai pair`` and
    ``Sign in a browser.cmd`` are her explicit ask, and stay as they are."""
    from . import pairing
    from .wizard.terminal import cli_command, how_to_run, signin_launcher
    origin = f"http://puffpai.localhost:{port}"
    if own_desktop_is_active(state):
        code = pairing.new_code(state)
        return DashboardLink(f"{origin}/#pair={code}", code, "")
    signin = signin_launcher()
    how = how_to_run(signin) if signin.is_file() \
        else f"run this in a terminal: {cli_command('pair', '--running')}"
    return DashboardLink(f"{origin}/", "", "Your Desktop is not one of PuffPAI's own, so it gets no sign-in link from "
                                           f"here. To sign it in, {how}.")


def avatar_view(state: Path, registry: Any) -> dict | None:
    avatar_id = choices(state, registry).get("avatar")
    if not avatar_id:
        # None picked: the avatar installed first (setup's), so adding another face never changes hers by
        # itself (lane looks); with none installed, the first built-in, as before.
        rows = sorted((r for r in registry.list(kind="avatar") if r.get("status") not in ("removed", "broken")),
                      key=lambda r: (str(r.get("installed_at") or ""), r["id"])) if registry is not None else []
        ids = [r["id"] for r in rows] or avatar_ids(registry)
        avatar_id = ids[0] if ids else None
    if not avatar_id:
        return None
    data = read_manifest(state, avatar_id, registry) or {}
    block = data.get("avatar") or {}
    preview = block.get("preview")
    return {"id": avatar_id, "name": data.get("name", avatar_id),
            "preview": f"/pkg/{avatar_id}/{preview}" if preview else None,
            "model": f"/pkg/{avatar_id}/{block['model']}" if block.get("model") else None}


def theme(state: Path, registry: Any) -> dict:
    """The look tokens: the defaults, the active ui package's theme.json, then settings\\theme.json."""
    out = json.loads(json.dumps(THEME))
    layers = []
    folder = package_dir(state, active_ui(state, registry), registry)
    if folder is not None:
        layers.append(jsonfile.read(folder / "theme.json", {}))
    layers.append(jsonfile.read(Path(state) / "settings" / "theme.json", {}))
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        for group in ("colors", "fonts"):
            if isinstance(layer.get(group), dict):
                out[group].update({k: str(v) for k, v in layer[group].items()})
        for key in ("name", "radius"):
            if isinstance(layer.get(key), str):
                out[key] = layer[key]
    props = [f"--pp-{k.replace('_', '-')}: {v};" for k, v in out["colors"].items()]
    props += [f"--pp-font-{k}: {v};" for k, v in out["fonts"].items()]
    props.append(f"--pp-radius: {out['radius']};")
    out["css"] = ":root { " + " ".join(props) + " }"
    return out
