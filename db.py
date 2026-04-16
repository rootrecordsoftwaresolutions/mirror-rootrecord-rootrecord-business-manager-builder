"""SQLite persistence for RootRecord time tracking (single file, portable across desktop/mobile builds)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from paths import data_dir, legacy_shared_db_path

log = logging.getLogger("time_tracker.db")

UTC = timezone.utc


@dataclass(frozen=True)
class DbConfig:
    """Path to the SQLite database file."""

    db_path: Path


def default_db_path() -> Path:
    return data_dir() / "rootrecord.db"


def maybe_migrate_legacy_desktop_db() -> None:
    """
    Upgraded install: copy legacy shared rootrecord.db into the current data folder once
    if the destination database does not exist yet.
    """
    dest = default_db_path()
    if dest.is_file():
        return
    leg = legacy_shared_db_path()
    if leg is None or not leg.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(leg, dest)


def load_db_config() -> DbConfig:
    raw = os.environ.get("SQLITE_PATH", "").strip()
    if raw:
        return DbConfig(db_path=Path(raw).expanduser().resolve())
    data_dir().mkdir(parents=True, exist_ok=True)
    return DbConfig(db_path=default_db_path())


def connect(cfg: DbConfig) -> sqlite3.Connection:
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(cfg.db_path), timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        import license_gate as _lg

        if _lg.is_read_only():
            conn.execute("PRAGMA query_only = ON")
    except sqlite3.OperationalError:
        pass
    except Exception:
        pass
    return conn


_TABLE_DDL = (
    """
    CREATE TABLE IF NOT EXISTS rr_users (
      telegram_user_id INTEGER NOT NULL PRIMARY KEY,
      username TEXT,
      first_name TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rr_users_updated ON rr_users (updated_at)",
    """
    CREATE TABLE IF NOT EXISTS rr_machine_sessions (
      id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
      started_at_utc TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_machine_started ON rr_machine_sessions (started_at_utc)",
    """
    CREATE TABLE IF NOT EXISTS rr_time_entries (
      id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      machine_session_id INTEGER,
      start_utc TEXT NOT NULL,
      end_utc TEXT NOT NULL,
      category TEXT NOT NULL CHECK (category IN ('evaluation', 'work')),
      description TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
      FOREIGN KEY (machine_session_id) REFERENCES rr_machine_sessions (id) ON DELETE SET NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_time_user_start ON rr_time_entries (user_id, start_utc)",
    "CREATE INDEX IF NOT EXISTS idx_time_category ON rr_time_entries (category)",
    """
    CREATE TABLE IF NOT EXISTS rr_session_events (
      id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      event_type TEXT NOT NULL,
      detail TEXT,
      created_at_utc TEXT NOT NULL,
      FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ev_user_time ON rr_session_events (user_id, created_at_utc)",
    "CREATE INDEX IF NOT EXISTS idx_ev_event_type ON rr_session_events (event_type)",
)


def ensure_schema(cfg: DbConfig | None = None) -> None:
    """Create tables if missing (idempotent)."""
    cfg = cfg or load_db_config()
    conn = connect(cfg)
    try:
        for stmt in _TABLE_DDL:
            conn.execute(stmt.strip())
        conn.commit()
    finally:
        conn.close()


def parse_iso_utc_to_storage(s: str) -> str:
    """Normalize ISO input to UTC naive ISO text for TEXT columns."""
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    dt = datetime.fromisoformat(t)
    if dt.tzinfo is not None:
        dt = dt.astimezone(UTC).replace(tzinfo=None)
    return dt.isoformat()


def now_utc_iso_text() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def insert_machine_session(cfg: DbConfig, started_iso: str) -> int:
    started = parse_iso_utc_to_storage(started_iso)
    created = now_utc_iso_text()
    conn = connect(cfg)
    try:
        cur = conn.execute(
            "INSERT INTO rr_machine_sessions (started_at_utc, created_at) VALUES (?, ?)",
            (started, created),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def upsert_user(
    cfg: DbConfig,
    user_id: int,
    username: str | None,
    first_name: str | None,
) -> None:
    now = now_utc_iso_text()
    conn = connect(cfg)
    try:
        conn.execute(
            """
            INSERT INTO rr_users (telegram_user_id, username, first_name, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET
              username = excluded.username,
              first_name = excluded.first_name,
              updated_at = excluded.updated_at
            """,
            (user_id, username, first_name, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def insert_time_entry(
    cfg: DbConfig,
    user_id: int,
    start_iso: str,
    end_iso: str,
    category: str,
    description: str,
    machine_session_id: int | None,
) -> int:
    start = parse_iso_utc_to_storage(start_iso)
    end = parse_iso_utc_to_storage(end_iso)
    created = now_utc_iso_text()
    conn = connect(cfg)
    try:
        cid = str(uuid.uuid4())
        cur = conn.execute(
            """
            INSERT INTO rr_time_entries
              (user_id, machine_session_id, start_utc, end_utc, category, description, created_at, client_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, machine_session_id, start, end, category, description, created, cid),
        )
        eid = int(cur.lastrowid)
        conn.commit()
        try:
            from sync_engine import enqueue_time_entry_snapshot_by_id, notify_data_changed

            enqueue_time_entry_snapshot_by_id(cfg, int(user_id), eid)
            notify_data_changed(cfg, local_user_id=int(user_id))
        except Exception:
            pass
        return eid
    finally:
        conn.close()


def insert_rich_time_entry(
    cfg: DbConfig,
    user_id: int,
    start_iso: str,
    end_iso: str,
    legacy_category: str,
    description: str,
    machine_session_id: int | None,
    *,
    work_category_id: int | None = None,
    project_id: int | None = None,
    notes: str | None = None,
    billable: int = 1,
    hourly_rate_cents: int | None = None,
    amount_cents: int | None = None,
    currency: str = "USD",
    tag_ids: list[int] | None = None,
    business_id: int | None = None,
) -> int:
    """Insert a time row with optional categorization, money, and tags (requires migration v1)."""
    start = parse_iso_utc_to_storage(start_iso)
    end = parse_iso_utc_to_storage(end_iso)
    created = now_utc_iso_text()
    cid = str(uuid.uuid4())
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            INSERT INTO rr_time_entries
              (user_id, machine_session_id, start_utc, end_utc, category, description, created_at,
               work_category_id, project_id, notes, billable, hourly_rate_cents, amount_cents, currency,
               business_id, client_uuid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                machine_session_id,
                start,
                end,
                legacy_category,
                description,
                created,
                work_category_id,
                project_id,
                notes,
                billable,
                hourly_rate_cents,
                amount_cents,
                currency,
                business_id,
                cid,
            ),
        )
        eid = int(cur.lastrowid)
        if tag_ids:
            for tid in tag_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO time_entry_tags (time_entry_id, tag_id) VALUES (?, ?)",
                    (eid, tid),
                )
        conn.commit()
        try:
            from sync_engine import enqueue_time_entry_snapshot_by_id, notify_data_changed

            enqueue_time_entry_snapshot_by_id(cfg, int(user_id), eid)
            notify_data_changed(cfg, local_user_id=int(user_id))
        except Exception:
            pass
        return eid
    finally:
        conn.close()


