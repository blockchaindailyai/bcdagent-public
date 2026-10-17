import { useEffect, useMemo, useRef, useState } from "react";
import toast from "react-hot-toast";
import { sunToTRX, toUintString } from "../utils/tron";
import { pushTx } from "../components/TxHistory";

type IntelItem = {
  id?: string;
  ts: number; // ms epoch (ARRIVED)
  source?: string;
  headline: string;
  body?: string;
  url?: string;
  tags?: string[];

  // V1 passthrough (server stores & returns it)
  classification?: {
    priority?: "normal" | "breaking";
    assetClass?:
      | "general"
      | "crypto"
      | "stocks"
      | "bonds"
      | "commodities"
      | "fx"
      | "rates"
      | "credit";
    subcategory?: string;
    tickers?: string[];
    topics?: string[];
    regions?: string[];
    [k: string]: any;
  };

  data?: {
    publishedDeltaSec?: number | null;
    publishedTs?: number | null; // ms epoch
    publishedAt?: string | null; // ISO
    raw?: { published?: string | null };
    [k: string]: any;
  };
};

const PRESETS = [1, 3, 6, 12];
const POLL_MS_ACTIVE = 10_000;

// ---- JWT storage helpers ----
const JWT_KEY = "bcd_intel_jwt";
const JWT_EXP_KEY = "bcd_intel_jwt_exp_ms";
const JWT_UNTIL_KEY = "bcd_intel_sub_until_ms";

// ---- Filter storage helpers ----
const FILTERS_KEY = "bcd_intel_filters_v1";
const NO_TICKER_KEY = "__NO_TICKER__";
const NO_TICKER_LABEL = "No ticker";

type FilterState = {
  assetClasses: Record<string, boolean>;
  topics: Record<string, boolean>;
  tickers: Record<string, boolean>;
  touched: boolean; // if false: show all
};

function setStoredJwt(token: string, expiresInSeconds: number, untilMs?: number) {
  const jwtExpMs = Date.now() + Math.max(1, expiresInSeconds) * 1000;

  const u = Number(untilMs || 0);
  const effExpMs = Number.isFinite(u) && u > 0 ? Math.min(jwtExpMs, u) : jwtExpMs;

  sessionStorage.setItem(JWT_KEY, token);
  sessionStorage.setItem(JWT_EXP_KEY, String(effExpMs));
  if (Number.isFinite(u) && u > 0) sessionStorage.setItem(JWT_UNTIL_KEY, String(u));
  else sessionStorage.removeItem(JWT_UNTIL_KEY);
}

function getStoredJwt(): { token: string; expMs: number; untilMs?: number } | null {
  const t = sessionStorage.getItem(JWT_KEY) || "";
  const e = Number(sessionStorage.getItem(JWT_EXP_KEY) || "0");
  const u = Number(sessionStorage.getItem(JWT_UNTIL_KEY) || "0");

  if (!t || !Number.isFinite(e) || e <= 0) return null;
  const untilMs = Number.isFinite(u) && u > 0 ? u : undefined;
  return { token: t, expMs: e, untilMs };
}

function clearStoredJwt() {
  sessionStorage.removeItem(JWT_KEY);
  sessionStorage.removeItem(JWT_EXP_KEY);
  sessionStorage.removeItem(JWT_UNTIL_KEY);
}

function isJwtFresh(expMs: number) {
  return Date.now() < expMs - 15_000;
}

function isBase58TronAddress(s: string): boolean {
  if (!s) return false;
  if (s[0] !== "T") return false;
  if (s.length < 30 || s.length > 36) return false;
  return true;
}

// ---- sorting + dual timestamps ----

function getPublishedTsMs(it: IntelItem): number {
  const p = it?.data?.publishedTs;
  if (typeof p === "number" && Number.isFinite(p) && p > 0) return p;

  const iso = it?.data?.publishedAt;
  if (typeof iso === "string" && iso) {
    const ms = Date.parse(iso);
    if (Number.isFinite(ms) && ms > 0) return ms;
  }

  const rawPub = it?.data?.raw?.published;
  if (typeof rawPub === "string" && rawPub) {
    const ms = Date.parse(rawPub);
    if (Number.isFinite(ms) && ms > 0) return ms;
  }

  return 0;
}

const ARRIVAL_BUCKET_MS = 10_000;

function arrivalBucket(ts: number) {
  const t = Number(ts || 0);
  if (!Number.isFinite(t) || t <= 0) return 0;
  return Math.floor(t / ARRIVAL_BUCKET_MS);
}

