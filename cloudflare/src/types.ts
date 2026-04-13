export interface Env {
  DB: D1Database;
  /**
   * Required for POST /v1/entitlement and POST /v1/billing/checkout.
   * Desktop app: same value as LICENSE_API_SECRET in .env (Bearer token).
   */
  LICENSE_API_SECRET?: string;
  /** Optional. If set (e.g. https://app.example.com), replaces Access-Control-Allow-Origin: *. */
  CORS_ALLOW_ORIGIN?: string;
  /** Stripe API secret key (sk_live_... / sk_test_...) */
  STRIPE_SECRET_KEY?: string;
  /** Stripe webhook signing secret (whsec_...) */
  STRIPE_WEBHOOK_SECRET?: string;
  /** Recurring price id (price_...) */
  STRIPE_PRICE_ID?: string;
}
