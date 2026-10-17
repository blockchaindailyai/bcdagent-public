import { useEffect, useMemo, useRef, useState } from "react";

type IntelItem = {
  id?: string;
  ts: number;
  source?: string;
  headline: string;
  body?: string;
  data?: Record<string, unknown>;
};

type LeaderboardRow = {
  rank: number;
  address: string;
  notionalUsd: number;
  realizedPnlUsd?: number;
  feesUsd?: number;
  netProfitUsd?: number;
  pnlPercent?: number;
  trades?: number;
  pairs?: string[];
  pairCount?: number;
  avgHoldMinutes?: number;
  medianHoldMinutes?: number;
  traderClass?: string;
  traderClassLabel?: string;
};

type LeaderboardSortKey = "volume" | "profit" | "pnlPercent" | "trades" | "pairs";

type HyperliquidLeaderboardPayload = {
  generatedAtTs?: number;
  configuredWindows?: string[];
  selectedWindow?: string;
  topAccountsByWindow?: Record<string, LeaderboardRow[]>;
  topAccounts?: LeaderboardRow[];
  archiveLeaderboard?: {
    topAccounts?: Array<Record<string, unknown>>;
    topAccountsByNotional?: Array<Record<string, unknown>>;
  };
  archiveWalletDailyPnl?: Record<string, ArchiveWalletDailyPnlPoint[]>;
  leaderboardsByClass?: Record<string, LeaderboardRow[]>;
  leaderboardsByWindowAndClass?: Record<string, Record<string, LeaderboardRow[]>>;
  classDefinitions?: Record<string, string>;
};

type WalletTradePoint = {
  ts: number;
  tradeId: number;
  market: string;
  side: string;
  sideCode: string;
  sideLabel: string;
  size: number;
  price: number;
  notionalUsd: number;
  feeUsd: number;
  realizedPnlUsd: number;
  tradeRole: string;
  txHash: string;
};

type WalletPnlPoint = {
  ts: number;
  pnlUsd: number;
};

type ArchiveWalletDailyPnlPoint = {
  dt: string;
  ts: number;
  notionalUsd?: number;
  realizedPnlUsd?: number;
  feesUsd?: number;
  netPnlUsd?: number;
  trades?: number;
};

type ArchiveWalletQueryResponse = {
  wallet?: string;
  rows?: ArchiveWalletDailyPnlPoint[];
  tradeRows?: ArchiveWalletTradePoint[];
};

type ArchiveWalletTradePoint = {
  dt?: string;
  ts: number;
  market?: string;
  side?: string;
  price?: number;
  size?: number;
  notionalUsd?: number;
  tradeId?: string;
  txHash?: string;
  feesUsd?: number;
  realizedPnlUsd?: number;
  isMaker?: boolean | number | string;
  maker?: boolean | number | string;
  liquidity?: string;
  aggressor?: string;
  [key: string]: unknown;
};

const JWT_KEY = "bcd_intel_jwt";
const JWT_EXP_KEY = "bcd_intel_jwt_exp_ms";
const JWT_UNTIL_KEY = "bcd_intel_sub_until_ms";
const POLL_MS_ACTIVE = 10_000;
const PAGE_SIZE = 10;

function setStoredJwt(token: string, expiresInSeconds: number, untilMs?: number) {
  const jwtExpMs = Date.now() + Math.max(1, expiresInSeconds) * 1000;
  const u = Number(untilMs || 0);
  const effExpMs = Number.isFinite(u) && u > 0 ? Math.min(jwtExpMs, u) : jwtExpMs;

  sessionStorage.setItem(JWT_KEY, token);
  sessionStorage.setItem(JWT_EXP_KEY, String(effExpMs));
  if (Number.isFinite(u) && u > 0) sessionStorage.setItem(JWT_UNTIL_KEY, String(u));
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

async function tronSignMessage(message: string): Promise<string> {
  const tw = (window as any).tronWeb;
  if (!tw?.trx) throw new Error("TronLink not available");

  if (typeof tw.trx.signMessageV2 === "function") return await tw.trx.signMessageV2(message);
  if (typeof tw.trx.signMessage === "function") return await tw.trx.signMessage(message);
  throw new Error("TronLink does not support message signing on this build");
}

function formatUsd(value: number) {
  if (!Number.isFinite(value)) return "-";
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function asFiniteNumber(value: unknown) {
  const n = Number(value || 0);
  return Number.isFinite(n) ? n : 0;
}

function compactAddress(address: string) {
  const a = String(address || "").trim();
  if (a.length < 16) return a;
  return `${a.slice(0, 8)}…${a.slice(-6)}`;
}

function normalizeAddress(address: string) {
  return String(address || "").trim().toLowerCase();
}

function formatSignedUsd(value: number) {
  if (!Number.isFinite(value)) return "-";
  const abs = Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 0 });
  if (value > 0) return `+$${abs}`;
  if (value < 0) return `-$${abs}`;
  return "$0";
}

