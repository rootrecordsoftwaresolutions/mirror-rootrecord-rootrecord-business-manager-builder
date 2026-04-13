"""Compare the running build to the latest GitHub Release (installer source of truth)."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import httpx

from app_version import APP_VERSION

_LOG = logging.getLogger("rootrecord.github_update")

# Default: public product + installer releases. Override for forks: GITHUB_UPDATE_REPO=owner/name
_DEFAULT_REPO = "RootRecord/rootrecord-business-manager-download"


def _releases_repo() -> str:
    raw = (os.environ.get("GITHUB_UPDATE_REPO") or "").strip()
    return raw if raw else _DEFAULT_REPO


def _latest_release_api_url() -> str:
    return f"https://api.github.com/repos/{_releases_repo()}/releases/latest"


def normalize_version_string(raw: str) -> str:
    s = (raw or "").strip()
    if s.lower().startswith("v"):
        return s[1:].strip()
    return s


def version_tuple(ver: str) -> tuple[int, ...]:
    s = normalize_version_string(ver)
    if not s:
        return (0,)
    parts: list[int] = []
    for seg in s.split("."):
        m = re.match(r"^(\d+)", seg.strip())
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts)


def compare_semver(a: str, b: str) -> int:
    """Return -1 if a < b, 0 if equal, 1 if a > b (numeric segments, zero-padded)."""
    ta, tb = version_tuple(a), version_tuple(b)
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


@dataclass(frozen=True)
class GithubLatestRelease:
    version: str
    tag_name: str
    html_url: str
    installer_download_url: str | None


def fetch_latest_release(timeout: float = 14.0) -> GithubLatestRelease | None:
    """GET /releases/latest; returns None on network/parse errors (caller should ignore)."""
    url = _latest_release_api_url()
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"RootRecordBusinessManager/{APP_VERSION}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(url, headers=headers)
    except Exception:
        _LOG.debug("GitHub release check failed (network)", exc_info=True)
        return None
    if r.status_code != 200:
        _LOG.debug("GitHub release check: HTTP %s", r.status_code)
        return None
    try:
        data = r.json()
    except Exception:
        _LOG.debug("GitHub release check: invalid JSON", exc_info=True)
        return None
    tag = str(data.get("tag_name") or "").strip()
    if not tag:
        return None
    ver = normalize_version_string(tag)
    html_url = str(data.get("html_url") or "").strip()
    if not html_url:
        html_url = f"https://github.com/{_releases_repo()}/releases/latest"
    installer_url: str | None = None
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "")
        dl = str(asset.get("browser_download_url") or "").strip()
        if not dl or not name.lower().endswith(".exe"):
            continue
        if "rootrecordsetup" in name.lower():
            installer_url = dl
            break
        installer_url = installer_url or dl
    return GithubLatestRelease(version=ver, tag_name=tag, html_url=html_url, installer_download_url=installer_url)


def is_newer_than_installed(info: GithubLatestRelease) -> bool:
    return compare_semver(info.version, APP_VERSION) > 0
