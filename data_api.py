"""High-level data access: settings, categories, projects, tags, money, summaries."""

from __future__ import annotations

import json
import re
import sqlite3
import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db import DbConfig, connect, now_utc_iso_text, parse_iso_utc_to_storage

UTC = timezone.utc


def resolve_app_timezone(cfg: DbConfig):
    """IANA name from settings, or system local when 'system'/empty/invalid."""
    raw = settings_get(cfg, "business_timezone", "system")
    if raw is None or str(raw).strip().lower() in ("", "system"):
        return datetime.now().astimezone().tzinfo
    name = str(raw).strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return datetime.now().astimezone().tzinfo


def utc_naive_bounds_for_local_date(cfg: DbConfig, d: date) -> tuple[str, str]:
    """UTC naive ISO bounds [start, end) for one calendar day in the app timezone."""
    tz = resolve_app_timezone(cfg)
    lo = datetime.combine(d, datetime.min.time(), tzinfo=tz)
    hi = lo + timedelta(days=1)
    return (
        lo.astimezone(UTC).replace(tzinfo=None).isoformat(),
        hi.astimezone(UTC).replace(tzinfo=None).isoformat(),
    )


def utc_naive_bounds_for_local_report_range(
    cfg: DbConfig, start_date: date, end_date_exclusive: date
) -> tuple[str, str]:
    """UTC naive ISO bounds for [start_date, end_date_exclusive) in the app timezone."""
    tz = resolve_app_timezone(cfg)
    lo = datetime.combine(start_date, datetime.min.time(), tzinfo=tz)
    hi = datetime.combine(end_date_exclusive, datetime.min.time(), tzinfo=tz)
    return (
        lo.astimezone(UTC).replace(tzinfo=None).isoformat(),
        hi.astimezone(UTC).replace(tzinfo=None).isoformat(),
    )


def format_stored_utc_as_local(cfg: DbConfig, utc_naive_iso: str) -> str:
    """Short label for a DB naive-UTC instant in the app timezone."""
    if not (utc_naive_iso or "").strip():
        return ""
    dt = datetime.fromisoformat(utc_naive_iso)
    aware = dt.replace(tzinfo=UTC)
    local = aware.astimezone(resolve_app_timezone(cfg))
    return local.strftime("%Y-%m-%d %H:%M")


def duration_seconds_between_iso(start_iso: str, end_iso: str) -> float:
    a = parse_iso_utc_to_storage(start_iso)
    b = parse_iso_utc_to_storage(end_iso)
    da = datetime.fromisoformat(a)
    db = datetime.fromisoformat(b)
    return max(0.0, (db - da).total_seconds())


def _clip_interval_to_window(
    lo: datetime, hi: datetime, w0: datetime, w1: datetime
) -> tuple[datetime, datetime] | None:
    s = max(lo, w0)
    e = min(hi, w1)
    if e <= s:
        return None
    return s, e


def merged_intervals_seconds_union(intervals: list[tuple[datetime, datetime]]) -> float:
    """Total seconds covered after merging overlapping or touching intervals."""
    if not intervals:
        return 0.0
    intervals = sorted(intervals, key=lambda x: x[0])
    acc = 0.0
    cur_s, cur_e = intervals[0]
    for s, e in intervals[1:]:
        if s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            acc += (cur_e - cur_s).total_seconds()
            cur_s, cur_e = s, e
    acc += (cur_e - cur_s).total_seconds()
    return acc


def _time_entry_clips_by_task(
    cfg: DbConfig,
    user_id: int,
    window_start_utc: str,
    window_end_utc: str,
    *,
    exclude_breaks: bool = False,
) -> tuple[list[tuple[datetime, datetime]], dict[str, list[tuple[datetime, datetime]]]]:
    """
    Clip each overlapping entry to [window_start, window_end).
    Returns (all_clips, task_name -> clips) using work category display name.
    """
    w0 = datetime.fromisoformat(window_start_utc)
    w1 = datetime.fromisoformat(window_end_utc)
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg, "t")
        cur = conn.execute(
            """
            SELECT CASE
                     WHEN LOWER(TRIM(COALESCE(t.category, ''))) = 'break' THEN 'Break'
                     ELSE COALESCE(NULLIF(TRIM(c.name), ''), NULLIF(TRIM(t.category), ''), 'Development')
                   END AS task_name,
                   t.start_utc, t.end_utc, t.category
            FROM rr_time_entries t
            LEFT JOIN work_categories c ON c.id = t.work_category_id
            WHERE t.user_id = ? AND t.start_utc < ? AND t.end_utc > ? {extra_biz}
            ORDER BY t.start_utc ASC, t.id ASC
            """.format(extra_biz=extra_biz),
            (user_id, window_end_utc, window_start_utc, *extra_biz_params),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    all_clips: list[tuple[datetime, datetime]] = []
    by_task: dict[str, list[tuple[datetime, datetime]]] = {}
    for task_name, start_s, end_s, legacy_cat in rows:
        tname = str(task_name).strip()
        lcat = str(legacy_cat or "").strip()
        # Exclusion is optional so Break can still appear in detailed/category views.
        if exclude_breaks and (tname.lower() == "break" or lcat.lower() == "break"):
            continue
        sa = datetime.fromisoformat(str(start_s))
        eb = datetime.fromisoformat(str(end_s))
        cl = _clip_interval_to_window(sa, eb, w0, w1)
        if cl is None:
            continue
        all_clips.append(cl)
        by_task.setdefault(tname, []).append(cl)
    return all_clips, by_task


def union_time_seconds_between(
    cfg: DbConfig, user_id: int, window_start_utc: str, window_end_utc: str
) -> float:
    """Unique clock time covered by any entry in the window (overlaps not double-counted)."""
    all_clips, _ = _time_entry_clips_by_task(
        cfg, user_id, window_start_utc, window_end_utc, exclude_breaks=True
    )
    return merged_intervals_seconds_union(all_clips)


def compute_amount_cents(
    start_iso: str,
    end_iso: str,
    hourly_rate_cents: int | None,
    billable: bool,
) -> int | None:
    if not billable or hourly_rate_cents is None or hourly_rate_cents <= 0:
        return None
    hours = duration_seconds_between_iso(start_iso, end_iso) / 3600.0
    return int(round(hours * hourly_rate_cents))


def settings_get(cfg: DbConfig, key: str, default: Any = None) -> Any:
    conn = connect(cfg)
    try:
        try:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        except sqlite3.OperationalError:
            return default
        if not row:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return row[0]
    finally:
        conn.close()


def settings_set(cfg: DbConfig, key: str, value: Any) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, json.dumps(value)),
        )
        conn.commit()
    finally:
        conn.close()


def multi_business_enabled(cfg: DbConfig) -> bool:
    return bool(settings_get(cfg, "multi_business_enabled", False))


def get_active_business_id(cfg: DbConfig) -> int:
    raw = settings_get(cfg, "active_business_id", 1)
    try:
        bid = int(raw)
    except Exception:
        bid = 1
    return bid if bid >= 0 else 1


