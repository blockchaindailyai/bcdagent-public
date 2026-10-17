// server.js
require("dotenv").config();

const express = require("express");
const cors = require("cors");
const helmet = require("helmet");
const rateLimit = require("express-rate-limit");
const { z } = require("zod");

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

// deps for gating
const TronWebPkg = require("tronweb");
const TronWeb = TronWebPkg.TronWeb || TronWebPkg.default || TronWebPkg;
const jwt = require("jsonwebtoken");
const crypto = require("crypto");

const app = express();

// ---- Config ----
const PORT = Number(process.env.PORT || 8787);
const JSON_BODY_LIMIT = process.env.JSON_BODY_LIMIT || "2mb";
const API_RATE_LIMIT_PER_MINUTE = Number(process.env.API_RATE_LIMIT_PER_MINUTE || 300);
const INGEST_RATE_LIMIT_PER_MINUTE = Number(process.env.INGEST_RATE_LIMIT_PER_MINUTE || 120);

// ONLY your bot should know this.
// Do NOT put this in the frontend VITE_ env vars.
const INGEST_TOKEN = process.env.INGEST_TOKEN;
if (!INGEST_TOKEN || INGEST_TOKEN.length < 24) {
  console.warn("WARNING: INGEST_TOKEN is missing/weak. Set a strong token (24+ chars).");
}

// CORS allowlist
// Prefer CORS_ORIGIN_ALLOWLIST="https://app.example.com,https://admin.example.com"
// Backward-compat: CORS_ORIGIN="https://app.example.com"
const CORS_ALLOWLIST = (
  process.env.CORS_ORIGIN_ALLOWLIST || process.env.CORS_ORIGIN || "http://localhost:5173"
)
  .split(",")
  .map((x) => x.trim())
  .filter(Boolean);

const CORS_ALLOW_ALL = CORS_ALLOWLIST.includes("*");

if (!CORS_ALLOW_ALL && CORS_ALLOWLIST.length === 0) {
  console.warn("WARNING: No CORS origin configured. Set CORS_ORIGIN_ALLOWLIST.");
}

// Dedupe window: collapse duplicates inside this window; allow repeats later.
const DEDUPE_WINDOW_MS = Number(process.env.DEDUPE_WINDOW_MS || 30 * 60 * 1000); // default 30 minutes

// ---- Persistent storage ----
const REPO_ROOT = path.resolve(__dirname, "..");
const STATE_DIR = process.env.STATE_DIR || __dirname;
const FEED_PATH = process.env.FEED_PATH || path.join(STATE_DIR, "feed.json");
const CHALLENGES_PATH = process.env.CHALLENGES_PATH || path.join(STATE_DIR, "challenges.json");

function envPathOrDefault(envValue, fallbackPath) {
  const raw = String(envValue || "").trim();
  if (!raw) return fallbackPath;

  const rawLower = raw.toLowerCase();
  const normalized = rawLower.replace(/\\/g, "/");
  if (rawLower.includes("path\\to\\") || normalized.includes("path/to/")) {
    console.warn(`WARNING: Ignoring placeholder path value: ${raw}`);
    return fallbackPath;
  }

  return raw;
}

const HYPERLIQUID_ARCHIVE_PARQUET_DIR = envPathOrDefault(
  process.env.HYPERLIQUID_ARCHIVE_PARQUET_DIR,
  path.join(REPO_ROOT, ".data", "hyperliquid_archive", "parquet_wallet_ledger_rewritten")
);
function resolveHyperliquidArchiveParquetDir(rawDir) {
  const configured = path.resolve(rawDir);
  const normalized = configured.replace(/\\/g, "/").toLowerCase();
  const legacySuffix = "/.data/hyperliquid_archive/parquet_wallet_ledger";

  if (!normalized.endsWith(legacySuffix)) {
    return configured;
  }

  const rewritten = path.join(path.dirname(configured), "parquet_wallet_ledger_rewritten");
  if (!fs.existsSync(rewritten) || !fs.statSync(rewritten).isDirectory()) {
    return configured;
  }

  console.warn(
    `[archive-wallet] HYPERLIQUID_ARCHIVE_PARQUET_DIR points to legacy archive (${configured}); ` +
      `auto-switching to rewritten archive (${rewritten}) for wallet queries.`,
  );
  return rewritten;
}
const RESOLVED_HYPERLIQUID_ARCHIVE_PARQUET_DIR = resolveHyperliquidArchiveParquetDir(HYPERLIQUID_ARCHIVE_PARQUET_DIR);
const HYPERLIQUID_ARCHIVE_START_DATE = String(process.env.HYPERLIQUID_ARCHIVE_START_DATE || "20250727");
const HYPERLIQUID_ARCHIVE_QUERY_SCRIPT = envPathOrDefault(
  process.env.HYPERLIQUID_ARCHIVE_QUERY_SCRIPT,
  path.join(REPO_ROOT, "scraper_bot", "scripts", "hyperliquid_archive_wallet_query.py")
);
const HYPERLIQUID_ARCHIVE_QUERY_TIMEOUT_MS = Number(process.env.HYPERLIQUID_ARCHIVE_QUERY_TIMEOUT_MS || 120_000);
const HYPERLIQUID_ARCHIVE_WALLET_BUCKET_COUNT = Number(process.env.HYPERLIQUID_ARCHIVE_WALLET_BUCKET_COUNT || 64);
const HYPERLIQUID_ARCHIVE_WALLET_MAX_TRADES = Number(process.env.HYPERLIQUID_ARCHIVE_WALLET_MAX_TRADES || 5000);
const POLYMARKET_LEADERBOARD_PATH = path.resolve(
  __dirname,
  "..",
  "bcd-treasury-frontend",
  "public",
  "polymarket",
  "top_traders_leaderboard.json",
);
const HYPERLIQUID_LEADERBOARD_PATH = envPathOrDefault(
  process.env.HYPERLIQUID_LEADERBOARD_PATH,
  path.join(process.cwd(), ".data", "hyperliquid_archive", "leaderboard.json")
);

