import { useEffect, useMemo, useRef, useState } from "react";

type IntelItem = {
  id?: string;
  ts: number;
  source?: string;
  headline: string;
  body?: string;
  url?: string;
  tags?: string[];
  data?: Record<string, unknown>;
};

type EnrichedMarket = {
  id: string;
  question: string;
  slug: string;
  url: string;
  volume: number;
  topOutcomeLabel: string;
  topOutcomeProbability: number;
  outcomes: Array<{ label: string; probability: number }>;
  category: string;
  resolutionDate: string;
  resolutionDateMs: number;
  groupKey: string;
  groupLabel: string;
};

type TopTraderRow = {
  rank: number;
  name: string;
  xUsername: string;
  proxyWallet: string;
  pnl: number;
  pnlPercent: number;
  volume: number;
};

type TopTradersSnapshot = {
  pageUrl?: string;
  scrapedAt?: string;
  category?: string;
  time?: string;
  sort?: "profit" | "volume" | "pnlPercent";
  rowCount?: number;
  rows: TopTraderRow[];
  availableTimes?: string[];
  snapshots?: Record<string, TopTradersSnapshot>;
};


const POLYMARKET_CATEGORIES = [
  "Politics",
  "Sports",
  "Crypto",
  "Iran",
  "Finance",
  "Geopolitics",
  "Tech",
  "Culture",
  "Economy",
  "Climate & Science",
  "Mentions",
  "Elections",
] as const;

const JWT_KEY = "bcd_intel_jwt";
const JWT_EXP_KEY = "bcd_intel_jwt_exp_ms";
const JWT_UNTIL_KEY = "bcd_intel_sub_until_ms";

function setStoredJwt(token: string, expiresInSeconds: number, untilMs?: number) {
  const jwtExpMs = Date.now() + Math.max(1, expiresInSeconds) * 1000;
  const u = Number(untilMs || 0);
  const effExpMs = Number.isFinite(u) && u > 0 ? Math.min(jwtExpMs, u) : jwtExpMs;

  sessionStorage.setItem(JWT_KEY, token);
  sessionStorage.setItem(JWT_EXP_KEY, String(effExpMs));
  if (Number.isFinite(u) && u > 0) sessionStorage.setItem(JWT_UNTIL_KEY, String(u));
}

function getStoredJwt(): { token: string; expMs: number } | null {
  const t = sessionStorage.getItem(JWT_KEY) || "";
  const e = Number(sessionStorage.getItem(JWT_EXP_KEY) || "0");

  if (!t || !Number.isFinite(e) || e <= 0) return null;
  return { token: t, expMs: e };
}

function clearStoredJwt() {
  sessionStorage.removeItem(JWT_KEY);
  sessionStorage.removeItem(JWT_EXP_KEY);
  sessionStorage.removeItem(JWT_UNTIL_KEY);
}

function isJwtFresh(expMs: number) {
  return Date.now() < expMs - 15_000;
}

async function tronSignMessage(message: string): Promise<string> {
  const tw = (window as any).tronWeb;
  if (!tw?.trx) throw new Error("TronLink not available");

  if (typeof tw.trx.signMessageV2 === "function") return await tw.trx.signMessageV2(message);
  if (typeof tw.trx.signMessage === "function") return await tw.trx.signMessage(message);
  throw new Error("TronLink does not support message signing on this build");
}

function normalizeLabel(value: unknown): string {
  return String(value || "").trim();
}

function normalizeGroupKey(value: string): string {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}

function extractEventTags(item: IntelItem): string[] {
  const explicit = Array.isArray(item.data?.eventTags) ? item.data?.eventTags : [];
  const raw = [...explicit];
  const deduped: string[] = [];
  const seen = new Set<string>();

  for (const value of raw) {
    const label = normalizeLabel(value);
    if (!label) continue;
    const key = label.toLowerCase();
    if (seen.has(key)) continue;
    deduped.push(label);
    seen.add(key);
  }
  return deduped;
}

function normalizeCategory(value: string): string {
  const v = value.toLowerCase();
  if (!v) return "Mentions";

  const direct = POLYMARKET_CATEGORIES.find((category) => category.toLowerCase() === v);
  if (direct) return direct;

  if (v.includes("elect")) return "Elections";
  if (v.includes("polit")) return "Politics";
  if (v.includes("sport")) return "Sports";
  if (v.includes("crypto") || v.includes("bitcoin") || v.includes("ethereum") || v.includes("token")) return "Crypto";
  if (v.includes("iran")) return "Iran";
  if (v.includes("finance") || v.includes("fed") || v.includes("bank") || v.includes("earnings")) return "Finance";
  if (v.includes("geopolit") || v.includes("war") || v.includes("sanction") || v.includes("nato")) return "Geopolitics";
  if (v.includes("tech") || v.includes("ai") || v.includes("software")) return "Tech";
  if (v.includes("culture") || v.includes("entertain") || v.includes("celebrity") || v.includes("movie") || v.includes("music")) return "Culture";
  if (v.includes("econom") || v.includes("inflation") || v.includes("gdp") || v.includes("recession")) return "Economy";
  if (v.includes("climate") || v.includes("science") || v.includes("weather") || v.includes("hurricane") || v.includes("space")) return "Climate & Science";
  if (v.includes("mention") || v.includes("tweet")) return "Mentions";

  return "Mentions";
}