def insert_session_event(
    cfg: DbConfig,
    user_id: int,
    event_type: str,
    detail: str,
    *,
    skip_sync_enqueue: bool = False,
) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            """
            INSERT INTO rr_session_events (user_id, event_type, detail, created_at_utc)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, event_type, detail, now_utc_iso_text()),
        )
        conn.commit()
        if not skip_sync_enqueue:
            try:
                from sync_engine import enqueue_session_activity

                enqueue_session_activity(cfg, user_id, event_type, detail)
            except Exception:
                pass
            try:
                from sync_engine import notify_data_changed

                notify_data_changed(cfg, local_user_id=int(user_id))
            except Exception:
                pass
    finally:
        conn.close()


def update_session_event_row(
    cfg: DbConfig,
    user_id: int,
    event_id: int,
    *,
    created_at_utc: str | None = None,
    detail: str | None = None,
) -> bool:
    """Adjust timestamp and/or detail for a Work Log session row (clock in/out, break, …)."""
    if created_at_utc is None and detail is None:
        return True
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT 1 FROM rr_session_events WHERE id = ? AND user_id = ?",
            (int(event_id), int(user_id)),
        ).fetchone()
        if not row:
            return False
        sets: list[str] = []
        vals: list[Any] = []
        if created_at_utc is not None:
            sets.append("created_at_utc = ?")
            vals.append(parse_iso_utc_to_storage(created_at_utc))
        if detail is not None:
            sets.append("detail = ?")
            vals.append(str(detail)[:5000])
        vals.extend([int(event_id), int(user_id)])
        conn.execute(
            f"UPDATE rr_session_events SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            vals,
        )
        conn.commit()
        try:
            from sync_engine import notify_data_changed

            notify_data_changed(cfg, local_user_id=int(user_id))
        except Exception:
            pass
        return True
    finally:
        conn.close()


def delete_session_event_row(cfg: DbConfig, user_id: int, event_id: int) -> bool:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            "DELETE FROM rr_session_events WHERE id = ? AND user_id = ?",
            (int(event_id), int(user_id)),
        )
        conn.commit()
        try:
            from sync_engine import notify_data_changed

            notify_data_changed(cfg, local_user_id=int(user_id))
        except Exception:
            pass
        return cur.rowcount > 0
    finally:
        conn.close()


def restore_session_event_row(
    cfg: DbConfig,
    user_id: int,
    *,
    event_id: int,
    event_type: str,
    detail: str,
    created_at_utc: str,
) -> bool:
    """Undo delete: reinsert the same primary key (best-effort)."""
    conn = connect(cfg)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO rr_session_events (id, user_id, event_type, detail, created_at_utc)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(event_id),
                int(user_id),
                str(event_type)[:200],
                str(detail)[:5000],
                parse_iso_utc_to_storage(created_at_utc),
            ),
        )
        conn.commit()
        try:
            from sync_engine import notify_data_changed

            notify_data_changed(cfg, local_user_id=int(user_id))
        except Exception:
            pass
        return True
    finally:
        conn.close()


def list_registered_user_ids(cfg: DbConfig) -> list[int]:
    conn = connect(cfg)
    try:
        cur = conn.execute("SELECT telegram_user_id FROM rr_users ORDER BY telegram_user_id")
        return [int(r[0]) for r in cur.fetchall()]
    finally:
        conn.close()


def _row_to_dict(cur: sqlite3.Cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    names = [d[0] for d in cur.description]
    return dict(zip(names, row))


def fetch_time_entries_for_user(
    cfg: DbConfig,
    user_id: int,
    since_utc: datetime | None = None,
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        if since_utc is None:
            cur = conn.execute(
                """
                SELECT id, user_id, machine_session_id, start_utc, end_utc, category, description, created_at,
                       work_category_id, project_id, notes, billable, hourly_rate_cents, amount_cents, currency,
                       business_id, client_uuid
                FROM rr_time_entries
                WHERE user_id = ?
                ORDER BY start_utc ASC, id ASC
                """,
                (user_id,),
            )
        else:
            since_txt = since_utc.isoformat() if isinstance(since_utc, datetime) else str(since_utc)
            cur = conn.execute(
                """
                SELECT id, user_id, machine_session_id, start_utc, end_utc, category, description, created_at,
                       work_category_id, project_id, notes, billable, hourly_rate_cents, amount_cents, currency,
                       business_id, client_uuid
                FROM rr_time_entries
                WHERE user_id = ? AND start_utc >= ?
                ORDER BY start_utc ASC, id ASC
                """,
                (user_id, since_txt),
            )
        rows = cur.fetchall()
        return [_row_to_dict(cur, row) for row in rows]
    finally:
        conn.close()


def fetch_time_entry_for_sync(cfg: DbConfig, user_id: int, entry_id: int) -> dict[str, Any] | None:
    """Full row + tag_ids for cloud sync payloads (requires migration v19 client_uuid)."""
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, user_id, machine_session_id, start_utc, end_utc, category, description, created_at,
                   work_category_id, project_id, notes, billable, hourly_rate_cents, amount_cents, currency,
                   business_id, client_uuid
            FROM rr_time_entries
            WHERE id = ? AND user_id = ?
            """,
            (entry_id, user_id),
        )
        row = cur.fetchone()
        if not row:
            return None
        d = _row_to_dict(cur, row)
        tags = conn.execute(
            "SELECT tag_id FROM time_entry_tags WHERE time_entry_id = ? ORDER BY tag_id",
            (entry_id,),
        ).fetchall()
        d["tag_ids"] = [int(t[0]) for t in tags]
        return d
    finally:
        conn.close()


