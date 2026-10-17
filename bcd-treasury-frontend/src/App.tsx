// src/App.tsx
import { useEffect, useMemo, useState } from "react";
import toast, { Toaster } from "react-hot-toast";
import { Link, Route, Routes, useLocation } from "react-router-dom";

import { CONFIG } from "./config";
import {
  waitForTronWeb,
  shorten,
  requestTronAccounts,
  getCurrentAddressBase58,
  getCurrentNodeHost,
} from "./utils/tron";

import {
  TreasuryVaultV2_ABI,
  SubscriptionRegistryTRXV3_ABI,
  BudgetModuleV3_ABI,
} from "./contracts/abis";

import SubscriptionPage from "./pages/SubscriptionPage";
import IntelPage from "./pages/IntelPage";
import AdminPage from "./pages/AdminPage";
import HyperliquidPage from "./pages/HyperliquidPage";
import PolymarketPage from "./pages/PolymarketPage";

const DEFAULT_ADMIN_ROLE =
  "0x0000000000000000000000000000000000000000000000000000000000000000";

function Nav({ isAdmin }: { isAdmin: boolean }) {
  const loc = useLocation();

  const pill = (active: boolean) => ({
    padding: "8px 10px",
    borderRadius: 999,
    border: "1px solid rgba(0,0,0,0.18)",
    background: active ? "rgba(0,0,0,0.06)" : "white",
    cursor: "pointer",
    fontWeight: 800 as const,
    fontSize: 12,
    textDecoration: "none",
    color: "black",
  });

  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
      <Link to="/" style={pill(loc.pathname === "/")}>
        Subscribe
      </Link>
      <Link to="/intel" style={pill(loc.pathname === "/intel")}>
        Intel
      </Link>
      <Link to="/hyperliquid" style={pill(loc.pathname === "/hyperliquid")}>
        Hyperliquid
      </Link>
      <Link to="/polymarket" style={pill(loc.pathname.startsWith("/polymarket"))}>
        Polymarket
      </Link>
      {isAdmin && (
        <Link to="/admin" style={pill(loc.pathname === "/admin")}>
          Admin
        </Link>
      )}
    </div>
  );
}

