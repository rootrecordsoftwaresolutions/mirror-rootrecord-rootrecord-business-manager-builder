"""Remote license API — reads env vars documented in `.env.example`.

Call `load_rootrecord_env()` from `env_loader` before importing this module
so `.env` is applied (e.g. from `desktop_app.py` entrypoint).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass


def runtime_frozen() -> bool:
    """True when running under PyInstaller (onefile/folder) or similar."""
    return bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))


def _shipped_license_api_base_url() -> str | None:
    if not runtime_frozen():
        return None
    try:
        from license_shipped import SHIPPED_LICENSE_API_BASE_URL

        u = (SHIPPED_LICENSE_API_BASE_URL or "").strip().rstrip("/")
        return u or None
    except ImportError:
        return None


def _normalize_license_api_base_url(base: str) -> str:
    """Strip trailing slashes; if the user pasted `.../license-host/v1`, avoid `/v1/v1/...` paths on the client."""
    b = base.strip().rstrip("/")
    low = b.lower()
    if low.endswith("/v1"):
        return b[: -len("/v1")].rstrip("/")
    return b


@dataclass(frozen=True)
class LicenseApiConfig:
    """POST {base_url}/v1/entitlement with Bearer session or (if allowed) shared api_secret."""

    base_url: str
    api_secret: str


def _license_force_signin_only() -> bool:
    """
    When True, LICENSE_API_SECRET is ignored on the client so end users must sign in (session only).

    Frozen (installed) builds default to True so the shared secret is never used unless
    LICENSE_SHIPPED_ALLOW_API_SECRET=1 is set for internal testing.
    """
    v = os.environ.get("LICENSE_FORCE_SIGNIN_ONLY", "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    if runtime_frozen():
        allow_secret = os.environ.get("LICENSE_SHIPPED_ALLOW_API_SECRET", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        return not allow_secret
    return False


def license_gate_strict() -> bool:
    """
    When True: no granting full access from entitlement cache alone after failed/cancelled sign-in;
    sign-in is required for new sessions (installed builds default strict).
    """
    v = os.environ.get("LICENSE_STRICT_AUTH", "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return runtime_frozen()


def legacy_license_before_signin() -> bool:
    """
    When True: if LICENSE_API_SECRET is set, try /v1/entitlement with it before showing the sign-in window.
    When False (default): show Sign in / Create account first when there is no valid session; Cancel falls back to secret.
    """
    return os.environ.get("LICENSE_LEGACY_LICENSE_FIRST", "").strip().lower() in ("1", "true", "yes", "on")


def get_license_api_config() -> LicenseApiConfig | None:
    """Returns config if `LICENSE_API_BASE_URL` (or shipped frozen default) is set; else None (dev source run)."""
    base = _normalize_license_api_base_url(os.environ.get("LICENSE_API_BASE_URL", ""))
    if not base:
        base = _normalize_license_api_base_url(_shipped_license_api_base_url() or "")
    if not base:
        return None
    secret = os.environ.get("LICENSE_API_SECRET", "").strip()
    if _license_force_signin_only():
        secret = ""
    return LicenseApiConfig(base_url=base, api_secret=secret)


# Public Stripe Payment Link (Checkout). Override with LICENSE_PAYMENT_LINK_URL if you use a different link.
DEFAULT_STRIPE_PAYMENT_LINK = "https://buy.stripe.com/9B64gzaB73pb1wM7oH5gc00"


def get_payment_link_url() -> str:
    """Stripe Payment Link URL for purchasing (optional prefilled_email query param added in UI)."""
    u = os.environ.get("LICENSE_PAYMENT_LINK_URL", "").strip()
    return u or DEFAULT_STRIPE_PAYMENT_LINK
