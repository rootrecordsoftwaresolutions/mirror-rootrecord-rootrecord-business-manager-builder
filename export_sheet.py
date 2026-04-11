"""Build spreadsheet (CSV) bytes from SQLite time-entry rows."""

from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta, timezone
from typing import Any

from db import DbConfig, fetch_time_entries_for_user, now_utc_naive


UTC = timezone.utc


def _fmt_dt(v: Any) -> str:
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
        return v.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return str(v)


def build_hours_csv_bytes(
    cfg: DbConfig,
    user_id: int,
    days: int | None,
) -> tuple[bytes, str]:
    """
    Returns (utf-8 csv bytes, suggested filename).
    If days is None, export all rows for the user.
    """
    since: datetime | None = None
    if days is not None and days > 0:
        since = now_utc_naive() - timedelta(days=days)

    rows = fetch_time_entries_for_user(cfg, user_id, since_utc=since)
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(
        (
            "id",
            "user_id",
            "machine_session_id",
            "start_utc",
            "end_utc",
            "legacy_kind",
            "description",
            "created_at",
            "work_category_id",
            "project_id",
            "notes",
            "billable",
            "hourly_rate_cents",
            "amount_cents",
            "currency",
        )
    )
    for r in rows:
        w.writerow(
            (
                r.get("id"),
                r.get("user_id"),
                r.get("machine_session_id"),
                _fmt_dt(r.get("start_utc")),
                _fmt_dt(r.get("end_utc")),
                r.get("category"),
                (r.get("description") or "").replace("\r\n", "\n"),
                _fmt_dt(r.get("created_at")),
                r.get("work_category_id"),
                r.get("project_id"),
                r.get("notes"),
                r.get("billable"),
                r.get("hourly_rate_cents"),
                r.get("amount_cents"),
                r.get("currency"),
            )
        )
    raw = buf.getvalue().encode("utf-8-sig")  # BOM helps Excel open UTF-8
    suffix = "all" if days is None else f"last_{days}d"
    fname = f"RootRecord_hours_{user_id}_{suffix}.csv"
    return raw, fname


def build_hours_csv_bytes_between(
    cfg: DbConfig,
    user_id: int,
    start_utc: datetime,
    end_utc: datetime,
) -> tuple[bytes, str]:
    rows = fetch_time_entries_for_user(cfg, user_id, since_utc=None)
    filtered = [
        r
        for r in rows
        if str(r.get("start_utc", "")) < end_utc.isoformat()
        and str(r.get("end_utc", "")) > start_utc.isoformat()
    ]
    buf = io.StringIO(newline="")
    w = csv.writer(buf)
    w.writerow(
        (
            "id",
            "user_id",
            "machine_session_id",
            "start_utc",
            "end_utc",
            "legacy_kind",
            "description",
            "created_at",
            "work_category_id",
            "project_id",
            "notes",
            "billable",
            "hourly_rate_cents",
            "amount_cents",
            "currency",
        )
    )
    for r in filtered:
        w.writerow(
            (
                r.get("id"),
                r.get("user_id"),
                r.get("machine_session_id"),
                _fmt_dt(r.get("start_utc")),
                _fmt_dt(r.get("end_utc")),
                r.get("category"),
                (r.get("description") or "").replace("\r\n", "\n"),
                _fmt_dt(r.get("created_at")),
                r.get("work_category_id"),
                r.get("project_id"),
                r.get("notes"),
                r.get("billable"),
                r.get("hourly_rate_cents"),
                r.get("amount_cents"),
                r.get("currency"),
            )
        )
    raw = buf.getvalue().encode("utf-8-sig")
    suffix = f"{start_utc.date().isoformat()}_to_{(end_utc - timedelta(seconds=1)).date().isoformat()}"
    fname = f"RootRecord_hours_{user_id}_{suffix}.csv"
    return raw, fname