// ---- Auth/Gating config ----
const JWT_SECRET = process.env.JWT_SECRET;
if (!JWT_SECRET || JWT_SECRET.length < 32) {
  console.warn("WARNING: JWT_SECRET is missing/weak. Set a strong secret (32+ chars).");
}
const JWT_TTL_SECONDS = Number(process.env.JWT_TTL_SECONDS || 600); // 10 min default
const CHALLENGE_TTL_SECONDS = Number(process.env.CHALLENGE_TTL_SECONDS || 120); // 2 min default

// Nile Subscription Registry
const SUB_REG_ADDRESS = process.env.SUB_REG_ADDRESS || "TVQreqbN4WJkmE8ekLHV7FY7FxHNL7DFVH";

// Nile endpoints
const TRON_FULLNODE = process.env.TRON_FULLNODE || "https://nile.trongrid.io";
const TRON_SOLIDITYNODE = process.env.TRON_SOLIDITYNODE || "https://nile.trongrid.io";
const TRON_EVENTSERVER = process.env.TRON_EVENTSERVER || "https://nile.trongrid.io";

// Owner address for TronWeb reads (required)
const TRON_OWNER_ADDRESS = process.env.TRON_OWNER_ADDRESS || "TAsC4FkCSSo4LeDCzp8yuATEGLZDAoLCY6";

let tronWeb;
try {
  tronWeb = new TronWeb({
    fullHost: TRON_FULLNODE,
    solidityNode: TRON_SOLIDITYNODE,
    eventServer: TRON_EVENTSERVER,
  });
} catch (e) {
  tronWeb = new TronWeb(TRON_FULLNODE, TRON_SOLIDITYNODE, TRON_EVENTSERVER);
}

if (!tronWeb.isAddress(TRON_OWNER_ADDRESS)) {
  console.warn("WARNING: TRON_OWNER_ADDRESS is invalid:", TRON_OWNER_ADDRESS);
} else {
  tronWeb.setAddress(TRON_OWNER_ADDRESS);
  tronWeb.defaultAddress = tronWeb.defaultAddress || {};
  tronWeb.defaultAddress.base58 = TRON_OWNER_ADDRESS;
  tronWeb.defaultAddress.hex = tronWeb.address.toHex(TRON_OWNER_ADDRESS);
}

// ---- Minimal ABI: SubscriptionRegistryTRXV3 ----
const SubscriptionRegistryTRXV3_ABI = [
  { "inputs":[{"internalType":"address","name":"_owner","type":"address"},{"internalType":"address payable","name":"_treasury","type":"address"},{"internalType":"uint256","name":"_trxUsdPriceE6","type":"uint256"}],"stateMutability":"nonpayable","type":"constructor" },
  { "stateMutability":"payable","type":"receive" },
  { "inputs":[{"internalType":"address","name":"user","type":"address"}],"name":"isActive","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function" },
  { "inputs":[{"internalType":"address","name":"user","type":"address"}],"name":"accessUntil","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function" },
];

function ensureParentDir(filePath) {
  const parent = path.dirname(filePath);
  if (!fs.existsSync(parent)) fs.mkdirSync(parent, { recursive: true });
}

