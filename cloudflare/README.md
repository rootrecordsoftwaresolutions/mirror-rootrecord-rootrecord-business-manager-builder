# RootRecord license API (Cloudflare Workers + D1)

Trial + entitlement backend. **Stripe webhooks** can be added later to set `subscription_status` / `stripe_customer_id` on `accounts`.

## Deploy without local terminals (recommended)

CI deploys the Worker and applies D1 migrations when you **push to `main`** (only when `cloudflare/**` changes) or run the workflow manually.

### One-time: Cloudflare + GitHub

1. **Create a D1 database** in the [Cloudflare dashboard](https://dash.cloudflare.com) → **Workers & Pages** → **D1** → **Create database** → name it **`rootrecord_license`** (must match `wrangler.toml`).
2. Open the database → copy **Database ID** → paste into **`cloudflare/wrangler.toml`** as `database_id = "..."` → **commit and push** (this repo must know which DB to migrate).
3. **API token** (Cloudflare): [My Profile → API Tokens](https://dash.cloudflare.com/profile/api-tokens) → **Create Token** → use template **Edit Cloudflare Workers** and add **D1: Edit** (or create custom with Workers Scripts:Edit + D1:Edit for your account).
4. **Account ID** is already set in **`cloudflare/wrangler.toml`** as `account_id` (no GitHub secret for it).
5. **GitHub** (this repo) → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
   - `CLOUDFLARE_API_TOKEN` — paste the token from step 3  
   - Optional: `LICENSE_API_SECRET` — long random string; if set, CI runs `wrangler secret put` so the Worker requires `Authorization: Bearer …` (same value in your app config).

6. Push to `main` (or **Actions** → **Deploy license API (Cloudflare)** → **Run workflow**). When green, use the Worker URL from the deploy log (or **Workers & Pages** → **rootrecord-license**).

### Required GitHub secrets

| Secret | Purpose |
|--------|---------|
| `CLOUDFLARE_API_TOKEN` | Deploy Worker + run D1 migrations |
| `LICENSE_API_SECRET` | Optional; syncs Bearer secret for `/v1/entitlement` |

---

## Behavior

- **POST `/v1/entitlement`** — body: `{ "email": "you@domain.com", "device_id": "stable-machine-id" }`.
- **New email + new device** → new account, **14-day trial**, device linked.
- **Same email, new device** → device linked; same trial/paid state (**one trial per email**).
- **Same device, different email** → **409** `EMAIL_DEVICE_MISMATCH`.
- **`subscription_status` = `active`** → full access (`reason: paid`). Otherwise trial vs read-only from `trial_ends_at`.

If **`LICENSE_API_SECRET`** is unset on the Worker, auth is skipped (**avoid in production**).

---

## Example (HTTPS)

```bash
curl -s "https://BASE/health"

curl -s -X POST "https://BASE/v1/entitlement" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_SECRET" \
  -d "{\"email\":\"test@example.com\",\"device_id\":\"dev-machine-001\"}"
```

Success response shape:

```json
{
  "account_id": "...",
  "access": "full",
  "reason": "trialing",
  "trial_ends_at": "...",
  "valid_until": "...",
  "subscription_status": "none"
}
```

---

## Get Account ID + D1 IDs without hunting the dashboard

1. **From the address bar:** when you’re anywhere in the dashboard, the URL often looks like  
   `https://dash.cloudflare.com/<ACCOUNT_ID>/...` — the first path segment after `.com/` is your **Account ID** (should match `account_id` in `wrangler.toml`).

2. **From the CLI** (after `npm install` in `cloudflare/` and `npx wrangler login` once):  
   `npm run cf:ids` — prints **`wrangler whoami`** (account) and **`wrangler d1 list`** (database names + UUIDs). Use that UUID for `wrangler.toml` if needed; yours may already be set.

## Optional: manual CLI

If you prefer `wrangler` locally: `npm install`, `npm run db:create` (or dashboard D1), paste `database_id`, `npm run db:migrate`, `wrangler secret put LICENSE_API_SECRET`, `npm run deploy`. **Not required** if you use GitHub Actions above.

---

## Optional: local `wrangler dev`

Only for offline iteration. Not required for production.