function sortIntelItems(a: IntelItem, b: IntelItem) {
  const aTs = Number(a?.ts || 0);
  const bTs = Number(b?.ts || 0);

  const aBuck = arrivalBucket(aTs);
  const bBuck = arrivalBucket(bTs);
  if (aBuck !== bBuck) return bBuck - aBuck;

  const aPub = getPublishedTsMs(a);
  const bPub = getPublishedTsMs(b);
  if (aPub !== bPub) return bPub - aPub;

  if (aTs !== bTs) return bTs - aTs;

  const aKey = String(a?.id || a?.url || a?.headline || "");
  const bKey = String(b?.id || b?.url || b?.headline || "");
  return aKey.localeCompare(bKey);
}

function fmtTimeOnly(ts: number) {
  try {
    return new Date(ts).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
  } catch {
    return String(ts);
  }
}

function fmtEarlier(deltaSec: number) {
  const d = Math.max(0, Math.floor(deltaSec));
  if (d < 60) return `${d}s`;
  if (d < 3600) return `${Math.floor(d / 60)}m`;
  return `${Math.floor(d / 3600)}h`;
}

function getPublishedDeltaSec(it: IntelItem): number | null {
  const d = it?.data?.publishedDeltaSec;
  if (typeof d === "number" && Number.isFinite(d)) return d;

  const pubMs = getPublishedTsMs(it);
  if (pubMs > 0) return (it.ts - pubMs) / 1000;

  return null;
}

function fmtArrivedPublished(it: IntelItem) {
  const arrived = fmtTimeOnly(it.ts);
  const deltaSec = getPublishedDeltaSec(it);

  if (deltaSec == null) return `Arrived ${arrived}`;
  return `Arrived ${arrived} · Published ${fmtEarlier(deltaSec)} earlier`;
}

// ---- classification helpers ----

function normKey(s: string) {
  return String(s || "").trim();
}

function normTopic(s: string) {
  return String(s || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9\-_.]/g, "");
}

function normTicker(s: string) {
  return String(s || "")
    .trim()
    .toUpperCase()
    .replace(/^\$/g, "")
    .replace(/\s+/g, "");
}

function getAssetClass(it: IntelItem): string {
  const a = it?.classification?.assetClass;
  if (typeof a === "string" && a) return a;
  return "general";
}

function getTopics(it: IntelItem): string[] {
  const t = it?.classification?.topics;
  if (Array.isArray(t)) return t.map(normTopic).filter(Boolean);
  return [];
}

function getTickers(it: IntelItem): string[] {
  const t = it?.classification?.tickers;
  if (Array.isArray(t)) return t.map(normTicker).filter(Boolean);
  return [];
}

// ---- filter persistence ----

function loadFilters(): FilterState {
  try {
    const raw = localStorage.getItem(FILTERS_KEY);
    if (!raw) return { assetClasses: {}, topics: {}, tickers: {}, touched: false };
    const parsed = JSON.parse(raw);
    return {
      assetClasses: parsed?.assetClasses && typeof parsed.assetClasses === "object" ? parsed.assetClasses : {},
      topics: parsed?.topics && typeof parsed.topics === "object" ? parsed.topics : {},
      tickers: parsed?.tickers && typeof parsed.tickers === "object" ? parsed.tickers : {},
      touched: !!parsed?.touched,
    };
  } catch {
    return { assetClasses: {}, topics: {}, tickers: {}, touched: false };
  }
}

function saveFilters(f: FilterState) {
  try {
    localStorage.setItem(FILTERS_KEY, JSON.stringify(f));
  } catch {}
}

function setAll(map: Record<string, boolean>, keys: string[], value: boolean) {
  const next = { ...map };
  for (const k of keys) next[k] = value;
  return next;
}

function hasAnyTrue(map: Record<string, boolean>) {
  return Object.values(map).some((v) => v === true);
}

function triStateForKeys(map: Record<string, boolean>, keys: string[]) {
  if (keys.length === 0) return "none";
  let t = 0;
  for (const k of keys) if (map[k]) t++;
  if (t === 0) return "none";
  if (t === keys.length) return "all";
  return "some";
}


function isNonPolymarketIntel(item: IntelItem) {
  return (item.source || "").toLowerCase() !== "polymarket";
}

// TronLink message signing compatibility
async function tronSignMessage(message: string): Promise<string> {
  const tw = (window as any).tronWeb;
  if (!tw?.trx) throw new Error("TronLink not available");

  if (typeof tw.trx.signMessageV2 === "function") return await tw.trx.signMessageV2(message);
  if (typeof tw.trx.signMessage === "function") return await tw.trx.signMessage(message);
  throw new Error("TronLink does not support message signing on this build");
}

