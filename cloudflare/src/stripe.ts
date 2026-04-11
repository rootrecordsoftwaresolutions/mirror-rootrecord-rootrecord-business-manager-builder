/**
 * Stripe Checkout + webhook handling (Workers runtime, no stripe npm package).
 */

import type { Env } from "./types";

export async function verifyStripeSignature(
  rawBody: string,
  stripeSignatureHeader: string | null,
  secret: string
): Promise<boolean> {
  if (!stripeSignatureHeader || !secret) return false;
  const parts = stripeSignatureHeader.split(",").map((s) => s.trim());
  let timestamp = "";
  const v1sigs: string[] = [];
  for (const p of parts) {
    const eq = p.indexOf("=");
    if (eq < 0) continue;
    const k = p.slice(0, eq);
    const v = p.slice(eq + 1);
    if (k === "t") timestamp = v;
    if (k === "v1") v1sigs.push(v);
  }
  if (!timestamp || v1sigs.length === 0) return false;

  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const signedPayload = `${timestamp}.${rawBody}`;
  const sigBuf = await crypto.subtle.sign("HMAC", key, enc.encode(signedPayload));
  const hex = [...new Uint8Array(sigBuf)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
  return v1sigs.some((expected) => timingSafeEqualHex(hex, expected));
}

function timingSafeEqualHex(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let out = 0;
  for (let i = 0; i < a.length; i++) {
    out |= a.charCodeAt(i)! ^ b.charCodeAt(i)!;
  }
  return out === 0;
}

function normalizeEmail(raw: string): string {
  return raw.trim().toLowerCase();
}

function mapStripeSubscriptionStatus(status: string): string {
  switch (status) {
    case "active":
    case "trialing":
      return "active";
    case "past_due":
      return "past_due";
    case "canceled":
    case "unpaid":
    case "incomplete_expired":
    case "paused":
    case "incomplete":
    default:
      return "none";
  }
}

export async function createStripeCheckoutSession(
  email: string,
  env: Env,
  requestOrigin: string
): Promise<{ url: string } | { error: string }> {
  const priceId = env.STRIPE_PRICE_ID?.trim();
  const sk = env.STRIPE_SECRET_KEY?.trim();
  if (!priceId || !sk) {
    return { error: "Stripe is not configured (set STRIPE_PRICE_ID and STRIPE_SECRET_KEY on the Worker)." };
  }
  const origin = requestOrigin.replace(/\/$/, "");
  const successUrl = `${origin}/billing/success`;
  const cancelUrl = `${origin}/billing/cancel`;
  const em = normalizeEmail(email);
  const params = new URLSearchParams();
  params.set("mode", "subscription");
  params.set("success_url", successUrl);
  params.set("cancel_url", cancelUrl);
  params.set("customer_email", em);
  params.set("line_items[0][price]", priceId);
  params.set("line_items[0][quantity]", "1");
  params.set("metadata[app_email]", em);
  params.set("subscription_data[metadata][app_email]", em);
  params.set("allow_promotion_codes", "true");

  const r = await fetch("https://api.stripe.com/v1/checkout/sessions", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${sk}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: params.toString(),
  });
  const text = await r.text();
  if (!r.ok) {
    return { error: text.slice(0, 800) };
  }
  try {
    const data = JSON.parse(text) as { url?: string };
    if (!data.url) return { error: "Stripe returned no checkout URL" };
    return { url: data.url };
  } catch {
    return { error: "Invalid JSON from Stripe" };
  }
}

type D1 = D1Database;

async function upsertAccountFromStripe(
  db: D1,
  email: string,
  fields: {
    stripe_customer_id: string | null;
    stripe_subscription_id: string | null;
    subscription_status: string;
  }
): Promise<void> {
  const now = Date.now();
  const em = normalizeEmail(email);
  const row = await db.prepare(`SELECT id FROM accounts WHERE email = ?`).bind(em).first<{ id: string }>();
  if (row) {
    await db
      .prepare(
        `UPDATE accounts SET stripe_customer_id = ?, stripe_subscription_id = ?, subscription_status = ?, updated_at = ?
         WHERE email = ?`
      )
      .bind(
        fields.stripe_customer_id,
        fields.stripe_subscription_id,
        fields.subscription_status,
        now,
        em
      )
      .run();
    return;
  }
  const id = crypto.randomUUID();
  await db
    .prepare(
      `INSERT INTO accounts (id, email, trial_started_at, trial_ends_at, subscription_status, stripe_customer_id, stripe_subscription_id, created_at, updated_at)
       VALUES (?, ?, NULL, NULL, ?, ?, ?, ?, ?)`
    )
    .bind(
      id,
      em,
      fields.subscription_status,
      fields.stripe_customer_id,
      fields.stripe_subscription_id,
      now,
      now
    )
    .run();
}

async function updateByStripeCustomerId(
  db: D1,
  customerId: string,
  fields: { stripe_subscription_id: string | null; subscription_status: string }
): Promise<void> {
  const now = Date.now();
  await db
    .prepare(
      `UPDATE accounts SET stripe_subscription_id = ?, subscription_status = ?, updated_at = ?
       WHERE stripe_customer_id = ?`
    )
    .bind(fields.stripe_subscription_id, fields.subscription_status, now, customerId)
    .run();
}

export async function handleStripeWebhook(eventJson: unknown, db: D1): Promise<void> {
  const ev = eventJson as { type?: string; data?: { object?: Record<string, unknown> } };
  const type = ev.type || "";
  const obj = ev.data?.object;
  if (!obj || typeof obj !== "object") return;

  if (type === "checkout.session.completed") {
    const session = obj as {
      customer?: string | null;
      subscription?: string | null;
      customer_email?: string | null;
      customer_details?: { email?: string | null };
      metadata?: { app_email?: string };
    };
    const email =
      session.customer_details?.email ||
      session.customer_email ||
      session.metadata?.app_email;
    if (!email) return;
    const cust =
      typeof session.customer === "string"
        ? session.customer
        : session.customer
          ? String(session.customer)
          : null;
    const subId =
      typeof session.subscription === "string"
        ? session.subscription
        : session.subscription
          ? String(session.subscription)
          : null;
    await upsertAccountFromStripe(db, email, {
      stripe_customer_id: cust,
      stripe_subscription_id: subId,
      subscription_status: "active",
    });
    return;
  }

  if (type === "customer.subscription.updated" || type === "customer.subscription.deleted") {
    const sub = obj as {
      id?: string;
      status?: string;
      customer?: string;
      metadata?: { app_email?: string };
    };
    const customerId = typeof sub.customer === "string" ? sub.customer : null;
    const subId = typeof sub.id === "string" ? sub.id : null;
    const metaEmail = sub.metadata?.app_email;
    const status =
      type === "customer.subscription.deleted" ? "none" : mapStripeSubscriptionStatus(sub.status || "");

    if (metaEmail) {
      await upsertAccountFromStripe(db, metaEmail, {
        stripe_customer_id: customerId,
        stripe_subscription_id: subId,
        subscription_status: status,
      });
    } else if (customerId) {
      await updateByStripeCustomerId(db, customerId, {
        stripe_subscription_id: subId,
        subscription_status: status,
      });
    }
  }
}
