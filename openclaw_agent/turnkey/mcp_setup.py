from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict

from .config import TurnkeyConfig


@dataclass(frozen=True)
class MCPServerSetupPlan:
    """Canonical setup plan for configuring MCP transport on TRON Nile."""

    network: str
    chain_id: str
    rpc_url: str
    mcp_server_tron_url: str
    use_mcp_server_tron: bool
    subgraph_indexer_url: str
    required_environment_variables: Dict[str, str]
    operator_readiness_checklist: list[str]
    investor_readiness_summary: Dict[str, str]


NILE_DEFAULT_MCP_URL = "http://127.0.0.1:3000"


def build_nile_mcp_server_setup_plan(
    cfg: TurnkeyConfig,
    *,
    mcp_server_tron_url: str = NILE_DEFAULT_MCP_URL,
    subgraph_indexer_url: str = "",
) -> MCPServerSetupPlan:
    """Build a standardized MCP setup plan for Nile testnet operations."""

    normalized_mcp_url = (mcp_server_tron_url or "").strip().rstrip("/")
    normalized_subgraph_url = (subgraph_indexer_url or "").strip().rstrip("/")
    if not normalized_subgraph_url and normalized_mcp_url:
        normalized_subgraph_url = f"{normalized_mcp_url}/ops/indexer/events"

    required_environment_variables = {
        "OPENCLAW_RPC_URL": cfg.rpc_url,
        "OPENCLAW_SIGNER_RPC_URL": cfg.signer_rpc_url,
        "OPENCLAW_MCP_SERVER_TRON_URL": normalized_mcp_url,
        "OPENCLAW_USE_MCP_SERVER_TRON": "true",
        "OPENCLAW_SUBGRAPH_INDEXER_URL": normalized_subgraph_url,
    }

    checklist = [
        "Confirm mcp-server-tron process is reachable from operator host",
        "Verify TreasuryVaultV2, strategy module, and ops module addresses are set for Nile",
        "Run turnkey health and borrow-readiness checks with MCP transport enabled",
        "Run a simulation-only run-jtrx cycle and verify deterministic audit bundle output",
        "Run a low-notional broadcast on Nile and validate receipt + indexer event emission",
    ]

    investor_summary = {
        "control_plane": "Treasury execution remains policy-gated with vault-constrained outflows.",
        "operational_resilience": "MCP transport centralizes TRON IO behavior for repeatable execution under testnet conditions.",
        "auditability": "Environment and checklist enforce deterministic traceability from intent to receipt.",
    }

    return MCPServerSetupPlan(
        network=cfg.network,
        chain_id=cfg.chain_id,
        rpc_url=cfg.rpc_url,
        mcp_server_tron_url=normalized_mcp_url,
        use_mcp_server_tron=bool(normalized_mcp_url),
        subgraph_indexer_url=normalized_subgraph_url,
        required_environment_variables=required_environment_variables,
        operator_readiness_checklist=checklist,
        investor_readiness_summary=investor_summary,
    )


def setup_plan_to_dict(plan: MCPServerSetupPlan) -> Dict[str, object]:
    return asdict(plan)