export default function IntelPage({ addr, subReg }: { addr: string; subReg: any }) {
  const apiBase = (import.meta as any).env?.VITE_INTEL_API_BASE as string | undefined;
  const base = useMemo(() => (apiBase ? apiBase.replace(/\/$/, "") : ""), [apiBase]);

  const [checking, setChecking] = useState(true);
  const [active, setActive] = useState(false);

  // Inline subscribe widget state
  const [loading, setLoading] = useState(false);
  const [months, setMonths] = useState<number>(1);
  const [giftMode, setGiftMode] = useState<boolean>(false);
  const [beneficiary, setBeneficiary] = useState<string>("");

  const [monthlyUsdE6, setMonthlyUsdE6] = useState<string>("0");
  const [trxUsdPriceE6, setTrxUsdPriceE6] = useState<string>("0");
  const [paused, setPaused] = useState<boolean>(false);
  const [requiredSun, setRequiredSun] = useState<string>("0");

  // Intel feed state
  const [intelLoading, setIntelLoading] = useState(false);
  const [intelErr, setIntelErr] = useState<string>("");
  const [items, setItems] = useState<IntelItem[]>([]);
  const pollRef = useRef<number | null>(null);

  // Filters
  const [filters, setFilters] = useState<FilterState>(() => loadFilters());

  // JWT state
  const [jwtReady, setJwtReady] = useState(false);
  const [subUntilMs, setSubUntilMs] = useState<number>(0);

  // auth anti-spam
  const authLockRef = useRef<Promise<string> | null>(null);
  const authCooldownUntilRef = useRef<number>(0);

  const usdAmount = useMemo(() => {
    const m = Number(monthlyUsdE6) / 1e6;
    return months * m;
  }, [months, monthlyUsdE6]);

  const trxAmount = useMemo(() => sunToTRX(requiredSun), [requiredSun]);

  // Compute available filter keys from items
  const available = useMemo(() => {
    const assetSet = new Set<string>();
    const topicSet = new Set<string>();
    const tickerSet = new Set<string>();

    for (const it of items) {
      assetSet.add(getAssetClass(it));
      for (const t of getTopics(it)) topicSet.add(t);
      const tickers = getTickers(it);
      if (tickers.length === 0) tickerSet.add(NO_TICKER_KEY);
      for (const x of tickers) tickerSet.add(x);
    }

    const assets = Array.from(assetSet).sort();
    const topics = Array.from(topicSet).sort();
    const tickers = Array.from(tickerSet).sort();

    return { assets, topics, tickers };
  }, [items]);

  // Ensure new keys default to checked when user hasn't touched filters yet
  useEffect(() => {
    setFilters((prev) => {
      const next: FilterState = {
        assetClasses: { ...prev.assetClasses },
        topics: { ...prev.topics },
        tickers: { ...prev.tickers },
        touched: prev.touched,
      };

      const ensure = (m: Record<string, boolean>, keys: string[]) => {
        for (const k of keys) {
          if (m[k] === undefined) m[k] = true;
        }
      };

      ensure(next.assetClasses, available.assets);
      ensure(next.topics, available.topics);
      ensure(next.tickers, available.tickers);

      saveFilters(next);
      return next;
    });
  }, [available.assets, available.topics, available.tickers]);

  // Apply filters to items
  const filteredItems = useMemo(() => {
    if (!filters.touched) return items;

    // Groups are independent:
    // - If user has at least one checked in group: enforce inclusion
    // - If a group has no discovered keys yet: skip that group
    // - If user has zero checked in a populated group: treat as "no items"
    const hasAsset = available.assets.length === 0 || hasAnyTrue(filters.assetClasses);
    const hasTopic = available.topics.length === 0 || hasAnyTrue(filters.topics);
    const hasTicker = available.tickers.length === 0 || hasAnyTrue(filters.tickers);

    return items.filter((it) => {
      // assetClass
      if (!hasAsset) return false;
      const ac = getAssetClass(it);
      if (!filters.assetClasses[ac]) return false;

      // topics (if any toggled)
      if (!hasTopic) return false;
      const itsTopics = getTopics(it);
      const okTopic =
        available.topics.length === 0 || itsTopics.length === 0 ? true : itsTopics.some((t) => filters.topics[t]);
      if (!okTopic) return false;

      // tickers (if any toggled)
      if (!hasTicker) return false;
      const itsTickers = getTickers(it);
      const okTicker =
        available.tickers.length === 0
          ? true
          : itsTickers.length === 0
          ? !!filters.tickers[NO_TICKER_KEY]
          : itsTickers.some((t) => filters.tickers[t]);
      if (!okTicker) return false;

      return true;
    });
  }, [items, filters, available.assets.length, available.topics.length, available.tickers.length]);

  function stopPolling() {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function startPolling() {
    stopPolling();
    const ac = new AbortController();
    fetchIntel(ac.signal);

    pollRef.current = window.setInterval(() => {
      const ac2 = new AbortController();
      fetchIntel(ac2.signal);
    }, POLL_MS_ACTIVE);

    return () => ac.abort();
  }

  async function checkAndPrime() {
    if (!addr || !subReg) {
      setActive(false);
      setChecking(false);
      return;
    }

    try {
      setChecking(true);

      const [ok, m, p, pa, req] = await Promise.all([
        subReg.isActive(addr).call(),
        subReg.monthlyPriceUsdE6().call(),
        subReg.trxUsdPriceE6().call(),
        subReg.paused().call(),
        subReg.requiredTRXSun(months).call(),
      ]);

      setActive(!!ok);
      setMonthlyUsdE6(toUintString(m));
      setTrxUsdPriceE6(toUintString(p));
      setPaused(!!pa);
      setRequiredSun(toUintString(req));
    } catch (e: any) {
      toast.error(e?.message || "Failed to check subscription");
      setActive(false);
    } finally {
      setChecking(false);
    }
  }

  async function refreshQuote() {
    if (!addr || !subReg) return;
    try {
      const req = await subReg.requiredTRXSun(months).call();
      setRequiredSun(toUintString(req));
    } catch (e: any) {
      toast.error(e?.message || "Failed to refresh quote");
    }
  }

  async function ensureJwt(): Promise<string> {
    if (!base) throw new Error("Missing VITE_INTEL_API_BASE (no backend configured)");
    if (!addr) throw new Error("No wallet connected");

    const now = Date.now();
    if (now < authCooldownUntilRef.current) throw new Error("Auth temporarily paused; try again in a moment");

    const cached = getStoredJwt();
    if (cached && isJwtFresh(cached.expMs)) {
      setJwtReady(true);
      setSubUntilMs(cached.untilMs || 0);
      return cached.token;
    }

    if (authLockRef.current) return authLockRef.current;

    authLockRef.current = (async () => {
      setJwtReady(false);

      const chRes = await fetch(`${base}/api/challenge?addr=${encodeURIComponent(addr)}`, {
        method: "GET",
        headers: { Accept: "application/json" },
      });
      const chJson = await chRes.json().catch(() => ({} as any));
      if (!chRes.ok) throw new Error(chJson?.error || `Challenge failed (${chRes.status})`);

      const message = String(chJson?.message || "");
      if (!message) throw new Error("Challenge missing message");

      const signature = await tronSignMessage(message);

      const authRes = await fetch(`${base}/api/auth`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ addr, signature }),
      });

      const authJson = await authRes.json().catch(() => ({} as any));
      if (!authRes.ok) throw new Error(authJson?.error || `Auth failed (${authRes.status})`);

      const token = String(authJson?.token || "");
      const exp = Number(authJson?.expiresInSeconds || 0);
      const untilMs = Number(authJson?.untilMs || 0);

      if (!token || !Number.isFinite(exp) || exp <= 0) throw new Error("Auth returned invalid token");
      if (!Number.isFinite(untilMs) || untilMs <= 0) throw new Error("Auth missing subscription expiry (untilMs)");

      setStoredJwt(token, exp, untilMs);
      setJwtReady(true);
      setSubUntilMs(untilMs);

      return token;
    })();

    try {
      return await authLockRef.current;
    } catch (e) {
      clearStoredJwt();
      setJwtReady(false);
      setSubUntilMs(0);
      authCooldownUntilRef.current = Date.now() + 60_000;
      throw e;
    } finally {
      authLockRef.current = null;
    }
  }

  async function fetchIntel(signal?: AbortSignal) {
    if (!base) {
      setIntelErr("Missing VITE_INTEL_API_BASE (no backend configured)");
      return;
    }
    if (!addr) return;

    setIntelLoading(true);
    setIntelErr("");

    try {
      const token = await ensureJwt();

      const res = await fetch(`${base}/api/intel`, {
        method: "GET",
        headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
        signal,
      });

      if (res.status === 401) {
        clearStoredJwt();
        setJwtReady(false);
        setSubUntilMs(0);

        const token2 = await ensureJwt();
        const res2 = await fetch(`${base}/api/intel`, {
          method: "GET",
          headers: { Accept: "application/json", Authorization: `Bearer ${token2}` },
          signal,
        });

        if (res2.status === 403) {
          clearStoredJwt();
          setJwtReady(false);
          setSubUntilMs(0);
          stopPolling();
          setIntelErr("Subscription expired. Please renew.");
          await checkAndPrime();
          return;
        }

        if (!res2.ok) {
          const txt2 = await res2.text().catch(() => "");
          throw new Error(`Intel fetch failed (${res2.status}): ${txt2 || res2.statusText}`);
        }

        const json2 = (await res2.json()) as { items?: IntelItem[] };
        const next2 = Array.isArray(json2.items) ? json2.items : [];
        setItems(next2.filter(isNonPolymarketIntel).sort(sortIntelItems));
        return;
      }

      if (res.status === 403) {
        clearStoredJwt();
        setJwtReady(false);
        setSubUntilMs(0);
        stopPolling();
        const txt = await res.text().catch(() => "");
        setIntelErr(txt || "Subscription expired. Please renew.");
        await checkAndPrime();
        return;
      }

      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`Intel fetch failed (${res.status}): ${txt || res.statusText}`);
      }

      const json = (await res.json()) as { items?: IntelItem[] };
      const next = Array.isArray(json.items) ? json.items : [];
      setItems(next.filter(isNonPolymarketIntel).sort(sortIntelItems));
    } catch (e: any) {
      if (e?.name === "AbortError") return;

      const msg = e?.message || "Failed to load intel";
      setIntelErr(msg);

      const lower = String(msg).toLowerCase();
      if (lower.includes("auth") || lower.includes("signature") || lower.includes("challenge")) stopPolling();
    } finally {
      setIntelLoading(false);
    }
  }

  // ---- filter UI handlers ----


  function resetFiltersToAll() {
    const next: FilterState = {
      assetClasses: setAll({}, available.assets, true),
      topics: setAll({}, available.topics, true),
      tickers: setAll({}, available.tickers, true),
      touched: false, // return to show-all mode
    };
    setFilters(next);
    saveFilters(next);
  }

  function toggleOne(group: "assetClasses" | "topics" | "tickers", key: string) {
    const k = normKey(key);
    setFilters((prev) => {
      const next: FilterState = {
        assetClasses: { ...prev.assetClasses },
        topics: { ...prev.topics },
        tickers: { ...prev.tickers },
        touched: true,
      };
      const cur = (next as any)[group][k] === true;
      (next as any)[group][k] = !cur;
      saveFilters(next);
      return next;
    });
  }

  function setGroupAll(group: "assetClasses" | "topics" | "tickers", keys: string[], value: boolean) {
    setFilters((prev) => {
      const next: FilterState = {
        assetClasses: { ...prev.assetClasses },
        topics: { ...prev.topics },
        tickers: { ...prev.tickers },
        touched: true,
      };
      (next as any)[group] = setAll((next as any)[group], keys, value);
      saveFilters(next);
      return next;
    });
  }

  useEffect(() => {
    clearStoredJwt();
    setJwtReady(false);
    setSubUntilMs(0);
    authCooldownUntilRef.current = 0;
    authLockRef.current = null;

    checkAndPrime();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addr, subReg]);

  useEffect(() => {
    if (!addr || !subReg) return;
    refreshQuote();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [months]);

  useEffect(() => {
    if (!active) {
      stopPolling();
      return;
    }
    const cleanup = startPolling();
    return () => {
      cleanup?.();
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, base, addr]);

  async function submit() {
    if (!addr || !subReg) return toast.error("Connect TronLink first");
    if (paused) return toast.error("Subscription registry is paused");
    if (!Number.isFinite(months) || months <= 0) return toast.error("Months must be > 0");

    if (giftMode) {
      const b = beneficiary.trim();
      if (!isBase58TronAddress(b)) return toast.error("Enter a valid beneficiary base58 address");
    }

    setLoading(true);
    const tId = toast.loading("Preparing subscription tx…");
    try {
      const req = await subReg.requiredTRXSun(months).call();
      const val = toUintString(req);

      let txid: string | undefined;

      toast.loading("Waiting for TronLink signature…", { id: tId });
      if (!giftMode) txid = await subReg.subscribeTRX(months).send({ callValue: val });
      else txid = await subReg.subscribeTRXFor(beneficiary.trim(), months).send({ callValue: val });

      toast.success("Subscription submitted", { id: tId });

      if (txid) {
        pushTx({
          ts: Date.now(),
          txid,
          months,
          beneficiary: giftMode ? beneficiary.trim() : undefined,
          paidSun: val,
        });
      }

      await checkAndPrime();

      clearStoredJwt();
      setJwtReady(false);
      setSubUntilMs(0);
      authCooldownUntilRef.current = 0;

      if (base) fetchIntel();
    } catch (e: any) {
      toast.error(e?.message || "Subscription failed", { id: tId });
    } finally {
      setLoading(false);
    }
  }

  if (!addr) {
    return (
      <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
        <div style={{ fontWeight: 900, fontSize: 16 }}>Intel</div>
        <div style={{ marginTop: 8, fontSize: 13, opacity: 0.85 }}>Connect TronLink to access subscriber intel.</div>
      </div>
    );
  }

  if (checking) {
    return (
      <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
        <div style={{ fontWeight: 900, fontSize: 16 }}>Intel</div>
        <div style={{ marginTop: 8, fontSize: 13, opacity: 0.85 }}>Checking subscription…</div>
      </div>
    );
  }

  if (active) {
    const assetTri = triStateForKeys(filters.assetClasses, available.assets);
    const topicTri = triStateForKeys(filters.topics, available.topics);
    const tickerTri = triStateForKeys(filters.tickers, available.tickers);

    const groupHeader = (label: string, tri: string, onAll: () => void, onNone: () => void) => (
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <div style={{ fontWeight: 800, fontSize: 13 }}>{label}</div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 11, opacity: 0.65 }}>
            {filters.touched ? (tri === "all" ? "All" : tri === "none" ? "None" : "Some") : "All"}
          </span>
          <button
            onClick={onAll}
            style={{
              padding: "5px 8px",
              borderRadius: 999,
              border: "1px solid rgba(0,0,0,0.18)",
              background: "white",
              cursor: "pointer",
              fontWeight: 900,
              fontSize: 11,
            }}
          >
            Check all
          </button>
          <button
            onClick={onNone}
            style={{
              padding: "5px 8px",
              borderRadius: 999,
              border: "1px solid rgba(0,0,0,0.18)",
              background: "white",
              cursor: "pointer",
              fontWeight: 900,
              fontSize: 11,
            }}
          >
            Uncheck all
          </button>
        </div>
      </div>
    );

    const chip = (checked: boolean, label: string, onClick: () => void) => (
      <button
        key={label}
        onClick={onClick}
        style={{
          padding: "7px 10px",
          borderRadius: 999,
          border: "1px solid rgba(0,0,0,0.18)",
          background: checked ? "rgba(0,0,0,0.06)" : "white",
          cursor: "pointer",
          fontWeight: 900,
          fontSize: 11,
          opacity: checked ? 1 : 0.75,
        }}
      >
        {label}
      </button>
    );

    return (
      <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
          <div style={{ fontWeight: 900, fontSize: 16 }}>Intel (Subscribers Only)</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              onClick={() => fetchIntel()}
              style={{
                padding: "6px 10px",
                borderRadius: 10,
                border: "1px solid rgba(0,0,0,0.18)",
                background: "white",
                cursor: "pointer",
              }}
            >
              Refresh feed
            </button>
            <button
              onClick={checkAndPrime}
              style={{
                padding: "6px 10px",
                borderRadius: 10,
                border: "1px solid rgba(0,0,0,0.18)",
                background: "white",
                cursor: "pointer",
              }}
            >
              Re-check
            </button>
          </div>
        </div>

        {!apiBase && (
          <div
            style={{
              marginTop: 10,
              padding: 10,
              borderRadius: 12,
              border: "1px solid rgba(255,0,0,0.25)",
              background: "rgba(255,0,0,0.06)",
              fontSize: 12,
            }}
          >
            <b>Backend not configured:</b> set <code>VITE_INTEL_API_BASE</code> to your ingest server.
          </div>
        )}

        {intelErr && (
          <div
            style={{
              marginTop: 10,
              padding: 10,
              borderRadius: 12,
              border: "1px solid rgba(255,0,0,0.25)",
              background: "rgba(255,0,0,0.06)",
              fontSize: 12,
            }}
          >
            <b>Intel feed error:</b> {intelErr}
          </div>
        )}

        <div style={{ marginTop: 10, fontSize: 13, opacity: 0.85, lineHeight: 1.5 }}>
          Live feed updates every {Math.round(POLL_MS_ACTIVE / 1000)}s while this page is open.
        </div>

        <div style={{ marginTop: 10, fontSize: 12, opacity: 0.7 }}>
          Session auth: <b>{jwtReady ? "ready" : "not ready"}</b>
          {subUntilMs > 0 ? (
            <>
              {" "}
              · sub expires: <b>{new Date(subUntilMs).toLocaleString()}</b>
            </>
          ) : null}
        </div>

        {/* Filters */}
        <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
          <div style={{ padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.08)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
              <div style={{ fontWeight: 800, fontSize: 13 }}>Filters</div>
              <button
                onClick={resetFiltersToAll}
                style={{
                  padding: "6px 10px",
                  borderRadius: 10,
                  border: "1px solid rgba(0,0,0,0.18)",
                  background: "white",
                  cursor: "pointer",
                  fontWeight: 900,
                  fontSize: 12,
                }}
              >
                Reset to all
              </button>
            </div>

            <div style={{ marginTop: 10, display: "grid", gap: 12 }}>
              {/* Asset Classes */}
              <div>
                {groupHeader(
                  "Asset class",
                  assetTri,
                  () => setGroupAll("assetClasses", available.assets, true),
                  () => setGroupAll("assetClasses", available.assets, false)
                )}
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {available.assets.map((a) =>
                    chip(
                      filters.assetClasses[a] !== false,
                      a,
                      () => toggleOne("assetClasses", a)
                    )
                  )}
                  {available.assets.length === 0 && <div style={{ fontSize: 12, opacity: 0.7 }}>No asset classes detected yet.</div>}
                </div>
              </div>

              {/* Topics */}
              <div>
                {groupHeader(
                  "Topics",
                  topicTri,
                  () => setGroupAll("topics", available.topics, true),
                  () => setGroupAll("topics", available.topics, false)
                )}
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {available.topics.slice(0, 60).map((t) =>
                    chip(
                      filters.topics[t] !== false,
                      t,
                      () => toggleOne("topics", t)
                    )
                  )}
                  {available.topics.length > 60 && (
                    <div style={{ fontSize: 11, opacity: 0.65, alignSelf: "center" }}>
                      Showing first 60 topics…
                    </div>
                  )}
                  {available.topics.length === 0 && <div style={{ fontSize: 12, opacity: 0.7 }}>No topics detected yet.</div>}
                </div>
              </div>

              {/* Tickers */}
              <div>
                {groupHeader(
                  "Tickers",
                  tickerTri,
                  () => setGroupAll("tickers", available.tickers, true),
                  () => setGroupAll("tickers", available.tickers, false)
                )}
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {available.tickers.slice(0, 60).map((t) =>
                    chip(
                      filters.tickers[t] !== false,
                      t === NO_TICKER_KEY ? NO_TICKER_LABEL : t,
                      () => toggleOne("tickers", t)
                    )
                  )}
                  {available.tickers.length > 60 && (
                    <div style={{ fontSize: 11, opacity: 0.65, alignSelf: "center" }}>
                      Showing first 60 tickers…
                    </div>
                  )}
                  {available.tickers.length === 0 && <div style={{ fontSize: 12, opacity: 0.7 }}>No tickers detected yet.</div>}
                </div>
              </div>

              <div style={{ fontSize: 11, opacity: 0.65, lineHeight: 1.4 }}>
                Note: filters apply as <b>AND</b> across groups (asset class AND topic AND ticker). If you uncheck all in a group,
                no items will show.
              </div>
            </div>
          </div>

          {/* Feed */}
          <div style={{ padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.08)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
              <div style={{ fontWeight: 800, fontSize: 13 }}>Feed</div>
              <div style={{ fontSize: 12, opacity: 0.7 }}>
                {intelLoading ? "Loading…" : `${filteredItems.length} shown · ${items.length} total`}
              </div>
            </div>

            {items.length === 0 ? (
              <div style={{ marginTop: 8, fontSize: 12, opacity: 0.8 }}>
                No intel items yet. Once your scraper posts to the ingest endpoint, updates will show here.
              </div>
            ) : filteredItems.length === 0 ? (
              <div style={{ marginTop: 8, fontSize: 12, opacity: 0.8 }}>
                No items match your filters.
              </div>
            ) : (
              <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
                {filteredItems.slice(0, 50).map((it, idx) => {
                  const ac = getAssetClass(it);
                  const pr = it?.classification?.priority;
                  const topics = getTopics(it);
                  const tickers = getTickers(it);

                  return (
                    <div
                      key={it.id || `${it.ts}-${idx}`}
                      style={{
                        padding: 12,
                        borderRadius: 12,
                        border: "1px solid rgba(0,0,0,0.08)",
                        background: "rgba(0,0,0,0.01)",
                      }}
                    >
                      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                        <div style={{ fontWeight: 900, fontSize: 13, lineHeight: 1.3 }}>{it.headline}</div>
                        <div style={{ fontSize: 11, opacity: 0.65, whiteSpace: "nowrap" }}>
                          {fmtArrivedPublished(it)}
                        </div>
                      </div>

                      <div style={{ marginTop: 6, fontSize: 11, opacity: 0.75 }}>
                        <b>{ac}</b>
                        {pr ? <span> · {pr}</span> : null}
                        {it.source ? <span> · {it.source}</span> : null}
                      </div>

                      {(tickers.length > 0 || topics.length > 0) ? (
                        <div style={{ marginTop: 6, fontSize: 11, opacity: 0.7, lineHeight: 1.4 }}>
                          {tickers.length > 0 ? (
                            <div>
                              <b>Tickers:</b> {tickers.join(", ")}
                            </div>
                          ) : null}
                          {topics.length > 0 ? (
                            <div>
                              <b>Topics:</b> {topics.join(", ")}
                            </div>
                          ) : null}
                        </div>
                      ) : null}

                      {it.body && (
                        <div style={{ marginTop: 8, fontSize: 12, opacity: 0.85, lineHeight: 1.5 }}>{it.body}</div>
                      )}

                      {it.url && (
                        <a
                          href={it.url}
                          target="_blank"
                          rel="noreferrer"
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 6,
                            marginTop: 8,
                            fontSize: 12,

                            // defensive overrides against global "prose"/list/counter styles
                            listStyle: "none",
                            listStyleType: "none",
                            padding: 0,
                            marginLeft: 0,
                            textIndent: 0,
                          }}
                        >
                          View source
                        </a>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          <div style={{ padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.08)" }}>
            <div style={{ fontWeight: 800, fontSize: 13 }}>Agent hooks</div>
            <div style={{ marginTop: 6, fontSize: 12, opacity: 0.8 }}>
              Next: OpenClaw agent writes signals/metrics to the backend, frontend renders them here.
            </div>
          </div>
        </div>
      </div>
    );
  }

  // Not active: subscribe UI
  return (
    <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <div style={{ fontWeight: 900, fontSize: 16 }}>Intel</div>
        <button
          onClick={checkAndPrime}
          style={{
            padding: "6px 10px",
            borderRadius: 10,
            border: "1px solid rgba(0,0,0,0.18)",
            background: "white",
            cursor: "pointer",
          }}
        >
          Re-check
        </button>
      </div>

      <div
        style={{
          marginTop: 10,
          padding: 12,
          borderRadius: 12,
          border: "1px solid rgba(0,0,0,0.10)",
          background: "rgba(0,0,0,0.02)",
        }}
      >
        <div style={{ fontWeight: 900, fontSize: 13 }}>Subscription required</div>
        <div style={{ marginTop: 6, fontSize: 12, opacity: 0.85, lineHeight: 1.5 }}>
          Your wallet does not have an active subscription. Purchase below to unlock intel access.
        </div>
      </div>

      <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div style={{ padding: 12, border: "1px solid rgba(0,0,0,0.08)", borderRadius: 12 }}>
            <div style={{ fontSize: 12, opacity: 0.8 }}>Pricing</div>
            <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.6 }}>
              <div>
                Paused: <b>{paused ? "Yes" : "No"}</b>
              </div>
              <div>
                Monthly: <b>${(Number(monthlyUsdE6) / 1e6).toFixed(2)}</b>
              </div>
              <div>
                TRX/USD: <b>${(Number(trxUsdPriceE6) / 1e6).toFixed(6)}</b> / TRX
              </div>
            </div>
          </div>

          <div style={{ padding: 12, border: "1px solid rgba(0,0,0,0.08)", borderRadius: 12 }}>
            <div style={{ fontSize: 12, opacity: 0.8 }}>Quote</div>
            <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.6 }}>
              <div>
                Total: <b>{requiredSun}</b> SUN
              </div>
              <div>
                ≈ <b>{trxAmount.toFixed(6)}</b> TRX
              </div>
              <div>
                ≈ <b>${usdAmount.toFixed(2)}</b> USD
              </div>
            </div>
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ fontSize: 13, fontWeight: 900 }}>Plan</div>
          {PRESETS.map((m) => (
            <button
              key={m}
              onClick={() => setMonths(m)}
              style={{
                padding: "8px 10px",
                borderRadius: 999,
                border: "1px solid rgba(0,0,0,0.18)",
                background: months === m ? "rgba(0,0,0,0.06)" : "white",
                cursor: "pointer",
                fontWeight: 900,
                fontSize: 12,
              }}
            >
              {m} mo
            </button>
          ))}
          <div style={{ marginLeft: 8, fontSize: 12, opacity: 0.8 }}>Custom:</div>
          <input
            type="number"
            min={1}
            value={months}
            onChange={(e) => setMonths(Number(e.target.value))}
            style={{ width: 110, padding: "8px 10px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
          />
        </div>

        <div style={{ padding: 12, border: "1px solid rgba(0,0,0,0.08)", borderRadius: 12 }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer" }}>
            <input type="checkbox" checked={giftMode} onChange={(e) => setGiftMode(e.target.checked)} />
            <span style={{ fontSize: 13, fontWeight: 900 }}>Gift subscription</span>
          </label>

          {giftMode ? (
            <input
              placeholder="Beneficiary (Base58 address, starts with T...)"
              value={beneficiary}
              onChange={(e) => setBeneficiary(e.target.value)}
              style={{
                marginTop: 10,
                width: "100%",
                padding: "10px 12px",
                borderRadius: 10,
                border: "1px solid rgba(0,0,0,0.18)",
              }}
            />
          ) : (
            <div style={{ marginTop: 10, fontSize: 12, opacity: 0.75 }}>
              Subscription will be applied to your connected wallet.
            </div>
          )}
        </div>

        <button
          disabled={loading}
          onClick={submit}
          style={{
            padding: "12px 14px",
            borderRadius: 12,
            border: "1px solid rgba(0,0,0,0.18)",
            background: "white",
            cursor: "pointer",
            fontWeight: 900,
          }}
        >
          {giftMode ? "Buy Gift Subscription" : "Subscribe"}
        </button>
      </div>
    </div>
  );
}
