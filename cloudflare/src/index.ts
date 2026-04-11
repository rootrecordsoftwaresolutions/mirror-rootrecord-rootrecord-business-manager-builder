export interface Env {
  DB: D1Database;
  /** If set, clients must send Authorization: Bearer <LICENSE_API_SECRET> */
  LICENSE_API_SECRET?: string;
}

const TRIAL_DAYS = 14;
const TRIAL_MS = TRIAL_DAYS * 24 * 60 * 60 * 1000;
/** Client may cache entitlement until this instant (offline grace handled client-side). */
const DEFAULT_VALID_UNTIL_HOURS = 48;

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (request.method === "OPTIONS") {
      return corsPreflight();
    }

    if (request.method === "GET" && url.pathname === "/health") {
      return json({ ok: true, service: "rootrecord-license" });
    }

    if (request.method === "POST" && url.pathname === "/v1/entitlement") {
      const authErr = requireApiSecret(request, env);
      if (authErr) return withCors(authErr);

      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return withCors(json({ error: { code: "BAD_JSON", message: "Body must be JSON" } }, 400));
      }

      const parsed = parseEntitlementBody(body);
      if ("error" in parsed) {
        return withCors(json({ error: parsed.error }, 400));
      }

      try {
        const result = await resolveEntitlement(env.DB, parsed.email, parsed.device_id);
        if (result.kind === "conflict") {
          return withCors(
            json({ error: { code: "EMAIL_DEVICE_MISMATCH", message: result.message } }, 409)
          );
        }
        return withCors(json(result.payload));
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return withCors(json({ error: { code: "INTERNAL", message: msg } }, 500));
      }
    }

    return withCors(json({ error: { code: "NOT_FOUND", message: "Not found" } }, 404));
  },
};

interface EntitlementPayload {
  account_id: string;
  access: "full" | "read_only";
  reason: "trialing" | "trial_expired" | "paid";
  trial_ends_at: string | null;
  valid_until: string;
  subscription_status: string;
}

type ResolveResult =
  | { kind: "ok"; payload: EntitlementPayload }
  | { kind: "conflict"; message: string };

interface AccountRow {
  id: string;
  email: string;
  trial_started_at: number | null;
  trial_ends_at: number | null;
  subscription_status: string;
  stripe_customer_id: string | null;
  created_at: number;
  updated_at: number;
}

function parseEntitlementBody(body: unknown):
  | { email: string; device_id: string }
  | { error: { code: string; message: string } } {
  if (!body || typeof body !== "object") {
    return { error: { code: "INVALID_BODY", message: "Expected JSON object" } };
  }
  const o = body as Record<string, unknown>;
  const email = typeof o.email === "string" ? normalizeEmail(o.email) : "";
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
  return { email, device_id };
}

function normalizeEmail(raw: string): string {
  return raw.trim().toLowerCase();
}

function isValidEmail(email: string): boolean {
  if (email.length > 254) return false;
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function isValidDeviceId(id: string): boolean {
  if (id.length < 8 || id.length > 128) return false;
  for (let i = 0; i < id.length; i++) {
    const c = id.charCodeAt(i);
    if (c < 33 || c > 126) return false;
  }
  return true;
}

async function resolveEntitlement(db: D1Database, email: string, deviceId: string): Promise<ResolveResult> {
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
      `INSERT INTO accounts (id, email, trial_started_at, trial_ends_at, subscription_status, stripe_customer_id, created_at, updated_at)
       VALUES (?, ?, ?, ?, 'none', NULL, ?, ?)`
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

async function loadAccount(db: D1Database, id: string): Promise<AccountRow | null> {
  return await db.prepare(`SELECT * FROM accounts WHERE id = ?`).bind(id).first<AccountRow>();
}

function buildEntitlementPayload(account: AccountRow, now: number): EntitlementPayload {
  const sub = account.subscription_status || "none";
  let access: "full" | "read_only" = "read_only";
  let reason: "trialing" | "trial_expired" | "paid" = "trial_expired";

  if (sub === "active") {
    access = "full";
    reason = "paid";
  } else if (account.trial_ends_at != null && now < account.trial_ends_at) {
    access = "full";
    reason = "trialing";
  } else {
    access = "read_only";
    reason = "trial_expired";
  }

  const trialIso = account.trial_ends_at != null ? new Date(account.trial_ends_at).toISOString() : null;
  const validUntil = new Date(now + DEFAULT_VALID_UNTIL_HOURS * 60 * 60 * 1000).toISOString();

  return {
    account_id: account.id,
    access,
    reason,
    trial_ends_at: trialIso,
    valid_until: validUntil,
    subscription_status: sub,
  };
}

function requireApiSecret(request: Request, env: Env): Response | null {
  const secret = env.LICENSE_API_SECRET?.trim();
  if (!secret) {
    return null;
  }
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!safeEqual(token, secret)) {
    return json({ error: { code: "UNAUTHORIZED", message: "Invalid or missing Authorization Bearer token" } }, 401);
  }
  return null;
}

function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  const enc = new TextEncoder();
  const ba = enc.encode(a);
  const bb = enc.encode(b);
  if (ba.length !== bb.length) return false;
  let out = 0;
  for (let i = 0; i < ba.length; i++) {
    out |= ba[i]! ^ bb[i]!;
  }
  return out === 0;
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
    },
  });
}

function withCors(res: Response): Response {
  const h = new Headers(res.headers);
  h.set("Access-Control-Allow-Origin", "*");
  h.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  h.set("Access-Control-Allow-Headers", "Content-Type, Authorization");
  return new Response(res.body, { status: res.status, headers: h });
}

function corsPreflight(): Response {
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
      "Access-Control-Max-Age": "86400",
    },
  });
}
