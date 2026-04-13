import { handleBackupHealth, handleBackupUpload } from "./backup_upload";
import type { Env } from "./types";

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

    if (request.method === "GET" && pathname === "/") {
      return json({
        ok: true,
        service: "rootrecord-desktop-backup",
        docs: "GET /health, GET /v1/backup/health, PUT /v1/backup/upload",
      });
    }

    if (request.method === "GET" && pathname === "/health") {
      return json({ ok: true, service: "rootrecord-desktop-backup" });
    }

    if (request.method === "GET" && pathname === "/v1/backup/health") {
      return withCors(await handleBackupHealth(env), env);
    }
    if (request.method === "PUT" && pathname === "/v1/backup/upload") {
      return withCors(await handleBackupUpload(request, env), env);
    }

    return withCors(json({ error: { code: "NOT_FOUND", message: "Not found" } }, 404), env);
  },
};

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

function withCors(res: Response, env: Env): Response {
  const h = new Headers(res.headers);
  h.set("Access-Control-Allow-Origin", corsAllowOrigin(env));
  h.set("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS");
  h.set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-RootRecord-Filename");
  return new Response(res.body, { status: res.status, headers: h });
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
      "Access-Control-Allow-Headers": "Content-Type, Authorization, X-RootRecord-Filename",
      "Access-Control-Max-Age": "86400",
    },
  });
}
