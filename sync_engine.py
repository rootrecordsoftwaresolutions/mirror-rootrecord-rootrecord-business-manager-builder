"""
Offline-first sync: local SQLite is authoritative for UX; the remote service holds a replayable event stream.

- Push: `sync_outbox` → POST /v1/sync/push (session Bearer).
- Pull: GET /v1/sync/pull → apply remote events idempotently (skip same device + already applied).

Call `notify_data_changed` after local writes so pending rows flush soon; only entity types with
both enqueue + apply handlers participate (today: session activity). Full-database snapshots use
the separate online backup path in the desktop app.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from typing import Any, Literal

import httpx

from data_api import settings_get, settings_set
from db import DbConfig, connect, now_utc_iso_text
from license_client import LICENSE_SESSION_TOKEN_KEY
from license_config import get_license_api_config
from license_gate import is_read_only

log = logging.getLogger("rootrecord.sync")

# Avoid overlapping sync cycles when many saves fire `notify_data_changed` in quick succession.
_sync_cycle_lock = threading.Lock()

SYNC_LAST_PULL_MS_KEY = "sync_last_pull_ms"
_SYNC_PUSH_PATH = "/v1/sync/push"
_SYNC_PULL_PATH = "/v1/sync/pull"
_MAX_BATCH = 50
_MAX_PULL = 200

Op = Literal["upsert", "delete"]


def _mark_mutation_applied(cfg: DbConfig, client_mutation_id: str, reason: str) -> None:
    try:
        conn = connect(cfg)
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO sync_applied_remote (client_mutation_id, applied_at_utc, reason)
                VALUES (?, ?, ?)
                """,
                (client_mutation_id[:200], now_utc_iso_text(), reason[:80]),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.debug("mark applied failed", exc_info=True)


def _is_mutation_applied(cfg: DbConfig, client_mutation_id: str) -> bool:
    try:
        conn = connect(cfg)
        try:
            row = conn.execute(
                "SELECT 1 FROM sync_applied_remote WHERE client_mutation_id = ?",
                (client_mutation_id[:200],),
            ).fetchone()
            return row is not None
        finally:
            conn.close()
    except Exception:
        return False


def apply_pulled_events(
    cfg: DbConfig,
    events: list[Any],
    *,
    local_user_id: int,
) -> int:
    """
    Replay remote sync events into local tables. Idempotent per client_mutation_id.
    Skips events from this device (already reflected locally). Returns count newly applied.
    """
    if is_read_only() or not events:
        return 0

    try:
        from license_runtime import load_or_create_device_id

        my_device = load_or_create_device_id()
    except Exception:
        my_device = ""

    from db import insert_session_event

    applied = 0
    for raw in events[:_MAX_PULL]:
        if not isinstance(raw, dict):
            continue
        cmid = raw.get("client_mutation_id")
        if not isinstance(cmid, str) or not cmid.strip():
            continue
        cmid = cmid.strip()[:200]

        if _is_mutation_applied(cfg, cmid):
            continue

        dev = raw.get("device_id")
        if isinstance(dev, str) and dev.strip() and dev.strip() == my_device:
            _mark_mutation_applied(cfg, cmid, "same_device")
            continue

        entity = str(raw.get("entity_type") or "")
        op = str(raw.get("op") or "upsert")
        payload = raw.get("payload")
        if not isinstance(payload, dict):
            payload = {}

        if entity == "session_activity" and op != "delete":
            et = str(payload.get("event_type") or "activity")
            detail = str(payload.get("detail") or "")[:5000]
            try:
                insert_session_event(cfg, int(local_user_id), et, detail, skip_sync_enqueue=True)
            except Exception:
                log.debug("apply session_activity failed", exc_info=True)
                continue
            _mark_mutation_applied(cfg, cmid, "applied")
            applied += 1
        else:
            _mark_mutation_applied(cfg, cmid, "skipped_unsupported")

    if applied:
        log.info("sync pull applied %s remote event(s)", applied)
    return applied