function readJsonFile(filePath, fallback) {
  try {
    if (!fs.existsSync(filePath)) return fallback;
    const raw = fs.readFileSync(filePath, "utf8");
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function writeJsonFileAtomic(filePath, data) {
  try {
    ensureParentDir(filePath);
    const tmp = `${filePath}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf8");
    fs.renameSync(tmp, filePath);
    return true;
  } catch (e) {
    console.error(`Failed to persist ${path.basename(filePath)}:`, e?.message || e);
    return false;
  }
}

class ChallengeStore {
  constructor(filePath) {
    this.filePath = filePath;
    this.map = new Map();
    this.load();
  }

  load() {
    const raw = readJsonFile(this.filePath, []);
    if (!Array.isArray(raw)) return;

    for (const item of raw) {
      if (!item || typeof item !== "object") continue;
      const addr = typeof item.addr === "string" ? item.addr.trim() : "";
      const message = typeof item.message === "string" ? item.message : "";
      const expiresAtMs = Number(item.expiresAtMs || 0);
      if (!addr || !message || !Number.isFinite(expiresAtMs) || expiresAtMs <= Date.now()) continue;
      this.map.set(addr, { message, expiresAtMs });
    }
  }

  persist() {
    const items = [];
    for (const [addr, c] of this.map.entries()) {
      items.push({ addr, message: c.message, expiresAtMs: c.expiresAtMs });
    }
    writeJsonFileAtomic(this.filePath, items);
  }

  set(addr, challenge) {
    this.map.set(addr, challenge);
    this.persist();
  }

  get(addr) {
    return this.map.get(addr);
  }

  delete(addr) {
    const deleted = this.map.delete(addr);
    if (deleted) this.persist();
    return deleted;
  }

  pruneExpired(now = Date.now()) {
    let changed = false;
    for (const [addr, c] of this.map.entries()) {
      if (!c || c.expiresAtMs <= now) {
        this.map.delete(addr);
        changed = true;
      }
    }
    if (changed) this.persist();
  }
}

const challenges = new ChallengeStore(CHALLENGES_PATH);

function makeNonce() {
  return crypto.randomBytes(16).toString("hex");
}

function isValidTronAddr(addr) {
  try {
    return tronWeb.isAddress(addr);
  } catch {
    return false;
  }
}

function normalizeTs(inputTs, now) {
  let ts = typeof inputTs === "number" ? inputTs : now;

  // seconds -> ms
  if (ts > 0 && ts < 10_000_000_000) ts = ts * 1000;

  // clamp silly future
  if (ts > now + 24 * 60 * 60 * 1000) ts = now;
  if (ts <= 0) ts = now;

  return ts;
}

function buildChallengeMessage(addr, nonce, issuedAtIso) {
  return [
    "Blockchain Daily Intel Login",
    `addr: ${addr}`,
    `nonce: ${nonce}`,
    `issuedAt: ${issuedAtIso}`,
    "chain: nile"
  ].join("\n");
}

function toBool(res) {
  if (res === true || res === "true") return true;
  if (res?.[0] === true || res?.[0] === "true") return true;
  if (typeof res?.toString === "function" && res.toString() === "true") return true;
  return false;
}

async function getSubscriptionStatus(addr) {
  const c = await tronWeb.contract(SubscriptionRegistryTRXV3_ABI, SUB_REG_ADDRESS);

  // accessUntil is uint256 "seconds since epoch" in your contract
  const activeRaw = await c.isActive(addr).call();
  const untilRaw = await c.accessUntil(addr).call();

  const active = toBool(activeRaw);

  // untilRaw can be BN-ish or string-ish; normalize to Number seconds
  const untilSec = Number(untilRaw?.toString ? untilRaw.toString() : untilRaw);
  const untilMs = Number.isFinite(untilSec) && untilSec > 0 ? untilSec * 1000 : 0;

  return { active, untilMs };
}

function requireJwt(req, res, next) {
  const auth = req.header("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";

  if (!token) return res.status(401).json({ error: "Missing token" });
  if (!JWT_SECRET) return res.status(500).json({ error: "Server missing JWT_SECRET" });

  try {
    const payload = jwt.verify(token, JWT_SECRET);
    req.user = payload; // { addr, untilMs }
    next();
  } catch {
    return res.status(401).json({ error: "Invalid/expired token" });
  }
}

// cleanup stale challenges
setInterval(() => {
  challenges.pruneExpired();
}, 30_000);

// ---- Feed IO ----
function loadFeed() {
  const parsed = readJsonFile(FEED_PATH, []);
  return Array.isArray(parsed) ? parsed : [];
}

function saveFeed(items) {
  writeJsonFileAtomic(FEED_PATH, items);
}

// ---- Middleware ----
app.use(helmet({ crossOriginResourcePolicy: false }));

app.use(
  cors({
    origin(origin, cb) {
      // Allow non-browser/server-to-server requests with no Origin.
      if (!origin) return cb(null, true);
      if (CORS_ALLOW_ALL || CORS_ALLOWLIST.includes(origin)) return cb(null, true);
      return cb(null, false);
    },
    methods: ["GET", "POST", "OPTIONS"],
    allowedHeaders: ["Content-Type", "Authorization", "Cache-Control", "Pragma"],
  })
);

app.use((req, res, next) => {
  const origin = req.header("origin") || "";
  if (!origin) return next();
  if (CORS_ALLOW_ALL || CORS_ALLOWLIST.includes(origin)) return next();
  return res.status(403).json({ error: "CORS blocked: origin not allowed" });
});

app.use(express.json({ limit: JSON_BODY_LIMIT }));

app.use(
  rateLimit({
    windowMs: 60 * 1000,
    limit: API_RATE_LIMIT_PER_MINUTE,
    standardHeaders: true,
    legacyHeaders: false,
  })
);

const ingestLimiter = rateLimit({
  windowMs: 60 * 1000,
  limit: INGEST_RATE_LIMIT_PER_MINUTE,
  standardHeaders: true,
  legacyHeaders: false,
});

// ---- Store ----
let feed = loadFeed();
const MAX_ITEMS = Number(process.env.MAX_ITEMS || 2000);

// ---- Auth helper (ingest) ----
function requireIngestAuth(req, res, next) {
  const auth = req.header("authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!INGEST_TOKEN) return res.status(500).send("Server missing INGEST_TOKEN");
  if (!token || token !== INGEST_TOKEN) return res.status(401).send("Unauthorized");
  next();
}

// ============================================================
//  NEW OBJECT MODEL (v1) + BACKWARD COMPAT INGEST
// ============================================================

// Allowed classification fields
const AssetClassEnum = z.enum([
  "general",
  "crypto",
  "stocks",
  "bonds",
  "commodities",
  "fx",
  "rates",
  "credit",
]);

const IntelTypeEnum = z.enum([
  "news",
  "signal",
  "macro",
  "onchain",
  "treasury",
  "system",
]);

const ClassificationSchema = z.object({
  priority: z.enum(["normal", "breaking"]).optional(),
  assetClass: AssetClassEnum.optional(),
  topics: z.array(z.string().max(40)).max(20).optional(),
  tickers: z.array(z.string().max(20)).max(50).optional(),  // store WITHOUT '$'
  regions: z.array(z.string().max(10)).max(20).optional(),
}).optional();

// V1 payload is flexible for now (we’ll harden later per-type)
const IntelEventV1Schema = z.object({
  id: z.string().max(240).optional(),
  ts: z.number().int().optional(),
  source: z.string().max(120).optional(),

  type: IntelTypeEnum.optional(),      // default: "news"
  title: z.string().min(1).max(280),   // canonical headline
  summary: z.string().max(4000).optional(),
  url: z.string().url().max(2048).optional(),
  tags: z.array(z.string().max(40)).max(30).optional(),

  severity: z.enum(["info", "watch", "urgent"]).optional(),
  chain: z.enum(["nile", "mainnet", "none"]).optional(),

  classification: ClassificationSchema,

  data: z.record(z.unknown()).optional(),  // typed union later
});

// Legacy schema (what your twitterbot sends today)
const LegacyIntelItemSchema = z.object({
  id: z.string().optional(),
  ts: z.number().int().optional(),
  source: z.string().max(120).optional(),
  headline: z.string().min(1).max(280),
  body: z.string().max(4000).optional(),
  url: z.string().url().max(2048).optional(),
  tags: z.array(z.string().max(40)).max(30).optional(),
});

// Accept either v1 or legacy
const IncomingItemSchema = z.union([IntelEventV1Schema, LegacyIntelItemSchema]);

// Accept: single item, array, or {items:[...]}
function normalizeIncoming(body) {
  if (Array.isArray(body)) return body;
  if (body && Array.isArray(body.items)) return body.items;
  return [body];
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

function normalizeSource(source) {
  return String(source || "").trim().toLowerCase();
}

function sourceAllowsFrequentUpdates(source) {
  return normalizeSource(source) === "polymarket";
}

// Dedupe by URL if present; else by canonical title/headline
function dedupeKeyFromNormalized({ source, url, title, data }) {
  const normalizedSource = normalizeSource(source);

  if (normalizedSource === "polymarket") {
    const marketId = String(data?.marketId || "").trim();
    if (marketId) return `polymarket:market:${marketId}`;
  }

  const u = normalizeUrl(url);
  if (u) return `url:${u}`;
  return `t:${normalizeHeadline(title)}`;
}

// Build a stable server id when caller doesn't provide one
function safeId({ id, ts, url, title }) {
  if (id) return id;
  const k = dedupeKeyFromNormalized({ url, title }).slice(0, 180);
  const bucket = Math.floor((ts || Date.now()) / 1000); // seconds bucket
  return `${k}:${bucket}`;
}

// Normalize incoming object into canonical stored event (v1)
function toCanonicalEvent(raw, now) {
  // Determine if legacy
  const isLegacy = Object.prototype.hasOwnProperty.call(raw, "headline");

  const ts = normalizeTs(raw.ts, now);

  const title = isLegacy ? raw.headline : raw.title;
  const summary = isLegacy ? (raw.body || "") : (raw.summary || "");
  const url = raw.url || "";
  const tags = raw.tags || [];

  // Default type for legacy is news
  const type = isLegacy ? "news" : (raw.type || "news");

  // Apply breaking inference (optional):
  // if tags include "breaking" or "🚨" and classification.priority not set => breaking
  const classification = raw.classification ? { ...raw.classification } : undefined;
  const hasBreakingTag =
    Array.isArray(tags) &&
    tags.some((t) => String(t).toLowerCase().includes("breaking") || String(t).includes("🚨"));

  const nextClassification = classification || {};
  if (!nextClassification.priority && hasBreakingTag) nextClassification.priority = "breaking";

  // Canonical event
  const ev = {
    id: safeId({ id: raw.id, ts, url, title }),
    ts,
    source: raw.source || "twitterbot",
    type,
    title,
    summary,
    url,
    tags,
    severity: raw.severity,
    chain: raw.chain,
    classification: Object.keys(nextClassification).length ? nextClassification : undefined,
    data: raw.data && typeof raw.data === "object" ? raw.data : {},
    meta: {
      ingestTs: now,
      version: 1,
    },
  };

  // Ensure dedupe key is consistent (computed from canonical)
  ev.meta.dedupeKey = dedupeKeyFromNormalized({ source: ev.source, url: ev.url, title: ev.title, data: ev.data });

  return ev;
}

// When serving to old UI, include headline/body aliases
function toLegacyShapeForResponse(ev) {
  return {
    ...ev,
    headline: ev.title,
    body: ev.summary || "",
  };
}

function asFiniteNumber(value) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n : 0;
}

function normalizeLeaderboardRow(raw, idx) {
  const notionalUsd = Math.abs(asFiniteNumber(raw?.notionalUsd));
  const netProfitUsd = asFiniteNumber(raw?.netProfitUsd);
  const realizedPnlUsd = asFiniteNumber(raw?.realizedPnlUsd);
  const feesUsd = asFiniteNumber(raw?.feesUsd);
  const pnlPercent = notionalUsd > 0 ? (netProfitUsd / notionalUsd) * 100 : 0;
  return {
    rank: idx + 1,
    address: String(raw?.address || "").trim().toLowerCase(),
    notionalUsd,
    netProfitUsd,
    realizedPnlUsd,
    feesUsd,
    pnlPercent,
  };
}

function normalizeLeaderboardWindows(payload) {
  const byWindowRaw = payload?.topAccountsByWindow;
  const byWindow = {};

  if (byWindowRaw && typeof byWindowRaw === "object" && !Array.isArray(byWindowRaw)) {
    for (const [windowKey, rows] of Object.entries(byWindowRaw)) {
      if (!Array.isArray(rows)) continue;
      const normalizedRows = rows
        .map((row, idx) => normalizeLeaderboardRow(row, idx))
        .filter((row) => row.address.length > 0);
      if (normalizedRows.length > 0) byWindow[String(windowKey)] = normalizedRows;
    }
  }

  if (Object.keys(byWindow).length === 0) {
    const fallbackRows = Array.isArray(payload?.topAccounts) ? payload.topAccounts : [];
    byWindow.all = fallbackRows
      .map((row, idx) => normalizeLeaderboardRow(row, idx))
      .filter((row) => row.address.length > 0);
  }

  return byWindow;
}

function sortLeaderboardRows(rows, sortKey) {
  const validSort = new Set(["volume", "profit", "pnlPercent"]);
  const key = validSort.has(sortKey) ? sortKey : "volume";
  const sorted = [...rows].sort((a, b) => {
    const aPrimary = key === "profit" ? a.netProfitUsd : key === "pnlPercent" ? a.pnlPercent : a.notionalUsd;
    const bPrimary = key === "profit" ? b.netProfitUsd : key === "pnlPercent" ? b.pnlPercent : b.notionalUsd;
    if (bPrimary !== aPrimary) return bPrimary - aPrimary;
    if (b.notionalUsd !== a.notionalUsd) return b.notionalUsd - a.notionalUsd;
    return a.address.localeCompare(b.address);
  });

  return sorted.map((row, idx) => ({ ...row, rank: idx + 1 }));
}

// ============================================================
//  ROUTES
// ============================================================

app.get("/api/health", (req, res) => {
  res.json({
    ok: true,
    ts: Date.now(),
    count: feed.length,
    dedupeWindowMs: DEDUPE_WINDOW_MS,
    chain: "nile",
    subReg: SUB_REG_ADDRESS,
  });
});

app.get("/api/polymarket/top_traders_leaderboard", requireJwt, (req, res) => {
  if (!fs.existsSync(POLYMARKET_LEADERBOARD_PATH)) {
    return res.status(404).json({
      error: "Leaderboard snapshot not found",
      path: POLYMARKET_LEADERBOARD_PATH,
    });
  }

  const payload = readJsonFile(POLYMARKET_LEADERBOARD_PATH, null);
  if (!payload || typeof payload !== "object") {
    return res.status(500).json({ error: "Leaderboard snapshot is invalid JSON" });
  }

  res.set("Cache-Control", "no-store, no-cache, must-revalidate");
  res.set("Pragma", "no-cache");
  return res.json(payload);
});

app.get("/api/hyperliquid/leaderboard", requireJwt, (req, res) => {
  const untilMs = Number(req.user?.untilMs || 0);
  if (Number.isFinite(untilMs) && untilMs > 0 && Date.now() > untilMs) {
    return res.status(403).send("Subscription expired");
  }

  if (!fs.existsSync(HYPERLIQUID_LEADERBOARD_PATH)) {
    return res.status(404).json({
      error: "Hyperliquid leaderboard snapshot not found",
      path: HYPERLIQUID_LEADERBOARD_PATH,
    });
  }

  const payload = readJsonFile(HYPERLIQUID_LEADERBOARD_PATH, null);
  if (!payload || typeof payload !== "object") {
    return res.status(500).json({ error: "Hyperliquid leaderboard snapshot is invalid JSON" });
  }

  const byWindow = normalizeLeaderboardWindows(payload);
  const configuredWindows = Array.isArray(payload.configuredWindows)
    ? payload.configuredWindows.map((w) => String(w)).filter(Boolean)
    : [];
  const availableWindows = Array.from(new Set([...configuredWindows, ...Object.keys(byWindow)]));
  const requestedWindow = String(req.query?.timeframe || req.query?.window || "all");
  const selectedWindow = byWindow[requestedWindow]
    ? requestedWindow
    : availableWindows.includes("all")
      ? "all"
      : availableWindows[0] || "all";

  const sortKey = String(req.query?.sort || "volume");
  const selectedRows = Array.isArray(byWindow[selectedWindow]) ? byWindow[selectedWindow] : [];
  const sortedRows = sortLeaderboardRows(selectedRows, sortKey);

  res.set("Cache-Control", "no-store, no-cache, must-revalidate");
  res.set("Pragma", "no-cache");
  return res.json({
    generatedAtTs: asFiniteNumber(payload.generatedAtTs),
    source: String(payload.source || "hyperliquid_archive_file"),
    path: HYPERLIQUID_LEADERBOARD_PATH,
    configuredWindows: availableWindows,
    selectedWindow,
    sort: ["volume", "profit", "pnlPercent"].includes(sortKey) ? sortKey : "volume",
    topAccountsByWindow: byWindow,
    topAccounts: sortedRows,
  });
});

// ---- Challenge route ----
app.get("/api/challenge", (req, res) => {
  const addr = String(req.query.addr || "").trim();
  if (!isValidTronAddr(addr)) return res.status(400).json({ error: "Invalid address" });

  const nonce = makeNonce();
  const issuedAtIso = new Date().toISOString();
  const message = buildChallengeMessage(addr, nonce, issuedAtIso);

  challenges.set(addr, {
    message,
    expiresAtMs: Date.now() + CHALLENGE_TTL_SECONDS * 1000,
  });

  res.json({
    addr,
    message,
    expiresInSeconds: CHALLENGE_TTL_SECONDS,
  });
});

// ---- Auth route (Option 3: token expires at min(JWT ttl, subscription expiry)) ----
app.post("/api/auth", async (req, res) => {
  try {
    const addr = String(req.body?.addr || "").trim();
    const signature = String(req.body?.signature || "").trim();

    if (!isValidTronAddr(addr)) return res.status(400).json({ error: "Invalid address" });
    if (!signature) return res.status(400).json({ error: "Missing signature" });

    const ch = challenges.get(addr);
    if (!ch) return res.status(400).json({ error: "No active challenge" });

    if (Date.now() > ch.expiresAtMs) {
      challenges.delete(addr);
      return res.status(400).json({ error: "Challenge expired" });
    }

    const message = ch.message;

    // Verify signature (TronLink compatibility: try V2 then legacy)
    let ok = false;
    try {
      ok = await tronWeb.trx.verifyMessageV2(message, signature, addr);
    } catch {}
    if (!ok) {
      try {
        ok = await tronWeb.trx.verifyMessage(message, signature, addr);
      } catch {}
    }
    if (!ok) return res.status(401).json({ error: "Bad signature" });

    // One-time nonce
    challenges.delete(addr);

    // Subscription check
    const { active, untilMs } = await getSubscriptionStatus(addr);
    if (!active || !untilMs || untilMs <= Date.now()) {
      return res.status(403).json({ error: "Not subscribed" });
    }

    if (!JWT_SECRET) return res.status(500).json({ error: "Server missing JWT_SECRET" });

    // Option 3: token should not outlive subscription
    const ttlMs = JWT_TTL_SECONDS * 1000;
    const maxExpMs = Date.now() + ttlMs;
    const effExpMs = Math.min(maxExpMs, untilMs);
    const effExpSeconds = Math.max(1, Math.floor((effExpMs - Date.now()) / 1000));

    const token = jwt.sign({ addr, untilMs }, JWT_SECRET, { expiresIn: effExpSeconds });

    res.json({
      ok: true,
      token,
      expiresInSeconds: effExpSeconds,
      untilMs, // IMPORTANT for frontend option 3
    });
  } catch (e) {
    console.error("auth error:", e?.message || e);
    res.status(500).json({ error: "Auth failed" });
  }
});

// ---- GATED: intel requires JWT ----
function sourceOf(item) {
  return String(item?.source || "").trim().toLowerCase();
}

function parseSourceCsv(value) {
  return Array.from(
    new Set(
      String(value || "")
        .split(",")
        .map((x) => x.trim().toLowerCase())
        .filter(Boolean)
    )
  );
}

function filterBySource(items, sourceCsv) {
  const targets = parseSourceCsv(sourceCsv);
  if (targets.length === 0) return [];

  const sourceAliases = {
    hyperliquid: ["hyperliquid", "hyperliquid_archive_s3"],
  };

  const expandedTargets = new Set();
  for (const target of targets) {
    const aliases = sourceAliases[target];
    if (Array.isArray(aliases) && aliases.length > 0) {
      aliases.forEach((alias) => expandedTargets.add(alias));
      continue;
    }
    expandedTargets.add(target);
  }

  const set = expandedTargets;
  return items.filter((it) => set.has(sourceOf(it)));
}

function sendIntelItems(res, items) {
  res.json({ items: items.map(toLegacyShapeForResponse) });
}

function normalizeWalletAddress(value) {
  return String(value || "").trim().toLowerCase();
}

function previewText(value, maxLen = 4000) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  if (raw.length <= maxLen) return raw;
  return `${raw.slice(0, maxLen)}... [truncated ${raw.length - maxLen} chars]`;
}

function safeTailLines(value, maxLines = 25, maxLen = 6000) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const lines = raw.split(/\r?\n/);
  const tail = lines.slice(-Math.max(1, maxLines)).join("\n");
  return previewText(tail, maxLen);
}

function queryArchiveWalletDailyPnl(wallet) {
  return new Promise((resolve, reject) => {
    const walletNorm = normalizeWalletAddress(wallet);
    if (!walletNorm || walletNorm.length < 6) {
      reject(new Error("Invalid wallet address"));
      return;
    }

    const args = [
      HYPERLIQUID_ARCHIVE_QUERY_SCRIPT,
      "--parquet-dir",
      RESOLVED_HYPERLIQUID_ARCHIVE_PARQUET_DIR,
      "--wallet",
      walletNorm,
      "--start-date",
      HYPERLIQUID_ARCHIVE_START_DATE,
      "--wallet-bucket-count",
      String(Math.max(1, HYPERLIQUID_ARCHIVE_WALLET_BUCKET_COUNT || 64)),
      "--max-trades",
      String(Math.max(0, HYPERLIQUID_ARCHIVE_WALLET_MAX_TRADES || 0)),
    ];

    const pythonCmd = process.env.PYTHON || process.env.PYTHON_PATH || "python";
    const startedAtMs = Date.now();
    const child = spawn(pythonCmd, args, { stdio: ["ignore", "pipe", "pipe"] });
    const stdoutChunks = [];
    const stderrChunks = [];
    const invocation = {
      wallet: walletNorm,
      pythonCmd,
      args,
      script: HYPERLIQUID_ARCHIVE_QUERY_SCRIPT,
      parquetDir: RESOLVED_HYPERLIQUID_ARCHIVE_PARQUET_DIR,
      startDate: HYPERLIQUID_ARCHIVE_START_DATE,
      timeoutMs: HYPERLIQUID_ARCHIVE_QUERY_TIMEOUT_MS,
      pid: child.pid,
      startedAtIso: new Date(startedAtMs).toISOString(),
    };

    const timeout = setTimeout(() => {
      child.kill("SIGKILL");
      const elapsedMs = Date.now() - startedAtMs;
      const stderrText = Buffer.concat(stderrChunks).toString("utf8");
      const stdoutText = Buffer.concat(stdoutChunks).toString("utf8");
      const timeoutErr = new Error("Archive query timed out");
      timeoutErr.details = {
        ...invocation,
        elapsedMs,
        stderrTail: safeTailLines(stderrText),
        stdoutPreview: previewText(stdoutText),
      };
      console.error("[archive-wallet] timeout", timeoutErr.details);
      reject(timeoutErr);
    }, HYPERLIQUID_ARCHIVE_QUERY_TIMEOUT_MS);

    child.stdout.on("data", (chunk) => stdoutChunks.push(chunk));
    child.stderr.on("data", (chunk) => stderrChunks.push(chunk));
    child.on("error", (err) => {
      clearTimeout(timeout);
      const elapsedMs = Date.now() - startedAtMs;
      err.details = {
        ...invocation,
        elapsedMs,
        spawnError: err?.message || String(err),
      };
      console.error("[archive-wallet] spawn failed", err.details);
      reject(err);
    });
    child.on("close", (code, signal) => {
      clearTimeout(timeout);
      const elapsedMs = Date.now() - startedAtMs;
      const stdoutText = Buffer.concat(stdoutChunks).toString("utf8").trim();
      const stderrText = Buffer.concat(stderrChunks).toString("utf8").trim();
      let parsed = null;
      if (stdoutText) {
        try {
          parsed = JSON.parse(stdoutText);
        } catch {
          parsed = null;
        }
      }

      const completionDetails = {
        ...invocation,
        elapsedMs,
        exitCode: code,
        signal: signal || null,
        stdoutBytes: Buffer.byteLength(stdoutText || "", "utf8"),
        stderrBytes: Buffer.byteLength(stderrText || "", "utf8"),
      };

      if (code !== 0) {
        const exitErr = new Error(parsed?.error || stderrText || stdoutText || `Archive query failed (${code})`);
        exitErr.details = {
          ...completionDetails,
          stderrTail: safeTailLines(stderrText),
          stdoutPreview: previewText(stdoutText),
          scriptErrors: Array.isArray(parsed?.errors) ? parsed.errors : [],
        };
        console.error("[archive-wallet] script non-zero exit", exitErr.details);
        reject(exitErr);
        return;
      }

      try {
        const parsedResult = parsed || JSON.parse(stdoutText || "{}");
        const rows = Array.isArray(parsedResult?.rows) ? parsedResult.rows : [];
        const tradeRows = Array.isArray(parsedResult?.tradeRows) ? parsedResult.tradeRows : [];
        const errors = Array.isArray(parsedResult?.errors) ? parsedResult.errors : [];

        if (errors.length > 0) {
          console.warn("[archive-wallet] script returned errors with zero exit", {
            ...completionDetails,
            scriptErrors: errors,
            stderrTail: safeTailLines(stderrText),
          });
        }

        resolve({ wallet: walletNorm, rows, tradeRows, errors });
      } catch (err) {
        const parseErr = new Error(`Archive query returned invalid JSON: ${err?.message || err}`);
        parseErr.details = {
          ...completionDetails,
          stderrTail: safeTailLines(stderrText),
          stdoutPreview: previewText(stdoutText),
        };
        console.error("[archive-wallet] parse failure", parseErr.details);
        reject(parseErr);
      }
    });
  });
}


app.get("/api/intel", requireJwt, (req, res) => {
  // Hard block if subscription expired even if JWT exists (belt + suspenders)
  const untilMs = Number(req.user?.untilMs || 0);
  if (Number.isFinite(untilMs) && untilMs > 0 && Date.now() > untilMs) {
    return res.status(403).send("Subscription expired");
  }

  // Return legacy aliases too so current UI continues working
  return sendIntelItems(res, feed);
});

app.get("/api/intel/source/:source", requireJwt, (req, res) => {
  const untilMs = Number(req.user?.untilMs || 0);
  if (Number.isFinite(untilMs) && untilMs > 0 && Date.now() > untilMs) {
    return res.status(403).send("Subscription expired");
  }

  const source = String(req.params?.source || "").trim();
  if (!source) return res.status(400).json({ error: "Missing source" });

  return sendIntelItems(res, filterBySource(feed, source));
});

app.get("/api/hyperliquid/archive/wallet/:wallet", requireJwt, async (req, res) => {
  const untilMs = Number(req.user?.untilMs || 0);
  if (Number.isFinite(untilMs) && untilMs > 0 && Date.now() > untilMs) {
    return res.status(403).send("Subscription expired");
  }

  const wallet = normalizeWalletAddress(req.params?.wallet || "");
  if (!wallet) return res.status(400).json({ error: "Missing wallet" });

  try {
    const payload = await queryArchiveWalletDailyPnl(wallet);
    if (Array.isArray(payload?.errors) && payload.errors.length > 0) {
      console.warn("[archive-wallet] script returned query warnings", {
        wallet,
        errors: payload.errors,
      });
    }
    return res.json(payload);
  } catch (err) {
    const diagnostics = {
      wallet,
      message: err?.message || "Archive wallet query failed",
      details: err?.details || null,
    };
    console.error("[archive-wallet] query failed", diagnostics);
    return res.status(200).json({
      wallet,
      rows: [],
      tradeRows: [],
      error: diagnostics.message,
      diagnostics,
    });
  }
});

// (Optional) debug endpoint
app.get("/api/debug/sub", async (req, res) => {
  const addr = String(req.query.addr || "").trim();
  if (!isValidTronAddr(addr)) return res.status(400).json({ error: "Invalid address" });

  try {
    const s = await getSubscriptionStatus(addr);
    res.json({ addr, ...s, subReg: SUB_REG_ADDRESS, chain: "nile" });
  } catch (e) {
    res.status(500).json({ error: e?.message || String(e) });
  }
});

// ---- INGEST ----
app.post("/api/ingest", ingestLimiter, requireIngestAuth, (req, res) => {
  const incoming = normalizeIncoming(req.body);
  if (incoming.length > 50) return res.status(400).send("Too many items");

  const now = Date.now();

  // map existing items to latest ts per dedupe key (prevents "refreshing" duplicates)
  const existingLatestTsByKey = new Map();
  for (const x of feed) {
    const title = x.title || x.headline || "";
    const url = x.url || "";
    const k = dedupeKeyFromNormalized({ source: x.source, url, title, data: x.data });
    const ts = typeof x.ts === "number" ? x.ts : 0;
    const prev = existingLatestTsByKey.get(k);
    if (typeof prev !== "number" || ts > prev) existingLatestTsByKey.set(k, ts);
  }

  const accepted = [];
  let skippedAsDup = 0;

  for (const raw of incoming) {
    if (!raw || typeof raw !== "object") {
      return res.status(400).json({ error: "Bad item shape (not an object)" });
    }

    // legacy vs v1 detection
    const isLegacy = Object.prototype.hasOwnProperty.call(raw, "headline");
    const isV1 = Object.prototype.hasOwnProperty.call(raw, "title");

    if (!isLegacy && !isV1) {
      return res.status(400).json({ error: "Bad item shape (missing headline/title)" });
    }

    // minimal required fields
    const title = isLegacy ? String(raw.headline || "").trim() : String(raw.title || "").trim();
    if (!title) return res.status(400).json({ error: "Missing headline/title" });

    const summary = isLegacy ? String(raw.body || "") : String(raw.summary || "");
    const url = String(raw.url || "");
    const source = String(raw.source || "twitterbot");
    const tags = Array.isArray(raw.tags) ? raw.tags.map((t) => String(t)).slice(0, 30) : [];

    const ts = normalizeTs(raw.ts, now);

    // canonical event
    const ev = {
      id: safeId({ id: raw.id, ts, url, title }),
      ts,
      source,
      type: String(raw.type || (isLegacy ? "news" : "news")),
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
      },
    };

    ev.meta.dedupeKey = dedupeKeyFromNormalized({ source: ev.source, url: ev.url, title: ev.title, data: ev.data });

    const k = ev.meta.dedupeKey;

    // DROP incoming duplicates within window (do not move existing items to top)
    const existingTs = existingLatestTsByKey.get(k);
    if (!sourceAllowsFrequentUpdates(ev.source) && typeof existingTs === "number" && (now - existingTs) < DEDUPE_WINDOW_MS) {
      skippedAsDup++;
      continue;
    }

    existingLatestTsByKey.set(k, ev.ts);
    accepted.push(ev);
  }

  // Merge + sort newest first
  const mergedSorted = [...accepted, ...feed].sort((a, b) => (b.ts || 0) - (a.ts || 0));

  // window dedupe (newest wins)
  const lastKeptTsByKey = new Map();
  const kept = [];

  for (const it of mergedSorted) {
    const title = it.title || it.headline || "";
    const url = it.url || "";
    const k = dedupeKeyFromNormalized({ source: it.source, url, title, data: it.data });

    const lastKept = lastKeptTsByKey.get(k);
    if (!sourceAllowsFrequentUpdates(it.source) && typeof lastKept === "number" && (lastKept - it.ts) < DEDUPE_WINDOW_MS) continue;

    lastKeptTsByKey.set(k, it.ts);
    kept.push(it);
    if (kept.length >= MAX_ITEMS) break;
  }

  feed = kept;
  saveFeed(feed);

  res.json({ ok: true, added: accepted.length, skippedAsDup, total: feed.length });
});

app.use((err, req, res, next) => {
  if (err?.type === "entity.too.large") {
    return res.status(413).json({ error: "Payload too large" });
  }
  return next(err);
});

// ---- Start ----
app.listen(PORT, () => {
  console.log(`Intel API listening on http://localhost:${PORT}`);
  console.log(`JSON body limit: ${JSON_BODY_LIMIT}`);
  console.log(`Global rate limit: ${API_RATE_LIMIT_PER_MINUTE}/min`);
  console.log(`Ingest rate limit: ${INGEST_RATE_LIMIT_PER_MINUTE}/min`);
});
