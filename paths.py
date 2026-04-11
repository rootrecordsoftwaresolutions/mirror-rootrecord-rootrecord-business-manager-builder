"""Workspace / app data paths. Installed builds use %LOCALAPPDATA%\\RootRecord on Windows."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# scripts/time_tracker/
_PKG_DIR = Path(__file__).resolve().parent
# RR Business Operations (repo root in development)
_WORKSPACE_ROOT = _PKG_DIR.parent.parent

# Set by the desktop UI from the signed-in Firebase account (localId). Leave unset for default layout.
_desktop_firebase_uid: str | None = None


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


def sanitize_firebase_uid_for_path(uid: str) -> str:
    s = "".join(c for c in uid if c.isalnum() or c in "_-")
    return s or "firebase_uid"


def set_desktop_firebase_account_uid(uid: str | None) -> None:
    """Scope data/, users/, and workspace_root() to this Firebase localId (desktop). Pass None when signed out."""
    global _desktop_firebase_uid
    if uid is None or not str(uid).strip():
        _desktop_firebase_uid = None
    else:
        _desktop_firebase_uid = sanitize_firebase_uid_for_path(str(uid))


def _frozen_base() -> Path | None:
    if not (getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS")):
        return None
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / "RootRecord"
    return Path.home() / ".rootrecord"


def resolve_app_root() -> Path:
    """Directory for users/, exports, plugins, and per-account layout roots."""
    ov = _rootrecord_home_override()
    if ov is not None:
        return ov
    fa = _frozen_base()
    if fa is not None:
        if _desktop_firebase_uid:
            return fa / "accounts" / _desktop_firebase_uid
        return fa
    if _desktop_firebase_uid:
        return _PKG_DIR / "accounts" / _desktop_firebase_uid
    return _WORKSPACE_ROOT


def workspace_root() -> Path:
    return resolve_app_root()


def data_dir() -> Path:
    ov = _rootrecord_home_override()
    if ov is not None:
        return ov / "data"
    fa = _frozen_base()
    if fa is not None:
        if _desktop_firebase_uid:
            return fa / "accounts" / _desktop_firebase_uid / "data"
        return fa / "data"
    if _desktop_firebase_uid:
        return _PKG_DIR / "accounts" / _desktop_firebase_uid / "data"
    return _PKG_DIR / "data"


def users_dir() -> Path:
    ov = _rootrecord_home_override()
    if ov is not None:
        return ov / "users"
    fa = _frozen_base()
    if fa is not None:
        if _desktop_firebase_uid:
            return fa / "accounts" / _desktop_firebase_uid / "users"
        return fa / "users"
    if _desktop_firebase_uid:
        return _PKG_DIR / "accounts" / _desktop_firebase_uid / "users"
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


def other_desktop_account_dirs_exist(current_firebase_uid: str) -> bool:
    """True if another Firebase account already has a folder under accounts/ (excluding current)."""
    cur = sanitize_firebase_uid_for_path(current_firebase_uid)
    fa = _frozen_base()
    if fa is not None:
        acc = fa / "accounts"
        if not acc.is_dir():
            return False
        return any(p.is_dir() and p.name != cur for p in acc.iterdir())
    acc = _PKG_DIR / "accounts"
    if not acc.is_dir():
        return False
    return any(p.is_dir() and p.name != cur for p in acc.iterdir())
