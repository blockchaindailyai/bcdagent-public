import { useEffect, useState } from "react";
import toast from "react-hot-toast";
import { sunToTRX, toUintString, unixToIso } from "../utils/tron";

export default function SubscriptionCard({
  tronWeb: _tronWeb,
  addr,
  contract,
}: {
  tronWeb: any;
  addr: string;
  contract: any;
}) {
  const [loading, setLoading] = useState(false);

  const [months, setMonths] = useState<number>(1);
  const [beneficiary, setBeneficiary] = useState<string>("");

  const [monthlyUsdE6, setMonthlyUsdE6] = useState<string>("0");
  const [trxUsdPriceE6, setTrxUsdPriceE6] = useState<string>("0");
  const [paused, setPaused] = useState<boolean>(false);

  const [requiredSun, setRequiredSun] = useState<string>("0");
  const [accessUntil, setAccessUntil] = useState<string>("0");
  const [isActive, setIsActive] = useState<boolean>(false);

  async function refresh() {
    if (!contract || !addr) return;
    try {
      const [m, p, pa, au, act, req] = await Promise.all([
        contract.monthlyPriceUsdE6().call(),
        contract.trxUsdPriceE6().call(),
        contract.paused().call(),
        contract.accessUntil(addr).call(),
        contract.isActive(addr).call(),
        contract.requiredTRXSun(months).call(),
      ]);

      setMonthlyUsdE6(toUintString(m));
      setTrxUsdPriceE6(toUintString(p));
      setPaused(!!pa);

      setAccessUntil(toUintString(au));
      setIsActive(!!act);
      setRequiredSun(toUintString(req));
    } catch (e: any) {
      toast.error(e?.message || "Failed to refresh subscription data");
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contract, addr, months]);

  async function subscribeSelf() {
    if (!contract || !addr) return toast.error("Connect TronLink first");
    if (paused) return toast.error("Registry is paused");
    if (months <= 0) return toast.error("Months must be > 0");

    setLoading(true);
    try {
      const req = await contract.requiredTRXSun(months).call();
      const val = toUintString(req);

      await contract.subscribeTRX(months).send({
        callValue: val, // SUN
      });

      toast.success("Subscribed");
      await refresh();
    } catch (e: any) {
      toast.error(e?.message || "Subscribe failed");
    } finally {
      setLoading(false);
    }
  }

  async function subscribeFor() {
    if (!contract || !addr) return toast.error("Connect TronLink first");
    if (paused) return toast.error("Registry is paused");
    if (!beneficiary) return toast.error("Enter beneficiary address");
    if (months <= 0) return toast.error("Months must be > 0");

    setLoading(true);
    try {
      const req = await contract.requiredTRXSun(months).call();
      const val = toUintString(req);

      await contract.subscribeTRXFor(beneficiary, months).send({
        callValue: val,
      });

      toast.success("Subscribed for beneficiary");
    } catch (e: any) {
      toast.error(e?.message || "SubscribeFor failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <div style={{ fontWeight: 800, fontSize: 16 }}>Subscription Registry</div>
        <button onClick={refresh} style={{ padding: "6px 10px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)", background: "white", cursor: "pointer" }}>
          Refresh
        </button>
      </div>

      <div style={{ marginTop: 10, fontSize: 13, opacity: 0.85, lineHeight: 1.45 }}>
        <div>Paused: <b>{paused ? "Yes" : "No"}</b></div>
        <div>Monthly price (USD E6): <b>{monthlyUsdE6}</b></div>
        <div>TRX/USD price (USD E6 per TRX): <b>{trxUsdPriceE6}</b></div>
      </div>

      <div style={{ marginTop: 12, display: "grid", gridTemplateColumns: "160px 1fr", gap: 10, alignItems: "center" }}>
        <label style={{ fontSize: 13, fontWeight: 700 }}>Months</label>
        <input
          type="number"
          min={1}
          value={months}
          onChange={(e) => setMonths(Number(e.target.value))}
          style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
        />

        <div style={{ fontSize: 13, fontWeight: 700 }}>Quote</div>
        <div style={{ fontSize: 13 }}>
          Required: <b>{requiredSun}</b> SUN (~ <b>{sunToTRX(requiredSun).toFixed(6)}</b> TRX)
        </div>
      </div>

      <div style={{ marginTop: 12, display: "flex", gap: 10, flexWrap: "wrap" }}>
        <button
          disabled={loading || !addr}
          onClick={subscribeSelf}
          style={{
            padding: "10px 14px",
            borderRadius: 12,
            border: "1px solid rgba(0,0,0,0.18)",
            background: "white",
            cursor: "pointer",
            fontWeight: 800,
          }}
        >
          Subscribe (self)
        </button>

        <input
          placeholder="Beneficiary (Base58 address)"
          value={beneficiary}
          onChange={(e) => setBeneficiary(e.target.value.trim())}
          style={{ flex: 1, minWidth: 260, padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
        />

        <button
          disabled={loading || !addr}
          onClick={subscribeFor}
          style={{
            padding: "10px 14px",
            borderRadius: 12,
            border: "1px solid rgba(0,0,0,0.18)",
            background: "white",
            cursor: "pointer",
            fontWeight: 800,
          }}
        >
          Subscribe For
        </button>
      </div>

      <div style={{ marginTop: 12, fontSize: 13, opacity: 0.85 }}>
        <div>My accessUntil: <b>{accessUntil}</b> (unix)</div>
        <div>My accessUntil (ISO): <b>{unixToIso(accessUntil)}</b></div>
        <div>My status: <b>{isActive ? "Active" : "Inactive"}</b></div>
      </div>
    </div>
  );
}