"""Startup: resolve email, call license API, set `license_gate` + SQLite read-only if needed."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from tkinter import messagebox, simpledialog

from data_api import settings_get, settings_set
from db import DbConfig
from license_client import EntitlementSnapshot, LicenseConflictError, cache_still_allows_full_access, fetch_entitlement
from license_config import get_license_api_config
from license_gate import set_read_only
from paths import resolve_app_root

_read_only_notice_shown = False


def _device_id_path() -> Path:
    return resolve_app_root() / ".license_device_id"


def load_or_create_device_id() -> str:
    p = _device_id_path()
    if p.is_file():
        raw = p.read_text(encoding="utf-8").strip()
        if len(raw) >= 8:
            return raw
    did = str(uuid.uuid4())
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(did, encoding="utf-8")
    return did


def _resolve_email(cfg: DbConfig) -> str:
    env_mail = os.environ.get("LICENSE_EMAIL", "").strip()
    if env_mail:
        return env_mail
    stored = settings_get(cfg, "license_account_email", None)
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return ""


def _prompt_email_interactive() -> str:
    root = None
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        s = simpledialog.askstring(
            "RootRecord — subscription",
            "Enter the email for your trial / subscription:",
            parent=root,
        )
        return (s or "").strip()
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def apply_license_at_startup(cfg: DbConfig) -> None:
    """Call after DB migrations and `bootstrap` so settings can be read/written before read-only mode."""
    lic = get_license_api_config()
    if not lic:
        set_read_only(False)
        return

    email = _resolve_email(cfg)
    if not email:
        email = _prompt_email_interactive()
        if email:
            settings_set(cfg, "license_account_email", email)
        else:
            messagebox.showerror(
                "RootRecord",
                "License API is configured but no email was provided.\n\n"
                "Set LICENSE_EMAIL in your .env or enter your email when prompted.",
            )
            set_read_only(True)
            return

    device_id = load_or_create_device_id()

    if cache_still_allows_full_access():
        set_read_only(False)
        return

    try:
        snap = fetch_entitlement(email, device_id, cfg=lic)
    except LicenseConflictError as exc:
        messagebox.showerror("RootRecord — license", str(exc.message))
        set_read_only(True)
        return
    except Exception as exc:
        messagebox.showwarning(
            "RootRecord — license",
            f"Could not reach the license server:\n{exc}\n\n"
            "Working in read-only mode until the connection succeeds.",
        )
        set_read_only(True)
        return

    _apply_snapshot(snap)


def _apply_snapshot(snap: EntitlementSnapshot) -> None:
    global _read_only_notice_shown
    if snap.access == "full":
        set_read_only(False)
        return
    set_read_only(True)
    if not _read_only_notice_shown:
        _read_only_notice_shown = True
        messagebox.showinfo(
            "RootRecord — read-only",
            "Your trial has ended or subscription is inactive.\n\n"
            "You can view data and use export; purchase a subscription to edit again.",
        )
