/**
 * Trial / device / entitlement resolution (D1).
 *
 * Rules: New accounts get 14 days from first trial anchor (trial_started_at or created_at).
 * Full access while `now < trial_ends_at` (or derived end) and subscription is not active/past_due.
 * `subscription_status === active` → full access. `past_due` → read-only. Otherwise trial vs expired.
 */

const TRIAL_DAYS = 14;
const TRIAL_MS = TRIAL_DAYS * 24 * 60 * 60 * 1000;
/** Client may cache entitlement until this instant (offline grace handled client-side). */
export const DEFAULT_VALID_UNTIL_HOURS = 48;

export interface EntitlementPayload {
  account_id: string;
  access: "full" | "read_only";
  reason: "trialing" | "trial_expired" | "paid" | "past_due";
  trial_started_at: string | null;
  trial_ends_at: string | null;
  valid_until: string;
  subscription_status: string;
}

export type ResolveResult =
  | { kind: "ok"; payload: EntitlementPayload }
  | { kind: "conflict"; message: string };

export interface AccountRow {
  id: string;
  email: string;
  trial_started_at: number | null;
  trial_ends_at: number | null;
  subscription_status: string;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  password_credential: string | null;
  created_at: number;
  updated_at: number;
}

export function normalizeEmail(raw: string): string {
  return raw.trim().toLowerCase();
}

export function isValidEmail(email: string): boolean {
  if (email.length > 254) return false;
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

export function isValidDeviceId(id: string): boolean {
  if (id.length < 8 || id.length > 128) return false;
  for (let i = 0; i < id.length; i++) {
    const c = id.charCodeAt(i);
    if (c < 33 || c > 126) return false;
  }
  return true;
}

export async function loadAccount(db: D1Database, id: string): Promise<AccountRow | null> {
  return await db.prepare(`SELECT * FROM accounts WHERE id = ?`).bind(id).first<AccountRow>();
}

export function buildEntitlementPayload(account: AccountRow, now: number): EntitlementPayload {
  const sub = account.subscription_status || "none";
  let access: "full" | "read_only" = "read_only";
  let reason: EntitlementPayload["reason"] = "trial_expired";

  if (sub === "active") {
    access = "full";
    reason = "paid";
  } else if (sub === "past_due") {
    access = "read_only";
    reason = "past_due";
  } else {
    const trialAnchorStart = account.trial_started_at ?? account.created_at;
    let effectiveTrialEnd = account.trial_ends_at;
    if (effectiveTrialEnd == null && trialAnchorStart != null) {
      effectiveTrialEnd = trialAnchorStart + TRIAL_MS;
    }
    if (effectiveTrialEnd != null && now < effectiveTrialEnd) {
      access = "full";
      reason = "trialing";
    } else {
      access = "read_only";
      reason = "trial_expired";
    }
  }

  let trialStartIso: string | null =
    account.trial_started_at != null ? new Date(account.trial_started_at).toISOString() : null;
  let trialEndIso: string | null =
    account.trial_ends_at != null ? new Date(account.trial_ends_at).toISOString() : null;

  if (reason === "trialing" || reason === "trial_expired") {
    const startMs = account.trial_started_at ?? account.created_at;
    let endMs = account.trial_ends_at;
    if (endMs == null && startMs != null) {
      endMs = startMs + TRIAL_MS;
    }
    trialStartIso = startMs != null ? new Date(startMs).toISOString() : null;
    trialEndIso = endMs != null ? new Date(endMs).toISOString() : null;
  }

  const validUntil = new Date(now + DEFAULT_VALID_UNTIL_HOURS * 60 * 60 * 1000).toISOString();

  return {
    account_id: account.id,
    access,
    reason,
    trial_started_at: trialStartIso,
    trial_ends_at: trialEndIso,
    valid_until: validUntil,
    subscription_status: sub,
  };
}

/**
 * Legacy / machine auth: match email+device, auto-create trial account when needed.
 */
export async function resolveEntitlementOpen(db: D1Database, email: string, deviceId: string): Promise<ResolveResult> {
  const now = Date.now();

  const deviceRow = await db
    .prepare(`SELECT device_id, account_id, created_at FROM devices WHERE device_id = ?`)
    .bind(deviceId)
    .first<{ device_id: string; account_id: string; created_at: number }>();

  const accountByEmail = await db
    .prepare(`SELECT * FROM accounts WHERE email = ?`)
    .bind(email)
    .first<AccountRow>();

  if (deviceRow) {
    const acc = await loadAccount(db, deviceRow.account_id);
    if (!acc) {
      throw new Error("Device references missing account");
    }
    if (acc.email !== email) {
      return {
        kind: "conflict",
        message:
          "This device is already registered to a different email. Use the original email or contact support.",
      };
    }
    return { kind: "ok", payload: buildEntitlementPayload(acc, now) };
  }

  if (accountByEmail) {
    await db
      .prepare(`INSERT INTO devices (device_id, account_id, created_at) VALUES (?, ?, ?)`)
      .bind(deviceId, accountByEmail.id, now)
      .run();
    return { kind: "ok", payload: buildEntitlementPayload(accountByEmail, now) };
  }

  const id = crypto.randomUUID();
  const trialEnd = now + TRIAL_MS;
  await db
    .prepare(
      `INSERT INTO accounts (id, email, trial_started_at, trial_ends_at, subscription_status, stripe_customer_id, stripe_subscription_id, password_credential, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'none', NULL, NULL, NULL, ?, ?)`
    )
    .bind(id, email, now, trialEnd, now, now)
    .run();

  await db
    .prepare(`INSERT INTO devices (device_id, account_id, created_at) VALUES (?, ?, ?)`)
    .bind(deviceId, id, now)
    .run();

  const created = await loadAccount(db, id);
  if (!created) throw new Error("Failed to load new account");
  return { kind: "ok", payload: buildEntitlementPayload(created, now) };
}

/**
 * User session: never create a new account; only link device to the signed-in account when allowed.
 */
export async function resolveEntitlementSessionScoped(
  db: D1Database,
  sessionAccountId: string,
  email: string,
  deviceId: string
): Promise<ResolveResult> {
  const now = Date.now();
  const acc = await loadAccount(db, sessionAccountId);
  if (!acc) {
    return { kind: "conflict", message: "Session is invalid. Sign in again." };
  }
  if (acc.email !== email) {
    return {
      kind: "conflict",
      message: "Email does not match this session. Use the email you signed in with.",
    };
  }

  const deviceRow = await db
    .prepare(`SELECT device_id, account_id, created_at FROM devices WHERE device_id = ?`)
    .bind(deviceId)
    .first<{ device_id: string; account_id: string; created_at: number }>();

  if (deviceRow) {
    if (deviceRow.account_id !== sessionAccountId) {
      return {
        kind: "conflict",
        message:
          "This device is already registered to a different account. Use the original account or contact support.",
      };
    }
    return { kind: "ok", payload: buildEntitlementPayload(acc, now) };
  }

  await db
    .prepare(`INSERT INTO devices (device_id, account_id, created_at) VALUES (?, ?, ?)`)
    .bind(deviceId, sessionAccountId, now)
    .run();
  return { kind: "ok", payload: buildEntitlementPayload(acc, now) };
}
