# RootRecord license API (Cloudflare Workers + D1)

Trial + entitlement backend. **Stripe webhooks** can be added later to set `subscription_status` / `stripe_customer_id` on `accounts`.

## Deploy without local terminals (recommended)

CI deploys the Worker and applies D1 migrations when you **push to `main`** (only when `cloudflare/**` changes) or run the workflow manually.

### One-time: Cloudflare + GitHub

1. **Create a D1 database** in the [Cloudflare dashboard](https://dash.cloudflare.com) → **Workers & Pages** → **D1** → **Create database** → name it **`rootrecord_license`** (must match `wrangler.toml`).
2. Open the database → copy **Database ID** → paste into **`cloudflare/wrangler.toml`** as `database_id = "..."` → **commit and push** (this repo must know which DB to migrate).
3. **Create the API token** (required permissions are spelled out below — **Workers Scripts: Edit** and **D1: Edit**). See **[Create `CLOUDFLARE_API_TOKEN`](#create-cloudflare_api_token-permissions--steps)**.
4. **Account ID** is already in **`cloudflare/wrangler.toml`** as `account_id` (no GitHub secret for it).
5. **GitHub** (this repo) → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:
   - `CLOUDFLARE_API_TOKEN` — the token string from step 3  
   - Optional: `LICENSE_API_SECRET` — long random string; if set, CI runs `wrangler secret put` so the Worker requires `Authorization: Bearer …` (same value in your app config).

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
