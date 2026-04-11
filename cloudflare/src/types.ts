export interface Env {
  DB: D1Database;
  /** If set, clients must send Authorization: Bearer <LICENSE_API_SECRET> */
  LICENSE_API_SECRET?: string;
  /** Stripe API secret key (sk_live_... / sk_test_...) */
  STRIPE_SECRET_KEY?: string;
  /** Stripe webhook signing secret (whsec_...) */
  STRIPE_WEBHOOK_SECRET?: string;
  /** Recurring price id (price_...) */
  STRIPE_PRICE_ID?: string;
}
