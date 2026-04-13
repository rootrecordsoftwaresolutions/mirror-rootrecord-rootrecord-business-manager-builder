"""
Optional upload of local database backups to RootRecord online backup storage.

Resolution order for the service base URL is internal (environment, shipped build default,
optional stored override, then legacy same-host fallback). Credentials are never the
account password or sign-in session.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import httpx

from data_api import settings_get, settings_set
from db import DbConfig

log = logging.getLogger("rootrecord.backup_r2")

_BACKUP_HEALTH = "/v1/backup/health"
_BACKUP_UPLOAD = "/v1/backup/upload"


def _license_worker_base_url() -> str | None:
    try:
        from license_config import get_license_api_config

        lic = get_license_api_config()
        if lic and lic.base_url:
            return lic.base_url.strip().rstrip("/")
    except Exception:
        return None
    return None


def _shipped_backup_api_base_url() -> str | None:
    try:
        from backup_shipped import SHIPPED_BACKUP_API_BASE_URL

        u = (SHIPPED_BACKUP_API_BASE_URL or "").strip().rstrip("/")
        return u or None
    except ImportError:
        return None


def backup_api_base_url(cfg: DbConfig) -> str | None:
    """Resolve backup service base URL (internal precedence: env, shipped default, DB, legacy host)."""
    env_first = (os.environ.get("ROOTRECORD_BACKUP_API_BASE_URL") or "").strip().rstrip("/")
    if env_first:
        return env_first
    ship = _shipped_backup_api_base_url()
    if ship:
        return ship
    db_url = str(settings_get(cfg, "cloud_backup_api_base_url", "") or "").strip().rstrip("/")
    if db_url:
        return db_url
    return _license_worker_base_url()


def bootstrap_cloud_backup_if_enabled(cfg: DbConfig) -> None:
    """When cloud backup is on and a URL resolves, ensure a vault token exists (no UI required)."""
    if not cloud_backup_enabled(cfg):
        return
    if not backup_api_base_url(cfg):
        return
    ensure_vault_token(cfg)


def cloud_backup_enabled(cfg: DbConfig) -> bool:
    return bool(settings_get(cfg, "cloud_backup_enabled", False))


def vault_token(cfg: DbConfig) -> str:
    return str(settings_get(cfg, "cloud_backup_vault_token", "") or "").strip()


def ensure_vault_token(cfg: DbConfig) -> str:
    tok = vault_token(cfg)
    if len(tok) >= 24:
        return tok
    import secrets

    tok = secrets.token_urlsafe(32)
    settings_set(cfg, "cloud_backup_vault_token", tok)
    return tok


def ping_backup_service(base: str) -> tuple[bool, str]:
    url = f"{base}{_BACKUP_HEALTH}"
    try:
        with httpx.Client(timeout=12.0) as client:
            r = client.get(url)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}"
        body = r.json()
        if not isinstance(body, dict) or body.get("ok") is not True:
            return False, "unexpected JSON"
        # Worker returns r2 when bound; refuse "unconfigured" so we do not claim uploads will work.
        r2 = str(body.get("r2", "ready")).strip().lower()
        if r2 == "unconfigured":
            return False, "R2 bucket is not bound on the backup worker (deploy wrangler with r2_buckets)."
        return True, "ok"
    except Exception as exc:
        return False, str(exc)[:200]


def _parse_upload_error_body(text: str) -> str:
    try:
        j = json.loads(text)
        if isinstance(j, dict):
            err = j.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])[:400]
            if isinstance(err, str):
                return err[:400]
    except Exception:
        pass
    return ""


def cloud_backup_status_summary(cfg: DbConfig) -> str:
    """Multi-line text for Account Settings (no secrets)."""
    lines: list[str] = []
    if not cloud_backup_enabled(cfg):
        lines.append("Online backup copy: off.")
        return "\n".join(lines)
    base = backup_api_base_url(cfg)
    if not base:
        lines.append("Online backup: no service URL (set ROOTRECORD_BACKUP_API_BASE_URL or ship backup_shipped default).")
        return "\n".join(lines)
    lines.append(f"Service: {base[:72]}{'…' if len(base) > 72 else ''}")
    if len(vault_token(cfg)) < 24:
        lines.append("Vault token missing: turn the switch on and Save Account Settings.")
    else:
        lines.append('Use "Test connection" below to verify the worker can reach R2.')
    last = str(settings_get(cfg, "cloud_backup_last_upload_utc", "") or "").strip()
    lines.append(f"Last successful upload: {last or 'never'}")
    key = str(settings_get(cfg, "cloud_backup_last_object_key", "") or "").strip()
    if key:
        lines.append(f"Last object key: {key[:100]}{'…' if len(key) > 100 else ''}")
    err = str(settings_get(cfg, "cloud_backup_last_error", "") or "").strip()
    if err:
        lines.append(f"Last error: {err[:220]}{'…' if len(err) > 220 else ''}")
    p = newest_local_backup_file(cfg)
    if p is None:
        lines.append("No local *.sqlite3 backup found yet. Run a backup (Program Settings: Backup Now or enable auto-backup).")
    else:
        lines.append(f"Latest local backup file: {p.name}")
    return "\n".join(lines)


def upload_sqlite_file(cfg: DbConfig, file_path: Path, *, filename: str | None = None) -> tuple[bool, str]:
    """Upload backup file bytes to online backup storage. Returns (ok, user-facing message)."""
    if not cloud_backup_enabled(cfg):
        return False, "Turn on online backup under Account Settings."
    base = backup_api_base_url(cfg)
    if not base:
        log.warning("online backup: no service URL resolved")
        return False, "Online backup is not available on this copy. Contact support if this continues."
    tok = vault_token(cfg)
    if len(tok) < 24:
        return False, "Open Account Settings, turn on online backup, and click Save Account Settings."

    ok_ping, ping_msg = ping_backup_service(base)
    if not ok_ping:
        log.warning("online backup health check failed: %s", ping_msg)
        return False, "Could not reach RootRecord online backup. Check your internet connection."

    path = Path(file_path)
    if not path.is_file():
        return False, "The backup file was not found."

    name = filename or path.name
    url = f"{base}{_BACKUP_UPLOAD}"
    try:
        data = path.read_bytes()
        headers = {
            "Authorization": f"Bearer {tok}",
            "Content-Type": "application/vnd.sqlite3",
            "X-RootRecord-Filename": name[:200],
        }
        with httpx.Client(timeout=120.0) as client:
            r = client.put(url, content=data, headers=headers)
        if r.status_code not in (200, 201):
            detail = ""
            try:
                detail = r.text[:800]
            except Exception:
                pass
            parsed = _parse_upload_error_body(detail) if detail else ""
            log.warning("online backup upload HTTP %s %s", r.status_code, (parsed or detail)[:300])
            if parsed:
                settings_set(cfg, "cloud_backup_last_error", f"HTTP {r.status_code}: {parsed}")
                return False, f"Online backup failed: {parsed}"
            settings_set(cfg, "cloud_backup_last_error", f"HTTP {r.status_code}")
            return False, "The online copy could not be saved. Try again later."
        settings_set(cfg, "cloud_backup_last_upload_utc", _now_iso())
        settings_set(cfg, "cloud_backup_last_source_mtime_ns", str(path.stat().st_mtime_ns))
        settings_set(cfg, "cloud_backup_last_error", "")
        try:
            body = r.json()
            if isinstance(body, dict) and isinstance(body.get("key"), str):
                settings_set(cfg, "cloud_backup_last_object_key", body["key"][:500])
        except Exception:
            pass
        return True, "Saved."
    except Exception as exc:
        err = str(exc)[:400]
        settings_set(cfg, "cloud_backup_last_error", err)
        log.info("online backup upload failed: %s", err)
        return False, "The online copy could not be saved. Try again later."


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def newest_local_backup_file(cfg: DbConfig) -> Path | None:
    """Most recently modified *.sqlite3 under the same backups folder as the app."""
    try:
        db_path = Path(str(cfg.db_path)).resolve()
        out_dir = db_path.parent / "backups"
        if not out_dir.is_dir():
            return None
        files = [p for p in out_dir.glob("*.sqlite3") if p.is_file()]
        if not files:
            return None
        files.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        return files[0]
    except Exception:
        return None


def maybe_upload_newest_backup(cfg: DbConfig) -> tuple[bool, str]:
    """If cloud backup on and we have a URL + token, upload newest file in local backups dir."""
    p = newest_local_backup_file(cfg)
    if p is None:
        return False, "No local backup file yet."
    return upload_sqlite_file(cfg, p, filename=p.name)


def maybe_upload_newest_if_stale(cfg: DbConfig) -> tuple[bool, str]:
    """Upload newest local backup only if it is newer than the last successful cloud copy."""
    if not cloud_backup_enabled(cfg):
        return False, "Online backup is off."
    if not backup_api_base_url(cfg):
        return False, "Online backup is not available on this copy."
    if len(vault_token(cfg)) < 24:
        return False, "Save Account Settings with online backup turned on."
    p = newest_local_backup_file(cfg)
    if p is None:
        return False, "No local backup yet."
    try:
        last_ns = int(str(settings_get(cfg, "cloud_backup_last_source_mtime_ns", "0") or "0") or 0)
    except ValueError:
        last_ns = 0
    if p.stat().st_mtime_ns <= last_ns:
        return False, "Already up to date."
    return upload_sqlite_file(cfg, p, filename=p.name)