function deriveCategory(item: IntelItem, eventTags: string[]): string {
  const explicit = normalizeLabel(item.data?.eventCategory);
  if (explicit) return normalizeCategory(explicit);

  for (const tag of eventTags) {
    const category = normalizeCategory(tag);
    if (category !== "Mentions") return category;
  }

  const inferred = Array.isArray(item.data?.inferredTopics) ? item.data?.inferredTopics : [];
  for (const value of inferred) {
    const category = normalizeCategory(normalizeLabel(value));
    if (category) return category;
  }

  const fromText = normalizeCategory(`${item.headline || ""} ${item.body || ""}`);
  return fromText || "Mentions";
}

function parseDateToMs(value: unknown): number {
  const text = String(value || "").trim();
  if (!text) return 0;
  const ms = Date.parse(text);
  return Number.isFinite(ms) ? ms : 0;
}

function resolveResolutionDate(item: IntelItem): { text: string; ms: number } {
  const d = item.data || {};
  const candidates = [
    d.resolutionDate,
    d.endDate,
    d.marketEndDate,
    d.eventEndDate,
    d.resolveDate,
    d.expirationDate,
    d.closedTime,
    d.updatedAt,
    d.startDate,
  ];

  for (const candidate of candidates) {
    const ms = parseDateToMs(candidate);
    if (!ms) continue;
    return { text: new Date(ms).toLocaleDateString(), ms };
  }

  return { text: "Unknown", ms: 0 };
}

function parseOutcomes(item: IntelItem): Array<{ label: string; probability: number }> {
  const labelsRaw = item.data?.outcomes;
  const pricesRaw = item.data?.outcomePrices;

  const labels: string[] = Array.isArray(labelsRaw)
    ? labelsRaw.map((v) => String(v || "").trim()).filter(Boolean)
    : [];
  const prices: number[] = Array.isArray(pricesRaw)
    ? pricesRaw.map((v) => Number(v)).map((v) => (Number.isFinite(v) ? Math.max(0, Math.min(1, v)) : 0))
    : [];

  const out: Array<{ label: string; probability: number }> = [];
  for (let i = 0; i < Math.min(labels.length, prices.length); i += 1) {
    out.push({ label: labels[i], probability: prices[i] });
  }

  if (out.length > 0) return out;
  return [
    {
      label: String(item.data?.topOutcome || "-").trim() || "-",
      probability: Number.isFinite(Number(item.data?.topOutcomeProbability || 0))
        ? Math.max(0, Math.min(1, Number(item.data?.topOutcomeProbability || 0)))
        : 0,
    },
  ];
}

function resolveGroup(item: IntelItem, eventTags: string[]): { groupKey: string; groupLabel: string } {
  const eventId = normalizeLabel(item.data?.eventId);
  const eventSlug = normalizeLabel(item.data?.eventSlug);
  const eventTitle = normalizeLabel(item.data?.eventTitle);

  if (eventId || eventSlug || eventTitle) {
    const key = eventId || eventSlug || normalizeGroupKey(eventTitle);
    const label = eventTitle || eventSlug || eventId;
    return { groupKey: key, groupLabel: label };
  }

  const series = Array.isArray(item.data?.eventSeries) ? item.data?.eventSeries : [];
  const seriesLabel = normalizeLabel(series[0]);
  if (seriesLabel) {
    return { groupKey: `series:${normalizeGroupKey(seriesLabel)}`, groupLabel: seriesLabel };
  }

  const firstTag = eventTags[0] || "General";
  return { groupKey: `tag:${normalizeGroupKey(firstTag)}`, groupLabel: firstTag };
}

