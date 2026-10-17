import { useEffect, useMemo, useState } from "react";
import toast from "react-hot-toast";
import { sunToTRX, toUintString, unixToIso } from "../utils/tron";
import TxHistory, { pushTx } from "../components/TxHistory";

const PRESETS = [1, 3, 6, 12];

function isBase58TronAddress(s: string): boolean {
  // loose check: Tron base58 addresses start with 'T' and are 34 chars typically
  if (!s) return false;
  if (s[0] !== "T") return false;
  if (s.length < 30 || s.length > 36) return false;
  return true;
}

export default function SubscriptionPage({
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
  const [giftMode, setGiftMode] = useState<boolean>(false);
  const [beneficiary, setBeneficiary] = useState<string>("");

  const [monthlyUsdE6, setMonthlyUsdE6] = useState<string>("0");
  const [trxUsdPriceE6, setTrxUsdPriceE6] = useState<string>("0");
  const [paused, setPaused] = useState<boolean>(false);

  const [requiredSun, setRequiredSun] = useState<string>("0");
  const [accessUntil, setAccessUntil] = useState<string>("0");
  const [isActive, setIsActive] = useState<boolean>(false);

  const usdAmount = useMemo(() => {
    const m = Number(monthlyUsdE6) / 1e6;
    return months * m;
  }, [months, monthlyUsdE6]);

  const trxAmount = useMemo(() => sunToTRX(requiredSun), [requiredSun]);

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

  async function submit() {
    if (!contract || !addr) return toast.error("Connect TronLink first");
    if (paused) return toast.error("Subscription registry is paused");
    if (!Number.isFinite(months) || months <= 0) return toast.error("Months must be > 0");

    if (giftMode) {
      const b = beneficiary.trim();
      if (!isBase58TronAddress(b)) return toast.error("Enter a valid beneficiary base58 address");
    }

    setLoading(true);
    try {
      const req = await contract.requiredTRXSun(months).call();
      const val = toUintString(req);

      let txid: string | undefined;

      if (!giftMode) {
        txid = await contract.subscribeTRX(months).send({ callValue: val });
      } else {
        txid = await contract.subscribeTRXFor(beneficiary.trim(), months).send({ callValue: val });
      }

      toast.success("Submitted");

      // persist receipt
      if (txid) {
        pushTx({
          ts: Date.now(),
          txid,
          months,
          beneficiary: giftMode ? beneficiary.trim() : undefined,
          paidSun: val,
        });
      }

      await refresh();
    } catch (e: any) {
      toast.error(e?.message || "Subscription failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ padding: 16, border: "1px solid rgba(0,0,0,0.12)", borderRadius: 14 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <div style={{ fontWeight: 800, fontSize: 16 }}>Subscribe</div>
        <button
          onClick={refresh}
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

      <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
        <div style={{ padding: 12, border: "1px solid rgba(0,0,0,0.08)", borderRadius: 12 }}>
          <div style={{ fontSize: 12, opacity: 0.8 }}>Registry status</div>
          <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.5 }}>
            <div>Paused: <b>{paused ? "Yes" : "No"}</b></div>
            <div>Monthly price (USD): <b>${(Number(monthlyUsdE6) / 1e6).toFixed(2)}</b></div>
            <div>TRX/USD (manual): <b>${(Number(trxUsdPriceE6) / 1e6).toFixed(6)}</b> per TRX</div>
          </div>
        </div>

        <div style={{ padding: 12, border: "1px solid rgba(0,0,0,0.08)", borderRadius: 12 }}>
          <div style={{ fontSize: 12, opacity: 0.8 }}>Your access</div>
          <div style={{ marginTop: 6, fontSize: 13, lineHeight: 1.5 }}>
            <div>Status: <b>{isActive ? "Active" : "Inactive"}</b></div>
            <div>accessUntil: <b>{accessUntil}</b></div>
            <div>ISO: <b>{unixToIso(accessUntil)}</b></div>
          </div>
        </div>
      </div>

      <div style={{ marginTop: 14, display: "grid", gap: 10 }}>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <div style={{ fontSize: 13, fontWeight: 800 }}>Plan</div>
          {PRESETS.map((m) => (
            <button
              key={m}
              onClick={() => setMonths(m)}
              style={{
                padding: "8px 10px",
                borderRadius: 10,
                border: "1px solid rgba(0,0,0,0.18)",
                background: months === m ? "rgba(0,0,0,0.06)" : "white",
                cursor: "pointer",
                fontWeight: 800,
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

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div style={{ padding: 12, border: "1px solid rgba(0,0,0,0.08)", borderRadius: 12 }}>
            <div style={{ fontSize: 12, opacity: 0.8 }}>Quote</div>
            <div style={{ marginTop: 6, fontSize: 14, lineHeight: 1.6 }}>
              <div>
                Total: <b>{requiredSun}</b> SUN
              </div>
              <div>
                ≈ <b>{trxAmount.toFixed(6)}</b> TRX
              </div>
              <div>
                ≈ <b>${usdAmount.toFixed(2)}</b> USD (based on monthly price)
              </div>
            </div>
          </div>

          <div style={{ padding: 12, border: "1px solid rgba(0,0,0,0.08)", borderRadius: 12 }}>
            <div style={{ fontSize: 12, opacity: 0.8 }}>Type</div>
            <div style={{ marginTop: 8, display: "flex", gap: 10, alignItems: "center" }}>
              <label style={{ display: "flex", gap: 8, alignItems: "center", cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={giftMode}
                  onChange={(e) => setGiftMode(e.target.checked)}
                />
                <span style={{ fontSize: 13, fontWeight: 800 }}>Gift subscription</span>
              </label>
            </div>

            {giftMode ? (
              <input
                placeholder="Beneficiary (Base58 address, starts with T...)"
                value={beneficiary}
                onChange={(e) => setBeneficiary(e.target.value)}
                style={{ marginTop: 10, width: "100%", padding: "10px 12px", borderRadius: 10, border: "1px solid rgba(0,0,0,0.18)" }}
              />
            ) : (
              <div style={{ marginTop: 10, fontSize: 12, opacity: 0.75 }}>
                Subscription will be applied to your connected wallet.
              </div>
            )}
          </div>
        </div>

        <button
          disabled={loading || !addr}
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

        <TxHistory />
      </div>
    </div>
  );
}