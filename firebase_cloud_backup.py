"""
Encrypt full SQLite database snapshots and upload to Firebase Storage (authenticated user only).

Sync model: one encrypted file per backup (not row-level). Uploads use a SQLite ``backup()`` snapshot
so WAL-mode databases are copied consistently without mutating the live file.

Future second app on the same file: use a single writer at a time (or SQLite file locking); avoid two
processes writing the same ``rootrecord.db`` concurrently. Coordinate version/epoch in app logic if
you add merge semantics later.
"""

from __future__ import annotations

import logging
import os
import hashlib
import shutil
import sqlite3
import tempfile
import zlib
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
import keyring
from cryptography.fernet import Fernet, InvalidToken

from firebase_auth_client import KEYRING_SERVICE, get_local_id_from_token, get_valid_id_token

log = logging.getLogger(__name__)

try:
    import firebase_embedded as _firebase_embedded
except ImportError:
    _firebase_embedded = None

KEYRING_FERNET = "cloud_sync_fernet_key"


@dataclass(frozen=True)
class CloudSyncResult:
    """Outcome of :func:`run_cloud_sync`."""

    mode: Literal["storage", "firestore_fallback"]
    detail: str
    storage_error: str | None = None


class StorageExhaustedError(RuntimeError):
    """Every Storage upload path/bucket attempt failed with a retryable outcome (rules, 404, disabled)."""


def _brief_api_message(detail: object) -> str:
    """Short text for logs / support; avoid dumping full JSON to end users."""
    if isinstance(detail, dict):
        err = detail.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"]).strip()
        if detail.get("message"):
            return str(detail["message"]).strip()
    s = str(detail).strip()
    return s[:240] + ("…" if len(s) > 240 else "")


def _embedded_bucket_and_project() -> tuple[str, str]:
    if _firebase_embedded is None:
        return "", ""
    fe = _firebase_embedded
    b = str(getattr(fe, "FIREBASE_STORAGE_BUCKET", "") or "").strip()
    pid = str(getattr(fe, "FIREBASE_PROJECT_ID", "") or "").strip()
    return b, pid


def _project_id_resolved() -> str:
    pid = os.environ.get("FIREBASE_PROJECT_ID", "").strip()
    if pid:
        return pid
    _eb, ep = _embedded_bucket_and_project()
    return ep.strip()


def _storage_bucket() -> str:
    """Primary bucket name (first candidate)."""
    c = _storage_bucket_candidates()
    return c[0]


def _storage_bucket_candidates() -> list[str]:
    """
    Ordered bucket IDs to try on upload. New Firebase projects often show
    ``project-id.firebasestorage.app`` in the console while the REST upload
    endpoint still expects ``project-id.appspot.com`` (or the reverse), which
    produces 404 if only one is wired up.
    """
    out: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        n = (name or "").strip()
        if n and n not in seen:
            seen.add(n)
            out.append(n)

    add(os.environ.get("FIREBASE_STORAGE_BUCKET", "").strip())
    eb, _ep = _embedded_bucket_and_project()
    add(eb)
    pid = _project_id_resolved()
    if pid:
        add(f"{pid}.appspot.com")
        add(f"{pid}.firebasestorage.app")
    if not out:
        raise RuntimeError("Cloud sync is not configured for this application.")
    return out


def _get_fernet() -> Fernet:
    """Symmetric key stored in OS keyring (per Windows user profile)."""
    try:
        raw = keyring.get_password(KEYRING_SERVICE, KEYRING_FERNET)
    except Exception:
        raw = None
    if raw:
        return Fernet(raw.encode("ascii"))
    key = Fernet.generate_key()
    keyring.set_password(KEYRING_SERVICE, KEYRING_FERNET, key.decode("ascii"))
    return Fernet(key)


def encrypt_payload(plain: bytes) -> bytes:
    f = _get_fernet()
    compressed = zlib.compress(plain, level=6)
    return f.encrypt(compressed)


def decrypt_payload(enc: bytes) -> bytes:
    """Decrypt+zlib decompress (inverse of :func:`encrypt_payload`)."""
    f = _get_fernet()
    try:
        compressed = f.decrypt(enc)
    except InvalidToken as exc:
        raise RuntimeError(
            "Could not decrypt this backup (wrong encryption key for this PC)."
        ) from exc
    return zlib.decompress(compressed)


