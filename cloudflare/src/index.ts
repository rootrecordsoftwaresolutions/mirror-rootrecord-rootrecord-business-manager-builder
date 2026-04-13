import {
  authClaimPassword,
  authLogin,
  authSignup,
  deleteSessionByRawToken,
  resolveSessionAccountId,
} from "./auth";
import {
  isValidDeviceId,
  isValidEmail,
  loadAccount,
  normalizeEmail,
  resolveEntitlementOpen,
  resolveEntitlementSessionScoped,
} from "./entitlement";
import {
  createStripeCheckoutSession,
  handleStripeWebhook,
  verifyStripeSignature,
} from "./stripe";
import type { Env } from "./types";

/** Collapse duplicate slashes and strip a trailing slash so `/v1/auth/signup/` still matches handlers. */
function normalizedPathname(pathname: string): string {
  const collapsed = pathname.replace(/\/+/g, "/");
  if (collapsed === "/") return "/";
  return collapsed.replace(/\/$/, "");
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const pathname = normalizedPathname(url.pathname);
    if (request.method === "OPTIONS") {
      return corsPreflight(env);
    }

    /** Browser / uptime checks — API lives under /v1/* (no handler on bare `/` before this). */
    if (request.method === "GET" && pathname === "/") {
      return json({
        ok: true,
        service: "rootrecord-license",
        docs: "POST /v1/entitlement, /v1/auth/login, GET /v1/me, GET /health (desktop backups: cloudflare/backup-worker/)",
      });
    }

    if (request.method === "GET" && pathname === "/health") {
      return json({ ok: true, service: "rootrecord-license" });
    }

    /** Session-only: current D1 `accounts` row (not callable with LICENSE_API_SECRET). */
    if (request.method === "GET" && pathname === "/v1/me") {
      const sid = await authenticateSessionOnly(request, env);
      if (sid instanceof Response) {
        return withCors(sid, env);
      }
      const acc = await loadAccount(env.DB, sid);
      if (!acc) {
        return withCors(json({ error: { code: "NOT_FOUND", message: "Account not found" } }, 404), env);
      }
      return withCors(
        json({
          account_id: acc.id,
          email: acc.email,
          subscription_status: acc.subscription_status,
          trial_started_at:
            acc.trial_started_at != null ? new Date(acc.trial_started_at).toISOString() : null,
          trial_ends_at: acc.trial_ends_at != null ? new Date(acc.trial_ends_at).toISOString() : null,
          has_password: Boolean(acc.password_credential),
        }),
        env
      );
    }

    if (request.method === "GET" && pathname === "/billing/success") {
      return htmlResponse(
        200,
        "<!doctype html><html><head><meta charset=utf-8><title>Subscribed</title></head><body><p>Payment complete. You can close this tab and return to RootRecord.</p></body></html>"
      );
    }
    if (request.method === "GET" && pathname === "/billing/cancel") {
      return htmlResponse(
        200,
        "<!doctype html><html><head><meta charset=utf-8><title>Cancelled</title></head><body><p>Checkout cancelled. You can close this tab.</p></body></html>"
      );
    }

    if (request.method === "POST" && pathname === "/webhooks/stripe") {
      const whSecret = env.STRIPE_WEBHOOK_SECRET?.trim();
      if (!whSecret) {
        return json({ error: { code: "WEBHOOK_DISABLED", message: "STRIPE_WEBHOOK_SECRET not set" } }, 503);
      }
      const rawBody = await request.text();
      const sig = request.headers.get("Stripe-Signature");
      const ok = await verifyStripeSignature(rawBody, sig, whSecret);
      if (!ok) {
        return json({ error: "Invalid signature" }, 400);
      }
      let parsed: unknown;
      try {
        parsed = JSON.parse(rawBody);
      } catch {
        return json({ error: "Invalid JSON" }, 400);
      }
      try {
        await handleStripeWebhook(parsed, env.DB);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return json({ error: msg }, 500);
      }
      return json({ received: true });
    }

    if (request.method === "POST" && pathname === "/v1/billing/checkout") {
      const authCtx = await authenticateBearerCheckout(request, env);
      if ("error" in authCtx) return withCors(authCtx.error, env);
      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return withCors(json({ error: { code: "BAD_JSON", message: "Body must be JSON" } }, 400), env);
      }
      const o = body as Record<string, unknown>;
      const email = typeof o.email === "string" ? o.email.trim().toLowerCase() : "";
      if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        return withCors(json({ error: { code: "INVALID_EMAIL", message: "Valid email required" } }, 400), env);
      }
      if (authCtx.mode === "session") {
        const acc = await loadAccount(env.DB, authCtx.accountId);
        if (!acc || acc.email !== email) {
          return withCors(
            json(
              {
                error: {
                  code: "EMAIL_SESSION_MISMATCH",
                  message: "Checkout email must match the signed-in account.",
                },
              },
              403
            ),
            env
          );
        }
      }
      const origin = url.origin;
      const result = await createStripeCheckoutSession(email, env, origin);
      if ("error" in result) {
        return withCors(json({ error: { code: "STRIPE_ERROR", message: result.error } }, 502), env);
      }
      return withCors(json({ url: result.url }), env);
    }

    if (request.method === "POST" && pathname === "/v1/auth/signup") {
      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return withCors(json({ error: { code: "BAD_JSON", message: "Body must be JSON" } }, 400), env);
      }
      const out = await authSignup(env.DB, body);
      if ("error" in out) {
        return withCors(json({ error: unwrapAuthErrorPayload(out.error) }, out.status), env);
      }
      return withCors(json(out.ok, 201), env);
    }

    if (request.method === "POST" && pathname === "/v1/auth/login") {
      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return withCors(json({ error: { code: "BAD_JSON", message: "Body must be JSON" } }, 400), env);
      }
      const out = await authLogin(env.DB, body);
      if ("error" in out) {
        return withCors(json({ error: unwrapAuthErrorPayload(out.error) }, out.status), env);
      }
      return withCors(json(out.ok, 200), env);
    }

    if (request.method === "POST" && pathname === "/v1/auth/claim-password") {
      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return withCors(json({ error: { code: "BAD_JSON", message: "Body must be JSON" } }, 400), env);
      }
      const out = await authClaimPassword(env.DB, body);
      if ("error" in out) {
        return withCors(json({ error: unwrapAuthErrorPayload(out.error) }, out.status), env);
      }
      return withCors(json(out.ok, 200), env);
    }

    if (request.method === "POST" && pathname === "/v1/auth/logout") {
      const auth = request.headers.get("Authorization") || "";
      const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
      if (!token) {
        return withCors(json({ error: { code: "UNAUTHORIZED", message: "Bearer session token required" } }, 401), env);
      }
      await deleteSessionByRawToken(env.DB, token);
      return withCors(json({ ok: true }, 200), env);
    }

    if (request.method === "POST" && pathname === "/v1/entitlement") {
      const authCtx = await authenticateBearerEntitlement(request, env);
      if ("error" in authCtx) return withCors(authCtx.error, env);

      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return withCors(json({ error: { code: "BAD_JSON", message: "Body must be JSON" } }, 400), env);
      }

      const parsed = parseEntitlementBody(body);
      if ("error" in parsed) {
        return withCors(json({ error: parsed.error }, 400), env);
      }

      try {
        if (authCtx.mode === "api_secret") {
          const result = await resolveEntitlementOpen(env.DB, parsed.email, parsed.device_id);
          if (result.kind === "conflict") {
            return withCors(
              json({ error: { code: "EMAIL_DEVICE_MISMATCH", message: result.message } }, 409),
              env
            );
          }
          return withCors(json(result.payload), env);
        }
        const result = await resolveEntitlementSessionScoped(
          env.DB,
          authCtx.accountId,
          parsed.email,
          parsed.device_id
        );
        if (result.kind === "conflict") {
          return withCors(
            json({ error: { code: "EMAIL_DEVICE_MISMATCH", message: result.message } }, 409),
            env
          );
        }
        return withCors(json(result.payload), env);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return withCors(json({ error: { code: "INTERNAL", message: msg } }, 500), env);
      }
    }

    if (request.method === "POST" && pathname === "/v1/sync/push") {
      const accountId = await authenticateSessionOnly(request, env);
      if (accountId instanceof Response) return withCors(accountId, env);
      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return withCors(json({ error: { code: "BAD_JSON", message: "Body must be JSON" } }, 400), env);
      }
      const o = body as Record<string, unknown>;
      const device_id = typeof o.device_id === "string" ? o.device_id.trim() : "";
      if (!isValidDeviceId(device_id)) {
        return withCors(
          json(
            {
              error: {
                code: "INVALID_DEVICE_ID",
                message: "device_id must be 8–128 visible ASCII characters (no spaces)",
              },
            },
            400
          ),
          env
        );
      }
      const eventsRaw = o.events;
      if (!Array.isArray(eventsRaw) || eventsRaw.length === 0) {
        return withCors(
          json({ error: { code: "INVALID_EVENTS", message: "events must be a non-empty array" } }, 400),
          env
        );
      }
      if (eventsRaw.length > 100) {
        return withCors(
          json({ error: { code: "TOO_MANY_EVENTS", message: "At most 100 events per request" } }, 400),
          env
        );
      }
      const now = Date.now();
      for (const ev of eventsRaw) {
        if (!ev || typeof ev !== "object") continue;
        const e = ev as Record<string, unknown>;
        const cmid = typeof e.client_mutation_id === "string" ? e.client_mutation_id.trim() : "";
        if (!cmid || cmid.length > 120) continue;
        const et = typeof e.entity_type === "string" ? e.entity_type.slice(0, 120) : "";
        const ek = typeof e.entity_key === "string" ? e.entity_key.slice(0, 500) : "";
        const op = e.op === "delete" ? "delete" : "upsert";
        let payloadJson: string;
        try {
          payloadJson = JSON.stringify(e.payload !== undefined ? e.payload : {});
        } catch {
          payloadJson = "{}";
        }
        const id = crypto.randomUUID();
        await env.DB.prepare(
          `INSERT OR IGNORE INTO sync_client_events (id, account_id, device_id, client_mutation_id, entity_type, entity_key, op, payload_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
        )
          .bind(id, accountId, device_id, cmid, et || "unknown", ek || "", op, payloadJson, now)
          .run();
      }
      return withCors(json({ ok: true, accepted: eventsRaw.length }), env);
    }

    if (request.method === "GET" && pathname === "/v1/sync/pull") {
      const accountId = await authenticateSessionOnly(request, env);
      if (accountId instanceof Response) return withCors(accountId, env);
      const sinceRaw = url.searchParams.get("since_ms");
      const sinceMs = Math.max(0, parseInt(sinceRaw || "0", 10) || 0);
      type Row = {
        device_id: string;
        client_mutation_id: string;
        entity_type: string;
        entity_key: string;
        op: string;
        payload_json: string;
        created_at: number;
      };
      const q = await env.DB.prepare(
        `SELECT device_id, client_mutation_id, entity_type, entity_key, op, payload_json, created_at
         FROM sync_client_events
         WHERE account_id = ? AND created_at > ?
         ORDER BY created_at ASC
         LIMIT 200`
      )
        .bind(accountId, sinceMs)
        .all<Row>();
      const rows = q.results ?? [];
      const events = rows.map((r) => {
        let payload: unknown = {};
        try {
          payload = JSON.parse(r.payload_json);
        } catch {
          payload = {};
        }
        return {
          device_id: r.device_id,
          client_mutation_id: r.client_mutation_id,
          entity_type: r.entity_type,
          entity_key: r.entity_key,
          op: r.op,
          payload,
          created_at: r.created_at,
        };
      });
      return withCors(json({ events, server_time_ms: Date.now() }), env);
    }

    return withCors(json({ error: { code: "NOT_FOUND", message: "Not found" } }, 404), env);
  },
};

async function authenticateSessionOnly(request: Request, env: Env): Promise<string | Response> {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!token) {
    return json({ error: { code: "UNAUTHORIZED", message: "Authorization Bearer session token required" } }, 401);
  }
  const secret = env.LICENSE_API_SECRET?.trim();
  if (secret && safeEqual(token, secret)) {
    return json(
      {
        error: {
          code: "FORBIDDEN",
          message: "Use a user session token from POST /v1/auth/login, not LICENSE_API_SECRET.",
        },
      },
      403
    );
  }
  const accountId = await resolveSessionAccountId(env.DB, token);
  if (!accountId) {
    return json({ error: { code: "UNAUTHORIZED", message: "Invalid or expired session" } }, 401);
  }
  return accountId;
}

type CheckoutAuth =
  | { mode: "api_secret" }
  | { mode: "session"; accountId: string }
  | { error: Response };

async function authenticateBearerCheckout(request: Request, env: Env): Promise<CheckoutAuth> {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!token) {
    return {
      error: json({ error: { code: "UNAUTHORIZED", message: "Authorization Bearer token required" } }, 401),
    };
  }
  const secret = env.LICENSE_API_SECRET?.trim();
  if (secret && safeEqual(token, secret)) {
    return { mode: "api_secret" };
  }
  const accountId = await resolveSessionAccountId(env.DB, token);
  if (accountId) {
    return { mode: "session", accountId };
  }
  if (secret) {
    return {
      error: json({ error: { code: "UNAUTHORIZED", message: "Invalid or expired session" } }, 401),
    };
  }
  return {
    error: json(
      {
        error: {
          code: "UNAUTHORIZED",
          message:
            "Sign in to obtain a session token, or set LICENSE_API_SECRET and use the shared-secret Bearer token for checkout.",
        },
      },
      401
    ),
  };
}

type EntitlementAuth =
  | { mode: "api_secret" }
  | { mode: "session"; accountId: string }
  | { error: Response };

async function authenticateBearerEntitlement(request: Request, env: Env): Promise<EntitlementAuth> {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
  if (!token) {
    return {
      error: json({ error: { code: "UNAUTHORIZED", message: "Authorization Bearer token required" } }, 401),
    };
  }
  const secret = env.LICENSE_API_SECRET?.trim();
  if (secret && safeEqual(token, secret)) {
    return { mode: "api_secret" };
  }
  const accountId = await resolveSessionAccountId(env.DB, token);
  if (accountId) {
    return { mode: "session", accountId };
  }
  if (!secret) {
    return {
      error: json(
        {
          error: {
            code: "LICENSE_API_SECRET_MISSING",
            message:
              "Set LICENSE_API_SECRET on this Worker for shared-secret access, or use a valid session token from POST /v1/auth/login.",
          },
        },
        503
      ),
    };
  }
  return {
    error: json({ error: { code: "UNAUTHORIZED", message: "Invalid or missing credentials" } }, 401),
  };
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

/** Auth handlers historically returned `{ error: { error: { code, message }}}` — normalize for clients. */
function unwrapAuthErrorPayload(err: unknown): Record<string, unknown> {
  if (!err || typeof err !== "object") {
    return { code: "UNKNOWN", message: String(err) };
  }
  const o = err as Record<string, unknown>;
  const mid = o.error;
  if (mid && typeof mid === "object") {
    const inner = (mid as Record<string, unknown>).error;
    if (inner && typeof inner === "object" && "code" in inner && "message" in inner) {
      return inner as Record<string, unknown>;
    }
    if ("code" in mid && "message" in mid) {
      return mid as Record<string, unknown>;
    }
  }
  if ("code" in o && "message" in o) {
    return o as Record<string, unknown>;
  }
  return { code: "UNKNOWN", message: JSON.stringify(err).slice(0, 200) };
}

function withCors(res: Response, env: Env): Response {
  const h = new Headers(res.headers);
  h.set("Access-Control-Allow-Origin", corsAllowOrigin(env));
  h.set("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS");
  h.set(
    "Access-Control-Allow-Headers",
    "Content-Type, Authorization, Stripe-Signature, X-RootRecord-Filename"
  );
  return new Response(res.body, { status: res.status, headers: h });
}

function htmlResponse(status: number, body: string): Response {
  return new Response(body, {
    status,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}

function corsAllowOrigin(env: Env): string {
  const o = env.CORS_ALLOW_ORIGIN?.trim();
  return o || "*";
}

function corsPreflight(env: Env): Response {
  return new Response(null, {
    status: 204,
    headers: {
      "Access-Control-Allow-Origin": corsAllowOrigin(env),
      "Access-Control-Allow-Methods": "GET, POST, PUT, OPTIONS",
      "Access-Control-Allow-Headers":
        "Content-Type, Authorization, Stripe-Signature, X-RootRecord-Filename",
      "Access-Control-Max-Age": "86400",
    },
  });
}