function formatUsd(value: number) {
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function compactAddress(value: string) {
  const v = String(value || "").trim();
  if (!v) return "-";
  if (v.length <= 14) return v;
  return `${v.slice(0, 6)}…${v.slice(-4)}`;
}

function formatSignedUsd(value: number) {
  const normalized = Number.isFinite(value) ? value : 0;
  const prefix = normalized > 0 ? "+" : "";
  return `${prefix}$${Math.abs(normalized).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

const TOP_TRADERS_REFRESH_MS = 30_000;
const POLYMARKET_FEED_REFRESH_MS = 30_000;
const LEADERBOARD_STALE_WARNING_MS = 10 * 60_000;
type LeaderboardSortKey = "profit" | "volume" | "pnlPercent";

function normalizeTimeKey(value: unknown): string {
  return String(value || "").trim().toLowerCase();
}

function asFiniteNumber(value: unknown): number {
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;
  const parsed = Number(String(value ?? "").replace(/,/g, "").trim());
  return Number.isFinite(parsed) ? parsed : 0;
}

function normalizeTopTraderRow(raw: Partial<TopTraderRow>, index: number): TopTraderRow {
  return {
    rank: Math.max(1, Math.floor(asFiniteNumber(raw.rank)) || index + 1),
    name: String(raw.name || "").trim(),
    xUsername: String(raw.xUsername || "").trim(),
    proxyWallet: String(raw.proxyWallet || "").trim(),
    pnl: asFiniteNumber(raw.pnl),
    pnlPercent: asFiniteNumber((raw as any).pnlPercent),
    volume: asFiniteNumber(raw.volume),
  };
}

function normalizedSnapshot(snapshot: Partial<TopTradersSnapshot>): TopTradersSnapshot {
  const normalizedRows = Array.isArray(snapshot.rows) ? snapshot.rows.map((row, idx) => normalizeTopTraderRow(row, idx)) : [];
  const normalizedSnapshots: Record<string, TopTradersSnapshot> = {};

  if (snapshot.snapshots && typeof snapshot.snapshots === "object") {
    for (const [rawKey, rawSnapshot] of Object.entries(snapshot.snapshots)) {
      const key = normalizeTimeKey(rawKey);
      if (!key || !rawSnapshot || typeof rawSnapshot !== "object") continue;
      normalizedSnapshots[key] = normalizedSnapshot(rawSnapshot as Partial<TopTradersSnapshot>);
    }
  }

  const normalizedTime = normalizeTimeKey(snapshot.time);
  const availableTimeKeys = new Set<string>();
  if (Array.isArray(snapshot.availableTimes)) {
    for (const value of snapshot.availableTimes) {
      const key = normalizeTimeKey(value);
      if (key) availableTimeKeys.add(key);
    }
  }
  for (const key of Object.keys(normalizedSnapshots)) {
    availableTimeKeys.add(key);
  }
  if (normalizedTime) {
    availableTimeKeys.add(normalizedTime);
  }

  return {
    ...snapshot,
    time: normalizedTime || undefined,
    sort: snapshot.sort === "volume" || snapshot.sort === "pnlPercent" ? snapshot.sort : "profit",
    rows: normalizedRows,
    availableTimes: [...availableTimeKeys],
    snapshots: normalizedSnapshots,
  };
}

function sortedRowsByKey(rows: TopTraderRow[], sortKey: LeaderboardSortKey): TopTraderRow[] {
  const sorted = [...rows].sort((a, b) => {
    if (sortKey === "volume") return b.volume - a.volume;
    if (sortKey === "pnlPercent") return b.pnlPercent - a.pnlPercent;
    return b.pnl - a.pnl;
  });
  return sorted.map((row, idx) => ({ ...row, rank: idx + 1 }));
}


function tileStyle() {
  return {
    border: "4px solid black",
    borderRadius: 2,
    padding: 12,
    background: "#f3f3f3",
  };
}

function extractMarketSlug(item: IntelItem): string {
  const dataSlug = String(item.data?.marketSlug || "").trim();
  if (dataSlug) return dataSlug;

  const url = String(item.url || "").trim();
  if (!url) return "";

  const marker = "/event/";
  const idx = url.indexOf(marker);
  if (idx < 0) return "";
  return url.slice(idx + marker.length).split("?")[0] || "";
}

function asBool(value: unknown): boolean | null {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "y"].includes(normalized)) return true;
    if (["false", "0", "no", "n"].includes(normalized)) return false;
  }
  return null;
}

function isActivePolymarketItem(item: IntelItem): boolean {
  const data = item.data || {};

  const active = asBool(data.active);
  if (active !== null) return active;

  const resolved = asBool(data.resolved ?? data.isResolved);
  if (resolved === true) return false;

  const closed = asBool(data.closed);
  if (closed === true) return false;

  const archived = asBool(data.archived);
  if (archived === true) return false;

  const acceptingOrders = asBool(data.acceptingOrders);
  if (acceptingOrders === false) return false;

  return true;
}

function toRows(items: IntelItem[]): EnrichedMarket[] {
  const out: EnrichedMarket[] = [];

  for (const it of items) {
    const source = (it.source || "").toLowerCase();
    if (source !== "polymarket") continue;
    if (!isActivePolymarketItem(it)) continue;

    const eventTags = extractEventTags(it);
    const { groupKey, groupLabel } = resolveGroup(it, eventTags);

    const volumeRaw = Number(it.data?.volume || 0);
    const topProbRaw = Number(it.data?.topOutcomeProbability || 0);
    const outcomes = parseOutcomes(it);
    const category = deriveCategory(it, eventTags);
    const resolution = resolveResolutionDate(it);

    const marketId = String(it.data?.marketId || it.id || `${it.ts}-${it.headline}`);
    const slug = extractMarketSlug(it);
    const fallbackUrl = slug ? `https://polymarket.com/event/${slug}` : "";
    const marketUrl = String(it.url || "").trim() || fallbackUrl;
    const row: EnrichedMarket = {
      id: marketId,
      question: String(it.headline || "").trim() || "Polymarket market",
      slug,
      url: marketUrl,
      volume: Number.isFinite(volumeRaw) ? volumeRaw : 0,
      topOutcomeLabel: String(it.data?.topOutcome || "").trim() || "-",
      topOutcomeProbability: Number.isFinite(topProbRaw) ? Math.max(0, Math.min(1, topProbRaw)) : 0,
      outcomes,
      category,
      resolutionDate: resolution.text,
      resolutionDateMs: resolution.ms,
      groupKey,
      groupLabel,
    };

    out.push(row);
  }

  out.sort((a, b) => b.volume - a.volume);
  return out;
}

