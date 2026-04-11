"""HTTP client for Cloudflare `/v1/entitlement`."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from license_config import LicenseApiConfig, get_license_api_config
from paths import resolve_app_root


@dataclass(frozen=True)
class EntitlementSnapshot:
    access: str
    reason: str
    trial_ends_at: str | None
    valid_until: str | None
    raw: dict[str, Any]


class LicenseConflictError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


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


def write_cache_from_response(body: dict[str, Any]) -> None:
    try:
        p = _cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(body, indent=0), encoding="utf-8")
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


def fetch_entitlement(email: str, device_id: str, *, cfg: LicenseApiConfig | None = None) -> EntitlementSnapshot:
    c = cfg or get_license_api_config()
    if not c:
        raise RuntimeError("fetch_entitlement called without LICENSE_API_BASE_URL")
    url = f"{c.base_url}/v1/entitlement"
    headers = {"Content-Type": "application/json"}
    if c.api_secret:
        headers["Authorization"] = f"Bearer {c.api_secret}"
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json={"email": email.strip(), "device_id": device_id}, headers=headers)
    if r.status_code == 409:
        try:
            err = r.json().get("error", {})
            msg = str(err.get("message") or "Device registered to another email.")
        except Exception:
            msg = "Device registered to another email."
        raise LicenseConflictError(msg)
    r.raise_for_status()
    body = r.json()
    write_cache_from_response(body)
    return EntitlementSnapshot(
        access=str(body.get("access") or "read_only"),
        reason=str(body.get("reason") or ""),
        trial_ends_at=body.get("trial_ends_at"),
        valid_until=body.get("valid_until"),
        raw=body,
    )
