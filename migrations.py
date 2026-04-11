"""Schema migrations after base rr_* tables exist."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from db import DbConfig

from db import connect, now_utc_iso_text
from paths import data_dir


def _has_column(conn: sqlite3.Connection, table: str, col: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Categories, projects, tags, expenses, settings, time entry extensions."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
          key TEXT NOT NULL PRIMARY KEY,
          value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS work_categories (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          color TEXT NOT NULL DEFAULT '#2B8A8F',
          icon TEXT NOT NULL DEFAULT '',
          kind TEXT NOT NULL DEFAULT 'time'
            CHECK (kind IN ('time','expense','both')),
          billable INTEGER NOT NULL DEFAULT 1,
          default_hourly_cents INTEGER,
          sort_order INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
          UNIQUE (user_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_wcat_user ON work_categories (user_id, sort_order);

        CREATE TABLE IF NOT EXISTS projects (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          client_name TEXT,
          color TEXT NOT NULL DEFAULT '#5C4D7D',
          default_hourly_cents INTEGER,
          currency TEXT NOT NULL DEFAULT 'USD',
          notes TEXT,
          archived INTEGER NOT NULL DEFAULT 0,
          sort_order INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
          UNIQUE (user_id, name)
        );
        CREATE INDEX IF NOT EXISTS idx_proj_user ON projects (user_id, sort_order);

        CREATE TABLE IF NOT EXISTS tags (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
          UNIQUE (user_id, name)
        );

        CREATE TABLE IF NOT EXISTS expense_entries (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          spent_at_utc TEXT NOT NULL,
          amount_cents INTEGER NOT NULL,
          currency TEXT NOT NULL DEFAULT 'USD',
          work_category_id INTEGER,
          project_id INTEGER,
          description TEXT NOT NULL,
          merchant TEXT,
          billable INTEGER NOT NULL DEFAULT 1,
          notes TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
          FOREIGN KEY (work_category_id) REFERENCES work_categories (id) ON DELETE SET NULL,
          FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_exp_user ON expense_entries (user_id, spent_at_utc);

        CREATE TABLE IF NOT EXISTS quick_actions (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          label TEXT NOT NULL,
          work_category_id INTEGER,
          project_id INTEGER,
          default_description TEXT,
          sort_order INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
          FOREIGN KEY (work_category_id) REFERENCES work_categories (id) ON DELETE SET NULL,
          FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS time_entry_tags (
          time_entry_id INTEGER NOT NULL,
          tag_id INTEGER NOT NULL,
          PRIMARY KEY (time_entry_id, tag_id),
          FOREIGN KEY (time_entry_id) REFERENCES rr_time_entries (id) ON DELETE CASCADE,
          FOREIGN KEY (tag_id) REFERENCES tags (id) ON DELETE CASCADE
        );
        """
    )

    alters = [
        ("rr_time_entries", "work_category_id", "INTEGER"),
        ("rr_time_entries", "project_id", "INTEGER"),
        ("rr_time_entries", "notes", "TEXT"),
        ("rr_time_entries", "billable", "INTEGER NOT NULL DEFAULT 1"),
        ("rr_time_entries", "hourly_rate_cents", "INTEGER"),
        ("rr_time_entries", "amount_cents", "INTEGER"),
        ("rr_time_entries", "currency", "TEXT NOT NULL DEFAULT 'USD'"),
    ]
    for table, col, decl in alters:
        if not _has_column(conn, table, col):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _read_install_options() -> dict[str, object]:
    """Read one-time install options from data/install_options.json and then remove it."""
    pref_file = data_dir() / "install_options.json"
    raw_options: dict[str, object] = {}
    if pref_file.is_file():
        try:
            loaded = json.loads(pref_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                raw_options = loaded
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            raw_options = {}
        try:
            pref_file.unlink()
        except OSError:
            pass
        return raw_options
    return raw_options


def _read_starter_seed_preference(opts: dict[str, object]) -> bool:
    """Resolve starter-content preference from install options or env var."""
    if "include_starter_content" in opts:
        return bool(opts.get("include_starter_content", True))
    env = os.environ.get("ROOTRECORD_SEED_DEFAULTS", "").strip().lower()
    if env in ("0", "false", "no", "skip", "none"):
        return False
    if env in ("1", "true", "yes", "include", "default"):
        return True
    return True


def _apply_install_options(conn: sqlite3.Connection, opts: dict[str, object]) -> None:
    """Apply installer-captured preferences into app_settings for first launch."""
    if not opts:
        return
    mapping: dict[str, str] = {
        "currency_default": "currency_default",
        "theme": "theme",
        "prompt_interval_sec": "prompt_interval_sec",
        "prompt_first_delay_sec": "prompt_first_delay_sec",
        "show_money_in_dashboard": "show_money_in_dashboard",
        "business_name": "business_name",
        "business_legal_name": "business_legal_name",
        "business_owner": "business_owner",
        "business_tax_id": "business_tax_id",
        "business_email": "business_email",
        "business_phone": "business_phone",
        "business_website": "business_website",
        "business_address": "business_address",
        "business_timezone": "business_timezone",
        "business_invoice_notes": "business_invoice_notes",
    }
    for src_key, dst_key in mapping.items():
        if src_key not in opts:
            continue
        conn.execute(
            """
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (dst_key, json.dumps(opts[src_key])),
        )


# Default time categories: (name, color, icon, billable 0/1, default_hourly_cents, sort_order).
# "Development" = hands-on / execution work (vs Evaluation = planning & notes).
STD_TIME_CATEGORIES: tuple[tuple[str, str, str, int, int | None, int], ...] = (
    ("Evaluation", "#E67700", "", 1, None, 0),
    ("Development", "#339af0", "", 1, None, 1),
    ("Break", "#868E96", "", 0, None, 2),
    ("Admin", "#7950F2", "", 0, None, 3),
    ("Research", "#1f6aa5", "", 1, None, 4),
    ("Marketing", "#fd7e14", "", 1, None, 5),
    ("Meetings", "#9775fa", "", 1, None, 6),
    ("Travel", "#15aabf", "", 1, None, 7),
    ("Sales", "#40c057", "", 1, None, 8),
    ("Customer support", "#4c6ef5", "", 1, None, 9),
    ("Operations", "#495057", "", 1, None, 10),
    ("Planning", "#e64980", "", 1, None, 11),
    ("Documentation", "#12b886", "", 1, None, 12),
    ("Finance", "#fab005", "", 1, None, 13),
    ("Training", "#cc5de8", "", 1, None, 14),
    ("Design", "#ff6b6b", "", 1, None, 15),
    ("Product", "#2f9e44", "", 1, None, 16),
)

# Shipped app_settings values (INSERT OR IGNORE on seed). Single source for Settings UI "factory" column.
FACTORY_APP_SETTINGS_DEFAULTS: dict[str, Any] = {
    "currency_default": "USD",
    "theme": "system",
    "prompt_interval_sec": 900,
    "prompt_first_delay_sec": 120,
    "show_money_in_dashboard": True,
    "currency_safe_summaries_enabled": False,
    "help_bubbles_enabled": True,
    "auto_post_scheduled_expenses_enabled": True,
    "auto_create_debt_for_credit_expenses_enabled": True,
    "show_process_status_banner_enabled": True,
    "notify_on_debt_settlement_enabled": True,
    "prompt_popup_topmost": False,
    "evaluation_label": "Evaluation until you log your first task.",
    "default_hourly_cents": 0,
    "prompt_no_response_action": "none",
    "prompt_no_response_timeout_sec": 45,
    "auto_backup_enabled": False,
    "auto_backup_interval_hours": 24,
    "last_backup_utc": "",
    "startup_clocked_in_prompt_enabled": True,
    "minimize_to_hidden_icons_enabled": False,
    "start_on_login_enabled": False,
    "multi_business_enabled": False,
    "active_business_id": 1,
    "last_app_closed_utc": "",
}

FACTORY_QUICK_ACTION_SEEDS: tuple[tuple[str, str], ...] = (
    ("Code", "Development work"),
    ("Review", "Code review and feedback"),
    ("Meeting", "Team/client meeting"),
)


def _seed_defaults(conn: sqlite3.Connection, user_id: int, *, include_starter_content: bool = True) -> None:
    # Defensive: ensure rr_users row exists before inserting FK-linked seed rows.
    now = now_utc_iso_text()
    conn.execute(
        """
        INSERT OR IGNORE INTO rr_users (telegram_user_id, username, first_name, created_at, updated_at)
        VALUES (?, NULL, NULL, ?, ?)
        """,
        (user_id, now, now),
    )
    n = conn.execute(
        "SELECT COUNT(*) FROM work_categories WHERE user_id = ? AND archived = 0",
        (user_id,),
    ).fetchone()[0]
    if include_starter_content and n == 0:
        for name, color, icon, billable, rate, so in STD_TIME_CATEGORIES:
            conn.execute(
                """
                INSERT OR IGNORE INTO work_categories
                  (user_id, name, color, icon, kind, billable, default_hourly_cents, sort_order, archived)
                VALUES (?, ?, ?, ?, 'time', ?, ?, ?, 0)
                """,
                (user_id, name, color, icon, billable, rate, so),
            )
    if include_starter_content:
        qn = conn.execute("SELECT COUNT(*) FROM quick_actions WHERE user_id = ?", (user_id,)).fetchone()[0]
        if qn == 0:
            for label, desc in FACTORY_QUICK_ACTION_SEEDS:
                conn.execute(
                    """
                    INSERT INTO quick_actions (user_id, label, work_category_id, project_id, default_description, sort_order)
                    VALUES (?, ?, NULL, NULL, ?, 999)
                    """,
                    (user_id, label, desc),
                )
    for key, val in FACTORY_APP_SETTINGS_DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
            (key, json.dumps(val)),
        )


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Income ledger table and indexes."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS income_entries (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          received_at_utc TEXT NOT NULL,
          amount_cents INTEGER NOT NULL,
          currency TEXT NOT NULL DEFAULT 'USD',
          description TEXT NOT NULL,
          work_category_id INTEGER,
          project_id INTEGER,
          source_type TEXT NOT NULL DEFAULT 'manual',
          source_ref_id INTEGER,
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
          FOREIGN KEY (work_category_id) REFERENCES work_categories (id) ON DELETE SET NULL,
          FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_income_user_time ON income_entries (user_id, received_at_utc);
        """
    )


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Clients, invoices, schedule, optional project→client link."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS clients (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          display_name TEXT NOT NULL,
          company TEXT,
          email TEXT,
          phone TEXT,
          address TEXT,
          website TEXT,
          tax_id TEXT,
          notes TEXT,
          sort_order INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_clients_user ON clients (user_id, sort_order);

        CREATE TABLE IF NOT EXISTS invoices (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          client_id INTEGER,
          invoice_number TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft','sent','paid','void')),
          issued_at_utc TEXT NOT NULL,
          due_at_utc TEXT,
          currency TEXT NOT NULL DEFAULT 'USD',
          subtotal_cents INTEGER NOT NULL DEFAULT 0,
          tax_cents INTEGER NOT NULL DEFAULT 0,
          total_cents INTEGER NOT NULL DEFAULT 0,
          notes TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
          FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE SET NULL,
          UNIQUE (user_id, invoice_number)
        );
        CREATE INDEX IF NOT EXISTS idx_invoices_user ON invoices (user_id, issued_at_utc);

        CREATE TABLE IF NOT EXISTS invoice_lines (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          invoice_id INTEGER NOT NULL,
          sort_order INTEGER NOT NULL DEFAULT 0,
          description TEXT NOT NULL,
          quantity REAL NOT NULL DEFAULT 1,
          unit_price_cents INTEGER NOT NULL DEFAULT 0,
          line_total_cents INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY (invoice_id) REFERENCES invoices (id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_invlines_inv ON invoice_lines (invoice_id, sort_order);

        CREATE TABLE IF NOT EXISTS schedule_events (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          starts_at_utc TEXT NOT NULL,
          ends_at_utc TEXT,
          all_day INTEGER NOT NULL DEFAULT 0,
          client_id INTEGER,
          project_id INTEGER,
          location TEXT,
          notes TEXT,
          status TEXT NOT NULL DEFAULT 'scheduled'
            CHECK (status IN ('scheduled','done','cancelled')),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
          FOREIGN KEY (client_id) REFERENCES clients (id) ON DELETE SET NULL,
          FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sched_user ON schedule_events (user_id, starts_at_utc);
        """
    )
    if not _has_column(conn, "projects", "client_id"):
        conn.execute("ALTER TABLE projects ADD COLUMN client_id INTEGER")


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """Sellable stock / products and internal supplies."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS stock_products (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          sku TEXT,
          description TEXT,
          unit TEXT NOT NULL DEFAULT 'ea',
          qty_on_hand REAL NOT NULL DEFAULT 0,
          reorder_level REAL NOT NULL DEFAULT 0,
          unit_cost_cents INTEGER,
          unit_price_cents INTEGER,
          currency TEXT NOT NULL DEFAULT 'USD',
          notes TEXT,
          sort_order INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_stock_products_user ON stock_products (user_id, archived, name);

        CREATE TABLE IF NOT EXISTS supplies (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          category TEXT,
          unit TEXT NOT NULL DEFAULT 'ea',
          qty_on_hand REAL NOT NULL DEFAULT 0,
          reorder_level REAL NOT NULL DEFAULT 0,
          vendor TEXT,
          notes TEXT,
          sort_order INTEGER NOT NULL DEFAULT 0,
          archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_supplies_user ON supplies (user_id, archived, name);
        """
    )


def _migrate_v5(conn: sqlite3.Connection) -> None:
    """Rename legacy Work category to Action; add standard business categories for each user."""
    for row in conn.execute(
        "SELECT DISTINCT user_id FROM work_categories WHERE name = 'Work' AND archived = 0"
    ):
        uid = int(row[0])
        if conn.execute(
            "SELECT 1 FROM work_categories WHERE user_id = ? AND name = 'Action' AND archived = 0",
            (uid,),
        ).fetchone():
            continue
        conn.execute(
            "UPDATE work_categories SET name = 'Action' WHERE user_id = ? AND name = 'Work' AND archived = 0",
            (uid,),
        )
    for uid_row in conn.execute("SELECT telegram_user_id FROM rr_users"):
        uid = int(uid_row[0])
        for name, color, icon, billable, rate, so in STD_TIME_CATEGORIES:
            conn.execute(
                """
                INSERT OR IGNORE INTO work_categories
                  (user_id, name, color, icon, kind, billable, default_hourly_cents, sort_order, archived)
                VALUES (?, ?, ?, ?, 'time', ?, ?, ?, 0)
                """,
                (uid, name, color, icon, billable, rate, so),
            )


def _migrate_v6(conn: sqlite3.Connection) -> None:
    """Evaluation is part of work and can be billable by default."""
    conn.execute(
        """
        UPDATE work_categories
        SET billable = 1
        WHERE archived = 0 AND LOWER(TRIM(name)) = 'evaluation'
        """
    )


def _migrate_v7(conn: sqlite3.Connection) -> None:
    """Audit trail for time edits and optional finalized (locked) report ranges."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS time_entry_audit (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          entry_id INTEGER,
          action TEXT NOT NULL,
          old_start_utc TEXT,
          old_end_utc TEXT,
          new_start_utc TEXT,
          new_end_utc TEXT,
          old_description TEXT,
          new_description TEXT,
          old_work_category_id INTEGER,
          new_work_category_id INTEGER,
          old_project_id INTEGER,
          new_project_id INTEGER,
          meta_json TEXT,
          changed_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_time_entry_audit_user_time ON time_entry_audit (user_id, changed_at DESC);

        CREATE TABLE IF NOT EXISTS finalized_ranges (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          start_utc TEXT NOT NULL,
          end_utc TEXT NOT NULL,
          notes TEXT,
          created_at TEXT NOT NULL,
          UNIQUE(user_id, start_utc, end_utc),
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_finalized_ranges_user ON finalized_ranges (user_id, start_utc, end_utc);
        """
    )


def _migrate_v8(conn: sqlite3.Connection) -> None:
    """Retire Action category in favor of Development for task defaults."""
    for uid_row in conn.execute("SELECT telegram_user_id FROM rr_users"):
        uid = int(uid_row[0])
        dev_row = conn.execute(
            "SELECT id FROM work_categories WHERE user_id = ? AND LOWER(TRIM(name)) = 'development' AND archived = 0",
            (uid,),
        ).fetchone()
        if dev_row:
            dev_id = int(dev_row[0])
        else:
            conn.execute(
                """
                INSERT INTO work_categories
                  (user_id, name, color, icon, kind, billable, default_hourly_cents, sort_order, archived)
                VALUES (?, 'Development', '#339af0', '', 'time', 1, NULL, 1, 0)
                """,
                (uid,),
            )
            dev_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        action_rows = conn.execute(
            "SELECT id FROM work_categories WHERE user_id = ? AND LOWER(TRIM(name)) = 'action'",
            (uid,),
        ).fetchall()
        for (action_id,) in action_rows:
            aid = int(action_id)
            conn.execute(
                "UPDATE rr_time_entries SET work_category_id = ? WHERE user_id = ? AND work_category_id = ?",
                (dev_id, uid, aid),
            )
            conn.execute(
                "UPDATE quick_actions SET work_category_id = ? WHERE user_id = ? AND work_category_id = ?",
                (dev_id, uid, aid),
            )
            conn.execute("UPDATE work_categories SET archived = 1 WHERE id = ?", (aid,))


def _migrate_v9(conn: sqlite3.Connection) -> None:
    """Plugin settings plus Power Monitoring snapshots/alerts."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rr_plugin_settings (
          plugin_id TEXT NOT NULL,
          key TEXT NOT NULL,
          value TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (plugin_id, key)
        );
        CREATE INDEX IF NOT EXISTS idx_plugin_settings_plugin ON rr_plugin_settings (plugin_id);

        CREATE TABLE IF NOT EXISTS rr_power_snapshots (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          provider TEXT NOT NULL,
          device_id TEXT,
          polled_at TEXT NOT NULL,
          battery_pct REAL,
          input_watts REAL,
          output_watts REAL,
          state_text TEXT,
          runtime_minutes REAL,
          poll_ok INTEGER NOT NULL DEFAULT 1,
          error_text TEXT,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_power_snapshots_user_time ON rr_power_snapshots (user_id, polled_at DESC);

        CREATE TABLE IF NOT EXISTS rr_power_alerts (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          snapshot_id INTEGER,
          alert_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          message TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE,
          FOREIGN KEY (snapshot_id) REFERENCES rr_power_snapshots (id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_power_alerts_user_time ON rr_power_alerts (user_id, created_at DESC);
        """
    )


def _migrate_v10(conn: sqlite3.Connection) -> None:
    """USGS earthquake plugin storage for events and alerts."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS usgs_quake_events (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          event_id TEXT NOT NULL UNIQUE,
          event_time_utc TEXT NOT NULL,
          magnitude REAL,
          place TEXT NOT NULL,
          latitude REAL,
          longitude REAL,
          depth_km REAL,
          detail_url TEXT,
          raw_json TEXT NOT NULL,
          received_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_usgs_events_time ON usgs_quake_events (event_time_utc DESC);

        CREATE TABLE IF NOT EXISTS usgs_quake_alerts (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          event_id TEXT NOT NULL UNIQUE,
          alerted_at TEXT NOT NULL,
          distance_miles REAL NOT NULL,
          rule_snapshot_json TEXT NOT NULL,
          acknowledged INTEGER NOT NULL DEFAULT 0,
          FOREIGN KEY (event_id) REFERENCES usgs_quake_events (event_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_usgs_alerts_time ON usgs_quake_alerts (alerted_at DESC);
        """
    )


def _migrate_v11(conn: sqlite3.Connection) -> None:
    """EcoFlow power bucket aggregation and AC/DC input split."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rr_power_buckets (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          provider TEXT NOT NULL,
          device_id TEXT NOT NULL DEFAULT '',
          bucket_start_utc TEXT NOT NULL,
          bucket_end_utc TEXT NOT NULL,
          avg_battery_pct REAL,
          avg_ac_input_watts REAL,
          avg_dc_input_watts REAL,
          avg_output_watts REAL,
          sample_count INTEGER NOT NULL DEFAULT 0,
          UNIQUE(user_id, provider, device_id, bucket_start_utc),
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_power_buckets_user_time
          ON rr_power_buckets (user_id, bucket_start_utc DESC);
        """
    )
    if not _has_column(conn, "rr_power_snapshots", "ac_input_watts"):
        conn.execute("ALTER TABLE rr_power_snapshots ADD COLUMN ac_input_watts REAL")
    if not _has_column(conn, "rr_power_snapshots", "dc_input_watts"):
        conn.execute("ALTER TABLE rr_power_snapshots ADD COLUMN dc_input_watts REAL")


def _migrate_v12(conn: sqlite3.Connection) -> None:
    """Optional multi-business support: profiles + business_id scoping columns."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS business_profiles (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          legal_name TEXT,
          owner TEXT,
          tax_id TEXT,
          email TEXT,
          phone TEXT,
          website TEXT,
          address TEXT,
          timezone TEXT,
          invoice_notes TEXT,
          archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          UNIQUE(user_id, name),
          FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_business_profiles_user ON business_profiles (user_id, archived, name);
        """
    )

    tables = (
        "rr_time_entries",
        "income_entries",
        "expense_entries",
        "clients",
        "invoices",
        "projects",
        "schedule_events",
        "stock_products",
        "supplies",
        "quick_actions",
    )
    for t in tables:
        if not _has_column(conn, t, "business_id"):
            conn.execute(f"ALTER TABLE {t} ADD COLUMN business_id INTEGER")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{t}_user_business ON {t} (user_id, business_id)")

    now = now_utc_iso_text()
    for uid_row in conn.execute("SELECT telegram_user_id FROM rr_users"):
        uid = int(uid_row[0])
        # Seed default profile from current singleton business settings.
        name = "Default Business"
        row_name = conn.execute(
            "SELECT value FROM app_settings WHERE key = 'business_name'"
        ).fetchone()
        if row_name:
            try:
                v = json.loads(row_name[0])
                if str(v or "").strip():
                    name = str(v).strip()
            except Exception:
                pass
        conn.execute(
            """
            INSERT OR IGNORE INTO business_profiles
              (id, user_id, name, created_at, updated_at)
            VALUES (1, ?, ?, ?, ?)
            """,
            (uid, name, now, now),
        )
        for t in tables:
            conn.execute(
                f"UPDATE {t} SET business_id = 1 WHERE user_id = ? AND (business_id IS NULL OR business_id = 0)",
                (uid,),
            )

    conn.execute(
        """
        INSERT OR IGNORE INTO app_settings (key, value) VALUES ('multi_business_enabled', ?)
        """,
        (json.dumps(False),),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_business_id', ?)
        """,
        (json.dumps(1),),
    )


def _migrate_v13(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS debt_entries (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          business_id INTEGER,
          created_at_utc TEXT NOT NULL,
          due_at_utc TEXT,
          amount_cents INTEGER NOT NULL,
          currency TEXT NOT NULL DEFAULT 'USD',
          creditor TEXT,
          description TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE INDEX IF NOT EXISTS idx_debt_entries_user_time ON debt_entries (user_id, created_at_utc DESC);
        CREATE INDEX IF NOT EXISTS idx_debt_entries_user_business ON debt_entries (user_id, business_id, status);
        """
    )
    conn.execute("UPDATE debt_entries SET business_id = 1 WHERE business_id IS NULL OR business_id = 0")


def _migrate_v14(conn: sqlite3.Connection) -> None:
    if not _has_column(conn, "debt_entries", "debt_type"):
        conn.execute("ALTER TABLE debt_entries ADD COLUMN debt_type TEXT NOT NULL DEFAULT 'loan'")
    if not _has_column(conn, "debt_entries", "account_ref"):
        conn.execute("ALTER TABLE debt_entries ADD COLUMN account_ref TEXT NOT NULL DEFAULT ''")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scheduled_expenses (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          business_id INTEGER,
          description TEXT NOT NULL,
          amount_cents INTEGER NOT NULL,
          currency TEXT NOT NULL DEFAULT 'USD',
          frequency TEXT NOT NULL DEFAULT 'monthly',
          next_due_utc TEXT NOT NULL,
          project_id INTEGER,
          work_category_id INTEGER,
          merchant TEXT,
          billable INTEGER NOT NULL DEFAULT 1,
          notes TEXT,
          active INTEGER NOT NULL DEFAULT 1,
          last_run_utc TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sched_exp_user_due ON scheduled_expenses (user_id, business_id, active, next_due_utc);
        """
    )
    conn.execute(
        "UPDATE scheduled_expenses SET business_id = 1 WHERE business_id IS NULL OR business_id = 0"
    )


def _migrate_v15(conn: sqlite3.Connection) -> None:
    if not _has_column(conn, "expense_entries", "funding_source"):
        conn.execute("ALTER TABLE expense_entries ADD COLUMN funding_source TEXT NOT NULL DEFAULT 'cash'")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS resource_entries (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          business_id INTEGER,
          at_utc TEXT NOT NULL,
          amount_cents INTEGER NOT NULL,
          currency TEXT NOT NULL DEFAULT 'USD',
          source_type TEXT NOT NULL DEFAULT 'owner_contribution',
          description TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_resource_entries_user_time ON resource_entries (user_id, at_utc DESC);
        CREATE INDEX IF NOT EXISTS idx_resource_entries_user_business ON resource_entries (user_id, business_id);
        """
    )
    conn.execute("UPDATE resource_entries SET business_id = 1 WHERE business_id IS NULL OR business_id = 0")


def _migrate_v16(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS available_funds_accounts (
          id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          business_id INTEGER,
          account_name TEXT NOT NULL,
          account_type TEXT NOT NULL DEFAULT 'cash',
          currency TEXT NOT NULL DEFAULT 'USD',
          current_balance_cents INTEGER NOT NULL DEFAULT 0,
          credit_limit_cents INTEGER NOT NULL DEFAULT 0,
          notes TEXT NOT NULL DEFAULT '',
          archived INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_avail_funds_user_business
          ON available_funds_accounts (user_id, business_id, archived, account_type);
        CREATE INDEX IF NOT EXISTS idx_avail_funds_user_name
          ON available_funds_accounts (user_id, account_name);
        """
    )
    conn.execute(
        "UPDATE available_funds_accounts SET business_id = 1 WHERE business_id IS NULL OR business_id = 0"
    )


def run_migrations(cfg: "DbConfig", *, local_user_id: int = 1) -> None:
    install_opts = _read_install_options()
    conn = connect(cfg)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
              version INTEGER NOT NULL PRIMARY KEY,
              applied_at TEXT NOT NULL
            )
            """
        )
        done = {
            int(r[0])
            for r in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        if 1 not in done:
            _migrate_v1(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
                (now_utc_iso_text(),),
            )
        if 2 not in done:
            _migrate_v2(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (2, ?)",
                (now_utc_iso_text(),),
            )
        if 3 not in done:
            _migrate_v3(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (3, ?)",
                (now_utc_iso_text(),),
            )
        if 4 not in done:
            _migrate_v4(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (4, ?)",
                (now_utc_iso_text(),),
            )
        if 5 not in done:
            _migrate_v5(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (5, ?)",
                (now_utc_iso_text(),),
            )
        if 6 not in done:
            _migrate_v6(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (6, ?)",
                (now_utc_iso_text(),),
            )
        if 7 not in done:
            _migrate_v7(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (7, ?)",
                (now_utc_iso_text(),),
            )
        if 8 not in done:
            _migrate_v8(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (8, ?)",
                (now_utc_iso_text(),),
            )
        if 9 not in done:
            _migrate_v9(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (9, ?)",
                (now_utc_iso_text(),),
            )
        if 10 not in done:
            _migrate_v10(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (10, ?)",
                (now_utc_iso_text(),),
            )
        if 11 not in done:
            _migrate_v11(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (11, ?)",
                (now_utc_iso_text(),),
            )
        if 12 not in done:
            _migrate_v12(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (12, ?)",
                (now_utc_iso_text(),),
            )
        if 13 not in done:
            _migrate_v13(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (13, ?)",
                (now_utc_iso_text(),),
            )
        if 14 not in done:
            _migrate_v14(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (14, ?)",
                (now_utc_iso_text(),),
            )
        if 15 not in done:
            _migrate_v15(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (15, ?)",
                (now_utc_iso_text(),),
            )
        if 16 not in done:
            _migrate_v16(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (16, ?)",
                (now_utc_iso_text(),),
            )
        conn.commit()

        conn = connect(cfg)
        _seed_defaults(conn, local_user_id, include_starter_content=_read_starter_seed_preference(install_opts))
        _apply_install_options(conn, install_opts)
        conn.commit()
    finally:
        conn.close()