function usePolymarketData({ addr, subReg, enabled }: { addr: string; subReg: any; enabled: boolean }) {
  const apiBase = (import.meta as any).env?.VITE_INTEL_API_BASE as string | undefined;
  const base = useMemo(() => (apiBase ? apiBase.replace(/\/$/, "") : ""), [apiBase]);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [rows, setRows] = useState<EnrichedMarket[]>([]);

  const authLockRef = useRef<Promise<string> | null>(null);
  const authCooldownUntilRef = useRef<number>(0);

  async function ensureJwt(): Promise<string> {
    if (!base) throw new Error("Missing VITE_INTEL_API_BASE (no backend configured)");
    if (!addr || !subReg) throw new Error("Connect wallet and activate Intel subscription");

    const now = Date.now();
    if (now < authCooldownUntilRef.current) throw new Error("Auth temporarily paused; try again in a moment");

    const cached = getStoredJwt();
    if (cached && isJwtFresh(cached.expMs)) return cached.token;

    if (authLockRef.current) return authLockRef.current;

    authLockRef.current = (async () => {
      const chRes = await fetch(`${base}/api/challenge?addr=${encodeURIComponent(addr)}`);
      const chJson = await chRes.json().catch(() => ({} as Record<string, unknown>));
      if (!chRes.ok) throw new Error(String(chJson?.error || `Challenge failed (${chRes.status})`));

      const message = String(chJson?.message || "");
      if (!message) throw new Error("Challenge missing message");

      const signature = await tronSignMessage(message);
      const authRes = await fetch(`${base}/api/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ addr, signature }),
      });

      const authJson = await authRes.json().catch(() => ({} as Record<string, unknown>));
      if (!authRes.ok) throw new Error(String(authJson?.error || `Auth failed (${authRes.status})`));

      const token = String(authJson?.token || "");
      const exp = Number(authJson?.expiresInSeconds || 0);
      const untilMs = Number(authJson?.untilMs || 0);
      if (!token || !Number.isFinite(exp) || exp <= 0) throw new Error("Auth returned invalid token");

      setStoredJwt(token, exp, untilMs);
      return token;
    })();

    try {
      return await authLockRef.current;
    } catch (e) {
      clearStoredJwt();
      authCooldownUntilRef.current = Date.now() + 60_000;
      throw e;
    } finally {
      authLockRef.current = null;
    }
  }

  async function refresh() {
    if (!enabled) return;
    if (!base) {
      setLoading(false);
      setRows([]);
      setError("Set VITE_INTEL_API_BASE to load the Polymarket market feed.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const token = await ensureJwt();
      const res = await fetch(`${base}/api/intel/source/polymarket`, {
        method: "GET",
        headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
      });

      if (res.status === 401) {
        clearStoredJwt();
        const token2 = await ensureJwt();
        const res2 = await fetch(`${base}/api/intel/source/polymarket`, {
          method: "GET",
          headers: { Accept: "application/json", Authorization: `Bearer ${token2}` },
        });
        if (!res2.ok) throw new Error(`Intel fetch failed (${res2.status})`);
        const json2 = (await res2.json()) as { items?: IntelItem[] };
        setRows(toRows(Array.isArray(json2.items) ? json2.items : []));
        return;
      }

      if (!res.ok) throw new Error(`Intel fetch failed (${res.status})`);

      const json = (await res.json()) as { items?: IntelItem[] };
      setRows(toRows(Array.isArray(json.items) ? json.items : []));
    } catch (e: any) {
      setError(e?.message || "Failed to load Polymarket data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!enabled) return;

    refresh();
    const timer = window.setInterval(() => {
      refresh();
    }, POLYMARKET_FEED_REFRESH_MS);

    return () => window.clearInterval(timer);
  }, [addr, subReg, base, enabled]);

  return { rows, loading, error, refresh };
}

function useTopTradersSnapshot({ addr, subReg }: { addr: string; subReg: any }) {
  const apiBase = (import.meta as any).env?.VITE_INTEL_API_BASE as string | undefined;
  const base = useMemo(() => (apiBase ? apiBase.replace(/\/$/, "") : ""), [apiBase]);
  const leaderboardUrl = base ? `${base}/api/polymarket/top_traders_leaderboard` : "";

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [snapshot, setSnapshot] = useState<TopTradersSnapshot | null>(null);

  const authLockRef = useRef<Promise<string> | null>(null);
  const authCooldownUntilRef = useRef<number>(0);

  async function ensureJwt(): Promise<string> {
    if (!base) throw new Error("Missing VITE_INTEL_API_BASE (no backend configured)");
    if (!addr || !subReg) throw new Error("Connect wallet and activate Intel subscription");

    const now = Date.now();
    if (now < authCooldownUntilRef.current) throw new Error("Auth temporarily paused; try again in a moment");

    const cached = getStoredJwt();
    if (cached && isJwtFresh(cached.expMs)) return cached.token;

    if (authLockRef.current) return authLockRef.current;

    authLockRef.current = (async () => {
      const chRes = await fetch(`${base}/api/challenge?addr=${encodeURIComponent(addr)}`);
      const chJson = await chRes.json().catch(() => ({} as Record<string, unknown>));
      if (!chRes.ok) throw new Error(String(chJson?.error || `Challenge failed (${chRes.status})`));

      const message = String(chJson?.message || "");
      if (!message) throw new Error("Challenge missing message");

      const signature = await tronSignMessage(message);
      const authRes = await fetch(`${base}/api/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ addr, signature }),
      });

      const authJson = await authRes.json().catch(() => ({} as Record<string, unknown>));
      if (!authRes.ok) throw new Error(String(authJson?.error || `Auth failed (${authRes.status})`));

      const token = String(authJson?.token || "");
      const exp = Number(authJson?.expiresInSeconds || 0);
      const untilMs = Number(authJson?.untilMs || 0);
      if (!token || !Number.isFinite(exp) || exp <= 0) throw new Error("Auth returned invalid token");

      setStoredJwt(token, exp, untilMs);
      return token;
    })();

    try {
      return await authLockRef.current;
    } catch (e) {
      clearStoredJwt();
      authCooldownUntilRef.current = Date.now() + 60_000;
      throw e;
    } finally {
      authLockRef.current = null;
    }
  }

  async function refresh() {
    if (!base) {
      setLoading(false);
      setSnapshot(null);
      setError("Set VITE_INTEL_API_BASE to load the authenticated Polymarket leaderboard.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const token = await ensureJwt();
      const stamp = Date.now();
      const separator = leaderboardUrl.includes("?") ? "&" : "?";
      const res = await fetch(`${leaderboardUrl}${separator}ts=${stamp}`, {
        method: "GET",
        cache: "no-store",
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
          "Cache-Control": "no-cache, no-store, must-revalidate",
          Pragma: "no-cache",
        },
      });

      if (res.status === 401) {
        clearStoredJwt();
        const token2 = await ensureJwt();
        const res2 = await fetch(`${leaderboardUrl}${separator}ts=${Date.now()}`, {
          method: "GET",
          cache: "no-store",
          headers: {
            Accept: "application/json",
            Authorization: `Bearer ${token2}`,
            "Cache-Control": "no-cache, no-store, must-revalidate",
            Pragma: "no-cache",
          },
        });
        if (!res2.ok) throw new Error(`Leaderboard fetch failed (${res2.status})`);
        const json2 = (await res2.json()) as Partial<TopTradersSnapshot>;
        setSnapshot(normalizedSnapshot(json2));
        return;
      }

      if (!res.ok) throw new Error(`Leaderboard fetch failed (${res.status})`);

      const json = (await res.json()) as Partial<TopTradersSnapshot>;
      setSnapshot(normalizedSnapshot(json));
    } catch (e: any) {
      setError(e?.message || "Failed to load top traders leaderboard");
      setSnapshot(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    const timer = window.setInterval(() => {
      refresh();
    }, TOP_TRADERS_REFRESH_MS);

    return () => window.clearInterval(timer);
  }, [base, addr, subReg]);

  return { snapshot, loading, error, refresh, leaderboardUrl };
}

