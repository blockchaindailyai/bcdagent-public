from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict


@dataclass(frozen=True)
class TurnkeyAddresses:
    treasury: str
    strategy_module: str
    justlend_ops_module: str
    operator: str
    justlend_controller: str
    jtrx_market: str
    jusdt_market: str = ""
    jusdd_market: str = ""


@dataclass(frozen=True)
class TurnkeyConfig:
    network: str
    chain_id: str
    rpc_url: str
    signer_rpc_url: str
    tronscan_api: str
    mcp_server_tron_url: str
    use_mcp_server_tron: bool
    subgraph_indexer_url: str
    subgraph_indexer_local_path: str
    enable_local_signing_fallback: bool
    permission_id: int
    addresses: TurnkeyAddresses
    allowed_assets: Dict[str, str]
    borrower_account_mode: str
    signer_wallets: Dict[str, str]


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "testnet_turnkey.json"


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return int(raw.strip())


def load_turnkey_config(config_path: str | None = None) -> TurnkeyConfig:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    addresses_payload = payload["addresses"]
    addresses = TurnkeyAddresses(
        treasury=_env("OPENCLAW_TREASURY_ADDRESS", addresses_payload["treasury"]),
        strategy_module=_env("OPENCLAW_STRATEGY_MODULE_ADDRESS", addresses_payload["strategy_module"]),
        justlend_ops_module=_env(
            "OPENCLAW_JUSTLEND_OPS_MODULE_ADDRESS",
            addresses_payload.get("justlend_ops_module", _env("OPENCLAW_STRATEGY_MODULE_ADDRESS", addresses_payload["strategy_module"])),
        ),
        operator=_env("OPENCLAW_OPERATOR_ADDRESS", addresses_payload["operator"]),
        justlend_controller=_env("OPENCLAW_JUSTLEND_CONTROLLER", addresses_payload["justlend_controller"]),
        jtrx_market=_env("OPENCLAW_JTRX_MARKET", addresses_payload["jtrx_market"]),
        jusdt_market=_env(
            "OPENCLAW_JUSDT_MARKET",
            addresses_payload.get("jusdt_market", payload.get("allowed_assets", {}).get("jUSDT", "")),
        ),
        jusdd_market=_env("OPENCLAW_JUSDD_MARKET", addresses_payload.get("jusdd_market", "")),
    )

    signer_wallets = payload.get("signer_wallets", {})

    return TurnkeyConfig(
        network=payload["network"],
        chain_id=payload["chain_id"],
        rpc_url=_env("OPENCLAW_RPC_URL", payload["rpc_url"]),
        signer_rpc_url=_env("OPENCLAW_SIGNER_RPC_URL", payload.get("signer_rpc_url", payload["rpc_url"])),
        tronscan_api=_env("OPENCLAW_TRONSCAN_API", payload["tronscan_api"]),
        mcp_server_tron_url=_env("OPENCLAW_MCP_SERVER_TRON_URL", payload.get("mcp_server_tron_url", "")),
        use_mcp_server_tron=_env_bool("OPENCLAW_USE_MCP_SERVER_TRON", bool(payload.get("use_mcp_server_tron", False))),
        subgraph_indexer_url=_env("OPENCLAW_SUBGRAPH_INDEXER_URL", payload.get("subgraph_indexer_url", "")),
        subgraph_indexer_local_path=_env(
            "OPENCLAW_SUBGRAPH_INDEXER_LOCAL_PATH",
            payload.get("subgraph_indexer_local_path", "openclaw_agent/data/subgraph/events.jsonl"),
        ),
        enable_local_signing_fallback=_env_bool(
            "OPENCLAW_ENABLE_LOCAL_SIGNING_FALLBACK",
            bool(payload.get("enable_local_signing_fallback", True)),
        ),
        permission_id=_env_int("OPENCLAW_PERMISSION_ID", int(payload.get("permission_id", 0))),
        addresses=addresses,
        allowed_assets=payload["allowed_assets"],
        borrower_account_mode=str(payload.get("policy", {}).get("borrower_account_mode", "justlend_ops_module")),
        signer_wallets={
            "execution": _env("OPENCLAW_EXECUTION_ADDRESS", signer_wallets.get("execution", addresses.operator)),
            "emergency": _env("OPENCLAW_EMERGENCY_ADDRESS", signer_wallets.get("emergency", "")),
            "analytics": _env("OPENCLAW_ANALYTICS_ADDRESS", signer_wallets.get("analytics", addresses.treasury)),
        },
    )
