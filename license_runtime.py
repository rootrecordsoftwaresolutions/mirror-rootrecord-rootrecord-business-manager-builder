"""Startup: resolve email, call license API, set `license_gate` + SQLite read-only if needed."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path

from tkinter import messagebox, simpledialog

from data_api import settings_get, settings_set
from db import DbConfig
from license_client import (
    LICENSE_CLOUD_ACCOUNT_ID_KEY,
    LICENSE_SESSION_TOKEN_KEY,
    EntitlementSnapshot,
    LicenseAuthError,
    LicenseConflictError,
    LicenseStartupCancelled,
    auth_exchange,
    cache_still_allows_full_access,
    clear_entitlement_cache_file,
    fetch_entitlement,
)
from license_config import get_license_api_config, legacy_license_before_signin, license_gate_strict, runtime_frozen
from license_gate import set_read_only
from paths import resolve_app_root

log = logging.getLogger("rootrecord.license")
_read_only_notice_shown = False


def _set_read_only_or_offline_grace() -> None:
    """After a network failure or cancelled sign-in: optional cache-based full access (non-strict only)."""
    if license_gate_strict():
        set_read_only(True)
        return
    if cache_still_allows_full_access():
        set_read_only(False)
    else:
        set_read_only(True)


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


def _stored_session_token(cfg: DbConfig) -> str:
    v = settings_get(cfg, LICENSE_SESSION_TOKEN_KEY, None)
    return v.strip() if isinstance(v, str) else ""


def _set_session_token(cfg: DbConfig, token: str | None) -> None:
    if token and token.strip():
        settings_set(cfg, LICENSE_SESSION_TOKEN_KEY, token.strip())
    else:
        settings_set(cfg, LICENSE_SESSION_TOKEN_KEY, "")


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


def apply_license_at_startup(
    cfg: DbConfig,
    *,
    ui_pump: Callable[[], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    ui_master: object | None = None,
) -> None:
    """Call after DB migrations and `bootstrap` so settings can be read/written before read-only mode."""
    lic = get_license_api_config()
    if not lic:
        if runtime_frozen():
            messagebox.showerror(
                "RootRecord",
                "This installed copy is missing the online service needed to verify your subscription.\n\n"
                "Contact the person who gave you RootRecord for an updated build.",
            )
            raise SystemExit(1)
        set_read_only(False)
        return

    device_id = load_or_create_device_id()

    session = _stored_session_token(cfg)
    email = _resolve_email(cfg)

    if session and not email:
        try:
            from license_client import fetch_cloud_account_me

            info = fetch_cloud_account_me(
                session, cfg=lic, ui_pump=ui_pump, cancel_check=cancel_check
            )
            em = (info.email or "").strip().lower()
            if em:
                email = em
                settings_set(cfg, "license_account_email", email)
        except LicenseStartupCancelled:
            raise
        except LicenseAuthError:
            _set_session_token(cfg, None)
            session = ""
        except Exception:
            log.debug("Could not load account email from session (/v1/me)", exc_info=True)

    can_use_shared_secret = bool(lic.api_secret.strip())

    def _fetch_with_bearer(bearer: str | None, em: str) -> EntitlementSnapshot:
        if not em:
            raise RuntimeError("Billing email is required for license check.")
        return fetch_entitlement(
            em,
            device_id,
            cfg=lic,
            bearer_token=bearer,
            ui_pump=ui_pump,
            cancel_check=cancel_check,
        )

    def _try_session_entitlement() -> bool:
        if not session or not email:
            return False
        try:
            snap = _fetch_with_bearer(session, email)
            _apply_snapshot(snap, cfg)
            return True
        except LicenseStartupCancelled:
            raise
        except LicenseAuthError:
            _set_session_token(cfg, None)
            return False
        except LicenseConflictError as exc:
            messagebox.showerror("RootRecord — license", str(exc.message))
            set_read_only(True)
            return True
        except Exception as exc:
            log.warning("License session entitlement failed: %s", exc, exc_info=True)
            messagebox.showwarning(
                "RootRecord — license",
                "Could not reach RootRecord online services to verify your subscription.\n\n"
                "You can keep working in read-only mode until the connection succeeds. "
                "Check your internet connection or try again later.",
            )
            _set_read_only_or_offline_grace()
            return True

    def _try_legacy_secret_entitlement(em: str) -> bool:
        if not can_use_shared_secret:
            return False
        use_email = em
        if not use_email:
            use_email = _prompt_email_interactive()
            if use_email:
                settings_set(cfg, "license_account_email", use_email)
        if not use_email:
            messagebox.showerror(
                "RootRecord",
                "An email address is required to verify your trial or subscription.\n\n"
                "Enter your email when asked, or add it under Account Settings.",
            )
            _set_read_only_or_offline_grace()
            return True
        try:
            snap = _fetch_with_bearer(None, use_email)
            _apply_snapshot(snap, cfg)
            return True
        except LicenseStartupCancelled:
            raise
        except LicenseConflictError as exc:
            messagebox.showerror("RootRecord — license", str(exc.message))
            set_read_only(True)
            return True
        except LicenseAuthError as exc:
            messagebox.showerror("RootRecord — license", exc.message)
            set_read_only(True)
            return True
        except Exception as exc:
            log.warning("License server unreachable: %s", exc, exc_info=True)
            messagebox.showwarning(
                "RootRecord — license",
                "Could not reach RootRecord online services to verify your subscription.\n\n"
                "You can keep working in read-only mode until the connection succeeds. "
                "Check your internet connection or try again later.",
            )
            _set_read_only_or_offline_grace()
            return True

    def _run_signin_dialog() -> bool:
        from auth_ui import prompt_license_signin_or_signup

        def _msg_parent():
            m = ui_master
            if m is None:
                return None
            try:
                import tkinter as tk

                return m if isinstance(m, tk.Misc) else None
            except Exception:
                return None

        _parent = _msg_parent()

        cred = prompt_license_signin_or_signup(master=ui_master)
        if not cred:
            return False
        action, em, pw = cred
        path = {"login": "login", "signup": "signup", "claim": "claim-password"}[action]
        try:
            token, snap = auth_exchange(
                path, em, pw, device_id, cfg=lic, ui_pump=ui_pump, cancel_check=cancel_check
            )
        except LicenseStartupCancelled:
            raise
        except LicenseConflictError as exc:
            messagebox.showerror("RootRecord — license", str(exc.message), parent=_parent)
            return False
        except LicenseAuthError as exc:
            messagebox.showerror("RootRecord — sign in", exc.message, parent=_parent)
            return False
        except Exception as exc:
            log.warning("Auth request failed: %s", exc, exc_info=True)
            messagebox.showwarning(
                "RootRecord — license",
                "Could not reach RootRecord online services to verify your subscription.\n\n"
                "You can keep working in read-only mode until the connection succeeds. "
                "Check your internet connection or try again later.",
                parent=_parent,
            )
            _set_read_only_or_offline_grace()
            return True
        settings_set(cfg, "license_account_email", em)
        _set_session_token(cfg, token)
        _apply_snapshot(snap, cfg)
        try:
            from sync_engine import schedule_background_sync

            schedule_background_sync(cfg)
        except Exception:
            pass
        return True

    if _try_session_entitlement():
        return

    email = _resolve_email(cfg)

    if legacy_license_before_signin() and _try_legacy_secret_entitlement(email):
        return

    if not legacy_license_before_signin():
        while True:
            if _run_signin_dialog():
                return
            email = _resolve_email(cfg)
            if _try_legacy_secret_entitlement(email):
                return
            if license_gate_strict() and not can_use_shared_secret:
                if not messagebox.askyesno(
                    "RootRecord — sign in required",
                    "You must sign in or create an account to use RootRecord.\n\n"
                    "Choose Yes to try again, or No to exit the application.",
                    icon=messagebox.WARNING,
                    default=messagebox.YES,
                ):
                    raise SystemExit(0)
                continue
            hint = (
                "Sign-in was cancelled and your subscription could not be verified.\n\n"
                "Try again, or add your billing email under Account Settings if you use an organization setup."
            )
            messagebox.showerror("RootRecord — sign in required", hint)
            _set_read_only_or_offline_grace()
            return

    while True:
        if _run_signin_dialog():
            return
        email = _resolve_email(cfg)
        if _try_legacy_secret_entitlement(email):
            return
        if license_gate_strict() and not can_use_shared_secret:
            if not messagebox.askyesno(
                "RootRecord — sign in required",
                "You must sign in or create an account to use RootRecord.\n\n"
                "Choose Yes to try again, or No to exit the application.",
                icon=messagebox.WARNING,
                default=messagebox.YES,
            ):
                raise SystemExit(0)
            continue
        hint = "Sign in with your RootRecord account, or create one.\n\nIf you need help, contact support."
        messagebox.showerror("RootRecord — sign in required", hint)
        _set_read_only_or_offline_grace()
        return


def logout_cloud_session(cfg: DbConfig) -> None:
    """Revoke the cloud session (best effort), clear the stored token, and re-apply read-only / entitlement."""
    lic = get_license_api_config()
    session = _stored_session_token(cfg)
    if lic and session:
        try:
            from license_client import post_auth_logout

            post_auth_logout(session, cfg=lic)
        except Exception:
            log.debug("Remote session logout failed", exc_info=True)
    _set_session_token(cfg, None)
    try:
        clear_entitlement_cache_file()
    except Exception:
        pass
    settings_set(cfg, LICENSE_CLOUD_ACCOUNT_ID_KEY, "")
    if not lic:
        return
    email = _resolve_email(cfg)
    if email:
        try:
            refresh_entitlement_and_apply(cfg)
            return
        except RuntimeError as exc:
            log.warning("Entitlement refresh after logout: %s", exc)
        except LicenseAuthError:
            log.debug("Entitlement after logout: auth", exc_info=True)
        except Exception:
            log.warning("Entitlement refresh after logout failed", exc_info=True)
    if license_gate_strict():
        set_read_only(True)
    elif cache_still_allows_full_access():
        set_read_only(False)
    else:
        set_read_only(True)


def refresh_entitlement_and_apply(cfg: DbConfig) -> EntitlementSnapshot:
    """Re-fetch subscription from RootRecord online services, update cache, and apply read-only mode."""
    lic = get_license_api_config()
    if not lic:
        set_read_only(False)
        raise RuntimeError("Online subscription service is not configured on this copy.")
    email = _resolve_email(cfg)
    if not email:
        raise RuntimeError("Add your billing email under Account Settings, or sign in when prompted.")
    device_id = load_or_create_device_id()
    session = _stored_session_token(cfg)
    bearer = session if session else None
    try:
        snap = fetch_entitlement(email, device_id, cfg=lic, bearer_token=bearer)
    except LicenseAuthError:
        _set_session_token(cfg, None)
        raise RuntimeError("Your sign-in session expired. Restart the app and sign in again.")
    _apply_snapshot(snap, cfg)
    return snap


def _persist_subscription_account_id(cfg: DbConfig, snap: EntitlementSnapshot) -> None:
    aid = snap.raw.get("account_id")
    if isinstance(aid, str):
        s = aid.strip()
        if len(s) >= 32:
            settings_set(cfg, LICENSE_CLOUD_ACCOUNT_ID_KEY, s)


def _apply_snapshot(snap: EntitlementSnapshot, cfg: DbConfig | None = None) -> None:
    global _read_only_notice_shown
    if cfg is not None:
        _persist_subscription_account_id(cfg, snap)
    if snap.access == "full":
        set_read_only(False)
        return
    set_read_only(True)
    if not _read_only_notice_shown:
        _read_only_notice_shown = True
        reason = str(snap.reason or "")
        if reason == "past_due":
            body = (
                "Your subscription payment is past due.\n\n"
                "The app is read-only until billing is updated. You can still view data and export. "
                "Use Activate Services at the bottom of the window."
            )
        else:
            body = (
                "Your 14-day free trial has ended and there is no active subscription.\n\n"
                "The app is read-only: you can view data and export. Subscribe to restore full access "
                "to clock in, edit entries, and other changes. Use the billing button at the bottom "
                "of the window."
            )
        messagebox.showinfo("RootRecord — read-only", body)
