import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { sunToTRX, toUintString } from "../utils/tron";

export default function TreasuryCard({
  tronWeb: _tronWeb,
  addr,
  contract,
}: {
  tronWeb: any;
  addr: string;
  contract: any;
}) {
  const [paused, setPaused] = useState<boolean>(false);
  const [trxBal, setTrxBal] = useState<string>("0");

  async function refresh() {
    if (!contract) return;
    try {
      const [p, b] = await Promise.all([
        contract.paused().call(),
        contract.trxBalance().call(),
      ]);
      setPaused(!!p);
      setTrxBal(toUintString(b));
    } catch (e: any) {
      toast.error(e?.message || "Failed to refresh treasury");
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contract, addr]);

  async function pause() {
    try {
      await contract.pause().send();
      toast.success("Paused");
      await refresh();
    } catch (e: any) {
      toast.error(e?.message || "Pause failed");
    }
  }

  async function unpause() {
    try {
      await contract.unpause().send();
      toast.success("Unpaused");
      await refresh();
    } catch (e: any) {
      toast.error(e?.message || "Unpause failed");
    }
  }

  return (
    <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <div style={{ fontWeight: 800, fontSize: 16 }}>Treasury Vault</div>
        <button onClick={refresh} style={{ padding: "6px 10px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)", background: "white", cursor: "pointer" }}>
          Refresh
        </button>
      </div>

      <div style={{ marginTop: 10, fontSize: 13, opacity: 0.85, lineHeight: 1.45 }}>
        <div>Paused: <b>{paused ? "Yes" : "No"}</b></div>
        <div>TRX balance: <b>{trxBal}</b> SUN (~ <b>{sunToTRX(trxBal).toFixed(6)}</b> TRX)</div>
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button
          onClick={pause}
          disabled={!addr}
          style={{ padding: "10px 14px", borderRadius: 12, border: "1px solid rgba(0,0,0,0.18)", background: "white", cursor: "pointer", fontWeight: 800 }}
        >
          Pause (guardian)
        </button>
        <button
          onClick={unpause}
          disabled={!addr}
          style={{ padding: "10px 14px", borderRadius: 12, border: "1px solid rgba(0,0,0,0.18)", background: "white", cursor: "pointer", fontWeight: 800 }}
        >
          Unpause (admin)
        </button>

        <div style={{ fontSize: 12, opacity: 0.75, alignSelf: "center" }}>
          These will revert unless connected wallet has the correct role.
        </div>
      </div>
    </div>
  );
}