// src/pages/AdminPage.tsx
import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { CONFIG } from "../config";
import { shorten, toUintString, sunToTRX } from "../utils/tron";

const DEFAULT_ADMIN_ROLE =
  "0x0000000000000000000000000000000000000000000000000000000000000000";

// Fallback keccak256 results (standard EVM keccak256 of the utf-8 strings)
const FALLBACK_GUARDIAN_ROLE =
  "0x8f4f2f27f2e0a7f7b64d2b6b4b0d6d1cfb7c6f35b6d4d2f6a393d469cafcda44";
const FALLBACK_MODULE_ROLE =
  "0x5b5e2b74b9a1c3d4d0a8b5d9c0e7b2f6f1c3a3b1e0d9a0b2c8c4b0fd3f9e1b1a";

function isBase58TronAddress(s: string): boolean {
  if (!s) return false;
  if (s[0] !== "T") return false;
  if (s.length < 30 || s.length > 36) return false;
  return true;
}

function fmtBool(b: any) {
  return b ? "Yes" : "No";
}

function hexToB58(tronWeb: any, maybeHexOrB58: string): string {
  try {
    if (!tronWeb || !maybeHexOrB58) return maybeHexOrB58 || "";
    if (maybeHexOrB58.startsWith("T")) return maybeHexOrB58;
    if (maybeHexOrB58.startsWith("41") && maybeHexOrB58.length === 42) {
      return tronWeb.address.fromHex(maybeHexOrB58);
    }
    return maybeHexOrB58;
  } catch {
    return maybeHexOrB58;
  }
}

function toHex(tronWeb: any, addr: string): string {
  try {
    if (!tronWeb || !addr) return addr;
    if (addr.startsWith("41") && addr.length === 42) return addr;
    if (addr.startsWith("T")) return tronWeb.address.toHex(addr);
    return addr;
  } catch {
    return addr;
  }
}

function isNode503(e: any): boolean {
  const status = e?.response?.status;
  const msg = String(e?.message || "");
  return (
    status === 503 ||
    msg.includes("status code 503") ||
    msg.includes("503 (Service Unavailable)") ||
    msg.includes("Service Unavailable")
  );
}

function extractErr(e: any): string {
  if (!e) return "Unknown error";
  if (typeof e === "string") return e;
  const parts: string[] = [];
  if (e.message) parts.push(String(e.message));
  if (e.error) parts.push(String(e.error));
  if (e?.data?.message) parts.push(String(e.data.message));
  if (e?.data?.Error) parts.push(String(e.data.Error));
  if (e?.transaction?.txID) parts.push(`txID: ${e.transaction.txID}`);
  try {
    const s = JSON.stringify(e);
    if (s && s !== "{}") parts.push(s);
  } catch {}
  return parts.filter(Boolean).join(" | ") || "Transaction failed";
}

async function runAction(label: string, fn: () => Promise<any>) {
  const id = toast.loading(`${label}…`);
  try {
    const res = await fn();
    toast.success(`${label}: success`, { id });
    return { ok: true as const, res };
  } catch (e: any) {
    console.error(`${label} error:`, e);
    toast.error(`${label}: ${extractErr(e)}`, { id });
    return { ok: false as const, err: e };
  }
}