def _sqlite_magic_ok(blob: bytes) -> bool:
    return len(blob) >= 16 and blob[:15] == b"SQLite format 3"


def read_consistent_database_bytes(db_path: Path) -> bytes:
    """
    Return one consistent snapshot of the SQLite file (WAL-aware).

    Uses ``sqlite3.Connection.backup()`` into a temp file, then reads bytes. Never modifies
    ``db_path``. Falls back to raw ``read_bytes()`` only if backup fails (logged).
    """
    db_path = db_path.resolve()
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    if db_path.stat().st_size == 0:
        return db_path.read_bytes()
    with open(db_path, "rb") as f:
        head = f.read(32)
    if len(head) < 16 or not _sqlite_magic_ok(head):
        log.warning("File does not look like SQLite; uploading raw bytes: %s", db_path)
        return db_path.read_bytes()

    tmp: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix="rr-dbsnap-", suffix=".db", dir=str(db_path.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        # Read-only connection so we never touch WAL checkpoint behavior on the live file.
        src = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True, timeout=60.0)
        dst = sqlite3.connect(str(tmp), timeout=60.0)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
            src.close()
        return tmp.read_bytes()
    except Exception as exc:
        log.warning(
            "SQLite backup snapshot failed (%s); falling back to raw file read (risk if WAL is active).",
            exc,
        )
        return db_path.read_bytes()
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def _sqlite_quick_check_file(path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(path), timeout=15.0)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row) and str(row[0]).lower() == "ok"
        finally:
            conn.close()
    except Exception as exc:
        log.warning("PRAGMA quick_check failed for %s: %s", path, exc)
        return False


def _remove_wal_shm_siblings(db_path: Path) -> None:
    """Remove WAL/SHM sidecars so a replaced main file does not inherit stale journal state."""
    for suffix in ("-wal", "-shm"):
        p = db_path.with_name(db_path.name + suffix)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def upload_encrypted_database(
    *,
    source_path: Path,
    id_token: str,
) -> str:
    """
    Upload encrypted DB to users/{uid}/sync/rootrecord-{utc}.enc
    Returns the object path (not URL).
    """
    uid = get_local_id_from_token(id_token)
    if not uid:
        raise RuntimeError("Could not read user id from token")
    plain = read_consistent_database_bytes(source_path)
    return upload_encrypted_plain_bytes(plain=plain, id_token=id_token)


def upload_encrypted_plain_bytes(*, plain: bytes, id_token: str) -> str:
    """Encrypt already-read DB bytes and upload (used when Storage + Firestore share one snapshot)."""
    uid = get_local_id_from_token(id_token)
    if not uid:
        raise RuntimeError("Could not read user id from token")
    enc = encrypt_payload(plain)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    filename = f"rootrecord-{stamp}.enc"
    object_paths = [
        f"users/{uid}/sync/{filename}",
        f"users/{uid}/backups/{filename}",  # Legacy rules compatibility.
    ]
    headers = {
        "Authorization": f"Bearer {id_token}",
        "Content-Type": "application/octet-stream",
    }
    candidates = _storage_bucket_candidates()
    last_detail: object = ""
    last_status: int | None = None
    with httpx.Client(timeout=300.0) as client:
        for object_path in object_paths:
            q = urllib.parse.quote(object_path, safe="")
            for bucket in candidates:
                url = f"https://firebasestorage.googleapis.com/v0/b/{bucket}/o?name={q}&uploadType=media"
                r = client.post(url, content=enc, headers=headers)
                if r.status_code in (200, 201):
                    return object_path
                last_status = r.status_code
                try:
                    last_detail = r.json()
                except Exception:
                    last_detail = r.text
                if r.status_code == 401:
                    raise RuntimeError(
                        "Cloud sync was not authorized. Sign out, sign in again, and retry."
                    )
                if r.status_code == 400:
                    raise RuntimeError("Cloud sync was rejected. Please try again or contact support.")
                # 403 can happen when rules allow only one object path; keep trying.
                if r.status_code not in (403, 404):
                    log.warning(
                        "Cloud storage upload failed bucket=%s status=%s detail=%s",
                        bucket,
                        last_status,
                        _brief_api_message(last_detail),
                    )
                    raise RuntimeError(
                        "Cloud sync could not upload. Please try again later or contact support."
                    )
    log.warning(
        "Cloud storage upload failed for all buckets/paths buckets=%s paths=%s last_status=%s last_detail=%s",
        candidates,
        object_paths,
        last_status,
        _brief_api_message(last_detail),
    )
    raise StorageExhaustedError(
        "Cloud sync could not reach storage. The project may not have cloud storage enabled, "
        "or this app may need an updated configuration from your vendor. "
        f"(last error: {_brief_api_message(last_detail)})"
    )