def set_active_business_id(cfg: DbConfig, business_id: int) -> None:
    settings_set(cfg, "active_business_id", int(business_id))


def business_id_for_new_time_entry(cfg: DbConfig) -> int | None:
    """When multi-business mode is on and a concrete profile is selected, tag new time rows with it."""
    if not multi_business_enabled(cfg):
        return None
    bid = get_active_business_id(cfg)
    return bid if bid > 0 else None


def is_master_business_context(cfg: DbConfig) -> bool:
    return multi_business_enabled(cfg) and get_active_business_id(cfg) == 0


def require_writable_business_context(cfg: DbConfig) -> int:
    if is_master_business_context(cfg):
        raise ValueError("Master profile is read-only. Switch to a business profile to save changes.")
    return get_active_business_id(cfg)


def _business_scope_sql(cfg: DbConfig, alias: str = "") -> tuple[str, tuple[Any, ...]]:
    if not multi_business_enabled(cfg):
        return "", ()
    bid = get_active_business_id(cfg)
    if bid <= 0:
        return "", ()
    col = f"{alias}.business_id" if alias else "business_id"
    return f" AND COALESCE({col}, 1) = ?", (bid,)


def list_business_profiles(
    cfg: DbConfig, user_id: int, *, include_archived: bool = False
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        q = (
            "SELECT id, user_id, name, legal_name, owner, tax_id, email, phone, website, address, "
            "timezone, invoice_notes, archived, created_at, updated_at "
            "FROM business_profiles WHERE user_id = ?"
        )
        params: list[Any] = [user_id]
        if not include_archived:
            q += " AND archived = 0"
        q += " ORDER BY name"
        cur = conn.execute(q, tuple(params))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        return [{"id": 0, "name": "Master (All Businesses)", "archived": 0}] + rows
    finally:
        conn.close()


def save_business_profile(
    cfg: DbConfig,
    user_id: int,
    *,
    profile_id: int | None,
    name: str,
) -> int:
    nm = (name or "").strip()
    if not nm:
        raise ValueError("Business name required")
    now = now_utc_iso_text()
    conn = connect(cfg)
    try:
        if profile_id:
            conn.execute(
                "UPDATE business_profiles SET name=?, updated_at=? WHERE id = ? AND user_id = ?",
                (nm, now, profile_id, user_id),
            )
            conn.commit()
            return int(profile_id)
        cur = conn.execute(
            """
            INSERT INTO business_profiles (user_id, name, archived, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?)
            """,
            (user_id, nm, now, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def set_business_profile_archived(
    cfg: DbConfig,
    user_id: int,
    profile_id: int,
    *,
    archived: bool = True,
) -> None:
    """Soft-delete a business profile (``archived = 1``). Cannot target Master (id 0)."""
    if int(profile_id) <= 0:
        raise ValueError("Cannot remove the Master (all businesses) entry.")
    now = now_utc_iso_text()
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            UPDATE business_profiles
            SET archived = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (1 if archived else 0, now, int(profile_id), user_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise ValueError("Business profile not found.")
    finally:
        conn.close()


def plugin_setting_get(
    cfg: DbConfig, plugin_id: str, key: str, default: Any = None
) -> Any:
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT value FROM rr_plugin_settings WHERE plugin_id = ? AND key = ?",
            (plugin_id, key),
        ).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return row[0]
    except sqlite3.OperationalError:
        return default
    finally:
        conn.close()


def plugin_setting_set(cfg: DbConfig, plugin_id: str, key: str, value: Any) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            """
            INSERT INTO rr_plugin_settings (plugin_id, key, value, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(plugin_id, key) DO UPDATE SET
              value = excluded.value,
              updated_at = excluded.updated_at
            """,
            (plugin_id, key, json.dumps(value), now_utc_iso_text()),
        )
        conn.commit()
    finally:
        conn.close()


def insert_power_snapshot(
    cfg: DbConfig,
    user_id: int,
    *,
    provider: str,
    device_id: str,
    battery_pct: float | None,
    input_watts: float | None,
    ac_input_watts: float | None,
    dc_input_watts: float | None,
    output_watts: float | None,
    state_text: str,
    runtime_minutes: float | None,
    poll_ok: bool,
    error_text: str = "",
) -> int:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            INSERT INTO rr_power_snapshots
              (user_id, provider, device_id, polled_at, battery_pct, input_watts, ac_input_watts, dc_input_watts, output_watts,
               state_text, runtime_minutes, poll_ok, error_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                provider,
                device_id,
                now_utc_iso_text(),
                battery_pct,
                input_watts,
                ac_input_watts,
                dc_input_watts,
                output_watts,
                state_text,
                runtime_minutes,
                1 if poll_ok else 0,
                error_text,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def get_latest_power_snapshot(cfg: DbConfig, user_id: int) -> dict[str, Any] | None:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, user_id, provider, device_id, polled_at, battery_pct, input_watts, ac_input_watts, dc_input_watts, output_watts,
                   state_text, runtime_minutes, poll_ok, error_text
            FROM rr_power_snapshots
            WHERE user_id = ?
            ORDER BY polled_at DESC
            LIMIT 1
            """,
            (user_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()


def insert_power_alert(
    cfg: DbConfig,
    user_id: int,
    *,
    snapshot_id: int | None,
    alert_type: str,
    severity: str,
    message: str,
) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            """
            INSERT INTO rr_power_alerts
              (user_id, snapshot_id, alert_type, severity, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, snapshot_id, alert_type, severity, message, now_utc_iso_text()),
        )
        conn.commit()
    finally:
        conn.close()

def get_work_category(cfg: DbConfig, cid: int | None) -> dict[str, Any] | None:
    if cid is None:
        return None
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT id, name, billable, default_hourly_cents FROM work_categories WHERE id = ?",
            (cid,),
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "billable": row[2], "default_hourly_cents": row[3]}
    finally:
        conn.close()


def list_power_snapshots_recent(
    cfg: DbConfig, user_id: int, limit: int = 240
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, user_id, provider, device_id, polled_at, battery_pct, input_watts, ac_input_watts, dc_input_watts, output_watts,
                   state_text, runtime_minutes, poll_ok, error_text
            FROM rr_power_snapshots
            WHERE user_id = ?
            ORDER BY polled_at DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def list_power_buckets_recent(
    cfg: DbConfig, user_id: int, limit: int = 120
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, user_id, provider, device_id, bucket_start_utc, bucket_end_utc,
                   avg_battery_pct, avg_ac_input_watts, avg_dc_input_watts, avg_output_watts, sample_count
            FROM rr_power_buckets
            WHERE user_id = ?
            ORDER BY bucket_start_utc DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def aggregate_power_snapshots_to_buckets(
    cfg: DbConfig, user_id: int, *, bucket_seconds: int = 300, keep_raw_buckets: int = 3
) -> None:
    conn = connect(cfg)
    try:
        now_utc = datetime.fromisoformat(now_utc_iso_text())
        current_bucket_start = int(now_utc.timestamp() // bucket_seconds) * bucket_seconds
        current_bucket_iso = datetime.fromtimestamp(current_bucket_start, tz=UTC).replace(tzinfo=None).isoformat()
        rows = conn.execute(
            """
            SELECT provider, device_id,
                   substr(polled_at, 1, 16) AS minute_key,
                   AVG(battery_pct) AS avg_battery_pct,
                   AVG(ac_input_watts) AS avg_ac_input_watts,
                   AVG(dc_input_watts) AS avg_dc_input_watts,
                   AVG(output_watts) AS avg_output_watts,
                   COUNT(*) AS sample_count
            FROM rr_power_snapshots
            WHERE user_id = ? AND poll_ok = 1 AND polled_at < ?
            GROUP BY provider, device_id, minute_key
            """,
            (user_id, current_bucket_iso),
        ).fetchall()

        bucket_map: dict[tuple[str, str, int], dict[str, Any]] = {}
        for provider, device_id, minute_key, avg_batt, avg_ac_in, avg_dc_in, avg_out, n in rows:
            try:
                minute_dt = datetime.fromisoformat(f"{minute_key}:00")
            except ValueError:
                continue
            bucket_start = int(minute_dt.timestamp() // bucket_seconds) * bucket_seconds
            key = (str(provider or "ecoflow"), str(device_id or ""), bucket_start)
            agg = bucket_map.setdefault(
                key,
                {"sum_batt": 0.0, "cnt_batt": 0, "sum_ac_in": 0.0, "cnt_ac_in": 0, "sum_dc_in": 0.0, "cnt_dc_in": 0, "sum_out": 0.0, "cnt_out": 0, "samples": 0},
            )
            if avg_batt is not None:
                agg["sum_batt"] += float(avg_batt)
                agg["cnt_batt"] += 1
            if avg_ac_in is not None:
                agg["sum_ac_in"] += float(avg_ac_in)
                agg["cnt_ac_in"] += 1
            if avg_dc_in is not None:
                agg["sum_dc_in"] += float(avg_dc_in)
                agg["cnt_dc_in"] += 1
            if avg_out is not None:
                agg["sum_out"] += float(avg_out)
                agg["cnt_out"] += 1
            agg["samples"] += int(n or 0)

        for (provider, device_id, bucket_start), agg in bucket_map.items():
            start_iso = datetime.fromtimestamp(bucket_start, tz=UTC).replace(tzinfo=None).isoformat()
            end_iso = datetime.fromtimestamp(bucket_start + bucket_seconds, tz=UTC).replace(tzinfo=None).isoformat()
            avg_batt = (agg["sum_batt"] / agg["cnt_batt"]) if agg["cnt_batt"] else None
            avg_ac_in = (agg["sum_ac_in"] / agg["cnt_ac_in"]) if agg["cnt_ac_in"] else None
            avg_dc_in = (agg["sum_dc_in"] / agg["cnt_dc_in"]) if agg["cnt_dc_in"] else None
            avg_out = (agg["sum_out"] / agg["cnt_out"]) if agg["cnt_out"] else None
            conn.execute(
                """
                INSERT INTO rr_power_buckets
                  (user_id, provider, device_id, bucket_start_utc, bucket_end_utc,
                   avg_battery_pct, avg_ac_input_watts, avg_dc_input_watts, avg_output_watts, sample_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider, device_id, bucket_start_utc)
                DO UPDATE SET
                  bucket_end_utc = excluded.bucket_end_utc,
                  avg_battery_pct = excluded.avg_battery_pct,
                  avg_ac_input_watts = excluded.avg_ac_input_watts,
                  avg_dc_input_watts = excluded.avg_dc_input_watts,
                  avg_output_watts = excluded.avg_output_watts,
                  sample_count = excluded.sample_count
                """,
                (
                    user_id,
                    provider,
                    device_id,
                    start_iso,
                    end_iso,
                    avg_batt,
                    avg_ac_in,
                    avg_dc_in,
                    avg_out,
                    int(agg["samples"]),
                ),
            )

        cutoff = current_bucket_start - max(1, keep_raw_buckets) * bucket_seconds
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).replace(tzinfo=None).isoformat()
        conn.execute("DELETE FROM rr_power_snapshots WHERE user_id = ? AND polled_at < ?", (user_id, cutoff_iso))
        conn.commit()
    except sqlite3.OperationalError:
        return
    finally:
        conn.close()


def insert_usgs_quake_event(
    cfg: DbConfig,
    *,
    event_id: str,
    event_time_utc: str,
    magnitude: float | None,
    place: str,
    latitude: float | None,
    longitude: float | None,
    depth_km: float | None,
    detail_url: str,
    raw_json: str,
) -> bool:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO usgs_quake_events
              (event_id, event_time_utc, magnitude, place, latitude, longitude, depth_km, detail_url, raw_json, received_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                event_time_utc,
                magnitude,
                place,
                latitude,
                longitude,
                depth_km,
                detail_url,
                raw_json,
                now_utc_iso_text(),
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def has_usgs_quake_alert(cfg: DbConfig, event_id: str) -> bool:
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT 1 FROM usgs_quake_alerts WHERE event_id = ? LIMIT 1",
            (event_id,),
        ).fetchone()
        return bool(row)
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def insert_usgs_quake_alert(
    cfg: DbConfig,
    *,
    event_id: str,
    distance_miles: float,
    rule_snapshot_json: str,
    acknowledged: bool = False,
) -> bool:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO usgs_quake_alerts
              (event_id, alerted_at, distance_miles, rule_snapshot_json, acknowledged)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_id,
                now_utc_iso_text(),
                distance_miles,
                rule_snapshot_json,
                1 if acknowledged else 0,
            ),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def list_recent_usgs_quake_matches(cfg: DbConfig, limit: int = 10) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT a.event_id, a.alerted_at, a.distance_miles, e.event_time_utc, e.magnitude, e.place
            FROM usgs_quake_alerts a
            JOIN usgs_quake_events e ON e.event_id = a.event_id
            ORDER BY a.alerted_at DESC
            LIMIT ?
            """,
            (int(limit),),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

def get_project(cfg: DbConfig, pid: int) -> dict[str, Any] | None:
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT id, name, default_hourly_cents, currency FROM projects WHERE id = ?",
            (pid,),
        ).fetchone()
        if not row:
            return None
        return {"id": row[0], "name": row[1], "default_hourly_cents": row[2], "currency": row[3]}
    finally:
        conn.close()


def effective_hourly_cents(
    cfg: DbConfig,
    user_id: int,
    *,
    work_category_id: int | None,
    project_id: int | None,
    override_cents: int | None,
) -> int | None:
    if override_cents is not None:
        return override_cents if override_cents > 0 else None
    if project_id:
        p = get_project(cfg, project_id)
        if p and p.get("default_hourly_cents"):
            return int(p["default_hourly_cents"])
    if work_category_id:
        c = get_work_category(cfg, work_category_id)
        if c and c.get("default_hourly_cents"):
            return int(c["default_hourly_cents"])
    d = settings_get(cfg, "default_hourly_cents", 0)
    try:
        d = int(d)
    except (TypeError, ValueError):
        d = 0
    return d if d > 0 else None


def get_work_category_id_by_name(cfg: DbConfig, user_id: int, name: str) -> int | None:
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT id FROM work_categories WHERE user_id = ? AND name = ? AND archived = 0",
            (user_id, name),
        ).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def list_work_categories(
    cfg: DbConfig,
    user_id: int,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra = "" if include_archived else " AND archived = 0"
        cur = conn.execute(
            f"""
            SELECT id, name, color, icon, kind, billable, default_hourly_cents, sort_order, archived
            FROM work_categories WHERE user_id = ?{extra} ORDER BY sort_order, name
            """,
            (user_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def upsert_work_category(
    cfg: DbConfig,
    user_id: int,
    name: str,
    *,
    color: str = "#2B8A8F",
    icon: str = "",
    kind: str = "time",
    billable: int = 1,
    default_hourly_cents: int | None = None,
    sort_order: int = 0,
    cid: int | None = None,
) -> None:
    conn = connect(cfg)
    try:
        if cid:
            conn.execute(
                """
                UPDATE work_categories SET name=?, color=?, icon=?, kind=?, billable=?,
                  default_hourly_cents=?, sort_order=?
                WHERE id = ? AND user_id = ?
                """,
                (name, color, icon, kind, billable, default_hourly_cents, sort_order, cid, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO work_categories
                  (user_id, name, color, icon, kind, billable, default_hourly_cents, sort_order, archived)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (user_id, name, color, icon, kind, billable, default_hourly_cents, sort_order),
            )
        conn.commit()
    finally:
        conn.close()


def archive_work_category(cfg: DbConfig, user_id: int, cid: int) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            "UPDATE work_categories SET archived = 1 WHERE id = ? AND user_id = ?",
            (cid, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def list_projects(
    cfg: DbConfig,
    user_id: int,
    *,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra = "" if include_archived else " AND archived = 0"
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        cur = conn.execute(
            f"""
            SELECT id, name, client_name, color, default_hourly_cents, currency, archived, sort_order
            FROM projects WHERE user_id = ?{extra_biz}{extra} ORDER BY sort_order, name
            """,
            (user_id, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def insert_project(
    cfg: DbConfig,
    user_id: int,
    name: str,
    *,
    client_name: str | None = None,
    color: str = "#5C4D7D",
    default_hourly_cents: int | None = None,
    currency: str = "USD",
    pid: int | None = None,
) -> None:
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        if pid:
            conn.execute(
                """
                UPDATE projects SET name=?, client_name=?, color=?, default_hourly_cents=?, currency=?
                WHERE id = ? AND user_id = ?
                """,
                (name, client_name, color, default_hourly_cents, currency, pid, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO projects (user_id, business_id, name, client_name, color, default_hourly_cents, currency, archived, sort_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 999)
                """,
                (user_id, bid, name, client_name, color, default_hourly_cents, currency),
            )
        conn.commit()
    finally:
        conn.close()


def list_tags(cfg: DbConfig, user_id: int) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            "SELECT id, name FROM tags WHERE user_id = ? ORDER BY name",
            (user_id,),
        )
        return [{"id": r[0], "name": r[1]} for r in cur.fetchall()]
    finally:
        conn.close()


def ensure_tag(cfg: DbConfig, user_id: int, name: str) -> int:
    name = name.strip()
    if not name:
        raise ValueError("empty tag")
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT id FROM tags WHERE user_id = ? AND name = ?",
            (user_id, name),
        ).fetchone()
        if row:
            return int(row[0])
        cur = conn.execute(
            "INSERT INTO tags (user_id, name) VALUES (?, ?)",
            (user_id, name),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def insert_expense(
    cfg: DbConfig,
    user_id: int,
    spent_at_utc: str,
    amount_cents: int,
    currency: str,
    description: str,
    *,
    work_category_id: int | None = None,
    project_id: int | None = None,
    merchant: str | None = None,
    billable: int = 1,
    notes: str | None = None,
    funding_source: str = "cash",
) -> int:
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        cur = conn.execute(
            """
            INSERT INTO expense_entries
              (user_id, business_id, spent_at_utc, amount_cents, currency, work_category_id, project_id,
               description, merchant, billable, notes, created_at, funding_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                bid,
                spent_at_utc,
                amount_cents,
                currency,
                work_category_id,
                project_id,
                description,
                merchant,
                billable,
                notes,
                now_utc_iso_text(),
                str(funding_source or "cash"),
            ),
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


def list_expenses(
    cfg: DbConfig,
    user_id: int,
    days: int = 30,
) -> list[dict[str, Any]]:
    now_u = datetime.fromisoformat(now_utc_iso_text())
    since = (now_u - timedelta(days=days)).isoformat()
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        cur = conn.execute(
            """
            SELECT id, spent_at_utc, amount_cents, currency, description, merchant, billable, notes,
                   work_category_id, project_id, funding_source
            FROM expense_entries
            WHERE user_id = ? AND spent_at_utc >= ? {extra_biz}
            ORDER BY spent_at_utc DESC
            """.format(extra_biz=extra_biz),
            (user_id, since, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def insert_debt(
    cfg: DbConfig,
    user_id: int,
    created_at_utc: str,
    amount_cents: int,
    currency: str,
    description: str,
    *,
    creditor: str = "",
    due_at_utc: str | None = None,
    debt_type: str = "loan",
    account_ref: str = "",
) -> int:
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        cur = conn.execute(
            """
            INSERT INTO debt_entries
              (user_id, business_id, created_at_utc, due_at_utc, amount_cents, currency, creditor, description, status, debt_type, account_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (user_id, bid, created_at_utc, due_at_utc, amount_cents, currency, creditor, description, debt_type, account_ref),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_debts(cfg: DbConfig, user_id: int, days: int = 365) -> list[dict[str, Any]]:
    now_u = datetime.fromisoformat(now_utc_iso_text())
    since = (now_u - timedelta(days=days)).isoformat()
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        cur = conn.execute(
            """
            SELECT id, created_at_utc, due_at_utc, amount_cents, currency, creditor, description, status, debt_type, account_ref
            FROM debt_entries
            WHERE user_id = ? AND created_at_utc >= ? {extra_biz}
            ORDER BY created_at_utc DESC
            """.format(extra_biz=extra_biz),
            (user_id, since, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def set_debt_status(cfg: DbConfig, user_id: int, debt_id: int, status: str) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            "UPDATE debt_entries SET status = ? WHERE id = ? AND user_id = ?",
            (str(status or "open"), int(debt_id), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def settle_debt_with_expense(cfg: DbConfig, user_id: int, debt_id: int) -> int:
    """
    Mark debt closed and post matching expense entry.
    Returns inserted expense entry id.
    """
    conn = connect(cfg)
    now = now_utc_iso_text()
    try:
        if is_master_business_context(cfg):
            raise ValueError("Master profile is read-only. Switch to a business profile to settle debt.")
        bid = require_writable_business_context(cfg)
        row = conn.execute(
            """
            SELECT amount_cents, currency, creditor, description, status
            FROM debt_entries
            WHERE id = ? AND user_id = ? AND COALESCE(business_id, 1) = ?
            """,
            (int(debt_id), user_id, bid),
        ).fetchone()
        if not row:
            raise ValueError("Debt not found in current business profile.")
        amount_cents, currency, creditor, desc, status = row
        if str(status or "").strip().lower() == "closed":
            raise ValueError("Debt is already closed.")
        pay_desc = f"Debt payment: {str(desc or '').strip() or 'Debt'}"
        merch = str(creditor or "").strip()
        cur = conn.execute(
            """
            INSERT INTO expense_entries
              (user_id, business_id, spent_at_utc, amount_cents, currency, work_category_id, project_id,
               description, merchant, billable, notes, created_at, funding_source)
            VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, 0, ?, ?, 'borrowed_funds')
            """,
            (
                user_id,
                bid,
                now,
                int(amount_cents or 0),
                str(currency or "USD"),
                pay_desc,
                merch,
                f"Auto-generated from debt settlement (debt_id={int(debt_id)})",
                now,
            ),
        )
        conn.execute(
            "UPDATE debt_entries SET status = 'closed' WHERE id = ? AND user_id = ?",
            (int(debt_id), user_id),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def summary_today(cfg: DbConfig, user_id: int) -> dict[str, Any]:
    ds, de = utc_naive_bounds_for_local_date(cfg, date.today())
    return summary_between(cfg, user_id, ds, de)


def insert_resource_entry(
    cfg: DbConfig,
    user_id: int,
    at_utc: str,
    amount_cents: int,
    currency: str,
    *,
    source_type: str = "owner_contribution",
    description: str = "",
) -> int:
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        cur = conn.execute(
            """
            INSERT INTO resource_entries
              (user_id, business_id, at_utc, amount_cents, currency, source_type, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                bid,
                at_utc,
                int(amount_cents),
                str(currency or "USD"),
                str(source_type or "owner_contribution"),
                str(description or "").strip(),
                now_utc_iso_text(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_resource_entries(cfg: DbConfig, user_id: int, days: int = 365) -> list[dict[str, Any]]:
    now_u = datetime.fromisoformat(now_utc_iso_text())
    since = (now_u - timedelta(days=days)).isoformat()
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        cur = conn.execute(
            """
            SELECT id, at_utc, amount_cents, currency, source_type, description
            FROM resource_entries
            WHERE user_id = ? AND at_utc >= ? {extra_biz}
            ORDER BY at_utc DESC
            """.format(extra_biz=extra_biz),
            (user_id, since, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def upsert_available_funds_account(
    cfg: DbConfig,
    user_id: int,
    *,
    account_name: str,
    account_type: str,
    currency: str,
    current_balance_cents: int,
    credit_limit_cents: int = 0,
    notes: str = "",
    account_id: int | None = None,
) -> int:
    name = str(account_name or "").strip()
    if not name:
        raise ValueError("Account name is required.")
    ac_type = str(account_type or "cash").strip().lower()
    cur = str(currency or "USD").strip().upper()
    if cur == "":
        cur = "USD"
    now = now_utc_iso_text()
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        if account_id:
            conn.execute(
                """
                UPDATE available_funds_accounts
                SET account_name = ?, account_type = ?, currency = ?,
                    current_balance_cents = ?, credit_limit_cents = ?,
                    notes = ?, updated_at = ?
                WHERE id = ? AND user_id = ? AND COALESCE(business_id, 1) = ?
                """,
                (
                    name,
                    ac_type,
                    cur,
                    int(current_balance_cents),
                    int(credit_limit_cents),
                    str(notes or "").strip(),
                    now,
                    int(account_id),
                    user_id,
                    bid,
                ),
            )
            conn.commit()
            return int(account_id)
        new_cur = conn.execute(
            """
            INSERT INTO available_funds_accounts
              (user_id, business_id, account_name, account_type, currency, current_balance_cents, credit_limit_cents, notes, archived, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                user_id,
                bid,
                name,
                ac_type,
                cur,
                int(current_balance_cents),
                int(credit_limit_cents),
                str(notes or "").strip(),
                now,
                now,
            ),
        )
        conn.commit()
        return int(new_cur.lastrowid)
    finally:
        conn.close()


def list_available_funds_accounts(
    cfg: DbConfig, user_id: int, *, include_archived: bool = False
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        active_clause = "" if include_archived else " AND archived = 0"
        cur = conn.execute(
            """
            SELECT
              id, account_name, account_type, currency, current_balance_cents,
              credit_limit_cents, notes, archived, updated_at
            FROM available_funds_accounts
            WHERE user_id = ? {active_clause} {extra_biz}
            ORDER BY archived ASC, account_type ASC, account_name COLLATE NOCASE ASC
            """.format(active_clause=active_clause, extra_biz=extra_biz),
            (user_id, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def set_available_funds_account_archived(
    cfg: DbConfig, user_id: int, account_id: int, archived: bool
) -> None:
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        conn.execute(
            """
            UPDATE available_funds_accounts
            SET archived = ?, updated_at = ?
            WHERE id = ? AND user_id = ? AND COALESCE(business_id, 1) = ?
            """,
            (1 if archived else 0, now_utc_iso_text(), int(account_id), user_id, bid),
        )
        conn.commit()
    finally:
        conn.close()


def available_funds_totals_by_currency(
    cfg: DbConfig, user_id: int
) -> dict[str, dict[str, int]]:
    rows = list_available_funds_accounts(cfg, user_id, include_archived=False)
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        cur = str(r.get("currency") or "USD")
        ac_type = str(r.get("account_type") or "cash").strip().lower()
        bal = int(r.get("current_balance_cents") or 0)
        lim = int(r.get("credit_limit_cents") or 0)
        bucket = out.setdefault(
            cur,
            {
                "available_cents": 0,
                "balance_cents": 0,
                "credit_limit_cents": 0,
                "used_credit_cents": 0,
            },
        )
        bucket["balance_cents"] += bal
        if ac_type in ("credit_card", "loan_line", "line_of_credit"):
            used = max(0, bal)
            avail = max(0, lim - used)
            bucket["available_cents"] += avail
            bucket["credit_limit_cents"] += max(0, lim)
            bucket["used_credit_cents"] += used
        else:
            bucket["available_cents"] += bal
    return out


def add_scheduled_expense(
    cfg: DbConfig,
    user_id: int,
    *,
    description: str,
    amount_cents: int,
    currency: str,
    frequency: str,
    next_due_utc: str,
    merchant: str = "",
    notes: str = "",
) -> int:
    conn = connect(cfg)
    now = now_utc_iso_text()
    try:
        bid = require_writable_business_context(cfg)
        cur = conn.execute(
            """
            INSERT INTO scheduled_expenses
              (user_id, business_id, description, amount_cents, currency, frequency, next_due_utc, merchant, notes, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (user_id, bid, description.strip(), int(amount_cents), currency, frequency, next_due_utc, merchant.strip(), notes.strip(), now, now),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def list_scheduled_expenses(
    cfg: DbConfig, user_id: int, *, include_inactive: bool = False
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        cur = conn.execute(
            """
            SELECT id, description, amount_cents, currency, frequency, next_due_utc, merchant, notes, active
            FROM scheduled_expenses
            WHERE user_id = ? {active_clause} {extra_biz}
            ORDER BY next_due_utc ASC
            """.format(
                extra_biz=extra_biz,
                active_clause="" if include_inactive else "AND active = 1",
            ),
            (user_id, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def set_scheduled_expense_active(cfg: DbConfig, user_id: int, schedule_id: int, active: bool) -> None:
    conn = connect(cfg)
    try:
        now = now_utc_iso_text()
        conn.execute(
            "UPDATE scheduled_expenses SET active = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (1 if active else 0, now, int(schedule_id), user_id),
        )
        conn.commit()
    finally:
        conn.close()


def _advance_schedule_due(due_iso: str, frequency: str) -> str:
    due = datetime.fromisoformat(due_iso)
    f = (frequency or "monthly").strip().lower()
    if f == "daily":
        return (due + timedelta(days=1)).isoformat()
    if f == "weekly":
        return (due + timedelta(days=7)).isoformat()
    # monthly (default): preserve day-of-month when possible.
    year, month = due.year, due.month + 1
    if month == 13:
        year += 1
        month = 1
    day = min(due.day, calendar.monthrange(year, month)[1])
    return due.replace(year=year, month=month, day=day).isoformat()


def run_due_scheduled_expenses(cfg: DbConfig, user_id: int, *, now_utc: str | None = None, max_runs: int = 24) -> int:
    conn = connect(cfg)
    created = 0
    now_s = now_utc or now_utc_iso_text()
    try:
        if is_master_business_context(cfg):
            return 0
        bid = require_writable_business_context(cfg)
        rows = conn.execute(
            """
            SELECT id, description, amount_cents, currency, frequency, next_due_utc, merchant, notes
            FROM scheduled_expenses
            WHERE user_id = ? AND active = 1 AND COALESCE(business_id, 1) = ? AND next_due_utc <= ?
            ORDER BY next_due_utc ASC
            """,
            (user_id, bid, now_s),
        ).fetchall()
        for sid, desc, amt, cur, freq, due, merch, notes in rows:
            loops = 0
            next_due = str(due)
            while next_due <= now_s and loops < max_runs:
                conn.execute(
                    """
                    INSERT INTO expense_entries
                      (user_id, business_id, spent_at_utc, amount_cents, currency, work_category_id, project_id, description, merchant, billable, notes, created_at)
                    VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, 0, ?, ?)
                    """,
                    (user_id, bid, next_due, int(amt), str(cur), str(desc), str(merch or ""), str(notes or ""), now_s),
                )
                created += 1
                loops += 1
                next_due = _advance_schedule_due(next_due, str(freq))
            conn.execute(
                "UPDATE scheduled_expenses SET next_due_utc = ?, last_run_utc = ?, updated_at = ? WHERE id = ?",
                (next_due, now_s, now_s, sid),
            )
        conn.commit()
        return created
    finally:
        conn.close()


def insert_income(
    cfg: DbConfig,
    user_id: int,
    received_at_utc: str,
    amount_cents: int,
    currency: str,
    description: str,
    *,
    work_category_id: int | None = None,
    project_id: int | None = None,
    source_type: str = "manual",
    source_ref_id: int | None = None,
    notes: str | None = None,
) -> int:
    now = now_utc_iso_text()
    conn = connect(cfg)
    try:
        bid = require_writable_business_context(cfg)
        cur = conn.execute(
            """
            INSERT INTO income_entries
              (user_id, business_id, received_at_utc, amount_cents, currency, description, work_category_id,
               project_id, source_type, source_ref_id, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                bid,
                received_at_utc,
                amount_cents,
                currency,
                description,
                work_category_id,
                project_id,
                source_type,
                source_ref_id,
                notes,
                now,
                now,
            ),
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


def list_income(
    cfg: DbConfig,
    user_id: int,
    days: int = 30,
) -> list[dict[str, Any]]:
    now_u = datetime.fromisoformat(now_utc_iso_text())
    since = (now_u - timedelta(days=days)).isoformat()
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        cur = conn.execute(
            """
            SELECT id, received_at_utc, amount_cents, currency, description, work_category_id, project_id, source_type, notes
            FROM income_entries
            WHERE user_id = ? AND received_at_utc >= ? {extra_biz}
            ORDER BY received_at_utc DESC
            """.format(extra_biz=extra_biz),
            (user_id, since, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def list_income_between(
    cfg: DbConfig,
    user_id: int,
    start_utc: str,
    end_utc: str,
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        cur = conn.execute(
            """
            SELECT id, received_at_utc, amount_cents, currency, description, work_category_id, project_id, source_type, notes
            FROM income_entries
            WHERE user_id = ? AND received_at_utc >= ? AND received_at_utc < ? {extra_biz}
            ORDER BY received_at_utc ASC
            """.format(extra_biz=extra_biz),
            (user_id, start_utc, end_utc, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def list_expenses_between(
    cfg: DbConfig,
    user_id: int,
    start_utc: str,
    end_utc: str,
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        cur = conn.execute(
            """
            SELECT id, spent_at_utc, amount_cents, currency, description, merchant, billable, notes,
                   work_category_id, project_id, funding_source
            FROM expense_entries
            WHERE user_id = ? AND spent_at_utc >= ? AND spent_at_utc < ? {extra_biz}
            ORDER BY spent_at_utc ASC
            """.format(extra_biz=extra_biz),
            (user_id, start_utc, end_utc, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def summary_between(
    cfg: DbConfig,
    user_id: int,
    start_utc: str,
    end_utc: str,
) -> dict[str, Any]:
    sec = union_time_seconds_between(cfg, user_id, start_utc, end_utc)
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        i = conn.execute(
            """
            SELECT SUM(amount_cents) FROM income_entries
            WHERE user_id = ? AND received_at_utc >= ? AND received_at_utc < ? {extra_biz}
            """.format(extra_biz=extra_biz),
            (user_id, start_utc, end_utc, *extra_biz_params),
        ).fetchone()[0]
        e = conn.execute(
            """
            SELECT SUM(amount_cents) FROM expense_entries
            WHERE user_id = ? AND spent_at_utc >= ? AND spent_at_utc < ? {extra_biz}
            """.format(extra_biz=extra_biz),
            (user_id, start_utc, end_utc, *extra_biz_params),
        ).fetchone()[0]
        return {
            "seconds_worked_approx": float(sec),
            "income_cents": int(i or 0),
            "expense_cents": int(e or 0),
        }
    finally:
        conn.close()


def money_totals_by_currency_between(
    cfg: DbConfig,
    user_id: int,
    start_utc: str,
    end_utc: str,
) -> dict[str, dict[str, int]]:
    """
    Return {currency: {'income_cents': int, 'expense_cents': int, 'net_cents': int}}
    scoped to active business context.
    """
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg)
        inc_rows = conn.execute(
            """
            SELECT UPPER(COALESCE(currency, 'USD')) AS ccy, COALESCE(SUM(amount_cents), 0)
            FROM income_entries
            WHERE user_id = ? AND received_at_utc >= ? AND received_at_utc < ? {extra_biz}
            GROUP BY UPPER(COALESCE(currency, 'USD'))
            """.format(extra_biz=extra_biz),
            (user_id, start_utc, end_utc, *extra_biz_params),
        ).fetchall()
        exp_rows = conn.execute(
            """
            SELECT UPPER(COALESCE(currency, 'USD')) AS ccy, COALESCE(SUM(amount_cents), 0)
            FROM expense_entries
            WHERE user_id = ? AND spent_at_utc >= ? AND spent_at_utc < ? {extra_biz}
            GROUP BY UPPER(COALESCE(currency, 'USD'))
            """.format(extra_biz=extra_biz),
            (user_id, start_utc, end_utc, *extra_biz_params),
        ).fetchall()
        out: dict[str, dict[str, int]] = {}
        for ccy, amt in inc_rows:
            k = str(ccy or "USD")
            out.setdefault(k, {"income_cents": 0, "expense_cents": 0, "net_cents": 0})
            out[k]["income_cents"] += int(amt or 0)
        for ccy, amt in exp_rows:
            k = str(ccy or "USD")
            out.setdefault(k, {"income_cents": 0, "expense_cents": 0, "net_cents": 0})
            out[k]["expense_cents"] += int(amt or 0)
        for k, v in out.items():
            v["net_cents"] = int(v["income_cents"] - v["expense_cents"])
        return out
    finally:
        conn.close()


def ledger_net_totals_by_currency(cfg: DbConfig, user_id: int) -> dict[str, dict[str, int]]:
    """
    Cumulative income minus expenses per currency (all recorded history, business scope).
    Used when no separate cash/bank accounts are configured so the dashboard can still
    reflect ledger activity.
    """
    return money_totals_by_currency_between(
        cfg, user_id, "1970-01-01T00:00:00", "2100-12-31T23:59:59"
    )


def list_time_entries_between(
    cfg: DbConfig,
    user_id: int,
    start_utc: str,
    end_utc: str,
) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        extra_biz, extra_biz_params = _business_scope_sql(cfg, "t")
        cur = conn.execute(
            """
            SELECT t.id, t.start_utc, t.end_utc, t.description, t.category, t.work_category_id, t.project_id,
                   t.billable, t.hourly_rate_cents, t.amount_cents, c.name AS category_name, p.name AS project_name
            FROM rr_time_entries t
            LEFT JOIN work_categories c ON c.id = t.work_category_id
            LEFT JOIN projects p ON p.id = t.project_id
            WHERE t.user_id = ? AND t.start_utc < ? AND t.end_utc > ? {extra_biz}
            ORDER BY t.start_utc ASC, t.id ASC
            """.format(extra_biz=extra_biz),
            (user_id, end_utc, start_utc, *extra_biz_params),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def daily_task_breakdown(
    cfg: DbConfig, user_id: int, window_start_utc: str, window_end_utc: str
) -> list[dict[str, Any]]:
    """
    Per-task time after merging overlaps within each task name; percents use unique clock
    time across all tasks as the denominator (so they sum to ~100% when categories do not
    overlap on the timeline; overlapping categories can exceed 100% combined).
    """
    all_clips, by_task = _time_entry_clips_by_task(cfg, user_id, window_start_utc, window_end_utc)
    global_sec = merged_intervals_seconds_union(all_clips)
    rows: list[dict[str, Any]] = []
    for task_name, clips in by_task.items():
        cat_sec = merged_intervals_seconds_union(clips)
        pct = (cat_sec / global_sec * 100.0) if global_sec > 0 else 0.0
        rows.append({"task_name": task_name, "seconds_total": cat_sec, "percent_of_day": pct})
    rows.sort(key=lambda r: -r["seconds_total"])
    return rows


def daily_project_breakdown(
    cfg: DbConfig, user_id: int, window_start_utc: str, window_end_utc: str
) -> list[dict[str, Any]]:
    """Per-project merged time inside a window, with percent of unique on-clock time."""
    rows = list_time_entries_between(cfg, user_id, window_start_utc, window_end_utc)
    w0 = datetime.fromisoformat(window_start_utc)
    w1 = datetime.fromisoformat(window_end_utc)
    all_clips: list[tuple[datetime, datetime]] = []
    by_project: dict[str, list[tuple[datetime, datetime]]] = {}
    for r in rows:
        label = str(r.get("project_name") or "(No project)").strip() or "(No project)"
        try:
            sa = datetime.fromisoformat(str(r.get("start_utc")))
            eb = datetime.fromisoformat(str(r.get("end_utc")))
        except ValueError:
            continue
        cl = _clip_interval_to_window(sa, eb, w0, w1)
        if cl is None:
            continue
        all_clips.append(cl)
        by_project.setdefault(label, []).append(cl)
    global_sec = merged_intervals_seconds_union(all_clips)
    out: list[dict[str, Any]] = []
    for project_name, clips in by_project.items():
        sec = merged_intervals_seconds_union(clips)
        pct = (sec / global_sec * 100.0) if global_sec > 0 else 0.0
        out.append({"project_name": project_name, "seconds_total": sec, "percent_of_day": pct})
    out.sort(key=lambda r: -r["seconds_total"])
    return out


def update_time_entry(
    cfg: DbConfig,
    user_id: int,
    entry_id: int,
    *,
    start_utc: str,
    end_utc: str,
    description: str,
    work_category_id: int | None,
    project_id: int | None,
) -> bool:
    start = parse_iso_utc_to_storage(start_utc)
    end = parse_iso_utc_to_storage(end_utc)
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT start_utc, end_utc FROM rr_time_entries WHERE id = ? AND user_id = ?",
            (entry_id, user_id),
        ).fetchone()
        if not row:
            return False
        prev_start = parse_iso_utc_to_storage(str(row[0]))
        prev_end = parse_iso_utc_to_storage(str(row[1]))
        same_window = start == prev_start and end == prev_end
        # Overlap guard only when moving/resizing the block. Metadata-only edits
        # (category, project, description) must succeed even if other entries overlap
        # the same window — e.g. duplicate rows the user is cleaning up via bulk edit.
        if not same_window:
            overlap = conn.execute(
                """
                SELECT COUNT(*) FROM rr_time_entries
                WHERE user_id = ? AND id <> ? AND start_utc < ? AND end_utc > ?
                """,
                (user_id, entry_id, end, start),
            ).fetchone()[0]
            if int(overlap or 0) > 0:
                return False
        cur = conn.execute(
            """
            UPDATE rr_time_entries
            SET start_utc=?, end_utc=?, description=?, work_category_id=?, project_id=?
            WHERE id = ? AND user_id = ?
            """,
            (start, end, description, work_category_id, project_id, entry_id, user_id),
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


def delete_time_entry(cfg: DbConfig, user_id: int, entry_id: int) -> bool:
    conn = connect(cfg)
    try:
        cur = conn.execute("DELETE FROM rr_time_entries WHERE id = ? AND user_id = ?", (entry_id, user_id))
        conn.commit()
        try:
            from sync_engine import notify_data_changed

            notify_data_changed(cfg, local_user_id=int(user_id))
        except Exception:
            pass
        return cur.rowcount > 0
    finally:
        conn.close()


def insert_time_entry_audit(
    cfg: DbConfig,
    user_id: int,
    *,
    entry_id: int | None,
    action: str,
    old_row: dict[str, Any] | None = None,
    new_row: dict[str, Any] | None = None,
    meta_json: str = "",
) -> None:
    conn = connect(cfg)
    try:
        o = old_row or {}
        n = new_row or {}
        conn.execute(
            """
            INSERT INTO time_entry_audit
              (user_id, entry_id, action,
               old_start_utc, old_end_utc, new_start_utc, new_end_utc,
               old_description, new_description,
               old_work_category_id, new_work_category_id,
               old_project_id, new_project_id, meta_json, changed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                entry_id,
                action,
                o.get("start_utc"),
                o.get("end_utc"),
                n.get("start_utc"),
                n.get("end_utc"),
                o.get("description"),
                n.get("description"),
                o.get("work_category_id"),
                n.get("work_category_id"),
                o.get("project_id"),
                n.get("project_id"),
                meta_json,
                now_utc_iso_text(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_time_entry_audit(cfg: DbConfig, user_id: int, limit: int = 120) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, entry_id, action, old_start_utc, old_end_utc, new_start_utc, new_end_utc,
                   old_description, new_description, old_work_category_id, new_work_category_id,
                   old_project_id, new_project_id, meta_json, changed_at
            FROM time_entry_audit
            WHERE user_id = ?
            ORDER BY changed_at DESC
            LIMIT ?
            """,
            (user_id, int(limit)),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def list_finalized_ranges(cfg: DbConfig, user_id: int) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, start_utc, end_utc, notes, created_at
            FROM finalized_ranges
            WHERE user_id = ?
            ORDER BY start_utc ASC
            """,
            (user_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def add_finalized_range(
    cfg: DbConfig, user_id: int, *, start_utc: str, end_utc: str, notes: str = ""
) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO finalized_ranges (user_id, start_utc, end_utc, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, start_utc, end_utc, notes, now_utc_iso_text()),
        )
        conn.commit()
    finally:
        conn.close()


def remove_finalized_range(cfg: DbConfig, user_id: int, *, start_utc: str, end_utc: str) -> None:
    conn = connect(cfg)
    try:
        conn.execute(
            "DELETE FROM finalized_ranges WHERE user_id = ? AND start_utc = ? AND end_utc = ?",
            (user_id, start_utc, end_utc),
        )
        conn.commit()
    finally:
        conn.close()


def is_time_range_locked(cfg: DbConfig, user_id: int, *, start_utc: str, end_utc: str) -> bool:
    conn = connect(cfg)
    try:
        row = conn.execute(
            """
            SELECT 1 FROM finalized_ranges
            WHERE user_id = ? AND start_utc < ? AND end_utc > ?
            LIMIT 1
            """,
            (user_id, end_utc, start_utc),
        ).fetchone()
        return bool(row)
    finally:
        conn.close()


def search_records(
    cfg: DbConfig,
    user_id: int,
    term: str,
    *,
    days: int = 90,
) -> list[dict[str, Any]]:
    q = f"%{re.sub(r'\\s+', ' ', (term or '').strip())}%"
    if q == "%%":
        return []
    now_u = datetime.fromisoformat(now_utc_iso_text())
    since = (now_u - timedelta(days=days)).isoformat()
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT 'time' AS kind, id, start_utc AS at_utc, description, COALESCE(amount_cents, 0) AS amount_cents, currency
            FROM rr_time_entries
            WHERE user_id = ? AND start_utc >= ? AND description LIKE ?
            UNION ALL
            SELECT 'income' AS kind, id, received_at_utc AS at_utc, description, amount_cents, currency
            FROM income_entries
            WHERE user_id = ? AND received_at_utc >= ? AND description LIKE ?
            UNION ALL
            SELECT 'expense' AS kind, id, spent_at_utc AS at_utc, description, amount_cents, currency
            FROM expense_entries
            WHERE user_id = ? AND spent_at_utc >= ? AND description LIKE ?
            ORDER BY at_utc DESC
            LIMIT 250
            """,
            (user_id, since, q, user_id, since, q, user_id, since, q),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def list_quick_actions(cfg: DbConfig, user_id: int) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT id, label, work_category_id, project_id, default_description, sort_order
            FROM quick_actions WHERE user_id = ? ORDER BY sort_order, label
            """,
            (user_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def save_quick_action(
    cfg: DbConfig,
    user_id: int,
    label: str,
    *,
    work_category_id: int | None = None,
    project_id: int | None = None,
    default_description: str = "",
    qid: int | None = None,
) -> None:
    conn = connect(cfg)
    try:
        if qid:
            conn.execute(
                """
                UPDATE quick_actions SET label=?, work_category_id=?, project_id=?, default_description=?
                WHERE id = ? AND user_id = ?
                """,
                (label, work_category_id, project_id, default_description, qid, user_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO quick_actions (user_id, label, work_category_id, project_id, default_description, sort_order)
                VALUES (?, ?, ?, ?, ?, 999)
                """,
                (user_id, label, work_category_id, project_id, default_description),
            )
        conn.commit()
    finally:
        conn.close()


def delete_quick_action(cfg: DbConfig, user_id: int, qid: int) -> None:
    conn = connect(cfg)
    try:
        conn.execute("DELETE FROM quick_actions WHERE id = ? AND user_id = ?", (qid, user_id))
        conn.commit()
    finally:
        conn.close()


def recent_time_entries(cfg: DbConfig, user_id: int, limit: int = 15) -> list[dict[str, Any]]:
    conn = connect(cfg)
    try:
        cur = conn.execute(
            """
            SELECT t.id, t.start_utc, t.end_utc, t.category, t.description, t.amount_cents, t.currency,
                   t.work_category_id, t.project_id, c.name AS cat_name
            FROM rr_time_entries t
            LEFT JOIN work_categories c ON c.id = t.work_category_id
            WHERE t.user_id = ?
            ORDER BY t.start_utc DESC, t.id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()