def _as_opt_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def upsert_time_entry_from_remote_sync_payload(
    cfg: DbConfig,
    user_id: int,
    payload: dict[str, Any],
) -> bool:
    """
    Apply a remote `time_entry` upsert (same shape as enqueue snapshot). Does not enqueue sync traffic.
    """
    client_uuid = str(payload.get("client_uuid") or "").strip()
    if not client_uuid:
        return False
    start_utc = str(payload.get("start_utc") or "").strip()
    end_utc = str(payload.get("end_utc") or "").strip()
    if not start_utc or not end_utc:
        return False
    category = str(payload.get("category") or "work").strip().lower()
    if category not in ("evaluation", "work"):
        category = "work"
    description = str(payload.get("description") or "")
    created_raw = str(payload.get("created_at") or "").strip()
    try:
        created_at = parse_iso_utc_to_storage(created_raw) if created_raw else now_utc_iso_text()
    except Exception:
        created_at = now_utc_iso_text()
    machine_session_id = _as_opt_int(payload.get("machine_session_id"))
    work_category_id = _as_opt_int(payload.get("work_category_id"))
    project_id = _as_opt_int(payload.get("project_id"))
    notes = payload.get("notes")
    notes_s = None if notes is None else str(notes)
    billable = int(payload.get("billable") if payload.get("billable") is not None else 1)
    hourly_rate_cents = _as_opt_int(payload.get("hourly_rate_cents"))
    amount_cents = _as_opt_int(payload.get("amount_cents"))
    currency = str(payload.get("currency") or "USD").strip() or "USD"
    business_id = _as_opt_int(payload.get("business_id"))
    tag_ids_raw = payload.get("tag_ids")
    tag_ids: list[int] = []
    if isinstance(tag_ids_raw, list):
        for t in tag_ids_raw:
            ti = _as_opt_int(t)
            if ti is not None:
                tag_ids.append(ti)

    conn = connect(cfg)
    try:
        existing = conn.execute(
            "SELECT id FROM rr_time_entries WHERE user_id = ? AND client_uuid = ?",
            (int(user_id), client_uuid),
        ).fetchone()
        if existing:
            eid = int(existing[0])
            conn.execute(
                """
                UPDATE rr_time_entries SET
                  machine_session_id = ?, start_utc = ?, end_utc = ?, category = ?, description = ?,
                  created_at = ?, work_category_id = ?, project_id = ?, notes = ?, billable = ?,
                  hourly_rate_cents = ?, amount_cents = ?, currency = ?, business_id = ?
                WHERE id = ? AND user_id = ?
                """,
                (
                    machine_session_id,
                    parse_iso_utc_to_storage(start_utc),
                    parse_iso_utc_to_storage(end_utc),
                    category,
                    description,
                    created_at,
                    work_category_id,
                    project_id,
                    notes_s,
                    billable,
                    hourly_rate_cents,
                    amount_cents,
                    currency,
                    business_id,
                    eid,
                    int(user_id),
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO rr_time_entries
                  (user_id, machine_session_id, start_utc, end_utc, category, description, created_at,
                   work_category_id, project_id, notes, billable, hourly_rate_cents, amount_cents, currency,
                   business_id, client_uuid)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    machine_session_id,
                    parse_iso_utc_to_storage(start_utc),
                    parse_iso_utc_to_storage(end_utc),
                    category,
                    description,
                    created_at,
                    work_category_id,
                    project_id,
                    notes_s,
                    billable,
                    hourly_rate_cents,
                    amount_cents,
                    currency,
                    business_id,
                    client_uuid,
                ),
            )
            eid = int(cur.lastrowid)
        conn.execute("DELETE FROM time_entry_tags WHERE time_entry_id = ?", (eid,))
        for tid in tag_ids:
            tag_ok = conn.execute(
                "SELECT 1 FROM tags WHERE id = ? AND user_id = ?",
                (tid, int(user_id)),
            ).fetchone()
            if not tag_ok:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO time_entry_tags (time_entry_id, tag_id) VALUES (?, ?)",
                (eid, tid),
            )
        conn.commit()
        return True
    except Exception:
        log.exception("upsert_time_entry_from_remote_sync_payload failed")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()


