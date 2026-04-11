"""Cloudflare license API — reads env vars documented in `.env.example`.

Call `load_rootrecord_env()` from `env_loader` before importing this module
so `.env` is applied (e.g. from `desktop_app.py` entrypoint).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LicenseApiConfig:
    """POST {base_url}/v1/entitlement with Authorization: Bearer {api_secret} when secret is set."""

    base_url: str
    api_secret: str


def get_license_api_config() -> LicenseApiConfig | None:
    """Returns config if `LICENSE_API_BASE_URL` is set; otherwise license checks are skipped (dev)."""
    base = os.environ.get("LICENSE_API_BASE_URL", "").strip().rstrip("/")
    if not base:
        return None
    secret = os.environ.get("LICENSE_API_SECRET", "").strip()
    return LicenseApiConfig(base_url=base, api_secret=secret)
