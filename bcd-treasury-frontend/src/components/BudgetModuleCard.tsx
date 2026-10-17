import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { sunToTRX, toUintString, unixToIso, trxToSUN } from "../utils/tron";

export default function BudgetModuleCard({
  tronWeb: _tronWeb,
  addr,
  contract,
}: {
  tronWeb: any;
  addr: string;
  contract: any;
}) {
  const [loading, setLoading] = useState(false);

  const [payee, setPayee] = useState<string>("");
  const [token, setToken] = useState<string>("");
  const [paused, setPaused] = useState<boolean>(false);

  const [cap, setCap] = useState<string>("0");
  const [spent, setSpent] = useState<string>("0");
  const [period, setPeriod] = useState<string>("0");
  const [epochStart, setEpochStart] = useState<string>("0");

  const [claimTrx, setClaimTrx] = useState<string>("1.0"); // TRX display

  async function refresh() {
    if (!contract) return;
    try {
      const [pye, tok, pa, c, s, ps, es] = await Promise.all([
        contract.payee().call(),
        contract.token().call(),
        contract.paused().call(),
        contract.capPerPeriod().call(),
        contract.spentThisEpoch().call(),
        contract.periodSeconds().call(),
        contract.epochStart().call(),
      ]);

      setPayee(String(pye));
      setToken(String(tok));
      setPaused(!!pa);
      setCap(toUintString(c));
      setSpent(toUintString(s));
      setPeriod(toUintString(ps));
      setEpochStart(toUintString(es));
    } catch (e: any) {
      toast.error(e?.message || "Failed to refresh budget module");
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contract, addr]);

  async function claim() {
    if (!addr) return toast.error("Connect TronLink first");
    if (!contract) return;
    if (paused) return toast.error("Module is paused");

    setLoading(true);
    try {
      // This module’s amount is in SUN if token == 0x0 (TRX path).
      // For TRC-20 path, amount is token’s smallest unit; UI would need decimals (not included here).
      const isTRX = token === "410000000000000000000000000000000000000000" || token === "0x0000000000000000000000000000000000000000" || token === "" || token === "0";

      let amt = claimTrx.trim();
      if (!amt) amt = "0";
      if (Number(amt) <= 0) throw new Error("Amount must be > 0");

      const amountSun = isTRX ? trxToSUN(amt) : amt; // for token case, user should input raw units
      await contract.claim(String(amountSun)).send();

      toast.success("Claimed");
      await refresh();
    } catch (e: any) {
      toast.error(e?.message || "Claim failed");
    } finally {
      setLoading(false);
    }
  }

  const remainingSun = (() => {
    try {
      const c = BigInt(cap || "0");
      const s = BigInt(spent || "0");
      return (c > s ? (c - s) : 0n).toString();
    } catch {
      return "0";
    }
  })();

  return (
    <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <div style={{ fontWeight: 800, fontSize: 16 }}>Budget Module</div>
        <button onClick={refresh} style={{ padding: "6px 10px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)", background: "white", cursor: "pointer" }}>
          Refresh
        </button>
      </div>

      <div style={{ marginTop: 10, fontSize: 13, opacity: 0.85, lineHeight: 1.45 }}>
        <div>Paused: <b>{paused ? "Yes" : "No"}</b></div>
        <div>Payee: <b>{payee || "-"}</b></div>
        <div>Token: <b>{token || "-"}</b> (address(0) means TRX in your design)</div>
        <div>Cap / period: <b>{cap}</b> units</div>
        <div>Spent this epoch: <b>{spent}</b> units</div>
        <div>Remaining this epoch: <b>{remainingSun}</b> units (~ <b>{sunToTRX(remainingSun).toFixed(6)}</b> TRX if TRX mode)</div>
        <div>Period seconds: <b>{period}</b></div>
        <div>Epoch start: <b>{epochStart}</b> ({unixToIso(epochStart)})</div>
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <input
          value={claimTrx}
          onChange={(e) => setClaimTrx(e.target.value)}
          placeholder="Claim amount (TRX) if TRX mode"
          style={{ flex: 1, minWidth: 240, padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
        />
        <button
          disabled={loading || !addr}
          onClick={claim}
          style={{ padding: "10px 14px", borderRadius: 12, border: "1px solid rgba(0,0,0,0.18)", background: "white", cursor: "pointer", fontWeight: 800 }}
        >
          Claim
        </button>
        <div style={{ fontSize: 12, opacity: 0.75 }}>
          Claim will revert unless connected wallet == payee.
        </div>
      </div>
    </div>
  );
}