def enqueue_mutation(
    cfg: DbConfig,
    user_id: int,
    entity_type: str,
    entity_key: str,
    op: Op,
    payload: dict[str, Any],
) -> str | None:
    """
    Queue a change while offline or online. Returns client_mutation_id, or None if not queued (read-only / error).
    """
    if is_read_only():
        return None
    client_mutation_id = str(uuid.uuid4())
    try:
        conn = connect(cfg)
        try:
            conn.execute(
                """
                INSERT INTO sync_outbox (
                  client_mutation_id, user_id, entity_type, entity_key, op, payload_json, created_at_utc, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    client_mutation_id,
                    int(user_id),
                    entity_type[:120],
                    entity_key[:500],
                    op,
                    json.dumps(payload, separators=(",", ":"), default=str),
                    now_utc_iso_text(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.debug("sync enqueue failed", exc_info=True)
        return None
    return client_mutation_id


def enqueue_session_activity(cfg: DbConfig, user_id: int, event_type: str, detail: str) -> None:
    """Lightweight hook: session UI events (clock in, break, etc.)."""
    enqueue_mutation(
        cfg,
        user_id,
        "session_activity",
        f"{event_type}:{detail[:120]}",
        "upsert",
        {"event_type": event_type, "detail": detail[:4000]},
    )


def _session_bearer(cfg: DbConfig) -> str | None:
    tok = settings_get(cfg, LICENSE_SESSION_TOKEN_KEY, None)
    if isinstance(tok, str) and tok.strip():
        return tok.strip()
    return None


def flush_sync_outbox(cfg: DbConfig) -> int:
    """POST pending rows to the license/sync service. Returns number of mutations accepted (not rows deleted locally)."""
    lic = get_license_api_config()
    bearer = _session_bearer(cfg)
    if not lic or not bearer:
        return 0
    if is_read_only():
        return 0

    try:
        from license_runtime import load_or_create_device_id

        device_id = load_or_create_device_id()
    except Exception:
        log.debug("sync device id", exc_info=True)
        return 0

    conn = connect(cfg)
    try:
        rows = conn.execute(
            """
            SELECT id, client_mutation_id, user_id, entity_type, entity_key, op, payload_json
            FROM sync_outbox
            WHERE status = 'pending'
            ORDER BY id
            LIMIT ?
            """,
            (_MAX_BATCH,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    events: list[dict[str, Any]] = []
    row_ids: list[int] = []
    for r in rows:
        rid, cmid, _uid, et, ek, op, pjson = (
            int(r[0]),
            str(r[1]),
            int(r[2]),
            str(r[3]),
            str(r[4]),
            str(r[5]),
            str(r[6]),
        )
        row_ids.append(rid)
        try:
            payload = json.loads(pjson)
        except Exception:
            payload = {"_raw": pjson}
        events.append(
            {
                "client_mutation_id": cmid,
                "entity_type": et,
                "entity_key": ek,
                "op": op if op in ("upsert", "delete") else "upsert",
                "payload": payload,
            }
        )

    url = f"{lic.base_url}{_SYNC_PUSH_PATH}"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {bearer}"}
    try:
        with httpx.Client(timeout=45.0) as client:
            r = client.post(url, headers=headers, json={"device_id": device_id, "events": events})
        if r.status_code == 401:
            return 0
        r.raise_for_status()
        body = r.json()
        accepted = int(body.get("accepted", len(events)))
    except Exception as exc:
        log.debug("sync push failed: %s", exc, exc_info=True)
        conn = connect(cfg)
        try:
            conn.execute(
                f"""
                UPDATE sync_outbox
                SET status = 'failed', last_error = ?, attempt_count = attempt_count + 1
                WHERE id IN ({",".join("?" * len(row_ids))})
                """,
                [str(exc)[:500], *row_ids],
            )
            conn.commit()
        finally:
            conn.close()
        return 0

    conn = connect(cfg)
    try:
        conn.execute(
            f"""
            UPDATE sync_outbox
            SET status = 'sent', last_error = NULL, attempt_count = attempt_count + 1
            WHERE id IN ({",".join("?" * len(row_ids))})
            """,
            row_ids,
        )
        conn.commit()
    finally:
        conn.close()

    return min(accepted, len(events))


def pull_remote_changes(cfg: DbConfig, *, local_user_id: int = 1) -> tuple[int, int]:
    """
    GET remote events since last pull cursor, apply them, advance cursor.
    Returns (events_received, events_applied_to_local).
    """
    lic = get_license_api_config()
    bearer = _session_bearer(cfg)
    if not lic or not bearer:
        return 0, 0

    since_raw = settings_get(cfg, SYNC_LAST_PULL_MS_KEY, "0")
    try:
        since_ms = int(str(since_raw).strip() or "0")
    except ValueError:
        since_ms = 0

    url = f"{lic.base_url}{_SYNC_PULL_PATH}?since_ms={since_ms}"
    headers = {"Authorization": f"Bearer {bearer}"}
    try:
        with httpx.Client(timeout=45.0) as client:
            r = client.get(url, headers=headers)
        if r.status_code == 401:
            return 0, 0
        r.raise_for_status()
        data = r.json()
        events = data.get("events")
        if not isinstance(events, list):
            return 0, 0

        applied = apply_pulled_events(cfg, events, local_user_id=local_user_id)

        server_time = data.get("server_time_ms")
        max_created = since_ms
        for ev in events[:_MAX_PULL]:
            if not isinstance(ev, dict):
                continue
            ca = ev.get("created_at")
            if isinstance(ca, int):
                max_created = max(max_created, ca)
        if isinstance(server_time, int):
            max_created = max(max_created, server_time)
        elif isinstance(server_time, float):
            max_created = max(max_created, int(server_time))

        if max_created > since_ms:
            settings_set(cfg, SYNC_LAST_PULL_MS_KEY, str(max_created))

        return len(events), applied
    except Exception:
        log.debug("sync pull failed", exc_info=True)
        return 0, 0


def sync_cycle_best_effort(cfg: DbConfig, *, local_user_id: int = 1) -> tuple[int, int, int]:
    """Push outbox, then pull + apply. Returns (pushed, pulled_count, applied_count). Serialized globally."""
    with _sync_cycle_lock:
        n_out = flush_sync_outbox(cfg)
        n_in, n_applied = pull_remote_changes(cfg, local_user_id=local_user_id)
    return n_out, n_in, n_applied


def schedule_background_sync(cfg: DbConfig, *, local_user_id: int = 1) -> None:
    """Run a sync cycle without blocking the UI thread."""

    def run() -> None:
        try:
            sync_cycle_best_effort(cfg, local_user_id=local_user_id)
        except Exception:
            log.debug("background sync", exc_info=True)

    threading.Thread(target=run, daemon=True).start()


def notify_data_changed(cfg: DbConfig, *, local_user_id: int = 1) -> None:
    """After local DB mutations: try to push outbox and pull remote soon (non-blocking)."""
    if is_read_only():
        return
    schedule_background_sync(cfg, local_user_id=local_user_id)