def run_cloud_backup(db_path: Path) -> str:
    """Encrypt and upload the current DB file. Returns storage object path."""
    tok = get_valid_id_token()
    if not tok:
        raise RuntimeError("Not signed in or session expired. Sign in again.")
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    return upload_encrypted_database(source_path=db_path, id_token=tok)


def run_cloud_sync(db_path: Path) -> CloudSyncResult:
    """
    Try full encrypted DB upload to Firebase Storage.

    If Storage is unreachable or blocked for all bucket/path combinations,
    falls back to a Firestore metadata marker only (no database bytes in cloud).
    """
    tok = get_valid_id_token()
    if not tok:
        raise RuntimeError("Not signed in or session expired. Sign in again.")
    if not db_path.is_file():
        raise FileNotFoundError(str(db_path))
    plain = read_consistent_database_bytes(db_path)
    try:
        path = upload_encrypted_plain_bytes(plain=plain, id_token=tok)
        return CloudSyncResult(mode="storage", detail=path, storage_error=None)
    except StorageExhaustedError as exc:
        log.warning("Firebase Storage unavailable; using Firestore metadata fallback: %s", exc)
        doc = _write_firestore_sync_marker_bytes(blob=plain, id_token=tok)
        return CloudSyncResult(mode="firestore_fallback", detail=doc, storage_error=str(exc))


