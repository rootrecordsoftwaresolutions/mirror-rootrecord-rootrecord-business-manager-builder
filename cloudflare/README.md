# RootRecord license API (Cloudflare Workers + D1)

Trial + entitlement + **Stripe Checkout + webhooks** (updates `subscription_status`, `stripe_customer_id`, `stripe_subscription_id` on `accounts`).

## Deploy without local terminals (recommended)

CI deploys the **license** Worker and applies D1 migrations when you **push to `main`** (paths under `cloudflare/src/`, `cloudflare/wrangler.toml`, etc. — see `.github/workflows/deploy-license-api.yml`) or run **Deploy license API (Cloudflare)** manually. The **backup** Worker deploys from **`cloudflare/backup-worker/`** via **Deploy backup Worker (Cloudflare)** (or push touching that folder).

### One-time: Cloudflare + GitHub

1. **Create a D1 database** in the [Cloudflare dashboard](https://dash.cloudflare.com) → **Workers & Pages** → **D1** → **Create database** → name it **`rootrecord_license`** (must match `wrangler.toml`).
2. Open the database → copy **Database ID** → paste into **`cloudflare/wrangler.toml`** as `database_id = "..."` → **commit and push** (this repo must know which DB to migrate).
3. **Create the API token** (required permissions are spelled out below — **Workers Scripts: Edit** and **D1: Edit**). See **[Create `CLOUDFLARE_API_TOKEN`](#create-cloudflare_api_token-permissions--steps)**.
4. **Account ID** is already in **`cloudflare/wrangler.toml`** as `account_id` (no GitHub secret for it).
5. **GitHub** (this repo) → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
   - `CLOUDFLARE_API_TOKEN` — the token string from step 3  
   - **`LICENSE_API_SECRET`** — long random string; CI runs `wrangler secret put` so the Worker accepts `Authorization: Bearer …` on **`/v1/entitlement`** and **`/v1/billing/checkout`**. Use the **same** value as `LICENSE_API_SECRET` in the desktop app `.env`. **Required** for those routes (503 if unset).

6. Push to `main` (or **Actions** → **Deploy license API (Cloudflare)** → **Run workflow**). When green, use the Worker URL from the deploy log (or **Workers & Pages** → **rootrecord-license**).

### Create `CLOUDFLARE_API_TOKEN` (permissions + steps)

**Why not the Global API Key:** use an **API Token** with narrow permissions. Do **not** use the Global API Key for GitHub Actions.

**Minimum permissions** (Cloudflare dashboard labels):

| Scope | Permission | Access |
|-------|------------|--------|
| **Account** | **Workers Scripts** | **Edit** |
| **Account** | **D1** | **Edit** |

**Clicks:**

1. Open **[API Tokens](https://dash.cloudflare.com/profile/api-tokens)** (avatar → *My Profile* → *API Tokens*, or that link).
2. **Create Token** → **Create Custom Token** → **Get started**.
3. **Token name:** e.g. `github-deploy-rootrecord-license`.
4. **Permissions** → **Add** — add **both** rows:
   - **Account** → **Workers Scripts** → **Edit**
   - **Account** → **D1** → **Edit**
5. **Account Resources:** **Include** → **Specific account** → pick this Cloudflare account (or *All accounts* if you only have one).
6. **Continue to summary** → **Create Token**.
7. **Copy** the token value immediately (shown once). This string is what goes in GitHub as **`CLOUDFLARE_API_TOKEN`**.

If the UI labels differ slightly, search the permission picker for **Workers Scripts** and **D1** — both must be **Edit**. If deploy fails with *Authentication error* or *forbidden*, the token is missing one of these or the wrong account was selected under **Account Resources**.

### Required GitHub secrets

| Secret | Purpose |
|--------|---------|
| `CLOUDFLARE_API_TOKEN` | Deploy Worker + run D1 migrations |
| `LICENSE_API_SECRET` | **Required** for `/v1/*`; CI syncs Bearer secret (same as app `.env`) |

---

## Stripe (set on the Worker in Cloudflare — not in GitHub)

After deploy, add these under **Workers → rootrecord-license → Settings → Variables and Secrets** (or `wrangler secret put` from `cloudflare/`):

| Name | Kind | Value |
|------|------|--------|
| `STRIPE_SECRET_KEY` | Secret | `sk_live_…` or `sk_test_…` ([API keys](https://dashboard.stripe.com/apikeys)) |
| `STRIPE_WEBHOOK_SECRET` | Secret | `whsec_…` from the webhook endpoint below |
| `STRIPE_PRICE_ID` | Variable (or secret) | `price_…` for your **$4.99/mo** recurring price ([Products](https://dashboard.stripe.com/products)) |

**Webhook endpoint (Stripe Dashboard → Developers → Webhooks → Add endpoint):**

- **URL:** `https://<your-worker-host>/webhooks/stripe`  
  Example: `https://rootrecord-license.wildecho94.workers.dev/webhooks/stripe`
- **Events to send:**  
  `checkout.session.completed`  
  `customer.subscription.updated`  
  `customer.subscription.deleted`

Copy the **Signing secret** (`whsec_…`) into **`STRIPE_WEBHOOK_SECRET`** on the Worker, then **redeploy** (or save — secrets bind on next deploy).

**Desktop app:** read-only footer shows **Activate Services** → opens Stripe Checkout (`POST /v1/billing/checkout` with the same Bearer as entitlement).

**CORS (optional):** set Worker variable **`CORS_ALLOW_ORIGIN`** to a single origin (e.g. `https://your-site.com`) to restrict `Access-Control-Allow-Origin`. If unset, the Worker uses `*` (desktop app is unaffected).

---

## Desktop DB cloud copies (dedicated Worker + R2)

Implemented in **`cloudflare/backup-worker/`** (Worker name default: `rootrecord-desktop-backup`). This is **separate** from the license Worker: no D1, no `LICENSE_API_SECRET`, no account session. The desktop app stores a random vault token locally and sends `Authorization: Bearer <that token>` to `PUT /v1/backup/upload`.

1. **Create an R2 bucket** if you do not have one (default name **`rootrecord-desktop-backups`**, must match `backup-worker/wrangler.toml`):  
   `cd cloudflare/backup-worker && npx wrangler r2 bucket create rootrecord-desktop-backups`
2. **`npm install`** in `cloudflare/backup-worker/`, then **`npm run deploy`** (or GitHub Actions workflow **Deploy backup Worker (Cloudflare)**).
3. **Optional Worker secret** `BACKUP_VAULT_PEPPER` — mixed into SHA-256 for the object prefix; not required for dev.

**Routes (backup Worker only)**

| Method | Path | Auth |
|--------|------|------|
| `GET` | `/v1/backup/health` | None — returns `{ ok, r2: "ready" \| "unconfigured" }` |
| `PUT` | `/v1/backup/upload` | `Authorization: Bearer <vault_token>` (min 24 chars). Body = raw SQLite bytes. Header `X-RootRecord-Filename: name.sqlite3` |

Objects are stored as `v/<sha256>/<iso>-<filename>`.

**Desktop:** Program Settings shows only an on/off toggle for cloud backup. URL resolution: **`ROOTRECORD_BACKUP_API_BASE_URL`** (env) → **`backup_shipped.SHIPPED_BACKUP_API_BASE_URL`** → optional DB override → legacy license Worker host (only if that Worker still exposes `/v1/backup/*`). Set **`SHIPPED_BACKUP_API_BASE_URL`** to your deployed backup Worker URL before shipping installers.

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