export default function AdminPage({
  addr,
  tronWeb,
  treasury,
  subReg,
  budget,
}: {
  addr: string;
  tronWeb: any;
  treasury: any;
  subReg: any;
  budget: any;
}) {
  const canUse = useMemo(
    () => !!addr && !!tronWeb && !!treasury && !!subReg && !!budget,
    [addr, tronWeb, treasury, subReg, budget]
  );

  const [busy, setBusy] = useState(false);
  const [nodeDown, setNodeDown] = useState(false);
  const [lastNodeError, setLastNodeError] = useState<string>("");

  // ✅ Post-tx delay to avoid refresh racing RPC/indexers (503 right after broadcast)
  const POST_TX_REFRESH_DELAY_MS = 4000;
  const sleep = (ms: number) =>
    new Promise<void>((resolve) => setTimeout(resolve, ms));
  
  // Prevent overlapping refreshes (button + effects + post-tx)
  const [refreshing, setRefreshing] = useState(false);

  function jitter(ms: number) {
    // +/- 25% jitter to avoid thundering herd
    const d = Math.floor(ms * 0.25);
    return ms + (Math.floor(Math.random() * (d * 2 + 1)) - d);
  }



  // Role ids
  const [ROLE_ADMIN] = useState<string>(DEFAULT_ADMIN_ROLE);
  const [ROLE_GUARDIAN, setROLE_GUARDIAN] = useState<string>("");
  const [ROLE_MODULE, setROLE_MODULE] = useState<string>("");

  const [meIsAdmin, setMeIsAdmin] = useState(false);
  const [meIsGuardian, setMeIsGuardian] = useState(false);
  const [meIsModule, setMeIsModule] = useState(false);

  // Subscription Registry state
  const [subPaused, setSubPaused] = useState(false);
  const [trxUsdPriceE6, setTrxUsdPriceE6] = useState("0");
  const [monthlyPriceUsdE6, setMonthlyPriceUsdE6] = useState("0");
  const [subOwner, setSubOwner] = useState("");
  const [subTreasury, setSubTreasury] = useState("");

  const [newTrxUsdE6, setNewTrxUsdE6] = useState("");
  const [newMonthlyUsdE6, setNewMonthlyUsdE6] = useState("");
  const [newSubTreasury, setNewSubTreasury] = useState("");
  const [newSubOwner, setNewSubOwner] = useState("");

  // Vault state + emergency
  const [vaultPaused, setVaultPaused] = useState(false);
  const [vaultTrxBalSun, setVaultTrxBalSun] = useState("0");

  const [sweepTo, setSweepTo] = useState(addr || "");
  const [sweepTrxSun, setSweepTrxSun] = useState("");
  const [sweepTokenAddr, setSweepTokenAddr] = useState("");
  const [sweepTokenTo, setSweepTokenTo] = useState(addr || "");
  const [sweepTokenAmount, setSweepTokenAmount] = useState("");

  // Role mgmt inputs
  const [roleTarget, setRoleTarget] = useState("");
  const [roleWhich, setRoleWhich] = useState<"ADMIN" | "GUARDIAN" | "MODULE">(
    "MODULE"
  );
  const [roleAction, setRoleAction] = useState<"GRANT" | "REVOKE">("GRANT");

  // Module limits
  const [limitModuleAddr, setLimitModuleAddr] = useState(
    CONFIG.addresses.budgetModule
  );

  const [trxLimEnabled, setTrxLimEnabled] = useState(true);
  const [trxLimMaxSun, setTrxLimMaxSun] = useState("");
  const [trxLimPeriodSec, setTrxLimPeriodSec] = useState("2592000");
  const [trxLimRead, setTrxLimRead] = useState<any>(null);

  const [tokAddr, setTokAddr] = useState("");
  const [tokLimEnabled, setTokLimEnabled] = useState(true);
  const [tokLimMax, setTokLimMax] = useState("");
  const [tokLimPeriodSec, setTokLimPeriodSec] = useState("2592000");
  const [tokLimRead, setTokLimRead] = useState<any>(null);

  // Budget module read + admin inputs
  const [budPaused, setBudPaused] = useState(false);
  const [budTreasury, setBudTreasury] = useState("");
  const [budPayee, setBudPayee] = useState("");
  const [budToken, setBudToken] = useState("");
  const [budCap, setBudCap] = useState("0");
  const [budPeriod, setBudPeriod] = useState("0");
  const [budEpochStart, setBudEpochStart] = useState("0");
  const [budSpentEpoch, setBudSpentEpoch] = useState("0");

  const [newBudTreasury, setNewBudTreasury] = useState("");
  const [newBudPayee, setNewBudPayee] = useState("");
  const [newBudToken, setNewBudToken] = useState("");
  const [newBudCap, setNewBudCap] = useState("");
  const [newBudPeriod, setNewBudPeriod] = useState("");

  // Compute role ids (prefer tronWeb.sha3 if present)
  useEffect(() => {
    if (!tronWeb) return;
    try {
      const sha3 = tronWeb.sha3?.bind(tronWeb);
      if (sha3) {
        const g = sha3("GUARDIAN_ROLE");
        const m = sha3("MODULE_ROLE");
        setROLE_GUARDIAN(g || FALLBACK_GUARDIAN_ROLE);
        setROLE_MODULE(m || FALLBACK_MODULE_ROLE);
      } else {
        setROLE_GUARDIAN(FALLBACK_GUARDIAN_ROLE);
        setROLE_MODULE(FALLBACK_MODULE_ROLE);
      }
    } catch {
      setROLE_GUARDIAN(FALLBACK_GUARDIAN_ROLE);
      setROLE_MODULE(FALLBACK_MODULE_ROLE);
    }
  }, [tronWeb]);


  
  async function refreshAll(showToast = false) {
    if (!canUse) return;

    // single-flight: if a refresh is running, don't start another
    if (refreshing) return;

    setRefreshing(true);

    const meHex = toHex(tronWeb, addr);
    const id = showToast ? toast.loading("Refreshing…") : undefined;

    // "Persistent 503" thresholds (within THIS refresh call)
    const PERSIST_503_TRIES = 4;
    const PERSIST_503_MS = 9000;

    // Retry / backoff
    const tries = 6;
    const baseDelay = 700; // ms
    const maxDelay = 6000; // ms

    // Track 503 persistence within this refresh attempt
    let consecutive503 = 0;
    let first503At: number | null = null;

    const isPersistent503 = () => {
      const now = Date.now();
      const elapsed = first503At ? now - first503At : 0;
      return consecutive503 >= PERSIST_503_TRIES || elapsed >= PERSIST_503_MS;
    };

    try {
      let lastErr: any = null;

      for (let attempt = 0; attempt < tries; attempt++) {
        try {
          const [
            // role membership
            rAdmin,
            rGuardian,
            rModule,

            // vault
            vp,
            vb,

            // subReg
            sp,
            p,
            m,
            so,
            st,

            // budget
            bp,
            bt,
            bpay,
            btok,
            bcap,
            bper,
            bes,
            bse,
          ] = await Promise.all([
            treasury.hasRole(ROLE_ADMIN, meHex).call(),
            treasury
              .hasRole(ROLE_GUARDIAN || FALLBACK_GUARDIAN_ROLE, meHex)
              .call(),
            treasury.hasRole(ROLE_MODULE || FALLBACK_MODULE_ROLE, meHex).call(),

            treasury.paused().call(),
            treasury.trxBalance().call(),

            subReg.paused().call(),
            subReg.trxUsdPriceE6().call(),
            subReg.monthlyPriceUsdE6().call(),
            subReg.owner().call(),
            subReg.treasury().call(),

            budget.paused().call(),
            budget.treasury().call(),
            budget.payee().call(),
            budget.token().call(),
            budget.capPerPeriod().call(),
            budget.periodSeconds().call(),
            budget.epochStart().call(),
            budget.spentThisEpoch().call(),
          ]);

          // success -> commit state
          setMeIsAdmin(!!rAdmin);
          setMeIsGuardian(!!rGuardian);
          setMeIsModule(!!rModule);

          setVaultPaused(!!vp);
          setVaultTrxBalSun(toUintString(vb));

          setSubPaused(!!sp);
          setTrxUsdPriceE6(toUintString(p));
          setMonthlyPriceUsdE6(toUintString(m));
          setSubOwner(hexToB58(tronWeb, String(so)));
          setSubTreasury(hexToB58(tronWeb, String(st)));

          setBudPaused(!!bp);
          setBudTreasury(hexToB58(tronWeb, String(bt)));
          setBudPayee(hexToB58(tronWeb, String(bpay)));
          setBudToken(hexToB58(tronWeb, String(btok)));
          setBudCap(toUintString(bcap));
          setBudPeriod(toUintString(bper));
          setBudEpochStart(toUintString(bes));
          setBudSpentEpoch(toUintString(bse));

          // clear node error UI on any success
          setNodeDown(false);
          setLastNodeError("");

          if (id) toast.success("Refresh: success", { id });
          return;
        } catch (e: any) {
          lastErr = e;

          // only retry on 503
          if (!isNode503(e)) throw e;

          const now = Date.now();
          if (first503At == null) first503At = now;
          consecutive503 += 1;

          // Option A: only show red error UI if 503 persists
          if (isPersistent503()) {
            setNodeDown(true);
            setLastNodeError(
              "TronGrid Nile returned 503 repeatedly. Reads are temporarily unavailable."
            );
          } else {
            // transient -> keep UI calm (no red box)
            setNodeDown(false);
            setLastNodeError("");
          }

          // backoff + jitter
          const delay = Math.min(maxDelay, baseDelay * Math.pow(2, attempt));
          await sleep(jitter(delay));
          continue;
        }
      }

      // exhausted retries
      throw lastErr;
    } catch (e: any) {
      console.error("refreshAll error:", e);

      if (isNode503(e)) {
        // After all retries, treat as persistent (now show UI + toast)
        setNodeDown(true);
        setLastNodeError(
          "TronGrid Nile returned 503 repeatedly. Reads are temporarily unavailable."
        );

        if (id) toast.error("Refresh failed: node 503 (TronGrid)", { id });
        else toast.error("Refresh failed: node 503 (TronGrid)");
      } else {
        setNodeDown(false);
        setLastNodeError(extractErr(e));
        if (id) toast.error(`Refresh failed: ${extractErr(e)}`, { id });
        else toast.error(`Refresh failed: ${extractErr(e)}`);
      }
    } finally {
      setRefreshing(false);
    }
  }

  // ✅ FIXED: no recursion. Wait a bit after tx, then run the same refresh path.
  async function refreshAfterTx() {
    if (!canUse) return;

    // Give the chain/indexer time to include the tx, then refresh with retries
    await sleep(POST_TX_REFRESH_DELAY_MS);
    await refreshAll(false);
  }

  useEffect(() => {
    refreshAll(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canUse, ROLE_GUARDIAN, ROLE_MODULE]);

  async function readTrxLimit() {
    if (!canUse) return;
    const modB58 = limitModuleAddr.trim();
    if (!isBase58TronAddress(modB58))
      return toast.error("Invalid module address");
    const modHex = toHex(tronWeb, modB58);

    await runAction("Read TRX limit", async () => {
      const lim = await treasury.trxLimit(modHex).call();
      setTrxLimRead({
        enabled: !!lim.enabled,
        periodSeconds: toUintString(lim.periodSeconds),
        periodStart: toUintString(lim.periodStart),
        maxPerPeriod: toUintString(lim.maxPerPeriod),
        spentThisPeriod: toUintString(lim.spentThisPeriod),
      });
      return lim;
    });
  }

  async function readTokenLimit() {
    if (!canUse) return;
    const modB58 = limitModuleAddr.trim();
    const tokenB58 = tokAddr.trim();
    if (!isBase58TronAddress(modB58))
      return toast.error("Invalid module address");
    if (!isBase58TronAddress(tokenB58))
      return toast.error("Invalid token address");

    const modHex = toHex(tronWeb, modB58);
    const tokenHex = toHex(tronWeb, tokenB58);

    await runAction("Read token limit", async () => {
      const lim = await treasury.tokenLimit(tokenHex, modHex).call();
      setTokLimRead({
        enabled: !!lim.enabled,
        periodSeconds: toUintString(lim.periodSeconds),
        periodStart: toUintString(lim.periodStart),
        maxPerPeriod: toUintString(lim.maxPerPeriod),
        spentThisPeriod: toUintString(lim.spentThisPeriod),
      });
      return lim;
    });
  }

  // -----------------------------
  // Actions
  // -----------------------------
  async function subPauseToggle(pause: boolean) {
    if (!canUse) return;
    setBusy(true);
    const out = await runAction(
      pause ? "SubReg pause" : "SubReg unpause",
      async () => {
        return pause
          ? await subReg.pause().send()
          : await subReg.unpause().send();
      }
    );
    setBusy(false);
    if (out.ok) await refreshAfterTx();
  }

  async function subSetPrices() {
    if (!canUse) return;
    const p = newTrxUsdE6.trim();
    const m = newMonthlyUsdE6.trim();
    if (!p && !m) return toast.error("Enter at least one value");
    setBusy(true);

    const out = await runAction("SubReg update pricing", async () => {
      if (p) await subReg.setTrxUsdPriceE6(p).send();
      if (m) await subReg.setMonthlyPriceUsdE6(m).send();
      return true;
    });

    setBusy(false);
    if (out.ok) {
      setNewTrxUsdE6("");
      setNewMonthlyUsdE6("");
      await refreshAfterTx();
    }
  }

  async function subSetTreasury() {
    if (!canUse) return;
    const tB58 = newSubTreasury.trim();
    if (!isBase58TronAddress(tB58))
      return toast.error("Invalid treasury address");
    const tHex = toHex(tronWeb, tB58);

    setBusy(true);
    const out = await runAction("SubReg set treasury", async () => {
      return await subReg.setTreasury(tHex).send();
    });
    setBusy(false);
    if (out.ok) {
      setNewSubTreasury("");
      await refreshAfterTx();
    }
  }

  async function subTransferOwnership() {
    if (!canUse) return;
    const oB58 = newSubOwner.trim();
    if (!isBase58TronAddress(oB58))
      return toast.error("Invalid new owner address");
    const oHex = toHex(tronWeb, oB58);

    setBusy(true);
    const out = await runAction("SubReg transfer ownership", async () => {
      return await subReg.transferOwnership(oHex).send();
    });
    setBusy(false);
    if (out.ok) {
      setNewSubOwner("");
      await refreshAfterTx();
    }
  }

  async function vaultPauseToggle(pause: boolean) {
    if (!canUse) return;
    setBusy(true);
    const out = await runAction(
      pause ? "Vault pause" : "Vault unpause",
      async () => {
        return pause
          ? await treasury.pause().send()
          : await treasury.unpause().send();
      }
    );
    setBusy(false);
    if (out.ok) await refreshAfterTx();
  }

  async function roleSubmit() {
    if (!canUse) return;
    const tB58 = roleTarget.trim();
    if (!isBase58TronAddress(tB58))
      return toast.error("Invalid target address");
    const tHex = toHex(tronWeb, tB58);

    const role =
      roleWhich === "ADMIN"
        ? ROLE_ADMIN
        : roleWhich === "GUARDIAN"
        ? ROLE_GUARDIAN || FALLBACK_GUARDIAN_ROLE
        : ROLE_MODULE || FALLBACK_MODULE_ROLE;

    setBusy(true);
    const out = await runAction(`${roleAction} ${roleWhich}`, async () => {
      return roleAction === "GRANT"
        ? await treasury.grantRole(role, tHex).send()
        : await treasury.revokeRole(role, tHex).send();
    });
    setBusy(false);
    if (out.ok) {
      setRoleTarget("");
      await refreshAfterTx();
    }
  }

  async function vaultConfigureModuleTRX() {
    if (!canUse) return;
    const modB58 = limitModuleAddr.trim();
    if (!isBase58TronAddress(modB58))
      return toast.error("Invalid module address");
    const modHex = toHex(tronWeb, modB58);

    const max = trxLimMaxSun.trim();
    const per = trxLimPeriodSec.trim();
    if (!max || Number(max) <= 0)
      return toast.error("Max per period must be > 0 (SUN)");
    if (!per || Number(per) <= 0)
      return toast.error("Period seconds must be > 0");

    setBusy(true);
    const out = await runAction("Configure module TRX limit", async () => {
      return await treasury
        .configureModuleTRX(modHex, trxLimEnabled, max, per)
        .send();
    });
    setBusy(false);
    if (out.ok) await readTrxLimit();
  }

  async function vaultConfigureModuleToken() {
    if (!canUse) return;
    const modB58 = limitModuleAddr.trim();
    const tokenB58 = tokAddr.trim();
    if (!isBase58TronAddress(modB58))
      return toast.error("Invalid module address");
    if (!isBase58TronAddress(tokenB58))
      return toast.error("Invalid token address");

    const modHex = toHex(tronWeb, modB58);
    const tokenHex = toHex(tronWeb, tokenB58);

    const max = tokLimMax.trim();
    const per = tokLimPeriodSec.trim();
    if (!max || Number(max) <= 0)
      return toast.error("Max per period must be > 0");
    if (!per || Number(per) <= 0)
      return toast.error("Period seconds must be > 0");

    setBusy(true);
    const out = await runAction("Configure module token limit", async () => {
      return await treasury
        .configureModuleToken(tokenHex, modHex, tokLimEnabled, max, per)
        .send();
    });
    setBusy(false);
    if (out.ok) await readTokenLimit();
  }

  async function emergencySweepTRX() {
    if (!canUse) return;
    const toB58 = sweepTo.trim();
    const amt = sweepTrxSun.trim();
    if (!isBase58TronAddress(toB58))
      return toast.error("Invalid sweep destination");
    if (!amt || Number(amt) <= 0)
      return toast.error("Amount must be > 0 (SUN)");

    const toHexAddr = toHex(tronWeb, toB58);

    setBusy(true);
    const out = await runAction("Emergency sweep TRX", async () => {
      return await treasury.emergencySweepTRX(toHexAddr, amt).send();
    });
    setBusy(false);
    if (out.ok) {
      setSweepTrxSun("");
      await refreshAfterTx();
    }
  }

  async function emergencySweepToken() {
    if (!canUse) return;
    const tokenB58 = sweepTokenAddr.trim();
    const toB58 = sweepTokenTo.trim();
    const amt = sweepTokenAmount.trim();
    if (!isBase58TronAddress(tokenB58))
      return toast.error("Invalid token address");
    if (!isBase58TronAddress(toB58))
      return toast.error("Invalid destination");
    if (!amt || Number(amt) <= 0) return toast.error("Amount must be > 0");

    const tokenHex = toHex(tronWeb, tokenB58);
    const toHexAddr = toHex(tronWeb, toB58);

    setBusy(true);
    const out = await runAction("Emergency sweep token", async () => {
      return await treasury.emergencySweepToken(tokenHex, toHexAddr, amt).send();
    });
    setBusy(false);
    if (out.ok) {
      setSweepTokenAmount("");
      await refreshAfterTx();
    }
  }

  async function budPauseToggle(pause: boolean) {
    if (!canUse) return;
    setBusy(true);
    const out = await runAction(
      pause ? "Budget pause" : "Budget unpause",
      async () => {
        return pause
          ? await budget.pause().send()
          : await budget.unpause().send();
      }
    );
    setBusy(false);
    if (out.ok) await refreshAfterTx();
  }

  async function budSetTreasury() {
    if (!canUse) return;
    const tB58 = newBudTreasury.trim();
    if (!isBase58TronAddress(tB58))
      return toast.error("Invalid treasury address");
    const tHex = toHex(tronWeb, tB58);

    setBusy(true);
    const out = await runAction("Budget set treasury", async () => {
      return await budget.setTreasury(tHex).send();
    });
    setBusy(false);
    if (out.ok) {
      setNewBudTreasury("");
      await refreshAfterTx();
    }
  }

  async function budSetPayee() {
    if (!canUse) return;
    const pB58 = newBudPayee.trim();
    if (!isBase58TronAddress(pB58))
      return toast.error("Invalid payee address");
    const pHex = toHex(tronWeb, pB58);

    setBusy(true);
    const out = await runAction("Budget set payee", async () => {
      return await budget.setPayee(pHex).send();
    });
    setBusy(false);
    if (out.ok) {
      setNewBudPayee("");
      await refreshAfterTx();
    }
  }

  async function budSetToken() {
    if (!canUse) return;
    const t = newBudToken.trim();
    if (!t) return toast.error("Enter token address (base58 or hex).");
    const tHex = t.startsWith("T") ? toHex(tronWeb, t) : t;

    setBusy(true);
    const out = await runAction("Budget set token", async () => {
      return await budget.setToken(tHex).send();
    });
    setBusy(false);
    if (out.ok) {
      setNewBudToken("");
      await refreshAfterTx();
    }
  }

  async function budSetCap() {
    if (!canUse) return;
    const c = newBudCap.trim();
    if (!c || Number(c) <= 0) return toast.error("Cap must be > 0");

    setBusy(true);
    const out = await runAction("Budget set cap", async () => {
      return await budget.setCap(c).send();
    });
    setBusy(false);
    if (out.ok) {
      setNewBudCap("");
      await refreshAfterTx();
    }
  }

  async function budSetPeriod() {
    if (!canUse) return;
    const p = newBudPeriod.trim();
    if (!p || Number(p) <= 0)
      return toast.error("Period must be > 0 seconds");

    setBusy(true);
    const out = await runAction("Budget set period", async () => {
      return await budget.setPeriod(p).send();
    });
    setBusy(false);
    if (out.ok) {
      setNewBudPeriod("");
      await refreshAfterTx();
    }
  }

  // UI
  if (!addr) {
    return (
      <div
        style={{
          padding: 16,
          border: "1px solid rgba(0,0,0,0.12)",
          borderRadius: 14,
        }}
      >
        <div style={{ fontWeight: 900, fontSize: 16 }}>Admin</div>
        <div style={{ marginTop: 8, fontSize: 13, opacity: 0.85 }}>
          Connect TronLink to use admin features.
        </div>
      </div>
    );
  }

  const remainingEpoch = Math.max(0, Number(budCap) - Number(budSpentEpoch));

  return (
    <div
      style={{
        padding: 16,
        border: "1px solid rgba(0,0,0,0.12)",
        borderRadius: 14,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          gap: 12,
        }}
      >
        <div>
          <div style={{ fontWeight: 900, fontSize: 16 }}>Admin</div>
          <div style={{ fontSize: 12, opacity: 0.75 }}>
            Connected: <b>{shorten(addr)}</b>
          </div>
          <div style={{ marginTop: 6, fontSize: 12, opacity: 0.75 }}>
            Vault: <b>{CONFIG.addresses.treasury}</b> • SubReg:{" "}
            <b>{CONFIG.addresses.subReg}</b> • Budget:{" "}
            <b>{CONFIG.addresses.budgetModule}</b>
          </div>
          <div style={{ marginTop: 6, fontSize: 12, opacity: 0.9 }}>
            Vault roles — Admin: <b>{fmtBool(meIsAdmin)}</b> • Guardian:{" "}
            <b>{fmtBool(meIsGuardian)}</b> • Module:{" "}
            <b>{fmtBool(meIsModule)}</b>
          </div>
        </div>

        <button
          onClick={() => refreshAll(true)}
          disabled={!canUse || busy}
          style={{
            padding: "6px 10px",
            borderRadius: 10,
            border: "1px solid rgba(0,0,0,0.18)",
            background: "white",
            cursor: "pointer",
            opacity: !canUse || busy ? 0.6 : 1,
          }}
        >
          Refresh
        </button>
      </div>

      {nodeDown && (
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
          <b>Node unavailable:</b> {lastNodeError}
          <div style={{ marginTop: 6, opacity: 0.85 }}>
            Contract reads (.call) are temporarily failing. You can still try
            transactions, but the UI may not update until the node recovers.
          </div>
        </div>
      )}

      {/* Treasury Vault */}
      <div
        style={{
          marginTop: 12,
          padding: 12,
          borderRadius: 12,
          border: "1px solid rgba(0,0,0,0.10)",
        }}
      >
        <div style={{ fontWeight: 900, fontSize: 13 }}>Treasury Vault</div>
        <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.6 }}>
          <div>
            Paused: <b>{fmtBool(vaultPaused)}</b>
          </div>
          <div>
            TRX balance: <b>{vaultTrxBalSun}</b> SUN (≈{" "}
            <b>{sunToTRX(vaultTrxBalSun).toFixed(6)}</b> TRX)
          </div>
        </div>

        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            disabled={busy || !canUse}
            onClick={() => vaultPauseToggle(true)}
            style={{
              padding: "8px 10px",
              borderRadius: 10,
              border: "1px solid rgba(0,0,0,0.18)",
              background: "white",
              cursor: "pointer",
              fontWeight: 900,
              fontSize: 12,
              opacity: busy || !canUse ? 0.6 : 1,
            }}
          >
            Pause (guardian)
          </button>
          <button
            disabled={busy || !canUse}
            onClick={() => vaultPauseToggle(false)}
            style={{
              padding: "8px 10px",
              borderRadius: 10,
              border: "1px solid rgba(0,0,0,0.18)",
              background: "white",
              cursor: "pointer",
              fontWeight: 900,
              fontSize: 12,
              opacity: busy || !canUse ? 0.6 : 1,
            }}
          >
            Unpause (admin)
          </button>
        </div>

        {/* Role Management */}
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid rgba(0,0,0,0.08)" }}>
          <div style={{ fontWeight: 900, fontSize: 13 }}>Role management</div>

          <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "1fr 220px 160px 140px", gap: 10, alignItems: "center" }}>
            <input
              value={roleTarget}
              onChange={(e) => setRoleTarget(e.target.value)}
              placeholder="Target address (base58)"
              style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            />

            <select
              value={roleWhich}
              onChange={(e) => setRoleWhich(e.target.value as any)}
              style={{ padding: "10px 10px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            >
              <option value="ADMIN">DEFAULT_ADMIN_ROLE</option>
              <option value="GUARDIAN">GUARDIAN_ROLE</option>
              <option value="MODULE">MODULE_ROLE</option>
            </select>

            <select
              value={roleAction}
              onChange={(e) => setRoleAction(e.target.value as any)}
              style={{ padding: "10px 10px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            >
              <option value="GRANT">Grant</option>
              <option value="REVOKE">Revoke</option>
            </select>

            <button
              disabled={busy || !canUse}
              onClick={roleSubmit}
              style={{
                padding: "10px 12px",
                borderRadius: 12,
                border: "1px solid rgba(0,0,0,0.18)",
                background: "white",
                cursor: "pointer",
                fontWeight: 900,
                opacity: busy || !canUse ? 0.6 : 1,
              }}
            >
              Submit
            </button>
          </div>

          <div style={{ marginTop: 8, fontSize: 12, opacity: 0.75 }}>
            Role IDs: Admin {ROLE_ADMIN} • Guardian{" "}
            {ROLE_GUARDIAN || FALLBACK_GUARDIAN_ROLE} • Module{" "}
            {ROLE_MODULE || FALLBACK_MODULE_ROLE}
          </div>
        </div>

        {/* Module limits */}
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid rgba(0,0,0,0.08)" }}>
          <div style={{ fontWeight: 900, fontSize: 13 }}>Module limits</div>

          <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "1fr 150px", gap: 10, alignItems: "center" }}>
            <input
              value={limitModuleAddr}
              onChange={(e) => setLimitModuleAddr(e.target.value)}
              placeholder="Module address (base58)"
              style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            />
            <button
              disabled={busy || !canUse}
              onClick={readTrxLimit}
              style={{
                padding: "10px 12px",
                borderRadius: 12,
                border: "1px solid rgba(0,0,0,0.18)",
                background: "white",
                cursor: "pointer",
                fontWeight: 900,
                opacity: busy || !canUse ? 0.6 : 1,
              }}
            >
              Read TRX limit
            </button>
          </div>

          {trxLimRead && (
            <div style={{ marginTop: 10, fontSize: 12, opacity: 0.9, lineHeight: 1.6 }}>
              TRX limit → enabled: <b>{fmtBool(trxLimRead.enabled)}</b>, periodSeconds:{" "}
              <b>{trxLimRead.periodSeconds}</b>, max: <b>{trxLimRead.maxPerPeriod}</b>{" "}
              SUN, spent: <b>{trxLimRead.spentThisPeriod}</b> SUN
            </div>
          )}

          <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr 140px", gap: 10, alignItems: "center" }}>
            <input
              value={trxLimMaxSun}
              onChange={(e) => setTrxLimMaxSun(e.target.value)}
              placeholder="Max per period (SUN) e.g. 50000000000"
              style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            />
            <input
              value={trxLimPeriodSec}
              onChange={(e) => setTrxLimPeriodSec(e.target.value)}
              placeholder="Period seconds e.g. 2592000"
              style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            />
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, fontWeight: 900 }}>
              <input type="checkbox" checked={trxLimEnabled} onChange={(e) => setTrxLimEnabled(e.target.checked)} />
              Enabled
            </label>
          </div>

          <button
            disabled={busy || !canUse}
            onClick={vaultConfigureModuleTRX}
            style={{
              marginTop: 10,
              padding: "10px 12px",
              borderRadius: 12,
              border: "1px solid rgba(0,0,0,0.18)",
              background: "white",
              cursor: "pointer",
              fontWeight: 900,
              opacity: busy || !canUse ? 0.6 : 1,
            }}
          >
            Configure module TRX limit
          </button>

          {/* Token limits */}
          <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid rgba(0,0,0,0.08)" }}>
            <div style={{ fontWeight: 900, fontSize: 13 }}>Token limits</div>

            <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "1fr 150px", gap: 10, alignItems: "center" }}>
              <input
                value={tokAddr}
                onChange={(e) => setTokAddr(e.target.value)}
                placeholder="Token address (base58)"
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
              />
              <button
                disabled={busy || !canUse}
                onClick={readTokenLimit}
                style={{
                  padding: "10px 12px",
                  borderRadius: 12,
                  border: "1px solid rgba(0,0,0,0.18)",
                  background: "white",
                  cursor: "pointer",
                  fontWeight: 900,
                  opacity: busy || !canUse ? 0.6 : 1,
                }}
              >
                Read token limit
              </button>
            </div>

            {tokLimRead && (
              <div style={{ marginTop: 10, fontSize: 12, opacity: 0.9, lineHeight: 1.6 }}>
                Token limit → enabled: <b>{fmtBool(tokLimRead.enabled)}</b>, periodSeconds:{" "}
                <b>{tokLimRead.periodSeconds}</b>, max: <b>{tokLimRead.maxPerPeriod}</b>,
                spent: <b>{tokLimRead.spentThisPeriod}</b>
              </div>
            )}

            <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr 140px", gap: 10, alignItems: "center" }}>
              <input
                value={tokLimMax}
                onChange={(e) => setTokLimMax(e.target.value)}
                placeholder="Max per period (raw token units)"
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
              />
              <input
                value={tokLimPeriodSec}
                onChange={(e) => setTokLimPeriodSec(e.target.value)}
                placeholder="Period seconds e.g. 2592000"
                style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
              />
              <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12, fontWeight: 900 }}>
                <input type="checkbox" checked={tokLimEnabled} onChange={(e) => setTokLimEnabled(e.target.checked)} />
                Enabled
              </label>
            </div>

            <button
              disabled={busy || !canUse}
              onClick={vaultConfigureModuleToken}
              style={{
                marginTop: 10,
                padding: "10px 12px",
                borderRadius: 12,
                border: "1px solid rgba(0,0,0,0.18)",
                background: "white",
                cursor: "pointer",
                fontWeight: 900,
                opacity: busy || !canUse ? 0.6 : 1,
              }}
            >
              Configure module token limit
            </button>
          </div>
        </div>

        {/* Emergency */}
        <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid rgba(0,0,0,0.08)" }}>
          <div style={{ fontWeight: 900, fontSize: 13 }}>Emergency (paused-only)</div>
          <div style={{ marginTop: 6, fontSize: 12, opacity: 0.8 }}>
            Vault must be paused to sweep. Use carefully.
          </div>

          <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr 180px", gap: 10 }}>
            <input
              value={sweepTo}
              onChange={(e) => setSweepTo(e.target.value)}
              placeholder="Sweep TRX to (base58)"
              style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            />
            <input
              value={sweepTrxSun}
              onChange={(e) => setSweepTrxSun(e.target.value)}
              placeholder="Amount (SUN)"
              style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            />
            <button
              disabled={busy || !canUse}
              onClick={emergencySweepTRX}
              style={{
                padding: "10px 12px",
                borderRadius: 12,
                border: "1px solid rgba(0,0,0,0.18)",
                background: "white",
                cursor: "pointer",
                fontWeight: 900,
                opacity: busy || !canUse ? 0.6 : 1,
              }}
            >
              Sweep TRX
            </button>
          </div>

          <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr 1fr 180px", gap: 10 }}>
            <input
              value={sweepTokenAddr}
              onChange={(e) => setSweepTokenAddr(e.target.value)}
              placeholder="Token (base58)"
              style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            />
            <input
              value={sweepTokenTo}
              onChange={(e) => setSweepTokenTo(e.target.value)}
              placeholder="To (base58)"
              style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            />
            <input
              value={sweepTokenAmount}
              onChange={(e) => setSweepTokenAmount(e.target.value)}
              placeholder="Amount (raw token units)"
              style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
            />
            <button
              disabled={busy || !canUse}
              onClick={emergencySweepToken}
              style={{
                padding: "10px 12px",
                borderRadius: 12,
                border: "1px solid rgba(0,0,0,0.18)",
                background: "white",
                cursor: "pointer",
                fontWeight: 900,
                opacity: busy || !canUse ? 0.6 : 1,
              }}
            >
              Sweep Token
            </button>
          </div>
        </div>
      </div>

      {/* Subscription Registry */}
      <div style={{ marginTop: 12, padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.10)" }}>
        <div style={{ fontWeight: 900, fontSize: 13 }}>Subscription Registry</div>
        <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.6 }}>
          <div>
            Paused: <b>{fmtBool(subPaused)}</b>
          </div>
          <div>
            Owner: <b>{subOwner || "-"}</b>
          </div>
          <div>
            Treasury: <b>{subTreasury || "-"}</b>
          </div>
          <div>
            TRX/USD (E6): <b>{trxUsdPriceE6}</b>
          </div>
          <div>
            Monthly price (E6): <b>{monthlyPriceUsdE6}</b>
          </div>
        </div>

        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button disabled={busy || !canUse} onClick={() => subPauseToggle(true)} style={btnStyle(busy, canUse)}>
            Pause
          </button>
          <button disabled={busy || !canUse} onClick={() => subPauseToggle(false)} style={btnStyle(busy, canUse)}>
            Unpause
          </button>
        </div>

        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid rgba(0,0,0,0.08)" }}>
          <div style={{ fontWeight: 900, fontSize: 13 }}>Update pricing</div>
          <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "1fr 1fr 180px", gap: 10, alignItems: "center" }}>
            <input value={newTrxUsdE6} onChange={(e) => setNewTrxUsdE6(e.target.value)} placeholder="TRX/USD (E6)" style={inputStyle} />
            <input value={newMonthlyUsdE6} onChange={(e) => setNewMonthlyUsdE6(e.target.value)} placeholder="Monthly (E6)" style={inputStyle} />
            <button disabled={busy || !canUse} onClick={subSetPrices} style={btnStyle(busy, canUse)}>
              Set
            </button>
          </div>
        </div>

        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid rgba(0,0,0,0.08)" }}>
          <div style={{ fontWeight: 900, fontSize: 13 }}>Treasury + Ownership</div>

          <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "1fr 180px", gap: 10, alignItems: "center" }}>
            <input value={newSubTreasury} onChange={(e) => setNewSubTreasury(e.target.value)} placeholder="Set treasury (base58)" style={inputStyle} />
            <button disabled={busy || !canUse} onClick={subSetTreasury} style={btnStyle(busy, canUse)}>
              Set Treasury
            </button>
          </div>

          <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 180px", gap: 10, alignItems: "center" }}>
            <input value={newSubOwner} onChange={(e) => setNewSubOwner(e.target.value)} placeholder="Transfer ownership (base58)" style={inputStyle} />
            <button disabled={busy || !canUse} onClick={subTransferOwnership} style={btnStyle(busy, canUse)}>
              Transfer Owner
            </button>
          </div>
        </div>
      </div>

      {/* Budget Module */}
      <div style={{ marginTop: 12, padding: 12, borderRadius: 12, border: "1px solid rgba(0,0,0,0.10)" }}>
        <div style={{ fontWeight: 900, fontSize: 13 }}>Budget Module</div>
        <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.6 }}>
          <div>
            Paused: <b>{fmtBool(budPaused)}</b>
          </div>
          <div>
            Treasury: <b>{budTreasury || "-"}</b>
          </div>
          <div>
            Payee: <b>{budPayee || "-"}</b>
          </div>
          <div>
            Token: <b>{budToken || "-"}</b>
          </div>
          <div>
            Cap/period: <b>{budCap}</b>
          </div>
          <div>
            Period seconds: <b>{budPeriod}</b>
          </div>
          <div>
            Epoch start: <b>{budEpochStart}</b>
          </div>
          <div>
            Spent this epoch: <b>{budSpentEpoch}</b>
          </div>
          <div>
            Remaining this epoch: <b>{String(remainingEpoch)}</b>
          </div>
        </div>

        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button disabled={busy || !canUse} onClick={() => budPauseToggle(true)} style={btnStyle(busy, canUse)}>
            Pause
          </button>
          <button disabled={busy || !canUse} onClick={() => budPauseToggle(false)} style={btnStyle(busy, canUse)}>
            Unpause
          </button>
        </div>

        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid rgba(0,0,0,0.08)" }}>
          <div style={{ fontWeight: 900, fontSize: 13 }}>Admin controls</div>

          <div style={{ marginTop: 8, display: "grid", gridTemplateColumns: "1fr 180px", gap: 10, alignItems: "center" }}>
            <input value={newBudTreasury} onChange={(e) => setNewBudTreasury(e.target.value)} placeholder="Set treasury (base58)" style={inputStyle} />
            <button disabled={busy || !canUse} onClick={budSetTreasury} style={btnStyle(busy, canUse)}>
              Set Treasury
            </button>
          </div>

          <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 180px", gap: 10, alignItems: "center" }}>
            <input value={newBudPayee} onChange={(e) => setNewBudPayee(e.target.value)} placeholder="Set payee (base58)" style={inputStyle} />
            <button disabled={busy || !canUse} onClick={budSetPayee} style={btnStyle(busy, canUse)}>
              Set Payee
            </button>
          </div>

          <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 180px", gap: 10, alignItems: "center" }}>
            <input value={newBudToken} onChange={(e) => setNewBudToken(e.target.value)} placeholder="Set token (base58 or hex)" style={inputStyle} />
            <button disabled={busy || !canUse} onClick={budSetToken} style={btnStyle(busy, canUse)}>
              Set Token
            </button>
          </div>

          <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 180px", gap: 10, alignItems: "center" }}>
            <input value={newBudCap} onChange={(e) => setNewBudCap(e.target.value)} placeholder="Set cap" style={inputStyle} />
            <button disabled={busy || !canUse} onClick={budSetCap} style={btnStyle(busy, canUse)}>
              Set Cap
            </button>
          </div>

          <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 180px", gap: 10, alignItems: "center" }}>
            <input value={newBudPeriod} onChange={(e) => setNewBudPeriod(e.target.value)} placeholder="Set period seconds" style={inputStyle} />
            <button disabled={busy || !canUse} onClick={budSetPeriod} style={btnStyle(busy, canUse)}>
              Set Period
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "10px 12px",
  borderRadius: 10,
  border: "1px solid rgba(0,0,0,0.18)",
};

function btnStyle(busy: boolean, canUse: boolean): React.CSSProperties {
  return {
    padding: "10px 12px",
    borderRadius: 12,
    border: "1px solid rgba(0,0,0,0.18)",
    background: "white",
    cursor: "pointer",
    fontWeight: 900,
    opacity: busy || !canUse ? 0.6 : 1,
  };
}