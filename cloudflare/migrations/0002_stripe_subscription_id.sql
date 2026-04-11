-- Optional Stripe subscription id for webhook idempotency / support

PRAGMA foreign_keys = ON;

ALTER TABLE accounts ADD COLUMN stripe_subscription_id TEXT;
