// worker.js (Cloudflare Worker)
// Bindings:
// - KV namespace: INTEL_KV
// - Env var: INGEST_TOKEN
// - Env var: JWT_SECRET (must match API signer secret)
// - Env var: CORS_ORIGIN_ALLOWLIST (comma-separated) or CORS_ORIGIN

const textEncoder = new TextEncoder();

function normalizeTs(inputTs, now) {
  let ts = typeof inputTs === "number" ? inputTs : now;
  if (ts > 0 && ts < 10_000_000_000) ts = ts * 1000;
  if (ts > now + 24 * 60 * 60 * 1000) ts = now;
  if (ts <= 0) ts = now;
  return ts;
}

function normalizeHeadline(h) {
  return String(h || "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function normalizeUrl(u) {
  return String(u || "").trim();
}

function dedupeKeyFromNormalized({ url, title }) {
  const u = normalizeUrl(url);
  if (u) return `url:${u}`;
  return `t:${normalizeHeadline(title)}`;
}

function safeId({ id, ts, url, title }) {
  if (id) return id;
  const k = dedupeKeyFromNormalized({ url, title }).slice(0, 180);
  const bucket = Math.floor((ts || Date.now()) / 1000);
  return `${k}:${bucket}`;
}

function toCanonicalEvent(raw, now) {
  const isLegacy = Object.prototype.hasOwnProperty.call(raw, "headline");
  const title = isLegacy ? String(raw.headline || "").trim() : String(raw.title || "").trim();
  if (!title) return null;

  const ts = normalizeTs(raw.ts, now);
  const summary = isLegacy ? String(raw.body || "") : String(raw.summary || "");
  const url = String(raw.url || "");
  const tags = Array.isArray(raw.tags) ? raw.tags.map((t) => String(t)).slice(0, 30) : [];

  return {
    id: safeId({ id: raw.id, ts, url, title }),
    ts,
    source: String(raw.source || "twitterbot"),
    type: String(raw.type || "news"),
    title,
    summary,
    url,
    tags,
    severity: raw.severity ? String(raw.severity) : undefined,
    chain: raw.chain ? String(raw.chain) : undefined,
    classification: raw.classification && typeof raw.classification === "object" ? raw.classification : undefined,
    data: raw.data && typeof raw.data === "object" ? raw.data : {},
    meta: {
      ingestTs: now,
      version: 1,
      dedupeKey: dedupeKeyFromNormalized({ url, title }),
    },
  };
}

function toLegacyShapeForResponse(ev) {
  return {
    ...ev,
    headline: ev.title,
    body: ev.summary || "",
  };
}


function fromBase64Url(str) {
  const normalized = str.replace(/-/g, "+").replace(/_/g, "/");
  const padded = normalized + "=".repeat((4 - (normalized.length % 4)) % 4);
  const raw = atob(padded);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function parseJwtPayload(token) {
  const parts = token.split(".");
  if (parts.length !== 3) return null;

  try {
    const payloadBytes = fromBase64Url(parts[1]);
    const payloadJson = new TextDecoder().decode(payloadBytes);
    return JSON.parse(payloadJson);
  } catch {
    return null;
  }
}

function parseJwtHeader(token) {
  const parts = token.split(".");
  if (parts.length !== 3) return null;

  try {
    const headerBytes = fromBase64Url(parts[0]);
    const headerJson = new TextDecoder().decode(headerBytes);
    return JSON.parse(headerJson);
  } catch {
    return null;
  }
}

async function verifyHs256Jwt(token, secret) {
  const parts = token.split(".");
  if (parts.length !== 3) return null;

  const [headerB64, payloadB64, sigB64] = parts;
  const header = parseJwtHeader(token);
  const payload = parseJwtPayload(token);
  if (!header || !payload) return null;

  // Reject algorithm confusion: only accept explicit HS256 JWTs.
  if (header.alg !== "HS256") return null;

  const data = textEncoder.encode(`${headerB64}.${payloadB64}`);
  const key = await crypto.subtle.importKey(
    "raw",
    textEncoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["verify"]
  );

  const signature = fromBase64Url(sigB64);
  const valid = await crypto.subtle.verify("HMAC", key, signature, data);
  if (!valid) return null;

  const nowSec = Math.floor(Date.now() / 1000);
  // Require exp for short-lived access tokens (prevents non-expiring tokens).
  if (typeof payload.exp !== "number" || !Number.isFinite(payload.exp)) return null;
  if (payload.exp <= nowSec) return null;
  if (typeof payload.nbf === "number" && payload.nbf > nowSec) return null;

  // Ensure this token is shaped like an intel access token.
  if (typeof payload.addr !== "string" || payload.addr.length === 0) return null;
  if (typeof payload.untilMs !== "number" || !Number.isFinite(payload.untilMs) || payload.untilMs <= 0) return null;

  return payload;
}

async function requireJwt(request, env) {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";

  if (!token) return { ok: false, status: 401, error: "Missing token" };
  if (!env.JWT_SECRET) return { ok: false, status: 500, error: "Server missing JWT_SECRET" };

  const payload = await verifyHs256Jwt(token, env.JWT_SECRET);
  if (!payload) return { ok: false, status: 401, error: "Invalid/expired token" };

  const untilMs = Number(payload.untilMs || 0);
  if (Number.isFinite(untilMs) && untilMs > 0 && Date.now() > untilMs) {
    return { ok: false, status: 403, error: "Subscription expired" };
  }

  return { ok: true, payload };
}

function getAllowedOrigins(env) {
  const raw = (env.CORS_ORIGIN_ALLOWLIST || env.CORS_ORIGIN || "http://localhost:5173").trim();
  const list = raw.split(",").map((x) => x.trim()).filter(Boolean);
  return {
    allowAll: list.includes("*"),
    list,
  };
}

function buildCorsHeaders(request, env) {
  const origin = request.headers.get("Origin") || "";
  const { allowAll, list } = getAllowedOrigins(env);

  // Non-browser callers may not send Origin.
  if (!origin) {
    return {
      headers: {
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
      },
      allowed: true,
    };
  }

  if (allowAll || list.includes(origin)) {
    return {
      headers: {
        "Access-Control-Allow-Origin": origin,
        "Vary": "Origin",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type,Authorization",
      },
      allowed: true,
    };
  }

  return {
    headers: {
      "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type,Authorization",
    },
    allowed: false,
  };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    const { headers: corsHeaders, allowed: corsAllowed } = buildCorsHeaders(request, env);
    if (request.method === "OPTIONS") {
      return new Response("", { status: corsAllowed ? 204 : 403, headers: corsHeaders });
    }

    if (!corsAllowed && request.headers.get("Origin")) {
      return Response.json({ error: "CORS blocked: origin not allowed" }, { status: 403, headers: corsHeaders });
    }

    const readFeedItems = async () => {
      const raw = await env.INTEL_KV.get("feed");
      return raw ? JSON.parse(raw) : [];
    };

    const sourceOf = (item) => String(item?.source || "").trim().toLowerCase();

    if (url.pathname === "/api/intel" && request.method === "GET") {
      const auth = await requireJwt(request, env);
      if (!auth.ok) {
        return Response.json({ error: auth.error }, { status: auth.status, headers: corsHeaders });
      }

      const items = (await readFeedItems()).map(toLegacyShapeForResponse);
      return Response.json({ items }, { headers: corsHeaders });
    }

    if (url.pathname.startsWith("/api/intel/source/") && request.method === "GET") {
      const auth = await requireJwt(request, env);
      if (!auth.ok) {
        return Response.json({ error: auth.error }, { status: auth.status, headers: corsHeaders });
      }

      const sourceCsv = decodeURIComponent(url.pathname.slice("/api/intel/source/".length)).trim().toLowerCase();
      const sources = Array.from(new Set(sourceCsv.split(",").map((x) => x.trim()).filter(Boolean)));

      if (sources.length === 0) {
        return Response.json({ error: "Missing source" }, { status: 400, headers: corsHeaders });
      }

      const sourceAliases = {
        hyperliquid: ["hyperliquid", "hyperliquid_archive_s3"],
      };
      const expanded = new Set();
      for (const source of sources) {
        const aliases = sourceAliases[source];
        if (Array.isArray(aliases) && aliases.length > 0) {
          aliases.forEach((alias) => expanded.add(alias));
          continue;
        }
        expanded.add(source);
      }

      const sourceSet = expanded;
      const items = (await readFeedItems())
        .filter((it) => sourceSet.has(sourceOf(it)))
        .map(toLegacyShapeForResponse);
      return Response.json({ items }, { headers: corsHeaders });
    }

    if (url.pathname === "/api/ingest" && request.method === "POST") {
      const auth = request.headers.get("Authorization") || "";
      const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
      if (!token || token !== env.INGEST_TOKEN) {
        return new Response("Unauthorized", { status: 401, headers: corsHeaders });
      }

      const body = await request.json().catch(() => null);
      if (!body) return new Response("Bad JSON", { status: 400, headers: corsHeaders });

      // Accept either a single item or {items:[...]} or an array
      const incoming = Array.isArray(body)
        ? body
        : Array.isArray(body.items)
          ? body.items
          : body;

      const now = Date.now();
      const newItems = (Array.isArray(incoming) ? incoming : [incoming])
        .map((x) => toCanonicalEvent(x, now))
        .filter(Boolean);

      const raw = await env.INTEL_KV.get("feed");
      const existing = raw ? JSON.parse(raw) : [];
      const merged = [...newItems, ...existing]
        // de-dupe by canonical id
        .filter((v, i, a) => a.findIndex((x) => x.id === v.id) === i)
        // newest first
        .sort((a, b) => b.ts - a.ts)
        .slice(0, Number(env.MAX_ITEMS || 2000));

      await env.INTEL_KV.put("feed", JSON.stringify(merged));

      return Response.json({ ok: true, added: newItems.length, total: merged.length }, { headers: corsHeaders });
    }

    return new Response("Not Found", { status: 404, headers: corsHeaders });
  },
};