export default function App() {
  const [tronWeb, setTronWeb] = useState<any | null>(null);
  const [addr, setAddr] = useState<string>("");
  const [net, setNet] = useState<string>("");
  const [isAdmin, setIsAdmin] = useState<boolean>(false);

  const contracts = useMemo(() => {
    if (!tronWeb) return null;
    return {
      treasury: tronWeb.contract(TreasuryVaultV2_ABI, CONFIG.addresses.treasury),
      subReg: tronWeb.contract(
        SubscriptionRegistryTRXV3_ABI,
        CONFIG.addresses.subReg
      ),
      budget: tronWeb.contract(
        BudgetModuleV3_ABI,
        CONFIG.addresses.budgetModule
      ),
    };
  }, [tronWeb]);

  async function connect() {
    try {
      if (window.tronLink) {
        try {
          await requestTronAccounts();
        } catch {
          // user may reject; keep app usable
        }
      }

      const tw = await waitForTronWeb();
      setTronWeb(tw);

      const base58 = tw.defaultAddress?.base58 || "";
      setAddr(base58);

      const host: string = tw.fullNode?.host || "";
      setNet(host.includes("nile") ? "Nile" : host || "Unknown");

      if (base58) toast.success(`Connected: ${shorten(base58)}`);
    } catch (e: any) {
      toast.error(e?.message || "Failed to connect TronLink");
    }
  }

  // Auto-connect + poll for account/network changes
  useEffect(() => {
    let stopped = false;

    (async () => {
      try {
        if (window.tronLink) {
          try {
            await requestTronAccounts();
          } catch {
            // ignore rejection
          }
        }

        const tw = await waitForTronWeb(1500);
        if (stopped) return;

        setTronWeb(tw);
        setAddr(tw.defaultAddress?.base58 || "");

        const host = tw.fullNode?.host || "";
        setNet(host.includes("nile") ? "Nile" : host || "Unknown");
      } catch {
        // ignore
      }
    })();

    const interval = setInterval(() => {
      if (stopped) return;

      const tw = (window as any).tronWeb;
      if (!tw || !tw.ready) return;

      setTronWeb((prev: any | null) => prev || tw);

      const nextAddr = getCurrentAddressBase58();
      setAddr((prev) => (prev !== nextAddr ? nextAddr : prev));

      const host = getCurrentNodeHost();
      const nextNet = host.includes("nile") ? "Nile" : host || "Unknown";
      setNet((prev) => (prev !== nextNet ? nextNet : prev));
    }, 1000);

    return () => {
      stopped = true;
      clearInterval(interval);
    };
  }, []);

  // Admin detection (on-chain) — controls whether Admin tab/route appears
  useEffect(() => {
    let cancelled = false;

    async function checkAdmin() {
      try {
        if (!addr || !contracts?.treasury) {
          if (!cancelled) setIsAdmin(false);
          return;
        }
        const meHex = tronWeb?.address?.toHex ? tronWeb.address.toHex(addr) : addr;
        const ok = await contracts.treasury.hasRole(DEFAULT_ADMIN_ROLE, meHex).call();
        if (!cancelled) setIsAdmin(!!ok);
      } catch {
        if (!cancelled) setIsAdmin(false);
      }
    }

    checkAdmin();
    return () => {
      cancelled = true;
    };
  }, [addr, contracts?.treasury]);

  return (
    <div
      style={{
        fontFamily: "ui-sans-serif, system-ui",
        padding: 20,
        maxWidth: 980,
        margin: "0 auto",
      }}
    >
      <Toaster position="top-right" />

      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          gap: 12,
          alignItems: "center",
          marginBottom: 14,
        }}
      >
        <div>
          <div style={{ fontSize: 18, fontWeight: 900 }}>{CONFIG.appName}</div>

          <div style={{ fontSize: 12, opacity: 0.8 }}>
            Treasury: {CONFIG.addresses.treasury} • SubReg:{" "}
            {CONFIG.addresses.subReg} • Budget: {CONFIG.addresses.budgetModule}
          </div>

          <div style={{ marginTop: 10 }}>
            <Nav isAdmin={isAdmin} />
          </div>
        </div>

        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 12, opacity: 0.8 }}>Network: {net || "-"}</div>
          <div style={{ fontSize: 12, opacity: 0.8 }}>
            Wallet: {addr ? shorten(addr) : "Not connected"}
          </div>

          <button
            onClick={connect}
            disabled={!!addr}
            style={{
              marginTop: 6,
              padding: "8px 12px",
              borderRadius: 10,
              border: "1px solid rgba(0,0,0,0.2)",
              background: "white",
              cursor: addr ? "default" : "pointer",
              fontWeight: 700,
              opacity: addr ? 0.6 : 1,
            }}
          >
            {addr ? "Connected" : "Connect / Unlock"}
          </button>

          {addr && isAdmin && (
            <div style={{ marginTop: 6, fontSize: 12, fontWeight: 800 }}>
              Admin: Yes
            </div>
          )}
        </div>
      </header>

      {!addr && (
        <div
          style={{
            padding: 14,
            border: "1px solid rgba(0,0,0,0.12)",
            borderRadius: 12,
            marginBottom: 14,
          }}
        >
          <div style={{ fontWeight: 900, marginBottom: 6 }}>TronLink required</div>
          <div style={{ fontSize: 13, opacity: 0.85 }}>
            Install TronLink, switch to <b>Nile</b>, unlock, then refresh the page.
          </div>
        </div>
      )}

      <Routes>
        <Route
          path="/"
          element={
            <SubscriptionPage
              tronWeb={tronWeb}
              addr={addr}
              contract={contracts?.subReg}
            />
          }
        />
        <Route path="/intel" element={<IntelPage addr={addr} subReg={contracts?.subReg} />} />
        <Route
          path="/hyperliquid"
          element={<HyperliquidPage addr={addr} subReg={contracts?.subReg} />}
        />

        <Route
          path="/polymarket"
          element={<PolymarketPage addr={addr} subReg={contracts?.subReg} />}
        />

        {isAdmin && (
          <Route
            path="/admin"
            element={
              <AdminPage
                addr={addr}
                tronWeb={tronWeb}
                treasury={contracts?.treasury}
                subReg={contracts?.subReg}
                budget={contracts?.budget}
              />
            }
          />
        )}

        {/* Optional: simple fallback */}
        <Route
          path="*"
          element={
            <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
              <div style={{ fontWeight: 900, fontSize: 16 }}>Not Found</div>
              <div style={{ marginTop: 8, fontSize: 13, opacity: 0.85 }}>
                This page doesn’t exist.
              </div>
            </div>
          }
        />
      </Routes>

      <footer style={{ marginTop: 18, fontSize: 12, opacity: 0.7 }}>
        MVP UI. Uses TronLink injected tronWeb. No keys stored.
      </footer>
    </div>
  );
}