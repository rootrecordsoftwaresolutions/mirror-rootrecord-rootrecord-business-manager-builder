"""Lightweight runtime plugin catalog/manifest discovery."""

from __future__ import annotations

import json
from pathlib import Path

from paths import workspace_root

def plugin_base_dir() -> Path:
    return workspace_root() / "plugins"


def ensure_plugin_dir() -> Path:
    p = plugin_base_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p


def discover_manifest_plugins() -> tuple[list[dict[str, str]], list[str]]:
    """Discover plugins from plugins/*/plugin.json manifests."""
    base = ensure_plugin_dir()
    found: list[dict[str, str]] = []
    errs: list[str] = []
    for sub in sorted([p for p in base.iterdir() if p.is_dir()]):
        mf = sub / "plugin.json"
        if not mf.exists():
            continue
        try:
            raw = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errs.append(f"{sub.name}: invalid plugin.json ({exc})")
            continue
        if not isinstance(raw, dict):
            errs.append(f"{sub.name}: manifest must be a JSON object")
            continue
        pid = str(raw.get("id") or sub.name).strip()
        name = str(raw.get("name") or pid).strip()
        version = str(raw.get("version") or "dev").strip()
        desc = str(raw.get("description") or "No description").strip()
        if not pid:
            errs.append(f"{sub.name}: missing plugin id")
            continue
        found.append({"id": pid, "name": name, "version": version, "desc": desc, "source": "manifest"})
    return found, errs


def merged_plugin_catalog() -> tuple[list[dict[str, str]], list[str]]:
    """Manifest-discovered plugins only."""
    merged: dict[str, dict[str, str]] = {}
    discovered, errs = discover_manifest_plugins()
    for p in discovered:
        merged[p["id"]] = p
    out = sorted(merged.values(), key=lambda x: x.get("name", "").lower())
    return out, errs