def delete_time_entry_by_client_uuid_for_sync(cfg: DbConfig, user_id: int, client_uuid: str) -> bool:
    """Delete by stable sync id (remote delete). Does not enqueue."""
    cu = (client_uuid or "").strip()
    if not cu:
        return False
    conn = connect(cfg)
    try:
        cur = conn.execute(
            "DELETE FROM rr_time_entries WHERE user_id = ? AND client_uuid = ?",
            (int(user_id), cu),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def migrate_registered_users_from_json(cfg: DbConfig, json_path: Path) -> int:
    """If JSON file exists with legacy user ids, insert minimal rr_users rows. Returns count migrated."""
    if not json_path.is_file():
        return 0
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        if not isinstance(data, list) or not data:
            return 0
    except (json.JSONDecodeError, OSError):
        return 0
    existing = set(list_registered_user_ids(cfg))
    n = 0
    now = now_utc_iso_text()
    conn = connect(cfg)
    try:
        for raw in data:
            try:
                uid = int(raw)
            except (TypeError, ValueError):
                continue
            if uid in existing:
                continue
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO rr_users (telegram_user_id, username, first_name, created_at, updated_at)
                VALUES (?, NULL, NULL, ?, ?)
                """,
                (uid, now, now),
            )
            n += cur.rowcount
        conn.commit()
        return n
    finally:
        conn.close()


# Backwards compatibility for export_sheet
def now_utc_naive() -> datetime:
    """Used by export_sheet for date-window filtering."""
    return datetime.now(UTC).replace(tzinfo=None)
