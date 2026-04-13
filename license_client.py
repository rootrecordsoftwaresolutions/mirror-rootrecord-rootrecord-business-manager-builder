"""HTTP client for license server `/v1/entitlement` and `/v1/auth/*`."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, NamedTuple

import httpx

from license_config import LicenseApiConfig, get_license_api_config
from paths import resolve_app_root

# Stored in SQLite settings when the user signs in (see `license_runtime`).
LICENSE_SESSION_TOKEN_KEY = "license_session_token"
# Server-side account UUID — synced from entitlement / auth responses.
LICENSE_CLOUD_ACCOUNT_ID_KEY = "license_cloud_account_id"


@dataclass(frozen=True)
class CloudAccountInfo:
    """Row summary from GET /v1/me (session auth)."""

    account_id: str
    email: str
    subscription_status: str
    trial_started_at: str | None
    trial_ends_at: str | None
    has_password: bool


@dataclass(frozen=True)
class EntitlementSnapshot:
    access: str
    reason: str
    trial_started_at: str | None
    trial_ends_at: str | None
    valid_until: str | None
    raw: dict[str, Any]


class LicenseConflictError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LicenseAuthError(Exception):
    """HTTP error from `/v1/auth/*` (4xx/5xx)."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class LicenseStartupCancelled(Exception):
    """Raised when the user cancels from the startup splash while waiting on the license server."""


def _run_http_pumped(
    name: str,
    task: Callable[[], httpx.Response],
    *,
    ui_pump: Callable[[], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> httpx.Response:
    """Run blocking httpx on a worker thread so the main thread can pump Tk and honor cancel."""
    if ui_pump is None and cancel_check is None:
        return task()
    out: list[Any] = []

    def worker() -> None:
        try:
            out.append(task())
        except BaseException as exc:  # noqa: BLE001
            out.append(exc)

    t = threading.Thread(target=worker, daemon=True, name=f"rr-license-{name}")
    t.start()
    while t.is_alive():
        if cancel_check is not None and cancel_check():
            raise LicenseStartupCancelled()
        if ui_pump is not None:
            try:
                ui_pump()
            except Exception:
                pass
        time.sleep(0.02)
    t.join(timeout=3.0)
    if not out:
        raise RuntimeError("No response from RootRecord online services.")
    val = out[0]
    if isinstance(val, BaseException):
        raise val
    return val  # type: ignore[return-value]


def _cache_path() -> Path:
    return resolve_app_root() / "license_entitlement_cache.json"


def read_cache_file() -> dict[str, Any] | None:
    p = _cache_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def clear_entitlement_cache_file() -> None:
    """Remove cached entitlement JSON so a future run does not treat stale access as current."""
    try:
        p = _cache_path()
        if p.is_file():
            p.unlink()
    except Exception:
        pass


_ENTITLEMENT_CACHE_KEYS = (
    "account_id",
    "access",
    "reason",
    "trial_started_at",
    "trial_ends_at",
    "valid_until",
    "subscription_status",
)


def write_cache_from_response(body: dict[str, Any]) -> None:
    try:
        cache_payload = {k: body[k] for k in _ENTITLEMENT_CACHE_KEYS if k in body}
        if not cache_payload:
            return
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cache_payload, indent=0), encoding="utf-8")
    except Exception:
        pass


