declare global {
  interface Window {
    tronWeb?: any;
    tronLink?: any;
  }
}

export function isTronReady(): boolean {
  return typeof window !== "undefined" && !!window.tronWeb && window.tronWeb.ready;
}

export async function waitForTronWeb(timeoutMs = 8000): Promise<any> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (isTronReady()) return window.tronWeb;
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error("TronLink not detected or not unlocked.");
}

export function shorten(addr: string, n = 6) {
  if (!addr) return "";
  return `${addr.slice(0, n)}…${addr.slice(-n)}`;
}

export function sunToTRX(sun: string | number): number {
  const x = typeof sun === "string" ? Number(sun) : sun;
  return x / 1_000_000;
}

export function trxToSUN(trx: string | number): number {
  const x = typeof trx === "string" ? Number(trx) : trx;
  return Math.round(x * 1_000_000);
}

export function toUintString(x: any): string {
  // TronWeb returns strings for uints; keep as string to avoid JS overflow.
  if (x === null || x === undefined) return "0";
  return typeof x === "string" ? x : String(x);
}

export function unixToIso(tsSec: string | number): string {
  const s = typeof tsSec === "string" ? Number(tsSec) : tsSec;
  if (!s) return "-";
  return new Date(s * 1000).toISOString();
}

export function getCurrentAddressBase58(): string {
  const tw = window.tronWeb;
  return tw?.defaultAddress?.base58 || "";
}

export function getCurrentNodeHost(): string {
  const tw = window.tronWeb;
  return tw?.fullNode?.host || "";
}

export async function requestTronAccounts(): Promise<void> {
  // Works on modern TronLink; safe to call and ignore rejection
  if (window.tronLink?.request) {
    await window.tronLink.request({ method: "tron_requestAccounts" });
  }
}