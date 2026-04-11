-- Reference schema for RootRecord (SQLite).
-- Runtime DDL lives in db.py (ensure_schema); keep in sync when changing tables.

PRAGMA foreign_keys = ON;

-- Registered Telegram users
CREATE TABLE IF NOT EXISTS rr_users (
  telegram_user_id INTEGER NOT NULL PRIMARY KEY,
  username TEXT,
  first_name TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- Bot / workstation sessions
CREATE TABLE IF NOT EXISTS rr_machine_sessions (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  started_at_utc TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Completed time blocks
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
);

-- Audit events
CREATE TABLE IF NOT EXISTS rr_session_events (
  id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  detail TEXT,
  created_at_utc TEXT NOT NULL,
  FOREIGN KEY (user_id) REFERENCES rr_users (telegram_user_id) ON DELETE CASCADE
);