def _parse_iso_utc(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        t = s.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def cache_still_allows_full_access() -> bool:
    """Offline grace: if cache says full and valid_until is in the future, skip network."""
    raw = read_cache_file()
    if not raw:
        return False
    if raw.get("access") != "full":
        return False
    vu = _parse_iso_utc(raw.get("valid_until"))
    if vu is None:
        return False
    return datetime.now(timezone.utc) < vu


def _license_api_error_message(r: httpx.Response) -> str:
    """Parse license-server JSON errors; unwrap `{ \"error\": { \"error\": { code, message }}}` from auth handlers."""
    try:
        data = r.json()
    except Exception:
        return (r.text or "")[:400].strip() or f"HTTP {r.status_code}"
    if not isinstance(data, dict):
        return (r.text or "")[:400].strip() or f"HTTP {r.status_code}"
    err = data.get("error")
    if isinstance(err, str) and err.strip():
        return err.strip()
    if isinstance(err, dict):
        inner = err.get("error")
        if isinstance(inner, dict):
            msg = inner.get("message") or inner.get("code")
            if msg:
                return str(msg)
        msg = err.get("message") or err.get("code")
        if msg:
            return str(msg)
    return (r.text or "")[:400].strip() or f"HTTP {r.status_code}"


def _bearer_for_request(c: LicenseApiConfig, bearer_token: str | None) -> str:
    t = (bearer_token or "").strip()
    if t:
        return t
    if c.api_secret.strip():
        return c.api_secret.strip()
    raise RuntimeError("Sign-in is required for this request.")


def fetch_entitlement(
    email: str,
    device_id: str,
    *,
    cfg: LicenseApiConfig | None = None,
    bearer_token: str | None = None,
    ui_pump: Callable[[], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> EntitlementSnapshot:
    c = cfg or get_license_api_config()
    if not c:
        raise RuntimeError("Online subscription service is not configured on this copy.")
    url = f"{c.base_url}/v1/entitlement"
    headers = {"Content-Type": "application/json"}
    headers["Authorization"] = f"Bearer {_bearer_for_request(c, bearer_token)}"
    timeout = httpx.Timeout(22.0, connect=12.0)

    def task() -> httpx.Response:
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, json={"email": email.strip(), "device_id": device_id}, headers=headers)

    r = _run_http_pumped("entitlement", task, ui_pump=ui_pump, cancel_check=cancel_check)
    if r.status_code == 409:
        msg = _license_api_error_message(r) or "Device registered to another email."
        raise LicenseConflictError(msg)
    if r.status_code == 401:
        msg = _license_api_error_message(r) or "Unauthorized."
        raise LicenseAuthError(401, msg)
    r.raise_for_status()
    body = r.json()
    write_cache_from_response(body)
    return EntitlementSnapshot(
        access=str(body.get("access") or "read_only"),
        reason=str(body.get("reason") or ""),
        trial_started_at=body.get("trial_started_at"),
        trial_ends_at=body.get("trial_ends_at"),
        valid_until=body.get("valid_until"),
        raw=body,
    )


def auth_exchange(
    action: Literal["signup", "login", "claim-password"],
    email: str,
    password: str,
    device_id: str,
    *,
    cfg: LicenseApiConfig | None = None,
    ui_pump: Callable[[], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> tuple[str, EntitlementSnapshot]:
    """POST /v1/auth/* — returns (access_token, entitlement snapshot). No Bearer required."""
    c = cfg or get_license_api_config()
    if not c:
        raise RuntimeError("Online subscription service is not configured on this copy.")
    path = {"signup": "/v1/auth/signup", "login": "/v1/auth/login", "claim-password": "/v1/auth/claim-password"}[action]
    url = f"{c.base_url}{path}"
    headers = {"Content-Type": "application/json"}
    payload = {"email": email.strip(), "password": password, "device_id": device_id}
    timeout = httpx.Timeout(35.0, connect=12.0)

    def task() -> httpx.Response:
        with httpx.Client(timeout=timeout) as client:
            return client.post(url, json=payload, headers=headers)

    r = _run_http_pumped(f"auth-{action}", task, ui_pump=ui_pump, cancel_check=cancel_check)
    if r.status_code == 409:
        msg = _license_api_error_message(r) or "Conflict."
        raise LicenseConflictError(msg)
    if r.status_code >= 400:
        msg = _license_api_error_message(r) or f"HTTP {r.status_code}"
        raise LicenseAuthError(r.status_code, msg)
    if r.status_code not in (200, 201):
        msg = _license_api_error_message(r) or f"HTTP {r.status_code}"
        raise LicenseAuthError(r.status_code, msg)
    body = r.json()
    token = body.get("access_token")
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("Sign-in did not complete. Please try again.")
    write_cache_from_response(body)
    snap = EntitlementSnapshot(
        access=str(body.get("access") or "read_only"),
        reason=str(body.get("reason") or ""),
        trial_started_at=body.get("trial_started_at"),
        trial_ends_at=body.get("trial_ends_at"),
        valid_until=body.get("valid_until"),
        raw=body,
    )
    return token.strip(), snap


def post_auth_logout(session_token: str, *, cfg: LicenseApiConfig | None = None) -> None:
    """POST /v1/auth/logout — invalidates the session on the server (Bearer = session token)."""
    c = cfg or get_license_api_config()
    if not c:
        raise RuntimeError("Online subscription service is not configured on this copy.")
    tok = session_token.strip()
    if not tok:
        return
    url = f"{c.base_url}/v1/auth/logout"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}
    timeout = httpx.Timeout(18.0, connect=10.0)
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, headers=headers)
    if r.status_code == 401:
        return
    r.raise_for_status()


def format_trial_remaining_human(trial_ends_at_iso: str | None) -> str | None:
    """Return a short phrase like '5 days, 3 hours' or None if unknown/expired."""
    end = _parse_iso_utc(trial_ends_at_iso)
    if end is None:
        return None
    now = datetime.now(timezone.utc)
    if now >= end:
        return None
    secs = int((end - now).total_seconds())
    if secs < 60:
        return "less than 1 minute"
    days, r = divmod(secs, 86400)
    hours, r2 = divmod(r, 3600)
    minutes = r2 // 60
    if days >= 1:
        return f"{days} day{'s' if days != 1 else ''}, {hours} hour{'s' if hours != 1 else ''}"
    if hours >= 1:
        return f"{hours} hour{'s' if hours != 1 else ''}, {minutes} minute{'s' if minutes != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


class LicenseFooterHints(NamedTuple):
    line: str | None
    warn: bool
    show_subscribe: bool
    tick_trial: bool


def license_footer_hints(*, api_configured: bool) -> LicenseFooterHints:
    """Build footer copy from cached entitlement (after last successful /v1/entitlement)."""
    if not api_configured:
        return LicenseFooterHints(None, False, False, False)
    raw = read_cache_file()
    if not raw:
        return LicenseFooterHints(None, False, False, False)
    access = str(raw.get("access") or "")
    reason = str(raw.get("reason") or "")
    trial_end = raw.get("trial_ends_at")

    if access == "read_only":
        if reason == "past_due":
            line = "Read-only — payment past due. Update your subscription to restore editing. View and export only."
        else:
            line = (
                "Read-only — your 14-day trial ended without an active subscription. "
                "Subscribe to unlock editing; view and export still work."
            )
        return LicenseFooterHints(line, True, True, False)

    if access == "full" and reason == "trialing":
        rem = format_trial_remaining_human(trial_end if isinstance(trial_end, str) else None)
        if rem:
            line = f"Trial — {rem} left. Use Activate Services to purchase before the trial ends and avoid interruption."
        else:
            line = "You are on a free trial. Use Activate Services to purchase before the trial ends."
        return LicenseFooterHints(line, False, True, True)

    if access == "full" and reason == "paid":
        return LicenseFooterHints(None, False, False, False)

    return LicenseFooterHints(None, False, False, False)


def fetch_cloud_account_me(
    session_token: str,
    *,
    cfg: LicenseApiConfig | None = None,
    ui_pump: Callable[[], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> CloudAccountInfo:
    """Load signed-in account details from RootRecord online services (session required)."""
    c = cfg or get_license_api_config()
    if not c:
        raise RuntimeError("Online subscription service is not configured on this copy.")
    tok = session_token.strip()
    if not tok:
        raise RuntimeError("Sign in again to load account details.")
    url = f"{c.base_url}/v1/me"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {tok}"}
    timeout = httpx.Timeout(22.0, connect=12.0)

    def task() -> httpx.Response:
        with httpx.Client(timeout=timeout) as client:
            return client.get(url, headers=headers)

    r = _run_http_pumped("me", task, ui_pump=ui_pump, cancel_check=cancel_check)
    if r.status_code == 401:
        raise LicenseAuthError(401, "Session invalid or expired.")
    r.raise_for_status()
    body = r.json()
    return CloudAccountInfo(
        account_id=str(body.get("account_id") or ""),
        email=str(body.get("email") or ""),
        subscription_status=str(body.get("subscription_status") or ""),
        trial_started_at=body.get("trial_started_at") if isinstance(body.get("trial_started_at"), str) else None,
        trial_ends_at=body.get("trial_ends_at") if isinstance(body.get("trial_ends_at"), str) else None,
        has_password=bool(body.get("has_password")),
    )


def create_checkout_session(
    email: str,
    *,
    cfg: LicenseApiConfig | None = None,
    bearer_token: str | None = None,
) -> str:
    """POST /v1/billing/checkout — returns Stripe Checkout URL."""
    c = cfg or get_license_api_config()
    if not c:
        raise RuntimeError("Online subscription service is not configured on this copy.")
    url = f"{c.base_url}/v1/billing/checkout"
    headers = {"Content-Type": "application/json"}
    headers["Authorization"] = f"Bearer {_bearer_for_request(c, bearer_token)}"
    with httpx.Client(timeout=45.0) as client:
        r = client.post(url, json={"email": email.strip()}, headers=headers)
    r.raise_for_status()
    data = r.json()
    checkout = data.get("url")
    if not isinstance(checkout, str) or not checkout.startswith("http"):
        raise RuntimeError("Could not open checkout. Try again or use the payment link in Billing.")
    return checkout