function GroupedFeed({ rows }: { rows: EnrichedMarket[] }) {
  const [minEventVolume, setMinEventVolume] = useState(0);
  const [categoryFilter, setCategoryFilter] = useState("All");
  const [resolutionBefore, setResolutionBefore] = useState("");
  const [resolutionAfter, setResolutionAfter] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const largeGroupThreshold = 4;

  const categoryOptions = useMemo(() => {
    const opts = new Set<string>(["All", ...POLYMARKET_CATEGORIES]);
    for (const row of rows) opts.add(row.category);
    return [...opts];
  }, [rows]);

  const resolutionBeforeMs = resolutionBefore ? parseDateToMs(resolutionBefore) : 0;
  const resolutionAfterMs = resolutionAfter ? parseDateToMs(resolutionAfter) : 0;
  const searchNormalized = searchQuery.trim().toLowerCase();

  const byGroup = useMemo(() => {
    const grouped = new Map<string, { label: string; markets: EnrichedMarket[] }>();
    for (const row of rows) {
      const key = row.groupKey || row.groupLabel || "general";
      const found = grouped.get(key) || { label: row.groupLabel || "General", markets: [] };
      found.markets.push(row);
      grouped.set(key, found);
    }

    return [...grouped.entries()]
      .map(([key, payload]) => {
        const markets = payload.markets
          .filter((market) => {
            if (!searchNormalized) return true;
            const haystack = `${market.question} ${market.groupLabel} ${market.category}`.toLowerCase();
            return haystack.includes(searchNormalized);
          })
          .sort((a, b) => b.volume - a.volume);

        return {
          key,
          label: payload.label,
          category: markets[0]?.category || payload.markets[0]?.category || "Mentions",
          resolutionDate: markets.reduce((latest, market) => {
            if (market.resolutionDateMs > latest.resolutionDateMs) {
              return { resolutionDate: market.resolutionDate, resolutionDateMs: market.resolutionDateMs };
            }
            return latest;
          }, { resolutionDate: "Unknown", resolutionDateMs: 0 }).resolutionDate,
          resolutionDateMs: markets.reduce((latest, market) => Math.max(latest, market.resolutionDateMs), 0),
          markets,
          totalVol: markets.reduce((sum, m) => sum + m.volume, 0),
        };
      })
      .filter((entry) => {
        if (!entry.markets.length) return false;
        if (entry.totalVol < minEventVolume) return false;
        if (categoryFilter !== "All" && entry.category !== categoryFilter) return false;
        if (resolutionAfterMs && (!entry.resolutionDateMs || entry.resolutionDateMs < resolutionAfterMs)) return false;
        if (resolutionBeforeMs && (!entry.resolutionDateMs || entry.resolutionDateMs > resolutionBeforeMs)) return false;
        return true;
      })
      .sort((a, b) => b.totalVol - a.totalVol);
  }, [rows, minEventVolume, categoryFilter, resolutionAfterMs, resolutionBeforeMs, searchNormalized]);

  function toggleGroup(groupKey: string) {
    setExpandedGroups((prev) => ({ ...prev, [groupKey]: !prev[groupKey] }));
  }

  function renderOutcome(m: EnrichedMarket) {
    if (m.outcomes.length >= 2) {
      return (
        <strong>
          {m.outcomes[0].label.toUpperCase()} {Math.round(m.outcomes[0].probability * 100)}% {m.outcomes[1].label.toUpperCase()} {Math.round(m.outcomes[1].probability * 100)}%
        </strong>
      );
    }
    return (
      <strong>
        {m.topOutcomeLabel.toUpperCase()} {Math.round(m.topOutcomeProbability * 100)}%
      </strong>
    );
  }

  return (
    <>
      <div style={{ ...tileStyle(), marginBottom: 14, display: "grid", gap: 10 }}>
        <div style={{ fontWeight: 800, fontSize: 14 }}>Filters</div>
        <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(5, minmax(0, 1fr))" }}>
          <label style={{ fontSize: 12, display: "grid", gap: 4 }}>
            Min event volume (USD)
            <input
              type="number"
              min={0}
              value={minEventVolume}
              onChange={(e) => setMinEventVolume(Math.max(0, Number(e.target.value) || 0))}
              style={{ padding: 6, border: "1px solid rgba(0,0,0,0.3)", borderRadius: 6 }}
            />
          </label>
          <label style={{ fontSize: 12, display: "grid", gap: 4 }}>
            Category (from API tags)
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              style={{ padding: 6, border: "1px solid rgba(0,0,0,0.3)", borderRadius: 6 }}
            >
              {categoryOptions.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
          <label style={{ fontSize: 12, display: "grid", gap: 4 }}>
            Search markets
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search question, event, or category"
              style={{ padding: 6, border: "1px solid rgba(0,0,0,0.3)", borderRadius: 6 }}
            />
          </label>
          <label style={{ fontSize: 12, display: "grid", gap: 4 }}>
            Resolution date after
            <input
              type="date"
              value={resolutionAfter}
              onChange={(e) => setResolutionAfter(e.target.value)}
              style={{ padding: 6, border: "1px solid rgba(0,0,0,0.3)", borderRadius: 6 }}
            />
          </label>
          <label style={{ fontSize: 12, display: "grid", gap: 4 }}>
            Resolution date before
            <input
              type="date"
              value={resolutionBefore}
              onChange={(e) => setResolutionBefore(e.target.value)}
              style={{ padding: 6, border: "1px solid rgba(0,0,0,0.3)", borderRadius: 6 }}
            />
          </label>
        </div>
      </div>

      <div
        style={{
          columnCount: 2,
          columnGap: 14,
        }}
      >
      {byGroup.map((entry) => (
        <div key={entry.key} style={{ ...tileStyle(), breakInside: "avoid", marginBottom: 14 }}>
          <div style={{ fontWeight: 900, fontSize: 28, lineHeight: 1, marginBottom: 10 }}>{entry.label}</div>
          <div style={{ fontSize: 12, opacity: 0.85, marginBottom: 8 }}>
            Total volume: ${formatUsd(entry.totalVol)} · Resolution: {entry.resolutionDate} · Category: {entry.category}
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {(entry.markets.length >= largeGroupThreshold && !expandedGroups[entry.key]
              ? entry.markets.slice(0, 1)
              : entry.markets
            ).map((m) => (
              <div key={m.id} style={{ borderTop: "1px dashed rgba(0,0,0,0.2)", paddingTop: 6 }}>
                <div style={{ fontWeight: 700, fontSize: 12 }}>{m.question}</div>
                <div style={{ fontSize: 12, opacity: 0.85 }}>
                  {renderOutcome(m)} · Volume ${formatUsd(m.volume)} · Resolution: {m.resolutionDate}
                </div>
                {m.url && (
                  <a
                    href={m.url}
                    target="_blank"
                    rel="noreferrer"
                    style={{ marginTop: 4, display: "inline-block", fontSize: 12, fontWeight: 700 }}
                  >
                    View on Polymarket ↗
                  </a>
                )}
              </div>
            ))}
            {entry.markets.length >= largeGroupThreshold && (
              <button
                onClick={() => toggleGroup(entry.key)}
                style={{
                  border: "1px solid rgba(0,0,0,0.2)",
                  borderRadius: 999,
                  background: "white",
                  padding: "6px 10px",
                  cursor: "pointer",
                  fontWeight: 700,
                  width: "fit-content",
                }}
              >
                {expandedGroups[entry.key] ? "Show fewer markets" : `Show all ${entry.markets.length} markets`}
              </button>
            )}
          </div>
        </div>
      ))}
      </div>
    </>
  );
}

export default function PolymarketPage({ addr, subReg }: { addr: string; subReg: any }) {
  const [view, setView] = useState<"feed" | "leaderboard">("leaderboard");
  const { rows, loading, error, refresh } = usePolymarketData({ addr, subReg, enabled: view === "feed" });
  const {
    snapshot: topTraders,
    loading: leaderboardLoading,
    error: leaderboardError,
    refresh: refreshLeaderboard,
    leaderboardUrl,
  } = useTopTradersSnapshot({ addr, subReg });
  const [leaderboardSort, setLeaderboardSort] = useState<LeaderboardSortKey>("profit");
  const [leaderboardTime, setLeaderboardTime] = useState("");

  const availableLeaderboardTimes = useMemo(() => {
    if (!topTraders) return [] as string[];
    const source = topTraders.availableTimes?.length ? topTraders.availableTimes : [topTraders.time || ""];
    return source.map((v) => normalizeTimeKey(v)).filter(Boolean);
  }, [topTraders]);

  useEffect(() => {
    if (!availableLeaderboardTimes.length) {
      setLeaderboardTime("");
      return;
    }

    setLeaderboardTime((current) => {
      const normalizedCurrent = normalizeTimeKey(current);
      if (normalizedCurrent && availableLeaderboardTimes.includes(normalizedCurrent)) return normalizedCurrent;
      const preferred = normalizeTimeKey(topTraders?.time);
      if (preferred && availableLeaderboardTimes.includes(preferred)) return preferred;
      return availableLeaderboardTimes[0];
    });
  }, [availableLeaderboardTimes, topTraders?.time]);

  const activeLeaderboardSnapshot = useMemo(() => {
    if (!topTraders) return null;
    const selectedTime = normalizeTimeKey(leaderboardTime || topTraders.time);
    const fromMap = selectedTime ? topTraders.snapshots?.[selectedTime] : undefined;
    const snapshot = fromMap || topTraders;
    return {
      ...snapshot,
      time: selectedTime || snapshot.time,
      sort: leaderboardSort,
      rows: sortedRowsByKey(snapshot.rows, leaderboardSort),
    };
  }, [topTraders, leaderboardSort, leaderboardTime]);

  const leaderboardAgeText = useMemo(() => {
    if (!activeLeaderboardSnapshot?.scrapedAt) return "n/a";
    const scrapedMs = Date.parse(activeLeaderboardSnapshot.scrapedAt);
    if (!Number.isFinite(scrapedMs) || scrapedMs <= 0) return "n/a";
    const ageMs = Math.max(0, Date.now() - scrapedMs);
    const mins = Math.floor(ageMs / 60_000);
    if (mins < 1) return "<1 min";
    return `${mins} min`;
  }, [activeLeaderboardSnapshot?.scrapedAt]);

  const leaderboardIsStale = useMemo(() => {
    if (!activeLeaderboardSnapshot?.scrapedAt) return false;
    const scrapedMs = Date.parse(activeLeaderboardSnapshot.scrapedAt);
    if (!Number.isFinite(scrapedMs) || scrapedMs <= 0) return false;
    return Date.now() - scrapedMs > LEADERBOARD_STALE_WARNING_MS;
  }, [activeLeaderboardSnapshot?.scrapedAt]);

  return (
    <div style={{ border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14, padding: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "center" }}>
        <div>
          <div style={{ fontWeight: 900, fontSize: 24 }}>Polymarket</div>
          <div style={{ fontSize: 13, opacity: 0.85 }}>Raw grouped Polymarket feed sourced from scraper-ingested intel.</div>
        </div>

        <button
          onClick={view === "feed" ? refresh : refreshLeaderboard}
          style={{
            border: "1px solid rgba(0,0,0,0.2)",
            borderRadius: 999,
            background: "white",
            padding: "8px 14px",
            cursor: "pointer",
            fontWeight: 700,
          }}
        >
          {view === "feed" ? "Refresh feed" : "Refresh leaderboard"}
        </button>
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
        <button
          onClick={() => setView("feed")}
          style={{
            border: "1px solid rgba(0,0,0,0.2)",
            borderRadius: 999,
            background: view === "feed" ? "#111" : "white",
            color: view === "feed" ? "white" : "#111",
            padding: "6px 12px",
            cursor: "pointer",
            fontWeight: 700,
            fontSize: 12,
          }}
        >
          Market feed
        </button>
        <button
          onClick={() => setView("leaderboard")}
          style={{
            border: "1px solid rgba(0,0,0,0.2)",
            borderRadius: 999,
            background: view === "leaderboard" ? "#111" : "white",
            color: view === "leaderboard" ? "white" : "#111",
            padding: "6px 12px",
            cursor: "pointer",
            fontWeight: 700,
            fontSize: 12,
          }}
        >
          Top traders leaderboard
        </button>
      </div>

      {view === "feed" && loading && <div style={{ marginTop: 12, fontSize: 13 }}>Loading Polymarket feed…</div>}
      {view === "feed" && !loading && error && <div style={{ marginTop: 12, fontSize: 13, color: "crimson" }}>Error: {error}</div>}

      {view === "feed" && !loading && !error && (
        <div style={{ marginTop: 12 }}>
          <GroupedFeed rows={rows} />
        </div>
      )}

      {view === "leaderboard" && leaderboardLoading && <div style={{ marginTop: 12, fontSize: 13 }}>Loading leaderboard…</div>}
      {view === "leaderboard" && !leaderboardLoading && leaderboardError && (
        <div style={{ marginTop: 12, fontSize: 13, color: "crimson" }}>Error: {leaderboardError}</div>
      )}

      {view === "leaderboard" && !leaderboardLoading && !leaderboardError && activeLeaderboardSnapshot && (
        <div style={{ ...tileStyle(), marginTop: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <div style={{ fontSize: 12, opacity: 0.8 }}>
              Snapshot: {activeLeaderboardSnapshot.scrapedAt ? new Date(activeLeaderboardSnapshot.scrapedAt).toLocaleString() : "n/a"}
              {activeLeaderboardSnapshot.time ? ` · Window: ${activeLeaderboardSnapshot.time}` : ""}
              {activeLeaderboardSnapshot.sort ? ` · Sort: ${activeLeaderboardSnapshot.sort}` : ""}
              {` · Age: ${leaderboardAgeText}`}
              {` · Source: ${leaderboardUrl}`}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end" }}>
              {availableLeaderboardTimes.map((timeKey) => (
                <button
                  key={timeKey}
                  onClick={() => setLeaderboardTime(timeKey)}
                  style={{
                    border: "1px solid rgba(0,0,0,0.2)",
                    borderRadius: 999,
                    background: normalizeTimeKey(activeLeaderboardSnapshot?.time) === timeKey ? "#111" : "white",
                    color: normalizeTimeKey(activeLeaderboardSnapshot?.time) === timeKey ? "white" : "#111",
                    padding: "4px 10px",
                    cursor: "pointer",
                    fontWeight: 700,
                    fontSize: 11,
                  }}
                >
                  Window: {timeKey}
                </button>
              ))}
              {(["profit", "volume", "pnlPercent"] as const).map((key) => (
                <button
                  key={key}
                  onClick={() => setLeaderboardSort(key)}
                  style={{
                    border: "1px solid rgba(0,0,0,0.2)",
                    borderRadius: 999,
                    background: leaderboardSort === key ? "#111" : "white",
                    color: leaderboardSort === key ? "white" : "#111",
                    padding: "4px 10px",
                    cursor: "pointer",
                    fontWeight: 700,
                    fontSize: 11,
                  }}
                >
                  Sort: {key}
                </button>
              ))}
            </div>
          </div>

          {leaderboardIsStale && (
            <div style={{ marginBottom: 10, fontSize: 12, color: "#b54708", fontWeight: 700 }}>
              Warning: leaderboard snapshot looks stale (&gt;10 min old). Verify that your scraper job is running and updating the configured source.
            </div>
          )}

          <div style={{ display: "grid", gap: 8 }}>
            {activeLeaderboardSnapshot.rows.map((row, idx) => (
              <div
                key={`${idx + 1}-${row.proxyWallet}-${leaderboardSort}`}
                style={{
                  borderRadius: 10,
                  border: "1px solid rgba(0,0,0,0.16)",
                  background: "white",
                  padding: 10,
                  display: "grid",
                  gridTemplateColumns: "70px 1fr auto auto auto",
                  gap: 10,
                  alignItems: "center",
                  fontSize: 12,
                }}
              >
                <div style={{ fontWeight: 900 }}>#{idx + 1}</div>
                <div>
                  <div style={{ fontWeight: 800 }}>{row.name || compactAddress(row.proxyWallet)}</div>
                  <div style={{ opacity: 0.75, fontFamily: "monospace" }}>{compactAddress(row.proxyWallet)}</div>
                </div>
                <div style={{ textAlign: "right", minWidth: 110 }}>
                  <div style={{ fontWeight: 800, color: row.pnl >= 0 ? "#067647" : "#b42318" }}>{formatSignedUsd(row.pnl)}</div>
                  <div style={{ opacity: 0.75 }}>PnL</div>
                </div>
                <div style={{ textAlign: "right", minWidth: 90 }}>
                  <div style={{ fontWeight: 800, color: row.pnlPercent >= 0 ? "#067647" : "#b42318" }}>
                    {`${row.pnlPercent >= 0 ? "+" : ""}${row.pnlPercent.toLocaleString(undefined, { maximumFractionDigits: 2 })}%`}
                  </div>
                  <div style={{ opacity: 0.75 }}>PnL %</div>
                </div>
                <div style={{ textAlign: "right", minWidth: 120 }}>
                  <div style={{ fontWeight: 800 }}>${formatUsd(row.volume)}</div>
                  <div style={{ opacity: 0.75 }}>Volume</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