def _write_firestore_sync_marker_bytes(*, blob: bytes, id_token: str) -> str:
    uid = get_local_id_from_token(id_token)
    if not uid:
        raise RuntimeError("Could not read user id from token")
    pid = _project_id_resolved()
    if not pid:
        raise RuntimeError("Cloud sync project is not configured.")
    digest = hashlib.sha256(blob).hexdigest()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    doc_rel = f"users/{uid}/meta/sync_status"
    url = f"https://firestore.googleapis.com/v1/projects/{pid}/databases/(default)/documents/{doc_rel}"
    payload = {
        "fields": {
            "source": {"stringValue": "rootrecord_desktop"},
            "status": {"stringValue": "ok"},
            "updated_at_utc": {"stringValue": now},
            "db_sha256": {"stringValue": digest},
            "db_size_bytes": {"integerValue": str(len(blob))},
            "sync_mode": {"stringValue": "metadata_fallback"},
        }
    }
    headers = {
        "Authorization": f"Bearer {id_token}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=60.0) as client:
        r = client.patch(url, json=payload, headers=headers)
    if r.status_code not in (200, 201):
        try:
            detail = r.json()
        except Exception:
            detail = r.text
        raise RuntimeError(
            "Cloud sync failed. Storage upload and Firestore fallback were both unavailable: "
            f"{_brief_api_message(detail)}"
        )
    return f"firestore:{doc_rel}"


def _list_bucket_prefix(
    client: httpx.Client, bucket: str, prefix: str, id_token: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_token: str | None = ""
    while True:
        params: dict[str, str] = {"prefix": prefix, "maxResults": "1000"}
        if page_token:
            params["pageToken"] = page_token
        q = urllib.parse.urlencode(params)
        url = f"https://firebasestorage.googleapis.com/v0/b/{bucket}/o?{q}"
        r = client.get(url, headers={"Authorization": f"Bearer {id_token}"})
        if r.status_code in (401, 403):
            log.warning("List storage objects denied bucket=%s status=%s", bucket, r.status_code)
            return []
        if r.status_code == 404:
            return []
        if r.status_code != 200:
            log.warning(
                "List storage objects failed bucket=%s status=%s body=%s",
                bucket,
                r.status_code,
                (r.text or "")[:200],
            )
            return []
        try:
            data = r.json()
        except Exception:
            return []
        out.extend(data.get("items") or [])
        page_token = data.get("nextPageToken") or ""
        if not page_token:
            break
    return out


def list_encrypted_backup_objects(id_token: str) -> list[tuple[str, str]]:
    """
    List ``rootrecord-*.enc`` under the user's sync/ and backups/ prefixes.
    Returns ``(object_name, updated_iso)`` sorted newest first (best-effort).
    """
    uid = get_local_id_from_token(id_token)
    if not uid:
        raise RuntimeError("Could not read user id from token")
    merged: dict[str, str] = {}
    prefixes = (f"users/{uid}/sync/", f"users/{uid}/backups/")
    with httpx.Client(timeout=120.0) as client:
        for bucket in _storage_bucket_candidates():
            for prefix in prefixes:
                for it in _list_bucket_prefix(client, bucket, prefix, id_token):
                    name = str(it.get("name") or "")
                    if not name.endswith(".enc"):
                        continue
                    base = os.path.basename(name)
                    if not base.startswith("rootrecord-"):
                        continue
                    upd = str(it.get("updated") or it.get("timeCreated") or "")
                    prev = merged.get(name)
                    if prev is None or upd > prev:
                        merged[name] = upd
    rows = [(n, merged[n]) for n in merged]
    rows.sort(key=lambda x: (x[1], x[0]), reverse=True)
    return rows


def download_storage_object(*, object_name: str, id_token: str) -> bytes:
    """Download raw bytes (encrypted payload) from the first bucket that has the object."""
    headers = {"Authorization": f"Bearer {id_token}"}
    enc_name = urllib.parse.quote(object_name, safe="")
    last_status: int | None = None
    last_body = ""
    with httpx.Client(timeout=300.0) as client:
        for bucket in _storage_bucket_candidates():
            url = f"https://firebasestorage.googleapis.com/v0/b/{bucket}/o/{enc_name}?alt=media"
            r = client.get(url, headers=headers)
            if r.status_code == 200:
                return r.content
            last_status = r.status_code
            last_body = (r.text or "")[:300]
            if r.status_code == 404:
                continue
            if r.status_code == 401:
                raise RuntimeError("Download was not authorized. Sign out, sign in again, and retry.")
    raise RuntimeError(
        f"Could not download backup from storage (HTTP {last_status}). {last_body}"
    )


def restore_latest_cloud_database(*, dest_path: Path, id_token: str | None = None) -> tuple[str, int]:
    """
    Download the newest encrypted backup, decrypt, and replace ``dest_path``.

    The Fernet key lives in the OS keyring — restores only work on the same
    Windows profile that created the backups unless the key is migrated.

    Returns ``(storage_object_name, plain_size_bytes)``.
    """
    tok = id_token or get_valid_id_token()
    if not tok:
        raise RuntimeError("Not signed in or session expired. Sign in again.")
    rows = list_encrypted_backup_objects(tok)
    if not rows:
        raise RuntimeError(
            "No encrypted database backups found in Firebase Storage. "
            "Upload at least one full backup (Sync database to cloud) while Storage is working."
        )
    object_name = rows[0][0]
    raw_enc = download_storage_object(object_name=object_name, id_token=tok)
    try:
        plain = decrypt_payload(raw_enc)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Could not unpack backup after decryption. The file may be corrupted."
        ) from exc
    if not _sqlite_magic_ok(plain):
        raise RuntimeError("Decrypted data does not look like a SQLite database.")
    dest_path = dest_path.resolve()
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    if dest_path.is_file():
        bak = dest_path.with_name(f"{dest_path.stem}.pre_restore_{stamp}{dest_path.suffix}")
        shutil.copy2(dest_path, bak)
    tmp = dest_path.with_name(f"{dest_path.name}.partial_{stamp}")
    try:
        tmp.write_bytes(plain)
        if not _sqlite_quick_check_file(tmp):
            raise RuntimeError(
                "Restored file failed SQLite integrity check (quick_check). "
                "Your existing database was not replaced; any *.pre_restore_*.db backup is unchanged."
            )
        _remove_wal_shm_siblings(dest_path)
        os.replace(tmp, dest_path)
    except Exception:
        try:
            if tmp.is_file():
                tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return object_name, len(plain)