function formatPercent(value: number) {
  if (!Number.isFinite(value)) return "-";
  const abs = Math.abs(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (value > 0) return `+${abs}%`;
  if (value < 0) return `-${abs}%`;
  return "0%";
}

function sideToSignedNotional(side: string, notionalUsd: number) {
  const s = String(side || "").trim().toLowerCase();
  if (s.includes("sell") || s === "a") return Math.abs(notionalUsd);
  if (s.includes("buy") || s === "b") return -Math.abs(notionalUsd);
  return 0;
}

function itemTime(ts: number) {
  return new Date(ts || 0).toLocaleString();
}

function decodeTradeSide(side: string) {
  const normalized = String(side || "").trim();
  const lower = normalized.toLowerCase();
  const upper = normalized.toUpperCase();
  if (lower.includes("buy") || upper === "B" || lower === "bid") {
    return { code: upper || "B", label: "Buy" };
  }
  if (lower.includes("sell") || upper === "A" || lower === "ask") {
    return { code: "S", label: "Sell" };
  }
  return { code: upper || "?", label: "Unknown" };
}

function decodeTradeRole(row: ArchiveWalletTradePoint) {
  const liq = String(row.liquidity || row.aggressor || "").trim().toLowerCase();
  if (liq === "maker" || liq === "add") return "Maker";
  if (liq === "taker" || liq === "remove") return "Taker";

  const makerRaw = row.isMaker ?? row.maker;
  if (typeof makerRaw === "boolean") return makerRaw ? "Maker" : "Taker";
  if (typeof makerRaw === "number") return makerRaw > 0 ? "Maker" : "Taker";
  if (typeof makerRaw === "string") {
    const makerStr = makerRaw.trim().toLowerCase();
    if (["1", "true", "maker", "m"].includes(makerStr)) return "Maker";
    if (["0", "false", "taker", "t"].includes(makerStr)) return "Taker";
  }

  return "Unknown";
}

function miniPageWindow(currentPage: number, totalPages: number) {
  const start = Math.max(1, currentPage - 2);
  const end = Math.min(totalPages, start + 4);
  const adjustedStart = Math.max(1, end - 4);
  const pages: number[] = [];
  for (let page = adjustedStart; page <= end; page += 1) pages.push(page);
  return pages;
}

function MiniPagination({
  page,
  totalPages,
  onChange,
}: {
  page: number;
  totalPages: number;
  onChange: (nextPage: number) => void;
}) {
  if (totalPages <= 1) return null;
  const pages = miniPageWindow(page, totalPages);

  return (
    <div style={{ marginTop: 8, display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
      <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        Prev
      </button>
      {pages.map((p) => (
        <button
          type="button"
          key={p}
          onClick={() => onChange(p)}
          style={{
            minWidth: 28,
            borderRadius: 8,
            border: "1px solid rgba(0,0,0,0.2)",
            background: p === page ? "#e2e8f0" : "white",
            fontWeight: p === page ? 700 : 500,
            cursor: "pointer",
          }}
        >
          {p}
        </button>
      ))}
      <button type="button" disabled={page >= totalPages} onClick={() => onChange(page + 1)}>
        Next
      </button>
    </div>
  );
}

function LoadingSpinner({ label }: { label: string }) {
  return (
    <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 12, opacity: 0.8 }}>
      <span
        aria-hidden="true"
        style={{
          width: 16,
          height: 16,
          borderRadius: "50%",
          border: "2px solid rgba(15, 23, 42, 0.18)",
          borderTopColor: "rgba(15, 23, 42, 0.92)",
          animation: "hl-wallet-spin 0.8s linear infinite",
        }}
      />
      <span>{label}</span>
    </div>
  );
}

export default function HyperliquidPage({ addr, subReg }: { addr: string; subReg: any }) {
  const apiBase = (import.meta as any).env?.VITE_INTEL_API_BASE as string | undefined;
  const base = useMemo(() => (apiBase ? apiBase.replace(/\/$/, "") : ""), [apiBase]);

  const [checking, setChecking] = useState(true);
  const [active, setActive] = useState(false);
  const [items, setItems] = useState<IntelItem[]>([]);
  const [leaderboardPayload, setLeaderboardPayload] = useState<HyperliquidLeaderboardPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [windowKey, setWindowKey] = useState("24h");
  const [leaderboardSortKey, setLeaderboardSortKey] = useState<LeaderboardSortKey>("volume");
  const [traderClassKey, setTraderClassKey] = useState("all");
  const [minTradesFilter, setMinTradesFilter] = useState(0);
  const [minVolumeFilter, setMinVolumeFilter] = useState(0);
  const [pairFilter, setPairFilter] = useState("");
  const [leaderboardPage, setLeaderboardPage] = useState(1);
  const [walletQuery, setWalletQuery] = useState("");
  const [walletPairFilter, setWalletPairFilter] = useState("all");
  const [walletTradePage, setWalletTradePage] = useState(1);
  const [recentTradesPage, setRecentTradesPage] = useState(1);
  const [walletArchiveDailyPnl, setWalletArchiveDailyPnl] = useState<ArchiveWalletDailyPnlPoint[]>([]);
  const [walletArchiveTradeRows, setWalletArchiveTradeRows] = useState<ArchiveWalletTradePoint[]>([]);
  const [walletArchiveLoading, setWalletArchiveLoading] = useState(false);
  const [walletArchiveErr, setWalletArchiveErr] = useState("");

  const pollRef = useRef<number | null>(null);
  const authLockRef = useRef<Promise<string> | null>(null);
  const authCooldownUntilRef = useRef<number>(0);

  const hyperliquidItems = useMemo(
    () =>
      items.filter((it) => {
        const source = (it.source || "").toLowerCase();
        return source === "hyperliquid" || source === "hyperliquid_archive_s3";
      }),
    [items]
  );

  const majorTrades = useMemo(
    () =>
      hyperliquidItems
        .filter((it) => Number((it.data?.tradeId as number | undefined) || 0) > 0)
        .sort((a, b) => Number(b.ts || 0) - Number(a.ts || 0)),
    [hyperliquidItems]
  );

  const windowOptions = useMemo(() => {
    const configured = Array.isArray(leaderboardPayload?.configuredWindows)
      ? (leaderboardPayload.configuredWindows as unknown[]).map((x) => String(x))
      : [];

    const byWindow = leaderboardPayload?.topAccountsByWindow as Record<string, LeaderboardRow[]> | undefined;
    const emitted = byWindow ? Object.keys(byWindow).map((x) => String(x)) : [];

    const merged = Array.from(new Set([...configured, ...emitted].filter(Boolean)));
    return merged.length ? merged : ["all"];
  }, [leaderboardPayload]);

  const traderClassOptions = useMemo(() => {
    const defs = leaderboardPayload?.classDefinitions as Record<string, string> | undefined;
    const byClass = leaderboardPayload?.leaderboardsByClass as Record<string, LeaderboardRow[]> | undefined;
    const keys = Array.from(new Set([...(defs ? Object.keys(defs) : []), ...(byClass ? Object.keys(byClass) : [])]));
    return keys.map((key) => ({ key, label: defs?.[key] || key }));
  }, [leaderboardPayload]);

  useEffect(() => {
    if (!windowOptions.includes(windowKey)) setWindowKey(windowOptions[0] || "all");
  }, [windowOptions, windowKey]);

  useEffect(() => {
    if (traderClassKey === "all") return;
    if (!traderClassOptions.find((o) => o.key === traderClassKey)) setTraderClassKey("all");
  }, [traderClassOptions, traderClassKey]);

  const rawLeaderboardRows = useMemo(() => {
    const byWindowAndClass = leaderboardPayload?.leaderboardsByWindowAndClass as
      | Record<string, Record<string, LeaderboardRow[]>>
      | undefined;
    if (traderClassKey !== "all" && byWindowAndClass?.[windowKey] && Array.isArray(byWindowAndClass[windowKey][traderClassKey])) {
      return byWindowAndClass[windowKey][traderClassKey];
    }

    const byClass = leaderboardPayload?.leaderboardsByClass as Record<string, LeaderboardRow[]> | undefined;
    if (traderClassKey !== "all" && byClass && Array.isArray(byClass[traderClassKey])) {
      return byClass[traderClassKey];
    }

    const byWindow = leaderboardPayload?.topAccountsByWindow as Record<string, LeaderboardRow[]> | undefined;
    if (byWindow && Array.isArray(byWindow[windowKey])) return byWindow[windowKey];

    const fallback = leaderboardPayload?.topAccounts as LeaderboardRow[] | undefined;
    if (Array.isArray(fallback) && fallback.length) return fallback;

    if (byWindow && typeof byWindow === "object") {
      const firstRows = Object.values(byWindow).find((rows) => Array.isArray(rows) && rows.length > 0);
      if (firstRows) return firstRows;
    }

    return [];
  }, [leaderboardPayload, windowKey, traderClassKey]);

  const leaderboardRows = useMemo(() => {
    const normalizedRows = rawLeaderboardRows
      .map((row) => ({
        rank: asFiniteNumber(row.rank),
        address: String(row.address || "").trim(),
        notionalUsd: asFiniteNumber(row.notionalUsd),
        realizedPnlUsd: asFiniteNumber(row.realizedPnlUsd),
        feesUsd: asFiniteNumber(row.feesUsd),
        netProfitUsd: asFiniteNumber(row.netProfitUsd),
        pnlPercent: asFiniteNumber(row.pnlPercent),
        trades: asFiniteNumber(row.trades),
        pairCount: asFiniteNumber(row.pairCount || row.pairs?.length),
        pairs: Array.isArray(row.pairs) ? row.pairs.map((p) => String(p).toUpperCase()) : [],
        avgHoldMinutes: asFiniteNumber(row.avgHoldMinutes),
        medianHoldMinutes: asFiniteNumber(row.medianHoldMinutes),
        traderClass: String(row.traderClass || "").trim().toLowerCase(),
        traderClassLabel: String(row.traderClassLabel || "").trim(),
      }))
      .filter((row) => row.address.length > 0);

    const pairNeedle = pairFilter.trim().toUpperCase();
    const filteredRows = normalizedRows.filter((row) => {
      if (row.trades < minTradesFilter) return false;
      if (Math.abs(row.notionalUsd) < minVolumeFilter) return false;
      if (pairNeedle && !row.pairs.some((pair) => pair.includes(pairNeedle))) return false;
      return true;
    });

    const sortedRows = [...filteredRows].sort((a, b) => {
      const aPrimary =
        leaderboardSortKey === "profit"
          ? a.netProfitUsd
          : leaderboardSortKey === "pnlPercent"
            ? a.pnlPercent
            : leaderboardSortKey === "trades"
              ? a.trades
              : leaderboardSortKey === "pairs"
                ? a.pairCount
            : Math.abs(a.notionalUsd);
      const bPrimary =
        leaderboardSortKey === "profit"
          ? b.netProfitUsd
          : leaderboardSortKey === "pnlPercent"
            ? b.pnlPercent
            : leaderboardSortKey === "trades"
              ? b.trades
              : leaderboardSortKey === "pairs"
                ? b.pairCount
            : Math.abs(b.notionalUsd);
      if (bPrimary !== aPrimary) return bPrimary - aPrimary;

      const aSecondary = leaderboardSortKey === "volume" ? a.netProfitUsd : Math.abs(a.notionalUsd);
      const bSecondary = leaderboardSortKey === "volume" ? b.netProfitUsd : Math.abs(b.notionalUsd);
      if (bSecondary !== aSecondary) return bSecondary - aSecondary;

      return a.address.localeCompare(b.address);
    });

    return sortedRows.map((row, idx) => ({
      ...row,
      rank: idx + 1,
    }));
  }, [rawLeaderboardRows, leaderboardSortKey, minTradesFilter, minVolumeFilter, pairFilter]);

  const leaderboardTotalPages = Math.max(1, Math.ceil(leaderboardRows.length / PAGE_SIZE));
  const pagedLeaderboardRows = useMemo(() => {
    const start = (leaderboardPage - 1) * PAGE_SIZE;
    return leaderboardRows.slice(start, start + PAGE_SIZE);
  }, [leaderboardRows, leaderboardPage]);

  useEffect(() => {
    setLeaderboardPage(1);
  }, [windowKey, leaderboardSortKey, traderClassKey, minTradesFilter, minVolumeFilter, pairFilter]);

  useEffect(() => {
    if (leaderboardPage > leaderboardTotalPages) setLeaderboardPage(leaderboardTotalPages);
  }, [leaderboardPage, leaderboardTotalPages]);

  const knownWallets = useMemo(() => {
    const wallets = new Set<string>();

    leaderboardRows.forEach((row) => {
      const address = String(row.address || "").trim();
      if (address) wallets.add(address);
    });

    majorTrades.forEach((it) => {
      const users = Array.isArray(it.data?.users) ? (it.data?.users as unknown[]) : [];
      users.forEach((user) => {
        const address = String(user || "").trim();
        if (address) wallets.add(address);
      });
    });

    const archiveLeaderboard = leaderboardPayload?.archiveLeaderboard as
      | {
          topAccounts?: Array<Record<string, unknown>>;
        }
      | undefined;

    const archiveTop = Array.isArray(archiveLeaderboard?.topAccounts)
      ? (archiveLeaderboard?.topAccounts as Array<Record<string, unknown>>)
      : [];
    archiveTop.forEach((row) => {
      const address = String(row.address || "").trim();
      if (address) wallets.add(address);
    });

    const archiveWalletDailyPnl = leaderboardPayload?.archiveWalletDailyPnl as Record<string, unknown> | undefined;
    if (archiveWalletDailyPnl && typeof archiveWalletDailyPnl === "object") {
      Object.keys(archiveWalletDailyPnl).forEach((wallet) => {
        const address = String(wallet || "").trim();
        if (address) wallets.add(address);
      });
    }

    return Array.from(wallets).sort((a, b) => a.localeCompare(b));
  }, [leaderboardRows, majorTrades, leaderboardPayload]);

  const selectedWallet = useMemo(() => {
    const q = normalizeAddress(walletQuery);
    if (!q) return "";

    const exact = knownWallets.find((wallet) => normalizeAddress(wallet) === q);
    if (exact) return exact;

    const startsWith = knownWallets.find((wallet) => normalizeAddress(wallet).startsWith(q));
    return startsWith || walletQuery.trim();
  }, [walletQuery, knownWallets]);

  const walletTradeHistory = useMemo(() => {
    if (!walletArchiveTradeRows.length) return [] as WalletTradePoint[];

    return walletArchiveTradeRows
      .map((row, idx) => ({
        ts: Number(row.ts || 0),
        tradeId: Number(row.tradeId || idx + 1),
        market: String(row.market || "?"),
        side: String(row.side || "?"),
        sideCode: decodeTradeSide(String(row.side || "?")).code,
        sideLabel: decodeTradeSide(String(row.side || "?")).label,
        size: Number(row.size || 0),
        price: Number(row.price || 0),
        notionalUsd: Number(row.notionalUsd || 0),
        feeUsd: Number(row.feesUsd || 0),
        realizedPnlUsd: Number(row.realizedPnlUsd || 0),
        tradeRole: decodeTradeRole(row),
        txHash: String(row.txHash || ""),
      }))
      .filter((row) => row.ts > 0)
      .sort((a, b) => a.ts - b.ts);
  }, [walletArchiveTradeRows]);

  const walletPairOptions = useMemo(() => {
    const pairs = new Set<string>();
    walletTradeHistory.forEach((row) => {
      const pair = String(row.market || "").trim();
      if (pair) pairs.add(pair);
    });
    return Array.from(pairs).sort((a, b) => a.localeCompare(b));
  }, [walletTradeHistory]);

  useEffect(() => {
    if (walletPairFilter === "all") return;
    if (!walletPairOptions.includes(walletPairFilter)) setWalletPairFilter("all");
  }, [walletPairFilter, walletPairOptions]);

  const filteredWalletTradeHistory = useMemo(() => {
    if (walletPairFilter === "all") return walletTradeHistory;
    return walletTradeHistory.filter((row) => row.market === walletPairFilter);
  }, [walletTradeHistory, walletPairFilter]);

  const walletTradeHistoryDesc = useMemo(() => [...filteredWalletTradeHistory].reverse(), [filteredWalletTradeHistory]);
  const walletTradeTotalPages = Math.max(1, Math.ceil(walletTradeHistoryDesc.length / PAGE_SIZE));
  const pagedWalletTradeHistory = useMemo(() => {
    const start = (walletTradePage - 1) * PAGE_SIZE;
    return walletTradeHistoryDesc.slice(start, start + PAGE_SIZE);
  }, [walletTradeHistoryDesc, walletTradePage]);

  useEffect(() => {
    setWalletTradePage(1);
  }, [selectedWallet, walletPairFilter]);

  useEffect(() => {
    if (walletTradePage > walletTradeTotalPages) setWalletTradePage(walletTradeTotalPages);
  }, [walletTradePage, walletTradeTotalPages]);

  const walletSummary = useMemo(() => {
    if (!filteredWalletTradeHistory.length) {
      return {
        trades: 0,
        totalNotionalUsd: 0,
        buyNotionalUsd: 0,
        sellNotionalUsd: 0,
        totalPnlUsd: 0,
        totalPnlPercent: 0,
        sharpeRatio: 0,
        mostTradedPair: "-",
      };
    }

    const pairCounts = new Map<string, number>();
    const tradeReturns: number[] = [];

    const aggregated = filteredWalletTradeHistory.reduce(
      (acc, row) => {
        const side = row.side.toLowerCase();
        if (side.includes("buy") || side === "b") acc.buyNotionalUsd += row.notionalUsd;
        else if (side.includes("sell") || side === "a") acc.sellNotionalUsd += row.notionalUsd;
        acc.totalNotionalUsd += row.notionalUsd;
        acc.totalPnlUsd += row.realizedPnlUsd;
        acc.trades += 1;

        const pair = String(row.market || "?");
        pairCounts.set(pair, (pairCounts.get(pair) || 0) + 1);

        const denom = Math.abs(row.notionalUsd);
        if (denom > 0) tradeReturns.push(row.realizedPnlUsd / denom);
        return acc;
      },
      {
        trades: 0,
        totalNotionalUsd: 0,
        buyNotionalUsd: 0,
        sellNotionalUsd: 0,
        totalPnlUsd: 0,
        totalPnlPercent: 0,
        sharpeRatio: 0,
        mostTradedPair: "-",
      }
    );

    aggregated.totalPnlPercent =
      aggregated.totalNotionalUsd > 0 ? (aggregated.totalPnlUsd / aggregated.totalNotionalUsd) * 100 : 0;

    if (tradeReturns.length > 1) {
      const mean = tradeReturns.reduce((sum, value) => sum + value, 0) / tradeReturns.length;
      const variance = tradeReturns.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (tradeReturns.length - 1);
      const stdDev = Math.sqrt(Math.max(variance, 0));
      aggregated.sharpeRatio = stdDev > 0 ? mean / stdDev : 0;
    }

    let topPair = "-";
    let topPairCount = -1;
    pairCounts.forEach((count, pair) => {
      if (count > topPairCount || (count === topPairCount && pair.localeCompare(topPair) < 0)) {
        topPair = pair;
        topPairCount = count;
      }
    });
    aggregated.mostTradedPair = topPair;

    return aggregated;
  }, [filteredWalletTradeHistory]);

  const walletPnlHistory = useMemo(() => {
    const target = normalizeAddress(selectedWallet);
    const archiveWalletDailyPnl = leaderboardPayload?.archiveWalletDailyPnl as Record<string, ArchiveWalletDailyPnlPoint[]> | undefined;
    const inMemoryArchiveRows = target && archiveWalletDailyPnl ? archiveWalletDailyPnl[target] : undefined;
    const archiveRows = walletArchiveDailyPnl.length > 0 ? walletArchiveDailyPnl : inMemoryArchiveRows;
    if (Array.isArray(archiveRows) && archiveRows.length > 0) {
      let running = 0;
      return archiveRows
        .map((row) => {
          const ts = Number(row.ts || 0);
          const net = Number(row.netPnlUsd || 0);
          running += net;
          return {
            ts,
            pnlUsd: running,
          };
        })
        .filter((p) => p.ts > 0) as WalletPnlPoint[];
    }

    if (!filteredWalletTradeHistory.length) return [] as WalletPnlPoint[];

    let running = 0;
    return filteredWalletTradeHistory.map((row) => {
      running += sideToSignedNotional(row.side, row.notionalUsd);
      return {
        ts: row.ts,
        pnlUsd: running,
      };
    });
  }, [filteredWalletTradeHistory, selectedWallet, leaderboardPayload, walletArchiveDailyPnl, walletPairFilter]);

  useEffect(() => {
    const target = normalizeAddress(selectedWallet);
    if (!target || !base || !active) {
      setWalletArchiveDailyPnl([]);
      setWalletArchiveTradeRows([]);
      setWalletArchiveLoading(false);
      setWalletArchiveErr("");
      return;
    }

    const ac = new AbortController();
    setWalletArchiveDailyPnl([]);
    setWalletArchiveTradeRows([]);
    setWalletArchiveLoading(true);
    setWalletArchiveErr("");

    (async () => {
      try {
        const token = await ensureJwt();
        const res = await fetch(`${base}/api/hyperliquid/archive/wallet/${encodeURIComponent(target)}`, {
          headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
          signal: ac.signal,
        });

        if (res.status === 401) {
          clearStoredJwt();
          const token2 = await ensureJwt();
          const retry = await fetch(`${base}/api/hyperliquid/archive/wallet/${encodeURIComponent(target)}`, {
            headers: { Accept: "application/json", Authorization: `Bearer ${token2}` },
            signal: ac.signal,
          });
          if (!retry.ok) throw new Error(`Archive wallet query failed (${retry.status})`);
          const payload2 = (await retry.json()) as ArchiveWalletQueryResponse;
          setWalletArchiveDailyPnl(Array.isArray(payload2.rows) ? payload2.rows : []);
          setWalletArchiveTradeRows(Array.isArray(payload2.tradeRows) ? payload2.tradeRows : []);
          return;
        }

        if (!res.ok) throw new Error(`Archive wallet query failed (${res.status})`);
        const payload = (await res.json()) as ArchiveWalletQueryResponse;
        setWalletArchiveDailyPnl(Array.isArray(payload.rows) ? payload.rows : []);
        setWalletArchiveTradeRows(Array.isArray(payload.tradeRows) ? payload.tradeRows : []);
      } catch (e: any) {
        if (e?.name === "AbortError") return;
        setWalletArchiveDailyPnl([]);
        setWalletArchiveTradeRows([]);
        setWalletArchiveErr(e?.message || "Failed to query archive wallet data");
      } finally {
        if (!ac.signal.aborted) setWalletArchiveLoading(false);
      }
    })();

    return () => {
      ac.abort();
    };
  }, [selectedWallet, base, active]);

  const walletPnlChart = useMemo(() => {
    if (walletPnlHistory.length < 2) return null;

    const width = 760;
    const height = 220;
    const pad = 36;
    const xs = walletPnlHistory.map((p) => p.ts);
    const ys = walletPnlHistory.map((p) => p.pnlUsd);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys, 0);
    const maxY = Math.max(...ys, 0);
    const xSpan = Math.max(1, maxX - minX);
    const ySpan = Math.max(1, maxY - minY);

    const toX = (x: number) => pad + ((x - minX) / xSpan) * (width - pad * 2);
    const toY = (y: number) => pad + ((maxY - y) / ySpan) * (height - pad * 2);

    const points = walletPnlHistory.map((p) => `${toX(p.ts)},${toY(p.pnlUsd)}`).join(" ");
    const zeroY = toY(0);
    const firstX = toX(walletPnlHistory[0].ts);
    const lastX = toX(walletPnlHistory[walletPnlHistory.length - 1].ts);
    const last = walletPnlHistory[walletPnlHistory.length - 1];
    const yTicks = [maxY, (maxY + minY) / 2, minY];

    return {
      width,
      height,
      pad,
      points,
      zeroY,
      firstX,
      lastX,
      last,
      minX,
      maxX,
      minY,
      maxY,
      yTicks,
    };
  }, [walletPnlHistory]);

  const walletArchiveStats = useMemo(() => {
    const target = normalizeAddress(selectedWallet);
    if (!target) return null;
    const archiveLeaderboard = leaderboardPayload?.archiveLeaderboard as
      | {
          topAccounts?: Array<Record<string, unknown>>;
          topAccountsByNotional?: Array<Record<string, unknown>>;
        }
      | undefined;
    if (!archiveLeaderboard) return null;

    const profitRow = (archiveLeaderboard.topAccounts || []).find(
      (row) => normalizeAddress(String(row.address || "")) === target
    );
    const notionalRow = (archiveLeaderboard.topAccountsByNotional || []).find(
      (row) => normalizeAddress(String(row.address || "")) === target
    );

    if (!profitRow && !notionalRow) return null;

    return {
      profitRank: Number(profitRow?.rank || 0),
      notionalRank: Number(notionalRow?.rank || 0),
      realizedPnlUsd: Number(profitRow?.realizedPnlUsd || 0),
      netProfitUsd: Number(profitRow?.netProfitUsd || 0),
      feesUsd: Number(profitRow?.feesUsd || 0),
      notionalUsd: Number(notionalRow?.notionalUsd || profitRow?.notionalUsd || 0),
    };
  }, [leaderboardPayload, selectedWallet]);

  const recentTradesTotalPages = Math.max(1, Math.ceil(majorTrades.length / PAGE_SIZE));
  const pagedRecentTrades = useMemo(() => {
    const start = (recentTradesPage - 1) * PAGE_SIZE;
    return majorTrades.slice(start, start + PAGE_SIZE);
  }, [majorTrades, recentTradesPage]);

  useEffect(() => {
    if (recentTradesPage > recentTradesTotalPages) setRecentTradesPage(recentTradesTotalPages);
  }, [recentTradesPage, recentTradesTotalPages]);

  async function ensureJwt(): Promise<string> {
    if (!base) throw new Error("Missing VITE_INTEL_API_BASE (no backend configured)");
    if (!addr) throw new Error("No wallet connected");

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

  async function checkActive() {
    if (!addr || !subReg) {
      setActive(false);
      setChecking(false);
      return;
    }

    setChecking(true);
    try {
      const ok = await subReg.isActive(addr).call();
      setActive(!!ok);
    } catch {
      setActive(false);
    } finally {
      setChecking(false);
    }
  }

  async function fetchLeaderboard(token: string, signal?: AbortSignal) {
    const res = await fetch(`${base}/api/hyperliquid/leaderboard`, {
      headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
      signal,
    });
    if (!res.ok) throw new Error(`Leaderboard fetch failed (${res.status})`);
    const payload = (await res.json()) as HyperliquidLeaderboardPayload;
    setLeaderboardPayload(payload);
  }

  async function fetchIntel(signal?: AbortSignal) {
    if (!base || !addr) return;

    setLoading(true);
    setErr("");

    try {
      const token = await ensureJwt();
      const res = await fetch(`${base}/api/intel/source/hyperliquid`, {
        headers: { Accept: "application/json", Authorization: `Bearer ${token}` },
        signal,
      });

      if (res.status === 401) {
        clearStoredJwt();
        const token2 = await ensureJwt();
        const res2 = await fetch(`${base}/api/intel/source/hyperliquid`, {
          headers: { Accept: "application/json", Authorization: `Bearer ${token2}` },
          signal,
        });
        if (!res2.ok) throw new Error(`Intel fetch failed (${res2.status})`);
        const json2 = (await res2.json()) as { items?: IntelItem[] };
        setItems(Array.isArray(json2.items) ? json2.items : []);
        await fetchLeaderboard(token2, signal);
        return;
      }

      if (res.status === 403) {
        setErr("Subscription expired. Please renew on Intel tab.");
        setActive(false);
        return;
      }

      if (!res.ok) throw new Error(`Intel fetch failed (${res.status})`);

      const json = (await res.json()) as { items?: IntelItem[] };
      setItems(Array.isArray(json.items) ? json.items : []);
      await fetchLeaderboard(token, signal);
    } catch (e: any) {
      if (e?.name !== "AbortError") setErr(e?.message || "Failed to load Hyperliquid feed");
    } finally {
      setLoading(false);
    }
  }

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

  useEffect(() => {
    checkActive();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addr, subReg]);

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

  if (!addr) {
    return <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>Connect TronLink.</div>;
  }

  if (checking) {
    return <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>Checking subscription…</div>;
  }

  if (!active) {
    return (
      <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
        Hyperliquid feed requires an active subscription. Renew from the Intel tab.
      </div>
    );
  }

  return (
    <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
      <style>{`@keyframes hl-wallet-spin { to { transform: rotate(360deg); } }`}</style>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <div style={{ fontWeight: 900, fontSize: 16 }}>Hyperliquid Trades & Leaderboards</div>
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
          Refresh
        </button>
      </div>

      {err && (
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
          <b>Feed error:</b> {err}
        </div>
      )}

      <div style={{ marginTop: 10, fontSize: 12, opacity: 0.75 }}>
        {loading ? "Loading…" : `${hyperliquidItems.length} Hyperliquid items · ${majorTrades.length} major trades`}
      </div>

      <div style={{ marginTop: 8, fontSize: 12, opacity: 0.75 }}>
        Leaderboard snapshot is loaded from server-side archive file ingestion.
      </div>

      <div style={{ marginTop: 12, padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.08)" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 10 }}>
          <div style={{ fontWeight: 800, fontSize: 13 }}>Leaderboard</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <label style={{ display: "grid", gap: 4, fontSize: 11, opacity: 0.8 }}>
              Timeframe
              <select value={windowKey} onChange={(e) => setWindowKey(e.target.value)}>
                {windowOptions.map((w) => (
                  <option value={w} key={w}>
                    {w}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: "grid", gap: 4, fontSize: 11, opacity: 0.8 }}>
              Trader class
              <select value={traderClassKey} onChange={(e) => setTraderClassKey(e.target.value)}>
                <option value="all">All classes</option>
                {traderClassOptions.map((opt) => (
                  <option value={opt.key} key={opt.key}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
            <label style={{ display: "grid", gap: 4, fontSize: 11, opacity: 0.8 }}>
              Sort by
              <select
                value={leaderboardSortKey}
                onChange={(e) => setLeaderboardSortKey(e.target.value as LeaderboardSortKey)}
              >
                <option value="volume">Volume</option>
                <option value="profit">Profit</option>
                <option value="pnlPercent">PnL %</option>
                <option value="trades">Trades</option>
                <option value="pairs">Pairs</option>
              </select>
            </label>
          </div>
        </div>

        <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(140px,1fr))", gap: 8 }}>
          <label style={{ display: "grid", gap: 4, fontSize: 11, opacity: 0.8 }}>
            Min trades
            <input
              type="number"
              min={0}
              value={minTradesFilter}
              onChange={(e) => setMinTradesFilter(Math.max(0, Number(e.target.value || 0)))}
            />
          </label>
          <label style={{ display: "grid", gap: 4, fontSize: 11, opacity: 0.8 }}>
            Min volume (USD)
            <input
              type="number"
              min={0}
              value={minVolumeFilter}
              onChange={(e) => setMinVolumeFilter(Math.max(0, Number(e.target.value || 0)))}
            />
          </label>
          <label style={{ display: "grid", gap: 4, fontSize: 11, opacity: 0.8 }}>
            Pair contains
            <input value={pairFilter} onChange={(e) => setPairFilter(e.target.value)} placeholder="BTC, ETH..." />
          </label>
        </div>

        {leaderboardRows.length === 0 ? (
          <div style={{ marginTop: 8, fontSize: 12, opacity: 0.8 }}>No leaderboard snapshot yet.</div>
        ) : (
          <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
            {pagedLeaderboardRows.map((row) => (
              <button
                type="button"
                key={`${row.rank}-${row.address}`}
                onClick={() => setWalletQuery(row.address)}
                style={{
                  display: "grid",
                  gridTemplateColumns: "56px 1fr auto auto",
                  gap: 8,
                  fontSize: 12,
                  padding: 8,
                  borderRadius: 10,
                  border: "1px solid rgba(0,0,0,0.08)",
                  background: "white",
                  textAlign: "left",
                  cursor: "pointer",
                }}
              >
                <b>#{row.rank}</b>
                <span style={{ fontFamily: "monospace" }}>{compactAddress(row.address)}</span>
                <span title="Notional traded">Vol: ${formatUsd(Math.abs(row.notionalUsd))}</span>
                <span title="Net profit">
                  PnL: {formatSignedUsd(row.netProfitUsd || 0)} ({asFiniteNumber(row.pnlPercent).toFixed(2)}%)
                </span>
                <span style={{ gridColumn: "2 / span 3", opacity: 0.72 }}>
                  {row.traderClassLabel || row.traderClass || "Unknown"} · Trades: {row.trades || 0} · Pairs: {row.pairCount || row.pairs?.length || 0}
                </span>
              </button>
            ))}
            <MiniPagination page={leaderboardPage} totalPages={leaderboardTotalPages} onChange={setLeaderboardPage} />
          </div>
        )}
      </div>

      <div style={{ marginTop: 12, padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.08)" }}>
        <div style={{ fontWeight: 800, fontSize: 13 }}>Wallet explorer (long-term observed history)</div>
        <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
          <input
            placeholder="Enter wallet address (0x...)"
            value={walletQuery}
            onChange={(e) => setWalletQuery(e.target.value)}
            list="known-hyper-wallets"
            style={{ padding: "8px 10px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.2)", fontFamily: "monospace" }}
          />
          <datalist id="known-hyper-wallets">
            {knownWallets.slice(0, 200).map((wallet) => (
              <option value={wallet} key={wallet} />
            ))}
          </datalist>
          <div style={{ fontSize: 12, opacity: 0.75 }}>
            Uses retained Hyperliquid trade points + archive leaderboard snapshots when available.
          </div>
        </div>

        {selectedWallet ? (
          <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
            <div style={{ fontFamily: "monospace", fontSize: 12, opacity: 0.9 }}>{selectedWallet}</div>

            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <label style={{ display: "grid", gap: 4, fontSize: 12, opacity: 0.85 }}>
                Pair scope
                <select value={walletPairFilter} onChange={(e) => setWalletPairFilter(e.target.value)}>
                  <option value="all">All pairs</option>
                  {walletPairOptions.map((pair) => (
                    <option value={pair} key={pair}>
                      {pair}
                    </option>
                  ))}
                </select>
              </label>
              <div style={{ fontSize: 12, opacity: 0.75 }}>
                {walletPairFilter === "all"
                  ? "Showing global wallet stats across all observed pairs."
                  : `Showing stats for ${walletPairFilter} only.`}
              </div>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))",
                gap: 8,
              }}
            >
              <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                <div style={{ opacity: 0.7 }}>Trades observed</div>
                <div style={{ fontWeight: 800, marginTop: 2 }}>{walletSummary.trades.toLocaleString()}</div>
              </div>
              <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                <div style={{ opacity: 0.7 }}>Total notional</div>
                <div style={{ fontWeight: 800, marginTop: 2 }}>${formatUsd(walletSummary.totalNotionalUsd)}</div>
              </div>
              <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                <div style={{ opacity: 0.7 }}>Buy notional</div>
                <div style={{ fontWeight: 800, marginTop: 2 }}>${formatUsd(walletSummary.buyNotionalUsd)}</div>
              </div>
              <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                <div style={{ opacity: 0.7 }}>Sell notional</div>
                <div style={{ fontWeight: 800, marginTop: 2 }}>${formatUsd(walletSummary.sellNotionalUsd)}</div>
              </div>
              <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                <div style={{ opacity: 0.7 }}>Total PnL</div>
                <div style={{ fontWeight: 800, marginTop: 2 }}>{formatSignedUsd(walletSummary.totalPnlUsd)}</div>
              </div>
              <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                <div style={{ opacity: 0.7 }}>Total PnL %</div>
                <div style={{ fontWeight: 800, marginTop: 2 }}>{formatPercent(walletSummary.totalPnlPercent)}</div>
              </div>
              <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                <div style={{ opacity: 0.7 }}>Sharpe ratio</div>
                <div style={{ fontWeight: 800, marginTop: 2 }}>{walletSummary.sharpeRatio.toFixed(2)}</div>
              </div>
              <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                <div style={{ opacity: 0.7 }}>Most traded pair</div>
                <div style={{ fontWeight: 800, marginTop: 2 }}>{walletSummary.mostTradedPair}</div>
              </div>
              {walletArchiveStats ? (
                <>
                  <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                    <div style={{ opacity: 0.7 }}>Archive net profit</div>
                    <div style={{ fontWeight: 800, marginTop: 2 }}>{formatSignedUsd(walletArchiveStats.netProfitUsd)}</div>
                  </div>
                  <div style={{ padding: 8, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}>
                    <div style={{ opacity: 0.7 }}>Archive realized PnL</div>
                    <div style={{ fontWeight: 800, marginTop: 2 }}>{formatSignedUsd(walletArchiveStats.realizedPnlUsd)}</div>
                  </div>
                </>
              ) : null}
            </div>

            {walletArchiveStats ? (
            <div style={{ fontSize: 12, opacity: 0.78 }}>
              Archive ranks → Profit #{walletArchiveStats.profitRank || "n/a"} · Notional #{walletArchiveStats.notionalRank || "n/a"}
            </div>
          ) : (
              <div style={{ fontSize: 12, opacity: 0.78 }}>
                No archive leaderboard row found for this wallet in retained top-account slices.
              </div>
            )}

            {walletArchiveLoading ? <LoadingSpinner label="Querying archive parquet for this wallet…" /> : null}
            {walletArchiveErr ? (
              <div style={{ fontSize: 12, color: "#b91c1c" }}>{walletArchiveErr}</div>
            ) : null}

            <div>
              <div style={{ fontWeight: 700, fontSize: 12 }}>Profit/loss history (archive daily net PnL when available)</div>
              {walletPnlChart ? (
                <div style={{ marginTop: 6, border: "1px solid rgba(0,0,0,0.08)", borderRadius: 10, padding: 8 }}>
                  <svg viewBox={`0 0 ${walletPnlChart.width} ${walletPnlChart.height}`} width="100%" height="220" role="img" aria-label="Wallet profit and loss history chart">
                    <line
                      x1={walletPnlChart.pad}
                      x2={walletPnlChart.pad}
                      y1={walletPnlChart.pad}
                      y2={walletPnlChart.height - walletPnlChart.pad}
                      stroke="rgba(0,0,0,0.18)"
                    />
                    <line
                      x1={walletPnlChart.pad}
                      x2={walletPnlChart.width - walletPnlChart.pad}
                      y1={walletPnlChart.height - walletPnlChart.pad}
                      y2={walletPnlChart.height - walletPnlChart.pad}
                      stroke="rgba(0,0,0,0.18)"
                    />
                    {walletPnlChart.yTicks.map((tick, idx) => {
                      const y = walletPnlChart.pad + ((walletPnlChart.maxY - tick) / Math.max(1, walletPnlChart.maxY - walletPnlChart.minY)) * (walletPnlChart.height - walletPnlChart.pad * 2);
                      return (
                        <g key={`y-${idx}`}>
                          <line
                            x1={walletPnlChart.pad}
                            x2={walletPnlChart.width - walletPnlChart.pad}
                            y1={y}
                            y2={y}
                            stroke="rgba(0,0,0,0.1)"
                            strokeDasharray="3 3"
                          />
                          <text x={walletPnlChart.pad - 6} y={y + 4} textAnchor="end" style={{ fontSize: 10, fill: "rgba(0,0,0,0.68)" }}>
                            {formatSignedUsd(tick)}
                          </text>
                        </g>
                      );
                    })}
                    <line
                      x1={walletPnlChart.firstX}
                      x2={walletPnlChart.lastX}
                      y1={walletPnlChart.zeroY}
                      y2={walletPnlChart.zeroY}
                      stroke="rgba(0,0,0,0.28)"
                      strokeDasharray="4 4"
                    />
                    <polyline fill="none" stroke="rgba(37,99,235,0.95)" strokeWidth="2" points={walletPnlChart.points} />
                    <text x={walletPnlChart.pad} y={walletPnlChart.height - 8} textAnchor="start" style={{ fontSize: 10, fill: "rgba(0,0,0,0.7)" }}>
                      {itemTime(walletPnlChart.minX)}
                    </text>
                    <text x={walletPnlChart.width - walletPnlChart.pad} y={walletPnlChart.height - 8} textAnchor="end" style={{ fontSize: 10, fill: "rgba(0,0,0,0.7)" }}>
                      {itemTime(walletPnlChart.maxX)}
                    </text>
                  </svg>
                  <div style={{ marginTop: 4, fontSize: 11, opacity: 0.8, display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <span>Range: {formatSignedUsd(walletPnlChart.minY)} → {formatSignedUsd(walletPnlChart.maxY)}</span>
                    <span>Latest: {formatSignedUsd(walletPnlChart.last.pnlUsd)} ({itemTime(walletPnlChart.last.ts)})</span>
                  </div>
                </div>
              ) : (
                <div style={{ marginTop: 6, fontSize: 12, opacity: 0.8 }}>Need at least 2 points to render chart (archive daily PnL or retained trades).</div>
              )}
            </div>

            <div>
              <div style={{ fontWeight: 700, fontSize: 12 }}>Buy/sell trade history</div>
              <div style={{ marginTop: 4, fontSize: 11, opacity: 0.78 }}>
                Side code legend from archive data: <b>S = Sell (ask)</b>, <b>B = Buy (bid)</b>.
              </div>
              {walletTradeHistoryDesc.length === 0 ? (
                <div style={{ marginTop: 6, fontSize: 12, opacity: 0.8 }}>No archive trade history for this wallet yet.</div>
              ) : (
                <div style={{ marginTop: 6, border: "1px solid rgba(0,0,0,0.08)", borderRadius: 10, overflow: "hidden" }}>
                  <div style={{ maxHeight: 380, overflowY: "auto" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
                      <thead style={{ position: "sticky", top: 0, background: "#f8fafc", zIndex: 1 }}>
                        <tr>
                          <th style={{ textAlign: "left", padding: "8px 10px", borderBottom: "1px solid rgba(0,0,0,0.08)" }}>Time</th>
                          <th style={{ textAlign: "left", padding: "8px 10px", borderBottom: "1px solid rgba(0,0,0,0.08)" }}>Pair</th>
                          <th style={{ textAlign: "left", padding: "8px 10px", borderBottom: "1px solid rgba(0,0,0,0.08)" }}>Side</th>
                          <th style={{ textAlign: "right", padding: "8px 10px", borderBottom: "1px solid rgba(0,0,0,0.08)" }}>Price</th>
                          <th style={{ textAlign: "right", padding: "8px 10px", borderBottom: "1px solid rgba(0,0,0,0.08)" }}>Amount</th>
                          <th style={{ textAlign: "right", padding: "8px 10px", borderBottom: "1px solid rgba(0,0,0,0.08)" }}>Notional</th>
                          <th style={{ textAlign: "left", padding: "8px 10px", borderBottom: "1px solid rgba(0,0,0,0.08)" }}>Role</th>
                          <th style={{ textAlign: "right", padding: "8px 10px", borderBottom: "1px solid rgba(0,0,0,0.08)" }}>Fee</th>
                        </tr>
                      </thead>
                      <tbody>
                        {pagedWalletTradeHistory.map((trade, idx) => (
                          <tr key={`${trade.tradeId}-${trade.ts}-${idx}`}>
                            <td style={{ padding: "7px 10px", borderBottom: "1px solid rgba(0,0,0,0.05)", whiteSpace: "nowrap", opacity: 0.86 }}>{itemTime(trade.ts)}</td>
                            <td style={{ padding: "7px 10px", borderBottom: "1px solid rgba(0,0,0,0.05)", whiteSpace: "nowrap" }}>{trade.market || "-"}</td>
                            <td style={{ padding: "7px 10px", borderBottom: "1px solid rgba(0,0,0,0.05)", whiteSpace: "nowrap" }}>
                              <b>{trade.sideLabel}</b> <span style={{ opacity: 0.65 }}>({trade.sideCode})</span>
                            </td>
                            <td style={{ padding: "7px 10px", borderBottom: "1px solid rgba(0,0,0,0.05)", textAlign: "right", whiteSpace: "nowrap" }}>${trade.price.toLocaleString()}</td>
                            <td style={{ padding: "7px 10px", borderBottom: "1px solid rgba(0,0,0,0.05)", textAlign: "right" }}>{trade.size.toLocaleString(undefined, { maximumFractionDigits: 6 })}</td>
                            <td style={{ padding: "7px 10px", borderBottom: "1px solid rgba(0,0,0,0.05)", textAlign: "right", whiteSpace: "nowrap" }}>${formatUsd(trade.notionalUsd)}</td>
                            <td style={{ padding: "7px 10px", borderBottom: "1px solid rgba(0,0,0,0.05)", whiteSpace: "nowrap" }}>{trade.tradeRole}</td>
                            <td style={{ padding: "7px 10px", borderBottom: "1px solid rgba(0,0,0,0.05)", textAlign: "right", whiteSpace: "nowrap" }}>
                              {trade.feeUsd ? `$${formatUsd(Math.abs(trade.feeUsd))}` : "-"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div style={{ padding: "0 10px 10px" }}>
                    <MiniPagination page={walletTradePage} totalPages={walletTradeTotalPages} onChange={setWalletTradePage} />
                  </div>
                </div>
              )}
            </div>
          </div>
        ) : (
          <div style={{ marginTop: 8, fontSize: 12, opacity: 0.8 }}>
            Select a wallet from the leaderboard or recent trades to inspect its long-term observed activity.
          </div>
        )}
      </div>

      <div style={{ marginTop: 12, padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.08)" }}>
        <div style={{ fontWeight: 800, fontSize: 13 }}>Recent major trades</div>
        {majorTrades.length === 0 ? (
          <div style={{ marginTop: 8, fontSize: 12, opacity: 0.8 }}>No major trade items yet.</div>
        ) : (
          <div style={{ marginTop: 8, display: "grid", gap: 8 }}>
            {pagedRecentTrades.map((it, idx) => {
              const market = String(it.data?.market || "?");
              const side = String(it.data?.side || "?");
              const notional = Number(it.data?.notionalUsd || 0);
              const size = Number(it.data?.size || 0);
              const price = Number(it.data?.price || 0);

              return (
                <div
                  key={it.id || `${it.ts}-${idx}`}
                  style={{ padding: 10, borderRadius: 10, border: "1px solid rgba(0,0,0,0.08)", fontSize: 12 }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <b>
                      {market} {side}
                    </b>
                    <span style={{ opacity: 0.7 }}>{itemTime(it.ts)}</span>
                  </div>
                  <div style={{ marginTop: 4, opacity: 0.9 }}>
                    ${formatUsd(notional)} notional · {size.toLocaleString()} @ ${price.toLocaleString()}
                  </div>
                  {it.body ? <div style={{ marginTop: 4, opacity: 0.75 }}>{it.body}</div> : null}
                </div>
              );
            })}
            <MiniPagination page={recentTradesPage} totalPages={recentTradesTotalPages} onChange={setRecentTradesPage} />
          </div>
        )}
      </div>
    </div>
  );
}
