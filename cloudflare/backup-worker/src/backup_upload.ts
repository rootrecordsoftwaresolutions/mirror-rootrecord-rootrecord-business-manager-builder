/**
 * Desktop DB snapshots → R2. Auth is a long random "vault token" stored only on the client.
 */
import type { Env } from "./types";

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

async function vaultIdFromToken(token: string, pepper: string): Promise<string> {
  const enc = new TextEncoder();
  const payload = pepper ? `${pepper}\n${token}` : token;
  const digest = await crypto.subtle.digest("SHA-256", enc.encode(payload));
  const bytes = new Uint8Array(digest);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

function safeFilename(name: string): string {
  const base = name.replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 160);
  return base || "backup.sqlite3";
}

function bearerToken(request: Request): string {
  const auth = request.headers.get("Authorization") || "";
  return auth.startsWith("Bearer ") ? auth.slice(7).trim() : "";
}

/** GET /v1/backup/health — no auth; checks Worker + R2 binding. */
export async function handleBackupHealth(env: Env): Promise<Response> {
  const hasR2 = Boolean(env.BACKUPS);
  return json({
    ok: true,
    service: "rootrecord-desktop-backup",
    r2: hasR2 ? "ready" : "unconfigured",
  });
}

/** PUT /v1/backup/upload — raw body = SQLite file bytes. Authorization: Bearer <vault_token> */
export async function handleBackupUpload(request: Request, env: Env): Promise<Response> {
  if (!env.BACKUPS) {
    return json(
      { error: { code: "BACKUP_DISABLED", message: "R2 bucket BACKUPS is not bound on this Worker." } },
      503
    );
  }
  if (request.method !== "PUT") {
    return json({ error: { code: "METHOD_NOT_ALLOWED", message: "Use PUT" } }, 405);
  }
  const token = bearerToken(request);
  if (token.length < 24) {
    return json(
      {
        error: {
          code: "INVALID_TOKEN",
          message: "Authorization Bearer vault token required (min 24 characters). Not your account password.",
        },
      },
      401
    );
  }
  const rawName = request.headers.get("X-RootRecord-Filename") || "backup.sqlite3";
  const fname = safeFilename(rawName);
  const pepper = (env.BACKUP_VAULT_PEPPER || "").trim();
  const vault = await vaultIdFromToken(token, pepper);
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const key = `v/${vault}/${stamp}-${fname}`;

  const body = request.body;
  if (!body) {
    return json({ error: { code: "EMPTY_BODY", message: "Request body required (SQLite bytes)" } }, 400);
  }

  const cl = request.headers.get("Content-Length");
  const n = cl ? parseInt(cl, 10) : NaN;
  if (Number.isFinite(n) && n > 95 * 1024 * 1024) {
    return json({ error: { code: "PAYLOAD_TOO_LARGE", message: "Max upload ~95 MiB" } }, 413);
  }

  try {
    await env.BACKUPS.put(key, body, {
      httpMetadata: { contentType: "application/vnd.sqlite3" },
    });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    return json({ error: { code: "R2_PUT_FAILED", message: msg.slice(0, 400) } }, 500);
  }

  return json({ ok: true, uploaded: true, key }, 201);
}
