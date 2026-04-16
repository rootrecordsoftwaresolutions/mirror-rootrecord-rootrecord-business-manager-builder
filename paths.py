"""Workspace / app data paths. Installed builds use %LOCALAPPDATA%\\RootRecord on Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# scripts/time_tracker/
_PKG_DIR = Path(__file__).resolve().parent
# RR Business Operations (repo root in development)
_WORKSPACE_ROOT = _PKG_DIR.parent.parent


def iter_app_bundle_asset_dirs() -> list[Path]:
    """Dirs that ship with the application (PyInstaller bundle, install folder, or package tree).

    Excludes user data paths such as ``%LOCALAPPDATA%\\RootRecord`` and Documents — use this for
    icons and About/splash images that belong beside the program, not in the workspace database tree.
    """
    dirs: list[Path] = []
    seen: set[str] = set()

    def add(p: Path) -> None:
        try:
            key = str(p.resolve())
        except Exception:
            key = str(p)
        if key not in seen:
            seen.add(key)
            dirs.append(p)

    if hasattr(sys, "_MEIPASS"):
        add(Path(sys._MEIPASS))
    if getattr(sys, "frozen", False):
        add(Path(sys.executable).resolve().parent)
    add(_PKG_DIR)
    # Packaged / dev marketing & About images (see docs/assets/README.md)
    add(_PKG_DIR / "docs" / "assets")
    return dirs


def resolve_shipped_asset(*names: str) -> Path | None:
    """First existing file under :func:`iter_app_bundle_asset_dirs` matching one of the basenames.

    Checks the bundle root and ``branding/`` (PyInstaller ships ``Loading.*`` there).
    """
    for name in names:
        for d in iter_app_bundle_asset_dirs():
            for candidate in (d / name, d / "branding" / name, d / "docs" / "assets" / name):
                if candidate.is_file():
                    return candidate
    return None


def _rootrecord_home_override() -> Path | None:
    env = os.environ.get("ROOTRECORD_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
            raw, _typ = winreg.QueryValueEx(k, "ROOTRECORD_HOME")
        val = str(raw or "").strip()
        if val:
            return Path(os.path.expandvars(val)).expanduser().resolve()
    except Exception:
        return None
    return None


def _frozen_base() -> Path | None:
    if not (getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")):
        return None
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "RootRecord"
    return Path.home() / ".rootrecord"


def resolve_app_root() -> Path:
    """Directory for users/, exports, plugins, and layout roots."""
    ov = _rootrecord_home_override()
    if ov is not None:
        return ov
    fa = _frozen_base()
    if fa is not None:
        return fa
    return _WORKSPACE_ROOT


def workspace_root() -> Path:
    return resolve_app_root()


def data_dir() -> Path:
    ov = _rootrecord_home_override()
    if ov is not None:
        return ov / "data"
    fa = _frozen_base()
    if fa is not None:
        return fa / "data"
    return _PKG_DIR / "data"


def users_dir() -> Path:
    ov = _rootrecord_home_override()
    if ov is not None:
        return ov / "users"
    fa = _frozen_base()
    if fa is not None:
        return fa / "users"
    return _WORKSPACE_ROOT / "users"


RECORD_SUBDIRS = ("sheets", "docs", "receipts", "reports", "media")


def user_root(user_id: int) -> Path:
    return users_dir() / str(int(user_id))


def user_records_dir(user_id: int, sub: str) -> Path:
    return user_root(user_id) / "records" / sub


def legacy_shared_db_path() -> Path | None:
    """Single-user layout before per-account dirs: <install>/data/rootrecord.db (no ROOTRECORD_HOME)."""
    if _rootrecord_home_override() is not None:
        return None
    fa = _frozen_base()
    if fa is None:
        leg = _PKG_DIR / "data" / "rootrecord.db"
        return leg if leg.is_file() else None
    p = fa / "data" / "rootrecord.db"
    return p if p.is_file() else None
