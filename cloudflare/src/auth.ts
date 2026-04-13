/**
 * Email + password signup/login, opaque sessions (SHA-256 at rest),
 * and legacy "claim password" for accounts created without a password.
 */

import {
  buildEntitlementPayload,
  isValidDeviceId,
  isValidEmail,
  loadAccount,
  normalizeEmail,
  resolveEntitlementSessionScoped,
  type AccountRow,
  type EntitlementPayload,
} from "./entitlement";

/** Cloudflare Workers reject PBKDF2 iterationCount > 100_000 (`NotSupportedError`). */
const PBKDF2_ITER = 100_000;
const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;
const MIN_PASSWORD_LEN = 10;
const MAX_PASSWORD_LEN = 256;

function bytesToHex(u: Uint8Array): string {
  return [...u].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function hexToBytes(hex: string): Uint8Array | null {
  if (!/^[0-9a-fA-F]+$/.test(hex) || hex.length % 2 !== 0) return null;
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return out;
}

function timingSafeEqualHex(a: string, b: string): boolean {
  const al = a.length;
  const bl = b.length;
  if (al !== bl) return false;
  let out = 0;
  for (let i = 0; i < al; i++) {
    out |= a.charCodeAt(i)! ^ b.charCodeAt(i)!;
  }
  return out === 0;
}

async function sha256Hex(s: string): Promise<string> {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
  return bytesToHex(new Uint8Array(buf));
}

async function derivePasswordHash(password: string, salt: Uint8Array, iterations: number): Promise<Uint8Array> {
  const enc = new TextEncoder();
  const keyMaterial = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits(
    { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
    keyMaterial,
    256
  );
  return new Uint8Array(bits);
}

export async function hashPasswordCredential(password: string): Promise<string> {
  const salt = new Uint8Array(16);
  crypto.getRandomValues(salt);
  const hash = await derivePasswordHash(password, salt, PBKDF2_ITER);
  return `v1$${PBKDF2_ITER}$${bytesToHex(salt)}$${bytesToHex(hash)}`;
}

export async function verifyPasswordCredential(password: string, stored: string | null): Promise<boolean> {
  if (!stored) return false;
  const parts = stored.split("$");
  if (parts.length !== 4 || parts[0] !== "v1") return false;
  const iter = parseInt(parts[1]!, 10);
  const salt = hexToBytes(parts[2]!);
  const expectHex = parts[3]!.toLowerCase();
  if (!Number.isFinite(iter) || iter !== PBKDF2_ITER || !salt || salt.length < 8) return false;
  const hash = await derivePasswordHash(password, salt, iter);
  return timingSafeEqualHex(bytesToHex(hash).toLowerCase(), expectHex);
}

function validatePasswordPlain(pw: string): { ok: true } | { error: { code: string; message: string } } {
  if (pw.length < MIN_PASSWORD_LEN) {
    return {
      error: {
        code: "WEAK_PASSWORD",
        message: `Password must be at least ${MIN_PASSWORD_LEN} characters.`,
      },
    };
  }
  if (pw.length > MAX_PASSWORD_LEN) {
    return { error: { code: "WEAK_PASSWORD", message: "Password is too long." } };
  }
  return { ok: true };
}

export async function createSession(db: D1Database, accountId: string): Promise<{ rawToken: string; expiresAt: number }> {
  const rawBytes = new Uint8Array(32);
  crypto.getRandomValues(rawBytes);
  let bin = "";
  for (let i = 0; i < rawBytes.length; i++) {
    bin += String.fromCharCode(rawBytes[i]!);
  }
  const rawToken = btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const tokenHash = await sha256Hex(rawToken);
  const now = Date.now();
  const expiresAt = now + SESSION_TTL_MS;
  const id = crypto.randomUUID();
  await db
    .prepare(`INSERT INTO sessions (id, account_id, token_hash, expires_at, created_at) VALUES (?, ?, ?, ?, ?)`)
    .bind(id, accountId, tokenHash, expiresAt, now)
    .run();
  return { rawToken, expiresAt };
}

export async function resolveSessionAccountId(db: D1Database, rawToken: string): Promise<string | null> {
  const tokenHash = await sha256Hex(rawToken);
  const now = Date.now();
  const row = await db
    .prepare(`SELECT account_id FROM sessions WHERE token_hash = ? AND expires_at > ?`)
    .bind(tokenHash, now)
    .first<{ account_id: string }>();
  return row?.account_id ?? null;
}

export async function deleteSessionByRawToken(db: D1Database, rawToken: string): Promise<void> {
  const tokenHash = await sha256Hex(rawToken);
  await db.prepare(`DELETE FROM sessions WHERE token_hash = ?`).bind(tokenHash).run();
}

export interface AuthEmailDeviceBody {
  email: string;
  password: string;
  device_id: string;
}

function parseEmailPasswordDevice(body: unknown): AuthEmailDeviceBody | { error: { code: string; message: string } } {
  if (!body || typeof body !== "object") {
    return { error: { code: "INVALID_BODY", message: "Expected JSON object" } };
  }
  const o = body as Record<string, unknown>;
  const email = typeof o.email === "string" ? normalizeEmail(o.email) : "";
  const password = typeof o.password === "string" ? o.password : "";
  const device_id = typeof o.device_id === "string" ? o.device_id.trim() : "";
  if (!email || !isValidEmail(email)) {
    return { error: { code: "INVALID_EMAIL", message: "Valid email is required" } };
  }
  if (!isValidDeviceId(device_id)) {
    return {
      error: {
        code: "INVALID_DEVICE_ID",
        message: "device_id must be 8–128 visible ASCII characters (no spaces)",
      },
    };
  }
  if (!password) {
    return { error: { code: "INVALID_PASSWORD", message: "Password is required." } };
  }
  return { email, password, device_id };
}

const TRIAL_MS = 14 * 24 * 60 * 60 * 1000;

function mergeAuthResponse(
  accessToken: string,
  expiresAt: number,
  payload: EntitlementPayload
): Record<string, unknown> {
  return {
    access_token: accessToken,
    token_type: "Bearer",
    expires_at: new Date(expiresAt).toISOString(),
    ...payload,
  };
}

export async function authSignup(db: D1Database, body: unknown): Promise<{ ok: Record<string, unknown> } | { status: number; error: unknown }> {
  const parsed = parseEmailPasswordDevice(body);
  if ("error" in parsed) {
    return { status: 400, error: { error: parsed.error } };
  }
  const { email, password, device_id } = parsed;
  const pwCheck = validatePasswordPlain(password);
  if ("error" in pwCheck) {
    return { status: 400, error: { error: pwCheck.error } };
  }

  const existing = await db.prepare(`SELECT id FROM accounts WHERE email = ?`).bind(email).first<{ id: string }>();
  if (existing) {
    return {
      status: 409,
      error: { error: { code: "EMAIL_TAKEN", message: "An account with this email already exists. Try signing in." } },
    };
  }

  const deviceRow = await db
    .prepare(`SELECT device_id, account_id FROM devices WHERE device_id = ?`)
    .bind(device_id)
    .first<{ device_id: string; account_id: string }>();
  if (deviceRow) {
    return {
      status: 409,
      error: {
        error: {
          code: "DEVICE_TAKEN",
          message: "This device is already registered. Sign in with the account that owns it.",
        },
      },
    };
  }

  const cred = await hashPasswordCredential(password);
  const now = Date.now();
  const trialEnd = now + TRIAL_MS;
  const id = crypto.randomUUID();
  await db
    .prepare(
      `INSERT INTO accounts (id, email, trial_started_at, trial_ends_at, subscription_status, stripe_customer_id, stripe_subscription_id, password_credential, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'none', NULL, NULL, ?, ?, ?)`
    )
    .bind(id, email, now, trialEnd, cred, now, now)
    .run();

  await db
    .prepare(`INSERT INTO devices (device_id, account_id, created_at) VALUES (?, ?, ?)`)
    .bind(device_id, id, now)
    .run();

  const acc = await loadAccount(db, id);
  if (!acc) {
    return { status: 500, error: { error: { code: "INTERNAL", message: "Account creation failed" } } };
  }
  const { rawToken, expiresAt } = await createSession(db, id);
  return { ok: mergeAuthResponse(rawToken, expiresAt, buildEntitlementPayload(acc, now)) };
}

export async function authLogin(db: D1Database, body: unknown): Promise<{ ok: Record<string, unknown> } | { status: number; error: unknown }> {
  const parsed = parseEmailPasswordDevice(body);
  if ("error" in parsed) {
    return { status: 400, error: { error: parsed.error } };
  }
  const { email, password, device_id } = parsed;

  const acc = await db.prepare(`SELECT * FROM accounts WHERE email = ?`).bind(email).first<AccountRow>();
  if (!acc) {
    return {
      status: 401,
      error: { error: { code: "INVALID_CREDENTIALS", message: "Incorrect email or password." } },
    };
  }
  if (!acc.password_credential) {
    return {
      status: 403,
      error: {
        error: {
          code: "PASSWORD_NOT_SET",
          message:
            'This account has no password yet. Use "Set password on this device" once, or continue with your existing license setup.',
        },
      },
    };
  }
  const okPw = await verifyPasswordCredential(password, acc.password_credential);
  if (!okPw) {
    return {
      status: 401,
      error: { error: { code: "INVALID_CREDENTIALS", message: "Incorrect email or password." } },
    };
  }

  const now = Date.now();
  const res = await resolveEntitlementSessionScoped(db, acc.id, email, device_id);
  if (res.kind === "conflict") {
    return { status: 409, error: { error: { code: "DEVICE_CONFLICT", message: res.message } } };
  }
  const { rawToken, expiresAt } = await createSession(db, acc.id);
  return { ok: mergeAuthResponse(rawToken, expiresAt, res.payload) };
}

/** Legacy: account exists without password; device already linked — set password and return session. */
export async function authClaimPassword(db: D1Database, body: unknown): Promise<{ ok: Record<string, unknown> } | { status: number; error: unknown }> {
  const parsed = parseEmailPasswordDevice(body);
  if ("error" in parsed) {
    return { status: 400, error: { error: parsed.error } };
  }
  const { email, password, device_id } = parsed;
  const pwCheck = validatePasswordPlain(password);
  if ("error" in pwCheck) {
    return { status: 400, error: { error: pwCheck.error } };
  }

  const acc = await db.prepare(`SELECT * FROM accounts WHERE email = ?`).bind(email).first<AccountRow>();
  if (!acc) {
    return { status: 404, error: { error: { code: "NOT_FOUND", message: "No account for this email." } } };
  }
  if (acc.password_credential) {
    return {
      status: 400,
      error: { error: { code: "ALREADY_CLAIMED", message: "This account already has a password. Sign in instead." } },
    };
  }

  const link = await db
    .prepare(`SELECT 1 AS ok FROM devices WHERE device_id = ? AND account_id = ?`)
    .bind(device_id, acc.id)
    .first<{ ok: number }>();
  if (!link) {
    return {
      status: 403,
      error: {
        error: {
          code: "DEVICE_NOT_LINKED",
          message: "This device is not registered to that email yet. Start the app once with your license email, then set a password.",
        },
      },
    };
  }

  const cred = await hashPasswordCredential(password);
  const now = Date.now();
  await db
    .prepare(`UPDATE accounts SET password_credential = ?, updated_at = ? WHERE id = ?`)
    .bind(cred, now, acc.id)
    .run();

  const fresh = await loadAccount(db, acc.id);
  if (!fresh) {
    return { status: 500, error: { error: { code: "INTERNAL", message: "Update failed" } } };
  }
  const { rawToken, expiresAt } = await createSession(db, acc.id);
  return { ok: mergeAuthResponse(rawToken, expiresAt, buildEntitlementPayload(fresh, now)) };
}
