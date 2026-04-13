"""SQLite persistence for RootRecord time tracking (single file, portable across desktop/mobile builds)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
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
        cur = conn.execute(
            """
            INSERT INTO rr_time_entries
              (user_id, machine_session_id, start_utc, end_utc, category, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, machine_session_id, start, end, category, description, created),
        )
        conn.commit()
        try:
            from sync_engine import notify_data_changed

            notify_data_changed(cfg, local_user_id=int(user_id))
        except Exception:
            pass
        return int(cur.lastrowid)
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
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            INSERT INTO rr_time_entries
              (user_id, machine_session_id, start_utc, end_utc, category, description, created_at,
               work_category_id, project_id, notes, billable, hourly_rate_cents, amount_cents, currency,
               business_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            from sync_engine import notify_data_changed

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
                       work_category_id, project_id, notes, billable, hourly_rate_cents, amount_cents, currency
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
                       work_category_id, project_id, notes, billable, hourly_rate_cents, amount_cents, currency
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
