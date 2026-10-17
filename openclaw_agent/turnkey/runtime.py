from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Dict

from .config import TurnkeyConfig, load_turnkey_config
from .transport import resolve_tron_transport_context
from .indexing import resolve_indexer_endpoint


@dataclass(frozen=True)
class RuntimeStatus:
    chain_healthy: bool
    justlend_healthy: bool
    details: Dict[str, str]


class TronRpcClient:
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url.rstrip("/")

    def healthy(self) -> bool:
        req = urllib.request.Request(
            f"{self.rpc_url}/wallet/getnowblock",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return "block_header" in body




@dataclass(frozen=True)
class MarketSnapshot:
    market: str
    status: str
    error_code: int | None
    error_detail: str | None
    jtoken_balance: int
    borrow_balance: int
    exchange_rate_mantissa: int

    @property
    def exchange_rate(self) -> float:
        return self.exchange_rate_mantissa / 1e18


class JustLendMonitor:
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url.rstrip("/")

    def account_snapshots(self, owner_address: str, markets: list[str]) -> list[MarketSnapshot]:
        out: list[MarketSnapshot] = []
        for market in markets:
            payload = {
                "owner_address": owner_address,
                "contract_address": market,
                "function_selector": "getAccountSnapshot(address)",
                "parameter": _abi_encode_address(owner_address),
                "visible": True,
            }
            body = self._post("/wallet/triggerconstantcontract", payload)
            const = body.get("constant_result", [])
            if not const:
                out.append(
                    MarketSnapshot(
                        market=market,
                        status="rpc_error",
                        error_code=None,
                        error_detail="missing_constant_result",
                        jtoken_balance=0,
                        borrow_balance=0,
                        exchange_rate_mantissa=0,
                    )
                )
                continue
            words = _decode_uint256_words(str(const[0]))
            if len(words) < 4:
                out.append(
                    MarketSnapshot(
                        market=market,
                        status="decode_error",
                        error_code=None,
                        error_detail="invalid_snapshot_return",
                        jtoken_balance=0,
                        borrow_balance=0,
                        exchange_rate_mantissa=0,
                    )
                )
                continue
            status = "ok" if words[0] == 0 else "protocol_error"
            out.append(MarketSnapshot(
                market=market,
                status=status,
                error_code=words[0],
                error_detail=None,
                jtoken_balance=words[1],
                borrow_balance=words[2],
                exchange_rate_mantissa=words[3],
            ))
        return out

    def _post(self, path: str, payload: Dict[str, object]) -> Dict[str, object]:
        req = urllib.request.Request(
            f"{self.rpc_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def call_bool(self, contract_address: str, function_selector: str, parameter: str = "") -> bool:
        payload = {
            "owner_address": contract_address,
            "contract_address": contract_address,
            "function_selector": function_selector,
            "parameter": parameter,
            "visible": True,
        }
        body = self._post("/wallet/triggerconstantcontract", payload)
        const = body.get("constant_result", [])
        if not const:
            return False
        words = _decode_uint256_words(str(const[0]))
        return bool(words and words[0] != 0)


def _abi_encode_address(base58_addr: str) -> str:
    raw = _b58check_decode(base58_addr)
    if len(raw) != 21 or raw[0] != 0x41:
        raise ValueError("invalid_tron_address")
    return raw[1:].rjust(32, b"\x00").hex()


def _decode_uint256_words(data_hex: str) -> list[int]:
    cleaned = data_hex.strip().replace("0x", "")
    if len(cleaned) % 64 != 0:
        return []
    return [int(cleaned[i : i + 64], 16) for i in range(0, len(cleaned), 64)]


def _abi_encode_bytes4(selector: str) -> str:
    cleaned = selector.lower().replace("0x", "")
    if len(cleaned) != 8:
        raise ValueError("invalid_selector")
    return cleaned + ("0" * 56)


_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58check_decode(s: str) -> bytes:
    num = 0
    for c in s:
        num = num * 58 + _B58_ALPHABET.index(c)
    full = num.to_bytes((num.bit_length() + 7) // 8, "big")
    pad = 0
    for c in s:
        if c == "1":
            pad += 1
        else:
            break
    full = b"\x00" * pad + full
    if len(full) < 5:
        raise ValueError("invalid_base58")
    data, checksum = full[:-4], full[-4:]
    import hashlib

    digest = hashlib.sha256(hashlib.sha256(data).digest()).digest()[:4]
    if checksum != digest:
        raise ValueError("invalid_base58_checksum")
    return data

class JustLendClient:
    def __init__(self, tronscan_api: str, controller_address: str):
        self.tronscan_api = tronscan_api.rstrip("/")
        self.controller_address = controller_address

    def healthy(self) -> bool:
        req = urllib.request.Request(
            f"{self.tronscan_api}/api/account?address={self.controller_address}",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return bool(body.get("address"))


def build_runtime_status(config_path: str | None = None) -> RuntimeStatus:
    cfg: TurnkeyConfig = load_turnkey_config(config_path)
    transport = resolve_tron_transport_context(cfg)
    chain_client = TronRpcClient(transport.api_base_url)
    justlend_client = JustLendClient(cfg.tronscan_api, cfg.addresses.justlend_controller)

    chain_ok = chain_client.healthy()
    justlend_ok = justlend_client.healthy()

    return RuntimeStatus(
        chain_healthy=chain_ok,
        justlend_healthy=justlend_ok,
        details={
            "network": cfg.network,
            "chain_id": cfg.chain_id,
            "rpc_url": cfg.rpc_url,
            "active_transport": transport.transport,
            "active_transport_api_url": transport.api_base_url,
            "mcp_server_tron_url": cfg.mcp_server_tron_url,
            "use_mcp_server_tron": str(cfg.use_mcp_server_tron).lower(),
            "subgraph_indexer_url": cfg.subgraph_indexer_url,
            "subgraph_indexer_local_path": cfg.subgraph_indexer_local_path,
            "resolved_indexer_url": resolve_indexer_endpoint(cfg),
            "treasury": cfg.addresses.treasury,
            "strategy_module": cfg.addresses.strategy_module,
            "justlend_ops_module": cfg.addresses.justlend_ops_module,
            "justlend_controller": cfg.addresses.justlend_controller,
            "execution_signer_wallet": cfg.signer_wallets.get("execution", ""),
            "emergency_signer_wallet": cfg.signer_wallets.get("emergency", ""),
            "analytics_signer_wallet": cfg.signer_wallets.get("analytics", ""),
        },
    )


def monitor_account_health(owner_address: str | None, config_path: str | None = None) -> Dict[str, object]:
    cfg: TurnkeyConfig = load_turnkey_config(config_path)
    transport = resolve_tron_transport_context(cfg)
    account = owner_address or cfg.addresses.treasury
    monitor = JustLendMonitor(transport.api_base_url)
    markets = [cfg.addresses.jtrx_market]
    if cfg.addresses.jusdt_market.startswith("T"):
        markets.append(cfg.addresses.jusdt_market)
    if cfg.addresses.jusdd_market.startswith("T"):
        markets.append(cfg.addresses.jusdd_market)
    snapshots = monitor.account_snapshots(account, markets)
    payload = []
    for s in snapshots:
        payload.append({
            "market": s.market,
            "status": s.status,
            "error_code": s.error_code,
            "error_detail": s.error_detail,
            "jtoken_balance": s.jtoken_balance,
            "borrow_balance": s.borrow_balance,
            "exchange_rate_mantissa": s.exchange_rate_mantissa,
            "exchange_rate": s.exchange_rate,
        })
    return {"owner_address": account, "treasury": cfg.addresses.treasury, "markets": payload}


def check_jtrx_borrow_readiness(config_path: str | None = None) -> Dict[str, object]:
    cfg: TurnkeyConfig = load_turnkey_config(config_path)
    transport = resolve_tron_transport_context(cfg)
    monitor = JustLendMonitor(transport.api_base_url)

    collateral = cfg.allowed_assets.get("jTRX", "")
    target = cfg.addresses.jtrx_market
    mode = cfg.borrower_account_mode.strip().lower()

    if mode == "strategy_module":
        module = cfg.addresses.strategy_module
        checks = {
            "allowed_target_jtrx_market": monitor.call_bool(module, "allowedTargets(address)", _abi_encode_address(target)),
            "allowed_asset_collateral_jtrx": monitor.call_bool(module, "allowedAssets(address)", _abi_encode_address(collateral)) if collateral else False,
            "allowed_asset_borrow_jtrx_market": monitor.call_bool(module, "allowedAssets(address)", _abi_encode_address(target)),
            "allowed_pair_jtrx_to_jtrx_market": monitor.call_bool(
                module,
                "allowedBorrowPairs(address,address)",
                _abi_encode_address(collateral) + _abi_encode_address(target),
            ) if collateral else False,
            "selector_borrow_allowed": monitor.call_bool(module, "allowedSelectors(bytes4)", _abi_encode_bytes4("0xc5ebeaec")),
            "selector_repay_allowed": monitor.call_bool(module, "allowedSelectors(bytes4)", _abi_encode_bytes4("0x0e752702")),
        }
    else:
        module = cfg.addresses.justlend_ops_module
        checks = {
            "allowed_market_jtrx": monitor.call_bool(module, "allowedMarkets(address)", _abi_encode_address(target)),
            "allowed_asset_collateral_jtrx": monitor.call_bool(module, "allowedAssets(address)", _abi_encode_address(collateral)) if collateral else False,
        }

    ready = all(checks.values())
    return {
        "ready": ready,
        "active_transport": transport.transport,
        "borrower_account_mode": mode,
        "strategy_module": cfg.addresses.strategy_module,
        "justlend_ops_module": cfg.addresses.justlend_ops_module,
        "active_module": module,
        "jtrx_market": target,
        "collateral_symbol": "jTRX",
        "borrow_symbol": "TRX",
        "checks": checks,
    }
