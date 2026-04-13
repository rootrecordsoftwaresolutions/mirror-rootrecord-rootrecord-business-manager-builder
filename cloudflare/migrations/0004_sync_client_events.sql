-- Opaque activity blobs from desktop offline outbox (multi-device / replay later)

PRAGMA foreign_keys = ON;

CREATE TABLE sync_client_events (
  id TEXT PRIMARY KEY NOT NULL,
  account_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  client_mutation_id TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_key TEXT NOT NULL,
  op TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  UNIQUE (account_id, client_mutation_id)
);

CREATE INDEX idx_sync_client_events_account_time ON sync_client_events (account_id, created_at);
