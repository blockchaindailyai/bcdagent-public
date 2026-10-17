import { useEffect, useMemo, useState } from "react";

type TxItem = {
  ts: number;
  txid: string;
  months: number;
  beneficiary?: string;
  paidSun: string;
};

const LS_KEY = "bcd_sub_tx_history_v1";

function load(): TxItem[] {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

function save(items: TxItem[]) {
  localStorage.setItem(LS_KEY, JSON.stringify(items.slice(0, 20)));
}

export function pushTx(item: TxItem) {
  const items = load();
  items.unshift(item);
  save(items);
}

export default function TxHistory() {
  const [items, setItems] = useState<TxItem[]>([]);

  useEffect(() => {
    setItems(load());
  }, []);

  const rows = useMemo(() => items.slice(0, 10), [items]);

  if (!rows.length) return null;

  return (
    <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid rgba(0,0,0,0.08)" }}>
      <div style={{ fontWeight: 800, fontSize: 13, marginBottom: 8 }}>Recent subscription transactions</div>
      <div style={{ display: "grid", gap: 8 }}>
        {rows.map((x) => (
          <div
            key={x.txid}
            style={{
              padding: 10,
              border: "1px solid rgba(0,0,0,0.10)",
              borderRadius: 12,
              fontSize: 12,
              display: "grid",
              gap: 4,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
              <div>
                <b>{x.months}</b> mo • Paid <b>{Number(x.paidSun) / 1e6}</b> TRX
                {x.beneficiary ? (
                  <>
                    {" "}
                    • Gift to <b>{x.beneficiary}</b>
                  </>
                ) : null}
              </div>
              <div style={{ opacity: 0.7 }}>{new Date(x.ts).toLocaleString()}</div>
            </div>

            <div style={{ opacity: 0.85, wordBreak: "break-all" }}>
              Tx: <b>{x.txid}</b>
            </div>
          </div>
        ))}
      </div>

      <button
        onClick={() => {
          localStorage.removeItem(LS_KEY);
          setItems([]);
        }}
        style={{
          marginTop: 10,
          padding: "8px 10px",
          borderRadius: 10,
          border: "1px solid rgba(0,0,0,0.18)",
          background: "white",
          cursor: "pointer",
          fontWeight: 700,
          fontSize: 12,
        }}
      >
        Clear history
      </button>
    </div>
  );
}