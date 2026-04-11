-- Entitlements + 14-day trial (Stripe wired later via subscription_status / stripe_customer_id)

PRAGMA foreign_keys = ON;

CREATE TABLE accounts (
  id TEXT PRIMARY KEY NOT NULL,
  email TEXT NOT NULL UNIQUE,
  trial_started_at INTEGER,
  trial_ends_at INTEGER,
  subscription_status TEXT NOT NULL DEFAULT 'none',
  stripe_customer_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE devices (
  device_id TEXT PRIMARY KEY NOT NULL,
  account_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE INDEX idx_devices_account_id ON devices(account_id);
